"""The Calendar tab's partial, its year overview and its inline day totals."""

from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum
from app.services import account_service

from tests._test_helpers import (
    create_account_of_type,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    set_default_grid_account,
)

from ._helpers import _shell_autoload_target


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
            # ``tests/test_routes/test_no_baseline_policy/``.

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
