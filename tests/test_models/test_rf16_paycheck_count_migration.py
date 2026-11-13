"""Tests for the R-F16 migration ``f2b7c40d918e``, in both directions.

The migration drops ``salary.salary_profiles.pay_periods_per_year`` and its
``ck_salary_profiles_positive_periods`` CHECK, because the paycheck count is
derived from ``budget.pay_schedule.cadence_days`` and a second stored answer is
what finding **F-16** is.

Four invariants, and each is here because prose could not hold it:

  1. **The downgrade restores the DERIVED count**, not the default.  The value
     is exactly recoverable -- ``round(365.2425 / cadence_days)`` is what the
     application uses either side of the migration -- so the downgrade
     backfills rather than refusing, and a developer stepping back reaches a
     database the OLD code prices correctly.
  2. **An owner with no cadence keeps the column default.**  There is nothing
     to derive from, and the ``ADD COLUMN`` is ``NOT NULL``, so the server
     default has to be what such a row lands on or the downgrade fails.
  3. **A round trip is a fixed point.**  Down then up leaves the schema where
     it started; the pair is re-runnable rather than one-shot.
  4. **The upgrade REPORTS a disagreeing row rather than refusing it.**  A row
     whose stored count disagreed with its owner's cadence is an owner whose
     modelled income was WRONG, and this migration is what corrects it, so
     refusing would leave them broken.  The warning names both values, which
     is the only record that the correction happened.

The Alembic ``MigrationContext`` bootstrap mirrors
``test_c41_baseline_unique_migration.py``, which is this repository's
established pattern for driving a migration's own callables from a test.
"""
# pylint: disable=redefined-outer-name,unused-argument
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern.
# ``unused-argument`` is unavoidable for ``restore_dropped_column``, which
# yields nothing and is requested purely so its teardown reaches the cleanup
# phase even when the body raises -- without it a failed test would leave the
# column in place and every later test in the same per-worker database would
# run against a schema that is not head.
from __future__ import annotations

import importlib.util
import logging
import pathlib
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from app.extensions import db as _db
from app.models.pay_schedule import PaySchedule
from app.services import pay_era_write, pay_schedule_service
from tests._test_helpers import (
    restore_pay_period_derived_columns,
    start_test_pay_list,
)
from app.models.salary_profile import SalaryProfile

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a module (no ``__init__.py`` there)."""
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_RF16 = _load_migration(
    "f2b7c40d918e_drop_salary_profiles_pay_periods_per_year.py"
)

#: Plan step ``pay_calendar:C4-b-2``, 84 revisions ABOVE ``f2b7c40d918e``.
#:
#: It adds ``fk_pay_periods_schedule``, which makes "paydays with no
#: ``budget.pay_schedule`` row" unstorable -- the exact input R-F16's own
#: downgrade backfill exists to handle.  Loaded here so
#: :func:`without_the_schedule_key` can put the schema where R-F16's downgrade
#: ACTUALLY runs, rather than where a shortcut would run it.
_M_C4B2 = _load_migration(
    "f1c8b3d5e920_an_owner_with_paydays_has_a_cadence.py"
)


def _column_present(session) -> bool:
    """Whether ``salary_profiles.pay_periods_per_year`` exists right now."""
    return bool(session.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'salary' AND table_name = 'salary_profiles' "
        "  AND column_name = 'pay_periods_per_year'"
    )).fetchall())


def _run(callable_, session):
    """Run one of the migration's callables against the test connection."""
    ctx = MigrationContext.configure(connection=session.connection())
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=session.connection()):
            callable_()
    session.commit()


@pytest.fixture
def restore_dropped_column(db):
    """Leave the schema at head however the test body ends.

    The per-worker database is built by running the migrations, so it starts
    WITHOUT the column.  A test that downgrades and then fails would otherwise
    hand every later test in that worker a schema one revision behind -- which
    is silent, because the ORM model has no such attribute to trip over.
    """
    yield
    if _column_present(db.session):
        db.session.execute(text(
            "ALTER TABLE salary.salary_profiles "
            "DROP CONSTRAINT IF EXISTS ck_salary_profiles_positive_periods"
        ))
        db.session.execute(text(
            "ALTER TABLE salary.salary_profiles "
            "DROP COLUMN IF EXISTS pay_periods_per_year"
        ))
        db.session.commit()


