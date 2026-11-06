"""
Shekel Budget App -- a row moved on the card keeps or takes its date

Plan step **credit_card:CC-6**, its task plan#2977 (ruling **plan#2976**, option A,
amending R-BAL33 and R-BAL93 for a card; finding plan#2969).  A bill whose date has
passed keeps it.  A bill dated ahead follows its paycheck: the new paycheck's first day,
or today if that paycheck has started.  An extra payment always follows that way.  A date
typed in the same save stands.  Off a card R-BAL33 and R-BAL93 stand.  What refuses a day
another row answers is ``test_card_row_moved_refusals.py``.

Every move here is posted as a browser posts it, every control the popover renders at the
value it renders, so a move's payload carries the old date.  Each figure is computed by
hand in the test that asserts it.  Where this task changed an answer the docstring names
the old one, which is that test's failing control; the rest guard an answer it kept.

**The money unless a case says otherwise.**  The card closes on the 5th and falls due on
the 27th.  Paydays are Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27, and the payoff row repeats
on each.  Checking opens at $1,000.00.  **A case's clock is its fixture's**, asked for
ahead of ``auth_client``, because a login followed by a travel back answers 302.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import time_machine
from werkzeug.datastructures import MultiDict

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import transfer_service
from app.services.one_off import place_row_of
from app.services.pay_calendar import calendar_for
from tests._test_helpers import create_account_of_type, generate_transfer_of, make_transfer_template
from tests.test_routes._statement_forms import form_fields
from tests.test_routes.test_card_row_with_no_date import (
    _add_bill,
    _add_extra_payment,
    _held_at,
    _THE_ADD_FORM,
)
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _balances,
    _payoff,
    _true_up,
)
from tests.test_services.test_card_payment_price import _card, _price

_ZERO = Decimal("0.00")
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_20 = date(2026, 1, 20)
_JAN_22 = date(2026, 1, 22)
_JAN_25 = date(2026, 1, 25)
_JAN_28 = date(2026, 1, 28)
_JAN_30 = date(2026, 1, 30)
_JAN_31 = date(2026, 1, 31)
_FEB_1 = date(2026, 2, 1)
_FEB_5 = date(2026, 2, 5)
_FEB_12 = date(2026, 2, 12)
_FEB_13 = date(2026, 2, 13)
_FEB_27 = date(2026, 2, 27)
_THE_NIGHT_OF_JAN_21 = datetime(2026, 1, 22, 4, 30, tzinfo=timezone.utc)


@pytest.fixture(name="on_jan_22")
def _on_jan_22():
    """Today is Jan 22, inside Jan 16's paycheck."""
    with _held_at(_JAN_22):
        yield


@pytest.fixture(name="on_jan_30")
def _on_jan_30():
    """Today is payday Jan 30."""
    with _held_at(_JAN_30):
        yield


@pytest.fixture(name="on_jan_31")
def _on_jan_31():
    """Today is Jan 31, the day after payday."""
    with _held_at(_JAN_31):
        yield


@pytest.fixture(name="on_feb_1")
def _on_feb_1():
    """Today is Feb 1, inside Jan 30's paycheck, which is what Carry Forward carries to."""
    with _held_at(_FEB_1):
        yield


@pytest.fixture(name="on_the_night_of_jan_21")
def _on_the_night_of_jan_21():
    """It is 11:30 pm on Jan 21 in the owner's zone, and already Jan 22 in UTC."""
    with time_machine.travel(_THE_NIGHT_OF_JAN_21, tick=False):
        yield


def _save_row(client, row, **typed):
    """PATCH *row* with what its popover renders, *typed* over it, as a browser does."""
    page = client.get(f"/transactions/{row.id}/full-edit")
    assert page.status_code == 200
    rendered = form_fields(
        page.data.decode(), f"/transactions/{row.id}", attribute="hx-patch",
    )
    assert ("due_date", row.due_date.isoformat()) in rendered
    payload = [pair for pair in rendered if pair[0] not in typed]
    payload.extend(typed.items())
    return client.patch(f"/transactions/{row.id}", data=MultiDict(payload))


