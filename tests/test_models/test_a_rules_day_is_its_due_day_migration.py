"""Migration ``1c569c51b449``: a rule's own day is the day its rows are due.

Plan step recurrence:R5-a, ruling R-R96.  The revision drops
``budget.recurrence_rules.due_day_of_month`` and its CHECK, and REFUSES to run
while any rule still states a due day, naming each one.

Driven two ways.  :class:`TestTheRefusal` pins the decision and the message
through a stub bind, with no database.  :class:`TestTheDDL` runs the revision's
own ``downgrade()`` and ``upgrade()`` against the test database -- built at
head, so the downgrade is what gives a row a column to be planted in
(``restore_rule_due_day_column``) -- and proves the refusal, the drop and the
restore on real DDL.
"""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests._test_helpers import (
    bare_expense_template,
    load_migration_module,
    make_cadence_rule,
    restore_rule_due_day_column,
    run_migration_callable,
)
from tests.oracles.recurrence_baseline import MONTHLY

_MIGRATION = load_migration_module("1c569c51b449_a_rules_day_is_its_due_day.py")


class _StubResult:
    """What ``bind.execute(...)`` returns: rows through ``.all()``."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        """Return the stubbed rows."""
        return self._rows


class _StubBind:
    """A bind that answers every statement with *rows* and records it."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    def execute(self, statement):
        """Record *statement* and answer the stubbed rows."""
        self.statements.append(str(statement))
        return _StubResult(self._rows)


class TestTheRevision:
    """Where the revision sits and what it owes the rules."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "1c569c51b449"
        assert _MIGRATION.down_revision == "9b2c5656eed9"

    def test_it_is_reviewed(self):
        """Destructive DDL carries the ``Review:`` line the rules require."""
        assert "Review:" in _MIGRATION.__doc__


class TestTheRefusal:
    """``refuse_stated_due_days``: a stated due day is never dropped silently."""

    def test_no_stated_due_day_passes(self):
        """The production state: nothing to lose, nothing refused."""
        bind = _StubBind([])

        _MIGRATION.refuse_stated_due_days(bind)

        assert len(bind.statements) == 1
        assert "due_day_of_month IS NOT NULL" in bind.statements[0]

    def test_a_stated_due_day_is_refused_by_id(self):
        """Every offending rule is named, with its day and its owner."""
        bind = _StubBind([
            SimpleNamespace(
                id=7, due_day_of_month=1, starts_on=date(2026, 1, 22),
                transaction_template_id=None, transfer_template_id=3,
                paycheck_line_id=None,
            ),
            SimpleNamespace(
                id=9, due_day_of_month=28, starts_on=date(2026, 2, 5),
                transaction_template_id=11, transfer_template_id=None,
                paycheck_line_id=None,
            ),
        ])

        with pytest.raises(RuntimeError) as exc_info:
            _MIGRATION.refuse_stated_due_days(bind)

        message = str(exc_info.value)
        assert "id=7 due_day_of_month=1" in message
        assert "transfer_template_id=3" in message
        assert "id=9 due_day_of_month=28" in message
        assert "transaction_template_id=11" in message
        assert "R-R96" in message


def _due_day_objects(session):
    """Return ``(column present, CHECK present)`` for the rule's due day."""
    column = session.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'recurrence_rules' "
        "AND column_name = 'due_day_of_month'"
    )).scalar_one()
    check = session.execute(text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'ck_recurrence_rules_due_dom'"
    )).scalar_one()
    return column == 1, check == 1


@pytest.mark.usefixtures("seed_periods")
class TestTheDDL:
    """The revision's own DDL, run: restore, refuse, drop."""

    def test_head_carries_neither_the_column_nor_its_check(self, app, db):
        """The upgrade is what the test database was built through."""
        with app.app_context():
            assert _due_day_objects(db.session) == (False, False)

    def test_down_restores_both_and_up_refuses_a_stated_day_then_drops(
        self, app, db, seed_user,
    ):
        """Down re-adds column and CHECK empty; up refuses a stated day, then drops both."""
        with app.app_context():
            template = bare_expense_template(db.session, seed_user)
            rule = make_cadence_rule(template, MONTHLY, fires_on_day=15)
            rule_id = rule.id
            db.session.commit()

            restore_rule_due_day_column(db.session)
            assert _due_day_objects(db.session) == (True, True)
            assert db.session.execute(text(
                "SELECT due_day_of_month FROM budget.recurrence_rules "
                "WHERE id = :id"
            ), {"id": rule_id}).scalar_one() is None

            db.session.execute(text(
                "UPDATE budget.recurrence_rules SET due_day_of_month = 5 "
                "WHERE id = :id"
            ), {"id": rule_id})
            db.session.commit()
            with pytest.raises(
                RuntimeError, match=f"id={rule_id} due_day_of_month=5",
            ):
                run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.rollback()
            # Refused BEFORE any DDL: the column and its value survive.
            assert _due_day_objects(db.session) == (True, True)

            db.session.execute(text(
                "UPDATE budget.recurrence_rules SET due_day_of_month = NULL "
                "WHERE id = :id"
            ), {"id": rule_id})
            db.session.commit()
            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert _due_day_objects(db.session) == (False, False)
