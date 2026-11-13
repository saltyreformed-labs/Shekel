"""A card is made with its one payment (task plan#2933; rulings plan#2888, plan#2926, plan#2669).

Adding a card through the create door, or re-typing an account into a card,
makes its payoff row each payday: from the owner's primary account, the one the
grid opens on, to the card, the whole balance worked out and no figure stored.
A card nothing can pay, or whose payment's name is taken, is refused with a
sentence and nothing written.
"""

from decimal import Decimal

import pytest
from markupsafe import escape

from app import ref_cache
from app.enums import CardPaymentModeEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.user import UserSettings
from app.services import card_payoff
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import resolve_transfer_amount
from tests._test_helpers import create_account_of_type


def _post_account(auth_client, type_name, name, owed="500.00"):
    """POST the create form for a *type_name* account named *name*, redirects followed."""
    acct_type = db.session.query(AccountType).filter_by(name=type_name).one()
    return auth_client.post("/accounts", data={
        "name": name,
        "account_type_id": acct_type.id,
        "anchor_balance": owed,
    }, follow_redirects=True)


def _retype(auth_client, account_id, type_name, **fields):
    """POST the edit form re-typing account *account_id* to *type_name*, redirects followed."""
    acct_type = db.session.query(AccountType).filter_by(
        name=type_name, user_id=None,
    ).one()
    return auth_client.post(f"/accounts/{account_id}", data={
        "account_type_id": acct_type.id, **fields,
    }, follow_redirects=True)


def _payments_into(account_id):
    """The recurring transfers paying into account *account_id*."""
    return db.session.query(TransferTemplate).filter_by(
        to_account_id=account_id,
    ).all()


def _named(seed_user, name):
    """The owner's account named *name*, or ``None``."""
    return db.session.query(Account).filter_by(
        user_id=seed_user["user"].id, name=name,
    ).one_or_none()


@pytest.mark.usefixtures("seed_periods_today")
class TestACardIsMadeWithItsPayment:
    """The create door makes a card's one payment with the card."""

    def test_a_card_owing_500_is_paid_off_the_next_payday(
        self, app, auth_client, seed_user,
    ):
        """A card added owing $500.00 is paid $500.00 the next payday, then $0.00.

        Its payment comes out of checking, the account the grid opens on, into
        the card, named in plain words, in the mode that works out everything on
        the card, and no row stores a figure.  Its repeat starts in the current
        paycheck (plan#2669), but the card's books open today, and no row lands
        on or before an account's books opening (rulings R-PC85, R-PC86): the
        $500.00 owed is dated today, so the first payday after it pays it
        (plan#2910).  That row pays what the card owes the day before it,
        $500.00, and the next pays what is left: $500.00 - $500.00 = $0.00.
        """
        with app.app_context():
            resp = _post_account(auth_client, "Credit Card", "Visa")
            assert resp.status_code == 200
            card = _named(seed_user, "Visa")
            template = db.session.query(TransferTemplate).filter_by(
                to_account_id=card.id,
            ).one()
            assert template.name == card_payoff.PAYOFF_NAME.format(card="Visa")
            assert template.from_account_id == seed_user["account"].id
            assert template.card_payment_settings.card_account_id == card.id
            assert template.card_payment_settings.mode_id == (
                ref_cache.card_payment_mode_id(CardPaymentModeEnum.CARD_BALANCE)
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            rows = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date).all()
            )
            # Every payday after the day the card is added, the day its books open.
            ahead = [
                period.period_id for period in ctx.calendar().saved()
                if period.start_date > ctx.as_of
            ]
            assert ahead
            assert [row.pay_period_id for row in rows] == ahead
            assert all(row.amount is None for row in rows)
            basis = ctx.amounts()
            assert [resolve_transfer_amount(row, basis) for row in rows[:2]] == [
                Decimal("500.00"), Decimal("0.00"),
            ]

    def test_an_account_that_is_not_a_card_makes_no_payment(
        self, app, auth_client, seed_user,
    ):
        """A savings account is made with no recurring transfer at all."""
        with app.app_context():
            _post_account(auth_client, "Savings", "Rainy Day", owed="100.00")
            savings = _named(seed_user, "Rainy Day")
            assert savings is not None
            assert db.session.query(TransferTemplate).filter(db.or_(
                TransferTemplate.to_account_id == savings.id,
                TransferTemplate.from_account_id == savings.id,
            )).count() == 0


