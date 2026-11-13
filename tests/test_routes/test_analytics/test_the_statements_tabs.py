"""The Income Statement and Balance Sheet tabs, and the one derivation their window uses."""

import re
from datetime import date, time
from decimal import Decimal
from html import unescape

from app.utils.dates import display_today

from tests._test_helpers import create_settled_cash_transaction, freeze_today

from ._helpers import _shell_autoload_target


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

    **Two of the three cases plan step C2-f3a wrote in this class FAILED on its
    merge base**, which made them a regression gate rather than a pin: the
    retired reader read the STORED span and the PROCESS clock, and each of the
    two moved exactly one of those away from what the paydays say.  Plan step
    ``pay_calendar:C4-c`` dropped the stored end date and deleted its case (the
    comment below says why), so one gate remains, the case that moves the
    process clock.  The other case below pins that the heading and the
    ``<option>`` the reader picked it from cannot drift apart, which is ledger
    row **P47**'s duplicate half made a predicate.

    ``seed_periods`` is ten biweekly periods from 2026-01-02, and this package's
    ``conftest.py`` freezes today to 2026-03-20 -- inside period 5, which runs 2026-03-13
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
