"""Unit tests for the rate-period amortization engine.

Pure-function tests (no database).  Every monetary expectation is
hand-computed with the arithmetic shown in a comment, per the project's
testing standards.  Interest accrues monthly as ``balance * rate / 12``
and is rounded HALF_UP to cents at each step.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import PaymentDates, RateChangeRecord
from app.services.installment_calendar import monthly_due_date
from app.services.rate_period_engine import (
    BalanceAnchor,
    LoanTerms,
    build_rate_periods,
    payment_number,
    period_for_date,
    replay_schedule,
)

ORIGINATION = date(2020, 1, 1)


def _confirmed(
    period_start: date, payment_day: int, settled_on: date | None = None,
) -> PaymentDates:
    """An ON-TIME confirmed payment: its pay period contains its due date.

    Derives the due date from the pay-period start, which is correct exactly
    while the payment's period still contains that due date -- the on-time case
    these replay tests exercise.  A payment settled LATE breaks that assumption
    and must carry its own stored due date; see
    ``TestReplayScheduleLatePayment``.

    ``settled_on`` defaults to the pay-period start, which is what the replay's
    as-of cap read BEFORE plan step X-an moved it onto the settled day.  Every
    hand-computed expectation below is therefore unchanged by that move, and the
    cases where the two dates differ are exercised explicitly (the caller passes
    a day) rather than by accident.
    """
    return PaymentDates(
        period_start=period_start,
        due_date=monthly_due_date(period_start, payment_day),
        settled_on=period_start if settled_on is None else settled_on,
    )


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


class TestReplaySchedule:
    """replay_schedule: schedule-driven, cash-independent balance."""

    def test_one_payment_reduces_by_scheduled_principal(self):
        """One confirmed payment reduces the balance by P&I minus interest.

        Fixed 6% loan, period_pi = 1798.65, anchor 300000 at 2026-01-01.
          interest   = 300000 * 0.005 = 1500.00
          principal  = 1798.65 - 1500.00 = 298.65
          balance    = 300000.00 - 298.65 = 299701.35
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 1, 1),
            ),
            payments=[
                _confirmed(d, 15)
                for d in [date(2026, 2, 15)]
            ],
            payment_day=15,
            as_of=date(2026, 2, 28),
        )
        assert result.balance_as_of == Decimal("299701.35")
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row.interest == Decimal("1500.00")
        assert row.principal == Decimal("298.65")
        assert row.payment == Decimal("1798.65")
        assert row.remaining_balance == Decimal("299701.35")

    def test_balance_independent_of_cash_amount(self):
        """The API consumes only payment DATES, so escrow/cash cannot leak.

        Two confirmed payments advance two scheduled steps regardless of
        what cash actually moved.  Step 2 interest is on the reduced
        balance:
          step1: int=1500.00, prin=298.65, bal=299701.35
          step2: int=299701.35*0.005=1498.51 (1498.50675 -> HALF_UP),
                 prin=1798.65-1498.51=300.14, bal=299701.35-300.14=299401.21
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 1, 1),
            ),
            payments=[
                _confirmed(d, 15)
                for d in [date(2026, 2, 15), date(2026, 3, 15)]
            ],
            payment_day=15,
            as_of=date(2026, 3, 31),
        )
        assert len(result.rows) == 2
        assert result.rows[1].interest == Decimal("1498.51")
        assert result.rows[1].principal == Decimal("300.14")
        assert result.balance_as_of == Decimal("299401.21")

    def test_uses_period_rate_per_step(self):
        """Each step accrues interest at its own period's rate.

        5/5 ARM, recorded period-1 P&I 2600.00, rate 7% from 2025-01-01,
        anchor 380000 at 2024-11-01:
          payment A 2024-12-15 (period 0, 6%):
            int = 380000 * 0.005 = 1900.00
            prin = 2398.20 - 1900.00 = 498.20 ; bal = 379501.80
          payment B 2025-02-15 (period 1, 7%):
            int = 379501.80 * 0.07/12 = 2213.76 (2213.7605 -> HALF_UP)
            prin = 2600.00 - 2213.76 = 386.24 ; bal = 379115.56
        """
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2025, 1, 1), interest_rate=Decimal("0.07"),
            ),
        ]
        recorded = {date(2025, 1, 1): Decimal("2600.00")}
        periods = _arm_periods(
            rate_changes=rate_changes, recorded_period_pi=recorded,
        )
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("380000.00"), as_of_date=date(2024, 11, 1),
            ),
            payments=[
                _confirmed(d, 15)
                for d in [date(2024, 12, 15), date(2025, 2, 15)]
            ],
            payment_day=15,
            as_of=date(2025, 3, 1),
        )
        row_a, row_b = result.rows
        assert row_a.interest_rate == Decimal("0.06")
        assert row_a.interest == Decimal("1900.00")
        assert row_a.remaining_balance == Decimal("379501.80")
        assert row_b.interest_rate == Decimal("0.07")
        assert row_b.interest == Decimal("2213.76")
        assert row_b.principal == Decimal("386.24")
        assert result.balance_as_of == Decimal("379115.56")

    def test_anchor_change_keeps_period_pi(self):
        """A different anchor moves the balance but never the period P&I.

        This is the engine-level decoupling behind the balance-only
        true-up: the current period's P&I is a property of the period
        structure, not of the anchor balance.
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
        common = dict(
            periods=periods,
            payments=[
                _confirmed(d, 15)
                for d in [date(2026, 2, 15)]
            ],
            payment_day=15,
            as_of=date(2026, 6, 1),
        )
        high = replay_schedule(
            anchor=BalanceAnchor(
                balance=Decimal("380000.00"), as_of_date=date(2026, 1, 1),
            ),
            **common,
        )
        low = replay_schedule(
            anchor=BalanceAnchor(
                balance=Decimal("375000.00"), as_of_date=date(2026, 1, 1),
            ),
            **common,
        )
        assert high.balance_as_of != low.balance_as_of
        assert high.current_period.period_pi == low.current_period.period_pi
        assert high.current_period.period_pi == Decimal("2600.00")

    def test_overpayment_capped_at_balance(self):
        """A scheduled payment larger than the balance closes the loan at zero.

        Fixed loan period_pi 1798.65, anchor 100.00:
          int = 100.00 * 0.005 = 0.50
          principal would be 1798.15 >= 100.00 -> capped to 100.00
          payment = 100.00 + 0.50 = 100.50 ; balance = 0.00
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("100.00"), as_of_date=date(2026, 1, 1),
            ),
            payments=[
                _confirmed(d, 15)
                for d in [date(2026, 2, 15)]
            ],
            payment_day=15,
            as_of=date(2026, 2, 28),
        )
        assert result.balance_as_of == Decimal("0.00")
        assert result.rows[0].principal == Decimal("100.00")
        assert result.rows[0].payment == Decimal("100.50")

    def test_no_payments_returns_anchor(self):
        """No confirmed payments -> balance is the anchor, projection next month."""
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 1, 1),
            ),
            payments=[],
            payment_day=15,
            as_of=date(2026, 1, 20),
        )
        assert result.rows == []
        assert result.balance_as_of == Decimal("300000.00")
        assert result.next_pay_date == date(2026, 2, 15)

    def test_payments_at_or_before_anchor_skipped(self):
        """Payments not strictly after the anchor are already reflected in it."""
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 1, 15),
            ),
            payments=[
                _confirmed(d, 15)
                for d in [date(2026, 1, 1), date(2026, 1, 15)]
            ],
            payment_day=15,
            as_of=date(2026, 2, 1),
        )
        assert result.rows == []
        assert result.balance_as_of == Decimal("300000.00")

    def test_due_date_after_anchor_replayed_though_period_start_before(self):
        """A payment due after the anchor replays even if its pay period began before it.

        The production bug (mortgage account 3): a balance true-up dated
        2026-05-22 lands one day after the biweekly pay period that begins
        2026-05-21 and carries the 2026-06-01 mortgage payment.  Keyed to
        the pay-period START (05-21), the payment is "before" the anchor
        and was wrongly stranded; keyed to its true monthly DUE date
        (06-01, payment_day=1) it is correctly after the anchor.

          due date  = first 1st on/after 05-21 = 2026-06-01 (> 05-22, <= 06-02)
          interest  = 300000 * 0.06/12 = 1500.00
          principal = 1798.65 - 1500.00 = 298.65
          balance   = 300000.00 - 298.65 = 299701.35

        The replayed row is dated by the true monthly due date (06-01),
        not the pay-period start (05-21): the rate/balance math is selected
        by the pay-period start (so the balance is unchanged) while the row
        carries the real statement date for the schedule display.
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 5, 22),
            ),
            payments=[
                _confirmed(d, 1)
                for d in [date(2026, 5, 21)]
            ],
            payment_day=1,
            as_of=date(2026, 6, 2),
        )
        assert len(result.rows) == 1
        assert result.balance_as_of == Decimal("299701.35")
        # The row is dated by the true monthly due date (06-01), not the
        # pay-period start (05-21) it was keyed to.
        assert result.rows[0].payment_date == date(2026, 6, 1)

    def test_prepaid_payment_replayed_when_its_cash_moved(self):
        """The as-of cap is the CASH day, not the due date (asymmetry).

        A payment whose cash moved 2026-05-21 (due 2026-06-01) is evaluated on
        2026-05-31 -- before its 06-01 due date but after the money left.  It is
        a real historical payment and replays: the as_of cap tests the settled
        day (05-21 <= 05-31), NOT the due date (which is still ahead).  The
        anchor boundary still uses the due date; only the as_of cap uses the
        cash day.

          interest  = 300000 * 0.06/12 = 1500.00
          principal = 1798.65 - 1500.00 = 298.65
          balance   = 300000.00 - 298.65 = 299701.35
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 5, 1),
            ),
            payments=[
                _confirmed(d, 1)
                for d in [date(2026, 5, 21)]
            ],
            payment_day=1,
            as_of=date(2026, 5, 31),
        )
        assert len(result.rows) == 1
        assert result.balance_as_of == Decimal("299701.35")

    def test_payment_whose_cash_has_not_moved_by_as_of_not_replayed(self):
        """A payment settled after as_of is excluded from the replay.

        Its cash moved 2026-06-15, after the 2026-06-02 evaluation date, so on
        that date it had not happened and is held for the forward projection --
        even though its due date (07-01) is well after the anchor.
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 5, 1),
            ),
            payments=[
                _confirmed(d, 1)
                for d in [date(2026, 6, 15)]
            ],
            payment_day=1,
            as_of=date(2026, 6, 2),
        )
        assert result.rows == []
        assert result.balance_as_of == Decimal("300000.00")


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


