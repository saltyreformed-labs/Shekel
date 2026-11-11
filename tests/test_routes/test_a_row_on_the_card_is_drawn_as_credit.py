"""
Shekel Budget App -- a row whose payment is on the card is drawn as Credit (plan task plan#3054)

Since plan#2929 a Credit press stores the row as Paid with its one movement on
the card (rulings plan#2887, plan#2888), and the trace on plan#2890 decided it
is DRAWN as Credit: the CC badge, Undo CC and the Status dropdown's Credit, and
no Paid offered, as on an old Credit row.  ``status_seam.drawn_status_id`` is
the one answer; the grid draws ``grid_view_service.drawn_status``, the dropdown
offers ``transaction_service.offerable_status_ids`` and the door reads back
``transaction_service.requested_status_id``.

**The example**: a $800.00 Groceries row in the paycheck holding today, dated
today, put on the Visa by the Credit press.  Checking holds the row; the card's
books open before any fixture day (``tests._cards.add_card``).
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.utils.dates import display_today
from tests._cards import add_card, add_savings
from tests._legacy_credit import plant_old_credit
from tests._test_helpers import add_txn
from tests.test_routes._statement_forms import ReconcileFormReader

_EIGHT_HUNDRED = Decimal("800.00")
# The paycheck ``seed_periods_today`` puts today in.
_TODAY_PERIOD = 4


def _status(member):
    """The ``ref.statuses.id`` of *member*."""
    return ref_cache.status_id(member)


def _groceries(seed_user, periods):
    """The $800.00 Groceries row on checking, dated today, committed."""
    row = add_txn(
        db.session, seed_user, periods[_TODAY_PERIOD], "Groceries", _EIGHT_HUNDRED,
        due_date=display_today(),
    )
    db.session.commit()
    return row


def _on_the_card(seed_user, periods):
    """The Visa, and the Groceries row the Credit press put on it."""
    card = add_card(seed_user)
    db.session.commit()
    row = _groceries(seed_user, periods)
    transaction_service.mark_as_credit(row)
    db.session.commit()
    return card, row


def _paid_on_checking(seed_user, periods):
    """The Groceries row Paid from checking, with a Visa in the app."""
    add_card(seed_user)
    db.session.commit()
    row = _groceries(seed_user, periods)
    transaction_service.settle_transaction(row)
    db.session.commit()
    return row


def _fresh(row_id):
    """The row read again from the database."""
    db.session.expire_all()
    return db.session.get(Transaction, row_id)


def _cell(auth_client, row_id):
    """The desktop cell's HTML."""
    response = auth_client.get(f"/transactions/{row_id}/cell")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _popover(auth_client, row_id):
    """The full-edit popover's HTML."""
    response = auth_client.get(f"/transactions/{row_id}/full-edit")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _status_options(html):
    """``{option label: (selected, disabled)}`` of the popover's Status dropdown."""
    select = re.search(r'<select name="status_id".*?</select>', html, re.S).group(0)
    return {
        label.strip(): ("selected" in attrs, "disabled" in attrs)
        for attrs, label in re.findall(r"<option([^>]*)>\s*([^<]*?)\s*</option>", select, re.S)
    }


def _the_save(html):
    """What a browser submits for the popover's PATCH form, control by control.

    A MultiDict of every control in order, so a repeated name (a checkbox
    and its hidden "false") keeps the first value, as werkzeug reads a
    browser's post.
    """
    start = html.index("<form hx-patch")
    reader = ReconcileFormReader()
    reader.feed(html[start:html.index("</form>", start)])
    return MultiDict(reader.fields)


