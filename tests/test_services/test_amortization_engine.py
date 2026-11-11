"""
Tests for the amortization engine service.
"""

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import (
    PaymentDates,
    PaymentRecord,
    PayoffRequest,
    PeriodTerms,
    ProjectionInputs,
    RateChangeRecord,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    calculate_remaining_months,
    project_forward,
    required_extra_for_projection,
)


def _terms(
    start_date: date, annual_rate: Decimal, monthly_pi: Decimal,
) -> list[PeriodTerms]:
    """Single fixed-rate terms feed for a projection.

    The SSOT analogue of the old ``(annual_rate, contractual_payment)``
    scalar pair: one :class:`PeriodTerms` entry governing every month.
    Tests that exercise a recast pass a multi-entry list instead.
    """
    return [PeriodTerms(
        start_date=start_date,
        annual_rate=annual_rate,
        monthly_pi=monthly_pi,
        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
    )]


class TestCalculateMonthlyPayment:
    """Tests for the monthly payment formula."""

    def test_basic_mortgage(self):
        """$200,000 at 6.5%, 30 years → ~$1,264.14."""
        payment = calculate_monthly_payment(
            Decimal("200000"), Decimal("0.065"), 360,
        )
        assert payment == Decimal("1264.14")

    def test_zero_rate(self):
        """0% rate → principal / months."""
        payment = calculate_monthly_payment(
            Decimal("12000"), Decimal("0"), 12,
        )
        assert payment == Decimal("1000.00")

    def test_zero_principal(self):
        """$0 principal → $0 payment."""
        payment = calculate_monthly_payment(
            Decimal("0"), Decimal("0.05"), 360,
        )
        assert payment == Decimal("0.00")

    def test_negative_principal(self):
        """Negative principal → $0 payment."""
        payment = calculate_monthly_payment(
            Decimal("-1000"), Decimal("0.05"), 360,
        )
        assert payment == Decimal("0.00")

    def test_zero_remaining_months(self):
        """0 months → $0 payment."""
        payment = calculate_monthly_payment(
            Decimal("100000"), Decimal("0.05"), 0,
        )
        assert payment == Decimal("0.00")

    def test_short_term_auto_loan(self):
        """5-year auto loan at 5%."""
        payment = calculate_monthly_payment(
            Decimal("25000"), Decimal("0.05"), 60,
        )
        assert payment == Decimal("471.78")

    def test_one_month(self):
        """1 month → principal + one month interest."""
        payment = calculate_monthly_payment(
            Decimal("10000"), Decimal("0.06"), 1,
        )
        # Should be ~$10,050 (principal + 0.5% interest)
        assert payment == Decimal("10050.00")


class TestPayoffByDate:
    """Tests for payoff-by-date calculations."""

    def test_achievable_target(self):
        """Target 15 years on $200k at 6.5% → $478.08 extra/mo.

        REGRESSION LOCK: Value determined by running the
        deterministic binary search to convergence
        (hi - lo <= 0.01). Verified below by proving $478.08
        achieves the target and $478.07 does not.
        """
        contractual = calculate_monthly_payment(
            Decimal("200000"), Decimal("0.065"), 360,
        )
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2041, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"), contractual,
                ),
            )
        )
        assert result == Decimal("478.08"), (
            f"Expected extra payment 478.08, got {result}"
        )

        # Project forward at the converged extra and confirm payoff lands
        # at or before the target.  The same engine primitive
        # ``calculate_payoff_by_date`` uses internally, so the cross-
        # check stays consistent with the binary search.
        starting_date = date(2026, 2, 1)  # origination + 1 month
        schedule_at = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("200000"),
                starting_date=starting_date,
                remaining_months=360,
                payment_day=1,
                terms_schedule=_terms(
                    starting_date, Decimal("0.065"), contractual,
                ),
            ),
            extra_monthly=Decimal("478.08"),
        )
        assert schedule_at[-1].payment_date <= date(2041, 1, 1), (
            f"$478.08 extra should pay off by 2041-01-01, "
            f"but last payment is {schedule_at[-1].payment_date}"
        )

        # And $478.07 (one penny less) must NOT achieve it -- pins the
        # binary search's penny-level discrimination.
        schedule_under = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("200000"),
                starting_date=starting_date,
                remaining_months=360,
                payment_day=1,
                terms_schedule=_terms(
                    starting_date, Decimal("0.065"), contractual,
                ),
            ),
            extra_monthly=Decimal("478.07"),
        )
        assert schedule_under[-1].payment_date > date(2041, 1, 1), (
            f"$478.07 extra should NOT pay off by 2041-01-01, "
            f"but last payment is {schedule_under[-1].payment_date}"
        )

    def test_target_too_soon(self):
        """Target in past → returns None."""
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2025, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            )
        )
        assert result is None

    def test_target_after_standard_payoff(self):
        """Target after standard payoff → returns 0."""
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2060, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            )
        )
        assert result == Decimal("0.00")

    def test_zero_principal(self):
        """Zero principal → returns $0."""
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("0"),
                remaining_months=360,
                target_date=date(2040, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            )
        )
        assert result == Decimal("0.00")


