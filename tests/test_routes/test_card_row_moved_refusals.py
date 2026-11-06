"""
Shekel Budget App -- a day another row answers refuses a move on the card

Plan step **credit_card:CC-6**, its task plan#2977 (ruling **plan#2976**), split from
``test_card_row_moved.py``, whose docstring states the rule, the money and the clock.  A
row moved on the card can take today or a first day, and the occurrence index refuses a
day a sibling already answers, so each door refuses it in words, and Carry Forward's modal
says so before the press.  Rows that answer one day and are dated another are a cleared
cadence's survivors (ruling R-BAL94's shape), written here by hand because no door can
now make one.
"""

from datetime import date

import pytest

from app.extensions import db
from tests._test_helpers import generate_transfer_of, make_transfer_template
from tests.test_routes.test_card_row_moved import (
    _a_second_row_of,
    _carry,
    _dates,
    _FEB_1,
    _FEB_5,
    _FEB_13,
    _JAN_16,
    _JAN_18,
    _JAN_20,
    _JAN_22,
    _JAN_25,
    _JAN_30,
    _JAN_31,
    _modal,
    _move,
    _save_row,
)
from tests.test_routes.test_card_row_with_no_date import (
    _add_bill,
    _held_at,
    _THE_ADD_FORM,
)
from tests.test_services.test_card_payment_price import _card


@pytest.fixture(name="on_jan_22")
def _on_jan_22():
    """Today is Jan 22, inside Jan 16's paycheck."""
    with _held_at(_JAN_22):
        yield


@pytest.fixture(name="on_feb_1")
def _on_feb_1():
    """Today is Feb 1, inside Jan 30's paycheck, which is what Carry Forward carries to."""
    with _held_at(_FEB_1):
        yield


