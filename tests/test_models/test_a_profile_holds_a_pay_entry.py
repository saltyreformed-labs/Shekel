"""The database refuses a salary profile with no pay entry (task plan#3167, ruling R-SAL68).

Every paycheck is priced from the profile's pay list, so a profile holds at
least one entry.  The doors keep that in words; :mod:`app.pay_entry_infrastructure`
makes it unstorable for every writer, at COMMIT:

* a raw delete of a profile's last entry, a move of its only entry to another
  profile, a profile inserted with none, and one renumbered with none are each
  refused, the refusal naming the profile it would have left empty;
* the legal writes beside each pass: removing one of two entries, moving one
  of two, a profile written and then given its first entry in one
  transaction, and a profile's own delete, whose entries cascade with it;
* Remove's own refusal comes first, in its own words, and the rule stands
  under it: an entry taken off the list around the door is refused at commit;
* two tabs each removing one of the last two entries, raced as two real
  requests: the second waits on the owner's write lock, counts one, and is
  refused in the door's words;
* two raw writers without that lock: the predicate's row lock makes the
  second's check wait for the first's;
* migration ``c3a7e5d91f42`` refuses to install over a profile holding none,
  and round-trips, its upgrade installing exactly the triggers the template
  build applies.

Every figure is made up.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy
from sqlalchemy.exc import DBAPIError

from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.pay_entry_infrastructure import (
    PAY_ENTRY_TRIGGERS,
    apply_pay_entry_infrastructure,
    remove_pay_entry_infrastructure,
)
from app.services import pay_list_service
from app.utils.db_errors import sqlstate_of
from tests._request_race import race_two_requests, signed_in_clients
from tests._salary_forms import rendered_page_form
from tests._test_helpers import (
    freeze_today,
    load_migration_module,
    make_salary_profile,
    refused_by_database_rule,
    run_migration_callable,
)

_MIGRATION = load_migration_module("c3a7e5d91f42_a_salary_profile_holds_a_pay_entry.py")

#: The three functions the rule installs, with their argument lists.
_FUNCTIONS = (
    "salary.assert_profile_holds_a_pay_entry(integer)",
    "salary.refuse_pay_entry_leaving_its_profile_empty()",
    "salary.refuse_profile_saved_without_pay()",
)

#: PostgreSQL's SQLSTATE for ``lock_not_available``: a statement that waited
#: past ``lock_timeout``.
_LOCK_NOT_AVAILABLE = "55P03"

#: The Remove door's refusal of the only entry, as the page renders it.
_ONLY_ENTRY_REFUSED = (
    b"is your only pay entry, and every paycheck is priced from it, so it "
    b"can&#39;t be removed.  Fix it instead."
)


def _refused_leaving(profile_id):
    """Expect the rule's refusal naming *profile_id* as the profile left empty."""
    return refused_by_database_rule(
        rf"salary profile {profile_id} would hold no pay entry"
    )


def _scalar(sql, **params):
    """Return the one value *sql* selects."""
    return db.session.execute(sqlalchemy.text(sql), params).scalar()


def _execute(sql, **params):
    """Run *sql* on the test session."""
    db.session.execute(sqlalchemy.text(sql), params)


def _entry_count(profile_id):
    """How many pay entries the database holds for *profile_id*."""
    return _scalar(
        "SELECT count(*) FROM salary.pay_entries WHERE salary_profile_id = :id",
        id=profile_id,
    )


def _profile_exists(profile_id):
    """Whether the database holds a salary profile numbered *profile_id*."""
    return _scalar(
        "SELECT EXISTS (SELECT 1 FROM salary.salary_profiles WHERE id = :id)",
        id=profile_id,
    )


def _job(seed_user, name="Day Job", pay="2000.00", pay_from=None):
    """A committed profile of the seed user's with ONE entry, from *pay_from*.

    *pay_from* defaults to the owner's first saved payday.

    Returns:
        ``(profile_id, entry_id)``.
    """
    profile = make_salary_profile(
        seed_user, db.session, name=name, pay=Decimal(pay), pay_from=pay_from,
    )
    db.session.commit()
    return profile.id, profile.pay_entries[0].id


