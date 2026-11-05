"""
Shekel Budget App -- the card payment's figure follows what the card owes now

Plan step **credit_card:CC-6**, its task plan#2927: ruling plan#2887 ("The
scheduled payment always covers what the balance is now"), ruling plan#2910 (the
next payday after a purchase pays it off) and **R-CC22** where they leave it
standing.  Each figure is computed by hand in the test that asserts it.

**The one example**: $800 of groceries bought Jan 3, on a card closing on the 5th
and falling due on the 27th, so the Jan 5 statement is valued at the end of Jan 4
and falls due Jan 27.  **Paydays are Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27.**
Checking opens at $1,000.00.

**A purchase goes on the card through the settle writer the Credit press will
use** (:func:`_on_the_card`): the row stays on checking, its movement is on the
card, on the day it was bought.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import (
    CardPaymentModeEnum,
    SettledDayBasisEnum,
    StatusEnum,
)
from app.extensions import db
from app.services.balance_at import BalanceContext, balance_at, cash_balance_at
from app.services.balance_at._cash_fold import assembled_fold
from app.services.balance_at._cash_periods import period_view_of
from app.services.settle_day import SettleDay
from app.services.transaction_service import settle_transaction
from tests._test_helpers import (
    add_txn,
    append_balance_assertion,
    rebuild_calendar_from_spans,
)
from tests.test_services.test_card_payment_price import (
    _card,
    _one_off,
    _price,
    _row,
    _settle,
    _type_over,
    _works_out,
)

_ZERO = Decimal("0.00")
_JAN_1 = date(2026, 1, 1)
_JAN_16 = date(2026, 1, 16)
_JAN_27 = date(2026, 1, 27)
_JAN_30 = date(2026, 1, 30)
_FEB_27 = date(2026, 2, 27)


def _on_the_card(seed_user, card, period, amount, on, *, name="Groceries"):
    """A row in *period* bought on *card* on *on*, by the writer Credit will use."""
    row = add_txn(
        db.session, seed_user, period, name, Decimal(amount), due_date=on,
    )
    db.session.commit()
    settle_transaction(
        row,
        settle_day=SettleDay(day=on, basis=SettledDayBasisEnum.ENTERED),
        tender_account_id=card.id,
    )
    db.session.commit()
    return row


def _true_up(card, owed, on):
    """Tell the app the card really owes *owed* on *on*."""
    append_balance_assertion(
        db.session, card, -Decimal(owed),
        datetime(on.year, on.month, on.day, 18, tzinfo=timezone.utc),
    )
    db.session.commit()


def _scheduled(seed_user, card, mode=CardPaymentModeEnum.STATEMENT_BALANCE):
    """The opt-in payment on each statement's due date."""
    return _works_out(seed_user, card, mode, on_due_dates=True)


def _payoff(seed_user, card, mode=CardPaymentModeEnum.CARD_BALANCE):
    """The payoff row: a payment each payday."""
    return _works_out(seed_user, card, mode)


def _balances(seed_user, card, as_of, day):
    """What checking and the card hold at the end of *day*, read at *as_of*."""
    ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)
    return (
        balance_at(seed_user["account"], ctx, day),
        cash_balance_at(card, ctx, day),
    )


