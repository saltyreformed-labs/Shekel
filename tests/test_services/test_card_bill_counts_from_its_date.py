"""
Shekel Budget App -- a bill on the card counts from its date, marked or not

Plan step **credit_card:CC-6**, its task plan#2928 (ruling **plan#2926**, option A):
a dated row on a card keeps its date in the card's fold, where the standing rule
R-G lands an overdue row tomorrow, and a true-up includes any unmarked row dated on
or before it.  Each figure is computed by hand in the test that asserts it.

**The one example**: $800 dated Jan 3, on a card whose books open Jan 1 with its
first balance stated Jan 2, closing on the 5th and falling due on the 27th, so the
Jan 5 statement is valued at the end of Jan 4 and falls due Jan 27.  **Paydays are
Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27.**  Checking opens at $1,000.00.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.services import bank_agreement, entry_service
from app.services.balance_at import (
    BalanceContext,
    balance_at,
    cash_anchor_history,
    cash_balance_at,
    cash_daily_facts_series,
    cash_outstanding_difference,
    records_balance_at,
    rows_counted_from_their_date,
)
from app.services.balance_at._assertions import assertion_corrections
from app.services.balance_at._cash_fold import assembled_fold
from app.services.balance_at._cash_periods import period_view_of
from app.services.cash_ledger import walk_cash_ledger
from tests._test_helpers import (
    add_entry,
    add_txn,
    generate_row_of,
    make_expense_template,
    typed,
)
from tests.test_services.test_card_payment_dates import _bought_and_refunded
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _on_the_card,
    _payoff,
    _scheduled,
    _true_up,
)
from tests.test_services.test_card_payment_price import (
    _card,
    _one_off,
    _price,
    _settle,
    _spend,
)
from tests.test_services.test_outstanding_difference import _FILE_CHAIN
from tests.test_services.test_statement_import.test_anchor import _seed_import
from tests._legacy_credit import plant_old_credit

_ZERO = Decimal("0.00")
_JAN_1 = date(2026, 1, 1)
_JAN_2 = date(2026, 1, 2)
_JAN_3 = date(2026, 1, 3)
_JAN_4 = date(2026, 1, 4)
_JAN_5 = date(2026, 1, 5)
_JAN_10 = date(2026, 1, 10)
_JAN_11 = date(2026, 1, 11)
_JAN_12 = date(2026, 1, 12)
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_20 = date(2026, 1, 20)
_JAN_21 = date(2026, 1, 21)
_JAN_25 = date(2026, 1, 25)
_JAN_26 = date(2026, 1, 26)
_JAN_27 = date(2026, 1, 27)
_JAN_30 = date(2026, 1, 30)
_FEB_27 = date(2026, 2, 27)


def _pass(seed_user, as_of):
    """A fresh read pass held at *as_of*."""
    return BalanceContext.build(seed_user["user"].id, as_of=as_of)


def _card_at(seed_user, card, as_of, day):
    """What the card holds at the end of *day*, read at *as_of*: negative when owed."""
    return cash_balance_at(card, _pass(seed_user, as_of), day)


def _checking_at(seed_user, as_of, day):
    """What checking holds at the end of *day*, read at *as_of*."""
    return balance_at(seed_user["account"], _pass(seed_user, as_of), day)


def _envelope(seed_user, period, budget, *, on_card=None):
    """A Projected envelope of *budget* in *period*, on *on_card* or on checking."""
    template = make_expense_template(
        db.session, seed_user, amount=budget, name="Groceries",
        category_key="Groceries", is_envelope=True, account=on_card,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row


def _swipe(seed_user, row, card, amount, on, *, name="Kroger"):
    """A purchase of *amount* on *card* under *row*, the bank not yet seen to take it.

    Under a row that lives on *card* it is filed on the row's own account;
    under any other row the CC tick puts it on *card*, as the forms post.
    """
    on_another_account = row.account_id != card.id
    entry_service.create_entry(
        row.id, seed_user["user"].id, entry_service.EntryDetails(
            figure=typed(Decimal(amount)), description=name, purchased_on=on,
            on_card=on_another_account,
            card_account_id=card.id if on_another_account else None,
        ),
    )
    db.session.commit()


class TestItCountsFromItsDate:
    """Ruling plan#2926: "whichever day you look", marked or not."""

    @pytest.mark.parametrize("read", [_JAN_1, _JAN_10, _JAN_20])
    def test_the_next_payday_pays_it_whichever_day_he_looks(
        self, seed_user, seed_periods, read,
    ):
        """$800 dated Jan 3, unmarked: Jan 16 $800.00 and Jan 30 $0.00 on every read.

        As R-G landed it, read Jan 20 gave Jan 16 $0.00 and Jan 30 $800.00.  That
        read is this test's failing control.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        template = _payoff(seed_user, card)

        assert _price(template, _JAN_16, read) == Decimal("800.00")
        assert _price(template, _JAN_30, read) == _ZERO
        assert _card_at(seed_user, card, read, _JAN_3) == Decimal("-800.00")
        assert _card_at(seed_user, card, read, date(2026, 1, 2)) == _ZERO

    @pytest.mark.parametrize("read", [_JAN_4, _JAN_26, date(2026, 2, 1)])
    def test_it_is_on_the_statement_its_date_says(
        self, seed_user, seed_periods, read,
    ):
        """Dated Jan 3, before the Jan 5 close: Jan 27 $800.00, Feb 27 $0.00, every read.

        As R-G landed it, read Jan 26 put it on the Feb 5 statement, due Feb 27.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, read) == Decimal("800.00")
        assert _price(template, _FEB_27, read) == _ZERO

    def test_checking_pays_it_from_the_next_payday(self, seed_user, seed_periods):
        """Read Jan 10: checking $1,000.00 through Jan 15 and 1,000 - 800 = $200.00 Jan 16.

        Read Jan 20 the row is overdue and leaves Jan 21, and still pays the $800.00.
        As R-G landed the bill that read held checking at $1,000.00 through Jan 29.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _payoff(seed_user, card)

        assert _checking_at(seed_user, _JAN_10, date(2026, 1, 15)) == Decimal("1000.00")
        assert _checking_at(seed_user, _JAN_10, _JAN_16) == Decimal("200.00")
        assert _checking_at(seed_user, _JAN_20, _JAN_20) == Decimal("1000.00")
        assert _checking_at(seed_user, _JAN_20, _JAN_21) == Decimal("200.00")

    def test_a_date_before_its_paycheck_is_not_clamped_up_to_it(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 14 with the row in Jan 16's paycheck: Jan 16 $800.00, Jan 30 $0.00.

        Ruling plan#2910.  Read at the row's paycheck's clamp (Jan 16) it would be
        Jan 16 $0.00 and Jan 30 $800.00.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[1], "800.00", date(2026, 1, 14))
        template = _payoff(seed_user, card)

        assert _price(template, _JAN_16, _JAN_1) == Decimal("800.00")
        assert _price(template, _JAN_30, _JAN_1) == _ZERO
        assert _card_at(seed_user, card, _JAN_1, date(2026, 1, 13)) == _ZERO
        assert _card_at(seed_user, card, _JAN_1, date(2026, 1, 14)) == Decimal("-800.00")

    def test_a_date_after_its_paycheck_is_not_clamped_down_to_it(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 20 with the row in Jan 2's paycheck: Jan 16 $0.00, Jan 30 $800.00.

        Clamped to that paycheck's last day, Jan 15, Jan 16's row would pay a
        purchase not yet made.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_20)
        template = _payoff(seed_user, card)

        assert _price(template, _JAN_16, _JAN_1) == _ZERO
        assert _price(template, _JAN_30, _JAN_1) == Decimal("800.00")
        assert _card_at(seed_user, card, _JAN_1, date(2026, 1, 19)) == _ZERO

    def test_the_columns_still_reconcile_when_its_date_leaves_its_paycheck(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 14, in Jan 16's paycheck: its money is Jan 2's column's, its budget not.

        Jan 2's column: net $0.00, timing -$800.00, balance -$800.00.  Jan 16's:
        net -$800.00, timing +$800.00, balance still -$800.00, so each column's change
        is its net plus its timing.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[1], "800.00", date(2026, 1, 14))
        ctx = _pass(seed_user, _JAN_1)

        columns = period_view_of(
            assembled_fold(card, ctx), ctx.reported_periods(),
        ).columns
        first, second = columns[seed_periods[0].id], columns[seed_periods[1].id]

        assert (first.net, first.period_timing) == (_ZERO, Decimal("-800.00"))
        assert first.balance == Decimal("-800.00")
        assert (second.net, second.period_timing) == (
            Decimal("-800.00"), Decimal("800.00"),
        )
        assert second.balance == Decimal("-800.00")

    def test_a_refund_row_counts_from_its_date_too(self, seed_user, seed_periods):
        """$800 and a $100 refund, both dated Jan 3, unmarked, read Jan 10: $700 owed from Jan 3.

        Jan 27's row is 800 - 100 = $700.00.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        add_txn(
            db.session, seed_user, seed_periods[0], "Refund", Decimal("100.00"),
            is_income=True, due_date=_JAN_3, account=card,
        )
        db.session.commit()
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_10) == Decimal("700.00")
        assert _card_at(seed_user, card, _JAN_10, _JAN_3) == Decimal("-700.00")
        assert _card_at(seed_user, card, _JAN_10, _JAN_11) == Decimal("-700.00")


    def test_a_refund_row_past_a_deadline_does_not_shrink_it(
        self, seed_user, seed_periods,
    ):
        """$800 dated Jan 3, $100 refund row dated Jan 28 in Jan 2's paycheck: Jan 27 $800.00.

        Jan 27's row counts money dated by Jan 27.  At the paycheck's clamp, Jan 15,
        the refund would read 800 - 100 = $700.00 and leave $100 of the statement unpaid.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        add_txn(
            db.session, seed_user, seed_periods[0], "Refund", Decimal("100.00"),
            is_income=True, due_date=date(2026, 1, 28), account=card,
        )
        db.session.commit()
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_1) == Decimal("800.00")


class TestWhatKeepsTheStandingRule:
    """R-G stays for checking, and on a card for what has no date of this kind."""

    @pytest.mark.parametrize("with_a_card", [True, False])
    def test_an_overdue_row_on_checking_still_lands_tomorrow(
        self, seed_user, seed_periods, with_a_card,
    ):
        """$100 on checking dated Jan 3, read Jan 20: $1,000.00 Jan 20 and $900.00 Jan 21.

        The same with or without a card, so an owner with no card reads as he did.
        """
        if with_a_card:
            _card(seed_user)
        add_txn(
            db.session, seed_user, seed_periods[0], "Rent", Decimal("100.00"),
            due_date=_JAN_3,
        )
        db.session.commit()

        assert _checking_at(seed_user, _JAN_20, _JAN_3) == Decimal("1000.00")
        assert _checking_at(seed_user, _JAN_20, _JAN_20) == Decimal("1000.00")
        assert _checking_at(seed_user, _JAN_20, date(2026, 1, 21)) == Decimal("900.00")
        fold = assembled_fold(seed_user["account"], _pass(seed_user, _JAN_20))
        assert fold.plan.on_their_dates == {}

    def _payback_on_the_card(self, seed_user, card, period):
        """A $500 row on *card* in the old Credit shape, an undated payback there.

        Planted (``tests._legacy_credit``): the press puts a row on its card
        since plan task plan#2929 and writes no payback, so only an old Credit
        row holds one.

        The one undated row the schema can hold: a row naming a definition must
        carry a date (``ck_transactions_template_row_needs_due_date``).  The row
        itself leaves the plan, so the payback is the card's only item.
        """
        row = add_txn(
            db.session, seed_user, period, "Groceries", Decimal("500.00"),
            due_date=_JAN_3, account=card,
        )
        db.session.commit()
        plant_old_credit(row)
        db.session.commit()

    def test_an_undated_row_on_a_card_lands_tomorrow(self, seed_user, seed_periods):
        """The payback, filed in Jan 16's paycheck, read Jan 20: $0.00 Jan 20, -$500.00 Jan 21."""
        card = _card(seed_user)
        self._payback_on_the_card(seed_user, card, seed_periods[0])

        assert _card_at(seed_user, card, _JAN_20, _JAN_20) == _ZERO
        assert _card_at(seed_user, card, _JAN_20, _JAN_21) == Decimal("-500.00")

    def test_a_true_up_does_not_include_an_undated_row(self, seed_user, seed_periods):
        """$300 stated Jan 18, the payback, read Jan 20: -$300.00, then -$800.00 Jan 21."""
        card = _card(seed_user)
        self._payback_on_the_card(seed_user, card, seed_periods[0])
        _true_up(card, "300.00", _JAN_18)

        assert _card_at(seed_user, card, _JAN_20, _JAN_20) == Decimal("-300.00")
        assert _card_at(seed_user, card, _JAN_20, _JAN_21) == Decimal("-800.00")

    def test_a_swipe_dated_by_the_opening_is_inside_it(self, seed_user, seed_periods):
        """Books open Jan 1, $60 swiped Jan 1 under a $60 envelope: the card reads $0.00."""
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "60.00", on_card=card)
        add_entry(db.session, seed_user, row, Decimal("60.00"), _JAN_1)
        db.session.commit()

        assert _card_at(seed_user, card, _JAN_10, _JAN_10) == _ZERO

    def test_a_row_dated_by_the_opening_is_inside_it(self, seed_user, seed_periods):
        """Books open Jan 1, $800 dated Jan 1: the opening holds it, so it moves nothing.

        The card reads $0.00, Jan 16's row counts only the $50 dated Jan 3, the
        control, and the true-up of Jan 2 books no correction.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_1)
        _spend(seed_user, card, seed_periods[0], "50.00", _JAN_3)
        template = _payoff(seed_user, card)
        ctx = _pass(seed_user, _JAN_10)

        assert _card_at(seed_user, card, _JAN_10, _JAN_2) == _ZERO
        assert _card_at(seed_user, card, _JAN_10, _JAN_3) == Decimal("-50.00")
        assert _price(template, _JAN_16, _JAN_10) == Decimal("50.00")
        assert assembled_fold(card, ctx).corrections[0].delta == _ZERO


class TestAnEnvelopeOnTheCard:
    """Decided under R-BAL207: the swipe keeps its date, the unspent budget lands tomorrow."""

    def test_the_swipe_and_the_budget_part(self, seed_user, seed_periods):
        """$500 envelope on the card, $60 swiped Jan 3, read Jan 10.

        Jan 27's row pays the swipe, $60.00.  The unspent 500 - 60 = $440.00 has not
        been spent, so it lands Jan 11 and is on the Feb 5 statement: Feb 27 $440.00.
        """
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "500.00", on_card=card)
        _swipe(seed_user, row, card, "60.00", _JAN_3)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_10) == Decimal("60.00")
        assert _price(template, _FEB_27, _JAN_10) == Decimal("440.00")
        assert _card_at(seed_user, card, _JAN_10, _JAN_3) == Decimal("-60.00")
        assert _card_at(seed_user, card, _JAN_10, _JAN_10) == Decimal("-60.00")
        assert _card_at(seed_user, card, _JAN_10, _JAN_11) == Decimal("-500.00")

    def test_a_true_up_holds_the_swipe_and_not_the_budget(
        self, seed_user, seed_periods,
    ):
        """$60 stated Jan 5, read Jan 10: -$60.00 Jan 10, then -$500.00 Jan 11.

        The swipe is inside it, or it would read -$120.00 and pay twice.  The $440.00
        unspent is still to come, or the budget would vanish at each true-up.
        """
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "500.00", on_card=card)
        _swipe(seed_user, row, card, "60.00", _JAN_3)
        _true_up(card, "60.00", _JAN_5)

        assert _card_at(seed_user, card, _JAN_10, _JAN_10) == Decimal("-60.00")
        assert _card_at(seed_user, card, _JAN_10, _JAN_11) == Decimal("-500.00")

    def test_a_swipe_under_an_envelope_on_checking(self, seed_user, seed_periods):
        """$500 on checking, $60 on the card Jan 3, read Jan 10.

        Jan 27's row is $60.00.  Checking holds $1,000.00 Jan 10, 1,000 - 440 =
        $560.00 from Jan 11 and 560 - 60 = $500.00 from Jan 27.
        """
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "500.00")
        _swipe(seed_user, row, card, "60.00", _JAN_3)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_10) == Decimal("60.00")
        assert _checking_at(seed_user, _JAN_10, _JAN_10) == Decimal("1000.00")
        assert _checking_at(seed_user, _JAN_10, _JAN_11) == Decimal("560.00")
        assert _checking_at(seed_user, _JAN_10, _JAN_27) == Decimal("500.00")


class TestATrueUpIncludesARowDatedByIt:
    """Ruling plan#2926: the true-up restates the balance, so it holds an unmarked bill."""

    def test_typing_what_the_row_says_does_not_double_it(
        self, seed_user, seed_periods,
    ):
        """$800 dated Jan 3, $800 stated Jan 4, read Jan 25: Jan 27 $800.00, not $1,600.00.

        The card reads -$800.00 from Jan 3 through Jan 26, and checking 1,000 - 800 =
        $200.00 from Jan 27.  Laid on top of the true-up it would be $1,600.00.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, "800.00", _JAN_4)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_25) == Decimal("800.00")
        assert _price(template, _FEB_27, _JAN_25) == _ZERO
        assert _card_at(seed_user, card, _JAN_25, _JAN_4) == Decimal("-800.00")
        assert _card_at(seed_user, card, _JAN_25, _JAN_26) == Decimal("-800.00")
        assert _checking_at(seed_user, _JAN_25, _JAN_27) == Decimal("200.00")

    @pytest.mark.parametrize(
        "stated,row,checking",
        [("300.00", "300.00", "700.00"), ("0.00", "0.00", "1000.00")],
    )
    def test_a_lower_figure_restates_it(
        self, seed_user, seed_periods, stated, row, checking,
    ):
        """$800 dated Jan 3, a lower figure stated Jan 4: Jan 27 pays what was stated."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, stated, _JAN_4)
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_25) == Decimal(row)
        assert _card_at(seed_user, card, _JAN_25, _JAN_26) == -Decimal(stated)
        assert _checking_at(seed_user, _JAN_25, _JAN_27) == Decimal(checking)

    def test_a_row_dated_on_the_true_ups_day_is_inside_it(
        self, seed_user, seed_periods,
    ):
        """A true-up closes its own day: $800 dated and stated Jan 4 reads -$800.00 after it.

        On top of the true-up it would read -$1,600.00 from the day after.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_4)
        _true_up(card, "800.00", _JAN_4)

        assert _card_at(seed_user, card, _JAN_25, _JAN_4) == Decimal("-800.00")
        assert _card_at(seed_user, card, _JAN_25, _JAN_26) == Decimal("-800.00")

    def test_a_row_dated_after_the_true_up_rides_on_top(
        self, seed_user, seed_periods,
    ):
        """$100 stated Jan 4, $800 dated Jan 5: -$100.00, then 100 + 800 = -$900.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_5)
        _true_up(card, "100.00", _JAN_4)

        assert _card_at(seed_user, card, _JAN_25, _JAN_4) == Decimal("-100.00")
        assert _card_at(seed_user, card, _JAN_25, _JAN_5) == Decimal("-900.00")

    def test_only_the_first_true_up_after_it_holds_it(
        self, seed_user, seed_periods,
    ):
        """$800 dated Jan 3, stated $800 Jan 4 and $500 Jan 10: corrections $0.00 and +$300.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, "800.00", _JAN_4)
        _true_up(card, "500.00", _JAN_10)

        corrections = assembled_fold(card, _pass(seed_user, _JAN_25)).corrections

        assert [(c.observed_on, c.delta) for c in corrections[1:]] == [
            (_JAN_4, _ZERO), (_JAN_10, Decimal("300.00")),
        ]
        assert _card_at(seed_user, card, _JAN_25, _JAN_10) == Decimal("-500.00")

    def test_a_swipe_in_flight_is_inside_it(self, seed_user, seed_periods):
        """$60 envelope on the card, swiped Jan 3, $60 stated Jan 5: -$60.00, not -$120.00."""
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "60.00", on_card=card)
        _swipe(seed_user, row, card, "60.00", _JAN_3)
        _true_up(card, "60.00", _JAN_5)

        assert _card_at(seed_user, card, _JAN_25, _JAN_26) == Decimal("-60.00")

    def test_the_true_up_form_and_the_history_read_it(
        self, seed_user, seed_periods,
    ):
        """$800 dated Jan 3, $300 stated Jan 4: "Shekel has" -$800.00, correction +$500.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        ctx = _pass(seed_user, _JAN_10)
        assert records_balance_at(card, ctx, _JAN_4) == Decimal("-800.00")

        _true_up(card, "300.00", _JAN_4)
        ctx = _pass(seed_user, _JAN_10)
        newest = cash_anchor_history(card, ctx).rows[0]

        assert records_balance_at(card, ctx, _JAN_4) == Decimal("-800.00")
        assert records_balance_at(card, ctx, _JAN_5) == Decimal("-300.00")
        assert (newest.ledger, newest.correction) == (
            Decimal("-800.00"), Decimal("500.00"),
        )

    def test_the_posting_writers_replay_stays_the_records_alone(
        self, seed_user, seed_periods,
    ):
        """The stored ledger holds no plan: its correction reads $0.00 before, not -$800.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, "300.00", _JAN_4)

        walk = walk_cash_ledger(card.id, seed_user["scenario"].id)

        assert assertion_corrections(walk)[-1].balance_before == _ZERO


class TestATransferDatedBeforeATrueUp:
    """Decided under R-BAL207: the stated balance holds money sent by then, marked or not."""

    def _owed_and_sent(self, seed_user, seed_periods):
        """$800 owed from Jan 3, $200 one-off dated Jan 5, each-payday row."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        extra = _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_5)
        return card, _payoff(seed_user, card), extra

    def test_it_is_inside_the_true_up(self, seed_user, seed_periods):
        """$600 stated Jan 10, read Jan 11: Jan 16 $600.00, where it read $400.00.

        The card stays -$600.00 past tomorrow.  Checking's leg did leave, so 1,000 -
        200 = $800.00 from Jan 12, and 800 - 600 = $200.00 from Jan 16.
        """
        card, template, _extra = self._owed_and_sent(seed_user, seed_periods)
        _true_up(card, "600.00", _JAN_10)

        assert _price(template, _JAN_16, _JAN_11) == Decimal("600.00")
        assert _card_at(seed_user, card, _JAN_11, _JAN_11) == Decimal("-600.00")
        assert _card_at(seed_user, card, _JAN_11, _JAN_12) == Decimal("-600.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_11) == Decimal("1000.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_12) == Decimal("800.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_16) == Decimal("200.00")

    def test_marked_on_its_date_it_reads_the_same(self, seed_user, seed_periods):
        """The same, Paid Jan 5: Jan 16 $600.00 and checking $200.00 from Jan 16."""
        card, template, extra = self._owed_and_sent(seed_user, seed_periods)
        _settle(extra, _JAN_5)
        _true_up(card, "600.00", _JAN_10)

        assert _price(template, _JAN_16, _JAN_11) == Decimal("600.00")
        assert _card_at(seed_user, card, _JAN_11, _JAN_12) == Decimal("-600.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_16) == Decimal("200.00")

    def test_with_no_true_up_both_legs_land_together(self, seed_user, seed_periods):
        """No true-up, read Jan 11: card -$800.00 and checking $1,000.00 Jan 11, then $200 moves.

        The card reads -$600.00 and checking $800.00 from Jan 12, so the money is
        never on the card before it leaves checking.  Jan 16 is 800 - 200 = $600.00.
        """
        card, template, _extra = self._owed_and_sent(seed_user, seed_periods)

        assert _card_at(seed_user, card, _JAN_11, _JAN_11) == Decimal("-800.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_11) == Decimal("1000.00")
        assert _card_at(seed_user, card, _JAN_11, _JAN_12) == Decimal("-600.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_12) == Decimal("800.00")
        assert _price(template, _JAN_16, _JAN_11) == Decimal("600.00")

    def test_dated_after_the_true_up_it_comes_on_top(self, seed_user, seed_periods):
        """$800 stated Jan 10, $200 dated Jan 12, read Jan 13: -$800.00, then -$600.00 Jan 14."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_12)
        template = _payoff(seed_user, card)
        _true_up(card, "800.00", _JAN_10)
        read = date(2026, 1, 13)

        assert _card_at(seed_user, card, read, read) == Decimal("-800.00")
        assert _card_at(seed_user, card, read, date(2026, 1, 14)) == Decimal("-600.00")
        assert _price(template, _JAN_16, read) == Decimal("600.00")

    def test_its_own_date_decides_and_not_its_paycheck(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 14, filed in Jan 16's paycheck, $600 stated Jan 15: Jan 16 $600.00.

        Compared at that paycheck's clamp, Jan 16, it would come on top of the true-up:
        Jan 16 $400.00.  Checking reads 1,000 - 200 - 600 = $200.00 from Jan 16.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _one_off(seed_user, card, seed_periods[1], "200.00", date(2026, 1, 14))
        template = _payoff(seed_user, card)
        read = date(2026, 1, 15)
        _true_up(card, "600.00", read)

        assert _price(template, _JAN_16, read) == Decimal("600.00")
        assert _checking_at(seed_user, read, _JAN_16) == Decimal("200.00")

    def test_dated_past_its_paycheck_it_stays_out_of_the_true_up(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 20, filed in Jan 2's paycheck, $800 stated Jan 18: it comes on top.

        Compared at the clamp, Jan 15, the true-up would hold it.  Read Jan 19 the card
        reads -$800.00, then -$600.00 once it lands Jan 20.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_20)
        _true_up(card, "800.00", _JAN_18)
        read = date(2026, 1, 19)

        assert _card_at(seed_user, card, read, read) == Decimal("-800.00")
        assert _card_at(seed_user, card, read, _JAN_20) == Decimal("-600.00")

    def test_dated_past_a_deadline_it_does_not_shrink_the_row(
        self, seed_user, seed_periods,
    ):
        """$800 on the Jan 5 statement, $100 dated Jan 28 filed in Jan 2's paycheck: Jan 27 $800.00.

        Read at its paycheck's clamp, Jan 15, it would read 800 - 100 = $700.00.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _one_off(seed_user, card, seed_periods[0], "100.00", date(2026, 1, 28))
        template = _scheduled(seed_user, card)

        assert _price(template, _JAN_27, _JAN_1) == Decimal("800.00")

    def test_the_cost_when_the_money_had_not_really_gone(
        self, seed_user, seed_periods,
    ):
        """$800 stated Jan 10, the $200 unsent: Jan 16 reads $800.00 until he marks it.

        Checking reads 1,000 - 200 - 800 = $0.00 from Jan 16, $200.00 low.  Marked
        with the day it really moved, Jan 12, it comes on top of the true-up: the card
        reads -$600.00, Jan 16 is $600.00 and checking is $200.00.
        """
        card, template, extra = self._owed_and_sent(seed_user, seed_periods)
        _true_up(card, "800.00", _JAN_10)

        assert _price(template, _JAN_16, _JAN_11) == Decimal("800.00")
        assert _checking_at(seed_user, _JAN_11, _JAN_16) == _ZERO

        _settle(extra, _JAN_12)
        read = date(2026, 1, 13)

        assert _card_at(seed_user, card, read, _JAN_12) == Decimal("-600.00")
        assert _price(template, _JAN_16, read) == Decimal("600.00")
        assert _checking_at(seed_user, read, _JAN_16) == Decimal("200.00")


class TestARefundInFlight:
    """plan#2879's review: a refund counts from the day it was made, like any swipe."""

    @pytest.mark.parametrize("posted_on", [None, date(2026, 1, 18)])
    def test_a_true_up_holds_it_posted_or_not(
        self, seed_user, seed_periods, posted_on,
    ):
        """$1,000 bought Jan 3, $50 back Jan 10, $950 stated Jan 20, read Jan 25.

        The card reads -$950.00 on Jan 25 and on Jan 26, where in flight it read
        -$900.00 on Jan 26.  Jan 27's row is 1,000 - 50 = $950.00 and Feb 27's $0.00.
        """
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("1000.00", _JAN_3), refund=("50.00", _JAN_10, posted_on),
        )
        _true_up(card, "950.00", _JAN_20)
        template = _scheduled(seed_user, card)

        assert _card_at(seed_user, card, _JAN_25, _JAN_25) == Decimal("-950.00")
        assert _card_at(seed_user, card, _JAN_25, _JAN_26) == Decimal("-950.00")
        assert _price(template, _JAN_27, _JAN_25) == Decimal("950.00")
        assert _price(template, _FEB_27, _JAN_25) == _ZERO

    def test_posted_after_the_true_up_it_comes_on_top(self, seed_user, seed_periods):
        """Made Jan 10, the bank still at $1,000 on Jan 20, posted Jan 22: -$1,000.00, then -$950.00.

        A movement dated after a true-up comes on top of it (ruling R-FL).  Marked with
        that later day, a refund the Jan 20 figure did hold would read $50 low, the cost
        ``_day_it_counts_from`` states.
        """
        card = _card(seed_user)
        _bought_and_refunded(
            seed_user, card, seed_periods[0],
            bought=("1000.00", _JAN_3), refund=("50.00", _JAN_10, date(2026, 1, 22)),
        )
        _true_up(card, "1000.00", _JAN_20)

        assert _card_at(seed_user, card, _JAN_25, _JAN_20) == Decimal("-1000.00")
        assert _card_at(seed_user, card, _JAN_25, date(2026, 1, 22)) == Decimal("-950.00")


class TestOutstandingDifferenceLearnsThem:
    """The card's books count an unmarked row, so a declared balance holding it is no gap."""

    @pytest.mark.parametrize("marked", [False, True])
    def test_a_declared_balance_holding_it_is_no_difference(
        self, seed_user, seed_periods, marked,
    ):
        """$800 dated Jan 3, $800 stated Jan 4: books -$800.00, difference $0.00, marked or not.

        Counting marked rows alone, an unmarked one would read a -$800.00 difference.
        """
        card = _card(seed_user)
        if marked:
            _on_the_card(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        else:
            _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, "800.00", _JAN_4)

        difference = cash_outstanding_difference(card, _pass(seed_user, _JAN_10))

        assert difference.books == Decimal("-800.00")
        assert difference.amount == _ZERO

    def test_a_row_dated_after_the_declaration_is_not_in_it(
        self, seed_user, seed_periods,
    ):
        """$100 stated Jan 4, $800 dated Jan 5: books $0.00, difference -$100.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_5)
        _true_up(card, "100.00", _JAN_4)

        difference = cash_outstanding_difference(card, _pass(seed_user, _JAN_10))

        assert difference.books == _ZERO
        assert difference.amount == Decimal("-100.00")

    def test_a_row_dated_by_the_opening_is_not_in_it(self, seed_user, seed_periods):
        """Books open Jan 1, $800 dated Jan 1, $0.00 stated Jan 4: books $0.00, difference $0.00."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_1)
        _true_up(card, "0.00", _JAN_4)

        difference = cash_outstanding_difference(card, _pass(seed_user, _JAN_10))

        assert difference.books == _ZERO
        assert difference.amount == _ZERO

    def test_the_books_are_read_in_date_order(self, seed_user, seed_periods):
        """$800 unmarked Jan 5, $100 marked Jan 10, $800 stated Jan 7: books -$800.00.

        The marked row is a later day than the declaration, though it comes first in
        the list the books are built from.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_5)
        _on_the_card(seed_user, card, seed_periods[0], "100.00", _JAN_10)
        _true_up(card, "800.00", date(2026, 1, 7))

        difference = cash_outstanding_difference(card, _pass(seed_user, _JAN_12))

        assert difference.books == Decimal("-800.00")
        assert difference.amount == _ZERO

    def test_the_books_of_checking_still_hold_no_plan(self, seed_user, seed_periods):
        """Checking stated $1,000.00 Jan 4, $100 dated Jan 3 unmarked: books $1,000.00."""
        _card(seed_user)
        add_txn(
            db.session, seed_user, seed_periods[0], "Rent", Decimal("100.00"),
            due_date=_JAN_3,
        )
        db.session.commit()
        _true_up(seed_user["account"], "-1000.00", _JAN_4)

        difference = cash_outstanding_difference(
            seed_user["account"], _pass(seed_user, _JAN_10),
        )

        assert difference.books == Decimal("1000.00")
        assert difference.amount == _ZERO


class TestADaysFacts:
    """``planned`` still promises zero on a past day, and a day's parts still sum."""

    def test_the_row_is_what_moved_that_day(self, seed_user, seed_periods):
        """Read Jan 20: Jan 3 balance and recorded -$800.00, planned $0.00; Jan 4 moved nothing."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)

        facts = cash_daily_facts_series(
            card, _pass(seed_user, _JAN_20), _JAN_3, _JAN_4,
        ).facts

        assert facts[_JAN_3].balance == Decimal("-800.00")
        assert facts[_JAN_3].recorded == Decimal("-800.00")
        assert facts[_JAN_3].planned == _ZERO
        assert facts[_JAN_4].recorded == _ZERO
        assert facts[_JAN_4].balance == Decimal("-800.00")

    def test_a_days_parts_sum_past_and_future(self, seed_user, seed_periods):
        """Jan 16's priced payment is planned, +$800.00; Jan 3's row is recorded, -$800.00.

        Every day from Jan 2 to Jan 20 moves the balance by exactly its three parts,
        and a true-up on the row's own date books what the row did not explain.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, "300.00", _JAN_4)
        _payoff(seed_user, card)

        facts = cash_daily_facts_series(
            card, _pass(seed_user, _JAN_10), _JAN_2, _JAN_20,
        ).facts

        assert facts[_JAN_16].planned == Decimal("300.00")
        assert facts[_JAN_16].recorded == _ZERO
        assert facts[_JAN_4].asserted == Decimal("500.00")
        days = sorted(facts)
        for before, day in zip(days, days[1:]):
            fact = facts[day]
            assert fact.balance - facts[before].balance == (
                fact.recorded + fact.asserted + fact.planned
            )


class TestBooksVersusBank:
    """A past-dated row on a card is the app's claim about that day, and is listed as one."""

    def _statement(self, card, *lines):
        """A statement of *lines* covering Jan 3 through Jan 5."""
        _seed_import(
            db, card, stated="-800.00", effective_on=_JAN_5, evidence=_FILE_CHAIN,
            lines=list(lines), period=(_JAN_3, _JAN_5),
        )
        db.session.commit()

    def test_a_line_a_day_later_reads_as_timing(self, seed_user, seed_periods):
        """$800 dated Jan 3, its line posted Jan 4: residues -$800.00 and +$800.00, net zero.

        The $10 dated and posted Jan 3 agrees, so Jan 3 is -810 - -10 = -$800.00.
        Counting marked rows alone, Jan 4 alone would read an $800.00 disagreement
        though the balances agree from Jan 4.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _spend(seed_user, card, seed_periods[0], "10.00", _JAN_3)
        self._statement(card, (_JAN_3, "-10.00"), (_JAN_4, "-800.00"))

        agreement = bank_agreement.bank_agreement(card, _pass(seed_user, _JAN_10))
        by_day = {day.day: day for day in agreement.days}

        assert by_day[_JAN_3].recorded == Decimal("-810.00")
        assert by_day[_JAN_3].residue == Decimal("-800.00")
        assert by_day[_JAN_4].residue == Decimal("800.00")

    def test_the_rows_it_lists_sum_to_the_days_figure(self, seed_user, seed_periods):
        """Jan 3 lists the row, unmatched, and sums to -$860.00 with a swipe beside it."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        row = _envelope(seed_user, seed_periods[0], "60.00", on_card=card)
        _swipe(seed_user, row, card, "60.00", _JAN_3)
        self._statement(card, (_JAN_3, "-860.00"))
        ctx = _pass(seed_user, _JAN_10)

        detail = bank_agreement.day_detail(card, ctx, _JAN_3)
        day = next(
            day for day in bank_agreement.bank_agreement(card, ctx).days
            if day.day == _JAN_3
        )

        assert [(row.amount, row.description, row.matched) for row in detail.rows] == [
            (Decimal("-800.00"), "Groceries", False),
            (Decimal("-60.00"), "Kroger", False),
        ]
        assert sum(row.amount for row in detail.rows) == day.recorded

    def test_a_transfer_inside_a_true_up_is_listed_on_its_date(
        self, seed_user, seed_periods,
    ):
        """$200 one-off dated Jan 5, $600 stated Jan 10: Jan 5 lists it at +$200.00."""
        card = _card(seed_user)
        _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_5)
        _true_up(card, "600.00", _JAN_10)
        ctx = _pass(seed_user, _JAN_11)

        assert rows_counted_from_their_date(card, ctx, _JAN_5) == [
            (Decimal("200.00"), "Transfer from Checking"),
        ]

    def test_a_row_worth_nothing_is_not_listed(self, seed_user, seed_periods):
        """A $0.00 row dated Jan 3 moved nothing, so nothing is offered to chase."""
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "0.00", _JAN_3)

        assert rows_counted_from_their_date(card, _pass(seed_user, _JAN_10), _JAN_3) == []

    def test_checking_lists_no_plan(self, seed_user, seed_periods):
        """$100 on checking dated Jan 3: nothing counted from Jan 3, however overdue."""
        add_txn(
            db.session, seed_user, seed_periods[0], "Rent", Decimal("100.00"),
            due_date=_JAN_3,
        )
        db.session.commit()

        assert rows_counted_from_their_date(
            seed_user["account"], _pass(seed_user, _JAN_10), _JAN_3,
        ) == []