def _save_transfer(client, xfer, **typed):
    """PATCH *xfer* with what its popover renders, *typed* over it, as a browser does."""
    page = client.get(f"/transfers/{xfer.id}/full-edit")
    assert page.status_code == 200
    rendered = form_fields(
        page.data.decode(), f"/transfers/instance/{xfer.id}", attribute="hx-patch",
    )
    assert ("due_date", xfer.due_date.isoformat()) in rendered
    payload = [pair for pair in rendered if pair[0] not in typed]
    payload.extend(typed.items())
    return client.patch(f"/transfers/instance/{xfer.id}", data=MultiDict(payload))


def _move(client, row, period, **typed):
    """Move *row* to *period* by its popover, and read it back."""
    saving = _save_transfer if hasattr(row, "from_account_id") else _save_row
    response = saving(client, row, pay_period_id=str(period.id), **typed)
    assert response.status_code == 200, response.data[:400]
    db.session.refresh(row)
    assert row.pay_period_id == period.id
    return row


def _carry(client, period):
    """Press Carry Forward on *period*, and answer what the route answered."""
    return client.post(f"/pay-periods/{period.id}/carry-forward", data={})


def _modal(client, period):
    """The Carry Forward modal for *period*, with its whitespace collapsed."""
    response = client.get(f"/pay-periods/{period.id}/carry-forward-preview")
    assert response.status_code == 200, response.data[:400]
    return " ".join(response.data.decode().split())


def _dates(row):
    """The row's due date and the occurrence it answers, read back."""
    db.session.refresh(row)
    return row.due_date, row.occurs_on


def _a_second_row_of(row, period, day, seed_user):
    """Place another row of *row*'s definition in *period*, dated *day*, committed."""
    sibling = place_row_of(
        row.template, calendar_for(seed_user["user"].id).period_by_id(period.id),
        scenario_id=seed_user["scenario"].id, due_date=day,
    )
    db.session.commit()
    return sibling


