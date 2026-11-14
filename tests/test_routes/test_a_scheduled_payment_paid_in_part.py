"""
Shekel Budget App -- a card payment paid in part, or beside an extra one

Plan step **credit_card:CC-6**, its task plan#4047 (rulings **R-CC22**,
**plan#2888**, **plan#2889**; plan#2679's third bullet before its split).  The
card's payment is switched on its page to the statement balance on each
statement's due date, and then, through the doors a browser posts:

* **the partial**: the owner types a smaller figure over a due date's row in the
  full-edit popover a click on the row's cell in checking's grid opens, and the
  next statement pays what is left;
* **the extra**: the owner sends the card a one-time transfer from the transfer
  form, and the due date it is dated on or before pays that much less; one
  dated after the due date, even in the same paycheck, leaves that row whole and
  comes off the next statement;
* **the Recurring page** names the payment by its next row: that row's day under
  Next, and its figure, worked out or typed over, under Amount (plan#3894's
  reader, ``balance_at.next_card_payment``).

**The same two on payoff rows each payday**, the payment every card is made
with, through the same doors.  Neither was driven so before: the partial's
PATCH in ``tests/test_routes/test_transfers/test_a_worked_out_payment_at_each_door.py``
posts the controls of the transfers page's quick edit, which no page draws now
(``transfers/_transfer_cell.html``, task plan#3810), and the extra in
``tests/test_routes/test_card_row_with_no_date.py`` is dated after a true-up
that holds it, never subtracted from the row that counts it.

**The calendar** is ``seed_periods``': paychecks from Fri Jan 2 2026, so Jan
16's covers Jan 16-29 and Jan 30's Jan 30-Feb 12.  **The card** is
:mod:`tests.test_routes.test_the_card_payment_door`'s: it closes on the 5th and
falls due on the 27th, its $0.00 owed observed Jan 2, so its books open Jan 1.
**$800 goes on it Jan 3**, on the statement closing Jan 5, valued at the end of
Jan 4 and due Jan 27.  **Today is held at Jan 8**, inside the Jan 2 paycheck.
Switched, the payment has a row on each due date, Jan 27, Feb 27, Mar 27 and
Apr 27, each paying the statement that closed before it, less every other
payment into the card held after the end of the day that statement is valued
at and dated on or before the row's day (R-CC22).  A payoff row pays what the
card owed at the end of the day before its payday, less every other payment
dated in its own paycheck.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from werkzeug.datastructures import MultiDict

from app.enums import CardPaymentModeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer import Transfer
from tests._test_helpers import freeze_today
from tests.test_routes._statement_forms import form_fields
from tests.test_routes.test_a_terms_change_moves_the_payments import (
    _live_dates,
    _row_on,
)
from tests.test_routes.test_card_payment_doors import _flashes
from tests.test_routes.test_the_card_payment_door import (
    _PAYDAYS,
    _card,
    _payment_of,
    _price_on,
    _switch,
    _with_its_payoff,
)
from tests.test_services.test_card_payment_price import _spend
from tests.test_services.test_due_date_repeat_read_pass import _DUE_ON_27

_TODAY = date(2026, 1, 8)
_JAN_16 = date(2026, 1, 16)
_JAN_27 = date(2026, 1, 27)
_JAN_28 = date(2026, 1, 28)
_JAN_30 = date(2026, 1, 30)
_FEB_13 = date(2026, 2, 13)
_FEB_27 = date(2026, 2, 27)
_MAR_27 = date(2026, 3, 27)
_ZERO = Decimal("0.00")

#: The Recurring page's monthly cell for a payment into a card the card works
#: out: no figure, as none of it repeats, and the note that it is not in the
#: totals (ruling plan#3527, task plan#3700).
_NO_MONTHLY = "-- Not in the totals: it pays the card's bills, which the totals count"

#: The Recurring page's words for a repeat on each statement's due date.
_ON_DUE_DATES = "On each statement's due date"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Hold every clock the doors and their passes read at Jan 8."""
    freeze_today(monkeypatch, _TODAY)