class TestReplayTakesTheWholeFeed:
    """An UNSETTLED payment in the feed is not replayed, and needs no filter.

    **The control for plan step balance:X-bl-2b's one behavioural claim.**  That
    step deleted the ``ConfirmedPayment`` type and the
    ``settled_on is not None`` comprehension its caller
    (``loan_resolver._periods._replay_from_anchor``) built it with, on the
    measurement that :func:`is_confirmed_payment_eligible` already excludes an
    unsettled payment: its ``has_settled_by`` term answers ``False`` for a
    missing day by that predicate's own documented contract.  So a caller now
    hands over its WHOLE feed, settled and projected alike.

    A claim that a filter was redundant is exactly the claim a green suite does
    not grade by itself -- the filtered and unfiltered feeds agree whether or
    not the reasoning holds.  So the second test below makes the dropped payment
    one that WOULD move the balance, by giving it a settle day and asserting the
    answer changes.  Without that arm this class would pass over a
    ``replay_schedule`` that silently ignored every payment.
    """

    _PERIODS_ANCHOR = BalanceAnchor(
        balance=Decimal("300000.00"), as_of_date=date(2026, 1, 1),
    )

    def _replay(self, payments, as_of=date(2026, 3, 31)):
        """Replay *payments* against the shared fixed-rate fixture."""
        return replay_schedule(
            periods=_fixed_loan_periods(),
            anchor=self._PERIODS_ANCHOR,
            payments=payments,
            payment_day=15,
            as_of=as_of,
        )

    def test_a_mixed_feed_answers_what_the_settled_half_alone_answers(self):
        """Settled + projected in one feed == the settled ones on their own.

        The identity the deleted filter used to guarantee, now a property of
        the eligibility predicate.
        """
        settled = _confirmed(date(2026, 2, 15), 15)
        projected = PaymentDates(
            period_start=date(2026, 3, 15),
            due_date=monthly_due_date(date(2026, 3, 15), 15),
            settled_on=None,
        )

        mixed = self._replay([settled, projected])
        settled_only = self._replay([settled])

        assert mixed.balance_as_of == settled_only.balance_as_of
        assert [row.payment_date for row in mixed.rows] == [
            row.payment_date for row in settled_only.rows
        ]

    def test_the_dropped_payment_is_one_that_would_otherwise_replay(self):
        """The non-vacuity arm: give the projected payment a day and it moves.

        Everything else about it -- its period, its installment, its position
        in the feed, the ``as_of`` -- is held constant, so the ONLY thing that
        decided whether it replayed is the settle day the predicate reads.
        """
        settled = _confirmed(date(2026, 2, 15), 15)
        installment = monthly_due_date(date(2026, 3, 15), 15)
        projected = PaymentDates(
            period_start=date(2026, 3, 15),
            due_date=installment,
            settled_on=None,
        )
        now_settled = PaymentDates(
            period_start=date(2026, 3, 15),
            due_date=installment,
            settled_on=date(2026, 3, 15),
        )

        without = self._replay([settled, projected])
        with_it = self._replay([settled, now_settled])

        assert len(without.rows) == 1
        assert len(with_it.rows) == 2
        assert with_it.balance_as_of < without.balance_as_of, (
            "the projected payment could never have moved the balance, so the "
            "test above proves nothing about it being dropped"
        )


