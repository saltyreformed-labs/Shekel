"""Tests for migration ``70680a4a7405`` (plan step salary:X-av-3a).

Rulings **R-SAL59** ("Dated pay list + forecasts") and **R-SAL60** ("Each
raise rounds"): the salary's stored fact moves from one undated yearly figure
(``salary.salary_profiles.annual_salary``) to a dated pay list
(``salary.pay_entries``), what ONE paycheck pays from a payday on.

* the upgrade writes ONE entry per profile, its yearly salary over its owner's
  paychecks a year, rounded HALF-UP to the cent, dated on the owner's first
  saved payday; the conversion is audited;
* it REFUSES, writing nothing, an owner with no saved payday, an owner with
  more than one pay era, and a raise landing on or before the first saved
  payday (an entry replaces every forecast raise due by its payday);
* the downgrade restores the yearly figure as the entry times its paychecks a
  year -- 26, 52, 24 or 12, each arm of the count run -- and refuses a profile
  holding pay history, a profile holding no entry, a multi-era owner, a raise
  its entry already holds, and (forced, since the refusals leave none) a
  profile the restore missed;
* the model and the migrated table agree.

Every figure is made up.  ``$52,000.13`` is chosen because it divides by 26
to exactly ``$2,000.005``: half-up gives ``$2,000.01`` where half-even would
give ``$2,000.00``, so the rounding rule is visible in one assertion.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import BusinessDayShiftEnum, RaiseTypeEnum
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.services import pay_era_write
from app.services.pay_rhythm import Monthly, Rhythm, SemiMonthly
from tests._test_helpers import (
    era_of,
    load_migration_module,
    make_salary_profile,
    rebuild_calendar,
    rebuild_calendar_on,
    run_migration_callable,
)

_MIGRATION = "70680a4a7405_a_salary_is_a_dated_pay_list.py"
#: The later revision that replaced the raise's year and month with a start
#: date (plan step salary plan#3048).  The revision under test reads the pair
#: in raw SQL, so its cases run with this one stepped back (:func:`_pair_shape`).
_DATED_RAISES = "6740ce13e174_a_raise_states_the_date_it_starts.py"
#: The later revision that gave the database the at-least-one-entry rule (task
#: plan#3167).  The revision under test met no such rule, and its downgrade
#: refuses a profile with no entry, a state the rule makes unstorable, so its
#: cases run with this one stepped back (:func:`_without_the_pay_entry_rule`).
_PAY_ENTRY_RULE = "c3a7e5d91f42_a_salary_profile_holds_a_pay_entry.py"


def _scalar(sql, **params):
    """Return the one value *sql* selects."""
    return db.session.execute(sqlalchemy.text(sql), params).scalar()


def _entries():
    """Return every pay entry as ``(profile id, payday, amount)``, in id order."""
    return [
        tuple(row) for row in db.session.execute(sqlalchemy.text(
            "SELECT salary_profile_id, payday, amount FROM salary.pay_entries "
            "ORDER BY salary_profile_id, payday"
        ))
    ]


def _pay_list_exists():
    """Whether ``salary.pay_entries`` exists."""
    return _scalar("SELECT to_regclass('salary.pay_entries') IS NOT NULL")


def _annual_column_exists():
    """Whether ``salary.salary_profiles.annual_salary`` exists."""
    return _scalar(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'salary' AND table_name = 'salary_profiles' "
        "AND column_name = 'annual_salary')"
    )


def _annual_of(profile_id):
    """Return the downgraded schema's ``annual_salary`` for *profile_id*."""
    return _scalar(
        "SELECT annual_salary FROM salary.salary_profiles WHERE id = :id",
        id=profile_id,
    )


def _set_annual(profile_id, annual):
    """Write *annual* as the downgraded schema's yearly salary of *profile_id*."""
    db.session.execute(sqlalchemy.text(
        "UPDATE salary.salary_profiles SET annual_salary = :annual "
        "WHERE id = :id"
    ), {"annual": annual, "id": profile_id})
    db.session.commit()