class TestCappedAtWhatIsOwed:
    """No mode pays more than what is left now of what the row pays."""

    def test_a_true_up_lowers_the_statement_row(self, seed_user, seed_periods):
        """S7: $800 statement, trued up to $300 on Jan 20: the row is $300.

        Read before the true-up it is the control's $800.  Read after: owed on
        Jan 26 is $300, so min($800, $300) = $300.  The card lands on 300 - 300 =
        $0.00, not $500 in credit, and checking on 1,000 - 300 = $700.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        assert _price(template, _JAN_27, date(2026, 1, 18)) == Decimal("800.00")

        _true_up(card, "300.00", date(2026, 1, 20))
        as_of = date(2026, 1, 21)

        assert _price(template, _JAN_27, as_of) == Decimal("300.00")
        assert _balances(seed_user, card, as_of, _JAN_27) == (
            Decimal("700.00"), _ZERO,
        )

    def test_a_true_up_below_the_minimum_lowers_it(self, seed_user, seed_periods):
        """The minimum too: $25.00 is due, but only $10.00 is owed on Jan 26.

        The control: trued up to $300 the floor's $25.00 stands, because the cap
        is a ceiling and min($25, $300) = $25.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(
            seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT,
        )
        _true_up(card, "300.00", date(2026, 1, 19))
        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("25.00")

        _true_up(card, "10.00", date(2026, 1, 20))
        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("10.00")

    def test_a_true_up_lowers_an_overdue_payoff_row(self, seed_user, seed_periods):
        """Jan 16's row unmarked, trued up to $300 Jan 20, read Jan 21: it is $300.

        Owed on Jan 15 is $800, but it lands Jan 22 and owed on Jan 21 is $300.
        Jan 30's row then reads 300 - 300 = $0.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 20))
        as_of = date(2026, 1, 21)

        assert _price(template, _JAN_16, as_of) == Decimal("300.00")
        assert _price(template, _JAN_30, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, date(2026, 1, 22)) == (
            Decimal("700.00"), _ZERO,
        )

    def test_an_overdue_row_counts_money_paid_since(self, seed_user, seed_periods):
        """S9: Jan 27's row unmarked, $800 one-off Paid Jan 30, read Feb 1: $0.00.

        Owed on Feb 1, the day before it lands, is $0.00.  Checking holds
        1,000 - 800 = $200.00, and not -$600.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[2], "800.00", _JAN_30), _JAN_30,
        )
        as_of = date(2026, 2, 1)

        assert _price(template, _JAN_27, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, date(2026, 2, 2)) == (
            Decimal("200.00"), _ZERO,
        )

    def test_a_smaller_extra_reads_low_until_marked(self, seed_user, seed_periods):
        """The bank took Jan 27's $800, then a $100 extra: the cost the owner was told.

        Unmarked, read Feb 1: owed is 800 - 100 = $700, so the row reads $700.00.
        Marked with the real figure and day it is the owner's $800.00, and the card
        holds 800 + 100 - 800 = $100.00 in credit, as it really does.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[2], "100.00", _JAN_30), _JAN_30,
        )
        as_of = date(2026, 2, 1)
        assert _price(template, _JAN_27, as_of) == Decimal("700.00")

        row = _row(template, _JAN_27)
        _type_over(row, "800.00")
        _settle(row, _JAN_27)

        assert _price(template, _JAN_27, as_of) == Decimal("800.00")
        assert _balances(seed_user, card, as_of, date(2026, 2, 2))[1] == (
            Decimal("100.00")
        )

    def test_a_lower_true_up_after_its_date_reads_low_until_marked(
        self, seed_user, seed_periods,
    ):
        """The bank took Jan 27's $800 and the card is trued up to $0.00 on Jan 30.

        Unmarked, read Feb 1, the row reads $0.00, so checking holds $1,000.00,
        $800 above the $200 really there.  Marked with the real figure and day,
        checking holds 1,000 - 800 = $200.00 and the card $0.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _true_up(card, "0.00", _JAN_30)
        as_of = date(2026, 2, 1)
        after_it_lands = date(2026, 2, 2)

        assert _price(template, _JAN_27, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, after_it_lands) == (
            Decimal("1000.00"), _ZERO,
        )

        row = _row(template, _JAN_27)
        _type_over(row, "800.00")
        _settle(row, _JAN_27)

        assert _balances(seed_user, card, as_of, after_it_lands) == (
            Decimal("200.00"), _ZERO,
        )

    def test_it_counts_a_one_off_beside_it(self, seed_user, seed_periods):
        """Trued up to $300, $200 one-off on Jan 27 too: the row is $100.00.

        Owed on Jan 26 is $300, of which the one-off beside the row pays $200:
        300 - 200 = $100.  At $300 the card would land $200 in credit.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 20))
        _one_off(seed_user, card, seed_periods[1], "200.00", _JAN_27)
        as_of = date(2026, 1, 21)

        assert _price(template, _JAN_27, as_of) == Decimal("100.00")
        assert _balances(seed_user, card, as_of, _JAN_27)[1] == _ZERO

    def test_an_overdue_row_counts_an_unmarked_one_off_dated_since(
        self, seed_user, seed_periods,
    ):
        """S9 with the one-off unmarked: both land Feb 2, so the row is $0.00.

        The control is the same two rows read before either is overdue: Jan 27's
        row is $800.00, because R-CC22 does not count Jan 30's money against it.
        Overdue, marked or not must read one figure: 800 - 800 = $0.00, and
        checking holds 1,000 - 800 = $200.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _one_off(seed_user, card, seed_periods[2], "800.00", _JAN_30)
        assert _price(template, _JAN_27, date(2026, 1, 10)) == Decimal("800.00")

        as_of = date(2026, 2, 1)

        assert _price(template, _JAN_27, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, date(2026, 2, 2)) == (
            Decimal("200.00"), _ZERO,
        )

    def test_an_overdue_row_leaves_out_money_still_to_come(
        self, seed_user, seed_periods,
    ):
        """Read on Jan 27 itself: its row lands Jan 28, and so does $100 dated then.

        Money dated after the day the reader stands on has not happened, so R-CC22
        holds: Dec 18's $100 does not count against a Dec 15 row, and the row is
        $800.00.  The next day that $100 is overdue too: 800 - 100 = $700.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 28))

        assert _price(template, _JAN_27, _JAN_27) == Decimal("800.00")
        assert _price(template, _JAN_27, date(2026, 1, 28)) == Decimal("700.00")

    def test_two_overdue_rows_landing_together_pay_what_is_owed_once(
        self, seed_user, seed_periods,
    ):
        """Jan 27's and Feb 27's rows overdue, trued up to $300 on Mar 1.

        Read Mar 10 both land Mar 11, and $1,000 was charged ($800 Jan 3, $200
        Jan 20).  Jan 27: min($800, $300) = $300.  Feb 27: $1,000 at the Feb 5
        close less Jan 27's $300 is $700, but owed on Mar 10 less that same $300
        is $0.00.  The card lands on $0.00, not $300 in credit.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        _on_the_card(
            seed_user, card, seed_periods[1], "200.00", date(2026, 1, 20),
            name="Gas",
        )
        template = _scheduled(seed_user, card)
        _true_up(card, "300.00", date(2026, 3, 1))
        as_of = date(2026, 3, 10)

        assert _price(template, _JAN_27, as_of) == Decimal("300.00")
        assert _price(template, _FEB_27, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, date(2026, 3, 11))[1] == _ZERO

    def test_a_cancelled_row_answers_by_its_own_date(self, seed_user, seed_periods):
        """A cancelled row never lands, so its ceiling reads up to its own date.

        $800 Paid Jan 30, read Feb 1.  An overdue unmarked row would read $0.00
        (:meth:`test_an_overdue_row_counts_money_paid_since`).  Cancelled, the
        row's day is Jan 27: owed on Jan 26 is $800, and Jan 30's money is after
        it, so it answers $800.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[2], "800.00", _JAN_30), _JAN_30,
        )
        row = _row(template, _JAN_27)
        row.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.commit()

        assert _price(template, _JAN_27, date(2026, 2, 1)) == Decimal("800.00")

    def test_a_cancelled_row_reads_a_true_up_before_its_date(
        self, seed_user, seed_periods,
    ):
        """Trued up to $300 on Jan 20: it answers $300.00, having paid nothing.

        So Feb 27's row still reads the $300 owed at the Feb 5 close.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 20))
        row = _row(template, _JAN_27)
        row.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.commit()
        as_of = date(2026, 1, 21)

        assert _price(template, _JAN_27, as_of) == Decimal("300.00")
        assert _price(template, _FEB_27, as_of) == Decimal("300.00")

    def test_a_cancelled_row_ignores_a_true_up_after_its_date(
        self, seed_user, seed_periods,
    ):
        """Trued up to $300 on Jan 30: Jan 27 had passed, so it answers $800.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _true_up(card, "300.00", _JAN_30)
        row = _row(template, _JAN_27)
        row.status_id = ref_cache.status_id(StatusEnum.CANCELLED)
        db.session.commit()

        assert _price(template, _JAN_27, date(2026, 2, 1)) == Decimal("800.00")

    def test_a_settled_rows_own_money_comes_out_of_the_cap(
        self, seed_user, seed_periods,
    ):
        """Paid Jan 20 for $800, so owed on Jan 26 is $0.00 only because of it.

        Its plan is what the card asked of it: its own $800 is no money it counts
        against itself, so min($800, $800) = $800.00, never $0.00.
        Settled through the door while still worked out (plan#2859).
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        row = _row(template, _JAN_27)
        _settle(row, date(2026, 1, 20))
        assert row.settled_figure == Decimal("800.00")

        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("800.00")


class TestAnEachPaydayRowCountsItsPaycheck:
    """Money dated anywhere in its own paycheck shrinks a payoff row."""

    def test_money_dated_after_the_row_in_its_paycheck(self, seed_user, seed_periods):
        """S4b: $800 owed, $200 dated Jan 20, so Jan 16's row is $600.00.

        The paycheck pays 600 + 200 = $800.00, what the card owes, and the card
        ends on $0.00 rather than $200 in credit.  Jan 30's row reads
        800 - 600 - 200 = $0.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "200.00", date(2026, 1, 20))

        assert _price(template, _JAN_16) == Decimal("600.00")
        assert _price(template, _JAN_30) == _ZERO

        ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_1)
        view = period_view_of(
            assembled_fold(seed_user["account"], ctx), ctx.reported_periods(),
        )
        assert view.columns[seed_periods[1].id].expense == Decimal("800.00")
        assert _balances(seed_user, card, _JAN_1, date(2026, 1, 20))[1] == _ZERO

    def test_the_paychecks_last_day_counts(self, seed_user, seed_periods):
        """$200 dated Jan 29, the last day of Jan 16's paycheck: 800 - 200 = $600.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "200.00", date(2026, 1, 29))

        assert _price(template, _JAN_16) == Decimal("600.00")

    def test_the_next_paydays_money_does_not_count(self, seed_user, seed_periods):
        """$200 dated Jan 30 is the next paycheck's: Jan 16 $800.00, Jan 30 $0.00.

        It is extra, so the card holds 800 - 800 + 200 = $200.00 from Jan 30.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _one_off(seed_user, card, seed_periods[2], "200.00", _JAN_30)

        assert _price(template, _JAN_16) == Decimal("800.00")
        assert _price(template, _JAN_30) == _ZERO
        assert _balances(seed_user, card, _JAN_1, _JAN_30)[1] == Decimal("200.00")

    def test_a_due_date_row_keeps_its_own_day(self, seed_user, seed_periods):
        """Jan 27 sits in Jan 16's paycheck; $100 dated Jan 28 does not count.

        R-CC22: Dec 18's $100 does not count against a Dec 15 row.  The row is
        $800.00, though the money falls inside the row's own paycheck.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 28))

        assert _price(template, _JAN_27) == Decimal("800.00")

    def test_a_due_date_row_counts_its_own_day(self, seed_user, seed_periods):
        """$100 on Jan 27 itself does count: 800 - 100 = $700.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "100.00", _JAN_27)

        assert _price(template, _JAN_27) == Decimal("700.00")

    def test_a_paycheck_ends_the_day_before_the_next_payday(self, seed_user):
        """OFF cadence: paydays Jan 2, Jan 16 and Feb 20, then every 7 days.

        Jan 16's paycheck ends Feb 19, so $200 dated Feb 10 is in it: 800 - 200 =
        $600.00.  A span worked out from a cadence of 7 or 14 days would end
        Jan 22 or Jan 29, and read $800.00.
        """
        paychecks = rebuild_calendar_from_spans(seed_user["user"].id, [
            (date(2026, 1, 2), date(2026, 1, 15)),
            (_JAN_16, date(2026, 2, 19)),
            (date(2026, 2, 20), date(2026, 2, 26)),
        ])
        db.session.commit()
        card = _card(seed_user)
        _on_the_card(seed_user, card, paychecks[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _one_off(seed_user, card, paychecks[1], "200.00", date(2026, 2, 10))

        assert _price(template, _JAN_16) == Decimal("600.00")

    def test_the_repeat_decides_and_not_the_amount(self, seed_user, seed_periods):
        """Everything-on-the-card on due dates keeps its day: Jan 28's $100 is out.

        The mode is the payoff row's, and the row is still $800.00, because the
        repeat is what says whether the row's date is a deadline or a paycheck.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card, CardPaymentModeEnum.CARD_BALANCE)
        _one_off(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 28))

        assert _price(template, _JAN_27) == Decimal("800.00")

    def test_a_statement_each_payday_counts_its_paycheck(
        self, seed_user, seed_periods,
    ):
        """The other cross: the statement's mode on paydays, $200 dated Jan 20.

        Jan 16's row reads the Jan 5 statement's $800 less that $200: $600.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE)
        _one_off(seed_user, card, seed_periods[1], "200.00", date(2026, 1, 20))

        assert _price(template, _JAN_16) == Decimal("600.00")


class TestALaterPurchaseIsALaterRowsToPay:
    """A charge after the row's own day does not lift the ceiling back up.

    Each purchase has its own due date (ruling plan#2888) and the next payday
    after it pays it off (ruling plan#2910), so what is owed now is not all this
    row's to pay.
    """

    def test_s9_with_a_new_charge_since(self, seed_user, seed_periods):
        """$800 Paid Jan 30, then $800 charged Jan 31, read Feb 1: $0.00 and $800.00.

        Jan 31's charge is on the Feb 5 statement, due Feb 27.  Checking holds
        1,000 - 800 = $200.00 through Feb 26 and 200 - 800 = -$600.00 from Feb 27,
        which is when the money really leaves, and not 25 days early.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[2], "800.00", _JAN_30), _JAN_30,
        )
        _on_the_card(
            seed_user, card, seed_periods[2], "800.00", date(2026, 1, 31),
            name="Tires",
        )
        as_of = date(2026, 2, 1)

        assert _price(template, _JAN_27, as_of) == _ZERO
        assert _price(template, _FEB_27, as_of) == Decimal("800.00")
        assert _balances(seed_user, card, as_of, date(2026, 2, 26))[0] == (
            Decimal("200.00")
        )
        assert _balances(seed_user, card, as_of, _FEB_27)[0] == Decimal("-600.00")

    def test_s9_with_a_smaller_charge_since(self, seed_user, seed_periods):
        """$800 Paid Jan 30, $300 charged Jan 31: Jan 27 $0.00, Feb 27 $300.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[2], "800.00", _JAN_30), _JAN_30,
        )
        _on_the_card(
            seed_user, card, seed_periods[2], "300.00", date(2026, 1, 31),
            name="Tires",
        )
        as_of = date(2026, 2, 1)

        assert _price(template, _JAN_27, as_of) == _ZERO
        assert _price(template, _FEB_27, as_of) == Decimal("300.00")

    def test_a_charge_after_the_true_up(self, seed_user, seed_periods):
        """Trued up to $300 Jan 20, $400 charged Jan 22: $300.00, then $400.00.

        The $400 is on the Feb 5 statement.  The $700 owed on Jan 26 is not all
        Jan 27's to pay.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 20))
        _on_the_card(
            seed_user, card, seed_periods[1], "400.00", date(2026, 1, 22),
            name="Tires",
        )
        as_of = date(2026, 1, 23)

        assert _price(template, _JAN_27, as_of) == Decimal("300.00")
        assert _price(template, _FEB_27, as_of) == Decimal("400.00")

    def test_a_planned_charge_after_the_true_up(self, seed_user, seed_periods):
        """Trued up to $300 Jan 20, $600 still planned Jan 25: $300.00, then $600.00.

        Without this, the planned charge would lift the ceiling past $800 and
        the true-up would change nothing.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 20))
        add_txn(
            db.session, seed_user, seed_periods[1], "Tires", Decimal("600.00"),
            due_date=date(2026, 1, 25), account=card,
        )
        db.session.commit()
        as_of = date(2026, 1, 21)

        assert _price(template, _JAN_27, as_of) == Decimal("300.00")
        assert _price(template, _FEB_27, as_of) == Decimal("600.00")

    def test_a_purchase_after_an_overdue_payoff_row_waits_for_the_next(
        self, seed_user, seed_periods,
    ):
        """Trued up to $300 Jan 20, $100 bought Jan 21, read Jan 22.

        Jan 16's row is the $300 restated, and the next payday after Jan 21 pays
        the $100 (ruling plan#2910): Jan 30 is 300 + 100 - 300 = $100.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 20))
        _on_the_card(
            seed_user, card, seed_periods[1], "100.00", date(2026, 1, 21),
            name="Gas",
        )
        as_of = date(2026, 1, 22)

        assert _price(template, _JAN_16, as_of) == Decimal("300.00")
        assert _price(template, _JAN_30, as_of) == Decimal("100.00")

    def test_a_purchase_after_money_paid_since_waits_for_the_next(
        self, seed_user, seed_periods,
    ):
        """$800 Paid Jan 31, $300 bought Jan 31, read Feb 1: $0.00, $0.00, $300.00.

        Both of Jan 16's and Jan 30's rows are overdue, and neither is the next
        payday after Jan 31.  Feb 13 is.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[2], "800.00", date(2026, 1, 31)),
            date(2026, 1, 31),
        )
        _on_the_card(
            seed_user, card, seed_periods[2], "300.00", date(2026, 1, 31),
            name="Gas",
        )
        as_of = date(2026, 2, 1)

        assert _price(template, _JAN_16, as_of) == _ZERO
        assert _price(template, _JAN_30, as_of) == _ZERO
        assert _price(template, date(2026, 2, 13), as_of) == Decimal("300.00")


class TestOneFigureMarkedOrNot:
    """Marking a row records it; it does not change what the next row asks."""

    def _before_a_true_up(self, seed_user, seed_periods):
        """$800 statement, $200 dated Jan 15, trued up to $600 on Jan 20."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        extra = _one_off(
            seed_user, card, seed_periods[0], "200.00", date(2026, 1, 15),
        )
        return card, template, extra

    def test_unmarked_money_dated_before_a_true_up_is_inside_it(
        self, seed_user, seed_periods,
    ):
        """The website's $600 already holds Jan 15's $200, so it is not taken twice.

        800 - 200 = $600.00, and not 600 - 200 = $400.00.
        """
        card, template, _extra = self._before_a_true_up(seed_user, seed_periods)
        _true_up(card, "600.00", date(2026, 1, 20))

        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("600.00")

    def test_marked_it_reads_the_same(self, seed_user, seed_periods):
        """The control: the same $200 marked Paid Jan 15 reads $600.00 too."""
        card, template, extra = self._before_a_true_up(seed_user, seed_periods)
        _settle(extra, date(2026, 1, 15))
        _true_up(card, "600.00", date(2026, 1, 20))

        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("600.00")

    def test_money_marked_after_the_true_up_is_not_inside_it(
        self, seed_user, seed_periods,
    ):
        """$200 dated Jan 15 but Paid Jan 25: the Jan 20 true-up cannot hold it.

        Trued up to $600 on Jan 20, read Jan 26: 600 - 200 = $400.00.
        """
        card, template, extra = self._before_a_true_up(seed_user, seed_periods)
        _true_up(card, "600.00", date(2026, 1, 20))
        _settle(extra, date(2026, 1, 25))

        assert _price(template, _JAN_27, date(2026, 1, 26)) == Decimal("400.00")

    def test_unmarked_money_dated_on_the_true_ups_day_is_inside_it(
        self, seed_user, seed_periods,
    ):
        """$200 dated Jan 20, unmarked, trued up to $600 that day: $600.00.

        A true-up closes its own day, so 800 - 200 = $600.00 and not $400.00.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "200.00", date(2026, 1, 20))
        _true_up(card, "600.00", date(2026, 1, 20))

        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("600.00")

    def test_money_on_the_true_ups_day_is_inside_it(self, seed_user, seed_periods):
        """$100 Paid Jan 20, trued up to $300 on Jan 20: $300.00, and not $200.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[1], "100.00", date(2026, 1, 20)),
            date(2026, 1, 20),
        )
        _true_up(card, "300.00", date(2026, 1, 20))

        assert _price(template, _JAN_27, date(2026, 1, 21)) == Decimal("300.00")

    def test_the_minimum_reads_money_dated_before_its_close_as_inside(
        self, seed_user, seed_periods,
    ):
        """$2,000 charged and $1,000 sent, both Jan 3: the statement is $1,000.

        2% of $1,000 is $20.00, so the floor's $25.00 is due: read Jan 1, read
        Jan 10 with the $1,000 unmarked, and marked.  Not 2% of $2,000 less $1,000.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "2000.00", date(2026, 1, 3))
        template = _scheduled(
            seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT,
        )
        extra = _one_off(
            seed_user, card, seed_periods[0], "1000.00", date(2026, 1, 3),
        )
        assert _price(template, _JAN_27, date(2026, 1, 2)) == Decimal("25.00")
        assert _price(template, _JAN_27, date(2026, 1, 10)) == Decimal("25.00")

        _settle(extra, date(2026, 1, 3))

        assert _price(template, _JAN_27, date(2026, 1, 10)) == Decimal("25.00")

    def test_an_earlier_minimum_does_not_cancel_the_next(
        self, seed_user, seed_periods,
    ):
        """Jan 27's $25.00 unmarked: Feb 27 is $25.00 before and after Feb 4.

        Read Feb 3 it lands Feb 4, inside the Feb 5 statement.  Read Feb 4 it
        lands Feb 5, and is dated before that close all the same: 800 - 25 = $775,
        2% is $15.50, so the floor's $25.00 is due either way.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(
            seed_user, card, CardPaymentModeEnum.MINIMUM_PAYMENT,
        )

        assert _price(template, _FEB_27, date(2026, 2, 3)) == Decimal("25.00")
        assert _price(template, _FEB_27, date(2026, 2, 4)) == Decimal("25.00")


