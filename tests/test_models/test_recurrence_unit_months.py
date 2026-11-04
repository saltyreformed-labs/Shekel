"""
Shekel Budget App -- a unit's months live on its reference row (plan#2302)

Ruling **plan#2748** (developer, 2026-11-03) moved the one statement of which
recurrence units are measured in months, and how many, off a code table and
onto ``ref.recurrence_units`` (migration ``c4e8a1f7d2b6``)::

    months_per_unit   smallint NULL   1 month, 12 year, NULL period and week
    has_day_of_month  boolean NOT NULL GENERATED (months_per_unit IS NOT NULL)

**Four classes.**  What the rows SAY, against the two places a database is
born from (the migration for the ones that exist, ``app.ref_seeds`` for a
fresh one): the test database is migration-built and then reseeded, and
``_seed_other_ref_tables`` leaves an existing row alone, so the stored value
is the MIGRATION's and comparing it with the seed file grades the half no
other test reads.  The CHECK on the span.  Then the MIGRATION itself, over
this test's own clone: the downgrade takes all three objects away, the
upgrade puts back exactly the spans the seed states, and its refusal over a
database missing a calendar row is FIRED rather than left as the one path a
green suite never runs.  And what the ENGINE reads: ``ref_cache`` answers
each unit's span and flag off the row, and the occurrence stride follows a
span changed in the database, which is the ruling's whole claim -- the code
holds no second copy to answer from.
"""
from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.models.ref import RecurrenceUnit
from app.ref_seeds import _REF_TABLE_SEEDS
from app.services.pay_calendar import PayCadence
from app.services.pay_rhythm import FixedDays
from app.services.recurrence import (
    Cadence,
    _months,
    has_day_of_month_coordinate,
)
from tests._test_helpers import load_migration_module
from tests.test_models._rule_unit_flag import rewind_rule_unit_flag

_MIGRATION = load_migration_module(
    "c4e8a1f7d2b6_a_units_months_live_on_its_reference_row.py",
)

#: What each unit's row must say, written out by hand: the paycheck and week
#: units are not measured in months, a month is one, a year twelve.
_EXPECTED = {
    "period": (None, False),
    "week": (None, False),
    "month": (1, True),
    "year": (12, True),
}


def _stored(db):
    """Return ``{name: (months_per_unit, has_day_of_month)}`` by raw SQL.

    Raw rather than through the model so it reads the same after the
    downgrade has taken the columns away (it is not called then) and so what
    it grades is the table, not the mapping.
    """
    rows = db.session.execute(sqlalchemy.text(
        "SELECT name, months_per_unit, has_day_of_month "
        "FROM ref.recurrence_units"
    )).all()
    return {name: (months, flag) for name, months, flag in rows}


def _columns(db):
    """Return the set of ``ref.recurrence_units``' column names right now."""
    return set(db.session.execute(sqlalchemy.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'ref' AND table_name = 'recurrence_units'"
    )).scalars())


def _run(db, step):
    """Drive one of the migration's steps over this test's own connection.

    The idiom ``test_recurrence_window_check`` uses: Alembic's ``op`` proxy
    needs an operations context, and ``op.get_bind`` is patched so the step
    gets the session's connection rather than a second one.
    """
    connection = db.session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            step()


class TestWhatTheRowsSay:
    """Each unit's span and flag, from the database and from the seed file."""

    def test_each_unit_states_its_months_and_the_flag_follows(self, app, db):
        """Month 1 and year 12 name a day of the month; paycheck and week do not.

        The flag is read back from the GENERATED column, so this also grades
        that the database computed it: no writer stated it.
        """
        with app.app_context():
            assert _stored(db) == _EXPECTED

    def test_the_seeder_and_the_database_agree_on_each_units_months(
        self, app, db,
    ):
        """Leg 3 of the dual seed for the one non-name value this table holds.

        ``_seed_other_ref_tables`` INSERTS a missing row and leaves a present
        one alone, so a span changed in ``app/ref_seeds.py`` and nowhere else
        changes a FRESH bootstrap and nothing about a database that exists;
        the two would disagree silently and for ever.  The stored value is the
        migration's (the test database is migration-built, then reseeded).
        """
        with app.app_context():
            seeded = {
                entry["name"]: entry["months_per_unit"]
                for entry in dict(_REF_TABLE_SEEDS)["RecurrenceUnit"]
            }
            stored = {
                row.name: row.months_per_unit
                for row in db.session.query(RecurrenceUnit).all()
            }
            assert seeded == stored


class TestTheSpanCheck:
    """``ck_recurrence_units_positive_months``."""

    def test_a_zero_span_is_refused(self, app, db):
        """A zero stride would walk a ``range`` that never advances."""
        with app.app_context():
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.execute(sqlalchemy.text(
                    "UPDATE ref.recurrence_units SET months_per_unit = 0 "
                    "WHERE name = 'month'"
                ))
            assert "ck_recurrence_units_positive_months" in str(exc.value)
            db.session.rollback()