def _paid_off(seed_user, seed_periods):
    """The card with $800 on it and its payoff rows each payday.

    Built as the account door builds a card's payment (:func:`_with_its_payoff`).

    Returns:
        ``(card, the payment's definition)``.
    """
    card = _card(seed_user)
    template = _with_its_payoff(seed_user, seed_periods, card)
    assert _live_dates(template) == _PAYDAYS
    return card, template


def _scheduled(auth_client, seed_user, seed_periods):
    """:func:`_paid_off`, its payment switched to the statement balance on the card's page.

    Returns:
        ``(card, the payment's definition)``.
    """
    card, template = _paid_off(seed_user, seed_periods)
    assert _switch(auth_client, card, "statement").status_code == 302
    assert _flashes(auth_client) == ["Visa's payment is saved."]
    assert _payment_of(template) == (
        CardPaymentModeEnum.STATEMENT_BALANCE,
        RecurrenceUnitEnum.DUE_DATE,
        date(2026, 1, 2),
    )
    assert _live_dates(template) == _DUE_ON_27
    return card, template


def _save_in_popover(auth_client, seed_user, row, **typed):
    """Open *row*'s full-edit popover from its cell in checking's grid, type, and Save.

    A click on the cell opens the popover with the leg's account
    (``grid_edit.js``), and Save posts every control it renders at the value it
    renders, *typed* replacing the controls of those names.

    Returns:
        What the popover rendered, ``{name: value}``.
    """
    checking = seed_user["account"].id
    page = auth_client.get(f"/transfers/{row.id}/full-edit?leg_account_id={checking}")
    assert page.status_code == 200
    door = f"/transfers/instance/{row.id}"
    rendered = form_fields(page.get_data(as_text=True), door, attribute="hx-patch")
    payload = [pair for pair in rendered if pair[0] not in typed]
    payload.extend(typed.items())
    response = auth_client.patch(door, data=MultiDict(payload))
    assert response.status_code == 200, response.data[:400]
    return dict(rendered)


def _type_over(auth_client, seed_user, template, day, figure):
    """Type *figure* over the payment on *day* in its popover; what its box offered."""
    rendered = _save_in_popover(
        auth_client, seed_user, _row_on(template, day), amount=figure,
    )
    return rendered["amount"]


def _press_paid(auth_client, seed_user, row):
    """Press the one-click Paid on *row*'s cell in checking's grid."""
    response = auth_client.post(
        f"/transfers/instance/{row.id}/mark-done",
        data={"leg_account_id": str(seed_user["account"].id)},
    )
    assert response.status_code == 200, response.data[:400]


def _send_extra(auth_client, seed_user, card, period, amount):
    """Send *amount* from checking to *card* once, in *period*, by the transfer form.

    The form's default selection, "Does not repeat", places one transfer.

    Returns:
        The transfer it placed.
    """
    response = auth_client.post("/transfers", data={
        "name": "Extra to the Visa",
        "amount": amount,
        "from_account_id": str(seed_user["account"].id),
        "to_account_id": str(card.id),
        "recurrence_unit": "",
        "start_period_id": str(period.id),
        "category_id": str(seed_user["categories"]["Rent"].id),
    })
    assert response.status_code == 302, response.data[:400]
    db.session.expire_all()
    return db.session.query(Transfer).filter_by(name="Extra to the Visa").one()


class _RecurringRows(HTMLParser):
    """The Recurring page's table rows: each row's cells' words, by its first cell."""

    def __init__(self):
        """Start with no row read."""
        super().__init__()
        self.rows = {}
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        """Open a row or a cell."""
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        """Close a cell into its row, or a row into the table by its name."""
        if tag == "td" and self._cell is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row:
            self.rows[self._row[0]] = self._row
            self._row = None

    def handle_data(self, data):
        """Gather a cell's words."""
        if self._cell is not None:
            self._cell.append(data)


def _recurring_row(auth_client, name="Pay the Visa"):
    """The Recurring page's row for *name*: ``(Amount, Recurrence, Monthly, Next)``."""
    response = auth_client.get("/templates")
    assert response.status_code == 200
    rows = _RecurringRows()
    rows.feed(response.get_data(as_text=True))
    _name, _accounts, amount, recurrence, monthly, next_date, _actions = rows.rows[name]
    return amount, recurrence, monthly, next_date


