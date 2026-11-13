"""
Shekel Budget App -- a rule's ceiling, nominal day and weekday reading are held to its unit

Plan step plan#2302, and ruling plan#2704 for the weekday reading.

Migration ``9b3e6d1f4a27`` carries the unit's ``has_day_of_month`` onto
``budget.recurrence_rules`` and holds the row to it::

    unit_has_day_of_month   boolean NOT NULL
    fk_recurrence_rules_unit_day_of_month
                            (unit_id, unit_has_day_of_month) onto the unit's superkey
    ck_recurrence_rules_max_per_month_unit
                            max_per_month IS NULL OR NOT unit_has_day_of_month
    ck_recurrence_rules_nominal_day_unit
                            nominal_day IS NULL OR unit_has_day_of_month

Migration ``897a6663d221`` adds the third rule of the same shape beside the column
it restrains, a revision above the flag::

    ck_recurrence_rules_weekday_reading_unit
                            weekday_reading IS NULL OR unit_has_day_of_month

**Rows here are built column by column**, as in
``test_recurrence_rule_constraints``: the door refuses each of these pairs before
it writes, so authoring one would prove nothing about the table.  Each refusal
names the constraint it expects, because ``IntegrityError`` covers them all and a
case failing on the wrong one would otherwise stay green.

Each CHECK is also lied to.  A flag stated falsely gets a value past its CHECK,
and it is the key that then refuses the row: the two only hold together.

The doors' refusals still answer first, and the one writer writes the flag on a
unit change as well as on the first write.  The migration is driven both ways
over this test's own clone, its census and its null refusal fired rather than
left as paths a green suite never runs.
"""
from datetime import date
from unittest.mock import patch

import pytest
import sqlalchemy
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.models.recurrence_rule import RecurrenceRule
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceResolutionError,
    RecurrenceSpec,
    WeekdayReading,
    author_rule,
    build_transient_rule,
    reauthor_rule,
)
from tests._test_helpers import (
    bare_expense_template,
    load_migration_module,
    run_migration_callable,
)
from tests.test_models._rule_unit_flag import rewind_weekday_reading

_MIGRATION = load_migration_module(
    "9b3e6d1f4a27_a_rule_carries_its_units_day_of_month_flag.py",
)

_KEY = "fk_recurrence_rules_unit_day_of_month"
_CEILING_CHECK = "ck_recurrence_rules_max_per_month_unit"
_NOMINAL_DAY_CHECK = "ck_recurrence_rules_nominal_day_unit"
_WEEKDAY_READING_CHECK = "ck_recurrence_rules_weekday_reading_unit"

#: What each unit's flag must be, written out by hand.
_FLAG = {
    RecurrenceUnitEnum.PERIOD: False,
    RecurrenceUnitEnum.WEEK: False,
    RecurrenceUnitEnum.MONTH: True,
    RecurrenceUnitEnum.YEAR: True,
}
_CALENDAR_UNITS = (RecurrenceUnitEnum.MONTH, RecurrenceUnitEnum.YEAR)
_REPEATING_UNITS = (RecurrenceUnitEnum.PERIOD, RecurrenceUnitEnum.WEEK)

#: A date that genuinely lost its day, so ``ck_recurrence_rules_nominal_day``
#: passes and whatever refuses the pair is the unit half under test.
_CLAMPED = {"starts_on": date(2026, 4, 30), "nominal_day": 31}

#: Each reading on a date it fits, so ``ck_recurrence_rules_weekday_reading``
#: passes and whatever refuses the row is the unit half under test.  Thu Jan 15
#: 2026 is the third Thursday (1, 8, 15); Thu Jan 29 2026 is the last, its next
#: Thursday being Feb 5.
_READ_BY_WEEKDAY = {
    "from_start": {"starts_on": date(2026, 1, 15), "weekday_reading": "from_start"},
    "from_end": {"starts_on": date(2026, 1, 29), "weekday_reading": "from_end"},
}