class TestRequiredExtraForProjection:
    """The contract-only payoff search (F-27's raw answer).

    ``required_extra_for_projection`` is the factored-out core of
    ``calculate_payoff_by_date``: one starting state drives the baseline
    run and every binary-search iteration.  It took an optional
    planned-outlay ``monthly_override`` until plan step R7d-g-3 (ruling
    **R-R88**); the plan-aware answer is the balance seam's
    ``loan_required_extra``, graded in ``test_loan_resolver`` and the seam's
    own tests, and nothing passed an override here any more.
    """

    TARGET = date(2041, 1, 1)
    CONTRACTUAL = calculate_monthly_payment(
        Decimal("200000"), Decimal("0.065"), 360,
    )

    def _inputs(self):
        """The $200k / 6.5% / 360-month state the C7-4 locks also use."""
        return ProjectionInputs(
            starting_balance=Decimal("200000"),
            starting_date=date(2026, 2, 1),
            remaining_months=360,
            payment_day=1,
            terms_schedule=_terms(
                date(2026, 2, 1), Decimal("0.065"), self.CONTRACTUAL,
            ),
        )

    def test_matches_calculate_payoff_by_date(self):
        """Refactor lock: the core reproduces the wrapped function.

        Same loan facts as ``test_achievable_target``'s regression lock
        ($478.08), proving the extraction is behavior-preserving for
        the raw (no-plan) path.
        """
        result = required_extra_for_projection(self._inputs(), self.TARGET)
        assert result == Decimal("478.08")

