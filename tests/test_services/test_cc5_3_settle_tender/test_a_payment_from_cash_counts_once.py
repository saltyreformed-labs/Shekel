"""A bill on Checking paid from another cash account counts once, wherever it is read.

Task plan#3232, measuring the premise ruling plan#3227 rests on ("Paid from SECU
Savings leaves checking at $1,000 and SECU Savings at $4,200") before "Paid from"
offers every cash account: a payment filed on Savings, or on an HYSA, under a bill
that lives on Checking is counted once by the paying account's balance, not at all
by Checking's, once by the posted ledger, and once by a true-up that follows it.

The payment is put on the other account as a settled row's "Paid from"
correction puts it there, because the gate still refuses those accounts as a
tender until this task changes it: ``movement_account.re_point_movement``, the
one write of a movement's account, then ``posting_service.
sync_transaction_postings``, which the correction door runs after the seam
(``transaction_service._door``).  Without the second the posted ledger keeps the
payment on Checking, which a first draft of this test measured.  Every balance is read through
``balance_at.balance_at``, the producer that dispatches on the account's kind, so
the HYSA is valued by the INTEREST fold (``balance_at._asset_fold``) and Savings
and Checking by the cash fold.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import balance_at, posting_service
from app.services.account_projection import AccountProjectionKind, classify_account
from app.services.balance_at import BalanceContext
from app.services.movement_account import re_point_movement
from app.services.row_valuation import settled_figure
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    append_balance_assertion,
    create_account_of_type,
    create_hysa_account,
    generate_row_of,
    ledger_net,
    linked_ledger_account,
    make_expense_template,
)

from ._helpers import _movement

_BILL = Decimal("800.00")
_OPENING = Decimal("5000.00")
#: A day inside ``seed_periods`` (2026-01-02 on, ten paychecks) and after the
#: suite's frozen today, 2026-03-20, so the reading holds every row the test
#: writes.
_LATER = date(2026, 5, 1)
#: 16:00 in New York on the frozen today: the day the payment is dated.
_TRUE_UP_AT = datetime(2026, 3, 20, 20, 0, tzinfo=timezone.utc)


def _balance(account, as_of=_LATER):
    """The account's balance on *as_of*, read as the screens read it."""
    return balance_at.balance_at(account, BalanceContext.build(account.user_id), as_of)


def _savings(seed_user, _periods):
    """A Savings account holding `$5,000.00`, opened the day before the frozen today."""
    return create_account_of_type(
        seed_user, db.session, "Savings", "SECU Savings", anchor_balance=_OPENING,
    )


def _hysa(seed_user, seed_periods):
    """An HYSA holding `$5,000.00` from its paycheck's first day, at 0% so no figure accrues."""
    return create_hysa_account(
        seed_user, db.session, seed_periods[5], _OPENING, apy=Decimal("0"),
    )


@pytest.mark.parametrize("make_account", [_savings, _hysa], ids=["savings", "hysa"])
class TestABillPaidFromAnotherCashAccount:
    """An `$800.00` bill on Checking, paid from Savings or an HYSA holding `$5,000.00`."""

    @staticmethod
    def _bill(seed_user, seed_periods):
        """The `$800.00` bill on Checking in the paycheck holding the frozen today."""
        template = make_expense_template(
            db.session, seed_user, amount=str(_BILL), name="Rent", category_key="Rent",
        )
        row = generate_row_of(template, seed_periods[5])
        db.session.commit()
        return row

    @staticmethod
    def _paid_from(txn, account):
        """Mark *txn* Paid, then move its payment onto *account*: Paid from that account."""
        settle_transaction(txn)
        db.session.commit()
        movement = _movement(txn)
        assert re_point_movement(movement, account.id, day=movement.settled_on, press=None)
        posting_service.sync_transaction_postings(txn)
        db.session.commit()
        return _movement(txn)

    def test_the_paying_account_counts_it_once_and_checking_not_at_all(
        self, app, seed_user, seed_periods, make_account,
    ):
        """Checking ends where it began; the paying account is `$800.00` lower, once.

        Before the payment Checking's projection holds the bill (its starting
        figure less `$800.00`) and the paying account reads `$5,000.00`.  Paid
        from the other account, Checking reads its starting figure again and
        the paying account `$5,000.00 - $800.00 = $4,200.00`.  The posted
        ledger moves the same way: the paying account's ledger account by
        `-$800.00`, Checking's by nothing.  The row is still worth `$800.00`
        to its paycheck.
        """
        with app.app_context():
            checking = seed_user["account"]
            paying = make_account(seed_user, seed_periods)
            db.session.commit()
            if make_account is _hysa:
                assert classify_account(paying) is AccountProjectionKind.INTEREST
            checking_start = _balance(checking)
            txn = self._bill(seed_user, seed_periods)
            scenario_id = txn.scenario_id
            checking_ledger = linked_ledger_account(db.session, checking.id)
            paying_ledger = linked_ledger_account(db.session, paying.id)
            checking_posted = ledger_net(db.session, checking_ledger.id, scenario_id)
            paying_posted = ledger_net(db.session, paying_ledger.id, scenario_id)
            assert _balance(checking) == checking_start - _BILL
            assert _balance(paying) == _OPENING

            movement = self._paid_from(txn, paying)

            assert movement.account_id == paying.id
            assert movement.amount == _BILL
            assert txn.account_id == checking.id
            assert settled_figure(txn) == _BILL
            assert _balance(checking) == checking_start
            assert _balance(paying) == Decimal("4200.00")
            assert ledger_net(db.session, paying_ledger.id, scenario_id) == paying_posted - _BILL
            assert ledger_net(db.session, checking_ledger.id, scenario_id) == checking_posted

    def test_a_true_up_after_the_payment_counts_it_once(
        self, app, seed_user, seed_periods, make_account,
    ):
        """The bank's `$4,200.00` on the payment's day: the account stays `$4,200.00`.

        A true-up dated the day the payment left absorbs it, so the account
        reads the asserted figure and not `$4,200.00 - $800.00 = $3,400.00`.
        Checking trued up to its starting figure the same day reads that figure,
        not `$800.00` less: the bill was paid from elsewhere.
        """
        with app.app_context():
            checking = seed_user["account"]
            paying = make_account(seed_user, seed_periods)
            db.session.commit()
            checking_start = _balance(checking)
            txn = self._bill(seed_user, seed_periods)
            movement = self._paid_from(txn, paying)
            assert movement.settled_on == _TRUE_UP_AT.date()

            append_balance_assertion(db.session, paying, "4200.00", at=_TRUE_UP_AT)
            append_balance_assertion(db.session, checking, str(checking_start), at=_TRUE_UP_AT)
            db.session.commit()

            assert _balance(paying) == Decimal("4200.00")
            assert _balance(checking) == checking_start