def _rule(seed_user, db, unit, **overrides):
    """Return an unflushed rule built column by column, on *unit*, with *overrides*.

    Its flag is its unit's unless a case overrides it: every other column here
    is an ordinary storable value, so what a case poisons is all that is wrong.

    Args:
        seed_user: The ``seed_user`` fixture dict, whose user owns the rule.
        db: The ``db`` fixture.
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.
        **overrides: Columns to set or replace.

    Returns:
        The unsaved, session-added :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    columns = {
        "transaction_template_id": bare_expense_template(db.session, seed_user).id,
        "unit_id": ref_cache.recurrence_unit_id(unit),
        "unit_has_day_of_month": _FLAG[unit],
        "placement_id": ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        ),
        "shift_id": ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        "starts_on": date(2026, 1, 15),
    }
    columns.update(overrides)
    rule = RecurrenceRule(**columns)
    db.session.add(rule)
    return rule


def _assert_refused_by(db, constraint, label):
    """Flush the session and assert *constraint* is what refuses it.

    Args:
        db: The ``db`` fixture.
        constraint: The name the database's message must carry.
        label: What to name in the failure, for a parametrized case.
    """
    with pytest.raises(IntegrityError) as exc_info:
        db.session.flush()
    assert constraint in str(exc_info.value), label
    db.session.rollback()


class TestTheCeilingIsHeldToTheUnit:
    """``ck_recurrence_rules_max_per_month_unit``: no ceiling on a calendar unit."""

    @pytest.mark.parametrize("unit", _CALENDAR_UNITS)
    def test_a_calendar_unit_refuses_a_ceiling(self, app, db, seed_user, unit):
        """A cadence that fires at most once a month has no ceiling to hold."""
        with app.app_context():
            _rule(seed_user, db, unit, max_per_month=2)
            _assert_refused_by(db, _CEILING_CHECK, unit)

    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_a_repeating_unit_holds_one(self, app, db, seed_user, unit):
        """The positive control: production's 12 ceilinged period rules stay legal."""
        with app.app_context():
            rule = _rule(seed_user, db, unit, max_per_month=2)
            db.session.flush()
            assert rule.id is not None, unit

    @pytest.mark.parametrize("unit", _CALENDAR_UNITS)
    def test_lying_about_the_flag_meets_the_key(self, app, db, seed_user, unit):
        """A false flag passes the CHECK, so the key must be what refuses it."""
        with app.app_context():
            _rule(
                seed_user, db, unit, max_per_month=2, unit_has_day_of_month=False,
            )
            _assert_refused_by(db, _KEY, unit)


class TestTheNominalDayIsHeldToTheUnit:
    """``ck_recurrence_rules_nominal_day_unit``: only a calendar unit has a day to clamp."""

    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_a_repeating_unit_refuses_one(self, app, db, seed_user, unit):
        """The card's own example: a paycheck rule from 2026-04-30 meaning the 31st."""
        with app.app_context():
            _rule(seed_user, db, unit, **_CLAMPED)
            _assert_refused_by(db, _NOMINAL_DAY_CHECK, unit)

    @pytest.mark.parametrize("unit", _CALENDAR_UNITS)
    def test_a_calendar_unit_holds_one(self, app, db, seed_user, unit):
        """The positive control: without it a CHECK refusing every value passes."""
        with app.app_context():
            rule = _rule(seed_user, db, unit, **_CLAMPED)
            db.session.flush()
            assert rule.id is not None, unit

    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_lying_about_the_flag_meets_the_key(self, app, db, seed_user, unit):
        """A false flag passes the CHECK, so the key must be what refuses it."""
        with app.app_context():
            _rule(seed_user, db, unit, unit_has_day_of_month=True, **_CLAMPED)
            _assert_refused_by(db, _KEY, unit)


