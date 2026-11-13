"""Unit tests for the rate-period amortization engine.

Pure-function tests (no database).  Every monetary expectation is
hand-computed with the arithmetic shown in a comment, per the project's
testing standards.  Interest accrues monthly as ``balance * rate / 12``
and is rounded HALF_UP to cents at each step.

*The replay's three classes -- ``TestReplaySchedule``,
``TestReplayTakesTheWholeFeed`` and ``TestReplayScheduleLatePayment`` -- went
with ``replay_schedule`` at plan step recurrence:R16-e (ruling plan#3382,
finding D60's third walk).  What they pinned the walk no longer does: the
loan's one walk charges and allocates every payment
(``test_loan_contract_calendar``), and the contract line's start is
``test_the_contract_line_start``'s.*
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import RateChangeRecord
from app.services.installment_calendar import monthly_due_date
from app.services.rate_period_engine import (
    LoanTerms,
    build_rate_periods,
    payment_number,
    period_for_date,
)

ORIGINATION = date(2020, 1, 1)


def _fixed_loan_periods():
    """A $300,000 / 6% / 30-year fixed-rate loan as a single period."""
    terms = LoanTerms(
        origination_date=ORIGINATION,
        original_principal=Decimal("300000.00"),
        base_rate=Decimal("0.06"),
        term_months=360,
        is_arm=False,
        arm_first_adjustment_months=None,
        arm_adjustment_interval_months=None,
        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
    )
    return build_rate_periods(
        terms=terms, rate_changes=None, recorded_period_pi=None,
    )


def _arm_periods(rate_changes=None, recorded_period_pi=None):
    """A $400,000 / 6% / 30-year 5/5 ARM (first adj at 60mo, then every 60)."""
    terms = LoanTerms(
        origination_date=ORIGINATION,
        original_principal=Decimal("400000.00"),
        base_rate=Decimal("0.06"),
        term_months=360,
        is_arm=True,
        arm_first_adjustment_months=60,
        arm_adjustment_interval_months=60,
        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
    )
    return build_rate_periods(
        terms=terms,
        rate_changes=rate_changes,
        recorded_period_pi=recorded_period_pi,
    )


class TestBuildRatePeriods:
    """build_rate_periods structure and per-period P&I derivation."""

    def test_fixed_rate_is_single_period(self):
        """A non-ARM loan collapses to one period spanning the full term.

        period_pi = amortize(300000, 0.06, 360):
          i = 0.06/12 = 0.005; (1.005)^360 = 6.0225752
          M = 300000 * 0.005 * 6.0225752 / (6.0225752 - 1)
            = 1500 * 6.0225752 / 5.0225752 = 1798.65
        """
        periods = _fixed_loan_periods()
        assert len(periods) == 1
        period = periods[0]
        assert period.index == 0
        assert period.start_date == ORIGINATION
        assert period.annual_rate == Decimal("0.06")
        assert period.start_month_index == 0
        assert period.term_months_at_start == 360
        assert period.period_pi == Decimal("1798.65")

    def test_arm_period_zero_pi(self):
        """Origination period P&I amortizes the original principal over the term.

        period_pi = amortize(400000, 0.06, 360):
          M = 2000 * 6.0225752 / 5.0225752 = 2398.20
        """
        periods = _arm_periods()
        assert periods[0].period_pi == Decimal("2398.20")

    def test_arm_builds_periods_on_cadence(self):
        """5/5 ARM yields periods at 0, 60, 120, 180, 240, 300 months."""
        periods = _arm_periods()
        assert [p.start_month_index for p in periods] == [0, 60, 120, 180, 240, 300]
        assert [p.start_date for p in periods] == [
            date(2020, 1, 1), date(2025, 1, 1), date(2030, 1, 1),
            date(2035, 1, 1), date(2040, 1, 1), date(2045, 1, 1),
        ]
        assert [p.term_months_at_start for p in periods] == [
            360, 300, 240, 180, 120, 60,
        ]

    def test_period_rate_sampled_at_start(self):
        """Each period's rate is the rate in effect on its start date."""
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 1, 1), interest_rate=Decimal("0.07"),
            ),
        ]
        periods = _arm_periods(rate_changes=rate_changes)
        # Period 0 (start 2020-01-01) predates the change -> base 6%.
        assert periods[0].annual_rate == Decimal("0.06")
        # Period 1 (start 2025-01-01) is on/after the change -> 7%.
        assert periods[1].annual_rate == Decimal("0.07")
        assert periods[2].annual_rate == Decimal("0.07")

    def test_recorded_monthly_pi_used_verbatim(self):
        """A recorded recast P&I overrides the derived amortization."""
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 1, 1), interest_rate=Decimal("0.07"),
            ),
        ]
        recorded = {date(2025, 1, 1): Decimal("2600.00")}
        periods = _arm_periods(
            rate_changes=rate_changes, recorded_period_pi=recorded,
        )
        assert periods[1].period_pi == Decimal("2600.00")
        # The origination period has no recorded recast -> still derived.
        assert periods[0].period_pi == Decimal("2398.20")

    def test_recorded_rate_change_starts_period_without_arm_cadence(self):
        """A recorded rate change is a boundary even when ARM cadence is unset.

        Regression for a real-loan bug: a 30-year loan flagged is_arm but
        with arm_first_adjustment_months / arm_adjustment_interval_months
        both None, whose rate adjusted from 4.875% to 6.875% (recorded in
        RateHistory).  Before the fix the engine had no boundary for the
        change, stayed in the origination period, and showed the 4.875%
        origination payment ($1,069.00) instead of recasting at 6.875%.

        origination 2018-12-01, $202,000, 360 months:
          period 0 [2018-12-01, 2023-12-01) @ 4.875%:
            pi = amortize(202000, 0.04875, 360) = 1,069.00
          period 1 [2023-12-01, ...) @ 6.875%, term_at_start = 300:
            pi = amortize(~185,162 month-60 balance, 0.06875, 300)
               = 1,293.96  (the lender's recast)
        """
        terms = LoanTerms(
            origination_date=date(2018, 12, 1),
            original_principal=Decimal("202000.00"),
            base_rate=Decimal("0.04875"),
            term_months=360,
            is_arm=True,
            arm_first_adjustment_months=None,
            arm_adjustment_interval_months=None,
            convention=InterestAccrualConventionEnum.MONTHLY_1_12,
        )
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2018, 12, 1),
                interest_rate=Decimal("0.04875"),
            ),
            RateChangeRecord(
                effective_date=date(2023, 12, 1),
                interest_rate=Decimal("0.06875"),
            ),
        ]
        periods = build_rate_periods(
            terms=terms, rate_changes=rate_changes, recorded_period_pi=None,
        )
        assert [p.start_date for p in periods] == [
            date(2018, 12, 1), date(2023, 12, 1),
        ]
        assert periods[0].annual_rate == Decimal("0.04875")
        assert periods[0].period_pi == Decimal("1069.00")
        assert periods[1].annual_rate == Decimal("0.06875")
        assert periods[1].term_months_at_start == 300
        assert periods[1].period_pi == Decimal("1293.96")
        # The current period recasts at 6.875% -> the real statement P&I.
        assert (
            period_for_date(periods, date(2026, 6, 2)).period_pi
            == Decimal("1293.96")
        )


