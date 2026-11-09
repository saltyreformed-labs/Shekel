"""The cutover to the per-raise end year -- plan step **salary:S3-c**.

The second half of ruling **R-SAL11**: plan step salary:S3-b added
``salary.salary_raises.terminal_year`` and wrote no value; this step carries
each owner's ``auth.user_settings.merit_raise_horizon_years`` onto their raise
rows, deletes that setting, and deletes the in-memory fabrication
(``pension_calculator._terminate_after_horizon``) that would otherwise
overwrite the stored value on every ``/retirement`` render.

**The BACKFILL is the money and it is EXECUTED here, not inspected.**  A
migration's raw SQL is the one part a recorder cannot grade: the recorder sees
that an ``execute`` happened and nothing about what it did.  So
:class:`TestTheBackfillCarriesEachOwnersHorizon` restores the dropped column
into the test's own database, seeds the shapes the statement discriminates
between, RUNS the migration module's own ``_BACKFILL`` text, and reads the
rows back.  DDL is safe to do here because ``conftest``'s ``db`` fixture
clones a database per test and drops it afterwards, so the ``ACCESS
EXCLUSIVE`` lock this takes is on a database nothing else can see.  *Plan step
salary:S3-b's own migration class states the opposite rule and is still right
for what it does: it drives ``upgrade``/``downgrade`` through a recorder
because those are pure DDL, and DDL is what the recorder can grade
completely.*

The backfill's cases are its clauses, one test each, because a statement
that is right about all but one of them silently moves money on that one.

:class:`TestTheGlobalSettingIsGone` reads the live catalog rather than the
model, for the reason ``test_c25_column_invariants`` exists: a model and a
migration drift apart silently, and this one is a DROP, so the direction that
matters is a column that survived the migration.

:class:`TestARaiseStopsBadgingAfterItsEndYear` covers the obligation this step
INHERITED from salary:S3-a rather than created -- ``get_raise_event`` walks
the same rows to badge a raise on the salary surfaces and knew nothing about
termination, so it went on announcing a raise every year after the field had
already stopped moving the gross.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.user import UserSettings
from app.services.salary_raises import get_raise_event
from tests._test_helpers import load_migration_module, start_test_pay_list

MIGRATION = "d4e8b1c62f07_the_end_year_moves_onto_the_raise.py"

#: The year the backfill is told to reckon from.  Fixed rather than read from
#: the clock so every expected cutoff below is a literal: the migration itself
#: passes ``date.today().year``, and which year that is is not what these
#: cases are about.
BASE_YEAR = 2026

#: The horizon the seeded owner has stored, and the cutoff it implies.
#:
#: **It is deliberately NOT 5**, and an adversarial review of this step is
#: why.  It was, and so was :data:`DEFAULT_HORIZON` -- so
#: ``test_a_recurring_non_cola_raise_takes_the_owners_cutoff`` and
#: ``test_an_owner_with_no_settings_row_takes_the_default_horizon`` asserted
#: the identical number for opposite conditions, and deleting the
#: ``LEFT JOIN auth.user_settings`` from the statement altogether left every
#: case in this file green: ``COALESCE(..., 5)`` alone reproduced them.  The
#: one thing the migration exists to do -- carry EACH OWNER's stored value
#: onto THEIR rows -- was the one thing ungraded.
HORIZON = 7
CUTOFF = BASE_YEAR + HORIZON  # 2033

#: What ``retirement_dashboard_service._resolve_merit_horizon`` answered for
#: an owner with no settings row, which the backfill's ``COALESCE`` preserves.
#: Distinct from :data:`HORIZON` so the two cases cannot agree by accident.
DEFAULT_HORIZON = 5


def _profile(seed_user, name="S3-c") -> SalaryProfile:
    """Return a committed salary profile to hang raises off."""
    single_id = (
        db.session.query(FilingStatus).filter_by(name="single").one().id
    )
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=single_id,
        name=name,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("3525.96"))  # $91,675.00 a year / 26
    db.session.commit()
    return profile


def _raise(profile, *, raise_type=RaiseTypeEnum.MERIT, effective_year=2027,
           recurring=True, terminal_year=None) -> SalaryRaise:
    """Commit one 2.5% January raise of the requested shape."""
    row = SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=ref_cache.raise_type_id(raise_type),
        effective_date=date(effective_year, 1, 1),
        percentage=Decimal("0.0250"),
        is_recurring=recurring,
        terminal_year=terminal_year,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _restore_the_deleted_setting(user_id, horizon):
    """Put ``merit_raise_horizon_years`` back, holding *horizon* for *user_id*.

    The migration under test DROPS this column, and the test template is built
    by running the Alembic chain to head -- so by the time any test runs, the
    column the backfill READS is already gone.  Restoring it is the only way
    to execute the statement at all, and executing it is the whole point.

    Args:
        user_id: The owner whose settings row carries *horizon*.
        horizon: The stored merit horizon in years.
    """
    db.session.execute(text(
        "ALTER TABLE auth.user_settings "
        "ADD COLUMN merit_raise_horizon_years INTEGER NOT NULL DEFAULT 5"
    ))
    db.session.execute(
        text(
            "UPDATE auth.user_settings SET merit_raise_horizon_years = :h "
            "WHERE user_id = :uid"
        ),
        {"h": horizon, "uid": user_id},
    )


def _restore_the_raise_year():
    """Put ``salary_raises.effective_year`` back, filled from each raise's start date.

    The statement under test reads ``r.effective_year``, a column migration
    ``6740ce13e174`` (plan step salary plan#3048) later replaced with
    ``effective_date`` -- the same reason :func:`_restore_the_deleted_setting`
    exists, one revision on.  Each raise's year is its start date's, which is
    exactly what the column held before the date replaced it.  Restoring is
    idempotent, because a case runs the statement twice.
    """
    db.session.execute(text(
        "ALTER TABLE salary.salary_raises ADD COLUMN IF NOT EXISTS effective_year INTEGER"
    ))
    db.session.execute(text(
        "UPDATE salary.salary_raises "
        "SET effective_year = EXTRACT(YEAR FROM effective_date)::INT"
    ))


def _run_backfill(base_year=BASE_YEAR):
    """Execute the migration module's own backfill statement."""
    module = load_migration_module(MIGRATION)
    _restore_the_raise_year()
    db.session.execute(
        module._BACKFILL.bindparams(  # pylint: disable=protected-access
            base_year=base_year,
            default_years=module._DEFAULT_HORIZON_YEARS,  # pylint: disable=protected-access
        )
    )
    db.session.commit()


def _terminal_year_of(raise_id):
    """Read one raise's end year straight out of the storage tier."""
    return db.session.execute(
        text("SELECT terminal_year FROM salary.salary_raises WHERE id = :i"),
        {"i": raise_id},
    ).scalar()


class TestTheBackfillCarriesEachOwnersHorizon:
    """The UPDATE that moves the setting onto the rows, run for real.

    Every case seeds ONE raise shape, runs the statement, and reads the row
    back.  ``_restore_the_deleted_setting`` is called first in each, because
    the column the statement reads no longer exists on a migrated database.

    **The pylint disables below are deliberate and narrow.**

    Pylint: ``protected-access`` (2) -- ``_BACKFILL`` and
    ``_DEFAULT_HORIZON_YEARS`` are module-private to the migration, and
    reaching them is what makes this test grade the SHIPPED statement rather
    than a copy of it retyped here.  A retyped copy is the failure mode this
    file exists to avoid: it would pass while the migration ran something
    else.
    """

    def test_a_recurring_non_cola_raise_takes_the_owners_cutoff(
        self, app, seed_user,
    ):
        """The central case: 2026 + a stored horizon of 5 is 2031.

        This is the developer's own merit raise (2.5%, effective 2027-01)
        under his own stored horizon, and 2031 is exactly what
        ``pension_calculator._terminate_after_horizon`` computed in memory for
        it on every render before this step.  Preserving that number is the
        migration's whole claim to move no figure.
        """
        with app.app_context():
            profile = _profile(seed_user)
            row = _raise(profile)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)

            _run_backfill()

            assert _terminal_year_of(row.id) == CUTOFF

    def test_a_recurring_cola_raise_keeps_no_end_year(self, app, seed_user):
        """A COLA is believed indefinitely, and the backfill must not end it.

        ``_terminate_after_horizon`` terminated a recurring cola at ``None``
        -- inflation does not stop at a planning horizon -- so writing a
        cutoff here would END a raise the app never ended, and every year past
        it would read a smaller salary than the page showed the day before.
        """
        with app.app_context():
            profile = _profile(seed_user)
            row = _raise(profile, raise_type=RaiseTypeEnum.COLA)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)

            _run_backfill()

            assert _terminal_year_of(row.id) is None

    def test_a_one_time_raise_keeps_no_end_year(self, app, seed_user):
        """A one-time raise cannot carry one, and the CHECK would refuse it.

        ``ck_salary_raises_terminal_year_only_on_a_recurring_raise`` makes
        this a migration that FAILS rather than a figure that moves, if the
        ``is_recurring`` clause is ever dropped -- so this case is a guard on
        the deploy as much as on the arithmetic.
        """
        with app.app_context():
            profile = _profile(seed_user)
            row = _raise(profile, recurring=False)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)

            _run_backfill()

            assert _terminal_year_of(row.id) is None

    def test_a_raise_that_already_states_its_end_year_is_left_alone(
        self, app, seed_user,
    ):
        """Idempotence, and the reason ``downgrade`` need not clear anything.

        The statement writes only where ``terminal_year IS NULL``, so running
        it twice -- or running ``upgrade`` again after a ``downgrade`` -- can
        never overwrite a year the owner set by hand in between.
        """
        with app.app_context():
            profile = _profile(seed_user)
            row = _raise(profile, terminal_year=2040)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)

            _run_backfill()
            _run_backfill()

            assert _terminal_year_of(row.id) == 2040

    def test_a_raise_starting_after_the_cutoff_keeps_no_end_year(
        self, app, seed_user,
    ):
        """The clause that CHANGES behaviour, and the one that must not fail.

        A recurring raise effective 2035 under a 2031 cutoff was handed a
        terminal year BEFORE its own effective year, contributed zero
        applications, and so never happened at all -- measurement 3 of ruling
        **R-SAL11**, the defect the column exists to make unrepresentable.
        Writing 2031 here would violate
        ``ck_salary_raises_terminal_year_not_before_effective`` and abort the
        deploy; leaving it ``NULL`` believes the raise as recorded, which is
        the ruling's direction.
        """
        with app.app_context():
            profile = _profile(seed_user)
            row = _raise(profile, effective_year=CUTOFF + 4)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)

            _run_backfill()

            assert _terminal_year_of(row.id) is None

    def test_each_owner_takes_their_OWN_horizon(
        self, app, seed_user, seed_second_user,
    ):
        """Two owners, two horizons, and neither raise takes the other's.

        **The statement writes across a multi-tenant table with no
        ``WHERE user_id``** -- its whole per-owner correctness is the
        ``LEFT JOIN auth.user_settings AS s ON s.user_id = p.user_id`` and
        the ``r.salary_profile_id = h.profile_id`` beside it.  Every other
        case here seeds ONE owner, so a defect that wrote one owner's cutoff
        onto everyone's raises would pass all of them.  This is the case that
        fails.
        """
        with app.app_context():
            mine = _profile(seed_user, name="mine")
            my_raise = _raise(mine)
            theirs = _profile(seed_second_user, name="theirs")
            their_raise = _raise(theirs)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)
            db.session.execute(
                text(
                    "UPDATE auth.user_settings SET merit_raise_horizon_years "
                    "= :h WHERE user_id = :uid"
                ),
                {"h": 3, "uid": seed_second_user["user"].id},
            )

            _run_backfill()

            assert _terminal_year_of(my_raise.id) == CUTOFF          # 2033
            assert _terminal_year_of(their_raise.id) == BASE_YEAR + 3  # 2029

    def test_an_owner_with_no_settings_row_takes_the_default_horizon(
        self, app, seed_user,
    ):
        """``COALESCE(..., 5)`` preserves what a settings-less owner projected.

        ``retirement_dashboard_service._resolve_merit_horizon`` returned
        ``_DEFAULT_MERIT_HORIZON_YEARS`` (5) when ``load_gap_inputs``' ``.first()``
        found no row, so such an owner's page showed a cutoff of ``year + 5``
        like everyone else's.  An INNER JOIN would skip them and hand them
        "believed forever" instead -- a silent move in the money-increasing
        direction, on the owners least likely to notice.
        """
        with app.app_context():
            profile = _profile(seed_user)
            row = _raise(profile)
            _restore_the_deleted_setting(seed_user["user"].id, HORIZON)
            db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            _run_backfill()

            assert _terminal_year_of(row.id) == BASE_YEAR + DEFAULT_HORIZON


