"""A settled payment moved to another account: the account it LEFT reads its asserted balance.

A settled bill's Paid from is corrected after the fact through the door a
user's correction runs (``transaction_service.apply_requested_status``).  The
payment's movement then stands on the NEW account, and the account it left
holds no payment, so the worked-out read must re-derive that account's
correction from its records and read it back on its assertion.

**These two cases were deleted with the stored ledger's re-check they also
graded** (plan task plan#3097, commit 8c6454559, as
``test_services/test_posting_service/test_a_moved_movement_re_checks_the_account_it_left.py``),
though their figures were already on the read.  The card's review (plan task
plan#3059) planted a read that files a bill's movement under the bill's own
account rather than the account its money moved through
(``cash_ledger._events._settled_facts_by_account``): across the ledger-lines,
integration, lifecycle and cash-walk suites only four tests failed, all in
``tests/test_services/test_cc5_3_settle_tender/``.  Both cases here fail
under that plant too (measured 2026-11-10).  They are restored without the
parity lines, which compared the read with the store that is gone (plan task
plan#3394).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.services import anchor_service, transaction_service
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.settle_day import SettleDay
from tests._ledger_reads import account_net
from tests._test_helpers import (
    create_account_of_type,
    freeze_today,
    generate_row_of,
    make_expense_template,
)

#: The owner's today: every new account's opening observes it, so a payment
#: settled on it is absorbed by that opening.
_TODAY = date(2026, 5, 15)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Pin the owner's today, which every new account's opening day reads."""
    freeze_today(monkeypatch, _TODAY)


def _opening_day(account) -> date:
    """Return the day *account*'s one balance assertion, its opening, observes."""
    return (
        _db.session.query(AccountAnchorHistory.observed_on)
        .filter_by(account_id=account.id)
        .one()
        .observed_on
    )


def _settled_bill_paid_from(seed_user, period, tender, day, *, account=None):
    """Return a $120.00 bill settled from *tender* on *day*, committed.

    The bill's own account is *account*, the seed's Checking when ``None``.
    A payment may be paid from its row's own account, the owner's checking
    account or a credit card.
    """
    template = make_expense_template(
        _db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent", account=account,
    )
    row = generate_row_of(template, period)
    _db.session.commit()
    transaction_service.settle_transaction(
        row,
        settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED),
        tender_account_id=tender.id,
    )
    _db.session.commit()
    return row


def _move_paid_from(row, account) -> None:
    """Correct a settled row's Paid from to *account*, as the full edit does."""
    transaction_service.apply_requested_status(
        row, row.status_id,
        stated=transaction_service.StatedRecord(tender_account_id=account.id),
    )
    _db.session.commit()


class TestAMovedPaymentLeavesTheAccountItLeftOnItsAssertion:
    """The account a settled payment leaves reads its asserted balance."""

    def test_a_paid_from_moved_off_a_card_leaves_the_card_on_its_opening(
        self, app, db, seed_user, seed_periods,
    ):
        """A $120.00 card payment moved to Checking: the card still reads -$500.00.

        The card opens at -$500.00, asserted on its opening day, and the bill
        is settled from it that day, so the assertion absorbs it: the card's
        lines are the -500.00 opening, the -120.00 payment and a +120.00
        true-up.  Moved to Checking, the card's records hold no payment, so
        its true-up returns to 0.00 and Checking carries the payment on top
        of its 2024 opening:

            card     -500.00 opening + 0.00 true-up = -500.00
            Checking 1,000.00 opening - 120.00 payment = 880.00
        """
        with app.app_context():
            checking = seed_user["account"]
            card = create_account_of_type(
                seed_user, _db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            _db.session.commit()
            # On the card's opening day, so the opening ABSORBS the payment.
            row = _settled_bill_paid_from(
                seed_user, seed_periods[0], card, _opening_day(card),
            )
            scenario_id = row.scenario_id
            assert account_net(
                _db.session, card.id, scenario_id,
            ) == Decimal("-500.00"), "CONTROL: the opening absorbs the payment"
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("1000.00"), "CONTROL: Checking holds no payment yet"

            _move_paid_from(row, checking)

            assert row.covering_movements[0].account_id == checking.id
            assert account_net(
                _db.session, card.id, scenario_id,
            ) == Decimal("-500.00")
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("880.00")

    def test_a_paid_from_moved_off_checking_leaves_checking_on_its_true_up(
        self, app, db, seed_user, seed_periods,
    ):
        """A $120.00 payment from Checking moved to Savings: Checking still reads $1,000.00.

        The same move between two cash accounts, off one the row does not
        name: a bill on Savings, paid from the owner's Checking on Savings's
        opening day, then corrected to Savings, its own account.  Checking
        opened at $1,000.00 in 2024; the payment leaves $880.00, and a true-up
        asserting $1,000.00 that day absorbs it with a +120.00 correction.
        Savings opens at $300.00 that day too.  Moved to Savings, Checking's
        records hold no payment, so its true-up is 0.00, and Savings's opening
        absorbs the payment as the card's did:

            Checking 1,000.00 opening + 0.00 true-up = 1,000.00
            Savings  300.00 asserted, the -120.00 payment absorbed = 300.00
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("300.00"),
            )
            _db.session.commit()
            day = _opening_day(savings)
            row = _settled_bill_paid_from(
                seed_user, seed_periods[0], checking, day, account=savings,
            )
            scenario_id = row.scenario_id
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("880.00"), "CONTROL: the payment rides on the 2024 opening"
            assert anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("1000.00"),
                observed_on=day,
            ).outcome is AnchorTrueUpOutcome.COMMITTED
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("1000.00"), "CONTROL: the true-up absorbs the payment"

            _move_paid_from(row, savings)

            assert row.covering_movements[0].account_id == savings.id
            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("300.00")