class TestWhatTheRowIsDrawnAs:
    """``status_seam.drawn_status_id``: Credit for a row Paid on the card, else its own."""

    def test_a_row_put_on_the_card_is_drawn_as_credit(self, seed_user, seed_periods_today):
        """Stored Paid, its movement on the Visa: drawn Credit."""
        _card, row = _on_the_card(seed_user, seed_periods_today)
        row = _fresh(row.id)

        assert row.status_id == _status(StatusEnum.DONE)
        assert status_seam.settled_on_a_card(row) is True
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.CREDIT)

    def test_a_row_paid_from_checking_is_drawn_as_paid(self, seed_user, seed_periods_today):
        """Paid from checking with a card in the app: drawn as stored, Paid."""
        row = _fresh(_paid_on_checking(seed_user, seed_periods_today).id)

        assert status_seam.settled_on_a_card(row) is False
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.DONE)

    def test_a_refund_received_onto_the_card_is_drawn_as_received(
        self, seed_user, seed_periods_today,
    ):
        """A $25.00 refund Received with its movement on the Visa: Received, not Credit.

        Credit is an expense's, so a row on the card is Credit only when it is
        Paid.  Read through the settled band, this refund was Credit.
        """
        card = add_card(seed_user)
        db.session.commit()
        refund = add_txn(
            db.session, seed_user, seed_periods_today[_TODAY_PERIOD], "Refund",
            Decimal("25.00"), is_income=True, due_date=display_today(),
        )
        db.session.commit()
        transaction_service.settle_transaction(refund, tender_account_id=card.id)
        db.session.commit()
        refund = _fresh(refund.id)

        assert refund.status_id == _status(StatusEnum.RECEIVED)
        assert status_seam.covering_movement_of(refund).account_id == card.id
        assert status_seam.settled_on_a_card(refund) is False
        assert status_seam.drawn_status_id(refund) == _status(StatusEnum.RECEIVED)

    def test_an_old_credit_row_is_drawn_as_credit(self, seed_user, seed_periods_today):
        """Stored Credit holding its payback: drawn as stored, Credit."""
        row = _groceries(seed_user, seed_periods_today)
        plant_old_credit(row)
        db.session.commit()

        assert status_seam.drawn_status_id(_fresh(row.id)) == _status(StatusEnum.CREDIT)


class TestWhatTheDropdownOffersAndReadsBack:
    """The dropdown speaks the drawn status; the door reads Credit back as Paid."""

    def test_a_row_on_the_card_offers_credit_and_projected(self, seed_user, seed_periods_today):
        """Credit in Paid's place, and Projected: no Paid, as an old Credit row offers none."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        assert transaction_service.offerable_status_ids(_fresh(row.id)) == {
            _status(StatusEnum.CREDIT), _status(StatusEnum.PROJECTED),
        }

    @pytest.mark.parametrize("shape, offered", [
        ("paid on checking", (StatusEnum.DONE, StatusEnum.PROJECTED)),
        ("old credit", (StatusEnum.CREDIT, StatusEnum.PROJECTED)),
    ])
    def test_every_other_row_offers_the_state_machines_set(
        self, seed_user, seed_periods_today, shape, offered,
    ):
        """Paid on checking: Paid and Projected; an old Credit row: Credit and Projected."""
        if shape == "paid on checking":
            row = _paid_on_checking(seed_user, seed_periods_today)
        else:
            row = _groceries(seed_user, seed_periods_today)
            plant_old_credit(row)
            db.session.commit()

        assert transaction_service.offerable_status_ids(_fresh(row.id)) == {
            _status(member) for member in offered
        }

    @pytest.mark.parametrize("posted, applied", [
        (StatusEnum.CREDIT, StatusEnum.DONE),
        (StatusEnum.PROJECTED, StatusEnum.PROJECTED),
        (StatusEnum.DONE, StatusEnum.DONE),
    ])
    def test_a_posted_status_on_a_row_on_the_card(
        self, seed_user, seed_periods_today, posted, applied,
    ):
        """Credit posted back means as it is, Paid; any other status is the move it names."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        assert transaction_service.requested_status_id(
            _fresh(row.id), _status(posted),
        ) == _status(applied)

    def test_a_posted_status_on_a_row_paid_from_checking_is_itself(
        self, seed_user, seed_periods_today,
    ):
        """Paid on checking: each posted status is itself (the state machine grades Credit)."""
        row = _fresh(_paid_on_checking(seed_user, seed_periods_today).id)

        assert transaction_service.requested_status_id(
            row, _status(StatusEnum.DONE),
        ) == _status(StatusEnum.DONE)
        assert transaction_service.requested_status_id(
            row, _status(StatusEnum.CREDIT),
        ) == _status(StatusEnum.CREDIT)