def _semi_monthly_owner(seed_user):
    """Rebuild the owner's schedule on the 1st and the 15th from 2026-01-01."""
    rebuild_calendar_on(
        seed_user["user"].id, date(2026, 1, 1), 6,
        Rhythm(SemiMonthly((1, 15)), BusinessDayShiftEnum.NONE),
    )


def _monthly_owner(seed_user):
    """Rebuild the owner's schedule on the 15th of each month from 2026-01-15."""
    rebuild_calendar_on(
        seed_user["user"].id, date(2026, 1, 15), 6,
        Rhythm(Monthly(15), BusinessDayShiftEnum.NONE),
    )


@pytest.fixture
def _pair_shape(app, db):
    """Step the raise table back to the year-and-month shape this revision met.

    ``70680a4a7405`` reads ``salary_raises.effective_year`` and
    ``effective_month`` in raw SQL, both ways; ``6740ce13e174`` later replaced
    them with ``effective_date``.  Downgrading that later revision first gives
    the revision under test the schema it was written against, which is the
    shape every database it ran on had.  Each test's database is a clone of
    its own, so nothing steps it forward again.
    """
    with app.app_context():
        run_migration_callable(load_migration_module(_DATED_RAISES).downgrade, db.session)


@pytest.fixture
def _without_the_pay_entry_rule(app, db):
    """Step the at-least-one-entry rule back: this revision met a database without it.

    ``c3a7e5d91f42`` installed it after this revision, on the table this
    revision's downgrade drops.  Stepped back first, newest first, before
    :func:`_pair_shape`; each test's database is a clone of its own, so
    nothing steps it forward again.
    """
    with app.app_context():
        run_migration_callable(load_migration_module(_PAY_ENTRY_RULE).downgrade, db.session)


def _raise(profile, year, month):
    """Add a 3% merit raise first landing on the 1st of *month* in *year*.

    A raw INSERT into the year-and-month shape :func:`_pair_shape` restores,
    which the ORM model, mapped to the start date, cannot write.
    """
    db.session.execute(sqlalchemy.text(
        "INSERT INTO salary.salary_raises (salary_profile_id, raise_type_id, "
        " effective_year, effective_month, percentage, is_recurring, version_id, "
        " created_at) VALUES (:pid, :tid, :year, :month, 0.0300, false, 1, now())"
    ), {
        "pid": profile.id, "tid": ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        "year": year, "month": month,
    })


def _downgraded(seed_user, *, pay=Decimal("2000.00"), name="Day job"):
    """One profile at head on *pay*, then the migration's downgrade; return (migration, profile id)."""
    migration = load_migration_module(_MIGRATION)
    profile = make_salary_profile(seed_user, db.session, name=name, pay=pay)
    db.session.commit()
    profile_id = profile.id
    run_migration_callable(migration.downgrade, db.session)
    return migration, profile_id