class TestABillAlreadyBought:
    """A bill whose date has passed keeps it, because it was bought."""

    @pytest.mark.usefixtures("on_feb_1")
    def test_carried_forward_it_keeps_its_date(
        self, auth_client, seed_user, seed_periods,
    ):
        """The ruling's first example: $100 dated Jan 16, $100 trued up Jan 20.

        Carried on Feb 1 it stays Jan 16, so the true-up holds it and it counts once.
        Jan 30 pays the $100.00 owed, the card owes $0.00 at the end of Feb 12, and on Feb
        13 checking holds 1,000 - 100 = $900.00.  Re-dated Jan 30, as this door did, it
        came on top of the true-up: the card $100.00, Feb 13 $100.00, checking $800.00.
        """
        card = _card(seed_user)
        _payoff(seed_user, card)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_16,
        )
        _true_up(card, "100.00", _JAN_20)

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        assert _dates(row) == (_JAN_16, _JAN_16)
        assert row.pay_period_id == seed_periods[2].id
        assert _balances(seed_user, card, _FEB_1, _FEB_12)[1] == _ZERO
        assert _balances(seed_user, card, _FEB_1, _FEB_13) == (Decimal("900.00"), _ZERO)

    @pytest.mark.usefixtures("on_feb_1")
    def test_moved_by_its_popover_it_keeps_its_date(
        self, auth_client, seed_user, seed_periods,
    ):
        """The same act by the other door reads the same: Jan 16, checking $900.00."""
        card = _card(seed_user)
        _payoff(seed_user, card)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_16,
        )
        _true_up(card, "100.00", _JAN_20)

        _move(auth_client, row, seed_periods[2])

        assert _dates(row) == (_JAN_16, _JAN_16)
        assert _balances(seed_user, card, _FEB_1, _FEB_13) == (Decimal("900.00"), _ZERO)

    @pytest.mark.usefixtures("on_jan_30")
    def test_added_on_payday_it_counts_from_that_day(
        self, auth_client, seed_user, seed_periods,
    ):
        """Added Jan 30 to Jan 30's paycheck, moved to Feb 13's: stays Jan 30.

        The card owes it from Jan 30, so at the end of Feb 12 it owes $100.00, Feb 13
        pays that and Feb 27 pays nothing.  Today reads as passed.  The position read
        took the first day for the default and re-dated it Feb 13: Feb 13 $0.00, Feb 27
        $100.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])
        assert row.due_date == _JAN_30

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_JAN_30, _JAN_30)
        assert _price(template, _FEB_13, _JAN_30) == Decimal("100.00")
        assert _price(template, _FEB_27, _JAN_30) == _ZERO

    @pytest.mark.usefixtures("on_jan_31")
    def test_added_a_day_after_payday_it_reads_the_same(
        self, auth_client, seed_user, seed_periods,
    ):
        """The same act a day later: stays Jan 31, Feb 13 $100.00, Feb 27 $0.00."""
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])
        assert row.due_date == _JAN_31

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_JAN_31, _JAN_31)
        assert _price(template, _FEB_13, _JAN_31) == Decimal("100.00")
        assert _price(template, _FEB_27, _JAN_31) == _ZERO

    @pytest.mark.usefixtures("on_the_night_of_jan_21")
    def test_passed_is_by_the_owners_day_not_the_servers(
        self, auth_client, seed_user, seed_periods,
    ):
        """At 11:30 pm Eastern on Jan 21, a bill dated Jan 22 is still ahead: Jan 30.

        Read by UTC's day, already Jan 22, it would have passed and stayed Jan 22.
        """
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_22,
        )

        _move(auth_client, row, seed_periods[2])

        assert _dates(row) == (_JAN_30, _JAN_30)


class TestABillDatedAhead:
    """A bill dated ahead is still a plan, so its date follows its paycheck."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_moved_back_to_a_paycheck_that_has_started_it_takes_today(
        self, auth_client, seed_user, seed_periods,
    ):
        """The ruling's second example: $100 dated Jan 30, $0 trued up Jan 20, moved Jan 22.

        It reads Jan 22, after the true-up, so at the end of Jan 29 the card owes $100.00,
        Jan 30 pays it and checking ends 1,000 - 100 = $900.00.  Re-dated Jan 16, as the
        position read did, the true-up held it: Jan 30 $0.00, checking $1,000.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "0.00", _JAN_20)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])
        assert row.due_date == _JAN_30

        _move(auth_client, row, seed_periods[1])

        assert _dates(row) == (_JAN_22, _JAN_22)
        assert _price(template, _JAN_30, _JAN_22) == Decimal("100.00")
        assert _balances(seed_user, card, _JAN_22, _JAN_30) == (Decimal("900.00"), _ZERO)

    @pytest.mark.usefixtures("on_jan_22")
    def test_moved_back_the_day_of_a_true_up_it_is_inside_it(
        self, auth_client, seed_user, seed_periods,
    ):
        """Trued up to $0 on Jan 22 and moved Jan 22: Jan 30 $0.00, checking $1,000.00.

        It takes today, and a true-up closes its own day (ruling plan#2965's "on or
        before"), so this one holds it, as it holds a row added that day.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "0.00", _JAN_22)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])

        _move(auth_client, row, seed_periods[1])

        assert _dates(row) == (_JAN_22, _JAN_22)
        assert _price(template, _JAN_30, _JAN_22) == _ZERO
        assert _balances(seed_user, card, _JAN_22, _JAN_30) == (Decimal("1000.00"), _ZERO)

    @pytest.mark.usefixtures("on_jan_22")
    def test_moved_back_to_a_paycheck_that_has_ended_it_still_takes_today(
        self, auth_client, seed_user, seed_periods,
    ):
        """Moved to Jan 2's paycheck, over by Jan 22: the same day, not Jan 2."""
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])

        _move(auth_client, row, seed_periods[0])

        assert _dates(row) == (_JAN_22, _JAN_22)

    @pytest.mark.usefixtures("on_jan_22")
    def test_moved_forward_it_takes_the_new_first_day(
        self, auth_client, seed_user, seed_periods,
    ):
        """Dated Jan 25, a day that has not come, moved on Jan 22 to Feb 13's paycheck.

        It reads Feb 13, so the card owes it from then: Jan 30 $0.00, Feb 13 $0.00, Feb 27
        $100.00.  The position read kept Jan 25, and Jan 30 paid a bill put off to February.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_25,
        )

        _move(auth_client, row, seed_periods[3])

        assert _dates(row) == (_FEB_13, _FEB_13)
        assert _price(template, _JAN_30, _JAN_22) == _ZERO
        assert _price(template, _FEB_13, _JAN_22) == _ZERO
        assert _price(template, _FEB_27, _JAN_22) == Decimal("100.00")

    @pytest.mark.usefixtures("on_feb_1")
    def test_carried_forward_it_takes_today(self, auth_client, seed_user, seed_periods):
        """Dated Feb 5 in Jan 16's paycheck, carried on Feb 1 into Jan 30's: Feb 1.

        Jan 30's paycheck has started, so not its first day, which is before today.  The
        card owes $100.00 at the end of Feb 12, Feb 13 pays it, and checking ends $900.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_FEB_5,
        )

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        assert _dates(row) == (_FEB_1, _FEB_1)
        assert _price(template, _FEB_13, _FEB_1) == Decimal("100.00")
        assert _balances(seed_user, card, _FEB_1, _FEB_13) == (Decimal("900.00"), _ZERO)


