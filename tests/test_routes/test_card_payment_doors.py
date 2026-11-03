"""
Shekel Budget App -- the doors that would break a card payment's settings (CC-6)

``budget.card_payment_settings`` names its card twice over -- through its
definition's destination, and as a card -- and two existing doors can move
either.  Each refuses with a sentence before the database's key or rule
would refuse the flush, and each refusal has its control: the same act on a
definition or a card that carries no settings goes through.

* the account edit door re-typing a card a payment names
  (:data:`app.utils.account_validation.CARD_PAYMENT_KEEPS_ITS_CARD`);
* the transfer edit door moving a card payment's destination
  (:data:`app.routes.transfers._helpers.CARD_PAYMENT_CANNOT_CHANGE_DESTINATION`).
"""

import pytest

from app import ref_cache
from app.enums import AcctCategoryEnum, CardPaymentModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.card_payment_settings import CardPaymentSettings
from app.models.ref import AccountType
from app.models.transfer_template import TransferTemplate
from app.routes.transfers._helpers import CARD_PAYMENT_CANNOT_CHANGE_DESTINATION
from app.utils.account_validation import CARD_PAYMENT_KEEPS_ITS_CARD
from tests._definition_price import amount_box_payload
from tests._test_helpers import create_account_of_type, make_transfer_template


def _card(seed_user, name="Visa"):
    """A Credit Card account of *seed_user*'s."""
    return create_account_of_type(seed_user, db.session, "Credit Card", name)


def _work_it_out(template):
    """Give *template* card-payment settings, statement mode, and commit."""
    db.session.add(CardPaymentSettings(
        template=template, user_id=template.user_id,
        mode_id=ref_cache.card_payment_mode_id(
            CardPaymentModeEnum.STATEMENT_BALANCE,
        ),
    ))
    db.session.commit()


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _store_credit_type(seed_user):
    """A custom Liability type that is not revolving, committed.

    A card re-typed to it crosses neither posting boundary (the amortizing
    flag and the ledger class both stay), so no refusal but the card
    payment's can answer the re-type.
    """
    store_credit = AccountType(
        user_id=seed_user["user"].id, name="Store credit",
        category_id=ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY),
    )
    db.session.add(store_credit)
    db.session.commit()
    return store_credit


class TestTheAccountEditDoor:
    """A card a payment names stays a card."""

    def test_re_typing_a_paid_card_is_refused(self, app, auth_client, seed_user):
        """The edit names the card and its payment, and the type stands."""
        with app.app_context():
            card = _card(seed_user)
            template = make_transfer_template(db.session, seed_user, card)
            template.name = "Pay the Visa"
            _work_it_out(template)
            store_credit = _store_credit_type(seed_user)
            card_type = card.account_type_id

            response = auth_client.post(
                f"/accounts/{card.id}",
                data={"account_type_id": str(store_credit.id)},
            )

            assert response.status_code == 302
            assert response.headers["Location"].endswith(f"/accounts/{card.id}/edit")
            assert _flashes(auth_client) == [CARD_PAYMENT_KEEPS_ITS_CARD.format(
                card="Visa", payment="Pay the Visa",
            )]
            db.session.expire_all()
            assert db.session.get(Account, card.id).account_type_id == card_type

    def test_re_typing_a_card_no_payment_names_goes_through(
        self, app, auth_client, seed_user,
    ):
        """The control: the same re-type of a card with a plain transfer into it."""
        with app.app_context():
            card = _card(seed_user)
            make_transfer_template(db.session, seed_user, card)
            db.session.commit()
            store_credit = _store_credit_type(seed_user)

            response = auth_client.post(
                f"/accounts/{card.id}",
                data={"account_type_id": str(store_credit.id)},
            )

            assert response.status_code == 302
            assert not any("card payment" in m for m in _flashes(auth_client))
            db.session.expire_all()
            assert db.session.get(Account, card.id).account_type_id == store_credit.id


def _post_destination(auth_client, template, to_account_id):
    """POST *template*'s edit form unchanged but for its destination."""
    return auth_client.post(f"/transfers/{template.id}", data={
        "name": template.name,
        **amount_box_payload(template),
        "from_account_id": str(template.from_account_id),
        "to_account_id": str(to_account_id),
        "version_id": str(template.version_id),
    })


@pytest.mark.usefixtures("seed_periods")
class TestTheTransferEditDoor:
    """A card payment pays the card it was set up for.

    Each definition repeats every period, so the edit regenerates over the
    seeded pay calendar (``seed_periods``).
    """

    def test_moving_a_card_payment_is_refused(
        self, app, auth_client, seed_user,
    ):
        """Pointed at another card: refused to the edit form, nothing moved."""
        with app.app_context():
            visa = _card(seed_user)
            amex = _card(seed_user, name="Amex")
            template = make_transfer_template(db.session, seed_user, visa)
            _work_it_out(template)

            response = _post_destination(auth_client, template, amex.id)

            assert response.status_code == 302
            assert response.headers["Location"].endswith(
                f"/transfers/{template.id}/edit",
            )
            assert _flashes(auth_client) == [
                CARD_PAYMENT_CANNOT_CHANGE_DESTINATION.format(payment=template.name),
            ]
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template.id).to_account_id == visa.id

    def test_moving_a_plain_transfer_into_a_card_goes_through(
        self, app, auth_client, seed_user,
    ):
        """The control: the same move of a definition with no settings lands."""
        with app.app_context():
            visa = _card(seed_user)
            amex = _card(seed_user, name="Amex")
            template = make_transfer_template(db.session, seed_user, visa)
            db.session.commit()

            response = _post_destination(auth_client, template, amex.id)

            assert response.status_code == 302
            assert not response.headers["Location"].endswith("/edit")
            db.session.expire_all()
            assert db.session.get(TransferTemplate, template.id).to_account_id == amex.id

    def test_an_unchanged_destination_saves(
        self, app, auth_client, seed_user,
    ):
        """A card payment's form posted with its own card is no move, and saves."""
        with app.app_context():
            visa = _card(seed_user)
            template = make_transfer_template(db.session, seed_user, visa)
            _work_it_out(template)

            response = _post_destination(auth_client, template, visa.id)

            assert response.status_code == 302
            assert not response.headers["Location"].endswith("/edit")
            assert CARD_PAYMENT_CANNOT_CHANGE_DESTINATION.format(
                payment=template.name,
            ) not in _flashes(auth_client)
