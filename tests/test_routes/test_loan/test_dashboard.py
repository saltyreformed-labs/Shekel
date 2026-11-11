"""Loan route tests: the loan dashboard page: its render, account creation, negative paths,
regressions, the dashboard with payments, and the nav pills.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, StatusEnum
from app.models.escrow_line import EscrowLine
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory
from app.models.ref import AccountType
from app.services import account_service

from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
    insert_tracking_start_event,
    loan_params_for,
)

from ._helpers import (
    _create_loan_account,
    _create_auto_loan,
    _create_mortgage,
    _create_other_loan,
    _AMORTIZING_TYPES,
    _create_transfer_to_loan,
)


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestLoanDashboard:
    """Tests for the unified loan dashboard page."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods, create_fn):
        """GET returns 200 with loan summary for any amortizing type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Balance owed" in resp.data

    def test_broken_loan_detail_page_folds_not_the_money_blind_replay(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan's detail page shows the seam's fold, not the replay (B-13, C4).

        Before C4 the loan route rendered ``LoanState.current_balance``, the
        resolver's MONEY-BLIND anchor replay, which advances only the SCHEDULED
        principal per payment and discards the actual cash.  C4 points the route at
        the ``balance_at`` seam, which FOLDS the loan's source facts (origination
        anchor + settled payments), so the page shows what the borrower actually
        owes.

        The $240,000 loan (6%, originated 2026-01-01, the month before the
        payment's 2026-02-01 installment -- ruling R-R101 moved it from
        2024-09-01 when plan step recurrence:R16-c-2 began charging every
        installment from origination) receives ONE $10,000 payment:
        $1,200 interest that month ($240,000 x 0.06 / 12) and $8,800 principal, so
        the fold owes $240,000 - $8,800 = $231,200.00.  The money-blind replay
        advances only the scheduled first-month principal of $238.92 ->
        $239,761.08, silently ignoring the $8,561.08 the borrower actually paid
        down.

        Control that fires: asserting the pre-C4 replay value ($239,761.08) is
        ABSENT proves the route moved off the money-blind path -- reverting C4 to
        ``ctx.state.current_balance`` renders that value and turns this red.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Broken Mortgage",
            principal=Decimal("240000.00"), rate=Decimal("0.06000"),
            term=360, origination_date=date(2026, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        checking = create_account_of_type(
            seed_user, db.session, "Checking", "Chk",
            anchor_balance=Decimal("50000.00"),
        )
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, checking, acct, seed_periods[0],
            amount=Decimal("10000.00"),
            settled_on=seed_periods[0].start_date,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"$231,200.00" in resp.data          # the seam's fold (C4)
        assert b"$239,761.08" not in resp.data       # the money-blind replay

    @pytest.mark.parametrize("payment_day,expected", [
        (1, "1st"), (2, "2nd"), (3, "3rd"),
        (11, "11th"), (12, "12th"), (13, "13th"),
        (21, "21st"), (22, "22nd"), (23, "23rd"),
        (30, "30th"), (31, "31st"),
    ])
    def test_dashboard_payment_day_ordinal(
        self, auth_client, seed_user, db, seed_periods, payment_day, expected,
    ):
        """Payment-day ordinal suffix is correct across the 1-31 range (TPLB-11).

        The old logic only special-cased days 1/2/3, so 21/22/23/31 rendered
        as '21th'/'22th'/'23th'/'31th', and the teens were never exercised.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Ordinal Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), payment_day,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert f"{expected} of each month".encode() in resp.data

    def test_the_transfer_prompt_names_the_first_payment_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The prompt says WHEN the payment it offers to create will start.

        Plan step R7c-b.  This form collects no recurrence controls at all --
        the route derives the whole cadence from the loan's contract -- so
        without the date the user commits to a schedule and finds out when it
        starts by opening the template afterwards.

        Hand-computed: a loan originating 2026-04-15 with ``payment_day`` 1
        first bills 2026-05-01 (``first_installment_date``'s convention is the
        payment day of the month AFTER origination, never a later day in the
        origination month itself).  It is asserted as the DATE rather than as
        the string a second derivation would produce, because the whole point
        is that the prompt and
        ``loan_recurrence_sync.loan_cadence_start`` answer once.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Closing In April",
            principal=Decimal("200000.00"), rate=Decimal("0.05000"),
            term=360, origination_date=date(2026, 4, 15), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")

        assert resp.status_code == 200
        assert b"No recurring payment set up" in resp.data, (
            "the prompt must be showing, or the date below proves nothing"
        )
        assert b"The first payment is due" in resp.data
        assert b"May 1, 2026" in resp.data

    def test_dashboard_anchor_scorecard_badges(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The Balance anchors card badges origination AND a tracking-start (C1).

        A mid-life-import loan (origination + a tracking-start assertion) renders
        the anchor scorecard with an "Origination" badge on the origination
        opening row and a "Tracking start" badge on the tracking-start assertion
        row -- since C1 the label rides ``is_tracking_start``, not ``is_opening``
        (the origination is the opening now, the tracking-start a non-opening
        assertion).
        """
        acct = create_loan_account(
            seed_user, db.session, name="Imported Mortgage",
            principal=Decimal("250000.00"), rate=Decimal("0.06000"),
            term=360, origination_date=date(2023, 6, 1),
            account_type=AcctTypeEnum.MORTGAGE,
        )
        insert_tracking_start_event(
            loan_params_for(db.session, acct.id),
            Decimal("180000.00"), date(2026, 2, 10),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Balance anchors" in resp.data
        # Match the badge spans specifically, not the "Origination date" field
        # label elsewhere on the page.
        assert b">Origination</span>" in resp.data
        assert b">Tracking start</span>" in resp.data

    def test_dashboard_setup_when_no_params(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders setup page when params don't exist yet."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

    def test_dashboard_404_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/loan")
        assert resp.status_code == 404

    def test_dashboard_idor(self, auth_client, second_user, db, seed_periods):
        """Another user's loan dashboard returns 404 without leaking data (security)."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(f"/accounts/{other.id}/loan")
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-amortizing account type returns 404.

        The loan dashboard route's _load_loan_account helper returns None
        for both ownership-failure and wrong-type cases, and the route
        now uniformly aborts 404 for any None result. This is the same
        404-for-not-found-or-not-yours security response.
        """
        acct = seed_user["account"]  # checking account
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 404

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated request redirects to login."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_dashboard_shows_term_field(self, auth_client, seed_user, db, seed_periods):
        """Dashboard parameter form includes editable term_months input."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b'name="term_months"' in resp.data
        assert b'value="60"' in resp.data

    def test_dashboard_shows_payoff_calculator(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the "pay off sooner" lever (with its slider) for all loan types."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Pay off sooner" in resp.data
        assert b'data-slider-group="payoff"' in resp.data

    def test_dashboard_shows_icon_from_account_type(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the correct icon class from account_type."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"bi-house" in resp.data


# ── Account Creation Redirect Tests ──────────────────────────────────


class TestLoanAccountCreation:
    """Test that creating amortizing account types redirects to loan setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_creation_redirects_to_loan_dashboard(
        self, auth_client, seed_user, db, seed_periods, type_name,
    ):
        """Creating an amortizing account redirects to the loan dashboard."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": f"New {type_name}",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "20000",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

    def test_params_not_duplicated(self, auth_client, seed_user, db, seed_periods):
        """Visiting dashboard does not create duplicate param records."""
        acct = _create_auto_loan(seed_user, db.session)
        auth_client.get(f"/accounts/{acct.id}/loan")
        auth_client.get(f"/accounts/{acct.id}/loan")

        count = db.session.query(LoanParams).filter_by(account_id=acct.id).count()
        assert count == 1


# ── Negative Path Tests ──────────────────────────────────────────────


class TestLoanNegativePaths:
    """Negative-path and boundary tests for loan routes."""

    def test_negative_interest_rate(self, auth_client, seed_user, db, seed_periods):
        """Negative interest rate is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        # DH-#56: the loan's rate lives in the origination RateHistory row,
        # not the dropped ``LoanParams.interest_rate`` column.  A rejected
        # negative-rate POST must leave that origination rate unchanged.
        orig_rate = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=params.origination_date)
            .one()
            .interest_rate
        )

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"interest_rate": "-0.01", "payment_day": "15"},
        )
        db.session.expire_all()
        after_rate = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=params.origination_date)
            .one()
            .interest_rate
        )
        assert after_rate == orig_rate

    def test_payment_day_zero(self, auth_client, seed_user, db, seed_periods):
        """Payment day 0 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"interest_rate": "5.000", "payment_day": "0"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_payment_day_32(self, auth_client, seed_user, db, seed_periods):
        """Payment day 32 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"interest_rate": "5.000", "payment_day": "32"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_escrow_missing_name(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST without name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"annual_amount": "1200.00"},
        )
        assert resp.status_code == 400

    def test_rate_change_missing_date(self, auth_client, seed_user, db, seed_periods):
        """Rate change without effective_date returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "5.5"},
        )
        assert resp.status_code == 400

    def test_escrow_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/escrow",
            data={"name": "Tax", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 404

    def test_rate_change_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Rate change to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/rate",
            data={"interest_rate": "5.5", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

    def test_escrow_idor_add(self, auth_client, second_user, db, seed_periods):
        """Escrow add to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/escrow",
            data={"name": "Stolen", "annual_amount": "9999.00"},
        )
        assert resp.status_code == 404

        count = db.session.query(EscrowLine).filter_by(account_id=other.id).count()
        assert count == 0


class TestLoanDashboardRegression:
    """Regression baseline for Section 5 loan dashboard changes.

    Verifies dashboard rendering, payoff calculator modes, and
    multi-type support before Section 5 modifies the amortization
    engine and loan UI.
    """

    @pytest.mark.parametrize("account_type,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_dashboard_renders_for_all_amortizing_types(
        self, auth_client, seed_user, db, seed_periods,
        account_type, principal, rate, term, max_term,
    ):
        """Dashboard must render successfully for every amortizing account type.

        Section 5 may add type-specific dashboard panels.  This ensures
        all existing types continue to work.
        """
        acct = _create_loan_account(
            seed_user, db.session, account_type, f"Test {account_type.value}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should display the monthly payment.
        assert "Monthly" in html or "monthly" in html

    @pytest.mark.parametrize("account_type,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_payoff_extra_payment_all_types(
        self, auth_client, seed_user, db, seed_periods,
        account_type, principal, rate, term, max_term,
    ):
        """Payoff calculator extra-payment mode works for all amortizing types.

        Verifies months saved, interest saved, and new payoff date are
        present in the response.
        """
        acct = _create_loan_account(
            seed_user, db.session, account_type, f"Test {account_type.value}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Response must contain savings metrics.
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_returns_required_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target-date mode returns the extra monthly payment needed.

        Verifies the payoff calculator correctly handles the target_date
        code path and returns a numeric result.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should contain a dollar amount for the required extra payment.
        assert "$" in html or "extra" in html.lower()
        # No recurring plan exists, so the raw single-number display
        # renders -- not the plan-aware reframe.
        assert "Required Extra Monthly Payment" in html
        assert "Current Plan" not in html

    def test_payoff_target_date_with_plan_shows_reframed_answer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A recurring plan switches the target-date result to the reframe.

        F-27 ("fix + reframe, show both"): with a projected transfer
        paying the loan, the headline is the extra needed ON TOP of the
        plan; the raw no-plan figure stays as the secondary line.
        Without the fix, the route discarded ``ctx.loan.payments`` and
        showed only the overstated raw number.

        Since plan step C8f the headline folds the loan's forward PLAN
        (``balance_at.loan_required_extra``) and the panel no longer
        prints "Current Plan Pays Off" -- that is the payoff CHIP's
        question, and answering it here from a second producer is how
        the panel came to contradict the chip.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Projected (future) transfer well above the ~$1,580 contractual
        # P&I, so the plan demonstrably contributes extra principal.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("2500.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The plan-aware headline renders...
        assert "Add on Top of Your Current Plan" in html
        # ...the raw figure stays as the secondary line...
        assert "Without your recurring plan" in html
        # ...and the payoff date is NOT restated here (C8f): one question,
        # one producer, one place on the page.
        assert "Current Plan Pays Off" not in html

    def test_payoff_zero_extra_payment_shows_standard_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Zero extra payment should return standard schedule metrics
        with zero months saved and zero interest saved.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # With zero extra, months saved should be 0.
        assert "0 months" in html.lower() or "0 mo" in html.lower() or \
               "$0" in html

    def test_payoff_invalid_mode_does_not_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Invalid payoff mode must not cause a server error.

        The handler returns 200 with default/empty results rather than
        a 400 validation error.  This documents the current behavior.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        # Must not crash -- 200 or 400 are both acceptable, not 500.
        assert resp.status_code != 500

    def test_payoff_negative_extra_payment_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative extra payment must be rejected by validation."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "-100.00"},
        )
        # Should not succeed -- either 400 or validation error.
        assert resp.status_code in (400, 422) or b"error" in resp.data.lower()

    def test_dashboard_idor_blocked(
        self, auth_client, seed_second_user, seed_second_periods,
        second_auth_client, seed_user, seed_periods, db,
    ):
        """User A cannot view User B's loan dashboard.

        Verifies the IDOR protection returns an identical response
        for 'not found' and 'not yours' per the security response rule.
        """
        other_acct = _create_loan_account(
            seed_second_user, db.session, AcctTypeEnum.MORTGAGE, "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        # User A tries to access User B's dashboard.
        resp = auth_client.get(f"/accounts/{other_acct.id}/loan")
        # Must not return 200 -- should redirect or return 404.
        assert resp.status_code in (302, 404)
        if resp.status_code == 200:
            pytest.fail("IDOR: User A could view User B's loan dashboard")


class TestLoanDashboardWithPayments:
    """Integration tests for payment-aware loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    payment history from the transfers into the loan, each read as its leg
    on the loan account (the settled ones were shadow transactions until plan
    step balance:X-bi-6-4b), and pass it to the amortization engine.
    """

    def test_dashboard_no_payments_backward_compat(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers renders identically to pre-5.1 behavior.

        This complements the Commit #0 regression tests by explicitly
        verifying the payment integration code path produces the same
        output when no payments exist.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html

    def test_dashboard_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed transfer payments renders successfully.

        A Paid transfer to the loan account is a confirmed payment, its leg
        on the loan account (a confirmed shadow income transaction until plan
        step balance:X-bi-6-4d-2).  The dashboard should load it and pass it
        to the engine without error.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html

    def test_dashboard_with_projected_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with projected (future) transfer payments renders.

        Projected transfers to the loan account represent committed future
        payments.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_dashboard_with_mixed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed + projected payments renders correctly.

        This is the typical real-world case: past payments are confirmed
        (Paid/Settled), future payments are projected.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_payoff_extra_payment_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator extra payment mode works with payment history.

        The calculator should not crash when a settled payment into the
        loan account exists.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator target date mode works with payment history.

        The target date mode projects from the loan's seam-derived balance
        (not derived from payments in this commit).
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_cancelled_transfer_excluded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Cancelled transfers do not affect the dashboard projection.

        A cancelled payment should not appear in the payment history.
        The dashboard output should match the no-payments case.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.CANCELLED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html


# ── Nav-Pills Consistency Tests ─────────────────────────────────────


class TestLoanNavPills:
    """Tests verifying loan dashboard uses nav-pills instead of nav-tabs."""

    def test_loan_dashboard_renders_pills(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard contains nav-pills markup."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"nav-pills" in resp.data

    def test_loan_dashboard_no_nav_tabs(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard does not contain nav-tabs markup."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"nav-tabs" not in resp.data

    def test_loan_uses_scroll_pills(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard contains shekel-scroll-pills class."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"shekel-scroll-pills" in resp.data

    def test_no_mobile_scroll_tabs_in_loan(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard does not contain mobile-scroll-tabs class."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"mobile-scroll-tabs" not in resp.data

    def test_loan_data_bs_toggle_pill(self, auth_client, seed_user, db, seed_periods):
        """All toggle attributes use pill, not tab."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        html = resp.data.decode()
        assert 'data-bs-toggle="pill"' in html
        assert 'data-bs-toggle="tab"' not in html