def _second_entry(profile_id, payday, pay="2100.00"):
    """Commit a second entry on *profile_id* from *payday*; return its id."""
    entry = SalaryPayEntry(
        salary_profile_id=profile_id, payday=payday, amount=Decimal(pay),
    )
    db.session.add(entry)
    db.session.commit()
    return entry.id


def _raw_bare_profile(profile_id, name):
    """INSERT, raw, a copy of *profile_id*'s profile under *name* with NO entry.

    A copy of an existing row's required columns, so the case states no
    profile field of its own: the writer it plays is one nobody enumerated.

    Returns:
        The new profile's id, uncommitted.
    """
    return db.session.execute(sqlalchemy.text(
        "INSERT INTO salary.salary_profiles "
        "(user_id, scenario_id, filing_status_id, name, "
        "period_end_days_before_payday) "
        "SELECT user_id, scenario_id, filing_status_id, :name, "
        "period_end_days_before_payday "
        "FROM salary.salary_profiles WHERE id = :id RETURNING id"
    ), {"id": profile_id, "name": name}).scalar_one()


def _trigger_definitions():
    """Each attachment's definition, read back, as ``{(name, table): definition}``.

    ``pg_get_triggerdef`` states the events, the column list, the deferral and
    the function, so a trigger present under the right name but firing on the
    wrong events reads as a difference.  ``None`` for an attachment missing.
    """
    return {
        (name, table): _scalar(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgname = :name AND tgrelid = to_regclass(:table)",
            name=name, table=table,
        )
        for name, table in PAY_ENTRY_TRIGGERS
    }


def _functions_present():
    """``{signature: exists}`` for each of the rule's three functions."""
    return {
        signature: _scalar(
            "SELECT to_regprocedure(:signature) IS NOT NULL",
            signature=signature,
        )
        for signature in _FUNCTIONS
    }


class TestTheRuleRefusesAProfileLeftEmpty:
    """Each event that can leave a profile with no entry is refused at commit."""

    def test_a_raw_delete_of_the_last_entry_is_refused_at_commit(
        self, app, seed_user, seed_periods,
    ):
        """The DELETE itself runs; the COMMIT is refused, and the entry stands."""
        with app.app_context():
            profile_id, entry_id = _job(seed_user)

            _execute("DELETE FROM salary.pay_entries WHERE id = :id", id=entry_id)
            assert _entry_count(profile_id) == 0  # deferred: stored until commit
            with _refused_leaving(profile_id):
                db.session.commit()
            db.session.rollback()

            assert _entry_count(profile_id) == 1

    def test_a_move_of_the_only_entry_to_another_profile_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """The entry's OLD profile is the one named: the move would empty it."""
        with app.app_context():
            source_id, entry_id = _job(seed_user, "Day Job")
            # A payday of its own, so the move meets the rule and not the
            # one-entry-per-payday key.
            target_id, _ = _job(
                seed_user, "Night Job", pay_from=seed_periods[4].start_date,
            )

            _execute(
                "UPDATE salary.pay_entries SET salary_profile_id = :target "
                "WHERE id = :id",
                target=target_id, id=entry_id,
            )
            with _refused_leaving(source_id):
                db.session.commit()
            db.session.rollback()

            assert (_entry_count(source_id), _entry_count(target_id)) == (1, 1)

    def test_a_profile_inserted_with_no_entry_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """A raw INSERT of a profile, and nothing after it: refused, and not stored."""
        with app.app_context():
            profile_id, _ = _job(seed_user)

            bare_id = _raw_bare_profile(profile_id, "Bare Job")
            with _refused_leaving(bare_id):
                db.session.commit()
            db.session.rollback()

            assert not _profile_exists(bare_id)

    def test_a_profile_renumbered_with_no_entry_is_refused_under_its_new_id(
        self, app, seed_user, seed_periods,
    ):
        """The INSERT's check reads the row as it was, finds no profile, and passes.

        So it is the ``UPDATE OF id`` arm that refuses, naming the NEW number.
        With that arm dropped this commit stores a profile with no entry.
        """
        with app.app_context():
            profile_id, _ = _job(seed_user)
            bare_id = _raw_bare_profile(profile_id, "Bare Job")
            renumbered = bare_id + 1000

            _execute(
                "UPDATE salary.salary_profiles SET id = :new WHERE id = :old",
                new=renumbered, old=bare_id,
            )
            with _refused_leaving(renumbered):
                db.session.commit()
            db.session.rollback()

            assert not _profile_exists(renumbered)
            assert not _profile_exists(bare_id)

    def test_the_rule_waits_for_the_commit_and_not_the_statement(self, app):
        """Both attachments are DEFERRABLE INITIALLY DEFERRED constraint triggers."""
        with app.app_context():
            for name, table in PAY_ENTRY_TRIGGERS:
                row = db.session.execute(sqlalchemy.text(
                    "SELECT tgdeferrable, tginitdeferred, tgconstraint <> 0 "
                    "FROM pg_trigger WHERE tgname = :name "
                    "AND tgrelid = to_regclass(:table)"
                ), {"name": name, "table": table}).one()
                assert tuple(row) == (True, True, True), name


