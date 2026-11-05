"""
Shekel Budget App -- which statement a card payment pays, and a refund's one date

Plan step **credit_card:CC-6**, its task plan#2879: two defects a review of the
payment's rule measured.  A due-date row pays the statement falling due on its day,
or one that closed later and has already fallen due, which the last close before it
is not where a change of terms moves a due date or cuts a cycle
(:func:`~app.services.card_statement.close_of_statement_paid_on`), and a refund is
dated the day it was made whether or not the bank has been seen to give it.  Each
figure is computed by hand in the test that asserts it.  The rule itself is
:mod:`tests.test_services.test_card_payment_price`.

**The calendar** is ``seed_periods``': paydays Jan 2, Jan 16, Jan 30, Feb 13, Feb 27
and Mar 13 2026 onward.  **The card closes on the 5th and falls due on the 27th**
unless a test says otherwise, on 2% with a $25.00 floor.  **Each pass is held at
Jan 1**, ahead of every row, unless a test holds it later.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import CardPaymentModeEnum
from app.extensions import db
from app.services import transfer_service
from app.services.balance_at import BalanceContext, cash_balance_at
from app.services.card_terms import set_terms
from app.services.carry_forward_service import carry_forward_unpaid
from tests._test_helpers import add_entry, add_txn
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _payoff,
    _scheduled,
    _true_up,
)
from tests.test_services.test_card_payment_price import (
    _card,
    _price,
    _row,
    _spend,
    _spent,
    _type_over,
    _works_out,
)
from tests.test_services.test_due_date_repeat_read_pass import _terms

_ZERO = Decimal("0.00")
_JAN_16 = date(2026, 1, 16)
_JAN_27 = date(2026, 1, 27)
_JAN_30 = date(2026, 1, 30)
_FEB_1 = date(2026, 2, 1)
_FEB_15 = date(2026, 2, 15)
_MAR_6 = date(2026, 3, 6)
_MAR_19 = date(2026, 3, 19)
_APR_6 = date(2026, 4, 6)


def _terms_change(card, on, close_day, due_day):
    """The card's terms change on *on* to 5% with a $10.00 floor, flushed."""
    set_terms(card.id, card.user_id, on, {
        **_terms(close_day, due_day),
        "min_payment_percent": Decimal("0.0500"),
        "min_payment_floor": Decimal("10.00"),
    })
    db.session.flush()