class TestAnExtraPayment:
    """An extra payment is not sent until marked, so its date always follows."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_put_off_it_comes_off_what_is_owed_after_a_second_true_up(
        self, auth_client, seed_user, seed_periods,
    ):
        """The ruling's third example: $800 trued up Jan 20, $200 added Jan 22, moved.

        Moved to Jan 30's paycheck it reads Jan 30.  $800 is trued up again on Jan 25,
        which cannot hold a payment dated after it.  Jan 30 pays 800 - 200 = $600.00 and
        checking ends 1,000 - 200 - 600 = $200.00.  Kept on Jan 22, as the position read
        did, the true-up held it: Jan 30 $800.00, checking $0.00, $200.00 too low.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _true_up(card, "800.00", _JAN_20)
        xfer = _add_extra_payment(auth_client, seed_user, card, seed_periods[1])
        assert xfer.due_date == _JAN_22

        _move(auth_client, xfer, seed_periods[2])

        assert _dates(xfer) == (_JAN_30, _JAN_30)
        _true_up(card, "800.00", _JAN_25)
        assert _price(template, _JAN_30, _JAN_25) == Decimal("600.00")
        assert _balances(seed_user, card, _JAN_25, _JAN_30) == (Decimal("200.00"), _ZERO)

    @pytest.mark.usefixtures("on_jan_22")
    def test_moved_to_a_paycheck_that_has_started_it_takes_today(
        self, auth_client, seed_user, seed_periods,
    ):
        """Added Jan 22 to Jan 30's paycheck, dated Jan 30; moved back: Jan 22."""
        card = _card(seed_user)
        xfer = _add_extra_payment(auth_client, seed_user, card, seed_periods[2])
        assert xfer.due_date == _JAN_30

        _move(auth_client, xfer, seed_periods[1])

        assert _dates(xfer) == (_JAN_22, _JAN_22)

    @pytest.mark.usefixtures("on_jan_22")
    @pytest.mark.parametrize("typed", [_JAN_28, _JAN_16])
    def test_a_new_date_typed_in_the_same_save_stands(
        self, auth_client, seed_user, seed_periods, typed,
    ):
        """Typed beside the move it stands, its old paycheck's first day included.

        Off a card a typed Jan 16, the paycheck it is leaving's first day, would read as
        the default and move to Jan 30.
        """
        card = _card(seed_user)
        xfer = _add_extra_payment(auth_client, seed_user, card, seed_periods[1])

        _move(auth_client, xfer, seed_periods[2], due_date=typed.isoformat())

        assert _dates(xfer) == (typed, typed)

    @pytest.mark.usefixtures("on_feb_1")
    def test_carried_forward_it_replaces_the_date_typed_earlier(
        self, auth_client, seed_user, seed_periods,
    ):
        """Jan 18 typed earlier and not sent, carried on Feb 1: Feb 1 (the ruling's cost)."""
        card = _card(seed_user)
        xfer = _add_extra_payment(auth_client, seed_user, card, seed_periods[1])
        response = _save_transfer(auth_client, xfer, due_date=_JAN_18.isoformat())
        assert response.status_code == 200, response.data[:400]
        db.session.refresh(xfer)
        assert xfer.due_date == _JAN_18

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        assert _dates(xfer) == (_FEB_1, _FEB_1)
        assert xfer.pay_period_id == seed_periods[2].id

    @pytest.mark.usefixtures("on_jan_22")
    def test_the_account_is_the_one_the_update_leaves(self, seed_user, seed_periods):
        """Re-pointed from savings onto the card beside the move: it follows, to Jan 30.

        Graded by the account it held, savings, the Jan 22 would read as stated and stay.
        Re-pointed off the card it takes savings' rule and its Jan 22 stays.  Through the
        service door: the popover posts no account.
        """
        card = _card(seed_user)
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Rainy Day",
            anchor_balance=_ZERO, observed_on=date(2026, 1, 2),
        )
        onto = _survivor(seed_user, savings, seed_periods[1], _JAN_22, name="Onto")
        off = _survivor(seed_user, card, seed_periods[1], _JAN_22, name="Off")

        transfer_service.update_transfer(
            onto.id, seed_user["user"].id,
            pay_period_id=seed_periods[2].id, to_account_id=card.id,
        )
        transfer_service.update_transfer(
            off.id, seed_user["user"].id,
            pay_period_id=seed_periods[2].id, to_account_id=savings.id,
        )
        db.session.commit()

        assert _dates(onto) == (_JAN_30, _JAN_30)
        assert _dates(off) == (_JAN_22, _JAN_22)


