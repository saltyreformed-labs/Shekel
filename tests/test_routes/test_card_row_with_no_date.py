"""
Shekel Budget App -- a row on the card with no date typed takes today's date

Plan step **credit_card:CC-6**, its task plan#2967 (ruling **plan#2965**, option A,
amending R-BAL22 for a card; an extra payment to the card the same; checking
unchanged): today's date once the row's paycheck has started, otherwise the
paycheck's first day.  Every row here is made by the door a browser posts to, so no
helper supplies its date, and each figure is computed by hand in the test that
asserts it.

**The money unless a case says otherwise.**  The card closes on the 5th and falls
due on the 27th.  Paydays are Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27, and the
payoff row repeats on each.  Checking opens at $1,000.00.  **A case's clock is its
fixture's**, asked for ahead of ``auth_client``, because a login followed by a
travel back answers 302.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import time_machine

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import create_account_of_type
from tests.test_services.test_statement_match._builders import a_bank_line, an_import
from tests.test_services.test_statement_match.test_income._helpers import _record
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _balances,
    _payoff,
    _true_up,
)
from tests.test_services.test_card_payment_price import _card, _price

_ZERO = Decimal("0.00")
_JAN_10 = date(2026, 1, 10)
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_20 = date(2026, 1, 20)
_JAN_22 = date(2026, 1, 22)
_JAN_23 = date(2026, 1, 23)
_JAN_25 = date(2026, 1, 25)
_JAN_29 = date(2026, 1, 29)
_JAN_30 = date(2026, 1, 30)
_FEB_9 = date(2026, 2, 9)
_FEB_10 = date(2026, 2, 10)
_FEB_13 = date(2026, 2, 13)
_THE_NIGHT_OF_JAN_21 = datetime(2026, 1, 22, 4, 30, tzinfo=timezone.utc)

_THE_CELL = "/transactions/inline"
_THE_ADD_FORM = "/transactions"


def _held_at(day):
    """Hold both clocks at noon Eastern on *day*."""
    return time_machine.travel(
        datetime(day.year, day.month, day.day, 17, tzinfo=timezone.utc), tick=False,
    )


@pytest.fixture(name="on_jan_10")
def _on_jan_10():
    """Today is Jan 10, ahead of Jan 16's paycheck."""
    with _held_at(_JAN_10):
        yield


@pytest.fixture(name="on_jan_22")
def _on_jan_22():
    """Today is Jan 22, inside Jan 16's paycheck and after a Jan 20 true-up."""
    with _held_at(_JAN_22):
        yield


@pytest.fixture(name="on_jan_25")
def _on_jan_25():
    """Today is Jan 25, after a bank line posted Jan 22."""
    with _held_at(_JAN_25):
        yield


@pytest.fixture(name="on_the_night_of_jan_21")
def _on_the_night_of_jan_21():
    """It is 11:30 pm on Jan 21 in the owner's zone, and already Jan 22 in UTC."""
    with time_machine.travel(_THE_NIGHT_OF_JAN_21, tick=False):
        yield


@pytest.fixture(name="on_feb_10")
def _on_feb_10():
    """Today is Feb 10, after Jan 16's paycheck has ended."""
    with _held_at(_FEB_10):
        yield


@pytest.fixture(name="on_feb_13")
def _on_feb_13():
    """Today is Feb 13, a payday."""
    with _held_at(_FEB_13):
        yield


def _add_bill(
    client, seed_user, account, period, *, door=_THE_CELL, typed=None, envelope=False,
):
    """Add a $100.00 bill to *period* on *account* through *door*, and return its row."""
    form = {
        "name": "Groceries",
        "estimated_amount": "100.00",
        "account_id": account.id,
        "category_id": seed_user["categories"]["Groceries"].id,
        "pay_period_id": period.id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "scenario_id": seed_user["scenario"].id,
    }
    if typed is not None:
        form["due_date"] = typed.isoformat()
    if envelope:
        form["is_envelope"] = "on"
    response = client.post(door, data=form)
    assert response.status_code == 201, response.data[:400]
    return db.session.query(Transaction).filter_by(name="Groceries").one()


def _add_extra_payment(client, seed_user, account, period):
    """Send $200.00 from checking to *account* once, in *period*, by the transfer form."""
    response = client.post("/transfers", data={
        "name": "Extra payment",
        "amount": "200.00",
        "from_account_id": seed_user["account"].id,
        "to_account_id": account.id,
        "category_id": seed_user["categories"]["Rent"].id,
        "start_period_id": period.id,
    })
    assert response.status_code == 302, response.data[:400]
    return db.session.query(Transfer).filter_by(name="Extra payment").one()


