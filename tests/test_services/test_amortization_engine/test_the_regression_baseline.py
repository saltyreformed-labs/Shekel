"""The regression baseline: project_forward's figures on a 30-year mortgage, pinned."""

from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    PayoffRequest,
    ProjectionInputs,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    project_forward,
)

from ._helpers import _terms


# ── Section 5 Regression Baseline (re-purposed for project_forward) ──


class TestAmortizationEngineRegression:
    """Regression baseline pinning ``project_forward``'s behaviour on a
    representative 30-year mortgage scenario.

    Originally written against ``generate_schedule`` / ``calculate_summary``
    during the Section 5 cleanup sprint.  Re-purposed in Commit 9 of the
    amortization-engine split (deletion of the legacy entry points) to
    drive the same scenarios through ``project_forward``.  The lock
    values are unchanged because the new primitive computes the same
    schedule for the no-payments / no-override / no-rate-change inputs
    these tests exercise.

    Focus areas: payoff round-trip accuracy via the binary search in
    ``calculate_payoff_by_date``, known-value Decimal locks for the
    $250k / 6.5% / 360 scenario, schedule invariants
    (payment = principal + interest; balance monotonically decreasing;
    Decimal types).
    """

    # ── Standard test scenario ─────────────────────────────────────
    # $250,000 mortgage at 6.5% for 30 years, payment day 1,
    # origination 2024-01-01.  Values independently verified via the
    # standard amortization formula M = P * [r(1+r)^n] / [(1+r)^n - 1].
    # First payment date = origination + 1 month = 2024-02-01.

    PRINCIPAL = Decimal("250000.00")
    RATE = Decimal("0.065")
    MONTHS = 360
    ORIGINATION = date(2024, 1, 1)
    PAYMENT_DAY = 1
    STARTING_DATE = date(2024, 2, 1)
    CONTRACTUAL_PAYMENT = Decimal("1580.17")

    def _project(
        self,
        principal: Decimal | None = None,
        rate: Decimal | None = None,
        months: int | None = None,
        extra_monthly: Decimal = Decimal("0.00"),
    ):
        """Project forward at the standard contractual payment.

        Helper that captures the boilerplate: derive the contractual
        payment from the inputs (the value a fixed-rate loan keeps for
        its life) and run ``project_forward`` from the standard
        starting state.  Equivalent to the legacy
        ``generate_schedule(principal, rate, months, extra=..., ...)``
        call but expressed in primitive terms.
        """
        principal = self.PRINCIPAL if principal is None else principal
        rate = self.RATE if rate is None else rate
        months = self.MONTHS if months is None else months
        contractual = calculate_monthly_payment(principal, rate, months)
        return project_forward(
            ProjectionInputs(
                starting_balance=principal,
                starting_date=self.STARTING_DATE,
                remaining_months=months,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(self.STARTING_DATE, rate, contractual),
            ),
            extra_monthly=extra_monthly,
        )

    # ── Payoff round-trip ──────────────────────────────────────────

    def test_payoff_by_date_round_trip(self):
        """``calculate_payoff_by_date`` result fed back into
        ``project_forward`` must produce a payoff on or before the
        target date.  Catches rounding drift in the binary search.
        """
        target = date(2044, 1, 1)  # 20-year payoff target
        extra = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=self.PRINCIPAL,
                remaining_months=self.MONTHS,
                target_date=target,
                origination_date=self.ORIGINATION,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.ORIGINATION, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            )
        )
        assert extra is not None
        assert extra > Decimal("0")

        accel = self._project(extra_monthly=extra)
        assert accel[-1].payment_date <= target
        assert accel[-1].remaining_balance == Decimal("0.00")

    # ── Known-value regression locks ───────────────────────────────

    def test_standard_schedule_known_values(self):
        """Lock down exact values for the standard $250k / 6.5% / 360
        scenario via ``project_forward``.

        Hand arithmetic at $250,000 / 6.5% / 360, payment_day=1,
        starting 2024-02-01:
          monthly_payment = M(250000, 0.065, 360) = $1,580.17
          row 1: interest = 250000.00 * (0.065 / 12) = $1,354.17;
                 principal = 1580.17 - 1354.17 = $226.00;
                 balance = 250000.00 - 226.00 = $249,774.00.
          row 360: final balance == $0.00 by the ``is_final``
                   absorbing branch.
        """
        schedule = self._project()

        # First row: mostly interest, small principal.
        assert schedule[0].month == 1
        assert schedule[0].payment == self.CONTRACTUAL_PAYMENT
        assert schedule[0].interest == Decimal("1354.17")
        assert schedule[0].principal == Decimal("226.00")
        assert schedule[0].remaining_balance == Decimal("249774.00")

        # Last row: final balance is exactly zero.
        assert schedule[-1].remaining_balance == Decimal("0.00")
        assert len(schedule) == self.MONTHS

        # Totals.
        total_interest = sum(r.interest for r in schedule)
        total_principal = sum(r.principal + r.extra_payment for r in schedule)
        assert total_interest == Decimal("318861.58")
        assert total_principal == self.PRINCIPAL

    def test_contractual_payment_known_value(self):
        """``calculate_monthly_payment`` for the standard scenario is
        $1,580.17 (the contractual P&I the projection uses).
        """
        payment = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.MONTHS,
        )
        assert payment == self.CONTRACTUAL_PAYMENT

    # ── Decimal type enforcement ───────────────────────────────────

    def test_all_schedule_monetary_fields_are_decimal(self):
        """Every monetary field in every projected row must be Decimal.

        Float values in amortization output would be a critical
        precision bug in a financial application.
        """
        schedule = self._project()
        for row in schedule:
            assert isinstance(row.payment, Decimal), (
                f"Row {row.month}: payment is {type(row.payment).__name__}"
            )
            assert isinstance(row.principal, Decimal), (
                f"Row {row.month}: principal is "
                f"{type(row.principal).__name__}"
            )
            assert isinstance(row.interest, Decimal), (
                f"Row {row.month}: interest is "
                f"{type(row.interest).__name__}"
            )
            assert isinstance(row.extra_payment, Decimal), (
                f"Row {row.month}: extra_payment is "
                f"{type(row.extra_payment).__name__}"
            )
            assert isinstance(row.remaining_balance, Decimal), (
                f"Row {row.month}: remaining_balance is "
                f"{type(row.remaining_balance).__name__}"
            )

    # ── Edge cases ─────────────────────────────────────────────────

    def test_zero_extra_payment_equals_default_schedule(self):
        """Passing ``extra_monthly=0`` produces the same schedule as
        omitting the parameter (default ``Decimal("0.00")``).
        """
        standard = self._project()
        with_zero = self._project(extra_monthly=Decimal("0.00"))
        assert len(standard) == len(with_zero)
        for s_row, z_row in zip(standard, with_zero):
            assert s_row.payment == z_row.payment
            assert s_row.principal == z_row.principal
            assert s_row.interest == z_row.interest
            assert s_row.remaining_balance == z_row.remaining_balance

    def test_huge_extra_payment_one_month_payoff(self):
        """An ``extra_monthly`` vastly exceeding principal pays off in
        one month.  The projection caps extra at
        ``balance - principal_portion`` so the row absorbs the residue
        and balance closes at zero.
        """
        schedule = self._project(extra_monthly=Decimal("999999.00"))
        assert len(schedule) == 1
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_one_remaining_month_schedule(self):
        """Loan with 1 remaining month produces a single-row schedule
        whose payment covers principal plus one month of interest.

        Hand arithmetic at $5,000 / 6.5% / 1 month:
          monthly_rate = 0.065 / 12 = 0.00541667
          interest = 5000.00 * 0.00541667 = $27.08
          principal = 5000.00; balance = $0.00.
        """
        principal = Decimal("5000.00")
        schedule = self._project(principal=principal, months=1)
        assert len(schedule) == 1
        assert schedule[0].interest == Decimal("27.08")
        assert schedule[0].principal == principal
        assert schedule[0].remaining_balance == Decimal("0.00")

    def test_schedule_payment_equals_principal_plus_interest(self):
        """For every row, payment (base) equals principal + interest.

        The schedule separates the base payment from the
        ``extra_payment`` field; this invariant holds regardless of
        rounding adjustments.  A mismatch indicates broken arithmetic.
        """
        schedule = self._project(extra_monthly=Decimal("100.00"))
        for row in schedule:
            expected_base = row.principal + row.interest
            assert row.payment == expected_base, (
                f"Row {row.month}: payment={row.payment} != "
                f"principal({row.principal}) + interest({row.interest}) "
                f"= {expected_base}"
            )

    def test_balance_monotonically_decreasing(self):
        """Remaining balance never increases between consecutive rows.

        With a contractual payment plus extra and no negative
        amortization (override below interest), balance must decrease
        monotonically.
        """
        schedule = self._project(extra_monthly=Decimal("50.00"))
        for i in range(1, len(schedule)):
            assert (
                schedule[i].remaining_balance
                <= schedule[i - 1].remaining_balance
            ), (
                f"Balance increased from row {i} to row {i + 1}: "
                f"{schedule[i - 1].remaining_balance} -> "
                f"{schedule[i].remaining_balance}"
            )
