"""
Shekel Budget App -- Paid on a payment due on each statement's due date records its due date

Plan step **credit_card:CC-6**, its task plan#4044 (ruling **plan#2889**: "Marking
it Paid records the scheduled date by default; a later real day goes under 'Money
moved on', and a day after the due date means interest").  A transfer entering the
settled band with no day typed borrows the day ``transfer_service._status.
_fallback_day`` gives it, the one derivation every Paid door reaches; for a
transfer whose definition repeats on each statement's due date that day is its due
date, never after today (ruling R-EJ).  Decided under R-BAL207: the card's
worked-out scheduled payment and a fixed one set up as a plain recurring transfer
alike, and a payoff row each payday keeps the click day.  Pinned through two doors
a browser posts: the grid's Paid (which the popover's Paid, the keyboard and the
palette post too) and the popover's Status dropdown.

**The calendar** is ``seed_periods``': paychecks from Fri Jan 2 2026, so Jan 16's
covers Jan 16-29 and Jan 30's Jan 30-Feb 12.  **The card** closes on the 5th and
falls due on the 27th; its $0.00 owed is observed Jan 2, so its books open Jan 1,
before every row.  **$800 goes on it Jan 3**, on the statement closing Jan 5 and
due Jan 27, whose payment row is in the Jan 16 paycheck.  **Today is held at Feb
2**, in the Jan 30 paycheck, six days after that due date, unless a class
restates it.

**The failing control**: before this task each Paid below recorded Feb 2, the
click day, so the card read $800.00 owed through Feb 1 and the first assertion
of every scheduled case read Feb 2.
"""

from datetime import date
from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import CardPaymentModeEnum, SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services.balance_at import BalanceContext, balance_at
from app.services.settle_day import SettleDay, recorded_settle_day
from app.services.transfer_legs import transfer_sides
from tests._test_helpers import freeze_today
from tests.test_routes._statement_forms import form_fields
from tests.test_services.test_card_payment_price import (
    _card,
    _row,
    _spend,
    _works_out,
)
from tests.test_services.test_due_date_repeat_read_pass import _generate, _payment

#: Today for every class but one: in the Jan 30 paycheck, after the Jan 27 due date.
_FEB_2 = date(2026, 2, 2)

#: The statement closing Jan 5 falls due on it, in the Jan 16 paycheck.
_JAN_27 = date(2026, 1, 27)

#: The payday of the Jan 16 paycheck, a payoff row's date.
_JAN_16 = date(2026, 1, 16)


@pytest.fixture(name="today")
def _today():
    """The day every request and pass here is held at; a class may restate it."""
    return _FEB_2


@pytest.fixture(autouse=True)
def _frozen(monkeypatch, today):
    """Hold every clock the doors and their passes read at *today*, before any login."""
    freeze_today(monkeypatch, today)


def _scheduled(seed_user, seed_periods):
    """The card's statement balance scheduled on each due date, $800 on it Jan 3.

    Returns:
        ``(card, the Jan 27 payment row)``, committed.
    """
    card = _card(seed_user)
    template = _works_out(
        seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE, on_due_dates=True,
    )
    _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
    return card, _row(template, _JAN_27)


def _days(row):
    """Both sides' recorded days of *row*, expense side first, read fresh."""
    db.session.expire_all()
    return [
        recorded_settle_day(leg.record)
        for leg in transfer_sides(db.session.get(Transfer, row.id))
    ]


def _borrowed(day):
    """What a side with no evidence of its own records on *day*."""
    return SettleDay(day=day, basis=SettledDayBasisEnum.BORROWED)


def _press_paid(auth_client, row):
    """Press the grid's Paid on *row*, as its cell, the keyboard and the palette post."""
    response = auth_client.post(f"/transfers/instance/{row.id}/mark-done")
    assert response.status_code == 200, response.data[:400]


def _set_paid_in_the_popover(auth_client, row):
    """Set *row*'s popover Status to Paid and Save, posting every control it renders."""
    page = auth_client.get(f"/transfers/{row.id}/full-edit")
    assert page.status_code == 200
    door = f"/transfers/instance/{row.id}"
    rendered = form_fields(page.data.decode(), door, attribute="hx-patch")
    payload = [pair for pair in rendered if pair[0] != "status_id"]
    payload.append(("status_id", str(ref_cache.status_id(StatusEnum.DONE))))
    response = auth_client.patch(door, data=MultiDict(payload))
    assert response.status_code == 200, response.data[:400]


def _owed(card, day):
    """The card's balance at the end of *day*, read on a pass held at today."""
    return balance_at(card, BalanceContext.build(card.user_id), day)


class TestAScheduledPaymentRecordsItsDueDate:
    """Paid after the due date, with no day typed, records the due date on both sides."""

    def test_the_cards_worked_out_payment(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Pressed Feb 2, the Jan 27 statement payment is paid Jan 27.

        It pays the $800.00 statement, so the card owes $800.00 at the end of
        Jan 26 and nothing at the end of Jan 27: the payment is on the day the
        card company took it, not the day it was marked, and no interest runs
        for the six days between.
        """
        with app.app_context():
            card, row = _scheduled(seed_user, seed_periods)

            _press_paid(auth_client, row)

            assert _days(row) == [_borrowed(_JAN_27), _borrowed(_JAN_27)]
            assert db.session.get(Transfer, row.id).settled_figure == Decimal("800.00")
            assert _owed(card, date(2026, 1, 26)) == Decimal("-800.00")
            assert _owed(card, _JAN_27) == Decimal("0.00")

    def test_a_fixed_payment_set_up_as_a_plain_recurring_transfer(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $200.00 transfer on each due date, pressed Feb 2, is paid Jan 27."""
        with app.app_context():
            template = _payment(seed_user, _card(seed_user))
            _generate(seed_user, template)
            db.session.commit()
            row = _row(template, _JAN_27)
            assert row.pay_period_id == seed_periods[1].id

            _press_paid(auth_client, row)

            assert _days(row) == [_borrowed(_JAN_27), _borrowed(_JAN_27)]

    def test_the_popovers_status_dropdown_records_it_too(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Status set to Paid in the popover and saved: both sides on Jan 27."""
        with app.app_context():
            _, row = _scheduled(seed_user, seed_periods)

            _set_paid_in_the_popover(auth_client, row)

            assert _days(row) == [_borrowed(_JAN_27), _borrowed(_JAN_27)]


class TestNeverAfterToday:
    """Paid before the due date records today: a day ahead is refused (ruling R-EJ)."""

    @pytest.fixture(name="today")
    def _jan_20(self):
        """Today is Jan 20, in the Jan 16 paycheck, a week before the due date."""
        return date(2026, 1, 20)

    def test_pressed_before_its_due_date_it_is_paid_today(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Pressed Jan 20, the Jan 27 payment records Jan 20."""
        with app.app_context():
            _, row = _scheduled(seed_user, seed_periods)

            _press_paid(auth_client, row)

            assert _days(row) == [
                _borrowed(date(2026, 1, 20)), _borrowed(date(2026, 1, 20)),
            ]


class TestAPayoffRowKeepsTheClickDay:
    """A payoff row each payday is sent by hand, so Paid records the day it is pressed."""

    def test_the_jan_16_payoff_row_pressed_feb_2_records_feb_2(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Its date is Jan 16, its paycheck's payday; Paid on Feb 2 records Feb 2."""
        with app.app_context():
            card = _card(seed_user)
            template = _works_out(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
            _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
            row = _row(template, _JAN_16)

            _press_paid(auth_client, row)

            assert _days(row) == [_borrowed(_FEB_2), _borrowed(_FEB_2)]
