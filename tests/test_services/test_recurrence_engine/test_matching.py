"""Recurrence engine tests: which periods each recurrence pattern matches, its edge cases, and the
Every N Periods phase.
"""

import pytest
from datetime import date, timedelta

from app.services.recurrence import RecurrenceResolutionError
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    MONTHLY_FIRST,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)

from ._helpers import build_rule, _calendar, _matched_periods, _matches


class TestMatchMonthly:
    """The Monthly pattern, through the public producer."""

    def test_monthly_day_15(self, biweekly_periods):
        """Finds the period containing the 15th of each month."""
        matched = _matches(
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 15)),
            biweekly_periods,
        )

        # 26 biweekly periods span Jan-Dec 2026 → one match per month = 12.
        assert len(matched) == 12

        # Each matched period's range must contain the 15th of some month.
        for period in matched:
            # Check start or end month's 15th falls in range.
            found = False
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    found = True
                    break
            assert found, (
                f"Period {period.period_index} ({period.start_date}-"
                f"{period.end_date}) doesn't contain a 15th"
            )

    def test_monthly_day_31_clamped_in_february(self, biweekly_periods):
        """fires_on_day=31 clamps to 28 in Feb 2026 (non-leap year)."""
        matched = _matches(
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 31)),
            biweekly_periods,
        )

        # Find the period matched for February.
        feb_periods = [
            p for p in matched
            if any(
                dt.month == 2 and dt.year == 2026
                for dt in (p.start_date, p.end_date)
            )
            and p.start_date <= date(2026, 2, 28) <= p.end_date
        ]
        assert len(feb_periods) == 1
        feb_period = feb_periods[0]
        # Feb 28 must be within the matched period's range.
        assert feb_period.start_date <= date(2026, 2, 28) <= feb_period.end_date

    def test_monthly_day_30_clamped_in_february(self, biweekly_periods):
        """fires_on_day=30 also clamps to 28 in Feb 2026."""
        matched = _matches(
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 30)),
            biweekly_periods,
        )

        feb_periods = [
            p for p in matched
            if any(
                dt.month == 2 and dt.year == 2026
                for dt in (p.start_date, p.end_date)
            )
            and p.start_date <= date(2026, 2, 28) <= p.end_date
        ]
        assert len(feb_periods) == 1
        assert feb_periods[0].start_date <= date(2026, 2, 28) <= feb_periods[0].end_date


class TestMatchMonthlyFirst:
    """The Monthly First pattern, through the public producer."""

    def test_picks_first_period_starting_in_each_month(self, biweekly_periods):
        """One period per calendar month, the earliest starting in that month."""
        matched = _matches(
            build_rule(cadence=MONTHLY_FIRST), biweekly_periods,
        )

        # 26 biweekly periods starting Jan 2 cover all 12 months of 2026.
        assert len(matched) == 12

        # Each matched period should be the first whose start_date falls in
        # its calendar month.
        seen_months = set()
        for period in matched:
            ym = (period.start_date.year, period.start_date.month)
            assert ym not in seen_months, f"Duplicate month {ym}"
            seen_months.add(ym)

            # Verify it's actually the earliest period starting in that month.
            earlier = [
                p for p in biweekly_periods
                if (p.start_date.year, p.start_date.month) == ym
                and p.period_index < period.period_index
            ]
            assert len(earlier) == 0, (
                f"Period {period.period_index} is not the first in month {ym}"
            )


