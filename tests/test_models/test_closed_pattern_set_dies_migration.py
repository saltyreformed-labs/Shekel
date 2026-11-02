"""Tests for the d9f5c1a48b73 "the closed pattern set dies" migration.

Plan step **R7c-c**, the CONTRACT half of the recurrence redesign's expand /
migrate / contract: it re-points ``interval_n`` off the closed-set encoding and
drops ``pattern_id``, ``day_of_month``, ``month_of_year``, ``start_date``,
``start_period_id``, ``offset_periods`` and the empty
``budget.recurrence_month_anchors``.

**What this file grades is the migration's two REFUSALS and the mapping they
share**, which nothing executed when the step was built -- against the standard
the migration's own docstring sets for them ("a refusal nothing executes is a
refusal nobody has seen work").  Both were driven by hand during an adversarial
review and both fired correctly; this is that drive made repeatable.

Three subjects, and none of them re-runs the migration:

* :func:`_pattern_for`, the closed set's INVERSE -- pure, and the piece a later
  edit gets wrong silently, because its exact-match-then-fallback ORDER is what
  decides whether ``(1, period)`` comes back as ``Every Period`` or as
  ``Every N Periods``.  Both resolve identically, so no behavioural test
  downstream can tell them apart, and only the named one round-trips.
* :func:`refuse_unencodable_cadences`, the downgrade's guard.  Driven against
  the live database, which needs NO DDL: it reads only columns that survive at
  head.
* the SQL spelling of the scheduling day, graded against the Python one over
  planted rules.  The migration's claim is that ``_authoring._author``'s
  expression, ``recurrence.scheduling_day_of_month`` and this SQL are the SAME
  rule; the equality was measured on a production clone and never asserted.

The upgrade's own refusal, :func:`refuse_unequal_scheduling_day`, is not driven
end to end here: its WHERE clause reads ``day_of_month``, which this revision
drops, so executing it at head would need the column back and DDL inside an
xdist worker takes locks on a database every other worker shares.  What that
refusal actually asks -- "does the SQL derivation equal the Python one" -- is
:class:`TestTheSqlDerivationIsThePythonOne` below, over the cases the two could
disagree on.  That is the half a future edit can break; the ``IS DISTINCT FROM``
comparison around it cannot drift on its own.

What R7c-c RETIRED, and why that is not a coverage regression
-------------------------------------------------------------

Two migration-grading files lost their subject to this revision's drops, and
both losses are stated rather than left to be noticed:

* ``tests/test_models/test_recurrence_start_bound_fold.py`` is DELETED (276
  lines).  It graded migration ``c4e1a8b70f36``'s fold -- plan step R7b-4's
  collapse of ``start_period_id`` plus ``start_date`` into the date alone --
  by running that revision's own SQL text over five planted shapes, the
  complete space of its predicate, with the last two as firing controls.  Both
  columns it reads are dropped here, so the statement raises ``UndefinedColumn``
  against a database at head before it can reach an assertion.  Not "it passes
  trivially", which would be the free pass ``docs/plans/verification.md``
  standard 3 warns about -- it cannot run at all.
* ``tests/test_models/test_recurrence_two_axis_backfill.py`` kept its file and
  lost its backfill half, for the same reason and with the same statement in
  its own docstring.

**The migrations themselves are unaffected and still exercised.**  Alembic runs
each at its own point in the chain, before the drop, on every build of the test
template; the evidence for what they did is in ``c4e1a8b70f36``'s commit and in
``370a30cc`` / ``900e761a``, whose messages carry the measured matrices.  A
migration is graded where it executes, and grading one against a schema
revisions ahead of it fails it for a change it cannot see.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.services.recurrence import scheduling_day_of_month
from tests._test_helpers import bare_expense_template, load_migration_module

_MIGRATION_FILENAME = "d9f5c1a48b73_the_closed_pattern_set_dies.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

# pylint: disable=protected-access
_pattern_for = _MIGRATION._pattern_for
_DERIVED_SCHEDULING_DAY = _MIGRATION._DERIVED_SCHEDULING_DAY
# pylint: enable=protected-access


def _plant_rule(seed_user, unit, placement, *, starts_on, nominal_day=None,
                interval_n=1):
    """Add and flush a rule with the stated cadence, on its own definition.

    Constructed rather than authored, for the reason the picker suite's own
    fixture is: ``author_rule`` resolves before it writes, and several of the
    cadences below exist precisely to be REFUSED.

    **It takes the fixture dict rather than a user id, since plan step R-F6**:
    the owning FK is on ``budget.recurrence_rules``, so a planted rule needs a
    definition to belong to and this builds a cadence-less one to be it.

    Args:
        seed_user: The ``seed_user`` fixture dict, whose user owns the rule.
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.
        placement: A :class:`~app.enums.PeriodPlacementEnum` member.
        starts_on: The rule's first occurrence.
        nominal_day: The day a clamped first occurrence MEANT, or ``None``.
        interval_n: How many units pass between occurrences.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    rule = RecurrenceRule(
        transaction_template_id=bare_expense_template(
            db.session, seed_user,
        ).id,
        interval_n=interval_n,
        unit_id=ref_cache.recurrence_unit_id(unit),
        placement_id=ref_cache.period_placement_id(placement),
        shift_id=ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE),
        starts_on=starts_on,
        nominal_day=nominal_day,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


