"""A card's delete takes its one payment, so a card added by mistake is deleted (task plan#3754).

Every card has one payment, made with the card (task plan#2933), so the
account delete's refusal of an account any recurring transfer names held
every card, and the history check counted the payment's own projected rows as
history.  The payment is the card's plan now: the delete takes it through the
transfers' own delete act, and the history check asks of it only what the
Recurring page's own delete keeps -- a settled transfer, soft-deleted or not,
and one holding a payment.  A card with history archives, its payment with
it.  Any other recurring transfer naming the card still refuses.
"""

from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import Account
from app.models.card_payment_settings import CardPaymentSettings
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import transfer_service
from tests._test_helpers import create_transfer, typed

from ._helpers import (
    _add_card,
    _bump_account_version_outside_session,
    _flashes,
    _live_rows,
    _reload,
    _retype_into_a_card,
)

#: What a card with history says once it is archived, the payment's clause
#: following.
_ARCHIVED_INSTEAD = (
    "'Visa' has transaction history and cannot be permanently deleted. It has "
    "been archived instead, and its payment 'Pay off Visa' with it: "
)


def _transfers_of(template_id):
    """The ids of every transfer of the template, soft-deleted ones too."""
    return [
        row.id for row in db.session.query(Transfer).filter_by(
            transfer_template_id=template_id,
        )
    ]


def _assert_archived_with_its_payment(card, payment):
    """The card and its payment are both archived, neither deleted."""
    assert _reload(Account, card.id).is_active is False
    assert db.session.get(TransferTemplate, payment.id).is_active is False


