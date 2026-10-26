"""A line's cadence: which paycheck of a month is its third or its first, and the frequency read
once per basis from the line's recurrence rule.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.paycheck_calculator._calendar_questions import _is_third_paycheck, _month_ordinal
from app.services.paycheck_calculator._lines import _LineContext, _priced_lines
from app.services.pay_calendar import DerivedPeriod
from tests._test_helpers import payroll_basis

from ._helpers import (
    TWO_PLACES,
    _timing_id,
    _period,
    FakeRule,
    twenty_four_rule,
    FakeDeduction,
    FakeProfile,
)


class TestThirdPaycheckDetection:
    """Tests for ``_month_ordinal`` and the ``_is_third_paycheck`` rule on it.

    They were two functions each scanning a caller-supplied period list until
    plan step **balance:X-bh-1** -- ``_is_third_paycheck(period, all_periods)``
    and ``_is_first_paycheck_of_month(period, all_periods)``.  Both asked one
    question of one payday, so both are now the ORDINAL, counted off the
    owner's calendar, and the cases below grade the number and the two
    predicates that read it.
    """

    def test_month_with_two_paychecks_places_the_second(self):
        """Standard month with 2 paychecks: the second is ordinal 2."""
        p1 = _period(start_date=date(2026, 2, 13), period_id=1)
        p2 = _period(start_date=date(2026, 2, 27), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 2, 13),  # $60,000.00 a year / 26
        ), [p1, p2])

        assert _month_ordinal(basis.calendar, p2.start_date) == 2
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p2.start_date)) is False

    def test_third_paycheck_of_the_month_is_ordinal_three(self):
        """Month with 3 paydays: the third is ordinal 3 and reads as third."""
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        ), [p1, p2, p3])

        assert _month_ordinal(basis.calendar, p3.start_date) == 3
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p3.start_date)) is True

    def test_first_period_of_a_three_paycheck_month_is_ordinal_one(self):
        """The first payday of a 3-paycheck month is not a 3rd paycheck."""
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        ), [p1, p2, p3])

        assert _month_ordinal(basis.calendar, p1.start_date) == 1
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p1.start_date)) is False

    def test_a_neighbouring_month_does_not_count(self):
        """Only the payday's OWN calendar month is counted.

        The two January paydays sit within a cadence of the February one, so a
        rule that counted a window rather than a month would place the
        February payday at 3 and skip a 24-per-year deduction on it.
        """
        jan1 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan2 = _period(start_date=date(2026, 1, 16), period_id=2)
        jan3 = _period(start_date=date(2026, 1, 30), period_id=3)
        feb = _period(start_date=date(2026, 2, 13), period_id=4)
        basis = payroll_basis(
            FakeProfile(pay=Decimal("2307.69")), [jan1, jan2, jan3, feb],  # $60,000.00 a year / 26
        )

        assert _month_ordinal(basis.calendar, feb.start_date) == 1

    def test_a_payday_past_the_horizon_is_placed_by_the_cadence(self):
        """A projected payday is counted, not answered as if it stood alone.

        The calendar holds 2026-01-02 and 2026-01-16 only, so its horizon is
        2026-01-29 and every later payday is projected at the 14-day cadence:
        01-30, 02-13, 02-27, 03-13, 03-27.  Counting only SAVED paydays would
        place BOTH March paydays at 0 -- so a 24-per-year deduction could
        never be skipped past the horizon and a 12-per-year one could never be
        taken.  The count projects forward instead (``pay_calendar:R-PC9``).
        """
        jan1 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan2 = _period(start_date=date(2026, 1, 16), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        ), [jan1, jan2])
        # Built directly rather than through ``_period``: a period past the
        # horizon carries ``period_id = None``, which is exactly what marks it
        # unmaterialised, and that helper numbers its index off the id.
        first_of_march = DerivedPeriod(
            period_id=None, period_index=6, start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 26), end_is_projected=True,
        )
        second_of_march = DerivedPeriod(
            period_id=None, period_index=7, start_date=date(2026, 3, 27),
            end_date=date(2026, 4, 9), end_is_projected=True,
        )

        assert _month_ordinal(basis.calendar, first_of_march.start_date) == 1
        assert _month_ordinal(basis.calendar, second_of_march.start_date) == 2

    def test_a_payday_below_the_opening_is_counted_for_a_STATED_owner(self):
        """The rhythm runs backwards too, which is plan step balance:X-bh-2.

        The calendar opens 2026-01-16, so the January payday two weeks before
        it -- 2026-01-02 -- is one the owner was really paid on and the app
        holds no row for.  It is counted once they SAY their paychecks began
        earlier: the opening payday then reads 2 and the one after it reads 3,
        so a 24-per-year deduction is SKIPPED on 2026-01-30 where before this
        step it was taken, and a 12-per-year one stops being taken on
        2026-01-16.  That is ledger row **N-390** on the month side.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(
            FakeProfile(
                pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            ), [opening, later],
            history_opens_on=date(2025, 1, 1),
        )

        assert _month_ordinal(basis.calendar, opening.start_date) == 2
        assert _month_ordinal(basis.calendar, later.start_date) == 3

    def test_an_UNSTATED_owner_counts_only_the_record(self):
        """THE CONTROL, and the reading every owner starts at.

        The same two paydays for somebody nobody has asked: nothing runs below
        the record, so the ordinals are 1 and 2 -- exactly what this calendar
        answered for every owner before plan step balance:X-bh-2.  ``NULL`` is
        an absence, not a claim (ruling **balance:R-IA** as amended
        2026-08-31), and the safe direction for an absence is to count less.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
        ),
                              [opening, later])

        assert _month_ordinal(basis.calendar, opening.start_date) == 1
        assert _month_ordinal(basis.calendar, later.start_date) == 2

    def test_a_floor_ON_the_opening_payday_also_counts_only_the_record(self):
        """The owner whose first paycheck IS the first one recorded.

        Distinct from the case above in what it MEANS -- a statement rather
        than an absence -- and identical in what it answers, which is why both
        stand: ``pay_calendar:R-PC14`` calls this owner ordinary, and the
        engine must not tell them apart.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(
            FakeProfile(
                pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            ), [opening, later],
            history_opens_on=opening.start_date,
        )

        assert _month_ordinal(basis.calendar, opening.start_date) == 1
        assert _month_ordinal(basis.calendar, later.start_date) == 2