class TestADayAnotherRowAnswers:
    """The occurrence index still refuses a day a sibling answers, as a sentence."""

    @pytest.mark.usefixtures("on_jan_22")
    def test_the_popover_refuses_it(self, auth_client, seed_user, seed_periods):
        """Today is the day it would take, and a sibling answers Jan 22: a designed 400."""
        card = _card(seed_user)
        row = _add_bill(auth_client, seed_user, card, seed_periods[2])
        _a_second_row_of(row, seed_periods[3], _JAN_22, seed_user)

        response = _save_row(auth_client, row, pay_period_id=str(seed_periods[1].id))

        assert response.status_code == 400
        assert b"already due that day" in response.data
        assert b"Invalid reference" not in response.data
        assert _dates(row) == (_JAN_30, _JAN_30)

    @pytest.mark.usefixtures("on_feb_1")
    def test_the_batch_refuses_it_and_the_modal_says_so_first(
        self, auth_client, seed_user, seed_periods,
    ):
        """Feb 5 follows to Feb 1, which a sibling in Feb 13's paycheck answers.

        The modal blocks Confirm and names the day; pressed anyway the batch is a 400
        naming it too, and nothing moved.  Unasked, the index answered with an
        ``IntegrityError`` this door has no handler for.
        """
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_FEB_5,
        )
        _a_second_row_of(row, seed_periods[3], _FEB_1, seed_user)

        modal = _modal(auth_client, seed_periods[1])
        assert "already due on 2026-02-01" in modal
        assert "1 row is blocked." in modal
        assert 'aria-disabled="true"' in modal

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 400
        assert b"already due on 2026-02-01" in response.data
        db.session.rollback()
        assert _dates(row) == (_FEB_5, _FEB_5)
        assert row.pay_period_id == seed_periods[1].id

    @pytest.mark.usefixtures("on_feb_1")
    def test_two_rows_following_onto_one_day_refuse_the_batch(
        self, auth_client, seed_user, seed_periods,
    ):
        """Two rows of one definition, Feb 5 and Feb 7, both following to Feb 1.

        Neither answers Feb 1 until the batch writes it, so only the batch can see it.
        """
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_FEB_5,
        )
        other = _a_second_row_of(row, seed_periods[1], date(2026, 2, 7), seed_user)

        modal = _modal(auth_client, seed_periods[1])
        assert "already due on 2026-02-01" in modal
        assert 'aria-disabled="true"' in modal

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 400
        assert b"already due on 2026-02-01" in response.data
        db.session.rollback()
        assert _dates(row) == (_FEB_5, _FEB_5)
        assert _dates(other) == (date(2026, 2, 7), date(2026, 2, 7))

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_row_keeping_its_date_is_not_asked(
        self, auth_client, seed_user, seed_periods,
    ):
        """A sibling answers Jan 16, and a row dated Jan 16 and staying is not refused.

        A cleared cadence's survivor can answer one day and be dated another (ruling
        R-BAL94's shape), so the row's own date can be a day its sibling answers.  It
        answers what it answered, so nothing collides, and the batch carries it.
        """
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_16,
        )
        row.occurs_on = date(2026, 1, 10)
        sibling = _a_second_row_of(row, seed_periods[3], _JAN_16, seed_user)
        sibling.due_date = _FEB_13
        db.session.commit()

        modal = _modal(auth_client, seed_periods[1])
        assert "It keeps its date, Jan 16, 2026." in modal
        assert 'aria-disabled="true"' not in modal

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        db.session.refresh(row)
        assert row.due_date == _JAN_16
        assert row.occurs_on == date(2026, 1, 10)

    @pytest.mark.usefixtures("on_feb_1")
    def test_the_paycheck_holding_a_row_is_said_first_too(
        self, auth_client, seed_user, seed_periods,
    ):
        """The target holds a row of the definition: the modal blocks what the batch refuses."""
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_16,
        )
        _a_second_row_of(row, seed_periods[2], _JAN_31, seed_user)

        modal = _modal(auth_client, seed_periods[1])
        assert "already holds a row" in modal
        assert 'aria-disabled="true"' in modal

        response = _carry(auth_client, seed_periods[1])
        assert response.status_code == 400
        assert b"already holds a row" in response.data

    @pytest.mark.usefixtures("on_feb_1")
    def test_two_extra_payments_following_onto_one_day_refuse_the_batch(
        self, auth_client, seed_user, seed_periods,
    ):
        """A cleared cadence's two survivors into the card both follow to today, Feb 1.

        The door refuses the second, nothing moves, and the modal says so before the
        press, with the remedy that works: an extra payment's date always follows, so no
        date typed ahead of the batch would stand.
        """
        first, second = _two_survivors_in_one_paycheck(seed_user, seed_periods)

        modal = _modal(auth_client, seed_periods[1])
        assert "already due on 2026-02-01" in modal
        assert "Move one of the two by its own edit" in modal
        assert 'aria-disabled="true"' in modal

        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 400
        assert b"already due that day" in response.data
        db.session.rollback()
        for xfer in (first, second):
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[1].id
        assert first.due_date == _JAN_16
        assert second.due_date == _JAN_20

    @pytest.mark.usefixtures("on_feb_1")
    def test_moving_one_extra_payment_by_its_own_edit_clears_the_refusal(
        self, auth_client, seed_user, seed_periods,
    ):
        """The remedy the modal names: Jan 25 typed beside one's move, then the other carries."""
        first, second = _two_survivors_in_one_paycheck(seed_user, seed_periods)

        _move(auth_client, second, seed_periods[2], due_date=_JAN_25.isoformat())

        assert 'aria-disabled="true"' not in _modal(auth_client, seed_periods[1])
        response = _carry(auth_client, seed_periods[1])
        assert response.status_code == 200, response.data[:400]
        assert _dates(first) == (_FEB_1, _FEB_1)
        assert _dates(second) == (_JAN_25, _JAN_25)

    @pytest.mark.usefixtures("on_feb_1")
    def test_dating_one_bill_on_a_passed_day_clears_the_refusal(
        self, auth_client, seed_user, seed_periods,
    ):
        """The remedy the modal names: Jan 18 typed on one, which then keeps it."""
        card = _card(seed_user)
        row = _add_bill(
            auth_client, seed_user, card, seed_periods[1],
            door=_THE_ADD_FORM, typed=_FEB_5,
        )
        other = _a_second_row_of(row, seed_periods[1], date(2026, 2, 7), seed_user)
        assert "Date one of the two on a day that has passed" in _modal(
            auth_client, seed_periods[1],
        )

        response = _save_row(auth_client, other, due_date=_JAN_18.isoformat())
        assert response.status_code == 200, response.data[:400]

        assert 'aria-disabled="true"' not in _modal(auth_client, seed_periods[1])
        response = _carry(auth_client, seed_periods[1])
        assert response.status_code == 200, response.data[:400]
        assert _dates(row) == (_FEB_1, _FEB_1)
        assert _dates(other) == (_JAN_18, _JAN_18)

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_kept_row_does_not_take_the_day_from_a_row_re_dated_onto_it(
        self, auth_client, seed_user, seed_periods,
    ):
        """Off a card: A stays Jan 30 answering Jan 10, B takes Jan 30, and the batch carries.

        A answers Jan 10, not the day it is dated, so nothing collides.  A lower id than
        B, so a kept row counting against the rows after it refused this batch, off a
        card, where ruling R-BAL33 stands.
        """
        row = _add_bill(
            auth_client, seed_user, seed_user["account"], seed_periods[1],
            door=_THE_ADD_FORM, typed=_JAN_30,
        )
        row.occurs_on = date(2026, 1, 10)
        other = _a_second_row_of(row, seed_periods[1], _JAN_16, seed_user)
        assert row.id < other.id

        assert 'aria-disabled="true"' not in _modal(auth_client, seed_periods[1])
        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        assert _dates(other) == (_JAN_30, _JAN_30)
        db.session.refresh(row)
        assert (row.due_date, row.occurs_on) == (_JAN_30, date(2026, 1, 10))

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_kept_transfer_does_not_take_the_day_from_one_re_dated_onto_it(
        self, auth_client, seed_user, seed_periods,
    ):
        """Into the card: A stays Feb 1 answering Jan 10, B takes Feb 1, and the modal agrees.

        The door lets this batch through, so a modal disabling Confirm would refuse what
        the batch does.
        """
        kept, other = _two_survivors_in_one_paycheck(seed_user, seed_periods)
        kept.due_date = _FEB_1
        kept.occurs_on = date(2026, 1, 10)
        db.session.commit()
        assert kept.id < other.id

        assert 'aria-disabled="true"' not in _modal(auth_client, seed_periods[1])
        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        assert _dates(other) == (_FEB_1, _FEB_1)
        db.session.refresh(kept)
        assert (kept.due_date, kept.occurs_on) == (_FEB_1, date(2026, 1, 10))

    @pytest.mark.usefixtures("on_feb_1")
    def test_a_transfer_keeping_its_date_is_not_asked(
        self, auth_client, seed_user, seed_periods,
    ):
        """Dated today, it stays, though a sibling answers today: the modal lets it carry.

        It answers Jan 10, so nothing collides, and the door does not ask a date it leaves
        alone.
        """
        kept, other = _two_survivors_in_one_paycheck(seed_user, seed_periods)
        kept.due_date = _FEB_1
        kept.occurs_on = date(2026, 1, 10)
        other.pay_period_id = seed_periods[3].id
        other.due_date = _FEB_13
        other.occurs_on = _FEB_1
        db.session.commit()

        assert 'aria-disabled="true"' not in _modal(auth_client, seed_periods[1])
        response = _carry(auth_client, seed_periods[1])

        assert response.status_code == 200, response.data[:400]
        db.session.refresh(kept)
        assert (kept.due_date, kept.occurs_on) == (_FEB_1, date(2026, 1, 10))


def _two_survivors_in_one_paycheck(seed_user, seed_periods):
    """A cleared cadence's two transfers into the card in Jan 16's paycheck, dated Jan 16 and 20."""
    card = _card(seed_user)
    template = make_transfer_template(db.session, seed_user, card)
    first = generate_transfer_of(template, seed_periods[1])
    second = generate_transfer_of(template, seed_periods[0])
    template.recurrence_rule = None
    second.pay_period_id = seed_periods[1].id
    second.due_date = _JAN_20
    second.occurs_on = _JAN_20
    db.session.commit()
    assert first.is_placed is True
    assert second.is_placed is True
    assert first.due_date == _JAN_16
    return first, second