class TestReplayScheduleLatePayment:
    """A payment settled LATE replays at the installment it actually paid.

    ``_confirmed`` (above) derives a due date from the pay-period start, which is
    correct only while the period still CONTAINS that due date.  A payment paid a
    few days late -- routine over a weekend or a holiday -- lands in the NEXT
    biweekly period, and that assumption breaks: deriving from the period start
    then returns the FOLLOWING month's installment.

    ``PaymentDates`` therefore carries all three, and the replay reads
    each for its own job: the DUE date for the anchor boundary and the row's
    date, the PERIOD START for the rate, and the SETTLED day for the ``as_of``
    cap (the day the fold counts the payment's principal from -- plan step
    X-an).
    """

    def test_row_is_dated_at_the_due_date_not_the_period_derivation(self):
        """A payment due 2026-05-15, paid late into the 2026-05-21 period.

        The pay period starting 2026-05-21 does NOT contain the 2026-05-15 due
        date.  Deriving from it (``monthly_due_date(2026-05-21, 15)``) yields
        2026-06-15 -- June's installment, a month late -- while the payment
        actually paid May's.  The replayed row must carry 2026-05-15.
        """
        periods = _fixed_loan_periods()
        anchor = BalanceAnchor(
            balance=Decimal("300000.00"), as_of_date=date(2026, 5, 1),
        )
        # The derivation the engine used to perform lands a month later.
        assert monthly_due_date(date(2026, 5, 21), 15) == date(2026, 6, 15)

        result = replay_schedule(
            periods=periods,
            anchor=anchor,
            payments=[
                PaymentDates(
                    period_start=date(2026, 5, 21),
                    due_date=date(2026, 5, 15),
                    settled_on=date(2026, 5, 21),
                ),
            ],
            payment_day=15,
            as_of=date(2026, 6, 1),
        )

        assert len(result.rows) == 1
        assert result.rows[0].payment_date == date(2026, 5, 15)
        # next_pay_date advances one month from the row it actually replayed.
        assert result.next_pay_date == date(2026, 6, 15)

    def test_as_of_cap_reads_the_settled_day_not_the_due_date(self):
        """The "has this happened?" cap is the day the CASH moved.

        A payment due 2026-05-15 whose money left on 2026-05-07: by an as_of of
        2026-05-08 it has happened and replays, even though its due date is
        still ahead.  This is the day the fold counts its principal from
        (``loan_ledger.payment_visible_on``), and the replay must match it or
        the two disagree on which payments have happened.
        """
        periods = _fixed_loan_periods()
        anchor = BalanceAnchor(
            balance=Decimal("300000.00"), as_of_date=date(2026, 5, 1),
        )
        result = replay_schedule(
            periods=periods,
            anchor=anchor,
            payments=[
                PaymentDates(
                    period_start=date(2026, 5, 7),
                    due_date=date(2026, 5, 15),
                    settled_on=date(2026, 5, 7),
                ),
            ],
            payment_day=15,
            as_of=date(2026, 5, 8),
        )

        assert len(result.rows) == 1
        assert result.rows[0].payment_date == date(2026, 5, 15)

    def test_as_of_cap_reads_the_settled_day_not_the_period_start(self):
        """N-187, the EARLY half: paid before the paycheck period funding it.

        The 2026-06-01 installment is funded by the pay period opening
        2026-05-31, and its cash left on 2026-05-28.  Read on 2026-05-28 the
        money HAS moved, so the payment is history -- which is what the fold
        says, because it counts the principal from that same day.  The
        pay-period start (05-31) is still ahead, and reading THAT was the defect:
        the ledger had already paid the installment down while the resolver was
        still planning it, so the forward projection paid it a second time.
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 5, 1),
            ),
            payments=[
                PaymentDates(
                    period_start=date(2026, 5, 31),
                    due_date=date(2026, 6, 1),
                    settled_on=date(2026, 5, 28),
                ),
            ],
            payment_day=1,
            as_of=date(2026, 5, 28),
        )

        assert len(result.rows) == 1
        assert result.rows[0].payment_date == date(2026, 6, 1)
        #   interest  = 300000 * 0.06/12 = 1500.00
        #   principal = 1798.65 - 1500.00 = 298.65
        assert result.balance_as_of == Decimal("299701.35")

    def test_a_period_that_has_begun_does_not_make_a_payment_historical(self):
        """N-187, the LATE half: the funding period opened, the cash had not.

        The mirror of the case above, and the one an ordinary read of a PAST
        date meets: the payment's pay period opened 2026-05-07 but its money did
        not leave until 2026-05-20.  Asked about 2026-05-08 the loan had not
        paid it, which is what the fold says; counting it there would have shown
        a balance the bank never reached, and excluded it from the forward plan
        at the same time.
        """
        periods = _fixed_loan_periods()
        result = replay_schedule(
            periods=periods,
            anchor=BalanceAnchor(
                balance=Decimal("300000.00"), as_of_date=date(2026, 5, 1),
            ),
            payments=[
                PaymentDates(
                    period_start=date(2026, 5, 7),
                    due_date=date(2026, 5, 15),
                    settled_on=date(2026, 5, 20),
                ),
            ],
            payment_day=15,
            as_of=date(2026, 5, 8),
        )

        assert result.rows == []
        assert result.balance_as_of == Decimal("300000.00")

    def test_anchor_boundary_reads_the_due_date_not_the_settled_day(self):
        """The anchor boundary stays on the DUE date.

        A true-up dated 2026-05-10 sits between the day the payment's cash moved
        (2026-05-07) and its due date (2026-05-15).  The payment came due AFTER
        the balance was verified, so it is NOT baked into the anchor and must
        still replay -- the mid-period-true-up rule.  Comparing the cash day
        against the anchor would strand it.
        """
        periods = _fixed_loan_periods()
        anchor = BalanceAnchor(
            balance=Decimal("300000.00"), as_of_date=date(2026, 5, 10),
        )
        result = replay_schedule(
            periods=periods,
            anchor=anchor,
            payments=[
                PaymentDates(
                    period_start=date(2026, 5, 7),
                    due_date=date(2026, 5, 15),
                    settled_on=date(2026, 5, 7),
                ),
            ],
            payment_day=15,
            as_of=date(2026, 6, 1),
        )

        assert len(result.rows) == 1
        assert result.rows[0].payment_date == date(2026, 5, 15)