class TestPayoffByDateProjectForward:
    """C7-1..C7-4, C7-6: lock ``calculate_payoff_by_date`` behaviour
    after the migration onto :func:`project_forward`.

    Commit 7 of the amortization-engine split rewrites the function
    so both the standard schedule and the binary-search inner loop
    call ``project_forward`` instead of ``generate_schedule``.  These
    tests pin the externally observable behaviour: API contracts for
    the three early-return cases (C7-1 / C7-2 / C7-3), byte-identical
    output across five representative inputs (C7-4), and the
    structural guarantee that no ``generate_schedule`` reference
    remains inside the function body (C7-6).
    """

    def test_calculate_payoff_by_date_target_in_past(self):
        """C7-1: target_date before the projection start returns None.

        ``starting_date = origination_date + 1 month``; a target_date
        before ``starting_date`` yields ``target_months <= 0`` which
        is the legacy ``None`` short-circuit (no extra payment can
        change history).
        """
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2025, 12, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            )
        )
        assert result is None

    def test_calculate_payoff_by_date_target_already_achieved(self):
        """C7-2: target after the standard payoff returns Decimal('0.00').

        Standard schedule for $200k / 6.5% / 360 months pays off on
        2056-01-01; target 2060-01-01 is later, so no extra is
        required and the function short-circuits before binary search.
        """
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2060, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            )
        )
        assert result == Decimal("0.00")

    def test_calculate_payoff_by_date_converges(self):
        """C7-3: binary search converges to a Decimal within $0.01.

        $200k / 6.5% / 360 months, target 2041-01-01 (~15-year payoff)
        converges deterministically to ``Decimal('478.08')`` -- the
        same value the legacy implementation produced.  Hand-derived
        via the bisection's convergence criterion (hi - lo <= 0.01).
        """
        result = calculate_payoff_by_date(
            PayoffRequest(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2041, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            )
        )
        assert result == Decimal("478.08"), (
            f"Expected $478.08 required extra, got {result}"
        )

    @pytest.mark.parametrize("label,kwargs,expected", [
        # Pre-commit values captured from the legacy generate_schedule
        # implementation on 2026-05-22 before the project_forward
        # migration.  Any drift here is a real regression.  Each terms
        # feed carries the contractual P&I the legacy implementation
        # derived internally (amortizing the projection state -- or the
        # original terms for the partial-paid case), so the pinned
        # dollars are unchanged across the DH-#1 terms-schedule reshape.
        (
            "basic_30yr",
            dict(
                current_principal=Decimal("200000"),
                remaining_months=360,
                target_date=date(2041, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            ),
            Decimal("478.08"),
        ),
        (
            "partial_paid_with_original_terms",
            dict(
                current_principal=Decimal("150000"),
                remaining_months=240,
                target_date=date(2038, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                # The contractual P&I of the ORIGINAL terms -- the
                # payment a partially-paid loan keeps for its life.
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.065"),
                    calculate_monthly_payment(
                        Decimal("200000"), Decimal("0.065"), 360,
                    ),
                ),
            ),
            Decimal("238.75"),
        ),
        (
            "15yr_target_5_percent",
            dict(
                current_principal=Decimal("250000"),
                remaining_months=360,
                target_date=date(2035, 6, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.05"),
                    calculate_monthly_payment(
                        Decimal("250000"), Decimal("0.05"), 360,
                    ),
                ),
            ),
            Decimal("1436.42"),
        ),
        (
            "short_horizon_payment_day_15",
            dict(
                current_principal=Decimal("50000"),
                remaining_months=120,
                target_date=date(2030, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=15,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.04"),
                    calculate_monthly_payment(
                        Decimal("50000"), Decimal("0.04"), 120,
                    ),
                ),
            ),
            Decimal("644.88"),
        ),
        (
            "large_loan_7_percent",
            dict(
                current_principal=Decimal("500000"),
                remaining_months=360,
                target_date=date(2045, 1, 1),
                origination_date=date(2026, 1, 1),
                payment_day=1,
                terms_schedule=_terms(
                    date(2026, 1, 1), Decimal("0.07"),
                    calculate_monthly_payment(
                        Decimal("500000"), Decimal("0.07"), 360,
                    ),
                ),
            ),
            Decimal("644.46"),
        ),
    ])
    def test_calculate_payoff_by_date_unchanged_vs_pre_commit(
        self, label, kwargs, expected,
    ):
        """C7-4: byte-identical Decimal output across 5 representative inputs.

        The migration from ``generate_schedule`` to ``project_forward``
        is behavior-preserving (per D-F of the implementation plan).
        These five cases are the assert-unchanged lock that proves it:
        if any value drifts even by one cent, that is a real
        regression caught here.
        """
        result = calculate_payoff_by_date(PayoffRequest(**kwargs))
        assert result == expected, (
            f"{label}: expected {expected}, got {result}"
        )

    def test_no_generate_schedule_in_calculate_payoff_by_date(self):
        """C7-6: ``calculate_payoff_by_date`` no longer calls generate_schedule.

        Structural guarantee that the migration is complete -- the
        function body slice between its ``def`` and the next top-level
        ``def`` must contain zero CALLS to ``generate_schedule`` (i.e.,
        the ``generate_schedule(`` syntactic form).  Docstring / comment
        mentions are exempt because they retain useful historical
        context after the rewrite.  Catches future merges that
        accidentally reintroduce the old engine call.
        """
        engine_path = (
            Path(__file__).resolve().parent.parent.parent
            / "app" / "services" / "amortization_engine" / "_payoff.py"
        )
        source = engine_path.read_text(encoding="utf-8")
        marker = "def calculate_payoff_by_date("
        start = source.index(marker)
        # The function body ends at the next top-level ``def`` /
        # class definition.  Slice from ``start`` to whichever comes
        # first; if neither, slice to end-of-file.
        next_def = source.find("\ndef ", start + len(marker))
        next_class = source.find("\nclass ", start + len(marker))
        candidates = [c for c in (next_def, next_class) if c != -1]
        end = min(candidates) if candidates else len(source)
        body = source[start:end]
        assert "generate_schedule(" not in body, (
            "calculate_payoff_by_date must not call "
            "generate_schedule() after the Commit 7 migration; "
            "found a call site in the function body."
        )


class TestCalculateRemainingMonths:
    """Tests for calculate_remaining_months date arithmetic."""

    def test_remaining_months_basic(self):
        """360-month loan, 60 months elapsed = 300 remaining.

        months_elapsed = (2025-2020)*12 + (1-1) = 60
        remaining = max(0, 360-60) = 300
        """
        result = calculate_remaining_months(
            date(2020, 1, 1), 360, as_of=date(2025, 1, 1),
        )
        assert result == 300, (
            f"Expected 300 remaining, got {result}"
        )

    def test_remaining_months_past_term(self):
        """12-month loan, 60 months elapsed = 0 remaining.

        months_elapsed = (2025-2020)*12 + (1-1) = 60
        remaining = max(0, 12-60) = 0
        """
        result = calculate_remaining_months(
            date(2020, 1, 1), 12, as_of=date(2025, 1, 1),
        )
        assert result == 0, (
            f"Expected 0 remaining, got {result}"
        )

    def test_remaining_months_same_month(self):
        """as_of same month as origination = full term remaining.

        months_elapsed = (2025-2025)*12 + (1-1) = 0
        remaining = max(0, 360-0) = 360
        """
        result = calculate_remaining_months(
            date(2025, 1, 1), 360, as_of=date(2025, 1, 15),
        )
        assert result == 360, (
            f"Expected 360 remaining, got {result}"
        )

    def test_remaining_months_none_as_of(self):
        """Default as_of=today returns a non-negative int."""
        result = calculate_remaining_months(
            date(2020, 1, 1), 360,
        )
        assert isinstance(result, int), (
            f"Expected int, got {type(result)}"
        )
        assert result >= 0, (
            f"Expected >= 0, got {result}"
        )


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


class TestPaymentDatesValidation:
    """Tests for PaymentDates dataclass validation.

    Validates that invalid inputs are caught at construction time
    with clear error messages rather than producing silent wrong
    results in the schedule loop.

    **These guards were on ``PaymentRecord`` until plan step balance:X-bl-2b**,
    which made a payment's three dates ONE value that both the priced record
    and the loader's installment compose.  The tests moved with the guards:
    grading the dates through a ``PaymentRecord`` wrapper would be grading the
    wrapper.
    """

    def test_string_period_start_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="period_start must be a date"):
            PaymentDates(
                period_start="2026-02-15",
                due_date=date(2026, 2, 15),
                settled_on=date(2026, 2, 15),
            )

    def test_string_due_date_raises_type_error(self):
        """String due_date must be rejected -- only date instances accepted.

        ``due_date`` is the installment the payment satisfies, and it is a
        SEPARATE fact from ``period_start`` (the pay period funding it) and from
        ``settled_on`` (the day its cash moved), so it carries the same
        construction-time type guard: a value that conflated any two of them,
        or carried a stringly-typed date, would mis-key every downstream
        due-month lookup.
        """
        with pytest.raises(TypeError, match="due_date must be a date"):
            PaymentDates(
                period_start=date(2026, 2, 15),
                due_date="2026-03-01",
                settled_on=date(2026, 2, 15),
            )

    def test_string_settled_on_raises_type_error(self):
        """String settled_on must be rejected -- only a date or None accepted.

        ``settled_on`` is the replay-vs-projection cut (plan step X-an), so a
        stringly-typed one would compare against ``as_of`` by lexicographic
        accident rather than by chronology.  ``None`` is the one non-date value
        it accepts, and it means the payment has not settled.
        """
        with pytest.raises(
            TypeError, match="settled_on must be a date or None",
        ):
            PaymentDates(
                period_start=date(2026, 2, 15),
                due_date=date(2026, 2, 15),
                settled_on="2026-02-15",
            )

    def test_bool_settled_on_raises_type_error(self):
        """A bool settled_on must be rejected -- ``True`` is not a day.

        ``settled_on is not None`` is what ``is_confirmed`` reads, so a ``bool``
        reaching this field would report the value as confirmed and then fail
        only deep inside the replay, where ``True <= as_of`` raises with no
        mention of the value that produced it.  The guard earns its place on
        the value, not on a migration path -- see
        :meth:`TestPaymentRecordValidation.test_a_bare_date_as_dates_raises_type_error`
        for why no stale-caller shape reaches a guard like this one.
        """
        with pytest.raises(
            TypeError, match="settled_on must be a date or None",
        ):
            PaymentDates(
                period_start=date(2026, 2, 15),
                due_date=date(2026, 2, 15),
                settled_on=True,
            )

    def test_is_confirmed_is_derived_from_the_settle_day(self):
        """``is_confirmed`` IS ``settled_on is not None`` -- never stored (X-an).

        The two facts cannot disagree because there is only one of them: a
        value carrying a day is confirmed, one carrying none is a plan, and
        there is no third state to validate against.  Pinned so a future edit
        that re-introduces the boolean has to delete this test to do it.
        """
        settled = PaymentDates(
            period_start=date(2026, 2, 15),
            due_date=date(2026, 3, 1),
            settled_on=date(2026, 2, 28),
        )
        projected = PaymentDates(
            period_start=date(2026, 3, 15),
            due_date=date(2026, 4, 1),
            settled_on=None,
        )
        assert settled.is_confirmed is True
        assert projected.is_confirmed is False
        assert "is_confirmed" not in {
            f.name for f in dataclasses.fields(PaymentDates)
        }