def _survivor(seed_user, to_account, period, day, *, name):
    """A cleared cadence's transfer into *to_account*, dated *day*, committed."""
    template = make_transfer_template(db.session, seed_user, to_account)
    template.name = name
    xfer = generate_transfer_of(template, period)
    template.recurrence_rule = None
    xfer.due_date = day
    xfer.occurs_on = day
    db.session.commit()
    assert xfer.is_placed is True
    return xfer


class TestATypedDateOnABill:
    """A date typed in the same save stands on a bill too, whichever side of today."""

    @pytest.mark.usefixtures("on_jan_22")
    @pytest.mark.parametrize("typed", [_JAN_18, _JAN_16, _JAN_28])
    def test_it_stands(self, auth_client, seed_user, seed_periods, typed):
        """Jan 22 held, moved to Jan 30's with a date typed: that date."""
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[1])

        _move(auth_client, row, seed_periods[2], due_date=typed.isoformat())

        assert _dates(row) == (typed, typed)

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_date_dated_ahead_and_typed_is_not_moved_to_the_paycheck(
        self, auth_client, seed_user, seed_periods,
    ):
        """Held Jan 30, typed Jan 28 beside a move to Jan 16's: Jan 28, not today."""
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])

        _move(auth_client, row, seed_periods[1], due_date=_JAN_28.isoformat())

        assert _dates(row) == (_JAN_28, _JAN_28)