@pytest.fixture
def with_the_pay_period_derived_columns(db):
    """Run the schema DOWN to where R-F16's downgrade can read what it reads.

    R-F16's ``downgrade()`` derives the paycheck count it restores from
    ``(pp.end_date - pp.start_date) + 1``, and plan step ``pay_calendar:C4-c``
    dropped ``budget.pay_periods.end_date``.  Alembic downgrades run
    newest-first, so by the time this revision's own ``downgrade()`` executes,
    C4-c's has already put the column back -- and this fixture is that step,
    driven through C4-c's shipped callable for the same reason
    :func:`without_the_schedule_key` drives C4-b-2's: a hand-written
    ``ADD COLUMN`` would be a second statement of the schema that could drift
    from the migration in silence.

    It restores nothing afterwards, for the reason that fixture gives: the
    ``db`` fixture re-clones the per-worker database for every test.
    """
    restore_pay_period_derived_columns(db.session)
    yield


@pytest.fixture
def without_the_schedule_key(db, with_the_pay_period_derived_columns):

    """Run the schema DOWN to where R-F16's downgrade actually meets it.

    Plan step ``pay_calendar:C4-b-2`` added ``fk_pay_periods_schedule``, so an
    owner with paydays and no ``budget.pay_schedule`` row -- the one input
    R-F16's downgrade backfill has a branch for -- is unstorable at head.  That
    does NOT make the branch dead: Alembic downgrades run newest-first, so by
    the time R-F16's own ``downgrade()`` executes, C4-b-2's has already dropped
    the key, and the state is representable again.

    **It runs C4-b-2's own ``downgrade()`` rather than hand-issuing an
    ``ALTER TABLE``**, and that is the load-bearing part: a hand-written DROP
    would be a second statement of what that revision does and could drift from
    it in silence, where calling the shipped callable turns "the reverse chain
    order is safe" from an argument in a docstring into something this suite
    measures.

    **It restores nothing, deliberately.**  The ``db`` fixture clones the
    per-worker database for EVERY test and drops it when the test ends
    (``setup_clone_template``, then ``teardown``), so a schema left short of
    head cannot reach the next test.  The round trip is worth asserting on its own terms rather than
    hiding in a teardown, and it is:
    ``tests/test_models/test_c4b2_pay_period_schedule_key.py`` runs
    ``downgrade`` then ``upgrade`` and checks the key comes back.
    """
    _run(_M_C4B2.downgrade, db.session)
    yield


def _profile(db, seed_user, name="R-F16"):
    """Add and flush a salary profile for the seeded owner."""
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=1,
        name=name,
        state_code="NC",
    )
    db.session.add(profile)
    db.session.flush()
    start_test_pay_list(profile, Decimal("3525.96"))  # $91,675.00 a year / 26
    return profile


def _set_cadence(db, user_id, cadence_days):
    """Force the owner's persisted cadence on the REWOUND schedule row.

    Every case that calls this runs under
    :func:`with_the_pay_period_derived_columns`, whose rewind also runs plan
    step ``pay_calendar:C17-a``'s downgrade -- putting ``cadence_days`` back
    on ``budget.pay_schedule`` (from the owner's latest era) and dropping
    ``budget.pay_eras``.  R-F16's downgrade reads the ROW's column, so that is
    what this writes, by SQL: the head mapper no longer declares it.  The
    seeded owner always holds the row (``record_paydays`` created it), so
    there is no create arm left; a case for an owner with no row deletes it
    explicitly.
    """
    db.session.execute(
        text("UPDATE budget.pay_schedule SET cadence_days = :cadence "
             "WHERE user_id = :uid"),
        {"cadence": cadence_days, "uid": user_id},
    )
    db.session.flush()


def _stored_count(db, profile_id):
    """Read the restored column straight out of the table."""
    return db.session.execute(
        text(
            "SELECT pay_periods_per_year FROM salary.salary_profiles "
            "WHERE id = :pid"
        ),
        {"pid": profile_id},
    ).scalar()


