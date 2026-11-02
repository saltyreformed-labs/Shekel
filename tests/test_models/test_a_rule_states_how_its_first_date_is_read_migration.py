"""Migration ``897a6663d221``: a rule states how its first date is read.

Plan step recurrence:R8-c, rulings plan#2668 (amending R-R25) and plan#2670.
The revision drops ``budget.recurrence_weekday_anchors`` -- refusing to while
it holds a row -- and adds ``budget.recurrence_rules.weekday_reading`` under
``ck_recurrence_rules_weekday_reading`` and
``ck_recurrence_rules_one_month_reading``.  Its downgrade refuses while any rule
is read by its weekday, then restores the empty table with its audit trigger.

Driven two ways.  :class:`TestTheRefusals` pins each refusal's decision and
message through a stub bind, with no database.  :class:`TestTheDDL` runs the
revision's own ``downgrade()`` and ``upgrade()`` against the test database --
built at head, and a clone of its own per test -- and proves both refusals,
the drop, the restore and the restored trigger FIRING on real DDL.  What the
two CHECKs refuse is graded in
``test_recurrence_rule_constraints.TestTheWeekdayReadingIsHeldToItsDate``.
"""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.audit_infrastructure import EXPECTED_TRIGGER_COUNT
from app.enums import RecurrenceUnitEnum
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, WeekdayReading, author_rule
from tests._test_helpers import (
    bare_expense_template,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "897a6663d221_a_rule_states_how_its_first_date_is_read.py",
)


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
        assert _MIGRATION.revision == "897a6663d221"
        assert _MIGRATION.down_revision == "0703995c8536"

    def test_it_is_reviewed(self):
        """A dropped table carries the ``Review:`` line the rules require."""
        assert "Review:" in _MIGRATION.__doc__


class TestTheRefusals:
    """Neither direction drops a stated coordinate silently."""

    def test_an_empty_anchor_table_passes(self):
        """The production state (0 rows, measured 2026-11-02): nothing refused."""
        bind = _StubBind([])

        _MIGRATION.refuse_written_weekday_anchors(bind)

        assert len(bind.statements) == 1
        assert "budget.recurrence_weekday_anchors" in bind.statements[0]

    def test_a_written_anchor_is_refused_by_id(self):
        """Every row is named with its rule and its coordinate."""
        bind = _StubBind([
            SimpleNamespace(id=2, recurrence_rule_id=40, nth_week=3, weekday=1),
            SimpleNamespace(id=5, recurrence_rule_id=41, nth_week=-1, weekday=4),
        ])

        with pytest.raises(RuntimeError) as refused:
            _MIGRATION.refuse_written_weekday_anchors(bind)

        message = str(refused.value)
        assert "id=2 recurrence_rule_id=40 nth_week=3 weekday=1" in message
        assert "id=5 recurrence_rule_id=41 nth_week=-1 weekday=4" in message
        assert "plan#2668" in message

    def test_no_weekday_reading_passes(self):
        """Every rule read by its day: the downgrade loses nothing."""
        bind = _StubBind([])

        _MIGRATION.refuse_weekday_readings(bind)

        assert len(bind.statements) == 1
        assert "weekday_reading IS NOT NULL" in bind.statements[0]

    def test_a_weekday_reading_is_refused_by_id(self):
        """Every such rule is named with its reading, date and owner."""
        bind = _StubBind([
            SimpleNamespace(
                id=7, weekday_reading="from_start",
                starts_on=date(2026, 11, 17), transaction_template_id=None,
                transfer_template_id=3, paycheck_line_id=None,
            ),
            SimpleNamespace(
                id=9, weekday_reading="from_end",
                starts_on=date(2027, 1, 29), transaction_template_id=11,
                transfer_template_id=None, paycheck_line_id=None,
            ),
        ])

        with pytest.raises(RuntimeError) as refused:
            _MIGRATION.refuse_weekday_readings(bind)

        message = str(refused.value)
        assert "id=7 weekday_reading=from_start starts_on=2026-11-17" in message
        assert "transfer_template_id=3" in message
        assert "id=9 weekday_reading=from_end starts_on=2027-01-29" in message
        assert "transaction_template_id=11" in message