class TestAnEnvelopeOnTheCard:
    """No card counts from an envelope's date, so a move takes ruling R-BAL33 there."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_it_takes_the_new_first_day_not_today(
        self, auth_client, seed_user, seed_periods,
    ):
        """On Jan 30's first day, moved to Jan 16's: Jan 16.  A bill would read Jan 22."""
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[2],
            door=_THE_ADD_FORM, envelope=True,
        )
        assert row.due_date == _JAN_30

        _move(auth_client, row, seed_periods[1])

        assert _dates(row) == (_JAN_16, _JAN_16)

    @pytest.mark.usefixtures("on_jan_22")
    def test_made_an_envelope_in_the_same_save_it_is_one(
        self, auth_client, seed_user, seed_periods,
    ):
        """A bill ticked to track purchases beside the move: Jan 16, by what it becomes."""
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])

        _move(auth_client, row, seed_periods[1], is_envelope="true")

        assert row.tracks_purchases is True
        assert _dates(row) == (_JAN_16, _JAN_16)

    @pytest.mark.usefixtures("on_jan_22")
    def test_made_a_bill_in_the_same_save_it_is_one(
        self, auth_client, seed_user, seed_periods,
    ):
        """An envelope unticked beside the move: Jan 22, by what it becomes."""
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[2],
            door=_THE_ADD_FORM, envelope=True,
        )

        _move(auth_client, row, seed_periods[1], is_envelope="false")

        assert row.tracks_purchases is False
        assert _dates(row) == (_JAN_22, _JAN_22)


class TestAMoveWithNoEnvelopeBox:
    """A request posting no ``is_envelope`` is read by the flag the row already holds."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_an_envelope_stays_an_envelope(self, auth_client, seed_user, seed_periods):
        """On Jan 30's first day, moved to Jan 16's by a bare PATCH: Jan 16, not today."""
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[2],
            door=_THE_ADD_FORM, envelope=True,
        )

        response = auth_client.patch(f"/transactions/{row.id}", data={
            "pay_period_id": str(seed_periods[1].id), "version_id": row.version_id,
        })

        assert response.status_code == 200, response.data[:400]
        assert _dates(row) == (_JAN_16, _JAN_16)

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_bill_stays_a_bill(self, auth_client, seed_user, seed_periods):
        """Dated Jan 30, moved to Jan 16's by a bare PATCH: today, Jan 22."""
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])

        response = auth_client.patch(f"/transactions/{row.id}", data={
            "pay_period_id": str(seed_periods[1].id), "version_id": row.version_id,
        })

        assert response.status_code == 200, response.data[:400]
        assert _dates(row) == (_JAN_22, _JAN_22)


class TestOffACard:
    """Off a card rulings R-BAL33 and R-BAL93 stand: the same acts, the old answers."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_first_day_row_takes_the_new_first_day(
        self, auth_client, seed_user, seed_periods,
    ):
        """On Jan 16's first day, moved to Jan 30's: Jan 30.  On a card it would stay."""
        row = _add_bill(auth_client, seed_user, seed_user["account"], seed_periods[1])
        assert row.due_date == _JAN_16

        _move(auth_client, row, seed_periods[2])

        assert _dates(row) == (_JAN_30, _JAN_30)

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_date_dated_ahead_stays(self, auth_client, seed_user, seed_periods):
        """Dated Jan 28 in Jan 16's paycheck, moved to Jan 30's: Jan 28 (on a card, Jan 30)."""
        row = _add_bill(
            auth_client, seed_user, seed_user["account"], seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_28,
        )

        _move(auth_client, row, seed_periods[2])

        assert _dates(row) == (_JAN_28, _JAN_28)

    @pytest.mark.usefixtures("on_jan_22")
    def test_a_transfer_to_savings_is_read_by_position(
        self, auth_client, seed_user, seed_periods,
    ):
        """Dated Jan 16, moved to Jan 30's: Jan 30.  Then typed Feb 5 and moved: Feb 5."""
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Rainy Day",
            anchor_balance=_ZERO, observed_on=date(2026, 1, 2),
        )
        db.session.commit()
        xfer = _add_extra_payment(auth_client, seed_user, savings, seed_periods[1])
        assert xfer.due_date == _JAN_16

        _move(auth_client, xfer, seed_periods[2])
        assert _dates(xfer) == (_JAN_30, _JAN_30)

        _move(auth_client, xfer, seed_periods[1], due_date=_FEB_5.isoformat())
        assert _dates(xfer) == (_FEB_5, _FEB_5)


