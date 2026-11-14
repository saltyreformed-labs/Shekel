"""
Shekel Budget App -- Undo CC says what the card keeps (plan task plan#3095)

Ruling **plan#3955**: Undo CC takes a row off the card (plan task plan#3055),
and where the card's payoff already sent the money, the card keeps it.  The
popover says so before the press: a read that changes nothing works out the
card's balance as if the row's payment were back on checking, through the one
fold the card's balance always is (``balance_at.cash_balance_without_movement``),
and the line shows when that is above $0.00
(``transaction_service.undo_credit_leaves_in_credit``).

**The one example** (plan#3095's): checking opens at $1,000.00; $800 of
groceries dated Jan 3, in the Jan 2 paycheck, put on the Visa by Credit; the
Visa's payoff row each payday works out what it owes, so Jan 16's is $800.
Each pass is read on Jan 20.  Each of the three worked cases checks the read
against the card's balance after the real Undo CC, on a fresh pass: the line
says what the press then does.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ForeignAccountError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.balance_at import (
    BalanceContext,
    balance_at,
    cash_balance_at,
    cash_balance_without_movement,
)
from tests.test_services.test_card_payment_follows_what_is_owed import _payoff, _true_up
from tests.test_services.test_card_payment_price import _card, _row, _settle
from tests.test_services.test_credit_puts_the_row_on_the_card import _groceries, _press

_ZERO = Decimal("0.00")
_EIGHT_HUNDRED = Decimal("800.00")
_JAN_3 = date(2026, 1, 3)
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_20 = date(2026, 1, 20)
_JAN_21 = date(2026, 1, 21)
_JAN_31 = date(2026, 1, 31)


def _pass(seed_user):
    """A read pass for the owner held at Jan 20."""
    return BalanceContext.build(seed_user["user"].id, as_of=_JAN_20)


def _on_the_visa(seed_user, seed_periods):
    """The Visa with its payoff, and the $800 groceries put on it by Credit."""
    card = _card(seed_user)
    template = _payoff(seed_user, card)
    row = _press(_groceries(seed_user, seed_periods[0]))
    return card, template, row


def _undo(row):
    """Press Undo CC on *row* and commit."""
    transaction_service.undo_credit(row)
    db.session.commit()
    db.session.expire_all()


def _card_after_the_press(seed_user, card, row, day):
    """Press Undo CC on *row*; return what the card holds at the end of *day*, read fresh."""
    _undo(row)
    return cash_balance_at(card, _pass(seed_user), day)


class TestTheLineSaysWhatTheCardKeeps:
    """The card's balance without the payment, and the line drawn from it."""

    def test_a_payoff_marked_paid_leaves_the_card_800_in_credit(
        self, seed_user, seed_periods,
    ):
        """Jan 16's $800 payoff marked Paid: the Visa would be $800.00 in credit.

        Now: -800 (Jan 3) + 800 (Jan 16) = $0.00.  Without the groceries'
        payment: 0 + 800 = $800.00.  After the press the card reads that
        $800.00 on Jan 20 and still on Jan 31, Jan 30's payoff working out
        $0.00 against a card owing nothing, and checking reads 1,000 - 800 -
        800 = -$600.00 from Jan 21, the groceries unpaid on it again
        (plan#3095's figures).
        """
        card, template, row = _on_the_visa(seed_user, seed_periods)
        _settle(_row(template, _JAN_16), _JAN_16)
        ctx = _pass(seed_user)
        assert cash_balance_at(card, ctx, _JAN_20) == _ZERO

        left = transaction_service.undo_credit_leaves_in_credit(row, ctx)

        assert (left.card.id, left.figure) == (card.id, _EIGHT_HUNDRED)
        _undo(row)
        after = _pass(seed_user)
        assert [cash_balance_at(card, after, day) for day in (_JAN_20, _JAN_31)] == [
            _EIGHT_HUNDRED, _EIGHT_HUNDRED,
        ]
        assert balance_at(seed_user["account"], after, _JAN_21) == Decimal("-600.00")

    def test_a_true_up_after_the_payoff_holds_the_card_at_zero(
        self, seed_user, seed_periods,
    ):
        """Paid, then the Visa trued up to owing $0.00 on Jan 18: no line.

        Without the payment the card holds 0 + 800 = $800.00 until Jan 18,
        and the true-up says $0.00 from then, so Jan 20 reads $0.00.  Today's
        balance plus the purchase would say 0 + 800 = $800.00 in credit
        where the card reads $0.00 (ruling plan#3955's option C).
        """
        card, template, row = _on_the_visa(seed_user, seed_periods)
        _settle(_row(template, _JAN_16), _JAN_16)
        _true_up(card, "0.00", _JAN_18)
        ctx = _pass(seed_user)
        movement_id = status_seam.covering_movement_of(row).id

        assert cash_balance_without_movement(card, ctx, _JAN_20, movement_id) == _ZERO
        assert transaction_service.undo_credit_leaves_in_credit(row, ctx) is None
        assert _card_after_the_press(seed_user, card, row, _JAN_20) == _ZERO

    def test_a_payoff_not_yet_marked_has_sent_nothing(
        self, seed_user, seed_periods,
    ):
        """Jan 16's payoff unmarked: nothing was sent, so no line, and it re-prices.

        Read Jan 20 the unmarked row lands Jan 21 (ruling R-G), so the line's
        day holds no payment at all: without the purchase the card owes 0 =
        $0.00 on Jan 20, which is the line's whole answer.  The re-price is
        graded on Jan 21, the day the payoff lands: with the payment
        -800 + 800 = $0.00; without it the payoff works out what is then
        owed, $0.00, so 0 + 0 = $0.00.  Kept at its real $800 it would read
        0 + 800 = $800.00 there.
        """
        card, _template, row = _on_the_visa(seed_user, seed_periods)
        ctx = _pass(seed_user)
        movement_id = status_seam.covering_movement_of(row).id
        assert cash_balance_at(card, ctx, _JAN_21) == _ZERO

        assert [
            cash_balance_without_movement(card, ctx, day, movement_id)
            for day in (_JAN_20, _JAN_21)
        ] == [_ZERO, _ZERO]
        assert transaction_service.undo_credit_leaves_in_credit(row, ctx) is None
        assert _card_after_the_press(seed_user, card, row, _JAN_21) == _ZERO