class TestTheDowngradeRestoresWhatTheApplicationUses:
    """Down-migrating gives the old code the count it would have derived."""

    @pytest.mark.parametrize("cadence_days,expected", [
        (7, 52), (14, 26), (15, 24), (30, 12), (365, 1),
    ])
    def test_the_restored_count_is_the_derived_one(
        self, app, db, seed_user, restore_dropped_column,
        with_the_pay_period_derived_columns, cadence_days, expected,
    ):
        """Downgrade backfills ``round(365.2425 / cadence_days)``.

        Input: an owner at each authorable cadence, with a salary profile.
        Expected: the re-added column holds the count that cadence derives.
        Why: the downgrade is a developer's step-back path, and restoring the
        column default (26) for a weekly-paid owner would hand the old code the
        exact mismatch F-16 is -- a rollback that BREAKS the money it was
        rolling back to protect.
        """
        with app.app_context():
            profile = _profile(db, seed_user, name=f"Cadence {cadence_days}")
            _set_cadence(db, seed_user["user"].id, cadence_days)
            db.session.commit()

            _run(_M_RF16.downgrade, db.session)

            assert _column_present(db.session)
            assert _stored_count(db, profile.id) == expected

    def test_an_owner_with_periods_but_no_schedule_row_is_reached(
        self, app, db, seed_user, restore_dropped_column,
        without_the_schedule_key,
    ):
        """The backfill INFERS a cadence from the last period, as the app does.

        Input: a legacy owner with 7-day pay periods and no
        ``budget.pay_schedule`` row -- the state the pre-C4-b-2
        :func:`app.services.pay_schedule_service.resolve_cadence` handled by
        reading the last period's stored length.
        Expected: 52, not the column default.
        Why: joining ``budget.pay_schedule`` alone would silently give this
        owner 26 while the pre-migration application was pricing them on 52 --
        a downgrade that HALVES their modelled paycheck, which is the exact
        failure the restore exists to prevent. R-F16's adversarial review
        caught the join.

        **``without_the_schedule_key`` is what makes this input constructible,
        and it is not a workaround** (plan step ``pay_calendar:C4-b-2``).  That
        step forbids this owner at HEAD, and R-F16's backfill still has to
        answer for them -- because a database sitting below C4-b-2 can hold
        one, and because the downgrade chain drops the key 84 revisions before
        this ``downgrade()`` runs.  The fixture reproduces that order using
        C4-b-2's own callable, so this case now grades the reverse chain order
        as well as the backfill.  The alternative -- deleting the case -- would
        have retired a branch that is still live for exactly the databases a
        downgrade exists for.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _profile(db, seed_user, name="Legacy weekly")
            # The era is a second child of the row since plan step C17-a
            # (``fk_pay_eras_schedule``); it goes before the parent too.
            pay_era_write.retire_eras(user_id, ())
            db.session.query(PaySchedule).filter_by(user_id=user_id).delete()
            # A 7-day period: its stored end is start + (cadence - 1).
            db.session.execute(
                text("DELETE FROM budget.pay_periods WHERE user_id = :uid"),
                {"uid": user_id},
            )
            db.session.execute(
                text(
                    "INSERT INTO budget.pay_periods "
                    "(user_id, start_date, end_date, period_index) "
                    "VALUES (:uid, DATE '2026-01-02', DATE '2026-01-08', 0)"
                ),
                {"uid": user_id},
            )
            db.session.commit()

            _run(_M_RF16.downgrade, db.session)

            assert _stored_count(db, profile.id) == 52

    def test_an_owner_with_no_cadence_keeps_the_default(
        self, app, db, seed_user, restore_dropped_column,
        with_the_pay_period_derived_columns,
    ):
        """A schedule-less owner lands on the column's own server default.

        Input: a profile whose owner has no ``budget.pay_schedule`` row and no
        pay period to infer one from.
        Expected: 26, the ``ADD COLUMN`` default.
        Why: the re-added column is ``NOT NULL``, so a row the backfill cannot
        reach must already hold a legal value or the downgrade fails partway
        through -- the state a step-back least wants to be in.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            # The profile is built while the owner still has a payday, since
            # its first pay entry must be dated on one (plan step
            # salary:X-av-3a); the entry references no pay period, so the
            # deletes below leave the state this case names.
            profile = _profile(db, seed_user, name="No schedule")
            # PERIODS FIRST, then the schedule row.  Since plan step
            # ``pay_calendar:C4-b-2`` that order is the only legal one:
            # ``fk_pay_periods_schedule`` is ``ON DELETE RESTRICT``, so
            # removing the parent under live children is refused.  It is also
            # the order this teardown always meant -- the owner being built is
            # somebody who has never generated a schedule, and they lose both.
            db.session.execute(
                text("DELETE FROM budget.pay_periods WHERE user_id = :uid"),
                {"uid": user_id},
            )
            # The era is a second child of the row since plan step C17-a
            # (``fk_pay_eras_schedule``); it goes before the parent too.
            pay_era_write.retire_eras(user_id, ())
            db.session.query(PaySchedule).filter_by(user_id=user_id).delete()
            db.session.commit()

            _run(_M_RF16.downgrade, db.session)

            assert _stored_count(db, profile.id) == 26