class TestThePartialPayment:
    """A due date's row typed over: the next statement pays what is left."""

    def test_the_next_statement_pays_what_is_left(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$300 typed over Jan 27's $800: Feb 27 pays the $500 left, Mar 27 nothing.

        The popover offers the worked-out $800.00, the Jan 5 statement.  Typed
        $300.00, the row owns that figure.  The Feb 5 statement, valued at the
        end of Feb 4, holds the $800 and the $300 sent Jan 27: 800 - 300 =
        $500.00 owed, and nothing else is sent on or before Feb 27, so Feb 27
        pays $500.00.  The Mar 5 statement holds 800 - 300 - 500 = $0.00.  A
        row left at $800.00 would leave Feb 27 at $0.00, its failing control.
        """
        with app.app_context():
            _, template = _scheduled(auth_client, seed_user, seed_periods)

            offered = _type_over(auth_client, seed_user, template, _JAN_27, "300.00")

            assert offered == "800.00"
            assert _price_on(template, _JAN_27, seed_user) == Decimal("300.00")
            assert _price_on(template, _FEB_27, seed_user) == Decimal("500.00")
            assert _price_on(template, _MAR_27, seed_user) == _ZERO
            assert _live_dates(template) == _DUE_ON_27

    def test_a_new_charge_is_added_to_what_is_left(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$300 typed over Jan 27 and $150 charged Jan 20: Feb 27 pays $650.

        The $150 bought Jan 20 is after the Jan 5 close and before the Feb 5
        one, so Jan 27 still offers the $800.00 statement (everything on the
        card would offer $950.00) and the Feb 5 statement holds it: 800 + 150 -
        300 = $650.00.
        """
        with app.app_context():
            card, template = _scheduled(auth_client, seed_user, seed_periods)
            _spend(seed_user, card, seed_periods[1], "150.00", date(2026, 1, 20))

            offered = _type_over(auth_client, seed_user, template, _JAN_27, "300.00")

            assert offered == "800.00"
            assert _price_on(template, _FEB_27, seed_user) == Decimal("650.00")
            assert _price_on(template, _MAR_27, seed_user) == _ZERO

    def test_the_recurring_page_names_the_typed_row_then_the_rest(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Next Jan 27 at the typed $300.00; marked Paid, Next Feb 27 at the $500.00 left.

        Marked Paid, the Jan 27 payment is money moved rather than a planned
        row, so it is not next.  The Feb 5 statement holds its $300 whichever
        day by Feb 4 it was paid: 800 - 300 = $500.00.  The scheduled payment
        has no monthly figure, as no one figure of it repeats.
        """
        with app.app_context():
            _, template = _scheduled(auth_client, seed_user, seed_periods)
            _type_over(auth_client, seed_user, template, _JAN_27, "300.00")

            assert _recurring_row(auth_client) == (
                "$300.00", _ON_DUE_DATES, _NO_MONTHLY, "Jan 27, 2026",
            )

            _press_paid(auth_client, seed_user, _row_on(template, _JAN_27))

            assert _recurring_row(auth_client) == (
                "$500.00", _ON_DUE_DATES, _NO_MONTHLY, "Feb 27, 2026",
            )


class TestTheExtraPayment:
    """A one-time transfer into the card: the due date it is dated by pays less."""

    def test_one_before_the_due_date_comes_off_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$200 sent in the Jan 16 paycheck is dated Jan 16: Jan 27 pays $600.

        The Jan 16 paycheck has not started on Jan 8, so the transfer form
        dates the extra its first day (ruling plan#2965).  Jan 16 is after the
        Jan 5 close and on or before Jan 27, so it is not inside the $800.00
        statement and is subtracted: 800 - 200 = $600.00.  The Feb 5 statement
        holds all three: 800 - 200 - 600 = $0.00.  The Recurring page names
        Jan 27 at $600.00.
        """
        with app.app_context():
            card, template = _scheduled(auth_client, seed_user, seed_periods)

            extra = _send_extra(auth_client, seed_user, card, seed_periods[1], "200.00")

            assert extra.occurs_on == _JAN_16
            assert _price_on(template, _JAN_27, seed_user) == Decimal("600.00")
            assert _price_on(template, _FEB_27, seed_user) == _ZERO
            assert _recurring_row(auth_client) == (
                "$600.00", _ON_DUE_DATES, _NO_MONTHLY, "Jan 27, 2026",
            )

    def test_one_after_the_due_date_comes_off_the_next(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$200 dated Jan 28, in Jan 27's own paycheck: Jan 27 pays $800, Feb 27 $300.

        The extra is sent in the Jan 16 paycheck and its date typed Jan 28 in
        its popover, so it is in the same paycheck as the Jan 27 payment, a
        day after that due date: the due date, not the paycheck, decides, and
        Jan 27 pays the whole $800.00 statement.  $500 charged Jan 20 is on the
        Feb 5 statement, which holds the $800, the $500, the $800 paid Jan 27
        and the $200: 800 + 500 - 800 - 200 = $300.00, so Feb 27 pays $300.00.
        Counted by its paycheck, Jan 27 would read $600.00; with the extra left
        out, Feb 27 would read $500.00.
        """
        with app.app_context():
            card, template = _scheduled(auth_client, seed_user, seed_periods)
            _spend(seed_user, card, seed_periods[1], "500.00", date(2026, 1, 20))
            extra = _send_extra(auth_client, seed_user, card, seed_periods[1], "200.00")

            _save_in_popover(auth_client, seed_user, extra, due_date=_JAN_28.isoformat())

            db.session.expire_all()
            extra = db.session.get(Transfer, extra.id)
            assert (extra.occurs_on, extra.pay_period_id) == (_JAN_28, seed_periods[1].id)
            assert _row_on(template, _JAN_27).pay_period_id == seed_periods[1].id
            assert _price_on(template, _JAN_27, seed_user) == Decimal("800.00")
            assert _price_on(template, _FEB_27, seed_user) == Decimal("300.00")


class TestPayoffRows:
    """The same two on the payment every card is made with: everything on it, each payday."""

    def test_typed_over_the_next_payday_pays_what_is_left(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$300 typed over Jan 16's $800: Jan 30 pays the $500 left, Feb 13 nothing.

        Jan 16 offers what the card owed at the end of Jan 15, the $800 bought
        Jan 3.  Typed $300.00, Jan 30 pays what it owed at the end of Jan 29:
        800 - 300 = $500.00, and Feb 13 800 - 300 - 500 = $0.00.
        """
        with app.app_context():
            _, template = _paid_off(seed_user, seed_periods)

            offered = _type_over(auth_client, seed_user, template, _JAN_16, "300.00")

            assert offered == "800.00"
            assert _price_on(template, _JAN_16, seed_user) == Decimal("300.00")
            assert _price_on(template, _JAN_30, seed_user) == Decimal("500.00")
            assert _price_on(template, _FEB_13, seed_user) == _ZERO

    def test_an_extra_in_the_paycheck_comes_off_its_row(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """$200 sent in the Jan 16 paycheck: Jan 16 pays $600, Jan 30 nothing.

        Dated Jan 16 (ruling plan#2965), the extra is in the Jan 16 row's own
        paycheck, which that row counts: 800 - 200 = $600.00.  Jan 30 pays what
        the card owed at the end of Jan 29: 800 - 200 - 600 = $0.00.
        """
        with app.app_context():
            card, template = _paid_off(seed_user, seed_periods)

            extra = _send_extra(auth_client, seed_user, card, seed_periods[1], "200.00")

            assert extra.occurs_on == _JAN_16
            assert _price_on(template, _JAN_16, seed_user) == Decimal("600.00")
            assert _price_on(template, _JAN_30, seed_user) == _ZERO


class TestTheRecurringPage:
    """The scheduled payment's Next date and Amount are its next row's."""

    def test_it_names_the_first_due_date_and_its_statement(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Switched Jan 8: Next Jan 27, $800.00, its repeat in words, no monthly figure."""
        with app.app_context():
            _scheduled(auth_client, seed_user, seed_periods)

            assert _recurring_row(auth_client) == (
                "$800.00", _ON_DUE_DATES, _NO_MONTHLY, "Jan 27, 2026",
            )
