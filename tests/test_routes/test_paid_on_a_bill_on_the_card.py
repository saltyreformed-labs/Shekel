"""
Shekel Budget App -- every Paid door dates a bill on the card like Credit (plan task plan#3234)

Ruling **plan#3225**: on a bill that lives on the card, Paid with its card
picked records the day ``transaction_service.credit_day`` gives -- the bill's
date, or today while that date is still ahead -- at every Paid door: the
popover's Paid, the cell's check (which the keyboard and the palette click),
the phone's Mark Paid, the Status dropdown and the companion page.  Paid with
checking picked is dated today, as any checking row is.  And Credit there
offers only another card: none on the owner's only card, the other one beside
a second.

**The example**: $800.00 of groceries dated Jan 3 in the Jan 2 paycheck,
living on the Visa, whose books open before any fixture day
(``tests._cards.add_card``).  The suite runs after Jan 3, so a Paid pressed
now is pressed after that date.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam
from app.utils.dates import display_today
from tests._cards import add_card
from tests._test_helpers import add_txn, generate_row_of, make_expense_template
from tests.test_routes.test_a_row_on_the_card_is_drawn_as_credit import _popover, _the_save

_EIGHT_HUNDRED = Decimal("800.00")
_JAN_3 = date(2026, 1, 3)
_PAID_ARIA = 'aria-label="Mark as paid"'
_CREDIT_ARIA = 'aria-label="Mark as credit card"'


def _bill(seed_user, period, account):
    """The $800.00 Groceries row in *period* on *account*, dated Jan 3, committed."""
    row = add_txn(
        db.session, seed_user, period, "Groceries", _EIGHT_HUNDRED,
        due_date=_JAN_3, account=account,
    )
    db.session.commit()
    return row


def _payment(row_id):
    """The row read fresh, and its covering movement as ``(account id, settled on)``."""
    db.session.expire_all()
    row = db.session.get(Transaction, row_id)
    movement = status_seam.covering_movement_of(row)
    return row, (movement.account_id, movement.settled_on)


class TestEveryPaidDoor:
    """The bill on the Visa, Paid at each door: dated Jan 3 on the Visa, drawn Credit."""

    def test_the_cells_check(self, auth_client, seed_user, seed_periods):
        """POST mark-done with nothing: what the check, the keyboard and the palette send."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)

        response = auth_client.post(f"/transactions/{row.id}/mark-done")

        assert response.status_code == 200
        row, payment = _payment(row.id)
        assert payment == (card.id, _JAN_3)
        assert status_seam.drawn_status_id(row) == ref_cache.status_id(StatusEnum.CREDIT)

    def test_the_phones_mark_paid(self, auth_client, seed_user, seed_periods):
        """The phone card's Mark Paid, which asks for its card back: the same Jan 3."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)

        response = auth_client.post(
            f"/transactions/{row.id}/mark-done",
            data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "1"},
        )

        assert response.status_code == 200
        assert f'id="card-tp-{row.id}"' in response.get_data(as_text=True)
        assert _payment(row.id)[1] == (card.id, _JAN_3)

    def test_the_popovers_paid_with_the_card_picked(self, auth_client, seed_user, seed_periods):
        """The popover's Paid, its "Paid from" showing the Visa: Jan 3 on the Visa."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)
        html = _popover(auth_client, row.id)
        assert _PAID_ARIA in html
        assert f'<option value="{card.id}" selected>Visa</option>' in html, (
            "precondition: Paid from shows the Visa"
        )
        assert f'hx-include="#tender-{row.id}"' in html, "precondition: Paid posts the box"

        response = auth_client.post(
            f"/transactions/{row.id}/mark-done",
            data={"tender_account_id": str(card.id), "shown_lines": ""},
        )

        assert response.status_code == 200
        assert _payment(row.id)[1] == (card.id, _JAN_3)

    def test_the_popovers_paid_with_checking_picked_is_today(
        self, auth_client, seed_user, seed_periods,
    ):
        """Checking picked under "Paid from": paid from checking today, drawn Paid."""
        card = add_card(seed_user)
        checking = seed_user["account"]
        row = _bill(seed_user, seed_periods[0], card)

        response = auth_client.post(
            f"/transactions/{row.id}/mark-done",
            data={"tender_account_id": str(checking.id), "shown_lines": ""},
        )

        assert response.status_code == 200
        row, payment = _payment(row.id)
        assert payment == (checking.id, display_today())
        assert status_seam.drawn_status_id(row) == ref_cache.status_id(StatusEnum.DONE)

    def test_the_status_dropdown(self, auth_client, seed_user, seed_periods):
        """The popover's Save with Status set to Paid, every other control as drawn: Jan 3."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)
        payload = _the_save(_popover(auth_client, row.id))
        assert "settled_on" not in payload, "precondition: a Projected row posts no day"
        payload["status_id"] = str(ref_cache.status_id(StatusEnum.DONE))

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 200
        assert _payment(row.id)[1] == (card.id, _JAN_3)

    def test_the_companion_page(self, companion_client, seed_user, seed_periods):
        """The companion's Mark Paid on the owner's card bill: dated by its date, not today."""
        card = add_card(seed_user)
        template = make_expense_template(
            db.session, seed_user, amount="800.00", name="Groceries",
            category_key="Groceries", companion_visible=True, account=card,
        )
        row = generate_row_of(template, seed_periods[0])
        db.session.commit()
        due = row.due_date
        assert due < display_today(), "precondition: today would differ"

        response = companion_client.post(
            f"/transactions/{row.id}/mark-done",
            data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "0"},
        )

        assert response.status_code == 200
        assert _payment(row.id)[1] == (card.id, due)


class TestCreditOffersAnotherCard:
    """On a bill that lives on a card, Credit means one of the owner's other cards."""

    def test_on_the_only_card_the_popover_draws_paid_and_no_credit(
        self, auth_client, seed_user, seed_periods,
    ):
        """The Visa alone: Paid, and no Credit button, sentence or "Money moved on" box for it."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)

        html = _popover(auth_client, row.id)

        assert _PAID_ARIA in html
        assert _CREDIT_ARIA not in html
        assert f'id="credit-press-{row.id}"' not in html

    def test_beside_another_card_the_popover_names_it(self, auth_client, seed_user, seed_periods):
        """The bill on the Amex, the Visa beside it: Credit puts it on the Visa, with no pick."""
        add_card(seed_user)
        amex = add_card(seed_user, name="Amex")
        row = _bill(seed_user, seed_periods[0], amex)

        html = _popover(auth_client, row.id)

        assert _CREDIT_ARIA in html
        assert "Credit puts this on Visa on the day shown." in html
        assert f'id="credit-card-{row.id}"' not in html

    def test_status_set_to_credit_there_names_paid(self, auth_client, seed_user, seed_periods):
        """A Save posting Status Credit on the Visa's own bill: refused, pointing at Paid."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)
        payload = _the_save(_popover(auth_client, row.id))
        payload["status_id"] = str(ref_cache.status_id(StatusEnum.CREDIT))

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 400
        assert (
            "Status cannot put Groceries on a credit card: it lives on Visa, so "
            "open it and press Paid instead."
        ) in response.get_data(as_text=True)

    def test_a_press_on_the_only_card_is_refused_in_words(
        self, auth_client, seed_user, seed_periods,
    ):
        """POST mark-credit on the Visa's own bill: a 400 saying why, nothing written."""
        card = add_card(seed_user)
        row = _bill(seed_user, seed_periods[0], card)

        response = auth_client.post(f"/transactions/{row.id}/mark-credit")

        assert response.status_code == 400
        assert "Groceries lives on Visa, your only card" in response.get_data(as_text=True)
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        assert not row.entries