class TestFirstPaycheckOfMonth:
    """The 12-per-year deduction cadence, which is ordinal 1 and only that."""

    def test_first_period_in_month_is_ordinal_one(self):
        p1 = _period(start_date=date(2026, 3, 6), period_id=1)
        p2 = _period(start_date=date(2026, 3, 20), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 3, 6),  # $60,000.00 a year / 26
        ), [p1, p2])

        assert _month_ordinal(basis.calendar, p1.start_date) == 1

    def test_second_period_in_month_is_not_ordinal_one(self):
        p1 = _period(start_date=date(2026, 3, 6), period_id=1)
        p2 = _period(start_date=date(2026, 3, 20), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 3, 6),  # $60,000.00 a year / 26
        ), [p1, p2])

        assert _month_ordinal(basis.calendar, p2.start_date) == 2


# ── salary:R15-b: the basis READS each line's cadence ────────────────


class TestTheBasisReadsEachLinesCadence:
    """A line's frequency is its recurrence rule's answer, read once per basis.

    Plan step **salary:R15-b** (rulings **R-SAL3**, **R-SAL29**).  Both
    deduction passes ask :meth:`PayrollBasis.line_applies_on`, which
    resolves each line's rule ONCE and walks it ONCE through the occurrence
    walk every recurring definition is generated by; a line with no rule is
    every paycheck.  The retired ``deductions_per_year`` compared a stored
    count against the payday's month ordinal per paycheck and, for a capped
    line, per prior payday of the year (ledger row SAL-556).
    """

    @staticmethod
    def _january():
        return [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
            _period(start_date=date(2026, 2, 13), period_id=4),
        ]

    def test_a_line_with_no_rule_is_every_paycheck(self):
        """R-SAL3's NULL: no rule, taken on every payday the calendar names."""
        line = FakeDeduction(name="401k", amount="200")
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        assert all(basis.line_applies_on(line, p.start_date) for p in self._january())

    def test_a_fake_with_no_rule_attribute_at_all_is_every_paycheck(self):
        """A duck-typed line that never heard of rules reads as the column's old 26."""
        line = FakeDeduction(name="401k", amount="200")
        del line.recurrence_rule
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        assert basis.line_applies_on(line, date(2026, 1, 30))

    def test_the_rule_is_resolved_and_walked_once_per_basis(self, monkeypatch):
        """Twenty asks, one resolution, one walk: the memo is per basis, not per ask."""
        # pylint: disable=import-outside-toplevel
        from app.services import payroll_basis as module

        line = FakeDeduction(
            name="Health", amount="100", recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        resolves, walks = [], []
        real_resolve, real_walk = module.resolve, module.projected_occurrence_placements

        def counted_resolve(*args, **kwargs):
            resolves.append(args)
            return real_resolve(*args, **kwargs)

        def counted_walk(*args, **kwargs):
            walks.append(args)
            return real_walk(*args, **kwargs)

        monkeypatch.setattr(module, "resolve", counted_resolve)
        monkeypatch.setattr(module, "projected_occurrence_placements", counted_walk)
        answers = [
            basis.line_applies_on(line, p.start_date)
            for p in self._january() * 5
        ]
        assert answers[:4] == [True, True, False, True]
        assert len(resolves) == 1 and len(walks) == 1

    def test_the_walk_reaches_paydays_past_the_saved_schedule(self):
        """A projected payday years past the record is admitted or skipped by the same rule.

        The engine prices projected paychecks (a 20-year retirement
        projection), so the memo walks past the saved horizon as far as it
        is asked: June 2028 holds three paydays on this rhythm, two and a
        half years past the seeded schedule, and the 24 line is taken on its
        first two and skipped on its third.
        """
        line = FakeDeduction(
            name="Health", amount="100", recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        rhythm = [date(2026, 1, 2) + timedelta(days=14 * k) for k in range(80)]
        june_2028 = [d for d in rhythm if (d.year, d.month) == (2028, 6)]
        assert len(june_2028) == 3, june_2028
        assert [basis.line_applies_on(line, d) for d in june_2028] == [True, True, False]
        may_2028 = [d for d in rhythm if (d.year, d.month) == (2028, 5)]
        assert len(may_2028) == 2, may_2028
        assert all(basis.line_applies_on(line, d) for d in may_2028)

    def test_lines_with_one_cadence_share_one_walk(self, monkeypatch):
        """Eleven 24-per-year lines resolve to one cadence and are walked ONCE.

        The developer's eleven lines are exactly this shape (measured
        2026-09-13 on a clone: one walk instead of eleven took the 63-paycheck
        pricing from 800 ms to 9 ms beside the base tree's 5 ms).
        """
        # pylint: disable=import-outside-toplevel
        from app.services import payroll_basis as module

        lines = [
            FakeDeduction(
                name=f"line {n}", amount="10",
                recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
            )
            for n in range(11)
        ]
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=lines,
        ), self._january())
        walks = []
        real_walk = module.projected_occurrence_placements
        monkeypatch.setattr(
            module, "projected_occurrence_placements",
            lambda *a, **k: walks.append(a) or real_walk(*a, **k),
        )
        for line in lines:
            for p in self._january():
                basis.line_applies_on(line, p.start_date)
        assert len(walks) == 1
        assert len({id(c) for c in basis._line_cadences.values()}) == 1  # pylint: disable=protected-access

    def test_the_walk_extends_when_asked_past_its_reach(self, monkeypatch):
        """Every walk reaches a year past the ask, and a re-walk at least doubles the span.

        Asked 2026-01-02 the walk reaches 2027-01-03; asked 2028-06-16 it
        must extend, to max(2028-06-16 + 366 days = 2029-06-17, the doubled
        span 2028-01-04) = 2029-06-17; asked 2028-06-02 and 2028-06-30 (June
        2028's first and third paydays), both within reach, no walk.  The
        year past the ask is what an adversarial review of this step showed
        to be load-bearing (the containing-date case below).
        """
        # pylint: disable=import-outside-toplevel
        from app.services import payroll_basis as module

        line = FakeDeduction(
            name="Health", amount="100", recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        reaches = []
        real_walk = module.projected_occurrence_placements
        monkeypatch.setattr(
            module, "projected_occurrence_placements",
            lambda *a, **k: reaches.append(k["through"]) or real_walk(*a, **k),
        )
        basis.line_applies_on(line, date(2026, 1, 2))
        basis.line_applies_on(line, date(2026, 12, 18))
        assert reaches == [date(2027, 1, 3)]
        basis.line_applies_on(line, date(2028, 6, 16))
        assert reaches == [date(2027, 1, 3), date(2029, 6, 17)]
        assert basis.line_applies_on(line, date(2028, 6, 2)) is True
        assert basis.line_applies_on(line, date(2028, 6, 30)) is False  # June's third
        assert reaches == [date(2027, 1, 3), date(2029, 6, 17)]

    def test_a_containing_date_occurrence_after_the_payday_is_admitted_at_the_reach(self):
        """An occurrence up to a period AFTER a payday places on it; the reach must cover it.

        Reproduced by the review of this step on the first memo shape, which
        set the reach exactly at the payday asked: a monthly-on-the-15th line
        under CONTAINING_DATE places 2028-01-15 on the paycheck of 2028-01-14,
        and an ask for 2028-01-14 that jumped past the reach walked through
        2028-01-14, missed the 15th, and CACHED the wrong ``False``.  Neither
        migrated shape can meet it (a paycheck occurrence IS its payday, and
        a monthly-first occurrence precedes its paycheck), and neither does a
        form-authored line: R15-c derives a monthly line's first occurrence
        as the 1st (ruling R-SAL36), so the 15th here is the engine's own
        contract, graded on a shape a hand-authored rule can still state --
        a CONTAINING_DATE monthly line from the 1st meets the same reach
        concern whenever the 1st falls the day after a payday.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum

        fifteenth = FakeRule(
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 1, 15),
        )
        line = FakeDeduction(name="Union dues", amount="25", recurrence_rule=fifteenth)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        assert basis.line_applies_on(line, date(2026, 1, 2)) is True   # Jan 2-15 holds the 15th
        # A far ask, then the same payday asked again: both must be True.
        assert basis.line_applies_on(line, date(2028, 1, 14)) is True  # Jan 14-27 holds the 15th
        assert basis.line_applies_on(line, date(2028, 1, 14)) is True
        assert basis.line_applies_on(line, date(2028, 1, 28)) is False

    def test_the_cumulative_asks_no_month_ordinal(self):
        """The capped line's year-to-date replays membership, not the month (SAL-556).

        Structural: the lines module (``_deductions`` until plan step
        salary:R18-b) no longer imports the ordinal reader at all, so the
        per-prior-payday month derivation the ledger row measured cannot be
        reintroduced without this line noticing.
        """
        # pylint: disable=import-outside-toplevel
        import inspect
        from app.services.paycheck_calculator import _lines as module

        source = inspect.getsource(module)
        assert "_month_ordinal" not in source
        assert "line_applies_on" in source

    def test_a_capped_twenty_four_line_caps_over_the_paydays_it_is_taken_on(self):
        """The year-to-date sums only admitted paydays: the cap lands one paycheck later than it would at 26."""
        line = FakeDeduction(
            name="HSA", amount="600", annual_cap="1500",
            recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        periods = self._january()
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        basis = payroll_basis(profile, periods)
        gross = (Decimal("60000") / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        amounts = [
            [l.amount for l in _priced_lines(
                _LineContext(basis, p.start_date, gross), _timing_id("pre_tax_deduction"),
            )]
            for p in periods
        ]
        # Jan 2 and Jan 16 taken (600, 600 -> 1200 of the 1500 cap); Jan 30 is
        # the third paycheck, skipped; Feb 13 takes the remaining 300.
        assert amounts == [[Decimal("600.00")], [Decimal("600.00")], [], [Decimal("300.00")]]
