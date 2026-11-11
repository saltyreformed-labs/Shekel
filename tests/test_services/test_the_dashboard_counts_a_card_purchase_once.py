"""
Shekel Budget App -- the dashboard's still due counts a purchase on the card once

Ruling plan#3524 (plan task plan#3528): the still-due totals count what still
leaves the balance account.  A bill on the card is listed among the due-soon
bills, chipped to the card, and is paid off by the card's payment out of
checking, which is what the totals count for it.  Plan task plan#2932 measured
the double count on 2026-11-11: $800 on the card Jan 3 read $800 still due in
the Jan 2 paycheck and $800 in Jan 16's.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16 and Jan 30 2026
onward.  The card (``Visa``) closes on the 5th and falls due on the 27th, and
its payment works out the card's whole balance each payday
(:mod:`tests.test_services.test_card_payment_price`'s fixture).  Checking
starts at $1,000.  Each test holds the day it reads on.
"""

from datetime import date
from decimal import Decimal

from app.enums import CardPaymentModeEnum
from app.extensions import db
from app.services import dashboard_service
from tests._test_helpers import add_txn, dashboard_section, freeze_today
from tests.test_services.test_card_payment_price import _card, _spend, _works_out


def _pulse(seed_user, monkeypatch, day):
    """The pulse region as the dashboard renders it on *day*."""
    freeze_today(monkeypatch, day)
    return dashboard_service.compute_pulse_section(
        dashboard_section(seed_user["user"].id, as_of=day),
    )


def _bills(pulse):
    """The due-soon list as ``(name, amount, on_account)`` triples."""
    return [(b["name"], b["amount"], b["on_account"]) for b in pulse["due_soon"]]


def test_bought_in_one_paycheck_and_paid_off_in_the_next(
    seed_user, seed_periods, monkeypatch,
):
    """$800 on the card Jan 3 and $300 of rent on checking Jan 6, read on Jan 5.

    This paycheck owes checking's rent alone, $300.00: the groceries are on the
    card and nothing leaves checking for them until Jan 16.  Next paycheck
    owes the payment, which works out the $800 the card owes on Jan 15.  The
    list draws all three, the groceries chipped to the card; the Jan 2
    payment works out $0.00, the card owing nothing on Jan 1.  It read
    $1,100.00 and $800.00 before the ruling.
    """
    card = _card(seed_user)
    _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
    add_txn(
        db.session, seed_user, seed_periods[0], "Rent", Decimal("300.00"),
        due_date=date(2026, 1, 6),
    )
    db.session.commit()
    _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

    pulse = _pulse(seed_user, monkeypatch, date(2026, 1, 5))

    assert pulse["still_due"]["current_period"] == Decimal("300.00")
    assert pulse["still_due"]["next_period"] == Decimal("800.00")
    assert _bills(pulse) == [
        ("Transfer to Visa", Decimal("0.00"), None),
        ("Groceries", Decimal("800.00"), "Visa"),
        ("Rent", Decimal("300.00"), None),
    ]
    # Checking's balance at the end of the Jan 2 paycheck: $1,000 less the
    # $300 rent, which is what "still due" now agrees with.
    assert pulse["hero"]["balance"] == Decimal("700.00")


def test_bought_and_paid_off_in_one_paycheck(seed_user, seed_periods, monkeypatch):
    """Bought Jan 14 with its row in the Jan 16 paycheck, read on Jan 16: $800, not $1,600."""
    card = _card(seed_user)
    _spend(seed_user, card, seed_periods[1], "800.00", date(2026, 1, 14))
    _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

    pulse = _pulse(seed_user, monkeypatch, date(2026, 1, 16))

    assert pulse["still_due"]["current_period"] == Decimal("800.00")
    assert _bills(pulse) == [
        ("Groceries", Decimal("800.00"), "Visa"),
        ("Transfer to Visa", Decimal("800.00"), None),
    ]