class TestTheWeekdayReadingIsHeldToTheUnit:
    """``ck_recurrence_rules_weekday_reading_unit``: a weekday of a month needs a month."""

    @pytest.mark.parametrize("reading", list(_READ_BY_WEEKDAY))
    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_a_repeating_unit_refuses_one(self, app, db, seed_user, unit, reading):
        """A row the door never sees: a weekly rule read as its month's third Thursday."""
        with app.app_context():
            _rule(seed_user, db, unit, **_READ_BY_WEEKDAY[reading])
            _assert_refused_by(db, _WEEKDAY_READING_CHECK, (unit, reading))

    @pytest.mark.parametrize("reading", list(_READ_BY_WEEKDAY))
    @pytest.mark.parametrize("unit", _CALENDAR_UNITS)
    def test_a_calendar_unit_holds_one(self, app, db, seed_user, unit, reading):
        """The positive control: without it a CHECK refusing every value passes."""
        with app.app_context():
            rule = _rule(seed_user, db, unit, **_READ_BY_WEEKDAY[reading])
            db.session.flush()
            assert rule.id is not None, (unit, reading)

    @pytest.mark.parametrize("reading", list(_READ_BY_WEEKDAY))
    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_lying_about_the_flag_meets_the_key(
        self, app, db, seed_user, unit, reading,
    ):
        """A false flag passes the CHECK, so the key must be what refuses it."""
        with app.app_context():
            _rule(
                seed_user, db, unit, unit_has_day_of_month=True,
                **_READ_BY_WEEKDAY[reading],
            )
            _assert_refused_by(db, _KEY, (unit, reading))


class TestTheFlagIsHeldToTheUnit:
    """``fk_recurrence_rules_unit_day_of_month``, from both of its sides."""

    @pytest.mark.parametrize("unit", list(_FLAG))
    def test_a_flag_that_disagrees_with_its_unit_is_refused(
        self, app, db, seed_user, unit,
    ):
        """Each unit, with no ceiling or day beside it: the key alone refuses."""
        with app.app_context():
            _rule(seed_user, db, unit, unit_has_day_of_month=not _FLAG[unit])
            _assert_refused_by(db, _KEY, unit)

    def test_a_null_flag_is_refused(self, app, db, seed_user):
        """``MATCH SIMPLE`` checks nothing with a NULL member, so NOT NULL must."""
        with app.app_context():
            _rule(
                seed_user, db, RecurrenceUnitEnum.MONTH,
                unit_has_day_of_month=None,
            )
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            # The database's own word: the statement's text names the column too.
            assert (
                exc_info.value.orig.diag.column_name == "unit_has_day_of_month"
            )
            db.session.rollback()

    def test_moving_the_unit_alone_is_refused(self, app, db, seed_user):
        """A stored rule re-pointed by hand cannot leave its flag behind."""
        with app.app_context():
            rule = _rule(seed_user, db, RecurrenceUnitEnum.MONTH)
            db.session.flush()
            rule.unit_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD)
            _assert_refused_by(db, _KEY, "unit moved alone")

    def test_moving_the_unit_and_its_flag_together_is_admitted(
        self, app, db, seed_user,
    ):
        """The positive control, and the shape the one writer's UPDATE takes."""
        with app.app_context():
            rule = _rule(seed_user, db, RecurrenceUnitEnum.MONTH)
            db.session.flush()
            rule.unit_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD)
            rule.unit_has_day_of_month = False
            db.session.flush()
            assert rule.unit_has_day_of_month is False

    def test_a_span_that_flips_a_units_flag_is_refused_under_its_rules(
        self, app, db, seed_user,
    ):
        """The unit's side: the generated flag cannot move out from under a rule."""
        with app.app_context():
            _rule(seed_user, db, RecurrenceUnitEnum.MONTH)
            db.session.flush()
            with pytest.raises(IntegrityError) as exc_info:
                db.session.execute(sqlalchemy.text(
                    "UPDATE ref.recurrence_units SET months_per_unit = NULL "
                    "WHERE id = :id"
                ), {"id": ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)})
            assert _KEY in str(exc_info.value)
            db.session.rollback()

    def test_a_span_that_keeps_the_flag_is_admitted(self, app, db, seed_user):
        """The positive control: only a flag that flips is the key's business."""
        with app.app_context():
            _rule(seed_user, db, RecurrenceUnitEnum.YEAR)
            db.session.flush()
            result = db.session.execute(sqlalchemy.text(
                "UPDATE ref.recurrence_units SET months_per_unit = 24 "
                "WHERE id = :id"
            ), {"id": ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR)})
            assert result.rowcount == 1
            db.session.rollback()