class TestTheLegalWritesBesideItPass:
    """The controls: a rule refusing every such write would pass every case above."""

    def test_removing_one_of_two_entries_commits(self, app, seed_user, seed_periods):
        """A raw DELETE of one of two: committed, one left."""
        with app.app_context():
            profile_id, first_id = _job(seed_user)
            _second_entry(profile_id, seed_periods[2].start_date)

            _execute("DELETE FROM salary.pay_entries WHERE id = :id", id=first_id)
            db.session.commit()

            assert _entry_count(profile_id) == 1

    def test_moving_one_of_two_entries_commits(self, app, seed_user, seed_periods):
        """A raw move of one of two to another profile: committed, each holding one or more."""
        with app.app_context():
            source_id, _ = _job(seed_user, "Day Job")
            moving_id = _second_entry(source_id, seed_periods[2].start_date)
            target_id, _ = _job(seed_user, "Night Job")

            _execute(
                "UPDATE salary.pay_entries SET salary_profile_id = :target "
                "WHERE id = :id",
                target=target_id, id=moving_id,
            )
            db.session.commit()

            assert (_entry_count(source_id), _entry_count(target_id)) == (1, 2)

    def test_an_update_naming_the_profile_but_keeping_it_commits(
        self, app, seed_user, seed_periods,
    ):
        """``SET salary_profile_id = salary_profile_id`` fires the arm and passes."""
        with app.app_context():
            profile_id, entry_id = _job(seed_user)

            _execute(
                "UPDATE salary.pay_entries SET salary_profile_id = salary_profile_id "
                "WHERE id = :id",
                id=entry_id,
            )
            db.session.commit()

            assert _entry_count(profile_id) == 1

    def test_a_profile_given_its_first_entry_after_a_flush_commits(
        self, app, seed_user, seed_periods,
    ):
        """The create door's order: the profile flushed alone, its entry after."""
        with app.app_context():
            profile_id, _ = _job(seed_user)
            bare_id = _raw_bare_profile(profile_id, "Second Job")
            db.session.flush()

            _execute(
                "INSERT INTO salary.pay_entries (salary_profile_id, payday, amount) "
                "VALUES (:id, :payday, :amount)",
                id=bare_id, payday=seed_periods[0].start_date, amount=Decimal("1500.00"),
            )
            db.session.commit()

            assert _entry_count(bare_id) == 1

    def test_a_profile_deleted_through_the_orm_takes_its_entries(
        self, app, seed_user, seed_periods,
    ):
        """The ``delete-orphan`` cascade deletes the entries FIRST; the rule finds no profile."""
        with app.app_context():
            profile_id, _ = _job(seed_user)
            _second_entry(profile_id, seed_periods[2].start_date)

            db.session.delete(db.session.get(SalaryProfile, profile_id))
            db.session.commit()

            assert not _profile_exists(profile_id)
            assert _entry_count(profile_id) == 0

    def test_a_profile_deleted_raw_takes_its_entries_by_the_key(
        self, app, seed_user, seed_periods,
    ):
        """``ON DELETE CASCADE`` deletes the entries AFTER the profile; it commits too."""
        with app.app_context():
            profile_id, _ = _job(seed_user)

            _execute("DELETE FROM salary.salary_profiles WHERE id = :id", id=profile_id)
            db.session.commit()

            assert not _profile_exists(profile_id)
            assert _entry_count(profile_id) == 0