class TestPricedRowsCount:
    """Each priced row is money the next one reads, on any day it lands."""

    def test_a_statement_each_payday_after_a_true_up(self, seed_user, seed_periods):
        """Statement mode each payday, trued up to $300 on Jan 10.

        Jan 16: min($800, $300) = $300.  Jan 30: the same statement, $800 less
        that $300 asks $500, but what is left is 300 - 300 = $0.00.  The card
        lands on $0.00, not $300 in credit.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card, CardPaymentModeEnum.STATEMENT_BALANCE)
        _true_up(card, "300.00", date(2026, 1, 10))
        as_of = date(2026, 1, 11)

        assert _price(template, _JAN_16, as_of) == Decimal("300.00")
        assert _price(template, _JAN_30, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, _JAN_30)[1] == _ZERO

    def test_rows_landing_together_are_priced_by_date_and_not_by_id(
        self, seed_user, seed_periods,
    ):
        """The row made first, moved to Feb 20, is priced after Jan 30's and Feb 13's.

        $500 charged Jan 5, read Feb 25, so all three land Feb 26.  Jan 30 is the
        earliest with anything owed: $500.00.  The others read 500 - 500 = $0.00,
        though the moved row holds the lowest id.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "500.00", date(2026, 1, 5))
        template = _payoff(seed_user, card)
        moved = _row(template, _JAN_16)
        later = _row(template, _JAN_30)
        assert moved.id < later.id
        moved.pay_period_id = seed_periods[3].id
        moved.due_date = date(2026, 2, 20)
        db.session.commit()
        as_of = date(2026, 2, 25)

        assert _price(template, _JAN_30, as_of) == Decimal("500.00")
        assert _price(template, date(2026, 2, 13), as_of) == _ZERO
        assert _price(template, date(2026, 2, 20), as_of) == _ZERO


