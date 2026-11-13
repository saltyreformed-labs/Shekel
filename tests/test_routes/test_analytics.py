"""
Shekel Budget App -- Analytics Route Tests

Tests for the analytics page shell and HTMX tab endpoints:
  - Authentication required for all endpoints
  - Main page renders with nav-pills and tab-content div
  - Tab endpoints return placeholders/content with HX-Request header
  - A direct (non-HTMX) tab GET renders the shell with that tab active (D13)
  - Nav bar shows Analytics link with correct active state
  - Statements pill groups the income statement + balance sheet toggle
  - Retired Variance / Trends / Year-End URLs redirect to the page
"""

import json
import re
from datetime import date, time
from decimal import Decimal
from html import unescape

import pytest

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum, TxnTypeEnum
from app.services import account_service, status_seam

from app.utils.dates import display_today

from tests._test_helpers import (
    cover_bare_settled_row,
    create_account_of_type,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    set_default_grid_account,
    settle_day_columns,
    start_test_pay_list,
)
from tests._test_helpers import create_settled_cash_transaction, freeze_today
from tests._test_helpers import (
    add_entry,
    create_envelope_txn,
    settlement_if_settling,
)
from app.routes.analytics_view import (
    _bar_pct,
    _history_note,
    _size_lens_rows,
)
from app.services.spending_report_service import (
    SeriesPoint,
    SpendingGroupRow,
    SpendingItemRow,
    SpendingWindow,
)


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    This module relies on calendar-anchored seed_periods (Jan-May 2026)
    and asserts on tax_year=2026, calendar 2026 due_dates, and other
    calendar-anchored values.  Auto-discovery patches every loaded
    module's ``date``/``datetime`` symbols so production code
    (e.g. the statement routes in app.routes.analytics) consuming
    ``date.today()`` agrees with the test's view of "today" regardless
    of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _shell_autoload_target(html):
    """Return the hx-get URL of the shell's single auto-loading element.

    D13: the analytics shell auto-loads its active tab from the #tab-content
    spinner (the one element carrying ``hx-trigger="load"``).  Tests use this
    to assert WHICH tab a direct (non-HTMX) GET pre-selected, since the
    active-tab discriminator drives both that loader's target and the active
    pill.  Returns ``None`` when no auto-loader is present.
    """
    before_load = html.split('hx-trigger="load"')[0]
    loader = before_load[before_load.rfind("<div"):]
    match = re.search(r'hx-get="([^"]+)"', loader)
    return match.group(1) if match else None


