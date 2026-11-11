"""
Shekel Budget App -- C and the palette press Credit at once, or open the popover (plan task plan#3114)

The C key and the command palette's Credit post no card, no day and no bank
line, so the door reads the row's own day and its one card (plan task
plan#3056), and a press naming no line may free none (ruling R-CC127).  They
press at once only where that is the press the popover would make: one card,
and no bank line a match names that the press would free.  With no card the
popover asks for one, with two or more it asks which, and where Credit would
unmatch a bank line it names the line first (ruling plan#3112, Josh
2026-11-09: "Open the popover"); there they open the popover instead.

The cell says which, as the route decided it: ``data-can-credit`` where Credit
is offered (``transaction_service.credit_offered``, the popover's own
predicate), and ``data-credit-opens-popover`` beside it where they open the
popover.  The JS (``app.js``'s ``creditFromShortcut``) reads the two stamps
and decides nothing.  Both surfaces that draw the cell publish the answer
from one producer (``transaction_service.credit_opens_the_popover_by_key``):
the grid page, and the cell fragment a click swaps in.

**The example**: $800 of groceries dated today in today's paycheck, on
checking.  The card's books open before any fixture day
(``tests._cards.add_card``).
"""

from types import SimpleNamespace
from decimal import Decimal

import jinja2
import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.movement_account import owner_accounts
from app.services.grid_view_service import leg_dom_id
from app.services.transfer_legs import cell_key, leg_of
from app.utils.dates import display_today
from tests._cards import add_card, add_savings
from tests._matched_payment import matched_and_reverted
from tests._test_helpers import (
    add_txn,
    capture_sql_statements,
    create_transfer,
    generate_row_of,
    make_expense_template,
)

_EIGHT_HUNDRED = Decimal("800.00")
# The paycheck ``seed_periods_today`` puts today in.
_TODAY_PERIOD = 4
_CAN_CREDIT = 'data-can-credit="1"'
_OPENS_POPOVER = 'data-credit-opens-popover="1"'


def _groceries(seed_user, periods, *, account=None):
    """The $800.00 Groceries row in today's paycheck, dated today, committed."""
    row = add_txn(
        db.session, seed_user, periods[_TODAY_PERIOD], "Groceries", _EIGHT_HUNDRED,
        due_date=display_today(), account=account,
    )
    db.session.commit()
    return row


def _span(html, dom_id):
    """The opening tag of the cell's ``.txn-open`` span whose ``data-cell`` is *dom_id*.

    The span the C key and the palette read their stamps off.  It must be
    drawn exactly once on the page.
    """
    marker = f'class="txn-open"\n        data-cell="{dom_id}"'
    assert html.count(marker) == 1, f"the page draws {html.count(marker)} spans for {dom_id}"
    start = html.index(marker)
    return html[start:html.index(">", start)]


def _cell_span(auth_client, row_id):
    """The row's ``.txn-open`` span in its cell fragment, as a click swaps it in."""
    response = auth_client.get(f"/transactions/{row_id}/cell")
    assert response.status_code == 200
    return _span(response.get_data(as_text=True), f"txn-cell-{row_id}")


def _grid_span(auth_client, dom_id):
    """The ``.txn-open`` span *dom_id* names on the grid page."""
    response = auth_client.get("/grid")
    assert response.status_code == 200
    return _span(response.get_data(as_text=True), dom_id)


def _press_as_the_c_key_does(auth_client, row_id):
    """POST the Credit press the C key and the palette make: no card, no day, no line."""
    return auth_client.post(f"/transactions/{row_id}/mark-credit")


def _payment(row_id):
    """The row read fresh, and its one covering movement."""
    db.session.expire_all()
    row = db.session.get(Transaction, row_id)
    return row, status_seam.covering_movement_of(row)