class TestPaymentRecordValidation:
    """Tests for PaymentRecord dataclass validation.

    What is left on the record itself since plan step balance:X-bl-2b: the
    AMOUNT, which is the only part of a payment this type adds, and the guard
    that it was handed a real :class:`PaymentDates` rather than something
    shaped like one.  The three date guards are
    :class:`TestPaymentDatesValidation` above.
    """

    def test_negative_amount_raises_value_error(self):
        """Negative payment amount is nonsensical and must be rejected."""
        with pytest.raises(ValueError, match="amount must be >= 0"):
            PaymentRecord(
                PaymentDates(
                    period_start=date(2026, 2, 15),
                    due_date=date(2026, 2, 15),
                    settled_on=date(2026, 2, 15),
                ),
                amount=Decimal("-100.00"),
            )

    def test_float_amount_raises_type_error(self):
        """Float amount is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="amount must be a Decimal"):
            PaymentRecord(
                PaymentDates(
                    period_start=date(2026, 2, 15),
                    due_date=date(2026, 2, 15),
                    settled_on=date(2026, 2, 15),
                ),
                amount=100.00,
            )

    def test_a_bare_date_as_dates_raises_type_error(self):
        """A bare ``date`` must be rejected -- only a PaymentDates is accepted.

        The composition's own guard: without it the record constructs and the
        failure surfaces deep in the replay as a missing ``period_start``
        attribute, naming neither the record nor the caller that built it.

        **It is NOT the stale-caller case, and a first draft of this docstring
        said it was.**  The record's fields were ``(payment_date, due_date,
        settled_on, amount)`` before plan step balance:X-bl-2b and are
        ``(dates, amount)`` after, so a stale POSITIONAL call raises on ARITY
        and a stale KEYWORD call raises ``unexpected keyword argument`` -- and
        neither reaches this guard.  That correction is the same one
        ``test_bool_settled_on_raises_type_error`` already carried and this
        step briefly deleted: the guard earns its place on the value, not on a
        migration path.
        """
        with pytest.raises(TypeError, match="dates must be a PaymentDates"):
            PaymentRecord(
                date(2026, 2, 15),
                amount=Decimal("100.00"),
            )

    def test_zero_amount_valid(self):
        """Zero amount is valid -- represents a missed payment."""
        record = PaymentRecord(
            PaymentDates(
                period_start=date(2026, 2, 15),
                due_date=date(2026, 2, 15),
                settled_on=None,
            ),
            amount=Decimal("0.00"),
        )
        assert record.amount == Decimal("0.00")

    def test_valid_construction(self):
        """Valid PaymentRecord construction succeeds."""
        record = PaymentRecord(
            PaymentDates(
                period_start=date(2026, 2, 15),
                due_date=date(2026, 2, 15),
                settled_on=date(2026, 2, 15),
            ),
            amount=Decimal("1500.00"),
        )
        assert record.dates.period_start == date(2026, 2, 15)
        assert record.amount == Decimal("1500.00")
        assert record.dates.is_confirmed is True
        assert "payment_date" not in {
            f.name for f in dataclasses.fields(PaymentRecord)
        }


class TestRateChangeRecordValidation:
    """Tests for RateChangeRecord dataclass validation.

    Mirrors TestPaymentRecordValidation: validates that invalid inputs
    are caught at construction time with clear error messages.
    """

    def test_negative_rate_raises_value_error(self):
        """Negative interest rate must be rejected."""
        with pytest.raises(ValueError, match="interest_rate must be >= 0"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=Decimal("-0.01"),
            )

    def test_float_rate_raises_type_error(self):
        """Float rate is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="interest_rate must be a Decimal"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=0.07,
            )

    def test_string_date_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="effective_date must be a date"):
            RateChangeRecord(
                effective_date="2025-06-01",
                interest_rate=Decimal("0.07"),
            )

    def test_zero_rate_valid(self):
        """Zero rate is valid -- represents an interest-free period."""
        record = RateChangeRecord(
            effective_date=date(2025, 6, 1),
            interest_rate=Decimal("0.00"),
        )
        assert record.interest_rate == Decimal("0.00")

    def test_valid_construction(self):
        """Valid RateChangeRecord construction succeeds."""
        record = RateChangeRecord(
            effective_date=date(2025, 6, 1),
            interest_rate=Decimal("0.07"),
        )
        assert record.effective_date == date(2025, 6, 1)
        assert record.interest_rate == Decimal("0.07")


