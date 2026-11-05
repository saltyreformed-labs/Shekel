"""A settled payment moved to another account re-checks the account it LEFT (plan task plan#2613).

The anchor self-heal re-derives an account's opening and true-ups when a
posting delta could have moved them.  Until plan task plan#2613 each caller
named the accounts to re-check from its rows' CURRENT columns, so the account
a reversal had just left was never named: a settled payment whose Paid from
was moved off a card reversed the card's leg and left the card's correction
booked for money no longer there (review plan#2521, finding A1; a defect at
the merge base too).  The self-heal now reads the accounts off the deltas'
own legs, and these cases grade both sides of such a move through the door a
user's correction runs (``transaction_service.apply_requested_status``).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.services import anchor_service, transaction_service
from app.services.anchor_service import AnchorTrueUpOutcome
from tests._ledger_reads import account_net
from tests._test_helpers import create_account_of_type, freeze_today
from ._helpers import (
    _TODAY,
    _opening_day,
    _settled_bill_paid_from,
)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Pin the owner's today, which every new account's opening day reads."""
    freeze_today(monkeypatch, _TODAY)


def _move_paid_from(row, account) -> None:
    """Correct a settled row's Paid from to *account*, as the full edit does."""
    transaction_service.apply_requested_status(
        row, row.status_id,
        stated=transaction_service.StatedRecord(tender_account_id=account.id),
    )
    _db.session.commit()


class TestAMovedPaymentReChecksTheAccountItLeft:
    """The account a settled payment leaves keeps its asserted balance."""

    def test_a_paid_from_moved_off_a_card_re_checks_the_card(
        self, app, db, seed_user, seed_periods,
    ):
        """A $120.00 card payment moved to Checking: the card still reads -$500.00.

        The card opens at -$500.00, asserted on 2026-05-14, and the bill is
        settled from it that day, so the assertion absorbs it: the card's
        ledger reads the -500.00 opening, the -120.00 leg and a +120.00
        true-up.  Moved to Checking, the leg reverses (+120.00 on the card)
        and the card's records hold no payment, so its true-up must return
        to 0.00:

            card     -500.00 (asserted; -380.00 with the card never re-checked)
            Checking 1,000.00 opening (2024) - 120.00 riding on top = 880.00
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

    def test_a_paid_from_moved_off_checking_re_checks_checking(
        self, app, db, seed_user, seed_periods,
    ):
        """A $120.00 payment from Checking moved to Savings: Checking still reads $1,000.00.

        The same move between two cash accounts, off one the row does not
        name: a bill on Savings, paid from the owner's Checking on
        2026-05-14, then corrected to Savings, its own account.  Checking
        opened at $1,000.00 in 2024; the payment leaves $880.00, and a true-up
        asserting $1,000.00 on 2026-05-14 absorbs it with a +120.00
        correction.  Savings opens at $300.00 on 2026-05-14 too.  Moved to
        Savings, Checking's records hold no payment, so its true-up is 0.00:

            Checking 1,000.00 (asserted; 1,120.00 with Checking never re-checked)
            Savings  300.00 (its opening absorbs the payment, as the card's did)
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

            assert account_net(
                _db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert account_net(
                _db.session, savings.id, scenario_id,
            ) == Decimal("300.00")