class TestTheCellSaysPressOrOpen:
    """The cell fragment's two stamps, and the press each one stands for."""

    def test_one_card_presses_at_once(self, auth_client, seed_user, seed_periods_today):
        """The Visa alone: Credit offered and no popover; the C key's press puts the $800.00 on it.

        200, and the row's payment is the $800.00 on the Visa from today, the
        row's own day: the press the popover would make with its day and card
        untouched.
        """
        visa = add_card(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today)

        span = _cell_span(auth_client, row.id)

        assert _CAN_CREDIT in span
        assert "data-credit-opens-popover" not in span

        response = _press_as_the_c_key_does(auth_client, row.id)

        assert response.status_code == 200
        _row, movement = _payment(row.id)
        assert (movement.account_id, movement.amount, movement.settled_on) == (
            visa.id, _EIGHT_HUNDRED, display_today(),
        )

    def test_two_cards_open_the_popover(self, auth_client, seed_user, seed_periods_today):
        """The Visa and the Amex: the popover opens, since a press naming none is refused.

        The refusal is what the popover is opened to spare him: 400, "pick the
        one Groceries went on", and nothing on either card.
        """
        add_card(seed_user)
        add_card(seed_user, name="Amex")
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today)

        span = _cell_span(auth_client, row.id)

        assert _CAN_CREDIT in span
        assert _OPENS_POPOVER in span

        response = _press_as_the_c_key_does(auth_client, row.id)

        assert response.status_code == 400
        assert "You have 2 credit cards: pick the one Groceries went on." in (
            response.get_data(as_text=True)
        )
        assert _payment(row.id)[1] is None

    def test_no_card_opens_the_popover(self, auth_client, seed_user, seed_periods_today):
        """No card in the app: the popover opens, where Credit asks for one to be added."""
        row = _groceries(seed_user, seed_periods_today)

        span = _cell_span(auth_client, row.id)

        assert _CAN_CREDIT in span
        assert _OPENS_POPOVER in span

        response = _press_as_the_c_key_does(auth_client, row.id)

        assert response.status_code == 400
        assert "there is no credit card in the app yet" in response.get_data(as_text=True)
        assert _payment(row.id)[1] is None

    def test_a_matched_line_opens_the_popover(self, auth_client, seed_user):
        """$120 Hotel Paid, matched to HOTEL -$120.00, set back: one card, yet the popover opens.

        Credit would move the kept payment onto the Visa and unmatch the line
        (ruling plan#3112's example), which the popover names beside Credit
        before the press and the C key could not.
        """
        txn, _line, _visa = matched_and_reverted(seed_user)

        span = _cell_span(auth_client, txn.id)

        assert _CAN_CREDIT in span
        assert _OPENS_POPOVER in span

    def test_a_kept_payment_no_match_names_presses_at_once(self, auth_client, seed_user, seed_periods_today):
        """$800 Paid on checking and set back, never matched: the Visa alone, so it presses.

        Credit moves the kept payment onto the Visa (``credit_moves_payment``
        is true), but no match names it, so the press frees no line: 200, the
        $800.00 on the Visa.
        """
        visa = add_card(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today)
        transaction_service.settle_transaction(row)
        db.session.commit()
        transaction_service.apply_requested_status(
            row, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
        assert _payment(row.id)[1].account_id == seed_user["account"].id, (
            "precondition: the payment kept on checking"
        )

        span = _cell_span(auth_client, row.id)

        assert _CAN_CREDIT in span
        assert "data-credit-opens-popover" not in span

        response = _press_as_the_c_key_does(auth_client, row.id)

        assert response.status_code == 200
        _row, movement = _payment(row.id)
        assert (movement.account_id, movement.amount) == (visa.id, _EIGHT_HUNDRED)

    def test_a_bill_on_a_card_presses_onto_the_other_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Two cards, the bill living on the Amex: one other card, so it presses onto the Visa.

        Ruling plan#3225: Paid with the Amex picked records that it went on
        the Amex, so Credit on it means another card.
        """
        visa = add_card(seed_user)
        amex = add_card(seed_user, name="Amex")
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today, account=amex)

        span = _cell_span(auth_client, row.id)

        assert _CAN_CREDIT in span
        assert "data-credit-opens-popover" not in span

        response = _press_as_the_c_key_does(auth_client, row.id)

        assert response.status_code == 200
        assert _payment(row.id)[1].account_id == visa.id

    def test_a_bill_on_the_only_card_offers_no_credit(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The Visa alone, the bill living on it: no other card, so neither stamp is drawn."""
        visa = add_card(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today, account=visa)

        span = _cell_span(auth_client, row.id)

        assert "data-can-credit" not in span
        assert "data-credit-opens-popover" not in span


    def test_a_zero_row_presses_and_the_door_says_why(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """$0.00 Groceries, the Visa alone: it presses, as the ruling says, and is refused in words.

        Ruling plan#3112 opens the popover for no card, two or more, or a bank
        line to free, and presses "on every other row".  The door refuses a
        press that would put $0.00 on the card (plan task plan#3056), 400
        with the sentence the popover prints beside its disabled Credit, and
        nothing is written.
        """
        add_card(seed_user)
        db.session.commit()
        row = add_txn(
            db.session, seed_user, seed_periods_today[_TODAY_PERIOD], "Groceries",
            Decimal("0.00"), due_date=display_today(),
        )
        db.session.commit()

        span = _cell_span(auth_client, row.id)

        assert _CAN_CREDIT in span
        assert "data-credit-opens-popover" not in span

        response = _press_as_the_c_key_does(auth_client, row.id)

        assert response.status_code == 400
        assert "Groceries is $0.00, so there is nothing to put on the card" in (
            response.get_data(as_text=True)
        )
        row, movement = _payment(row.id)
        assert movement is None
        assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

class TestNoCreditWhereNoneIsOffered:
    """Rows the popover offers no Credit on carry neither stamp, so C and the palette skip them."""

    def test_a_paid_row(self, auth_client, seed_user, seed_periods_today):
        """Paid on checking, the Visa in the app: neither stamp."""
        add_card(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today)
        transaction_service.settle_transaction(row)
        db.session.commit()

        span = _cell_span(auth_client, row.id)

        assert "data-can-credit" not in span
        assert "data-credit-opens-popover" not in span

    def test_an_income_row(self, auth_client, seed_user, seed_periods_today):
        """A $2,000.00 paycheck, the Visa in the app: neither stamp."""
        add_card(seed_user)
        db.session.commit()
        row = add_txn(
            db.session, seed_user, seed_periods_today[_TODAY_PERIOD], "Paycheck",
            Decimal("2000.00"), is_income=True, due_date=display_today(),
        )
        db.session.commit()

        span = _cell_span(auth_client, row.id)

        assert "data-can-credit" not in span
        assert "data-credit-opens-popover" not in span

    def test_an_envelope(self, auth_client, seed_user, seed_periods_today):
        """A $500.00 Groceries envelope tracking its purchases: neither stamp, and its own."""
        add_card(seed_user)
        db.session.commit()
        template = make_expense_template(
            db.session, seed_user, amount=Decimal("500.00"), name="Groceries",
            category_key="Groceries", is_envelope=True,
        )
        row = generate_row_of(template, seed_periods_today[_TODAY_PERIOD])
        db.session.commit()

        span = _cell_span(auth_client, row.id)

        assert "data-can-credit" not in span
        assert "data-credit-opens-popover" not in span
        assert 'data-envelope="1"' in span


class TestTheGridPageSaysTheSame:
    """The grid page stamps its cells from the producer the fragment asks."""

    def test_one_card_the_page_presses(self, auth_client, seed_user, seed_periods_today):
        """The Visa alone: the page's cell offers Credit and opens no popover."""
        add_card(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today)

        span = _grid_span(auth_client, f"txn-cell-{row.id}")

        assert _CAN_CREDIT in span
        assert "data-credit-opens-popover" not in span

    def test_two_cards_the_page_opens_the_popover(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The Visa and the Amex: the page's cell carries both stamps."""
        add_card(seed_user)
        add_card(seed_user, name="Amex")
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today)

        span = _grid_span(auth_client, f"txn-cell-{row.id}")

        assert _CAN_CREDIT in span
        assert _OPENS_POPOVER in span

    def test_a_transfer_side_offers_no_credit(self, auth_client, seed_user, seed_periods_today):
        """$200 from checking to Savings in today's paycheck: checking's side carries neither stamp."""
        add_card(seed_user)
        savings = add_savings(seed_user)
        db.session.commit()
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods_today[_TODAY_PERIOD], amount=Decimal("200.00"),
        )
        db.session.commit()

        span = _grid_span(auth_client, leg_dom_id(xfer.id, seed_user["account"].id))

        assert "data-can-credit" not in span
        assert "data-credit-opens-popover" not in span


    def test_an_account_override_reads_the_owner_s_cards(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """``/grid?account_id=<Savings>``, a row on Savings, the Visa alone: it presses.

        An override naming an account outside the cash-flow set narrows the
        page to that account, but the cards a Credit press may mean are the
        OWNER's (``credit_cards`` over the set with no override), so the
        row's one card is the Visa and the press is made at once.  Read off
        the page's own view, the row would have no card and open the popover.
        """
        add_card(seed_user)
        savings = add_savings(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods_today, account=savings)

        response = auth_client.get(f"/grid?account_id={savings.id}")

        assert response.status_code == 200
        span = _span(response.get_data(as_text=True), f"txn-cell-{row.id}")
        assert _CAN_CREDIT in span
        assert "data-credit-opens-popover" not in span

class TestTheMapIsRequired:
    """``credit_opens_popover`` is a REQUIRED map of the cell partial, as ``due_captions`` is."""

    def test_a_render_without_the_map_FAILS(self, app):
        """``credit_opens_popover[key]`` on an absent map raises ``UndefinedError``.

        A surface that forgot to publish it must raise rather than stamp no
        Credit on every cell, which would read as "Credit is not offered" with
        nothing on screen to say a decision was skipped.  Rendered directly,
        as the other required keys' tests are: every route that draws the
        cell publishes the map, so no route can reach this state.
        """
        txn = SimpleNamespace(
            id=1, name="Rent", settled_amount=None,
            estimated_amount=Decimal("1200.00"), due_date=None,
            status=SimpleNamespace(is_settled=False, name="Projected"),
            status_id=99, transfer_id=None, credit_payback_for_id=None,
            is_expense=True, tracks_purchases=False, notes=None,
        )
        template = app.jinja_env.get_template("grid/_transaction_cell.html")
        with app.test_request_context("/"):
            with pytest.raises(
                jinja2.exceptions.UndefinedError, match="credit_opens_popover",
            ):
                template.render(
                    txn=txn, budgets={1: Decimal("1200.00")},
                    settled={1: None}, retained={1: None},
                )


class TestTheMapIsReadOnceForThePage:
    """``credit_opens_the_popover_by_key`` over mixed rows, and its one read of the matches."""

    @staticmethod
    def _kept(seed_user, name):
        """*name*, $800.00, Paid on checking and set back to Projected, matched to nothing."""
        row = add_txn(
            db.session, seed_user, seed_user["bootstrap_period"], name, _EIGHT_HUNDRED,
        )
        db.session.commit()
        transaction_service.settle_transaction(row)
        db.session.commit()
        transaction_service.apply_requested_status(
            row, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
        return row

    def test_each_row_shape_answers_its_own(self, seed_user):
        """Matched Hotel opens; a kept payment matched to nothing and a plain row press; a leg is none.

        The Visa alone (``matched_and_reverted`` adds it).
        """
        hotel, _line, _visa = matched_and_reverted(seed_user)
        kept = self._kept(seed_user, "Groceries")
        plain = add_txn(
            db.session, seed_user, seed_user["bootstrap_period"], "Coffee", Decimal("4.00"),
        )
        savings = add_savings(seed_user)
        db.session.commit()
        xfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"],
        )
        db.session.commit()
        leg = leg_of(xfer, seed_user["account"].id)

        answers = transaction_service.credit_opens_the_popover_by_key(
            [hotel, kept, plain, leg],
            owner_accounts(seed_user["user"].id),
        )

        assert answers == {
            hotel.id: True, kept.id: False, plain.id: False, cell_key(leg): None,
        }

    def test_the_matches_are_read_once_however_many_rows_need_them(self, seed_user):
        """One kept payment or three, the statements the map issues are the same count.

        Measured before the batch (the review of this task): one
        ``statement_match_members`` read per such row.  A row needing no
        withdrawal adds none, so a map over plain rows reads no match.
        """
        hotel, _line, _visa = matched_and_reverted(seed_user)
        kept = [self._kept(seed_user, name) for name in ("Groceries", "Gas")]
        plain = add_txn(
            db.session, seed_user, seed_user["bootstrap_period"], "Coffee", Decimal("4.00"),
        )
        db.session.commit()
        owner_set = owner_accounts(seed_user["user"].id)
        rows = [hotel, *kept, plain]
        # Warm the identity map: what is under count is the match read.
        transaction_service.credit_opens_the_popover_by_key(rows, owner_set)

        _one, one = capture_sql_statements(
            lambda: transaction_service.credit_opens_the_popover_by_key(
                [hotel], owner_set,
            ),
        )
        _three, three = capture_sql_statements(
            lambda: transaction_service.credit_opens_the_popover_by_key(
                rows, owner_set,
            ),
        )
        _none, none = capture_sql_statements(
            lambda: transaction_service.credit_opens_the_popover_by_key(
                [plain], owner_set,
            ),
        )

        assert one, "precondition: a kept payment's matches are read"
        assert len(three) == len(one)
        assert not [text for text, _params in none if "statement_match" in text]