@pytest.mark.usefixtures("_without_the_pay_entry_rule", "_pair_shape")
class TestTheUpgrade:
    """One entry per profile, half-up to the cent, on the first saved payday."""

    def test_each_profile_gets_one_entry_on_the_first_saved_payday(
        self, app, db, seed_user, seed_periods,
    ):
        """``$52,000.13`` biweekly -> ``$2,000.01`` from 2026-01-02, and the column goes.

        52,000.13 / 26 = 2,000.005 exactly; half-up is 2,000.01.
        """
        with app.app_context():
            migration, profile_id = _downgraded(seed_user)
            _set_annual(profile_id, Decimal("52000.13"))

            run_migration_callable(migration.upgrade, db.session)

            assert _entries() == [
                (profile_id, date(2026, 1, 2), Decimal("2000.01")),
            ]
            assert not _annual_column_exists()

    def test_a_weekly_owner_divides_by_52(self, app, db, seed_user):
        """``$75,000.00`` weekly -> 75,000 / 52 = 1,442.3077 -> ``$1,442.31``."""
        with app.app_context():
            rebuild_calendar(seed_user["user"].id, date(2026, 1, 2), 6, 7)
            migration, profile_id = _downgraded(seed_user)
            _set_annual(profile_id, Decimal("75000.00"))

            run_migration_callable(migration.upgrade, db.session)

            assert _entries() == [
                (profile_id, date(2026, 1, 2), Decimal("1442.31")),
            ]

    def test_a_semi_monthly_owner_divides_by_24(self, app, db, seed_user):
        """``$50,000.00`` on the 1st and 15th -> 50,000 / 24 = 2,083.3333 -> ``$2,083.33``.

        The count's semi-monthly arm (an era with ``other_day``): 24, as
        ``PayCadence.periods_per_year`` answers two days a month.
        """
        with app.app_context():
            _semi_monthly_owner(seed_user)
            migration, profile_id = _downgraded(seed_user)
            _set_annual(profile_id, Decimal("50000.00"))

            run_migration_callable(migration.upgrade, db.session)

            assert _entries() == [
                (profile_id, date(2026, 1, 1), Decimal("2083.33")),
            ]

    def test_a_monthly_owner_divides_by_12(self, app, db, seed_user):
        """``$50,000.00`` on the 15th -> 50,000 / 12 = 4,166.6667 -> ``$4,166.67``.

        The count's monthly arm (neither ``cadence_days`` nor ``other_day``).
        """
        with app.app_context():
            _monthly_owner(seed_user)
            migration, profile_id = _downgraded(seed_user)
            _set_annual(profile_id, Decimal("50000.00"))

            run_migration_callable(migration.upgrade, db.session)

            assert _entries() == [
                (profile_id, date(2026, 1, 15), Decimal("4166.67")),
            ]

    def test_the_conversion_is_audited(self, app, db, seed_user, seed_periods):
        """The trigger exists before the entries are written, so each write is logged.

        Counted past the log's high-water mark: the table is re-created, so
        the new entry can reuse the id the head-built entry was logged under.
        """
        with app.app_context():
            migration, profile_id = _downgraded(seed_user)
            _set_annual(profile_id, Decimal("52000.00"))
            logged_before = _scalar("SELECT coalesce(max(id), 0) FROM system.audit_log")

            run_migration_callable(migration.upgrade, db.session)

            entry_id = _scalar(
                "SELECT id FROM salary.pay_entries WHERE salary_profile_id = :id",
                id=profile_id,
            )
            assert _scalar(
                "SELECT count(*) FROM system.audit_log "
                "WHERE id > :before AND table_schema = 'salary' "
                "AND table_name = 'pay_entries' AND operation = 'INSERT' "
                "AND row_id = :id",
                before=logged_before, id=entry_id,
            ) == 1

    def test_a_raise_landing_after_the_first_payday_does_not_refuse(
        self, app, db, seed_user,
    ):
        """First payday 2026-01-01; a raise landing 2026-02-01 is after it: converted."""
        with app.app_context():
            rebuild_calendar(seed_user["user"].id, date(2026, 1, 1), 6, 14)
            migration, profile_id = _downgraded(seed_user)
            profile = db.session.get(SalaryProfile, profile_id)
            _raise(profile, 2026, 2)
            db.session.commit()

            run_migration_callable(migration.upgrade, db.session)

            assert _entries() == [
                (profile_id, date(2026, 1, 1), Decimal("2000.00")),
            ]

    def test_a_raise_landing_on_the_first_payday_refuses_and_writes_nothing(
        self, app, db, seed_user,
    ):
        """First payday 2026-01-01; a raise landing 2026-01-01 is ON it: refused, named.

        The boundary of ``<=``: the entry would replace the raise, so it would
        silently stop applying.
        """
        with app.app_context():
            rebuild_calendar(seed_user["user"].id, date(2026, 1, 1), 6, 14)
            migration, profile_id = _downgraded(seed_user)
            profile = db.session.get(SalaryProfile, profile_id)
            _raise(profile, 2026, 1)
            db.session.commit()
            raise_id = _scalar(
                "SELECT id FROM salary.salary_raises WHERE salary_profile_id = :id",
                id=profile_id,
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()

            assert (
                f"profile {profile_id} (Day job): raise {raise_id} lands "
                "2026-01-01, on or before the first saved payday 2026-01-01"
            ) in str(excinfo.value)
            assert not _pay_list_exists()
            assert _annual_column_exists()

    def test_an_owner_with_two_pay_eras_refuses_and_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A later weekly era: which count divides is the calendar's rule, not guessed here."""
        with app.app_context():
            migration, profile_id = _downgraded(seed_user)
            pay_era_write.mint_era(
                seed_user["user"].id, era_of(date(2026, 6, 5), 7),
            )
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()

            assert f"profile {profile_id} (Day job): 2 pay eras" in str(
                excinfo.value,
            )
            assert not _pay_list_exists()
            assert _annual_column_exists()

    def test_an_owner_with_no_saved_payday_refuses_and_writes_nothing(
        self, app, db, seed_user,
    ):
        """No payday to date the entry on: refused, named."""
        with app.app_context():
            migration, profile_id = _downgraded(seed_user)
            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.pay_periods WHERE user_id = :user_id"
            ), {"user_id": seed_user["user"].id})
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.upgrade, db.session)
            db.session.rollback()

            assert f"profile {profile_id} (Day job): no saved payday" in str(
                excinfo.value,
            )
            assert not _pay_list_exists()
            assert _annual_column_exists()