class TestTheReadChangesNothing:
    """A read before the press: nothing written, nothing left on the pass."""

    def test_the_pass_still_reads_the_card_as_it_is(self, seed_user, seed_periods):
        """On one pass: $0.00, then the what-if's $800.00, then $0.00 again.

        The what-if is never stored where the pass keeps the card's fold, or
        the third read would return $800.00.  Committed and read back, the
        row is still Paid on the Visa from Jan 3: the read wrote nothing.
        """
        card, template, row = _on_the_visa(seed_user, seed_periods)
        _settle(_row(template, _JAN_16), _JAN_16)
        ctx = _pass(seed_user)
        movement_id = status_seam.covering_movement_of(row).id

        readings = [
            cash_balance_at(card, ctx, _JAN_20),
            cash_balance_without_movement(card, ctx, _JAN_20, movement_id),
            cash_balance_at(card, ctx, _JAN_20),
        ]

        assert readings == [_ZERO, _EIGHT_HUNDRED, _ZERO]
        db.session.commit()
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        movement = status_seam.covering_movement_of(row)
        assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
        assert (movement.account_id, movement.settled_on) == (card.id, _JAN_3)

    def test_a_movement_on_another_account_is_refused(self, seed_user, seed_periods):
        """Checking asked to leave out the Visa's payment: refused, not read as it is."""
        _card_account, _template, row = _on_the_visa(seed_user, seed_periods)
        movement_id = status_seam.covering_movement_of(row).id

        with pytest.raises(ValueError, match="holds no dated movement"):
            cash_balance_without_movement(
                seed_user["account"], _pass(seed_user), _JAN_20, movement_id,
            )

    def test_another_owners_pass_is_refused(
        self, seed_user, seed_periods, seed_second_user,
    ):
        """A pass built for another owner cannot read this owner's card."""
        _card_account, template, row = _on_the_visa(seed_user, seed_periods)
        _settle(_row(template, _JAN_16), _JAN_16)

        with pytest.raises(ForeignAccountError):
            transaction_service.undo_credit_leaves_in_credit(
                row, BalanceContext.build(seed_second_user["user"].id, as_of=_JAN_20),
            )


class TestNoLineWhereUndoCcMovesNothing:
    """Undo CC takes nothing off a card: there is no line to draw."""

    def test_a_bill_that_lives_on_the_card(self, seed_user, seed_periods):
        """$800 planned on the Visa and Paid there: its payment stays on its own account."""
        card = _card(seed_user)
        row = _groceries(seed_user, seed_periods[0], account=card)
        transaction_service.settle_transaction(row)
        db.session.commit()
        assert status_seam.covering_movement_of(row).account_id == card.id

        assert transaction_service.undo_credit_leaves_in_credit(row, _pass(seed_user)) is None

    def test_a_row_not_on_a_card(self, seed_user, seed_periods):
        """$800 still Projected on checking: nothing is on a card."""
        _card(seed_user)
        row = _groceries(seed_user, seed_periods[0])

        assert transaction_service.undo_credit_leaves_in_credit(row, _pass(seed_user)) is None
