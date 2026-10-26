"""Grid route tests: the grid reading checking and its cards, the chip on a row of another account,
and the create forms' set.
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.account_resolver import ResolvedCashFlow
from app.services.cash_flow_set import CashFlowSet
from app.services.grid_view_service import leg_dom_id
from app import ref_cache
from app.enums import TxnTypeEnum
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_savings_account,
    create_transfer,
    one_off_row_of,
)

from ._helpers import (
    _summary_periods,
    _summary_columns,
    _row_flags,
    _render_grid_footer,
    _render_mobile_card,
    _render_plan_recap,
)


class TestTheGridReadsCheckingAndItsCards:
    """Plan step credit_card:CC-4-1: the paycheck grid across the owner's cash-flow set.

    Developer ruling ``credit_card:R-CC16``: a plan item's ``account_id`` is
    the account its money is expected to move through, so the phone bill that
    is always paid by card is a row ON the card and the grid reads checking
    and its cards as ONE paycheck -- the rows are every member's, the Total
    Income / Expenses / Net rows are the paycheck's, the Projected End Balance
    stays one account's, and the footer gains "On other accounts".  Ruling
    ``credit_card:R-CC23``: a transfer between two members shows once, from
    the balance line's side.

    The first three tests render the three surfaces that carry the new row
    against hand-built columns (the same shape ``TestTheTwoRemainderRows``
    grades); the rest drive the live routes over the ruling's own worked
    example -- phone ``$45`` on the card, grocery ``$500`` on checking, a
    ``$165`` card payment in the same paycheck -- and read the figures the
    seam's test hand-computed (``Total Expenses $710``, ``On other accounts
    $45``, and from the card's side ``Total Income $165``).
    """

    _ROW = "On other accounts"

    def test_the_desktop_footer_renders_the_row_first_when_flagged(self, app):
        """Above Period timing (it is the budget-side term), hidden on a zero flag."""
        periods = _summary_periods()
        with app.app_context():
            shown = _render_grid_footer(
                app, periods,
                _summary_columns(periods, elsewhere="45.00", period_timing="-427.22"),
                _row_flags(elsewhere=True, period_timing=True),
            )
            hidden = _render_grid_footer(
                app, periods,
                _summary_columns(periods, period_timing="-427.22"),
                _row_flags(period_timing=True),
            )

        assert self._ROW in shown
        assert "$45" in shown
        assert shown.index(self._ROW) < shown.index("Period timing")
        assert shown.index("Period timing") < shown.index("Projected End Balance")
        assert self._ROW not in hidden
        assert "bi-wallet2" not in hidden

    def test_the_mobile_card_and_the_plan_recap_render_it_on_the_same_rule(self, app):
        """Ruling R-P: every form factor explains its balance the same way."""
        period = _summary_periods()[0]
        columns = _summary_columns([period], elsewhere="45.00")
        with app.app_context():
            card = _render_mobile_card(
                app, period, columns, _row_flags(elsewhere=True),
            )
            card_hidden = _render_mobile_card(
                app, period, columns, _row_flags(),
            )
            recap = _render_plan_recap(
                app, period, columns, _row_flags(elsewhere=True),
            )
            recap_hidden = _render_plan_recap(
                app, period, columns, _row_flags(),
            )

        assert self._ROW in card
        assert "$45" in card
        assert card.index("Net Cash Flow") < card.index(self._ROW)
        assert self._ROW not in card_hidden
        assert "Elsewhere $45" in recap
        assert "Elsewhere" not in recap_hidden

    @staticmethod
    def _world(seed_user, periods):
        """Build the worked example on a FUTURE paycheck; return its parts."""
        user_id = seed_user["user"].id
        scenario_id = seed_user["scenario"].id
        checking = seed_user["account"]
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )
        paycheck = periods[6]
        assert paycheck.start_date > BalanceContext.build(user_id).as_of + timedelta(days=1)
        expense = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        phone = one_off_row_of(
            paycheck, name="Phone on the card", amount=Decimal("45.00"),
            user_id=user_id, account_id=card.id, scenario_id=scenario_id,
            transaction_type_id=expense,
        )
        one_off_row_of(
            paycheck, name="Grocery", amount=Decimal("500.00"),
            user_id=user_id, account_id=checking.id, scenario_id=scenario_id,
            transaction_type_id=expense,
        )
        create_transfer(
            seed_user, db.session, checking, card, paycheck,
            amount=Decimal("165.00"), name="Card payment",
        )
        db.session.commit()
        # Ids, not rows: the request below closes the session, and a detached
        # row cannot answer ``.id`` afterwards.
        return checking.id, card.id, paycheck, phone.id

    @staticmethod
    def _tfoot(html):
        """Return the sticky footer's HTML, closing tag included."""
        end = html.index("</tfoot>") + len("</tfoot>")
        return html[html.index("<tfoot"):end]

    @staticmethod
    def _figures(html, label):
        """Return every money figure in the ``<tr>`` labelled *label*, in order.

        Read per ROW rather than by substring over a section, so ``$710``
        cannot be satisfied by ``-$710`` in another row and a fixture anchor
        (``-$500.00``) cannot satisfy a ``$500`` the test means for a
        different line.
        """
        start = html.index(label)
        row = html[start:html.index("</tr>", start)]
        return re.findall(r"-?\$[\d,]+(?:\.\d\d)?", row)

    def test_the_page_shows_the_cards_row_and_the_paychecks_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The phone bill renders as a grid row; the subtotals sum both accounts.

        ``periods=1&offset=2`` renders ONE column, the fixture paycheck
        (period 6: today sits in period 4, so an offset of two from the
        current column reaches it), and every figure below is read off its
        own labelled row.
        """
        with app.app_context():
            checking, card, paycheck, phone = self._world(seed_user, seed_periods_today)
            del checking, card
            response = auth_client.get("/grid?periods=1&offset=2")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        # The window's one column is the fixture paycheck.
        assert paycheck.start_date.strftime("%-m/%-d") in html
        # The card's row is on the paycheck grid.
        assert f'id="txn-cell-{phone}"' in html
        assert "Phone on the card" in html
        # The paycheck's subtotals across both accounts (the ruling's worked
        # example), and the term that reconciles them with checking's balance.
        assert self._figures(html, "Total Income") == ["$0"]
        assert self._figures(html, "Total Expenses") == ["$710"]
        assert self._figures(html, "Net Cash Flow") == ["-$710"]
        footer = self._tfoot(html)
        assert self._figures(footer, self._ROW) == ["$45"]
        assert footer.index(self._ROW) < footer.index("Projected End Balance")
        # Checking's balance line moves by the paycheck's net plus the term:
        # -710 + 45 = -665 off the seeded $1,000.00 opening.
        assert self._figures(footer, "Projected End Balance") == ["$335"]

    def test_the_self_refresh_partials_agree_with_the_page(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The balance row and the subtotal rows recompute the SAME set."""
        with app.app_context():
            self._world(seed_user, seed_periods_today)
            page = auth_client.get("/grid?periods=1&offset=2").get_data(as_text=True)
            balance_row = auth_client.get(
                "/grid/balance-row?periods=1&offset=2",
            ).get_data(as_text=True)
            subtotal_rows = auth_client.get(
                "/grid/subtotal-rows?periods=1&offset=2",
            ).get_data(as_text=True)

        assert self._figures(balance_row, self._ROW) == ["$45"]
        assert self._figures(subtotal_rows, "Total Expenses") == ["$710"]
        # The page's footer and the partial's are the same rendered rows.
        assert self._tfoot(page).strip() == balance_row.strip()

    def test_the_mobile_summary_partial_reads_the_set_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``/grid/this-period-summary`` recomputes the SAME set for its one period."""
        with app.app_context():
            checking, card, paycheck, phone = self._world(seed_user, seed_periods_today)
            del checking, card, phone
            period_id = paycheck.id
            response = auth_client.get(
                f"/grid/this-period-summary?period_id={period_id}",
            )
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        # The mobile bars are ``<div>``s, not ``<tr>``s: read each labelled
        # bar to its closing ``</div>`` the way ``_figures`` reads a row.
        def bar(label):
            start = html.index(label)
            return re.findall(r"-?\$[\d,]+", html[start:html.index("</div>", start)])
        assert bar("Net Cash Flow") == ["-$710"]
        assert bar(self._ROW) == ["$45"]

    def test_an_override_naming_the_card_moves_the_balance_line_only(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?account_id=<card>``: the card's balance and header; the same paycheck's rows.

        From the card's side the payment is the NEAR leg -- Total Income
        ``$165`` -- and "On other accounts" is checking's ``$500``.
        """
        with app.app_context():
            checking, card, paycheck, phone = self._world(seed_user, seed_periods_today)
            del checking, paycheck
            response = auth_client.get(f"/grid?periods=1&offset=2&account_id={card}")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Rewards Card Balance" in html
        assert f'id="txn-cell-{phone}"' in html
        assert "Grocery" in html
        assert self._figures(html, "Total Income") == ["$165"]
        assert self._figures(html, "Total Expenses") == ["$545"]
        assert self._figures(html, "Net Cash Flow") == ["-$380"]
        footer = self._tfoot(html)
        assert self._figures(footer, self._ROW) == ["$500"]
        # The card's line: -500 opening, then -45 + 165 = +120 -> -$380.
        assert self._figures(footer, "Projected End Balance") == ["-$380"]

    def test_an_override_outside_the_set_is_the_single_account_grid_as_before(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?account_id=<savings>``: no card row, no "On other accounts"."""
        with app.app_context():
            checking, card, paycheck, phone = self._world(seed_user, seed_periods_today)
            del checking, card, paycheck
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            db.session.commit()
            savings_id = savings.id
            response = auth_client.get(f"/grid?periods=1&offset=2&account_id={savings_id}")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Savings Balance" in html
        assert f'id="txn-cell-{phone}"' not in html
        assert "Grocery" not in html
        assert self._ROW not in self._tfoot(html)

    def test_an_owner_with_no_card_sees_no_row_and_the_same_subtotals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The pre-card grid: one member, the row hidden, checking's own figures."""
        with app.app_context():
            paycheck = seed_periods_today[6]
            one_off_row_of(
                paycheck, name="Grocery", amount=Decimal("500.00"),
                user_id=seed_user["user"].id, account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            )
            db.session.commit()
            response = auth_client.get("/grid?periods=1&offset=2")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert self._ROW not in html
        assert "bi-wallet2" not in self._tfoot(html)
        assert self._figures(html, "Total Expenses") == ["$500"]


class TestTheChipMarksARowOnAnotherAccount:
    """Plan step credit_card:CC-4-2: the account chip, on every surface that draws a row.

    Developer ruling ``credit_card:R-CC16``: a plan item's ``account_id`` is
    the account its money is expected to move through, and "a row on another
    account carries an account chip".  The grid reads checking and its cards
    as one paycheck (CC-4-1), so the phone bill that lives on the card is a
    row on checking's grid -- and the chip is what says, before it is paid,
    that its money will not move on checking.  It is decided against the
    BALANCE LINE, not against checking: from the card's side the chips move
    to checking's rows.  A transfer between two members shows once, from the
    balance line's side (ruling R-CC23), so no intra-set leg ever chips.

    Four surfaces draw a row and every one is graded here: the desktop cell
    (page and HTMX fragment), the mobile This Period card (page and the
    single-card fragment a Mark Paid swaps in), the mobile Plan row, and the
    companion's card -- which has no balance line and so no chip.  The
    contract test at the end grades the template itself: a surface that
    forgets to publish the balance line raises rather than drawing nothing.
    """

    _CHIP = 'class="flag-chip'

    @staticmethod
    def _world(seed_user, periods):
        """CC-4-1's worked example, plus the ids of every row it planted.

        Returns ``(checking_id, card_id, paycheck_id, rows)`` where ``rows``
        maps ``"phone"`` / ``"grocery"`` to the two one-offs' transaction ids
        and ``"checking_leg"`` / ``"card_leg"`` to the payment's two LEGS'
        CELL ids (``xfer-leg-<transfer>-<account>``, plan step
        balance:X-bi-6-1: the grid draws a transfer's leg off its parent, so
        the leg's cell is keyed by the transfer and the account rather than
        by a shadow row, and a leg has no transaction id to name).
        Ids, not rows: the request that follows closes the session.
        """
        checking, card, paycheck, phone = (
            TestTheGridReadsCheckingAndItsCards._world(seed_user, periods)
        )
        payment = (
            db.session.query(Transfer)
            .filter_by(pay_period_id=paycheck.id, is_deleted=False).one()
        )
        grocery = (
            db.session.query(Transaction)
            .filter_by(pay_period_id=paycheck.id, name="Grocery").one().id
        )
        return checking, card, paycheck.id, {
            "phone": phone, "grocery": grocery,
            "checking_leg": leg_dom_id(payment.id, checking),
            "card_leg": leg_dom_id(payment.id, card),
        }

    @staticmethod
    def _cell(html, cell_id):
        """Return the desktop cell block for one item, to its ``</td>``.

        *cell_id* is the wrapper's id: ``txn-cell-<id>`` for a row (pass the
        transaction id through :meth:`_txn_cell`), the leg's own for a leg.
        """
        start = html.index(f'id="{cell_id}"')
        return html[start:html.index("</td>", start)]

    @staticmethod
    def _txn_cell(txn_id):
        """The wrapper id of a plain row's desktop cell."""
        return f"txn-cell-{txn_id}"

    @staticmethod
    def _card_header(html, prefix, txn_id):
        """Return the mobile card's header ``<li>``, before its expansion."""
        start = html.index(f'id="card-{prefix}-{txn_id}"')
        return html[start:html.index("mobile-card-expansion", start)]

    @staticmethod
    def _plan_row(html, name):
        """Return the Plan tab's static ``<li>`` naming *name*."""
        plan = html[html.index('id="plan-accordion"'):]
        start = plan.index(name)
        return plan[start:plan.index("</li>", start)]

    def _assert_chip(self, block, account_name):
        """The chip is present exactly once, titled and labelled with the account."""
        assert block.count(self._CHIP) == 1, block
        assert f'title="On {account_name}"' in block
        assert f">{account_name}</span>" in block

    def test_the_desktop_cells_chip_the_card_row_seen_from_checking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Phone (on the card) chips ``Rewards Card``; Grocery and the payment leg do not.

        The chip is a caption UNDER the amount pill, never inside it: the
        amount stays the hero and a settled row's filled pill carries no
        pill of its own.
        """
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking, card, paycheck
            response = auth_client.get("/grid?periods=1&offset=2")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        phone = self._cell(html, self._txn_cell(rows["phone"]))
        self._assert_chip(phone, "Rewards Card")
        assert phone.index("</div>") < phone.index(self._CHIP)
        assert self._CHIP not in self._cell(html, self._txn_cell(rows["grocery"]))
        assert self._CHIP not in self._cell(html, rows["checking_leg"])
        # R-CC23: the far leg is not a row on this side at all.
        assert f'id="{rows["card_leg"]}"' not in html

    def test_from_the_cards_side_the_chips_move_to_checking_rows(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``?account_id=<card>``: Grocery chips ``Checking``; Phone and the card leg do not."""
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking, paycheck
            response = auth_client.get(f"/grid?periods=1&offset=2&account_id={card}")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        self._assert_chip(self._cell(html, self._txn_cell(rows["grocery"])), "Checking")
        assert self._CHIP not in self._cell(html, self._txn_cell(rows["phone"]))
        assert self._CHIP not in self._cell(html, rows["card_leg"])
        assert f'id="{rows["checking_leg"]}"' not in html

    def test_the_cell_fragment_draws_the_same_chip_the_page_does(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``GET /transactions/<id>/cell`` -- the swap after every mutation -- agrees.

        Graded as the SAME bytes, not merely the same presence: the fragment
        is what replaces the page's cell after a click, and a chip that
        renders two ways would flicker on that click.
        """
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking, card, paycheck
            page = auth_client.get("/grid?periods=1&offset=2").get_data(as_text=True)
            phone = auth_client.get(f"/transactions/{rows['phone']}/cell")
            grocery = auth_client.get(f"/transactions/{rows['grocery']}/cell")
        assert phone.status_code == 200 and grocery.status_code == 200
        fragment = phone.get_data(as_text=True)
        self._assert_chip(fragment, "Rewards Card")
        chip_line = re.search(r"<div class=\"mt-1\">.*?</div>", fragment).group(0)
        assert chip_line in self._cell(page, self._txn_cell(rows["phone"]))
        assert self._CHIP not in grocery.get_data(as_text=True)

    def test_the_fragment_reads_the_pages_own_side_off_the_current_url(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """On the card's balance line every re-drawn cell is drawn from the card's side.

        htmx sends the page's URL as ``HX-Current-URL`` with every request;
        the fragment resolves its balance line from that URL's ``account_id``
        through the SAME call the page makes.  Developer ruling 2026-09-18
        (CC-4-2's review, M1): one producer for page and fragment, so a Mark
        Paid on the card's view cannot flip the chips until a reload.  The
        transfer cell's direction arrow rides the same resolution: from the
        card's side the payment is INCOMING.

        The card LEG's re-drawn cell is the TRANSFER door's response since
        plan step balance:X-bi-6-1 (a leg has no transaction cell to GET):
        the Mark Paid its cell posts, carrying ``leg_account_id``, returns
        the leg's cell drawn against the same balance line.
        """
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking
            xfer_id = (
                db.session.query(Transfer)
                .filter_by(pay_period_id=paycheck, is_deleted=False).one().id
            )
            page_url = f"http://localhost/grid?periods=1&offset=2&account_id={card}"
            headers = {"HX-Request": "true", "HX-Current-URL": page_url}
            phone = auth_client.get(f"/transactions/{rows['phone']}/cell", headers=headers)
            grocery = auth_client.get(f"/transactions/{rows['grocery']}/cell", headers=headers)
            card_leg = auth_client.post(
                f"/transfers/instance/{xfer_id}/mark-done",
                data={"leg_account_id": str(card)}, headers=headers,
            )
            mobile = auth_client.post(
                f"/transactions/{rows['grocery']}/mark-done",
                data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "1"},
                headers=headers,
            )
            arrow = auth_client.get(f"/transfers/cell/{xfer_id}", headers=headers)
        assert phone.status_code == 200 and grocery.status_code == 200
        assert self._CHIP not in phone.get_data(as_text=True)
        self._assert_chip(grocery.get_data(as_text=True), "Checking")
        assert card_leg.status_code == 200, card_leg.get_data(as_text=True)
        assert f'data-leg-account-id="{card}"' in card_leg.get_data(as_text=True)
        assert self._CHIP not in card_leg.get_data(as_text=True)
        assert mobile.status_code == 200, mobile.get_data(as_text=True)
        self._assert_chip(
            self._card_header(mobile.get_data(as_text=True), "tp", rows["grocery"]),
            "Checking",
        )
        assert arrow.status_code == 200
        assert "bi-arrow-down-left" in arrow.get_data(as_text=True)

    def test_a_current_url_naming_a_refused_or_absent_account_is_the_primary(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The page's own fall-through: a loan, junk, no ``account_id``, a bad URL: checking."""
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking, card, paycheck
            loan = create_loan_account(seed_user, db.session).id
            db.session.commit()
            pairs = [
                (
                    auth_client.get(
                        f"/transactions/{rows['phone']}/cell",
                        headers={"HX-Current-URL": url},
                    ),
                    auth_client.get(
                        f"/transactions/{rows['grocery']}/cell",
                        headers={"HX-Current-URL": url},
                    ),
                )
                for url in (
                    f"http://localhost/grid?account_id={loan}",
                    "http://localhost/grid?account_id=junk",
                    "http://localhost/grid?periods=6",
                    "http://localhost/dashboard",
                    # A URL urlsplit refuses: the render must not 500 after
                    # the commit it follows.
                    "http://[",
                )
            ]
        for phone, grocery in pairs:
            assert phone.status_code == 200 and grocery.status_code == 200
            # Both halves: the card row chipped AND checking's row bare is
            # what says "checking's side" -- a loan on the line would chip
            # the card row too.
            self._assert_chip(phone.get_data(as_text=True), "Rewards Card")
            assert self._CHIP not in grocery.get_data(as_text=True)

    def test_the_mobile_card_and_the_plan_row_carry_it_beside_their_badges(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """This Period's card header and the Plan tab's static row both chip Phone.

        The badges a card preserves on the Plan row (the transfer arrow, the
        CC marks) are what a glance at that tab reads; the chip joins them
        so a card row is told apart there as on This Period.
        """
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking, card, paycheck
            response = auth_client.get("/grid?periods=1&offset=2")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        self._assert_chip(self._card_header(html, "tp", rows["phone"]), "Rewards Card")
        assert self._CHIP not in self._card_header(html, "tp", rows["grocery"])
        self._assert_chip(self._plan_row(html, "Phone on the card"), "Rewards Card")
        assert self._CHIP not in self._plan_row(html, "Grocery")

    def test_the_single_card_fragment_a_mark_paid_swaps_in_carries_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile Mark Paid response is one card, drawn against the owner's line.

        Posts what ``_mobile_card_actions.html`` emits (``render``,
        ``card_prefix``, ``can_edit``); the card that comes back -- the same
        card with a banner on a refused figure (the designed 422), the
        settled one on success -- carries the chip either way, because the
        balance line is resolved by the row's OWNER and not by the outcome.
        """
        with app.app_context():
            checking, card, paycheck, rows = self._world(seed_user, seed_periods_today)
            del checking, card, paycheck
            form = {"render": "mobile_card", "card_prefix": "tp", "can_edit": "1"}
            # A figure the schema refuses: the designed 422 re-renders THIS card
            # with the per-field message in its banner and settles nothing.
            refused = auth_client.post(
                f"/transactions/{rows['phone']}/mark-done",
                data={**form, "settled_amount": "not money"},
            )
            settled = auth_client.post(f"/transactions/{rows['phone']}/mark-done", data=form)
        banner = refused.get_data(as_text=True)
        assert refused.status_code == 422, banner
        assert 'role="alert"' in banner
        self._assert_chip(self._card_header(banner, "tp", rows["phone"]), "Rewards Card")
        body = settled.get_data(as_text=True)
        assert settled.status_code == 200, body
        assert f'id="card-tp-{rows["phone"]}"' in body
        self._assert_chip(self._card_header(body, "tp", rows["phone"]), "Rewards Card")

    def test_the_companion_has_no_balance_line_and_so_no_chip(
        self, app, db, seed_user, seed_periods_today, companion_client,
    ):  # pylint: disable=unused-argument
        """The companion reads the owner's plan across every account: nothing is "other".

        The same card row that chips on the owner's grid renders bare on the
        companion's page, and the page names it, so the negative is not an
        empty page passing by accident.
        """
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            paycheck = seed_periods_today[6]
            one_off_row_of(
                paycheck, name="Phone on the card", amount=Decimal("45.00"),
                user_id=seed_user["user"].id, account_id=card.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                companion_visible=True,
            )
            db.session.commit()
            period_id = paycheck.id
            response = companion_client.get(f"/companion/period/{period_id}")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Phone on the card" in html
        assert self._CHIP not in html

    def test_the_balance_line_is_the_owners_even_if_the_resolver_answered_for_the_requester(
        self, app, db, seed_user, seed_periods_today, seed_companion, companion_client,
        monkeypatch,
    ):  # pylint: disable=unused-argument,too-many-arguments
        """A companion's Mark Paid card is drawn against NO line, by the row's owner.

        ``fragment_balance_line`` answers ``None`` for anyone but the owner by
        the owner's id, not by what the requester holds.  Today a companion
        holds no account -- the account factory refuses a user with no pay
        periods and the account door is owner-gated -- so the rule is
        indistinguishable from that population fact on any real state.  It is
        graded here by making the resolver ANSWER for the requester: with the
        owner test deleted, the companion's card would chip the owner's card
        row against the account the resolver handed back.
        """
        with app.app_context():
            checking = seed_user["account"]
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            phone = one_off_row_of(
                seed_periods_today[4], name="Phone on the card", amount=Decimal("45.00"),
                user_id=seed_user["user"].id, account_id=card.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                companion_visible=True,
            )
            db.session.commit()
            phone_id = phone.id
            # The resolver the fragment producer reads for the OWNER branch
            # (``resolve_owner_and_view`` since plan step credit_card:CC-5-2,
            # one walk for the balance line and the picker): made to answer
            # for anyone, so only the owner test stands between the companion
            # and a chip.
            monkeypatch.setattr(
                "app.routes._render_helpers.resolve_owner_and_view",
                lambda user_id, settings=None, override=None: ResolvedCashFlow(
                    owner=CashFlowSet.single(checking),
                    view=CashFlowSet.single(checking),
                ),
            )
            response = companion_client.post(
                f"/transactions/{phone_id}/mark-done",
                data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "0"},
            )
        body = response.get_data(as_text=True)
        assert response.status_code == 200, body
        assert f'id="card-tp-{phone_id}"' in body
        assert self._CHIP not in body

    def test_an_owner_with_no_card_sees_no_chip_anywhere(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The pre-card grid, byte for byte: no ``flag-chip`` on the page at all."""
        with app.app_context():
            one_off_row_of(
                seed_periods_today[6], name="Grocery", amount=Decimal("500.00"),
                user_id=seed_user["user"].id, account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            )
            db.session.commit()
            response = auth_client.get("/grid?periods=1&offset=2")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Grocery" in html
        # Scoped to the three surfaces that draw rows -- the desktop table, the
        # This Period pane and the Plan accordion -- so page chrome elsewhere
        # cannot fail this for an unrelated reason.
        table = html[html.index('class="table table-bordered table-sm grid-table'):]
        assert self._CHIP not in table[:table.index("</table>")]
        mobile = html[html.index('id="mobile-this-period"'):html.index('id="addTransactionModal"')]
        assert self._CHIP not in mobile
        assert "Grocery" in mobile[mobile.index('id="plan-accordion"'):]

    def test_a_render_without_the_balance_line_FAILS(self, app):
        """``account`` is a REQUIRED key of the cell partial; ``None`` is a value, absence is not.

        The twin of the ``due_captions`` contract test one file over: a
        surface that forgets to publish the balance line must raise, not draw
        a row with no chip and nothing on screen to say a decision was
        skipped.  ``is not none`` is what makes the difference -- a truth
        test would read Jinja's ``Undefined`` as "no balance line" in silence.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

        import jinja2  # pylint: disable=import-outside-toplevel

        txn = SimpleNamespace(
            id=1, name="Rent", settled_amount=None, account_id=7,
            estimated_amount=Decimal("1200.00"), due_date=None,
            status=SimpleNamespace(is_settled=False, name="Projected"),
            status_id=99, transfer_id=None, credit_payback_for_id=None,
            is_expense=True, tracks_purchases=False, notes=None,
        )
        template = app.jinja_env.get_template("grid/_transaction_cell.html")
        with app.test_request_context("/"):
            with pytest.raises(jinja2.exceptions.UndefinedError, match="account"):
                template.render(
                    txn=txn, budgets={1: Decimal("1200.00")},
                    settled={1: None}, retained={1: None}, due_captions={1: None},
                )


class TestTheCreateFormsOfferTheSet:
    """Plan step credit_card:CC-4-2: the account picker on the two full create forms.

    Ruling ``credit_card:R-CC16``: "the create popover takes an account
    (default: the balance line's)".  The full-create popover and the Add
    Transaction modal render a ``<select name="account_id">`` over the set's
    members -- checking and its cards -- with the balance line's selected,
    and the hidden input they always carried when the set is one account, so
    the pre-card forms are byte-identical.  The quick-create keeps its hidden
    default: it is the one-keystroke path.  The create doors gain no refusal:
    a foreign account was 404 and a loan 422 before this step and still are.
    """

    _SELECT = 'name="account_id" class="form-select'
    _HIDDEN = 'type="hidden" name="account_id"'

    @staticmethod
    def _full_create(client, seed_user, paycheck, account_id):
        return client.get(
            "/transactions/new/full",
            query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": paycheck.id,
                "account_id": account_id,
                "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            },
        )

    @staticmethod
    def _modal(html):
        """Return the Add Transaction modal's markup, to its closing form."""
        start = html.index('id="addTransactionModal"')
        return html[start:html.index("</form>", start)]

    @staticmethod
    def _options(html):
        """Return ``[(value, selected)]`` for the account select's options."""
        select = html[html.index('id="create_account_id"') if 'id="create_account_id"' in html
                      else html.index('id="add_txn_account"'):]
        select = select[:select.index("</select>")]
        return [
            (int(value), "selected" in rest)
            for value, rest in re.findall(r'<option value="(\d+)"([^>]*)>', select)
        ]

    def test_the_full_create_offers_the_set_with_the_balance_line_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Opened from checking's grid: both members, checking selected, no hidden input."""
        with app.app_context():
            checking = seed_user["account"].id
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            ).id
            db.session.commit()
            from_checking = self._full_create(
                auth_client, seed_user, seed_periods_today[6], checking,
            )
            from_card = self._full_create(
                auth_client, seed_user, seed_periods_today[6], card,
            )
        assert from_checking.status_code == 200 and from_card.status_code == 200
        html = from_checking.get_data(as_text=True)
        assert self._SELECT in html
        assert self._HIDDEN not in html
        assert self._options(html) == [(checking, True), (card, False)]
        assert ">Rewards Card</option>" in html
        # Opened from the card's grid: the same two members, the card selected.
        assert self._options(from_card.get_data(as_text=True)) == [
            (checking, False), (card, True),
        ]

    def test_the_full_create_keeps_the_hidden_input_for_a_set_of_one(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No card: the hidden input naming checking, and no select at all."""
        with app.app_context():
            checking = seed_user["account"].id
            response = self._full_create(
                auth_client, seed_user, seed_periods_today[6], checking,
            )
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f'{self._HIDDEN} value="{checking}"' in html
        assert self._SELECT not in html

    def test_a_savings_cell_is_its_own_set_of_one_even_beside_a_card(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An account outside the set heads a set of one: hidden input, no picker."""
        with app.app_context():
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            ).id
            db.session.commit()
            response = self._full_create(
                auth_client, seed_user, seed_periods_today[6], savings,
            )
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f'{self._HIDDEN} value="{savings}"' in html
        assert self._SELECT not in html

    def test_a_cell_the_grid_resolver_refuses_carries_its_own_id_for_the_door_to_refuse(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan on the URL: the hidden loan id, no picker, and the create door's 422.

        Developer ruling 2026-09-18 (CC-4-2's review, L1): a WRITE form never
        re-targets silently, so the popover carries the account it was
        opened with as a set of one and the create door refuses it exactly
        as it refuses the quick-create's hidden input.  A first draft fell
        through to the primary's set the way the grid page does for the same
        crafted URL; only a crafted URL reaches this.
        """
        with app.app_context():
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            loan = create_loan_account(seed_user, db.session).id
            db.session.commit()
            response = self._full_create(
                auth_client, seed_user, seed_periods_today[6], loan,
            )
            refused = auth_client.post("/transactions/inline", data={
                "account_id": loan,
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": seed_periods_today[6].id,
                "scenario_id": seed_user["scenario"].id,
                "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                "estimated_amount": "45.00", "notes": "",
            })
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f'{self._HIDDEN} value="{loan}"' in html
        assert self._SELECT not in html
        assert refused.status_code == 422, refused.get_data(as_text=True)

    def test_the_quick_create_keeps_its_hidden_default(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The one-keystroke path offers no picker even when a card exists."""
        with app.app_context():
            checking = seed_user["account"].id
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            response = auth_client.get(
                "/transactions/new/quick",
                query_string={
                    "category_id": seed_user["categories"]["Groceries"].id,
                    "period_id": seed_periods_today[6].id,
                    "account_id": checking,
                },
            )
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f'{self._HIDDEN} value="{checking}"' in html
        assert self._SELECT not in html

    def test_the_add_transaction_modal_offers_the_set(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``/grid``'s modal: the picker with a card, the hidden input without."""
        with app.app_context():
            checking = seed_user["account"].id
            before = auth_client.get("/grid").get_data(as_text=True)
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            ).id
            db.session.commit()
            after = auth_client.get("/grid").get_data(as_text=True)
        modal_before = self._modal(before)
        modal_after = self._modal(after)
        assert f'{self._HIDDEN} value="{checking}"' in modal_before
        assert 'id="add_txn_account"' not in modal_before
        assert 'id="add_txn_account"' in modal_after
        assert self._HIDDEN not in modal_after
        assert self._options(modal_after) == [(checking, True), (card, False)]

    def test_creating_on_the_card_returns_a_chipped_cell_and_lands_the_row_there(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Both create doors: the posted member is the row's account; the cell says so.

        Posts what each form emits.  The inline door answers the cell the
        popover's ``hx-target`` swaps in; the modal's door answers the same
        cell for its ``hx-swap="none"`` -- and both are drawn against
        checking's line, so a row created on the card comes back chipped.
        """
        with app.app_context():
            checking = seed_user["account"].id
            scenario = seed_user["scenario"].id
            category = seed_user["categories"]["Groceries"].id
            paycheck = seed_periods_today[6].id
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            ).id
            db.session.commit()
            inline = auth_client.post("/transactions/inline", data={
                "account_id": card, "category_id": category,
                "pay_period_id": paycheck, "scenario_id": scenario,
                "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                "estimated_amount": "45.00", "notes": "",
            })
            modal = auth_client.post("/transactions", data={
                "scenario_id": scenario, "account_id": card, "name": "Phone",
                "estimated_amount": "45.00",
                "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                "category_id": category, "pay_period_id": paycheck,
            })
            on_checking = auth_client.post("/transactions/inline", data={
                "account_id": checking, "category_id": category,
                "pay_period_id": paycheck, "scenario_id": scenario,
                "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                "estimated_amount": "500.00", "notes": "",
            })
            assert inline.status_code == 201, inline.get_data(as_text=True)
            assert modal.status_code == 201, modal.get_data(as_text=True)
            assert on_checking.status_code == 201, on_checking.get_data(as_text=True)
            by_account = {}
            for row in db.session.query(Transaction).filter_by(
                pay_period_id=paycheck, is_deleted=False,
            ):
                by_account.setdefault(row.account_id, []).append(row.name)
        # The two card posts landed on the card, the checking post on checking
        # (the inline door names a row after its category's display name).
        assert sorted(by_account[card]) == ["Family: Groceries", "Phone"]
        assert by_account[checking] == ["Family: Groceries"]
        assert set(by_account) == {card, checking}
        chip = TestTheChipMarksARowOnAnotherAccount._CHIP
        assert chip in inline.get_data(as_text=True)
        assert 'title="On Rewards Card"' in inline.get_data(as_text=True)
        assert chip in modal.get_data(as_text=True)
        assert chip not in on_checking.get_data(as_text=True)