@pytest.mark.usefixtures("_without_the_pay_entry_rule", "_pair_shape")
class TestTheDowngrade:
    """The yearly figure back as entry x count; history, emptiness and eras refused."""

    def test_the_yearly_figure_is_the_entry_times_its_count(
        self, app, db, seed_user, seed_periods,
    ):
        """``$2,000.01`` biweekly -> 2,000.01 x 26 = ``$52,000.26``."""
        with app.app_context():
            _, profile_id = _downgraded(seed_user, pay=Decimal("2000.01"))

            assert _annual_of(profile_id) == Decimal("52000.26")
            assert not _pay_list_exists()

    def test_a_weekly_entry_multiplies_by_52(self, app, db, seed_user):
        """``$1,442.31`` weekly -> 1,442.31 x 52 = ``$75,000.12``."""
        with app.app_context():
            rebuild_calendar(seed_user["user"].id, date(2026, 1, 2), 6, 7)
            _, profile_id = _downgraded(seed_user, pay=Decimal("1442.31"))

            assert _annual_of(profile_id) == Decimal("75000.12")

    def test_a_semi_monthly_entry_multiplies_by_24(self, app, db, seed_user):
        """``$2,083.33`` on the 1st and 15th -> 2,083.33 x 24 = ``$49,999.92``."""
        with app.app_context():
            _semi_monthly_owner(seed_user)
            _, profile_id = _downgraded(seed_user, pay=Decimal("2083.33"))

            assert _annual_of(profile_id) == Decimal("49999.92")

    def test_a_monthly_entry_multiplies_by_12(self, app, db, seed_user):
        """``$4,166.67`` on the 15th -> 4,166.67 x 12 = ``$50,000.04``."""
        with app.app_context():
            _monthly_owner(seed_user)
            _, profile_id = _downgraded(seed_user, pay=Decimal("4166.67"))

            assert _annual_of(profile_id) == Decimal("50000.04")

    def test_a_profile_the_restore_missed_refuses_before_not_null(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A profile left without a yearly figure is named, with the diagnostic SELECT.

        The refusals before the restore leave no such profile, so the restore
        is replaced by a statement that restores nothing to reach the check;
        the column the downgrade added goes with the rollback.
        """
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(seed_user, db.session, name="Day job")
            db.session.commit()
            monkeypatch.setattr(
                migration, "_RESTORE_ANNUAL", sqlalchemy.text("SELECT 1"),
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            message = str(excinfo.value)
            assert f"profile {profile.id} (Day job)" in message
            assert "annual_salary cannot be made NOT NULL" in message
            assert "diagnose with: SELECT sp.id, sp.name" in message
            # The diagnostic runs against the schema the rollback left: it
            # lists nothing here, since the restore was forced to miss.
            diagnostic = message.split("diagnose with: ", 1)[1]
            assert db.session.execute(sqlalchemy.text(diagnostic)).fetchall() == []
            assert _pay_list_exists()
            assert not _annual_column_exists()

    def test_the_diagnostic_lists_a_profile_the_restore_really_missed(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A profile with no entry that gets past the refusals is named, and the diagnostic lists it.

        The refusal of a profile without pay is blinded, standing in for a
        writer that removed the entry between the refusals and the restore:
        the REAL restore then misses that profile, the check fires, and the
        diagnostic the message quotes, run after the rollback, lists exactly
        it (the forced case above can only show that the diagnostic runs).
        """
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            make_salary_profile(seed_user, db.session, name="Day job")
            bare = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                name="Side job",
            )
            db.session.add(bare)
            db.session.commit()
            monkeypatch.setattr(
                migration, "_PROFILES_WITHOUT_PAY",
                sqlalchemy.text("SELECT NULL::int, NULL::text WHERE false"),
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            message = str(excinfo.value)
            assert f"profile {bare.id} (Side job)" in message
            diagnostic = message.split("diagnose with: ", 1)[1]
            assert db.session.execute(sqlalchemy.text(diagnostic)).fetchall() == [
                (bare.id, "Side job"),
            ]
            assert not _annual_column_exists()

    def test_the_round_trip_keeps_the_entry(self, app, db, seed_user, seed_periods):
        """52,000.13 -> 2,000.01 -> 52,000.26 -> 2,000.01: every paycheck priced alike.

        The stored yearly figure moves to a multiple of the count; the entry,
        which is what prices a paycheck, survives the trip exactly.
        """
        with app.app_context():
            migration, profile_id = _downgraded(seed_user)
            _set_annual(profile_id, Decimal("52000.13"))
            run_migration_callable(migration.upgrade, db.session)
            run_migration_callable(migration.downgrade, db.session)
            assert _annual_of(profile_id) == Decimal("52000.26")

            run_migration_callable(migration.upgrade, db.session)
            assert _entries() == [
                (profile_id, date(2026, 1, 2), Decimal("2000.01")),
            ]

    def test_pay_history_refuses_and_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A second entry is history the one yearly figure cannot hold: refused."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(
                seed_user, db.session, pay=Decimal("2000.00"),
            )
            db.session.add(SalaryPayEntry(
                salary_profile_id=profile.id,
                payday=seed_periods[3].start_date,
                amount=Decimal("2100.00"),
            ))
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            assert "1 salary profile(s) hold more than one pay entry" in str(
                excinfo.value,
            )
            assert _pay_list_exists()
            assert not _annual_column_exists()

    def test_a_profile_without_pay_refuses_and_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """No door leaves a profile without an entry; the downgrade names one that has none."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            make_salary_profile(seed_user, db.session, name="Day job")
            bare = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                name="Side job",
            )
            db.session.add(bare)
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            assert f"profile {bare.id} (Side job)" in str(excinfo.value)
            assert _pay_list_exists()
            assert not _annual_column_exists()

    def test_a_raise_the_entry_already_holds_refuses_and_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Entry from 2026-01-16; a raise landing 2026-01-01 is inside it: refused, named.

        Made-up: restored as one yearly figure, the older engine would apply
        the raise to it a second time.
        """
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(
                seed_user, db.session, name="Day job",
                pay_from=seed_periods[1].start_date,
            )
            _raise(profile, 2026, 1)
            db.session.commit()
            raise_id = _scalar(
                "SELECT id FROM salary.salary_raises WHERE salary_profile_id = :id",
                id=profile.id,
            )

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            assert (
                f"profile {profile.id} (Day job): raise {raise_id} lands "
                "2026-01-01, on or before the pay entry of 2026-01-16"
            ) in str(excinfo.value)
            assert _pay_list_exists()
            assert not _annual_column_exists()

    def test_a_raise_landing_on_the_entry_payday_refuses(
        self, app, db, seed_user,
    ):
        """Entry from 2026-01-01; a raise landing 2026-01-01 is ON it: the boundary of ``<=``."""
        with app.app_context():
            rebuild_calendar(seed_user["user"].id, date(2026, 1, 1), 6, 14)
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(seed_user, db.session, name="Day job")
            _raise(profile, 2026, 1)
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            assert "on or before the pay entry of 2026-01-01" in str(excinfo.value)
            assert _pay_list_exists()

    def test_a_raise_landing_after_the_entry_does_not_refuse(
        self, app, db, seed_user, seed_periods,
    ):
        """Entry from 2026-01-02; a raise landing 2026-02-01 is after it: restored."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(
                seed_user, db.session, pay=Decimal("2000.00"),
            )
            _raise(profile, 2026, 2)
            db.session.commit()

            run_migration_callable(migration.downgrade, db.session)

            assert _annual_of(profile.id) == Decimal("52000.00")

    def test_no_saved_payday_does_not_refuse_the_downgrade(
        self, app, db, seed_user, seed_periods,
    ):
        """The downgrade dates nothing, so an owner without a saved payday restores."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(
                seed_user, db.session, pay=Decimal("2000.00"),
            )
            db.session.commit()
            db.session.execute(sqlalchemy.text(
                "DELETE FROM budget.pay_periods WHERE user_id = :user_id"
            ), {"user_id": seed_user["user"].id})
            db.session.commit()

            run_migration_callable(migration.downgrade, db.session)

            assert _annual_of(profile.id) == Decimal("52000.00")

    def test_an_owner_with_two_pay_eras_refuses_the_downgrade(
        self, app, db, seed_user, seed_periods,
    ):
        """The yearly figure multiplies by one era's count; two are refused, not guessed."""
        with app.app_context():
            migration = load_migration_module(_MIGRATION)
            profile = make_salary_profile(seed_user, db.session, name="Day job")
            pay_era_write.mint_era(
                seed_user["user"].id, era_of(date(2026, 6, 5), 7),
            )
            db.session.commit()

            with pytest.raises(RuntimeError) as excinfo:
                run_migration_callable(migration.downgrade, db.session)
            db.session.rollback()

            assert f"profile {profile.id} (Day job): 2 pay eras" in str(
                excinfo.value,
            )
            assert _pay_list_exists()
            assert not _annual_column_exists()