class TestTheMigration:
    """``c4e8a1f7d2b6`` over this test's own clone, in both directions."""

    def test_the_downgrade_removes_the_three_and_the_upgrade_restores_them(
        self, app, db,
    ):
        """Down drops the span, the flag and the superkey; up writes them back.

        The upgrade writes the spans from nothing, so the values it leaves are
        the MIGRATION's own: equal to the hand-written expectation, which the
        seed file is graded against above.
        """
        with app.app_context():
            rewind_rule_unit_flag(db.session)
            _run(db, _MIGRATION.downgrade)
            assert not {"months_per_unit", "has_day_of_month"} & _columns(db)

            _run(db, _MIGRATION.upgrade)
            assert _stored(db) == _EXPECTED
            superkeys = db.session.execute(sqlalchemy.text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'uq_recurrence_units_id_has_day_of_month'"
            )).scalar()
            assert superkeys == 1
            db.session.rollback()

    def test_the_upgrade_refuses_a_database_missing_a_calendar_row(
        self, app, db,
    ):
        """A ``year`` row the upgrade cannot find is refused, naming the count.

        Without the refusal the year unit would carry no span, and every
        yearly rule would meet ``MonthStepError`` on its next read.  The row is
        RENAMED rather than deleted: rules on the clone may reference it.
        """
        with app.app_context():
            rewind_rule_unit_flag(db.session)
            _run(db, _MIGRATION.downgrade)
            db.session.execute(sqlalchemy.text(
                "UPDATE ref.recurrence_units SET name = 'annual' "
                "WHERE name = 'year'"
            ))

            with pytest.raises(RuntimeError) as exc:
                _run(db, _MIGRATION.upgrade)

            assert "wrote a month span on 1 row(s), expected 2" in str(
                exc.value
            )
            assert "SELECT id, name FROM ref.recurrence_units" in str(exc.value)
            db.session.rollback()


class TestTheEngineReadsTheRow:
    """``ref_cache`` answers from the row, and the engine asks the cache."""

    def test_the_cache_answers_each_units_span_and_flag(self, app):
        """Every member, both accessors, against the hand-written expectation."""
        with app.app_context():
            answered = {
                member.value: (
                    ref_cache.recurrence_unit_months(member),
                    ref_cache.recurrence_unit_has_day_of_month(member),
                )
                for member in RecurrenceUnitEnum
            }
            assert answered == _EXPECTED

    def test_the_stride_follows_a_span_changed_on_the_row(self, app, db):
        """A yearly span of 24 makes a one-year stride 24 months.

        No reader would author this; it is here because it is the one way to
        show the engine holds no copy of its own.  With a code table the
        stride would still read 12.  The flag stays True (24 is not NULL), and
        the cache is re-initialised clean afterwards so no other test sees it.
        """
        with app.app_context():
            assert _months.months_per_step(RecurrenceUnitEnum.YEAR, 1) == 12
            db.session.execute(sqlalchemy.text(
                "UPDATE ref.recurrence_units SET months_per_unit = 24 "
                "WHERE name = 'year'"
            ))
            ref_cache.init(db.session)
            try:
                # 24 months a year-unit, times an interval of 2.
                assert _months.months_per_step(RecurrenceUnitEnum.YEAR, 2) == 48
                assert has_day_of_month_coordinate(RecurrenceUnitEnum.YEAR)
            finally:
                db.session.rollback()
                ref_cache.init(db.session)
            assert _months.months_per_step(RecurrenceUnitEnum.YEAR, 1) == 12

    def test_the_yearly_count_follows_a_span_changed_on_the_row(self, app, db):
        """A yearly span of 24 is half a unit a year (finding plan#2801).

        The count the monthly equivalent reads and the stride above read one
        span.  With the code table this replaced, the count would still read 1
        beside a 24-month stride.
        """
        biweekly = PayCadence(FixedDays(14))
        yearly = Cadence(interval_n=1, unit=RecurrenceUnitEnum.YEAR)
        monthly = Cadence(interval_n=1, unit=RecurrenceUnitEnum.MONTH)
        with app.app_context():
            assert monthly.units_per_year(biweekly) == Decimal("12")
            assert yearly.units_per_year(biweekly) == Decimal("1")
            db.session.execute(sqlalchemy.text(
                "UPDATE ref.recurrence_units SET months_per_unit = 24 "
                "WHERE name = 'year'"
            ))
            ref_cache.init(db.session)
            try:
                # 12 months a year over 24 months a unit.
                assert yearly.units_per_year(biweekly) == Decimal("0.5")
            finally:
                db.session.rollback()
                ref_cache.init(db.session)
            assert yearly.units_per_year(biweekly) == Decimal("1")