class TestTheDoorsAnswerFirst:
    """The CHECKs are the backstop: a caller meets the door's sentence, not a flush."""

    @pytest.mark.parametrize("unit", _CALENDAR_UNITS)
    def test_a_spec_refuses_a_ceiling_on_a_calendar_unit(self, app, unit):
        """``_require_month_ceiling_pair`` refuses the value at construction."""
        with app.app_context():
            with pytest.raises(
                RecurrenceResolutionError, match="max_per_month 2 cannot sit",
            ):
                RecurrenceSpec(
                    user_id=1, unit=unit, starts_on=date(2026, 1, 15),
                    max_per_month=2,
                )

    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_a_spec_refuses_a_nominal_day_on_a_repeating_unit(self, app, unit):
        """``_require_nominal_day_pair`` refuses the value at construction."""
        with app.app_context():
            with pytest.raises(
                RecurrenceResolutionError, match="nominal_day 31 cannot sit",
            ):
                RecurrenceSpec(user_id=1, unit=unit, **_CLAMPED)

    @pytest.mark.parametrize("unit", _REPEATING_UNITS)
    def test_a_spec_refuses_a_weekday_reading_on_a_repeating_unit(self, app, unit):
        """``_require_weekday_reading_pair`` refuses the value at construction."""
        with app.app_context():
            with pytest.raises(
                RecurrenceResolutionError, match="no day-of-month coordinate",
            ):
                RecurrenceSpec(
                    user_id=1, unit=unit, starts_on=date(2026, 1, 15),
                    weekday_reading=WeekdayReading.FROM_START,
                )


@pytest.mark.usefixtures("seed_periods")
class TestTheOneWriterWritesTheFlag:
    """``_author`` writes the flag beside ``unit_id``, the second time as well."""

    @staticmethod
    def _spec(seed_user, unit):
        """Return a spec on *unit* from the owner's opening payday."""
        user_id = seed_user["user"].id
        return RecurrenceSpec(
            user_id=user_id, unit=unit,
            starts_on=calendar_for(user_id).opening_bound(),
        )

    @staticmethod
    def _stored_flag(db, rule_id):
        """Commit, then re-read the row: a staged value proves nothing."""
        db.session.commit()
        return db.session.execute(sqlalchemy.text(
            "SELECT unit_has_day_of_month FROM budget.recurrence_rules "
            "WHERE id = :id"
        ), {"id": rule_id}).scalar_one()

    @pytest.mark.parametrize("unit", list(_FLAG))
    def test_the_first_write_stores_the_units_flag(self, app, db, seed_user, unit):
        """Each unit, through the door, reaches the row with its own flag."""
        with app.app_context():
            rule = author_rule(
                self._spec(seed_user, unit),
                calendar_for(seed_user["user"].id),
                bare_expense_template(db.session, seed_user),
            )
            assert self._stored_flag(db, rule.id) is _FLAG[unit]

    def test_a_change_of_unit_moves_the_flag_each_way(self, app, db, seed_user):
        """Month to paycheck and back: both flips clear the key and reach the row."""
        with app.app_context():
            calendar = calendar_for(seed_user["user"].id)
            rule = author_rule(
                self._spec(seed_user, RecurrenceUnitEnum.MONTH), calendar,
                bare_expense_template(db.session, seed_user),
            )
            assert self._stored_flag(db, rule.id) is True

            reauthor_rule(
                rule, self._spec(seed_user, RecurrenceUnitEnum.PERIOD), calendar,
            )
            assert self._stored_flag(db, rule.id) is False

            reauthor_rule(
                rule, self._spec(seed_user, RecurrenceUnitEnum.MONTH), calendar,
            )
            assert self._stored_flag(db, rule.id) is True

    @pytest.mark.parametrize("unit", list(_FLAG))
    def test_a_transient_rule_carries_it_too(self, app, seed_user, unit):
        """The door's other entry point writes through the same function."""
        with app.app_context():
            rule = build_transient_rule(
                self._spec(seed_user, unit), calendar_for(seed_user["user"].id),
            )
            assert rule.unit_has_day_of_month is _FLAG[unit]


