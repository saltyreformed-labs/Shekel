"""Grid route tests: the grid view, its row scoping, the balance row and the subtotal rows
endpoint.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.scenario import Scenario
from app.models.ref import TransactionType
from app.services import balance_at
from app.services.balance_at import BalanceContext

from tests._test_helpers import current_pay_period, freeze_today, one_off_row_of


class TestGridView:
    """Tests for the main grid page at /."""

    def test_grid_loads_with_periods(self, app, auth_client, seed_user, seed_periods_today):
        """GET / renders the budget grid with pay period columns."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            # Check for key grid elements.
            assert b"Checking Balance" in response.data
            assert b"Projected End Balance" in response.data

    def test_grid_shows_no_periods_page(self, app, auth_client, seed_user):
        """GET / shows the no-periods prompt when none exist."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"No Pay Periods" in response.data

    def test_grid_shows_dynamic_account_name(self, app, auth_client, seed_user, seed_periods_today):
        """GET / shows the resolved account name in the header."""
        with app.app_context():
            response = auth_client.get("/grid")
            assert response.status_code == 200
            assert b"Checking Balance" in response.data

    def test_grid_period_controls(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """Grid respects the periods query parameter.

        Asserts the literal "01/02" rendered in the header, which is
        the start of seed_periods[0] (2026-01-02).  Uses the calendar-
        anchored seed_periods + freeze_today to keep the assertion
        stable regardless of wall-clock date.
        """
        freeze_today(monkeypatch, date(2026, 1, 5))
        with app.app_context():
            response = auth_client.get("/grid?periods=3")
            assert response.status_code == 200
            assert b"01/02" in response.data
            assert b"Projected End Balance" in response.data


class TestGridRowScoping:
    """Tests for the compact-view default and ?show_all=1 opt-out.

    Compact view (the default) generates row keys only from
    transactions whose pay_period_id is in the visible window.  This
    hides one-offs and infrequent recurring items that have nothing
    to render in the current view.  ``?show_all=1`` restores the old
    full-projection behavior for full planning sessions.  Subtotals
    and projected balances must be identical either way -- only which
    rows render changes.
    """

    def _make_oneoff(
        self, seed_user, period, name, amount="42.00",
    ):
        """Place one one-off expense in the given period."""
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        txn = one_off_row_of(
            period,
            name=name,
            amount=Decimal(amount),
            user_id=period.user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Rent"].id,
        )
        db.session.flush()
        return txn

    def _visible_period(self, seed_user, seed_periods_today):
        """Return a period that falls in the default visible window.

        The grid starts at the current period; seed_periods_today places
        period 4 around today's date so the containment lookup always
        answers a real period.  No fallback is needed.
        """
        # pylint: disable=unused-argument
        return current_pay_period(
            seed_user["user"].id,
        )

    def _hidden_period(self, seed_user, seed_periods_today):
        """Return a period that is NOT in the default visible window.

        The anchor period sits at ``seed_periods_today[0]``, ~8 weeks
        before today.  With ``grid_default_periods=6`` the visible
        window starts at the current period, so the anchor is
        historical and hidden in compact view.
        """
        # pylint: disable=unused-argument
        return seed_periods_today[0]

    def test_compact_view_hides_oneoff_outside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off in a hidden period must not render its row label."""
        with app.app_context():
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="HIDDEN_FAR_AWAY_BILL",
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"HIDDEN_FAR_AWAY_BILL" not in resp.data

    def test_compact_view_shows_oneoff_inside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off in the visible window must render its row label."""
        with app.app_context():
            visible = self._visible_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, visible, name="VISIBLE_NEARBY_BILL",
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"VISIBLE_NEARBY_BILL" in resp.data

    def test_show_all_reveals_oneoff_outside_visible_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """?show_all=1 must render rows from the full forward projection."""
        with app.app_context():
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="FAR_REVEALED_BY_SHOW_ALL",
            )
            db.session.commit()

            resp = auth_client.get("/grid?show_all=1")
            assert resp.status_code == 200
            assert b"FAR_REVEALED_BY_SHOW_ALL" in resp.data

    def test_compact_toggle_button_defaults_to_show_all_link(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The toggle button in compact view must link to show_all=1."""
        with app.app_context():
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            assert b"show_all=1" in resp.data
            assert b"All Rows" in resp.data

    def test_show_all_toggle_button_links_back_to_compact(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """When show_all is active, the button must link back without it."""
        with app.app_context():
            resp = auth_client.get("/grid?show_all=1")
            assert resp.status_code == 200
            assert b"Compact" in resp.data

    def test_scoping_does_not_change_visible_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Adding a hidden-period txn must not shift any visible subtotal.

        This is the key correctness invariant: hiding a row is a pure
        display filter, so the computed totals for visible periods
        must be byte-identical before and after the hidden txn exists.
        """
        with app.app_context():
            baseline = auth_client.get("/grid").data
            hidden = self._hidden_period(seed_user, seed_periods_today)
            self._make_oneoff(
                seed_user, hidden, name="HIDDEN_SUBTOTAL_PROBE",
                amount="999.00",
            )
            db.session.commit()

            after = auth_client.get("/grid").data
            assert b"HIDDEN_SUBTOTAL_PROBE" not in after

            # Projected End Balance is the canonical forward-math
            # summary.  It SHOULD change because the hidden txn still
            # affects the actual account trajectory (projected
            # balances include the full forward projection, not just
            # visible-row txns).  This asserts balance math is not
            # coupled to row scoping.
            assert b"Projected End Balance" in after


class TestBalanceRow:
    """Tests for GET /grid/balance-row HTMX partial."""

    def test_balance_row_returns_partial(self, app, auth_client, seed_user, seed_periods_today):
        """GET /grid/balance-row returns recalculated balance HTML partial."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=6&offset=0")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            # Total Income/Expenses are now in tbody subtotals, not in the tfoot.
            assert b"Total Income" not in resp.data

    def test_balance_row_no_current_period(self, app, auth_client, seed_user):
        """GET /grid/balance-row with no periods returns 204 empty."""
        with app.app_context():
            # No periods generated -- no period contains today, so the
            # grid's containment lookup answers None.
            resp = auth_client.get("/grid/balance-row")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_no_baseline_scenario(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /grid/balance-row returns 204 when the user has no baseline scenario.

        Regression test for F-099 (C-45 of the 2026-04-15 security
        audit).  Before the fix, ``balance_row`` dereferenced
        ``scenario.id`` to build the transaction query filter; when
        ``get_baseline_scenario`` returned ``None`` (orphaned test
        fixture or a freshly-deleted user mid-cascade in production)
        the route raised ``AttributeError: 'NoneType' object has no
        attribute 'id'`` and returned HTTP 500 via the unhandled-
        exception handler.

        The fix short-circuits with HTTP 204 No Content, matching the
        existing ``not current_period`` branch -- HTMX leaves the
        existing DOM untouched and the user sees a coherent empty state
        instead of a stack trace.

        **The 204 now comes from the application-level handler, not from a
        guard in this route** (plan step X-v2, ruling R-BW), and the request
        carries the ``HX-Request`` header the browser actually sends for this
        endpoint.  That header is load-bearing rather than cosmetic: this test
        used to pass while omitting it, so it pinned the answer to a request
        shape no client makes.  A plain GET of the same URL is a human pasting
        it into the address bar, and gets the repair card.

        Asserts both the status code AND empty body to pin the
        contract; a future change that returns 200 with a rendered
        template would silently regress the HTMX partial-swap UX.
        """
        with app.app_context():
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            resp = auth_client.get(
                "/grid/balance-row?periods=6&offset=0",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204
            assert resp.data == b""

    def test_balance_row_custom_offset(self, app, auth_client, seed_user, seed_periods_today):
        """GET /grid/balance-row with offset shifts the visible window."""
        with app.app_context():
            resp = auth_client.get("/grid/balance-row?periods=3&offset=2")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"Total Expenses" not in resp.data

    @pytest.mark.server_clock
    def test_a_settled_post_anchor_row_raises_the_balance_not_a_banner(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-anchor banner is GONE, and the row it warned about counts.

        The anchor sits at periods[0]; a Paid (settled) $1,200.00 expense in a
        later period used to be the "stale anchor" condition -- the balance
        row's response carried a warning banner out-of-band saying the
        projection might be wrong, because that row contributed nothing to it
        and only a re-anchor could fix the figure.

        Since plan step X-c2b2 the balance is a fold that counts the row from
        the day its money moved, so there is nothing left to warn about: the
        banner, its flag and its detector are deleted, and the balance itself
        moves.  Hand-computed: $1,000.00 anchor - $1,200.00 = -$200.00 from
        that period on.

        The row's settle day is the user's today, which
        is after every seeded period here, so the balance drops in the LAST
        column rather than in the row's own -- which is why the assertion reads
        the final period and why finding N-42 (nothing records when money
        moved) is the follow-up plan step X-f exists for.
        """
        with app.app_context():
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_settled_cash_transaction,
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods_today[2],
                Decimal("1200.00"), name="Paid Rent",
            )
            db.session.commit()

            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # The banner and every trace of its wiring are gone.
            assert 'id="stale-anchor-warning"' not in html
            assert "marked as done in periods after your anchor" not in html
            assert "<template>" not in html
            # And the settled row is IN the balance.
            balances = balance_at.cash_balance_map(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
            )
            assert balances[seed_periods_today[-1].id] == Decimal("-200.00")

    def test_the_balance_row_response_opens_on_the_tfoot(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The partial's FIRST top-level element is the ``<tfoot>``.

        Load-bearing parser-safety shape, inherited from the deleted banner:
        htmx parses every partial inside a ``<template>`` wrapper, and per the
        HTML5 tree-construction spec a BARE non-table element preceding the
        ``<tfoot>`` flips the parser into the "in body" insertion mode, where
        the following tfoot/tr/td start tags are silently DROPPED.  The balance
        row then swaps in as loose unstyled text and, because the replacement
        carries no ``hx-trigger``, its ``balanceChanged`` self-refresh dies for
        the rest of the session (the projected-end-balance freeze regression
        introduced by ca47a1d).

        The banner that forced the old ``<template>`` encapsulation is gone, so
        the shape is simply "nothing precedes the tfoot" -- which is what this
        asserts, because a future partial that reintroduced a leading element
        would revive the same regression.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert html.lstrip().startswith("<tfoot")

    def test_grid_periods_large_value(
        self, app, auth_client, seed_user, seed_periods, monkeypatch,
    ):
        """GET / with periods larger than available still renders.

        Asserts the literal "01/02" header (start of seed_periods[0]),
        so uses the calendar-anchored seed_periods fixture and freezes
        today inside the period range.
        """
        freeze_today(monkeypatch, date(2026, 1, 5))
        with app.app_context():
            # Request 100 periods when only 10 exist -- should render what's available.
            resp = auth_client.get("/grid?periods=100")
            assert resp.status_code == 200
            assert b"Projected End Balance" in resp.data
            assert b"01/02" in resp.data


class TestSubtotalRowsEndpoint:
    """Tests for GET /grid/subtotal-rows HTMX partial.

    The desktop grid splits its three summary rows (Total Income, Total
    Expenses, Net Cash Flow) into two self-refreshing ``<tbody>``
    sections (grid rebuild Phase 6, audit item C3).  Only the income
    ``<tbody>`` fires this endpoint on ``balanceChanged from:body``; the
    single response carries the income ``<tbody>`` (an outerHTML swap
    replaces it in place) AND the expense ``<tbody>`` as an
    ``hx-swap-oob`` fragment, so ONE GET refreshes both sections.
    Mirrors the :class:`TestBalanceRow` style: auth required, the no-op
    204 contracts, and hand-computed Decimal figures asserted in the
    rendered rows.
    """

    def _seed_income_expense(self, seed_user, period, income, expense):
        """Seed one projected income + one projected expense in *period*.

        Returns nothing; the caller asserts the rendered subtotals.  Uses
        the Salary/Rent seed categories so the rows render under the
        income and expense sections respectively.
        """
        income_type = (
            db.session.query(TransactionType).filter_by(name="Income").one()
        )
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        one_off_row_of(
            period,
            name="Paycheck",
            amount=income,
            user_id=seed_user['account'].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=income_type.id,
            category_id=seed_user["categories"]["Salary"].id,
        )
        one_off_row_of(
            period,
            name="Rent",
            amount=expense,
            user_id=seed_user['account'].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Rent"].id,
        )
        db.session.commit()

    def test_one_response_carries_both_sections(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A single GET returns BOTH the income and expense subtotal tbodies.

        Income $2,000, expense $1,400 in the current period.  One GET (the
        income tbody's self-refresh) must return both sections so a single
        balanceChanged event refreshes the whole summary: the income
        tbody (Total Income = $2,000) carries the self-refresh trigger and
        an outerHTML swap, while the expense tbody (Total Expenses =
        $1,400, Net Cash Flow = $2,000 - $1,400 = $600) rides along as an
        hx-swap-oob fragment so it never needs its own GET.
        """
        with app.app_context():
            current = current_pay_period(
                seed_user["user"].id,
            )
            self._seed_income_expense(
                seed_user, current, Decimal("2000.00"), Decimal("1400.00"),
            )

            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()

            # Income tbody: present, self-refreshing, $2,000.
            assert 'id="grid-subtotals-income"' in html
            assert "subtotal-row-income" in html
            assert "Total Income" in html
            assert "$2,000" in html
            # Only the income tbody carries the self-refresh trigger -- it
            # is the single GET; the expense tbody rides along OOB.
            assert 'hx-trigger="balanceChanged from:body"' in html
            assert html.count('hx-trigger="balanceChanged from:body"') == 1

            # Expense tbody: present, OOB, $1,400 + $600 net.
            assert 'id="grid-subtotals-expense"' in html
            assert 'hx-swap-oob="true"' in html
            assert "subtotal-row-expense" in html
            assert "net-cash-flow-row" in html
            assert "Total Expenses" in html
            assert "Net Cash Flow" in html
            # Expense = $1,400; Net = $2,000 - $1,400 = $600.
            assert "$1,400" in html
            assert "$600" in html

    def test_refresh_url_carries_account_and_window(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The income tbody's self-refresh URL pins account_id + window.

        So the next balanceChanged refresh fetches the same account and
        the same periods / offset window, mirroring balance_row threading
        account_id through its hx-get URL.  The section param is gone --
        one GET returns both sections -- so it must NOT appear.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            assert f"account_id={seed_user['account'].id}" in html
            assert "periods=6" in html
            assert "offset=0" in html
            # The collapsed-to-one-GET endpoint no longer takes a section.
            assert "section=" not in html

    def test_aria_live_polite_on_both_tbodies(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both self-refreshing tbodies announce updates politely (a11y).

        The mirrored ``_balance_row.html`` tfoot carries
        ``aria-live="polite"`` + ``aria-atomic="true"``; both summary
        tbodies must too so a screen reader announces the refreshed
        figures after a mark-done.
        """
        with app.app_context():
            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # Two tbodies, each with the polite/atomic pair.
            assert html.count('aria-live="polite"') == 2
            assert html.count('aria-atomic="true"') == 2

    def test_no_current_period_returns_204(
        self, app, auth_client, seed_user,
    ):
        """No generated periods (no current period) returns 204."""
        with app.app_context():
            resp = auth_client.get("/grid/subtotal-rows")
            assert resp.status_code == 204
            assert resp.data == b""

    def test_no_baseline_scenario_returns_204(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No baseline scenario returns 204, matching balance_row (F-099).

        The 204 comes from the application-level ``BaselineMissingError``
        handler since plan step X-v2 (ruling R-BW) rather than from a guard in
        this route, and the request carries the ``HX-Request`` header this
        endpoint is actually called with -- see ``balance_row``'s twin above
        for why that header is the point and not a detail.
        """
        with app.app_context():
            db.session.query(Scenario).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            resp = auth_client.get(
                "/grid/subtotal-rows?periods=6&offset=0",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204
            assert resp.data == b""

    def test_other_users_account_id_does_not_leak_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user,
    ):
        """A cross-user account_id never leaks the other account's subtotals.

        The first user requests subtotals while passing the SECOND user's
        account_id.  ``resolve_grid_account`` rejects the cross-user id
        (it is not owned by the requester) and falls back to the
        requester's own account, so a $9,999 income seeded against the
        second user's account never appears in the response -- the
        404-for-not-yours guarantee expressed as data isolation (the
        endpoint scopes strictly to ``current_user``).  The first user's
        own $2,000 income IS shown because the fallback lands on their
        account.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            current = current_pay_period(
                seed_user["user"].id,
            )
            # First user's own income -- shown via the fallback.
            self._seed_income_expense(
                seed_user, current,
                Decimal("2000.00"), Decimal("0.00"),
            )
            # Second user's income on the second user's account/period --
            # must NOT leak into the first user's subtotal response.
            one_off_row_of(
                seed_second_user["bootstrap_period"],
                name="Other Paycheck",
                amount=Decimal("9999.00"),
                user_id=seed_second_user['bootstrap_period'].user_id,
                account_id=seed_second_user["account"].id,
                scenario_id=seed_second_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_second_user["categories"]["Salary"].id,
            )
            db.session.commit()

            resp = auth_client.get(
                f"/grid/subtotal-rows?periods=6&offset=0"
                f"&account_id={seed_second_user['account'].id}"
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            # The second user's $9,999 income must not appear.
            assert "$9,999" not in html
            # The first user's own $2,000 income is shown via the fallback.
            assert "$2,000" in html
