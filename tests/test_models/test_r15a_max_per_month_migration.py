"""Migration ``ef32dfe4cd8e`` -- a rule admits at most N occurrences a month.

Plan step **salary:R15-a** (ruling **R-SAL29**).  The migration adds
``budget.recurrence_rules.max_per_month`` with its floor CHECK, and its
downgrade REFUSES while any row carries a ceiling: dropping the column would
turn "every paycheck, at most 2 a month" into "every paycheck", which on the
developer's eleven payroll lines is ``$1,034.42`` a year of deductions the
employer does not take.  Both directions are driven through the migration's
own shipped callables, never through hand-written DDL standing in for them,
and every assertion reads the DATABASE for the objects.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.enums import RecurrenceUnitEnum
from tests._test_helpers import (
    bare_expense_template,
    load_migration_module,
    run_migration_callable as _run,
)
from tests.test_models._rule_unit_flag import rewind_rule_unit_flag

#: This revision, loaded so its own callables are what this file drives.
_M_R15A = load_migration_module(
    "ef32dfe4cd8e_a_rule_admits_at_most_n_a_month.py",
)

_COLUMN = "max_per_month"
_CHECK = "ck_recurrence_rules_positive_max_per_month"


def _columns(session) -> list[str]:
    """Return the column names of ``budget.recurrence_rules``, in ordinal order."""
    return [
        row[0] for row in session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = 'recurrence_rules' "
            "ORDER BY ordinal_position"
        ))
    ]


def _constraints(session) -> set[str]:
    """Return the constraint names on ``budget.recurrence_rules`` as a set.

    Read from ``pg_constraint`` rather than from the model, which is the whole
    point: the two are separate statements of one schema.
    """
    return {
        row[0] for row in session.execute(text(
            "SELECT conname FROM pg_constraint c "
            "  JOIN pg_class t ON t.oid = c.conrelid "
            "  JOIN pg_namespace n ON n.oid = t.relnamespace "
            " WHERE n.nspname = 'budget' AND t.relname = 'recurrence_rules'"
        ))
    }


def _ceilinged_rule(db, seed_user):
    """Author one every-paycheck rule ceilinged at 2 a month, and commit it."""
    user_id = seed_user["user"].id
    rule = author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=date(2026, 3, 26),
            max_per_month=2,
        ),
        calendar_for(user_id),
        bare_expense_template(db.session, seed_user),
    )
    db.session.commit()
    return rule


@pytest.mark.usefixtures("seed_periods")
class TestTheRoundTrip:
    """Down then up restores exactly the column and the CHECK, on an empty ceiling set."""

    def test_the_upgrade_left_the_column_and_its_floor(self, app, db):
        """The template the suite runs on already carries both objects."""
        with app.app_context():
            assert _COLUMN in _columns(db.session)
            assert _CHECK in _constraints(db.session)

    def test_down_then_up_removes_and_restores_exactly_the_two_objects(
        self, app, db,
    ):
        """With no ceilinged row the downgrade drops both, and the upgrade puts both back.

        plan#2302's unit CHECK names the column, and PostgreSQL drops a CHECK with
        a column it names, so that newer object is rewound before this one is graded.
        """
        with app.app_context():
            rewind_rule_unit_flag(db.session)
            before_columns = _columns(db.session)
            before_constraints = _constraints(db.session)

            _run(_M_R15A.downgrade, db.session)

            assert _COLUMN not in _columns(db.session)
            assert _CHECK not in _constraints(db.session)
            assert set(before_constraints) - _constraints(db.session) == {
                _CHECK,
            }, "the downgrade removed a constraint it did not add"

            _run(_M_R15A.upgrade, db.session)

            # As a SET: a later migration that appends a column to this table
            # moves the re-added ceiling's ordinal position, which is not a
            # fact this round trip claims.
            assert set(_columns(db.session)) == set(before_columns)
            assert _constraints(db.session) == before_constraints


@pytest.mark.usefixtures("seed_periods")
class TestTheDowngradeRefusesWhileACeilingExists:
    """A rollback that silently widened a cadence would move money; it refuses instead."""

    def test_a_ceilinged_rule_makes_the_downgrade_refuse_by_name(
        self, app, db, seed_user,
    ):
        """The refusal names the rule, its owning arm and the ceiling, plus the repair."""
        rule = _ceilinged_rule(db, seed_user)
        template_id = rule.transaction_template_id

        with app.app_context():
            with pytest.raises(RuntimeError) as excinfo:
                _run(_M_R15A.downgrade, db.session)
            db.session.rollback()

        message = str(excinfo.value)
        assert f"rule {rule.id} (transaction_template_id={template_id}" in message
        assert "at most 2 a month" in message
        assert "UPDATE budget.recurrence_rules SET max_per_month = NULL" in message

    def test_the_refused_downgrade_leaves_both_objects_in_place(
        self, app, db, seed_user,
    ):
        """Refused means untouched: the column and the CHECK survive the attempt."""
        _ceilinged_rule(db, seed_user)

        with app.app_context():
            with pytest.raises(RuntimeError):
                _run(_M_R15A.downgrade, db.session)
            db.session.rollback()

            assert _COLUMN in _columns(db.session)
            assert _CHECK in _constraints(db.session)
