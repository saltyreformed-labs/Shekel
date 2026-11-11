"""
Shekel Budget App -- "Paid from" cannot move a row off the card (plan task plan#3289)

Ruling **plan#3227**, with plan#3054's comment on plan task plan#2931: a row
Credit put on the card from another account is drawn Credit, and its way off
the card is Undo CC (plan task plan#3055), after which Paid takes it from the
account the owner picks.  So the full-edit popover draws no "Paid from" box on
such a row, and the seam's one reading of a submitted account
(``status_seam.tender_for_status``) refuses one other than the card in a
sentence naming Undo CC (``_refusals.reject_tender_off_the_card``), whichever
door hands it over, rather than re-pointing the payment off the card under
Paid.  An account equal to the card is the echo, and is dropped.

**The one example**: $800 of groceries dated Jan 3, its row on Checking in the
Jan 2 paycheck, put on the Visa by a Credit press.  Savings opens on Jan 2,
the owner's first paycheck, so a payment dated Jan 3 is inside its books.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from tests._cards import add_savings
from tests._test_helpers import add_txn
from tests.test_services.test_card_payment_price import _card
from tests.test_services.test_credit_puts_the_row_on_the_card import _groceries, _press

_EIGHT_HUNDRED = Decimal("800.00")
_JAN_3 = date(2026, 1, 3)
_SAVINGS_OPENS = date(2026, 1, 2)
_UNDO_CC = "Press Undo CC to take it off the card"


def _status(member):
    """The ``ref.statuses.id`` of *member*."""
    return ref_cache.status_id(member)


def _on_the_card(seed_user, period):
    """The Visa, Savings, and the Groceries row a Credit press put on the Visa."""
    card = _card(seed_user)
    savings = add_savings(seed_user, observed_on=_SAVINGS_OPENS)
    db.session.commit()
    return card, savings, _press(_groceries(seed_user, period))


def _correct(row, status, account):
    """Hand *row* to the full edit's door with *status* and "Paid from" *account*."""
    transaction_service.apply_requested_status(
        row, _status(status),
        stated=transaction_service.StatedRecord(tender_account_id=account.id),
    )


def _assert_still_on(card, row_id):
    """Roll back; assert the row is Paid with its $800.00 payment on *card*, dated Jan 3."""
    db.session.rollback()
    db.session.expire_all()
    row = db.session.get(Transaction, row_id)
    movement = status_seam.covering_movement_of(row)
    assert row.status_id == _status(StatusEnum.DONE)
    assert (movement.account_id, movement.amount) == (card.id, _EIGHT_HUNDRED)
    assert row.settled_on == _JAN_3


class TestAnotherAccountIsRefused:
    """Every account but the card, beside every status, is refused naming Undo CC."""

    @pytest.mark.parametrize("names", ["checking", "savings"])
    def test_a_correction_naming_another_account_is_refused(
        self, seed_user, seed_periods, names,
    ):
        """The row's own Checking, or Savings, beside Paid: refused, still $800.00 on the Visa."""
        card, savings, row = _on_the_card(seed_user, seed_periods[0])
        account = seed_user["account"] if names == "checking" else savings

        with pytest.raises(ValidationError, match=_UNDO_CC) as refused:
            _correct(row, StatusEnum.DONE, account)

        assert str(refused.value).startswith("Groceries is on Visa, so")
        _assert_still_on(card, row.id)

    def test_beside_projected_the_sentence_still_names_undo_cc(
        self, seed_user, seed_periods,
    ):
        """Checking beside Projected: the Undo CC sentence, not "is not settling"; nothing moves.

        A revert does take the payment home to Checking, so the two facts do
        not contradict: the sentence names the act that says both.
        """
        card, _savings, row = _on_the_card(seed_user, seed_periods[0])

        with pytest.raises(ValidationError, match=_UNDO_CC):
            _correct(row, StatusEnum.PROJECTED, seed_user["account"])

        _assert_still_on(card, row.id)

    def test_a_mark_paid_naming_another_account_is_refused(
        self, seed_user, seed_periods,
    ):
        """A crafted Mark Paid naming Checking: refused naming Undo CC, not a silent no-op.

        The settle verb reads its tender through the same reading before its
        identity no-op, so the refusal reaches this door too.
        """
        card, _savings, row = _on_the_card(seed_user, seed_periods[0])

        with pytest.raises(ValidationError, match=_UNDO_CC):
            transaction_service.settle_transaction(
                row, tender_account_id=seed_user["account"].id,
            )

        _assert_still_on(card, row.id)


