"""
Shekel Budget App -- the analytics calendar counts a purchase on the card once

Ruling plan#3526 (plan task plan#3529): the calendar's day, month and year
totals count what leaves the balance account.  A purchase on the card is drawn
on its own day, marked as the card's, and is in no figure; the card's payment
out of checking is what counts for it.  Plan task plan#2932 measured the double
count on 2026-11-11: $800 on the card Jan 3 read January expenses $1,600 and
net -$1,600, while checking's month-end balance fell $800.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16, Jan 30, Feb 13
and fortnightly on.  The card (``Visa``) closes on the 5th and falls due on the
27th, and its payment works out the card's whole balance each payday
(:mod:`tests.test_services.test_card_payment_price`'s fixture), so a purchase
is paid off by the next payday after its date (ruling plan#2910).  Checking
starts at $1,000.  Each test holds the day it reads on, after the card's books
open on Jan 2.
"""

from datetime import date
from decimal import Decimal

from app.enums import CardPaymentModeEnum
from app.services import calendar_service
from tests._test_helpers import freeze_today
from tests.test_services.test_card_payment_price import _card, _spend, _works_out

_ZERO = Decimal("0")


def _month(seed_user, monkeypatch, month, today):
    """The month view of checking's calendar as it renders on *today*."""
    freeze_today(monkeypatch, today)
    return calendar_service.get_month_detail(
        seed_user["user"].id, 2026, month, today=today, user_settings=None,
    )


def _drawn(summary, day):
    """The day's entries as ``(name, amount, member it is on or None)`` triples."""
    return [(e.name, e.amount, e.other_member) for e in summary.day_entries[day]]


def test_bought_in_one_paycheck_and_paid_off_in_the_next(
    seed_user, seed_periods, monkeypatch,
):
    """$800 on the card Jan 3, read on Jan 5: January is $800 out, once.

    The groceries are drawn on Jan 3, the card's, and that day folds to $0:
    nothing leaves checking for them until the payment.  Jan 16's payment
    works out the $800 the card owes on Jan 15, the one $800 of January.  The
    Jan 2 and Jan 30 payments work out $0.00, the card owing nothing on Jan 1
    and nothing after Jan 16.  January: income 0, expenses 800, net -800,
    which is the month-end balance's fall, 1,000 - 800 = 200.  So far
    (through Jan 5) is $0 and Remaining $800.  It read expenses $1,600, the
    Jan 3 day $800 and So far $800 before the ruling.
    """
    card = _card(seed_user)
    _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
    _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

    january = _month(seed_user, monkeypatch, 1, date(2026, 1, 5))

    assert _drawn(january, 3) == [("Groceries", Decimal("800.00"), card)]
    assert january.day_totals[3] == (_ZERO, _ZERO)
    assert _drawn(january, 16) == [("Transfer to Visa", Decimal("800.00"), None)]
    assert january.day_totals[16] == (_ZERO, Decimal("800.00"))
    assert (january.total_income, january.total_expenses, january.net) == (
        _ZERO, Decimal("800.00"), Decimal("-800.00"),
    )
    assert january.daily.daily_balances[31] == Decimal("200.00")
    assert (january.daily.elapsed_expense, january.daily.remaining_expense) == (
        _ZERO, Decimal("800.00"),
    )

    year = calendar_service.get_year_overview(
        seed_user["user"].id, 2026, user_settings=None,
    )
    assert (year.annual_expenses, year.annual_net) == (
        Decimal("800.00"), Decimal("-800.00"),
    )


def test_bought_and_paid_off_in_one_paycheck(seed_user, seed_periods, monkeypatch):
    """Bought Jan 14 with its row in the Jan 16 paycheck, read on Jan 16: $800, not $1,600.

    The row is drawn on Jan 16, its date clamped into its paycheck, beside the
    payment that pays it off the same day; the day folds to the payment's $800.
    """
    card = _card(seed_user)
    _spend(seed_user, card, seed_periods[1], "800.00", date(2026, 1, 14))
    _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

    january = _month(seed_user, monkeypatch, 1, date(2026, 1, 16))

    assert _drawn(january, 16) == [
        ("Groceries", Decimal("800.00"), card),
        ("Transfer to Visa", Decimal("800.00"), None),
    ]
    assert january.day_totals[16] == (_ZERO, Decimal("800.00"))
    assert january.total_expenses == Decimal("800.00")


def test_bought_on_the_last_day_of_the_month_counts_in_the_next(
    seed_user, seed_periods, monkeypatch,
):
    """Bought Jan 31, read on Jan 5: January $0 and February $800, the month checking pays.

    The Jan 30 payment works out the card's balance at the end of Jan 29, $0;
    the Feb 13 payment works out the $800 owed from Jan 31.  Counting the
    purchase on its own day would put $800 in January and $0 in February,
    where checking's money moves the other way round.
    """
    card = _card(seed_user)
    _spend(seed_user, card, seed_periods[2], "800.00", date(2026, 1, 31))
    _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

    january = _month(seed_user, monkeypatch, 1, date(2026, 1, 5))
    february = _month(seed_user, monkeypatch, 2, date(2026, 1, 5))

    assert _drawn(january, 31) == [("Groceries", Decimal("800.00"), card)]
    assert january.total_expenses == _ZERO
    assert _drawn(february, 13) == [("Transfer to Visa", Decimal("800.00"), None)]
    assert february.total_expenses == Decimal("800.00")