def _rule_table_state(db):
    """Return ``(columns, {constraint: definition})`` of the rule table, by catalogue.

    PostgreSQL 18 records a NOT NULL as a constraint too; the column's nullability
    is what carries it here, so those rows are left out of the constraints.
    """
    columns = dict(db.session.execute(sqlalchemy.text(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'recurrence_rules'"
    )).all())
    constraints = dict(db.session.execute(sqlalchemy.text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'budget.recurrence_rules'::regclass "
        "AND contype <> 'n'"
    )).all())
    return columns, constraints


def _plant(db, seed_user, unit, starts_on, **values):
    """Insert a rule by raw SQL on a schema carrying no flag, and return its id.

    Args:
        db: The ``db`` fixture.
        seed_user: The ``seed_user`` fixture dict, whose user owns the rule.
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.
        starts_on: The rule's first occurrence.
        **values: ``max_per_month`` and ``nominal_day``, each defaulting to NULL.

    Returns:
        int: The new rule's id.
    """
    return db.session.execute(sqlalchemy.text(
        "INSERT INTO budget.recurrence_rules "
        "(transaction_template_id, unit_id, placement_id, shift_id, starts_on, "
        " max_per_month, nominal_day) "
        "VALUES (:owner, :unit, :placement, :shift, :starts_on, "
        "        :max_per_month, :nominal_day) RETURNING id"
    ), {
        "owner": bare_expense_template(db.session, seed_user).id,
        "unit": ref_cache.recurrence_unit_id(unit),
        "placement": ref_cache.period_placement_id(
            PeriodPlacementEnum.CONTAINING_DATE,
        ),
        "shift": ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        "starts_on": starts_on,
        "max_per_month": values.get("max_per_month"),
        "nominal_day": values.get("nominal_day"),
    }).scalar_one()
