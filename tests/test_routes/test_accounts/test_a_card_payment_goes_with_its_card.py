"""A card's payment is archived and deleted only with its card (task plan#3757).

Every card has one payment (task plan#2933), and the card's own Archive,
Unarchive and Delete take it with them (tasks plan#3697, plan#3754).  The
Recurring page's Archive and Delete of that payment are refused, and the page
offers neither: an archive by hand left an active card with no payment, and a
delete by hand left an archived card none to bring back.  A fixed payment into
a card is a plain transfer and is archived and deleted as one.  The account a
card's payment comes from is refused with the payment named, because the
owner cannot clear it by hand as the refusal asks of any other transfer.
"""

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate
from app.routes.transfers.lifecycle import CARD_PAYMENT_GOES_WITH_ITS_CARD
from tests._test_helpers import make_transfer_template

from ._helpers import _add_card, _flashes, _live_rows, _reload

#: The Recurring page's refusal for the card the helpers add.
_REFUSED = CARD_PAYMENT_GOES_WITH_ITS_CARD.format(
    payment="Pay off Visa", card="Visa",
)


def _a_fixed_payment_into(card, seed_user):
    """A plain $200.00-a-paycheck transfer from Checking into *card*, named Extra to Visa."""
    extra = make_transfer_template(db.session, seed_user, card)
    extra.name = "Extra to Visa"
    db.session.commit()
    return extra


def _action(template_id, act):
    """The form action of the Recurring page's *act* button for one transfer."""
    return f'action="/transfers/{template_id}/{act}"'