class TestTheCell:
    """The desktop cell: the CC chip and badge, no tick, no C key."""

    def test_a_row_on_the_card_draws_cc_and_offers_no_paid(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The credit chip, the CC badge, "-- Credit" in its label; no tick, no data-can-credit."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        html = _cell(auth_client, row.id)

        assert "txn-chip st-credit" in html
        assert 'aria-label="Credit card"' in html
        assert "badge-done" not in html
        assert "$800.00 -- Credit" in html
        assert "paybtn" not in html
        assert "data-can-credit" not in html

    def test_a_refund_received_onto_the_card_draws_the_tick(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """A $25.00 refund Received with its movement on the Visa: the tick, named Received."""
        card = add_card(seed_user)
        db.session.commit()
        refund = add_txn(
            db.session, seed_user, seed_periods_today[_TODAY_PERIOD], "Refund",
            Decimal("25.00"), is_income=True, due_date=display_today(),
        )
        db.session.commit()
        transaction_service.settle_transaction(refund, tender_account_id=card.id)
        db.session.commit()

        html = _cell(auth_client, refund.id)

        assert "txn-chip st-done" in html
        assert 'class="badge-done ms-1" aria-label="Received"' in html
        assert "badge-credit" not in html

    def test_a_row_paid_from_checking_still_draws_the_tick(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The control: Paid on checking draws the done chip and the tick, named Paid."""
        row = _paid_on_checking(seed_user, seed_periods_today)

        html = _cell(auth_client, row.id)

        assert "txn-chip st-done" in html
        assert 'class="badge-done ms-1" aria-label="Paid"' in html
        assert "badge-credit" not in html
        assert "$800.00 -- Paid" in html


class TestThePopover:
    """The full-edit popover: Credit named, Undo CC, the dropdown on Credit, no Paid."""

    def test_a_row_on_the_card_is_credit_with_undo_cc_and_no_paid(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Context line Credit; Undo CC; no Paid, no Credit press; no "Paid from" box.

        Undo CC is the row's way off the card (plan task plan#3289, ruling
        plan#3227).  A Savings account is planted so the box would have a
        choice: checking and Savings, beside the Visa it records, so only
        that rule can withhold it (ruling R-CC34 draws none with one choice).
        """
        _card, row = _on_the_card(seed_user, seed_periods_today)
        add_savings(seed_user)
        db.session.commit()

        html = _popover(auth_client, row.id)

        meta = re.search(r'<div class="txn-card-meta mb-2">(.*?)</div>', html, re.S).group(1)
        assert re.sub(r"\s+", " ", meta).strip().endswith("&middot; Credit &middot; Due " + (
            display_today().strftime("%-m/%-d")
        ))
        assert "Undo CC" in html
        assert 'aria-label="Mark as paid"' not in html
        assert 'aria-label="Mark as credit card"' not in html
        assert 'name="tender_account_id"' not in html
        assert "Paid from" not in html

    def test_the_status_dropdown_selects_credit_and_disables_paid(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Credit selected, Projected open, Paid / Received / Cancelled not reachable."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        options = _status_options(_popover(auth_client, row.id))

        assert options["Credit"] == (True, False)
        assert options["Projected"] == (False, False)
        assert options["Paid"] == (False, True)
        assert options["Received"] == (False, True)
        assert options["Cancelled"] == (False, True)

    def test_what_the_card_took_and_when_stay_correctable(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The Actual box holds $800.00 and "Money moved on" today: facts about the card."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        html = _popover(auth_client, row.id)

        assert re.search(r'name="settled_amount"[^>]*value="800.00"', html, re.S)
        assert re.search(
            rf'name="settled_on"[^>]*value="{display_today().isoformat()}"', html, re.S,
        )

    def test_a_row_paid_from_checking_keeps_paid_and_its_picker(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """The control: Paid on checking selects Paid, draws the picker, offers no Undo CC.

        A Savings account is planted so the picker has a choice: the Visa is
        none since ruling plan#3227 (plan task plan#3232), so with checking
        and the Visa alone no picker is drawn.
        """
        row = _paid_on_checking(seed_user, seed_periods_today)
        add_savings(seed_user)
        db.session.commit()

        html = _popover(auth_client, row.id)

        assert _status_options(html)["Paid"] == (True, False)
        assert 'name="tender_account_id"' in html
        assert "Undo CC" not in html


class TestTheSave:
    """The popover's Save, posting what it renders, on a row on the card."""

    def test_an_untouched_save_keeps_the_row_on_the_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Credit posted back: 200, still Paid, the same $800.00 movement on the Visa."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        before = _fresh(row.id)
        movement = status_seam.covering_movement_of(before)
        snapshot = (before.version_id, movement.id, movement.amount, before.settled_on)
        payload = _the_save(_popover(auth_client, row.id))
        assert payload["status_id"] == str(_status(StatusEnum.CREDIT))

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 200
        after = _fresh(row.id)
        movement = status_seam.covering_movement_of(after)
        assert after.status_id == _status(StatusEnum.DONE)
        assert movement.account_id == card.id
        assert (after.version_id, movement.id, movement.amount, after.settled_on) == snapshot
        assert "st-credit" in response.get_data(as_text=True)

    @pytest.mark.parametrize("names", ["checking", "savings"])
    def test_a_paid_from_posted_off_the_card_is_refused(
        self, auth_client, seed_user, seed_periods_today, names,
    ):
        """A hand-built Save naming checking, or Savings: 400 naming Undo CC, still on the Visa.

        The popover draws no box here (plan task plan#3289), so only a
        hand-built post names an account; the door refuses it rather than
        move the payment off the card under Paid.
        """
        card, row = _on_the_card(seed_user, seed_periods_today)
        savings = add_savings(seed_user)
        db.session.commit()
        account = seed_user["account"] if names == "checking" else savings
        before = _fresh(row.id)
        movement = status_seam.covering_movement_of(before)
        snapshot = (before.version_id, movement.id, movement.amount, before.settled_on)
        payload = _the_save(_popover(auth_client, row.id))
        payload["tender_account_id"] = str(account.id)

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 400
        assert b"Press Undo CC to take it off the card" in response.data
        after = _fresh(row.id)
        movement = status_seam.covering_movement_of(after)
        assert after.status_id == _status(StatusEnum.DONE)
        assert movement.account_id == card.id
        assert (after.version_id, movement.id, movement.amount, after.settled_on) == snapshot

    def test_a_save_naming_the_card_itself_moves_nothing(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """A hand-built Save naming the Visa: the echo, 200, the $800.00 still on the Visa."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        payload = _the_save(_popover(auth_client, row.id))
        payload["tender_account_id"] = str(card.id)

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 200
        after = _fresh(row.id)
        movement = status_seam.covering_movement_of(after)
        assert after.status_id == _status(StatusEnum.DONE)
        assert (movement.account_id, movement.amount) == (card.id, _EIGHT_HUNDRED)

    def test_a_note_and_a_corrected_figure_save_on_the_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """Notes "Visa statement" and Actual $805.00: saved, the $805.00 still on the Visa."""
        card, row = _on_the_card(seed_user, seed_periods_today)
        payload = _the_save(_popover(auth_client, row.id))
        payload["notes"] = "Visa statement"
        payload["settled_amount"] = "805.00"

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 200
        after = _fresh(row.id)
        movement = status_seam.covering_movement_of(after)
        assert after.notes == "Visa statement"
        assert after.status_id == _status(StatusEnum.DONE)
        assert (movement.account_id, movement.amount) == (card.id, Decimal("805.00"))


    def test_a_corrected_day_saves_on_the_card(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """"Money moved on" set to yesterday: saved, the $800.00 still on the Visa.

        A settle day may fall before the row's own paycheck, so yesterday is
        a day the box accepts whatever today is; the card's books open before
        every fixture day.
        """
        card, row = _on_the_card(seed_user, seed_periods_today)
        day_before = display_today() - timedelta(days=1)
        payload = _the_save(_popover(auth_client, row.id))
        payload["settled_on"] = day_before.isoformat()

        response = auth_client.patch(f"/transactions/{row.id}", data=payload)

        assert response.status_code == 200
        after = _fresh(row.id)
        movement = status_seam.covering_movement_of(after)
        assert after.settled_on == day_before
        assert after.status_id == _status(StatusEnum.DONE)
        assert (movement.account_id, movement.amount) == (card.id, _EIGHT_HUNDRED)


class TestTheMobileCardAndThePlanRow:
    """The grid page's mobile card and Plan row draw the CC badge too."""

    def test_the_grid_page_draws_cc_on_the_card_and_the_plan_row(
        self, auth_client, seed_user, seed_periods_today,
    ):
        """This Period's card: CC, "-- Credit", no Mark Paid; the Plan row: CC, no tick."""
        _card, row = _on_the_card(seed_user, seed_periods_today)

        response = auth_client.get("/grid")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        start = html.index(f'id="card-tp-{row.id}"')
        following = html.find('class="mobile-card-wrapper"', start)
        card = html[start:following if following != -1 else len(html)]
        assert '<span class="badge-credit ms-1">CC</span>' in card
        assert "badge-done" not in card
        assert "$800.00 -- Credit" in card
        assert "Mark Paid" not in card
        assert "Open Full" in card, "precondition: the slice holds the card's actions"
        plan = html[html.index('id="plan-accordion"'):]
        assert '<span class="badge-credit ms-1">CC</span>' in plan
        assert "badge-done" not in plan