class TestABillOnTheCard:
    """Both doors a bill is added by: the grid's cell and the Add Transaction form."""

    @pytest.mark.usefixtures("on_jan_22")
    @pytest.mark.parametrize("door", [_THE_CELL, _THE_ADD_FORM])
    def test_added_after_a_true_up_the_card_owes_it(
        self, auth_client, seed_user, seed_periods, door,
    ):
        """Ruling plan#2965's example: the row is dated Jan 22, not Jan 16.

        Trued up to $0 on Jan 20, $100 added on Jan 22 to Jan 16's paycheck.  The card
        owes $100.00, Jan 30 pays $100.00 and checking ends 1,000 - 100 = $900.00.
        Dated Jan 16 the true-up holds it: the card $0.00, Jan 30 $0.00, checking
        $1,000.00, which is this test's failing control.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "0.00", _JAN_20)

        row = _add_bill(auth_client, seed_user, card, seed_periods[1], door=door)

        assert row.due_date == _JAN_22
        assert row.occurs_on == _JAN_22
        assert _price(template, _JAN_30, _JAN_22) == Decimal("100.00")
        assert _balances(seed_user, card, _JAN_22, _JAN_29) == (
            Decimal("1000.00"), Decimal("-100.00"),
        )
        assert _balances(seed_user, card, _JAN_22, _JAN_30) == (
            Decimal("900.00"), _ZERO,
        )

    @pytest.mark.usefixtures("on_jan_10")
    @pytest.mark.parametrize("door", [_THE_CELL, _THE_ADD_FORM])
    def test_added_before_its_paycheck_starts_it_takes_the_first_day(
        self, auth_client, seed_user, seed_periods, door,
    ):
        """Added Jan 10 to Jan 16's paycheck: dated Jan 16, Jan 16 $0.00, Jan 30 $100.00.

        Jan 16's row pays what the card owed at the end of Jan 15, which is nothing
        yet.  Dated today instead, Jan 10, Jan 16's row would pay the $100.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)

        row = _add_bill(auth_client, seed_user, card, seed_periods[1], door=door)

        assert row.due_date == _JAN_16
        assert row.occurs_on == _JAN_16
        assert _price(template, _JAN_16, _JAN_10) == _ZERO
        assert _price(template, _JAN_30, _JAN_10) == Decimal("100.00")

    @pytest.mark.usefixtures("on_feb_10")
    @pytest.mark.parametrize("door", [_THE_CELL, _THE_ADD_FORM])
    def test_added_after_its_paycheck_ended_it_still_takes_today(
        self, auth_client, seed_user, seed_periods, door,
    ):
        """Added Feb 10 to Jan 16's paycheck, which ended Jan 29: dated Feb 10.

        The ruling's words are "once its paycheck has started".  The card owes $0.00
        through Feb 9 and $100.00 from Feb 10, so Feb 13 pays $100.00 and checking
        ends at $900.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)

        row = _add_bill(auth_client, seed_user, card, seed_periods[1], door=door)

        assert row.due_date == _FEB_10
        assert _balances(seed_user, card, _FEB_10, _FEB_9)[1] == _ZERO
        assert _balances(seed_user, card, _FEB_10, _FEB_10)[1] == Decimal("-100.00")
        assert _price(template, _FEB_13, _FEB_10) == Decimal("100.00")
        assert _balances(seed_user, card, _FEB_10, _FEB_13) == (
            Decimal("900.00"), _ZERO,
        )

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_typed_date_stands(
        self, auth_client, seed_user, seed_periods,
    ):
        """Jan 18 sent, trued up to $0 on Jan 20: the true-up holds it (ruling plan#2926).

        The card $0.00, Jan 30 $0.00, checking $1,000.00.  With the date lost to
        today's, the row would be dated Jan 22 and Jan 30 would pay $100.00.  The schema
        takes a date its form does not render; the next test is how a browser types one.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "0.00", _JAN_20)

        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_18,
        )

        assert row.due_date == _JAN_18
        assert _price(template, _JAN_30, _JAN_22) == _ZERO
        assert _balances(seed_user, card, _JAN_22, _JAN_30) == (
            Decimal("1000.00"), _ZERO,
        )


    @pytest.mark.usefixtures("on_jan_22")
    def test_a_date_typed_on_the_row_afterwards_stands(
        self, auth_client, seed_user, seed_periods,
    ):
        """Added Jan 22, then Jan 18 typed in its popover: $0.00 where it read $100.00.

        Trued up to $0 on Jan 20, so the row dated Jan 22 makes Jan 30 pay $100.00,
        and dated Jan 18 the true-up holds it and Jan 30 pays $0.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "0.00", _JAN_20)
        row = _add_bill(auth_client, seed_user, card, seed_periods[1])
        assert _price(template, _JAN_30, _JAN_22) == Decimal("100.00")

        response = auth_client.patch(f"/transactions/{row.id}", data={
            "due_date": _JAN_18.isoformat(),
            "version_id": row.version_id,
        })

        assert response.status_code == 200, response.data[:400]
        db.session.refresh(row)
        assert row.due_date == _JAN_18
        assert row.occurs_on == _JAN_18
        assert _price(template, _JAN_30, _JAN_22) == _ZERO

    @pytest.mark.usefixtures("on_jan_22")
    def test_added_the_day_of_a_true_up_it_is_inside_it(
        self, auth_client, seed_user, seed_periods,
    ):
        """Trued up to $0 on Jan 22 and added Jan 22: the card $0.00, Jan 30 $0.00.

        A true-up closes its own day (the ruling's "on or before it"), so checking
        stays $1,000.00.  This is the rule's stated cost, in ``due_date_for``.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "0.00", _JAN_22)

        row = _add_bill(auth_client, seed_user, card, seed_periods[1])

        assert row.due_date == _JAN_22
        assert _price(template, _JAN_30, _JAN_22) == _ZERO
        assert _balances(seed_user, card, _JAN_22, _JAN_30) == (
            Decimal("1000.00"), _ZERO,
        )

    @pytest.mark.usefixtures("on_the_night_of_jan_21")
    def test_today_is_the_owners_day_not_the_servers(
        self, auth_client, seed_user, seed_periods,
    ):
        """At 11:30 pm Eastern on Jan 21, already Jan 22 in UTC: dated Jan 21."""
        card = _card(seed_user)

        row = _add_bill(auth_client, seed_user, card, seed_periods[1])

        assert row.due_date == date(2026, 1, 21)

    @pytest.mark.usefixtures("on_feb_13")
    def test_an_envelope_keeps_the_first_day_so_it_can_carry_forward(
        self, auth_client, seed_user, seed_periods,
    ):
        """An envelope added Feb 13 to Jan 16's paycheck: dated Jan 16, and it carries.

        No card counts from an envelope's date.  Dated today, Feb 13, it would answer
        the day its leftover is placed on in Feb 13's paycheck, and the carry-forward
        would be refused with a 400 where it answers 200.
        """
        card = _card(seed_user)

        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, envelope=True,
        )
        response = auth_client.post(
            f"/pay-periods/{seed_periods[1].id}/carry-forward", data={},
        )

        assert row.due_date == _JAN_16
        assert response.status_code == 200, response.data[:400]