class TestTheMigrationDropsTheSettingAndCanRestoreIt:
    """What ``upgrade`` and ``downgrade`` CALL, through a recorder.

    The same pattern plan step salary:S3-b used and for the same reason: a
    constraint or a column named in the module and never acted on reads as
    though the migration handled it, and CI runs only the upgrade direction
    (it builds the test template by running the chain to head), so the
    downgrade path has no other coverage in the suite.
    """

    class _Recorder:
        """Stand-in for ``alembic.op`` recording calls instead of running them."""

        def __init__(self):
            """Start with an empty call log."""
            self.calls: "list[tuple]" = []

        def execute(self, statement):
            """Record one raw statement, as its SQL text."""
            self.calls.append(("execute", str(statement)))

        def add_column(self, table, column, schema=None):
            """Record one ADD COLUMN."""
            self.calls.append(
                ("add_column", schema, table, column.name, column.nullable,
                 str(column.server_default.arg)),
            )

        def create_check_constraint(self, name, table, condition, schema=None):
            """Record one CREATE CHECK."""
            self.calls.append(("create_check", schema, table, name, condition))

        def drop_constraint(self, name, table, type_=None, schema=None):
            """Record one DROP CONSTRAINT."""
            self.calls.append(("drop_constraint", schema, table, name, type_))

        def drop_column(self, table, column, schema=None):
            """Record one DROP COLUMN."""
            self.calls.append(("drop_column", schema, table, column))

    def _drive(self, monkeypatch, direction):
        """Call *direction* on the migration with a recording ``op``."""
        module = load_migration_module(MIGRATION)
        recorder = self._Recorder()
        monkeypatch.setattr(module, "op", recorder)
        getattr(module, direction)()
        return recorder.calls

    def test_upgrade_backfills_before_it_drops_what_the_backfill_reads(
        self, monkeypatch,
    ):
        """Order is the assertion: the UPDATE must precede the DROP COLUMN.

        The statement reads ``auth.user_settings.merit_raise_horizon_years``.
        Dropping the column first would not fail loudly in review -- it fails
        in the deploy, against production data, after the point where the
        owners' horizons still exist to be read.
        """
        calls = self._drive(monkeypatch, "upgrade")
        kinds = [call[0] for call in calls]

        assert kinds == ["execute", "drop_constraint", "drop_column"], (
            f"upgrade's call sequence changed: {kinds}"
        )
        assert "merit_raise_horizon_years" in calls[0][1], (
            "upgrade's first statement does not read the horizon column, so "
            "whatever it backfills from, it is not the setting being deleted"
        )
        assert calls[1] == (
            "drop_constraint", "auth", "user_settings",
            "ck_user_settings_valid_merit_horizon", "check",
        )
        assert calls[2] == (
            "drop_column", "auth", "user_settings",
            "merit_raise_horizon_years",
        )

    def test_downgrade_restores_the_column_and_its_check(self, monkeypatch):
        """The column comes back NOT NULL at 5, with its 0-50 CHECK.

        It is value-lossy in the only way it can be -- many rows map back to
        one setting -- and deliberately does NOT clear ``terminal_year``,
        which would destroy a belief the owner may have edited since and
        which it has no marker to tell from one this migration wrote.

        **It is therefore state-lossy rather than figure-neutral, and an
        adversarial review of this step corrected a draft of this docstring
        that claimed the stronger thing.**  Restoring
        ``_terminate_after_horizon`` overwrites every stored end year on the
        PENSION path only; the paycheck engine reads ``terminal_year`` off
        the row and always did, so after a downgrade it sees the backfilled
        year where before the upgrade it saw ``NULL``.  A state-lossless
        downgrade needs a manual ``UPDATE salary.salary_raises SET
        terminal_year = NULL`` beside it.  This test grades the CALL LIST and
        nothing about that; the sentence is here because it is what the call
        list does not say.
        """
        calls = self._drive(monkeypatch, "downgrade")

        assert calls == [
            ("add_column", "auth", "user_settings",
             "merit_raise_horizon_years", False, "5"),
            ("create_check", "auth", "user_settings",
             "ck_user_settings_valid_merit_horizon",
             "merit_raise_horizon_years >= 0 AND "
             "merit_raise_horizon_years <= 50"),
        ], f"downgrade's calls changed: {calls}"


