"""
Shekel Budget App -- the Recurring page names a card's payment by one answer

Plan tasks plan#3700 and plan#3894: the Recurring page shows a card's worked-out
payment's Next date and Amount from the card's one answer for its next payment
(``balance_at.next_card_payment``): the first day one of its rows still planned is
dated on or after the read pass's day, and what its rows dated that day ask.  The
Next date came from the repeat's schedule until plan#3894, so a cancelled, paid or
moved row left the Next date on its own day beside the figure of the row after.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16, Jan 30 2026 and
fortnightly on.  The card (``Visa``) is
:mod:`tests.test_services.test_card_payment_price`'s, its books open on Jan 2,
closing on the 5th and due on the 27th.  $800 of groceries is on it from Jan 3,
counted from that date.  Each case is read through ``recurring_view.build_view``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CardPaymentModeEnum, StatusEnum
from app.extensions import db
from app.services import recurring_view, transfer_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import freeze_today
from tests.test_services.test_card_payment_price import (
    _card,
    _price,
    _row,
    _settle,
    _spend,
    _type_over,
    _works_out,
)

_JAN_5 = date(2026, 1, 5)
_JAN_16 = date(2026, 1, 16)
_JAN_30 = date(2026, 1, 30)
_EIGHT_HUNDRED = Decimal("800.00")
_ZERO = Decimal("0.00")


def _paid_off_each_payday(seed_user, seed_periods, monkeypatch, today, **mode):
    """The $800 on the card and its payment working out the whole balance, today held.

    Returns:
        The payment's committed definition.
    """
    freeze_today(monkeypatch, today)
    card = _card(seed_user)
    _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
    return _works_out(
        seed_user, card,
        mode.get("mode", CardPaymentModeEnum.CARD_BALANCE),
        on_due_dates=mode.get("on_due_dates", False),
    )


def _shown(seed_user, payment, as_of):
    """The payment's row on the Recurring page read on *as_of*: Next, Amount, monthly."""
    view = recurring_view.build_view(
        [], [], [payment], BalanceContext.build(seed_user["user"].id, as_of=as_of),
    )
    [row] = view.transfers.rows
    return row.next_date, row.amount, row.equivalent.monthly


def test_the_next_payday_and_what_it_pays(seed_user, seed_periods, monkeypatch):
    """Read Jan 5: Next Jan 16, $800.00, no monthly figure; Jan 2's $0.00 row is past."""
    payment = _paid_off_each_payday(seed_user, seed_periods, monkeypatch, _JAN_5)

    assert _shown(seed_user, payment, _JAN_5) == (_JAN_16, _EIGHT_HUNDRED, None)


def test_a_cancelled_row_hands_both_to_the_row_after(
    seed_user, seed_periods, monkeypatch,
):
    """Jan 16 cancelled (ruling plan#3854's way to skip one), read Jan 5: Jan 30, $800.00.

    A cancelled row is no credit, so Jan 30 owes the whole $800.  The Next date
    read Jan 16 beside that $800.00 until plan#3894.
    """
    payment = _paid_off_each_payday(seed_user, seed_periods, monkeypatch, _JAN_5)
    row = _row(payment, _JAN_16)
    transfer_service.update_transfer(
        row.id, row.user_id, status_id=ref_cache.status_id(StatusEnum.CANCELLED),
    )
    db.session.commit()

    assert _shown(seed_user, payment, _JAN_5) == (_JAN_30, _EIGHT_HUNDRED, None)


def test_a_row_paid_early_hands_both_to_the_row_after(
    seed_user, seed_periods, monkeypatch,
):
    """Jan 16's $800 paid on Jan 5, read Jan 6: Jan 30, $0.00, the card paid off.

    The Next date read Jan 16, a payment already made, until plan#3894.
    """
    jan_6 = date(2026, 1, 6)
    payment = _paid_off_each_payday(seed_user, seed_periods, monkeypatch, jan_6)
    _settle(_row(payment, _JAN_16), _JAN_5)

    assert _shown(seed_user, payment, jan_6) == (_JAN_30, _ZERO, None)


def test_a_row_moved_into_a_past_paycheck_is_the_grids(
    seed_user, seed_periods, monkeypatch,
):
    """Statement mode on due dates, Jan 27's row moved into the Jan 2 paycheck, read Jan 20.

    The moved row is dated in a paycheck that ended Jan 15 and still owes the
    Jan 5 statement's $800, unmarked: the grid shows it there, so it is not next.
    The next is Feb 27's row, the Feb 5 statement's 800 - 800 = $0.00.  The Next
    date read Jan 27 beside that $0.00 until plan#3894.
    """
    jan_20 = date(2026, 1, 20)
    feb_27 = date(2026, 2, 27)
    payment = _paid_off_each_payday(
        seed_user, seed_periods, monkeypatch, jan_20,
        mode=CardPaymentModeEnum.STATEMENT_BALANCE, on_due_dates=True,
    )
    row = _row(payment, date(2026, 1, 27))
    transfer_service.update_transfer(
        row.id, row.user_id, pay_period_id=seed_periods[0].id,
    )
    db.session.commit()

    assert _price(payment, date(2026, 1, 27), jan_20) == _EIGHT_HUNDRED
    assert _shown(seed_user, payment, jan_20) == (feb_27, _ZERO, None)


def test_two_rows_on_one_day_are_one_payment(seed_user, seed_periods, monkeypatch):
    """Jan 16 typed to $300 and carried into Jan 30's paycheck, read Jan 5: Jan 30, $800.00.

    Jan 16's paycheck then holds no row.  The carried row lands Jan 30 beside Jan
    30's worked-out row, which counts it, as it counts any money its own paycheck
    holds: 800 - 300 = $500.  The day's payment is 300 + 500 = $800.00, where the
    first row in date order alone read $300.00 (plan#3734 is how two rows come to
    share a paycheck).
    """
    payment = _paid_off_each_payday(seed_user, seed_periods, monkeypatch, _JAN_5)
    jan_16 = _row(payment, _JAN_16)
    _type_over(jan_16, "300.00")
    transfer_service.update_transfer(
        jan_16.id, jan_16.user_id, pay_period_id=seed_periods[2].id,
    )
    db.session.commit()

    assert _price(payment, _JAN_30, _JAN_5) == Decimal("500.00")
    assert _shown(seed_user, payment, _JAN_5) == (_JAN_30, _EIGHT_HUNDRED, None)