class TestTheNextPaydayPaysItOff:
    """Ruling plan#2910: the next payday after the purchase, wherever its row sits."""

    def test_bought_before_its_rows_paycheck(self, seed_user, seed_periods):
        """Bought Jan 14, row in Jan 16's paycheck: Jan 16 $800.00, Jan 30 $0.00.

        Checking holds 1,000 - 800 = $200.00 from Jan 16, and $1,000.00 before it.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[1], "800.00", date(2026, 1, 14))
        template = _payoff(seed_user, card)
        as_of = date(2026, 1, 15)

        assert _price(template, _JAN_16, as_of) == Decimal("800.00")
        assert _price(template, _JAN_30, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, date(2026, 1, 15))[0] == (
            Decimal("1000.00")
        )
        assert _balances(seed_user, card, as_of, _JAN_16)[0] == Decimal("200.00")

    def test_bought_on_payday_rolls_to_the_next(self, seed_user, seed_periods):
        """The control: bought on Jan 16 itself, Jan 16 $0.00 and Jan 30 $800.00.

        The next payday AFTER its date, so a figure keyed on the row's paycheck
        alone could not satisfy both this test and the one above.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[1], "800.00", _JAN_16)
        template = _payoff(seed_user, card)
        as_of = date(2026, 1, 17)

        assert _price(template, _JAN_16, as_of) == _ZERO
        assert _price(template, _JAN_30, as_of) == Decimal("800.00")

    def test_bought_long_before_pays_ahead_of_its_due_date(
        self, seed_user, seed_periods,
    ):
        """Bought Jan 3, row in Jan 16's paycheck: Jan 16 $800.00, before Jan 27.

        The Jan 5 statement falls due Jan 27, so the paycheck after the row's
        (Jan 30) would be past it.  The card owes $0.00 from Jan 16.
        """
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[1], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        as_of = date(2026, 1, 15)

        assert _price(template, _JAN_16, as_of) == Decimal("800.00")
        assert _price(template, _JAN_30, as_of) == _ZERO
        assert _balances(seed_user, card, as_of, _JAN_27)[1] == _ZERO


