"""A card's one payment is archived and unarchived with the card (task plan#3697).

Every card has one payment, made with the card (task plan#2933), so the
account archive's refusal of an account an active recurring transfer names
held every card.  The card's own payment is archived with it now, through the
transfers' own archive act, and comes back with it through their unarchive
act; any other recurring transfer naming the card still refuses.  An archived
account re-typed into a card has its new payment archived with it, and the
edit door writes no ``is_active``, so the archive door is the one way an
account is archived.
"""

from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.transfer_template import TransferTemplate
from app.services import transfer_service
from tests._test_helpers import create_account_of_type

from ._helpers import (
    _add_card,
    _bump_account_version_outside_session,
    _flashes,
    _live_rows,
    _reload,
    _retype_into_a_card,
)


@pytest.mark.usefixtures("seed_periods_today")
class TestArchivingACardArchivesItsPayment:
    """POST /accounts/<card>/archive takes the card's payment with it."""

    def test_the_card_and_its_payment_are_archived(
        self, app, auth_client, seed_user,
    ):
        """The card archives, its payment archives, and every projected row goes.

        NEGATIVE CONTROL: before this task the guard counted the card's own
        payment as an active recurring transfer naming it, and refused every
        card ("Cannot archive this account").
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            rows = _live_rows(payment.id)
            assert rows, "precondition: the payment has projected rows"

            resp = auth_client.post(f"/accounts/{card.id}/archive")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "Account 'Visa' archived, and its payment 'Pay off Visa' with "
                f"it: {len(rows)} projected payment(s) removed."
            ]
            assert _reload(Account, card.id).is_active is False
            assert db.session.get(TransferTemplate, payment.id).is_active is False
            assert _live_rows(payment.id) == []

    def test_a_row_holding_a_recorded_payment_stays(
        self, app, auth_client, seed_user,
    ):
        """A payment row marked Paid and set back to Projected keeps its payment, and stays.

        A revert keeps the settle's payment on each leg (ruling R-BAL61), so
        the row is history the archive leaves on the budget, as the Recurring
        page's Archive does (ruling R-CC65).  The card is re-typed from a
        Savings account owing $500.00 so its first row is dated this paycheck
        and prices $500.00.
        """
        with app.app_context():
            savings, payment = _retype_into_a_card(auth_client, seed_user)
            first, *later = _live_rows(payment.id)
            for status in (StatusEnum.DONE, StatusEnum.PROJECTED):
                transfer_service.update_transfer(
                    first, seed_user["user"].id,
                    status_id=ref_cache.status_id(status),
                )
                db.session.commit()

            auth_client.post(f"/accounts/{savings.id}/archive")

            assert _flashes(auth_client) == [
                "Account 'Visa' archived, and its payment 'Pay off Visa' with "
                f"it: {len(later)} projected payment(s) removed; the row "
                "holding a recorded payment stays on your budget."
            ]
            assert _live_rows(payment.id) == [first]

    def test_another_recurring_transfer_into_the_card_still_refuses(
        self, app, auth_client, seed_user,
    ):
        """A standing $100 a paycheck into the card refuses the archive; nothing changes."""
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            db.session.add(TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=card.id,
                name="Extra to Visa",
            ))
            db.session.commit()
            rows = _live_rows(payment.id)

            auth_client.post(f"/accounts/{card.id}/archive")

            assert _flashes(auth_client) == [
                "Cannot archive this account -- it is used by active recurring "
                "transfers. Archive those recurring transfers first."
            ]
            assert _reload(Account, card.id).is_active is True
            assert db.session.get(TransferTemplate, payment.id).is_active is True
            assert _live_rows(payment.id) == rows

    def test_a_payment_already_archived_is_left_as_it_is(
        self, app, auth_client, seed_user,
    ):
        """With its payment archived on the Recurring page first, only the card archives."""
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            auth_client.post(f"/transfers/{payment.id}/archive")
            _flashes(auth_client)

            auth_client.post(f"/accounts/{card.id}/archive")

            assert _flashes(auth_client) == ["Account 'Visa' archived."]
            assert _reload(Account, card.id).is_active is False
            assert db.session.get(TransferTemplate, payment.id).is_active is False


@pytest.mark.usefixtures("seed_periods_today")
class TestUnarchivingACardBringsItsPaymentBack:
    """POST /accounts/<card>/unarchive restores the card's payment with it."""

    def test_the_payment_and_its_rows_come_back(
        self, app, auth_client, seed_user,
    ):
        """Archived and unarchived the same day: the same rows come back, live.

        Every card has one payment (task plan#2933), so an active card has
        an active one.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            rows = _live_rows(payment.id)
            auth_client.post(f"/accounts/{card.id}/archive")
            _flashes(auth_client)

            resp = auth_client.post(f"/accounts/{card.id}/unarchive")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "Account 'Visa' unarchived, and its payment 'Pay off Visa' "
                f"with it: {len(rows)} projected payment(s) restored."
            ]
            assert _reload(Account, card.id).is_active is True
            assert db.session.get(TransferTemplate, payment.id).is_active is True
            assert _live_rows(payment.id) == rows

    def test_an_archived_payer_keeps_the_card_archived(
        self, app, auth_client, seed_user,
    ):
        """The payment comes out of an archived Checking: refused, nothing unarchived.

        NEGATIVE CONTROL: without the unarchive act's refusal, restoring the
        payment's rows onto archived Checking raises out of
        ``transfer_service.restore_transfer`` (finding F-164), a 500.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            rows = _live_rows(payment.id)
            auth_client.post(f"/accounts/{card.id}/archive")
            auth_client.post(f"/accounts/{seed_user['account'].id}/archive")
            _flashes(auth_client)
            assert _reload(Account, seed_user["account"].id).is_active is False

            resp = auth_client.post(f"/accounts/{card.id}/unarchive")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "Nothing was unarchived: 'Pay off Visa' moves money from "
                "'Checking' to 'Visa', and 'Checking' is archived. Unarchive "
                "'Checking' first."
            ]
            assert _reload(Account, card.id).is_active is False
            assert db.session.get(TransferTemplate, payment.id).is_active is False
            assert not set(rows) & set(_live_rows(payment.id))

    def test_an_active_cards_unarchive_restores_no_payment(
        self, app, auth_client, seed_user,
    ):
        """A stale tab's Unarchive on an active card leaves its archived payment archived."""
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            auth_client.post(f"/transfers/{payment.id}/archive")
            _flashes(auth_client)

            auth_client.post(f"/accounts/{card.id}/unarchive")

            assert _flashes(auth_client) == ["Account 'Visa' unarchived."]
            assert _reload(TransferTemplate, payment.id).is_active is False

    def test_a_race_on_the_card_is_the_stale_flash_not_a_500(
        self, app, auth_client, seed_user,
    ):
        """Another tab's write to the card lands first: the stale flash, nothing restored.

        The card's write is flushed under the stale guard before the payment's
        unarchive.  NEGATIVE CONTROL: drop that flush and the act's pre-write
        reads autoflush the card's version-pinned UPDATE outside any guard,
        and the race escapes as a 500.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            auth_client.post(f"/accounts/{card.id}/archive")
            _flashes(auth_client)
            card_id = card.id
            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                """Bump the card's version once, as another tab's save would."""
                if fired["flag"] or target.id != card_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(card_id)

            event.listen(Account, "before_update", make_stale)
            try:
                resp = auth_client.post(f"/accounts/{card_id}/unarchive")
            finally:
                event.remove(Account, "before_update", make_stale)

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "This account was changed by another action.  Please reload "
                "the page and try again."
            ]
            assert _reload(Account, card_id).is_active is False
            assert db.session.get(TransferTemplate, payment.id).is_active is False


@pytest.mark.usefixtures("seed_periods_today")
class TestTheEditDoor:
    """The edit door: an archived account re-typed into a card, and no ``is_active``."""

    def test_an_archived_account_retyped_into_a_card_has_its_payment_archived(
        self, app, auth_client, seed_user,
    ):
        """Archived Savings re-typed into a card: its new payment is made archived.

        Then the card's Unarchive brings the payment and its rows back.
        NEGATIVE CONTROL: without the update door's archive act the payment
        is active, with live rows, on an archived card.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            auth_client.post(f"/accounts/{savings.id}/archive")
            _flashes(auth_client)
            card_type = db.session.query(AccountType).filter_by(
                name="Credit Card", user_id=None,
            ).one()

            auth_client.post(f"/accounts/{savings.id}", data={
                "account_type_id": card_type.id, "name": "Visa",
            })

            assert _flashes(auth_client) == ["Account 'Visa' updated."]
            card = _reload(Account, savings.id)
            assert card.is_active is False
            payment = db.session.query(TransferTemplate).filter_by(
                to_account_id=card.id,
            ).one()
            assert payment.is_active is False
            assert _live_rows(payment.id) == []

            auth_client.post(f"/accounts/{card.id}/unarchive")

            assert _reload(TransferTemplate, payment.id).is_active is True
            assert _live_rows(payment.id)

    def test_a_posted_is_active_archives_nothing(
        self, app, auth_client, seed_user,
    ):
        """A forged ``is_active=false`` on the edit form leaves the card and its payment active.

        No form renders the field.  NEGATIVE CONTROL: before this task the
        door wrote it, archiving the card past the archive door's refusal
        and leaving its payment active on an archived card.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)

            auth_client.post(f"/accounts/{card.id}", data={
                "name": "Visa", "is_active": "false",
            })

            assert _flashes(auth_client) == ["Account 'Visa' updated."]
            assert _reload(Account, card.id).is_active is True
            assert db.session.get(TransferTemplate, payment.id).is_active is True