class TestOneBatch:
    """One Carry Forward holds every answer, and its modal says each before the press."""

    @pytest.mark.usefixtures("on_feb_1")
    def test_card_rows_and_other_rows_differ_and_the_modal_says_so(
        self, auth_client, seed_user, seed_periods,
    ):
        """Carried on Feb 1 from Jan 16's paycheck into Jan 30's.

        On the card: Jan 16 has passed and stays, Feb 5 is ahead and takes today.  On
        checking: the first day takes Jan 30, and Feb 5 stays where the card's would not.
        """
        card = _card(seed_user)
        rows = {}
        for key, account, typed in (
            ("bought", card, _JAN_16),
            ("planned", card, _FEB_5),
            ("first_day", seed_user["account"], _JAN_16),
            ("stated", seed_user["account"], _FEB_5),
        ):
            rows[key] = _named_bill(auth_client, seed_user, account, seed_periods[1], key, typed)

        modal = _modal(auth_client, seed_periods[1])
        assert "It keeps its date, Jan 16, 2026." in _sentence_for(modal, "bought")
        assert "It will be dated Feb 01, 2026." in _sentence_for(modal, "planned")
        assert "It will be dated Jan 30, 2026." in _sentence_for(modal, "first_day")
        assert "It keeps its date, Feb 05, 2026." in _sentence_for(modal, "stated")

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        assert _dates(rows["bought"]) == (_JAN_16, _JAN_16)
        assert _dates(rows["planned"]) == (_FEB_1, _FEB_1)
        assert _dates(rows["first_day"]) == (_JAN_30, _JAN_30)
        assert _dates(rows["stated"]) == (_FEB_5, _FEB_5)
        for row in rows.values():
            assert row.pay_period_id == seed_periods[2].id

    @pytest.mark.usefixtures("on_feb_1")
    def test_the_modal_says_what_an_extra_payment_will_read(
        self, auth_client, seed_user, seed_periods,
    ):
        """Dated Feb 1, today, carried to a paycheck that has started: it stays today."""
        card = _card(seed_user)
        xfer = _add_extra_payment(auth_client, seed_user, card, seed_periods[1])
        assert xfer.due_date == _FEB_1

        modal = _modal(auth_client, seed_periods[1])

        assert "It keeps its date, Feb 01, 2026." in modal


def _named_bill(client, seed_user, account, period, name, typed):
    """A $100.00 bill called *name*, added by the Add Transaction form, dated *typed*."""
    response = client.post(_THE_ADD_FORM, data={
        "name": name,
        "estimated_amount": "100.00",
        "account_id": account.id,
        "category_id": seed_user["categories"]["Groceries"].id,
        "pay_period_id": period.id,
        "transaction_type_id": ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        "scenario_id": seed_user["scenario"].id,
        "due_date": typed.isoformat(),
    })
    assert response.status_code == 201, response.data[:400]
    return db.session.query(Transaction).filter_by(name=name).one()


def _sentence_for(modal, name):
    """The stretch of the modal listing the row called *name*, up to the next row."""
    _, _, after = modal.partition(f"<strong>{name}</strong>")
    assert after, f"the modal lists no row called {name}"
    return after.partition('<li class="list-group-item')[0]


class TestARowOfNoOtherShape:
    """Only a placed row is re-placed: the modal says nothing of the rest."""

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_repeating_transfer_into_the_card_says_no_date(
        self, auth_client, seed_user, seed_periods,
    ):
        """The payoff row of Jan 16's paycheck is carried and the modal names no date."""
        card = _card(seed_user)
        _payoff(seed_user, card)

        modal = _modal(auth_client, seed_periods[1])

        assert "Move transfer" in modal
        assert "It keeps its date" not in modal
        assert "It will be dated" not in modal

