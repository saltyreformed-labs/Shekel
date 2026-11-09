"""
Shekel Budget App -- the Credit press says its day and asks which card (plan task plan#3056)

The popover's Credit press shows the day it puts the row on the card and lets
the owner type the real one, using a "Money moved on" box; with no card in the
app it asks him to add one, and with two or more it asks which.  The door reads
what the press posts (``CreditPressSchema``): the day, the card, and the bank
lines its caption named.  A press posting none of them -- the C key and the
palette's -- reads the row's own day and its one card.

**The example**: $800 of groceries dated Jan 3 in the Jan 2 paycheck, on a Visa
closing on the 5th and due on the 27th.  Each figure is the row's own.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.statement_match import matched_subjects
from app.utils.dates import display_today
from tests._cards import add_card
from tests._matched_payment import matched_and_reverted
from tests._test_helpers import add_txn
from tests.test_services.test_card_payment_price import _card

_EIGHT_HUNDRED = Decimal("800.00")
_JAN_3 = date(2026, 1, 3)
_JAN_5 = date(2026, 1, 5)


def _groceries(seed_user, period):
    """The $800 Groceries row in *period*, dated Jan 3, committed."""
    row = add_txn(
        db.session, seed_user, period, "Groceries", _EIGHT_HUNDRED, due_date=_JAN_3,
    )
    db.session.commit()
    return row


def _popover(auth_client, row_id):
    """The row's full-edit popover, as the grid opens it."""
    response = auth_client.get(f"/transactions/{row_id}/full-edit")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _block(html, row_id):
    """The Credit press's block, up to the popover's form, which must be drawn."""
    marker = f'id="credit-press-{row_id}"'
    assert marker in html, "the popover draws no Credit press block"
    block = html[html.index(marker):]
    return block[:block.index("<form")]


def _says(html, row_id):
    """What the block's sentence says, whitespace folded."""
    block = _block(html, row_id)
    start = block.index(">", block.index('class="form-text"')) + 1
    return " ".join(block[start:block.index("</div>", start)].split())


def _credit_button(html):
    """The Credit button's opening tag, up to its label."""
    end = html.index('aria-label="Mark as credit card"')
    return html[html.rindex("<button", 0, end):end]


def _read(row_id):
    """The row read fresh, and its one covering movement."""
    db.session.expire_all()
    row = db.session.get(Transaction, row_id)
    return row, status_seam.covering_movement_of(row)


class TestWhatThePressPosts:
    """The day and the card the press posts are the ones the row goes on the card with."""

    def test_a_typed_day_is_the_day(self, auth_client, seed_user, seed_periods):
        """Jan 5 typed over Jan 3: 200, the $800.00 on the Visa from Jan 5."""
        visa = _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(
            f"/transactions/{row.id}/mark-credit",
            data={"settled_on": "2026-01-05", "shown_lines": ""},
        )

        assert response.status_code == 200
        row, movement = _read(row.id)
        assert row.settled_on == _JAN_5
        assert (movement.account_id, movement.settled_on, movement.amount) == (
            visa.id, _JAN_5, _EIGHT_HUNDRED,
        )

    def test_an_empty_box_is_the_row_s_own_day(self, auth_client, seed_user, seed_periods):
        """The box cleared before the press: the row's own Jan 3, not a cleared day."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(
            f"/transactions/{row.id}/mark-credit",
            data={"settled_on": "", "shown_lines": ""},
        )

        assert response.status_code == 200
        assert _read(row.id)[0].settled_on == _JAN_3

    def test_the_card_picked_is_the_card(self, auth_client, seed_user, seed_periods):
        """The Visa and the Amex, the Amex picked: the $800.00 is on the Amex."""
        _card(seed_user)
        amex = add_card(seed_user, name="Amex")
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(
            f"/transactions/{row.id}/mark-credit",
            data={"card_account_id": str(amex.id), "shown_lines": ""},
        )

        assert response.status_code == 200
        assert _read(row.id)[1].account_id == amex.id

    def test_a_malformed_day_is_refused_before_anything_moves(
        self, auth_client, seed_user, seed_periods,
    ):
        """A day that is not a date: 422 with the field's message, the row still Projected."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(
            f"/transactions/{row.id}/mark-credit", data={"settled_on": "Jan 5th"},
        )

        assert response.status_code == 422
        assert "Not a valid date" in response.get_data(as_text=True)
        row, movement = _read(row.id)
        assert movement is None
        assert row.settled_on is None

    def test_another_owner_s_card_is_not_found(
        self, auth_client, seed_user, seed_second_user, seed_periods,
    ):
        """Another owner's card named: 404, one answer for "not found" and "not yours"."""
        _card(seed_user)
        theirs = add_card(seed_second_user, name="Theirs")
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])

        response = auth_client.post(
            f"/transactions/{row.id}/mark-credit",
            data={"card_account_id": str(theirs.id)},
        )

        assert response.status_code == 404
        assert _read(row.id)[1] is None