def _a_transitional_cycle(seed_user, seed_periods, *, due_day=27):
    """Close 5 and *due_day*, then close 20 due 15 from Jan 10; $800 Jan 3, $200 Jan 12.

    So the Jan 5 statement is $800 and the Jan 20 statement is $1,000.  Both
    purchases are planned, which a pass held at Jan 1 reads on their own days.
    """
    card = _card(seed_user, due_day=due_day)
    _terms_change(card, date(2026, 1, 10), 20, 15)
    _spend(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
    _spend(seed_user, card, seed_periods[0], "200.00", date(2026, 1, 12))
    return card


def _due_dates_out_of_order(seed_user, seed_periods):
    """Close 20 due 19, then close 5 due 6 from Mar 1; $800 Feb 10, $200 Feb 25.

    The Feb 20 statement is $800, due Mar 19.  The Mar 5 statement is $1,000 and
    holds it, due Mar 6.  Rows on Mar 6, Mar 19 and Apr 6 pay it.  Both purchases are
    recorded as moved, so a pass held after them reads them on their own days too.
    """
    card = _card(seed_user, close_day=20, due_day=19)
    _terms_change(card, date(2026, 3, 1), 5, 6)
    _spent(seed_user, card, seed_periods[2], "800.00", date(2026, 2, 10))
    _spent(seed_user, card, seed_periods[3], "200.00", date(2026, 2, 25))
    return _scheduled(seed_user, card)


class TestADueDateRowPaysItsOwnStatement:
    """Close order is not date order, so a row is told which statement falls due."""

    def test_the_statement_balance_across_a_transitional_cycle(
        self, seed_user, seed_periods,
    ):
        """Jan 27 pays Jan 5's $800; Feb 15 pays Jan 20's 1,000 - 800 = $200.

        Read off the last close before each row, Jan 27 was $1,000 and Feb 15
        $0.00: the total was right, and $200 left two paychecks early.
        """
        card = _a_transitional_cycle(seed_user, seed_periods)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27) == Decimal("800.00")
        assert _price(template, _FEB_15) == Decimal("200.00")

    def test_the_minimum_across_a_transitional_cycle(self, seed_user, seed_periods):
        """Jan 27 is the old terms' $25.00; Feb 15 is the new terms' $50.00 less it.

        Jan 5's statement closed under 2%, floor $25.00: max(25.00, 16.00) = $25.00.
        Jan 20's closed under 5%, floor $10.00: max(10.00, 50.00) = $50.00, less the
        $25.00 dated since it closed: $25.00.  Read off the last close, Jan 27 was
        $50.00 and Feb 15 $0.00.
        """
        card = _a_transitional_cycle(seed_user, seed_periods)
        template = _scheduled(seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT)

        assert _price(template, _JAN_27) == Decimal("25.00")
        assert _price(template, _FEB_15) == Decimal("25.00")

    def test_the_minimum_reads_the_terms_in_force_at_its_close(
        self, seed_user, seed_periods,
    ):
        """Terms changing ON Jan 5 govern the statement closing that day: 5% of 2,000.

        $100.00, where the terms of the day before read 2% of 2,000 = $40.00.
        """
        card = _card(seed_user)
        _terms_change(card, date(2026, 1, 5), 5, 27)
        _spend(seed_user, card, seed_periods[0], "2000.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT)

        assert _price(template, _JAN_27) == Decimal("100.00")

    def test_a_short_cycle_falling_due_first_pays_all_it_holds(
        self, seed_user, seed_periods,
    ):
        """Mar 6 falls due before Mar 19, so Mar 6 is $1,000 and Mar 19 is $0.00.

        Mar 19 reads the Mar 5 statement too, whose $1,000 less the 1,000 dated since
        is $0.00.  The control: a walk taking the earlier statement while it is not
        yet past due reads Mar 6 as $800 and Mar 19 as $0.00, and the $200 waits a
        month past its due date.
        """
        template = _due_dates_out_of_order(seed_user, seed_periods)

        assert _price(template, _MAR_6) == Decimal("1000.00")
        assert _price(template, _MAR_19) == _ZERO
        assert _price(template, _APR_6) == _ZERO

    def test_a_row_moved_past_a_due_date_still_pays_that_statement(
        self, seed_user, seed_periods,
    ):
        """Mar 6's row moved to Mar 7, before Feb 20 falls due: still $1,000.

        The Mar 5 statement has fallen due and holds everything.  The control: asked
        only which statement falls due next, Mar 7 read Feb 20's $800, and the $200
        waited for Apr 6, a month past its due date.
        """
        template = _due_dates_out_of_order(seed_user, seed_periods)
        moved_to = date(2026, 3, 7)
        row = _row(template, _MAR_6)
        transfer_service.update_transfer(row.id, row.user_id, due_date=moved_to)
        db.session.commit()

        assert _price(template, moved_to) == Decimal("1000.00")
        assert _price(template, _MAR_19) == _ZERO
        assert _price(template, _APR_6) == _ZERO

    def test_a_row_carried_past_a_due_date_still_pays_that_statement(
        self, seed_user, seed_periods,
    ):
        """Mar 6's row carried to the Mar 13 paycheck, read Mar 15: still $1,000.

        It keeps its due date, and its paycheck clamps its day to Mar 13, so one press
        reaches this with no edit.  The control: Mar 13 read Feb 20's $800, and the
        $200 waited for Apr 6.
        """
        template = _due_dates_out_of_order(seed_user, seed_periods)
        as_of = date(2026, 3, 15)
        carry_forward_unpaid(
            seed_periods[4].id, seed_periods[5].id, seed_user["scenario"].id,
            balance_ctx=BalanceContext.build(template.user_id, as_of=as_of),
        )
        db.session.commit()

        assert _price(template, _MAR_6, as_of) == Decimal("1000.00")
        assert _price(template, _MAR_19, as_of) == _ZERO
        assert _price(template, _APR_6, as_of) == _ZERO

    def test_what_a_typed_row_leaves_goes_out_at_the_next_due_date(
        self, seed_user, seed_periods,
    ):
        """Mar 6 typed to $300: Mar 19 pays the Mar 5 statement's other 1,000 - 300 = $700.

        That statement has fallen due, so nothing is left for Apr 6.  The control:
        Mar 19 read Feb 20's 800 - 300 = $500, and $200 of an overdue statement waited
        for Apr 6.
        """
        template = _due_dates_out_of_order(seed_user, seed_periods)
        _type_over(_row(template, _MAR_6), "300.00")

        assert _price(template, _MAR_19) == Decimal("700.00")
        assert _price(template, _APR_6) == _ZERO

    def test_a_deleted_row_leaves_the_next_due_date_all_of_it(
        self, seed_user, seed_periods,
    ):
        """Mar 6's row deleted: Mar 19 pays the Mar 5 statement's whole $1,000.

        The control: Mar 19 read Feb 20's $800, and the $200 waited for Apr 6.
        """
        template = _due_dates_out_of_order(seed_user, seed_periods)
        row = _row(template, _MAR_6)
        transfer_service.delete_transfer(row.id, row.user_id, soft=True)
        db.session.commit()

        assert _price(template, _MAR_19) == Decimal("1000.00")
        assert _price(template, _APR_6) == _ZERO

    def test_two_statements_due_on_one_day_are_paid_by_one_row(
        self, seed_user, seed_periods,
    ):
        """Close 28 due 29: Feb 28 and Mar 28 both fall due Mar 29, one row of $1,000.

        $800 Feb 10 is on both, $200 Mar 10 on Mar 28's.  One payment answers both
        (:meth:`~app.services.card_statement.DueDateSchedule.on_or_after`), so it
        pays the later.  The control: the same walk reads Mar 29 as $800, and the
        $200 is paid Apr 29, a month late.
        """
        card = _card(seed_user, close_day=28, due_day=29)
        _spend(seed_user, card, seed_periods[2], "800.00", date(2026, 2, 10))
        _spend(seed_user, card, seed_periods[4], "200.00", date(2026, 3, 10))
        template = _scheduled(seed_user, card)

        assert _price(template, date(2026, 3, 29)) == Decimal("1000.00")
        assert _price(template, date(2026, 4, 29)) == _ZERO

    def test_a_row_moved_a_day_early_still_pays_that_statement(
        self, seed_user, seed_periods,
    ):
        """Jan 27's row moved to Jan 26: Jan 5 falls due next, so $800, then $200.

        Read off the last close, Jan 26 was Jan 20's $1,000 and Feb 15 $0.00.
        """
        card = _a_transitional_cycle(seed_user, seed_periods)
        template = _scheduled(seed_user, card)
        moved_to = date(2026, 1, 26)
        row = _row(template, _JAN_27)
        transfer_service.update_transfer(row.id, row.user_id, due_date=moved_to)
        db.session.commit()

        assert _price(template, moved_to) == Decimal("800.00")
        assert _price(template, _FEB_15) == Decimal("200.00")

    def test_an_each_payday_row_keeps_the_last_close(self, seed_user, seed_periods):
        """Close 5 due 31, then close 20 due 15 from Jan 10: its date is only a paycheck's.

        Jan 16 pays Jan 5's $800.  Jan 30 pays Jan 20's 1,000 - 800 = $200, though
        Jan 5 is not due until Jan 31.  Asked which falls due next, Jan 30 would read
        Jan 5, 800 - 800 = $0.00, and the $200 would wait for Feb 13.
        """
        card = _a_transitional_cycle(seed_user, seed_periods, due_day=31)
        template = _works_out(seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE)

        assert _price(template, _JAN_16) == Decimal("800.00")
        assert _price(template, _JAN_30) == Decimal("200.00")

    def test_a_due_date_row_on_those_terms_pays_its_own(self, seed_user, seed_periods):
        """The same terms on due dates: Jan 31 pays Jan 5's $800, Feb 15 the $200."""
        card = _a_transitional_cycle(seed_user, seed_periods, due_day=31)
        template = _scheduled(seed_user, card)

        assert _price(template, date(2026, 1, 31)) == Decimal("800.00")
        assert _price(template, _FEB_15) == Decimal("200.00")


def _bought_and_refunded(seed_user, card, period, *, bought, refund):
    """An envelope on *card*: a purchase that moved, and a refund entry under it.

    Budgeted at what the two net to, so it holds nothing back and only its two
    movements move the card.

    Args:
        seed_user: The owner.
        card: The card.
        period: The paycheck the envelope is in.
        bought: ``(amount, day)``: the purchase, moved on its day.
        refund: ``(amount, made_on, posted_on)``; ``posted_on`` is ``None`` for a
            refund the bank has not been seen to give.
    """
    amount, bought_on = bought
    back, made_on, posted_on = refund
    row = add_txn(
        db.session, seed_user, period, "Groceries",
        Decimal(amount) - Decimal(back), due_date=bought_on, account=card,
    )
    add_entry(db.session, seed_user, row, Decimal(amount), bought_on, settled_on=bought_on)
    add_entry(
        db.session, seed_user, row, -Decimal(back), made_on,
        settled_on=posted_on, description="Refund",
    )
    db.session.commit()


class TestARefundHasOneDate:
    """A refund counts from the day it was made, in flight or posted."""

    @pytest.mark.parametrize(
        "posted_on", [None, date(2026, 1, 7)], ids=["in_flight", "posted_after_the_close"],
    )
    def test_the_minimum_reads_a_refund_made_before_the_close(
        self, seed_user, seed_periods, posted_on,
    ):
        """$2,000 bought and $500 refunded Jan 3, closing Jan 5: 2% of 1,500 = $30.00.

        The date decides the statement, so the refund is on Jan 5's whichever day it
        posts.  Dated the day it posted, Jan 7, it fell after the close: 2% of 2,000 =
        $40.00 less a $500.00 credit = $0.00, so posting it wiped out the payment.
        """
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("2000.00", date(2026, 1, 3)),
            refund=("500.00", date(2026, 1, 3), posted_on),
        )
        template = _scheduled(seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT)

        assert _price(template, _JAN_27, date(2026, 1, 20)) == Decimal("30.00")

    @pytest.mark.parametrize(
        "posted_on", [None, date(2026, 1, 12)], ids=["in_flight", "posted"],
    )
    def test_the_minimum_reads_a_refund_made_after_the_close_as_a_credit(
        self, seed_user, seed_periods, posted_on,
    ):
        """$10 refunded Jan 10, after the Jan 5 close: 2% of 2,000 = $40.00 - $10.00 = $30.00."""
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("2000.00", date(2026, 1, 3)),
            refund=("10.00", date(2026, 1, 10), posted_on),
        )
        template = _scheduled(seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT)

        assert _price(template, _JAN_27, date(2026, 1, 20)) == Decimal("30.00")

    @pytest.mark.parametrize(
        "posted_on", [None, date(2026, 1, 28)], ids=["in_flight", "posted_after_the_row"],
    )
    def test_a_refund_posted_after_the_row_still_counts_against_it(
        self, seed_user, seed_periods, posted_on,
    ):
        """$50 made Jan 10 and Jan 27's statement row: 500 - 50 = $450.00.

        Read Feb 1, a $150 charge Jan 29 and a true-up to $600 owed Jan 30 lift the
        ceiling to $600, so the row's own figure shows.  Dated the day it posted,
        Jan 28, the refund fell after Jan 27 and the row read $500.00.
        """
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("500.00", date(2026, 1, 3)),
            refund=("50.00", date(2026, 1, 10), posted_on),
        )
        _spent(seed_user, card, seed_periods[1], "150.00", date(2026, 1, 29))
        _true_up(card, "600.00", _JAN_30)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _FEB_1) == Decimal("450.00")

    @pytest.mark.parametrize(
        "posted_on", [None, date(2026, 1, 18)], ids=["in_flight", "posted"],
    )
    def test_the_card_examples_payday_rows(self, seed_user, seed_periods, posted_on):
        """$500 Jan 3, $50 back made Jan 10, $100 Jan 17, read Jan 20.

        Jan 16's row pays 500 - 50 = $450.00; the $100 bought in its own paycheck is
        Jan 30's.  Each-payday rows count their whole paycheck, so this one has read
        the same both ways since plan#2927.
        """
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("500.00", date(2026, 1, 3)),
            refund=("50.00", date(2026, 1, 10), posted_on),
        )
        _spent(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 17))
        template = _payoff(seed_user, card)
        as_of = date(2026, 1, 20)

        assert _price(template, _JAN_16, as_of) == Decimal("450.00")
        assert _price(template, _JAN_30, as_of) == Decimal("100.00")

    def test_its_money_still_moves_the_day_it_posted(self, seed_user, seed_periods):
        """Dating it Jan 3 moves no balance: the card owes $2,000 until Jan 7, then $1,500."""
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("2000.00", date(2026, 1, 3)),
            refund=("500.00", date(2026, 1, 3), date(2026, 1, 7)),
        )
        ctx = BalanceContext.build(seed_user["user"].id, as_of=date(2026, 1, 20))

        assert cash_balance_at(card, ctx, date(2026, 1, 6)) == Decimal("-2000.00")
        assert cash_balance_at(card, ctx, date(2026, 1, 7)) == Decimal("-1500.00")