class TestTheGlobalSettingIsGone:
    """No second home for the belief survives, in the model or the catalog."""

    def test_the_column_is_absent_from_the_live_catalog(self, app):
        """PostgreSQL, not the model, is asked.

        A migration and a model drift apart silently in BOTH directions, and
        the one that matters for a DROP is a column the migration failed to
        remove: the model would stop naming it, every test would pass, and the
        owner's stale horizon would sit in the database as a second answer to
        a question the raise rows now own.
        """
        with app.app_context():
            found = db.session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'auth' AND table_name = 'user_settings' "
                "AND column_name = 'merit_raise_horizon_years'"
            )).scalar()
            assert found is None, (
                "auth.user_settings.merit_raise_horizon_years still exists in "
                "the database; ruling R-SAL11 deletes it, and while it is "
                "there the app has two homes for one belief"
            )

    def test_the_model_no_longer_declares_it(self, app):
        """And the ORM class does not carry it either.

        Both halves are needed: the catalog check above passes on a tree whose
        model still declares the attribute (SQLAlchemy would simply fail at
        query time), and a model check alone passes on a database where the
        DROP never ran.
        """
        with app.app_context():
            assert "merit_raise_horizon_years" not in (
                UserSettings.__table__.columns
            )


class TestARaiseStopsBadgingAfterItsEndYear:
    """``get_raise_event`` honours the end year (the INHERITED obligation).

    Plan step salary:S3-a named this walk as the one that did not know about
    termination and left nothing enforcing it.  It badges a raise on the
    salary surfaces -- the paycheck engine's ``PeriodInfo`` and the cockpit's
    one-banner-per-run collapse -- so before this step a raise whose end year
    had already stopped moving the gross went on announcing itself every
    January for as long as the projection ran.
    """

    class _Period:
        """The one attribute ``get_raise_event`` reads off a period."""

        def __init__(self, start_date):
            """Pin the period's start date."""
            self.start_date = start_date

    def test_a_recurring_raise_badges_through_its_end_year(
        self, app, seed_user,
    ):
        """Every January from the effective year through the end year.

        The direction that must NOT break: an end year is not a reason to
        stop announcing a raise that is still applying.
        """
        with app.app_context():
            profile = _profile(seed_user)
            _raise(profile, effective_year=2027, terminal_year=2031)
            db.session.refresh(profile)

            for year in (2027, 2029, 2031):
                assert get_raise_event(
                    profile.raises, self._Period(date(year, 1, 15)),
                ), f"no raise event badged in {year}, which is within its run"

    def test_a_recurring_raise_stops_badging_after_its_end_year(
        self, app, seed_user,
    ):
        """2032 is past 2031, so nothing is announced.

        The obligation itself.  The raise walk stops accruing this raise's
        applications after 2031 -- ``apply_raises`` did from plan step
        salary:S3-b, and since salary:X-av-3a the pay list's walk does through
        ``applications_between`` -- so a badge here is a banner on a paycheck
        the raise did not move: the two walks reading one column and
        disagreeing about it.
        """
        with app.app_context():
            profile = _profile(seed_user)
            _raise(profile, effective_year=2027, terminal_year=2031)
            db.session.refresh(profile)

            assert get_raise_event(
                profile.raises, self._Period(date(2032, 1, 15)),
            ) == "", (
                "get_raise_event badged a raise in 2032 whose last believed "
                "year is 2031; the raise walk stopped applying it, so the "
                "surfaces now announce a raise that moves no money"
            )

    def test_a_raise_with_no_end_year_badges_indefinitely(
        self, app, seed_user,
    ):
        """The mutation control: without an end year nothing is suppressed.

        If the clause were written to suppress unconditionally, the case above
        would still pass; this is what fails.
        """
        with app.app_context():
            profile = _profile(seed_user)
            _raise(profile, effective_year=2027, terminal_year=None)
            db.session.refresh(profile)

            assert get_raise_event(profile.raises, self._Period(date(2040, 1, 15)))


#: The tightest end year ``ck_salary_raises_terminal_year_not_before_
#: effective`` permits: the raise's own effective year.
_BOUNDARY_YEAR = 2027


def test_a_raise_believed_only_in_its_first_year_still_badges_there(
    app, seed_user,
):
    """The boundary the ordering CHECK makes the tightest legal one.

    ``terminal_year == effective_year`` is the smallest value
    ``ck_salary_raises_terminal_year_not_before_effective`` permits, and it is
    what the S3-c form writes for a raise the owner believes happens once and
    then stops.  An off-by-one in either walk shows up here first: an
    exclusive comparison would badge nothing at all and apply nothing at all,
    which reads as a raise the owner recorded and the app never saw.
    """
    with app.app_context():
        profile = _profile(seed_user)
        _raise(
            profile, effective_year=_BOUNDARY_YEAR,
            terminal_year=_BOUNDARY_YEAR,
        )
        db.session.refresh(profile)

        class _Period:  # pylint: disable=too-few-public-methods
            """Pylint: ``too-few-public-methods`` -- a one-attribute stub is
            the whole of what ``get_raise_event`` reads off a period."""

            start_date = date(_BOUNDARY_YEAR, 1, 15)

        assert get_raise_event(profile.raises, _Period())