def _create_paid_expense_for_route_test(db, seed_user, seed_periods,
                                        name, amount, category_key):
    """Create a settled expense for year-end spending tests.

    Args:
        db: Database session fixture.
        seed_user: User fixture dict.
        seed_periods: Pay periods list.
        name: Transaction name.
        amount: Decimal amount.
        category_key: Key into seed_user['categories'] dict.
    """
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    paid_status_id = ref_cache.status_id(StatusEnum.DONE)
    cat = seed_user["categories"].get(category_key)
    txn = one_off_row_of(
        seed_periods[0],
        name=name,
        amount=amount,
        user_id=seed_periods[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type_id,
        category_id=cat.id if cat else None,
    )
    txn.status_id = paid_status_id
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(seed_periods[0].start_date).items():
        setattr(txn, _column, _value)
    db.session.flush()
    # The record's home is the covering movement (X-bi-4b-1).
    cover_bare_settled_row(db.session, txn, amount)
    db.session.commit()


def _chart_payload(html):
    """Parse the spending chart canvas's ``data-chart`` JSON from a page.

    The attribute value is HTML-escaped by Jinja autoescaping, so it is
    unescaped before parsing.  Raises (failing the test) when the canvas
    or its attribute is missing.

    Args:
        html: The rendered Spending tab HTML.

    Returns:
        The deserialized series dict.
    """
    match = re.search(r'data-chart="([^"]*)"', html)
    assert match is not None, "spending chart data-chart attribute missing"
    return json.loads(unescape(match.group(1)))


# ── Auth Tests ──────────────────────────────────────────────────────


class TestAnalyticsAuth:
    """Tests for authentication requirements on analytics endpoints."""

    def test_analytics_requires_auth(self, app, client):
        """GET /analytics redirects unauthenticated users to login."""
        with app.app_context():
            resp = client.get("/analytics")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_all_tabs_require_auth(self, app, client):
        """Every analytics tab endpoint requires auth.

        The five current tab endpoints AND the three retired-redirect URLs
        (/variance, /trends, /year-end) all sit behind ``@login_required``,
        so an unauthenticated GET 302s to /login before any content render
        or redirect-to-page fires.
        """
        tab_urls = [
            "/analytics/calendar",
            "/analytics/spending",
            "/analytics/taxes",
            "/analytics/income-statement",
            "/analytics/balance-sheet",
            "/analytics/variance",
            "/analytics/trends",
            "/analytics/year-end",
        ]
        with app.app_context():
            for url in tab_urls:
                resp = client.get(url)
                assert resp.status_code == 302, (
                    f"{url} did not require auth"
                )
                assert "/login" in resp.headers["Location"], (
                    f"{url} did not redirect to login"
                )


# ── Page Rendering Tests ──────────────────────────────────────────


class TestAnalyticsPage:
    """Tests for GET /analytics page structure and content."""

    def test_analytics_page_renders(self, app, auth_client, seed_user):
        """GET /analytics returns 200 with Analytics heading."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data

    def test_analytics_page_has_pills(self, app, auth_client, seed_user):
        """GET /analytics shows the four Slice-4 nav pills.

        The Slice-4 shell collapse reduced the nav to four pills: Calendar,
        Spending, Statements, and Taxes.  The Income Statement and Balance
        Sheet are no longer shell pills -- they load inside the Statements
        pill's internal toggle via HTMX, so their labels do not appear on
        the bare page.  The retired Year-End / Variance / Trends labels are
        gone from the nav (their routes now redirect to the page).
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data
            assert b"Calendar" in html
            assert b"Spending" in html
            assert b"Statements" in html
            assert b"Taxes" in html
            assert b"Year-End" not in html
            assert b"Variance" not in html
            assert b"Trends" not in html
            # Income Statement / Balance Sheet load inside the Statements
            # toggle (HTMX), so they are absent from the bare shell.
            assert b"Income Statement" not in html
            assert b"Balance Sheet" not in html

    def test_analytics_page_has_tab_content_div(self, app, auth_client, seed_user):
        """GET /analytics contains the #tab-content target div for HTMX swaps."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b'id="tab-content"' in resp.data

    def test_calendar_tab_is_default_load(self, app, auth_client, seed_user):
        """The shell auto-loads Calendar by default from inside #tab-content.

        D13 moved the auto-load off the pill onto the #tab-content spinner
        (so the initial fetch never pushes a URL); on the bare /analytics page
        (active_tab="calendar") that loader targets the calendar tab.
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            # Exactly one element auto-loads on page load, and it fetches the
            # Calendar partial.
            assert 'hx-trigger="load"' in html
            loader = html.split('hx-trigger="load"')[0].rsplit("<div", 1)[1]
            assert "/analytics/calendar" in loader

    def test_other_tabs_no_auto_load(self, app, auth_client, seed_user):
        """Only one element auto-loads; the pills are click-only (D13).

        The pills push their URL on click (hx-push-url) and no longer carry a
        'load' trigger -- the single auto-load lives on the #tab-content
        spinner -- so exactly one 'load' trigger exists on the page.
        """
        with app.app_context():
            resp = auth_client.get("/analytics")
            html = resp.data.decode()
            load_triggers = html.count('hx-trigger="load"')
            assert load_triggers == 1, (
                f"Expected exactly 1 'load' trigger, found {load_triggers}"
            )
            # Every pill pushes its own URL (D13).
            assert html.count('hx-push-url="true"') == 4

    def test_tab_content_has_spinner(self, app, auth_client, seed_user):
        """The #tab-content div contains spinner markup as initial content."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"spinner-border" in resp.data

    def test_analytics_uses_scroll_pills(self, app, auth_client, seed_user):
        """GET /analytics uses the shekel-scroll-pills class for scroll behavior."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            assert b"shekel-scroll-pills" in resp.data


# ── HTMX Tab Tests ────────────────────────────────────────────────


class TestCalendarTab:
    """Tests for GET /analytics/calendar HTMX partial endpoint."""

    def test_calendar_tab_htmx(self, app, auth_client, seed_user, seed_periods):
        """GET /analytics/calendar with HX-Request returns 200 with calendar."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # Calendar replaced the placeholder; month view renders by default.
            assert b"calendar-grid" in resp.data

    def test_calendar_tab_no_htmx_renders_shell(self, app, auth_client, seed_user):
        """GET /analytics/calendar without HX-Request renders the shell (D13).

        A direct navigation to the tab URL now serves the analytics shell with
        Calendar active (which then auto-loads the calendar partial), instead
        of redirecting to the page and defaulting to Calendar the long way.
        """
        with app.app_context():
            resp = auth_client.get("/analytics/calendar")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/calendar"

    def test_calendar_tab_404_when_account_unresolvable(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """C11-1 (route): unresolvable analytics account returns 404.

        F-2 / Commit 11: pre-remediation the route silently rendered a
        zeroed calendar.  After the contract change, the route maps
        ``CalendarAccountNotResolvableError`` to a 404 ("404 for both
        'not found' and 'not yours'").  Monkeypatching the resolver
        is the deterministic way to simulate the upstream defect
        without coupling the test to user/account fixture deletion.
        """
        from app.services import calendar_service as cs
        monkeypatch.setattr(
            cs, "resolve_analytics_cash_flow_set",
            lambda _user_id, _user_settings, _account_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_calendar_tab_204_when_the_user_has_no_baseline(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A missing baseline answers 204 to this fragment, not 404.

        **Changed at plan step X-v2** (ruling R-BW), and the old answer is the
        reason: this endpoint 404'd, telling a user with a one-click-repairable
        setup problem that their calendar does not exist, while `/savings`
        showed them a fabricated ``$0.00``, the investment page showed a cache
        column as a balance, and the loan page 500'd -- seven answers to one
        state.  There is one now: an HTMX request gets 204 and leaves the DOM
        alone, a page request gets the repair card.  ``CalendarAccountNotResolvableError``
        still means what its name says (no analytics ACCOUNT), and the
        neighbouring test asserts that 404 is untouched.
        """
        # The baseline scenario is resolved inside the balance context now.
        from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
            _context as resolution_context,
        )
        monkeypatch.setattr(
            resolution_context, "get_baseline_scenario",
            lambda _user_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204
            assert resp.data == b""

            # No page arm here, and that is a fact about the endpoint rather
            # than an omission: a plain GET of this URL renders the analytics
            # SHELL, whose tab body is fetched by the HTMX request above, so it
            # never reaches the calendar producer at all.  The page answer for
            # this state is graded where a page actually raises, in
            # ``tests/test_routes/test_no_baseline_policy.py``.

    def test_calendar_tab_404_for_amortizing_account(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """X-a1 (route): ``?account_id=<loan>`` 404s instead of rendering.

        Finding N-38: the calendar's balance line and month-end figure
        are the seam's CASH-FLOW view, which sums the account's own
        transaction rows.  Pointed at a loan it answered with a
        cash-basis figure that ignores interest -- measured on a dev
        clone, ``$531.94`` for a Van Loan owing ``$15,663.59``.  The
        resolver now refuses an amortizing account (ruling D4's gate,
        extended to the surface its enumeration missed), and the route
        maps the unresolvable account to a 404 exactly as it does for a
        cross-owner id.

        No monkeypatch: this drives the REAL resolver with a real loan
        account, so it fails if the kind gate is removed.
        """
        with app.app_context():
            loan = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=ref_cache.acct_type_id(
                        AcctTypeEnum.MORTGAGE,
                    ),
                    name="Mortgage",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(loan)
            db.session.commit()
            loan_id = loan.id

            resp = auth_client.get(
                f"/analytics/calendar?account_id={loan_id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

            year_resp = auth_client.get(
                f"/analytics/calendar?view=year&account_id={loan_id}",
                headers={"HX-Request": "true"},
            )
            assert year_resp.status_code == 404

    def test_calendar_tab_year_view_404_when_account_unresolvable(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """C11-1 (route, year view): year-view path also 404s."""
        from app.services import calendar_service as cs
        monkeypatch.setattr(
            cs, "resolve_analytics_cash_flow_set",
            lambda _user_id, _user_settings, _account_id: None,
        )
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_calendar_tab_200_when_resolvable(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C11-3 (route): valid account + scenario renders the calendar.

        Locks the happy path so future regressions of the exception
        handler (e.g. raising too eagerly) fail loud.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_basis_chip_and_scope(self, app, auth_client, seed_user,
                                           seed_periods):
        """The calendar month view labels its MIXED basis and checking scope.

        Slice-4 cross-cutting fix: the calendar states its data basis (a
        projected balance line combined with measured/projected flows) via
        the unified basis chip, and names the checking account it reads so
        the previously-silent scope is on screen.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "analytics-basis-chip" in html
            assert ">mixed<" in html
            assert "Checking account:" in html

    def test_calendar_default_is_the_saved_default_grid_account(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The route passes the settings row it holds to the service (CC-4-3).

        The calendar's default is the set's PRIMARY -- the owner's saved
        default grid account first -- which the service can only read off
        the ``UserSettings`` row the route already loaded for its thresholds.
        An HYSA saved as the default names itself on the scope line; a route
        that dropped ``user_settings=`` would render Checking here and pass
        every other test in this class.
        """
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Rainy Day",
                anchor_balance=Decimal("1000.00"),
            )
            set_default_grid_account(db.session, seed_user["user"].id, hysa.id)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # The scope line names the balance account.  Its "Checking
            # account:" prefix is the template's own wording and is not
            # pinned here: it predates the set and is wrong for any
            # non-checking line (a candidate row for the design loop).
            assert "account: Rainy Day." in resp.data.decode()

    def test_calendar_account_id_naming_a_card_puts_it_on_the_line(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """``?account_id=<card>`` renders the card's calendar, not a 404.

        The grid's meaning of the parameter since plan step CC-4-3: a member
        of the set on the balance line.  The ownership guard admits it (the
        owner's) and the set twin admits it (a member), so the scope line
        names the card; the loan and cross-user refusals beside this test
        are unchanged.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            resp = auth_client.get(
                f"/analytics/calendar?view=month&year=2026&month=1"
                f"&account_id={card.id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "account: Rewards Card." in resp.data.decode()


# ── Income Statement Tab Tests ────────────────────────────────────


class TestIncomeStatementTab:
    """Tests for GET /analytics/income-statement HTMX partial endpoint.

    Build-Order Step 5: the confirmed-ledger income statement.  The
    money queries read the double-entry posting ledger, so content
    tests settle through ``create_settled_cash_transaction`` (the real
    go-forward posting path) -- a directly-inserted transaction never
    posts and would not appear.
    """

    def test_income_statement_requires_auth(self, app, client):
        """Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/income-statement")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_income_statement_no_htmx_renders_shell(self, app, auth_client,
                                                    seed_user):
        """Non-HTMX request renders the shell with Statements active (D13).

        The Statements pill's default half is the income statement, so a
        direct GET auto-loads /analytics/income-statement inside the shell.
        """
        with app.app_context():
            resp = auth_client.get("/analytics/income-statement")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/income-statement"

    def test_income_statement_htmx_renders(self, app, auth_client, seed_user,
                                           seed_periods):
        """HTMX request renders the statement with its heading and toggle."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Income Statement" in html
            # The pay-period / month / year window toggle.
            assert "Pay Period" in html
            assert "Month" in html
            assert "Year" in html

    def test_income_statement_shows_statements_toggle(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The shared Statements header renders: the confirmed basis chip,
        the Statements title, and the Balance Sheet toggle target so the two
        confirmed-ledger reports switch inside one pill.  No CSV button (the
        Statements exports were retired at Slice 4).
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "analytics-basis-chip" in html
            assert ">confirmed<" in html
            assert "Statements" in html
            assert "/analytics/balance-sheet" in html
            assert "Balance Sheet" in html
            # CSV export retired: no download button, no format=csv link.
            assert "format=csv" not in html
            assert "download" not in html

    def test_income_statement_empty_state(self, app, auth_client, seed_user,
                                          seed_periods):
        """A window with no posted income or expense shows the empty state.

        The seed Checking's $1000 opening is an Equity correction, so it
        never reaches the Income/Expense filter -- the current period is
        genuinely empty on the income statement.  S6/D8: the empty state
        names the window (the old "this window" wording predates the
        window-label caption's removal, P-AN14).
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period"
                f"&period_id={seed_periods[3].id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No income or expenses in" in resp.data

    def test_income_statement_no_current_period_falls_back(self, app, auth_client,
                                                           seed_user):
        """Outside every period, a bare pay_period window uses the most recent.

        The frozen today (2026-03-20) sits outside seed_user's only period
        (the 2024-01-05 bootstrap anchor period), so ``_resolve_window_params``
        falls back from "current period" to the user's most recent one.  The
        S6/D8 empty state names its window, which pins WHICH period the
        fallback chose -- the old "this window" wording only proved that
        something empty rendered, and hid that this test never reached the
        no-periods-at-all month fallback its docstring used to claim (every
        seeded account requires a bootstrap anchor period, so that branch is
        unreachable through fixtures).
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No income or expenses in Jan 05 - Jan 18, 2024" in resp.data

    def test_income_statement_pay_period_content(self, app, auth_client,
                                                 seed_user, seed_periods, db):
        """A settled income + expense in a period section and net out.

        Income 2000, expense 300 -> net income 1700.  Both settle through
        the real posting path so they land on the confirmed ledger the
        statement reads.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("2000.00"),
                is_income=True, name="Paycheck",
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("300.00"),
                is_income=False, name="Groceries",
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/income-statement?window=pay_period"
                f"&period_id={seed_periods[0].id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Income" in html
            assert "Expenses" in html
            assert "$2,000.00" in html
            assert "$300.00" in html
            # S6/D8: Net income is the band hero (sentence-case label,
            # like every other band hero), no longer a bottom-line card.
            assert "Net income" in html
            assert "$1,700.00" in html

    def test_income_statement_monthly_window(self, app, auth_client, seed_user,
                                             seed_periods):
        """Monthly window label contains the month name and year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=month&month=1&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"January" in resp.data
            assert b"2026" in resp.data

    def test_income_statement_annual_window(self, app, auth_client, seed_user,
                                            seed_periods):
        """Annual window label contains the year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2026" in resp.data

    def test_income_statement_out_of_range_month_clamped(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A hand-crafted out-of-range month/year clamps instead of 500ing.

        ``calendar_tab`` clamps its calendar fields; the income statement
        does the same so a crafted URL cannot reach ``monthrange()`` /
        ``date()`` in the service and raise.  month=13 clamps to 12
        (December), year=99999 clamps to 2100.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement?window=month&month=13&year=99999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"December" in resp.data
            assert b"2100" in resp.data



# ── The window comes from the DERIVATION (plan step C2-f3a) ───────


class TestTheWindowIsAnsweredByTheDerivation:
    """The Statements window resolves off ONE pay-calendar derivation.

    This render asked ``budget.pay_periods`` FOUR times before plan step
    C2-f3a, and every one of the four is a question the derivation answers:
    which period is current (``pay_period_service.get_current_period`` -- SQL
    whose ``.first()`` carried no ``ORDER BY``, ledger row **P19**), the whole
    list for the ``<select>``, the earliest period for the year list, and the
    chosen period's dates for the heading, that last one issued again inside
    ``ledger_report_service``.

    **Two of the three cases below FAIL on the merge base**, which is what
    makes them a regression gate rather than a pin: the retired reader read
    the STORED span and the PROCESS clock, and each case moves exactly one of
    those away from what the paydays say.  The third pins that the heading and
    the ``<option>`` the reader picked it from cannot drift apart, which is
    ledger row **P47**'s duplicate half made a predicate.

    ``seed_periods`` is ten biweekly periods from 2026-01-02, and this module
    freezes today to 2026-03-20 -- inside period 5, which runs 2026-03-13
    through 2026-03-26 because period 6's payday is 2026-03-27.
    """

    @staticmethod
    def _selected_option(html):
        """Return ``(value, label)`` of the window ``<select>``'s selected option."""
        match = re.search(
            r'<option value="(\d+)" selected>\s*(.*?)\s*</option>', html,
        )
        assert match is not None, "no selected period option in the rendered window"
        return int(match.group(1)), unescape(match.group(2))

    # **``test_a_doctored_stored_end_date_does_not_move_the_default_window`` was
    # deleted at plan step ``pay_calendar:C4-c``.**  It rewrote period 5's stored
    # ``end_date`` to the day before the frozen today and asserted the statement
    # page still defaulted to period 5 -- the derivation's answer -- where the
    # retired SQL matched ``start_date <= today <= end_date`` against the
    # doctored column, found no period covering today, and fell through to the
    # last one, showing a paycheck three months out with no sign anything was
    # wrong.
    #
    # **It planted a stored ``budget.pay_periods.end_date`` that disagreed with
    # the owner's paydays, and plan step ``pay_calendar:C4-c`` dropped that
    # column.**  The plant is not merely unreachable, it is SILENT: assigning to
    # an attribute the model no longer maps sets a plain Python attribute, writes
    # no UPDATE, and survives ``expire_all`` -- so the case and its own premise
    # assertion both went on passing while measuring nothing.  Deleted rather
    # than left green.
    #
    # The two cases that remain in this class grade what outlives the column:
    # that the default window follows the OWNER's civil day rather than the
    # container's clock, and that the selected option and the report heading
    # render one label rule.

    def test_the_default_window_follows_the_OWNERS_day_not_the_containers(
        self, app, db, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """Midnight UTC on a payday is the PREVIOUS paycheck for the owner.

        Ledger row **P49**: none of the retired reader's call sites passed an
        ``as_of``, so every "which paycheck am I in" answer was the container's
        civil day.  Frozen at midnight UTC on 2026-03-27 -- which is 8pm on
        2026-03-26 in ``America/New_York`` -- the two clocks name DIFFERENT
        paychecks, because 03-27 is period 6's payday and 03-26 is period 5's
        last covered day.

        The owner is still in period 5, so that is what the page must default
        to.  On the merge base ``date.today()`` answered 2026-03-27 and the
        page opened on period 6.  In the deployed container the two agree --
        both compose files pin ``TZ: America/New_York`` (the 2026-06-12 parity
        audit's finding M01) -- so this grades the code rather than the
        deployment, which is the whole point of the row.
        """
        freeze_today(monkeypatch, date(2026, 3, 27), at_time=time.min)
        # RE-LOGIN under the moved clock.  ``auth_client`` authenticated at the
        # module freeze (2026-03-20 noon UTC) and the session's idle bound is
        # measured against the same ``datetime.now`` this freeze moves, so a
        # seven-day jump logs that session out and the route answers 302.  The
        # 302 is the session's, not the window's, and it would read exactly
        # like this case failing.
        assert auth_client.post("/login", data={
            "email": "test@shekel.local", "password": "testpass",
        }).status_code == 302
        with app.app_context():
            assert date.today() == date(2026, 3, 27), (
                "the freeze did not move the PROCESS clock, so this case "
                "cannot tell the two apart"
            )
            assert display_today() == date(2026, 3, 26), (
                "the freeze did not put the two clocks on different civil "
                "days, so this case cannot tell them apart"
            )
            expected_id = seed_periods[5].id
            other_id = seed_periods[6].id

            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        value, _label = self._selected_option(resp.data.decode())
        assert value != other_id, (
            "the page opened on the paycheck the CONTAINER's clock is in"
        )
        assert value == expected_id

    def test_the_option_and_the_heading_are_one_label_rule(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The selected ``<option>`` and the report heading render one string.

        They were two: an inline Jinja expression here and
        ``ledger_report_service._income_statement._window_label``, both
        producing ``"Feb 21 - Mar 06, 2026"`` from separate code.  Ledger row
        **P47** measured six spellings of a period's range and named these two
        as the same register written twice; plan step C2-f3a put both on
        ``spending_analysis.window_label``, which the Spending report's third
        copy also calls now.

        This is a PIN rather than a regression catcher -- the two agreed on the
        merge base too -- and it is worth pinning because they sit on one
        screen: the heading is what the ``<option>`` swaps in.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/income-statement",
                headers={"HX-Request": "true"},
            )

        assert resp.status_code == 200
        html = resp.data.decode()
        _value, label = self._selected_option(html)
        # The heading renders in ONE of two places: the hero caption when the
        # window has posted rows, and the empty state's sentence when it has
        # none.  This fixture's window is genuinely empty -- the seed
        # Checking's opening is an Equity correction and never reaches the
        # Income/Expense filter -- so the empty state is the one that renders,
        # and it carries the same ``report.window_label``.  Both are matched so
        # the case does not silently stop grading if the fixture gains a row.
        heading = re.search(
            r'<div class="nw-hero__cap">(.*?) &middot;', html,
        ) or re.search(r"No income or expenses in (.*?)\.", html)
        assert heading is not None, (
            "neither the statement hero caption nor its empty state rendered, "
            "so there is no heading to compare the option against"
        )
        assert unescape(heading.group(1)) == label


# ── Balance Sheet Tab Tests ───────────────────────────────────────


class TestBalanceSheetTab:
    """Tests for GET /analytics/balance-sheet HTMX partial endpoint.

    Build-Order Step 5: the confirmed-ledger balance sheet.  The seed
    Checking's $1000 opening is dated by its origination ``created_at``
    (the real DB clock), which the module's frozen 2026-03-20 ``today``
    predates, so the default as-of does NOT fold it.  Content is therefore
    exercised with a settled transaction whose settle day is pinned inside
    the frozen range; the opening is excluded but as a WHOLE entry, so the
    tie-out still closes.  A far-future ``today`` refreeze is avoided
    deliberately: Flask-Login would treat the real-clock session as
    idle-expired and redirect the request to login.
    """

    def test_balance_sheet_requires_auth(self, app, client):
        """Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/analytics/balance-sheet")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]

    def test_balance_sheet_no_htmx_renders_shell(self, app, auth_client, seed_user):
        """Non-HTMX request renders the shell with Statements active (D13).

        Balance Sheet is the Statements pill's second half (reached by its
        internal toggle), so a direct GET lands on the Statements tab, which
        auto-loads its income-statement default.
        """
        with app.app_context():
            resp = auth_client.get("/analytics/balance-sheet")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/income-statement"

    def test_balance_sheet_htmx_renders(self, app, auth_client, seed_user):
        """HTMX request renders the balance sheet heading and as-of input."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Balance Sheet" in html
            assert 'type="date"' in html
            assert 'name="as_of"' in html

    def test_balance_sheet_shows_statements_toggle(self, app, auth_client,
                                                   seed_user):
        """The shared Statements header renders with the confirmed basis chip
        and the Income Statement toggle target; no CSV button (retired)."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "analytics-basis-chip" in html
            assert ">confirmed<" in html
            assert "/analytics/income-statement" in html
            assert "format=csv" not in html
            assert "download" not in html

    def test_balance_sheet_format_csv_no_longer_exports(
        self, app, auth_client, seed_user,
    ):
        """The retired ?format=csv no longer returns a CSV body.

        The balance-sheet CSV export was removed at Slice 4; a stale
        ?format=csv now falls through to the normal render (an HTMX
        partial), never a text/csv attachment.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?format=csv",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "text/csv" not in resp.headers.get("Content-Type", "")

    def test_balance_sheet_shows_posted_content_and_ties_out(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """A settled income posts to the sheet and the tie-out stays green.

        A $500 income settled on a day inside the frozen range
        (2026-02-15) folds into the default as-of (2026-03-20): Checking
        +500 (Asset) and Retained Earnings +500 (Income closed into
        equity).  The seed opening's journal entry_date is the real-clock
        origination ``created_at`` (after the frozen today), so it is
        excluded -- but as a WHOLE entry (both its Asset and Equity legs),
        so the tie-out still closes.  A far-future refreeze is avoided
        deliberately: it would make Flask-Login treat the real-clock
        session as idle-expired.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("500.00"),
                is_income=True, name="Paycheck",
                settled_on=date(2026, 2, 15),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/balance-sheet",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Assets" in html
            assert "Liabilities" in html
            assert "Equity" in html
            assert "Checking" in html
            assert "Retained Earnings" in html
            assert "$500.00" in html
            assert "In balance" in html

    def test_balance_sheet_before_opening_is_empty(self, app, auth_client,
                                                   seed_user):
        """An as-of before any posted source shows the empty state.

        The seed opening is dated no earlier than the account's
        origination, so an as-of of 2001-01-01 folds nothing.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?as_of=2001-01-01",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"No posted balances" in resp.data

    def test_balance_sheet_future_as_of_clamped(self, app, auth_client,
                                                seed_user):
        """A future as-of clamps to today (the date input shows today)."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?as_of=2099-12-31",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            # Frozen today is 2026-03-20; the future as-of clamps to it.
            assert 'value="2026-03-20"' in resp.data.decode()

    def test_balance_sheet_garbage_as_of_defaults_today(self, app, auth_client,
                                                        seed_user):
        """A non-ISO as-of degrades to today rather than raising."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/balance-sheet?as_of=not-a-date",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert 'value="2026-03-20"' in resp.data.decode()


# ── CSV Export Tests ──────────────────────────────────────────────


class TestCsvExportRetired:
    """The calendar CSV export was removed root-and-branch (P-AN4).

    The calendar month/year CSV was the last surviving analytics export (the
    Slice-4 shell collapse had already retired the year-end, variance, trends,
    income-statement, and balance-sheet exports).  With P-AN4 no analytics
    export remains: neither calendar view offers a CSV button, and a stale
    ``?format=csv`` is now an inert query arg that renders the normal calendar
    rather than a download.
    """

    def test_calendar_month_has_no_csv_button(self, app, auth_client, seed_user,
                                              seed_periods):
        """The calendar month view offers no CSV download control."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "format=csv" not in html
            assert "bi-download" not in html

    def test_calendar_year_has_no_csv_button(self, app, auth_client, seed_user,
                                             seed_periods):
        """The calendar year view offers no CSV download control."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "format=csv" not in html
            assert "bi-download" not in html

    def test_format_csv_is_inert_on_htmx_request(self, app, auth_client,
                                                 seed_user, seed_periods):
        """A stale ?format=csv on an HTMX calendar GET renders the grid.

        The format arg has no branch anymore, so it is ignored: the request
        returns the normal calendar partial, never a text/csv attachment.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data
            assert "text/csv" not in resp.headers.get("Content-Type", "")

    def test_format_csv_non_htmx_renders_shell_not_csv(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stale ?format=csv on a direct (non-HTMX) GET serves the shell.

        With the CSV branch gone, format=csv no longer bypasses the HTMX
        guard; a non-HTMX hit renders the analytics shell (Calendar active,
        D13), never a CSV attachment.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?format=csv&view=month&year=2026&month=1",
            )
            assert resp.status_code == 200
            assert "text/csv" not in resp.headers.get("Content-Type", "")
            assert "shekel-scroll-pills" in resp.data.decode()


# ── Retired Tab Redirect Tests ────────────────────────────────────


class TestRetiredTabRedirects:
    """The retired Variance / Trends / Year-End URLs redirect to /analytics.

    Slice-4 shell collapse: these three tabs were folded into Spending and
    Taxes, so their routes are now a single redirect stub.  Old bookmarks and
    the former ?format=csv exports land on the current page instead of 404ing.
    """

    def test_retired_urls_redirect_to_page(self, app, auth_client, seed_user):
        """Each retired URL 302s to /analytics for an authenticated user."""
        with app.app_context():
            for url in ("/analytics/variance", "/analytics/trends",
                        "/analytics/year-end"):
                resp = auth_client.get(url)
                assert resp.status_code == 302, f"{url} did not redirect"
                assert resp.headers["Location"].endswith("/analytics"), (
                    f"{url} did not redirect to /analytics"
                )

    def test_retired_csv_urls_also_redirect(self, app, auth_client, seed_user):
        """The former ?format=csv exports redirect too (no CSV body)."""
        with app.app_context():
            resp = auth_client.get("/analytics/year-end?format=csv&year=2026")
            assert resp.status_code == 302
            assert "text/csv" not in resp.headers.get("Content-Type", "")
            assert resp.headers["Location"].endswith("/analytics")

    def test_retired_urls_still_require_auth(self, app, client):
        """The redirect stub is behind login_required: unauth -> /login."""
        with app.app_context():
            resp = client.get("/analytics/variance")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Nav Bar Tests ─────────────────────────────────────────────────


class TestAnalyticsNav:
    """Tests for nav bar updates after Charts-to-Analytics rename."""

    def test_nav_shows_analytics_link(self, app, auth_client, seed_user):
        """Authenticated pages show Analytics link in the nav bar."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            assert b"Analytics" in resp.data
            assert b'href="/analytics"' in resp.data

    def test_nav_does_not_show_charts_link(self, app, auth_client, seed_user):
        """Nav bar no longer shows a link pointing to /charts."""
        with app.app_context():
            resp = auth_client.get("/")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'href="/charts"' not in html

    def test_charts_route_redirects(self, app, auth_client, seed_user):
        """GET /charts returns 301 redirect to /analytics."""
        with app.app_context():
            resp = auth_client.get("/charts")
            assert resp.status_code == 301
            assert "/analytics" in resp.headers["Location"]

    def test_analytics_active_nav_state(self, app, auth_client, seed_user):
        """GET /analytics shows the Analytics nav item as active."""
        with app.app_context():
            resp = auth_client.get("/analytics")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The nav link for /analytics should have the active class.
            assert 'class="nav-link active" href="/analytics"' in html


# ── Calendar Month View Tests ────────────────────────────────────────


class TestCalendarMonthView:
    """Tests for the calendar month detail view."""

    def test_calendar_month_renders(self, app, auth_client, seed_user, seed_periods):
        """Month view renders with current month name."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_navigation(self, app, auth_client, seed_user, seed_periods):
        """Month view for specific month/year contains the correct heading."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=3",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"March" in resp.data
            assert b"2026" in resp.data

    def test_calendar_month_has_day_cells(self, app, auth_client, seed_user, seed_periods):
        """Month view contains calendar-day elements."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day" in resp.data

    def test_calendar_paycheck_highlighting(self, app, auth_client, seed_user, seed_periods):
        """Paycheck days have the calendar-paycheck CSS class."""
        with app.app_context():
            # Request a month with known paycheck days (Jan 2026 has
            # periods starting Jan 2, Jan 16, Jan 30).
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-paycheck" in resp.data

    def test_calendar_month_empty(self, app, auth_client, seed_user, seed_periods):
        """Month with no transactions renders without crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=4",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_month_prev_next(self, app, auth_client, seed_user, seed_periods):
        """Month view has prev/next navigation buttons."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=6",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "month=5" in html  # prev button
            assert "month=7" in html  # next button

    def test_calendar_month_december_next_wraps(self, app, auth_client, seed_user, seed_periods):
        """December next button wraps to January of next year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=12",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2027" in html
            assert "month=1" in html

    def test_calendar_month_january_prev_wraps(self, app, auth_client, seed_user, seed_periods):
        """January prev button wraps to December of prior year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "year=2025" in html
            assert "month=12" in html

    def test_calendar_month_year_overview_button(self, app, auth_client, seed_user, seed_periods):
        """Month view has a button to switch to year overview."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=year" in html

    def test_calendar_month_summary_strip_displayed(self, app, auth_client, seed_user, seed_periods, db):
        """Month view shows the cockpit band per the S5 recomposition (P-AN3).

        Redesign pin (analytics_audit.md Gate A locked the summary figures
        2026-07-04; ui_ux_polish_audit.md P-AN3 recomposed them as a cockpit
        in S5): one .nw-sky band carries a balance hero plus the summary
        chips.  January 2026 is wholly past relative to any display-tz today
        after 2026-01-31, so the hero is Month end (Balance today only
        renders inside the current month), the So far chip renders
        (captioned "full month"), and the future-only Remaining chip does
        not.
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            one_off_row_of(
                seed_periods[0],
                name="Test Income",
                amount=Decimal("3000.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "nw-sky" in html
            assert "So far" in html
            assert "Income" in html
            assert "Expenses" in html
            assert "Month end" in html
            assert "Month trough" in html
            # Wholly past month: the projected-remainder chip is empty by
            # construction and hidden.
            assert "Remaining" not in html
            # So far pair: the seeded $3,000 income is the month's only flow.
            # elapsed_income = 3000.00, elapsed_expense = 0.00.
            assert "$3,000.00" in html
            assert "$0.00" in html

    def test_calendar_default_view_is_month(self, app, auth_client, seed_user, seed_periods):
        """No view param defaults to month view."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-grid" in resp.data

    def test_calendar_invalid_month_handled(self, app, auth_client, seed_user, seed_periods):
        """Invalid month=13 clamped to valid range, no crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&month=13",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_calendar_today_highlighted(self, app, auth_client, seed_user, seed_periods):
        """Current month view contains today indicator class."""
        with app.app_context():
            from datetime import datetime, timezone
            from app.utils.dates import to_display_date
            # The route marks today in the display timezone, so the test must
            # ask for the display-tz month (not the server's UTC day) or it
            # flakes at the midnight-UTC month boundary.
            today = to_display_date(datetime.now(timezone.utc))
            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={today.year}&month={today.month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"calendar-day--today" in resp.data

    def test_calendar_notation_legend(self, app, auth_client, seed_user, seed_periods):
        """Month view names its notation glyphs on screen (P-AN8).

        The PAY / asterisk / tilde / check glyphs previously relied on a
        cell tooltip for the asterisk's meaning, unreachable on touch; the
        legend under the grid states all four.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "calendar-legend" in html
            assert "payday" in html
            assert "includes an infrequent item" in html
            assert "projected (after today)" in html


# ── Calendar Year View Tests ─────────────────────────────────────────


class TestCalendarYearView:
    """Tests for the calendar year overview."""

    def test_calendar_year_renders(self, app, auth_client, seed_user, seed_periods):
        """Year view renders with all 12 month names."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            for month_name in [b"January", b"February", b"March", b"April",
                               b"May", b"June", b"July", b"August",
                               b"September", b"October", b"November", b"December"]:
                assert month_name in resp.data

    def test_calendar_third_paycheck_badge(self, app, auth_client, seed_user, db):
        """Year with 26 periods shows '3rd check' badge."""
        with app.app_context():
            from datetime import date
            # The BINDING went with the ``current_anchor_period_id`` line it
            # fed (ruling R-EH); the CALL is fixture setup and stays -- these
            # 26 periods ARE the third-paycheck year under test.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"3rd check" in resp.data

    def test_calendar_year_navigation(self, app, auth_client, seed_user, seed_periods):
        """Year view navigation shows correct year."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2025",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"2025" in resp.data

    def test_calendar_year_month_click_links(self, app, auth_client, seed_user, seed_periods):
        """Month cards contain hx-get with view=month params."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert "view=month" in html

    def test_calendar_year_annual_totals(self, app, auth_client, seed_user, seed_periods):
        """Year view shows annual total labels."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=year&year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert b"Annual Income" in resp.data
            assert b"Annual Expenses" in resp.data
            assert b"Annual Net" in resp.data


# ── Calendar Inline Totals and Day Detail Tests ───────────────────────


class TestCalendarInlineTotals:
    """Tests for inline day totals and day detail section."""

    def test_calendar_day_totals_rendered(self, app, auth_client, seed_user, seed_periods, db):
        """Day with transactions shows inline income/expense totals."""
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            txn_inc = one_off_row_of(
                seed_periods[0],
                name="Test Paycheck",
                amount=Decimal("2500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                due_date=date(2026, 1, 5),
            )
            txn_exp = one_off_row_of(
                seed_periods[0],
                name="Test Rent",
                amount=Decimal("1200.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                due_date=date(2026, 1, 5),
            )
            db.session.add_all([txn_inc, txn_exp])
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert "calendar-day-income" in html
            assert "calendar-day-expense" in html

    def test_calendar_day_detail_template(self, app, auth_client, seed_user, seed_periods, db):
        """Day with entries has a template element containing the transaction name."""
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            one_off_row_of(
                seed_periods[0],
                name="Electric Bill Detail",
                amount=Decimal("150.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'data-detail-day="10"' in html
            assert "Electric Bill Detail" in html

    def test_calendar_no_popover_attributes(self, app, auth_client, seed_user, seed_periods, db):
        """Calendar month view does not contain Bootstrap popover attributes."""
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            one_off_row_of(
                seed_periods[0],
                name="Popover Check",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                due_date=date(2026, 1, 15),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'data-bs-toggle="popover"' not in html

    def test_calendar_day_click_attributes(self, app, auth_client, seed_user, seed_periods, db):
        """Day with entries has data-day and role=button attributes."""
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            # Period 1 (Jan 16-29) is the period whose span contains the
            # Jan 20 due date, so the clamped attribution lands on the 20th.
            one_off_row_of(
                seed_periods[1],
                name="Click Test",
                amount=Decimal("200.00"),
                user_id=seed_periods[1].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                due_date=date(2026, 1, 20),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            html = resp.data.decode()
            assert resp.status_code == 200
            assert 'data-day="20"' in html
            assert 'role="button"' in html


# ── Calendar Flow Strip and Day-Cell Hero Tests (slice-1 rebuild) ─────


def _extract_flow_strip_payload(page_html):
    """Parse the flow strip canvas's ``data-chart`` JSON out of a month view.

    The attribute value is HTML-escaped by Jinja autoescaping (quotes as
    ``&#34;``), so it is unescaped before ``json.loads`` -- the same
    decode path the browser's ``getAttribute`` performs.
    """
    import html as html_mod
    import json
    import re

    match = re.search(r"data-chart='([^']*)'", page_html)
    assert match is not None, "flow strip data-chart attribute missing"
    return json.loads(html_mod.unescape(match.group(1)))


class TestCalendarFlowStrip:
    """Pins for the month flow strip payload and the day-cell balance hero.

    Slice-1 rebuild behavior (analytics_audit.md, "Calendar rebuild
    decisions" + "Loop B P1 as-built"): the strip serializes the daily
    running-balance series with measured/projected split indices, the
    user's low-balance threshold, payday dots, and the labeled trough;
    day cells carry the projected end-of-day balance hero with the
    modeled-tilde and below-threshold danger treatments.
    """

    def test_flow_strip_payload_shape(self, app, auth_client, seed_user, seed_periods):
        """January 2026 with no transactions serializes a flat $1,000 series.

        Hand-computed: the seeded account anchors at $1,000.00 with no
        transactions, so every end-of-day balance is 1000.0.  January 2026
        is wholly past (display-tz today is after 2026-01-31), so
        current_index equals the day count (all measured).  Paydays are the
        seeded period starts Jan 2 / 16 / 30 (0-based indices 1, 15, 29).
        Weekly ticks are the 1st plus every Sunday: 2026-01-01 is a
        Thursday, so Sundays fall on Jan 4 / 11 / 18 / 25 (indices 3, 10,
        17, 24).  The default low-balance threshold is 500.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'id="calendar-flow-canvas"' in html

            payload = _extract_flow_strip_payload(html)
            assert len(payload["values"]) == 31
            assert all(value == 1000.0 for value in payload["values"])
            assert payload["current_index"] == 31
            assert payload["threshold"] == 500.0
            assert payload["payday_indices"] == [1, 15, 29]
            # Flat series: the trough is the earliest minimum, day 1.
            assert payload["trough_index"] == 0
            # $1,000.00 >= the $500 threshold: a healthy trough (D11,
            # P-AN6) -- "ok" state, and no state ink anywhere on the
            # board (being the trough alone no longer colors a figure).
            assert payload["trough_state"] == "ok"
            assert "calendar-day-balance--danger" not in html
            assert "calendar-day-balance--low" not in html
            assert "pulse-chip--danger" not in html
            assert "pulse-chip--warning" not in html
            assert payload["week_tick_indices"] == [0, 3, 10, 17, 24]
            assert len(payload["labels"]) == 31
            assert payload["labels"][0] == "Jan 1"

    def test_flow_strip_low_trough_warning_cells(self, app, auth_client, seed_user, seed_periods, db):
        """A low-but-positive trough renders the WARNING chip and cells.

        Hand-computed: anchor $1,000.00; one $600.00 expense SETTLED on Jan 5
        (inside period Jan 2-15).  End-of-day balances: Jan 1-4 = $1,000.00;
        Jan 5-31 = 1000 - 600 = $400.00.  The trough is Jan 5 (0-based index 4)
        at $400.00 -- below the default $500 threshold but positive, so under
        the D11 ruling (the calendar adopts the grid's thresholds: danger only
        for a NEGATIVE balance, the caution token for low-but-positive) the
        Month trough chip takes the warning variant, the below-threshold day
        cells take the low hero class, and neither danger class renders.
        January 2026 is wholly past, so no modeled tilde renders anywhere.

        **The row is SETTLED rather than projected, and that is the shape a
        past month has** (plan step X-c2b2).  A settled row moves the line from
        the day its money moved, which is finding cash D1 closed; a row still
        marked Projected in a past month has not happened, so ruling R-G lands
        it at ``as_of + 1`` and January stays flat -- the honest answer for a
        bill nobody paid, and one that pins no trough.
        """
        with app.app_context():
            from datetime import date
            from decimal import Decimal
            from tests._test_helpers import create_settled_cash_transaction

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("600.00"),
                name="Trough Expense",
                settled_on=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["trough_index"] == 4
            assert payload["trough_state"] == "low"
            assert payload["values"][3] == 1000.0
            assert payload["values"][4] == 400.0
            assert payload["values"][30] == 400.0

            assert "Month trough" in html
            assert "$400.00" in html
            assert "pulse-chip--warning" in html
            assert "calendar-day-balance--low" in html
            assert "pulse-chip--danger" not in html
            assert "calendar-day-balance--danger" not in html
            # Wholly past month: measured treatment only, no modeled tilde.
            assert "~$" not in html

    def test_flow_strip_negative_trough_danger_cells(self, app, auth_client, seed_user, seed_periods, db):
        """A negative trough renders the DANGER chip and cells.

        Hand-computed: anchor $1,000.00; one $1,600.00 expense SETTLED on
        Jan 5 (inside period Jan 2-15).  End-of-day balances: Jan 1-4
        = $1,000.00; Jan 5-31 = 1000 - 1600 = -$600.00.  The trough is
        Jan 5 (0-based index 4) at -$600.00 -- a true negative money
        state, so the Month trough chip takes the danger variant and the
        negative day cells take the danger hero class (D11: danger is
        reserved for negative).  Jan 1-4 stay healthy at $1,000.00, so
        the low/warning classes do not render.

        Settled rather than projected, for the reason the sibling test above
        documents: a past month's line moves on what actually happened.
        """
        with app.app_context():
            from datetime import date
            from decimal import Decimal
            from tests._test_helpers import create_settled_cash_transaction

            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("1600.00"),
                name="Overdraft Expense",
                settled_on=date(2026, 1, 5),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["trough_index"] == 4
            assert payload["trough_state"] == "negative"
            assert payload["values"][4] == -600.0

            assert "pulse-chip--danger" in html
            assert "calendar-day-balance--danger" in html
            assert "pulse-chip--warning" not in html
            assert "calendar-day-balance--low" not in html

    def test_future_month_modeled_treatment(self, app, auth_client, seed_user, seed_periods):
        """A wholly future month renders all-modeled heroes and no measured chips.

        Uses the display-timezone today (the route's split basis) to pick a
        month two months ahead, so the assert never straddles the boundary.
        With no transactions the series is flat at the $1,000.00 anchor:
        every day is after today, so every balance hero carries the modeled
        tilde + secondary-ink class, current_index is 0 (all projected),
        and the elapsed-side chips (Balance today / So far) do not render
        while Remaining does.
        """
        with app.app_context():
            from datetime import datetime, timezone
            from app.utils.dates import to_display_date

            today = to_display_date(datetime.now(timezone.utc))
            year = today.year + (1 if today.month >= 11 else 0)
            month = today.month - 10 if today.month >= 11 else today.month + 2

            resp = auth_client.get(
                f"/analytics/calendar?view=month&year={year}&month={month}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            payload = _extract_flow_strip_payload(html)
            assert payload["current_index"] == 0

            assert "calendar-day-balance--modeled" in html
            assert "~$1,000" in html
            assert "Balance today" not in html
            assert "So far" not in html
            assert "Remaining" in html

    def test_day_cell_flow_lines_cap_and_overflow(self, app, auth_client, seed_user, seed_periods, db):
        """A five-flow day shows three named lines plus the +N more residual.

        Hand-computed: on Jan 5, one $3,000.00 income and four expenses
        ($1,200 / $300 / $100 / $50).  Cell order is income first then
        expenses by descending magnitude, capped at three named lines
        (Salary Chunk, Rent Big, Utility Mid), so exactly three
        calendar-day-flow lines render.  The hidden tail is Snack Small
        ($100) + Tiny Fee ($50): count 2, residual net -(100 + 50) =
        -$150, rendered whole-dollar as "-$150".
        """
        with app.app_context():
            from app import ref_cache
            from app.enums import TxnTypeEnum
            from datetime import date
            from decimal import Decimal

            flows = [
                ("Salary Chunk", TxnTypeEnum.INCOME, Decimal("3000.00")),
                ("Rent Big", TxnTypeEnum.EXPENSE, Decimal("1200.00")),
                ("Utility Mid", TxnTypeEnum.EXPENSE, Decimal("300.00")),
                ("Snack Small", TxnTypeEnum.EXPENSE, Decimal("100.00")),
                ("Tiny Fee", TxnTypeEnum.EXPENSE, Decimal("50.00")),
            ]
            for name, txn_type, amount in flows:
                one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=amount,
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=ref_cache.txn_type_id(txn_type),
                    due_date=date(2026, 1, 5),
                )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Exactly three named flow lines on the one populated day.
            assert html.count('class="calendar-day-flow"') == 3
            assert "+2 more" in html
            assert "-$150" in html

    def test_paycheck_day_pay_tag(self, app, auth_client, seed_user, seed_periods):
        """Payday cells carry the PAY tag marker alongside the period tint."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?view=month&year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "calendar-pay-tag" in html
            assert ">PAY</span>" in html


# ── Taxes Tab Tests (slice-2 rebuild, T-P4) ──────────────────────────


def _seed_taxes_profile(seed_user, db):
    """Seed a 130k single/NC salary profile; it prices under the shipped law.

    The T-P4 route-test fixture: 130,000 / 26 = 5,000.00 gross per period
    exactly (no rounding residue), no deductions, no pay stub -- so every
    figure asserted below is hand-computable from the 2026 law.
    """
    from app.extensions import db as _db
    from app.models.ref import FilingStatus
    from app.models.salary_profile import SalaryProfile

    filing_status = (
        _db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name="Taxes Tab Profile",
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("5000.00"))  # $130,000.00 a year / 26
    db.session.commit()
    return profile


class TestTaxesTab:
    """Pins for the Taxes tab (T-P4): hero, chips, ledger, W-2, Schedule A.

    Hand-computed baseline (130k single/NC profile, seed_periods = 10
    paydays in 2026, no checkpoint, no pay stub, no deductions):

      per-period federal = round(19,934 / 26) = 766.69 -> x10 = 7,666.90
      per-period NC      = round(4,678.28 / 26) = 179.93 -> x10 = 1,799.30
      liability on hybrid gross 50,000:
        federal taxable 33,900 -> 1,240 + 21,500 x 0.12 = 3,820.00
        NC (50,000 - 12,750) x 0.0399 = 1,486.2750 -> 1,486.28
      federal refund 7,666.90 - 3,820.00 = 3,846.90
      NC refund      1,799.30 - 1,486.28 =   313.02
      total refund                       = 4,159.92
      effective (3,820 + 1,486.28) / 50,000 = 0.106126 -> 10.61%
      marginal: 33,900 sits in the 12,400-50,400 band -> 12.00%
    """

    def test_taxes_tab_hand_pinned_figures(self, app, auth_client, seed_user, seed_periods, db):
        """The full-modeled baseline renders every hand-computed figure."""
        with app.app_context():
            _seed_taxes_profile(seed_user, db)
            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Estimated refund" in html
            assert "$4,159.92" in html   # hero + ledger total
            assert "$3,846.90" in html   # federal refund chip + ledger
            assert "$313.02" in html     # NC refund chip + ledger
            assert "10.61%" in html      # effective rate chip
            assert "12.00%" in html      # marginal rate chip
            assert "modeled estimate" in html
            assert "withholding fully modeled" in html

            # Derivation ledger + W-2 tie-out: Box 2 == federal withheld.
            assert "How the estimate is derived" in html
            assert "$7,666.90" in html
            assert "$3,820.00" in html   # federal tax line
            assert "$1,486.28" in html   # NC tax line
            assert "W-2 preview" in html
            assert "$50,000.00" in html  # Box 1 / Box 3 / Box 5 wages
            assert "$1,799.30" in html   # Box 17 == NC withheld

            # Schedule A: no loans seeded -> itemized = state tax only,
            # standard deduction 16,100 wins by 16,100 - 1,799.30 = 14,300.70.
            assert "Schedule A check" in html
            assert "standard deduction wins by" in html
            assert "$14,300.70" in html

            # The YTD checkpoint card and assumptions card are present.
            assert "ytd-checkpoint-card" in html
            assert "Assumptions" in html
            # No pay stub seeded: the tax formulas priced every estimated
            # paycheck (R-SAL100, words R-SAL121; "bracket model" until plan
            # step salary:S11-c-2c deleted the calibration it stood against).
            text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
            assert "Withholding basis the tax formulas Measured through" in text

    def test_taxes_tab_names_the_formulas_then_each_pricing_stub(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The Withholding basis row lists what priced the estimated paychecks.

        Plan step salary:S11-c-2c, ruling R-SAL100 (its words R-SAL121).  Two
        made-up stubs on the fourth and seventh paydays and no checkpoint: the
        first three paydays take the tax formulas, the rest one stub or the
        other, so the row reads "the tax formulas, then your <4th> and <7th>
        pay stubs" -- the formulas first, the stubs in payday order.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        from app.enums import WithholdingKindEnum  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import add_test_pay_stub  # pylint: disable=import-outside-toplevel

        with app.app_context():
            profile = _seed_taxes_profile(seed_user, db)
            for period in (seed_periods[6], seed_periods[3]):
                add_test_pay_stub(
                    profile, period.start_date, "5000.00",
                    taxes={
                        WithholdingKindEnum.FEDERAL_INCOME: "600.00",
                        WithholdingKindEnum.STATE_INCOME: "180.00",
                        WithholdingKindEnum.SOCIAL_SECURITY: "290.00",
                        WithholdingKindEnum.MEDICARE: "70.00",
                    },
                )
            db.session.commit()
            first, second = (
                seed_periods[i].start_date.strftime("%b %-d, %Y") for i in (3, 6)
            )

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.data.decode()).split())

            assert resp.status_code == 200
            assert (
                f"Withholding basis the tax formulas, then your {first} and "
                f"{second} pay stubs Measured through"
            ) in text

    def test_taxes_tab_names_each_job_when_two_are_active(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """With two active jobs each line of the row starts with its job (R-BAL207).

        Plan step salary:S11-c-2c (the leaf review's MED-1): each job's
        paychecks are priced from its own stubs, so the row is one line per
        job, named only when more than one is active.  A made-up stub on the
        fourth payday of the first job; the second job has none.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        from app.enums import WithholdingKindEnum  # pylint: disable=import-outside-toplevel
        from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import add_test_pay_stub  # pylint: disable=import-outside-toplevel

        with app.app_context():
            first = _seed_taxes_profile(seed_user, db)
            second = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id, scenario_id=seed_user["scenario"].id,
                name="Second Job", filing_status_id=first.filing_status_id,
                state_code="NC", is_active=True, sort_order=1,
            )
            db.session.add(second)
            start_test_pay_list(second, Decimal("1000.00"))
            add_test_pay_stub(
                first, seed_periods[3].start_date, "5000.00",
                taxes={
                    WithholdingKindEnum.FEDERAL_INCOME: "600.00",
                    WithholdingKindEnum.STATE_INCOME: "180.00",
                    WithholdingKindEnum.SOCIAL_SECURITY: "290.00",
                    WithholdingKindEnum.MEDICARE: "70.00",
                },
            )
            db.session.commit()
            day = seed_periods[3].start_date.strftime("%b %-d, %Y")

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.data.decode()).split())

            assert resp.status_code == 200
            assert (
                f"Withholding basis Taxes Tab Profile: the tax formulas, then "
                f"your {day} pay stub Second Job: the tax formulas Measured through"
            ) in text

    def test_taxes_tab_says_nothing_is_left_to_estimate(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """A checkpoint covering every payday leaves no basis to name (R-SAL121).

        Plan step salary:S11-c-2c.  The checkpoint is dated 2026-12-31, after
        every seeded payday, so no paycheck is estimated and the row reads
        "nothing left to estimate".  Its figures are made up and none is read.
        """
        # Pylint: import-outside-toplevel -- file-wide test convention.
        from app.services import tax_withholding_service  # pylint: disable=import-outside-toplevel
        from app.services.tax_withholding_service import CheckpointFigures  # pylint: disable=import-outside-toplevel

        with app.app_context():
            profile = _seed_taxes_profile(seed_user, db)
            tax_withholding_service.save_checkpoint(
                profile.id,
                CheckpointFigures(
                    as_of_date=date(2026, 12, 31),
                    ytd_gross=Decimal("52341.18"),
                    ytd_federal=Decimal("4127.66"),
                    ytd_state=Decimal("1903.29"),
                    ytd_social_security=Decimal("3245.07"),
                    ytd_medicare=Decimal("761.14"),
                ),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            text = " ".join(re.sub(r"<[^>]+>", " ", resp.data.decode()).split())

            assert resp.status_code == 200
            assert "Withholding basis nothing left to estimate Measured through" in text

    def test_taxes_tab_checkpoint_reanchors_withholding(self, app, auth_client, seed_user, seed_periods, db):
        """A saved checkpoint re-anchors the measured side of the refund.

        Stub dated 2026-01-16 (the second payday) covers paydays 1-2;
        the remainder is the 8 later paydays.  Entered federal 1,600.00
        (vs 2 x 766.69 = 1,533.38 modeled), so:

          federal withheld = 1,600.00 + 8 x 766.69 = 7,733.52
          federal refund   = 7,733.52 - 3,820.00   = 3,913.52
        """
        with app.app_context():
            from datetime import date as date_cls
            from app.services import tax_withholding_service
            from app.services.tax_withholding_service import CheckpointFigures

            profile = _seed_taxes_profile(seed_user, db)
            tax_withholding_service.save_checkpoint(
                profile.id,
                CheckpointFigures(
                    as_of_date=date_cls(2026, 1, 16),
                    ytd_gross=Decimal("10000.00"),
                    ytd_federal=Decimal("1600.00"),
                    ytd_state=Decimal("360.00"),
                    ytd_social_security=Decimal("620.00"),
                    ytd_medicare=Decimal("145.00"),
                ),
            )
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$3,913.52" in html
            assert "measured through Jan 16" in html
            assert "Jan 16, 2026" in html   # assumptions "Measured through"

    def test_taxes_tab_actc_and_nc_child_deduction(self, app, auth_client, seed_user, seed_periods, db):
        """An MFJ 4-child household renders the ACTC row and NC child deduction.

        Hand-computed (130k MFJ, 4 qualifying children, seed_periods = 10
        paydays, no pre-tax, no checkpoint, 2026 seeds w/ OBBBA CTC 2,200 and
        ACTC cap 1,700):

          liability on hybrid gross 50,000:
            fed taxable 50,000 - 32,200 = 17,800 -> 10% band -> 1,780.00
            credits 4 x 2,200 = 8,800 -> liability clamps at 0
            unused 8,800 - 1,780 = 7,020
            ACTC = min(7,020, 4 x 1,700 = 6,800, 15% x 47,500 = 7,125)
                 = 6,800.00 (the CAP leg binds)
          fed withheld: annualized taxable 97,800 -> 11,240 - 8,800 = 2,440
            -> 93.85/period x 10 = 938.50
          fed refund = 938.50 - 0 + 6,800.00 = 7,738.50
          NC: base 50,000 -> tier 40-60k -> 2,500 x 4 = 10,000 child ded;
            taxable 50,000 - 25,500 - 10,000 = 14,500 x 0.0399 = 578.55
          NC withheld: (130,000 - 25,500) x 0.0399 = 4,169.55 -> 160.37 x 10
            = 1,603.70 -> NC refund 1,603.70 - 578.55 = 1,025.15
        """
        with app.app_context():
            from app.extensions import db as _db
            from app.models.ref import FilingStatus
            from app.models.salary_profile import SalaryProfile

            filing_status = (
                _db.session.query(FilingStatus)
                .filter_by(name="married_jointly").one()
            )
            profile = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                name="MFJ Four Kids",
                filing_status_id=filing_status.id,
                state_code="NC",
                is_active=True,
                qualifying_children=4,
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("5000.00"))  # $130,000.00 a year / 26
            db.session.commit()

            resp = auth_client.get(
                "/analytics/taxes?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Plus refundable child tax credit" in html
            assert "$6,800.00" in html          # the ACTC (cap leg binds)
            assert "$7,738.50" in html          # federal refund
            assert "$1,025.15" in html          # NC refund
            assert "$10,000 child deduction" in html
            assert "ACTC refundable cap" in html
            assert "$1,700 / child" in html
            assert "Refundable child tax credit (ACTC) modeled" in html
            assert "nonrefundable" not in html  # the stale caveat is gone

    def test_taxes_tab_empty_state_without_profile(self, app, auth_client, seed_user):
        """No active salary profile renders the empty state, not a crash."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/taxes",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No active salary profile" in html
            assert "Set up salary" in html

    def test_taxes_tab_non_htmx_renders_shell(self, app, auth_client, seed_user):
        """A non-HTMX GET renders the shell with Taxes active (D13)."""
        with app.app_context():
            resp = auth_client.get("/analytics/taxes")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/taxes"


def _settled_spending_txn(db, seed_user, period, name, category_key,
                          estimated, *, actual=None, due_date=None):
    """Create one settled (DONE) expense for the Spending route tests.

    Args:
        db: Database session fixture.
        seed_user: User fixture dict.
        period: The owning pay period.
        name: Transaction name.
        category_key: Key into ``seed_user['categories']`` (or ``None``).
        estimated: Estimated amount (string, Decimal-safe).
        actual: Entered actual (string) or ``None`` (settled-without-actual).
        due_date: Optional due date; ``None`` attributes by the period start.

    Returns:
        The flushed :class:`Transaction`.
    """
    cat = seed_user["categories"].get(category_key)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(estimated),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=cat.id if cat else None,
        due_date=due_date,
    )
    txn.status_id = ref_cache.status_id(StatusEnum.DONE)
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(due_date or period.start_date).items():
        setattr(txn, _column, _value)
    db.session.flush()
    # The record's home is the covering movement (X-bi-4b-1).
    cover_bare_settled_row(
        db.session, txn, Decimal(estimated),
        Decimal(actual) if actual is not None else None,
    )
    return txn


class TestSpendingTab:
    """Pins for the Spending tab (Slice 3, S-P2): hero, breakdown, rail.

    Today is frozen to 2026-03-20 by the module autouse fixture, so the
    ``seed_periods`` months (Jan-May 2026) are completed and the calendar-
    month windows below are deterministic.
    """

    def test_spending_tab_breakdown_and_hero(self, app, auth_client, seed_user,
                                             seed_periods, db):
        """A January window renders the spent hero, group shares, and scope.

        seed_periods[0] (starts 2026-01-02) carries Rent 1200 (Home),
        Groceries 500 (Family), Car Payment 300 (Auto): total 2000, shares
        60% / 25% / 15%.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "Rent",
                                  "Rent", "1200.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Food",
                                  "Groceries", "500.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Car",
                                  "Car Payment", "300.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Hero: 1200 + 500 + 300 = 2000, measured scope on Checking.
            assert "$2,000.00" in html
            assert "January 2026" in html
            assert "measured" in html
            assert "Checking" in html
            # Where It Went: groups amount-descending with the group amount.
            assert "Where It Went" in html
            assert "Home" in html
            assert "Family" in html
            assert "Auto" in html
            assert "$1,200.00" in html
            # Shares 60 / 25 / 15 percent.
            assert "60%" in html
            assert "25%" in html
            assert "15%" in html
            # Share bars carry the CSP-safe width attribute.
            assert "data-progress-pct" in html

    def test_spending_tab_comparison_chips(self, app, auth_client, seed_user,
                                           seed_periods, db):
        """A February window shows the vs-January comparison chip.

        January (period[0]) spent 1000; February (period[3], starts
        2026-02-13) spent 1500.  vs-prior delta = 1500 - 1000 = +500 (+50%),
        rendered as a spent-more (danger) direction.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "JanRent",
                                  "Rent", "1000.00")
            _settled_spending_txn(db, seed_user, seed_periods[3], "FebRent",
                                  "Rent", "1500.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=2",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "$1,500.00" in html          # February spent hero
            assert "vs January" in html          # vs-prior chip label
            assert "+$500.00" in html            # SIGNED delta (P-AN11 form)
            assert "+50.0%" in html
            # The caption names the prior month's total, so the delta can
            # no longer read as January's total (P-AN11).
            assert "January total $1,000.00" in html
            assert "trend-up" in html            # spent more -> danger dir

    def test_spending_tab_surprises(self, app, auth_client, seed_user,
                                    seed_periods, db):
        """A settled row whose actual differs from estimate is a surprise.

        Electric Bill est 100 actual 145 -> delta +45 (a surprise); Rent
        Exact est 1200 actual 1200 -> delta 0 (not a surprise).  The net over
        ALL surprises is +45.
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "Electric Bill",
                                  "Rent", "100.00", actual="145.00")
            _settled_spending_txn(db, seed_user, seed_periods[0], "Rent Exact",
                                  "Rent", "1200.00", actual="1200.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            assert "Estimate Surprises" in html
            assert "Electric Bill" in html
            assert "+$45.00" in html             # delta 145 - 100
            # Net over ALL surprises = +45 (the est==actual row is excluded).
            assert "net +$45.00" in html

    def test_spending_tab_chart_and_lens_rows(self, app, auth_client,
                                              seed_user, seed_periods, db):
        """The month chart serializes the series; the ledger carries both lenses.

        January (period[0]): Rent 1000, then it stops.  February
        (period[3], starts 2026-02-13): Groceries 460, brand new.  Viewing
        February 2026: the chart's 12 bars end at Feb (index 11 = 460.0)
        with Jan at index 10 (1000.0), pre-history 2025 months null, the
        6-mo avg = 1000.0 (Jan is the only existing trailing month), and
        the history note at Jan 2026.  The By-size lens collapses the
        singleton Family group (Groceries as kin, "new" badge); the
        By-change lens lists the stopped Rent at -$1,000.00.  Top Movers
        and sparklines are retired (D7).
        """
        with app.app_context():
            _settled_spending_txn(db, seed_user, seed_periods[0], "JanRent",
                                  "Rent", "1000.00")
            _settled_spending_txn(db, seed_user, seed_periods[3], "FebFood",
                                  "Groceries", "460.00")
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=2",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # The in-canvas chart carries the trailing-12 series.
            assert 'id="spending-months-canvas"' in html
            chart = _chart_payload(html)
            assert len(chart["values"]) == 12
            assert chart["viewed_index"] == 11
            assert chart["values"][11] == 460.0    # February (viewed)
            assert chart["values"][10] == 1000.0   # January (comparison)
            assert chart["values"][0] is None      # Mar 2025: pre-history
            assert chart["nav"][11] == {"year": 2026, "month": 2}
            # 6-mo avg: Jan is the only trailing month that exists -> 1000.
            assert chart["avg"] == 1000.0
            assert chart["history_note"] == "settled history begins Jan 2026"

            # Lens pill; singleton Family collapses with Groceries as kin.
            assert "By size" in html
            assert "By change" in html
            assert "spend-kin" in html
            assert "spend-badge-new" in html
            # By change: the stopped Rent appears as a zero-current row.
            assert "spend-chrow" in html
            assert "-$1,000.00" in html
            # The retired surfaces are gone (D7).
            assert "Top Movers" not in html
            assert "spend-spark-svg" not in html

    def test_spending_tab_empty_month(self, app, auth_client, seed_user,
                                      seed_periods):
        """A window with no settled spend renders the zeroed empty breakdown."""
        with app.app_context():
            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "$0.00" in html
            assert "No settled spending in January 2026" in html

    def test_spending_tab_empty_state_no_account(self, app, auth_client,
                                                 seed_user):
        """No active checking account renders the empty state, not a crash."""
        from unittest.mock import patch
        with app.app_context():
            with patch(
                "app.services.spending_report_service.resolve_cash_flow_set",
                return_value=None,
            ):
                resp = auth_client.get(
                    "/analytics/spending",
                    headers={"HX-Request": "true"},
                )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No active checking account" in html
            assert "Set up an account" in html

    def test_spending_tab_scope_is_the_saved_default_grid_account(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The route passes ``current_user.settings`` to the service (CC-4-3).

        The report's scope is the set's PRIMARY -- the owner's saved default
        grid account first.  An HYSA saved as the default names itself on the
        scope line; a route that dropped ``user_settings=`` would render
        Checking here and pass every other test in this class.
        """
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Rainy Day",
                anchor_balance=Decimal("1000.00"),
            )
            set_default_grid_account(db.session, seed_user["user"].id, hysa.id)
            db.session.commit()

            resp = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "Settled spending from your Rainy Day account." in (
                resp.data.decode()
            )

    def test_spending_tab_default_is_prior_month(self, app, auth_client,
                                                 seed_user, seed_periods):
        """With no month param, the default window is the prior completed month.

        Today is frozen 2026-03-20, so the default is February 2026.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/spending",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "February 2026" in resp.data.decode()

    def test_spending_tab_non_htmx_renders_shell(self, app, auth_client, seed_user):
        """A non-HTMX GET renders the shell with Spending active (D13)."""
        with app.app_context():
            resp = auth_client.get("/analytics/spending")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "shekel-scroll-pills" in html
            assert _shell_autoload_target(html) == "/analytics/spending"


class TestARefundedCategoryDrawsNoBar:
    """The By-size lens's geometry when a category's total is NEGATIVE.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A merchant
    credit files as a NEGATIVE purchase, so a category whose refunds exceeded
    its purchases comes to a negative total.  ``_totals_by_category`` took an
    ``abs()`` until that step and this geometry could not see one.

    **What a bar answers is *how much of the budget did this consume*, and the
    answer for such a row is none.**  Asserted at the SERVER, because the
    clamp in ``progress_bar.js`` is written as a defence against a malformed
    value -- a refunded category is not malformed, and a money-shaped decision
    left to the browser is one no test in this project drives.
    """

    @staticmethod
    def _group(name, amount, share):
        """One singleton group row worth *amount*."""
        return SpendingGroupRow(
            group_name=name, amount=Decimal(amount), share=Decimal(share),
            delta=Decimal("0.00"), is_new=False,
            items=[SpendingItemRow(
                category_id=1, item_name=name, amount=Decimal(amount),
                share=Decimal(share), delta=Decimal("0.00"), is_new=False,
            )],
        )

    def test_a_negative_group_gets_a_zero_width_bar(self):
        """The refunded row, beside an ordinary one that still scales."""
        rows = _size_lens_rows([
            self._group("Home", "1200.00", "0.93"),
            self._group("Amazon", "-86.67", "-0.07"),
        ])

        assert rows[0]["bar_pct"] == 100.0
        assert rows[1]["bar_pct"] == 0.0, (
            "a category whose refunds exceeded its purchases consumed no "
            "budget, so it draws no bar"
        )
        # The FIGURE is untouched: only the geometry is floored.
        assert rows[1]["amount"] == Decimal("-86.67")
        assert rows[1]["share"] == Decimal("-0.07")

    def test_a_window_of_only_refunds_draws_no_bars_at_all(self):
        """Every row negative, so the largest is too and nothing scales.

        The arm ``max_amount <= 0`` covers, which was reachable before this
        step only for an all-ZERO window.
        """
        rows = _size_lens_rows([
            self._group("Amazon", "-86.67", "0"),
            self._group("Walmart", "-29.79", "0"),
        ])

        assert [row["bar_pct"] for row in rows] == [0.0, 0.0]

    def test_an_ordinary_positive_row_still_scales(self):
        """The control: the floor is on the sign, not on every row."""
        assert _bar_pct(Decimal("50.00"), Decimal("200.00")) == 25.0

    @staticmethod
    def _group_of(name, items):
        """One group row whose amount is the SIGNED sum of its items."""
        total = sum((Decimal(a) for _, a in items), Decimal("0.00"))
        return SpendingGroupRow(
            group_name=name, amount=total, share=Decimal("0"),
            delta=Decimal("0.00"), is_new=False,
            items=[
                SpendingItemRow(
                    category_id=index, item_name=item_name,
                    amount=Decimal(amount), share=Decimal("0"),
                    delta=Decimal("0.00"), is_new=False,
                )
                for index, (item_name, amount) in enumerate(items, start=1)
            ],
        )

    def test_an_ITEM_bar_cannot_exceed_its_own_track(self):
        """A mixed-sign group holds an item BIGGER than any group total.

        Plan step ``bank_import:X-gj-2b-3``, found by adversarial financial
        review.  The denominator was ``max(group.amount ...)``, and every bar
        was bounded at 100% only because an item could not exceed its group:
        while every category total was non-negative, ``item <= group <= max``.
        Ruling **bank_import:R-II** ended that.

        ``Food`` = ``Groceries +600.00`` and ``Restaurants -500.00`` sums to
        ``+100.00`` while holding a ``+600.00`` item; against ``Housing``'s
        ``+300.00`` that item rendered **200.0** -- off its own track, and
        clamped only by ``progress_bar.js``, which is the tier ``_bar_pct``
        says in as many words a money-shaped decision may not be left to.

        Asserts the BOUND over every rendered bar rather than one number, so a
        denominator that merely moves the overflow somewhere else still fails.
        """
        rows = _size_lens_rows([
            self._group_of("Food", [
                ("Groceries", "600.00"), ("Restaurants", "-500.00"),
            ]),
            self._group_of("Housing", [("Rent", "300.00")]),
        ])

        every_bar = [row["bar_pct"] for row in rows] + [
            item["bar_pct"] for row in rows for item in row["item_rows"]
        ]
        assert every_bar, "the case staged no bars, so it graded nothing"
        assert max(every_bar) <= 100.0, (
            f"a bar overflowed its track: {every_bar}"
        )
        # ...and the largest RENDERED row is what fills it, which is the
        # contract the docstring states.
        assert max(every_bar) == 100.0

    def test_a_window_with_no_refunds_scales_exactly_as_before(self):
        """The control the fix turns on: nothing moves without a refund.

        An item cannot exceed its own group while every category is
        non-negative, so the maximum over groups and the maximum over
        everything rendered are the SAME figure -- and this case fails if the
        denominator were changed in a way that moved an ordinary window.
        """
        rows = _size_lens_rows([
            self._group_of("Food", [
                ("Groceries", "600.00"), ("Restaurants", "200.00"),
            ]),
            self._group_of("Housing", [("Rent", "400.00")]),
        ])
        by_name = {row["name"]: row for row in rows}

        # max over groups is Food's 800.00, and no item exceeds it.
        assert by_name["Food"]["bar_pct"] == 100.0
        assert by_name["Housing"]["bar_pct"] == 50.0
        assert [item["bar_pct"] for item in by_name["Food"]["item_rows"]] == [
            75.0, 25.0,
        ]


class TestTheHeroStatesBOTHBasesWhenTheyDiffer:
    """The card carried two undisclosed bases the moment a refund landed.

    Developer ruling 2026-09-01, plan step ``bank_import:X-gj-2b-3``, found by
    adversarial design review.  The hero is the NET (``_spent_total``) and
    every breakdown row's percent is a slice of what MOVED
    (``_breakdown._share_base``).  Ruling **bank_import:R-II** separated the
    two: `Groceries $600.00` beside `Electronics -$500.00` gave a hero of
    `$100.00` over rows reading `55%` and `-45%` -- percentages summing to
    about 9%, and a row six times its own hero.  The second figure is stated
    where they differ so a reader can multiply a row by a number on the card.
    """

    @staticmethod
    def _settled_envelope(db, seed_user, period, name, category_key, amounts):
        """Settle an envelope worth exactly the purchases handed to it.

        A negative CATEGORY total cannot come from a negative transaction --
        ``ck_transactions_estimated_amount`` is ``>= 0`` -- so the refunded
        shape is an ENVELOPE whose entries net below zero, which is the shape
        ruling **bank_import:R-II** actually creates.
        """
        txn = create_envelope_txn(
            seed_user, db.session, period, name, Decimal("0.00"),
        )
        txn.category_id = seed_user["categories"][category_key].id
        for amount in amounts:
            add_entry(
                db.session, seed_user, txn, Decimal(amount),
                period.start_date,
            )
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(
                txn, ref_cache.status_id(StatusEnum.DONE),
            ),
        )
        db.session.flush()
        return txn

    def test_a_refunded_month_states_what_MOVED_beside_the_net(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """`$600.00` out and `$500.00` back: hero `$100.00`, moved `$1,100.00`."""
        with app.app_context():
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Shop", "Groceries",
                ["600.00"],
            )
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Return", "Rent",
                ["-500.00"],
            )
            db.session.commit()

            html = auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            ).data.decode()
            said = " ".join(html.split())

            assert "$100.00" in said, "the hero is still the NET"
            assert "$1,100.00 moved" in said
            assert "net of refunds above" in said

    def test_an_ORDINARY_month_states_ONE_figure(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The control: the two bases are the same figure without a refund.

        Printing one number twice would be noise, and this is what makes the
        second line a disclosure rather than decoration -- it fails if the
        line were rendered unconditionally.
        """
        with app.app_context():
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Shop", "Groceries",
                ["600.00"],
            )
            db.session.commit()

            said = " ".join(auth_client.get(
                "/analytics/spending?year=2026&month=1",
                headers={"HX-Request": "true"},
            ).data.decode().split())

            assert "$600.00" in said
            assert "moved" not in said
            assert "net of refunds above" not in said


class TestTheHistoryNoteAgreesWithTheBarsBesideIt:
    """The chart's *settled history begins* caption asks *did money MOVE*.

    Plan step ``bank_import:X-gj-2b-3``.  The note exists to explain a LEADING
    RUN OF EMPTY BARS, and it found the first bar by ``point.total > 0`` on the
    stated premise that a window's total is non-negative.  Ruling
    **bank_import:R-II** ended that premise: a month whose refunds exceeded its
    purchases has a negative total and DRAWS A BAR, so a leading negative month
    got a caption saying history began a month after the chart's own first bar
    -- the sentence contradicting the picture it sits under.

    A window with pay periods but no settled spend is ``Decimal("0")`` and is
    genuinely an empty bar; one before the owner's periods is ``None``.  Both
    still count as no history, which is what the cases below pin.
    """

    @staticmethod
    def _point(year, month, total):
        """One series bar for *month*, worth *total* (``None`` = pre-history)."""
        return SeriesPoint(
            window=SpendingWindow(
                window_type="month", month=month, year=year,
            ),
            total=None if total is None else Decimal(total),
        )

    def test_a_leading_REFUNDED_month_is_where_history_begins(self):
        """A negative first bar is history, so there is no note to make.

        Under ``total > 0`` this returned *settled history begins Feb 2026*
        while January's bar was drawn at ``-50.00`` right beside it.
        """
        assert _history_note([
            self._point(2026, 1, "-50.00"),
            self._point(2026, 2, "100.00"),
        ]) is None

    def test_a_leading_EMPTY_month_still_makes_the_note(self):
        """The control: a zero month really is an empty bar.

        Without this case the class above would pass on a function that never
        returned a note at all.
        """
        assert _history_note([
            self._point(2026, 1, "0.00"),
            self._point(2026, 2, "100.00"),
        ]) == "settled history begins Feb 2026"

    def test_a_leading_PRE_HISTORY_month_still_makes_the_note(self):
        """``None`` is a window before the owner's pay periods, not a zero."""
        assert _history_note([
            self._point(2025, 12, None),
            self._point(2026, 2, "100.00"),
        ]) == "settled history begins Feb 2026"

    def test_a_series_that_moved_nothing_makes_no_note(self):
        """Nothing to date, so nothing is said."""
        assert _history_note([
            self._point(2026, 1, "0.00"),
            self._point(2026, 2, None),
        ]) is None

    def test_a_REFUNDED_month_after_a_zero_one_dates_the_note_to_itself(self):
        """The two halves together: zero is not history and negative is.

        January ``0.00`` then February ``-50.00`` -- the note must name
        February, which is the first bar the chart draws.  Under ``total > 0``
        this returned ``None`` and the leading empty bar went unexplained.
        """
        assert _history_note([
            self._point(2026, 1, "0.00"),
            self._point(2026, 2, "-50.00"),
        ]) == "settled history begins Feb 2026"