class TestPeriodForDate:
    """period_for_date locates the governing period."""

    def test_locates_correct_period(self):
        """The latest period whose start_date <= target governs."""
        periods = _arm_periods()
        # 2027-06-15 falls in period 1 [2025-01-01, 2030-01-01).
        assert period_for_date(periods, date(2027, 6, 15)).start_month_index == 60
        # The boundary date itself belongs to the period it starts.
        assert period_for_date(periods, date(2030, 1, 1)).start_month_index == 120
        # A date before origination is governed by the origination period.
        assert period_for_date(periods, date(2019, 1, 1)).start_month_index == 0

    def test_payment_constant_across_period(self):
        """The headline fix: P&I is identical for every date inside a period.

        Two evaluation dates a year apart, both inside the second
        fixed-rate period, must return the same period_pi -- no
        month-to-month re-amortization (E-02 generalized to all periods).
        """
        recorded = {date(2025, 1, 1): Decimal("2600.00")}
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 1, 1), interest_rate=Decimal("0.07"),
            ),
        ]
        periods = _arm_periods(
            rate_changes=rate_changes, recorded_period_pi=recorded,
        )
        pi_2026 = period_for_date(periods, date(2026, 6, 1)).period_pi
        pi_2027 = period_for_date(periods, date(2027, 6, 1)).period_pi
        assert pi_2026 == pi_2027 == Decimal("2600.00")

    def test_empty_periods_raises(self):
        """An empty period list is a caller bug, surfaced loudly."""
        with pytest.raises(ValueError):
            period_for_date([], date(2026, 1, 1))


class TestPaymentNumber:
    """payment_number: continuous payment count from origination."""

    def test_first_payment_is_one(self):
        """The first contractual payment (one month after origination) is #1."""
        assert payment_number(date(2018, 12, 1), date(2019, 1, 1)) == 1

    def test_counts_whole_months_from_origination(self):
        """A payment N whole months after origination is payment N."""
        # 2018-12 origination -> 2026-06 is 90 months later (mortgage
        # account 3 in its 90th month / ~7.5 years).
        assert payment_number(date(2018, 12, 1), date(2026, 6, 1)) == 90
        # The following month continues the count, not restarting.
        assert payment_number(date(2018, 12, 1), date(2026, 7, 1)) == 91

    def test_origination_month_is_zero(self):
        """A payment in the origination month itself is payment 0 (day ignored)."""
        assert payment_number(date(2024, 1, 1), date(2024, 1, 15)) == 0


class TestMonthlyDueDate:
    """monthly_due_date: recover a payment's contractual due date."""

    def test_next_payment_day_after_period_start(self):
        """The due date is the first payment_day on or after the period start."""
        # Pay period begins 05-21; payment_day 1 -> next 1st is 06-01.
        assert monthly_due_date(date(2026, 5, 21), 1) == date(2026, 6, 1)
        # Pay period begins 03-26; payment_day 1 -> 04-01.
        assert monthly_due_date(date(2026, 3, 26), 1) == date(2026, 4, 1)

    def test_period_start_on_payment_day_returns_same_day(self):
        """When the period starts on payment_day, that day is the due date."""
        assert monthly_due_date(date(2026, 6, 1), 1) == date(2026, 6, 1)

    def test_payment_day_later_in_same_month(self):
        """A payment_day still ahead in the start month stays in that month."""
        # Period begins 05-10; payment_day 15 -> 05-15 (same month).
        assert monthly_due_date(date(2026, 5, 10), 15) == date(2026, 5, 15)

    def test_payment_day_clamped_to_short_month(self):
        """payment_day past the month length clamps to the last day."""
        # Period begins 02-05; payment_day 31 -> 02-28 (2026 not a leap year).
        assert monthly_due_date(date(2026, 2, 5), 31) == date(2026, 2, 28)

    def test_rolls_into_next_year(self):
        """A December period start rolls the due date into January."""
        # Period begins 12-20; payment_day 1 -> 2027-01-01.
        assert monthly_due_date(date(2026, 12, 20), 1) == date(2027, 1, 1)
