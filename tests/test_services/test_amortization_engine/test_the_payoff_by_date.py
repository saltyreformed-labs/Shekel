"""The payoff-by-date search: its answers, the contract-only extra-payment search at its core, and
its behaviour on project_forward since the engine split.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.amortization_engine import (
    PayoffRequest,
    ProjectionInputs,
    calculate_monthly_payment,
    calculate_payoff_by_date,
    project_forward,
    required_extra_for_projection,
)
from tests._repo_root import REPO_ROOT

from ._helpers import _terms


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
            REPO_ROOT / "app" / "services" / "amortization_engine" / "_payoff.py"
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