class TestChaining:
    """The revision sits where it claims in the Alembic chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "d9f5c1a48b73"
        assert _MIGRATION.down_revision == "b6d41f0a9c27"

    def test_it_is_reviewed(self):
        """Destructive DDL carries the ``Review:`` line the rules require."""
        assert "Review:" in _MIGRATION.__doc__


class TestThePatternInverse:
    """``_pattern_for``: which closed-set pattern stores a given reading."""

    @pytest.mark.parametrize(
        ("interval_n", "unit_name", "placement_name", "pattern", "stored"),
        [
            # The six patterns that NAME their interval store 1 in the column,
            # because that is what the closed set did -- the whole defect this
            # revision's migration re-points.
            (1, "period", "containing_date", "Every Period", 1),
            (1, "month", "containing_date", "Monthly", 1),
            (1, "month", "period_starting_on_or_after", "Monthly First", 1),
            (3, "month", "containing_date", "Quarterly", 1),
            (6, "month", "containing_date", "Semi-Annual", 1),
            (1, "year", "containing_date", "Annual", 1),
            # The one pattern that reads the column keeps the authored count.
            (2, "period", "containing_date", "Every N Periods", 2),
            (4, "period", "containing_date", "Every N Periods", 4),
            (26, "period", "containing_date", "Every N Periods", 26),
        ],
    )
    def test_each_storable_reading_maps_to_its_pattern(
        self, interval_n, unit_name, placement_name, pattern, stored,
    ):
        """Every reading the app could author before this revision round-trips.

        The ``stored`` half is asserted beside the name because the pair is one
        answer: restoring ``Quarterly`` with the authored 3 still in the column
        would give the target revision a row saying "every 3 months" twice, and
        its resolver reads the pattern.
        """
        assert _pattern_for(interval_n, unit_name, placement_name) == (
            pattern, stored,
        )

    def test_one_paycheck_takes_the_NAMED_pattern_not_the_free_one(self):
        """The exact match is tried BEFORE the interval-free fallback.

        ``(1, period, containing_date)`` matches two entries -- ``Every
        Period`` exactly, and ``Every N Periods`` through the ``None`` key --
        and both resolve to the same schedule, so no behavioural test
        downstream can tell a wrong choice from a right one.  Only the named
        one round-trips a rule that was stored that way, which is what this
        migration's own measurement ("0 of 46 differ") depends on.
        """
        assert _pattern_for(1, "period", "containing_date") == (
            "Every Period", 1,
        )

    @pytest.mark.parametrize(
        ("label", "interval_n", "unit_name", "placement_name"),
        [
            # This revision is what makes each of these authorable, so none has
            # a pattern to come back as.
            ("every other month", 2, "month", "containing_date"),
            ("every 4 months", 4, "month", "containing_date"),
            ("quarterly, first paycheck", 3, "month",
             "period_starting_on_or_after"),
            ("every 2 years", 2, "year", "containing_date"),
            ("the WEEK unit", 1, "week", "containing_date"),
        ],
    )
    def test_a_cadence_the_closed_set_cannot_name_returns_none(
        self, label, interval_n, unit_name, placement_name,
    ):
        """``None`` rather than the nearest pattern.

        The nearest pattern to "every 2 months" is Monthly, which generates
        twice as many rows as the rule says, forever.  ``None`` is what makes
        :func:`refuse_unencodable_cadences` able to name the rule instead.
        """
        assert _pattern_for(interval_n, unit_name, placement_name) is None, (
            label
        )


class TestTheDowngradeRefusesWhatItCannotEncode:
    """``refuse_unencodable_cadences``, driven against the live database.

    No DDL: it reads ``interval_n`` and the two axis ids, all of which survive
    at head, which is what lets the guard that matters most be a real test
    rather than a hand drive on a clone.
    """

    def test_the_live_database_passes(self, app, seed_user):
        """The seeded database carries nothing the closed set cannot name.

        The premise every case below rests on -- without it, a refusal that
        raised unconditionally would look like it was catching the planted row.
        """
        assert seed_user
        with app.app_context():
            _MIGRATION.refuse_unencodable_cadences(db.session)

    def test_an_every_other_month_rule_is_refused_by_id(self, app, seed_user):
        """The cadence this revision makes authorable stops the downgrade.

        Naming the id is the point: the operator has to be able to find the
        row, and the alternative the message rules out -- seating it on
        Monthly -- would silently double every occurrence it generates.
        """
        with app.app_context():
            rule = _plant_rule(
                seed_user,
                RecurrenceUnitEnum.MONTH,
                PeriodPlacementEnum.CONTAINING_DATE,
                starts_on=date(2026, 3, 15),
                interval_n=2,
            )

            with pytest.raises(RuntimeError) as exc:
                _MIGRATION.refuse_unencodable_cadences(db.session)

            message = str(exc.value)
            assert f"id={rule.id}" in message
            assert "every 2 month" in message
            assert "RESTORING THE DATABASE" in message

    def test_a_week_unit_rule_is_refused(self, app, seed_user):
        """The unit the closed set has no name for at any interval.

        A second shape, because the first is refused by its INTERVAL and this
        one by its UNIT -- a mapping that special-cased intervals alone would
        pass the case above and seat this row on nothing.
        """
        with app.app_context():
            rule = _plant_rule(
                seed_user,
                RecurrenceUnitEnum.WEEK,
                PeriodPlacementEnum.CONTAINING_DATE,
                starts_on=date(2026, 3, 15),
                interval_n=1,
            )

            with pytest.raises(RuntimeError) as exc:
                _MIGRATION.refuse_unencodable_cadences(db.session)

            assert f"id={rule.id}" in str(exc.value)


class TestTheSqlDerivationIsThePythonOne:
    """The migration's central claim, asserted rather than measured once.

    ``day_of_month`` is dropped four steps ahead of its reader on the argument
    that the column was a DERIVED ENCODING, so ``compute_due_date`` can read
    the derivation instead.  Three implementations have to agree for that to
    hold -- ``_authoring._author``'s original expression, the Python
    ``scheduling_day_of_month`` that replaced it, and the SQL below that graded
    the live rows before the ``ALTER TABLE``.  The first two are one function
    now; these cases grade the third against it.

    If they ever disagree, every row the affected rules generate changes date
    silently, which is the exact harm the migration's refusal exists to stop.
    """

    #: ``(label, unit, placement, starts_on, nominal_day)``.
    _CASES = (
        # The ordinary monthly bill: the derived day is the date's own.
        (
            "monthly on the 15th", RecurrenceUnitEnum.MONTH,
            PeriodPlacementEnum.CONTAINING_DATE, date(2026, 3, 15), None,
        ),
        # A month-end rule, where nominal_day is what the date lost.  The
        # derivation must prefer it over the date's day, or a 31st rule reads
        # as the 30th forever.
        (
            "month-end, clamped by April", RecurrenceUnitEnum.MONTH,
            PeriodPlacementEnum.CONTAINING_DATE, date(2026, 4, 30), 31,
        ),
        # THE case the two day questions disagree about: a monthly rule funded
        # from the month's FIRST paycheck fires on days of the month, but
        # day_of_month was always NULL for it and NULL is what dates the row
        # from its paycheck.  A derivation gated on the unit rather than the
        # anchor family answers 15 here and moves 11 rows (ledger row D26).
        (
            "monthly FIRST paycheck", RecurrenceUnitEnum.MONTH,
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            date(2026, 3, 15), None,
        ),
        # A paycheck cadence names no day of the month at all.
        (
            "every paycheck", RecurrenceUnitEnum.PERIOD,
            PeriodPlacementEnum.CONTAINING_DATE, date(2026, 3, 15), None,
        ),
        # The YEAR unit anchors on a day of the month like MONTH does.
        (
            "annual", RecurrenceUnitEnum.YEAR,
            PeriodPlacementEnum.CONTAINING_DATE, date(2026, 7, 4), None,
        ),
        # A weekly cadence names no day of the month: the SQL CASE has no
        # arm for it and derives NULL, and the Python answers None.  Until
        # plan step R8-b the Python REFUSED the unit instead, because None
        # meant "date this row from its paycheck"; R8-b dates a weekly row
        # from its occurrence, so the two now agree here too.
        (
            "every week", RecurrenceUnitEnum.WEEK,
            PeriodPlacementEnum.CONTAINING_DATE, date(2026, 3, 15), None,
        ),
    )

    @pytest.mark.parametrize(
        ("label", "unit", "placement", "starts_on", "nominal_day"), _CASES,
    )
    def test_the_two_implementations_agree(
        self, app, seed_user, label, unit, placement, starts_on, nominal_day,
    ):
        """The SQL CASE and ``scheduling_day_of_month`` answer the same day.

        Run over a REAL row through the migration's own SQL text, joined the
        way the migration joins it, so the expression under test is the one
        that ships rather than a paraphrase of it.
        """
        with app.app_context():
            rule = _plant_rule(
                seed_user, unit, placement,
                starts_on=starts_on, nominal_day=nominal_day,
            )

            derived = db.session.execute(
                text(
                    f"""
                    SELECT ({_DERIVED_SCHEDULING_DAY}) AS derived_day
                    FROM budget.recurrence_rules r
                    JOIN ref.recurrence_units u ON u.id = r.unit_id
                    JOIN ref.period_placements p ON p.id = r.placement_id
                    WHERE r.id = :rule_id
                    """
                ),
                {"rule_id": rule.id},
            ).scalar_one()

            expected = scheduling_day_of_month(rule)
            assert (
                None if derived is None else int(derived)
            ) == expected, label

    def test_the_first_paycheck_case_really_is_none(self, app, seed_user):
        """The premise the sweep above would otherwise assert vacuously.

        Both implementations answering ``None`` for every case would pass every
        arm of the sweep.  This pins the ONE cadence where ``None`` is the
        surprising answer -- its occurrences DO land on days of the month --
        so an agreement built out of two broken derivations cannot hide here.
        """
        with app.app_context():
            rule = _plant_rule(
                seed_user,
                RecurrenceUnitEnum.MONTH,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                starts_on=date(2026, 3, 15),
            )

            assert scheduling_day_of_month(rule) is None

    def test_an_ordinary_monthly_rule_really_answers_its_day(
        self, app, seed_user,
    ):
        """The other half of the same guard: a real day, not just non-None."""
        with app.app_context():
            rule = _plant_rule(
                seed_user,
                RecurrenceUnitEnum.MONTH,
                PeriodPlacementEnum.CONTAINING_DATE,
                starts_on=date(2026, 4, 30),
                nominal_day=31,
            )

            assert scheduling_day_of_month(rule) == 31