class TestRemovesOwnRefusalComesFirst:
    """The door refuses in its words; the rule is under it, never reached."""

    def test_the_door_refuses_the_only_entry_before_any_write(
        self, app, seed_user, seed_periods,
    ):
        """``remove_entry`` raises its sentence and stages nothing, so the commit is clean."""
        with app.app_context():
            profile_id, entry_id = _job(seed_user)
            entry = db.session.get(SalaryPayEntry, entry_id)

            with pytest.raises(ValidationError, match="is your only pay entry"):
                pay_list_service.remove_entry(entry)
            db.session.commit()

            assert _entry_count(profile_id) == 1

    def test_an_entry_taken_off_the_list_around_the_door_is_refused_at_commit(
        self, app, seed_user, seed_periods,
    ):
        """The list's ``delete-orphan`` cascade DELETEs it; the rule refuses the commit."""
        with app.app_context():
            profile_id, entry_id = _job(seed_user)
            profile = db.session.get(SalaryProfile, profile_id)

            profile.pay_entries.remove(db.session.get(SalaryPayEntry, entry_id))
            with _refused_leaving(profile_id):
                db.session.commit()
            db.session.rollback()

            assert _entry_count(profile_id) == 1


class TestTwoTabsRemovingTheLastTwoEntries:
    """Two tabs each remove one of the last two entries at once: one goes, one is refused."""

    def test_the_second_waits_counts_one_and_is_refused_in_the_doors_words(
        self, app, seed_user, seed_periods, monkeypatch,
    ):
        """Raced as two real requests: the profile keeps one entry, and the second tab hears why.

        Both Remove forms are rendered while the profile holds two entries.
        The first tab's Remove of 01-02 is held at its commit, still holding
        the owner's write lock; the second tab's Remove of 01-30 is seen
        waiting on that lock, so it reads the pay list only after the first
        removal commits, counts one, and is refused by the door -- not by
        the rule, whose words would reach the owner as the route's generic
        "Failed to remove the pay entry."
        """
        freeze_today(monkeypatch, date(2026, 3, 20))
        with app.app_context():
            profile_id, first_id = _job(seed_user)
            second_id = _second_entry(profile_id, seed_periods[2].start_date)
            owner_id = seed_user["user"].id
            one, two = signed_in_clients(app)
            page = f"/salary/{profile_id}/edit"
            # Each tab renders its own Remove form while both entries stand.
            forms = {
                entry_id: (
                    client,
                    f"/salary/pay/{entry_id}/remove",
                    rendered_page_form(client, page, f"/salary/pay/{entry_id}/remove"),
                )
                for client, entry_id in ((one, first_id), (two, second_id))
            }

            def remove(entry_id):
                client, url, controls = forms[entry_id]
                return client.post(url, data=controls)

            first, second = race_two_requests(
                app, owner_id,
                lambda: remove(first_id),
                lambda: remove(second_id),
            )

            assert second.waited
            assert first.response.status_code == 302
            assert second.response.status_code == 302
            assert [
                entry.id for entry in
                db.session.query(SalaryPayEntry).filter_by(salary_profile_id=profile_id)
            ] == [second_id]
            refused = two.get(page)
            assert _ONLY_ENTRY_REFUSED in refused.data
            assert b"Failed to remove the pay entry" not in refused.data