class TestWhatStays:
    """The trace's list: the cases the cap and the window must leave alone."""

    def test_the_payoff_row_pays_what_is_owed(self, seed_user, seed_periods):
        """$800 owed: Jan 16 $800.00; with $100 more on Jan 8, $900.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        as_of = date(2026, 1, 10)
        assert _price(template, _JAN_16, as_of) == Decimal("800.00")

        _on_the_card(
            seed_user, card, seed_periods[0], "100.00", date(2026, 1, 8),
            name="Gas",
        )

        assert _price(template, _JAN_16, as_of) == Decimal("900.00")

    def test_a_typed_row_leaves_the_rest_for_the_next(self, seed_user, seed_periods):
        """$300 typed over Jan 16's $800: Jan 30 reads 800 - 300 = $500.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        as_of = date(2026, 1, 10)

        _type_over(_row(template, _JAN_16), "300.00")

        assert _price(template, _JAN_16, as_of) == Decimal("300.00")
        assert _price(template, _JAN_30, as_of) == Decimal("500.00")

    def test_a_one_off_on_the_rows_day_shrinks_it(self, seed_user, seed_periods):
        """$200 one-off on Jan 16: 800 - 200 = $600.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _one_off(seed_user, card, seed_periods[1], "200.00", _JAN_16)

        assert _price(template, _JAN_16, date(2026, 1, 10)) == Decimal("600.00")

    def test_a_true_up_before_the_row_restates_it(self, seed_user, seed_periods):
        """Trued up to $300 on Jan 10: Jan 16 $300.00, Jan 30 $0.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _payoff(seed_user, card)
        _true_up(card, "300.00", date(2026, 1, 10))
        as_of = date(2026, 1, 11)

        assert _price(template, _JAN_16, as_of) == Decimal("300.00")
        assert _price(template, _JAN_30, as_of) == _ZERO

    def test_money_on_the_rules_day_is_already_inside(self, seed_user, seed_periods):
        """$100 Paid Jan 4, the rule's own day: owed is $700.00, taken once."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        template = _scheduled(seed_user, card)
        _settle(
            _one_off(seed_user, card, seed_periods[0], "100.00", date(2026, 1, 4)),
            date(2026, 1, 4),
        )

        assert _price(template, _JAN_27, date(2026, 1, 10)) == Decimal("700.00")

    def test_each_statement_pays_its_own(self, seed_user, seed_periods):
        """$800 Jan 3 on Jan 5's statement, $100 Jan 8 on Feb 5's: $800.00, $100.00."""
        card = _card(seed_user)
        _on_the_card(seed_user, card, seed_periods[0], "800.00", date(2026, 1, 3))
        _on_the_card(
            seed_user, card, seed_periods[0], "100.00", date(2026, 1, 8),
            name="Gas",
        )
        template = _scheduled(seed_user, card)
        as_of = date(2026, 1, 10)

        assert _price(template, _JAN_27, as_of) == Decimal("800.00")
        assert _price(template, _FEB_27, as_of) == Decimal("100.00")
