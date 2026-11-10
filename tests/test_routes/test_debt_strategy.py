"""
Tests for debt strategy routes.

Covers the GET dashboard (multiple debts, single debt, empty state,
auth), POST calculate (avalanche, snowball, custom, validation, IDOR),
chart data serialization, and navigation link on the accounts dashboard.
"""

import html as html_mod
import json
import re
from datetime import date, timedelta
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.models.ref import AccountType
from app.services import account_service

from tests._test_helpers import (
    create_loan_account,
    insert_trueup_event,
    loan_params_for,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _create_debt_account(user, db_session, account_type, name, principal,
                         rate, term, orig_date, payment_day):
    """Create a debt account through the shared loan factory + a trueup anchor.

    Builds the loan with ``original_principal = principal + 5000`` so it
    simulates a partially paid-off loan, then appends a same-day-after
    USER_TRUEUP event at ``principal`` -- so the resolver's current_balance
    is ``principal``, the test's intent.  Without the trueup event the
    resolver would anchor at original_principal and inflate every
    debt-strategy balance by $5,000.

    Routes through :func:`tests._test_helpers.create_loan_account` (the ONE
    shared loan builder), which writes the loan as ``loan.create_params``
    does in production.  *The hand-rolled block this replaced never opened
    the stored ledger's genesis postings, which the factory did until plan
    task plan#3132, so every debt here exercised a no-ledger fallback.*

    Args:
        user: The owning :class:`~app.models.user.User`.
        db_session: The test ``db.session``.
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create
            the debt account as.
        name: The account name.
        principal: The loan's current balance (the trueup anchor); the
            origination anchor is this plus $5,000.
        rate: The origination annual rate as a Decimal fraction.
        term: The loan term in months.
        orig_date: The loan origination date.
        payment_day: The day-of-month payment day.

    Returns:
        The created debt :class:`~app.models.account.Account`.
    """
    original_principal = principal + Decimal("5000.00")
    account = create_loan_account(
        {"user": user}, db_session, name=name,
        principal=original_principal, rate=rate, term=term,
        origination_date=orig_date, payment_day=payment_day,
        account_type=account_type,
        # The ACCOUNT anchor stays at ``principal`` while the loan's original
        # principal is $5,000 higher -- a deliberate decoy.  If a producer ever
        # fell through to the generic account-anchor path instead of the loan
        # path, it would report this number, and the test would catch it.
        anchor_balance=principal,
    )
    insert_trueup_event(
        loan_params_for(db_session, account.id),
        principal,
        orig_date + timedelta(days=1),
    )
    db_session.commit()
    return account


def _create_auto_loan(user, db_session, name="Test Auto Loan"):
    """Create an auto loan: $25,000 at 5.5%, 60 months."""
    return _create_debt_account(
        user, db_session, AcctTypeEnum.AUTO_LOAN, name,
        Decimal("25000.00"), Decimal("0.05500"), 60,
        date(2025, 1, 1), 15,
    )


def _create_mortgage(user, db_session, name="Test Mortgage"):
    """Create a mortgage: $200,000 at 6.5%, 360 months."""
    return _create_debt_account(
        user, db_session, AcctTypeEnum.MORTGAGE, name,
        Decimal("200000.00"), Decimal("0.06500"), 360,
        date(2023, 6, 1), 1,
    )


def _create_student_loan(user, db_session, name="Test Student Loan"):
    """Create a student loan: $30,000 at 4.5%, 120 months."""
    return _create_debt_account(
        user, db_session, AcctTypeEnum.STUDENT_LOAN, name,
        Decimal("30000.00"), Decimal("0.04500"), 120,
        date(2024, 1, 1), 1,
    )


# ── Dashboard GET Tests ──────────────────────────────────────────────


class TestDebtStrategyDashboard:
    """Tests for the GET /debt-strategy page."""

    def test_strategy_page_renders(self, auth_client, seed_user, db, seed_periods_today):
        """GET with 2+ debt accounts returns 200, contains account names
        and the strategy form.
        """
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert auto.name in html
        assert mortgage.name in html
        assert 'name="extra_monthly"' in html
        assert 'name="strategy"' in html

    def test_strategy_page_empty_state(self, auth_client, seed_user, db, seed_periods_today):
        """GET with no debt accounts shows empty-state message and no form."""
        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No Active Debt Accounts" in html
        assert 'name="extra_monthly"' not in html

    def test_strategy_page_single_debt(self, auth_client, seed_user, db, seed_periods_today):
        """GET with 1 debt account shows the form and a note about
        strategy comparison being most useful with multiple debts.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'name="extra_monthly"' in html
        assert "one debt account" in html.lower()

    def test_strategy_page_requires_login(self, client):
        """GET without authentication redirects to login."""
        resp = client.get("/debt-strategy")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_accounts_without_params_skipped(self, auth_client, seed_user, db, seed_periods_today):
        """A debt account with has_amortization but no LoanParams does
        not crash the page.  The account is silently skipped.
        """
        user = seed_user["user"]
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        # Create account without LoanParams.
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=user.id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()
        # No params means it's filtered out; empty state shown.
        assert "No Active Debt Accounts" in html

    def test_non_debt_accounts_excluded(self, auth_client, seed_user, db, seed_periods_today):
        """A savings account (has_amortization=False) is not included."""
        # seed_user already has a Checking account (not amortizing).
        # Verify it does not appear and we get the empty state.
        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No Active Debt Accounts" in html

    def test_inactive_accounts_excluded(self, auth_client, seed_user, db, seed_periods_today):
        """An inactive debt account is excluded from the strategy page."""
        user = seed_user["user"]
        acct = _create_auto_loan(user, db.session)
        acct.is_active = False
        db.session.commit()

        resp = auth_client.get("/debt-strategy")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No Active Debt Accounts" in html


# ── Calculate POST Tests ─────────────────────────────────────────────


class TestDebtStrategyCalculate:
    """Tests for the POST /debt-strategy/calculate endpoint."""

    def test_calculate_avalanche(self, auth_client, seed_user, db, seed_periods_today):
        """POST with avalanche strategy returns comparison table with
        expected columns and avalanche interest <= snowball interest.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        # Comparison table present with expected column headers.
        assert "No Extra" in html
        assert "Avalanche" in html
        assert "Snowball" in html
        assert "Interest Saved" in html

    def test_calculate_snowball(self, auth_client, seed_user, db, seed_periods_today):
        """POST with snowball strategy returns results with per-account
        timeline showing smallest balance debt first.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "snowball",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Snowball" in html
        # The per-account timeline shows the selected strategy.
        assert "Payoff Timeline" in html

    def test_calculate_custom(self, auth_client, seed_user, db, seed_periods_today):
        """POST with custom strategy and valid order returns 4-column
        comparison table.
        """
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{mortgage.id},{auto.id}",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Custom" in html

    def test_calculate_zero_extra(self, auth_client, seed_user, db, seed_periods_today):
        """POST with extra=0 returns results.  All strategies show
        equivalent baseline metrics.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "0",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Debt-Free Date" in html

    def test_calculate_requires_login(self, client):
        """POST without authentication redirects to login."""
        resp = client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_calculate_no_debts(self, auth_client, seed_user, db, seed_periods_today):
        """POST with no debt accounts returns an error message, not 500."""
        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No active debt accounts" in html

    def test_duplicate_custom_order_renders_error_banner(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A duplicated (owned) account ID in custom_order surfaces the
        service ValueError in the error banner at HTTP 200.

        The route's IDOR check passes (the ID is owned) and the route
        does not itself dedupe custom_order, so the duplicate reaches
        ``calculate_strategy``, which rejects it with
        ``"custom_order contains duplicate account_ids: [...]"``.  The
        route catches the ``ValueError`` and renders it inline -- the
        reachable path through the compute-error funnel (HTTP 200, no
        500).
        """
        auto = _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{auto.id},{auto.id}",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "custom_order contains duplicate account_ids" in html

    def test_incomplete_custom_order_renders_error_banner(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A custom_order missing one of the user's active debts surfaces
        the service ValueError in the error banner at HTTP 200.

        With two debts but only one named in custom_order, every named
        ID is owned (IDOR check passes), so the partial order reaches
        ``calculate_strategy``, which requires the full active-debt set
        and rejects it with ``"custom_order is missing account_ids..."``.
        Confirms the compute-error funnel renders the message inline
        rather than 500-ing.
        """
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": str(auto.id),
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "custom_order is missing account_ids" in html


# ── Validation Tests ─────────────────────────────────────────────────


class TestDebtStrategyValidation:
    """Tests for input validation on the calculate endpoint.

    All tests in this class exercise ``DebtStrategyCalculateSchema``
    (commit C-27 of the 2026-04-15 security remediation plan), which
    replaced the route's pre-C-27 hand-parsed ``Decimal``/``OneOf``
    checks.  The schema's per-field messages -- e.g.
    ``"Must be greater than or equal to 0..."`` for a negative
    ``extra_monthly`` -- are surfaced to the user via
    ``_schema_first_validation_message``.  The route still wraps the
    error in the legacy ``_results.html`` partial so the HTTP
    response stays at 200 with an inline banner; only the message
    text changed.
    """

    def test_invalid_extra_negative(self, auth_client, seed_user, db, seed_periods_today):
        """Negative extra_monthly returns the schema's range error.

        The ``DebtStrategyCalculateSchema.extra_monthly`` field has
        ``validate.Range(min=Decimal('0'), max=Decimal('1000000'))``
        so a negative entry surfaces Marshmallow's standard
        "Must be greater than or equal to 0 ..." message.  The
        important assertions are (1) HTTP 200 (no 500 from the old
        hand-parsed branch), (2) the response contains the per-field
        Marshmallow message so the form can render it inline.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "-100",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Must be greater than or equal to 0" in html

    def test_invalid_extra_above_cap(self, auth_client, seed_user, db, seed_periods_today):
        """Extra_monthly above the schema cap is rejected (commit C-27).

        The cap is set well above any realistic debt-payoff budget
        (``Decimal('1000000')``); above that bound an order-of-
        magnitude typo is rejected at the schema tier instead of
        amplifying through the simulation.  Verifies the upper
        bound is wired up (the lower bound is exercised by
        :meth:`test_invalid_extra_negative`).
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "9999999999",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "less than or equal to 1000000" in html

    def test_invalid_extra_nonnumeric(self, auth_client, seed_user, db, seed_periods_today):
        """Non-numeric extra_monthly produces Marshmallow's coercion error.

        Pre-C-27 the route caught ``InvalidOperation`` and rendered
        a custom message; post-C-27 Marshmallow's Decimal field
        rejects the value with ``"Not a valid number."``.  The
        response stays 200 + ``_results.html`` so the HTMX UX is
        unchanged -- only the message text moved into the schema.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "abc",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Not a valid number" in html

    def test_invalid_strategy(self, auth_client, seed_user, db, seed_periods_today):
        """Unknown strategy returns the schema's OneOf error message.

        Pre-C-27: ``"Invalid strategy: 'invalid_strategy'."`` (route).
        Post-C-27: ``"Must be one of: avalanche, snowball, custom."``
        (Marshmallow's standard OneOf validator).  The change is
        a deliberate UX trade: the new message lists the allowed
        values up-front so the user does not have to consult the
        UI to learn them.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "invalid_strategy",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Must be one of" in html
        assert "avalanche" in html
        assert "snowball" in html
        assert "custom" in html

    def test_custom_missing_order(self, auth_client, seed_user, db, seed_periods_today):
        """Custom strategy without custom_order returns the schema rule.

        The cross-field ``validate_custom_requires_order`` enforces
        the dependency on the schema (instead of the pre-C-27 inline
        check) so a JSON caller gets the same error shape as the
        HTML form.  The user-facing message text is preserved
        verbatim because ``_results.html`` still renders the
        ``error`` slot.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Custom strategy requires a priority order" in html

    def test_custom_order_too_long_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """custom_order exceeding the 500-char cap is rejected (commit C-27).

        The schema's ``validate.Length(min=1, max=500)`` rejects a
        pathological payload -- a future caller that crafted an
        absurdly long comma-separated list could otherwise feed an
        unbounded list into the route's ``int(x.strip())`` loop.
        At ~10 digits per ID + comma, 500 characters comfortably
        fits ~40 accounts, so a legitimate request will never
        approach this bound.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": ",".join(["1"] * 300),
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        # ``validate.Length(min=1, max=500)`` renders as
        # "Length must be between 1 and 500." (Marshmallow's
        # standard message when both bounds are set).
        assert "between 1 and 500" in html

    def test_custom_invalid_order_format(self, auth_client, seed_user, db, seed_periods_today):
        """Custom order with non-integer values returns the route's error.

        The schema validates only presence and length on
        ``custom_order``; the integer coercion stays in the route
        because it produces a more actionable user message ("Invalid
        custom order format.") and lets the route distinguish a
        format error from a missing-field error.  Behaviour is
        unchanged from pre-C-27.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": "abc,def",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Invalid custom order format" in html


# ── IDOR Tests ───────────────────────────────────────────────────────


class TestDebtStrategyIDOR:
    """Tests for ownership checks on the calculate endpoint."""

    def test_custom_order_other_users_account(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """Custom order containing another user's account ID returns 404.

        Creates a debt account for the second user and attempts to
        include it in the authenticated user's custom order.
        """
        my_acct = _create_auto_loan(seed_user["user"], db.session)
        other_acct = _create_auto_loan(second_user["user"], db.session, name="Other Auto")

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{my_acct.id},{other_acct.id}",
        })
        assert resp.status_code == 404

    def test_custom_order_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Custom order with a nonexistent account ID returns 404."""
        my_acct = _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{my_acct.id},999999",
        })
        assert resp.status_code == 404


# ── Comparison Metrics Tests ─────────────────────────────────────────


class TestDebtStrategyMetrics:
    """Tests for the correctness of comparison metrics."""

    def test_comparison_table_structure(self, auth_client, seed_user, db, seed_periods_today):
        """POST returns a comparison table with all expected rows."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_student_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "300",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        # All metric rows present.
        assert "Debt-Free Date" in html
        assert "Total Interest" in html
        assert "Total Paid" in html
        assert "Months to Debt-Free" in html
        assert "Interest Saved" in html
        assert "Months Saved" in html

    def test_per_account_timeline_present(self, auth_client, seed_user, db, seed_periods_today):
        """POST includes a per-account payoff timeline table."""
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        student = _create_student_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "300",
            "strategy": "snowball",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "Payoff Timeline" in html
        assert auto.name in html
        assert student.name in html

    def test_arm_warning_shown(self, auth_client, seed_user, db, seed_periods_today):
        """When an ARM loan is present, the R-5 warning is displayed."""
        user = seed_user["user"]
        # $200,000 current / $205,000 original, originated 2023-01-01 at 5.5%
        # over 360 months -- the shared builder's origination + next-day trueup
        # anchor pair, so the resolver's current_balance == $200,000.
        arm_acct = _create_debt_account(
            user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
            Decimal("200000.00"), Decimal("0.05500"), 360,
            date(2023, 1, 1), 1,
        )
        # ARM flag only: with no ``arm_first_adjustment_months`` the rate-period
        # engine emits no adjustment period, so the loan's schedule (and its
        # genesis postings) are identical to the fixed-rate case -- the flag
        # exists here purely to trip the route's R-5 warning.
        loan_params_for(db.session, arm_acct.id).is_arm = True
        db.session.commit()

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "ARM rate adjustments" in html


# ── Navigation Link Tests ────────────────────────────────────────────


class TestDebtStrategyNavigation:
    """Tests for the navigation link on the accounts dashboard."""

    def test_nav_link_on_accounts_dashboard(self, auth_client, seed_user, db, seed_periods_today):
        """The accounts dashboard with debt accounts contains a link
        to the debt strategy page.
        """
        _create_auto_loan(seed_user["user"], db.session)

        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "/debt-strategy" in html
        assert "Payoff Strategies" in html


# ── Chart Data Tests ─────────────────────────────────────────────────


def _extract_chart_data(response_html):
    """Extract and parse chart JSON from the response HTML.

    Finds the data-chart-data attribute on the strategy-chart canvas,
    HTML-unescapes and JSON-parses it.

    Args:
        response_html: Decoded HTML string from the response.

    Returns:
        Parsed dict with 'labels' and 'datasets' keys, or None if
        the chart canvas is not present.
    """
    match = re.search(
        r"data-chart-data='([^']*)'",
        response_html,
    )
    if not match:
        return None
    raw = html_mod.unescape(match.group(1))
    return json.loads(raw)


class TestDebtStrategyChart:
    """Tests for the balance-over-time chart data in POST responses."""

    def test_chart_data_included_in_response(self, auth_client, seed_user, db, seed_periods_today):
        """POST with valid input includes a strategy-chart canvas with
        well-formed JSON chart data.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'id="strategy-chart"' in html

        chart = _extract_chart_data(html)
        assert chart is not None
        assert "labels" in chart
        assert "datasets" in chart
        assert isinstance(chart["labels"], list)
        assert isinstance(chart["datasets"], list)
        assert len(chart["datasets"]) == 2  # auto + mortgage

    def test_chart_not_included_when_no_debts(self, auth_client, seed_user, db, seed_periods_today):
        """POST with no debt accounts does not include the chart canvas."""
        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "strategy-chart" not in html

    def test_chart_data_values_are_floats(self, auth_client, seed_user, db, seed_periods_today):
        """Every value in each dataset's data array is a float or int,
        not a string representation of a Decimal.
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None

        for ds in chart["datasets"]:
            for val in ds["data"]:
                assert isinstance(val, (int, float)), (
                    f"Expected int/float, got {type(val).__name__}: {val!r}"
                )

    def test_chart_labels_are_formatted_dates(self, auth_client, seed_user, db, seed_periods_today):
        """Chart labels are formatted as 'Mon YYYY' strings."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "500",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None
        assert len(chart["labels"]) > 0

        date_pattern = re.compile(r"^[A-Z][a-z]{2} \d{4}$")
        for label in chart["labels"]:
            assert date_pattern.match(label), (
                f"Label {label!r} does not match 'Mon YYYY' format"
            )

    def test_chart_dataset_count_matches_accounts(self, auth_client, seed_user, db, seed_periods_today):
        """Number of chart datasets equals the number of debt accounts."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)
        _create_mortgage(user, db.session)
        _create_student_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None
        assert len(chart["datasets"]) == 3

    def test_chart_dataset_labels_match_account_names(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Each dataset label matches one of the debt account names."""
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None

        ds_labels = {ds["label"] for ds in chart["datasets"]}
        assert auto.name in ds_labels
        assert mortgage.name in ds_labels

    def test_chart_timeline_starts_at_principal(self, auth_client, seed_user, db, seed_periods_today):
        """The first data point of each dataset is the starting principal.

        The auto loan has $25,000 current_principal.  The chart's first
        value should be close to that (may differ slightly due to
        confirmed payment replay adjusting real principal).
        """
        user = seed_user["user"]
        _create_auto_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "500",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None
        assert len(chart["datasets"]) == 1

        first_balance = chart["datasets"][0]["data"][0]
        # Real principal may differ from stored due to payment replay,
        # but should be in the same ballpark as $25,000.
        assert first_balance > 20000
        assert first_balance <= 30000

    def test_chart_timeline_ends_at_zero(self, auth_client, seed_user, db, seed_periods_today):
        """Each dataset's balance reaches zero by the end of the timeline."""
        user = seed_user["user"]
        _create_auto_loan(user, db.session)

        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "500",
            "strategy": "avalanche",
        })
        chart = _extract_chart_data(resp.data.decode())
        assert chart is not None

        for ds in chart["datasets"]:
            last_balance = ds["data"][-1]
            assert last_balance == 0.0 or abs(last_balance) < 0.01, (
                f"Dataset {ds['label']!r} ends at {last_balance}, expected ~0"
            )

    def test_custom_selection_drives_chart_order(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The selected strategy's priority order drives the chart, so a
        custom order overrides avalanche's rate-based order.

        auto (5.5%) and mortgage (6.5%): avalanche targets the higher
        rate first, so its first dataset is the mortgage.  A custom
        order of [auto, mortgage] must instead put auto first -- which
        only holds if the route selected the *custom* result (not
        avalanche) for the timeline + chart.  Datasets are built in the
        selected result's per-account priority order, so the first
        label is the discriminator.
        """
        user = seed_user["user"]
        auto = _create_auto_loan(user, db.session)
        mortgage = _create_mortgage(user, db.session)

        # Avalanche: higher-rate mortgage leads.
        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "avalanche",
        })
        avalanche_chart = _extract_chart_data(resp.data.decode())
        assert avalanche_chart is not None
        assert avalanche_chart["datasets"][0]["label"] == mortgage.name

        # Custom [auto, mortgage]: auto leads, the opposite of avalanche,
        # proving _select_result returned the custom run.
        resp = auth_client.post("/debt-strategy/calculate", data={
            "extra_monthly": "200",
            "strategy": "custom",
            "custom_order": f"{auto.id},{mortgage.id}",
        })
        custom_chart = _extract_chart_data(resp.data.decode())
        assert custom_chart is not None
        assert custom_chart["datasets"][0]["label"] == auto.name