class TestTheMigration:
    """``9b3e6d1f4a27`` over this test's own clone, in both directions.

    Alembic undoes the newest first, so each case rewinds ``897a6663d221`` before
    it drives this revision: that revision's unit CHECK names the flag, and
    PostgreSQL drops a CHECK with a column it names.
    """

    def test_down_removes_the_four_objects_and_up_restores_them_exactly(
        self, app, db, capsys,
    ):
        """Down touches nothing else; up puts back the same definitions, NOT NULL."""
        with app.app_context():
            rewind_weekday_reading(db.session)
            before_columns, before_constraints = _rule_table_state(db)
            assert before_columns["unit_has_day_of_month"] == "NO"

            run_migration_callable(_MIGRATION.downgrade, db.session)

            columns, constraints = _rule_table_state(db)
            assert "unit_has_day_of_month" not in columns
            assert set(before_constraints) - set(constraints) == {
                _KEY, _CEILING_CHECK, _NOMINAL_DAY_CHECK,
            }

            run_migration_callable(_MIGRATION.upgrade, db.session)

            assert _rule_table_state(db) == (before_columns, before_constraints)
            db.session.rollback()
        out = capsys.readouterr().out
        assert "plan#2302: cleared 0 max_per_month value(s)." in out
        assert "plan#2302: cleared 0 nominal_day value(s)." in out

    def test_the_backfill_gives_each_rule_its_units_flag(self, app, db, seed_user):
        """Rules on all four units, flagless before, each take their own unit's."""
        with app.app_context():
            rewind_weekday_reading(db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            planted = {
                unit: _plant(db, seed_user, unit, date(2026, 1, 15))
                for unit in _FLAG
            }

            run_migration_callable(_MIGRATION.upgrade, db.session)

            stored = dict(db.session.execute(sqlalchemy.text(
                "SELECT id, unit_has_day_of_month FROM budget.recurrence_rules "
                "WHERE id = ANY(:ids)"
            ), {"ids": list(planted.values())}).all())
            assert stored == {
                rule_id: _FLAG[unit] for unit, rule_id in planted.items()
            }
            db.session.rollback()

    def test_the_census_clears_what_no_unit_can_hold_and_reports_each(
        self, app, db, seed_user, capsys,
    ):
        """Four unholdable values cleared and printed; the two legal ones untouched."""
        april_30 = date(2026, 4, 30)
        with app.app_context():
            rewind_weekday_reading(db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            month_ceiling = _plant(
                db, seed_user, RecurrenceUnitEnum.MONTH, april_30, max_per_month=2,
            )
            year_ceiling = _plant(
                db, seed_user, RecurrenceUnitEnum.YEAR, april_30, max_per_month=1,
            )
            period_day = _plant(
                db, seed_user, RecurrenceUnitEnum.PERIOD, april_30, nominal_day=31,
            )
            week_day = _plant(
                db, seed_user, RecurrenceUnitEnum.WEEK, april_30, nominal_day=31,
            )
            legal_ceiling = _plant(
                db, seed_user, RecurrenceUnitEnum.PERIOD, april_30, max_per_month=2,
            )
            legal_day = _plant(
                db, seed_user, RecurrenceUnitEnum.MONTH, april_30, nominal_day=31,
            )
            capsys.readouterr()

            run_migration_callable(_MIGRATION.upgrade, db.session)

            rows = {
                row.id: (row.max_per_month, row.nominal_day)
                for row in db.session.execute(sqlalchemy.text(
                    "SELECT id, max_per_month, nominal_day "
                    "FROM budget.recurrence_rules"
                ))
            }
            assert rows == {
                month_ceiling: (None, None),
                year_ceiling: (None, None),
                period_day: (None, None),
                week_day: (None, None),
                legal_ceiling: (2, None),
                legal_day: (None, 31),
            }
            # What the docstring says recovers a cleared value: the UPDATE's
            # own audit row, holding the value it replaced.
            audited = db.session.execute(sqlalchemy.text(
                "SELECT old_data ->> 'max_per_month' FROM system.audit_log "
                "WHERE table_schema = 'budget' AND table_name = 'recurrence_rules' "
                "  AND operation = 'UPDATE' AND row_id = :id "
                "  AND 'max_per_month' = ANY(changed_fields)"
            ), {"id": month_ceiling}).scalars().all()
            assert audited == ["2"]
            db.session.rollback()
        out = capsys.readouterr().out
        assert (
            f"plan#2302: cleared 2 max_per_month value(s). "
            f"rule {month_ceiling} (month, max_per_month 2); "
            f"rule {year_ceiling} (year, max_per_month 1);"
        ) in out
        assert (
            f"plan#2302: cleared 2 nominal_day value(s). "
            f"rule {period_day} (period, nominal_day 31); "
            f"rule {week_day} (week, nominal_day 31);"
        ) in out

    def test_the_upgrade_refuses_a_row_the_backfill_left_null(
        self, app, db, seed_user,
    ):
        """The three-step rule's refusal, fired with the backfill made a no-op."""
        with app.app_context():
            rewind_weekday_reading(db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            _plant(db, seed_user, RecurrenceUnitEnum.MONTH, date(2026, 1, 15))

            with patch.object(
                _MIGRATION, "_BACKFILL", sqlalchemy.text("SELECT 1"),
            ):
                with pytest.raises(RuntimeError) as exc:
                    run_migration_callable(_MIGRATION.upgrade, db.session)

            assert "1 row(s) took no unit_has_day_of_month" in str(exc.value)
            assert "SELECT r.id, r.unit_id FROM" in str(exc.value)
            db.session.rollback()