class TestMatchQuarterly:
    """The Quarterly pattern, through the public producer."""

    def test_quarterly_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan, Apr, Jul, Oct."""
        matched = _matches(
            build_rule(
                cadence=QUARTERLY, starts_on=date(2026, 1, 15),
            ),
            biweekly_periods,
        )

        # 26 biweekly periods cover Jan-Dec 2026 → 4 quarterly months.
        assert len(matched) == 4

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {1, 4, 7, 10}

    def test_quarterly_nov_start_wraps(self, biweekly_periods):
        """start_month=11 wraps: targets Nov, Feb, May, Aug."""
        matched = _matches(
            build_rule(
                cadence=QUARTERLY, starts_on=date(2026, 2, 15),
            ),
            biweekly_periods,
        )

        assert len(matched) == 4

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {2, 5, 8, 11}


class TestMatchSemiAnnual:
    """The Semi-Annual pattern, through the public producer."""

    def test_semi_annual_jan_start(self, biweekly_periods):
        """start_month=1 targets Jan and Jul."""
        matched = _matches(
            build_rule(
                cadence=SEMI_ANNUAL, starts_on=date(2026, 1, 15),
            ),
            biweekly_periods,
        )

        assert len(matched) == 2

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {1, 7}

    def test_semi_annual_aug_start_wraps(self, biweekly_periods):
        """start_month=8 wraps: targets Aug and Feb."""
        matched = _matches(
            build_rule(
                cadence=SEMI_ANNUAL, starts_on=date(2026, 2, 15),
            ),
            biweekly_periods,
        )

        assert len(matched) == 2

        matched_months = set()
        for period in matched:
            for dt in (period.start_date, period.end_date):
                target = date(dt.year, dt.month, 15)
                if period.start_date <= target <= period.end_date:
                    matched_months.add(dt.month)
        assert matched_months == {2, 8}


class TestMatchAnnual:
    """The Annual pattern, through the public producer."""

    def test_annual_one_per_year(self, biweekly_periods):
        """One match per calendar year on a specific month/day."""
        matched = _matches(
            build_rule(
                cadence=ANNUAL, starts_on=date(2026, 3, 15),
            ),
            biweekly_periods,
        )

        # All periods are in 2026, so exactly one match.
        assert len(matched) == 1

        period = matched[0]
        assert period.start_date <= date(2026, 3, 15) <= period.end_date

    def test_annual_feb29_non_leap_year(self, biweekly_periods):
        """Feb 29 target in 2026 (non-leap) clamps to Feb 28."""
        matched = _matches(
            build_rule(
                cadence=ANNUAL, starts_on=date(2026, 2, 28), nominal_day=29,
            ),
            biweekly_periods,
        )

        assert len(matched) == 1

        period = matched[0]
        # Clamped to Feb 28 since 2026 is not a leap year.
        assert period.start_date <= date(2026, 2, 28) <= period.end_date


class TestMatchPeriodsEdgeCases:
    """Edge case tests for _matched_periods() -- pure function, no DB."""

    def test_effective_from_filters_earlier_periods(self, biweekly_periods):
        """Only periods on/after effective_from are candidates."""
        rule = build_rule(cadence=EVERY_PERIOD)
        # Use the 4th period's start_date as effective_from.
        effective_from = biweekly_periods[3].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26 - 3  # Periods 3-25.
        for period in matched:
            assert period.start_date >= effective_from

    def test_an_unmodelled_unit_is_refused(self, biweekly_periods):
        """An unmodelled cadence id is REFUSED, naming the id.

        It used to log a warning and answer ``[]``, which reads as "this rule
        fires nowhere" -- a rule that generates nothing forever, silently.  An
        id the application does not MODEL is a broken invariant, not a rule
        with no occurrences: the enums are the vocabulary (plan step R2e-2) and
        the write doors refuse anything outside them.  ``resolve`` raises
        rather than fabricating a cadence.

        **The unreadable COLUMN moved at plan step R7c-c.**  This planted a
        ``pattern_id`` no member named; that column is dropped, and the state
        it leaves is a ``unit_id`` naming a ``ref.recurrence_units`` row the
        enums do not model -- the same broken invariant through the column
        that replaced it.
        """
        rule = build_rule(cadence=MONTHLY)
        rule.unit_id = 99999
        effective_from = biweekly_periods[0].start_date

        with pytest.raises(RecurrenceResolutionError, match="99999"):
            _matched_periods(rule, _calendar(biweekly_periods), effective_from)


class TestTheEveryNPeriodsPhase:
    """Which paychecks an ``Every N Periods`` rule fires on, and from what.

    **ONE input decides it, since plan step R7b-4**: the opening bound.  The
    phase is the ordinal of the paycheck that bound falls in, so a rule cannot
    state its cadence twice and there is no second value for the anchor to
    disagree with.

    What this class covered before is the history worth keeping, because it is
    what the single input replaced.  The reverse matcher read the STORED
    ``offset_periods`` column unconditionally; plan step R4a made the read
    RESOLVE instead, taking the phase from the rule's start PERIOD when the
    calendar contained that period and falling back to the column when it did
    not.  Two facts, two sources, and a fallback between them -- a neutral
    review of R4a found the gap and these tests were what closed it.  R7b-4
    removed the second source rather than the disagreement: the start period
    folded into ``start_date``, and the column became output-only.

    All 46 live rules carry ``interval_n = 1``, where the phase is inert
    (measured 2026-08-14 against a production clone), so the change cost
    ``$0.00`` and is about rules authored from here on.
    """

    def test_the_phase_comes_from_the_paycheck_the_bound_falls_in(
        self, biweekly_periods,
    ):
        """A rule starting in period 4 fires from it, every third after.

        ``4 % 3 == 1``, so the fired set is indices 4, 7, 10, ... -- the
        paycheck the user named and every third one after it.

        The bound named that paycheck by FK until plan step R7b-4 and names it
        by date now; the assertion is unchanged, which is what makes this a
        re-expression rather than a new claim.
        """
        rule = build_rule(
            cadence=EVERY_N_PERIODS,
            interval_n=3,
            starts_on=biweekly_periods[4].start_date,
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert [p.period_index for p in matched] == [4, 7, 10, 13, 16, 19, 22, 25]

    def test_a_mid_period_bound_phases_on_the_paycheck_that_contains_it(
        self, biweekly_periods,
    ):
        """The bound need not BE a payday for the phase to be exact.

        A date one day after period 4 opens is still inside period 4, so the
        rule phases at ``4 % 3 == 1`` and fires the identical set.  It is the
        paycheck the money comes out of that the cadence counts, not the
        calendar day -- which is why the derivation asks containment rather
        than equality, and why an opening bound the user typed by hand (a loan
        origination, a mid-month start) lands where they meant.
        """
        rule = build_rule(
            cadence=EVERY_N_PERIODS,
            interval_n=3,
            starts_on=biweekly_periods[4].start_date + timedelta(days=1),
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert [p.period_index for p in matched] == [4, 7, 10, 13, 16, 19, 22, 25]

    def test_a_bound_past_the_horizon_still_derives_a_phase(
        self, biweekly_periods,
    ):
        """Totality: the derivation answers past the materialised schedule.

        ``PayCalendar.span_containing`` PROJECTS the ordinal forward from the
        last saved payday at the owner's own cadence, so a bound the schedule
        has not reached still has an ordinal to take a remainder of.  Nothing
        fires -- there are no materialised periods out there to fire in -- but
        the value stays derivable, which is what plan step R7c's NOT NULL
        columns require.

        **This replaces a case about a DANGLING start-period id.**  That state
        was reachable -- the FK is ``ON DELETE SET NULL``, but a rule read into
        memory outlives the row its id named -- and it was the one branch where
        the phase fell back to the stored column.  Neither the id nor the
        fallback exists now, so the question that survives is what the
        derivation answers where the schedule stops.
        """
        past_horizon = biweekly_periods[-1].end_date + timedelta(days=365)
        rule = build_rule(
            cadence=EVERY_N_PERIODS,
            interval_n=3,
            starts_on=past_horizon,
        )

        matched = _matched_periods(
            rule, _calendar(biweekly_periods), biweekly_periods[0].start_date,
        )

        assert matched == []


class TestMatchPeriodsFull:
    """Integration tests for _matched_periods() dispatch -- pure, no DB."""

    def test_every_period_returns_all_candidates(self, biweekly_periods):
        """every_period returns all periods after effective_from filtering."""
        rule = build_rule(cadence=EVERY_PERIOD)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26

    def test_no_periods_empty_result(self):
        """Empty periods list produces an empty result."""
        rule = build_rule(cadence=EVERY_PERIOD)

        matched = _matched_periods(rule, _calendar([]),
                                 date(2026, 1, 1))

        assert matched == []


class TestMatchPeriodsEdgeCaseSafety:
    """Behaviour of period selection on values the DB CHECKs forbid.

    These values are prevented at the storage tier but could reach the
    service via FakeRule objects, direct calls, or an unflushed in-memory
    rule.  The contract differs by field:

      * ``interval_n`` is NOT NULL with CHECK ``> 0``
        (``ck_recurrence_rules_positive_interval``), so an invalid 0 / None
        is a programming error and is REFUSED, naming the value -- never
        coerced to 1, which would mis-generate on every period instead of
        every N.
      * ``day_of_month`` and ``month_of_year`` USED to be here too, with
        their own domains and their own NULL defaults.  Plan step R7c-b made
        both ENCODED columns rather than authored ones -- a rule states its
        first occurrence and the door derives them from it -- so there is no
        caller-supplied value left to refuse.  The last refusal of that
        shape, ``due_day_of_month``'s, left with its column at plan step
        R5-a (ruling R-R96).

    **Plan step R4a moved every one of these refusals to one door.**  The five
    reverse-matching helpers each failed in their own way and in their own
    place -- ``% 0`` raised ``ZeroDivisionError``, ``date(y, m, 0)`` raised
    ``ValueError``, ``monthrange(y, 13)`` raised ``ValueError``, and a day of
    32 was silently clamped -- so the same malformed rule had four different
    dispositions depending on its pattern.  It is now one:
    ``app.services.recurrence.resolve`` raises
    :class:`~app.services.recurrence.RecurrenceResolutionError` naming the
    field and the value, before any walking.
    """

    # -- interval_n edge cases (every_n_periods) --

    def test_every_n_periods_interval_zero_raises(
        self, biweekly_periods
    ):
        """interval_n=0 is refused, naming the value (fail-loud, no coercion).

        ``ck_recurrence_rules_positive_interval`` + NOT NULL make a persisted
        interval_n=0 impossible, so reaching the matcher with 0 means an
        invalid in-memory rule (a programming error).  It used to surface as a
        bare ``ZeroDivisionError`` from the phase modulo; the resolution door
        refuses it first and says which value and which rule, which is what an
        operator reading the traceback needs.
        """
        rule = build_rule(
            cadence=EVERY_N_PERIODS,
            interval_n=0,
        )
        with pytest.raises(RecurrenceResolutionError, match="interval_n"):
            _matched_periods(
                rule, _calendar(biweekly_periods),
                biweekly_periods[0].start_date,
            )

    def test_every_n_periods_interval_none_raises(
        self, biweekly_periods
    ):
        """interval_n=None raises TypeError (fail-loud, no coercion).

        Distinct failure mode from 0: None means the column default was
        not applied (an unflushed / invalid in-memory rule).  ``% None``
        raises TypeError rather than the old ``or 1`` silently treating
        the rule as every-period.
        """
        rule = build_rule(
            cadence=EVERY_N_PERIODS,
            interval_n=None,
        )
        with pytest.raises(TypeError):
            _matched_periods(
                rule, _calendar(biweekly_periods),
                biweekly_periods[0].start_date,
            )
