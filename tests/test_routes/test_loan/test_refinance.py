"""Loan route tests: the refinance calculator and its forward-only baseline.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import AcctTypeEnum, InterestAccrualConventionEnum, StatusEnum
from app.models.loan_features import RateHistory
from app.models.ref import AccountType
from app.services import account_service

from ._helpers import (
    _create_loan_account,
    _create_mortgage,
    _create_other_loan,
    _create_transfer_to_loan,
    _create_exact_mortgage,
)


class TestRefinanceCalculator:
    """Tests for the refinance what-if calculator (Commit 5.10-1).

    Verifies side-by-side comparison of current loan vs. hypothetical
    refinance scenario, including monthly savings, interest savings,
    break-even calculation, and edge cases.
    """

    def test_refinance_lower_rate(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-1: Lower rate refinance shows monthly and interest savings.

        Mortgage at 6.5% refinanced to 5.0%, same term (360 months).
        Monthly payment must decrease and interest savings must be positive.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Both savings columns should be green (positive savings).
        assert "text-success" in html
        # Comparison table must contain key metrics.
        assert "Monthly Payment" in html
        assert "Total Interest" in html

    def test_refinance_shorter_term(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-2: Shorter term increases monthly but decreases total interest.

        30yr loan refinanced to 15yr at the same rate.  Monthly payment
        increases (red), total interest decreases significantly (green),
        and no break-even is shown (monthly savings <= 0).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "6.5", "new_term_months": "180"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Monthly increases (red), interest decreases (green).
        assert "text-danger" in html
        assert "text-success" in html
        # No break-even when monthly savings <= 0.
        assert "Break-even" not in html

    def test_refinance_with_closing_costs(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-3: Closing costs produce a break-even calculation.

        Refinance to lower rate with $5,000 closing costs.
        Break-even should be calculated and displayed.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" in html
        assert "months" in html
        # Closing costs appear in the break-even explanation.
        assert "$5,000.00" in html

    @pytest.mark.parametrize("data", [
        {},
        {"new_rate": "5.0"},
        {"new_term_months": "360"},
        {"new_rate": "-1", "new_term_months": "360"},
        {"new_rate": "5.0", "new_term_months": "0"},
        {"new_rate": "5.0", "new_term_months": "700"},
    ])
    def test_refinance_validation(self, auth_client, seed_user, db, seed_periods, data):
        """C-5.10-4: Invalid inputs return validation error.

        Tests missing required fields, negative rate, zero term, and
        term exceeding max (600).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data=data,
        )
        assert resp.status_code == 200
        assert b"Please correct" in resp.data

    def test_refinance_idor(self, auth_client, second_user, db, seed_periods):
        """C-5.10-5: Refinance on another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 404

    def test_refinance_principal_auto_calculated(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-6: Without new_principal, refinance uses current + closing.

        Auto-calculated: refi_principal = current_real_principal + closing_costs.
        The principal row shows the difference from closing costs.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "3000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Principal row appears (refi != current due to closing costs).
        assert "Principal" in html
        # Current principal = $250,000 (from _create_mortgage).
        assert "$250,000.00" in html
        # Refi principal = $250,000 + $3,000 = $253,000.
        assert "$253,000.00" in html

    def test_refinance_principal_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-7: User-provided principal overrides auto-calculation.

        When new_principal is specified, the refinance ignores current
        balance + closing costs and uses the override directly.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
                "new_principal": "300000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Refi principal should be the override.
        assert "$300,000.00" in html
        # Should NOT show $255,000 (current + closing).
        assert "$255,000.00" not in html

    def test_refinance_no_closing_costs(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-8: Zero closing costs means no break-even calculation.

        With closing_costs=0, refi_principal = current_real_principal
        and no break-even message is shown.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" not in html
        # Lower rate still produces savings.
        assert "text-success" in html

    def test_refinance_higher_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-9: Higher rate refinance shows negative savings.

        Refinancing to a higher rate increases both monthly payment
        and total interest.  Differences should be red (negative).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "8.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Both monthly and interest show red (negative savings).
        assert "text-danger" in html
        # No break-even when savings are negative.
        assert "Break-even" not in html

    def test_refinance_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-10: Confirmed payments reduce real principal for refinance.

        With confirmed payments, the current side uses the committed
        schedule metrics and the refinance principal is based on the
        reduced real balance, not the balance stated at setup.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1700.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Valid comparison produced (not an error).
        assert "Monthly Payment" in html
        assert "Total Interest" in html

    def test_refinance_arm_current_side(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-11: ARM loan current side reflects rate-adjusted schedule.

        An ARM mortgage with rate history uses the adjusted committed
        schedule for the current baseline.  Refinancing to a lower
        fixed rate should show savings.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1, is_arm=True,
        )
        rh = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 1, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(rh)
        # DH-#56: the prior ``params.interest_rate = 0.07`` mirror-write is
        # gone -- the RateHistory change row at 2025-01-01 (above) already
        # makes the resolver-derived current rate 7% as of the frozen today
        # (2026-03-20), which is what the refinance current side reads.
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Monthly Payment" in html
        # 5% < 7% current ARM rate → savings expected.
        assert "text-success" in html

    def test_refinance_paid_off_loan(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-12: Paid-off loan returns error, not a comparison."""
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Paid Off Mortgage",
            Decimal("0.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        assert b"paid off" in resp.data

    def test_refinance_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-13: Loan with no LoanParams returns 404."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Mortgage",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(acct)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 404

    def test_refinance_break_even_calculation_exact(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-14: Break-even equals ceil(closing_costs / monthly_savings).

        200K mortgage at 6.5%, refinance to 5.0%, 360 months.
        Closing costs = $5,000.

        Refi principal = 200000 + 5000 = 205000.
        Current monthly = M(200000, 0.065, 360) = $1,264.14.
        Refi monthly = M(205000, 0.05, 360)    = $1,100.48.
        Monthly savings = 1264.14 - 1100.48     = $163.66.
        Break-even = ceil(5000 / 163.66)        = 31 months.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" in html
        assert "31 months" in html

    def test_refinance_lever_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-15: the dashboard for a configured loan includes the Refinance lever."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Refinance" in html
        assert 'name="new_rate"' in html

    def test_refinance_tab_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-16: Loan without params shows setup page, no Refinance tab."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Mortgage",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(acct)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Setup page does not contain the Refinance tab.
        assert "Refinance Calculator" not in html

    def test_refinance_rate_conversion(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-17: Rate input as percentage is correctly converted to decimal.

        Submitting new_rate=5.0 (meaning 5%) must produce a monthly
        payment consistent with 0.05 annual rate.

        200K at 5.0%, 360 months: M = $1,073.64.
        At 500% (unconverted):    M would be ~$83,333.
        At 0.05% (double-conv):   M would be ~$556.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Correct 5% rate produces $1,073.64/mo refinanced payment.
        assert "$1,073.64" in html

    def test_refinance_comparison_metrics_hand_calculated(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-18: Exact comparison metrics match engine calculation.

        Known loan: $200K, 6.5%, 30yr.
        Refinance: same principal, 5.0%, 30yr, no closing costs.

        Amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1]

        Current:   P=200000, r=0.065/12, n=360
                   M = $1,264.14/mo
                   Total interest = $255,085.82

        Refinance: P=200000, r=0.05/12, n=360
                   M = $1,073.64/mo
                   Total interest = $186,513.24

        Savings:   Monthly  = $1,264.14 - $1,073.64 = $190.50/mo
                   Interest = $255,085.82 - $186,513.24 = $68,572.58
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        # Current monthly P&I.
        assert "$1,264.14" in html
        # Refinance monthly P&I.
        assert "$1,073.64" in html
        # Monthly savings.
        assert "$190.50" in html
        # Current total interest.
        assert "$255,085.82" in html
        # Refinance total interest.
        assert "$186,513.24" in html
        # Interest savings.
        assert "$68,572.58" in html


class TestRefinanceForwardOnlyBaseline:
    """The refinance comparison measures the CURRENT loan contractual + forward-only.

    The refinance side is inherently forward-only (a brand-new loan from today),
    so the current side must be too: "Remaining Term" counts the payments still
    ahead and "Total Interest" the interest still to be paid.  And the current
    side is the pure CONTRACT -- the contract line
    (``balance_at.loan_contract_installments``, plan step recurrence:R16-f), a
    like-for-like minimum-vs-minimum comparison -- never the owner's PLAN (the
    balance seam's fold, which carries a standing extra a borrower could pay on
    either loan; the builder is handed no plan and no history at all, so both
    are structural).  Before these rules the builder used whole-schedule
    aggregates, counting sunk history against a from-today refinance.
    """

    def test_current_side_reads_the_contract_line_through_its_payoff(self):
        """The current side is the contract's installments through the one that clears it.

        The contract line here bills three installments: Mar 2026 (interest
        400.00, leaving 98,500.00), Apr 2026 (interest 300.00, clearing the
        loan) and May 2026, which pays into the zero balance Apr left
        (interest 0.00; projections run past the payoff until finding
        REC-539 is fixed).  The current side must report 2 remaining
        installments, 400.00 + 300.00 = 700.00 of interest and an Apr 2026
        payoff: counting May would read 3, and the route context's payoff
        (a stand-in dated Mar 2026) is the fallback for a contract that bills
        nothing more, so reading it would date the payoff a month early.
        Until plan step recurrence:R16-f the same figures came off the
        composer's ``original_forward`` rows.
        """
        # Pylint: ``import-outside-toplevel`` -- route-private helper under
        # test; imported here so the module import stays route-surface only.
        from app.routes.loan.calculators import (  # pylint: disable=import-outside-toplevel
            _build_refinance_comparison,
        )

        def _installment(month, interest, balance_after):
            """A contract-line outcome's stand-in: the fields the builder reads."""
            return SimpleNamespace(
                due_date=date(2026, month, 1), interest=interest,
                balance_after=balance_after,
            )

        contract = [
            _installment(3, Decimal("400.00"), Decimal("98500.00")),
            _installment(4, Decimal("300.00"), Decimal("0.00")),
            _installment(5, Decimal("0.00"), Decimal("0.00")),
        ]
        # The balance the route threads in explicitly (C4); the route reads the
        # seam's fold.
        current_balance = Decimal("99000.00")
        # The route context stand-in.  ``_build_refinance_comparison`` reads only
        # ``monthly_payment`` and ``payoff_date`` off it, and since plan C8d the
        # payoff is the seam's DERIVED figure carried on the route context.  The
        # value here is the fallback only; this contract clears the loan, so it
        # must NOT be read.
        route_ctx = SimpleNamespace(
            monthly_payment=Decimal("1000.00"),
            payoff_date=date(2026, 3, 1),
        )
        # The loan row's stand-in: its due day, and how its note accrues
        # interest, which the hypothetical refinance keeps (plan step
        # recurrence:R16-d).
        params = SimpleNamespace(
            payment_day=1,
            interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
                InterestAccrualConventionEnum.MONTHLY_1_12,
            ),
        )
        data = {
            "closing_costs": Decimal("0.00"),
            "new_principal": None,
            "new_term_months": 12,
            "new_rate": Decimal("0.05"),
        }

        comparison = _build_refinance_comparison(
            current_balance, route_ctx, contract, data, params,
        )

        # Through the clearing installment: 2 installments, 400 + 300 = 700.00,
        # payoff Apr 2026; a 12-month refinance is 12 - 2 = 10 longer.
        assert comparison["current_remaining_months"] == 2
        assert comparison["current_total_interest"] == Decimal("700.00")
        assert comparison["current_payoff"] == date(2026, 4, 1)
        assert comparison["term_diff"] == 10
        assert comparison["interest_savings"] == (
            Decimal("700.00") - comparison["refi_total_interest"]
        )