@pytest.mark.usefixtures("seed_periods_today")
class TestACardNothingCanPayIsRefused:
    """Each refusal is the owner's sentence, and the card is not written."""

    def test_a_taken_name_refuses_the_card(self, app, auth_client, seed_user):
        """A recurring transfer already named "Pay off Visa" refuses a card named Visa."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("0.00"),
            )
            db.session.add(TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Pay off Visa",
            ))
            db.session.commit()

            resp = _post_account(auth_client, "Credit Card", "Visa")

            assert str(escape(card_payoff.PAYOFF_NAME_TAKEN.format(
                card="Visa", name="Pay off Visa",
            ))) in resp.get_data(as_text=True)
            assert _named(seed_user, "Visa") is None

    def test_a_card_as_the_grids_account_refuses_another(
        self, app, auth_client, seed_user,
    ):
        """With the grid opening on a card, a new card has no account to pay it from."""
        with app.app_context():
            old_card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Old Card",
                anchor_balance=Decimal("0.00"),
            )
            db.session.get(
                UserSettings, seed_user["settings"].id,
            ).default_grid_account_id = old_card.id
            db.session.commit()

            resp = _post_account(auth_client, "Credit Card", "Visa")

            assert str(escape(card_payoff.PRIMARY_CANNOT_PAY_A_CARD.format(
                card="Visa", primary="Old Card",
            ))) in resp.get_data(as_text=True)
            assert _named(seed_user, "Visa") is None

    def test_no_active_account_refuses_the_card(
        self, app, auth_client, seed_user,
    ):
        """An owner whose only account is archived has nothing to pay a card from."""
        with app.app_context():
            db.session.get(Account, seed_user["account"].id).is_active = False
            db.session.commit()

            resp = _post_account(auth_client, "Credit Card", "Visa")

            assert str(escape(card_payoff.NO_ACCOUNT_TO_PAY_FROM.format(
                card="Visa",
            ))) in resp.get_data(as_text=True)
            assert _named(seed_user, "Visa") is None


@pytest.mark.usefixtures("seed_periods_today")
class TestARetypeIntoACardMakesItsPayment:
    """The update door makes the payment when a re-type brings a card into being."""

    def test_a_savings_account_owing_500_becomes_a_card_paid_off_from_this_paycheck(
        self, app, auth_client, seed_user,
    ):
        """Savings held at -$500.00, re-typed into a card and renamed: $500.00 now, then $0.00.

        The account opened before today, so the repeat's first row is the
        current paycheck's, dated its payday (ruling plan#2669), as for a
        payment set up by hand.  Re-typed, the -$500.00 it held reads as
        $500.00 owed from its opening, so that row pays what the card owes
        the day before it, $500.00, and the next pays what is left:
        $500.00 - $500.00 = $0.00.  The payment is named after the name the
        same save gives the account.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            resp = _retype(auth_client, savings.id, "Credit Card", name="Visa")

            assert resp.status_code == 200
            assert b"updated" in resp.data
            [template] = _payments_into(savings.id)
            assert template.name == card_payoff.PAYOFF_NAME.format(card="Visa")
            assert template.from_account_id == seed_user["account"].id
            assert template.card_payment_settings.card_account_id == savings.id
            assert template.card_payment_settings.mode_id == (
                ref_cache.card_payment_mode_id(CardPaymentModeEnum.CARD_BALANCE)
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            rows = (
                db.session.query(Transfer)
                .filter_by(transfer_template_id=template.id)
                .order_by(Transfer.due_date).all()
            )
            # Every paycheck from the one holding today.
            from_now = [
                period.period_id for period in ctx.calendar().saved()
                if period.end_date >= ctx.as_of
            ]
            assert from_now
            assert [row.pay_period_id for row in rows] == from_now
            assert rows[0].due_date <= ctx.as_of
            assert all(row.amount is None for row in rows)
            basis = ctx.amounts()
            assert [resolve_transfer_amount(row, basis) for row in rows[:2]] == [
                Decimal("500.00"), Decimal("0.00"),
            ]

    def test_saving_a_card_keeps_its_one_payment(
        self, app, auth_client, seed_user,
    ):
        """A card saved as it stands brings no card into being: saved, still one payment.

        Made through the create door, it holds "Pay off Visa".  A save that
        asked the payment's refusals again would find that name taken and
        refuse every save of the card.
        """
        with app.app_context():
            _post_account(auth_client, "Credit Card", "Visa")
            card = _named(seed_user, "Visa")
            [payment] = _payments_into(card.id)

            resp = _retype(auth_client, card.id, "Credit Card", name="Visa")

            assert b"updated" in resp.data
            assert [template.id for template in _payments_into(card.id)] == [
                payment.id,
            ]

    def test_the_grids_account_cannot_become_a_card(
        self, app, auth_client, seed_user,
    ):
        """Checking, the account the grid opens on, is refused as a card and left as it was."""
        with app.app_context():
            checking_id = seed_user["account"].id
            checking_type_id = seed_user["account"].account_type_id

            resp = _retype(auth_client, checking_id, "Credit Card")

            assert str(escape(card_payoff.PRIMARY_CANNOT_BECOME_A_CARD.format(
                card="Checking",
            ))) in resp.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Account, checking_id).account_type_id == (
                checking_type_id
            )
            assert _payments_into(checking_id) == []