def _schema_objects(session) -> dict[str, bool]:
    """Return which of the revision's objects the database carries.

    Args:
        session: The test ``db.session``.

    Returns:
        ``{object: present}`` for the column, both CHECKs, the table and its
        audit trigger.
    """
    def present(sql, **params):
        return session.execute(text(sql), params).scalar_one() == 1

    return {
        "column": present(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = 'recurrence_rules' "
            "AND column_name = 'weekday_reading'"
        ),
        "reading check": present(
            "SELECT count(*) FROM pg_constraint WHERE conname = :n",
            n="ck_recurrence_rules_weekday_reading",
        ),
        "arc check": present(
            "SELECT count(*) FROM pg_constraint WHERE conname = :n",
            n="ck_recurrence_rules_one_month_reading",
        ),
        "table": present(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'budget' "
            "AND table_name = 'recurrence_weekday_anchors'"
        ),
        "trigger": present(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgname = 'audit_recurrence_weekday_anchors' "
            "AND NOT tgisinternal"
        ),
    }


#: Head: the column and its CHECKs, no table.
_AT_HEAD = {
    "column": True, "reading check": True, "arc check": True,
    "table": False, "trigger": False,
}

#: The revision below: the table and its trigger, no column.
_BELOW = {
    "column": False, "reading check": False, "arc check": False,
    "table": True, "trigger": True,
}


def _audit_trigger_count(session) -> int:
    """Return how many audit triggers the database carries.

    Its own spelling rather than ``AUDIT_TRIGGER_COUNT_SQL``, so an oracle
    sharing the deploy's could not be wrong in step with it.
    """
    return session.execute(text(
        "SELECT count(*) FROM pg_trigger "
        "WHERE tgname LIKE 'audit\\_%' AND NOT tgisinternal"
    )).scalar_one()


@pytest.mark.usefixtures("seed_periods")
@pytest.mark.xdist_group("recurrence_rules_ddl")
class TestTheDDL:
    """The revision's own DDL, run: refuse, restore, refuse, drop."""

    def test_head_carries_the_column_and_both_checks_and_no_table(self, app, db):
        """The upgrade is what the test database was built through."""
        with app.app_context():
            assert _schema_objects(db.session) == _AT_HEAD
            assert _audit_trigger_count(db.session) == EXPECTED_TRIGGER_COUNT

    def test_each_direction_refuses_what_it_would_lose_then_runs(
        self, app, db, seed_user,
    ):
        """Down refuses a reading, restores the audited table; up refuses a row, drops it."""
        with app.app_context():
            user_id = seed_user["user"].id
            rule = author_rule(
                RecurrenceSpec(
                    user_id=user_id, unit=RecurrenceUnitEnum.MONTH,
                    starts_on=date(2026, 11, 17),
                    weekday_reading=WeekdayReading.FROM_START,
                ),
                calendar_for(user_id),
                bare_expense_template(db.session, seed_user),
            )
            rule_id = rule.id
            db.session.commit()

            # DOWN refuses the third Tuesday before any DDL.
            with pytest.raises(
                RuntimeError, match=f"id={rule_id} weekday_reading=from_start",
            ):
                run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.rollback()
            assert _schema_objects(db.session) == _AT_HEAD

            db.session.execute(text(
                "UPDATE budget.recurrence_rules SET weekday_reading = NULL "
                "WHERE id = :id"
            ), {"id": rule_id})
            db.session.commit()
            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _schema_objects(db.session) == _BELOW
            assert _audit_trigger_count(db.session) == EXPECTED_TRIGGER_COUNT + 1

            # The restored trigger FIRES, with the row's own id: the reason the
            # table carries a surrogate key (``audit_trigger_func`` reads NEW.id).
            anchor_id = db.session.execute(text(
                "INSERT INTO budget.recurrence_weekday_anchors "
                "(recurrence_rule_id, nth_week, weekday) "
                "VALUES (:r, -1, 4) RETURNING id"
            ), {"r": rule_id}).scalar_one()
            logged = db.session.execute(text(
                "SELECT row_id FROM system.audit_log "
                "WHERE table_schema = 'budget' "
                "AND table_name = 'recurrence_weekday_anchors' "
                "AND operation = 'INSERT'"
            )).scalars().all()
            assert logged == [anchor_id]
            db.session.commit()

            # UP refuses the written row before any DDL.
            with pytest.raises(
                RuntimeError,
                match=(
                    f"id={anchor_id} recurrence_rule_id={rule_id} "
                    f"nth_week=-1 weekday=4"
                ),
            ):
                run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.rollback()
            assert _schema_objects(db.session) == _BELOW

            db.session.execute(text(
                "DELETE FROM budget.recurrence_weekday_anchors WHERE id = :id"
            ), {"id": anchor_id})
            db.session.commit()
            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert _schema_objects(db.session) == _AT_HEAD
            assert _audit_trigger_count(db.session) == EXPECTED_TRIGGER_COUNT