@pytest.mark.usefixtures("seed_periods_today")
class TestTheRecurringPageRefusesACardPayment:
    """POST /transfers/<payment>/archive and /hard-delete refuse a card's payment."""

    def test_its_archive_is_refused_and_nothing_changes(
        self, app, auth_client, seed_user,
    ):
        """The card is active, its payment stays active with every row.

        NEGATIVE CONTROL: before this task the archive went ahead, leaving an
        active card with no payment.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            rows = _live_rows(payment.id)

            resp = auth_client.post(f"/transfers/{payment.id}/archive")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [_REFUSED]
            assert _reload(TransferTemplate, payment.id).is_active is True
            assert _live_rows(payment.id) == rows
            assert db.session.get(Account, card.id).is_active is True

    def test_its_delete_is_refused_and_nothing_changes(
        self, app, auth_client, seed_user,
    ):
        """The payment and every row of it stay.

        NEGATIVE CONTROL: before this task the delete went ahead, the payment
        and its rows gone and the card left with none.
        """
        with app.app_context():
            _card, payment = _add_card(auth_client, seed_user)
            rows = _live_rows(payment.id)

            auth_client.post(f"/transfers/{payment.id}/hard-delete")

            assert _flashes(auth_client) == [_REFUSED]
            assert _reload(TransferTemplate, payment.id) is not None
            assert _live_rows(payment.id) == rows

    def test_an_archived_cards_payment_is_not_deleted_by_hand(
        self, app, auth_client, seed_user,
    ):
        """Archived with its card, the payment is refused too, and comes back with it."""
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            auth_client.post(f"/accounts/{card.id}/archive")
            _flashes(auth_client)

            auth_client.post(f"/transfers/{payment.id}/hard-delete")

            assert _flashes(auth_client) == [_REFUSED]
            assert _reload(TransferTemplate, payment.id).is_active is False

            auth_client.post(f"/accounts/{card.id}/unarchive")

            assert _reload(TransferTemplate, payment.id).is_active is True
            assert _live_rows(payment.id)

    def test_a_fixed_payment_into_the_card_is_archived_and_deleted_by_hand(
        self, app, auth_client, seed_user,
    ):
        """CONTROL: a plain transfer into the card has no settings row, and passes."""
        with app.app_context():
            card, _payment = _add_card(auth_client, seed_user)
            extra = _a_fixed_payment_into(card, seed_user)
            extra_id = extra.id

            auth_client.post(f"/transfers/{extra_id}/archive")

            [archived] = _flashes(auth_client)
            assert archived.startswith("Recurring transfer 'Extra to Visa' archived.")
            assert _reload(TransferTemplate, extra_id).is_active is False

            auth_client.post(f"/transfers/{extra_id}/hard-delete")

            assert _flashes(auth_client) == [
                "Recurring transfer 'Extra to Visa' permanently deleted."
            ]
            assert _reload(TransferTemplate, extra_id) is None


@pytest.mark.usefixtures("seed_periods_today")
class TestTheRecurringPageOffersNeither:
    """GET /templates: no Archive on a card's payment, no Delete once it is archived."""

    def test_the_active_list_offers_edit_but_no_archive(
        self, app, auth_client, seed_user,
    ):
        """The payment's row has its Edit and no Archive; the fixed payment's has both."""
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            extra = _a_fixed_payment_into(card, seed_user)

            page = auth_client.get("/templates").get_data(as_text=True)

            assert f'href="/transfers/{payment.id}/edit"' in page
            assert _action(payment.id, "archive") not in page
            assert _action(extra.id, "archive") in page

    def test_the_archived_drawer_offers_unarchive_but_no_delete(
        self, app, auth_client, seed_user,
    ):
        """Archived with its card: Unarchive stays, Delete is not offered.

        Unarchive is the act that refuses while the card is archived, with a
        sentence naming the card to unarchive first (task plan#3697).
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            extra = _a_fixed_payment_into(card, seed_user)
            auth_client.post(f"/transfers/{extra.id}/archive")
            auth_client.post(f"/accounts/{card.id}/archive")
            _flashes(auth_client)

            page = auth_client.get("/templates").get_data(as_text=True)

            assert _action(payment.id, "unarchive") in page
            assert _action(payment.id, "hard-delete") not in page
            assert _action(extra.id, "hard-delete") in page


@pytest.mark.usefixtures("seed_periods_today")
class TestTheAccountACardIsPaidFrom:
    """Checking pays the card: its archive and delete name the payment and its ways out."""

    def test_its_archive_names_the_payment(self, app, auth_client, seed_user):
        """Refused, Checking active, and the sentence says what the owner can do.

        NEGATIVE CONTROL: before this task the sentence said to archive the
        recurring transfers first, which the Recurring page now refuses for
        this one.
        """
        with app.app_context():
            _add_card(auth_client, seed_user)
            checking_id = seed_user["account"].id

            auth_client.post(f"/accounts/{checking_id}/archive")

            assert _flashes(auth_client) == [
                "Cannot archive this account -- it is used by active recurring "
                "transfers. 'Pay off Visa' is archived only with 'Visa': edit "
                "it to pay from another account, or archive 'Visa' first."
            ]
            assert _reload(Account, checking_id).is_active is True

    def test_its_delete_names_the_payment(self, app, auth_client, seed_user):
        """Refused, Checking kept."""
        with app.app_context():
            _add_card(auth_client, seed_user)
            checking_id = seed_user["account"].id

            auth_client.post(f"/accounts/{checking_id}/hard-delete")

            assert _flashes(auth_client) == [
                "Cannot delete this account -- it is used by recurring "
                "transfers. 'Pay off Visa' is deleted only with 'Visa': edit "
                "it to pay from another account, or delete 'Visa' first."
            ]
            assert _reload(Account, checking_id) is not None

    def test_another_transfer_beside_it_keeps_its_own_sentence(
        self, app, auth_client, seed_user,
    ):
        """A fixed payment into the card too: the general sentence, then the payment's."""
        with app.app_context():
            card, _payment = _add_card(auth_client, seed_user)
            _a_fixed_payment_into(card, seed_user)
            checking_id = seed_user["account"].id

            auth_client.post(f"/accounts/{checking_id}/archive")

            assert _flashes(auth_client) == [
                "Cannot archive this account -- it is used by active recurring "
                "transfers. Archive those recurring transfers first. 'Pay off "
                "Visa' is archived only with 'Visa': edit it to pay from "
                "another account, or archive 'Visa' first."
            ]
