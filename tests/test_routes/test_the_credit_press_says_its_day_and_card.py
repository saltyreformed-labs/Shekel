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

from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam
from tests._cards import add_card
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
