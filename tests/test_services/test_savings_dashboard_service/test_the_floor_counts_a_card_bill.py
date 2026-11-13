"""
Shekel Budget App -- the emergency-fund floor counts a bill on the card, not its payment

Plan task plan#3700, decided under R-BAL207 and recorded on that task: the
committed floor on ``/savings`` counts a bill on a card that checking pays, and
no payment into a card, which is the Recurring page's rule (ruling plan#3527).
It counted the card's payment, at its next row times its repeats, and none of the
card's bills, so one purchase on the card read as a monthly commitment and a
paid-up card's bills as none.

**What is read** is ``avg_monthly_expenses``, the higher of the last six
paychecks' settled checking outflow and the committed floor.  Nothing here
settles a row on checking, so it is the floor.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16, Jan 30 2026 and
fortnightly on, 26 a year, so $100 a paycheck is 100 * 26 / 12 = $216.67 a month
and $50 a paycheck is $108.33.  The card (``Visa``) is
:mod:`tests.test_services.test_card_payment_price`'s, its books open on Jan 2,
and its payment from checking works out the whole balance each payday.  Each
read is held on Jan 5, when the Jan 2 row is past and $0.00, and Jan 16's next.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import CardPaymentModeEnum
from app.extensions import db
from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    create_savings_account,
    freeze_today,
    make_expense_template,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from tests.test_services.test_card_payment_price import _card, _spend, _works_out
from tests.test_services.test_the_recurring_page_counts_a_card_bill_once import (
    _phone_on_the_card,
    _plain_transfer,
)

_JAN_5 = date(2026, 1, 5)
_HUNDRED_A_PAYCHECK = Decimal("216.67")  # a month: 100 * 26 / 12
_FIFTY_A_PAYCHECK = Decimal("108.33")  # a month: 50 * 26 / 12


def _floor(seed_user):
    """``avg_monthly_expenses`` on ``/savings``, read on Jan 5."""
    data = savings_dashboard_service.compute_dashboard_data(
        BalanceContext.build(seed_user["user"].id, as_of=_JAN_5),
    )
    return data["avg_monthly_expenses"]


def _water_from_checking(seed_user):
    """A $50 bill every paycheck from checking, committed: the control."""
    template = make_expense_template(
        db.session, seed_user, "50.00", name="Water",
    )
    db.session.commit()
    return template


def test_a_bill_on_the_card_counts_and_its_payment_does_not(
    seed_user, seed_periods, monkeypatch,
):
    """Water from checking and the phone on the card: 108.33 + 216.67 = $325.00.

    A single $2,000 balance on the card makes Jan 16's row, the payment's next,
    $2,000, and the payment is not counted.  Counted at its next row times its
    repeats it read (50 + 2,000) * 26 / 12 = $4,441.67 (measured with that
    figure put back); on the code before plan#3700, $108.33, Jan 2's unmarked
    $0.00 row holding the payment.  The phone counted in neither.
    """
    freeze_today(monkeypatch, _JAN_5)
    card = _card(seed_user)
    _water_from_checking(seed_user)
    _phone_on_the_card(seed_user, card)
    _spend(seed_user, card, seed_periods[0], "2000.00", date(2026, 1, 3))
    _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)

    assert _floor(seed_user) == _FIFTY_A_PAYCHECK + _HUNDRED_A_PAYCHECK


@pytest.mark.usefixtures("seed_periods")
def test_a_card_checking_does_not_pay_counts_none_of_its_bills(seed_user, monkeypatch):
    """The phone on a card with no payment from checking: the water's $108.33 alone.

    Its bills do not leave checking, as no definition from checking pays it.
    """
    freeze_today(monkeypatch, _JAN_5)
    card = _card(seed_user)
    _water_from_checking(seed_user)
    _phone_on_the_card(seed_user, card)

    assert _floor(seed_user) == _FIFTY_A_PAYCHECK


@pytest.mark.usefixtures("seed_periods")
def test_a_fixed_amount_sent_to_the_card_is_not_counted(seed_user, monkeypatch):
    """$500 a month on the 15th into the card beside the phone: $216.67, not $716.67.

    The ruling's stated cost: only the card's bills count.  The control, $50 a
    paycheck to savings from checking, still counts: 216.67 + 108.33 = $325.00.
    """
    freeze_today(monkeypatch, _JAN_5)
    card = _card(seed_user)
    _phone_on_the_card(seed_user, card)
    _plain_transfer(
        seed_user, card, "500.00", MONTHLY, "Extra to the Visa", fires_on_day=15,
    )

    assert _floor(seed_user) == _HUNDRED_A_PAYCHECK

    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("500.00"),
    )
    _plain_transfer(seed_user, savings, "50.00", EVERY_PERIOD, "To savings")

    assert _floor(seed_user) == _HUNDRED_A_PAYCHECK + _FIFTY_A_PAYCHECK