class TestTheRevision:
    """Where the revision sits and what it owes the rules."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration onto R5-a's head."""
        migration = load_migration_module(_MIGRATION)
        assert migration.revision == "70680a4a7405"
        assert migration.down_revision == "1c569c51b449"

    def test_it_is_reviewed(self):
        """Destructive DDL (a column and a CHECK dropped) carries the ``Review:`` line the rules require."""
        assert "Review: Josh, 2026-09-25" in load_migration_module(_MIGRATION).__doc__


class TestTheTable:
    """The model and the migrated table agree, and the table keeps its rules."""

    def test_autogenerate_sees_no_drift_on_the_tables(self, app):
        """The migrated pay list and profile tables match the models, defaults included."""
        scoped = {("salary", "pay_entries"), ("salary", "salary_profiles")}
        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or (obj.schema, name) in scoped
                    ),
                },
            )
            assert compare_metadata(ctx, db.metadata) == []

    def test_the_model_names_every_check_the_table_holds(self, app):
        """The CHECKs by name, model against ``pg_constraint``."""
        with app.app_context():
            model_checks = {
                constraint.name
                for constraint in SalaryPayEntry.__table__.constraints
                if isinstance(constraint, sqlalchemy.CheckConstraint)
            }
            table_checks = {
                row[0] for row in db.session.execute(sqlalchemy.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'salary.pay_entries'::regclass "
                    "AND contype = 'c'"
                ))
            }
            assert model_checks == table_checks == {
                "ck_pay_entries_positive_amount",
                "ck_pay_entries_version_id_positive",
            }

    @pytest.mark.parametrize("amount", [Decimal("0.00"), Decimal("-1.00")])
    def test_an_amount_not_above_zero_is_refused(
        self, app, db, seed_user, seed_periods, amount,
    ):
        """``ck_pay_entries_positive_amount``: a paycheck pays something."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            db.session.add(SalaryPayEntry(
                salary_profile_id=profile.id,
                payday=seed_periods[2].start_date,
                amount=amount,
            ))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "ck_pay_entries_positive_amount" in str(excinfo.value)

    def test_a_second_entry_on_one_payday_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """``uq_pay_entries_profile_payday``: one pay per payday per profile."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            db.session.add(SalaryPayEntry(
                salary_profile_id=profile.id,
                payday=seed_periods[0].start_date,
                amount=Decimal("2100.00"),
            ))
            with pytest.raises(IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert "uq_pay_entries_profile_payday" in str(excinfo.value)