class TestTheRoundTripIsAFixedPoint:
    """Down then up leaves the schema exactly where it started."""

    def test_down_then_up_drops_the_column_and_its_check(
        self, app, db, seed_user, restore_dropped_column,
        with_the_pay_period_derived_columns,
    ):
        """The pair is re-runnable, and the CHECK travels with the column.

        Why: a CHECK left behind on a dropped column is what makes the NEXT
        downgrade fail -- ``create_check_constraint`` would collide on the
        name.  Asked of ``pg_constraint`` rather than of the model.
        """
        with app.app_context():
            _profile(db, seed_user, name="Round trip")
            _set_cadence(db, seed_user["user"].id, 14)
            db.session.commit()

            _run(_M_RF16.downgrade, db.session)
            assert _column_present(db.session)
            assert db.session.execute(text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_salary_profiles_positive_periods'"
            )).fetchall()

            _run(_M_RF16.upgrade, db.session)
            assert not _column_present(db.session)
            assert db.session.execute(text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_salary_profiles_positive_periods'"
            )).fetchall() == []


class TestTheUpgradeReportsWhatItCorrects:
    """A disagreeing row is named in the log, and is not a refusal."""

    def test_a_disagreeing_row_is_reported_and_dropped_anyway(
        self, app, db, seed_user, restore_dropped_column, caplog,
        with_the_pay_period_derived_columns,
    ):
        """26 stored beside a 7-day cadence logs the correction and proceeds.

        Input: the exact F-16 shape -- a stored 26 on an owner paid weekly.
        Expected: a WARNING naming both counts, and the column still dropped.
        Why: such an owner's modelled income was DOUBLE their real one, and
        this migration is the correction; refusing would leave them broken and
        block the release that fixes them.  The warning is the only record that
        a figure moved, which is what a MOVES MONEY step owes its operator.
        """
        with app.app_context():
            profile = _profile(db, seed_user, name="Mismatched")
            _set_cadence(db, seed_user["user"].id, 7)
            db.session.commit()

            _run(_M_RF16.downgrade, db.session)
            # The downgrade restores 52; overwrite it with the mismatch the
            # dropped dropdown made authorable.
            db.session.execute(
                text(
                    "UPDATE salary.salary_profiles SET pay_periods_per_year = 26 "
                    "WHERE id = :pid"
                ),
                {"pid": profile.id},
            )
            db.session.commit()

            with caplog.at_level(
                logging.WARNING, logger="alembic.runtime.migration",
            ):
                _run(_M_RF16.upgrade, db.session)

            assert not _column_present(db.session)
            reported = [
                r.getMessage() for r in caplog.records
                if "R-F16" in r.getMessage()
            ]
            assert len(reported) == 1, reported
            assert "stored 26 paychecks" in reported[0]
            assert "7-day cadence" in reported[0]
            assert "derives 52" in reported[0]

    def test_an_agreeing_row_is_not_reported(
        self, app, db, seed_user, restore_dropped_column, caplog,
        with_the_pay_period_derived_columns,
    ):
        """THE CONTROL: a consistent owner produces no warning.

        Why: without this, the assertion above could pass on a migration that
        warned about every row -- which would bury the one that matters and
        report a money move that never happened.  Production is this case (26
        beside a 14-day cadence), so this is also the pin on the step's own
        "zero rows move" claim.
        """
        with app.app_context():
            _profile(db, seed_user, name="Consistent")
            _set_cadence(db, seed_user["user"].id, 14)
            db.session.commit()

            _run(_M_RF16.downgrade, db.session)
            db.session.commit()

            with caplog.at_level(
                logging.INFO, logger="alembic.runtime.migration",
            ):
                _run(_M_RF16.upgrade, db.session)

            warned = [
                r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and "R-F16" in r.getMessage()
            ]
            assert warned == []