class TestPeriodTermsValidation:
    """Construction-time validation for PeriodTerms and the
    ProjectionInputs empty-terms guard.

    Mirrors TestPaymentRecordValidation: invalid data must fail loudly
    at construction, not produce a silently wrong schedule.
    """

    def test_negative_rate_raises_value_error(self):
        """Negative annual rate must be rejected."""
        with pytest.raises(ValueError, match="annual_rate must be >= 0"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("-0.01"),
                monthly_pi=Decimal("1500.00"),
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_negative_pi_raises_value_error(self):
        """Negative level payment must be rejected."""
        with pytest.raises(ValueError, match="monthly_pi must be >= 0"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("0.06"),
                monthly_pi=Decimal("-1.00"),
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_float_pi_raises_type_error(self):
        """Float P&I is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="monthly_pi must be a Decimal"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("0.06"),
                monthly_pi=1500.00,
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_string_date_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="start_date must be a date"):
            PeriodTerms(
                start_date="2026-01-01",
                annual_rate=Decimal("0.06"),
                monthly_pi=Decimal("1500.00"),
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_empty_terms_schedule_rejected(self):
        """ProjectionInputs refuses an empty terms feed at construction.

        A projection with no terms has no payment or rate to apply;
        the constructor fails loudly instead of producing an empty or
        wrong schedule downstream.
        """
        with pytest.raises(ValueError, match="terms_schedule"):
            ProjectionInputs(
                starting_balance=Decimal("1000.00"),
                starting_date=date(2026, 2, 1),
                remaining_months=12,
                payment_day=1,
                terms_schedule=[],
            )


class TestProjectForward:
    """Tests for ``project_forward``.

    The second half of the amortization-engine split (Commit 2 of
    ``docs/plans/2026-05-21-amortization-engine-split-implementation.md``;
    architectural plan
    ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``).
    Verifies that projection is a pure forward-only function of a
    known starting state:

      - ``extra_monthly`` lives only on this surface, and applies to
        EVERY forward month (C2-4 -- the target-date search's contract-only
        what-if);
      - a ``monthly_override`` channel routed the user's planned payments
        through this projection until plan step R7d-g-3 (ruling **R-R88**):
        the balance seam's plan fold is the one walk of what a loan is
        projected to pay, and the channel went with its last caller;
      - negative amortization, overpayment cap, and ARM rate-change
        re-amortization all mirror ``generate_schedule``'s existing
        behavior on the projection side;
      - every projected row carries ``is_confirmed=False``.

    Standard scenario: $300,000 / 6% / 360 months / payment_day=1
    with a known starting date of 2026-02-01.  Monthly P&I:
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        r = 0.06/12 = 0.005, n = 360
        (1.005)^360 ~ 6.022575
        M = 300000 * 0.005 * 6.022575 / 5.022575 = $1,798.65
    """

    PRINCIPAL = Decimal("300000.00")
    RATE = Decimal("0.06")
    TERM_MONTHS = 360
    PAYMENT_DAY = 1
    CONTRACTUAL_PAYMENT = Decimal("1798.65")
    STARTING_DATE = date(2026, 2, 1)

    # ── C2-1: no override, no extra -> contractual schedule ───────

    def test_no_override_no_extra_is_contractual(self):
        """C2-1: with no override and no extra, ``project_forward``
        produces the standard contractual schedule over
        ``remaining_months``.

        Hand arithmetic at $300,000 / 6% / 360, payment_day=1, first
        payment 2026-02-01:
          row 1: interest = 300000.00 * 0.005 = $1,500.00;
                 principal = 1798.65 - 1500.00 = $298.65;
                 balance = $299,701.35
          row 360: schedule terminates with balance == $0.00 by the
                   ``is_final`` absorbing branch (final row rolls up
                   any sub-penny residue).
        ``extra_payment`` is $0.00 on every row.
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert len(rows) == self.TERM_MONTHS
        # Row 1.
        assert rows[0].payment_date == date(2026, 2, 1)
        assert rows[0].interest == Decimal("1500.00")
        assert rows[0].principal == Decimal("298.65")
        assert rows[0].extra_payment == Decimal("0.00")
        assert rows[0].remaining_balance == Decimal("299701.35")
        assert rows[0].is_confirmed is False
        # Every row uses the contractual payment (sans final-row
        # absorption rounding), no extra.
        for row in rows[:-1]:
            assert row.payment == self.CONTRACTUAL_PAYMENT
            assert row.extra_payment == Decimal("0.00")
            assert row.is_confirmed is False
        # Final row absorbs the residue and closes the balance at 0.
        assert rows[-1].payment_date == date(2056, 1, 1)
        assert rows[-1].remaining_balance == Decimal("0.00")
        assert rows[-1].extra_payment == Decimal("0.00")

    # ── C2-3: extra_monthly only, no override ─────────────────────

    def test_extra_monthly_only_no_override(self):
        """C2-3: ``extra_monthly`` applies to every non-final month;
        the schedule shortens to ~17.7 years and the final row
        absorbs the residue with ``extra_payment == 0``.

        Hand arithmetic at row 1:
          interest = 300000.00 * 0.005 = $1,500.00
          principal = 1798.65 - 1500.00 = $298.65
          extra = $500.00 (uncapped: 500.00 < 299701.35)
          balance = 300000.00 - 298.65 - 500.00 = $299,201.35
        At $300,000 / 6% with $500 extra, the loan amortizes in 212
        rows; the final row's ``is_final`` branch absorbs the balance
        and reports ``extra_payment == $0.00``.
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
            extra_monthly=Decimal("500.00"),
        )
        # Row 1 hand-computed values.
        assert rows[0].payment == self.CONTRACTUAL_PAYMENT
        assert rows[0].extra_payment == Decimal("500.00")
        assert rows[0].principal == Decimal("298.65")
        assert rows[0].interest == Decimal("1500.00")
        assert rows[0].remaining_balance == Decimal("299201.35")
        # Schedule shortens substantially (under the unaccelerated
        # 360 months).
        assert len(rows) == 212
        # Final row absorbs residue.
        assert rows[-1].remaining_balance == Decimal("0.00")
        # Every non-final row has the configured extra applied.
        for row in rows[:-1]:
            assert row.extra_payment == Decimal("500.00")

    # ── C2-4: override + extra -- the standing extra rides override months ──

    # ── C2-5: override below interest -> negative amortization ────

    # ── C2-6: ARM rate change during projection ───────────────────

    def test_arm_rate_change_during_projection(self):
        """C2-6: a second ``terms_schedule`` entry recasts the rate AND
        payment from its ``start_date`` -- to the entry's SSOT values.

        Retargeted for the DH-#1 terms-schedule reshape (developer
        ratified "full SSOT", 2026-06-11): the engine no longer
        re-amortizes from its own balance at a rate boundary; it pays
        the governing entry's ``monthly_pi`` verbatim (the rate-period
        engine's derived-or-recorded figure, the same one the loan
        card shows).

        Setup: 30 yr / $300,000 / starts at 6%; recast at 2027-02-01
        to 7.5% with the schedule-derived level payment.  Hand
        arithmetic at month 13 (Feb 2027):
          balance entering Feb 2027 (post-Jan 2027) = $296,316.00
          new monthly_rate = 0.075 / 12 = 0.00625
          interest = 296316.00 * 0.00625 = 1851.97500 -> $1,851.98
          level payment M(296316.00, 0.075, 348) = $2,091.16
        (Identical to the pre-reshape re-amortized value here, because
        a no-override/no-extra projection's balance at the boundary IS
        the scheduled balance -- the reshape changes behavior only when
        a what-if balance path diverges from the schedule.)
        """
        contractual = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.TERM_MONTHS,
        )
        recast_pi = calculate_monthly_payment(
            Decimal("296316.00"), Decimal("0.075"), 348,
        )
        # Hand-anchored: the derived recast level payment.
        assert recast_pi == Decimal("2091.16")
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=[
                    PeriodTerms(
                        start_date=self.STARTING_DATE,
                        annual_rate=self.RATE,
                        monthly_pi=contractual,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                    PeriodTerms(
                        start_date=date(2027, 2, 1),
                        annual_rate=Decimal("0.075"),
                        monthly_pi=recast_pi,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                ],
            ),
        )
        # Row 12 (month 13, Feb 2027) is the first row at the new
        # rate.  Pre-change row 11 (Jan 2027, month 12) keeps the
        # base 6% rate.
        jan_2027 = rows[11]
        feb_2027 = rows[12]
        assert jan_2027.payment_date == date(2027, 1, 1)
        assert jan_2027.interest_rate == Decimal("0.06")
        assert jan_2027.payment == self.CONTRACTUAL_PAYMENT
        assert feb_2027.payment_date == date(2027, 2, 1)
        assert feb_2027.interest_rate == Decimal("0.075")
        # interest at the new rate: 296316.00 * 0.075/12.
        assert feb_2027.interest == Decimal("1851.98")
        # The payment IS the second entry's SSOT level payment.
        assert feb_2027.payment == recast_pi

    def test_recorded_recast_pi_paid_verbatim(self):
        """DH-#1 engine pin: a recorded recast P&I is paid VERBATIM.

        The user's lender stated the post-recast payment on the
        adjustment notice ($2,500.00, recorded as
        ``RateHistory.monthly_pi`` and carried into the terms entry).
        Every post-recast row must pay exactly that figure -- the loan
        card shows it, so a schedule that re-derives a different
        payment contradicts the card (the original DH-#1 divergence:
        the pre-reshape engine re-amortized $2,582.13 here).
        Revert-proof: restoring balance-reactive re-amortization fails
        the equality.
        """
        contractual = calculate_monthly_payment(
            self.PRINCIPAL, self.RATE, self.TERM_MONTHS,
        )
        recorded = Decimal("2500.00")
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=[
                    PeriodTerms(
                        start_date=self.STARTING_DATE,
                        annual_rate=self.RATE,
                        monthly_pi=contractual,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                    PeriodTerms(
                        start_date=date(2027, 2, 1),
                        annual_rate=Decimal("0.075"),
                        monthly_pi=recorded,
                        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
                    ),
                ],
            ),
        )
        post_recast = [
            r for r in rows if r.payment_date >= date(2027, 2, 1)
        ]
        assert post_recast, "projection must reach the recast"
        # Every post-recast row except the residue-absorbing final one
        # pays the recorded figure exactly.
        for row in post_recast[:-1]:
            assert row.payment == recorded, (
                f"{row.payment_date}: paid {row.payment}, recorded "
                f"recast is {recorded}"
            )
            assert row.interest_rate == Decimal("0.075")

    # ── C2-7: overpayment cap on the final row ────────────────────

    def test_overpayment_cap_final_row(self):
        """C2-7: when extra (or an override) would drive the balance
        below zero, the final row absorbs the remaining balance
        exactly, ``extra_payment`` is capped, and the closing balance
        is $0.00.

        Hand arithmetic at $1,000 balance, 6%, contractual $1,798.65,
        extra $500 over a 360-month projection:
          interest = 1000.00 * 0.005 = $5.00
          principal (contractual - interest) = 1798.65 - 5.00 = $1,793.65
          ``is_final`` triggers because principal >= balance:
            principal := 1000.00
            actual_payment = 1000.00 + 5.00 = $1,005.00
            extra := $0.00 (final-row absorption clears extra)
            balance := $0.00
        The schedule terminates in a single row.
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("1000.00"),
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
            extra_monthly=Decimal("500.00"),
        )
        assert len(rows) == 1
        final = rows[0]
        assert final.payment == Decimal("1005.00")
        assert final.principal == Decimal("1000.00")
        assert final.interest == Decimal("5.00")
        assert final.extra_payment == Decimal("0.00")
        assert final.remaining_balance == Decimal("0.00")

    # ── C2-8: zero starting balance returns empty ─────────────────

    def test_zero_starting_balance_returns_empty(self):
        """C2-8: ``starting_balance == 0`` returns an empty list
        (the loan is already paid off; nothing to project).
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("0.00"),
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []
        # Negative balance is treated the same way (defensive).
        rows = project_forward(
            ProjectionInputs(
                starting_balance=Decimal("-10.00"),
                starting_date=self.STARTING_DATE,
                remaining_months=self.TERM_MONTHS,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []

    # ── C2-9: zero remaining_months returns empty ─────────────────

    def test_zero_remaining_months_returns_empty(self):
        """C2-9: ``remaining_months == 0`` returns an empty list
        (no time horizon for projection).
        """
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=0,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []
        # Negative remaining_months is treated the same way.
        rows = project_forward(
            ProjectionInputs(
                starting_balance=self.PRINCIPAL,
                starting_date=self.STARTING_DATE,
                remaining_months=-3,
                payment_day=self.PAYMENT_DAY,
                terms_schedule=_terms(
                    self.STARTING_DATE, self.RATE, self.CONTRACTUAL_PAYMENT,
                ),
            ),
        )
        assert rows == []

    # ── C2-10: hand-computed payoff lock ──────────────────────────

    def test_hand_computed_payoff_with_extra(self):
        """C2-10: $279,985 starting balance / 6% / 336 remaining
        months / $200 extra produces the hand-computed payoff baked
        into the architectural plan.

        Hand-arithmetic anchor for the contractual payment used by
        the projection:
          r = 0.005, n = 336
          (1.005)^336 ~ 5.34324
          M = 279985 * 0.005 * 5.34324 / (5.34324 - 1)
            = 7479.4 / 4.34324
            ~ $1,722.25
        Independently-computed payoff: 262 rows; final pay_date
        2048-02-29 (payment_day=30 clamped to 29 in February); final
        ``remaining_balance == $0.00``.

        Pre-Commit-9 this test also cross-checked against
        ``generate_schedule`` for row-by-row identity; Commit 9
        deletes ``generate_schedule`` and the cross-check.  The
        hand-anchored payoff is the lasting regression lock.
        """
        starting = Decimal("279985.00")
        remaining = 336
        extra = Decimal("200.00")
        payment_day = 30
        contractual = calculate_monthly_payment(
            starting, self.RATE, remaining,
        )
        # Hand-anchored contractual value.
        assert contractual == Decimal("1722.25")
        rows = project_forward(
            ProjectionInputs(
                starting_balance=starting,
                starting_date=date(2026, 5, 30),
                remaining_months=remaining,
                payment_day=payment_day,
                terms_schedule=_terms(
                    date(2026, 5, 30), self.RATE, contractual,
                ),
            ),
            extra_monthly=extra,
        )
        # Hand-anchored independently-computed payoff.
        assert len(rows) == 262
        assert rows[-1].payment_date == date(2048, 2, 29)
        assert rows[-1].remaining_balance == Decimal("0.00")

    # ── C2-11: round_money is the only rounding boundary ──────────

    def test_round_money_is_only_rounding_boundary(self):
        """C2-11: ``project_forward``'s body contains no bare
        ``.quantize(Decimal("0.01"))`` without an explicit
        ``rounding=`` keyword (coding standards: rounding goes
        through ``round_money`` or an explicit ``ROUND_HALF_UP``).

        Source-level guard against silent ``ROUND_HALF_EVEN``
        regressions: a bare ``.quantize(Decimal("0.01"))`` would use
        Python's default banker's rounding and silently drift away
        from the project's hand-computed test assertions.  The
        ``round_money`` helper rejects ``float`` and pins
        ``ROUND_HALF_UP``; this test pins the helper as the only
        rounding surface inside the new primitive.
        """
        engine_source = Path(
            "app/services/amortization_engine/_projection.py"
        ).read_text(encoding="utf-8")
        # Slice out the project_forward body for the assertion.  The
        # body ends at the next top-level ``def`` or ``class`` after
        # ``project_forward``, or at end-of-file -- since the package
        # split, ``project_forward`` is the last definition in
        # ``_projection.py`` (the payoff layer lives in ``_payoff.py``).
        # Taking the min of both markers keeps the slice correct
        # regardless of what construct comes next.
        marker = "def project_forward("
        start = engine_source.index(marker)
        next_def = engine_source.find("\ndef ", start + len(marker))
        next_class = engine_source.find("\nclass ", start + len(marker))
        candidates = [c for c in (next_def, next_class) if c != -1]
        end = min(candidates) if candidates else len(engine_source)
        body = engine_source[start:end]
        # No bare cents quantize without explicit rounding= keyword.
        assert '.quantize(Decimal("0.01"))' not in body
        assert '.quantize(TWO_PLACES)' not in body
        # round_money IS used (positive assertion that the helper
        # is the rounding boundary).
        assert "round_money(" in body