class TestTwoRawWritersWithoutTheOwnersLock:
    """The predicate's row lock: a second writer's check waits for the first's."""

    def test_the_second_check_waits_on_the_profile_the_first_checked(
        self, app, seed_user, seed_periods,
    ):
        """Each deletes one of the last two; the first checks now and holds; the second waits.

        The first writer (this session) deletes 01-02's entry and runs its
        check at once (``SET CONSTRAINTS ... IMMEDIATE``), which passes -- the
        other entry is still there -- and leaves it holding the profile's row
        lock until it ends.  The second writer, on a connection of its own,
        deletes 01-30's entry, which takes no lock the first holds, and its
        check then waits on that row lock past a short ``lock_timeout``.
        Without the lock it would count 01-02's entry, whose delete it cannot
        see, pass, and both would commit a profile with none.  Once the first
        commits, the same check sees its delete and refuses.
        """
        with app.app_context():
            profile_id, first_id = _job(seed_user)
            second_id = _second_entry(profile_id, seed_periods[2].start_date)
            # Schema-qualified: SET CONSTRAINTS finds an unqualified name on the
            # search path, which does not hold ``salary``.
            entry_trigger = f"salary.{PAY_ENTRY_TRIGGERS[0][0]}"

            _execute("DELETE FROM salary.pay_entries WHERE id = :id", id=first_id)
            _execute(f"SET CONSTRAINTS {entry_trigger} IMMEDIATE")

            with db.engine.connect() as other:
                transaction = other.begin()
                other.execute(sqlalchemy.text("SET LOCAL lock_timeout = '300ms'"))
                other.execute(
                    sqlalchemy.text("DELETE FROM salary.pay_entries WHERE id = :id"),
                    {"id": second_id},
                )
                with pytest.raises(DBAPIError) as waited:
                    other.execute(sqlalchemy.text(
                        f"SET CONSTRAINTS {entry_trigger} IMMEDIATE"
                    ))
                assert sqlstate_of(waited.value) == _LOCK_NOT_AVAILABLE, waited.value
                transaction.rollback()

                db.session.commit()
                assert _entry_count(profile_id) == 1

                transaction = other.begin()
                other.execute(
                    sqlalchemy.text("DELETE FROM salary.pay_entries WHERE id = :id"),
                    {"id": second_id},
                )
                with _refused_leaving(profile_id):
                    transaction.commit()

            db.session.expire_all()
            assert _entry_count(profile_id) == 1


class TestTheMigration:
    """``c3a7e5d91f42``: a census first, then the triggers; and its downgrade removes them."""

    def test_the_upgrade_refuses_a_profile_already_holding_none(
        self, app, seed_user, seed_periods,
    ):
        """With the rule lifted, a profile with no entry is stored; the census names it."""
        with app.app_context():
            profile_id, _ = _job(seed_user)
            remove_pay_entry_infrastructure(_execute)
            bare_id = _raw_bare_profile(profile_id, "Bare Job")

            with pytest.raises(RuntimeError, match=(
                rf"1 salary profile\(s\) hold no pay entry \(id={bare_id} "
                rf"user_id={seed_user['user'].id} name='Bare Job'\)"
            )):
                _MIGRATION.refuse_profiles_without_pay(db.session.connection())
            db.session.rollback()

    def test_it_round_trips_installing_what_the_template_build_applies(
        self, app, seed_user, seed_periods,
    ):
        """Down: no trigger, no function, and a bare profile stores.  Up: refused, then exact.

        The template carries the rule from :func:`apply_pay_entry_infrastructure`
        (``scripts/build_test_template.py``); the definitions read before the
        downgrade are what the upgrade must put back.
        """
        with app.app_context():
            profile_id, _ = _job(seed_user)
            applied = _trigger_definitions()
            assert None not in applied.values()

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert set(_trigger_definitions().values()) == {None}
            assert not any(_functions_present().values())
            bare_id = _raw_bare_profile(profile_id, "Bare Job")
            db.session.commit()

            with pytest.raises(RuntimeError, match=rf"id={bare_id} "):
                run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.rollback()
            _execute("DELETE FROM salary.salary_profiles WHERE id = :id", id=bare_id)

            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert _trigger_definitions() == applied
            assert all(_functions_present().values())

    def test_applying_twice_is_applying_once(self, app):
        """The second apply re-pins the same two triggers: one each, the same definition."""
        with app.app_context():
            before = _trigger_definitions()
            apply_pay_entry_infrastructure(_execute)
            apply_pay_entry_infrastructure(_execute)
            assert _trigger_definitions() == before
            assert _scalar(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN (:entry, :profile)",
                entry=PAY_ENTRY_TRIGGERS[0][0], profile=PAY_ENTRY_TRIGGERS[1][0],
            ) == len(PAY_ENTRY_TRIGGERS)
            db.session.rollback()