class TestABankLinesRow:
    """A row a bank line makes on a card is dated the day the line posted."""

    @pytest.mark.usefixtures("on_jan_25")
    def test_undone_it_stays_on_that_day(self, auth_client, seed_user, seed_periods):
        """A $100.00 refund posted Jan 22, the card stated at $500.00 on Jan 20.

        Recorded, the card owes 500 - 100 = $400.00 and Jan 30 pays $400.00.  Undone to
        Projected the row still counts from Jan 22 (ruling plan#2926), so nothing moves
        and checking ends 1,000 - 400 = $600.00.  Dated its paycheck's first day, Jan 16,
        the true-up would hold the undone row: Jan 30 $500.00, checking $500.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "500.00", _JAN_20)
        line = a_bank_line(
            seed_user, an_import(seed_user, card), amount="100.00", posted_on=_JAN_22,
            description="MERCHANT REFUND", merchant="Merchant",
        )
        recorded = _record(seed_user, line, account=card)
        row = db.session.get(Transaction, recorded.transaction_id)
        db.session.commit()
        assert row.pay_period_id == seed_periods[1].id
        assert row.due_date == _JAN_22
        assert row.occurs_on == _JAN_22
        assert _price(template, _JAN_30, _JAN_25) == Decimal("400.00")

        response = auth_client.patch(f"/transactions/{row.id}", data={
            "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
            "version_id": row.version_id,
        })

        assert response.status_code == 200, response.data[:400]
        db.session.refresh(row)
        assert row.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
        assert row.due_date == _JAN_22
        assert _price(template, _JAN_30, _JAN_25) == Decimal("400.00")
        assert _balances(seed_user, card, _JAN_25, _JAN_30) == (
            Decimal("600.00"), _ZERO,
        )


class TestABillOnChecking:
    """Ruling plan#2965: rows on checking don't change."""

    @pytest.mark.usefixtures("on_jan_22")
    @pytest.mark.parametrize("door", [_THE_CELL, _THE_ADD_FORM])
    def test_it_keeps_its_paychecks_first_day(
        self, auth_client, seed_user, seed_periods, door,
    ):
        """Added Jan 22 to Jan 16's paycheck on checking: dated Jan 16, as before.

        Overdue, it lands tomorrow (ruling R-G): $1,000.00 on Jan 22, $900.00 on Jan 23.
        """
        card = _card(seed_user)

        row = _add_bill(
            auth_client, seed_user, seed_user["account"], seed_periods[1], door=door,
        )

        assert row.due_date == _JAN_16
        assert row.occurs_on == _JAN_16
        assert _balances(seed_user, card, _JAN_22, _JAN_22)[0] == Decimal("1000.00")
        assert _balances(seed_user, card, _JAN_22, _JAN_23)[0] == Decimal("900.00")