class TestWhatIsNotRefused:
    """The echo, a payment between two cash accounts, a refund, and a bill living on the card."""

    def test_the_card_itself_is_the_echo(self, seed_user, seed_periods):
        """The Visa named beside Paid: nothing changes, nothing is refused."""
        card, _savings, row = _on_the_card(seed_user, seed_periods[0])

        _correct(row, StatusEnum.DONE, card)
        db.session.commit()

        _assert_still_on(card, row.id)

    def test_a_payment_from_checking_still_moves_to_savings(
        self, seed_user, seed_periods,
    ):
        """The control: Paid from Checking, corrected to Savings: the $800.00 moves.

        The refusal is the card's alone: a row on the card is the one row
        whose way off its account is another act.
        """
        _card(seed_user)
        savings = add_savings(seed_user, observed_on=_SAVINGS_OPENS)
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0])
        transaction_service.settle_transaction(row)
        db.session.commit()

        _correct(row, StatusEnum.DONE, savings)
        db.session.commit()

        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        movement = status_seam.covering_movement_of(row)
        assert row.status_id == _status(StatusEnum.DONE)
        assert (movement.account_id, movement.amount) == (savings.id, _EIGHT_HUNDRED)

    def test_a_refund_received_into_the_card_still_moves(self, seed_user, seed_periods):
        """$25.00 Received into the Visa, corrected to Savings: the $25.00 moves.

        Received, not Paid, so the row is not drawn Credit and Undo CC is not
        its way off: "Received into" lists the cards and the cash (ruling
        plan#3310).
        """
        card = _card(seed_user)
        savings = add_savings(seed_user, observed_on=_SAVINGS_OPENS)
        refund = add_txn(
            db.session, seed_user, seed_periods[0], "Refund", Decimal("25.00"),
            is_income=True, due_date=_JAN_3,
        )
        db.session.commit()
        transaction_service.settle_transaction(refund, tender_account_id=card.id)
        db.session.commit()

        _correct(refund, StatusEnum.RECEIVED, savings)
        db.session.commit()

        db.session.expire_all()
        refund = db.session.get(Transaction, refund.id)
        movement = status_seam.covering_movement_of(refund)
        assert refund.status_id == _status(StatusEnum.RECEIVED)
        assert (movement.account_id, movement.amount) == (savings.id, Decimal("25.00"))

    def test_a_bill_that_lives_on_the_card_still_moves(self, seed_user, seed_periods):
        """The $800.00 bill planned on the Visa and Paid there, corrected to Checking: it moves.

        Its payment is on its own account, so it is not a row put on the card
        from another account; "Paid from" lists its own card and the cash
        (ruling plan#3227).
        """
        card = _card(seed_user)
        db.session.commit()
        row = _groceries(seed_user, seed_periods[0], account=card)
        transaction_service.settle_transaction(row)
        db.session.commit()
        assert status_seam.covering_movement_of(row).account_id == card.id, (
            "CONTROL: the bill is paid on its own card"
        )

        _correct(row, StatusEnum.DONE, seed_user["account"])
        db.session.commit()

        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        movement = status_seam.covering_movement_of(row)
        assert row.status_id == _status(StatusEnum.DONE)
        assert (movement.account_id, movement.amount) == (
            seed_user["account"].id, _EIGHT_HUNDRED,
        )