class TestThePopoverSaysItsDayAndCard:
    """What the popover shows beside Credit, and what its press posts."""

    def test_one_card_is_named_and_the_day_is_shown(self, auth_client, seed_user, seed_periods):
        """The Visa alone: Jan 3 in the box, "Credit puts this on Visa", no picker."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])

        html = _popover(auth_client, row.id)

        block = _block(html, row.id)
        assert f'id="credit-on-{row.id}"' in block
        assert 'name="settled_on"' in block and 'value="2026-01-03"' in block
        assert f'id="credit-card-{row.id}"' not in block
        assert _says(html, row.id) == "Credit puts this on Visa on the day shown."
        button = _credit_button(html)
        assert f'hx-include="#credit-press-{row.id}"' in button
        assert " disabled " not in button

    def test_two_cards_ask_which(self, auth_client, seed_user, seed_periods):
        """The Visa and the Amex: a picker with neither chosen, "the card you pick"."""
        visa = _card(seed_user)
        amex = add_card(seed_user, name="Amex")
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])

        html = _popover(auth_client, row.id)

        block = _block(html, row.id)
        picker = block[block.index(f'id="credit-card-{row.id}"'):block.index("</select>")]
        assert 'name="card_account_id"' in block
        assert '<option value="" selected>Pick one</option>' in picker
        assert f'<option value="{visa.id}">Visa</option>' in picker
        assert f'<option value="{amex.id}">Amex</option>' in picker
        assert _says(html, row.id) == "Credit puts this on the card you pick on the day shown."

    def test_no_card_asks_to_add_one(self, auth_client, seed_user, seed_periods):
        """No card in the app: Credit is drawn disabled beside a link to add one."""
        row = _groceries(seed_user, seed_periods[0])

        html = _popover(auth_client, row.id)

        block = _block(html, row.id)
        assert 'name="settled_on"' not in block
        assert "there is no card in the app to put it on" in block
        assert 'href="/accounts/new"' in block
        button = _credit_button(html)
        assert f'disabled aria-describedby="credit-press-{row.id}"' in button
        assert "hx-include" not in button

    def test_an_undated_row_shows_its_paycheck_s_first_day(
        self, auth_client, seed_user, seed_periods,
    ):
        """No date, in Jan 16's paycheck: the box shows Jan 16, or today if that has not come."""
        _card(seed_user)
        row = add_txn(
            db.session, seed_user, seed_periods[1], "Groceries", _EIGHT_HUNDRED,
        )
        db.session.commit()

        html = _popover(auth_client, row.id)

        shown = min(date(2026, 1, 16), display_today())
        assert f'value="{shown.isoformat()}"' in _block(html, row.id)

    def test_a_zero_row_says_there_is_nothing_to_put_on(
        self, auth_client, seed_user, seed_periods,
    ):
        """$0.00 Groceries: Credit disabled beside the sentence; a press anyway is refused.

        The 400 names why, and the row read back is still Projected with no
        payment: the door rolled the settle back.
        """
        _card(seed_user)
        row = add_txn(
            db.session, seed_user, seed_periods[0], "Groceries", Decimal("0.00"),
            due_date=_JAN_3,
        )
        db.session.commit()

        html = _popover(auth_client, row.id)

        block = _block(html, row.id)
        assert "Groceries is $0.00, so there is nothing to put on the card" in block
        assert 'name="settled_on"' not in block
        assert f'disabled aria-describedby="credit-press-{row.id}"' in _credit_button(html)

        response = auth_client.post(f"/transactions/{row.id}/mark-credit")

        assert response.status_code == 400
        assert "nothing to put on the card" in response.get_data(as_text=True)
        row, movement = _read(row.id)
        assert movement is None
        assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)

    def test_a_settled_row_draws_no_credit_press(self, auth_client, seed_user, seed_periods):
        """Paid on checking: no Credit, so no day and no card beside it."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])
        transaction_service.settle_transaction(row)
        db.session.commit()

        html = _popover(auth_client, row.id)

        assert f'id="credit-press-{row.id}"' not in html


class TestAMatchedLineIsNamed:
    """A reverted row's kept payment, matched on checking: Credit names the line and frees it."""

    def test_the_caption_names_the_line_and_the_press_frees_it(
        self, auth_client, seed_user,
    ):
        """The caption names HOTEL -$120.00; the press posting what it drew moves it.

        Posted exactly as the template emits it: Credit's ``shown_lines`` and
        the day in its box.  200, the $120.00 on the Visa from that day, and
        the line no longer matched on checking.
        """
        txn, line, visa = matched_and_reverted(seed_user)

        html = _popover(auth_client, txn.id)

        says = _says(html, txn.id)
        assert says.startswith(
            "Credit puts this on Visa on the day shown and withdraws 1 accepted match",
        )
        assert "HOTEL -$120.00" in says
        assert f'"shown_lines": "{line.id}"' in _credit_button(html)
        block = _block(html, txn.id)
        day = block[block.index('value="', block.index('name="settled_on"')) + len('value="'):]
        day = day[:day.index('"')]

        response = auth_client.post(
            f"/transactions/{txn.id}/mark-credit",
            data={"shown_lines": str(line.id), "settled_on": day},
        )

        assert response.status_code == 200
        row, movement = _read(txn.id)
        assert (movement.account_id, movement.amount) == (visa.id, Decimal("120.00"))
        assert row.settled_on.isoformat() == day
        assert not matched_subjects(seed_user["account"].id).lines

    def test_with_no_card_nothing_is_named_for_credit(self, auth_client, seed_user):
        """The same matched row and no card: Credit cannot press, so it names no line.

        Its block says no line, and its disabled button posts nothing.  The
        lines the card's other captions name (a $0.00 estimate saved as Paid
        frees this one) are theirs, unchanged.
        """
        txn, _line, _visa = matched_and_reverted(seed_user, card=False)

        html = _popover(auth_client, txn.id)

        assert "accepted match" not in _block(html, txn.id)
        assert "hx-vals" not in _credit_button(html)