class TestAnExtraPayment:
    """The transfer form's one-time transfer: the ruling's "an extra payment the same"."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_added_after_a_true_up_it_comes_off_what_is_owed(
        self, auth_client, seed_user, seed_periods,
    ):
        """$800 stated Jan 20, $200 added Jan 22 to Jan 16's paycheck: dated Jan 22.

        Both its legs land tomorrow, Jan 23, so at the end of Jan 29 checking holds
        1,000 - 200 = $800.00 and the card owes 800 - 200 = $600.00.  Jan 30 pays the
        $600.00, leaving checking 800 - 600 = $200.00 and the card $0.00.  Dated Jan 16
        the true-up holds it: Jan 30 pays $800.00 and checking ends $0.00, $200.00 too
        low, which is this test's failing control.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "800.00", _JAN_20)

        row = _add_extra_payment(auth_client, seed_user, card, seed_periods[1])

        assert row.due_date == _JAN_22
        assert row.occurs_on == _JAN_22
        assert _price(template, _JAN_30, _JAN_22) == Decimal("600.00")
        assert _balances(seed_user, card, _JAN_22, _JAN_29) == (
            Decimal("800.00"), Decimal("-600.00"),
        )
        assert _balances(seed_user, card, _JAN_22, _JAN_30) == (
            Decimal("200.00"), _ZERO,
        )

    @pytest.mark.usefixtures("on_jan_10")
    def test_added_before_its_paycheck_starts_it_takes_the_first_day(
        self, auth_client, seed_user, seed_periods,
    ):
        """Added Jan 10 to Jan 16's paycheck: dated Jan 16, not today."""
        card = _card(seed_user)

        row = _add_extra_payment(auth_client, seed_user, card, seed_periods[1])

        assert row.due_date == _JAN_16
        assert row.occurs_on == _JAN_16

    @pytest.mark.usefixtures("on_jan_22")
    def test_to_an_account_that_is_no_card_it_keeps_the_first_day(
        self, auth_client, seed_user, seed_periods,
    ):
        """Checking to savings, added Jan 22 to Jan 16's paycheck: dated Jan 16, as before."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Rainy Day",
            anchor_balance=_ZERO, observed_on=date(2026, 1, 2),
        )
        db.session.commit()

        row = _add_extra_payment(auth_client, seed_user, savings, seed_periods[1])

        assert row.due_date == _JAN_16
        assert row.occurs_on == _JAN_16


class TestARowMovedBetweenPaychecks:
    """A date not on its paycheck's first day stays once it moves (ruling R-BAL33).

    What a move should do to a row on a card is finding plan#2969's, so this pins only
    the read a bill and that finding's recommendation agree on.
    """

    @pytest.mark.usefixtures("on_jan_22")
    def test_todays_date_stays_with_the_row(
        self, auth_client, seed_user, seed_periods,
    ):
        """Dated Jan 22 in Jan 16's paycheck, moved to Jan 30's: still Jan 22.

        ``due_date_after_move`` reads a date that is not its paycheck's first day as
        one the owner stated.
        """
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[1])

        response = auth_client.patch(f"/transactions/{row.id}", data={
            "pay_period_id": str(seed_periods[2].id),
            "version_id": row.version_id,
        })

        assert response.status_code == 200, response.data[:400]
        db.session.refresh(row)
        assert row.pay_period_id == seed_periods[2].id
        assert row.due_date == _JAN_22