@pytest.mark.usefixtures("seed_periods_today")
class TestDeletingACardDeletesItsPayment:
    """POST /accounts/<card>/hard-delete takes the card's payment with it."""

    def test_a_card_added_by_mistake_is_deleted_with_its_payment(
        self, app, auth_client, seed_user,
    ):
        """The card, its payment, its settings row and every row of it are gone.

        NEGATIVE CONTROL: before this task guard 2 counted the card's own
        payment as a recurring transfer naming it and refused every card
        ("Cannot delete this account"); with guard 2 excluding it but the
        history check counting its projected rows, the card archived instead.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            card_id, payment_id = card.id, payment.id
            assert _live_rows(payment_id), "precondition: the payment has rows"

            resp = auth_client.post(f"/accounts/{card_id}/hard-delete")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "Account 'Visa' permanently deleted, and its payment "
                "'Pay off Visa' with it."
            ]
            assert _reload(Account, card_id) is None
            assert db.session.get(TransferTemplate, payment_id) is None
            assert db.session.query(CardPaymentSettings).filter_by(
                card_account_id=card_id,
            ).count() == 0
            assert db.session.query(Transfer).filter(
                Transfer.to_account_id == card_id,
            ).count() == 0

    def test_an_archived_card_is_deleted_with_its_archived_payment(
        self, app, auth_client, seed_user,
    ):
        """Archived first, its rows hidden by the archive: the delete takes all of it."""
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            card_id, payment_id = card.id, payment.id
            auth_client.post(f"/accounts/{card_id}/archive")
            _flashes(auth_client)
            assert _transfers_of(payment_id), "precondition: hidden rows remain"
            assert _live_rows(payment_id) == []

            auth_client.post(f"/accounts/{card_id}/hard-delete")

            assert _flashes(auth_client) == [
                "Account 'Visa' permanently deleted, and its payment "
                "'Pay off Visa' with it."
            ]
            assert _reload(Account, card_id) is None
            assert db.session.get(TransferTemplate, payment_id) is None
            assert _transfers_of(payment_id) == []

    def test_another_recurring_transfer_into_the_card_still_refuses(
        self, app, auth_client, seed_user,
    ):
        """An ARCHIVED standing transfer into the card refuses the delete; nothing changes.

        Archived, because the delete's guard counts archived definitions
        too (their key would refuse the card's DELETE), where the archive's
        counts only active ones.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            db.session.add(TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=card.id,
                name="Extra to Visa",
                is_active=False,
            ))
            db.session.commit()
            rows = _live_rows(payment.id)

            auth_client.post(f"/accounts/{card.id}/hard-delete")

            assert _flashes(auth_client) == [
                "Cannot delete this account -- it is used by recurring "
                "transfers. Delete those recurring transfers first."
            ]
            assert _reload(Account, card.id).is_active is True
            assert db.session.get(TransferTemplate, payment.id).is_active is True
            assert _live_rows(payment.id) == rows

    def test_a_race_on_the_card_keeps_the_card_and_its_payment(
        self, app, auth_client, seed_user,
    ):
        """Another tab's write to the card lands first: the stale flash, nothing deleted.

        The payment's DELETE flushes first and the card's lands at the one
        commit, so the race rolls both back.  NEGATIVE CONTROL: had the
        payment's act committed on its own (the transfer door's
        ``destroy_transfer_definition``), the payment would be gone while the
        card stayed.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            card_id, payment_id = card.id, payment.id
            rows = _live_rows(payment_id)
            fired = {"flag": False}

            def make_stale(_mapper, _connection, target):
                """Bump the card's version once, as another tab's save would."""
                if fired["flag"] or target.id != card_id:
                    return
                fired["flag"] = True
                _bump_account_version_outside_session(card_id)

            event.listen(Account, "before_delete", make_stale)
            try:
                resp = auth_client.post(f"/accounts/{card_id}/hard-delete")
            finally:
                event.remove(Account, "before_delete", make_stale)

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                "This account was changed by another action.  Please reload "
                "the page and try again."
            ]
            assert _reload(Account, card_id).is_active is True
            assert db.session.get(TransferTemplate, payment_id).is_active is True
            assert _live_rows(payment_id) == rows


@pytest.mark.usefixtures("seed_periods_today")
class TestACardWithHistoryIsArchivedWithItsPayment:
    """What under the card, or under its payment, archives the card instead."""

    def test_a_paid_payment_row_archives_the_card_and_its_payment(
        self, app, auth_client, seed_user,
    ):
        """The first payment row marked Paid: history, so the card and payment archive.

        The Paid row is settled and holds its payment; the other rows are
        the payment's plan, retired by the archive.
        """
        with app.app_context():
            card, payment = _retype_into_a_card(auth_client, seed_user)
            first, *later = _live_rows(payment.id)
            transfer_service.update_transfer(
                first, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            auth_client.post(f"/accounts/{card.id}/hard-delete")

            assert _flashes(auth_client) == [
                _ARCHIVED_INSTEAD + f"{len(later)} projected payment(s) removed."
            ]
            _assert_archived_with_its_payment(card, payment)
            assert _live_rows(payment.id) == [first]

    def test_a_hidden_zero_close_is_settled_history(
        self, app, auth_client, seed_user,
    ):
        """A $0.00 Paid payment row, then deleted, stays Paid: the card archives.

        A $0.00 close holds no payment record, so its soft delete keeps it
        Paid (ruling R-BAL246): a hidden settled transfer, the one the
        settled-history question alone sees (ruling R-JE).  NEGATIVE CONTROL:
        without that question the delete went ahead; the transfer delete act
        keeps a settled transfer, and its definition's DELETE then nulled the
        transfer's link to it, which ``ck_transfers_adhoc_owns_amount``
        refuses: an ``IntegrityError``, a 500.
        """
        with app.app_context():
            card, payment = _retype_into_a_card(auth_client, seed_user)
            first, *later = _live_rows(payment.id)
            transfer_service.settle_transfer(
                first, seed_user["user"].id, submitted=typed(Decimal("0.00")),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                first, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            stored = db.session.get(Transfer, first)
            assert (stored.is_deleted, stored.status_id) == (
                True, ref_cache.status_id(StatusEnum.DONE),
            ), "precondition: a hidden settled payment row"

            resp = auth_client.post(f"/accounts/{card.id}/hard-delete")

            assert resp.status_code == 302
            assert _flashes(auth_client) == [
                _ARCHIVED_INSTEAD + f"{len(later)} projected payment(s) removed."
            ]
            _assert_archived_with_its_payment(card, payment)

    def test_a_row_holding_a_recorded_payment_archives_and_stays(
        self, app, auth_client, seed_user,
    ):
        """A payment row marked Paid and set back to Projected: history, and it stays.

        A revert keeps the settle's payment on each leg (ruling R-BAL61); the
        row is not settled, so only the holding question sees it (ruling
        R-CC65), and the archive leaves it on the budget, named.
        """
        with app.app_context():
            card, payment = _retype_into_a_card(auth_client, seed_user)
            first, *later = _live_rows(payment.id)
            for status in (StatusEnum.DONE, StatusEnum.PROJECTED):
                transfer_service.update_transfer(
                    first, seed_user["user"].id,
                    status_id=ref_cache.status_id(status),
                )
                db.session.commit()

            auth_client.post(f"/accounts/{card.id}/hard-delete")

            assert _flashes(auth_client) == [
                _ARCHIVED_INSTEAD + f"{len(later)} projected payment(s) "
                "removed; the row holding a recorded payment stays on your "
                "budget."
            ]
            _assert_archived_with_its_payment(card, payment)
            assert _live_rows(payment.id) == [first]

    def test_a_transfer_naming_no_definition_is_still_history(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-time $100.00 sent to the card, set up with no definition: the card archives.

        The history check leaves out only the card's own payment's
        transfers.  NEGATIVE CONTROL: spelled as ``!=`` rather than ``IS
        DISTINCT FROM``, the exclusion drops every transfer naming no
        definition too (``NULL != n`` is not true), and the card was deleted
        with this transfer.
        """
        with app.app_context():
            card, payment = _add_card(auth_client, seed_user)
            extra = create_transfer(
                seed_user, db.session, seed_user["account"], card,
                seed_periods_today[4], Decimal("100.00"),
            )
            db.session.commit()
            assert extra.transfer_template_id is None

            auth_client.post(f"/accounts/{card.id}/hard-delete")

            [message] = _flashes(auth_client)
            assert message.startswith(_ARCHIVED_INSTEAD)
            _assert_archived_with_its_payment(card, payment)
            assert db.session.get(Transfer, extra.id).is_deleted is False
