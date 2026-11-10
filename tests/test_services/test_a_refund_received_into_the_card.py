"""
Shekel Budget App -- a refund marked Received into the card lowers what the card owes

Task plan#3232, ruling plan#3310 (developer 2026-11-10, answer A): on money coming
in, the box reads "Received into" and lists the row's own account, every account
that holds cash, then every active card, because Credit refuses money coming in
and the box is the only way to say a refund landed on a card.  A bill's "Paid
from" stays cash only (ruling plan#3227).  One producer feeds the box and the
gate (``movement_account.paid_from_accounts``).

**The worked example**, on the card payment's calendar
(``test_card_payment_follows_what_is_owed``): paydays Jan 2, Jan 16 and Jan 30;
the Visa closes on the 5th and falls due on the 27th; Checking opens at
$1,000.00.  $800.00 of groceries go on the Visa Jan 3 by a Credit press, and a
$25.00 Visa refund is planned on Checking in the Jan 2 paycheck, dated Jan 8.
Marked Received into the Visa on Jan 8: the Visa owes 800 - 25 = $775.00,
Checking stays $1,000.00, and the Jan 16 payoff row is $775.00.  Left on
Checking it reads 1,000 + 25 = $1,025.00, the Visa $800.00, and the payoff row
$800.00, $25.00 too high.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.services.settle_day import SettleDay
from app.services.transaction_service import settle_transaction
from tests._test_helpers import add_txn, create_account_of_type
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _balances,
    _on_the_card,
    _payoff,
)
from tests.test_services.test_card_payment_price import _card, _price

_JAN_8 = date(2026, 1, 8)
_JAN_15 = date(2026, 1, 15)
_JAN_16 = date(2026, 1, 16)
#: A pass held after the refund's day and before the payoff row's.
_AS_OF = date(2026, 1, 10)


def _refund(seed_user, period):
    """The `$25.00` Visa refund, money coming in planned on Checking, dated Jan 8."""
    row = add_txn(
        db.session, seed_user, period, "Visa refund", Decimal("25.00"),
        is_income=True, due_date=_JAN_8,
    )
    db.session.commit()
    return row


def _received(row, account=None):
    """Mark *row* Received on Jan 8, into *account* when one is named."""
    settle_transaction(
        row,
        settle_day=SettleDay(day=_JAN_8, basis=SettledDayBasisEnum.ENTERED),
        tender_account_id=None if account is None else account.id,
    )
    db.session.commit()


class TestARefundReceivedIntoTheVisa:
    """The developer's example, and the same refund left on Checking as its control."""

    def test_the_visa_owes_775_and_checking_stays_1000(self, seed_user, seed_periods):
        """Received into the Visa: 800 - 25 = $775.00 owed, Checking $1,000.00.

        Read at the end of Jan 15, the day before the payoff row: Checking holds
        its opening $1,000.00 (the groceries went on the Visa, the refund came
        back to it), and the Visa owes $800.00 - $25.00 = $775.00, which is what
        the Jan 16 payoff row covers.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        payoff = _payoff(seed_user, card)
        refund = _refund(seed_user, seed_periods[0])

        _received(refund, card)

        assert _balances(seed_user, card, _AS_OF, _JAN_15) == (
            Decimal("1000.00"), Decimal("-775.00"),
        )
        assert _price(payoff, _JAN_16, _AS_OF) == Decimal("775.00")

    def test_left_on_checking_it_reads_1025_and_the_visa_800(
        self, seed_user, seed_periods,
    ):
        """The control: Received with nothing named lands on the row's own Checking.

        Checking reads 1,000 + 25 = $1,025.00, the Visa still owes $800.00, and
        the Jan 16 payoff row is $800.00: what option B of ruling plan#3310
        called $25.00 too high.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        payoff = _payoff(seed_user, card)
        refund = _refund(seed_user, seed_periods[0])

        _received(refund)

        assert _balances(seed_user, card, _AS_OF, _JAN_15) == (
            Decimal("1025.00"), Decimal("-800.00"),
        )
        assert _price(payoff, _JAN_16, _AS_OF) == Decimal("800.00")


class TestOnlyMoneyComingInMayNameACard:
    """The one gate: a card for a refund, never for a bill (rulings plan#3310, plan#3227)."""

    def test_a_bill_paid_from_the_visa_is_refused_and_writes_nothing(
        self, seed_user, seed_periods,
    ):
        """The same $25.00 as a bill: Paid naming the Visa is Credit's, refused whole."""
        card = _card(seed_user)
        bill = add_txn(
            db.session, seed_user, seed_periods[0], "Hotel", Decimal("25.00"),
            due_date=_JAN_8,
        )
        db.session.commit()

        with pytest.raises(ValidationError) as refused:
            settle_transaction(bill, tender_account_id=card.id)

        assert str(refused.value) == (
            "'Visa' is a credit card, so a payment cannot be paid from it: "
            "Credit puts a bill on a card, and CC puts a purchase on one."
        )
        db.session.rollback()
        assert bill.covering_movements == []

    def test_a_refund_into_an_account_that_holds_no_cash_is_refused_naming_the_cards(
        self, seed_user, seed_periods,
    ):
        """A 401(k) is neither cash nor a card: the sentence names all three it may be."""
        _card(seed_user)
        retirement = create_account_of_type(
            seed_user, db.session, "401(k)", "Work 401(k)",
        )
        db.session.commit()
        refund = _refund(seed_user, seed_periods[0])

        with pytest.raises(ValidationError) as refused:
            settle_transaction(refund, tender_account_id=retirement.id)

        assert str(refused.value) == (
            "'Work 401(k)' is not an account money can be received into: pick "
            "this row's own account, one that holds cash (checking, savings, "
            "an HYSA or a money market account), or a credit card."
        )
        db.session.rollback()
        assert refund.covering_movements == []
