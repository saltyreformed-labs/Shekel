"""
Shekel Budget App -- a tick on the card's reconcile panel leaves the money on its date

Plan step **credit_card:CC-6**, its task plan#2958 (ruling **plan#2926**, "marked or
not"; rulings **plan#2888** and **plan#2910**, "the date decides the statement"):
ticking a bill or a swipe on the card's "Has your bank taken these yet?" panel records
the day the card already counts it from, where it recorded the statement's day.

**The one example**: $800 dated Jan 3, on a card whose books open Jan 1, closing on the
5th and falling due on the 27th, so the Jan 5 statement is valued at the end of Jan 4
and falls due Jan 27.  The card is trued up to $800 owed on Jan 20 and its panel is
ticked against that true-up; every read is held at Jan 21.  **Paydays are Jan 2, Jan
16, Jan 30, Feb 13 and Feb 27.**  Checking opens at $1,000.00.

**The failing control** for each case is the statement's day: as the tick recorded it,
$800 dated Jan 3 counted from Jan 20, so Jan 27 paid $0.00, Feb 27 $800.00, and the card
owed $0.00 on Jan 3 through Jan 19 (measured on this tree before the fix).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import cash_ledger, reconcile_service, transfer_service
from app.services.balance_at import BalanceContext, balance_at, cash_balance_at
from app.services.pay_calendar import calendar_for
from app.services.settle_day import recorded_settle_day
from app.services.transfer_legs import transfer_sides
from tests._test_helpers import (
    add_txn,
    append_balance_assertion,
    create_account_via_service,
    make_expense_template,
    generate_row_of,
    outstanding_set_of,
)
from tests.test_services.test_card_bill_counts_from_its_date import _envelope, _swipe
from tests.test_services.test_card_payment_follows_what_is_owed import (
    _payoff,
    _scheduled,
    _true_up,
)
from tests.test_services.test_card_payment_price import (
    _card,
    _one_off,
    _price,
    _row,
    _spend,
)

_ZERO = Decimal("0.00")
_OWED = Decimal("-800.00")
_JAN_1 = date(2026, 1, 1)
_JAN_2 = date(2026, 1, 2)
_JAN_3 = date(2026, 1, 3)
_JAN_4 = date(2026, 1, 4)
_JAN_5 = date(2026, 1, 5)
_JAN_7 = date(2026, 1, 7)
_JAN_10 = date(2026, 1, 10)
_JAN_14 = date(2026, 1, 14)
_JAN_15 = date(2026, 1, 15)
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_19 = date(2026, 1, 19)
_JAN_20 = date(2026, 1, 20)
_JAN_21 = date(2026, 1, 21)
_JAN_27 = date(2026, 1, 27)
_JAN_29 = date(2026, 1, 29)
_JAN_30 = date(2026, 1, 30)
_FEB_1 = date(2026, 2, 1)
_FEB_2 = date(2026, 2, 2)
_FEB_27 = date(2026, 2, 27)


def _statement(seed_user, account):
    """The statement of *account*'s latest true-up, as the panel's route builds it."""
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account.id,
        cash_ledger.governing_anchor(account.id),
    )


def _tick(statement, *, rows=(), purchases=(), transfers=(), typed=None):
    """Tick rows, purchases and transfers on *statement*'s panel and save.

    *typed* is ``{transfer id: figure}`` for a transfer's amount box typed over.

    Returns:
        How many of the ticks landed.
    """
    landed = reconcile_service.record_reconciliation(
        reconcile_service.ReconcileSubmission(
            statement=statement, entry_ids=set(purchases),
            transaction_ids=set(rows), corrections={},
            transfer_ids=set(transfers), transfer_corrections=dict(typed or {}),
        ),
    )
    db.session.commit()
    return landed


def _card_at(seed_user, card, day, as_of=_JAN_21):
    """What the card holds at the end of *day*, read *as_of*: negative when owed."""
    ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)
    return cash_balance_at(card, ctx, day)


def _the_row(card):
    """The one row on *card*, re-read."""
    db.session.expire_all()
    return db.session.query(Transaction).filter_by(account_id=card.id).one()


def _the_swipe(card):
    """The one purchase on *card*, re-read."""
    db.session.expire_all()
    return db.session.query(TransactionEntry).filter_by(account_id=card.id).one()


def _offered_rows(statement):
    """``{row id: the day it is offered by}`` on *statement*'s panel."""
    return {
        group.settle.key: group.settle.attributed_on
        for group in outstanding_set_of(statement).groups
        if group.settle is not None
    }


def _bill_ticked(seed_user, seed_periods, card):
    """$800 on *card* dated Jan 3, $800 trued up Jan 20, ticked on that statement."""
    _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
    _true_up(card, "800.00", _JAN_20)
    row = _the_row(card)
    assert _tick(_statement(seed_user, card), rows={row.id}) == 1
    return _the_row(card)


class TestABillOnTheCard:
    """Ticked, $800 dated Jan 3 stays on the Jan 5 statement."""

    def test_the_statement_it_is_on_still_pays_it(self, seed_user, seed_periods):
        """Jan 27 $800.00 and Feb 27 $0.00, ticked as unticked.

        The Jan 5 statement is valued at the end of Jan 4: 0 - 800 = $800.00 owed,
        due Jan 27.  The control: ticked on the statement's day, Jan 27 $0.00 and
        Feb 27 $800.00.
        """
        card = _card(seed_user)
        template = _scheduled(seed_user, card)
        _bill_ticked(seed_user, seed_periods, card)

        assert _price(template, _JAN_27, _JAN_21) == Decimal("800.00")
        assert _price(template, _FEB_27, _JAN_21) == _ZERO

    def test_the_card_owes_it_from_its_date(self, seed_user, seed_periods):
        """The card: $0.00 on Jan 2, -$800.00 on Jan 3, Jan 19 and Jan 20.

        The control: $0.00 on Jan 3 through Jan 19, -$800.00 from Jan 20.
        """
        card = _card(seed_user)
        _bill_ticked(seed_user, seed_periods, card)

        assert _card_at(seed_user, card, _JAN_2) == _ZERO
        assert _card_at(seed_user, card, _JAN_3) == _OWED
        assert _card_at(seed_user, card, _JAN_19) == _OWED
        assert _card_at(seed_user, card, _JAN_20) == _OWED

    def test_the_next_payday_still_pays_it(self, seed_user, seed_periods):
        """The payoff row: Jan 16 $800.00, Jan 30 $0.00, ticked as unticked.

        Owed at the end of Jan 15: $800.00, inside the $800 owed on Jan 20.  The
        control: Jan 16 $0.00 and Jan 30 $800.00.
        """
        card = _card(seed_user)
        template = _payoff(seed_user, card)
        _bill_ticked(seed_user, seed_periods, card)

        assert _price(template, _JAN_16, _JAN_21) == Decimal("800.00")
        assert _price(template, _JAN_30, _JAN_21) == _ZERO

    def test_it_is_marked_on_its_date_and_names_the_statement(
        self, seed_user, seed_periods,
    ):
        """Paid on Jan 3, the owner's date, linked to the Jan 20 true-up; its payment too.

        The control: Jan 20 on the asserted basis.
        """
        card = _card(seed_user)
        row = _bill_ticked(seed_user, seed_periods, card)
        anchor_id = cash_ledger.governing_anchor(card.id).anchor_id

        assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
        assert row.settled_on == _JAN_3
        assert recorded_settle_day(row).basis is SettledDayBasisEnum.ENTERED
        assert row.reconciled_by_id == anchor_id
        payment = row.covering_movements[0]
        assert (payment.account_id, payment.settled_on, payment.amount) == (
            card.id, _JAN_3, Decimal("800.00"),
        )
        assert payment.reconciled_by_id == anchor_id

    def test_it_is_offered_by_its_own_date(self, seed_user, seed_periods):
        """Dated Jan 14 with the row in Jan 16's paycheck: offered by Jan 14, marked Jan 14.

        Ruling plan#2910: the card counts it from Jan 14.  The control: offered by
        Jan 16, its paycheck's first day, and marked Jan 20.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[1], "800.00", _JAN_14)
        _true_up(card, "800.00", _JAN_20)
        statement = _statement(seed_user, card)
        row = _the_row(card)

        assert _offered_rows(statement) == {row.id: _JAN_14}
        assert _tick(statement, rows={row.id}) == 1
        assert _the_row(card).settled_on == _JAN_14

    def test_one_dated_after_the_statement_is_not_offered(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 20 with the row in Jan 2's paycheck, trued up Jan 18: not offered.

        The card counts it from Jan 20, after the Jan 18 true-up, so that statement
        cannot show it.  The control: offered by Jan 15, its paycheck's last day, and
        a tick marked it Jan 18, two days before it was bought.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_20)
        _true_up(card, "0.00", _JAN_18)
        statement = _statement(seed_user, card)
        row = _the_row(card)

        assert _offered_rows(statement) == {}
        assert _tick(statement, rows={row.id}) == 0
        assert _the_row(card).settled_on is None

    def test_an_envelope_keeps_the_statements_day(self, seed_user, seed_periods):
        """An $800 envelope on the card holding nothing, ticked: marked Jan 20, reconciled.

        Ruling R-G holds an envelope's unspent budget forward (plan#2928), so the
        card counts it from no day of its own and the statement's day stands.
        """
        card = _card(seed_user)
        _envelope(seed_user, seed_periods[0], "800.00", on_card=card)
        _true_up(card, "800.00", _JAN_20)
        row = _the_row(card)

        assert _tick(_statement(seed_user, card), rows={row.id}) == 1
        row = _the_row(card)
        assert row.settled_on == _JAN_20
        assert recorded_settle_day(row).basis is SettledDayBasisEnum.ASSERTED

    def test_a_bill_inside_the_opening_keeps_the_statements_day(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 1, the day the card's books open: marked Jan 20, not refused.

        The opening balance already holds it, so the card counts it from no day of
        its own; marked on its date it would be refused as inside the opening.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_1)
        _true_up(card, "800.00", _JAN_20)
        row = _the_row(card)

        assert _tick(_statement(seed_user, card), rows={row.id}) == 1
        row = _the_row(card)
        assert row.settled_on == _JAN_20
        assert recorded_settle_day(row).basis is SettledDayBasisEnum.ASSERTED


def _sides(transfer):
    """``{account id: (day, basis, statement id)}`` for each side of *transfer*, re-read."""
    db.session.expire_all()
    xfer = db.session.get(Transfer, transfer.id)
    return {
        side.account_id: (
            side.record.settled_on,
            recorded_settle_day(side.record).basis,
            side.record.reconciled_by_id,
        )
        for side in transfer_sides(xfer)
    }


def _checking_at(seed_user, day, as_of=_JAN_21):
    """What checking holds at the end of *day*, read *as_of*."""
    ctx = BalanceContext.build(seed_user["user"].id, as_of=as_of)
    return balance_at(seed_user["account"], ctx, day)


def _payment(seed_user, seed_periods, card):
    """$800 on *card* dated Jan 3, $200 sent to it dated Jan 5 and unmarked, $600 owed Jan 20.

    Returns:
        The $200 transfer, still Projected.
    """
    _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
    transfer = _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_5)
    _true_up(card, "600.00", _JAN_20)
    return transfer


class TestAPaymentIntoTheCard:
    """Ticked, a payment into the card keeps its own date on both accounts (plan#3046).

    The ruling's example: $800 on the card Jan 3, $200 sent to it dated Jan 5 and not
    marked, the card trued up to $600 owed on Jan 20 and the $200 ticked there.  The
    card counts the $200 from Jan 5 before the tick, because the Jan 20 true-up holds
    it (ruling plan#2963).  **The control** for each case is the statement's day, as
    the tick recorded it: Jan 20 on both accounts (measured on this tree before the
    fix).
    """

    def test_it_is_dated_its_own_date_on_both_accounts(self, seed_user, seed_periods):
        """The card's side Jan 5, the owner's date, linked to the Jan 20 true-up;
        checking's side borrows Jan 5 and names no statement.

        The control: the card's side Jan 20 asserted, checking's Jan 20 borrowed.
        """
        card = _card(seed_user)
        transfer = _payment(seed_user, seed_periods, card)
        anchor_id = cash_ledger.governing_anchor(card.id).anchor_id

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        assert _sides(transfer) == {
            card.id: (_JAN_5, SettledDayBasisEnum.ENTERED, anchor_id),
            seed_user["account"].id: (_JAN_5, SettledDayBasisEnum.BORROWED, None),
        }

    def test_the_card_owes_what_it_owed_before_the_tick(self, seed_user, seed_periods):
        """The card: -$800.00 on Jan 4, -$600.00 on Jan 5, Jan 19 and Jan 20, ticked as unticked.

        0 - 800 = -800 from Jan 3; + 200 = -600 from Jan 5; the Jan 20 true-up states
        -600, so it corrects nothing.  The control: -$800.00 through Jan 19, the $200
        counted from Jan 20, so the card owed $200 more Jan 5-19.
        """
        card = _card(seed_user)
        transfer = _payment(seed_user, seed_periods, card)
        days = (_JAN_4, _JAN_5, _JAN_19, _JAN_20)
        expected = [
            Decimal("-800.00"), Decimal("-600.00"), Decimal("-600.00"),
            Decimal("-600.00"),
        ]
        assert [_card_at(seed_user, card, day) for day in days] == expected

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        assert [_card_at(seed_user, card, day) for day in days] == expected

    def test_checking_pays_it_on_its_date(self, seed_user, seed_periods):
        """Checking: $1,000.00 on Jan 4, $800.00 on Jan 5, Jan 19 and Jan 20.

        1,000 - 200 = 800 from Jan 5.  Before the tick checking lands its leg
        tomorrow (ruling R-G): $1,000.00 through Jan 21.  The control: $1,000.00
        through Jan 19, $800.00 from Jan 20.
        """
        card = _card(seed_user)
        transfer = _payment(seed_user, seed_periods, card)
        assert _checking_at(seed_user, _JAN_21) == Decimal("1000.00")

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        assert [
            _checking_at(seed_user, day) for day in (_JAN_4, _JAN_5, _JAN_19, _JAN_20)
        ] == [
            Decimal("1000.00"), Decimal("800.00"), Decimal("800.00"),
            Decimal("800.00"),
        ]

    def test_a_checking_true_up_after_it_left_holds_it(self, seed_user, seed_periods):
        """Checking trued up to $800 on Jan 10, the $200 gone Jan 5: $800.00 on Jan 21.

        The ruling's first figure.  Jan 5 is inside the Jan 10 true-up, which states
        $800, so the $200 corrects nothing.  The control: $600.00, the $200 counted
        again from Jan 20, after the true-up that already held it.
        """
        card = _card(seed_user)
        transfer = _payment(seed_user, seed_periods, card)
        _checking_true_up(seed_user, "800.00", _JAN_10)

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        assert _checking_at(seed_user, _JAN_21) == Decimal("800.00")

    def test_a_checking_true_up_before_it_left_reads_high(self, seed_user, seed_periods):
        """Checking trued up to $1,000 on Jan 10, the $200 not yet gone: $1,000.00 on Jan 21.

        The ruling's cost, $200 high until the payment's date is fixed: Jan 5 is
        inside the Jan 10 true-up, which states $1,000.  The control: $800.00, the
        $200 counted from Jan 20.
        """
        card = _card(seed_user)
        transfer = _payment(seed_user, seed_periods, card)
        _checking_true_up(seed_user, "1000.00", _JAN_10)

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        assert _checking_at(seed_user, _JAN_21) == Decimal("1000.00")

    def test_it_is_offered_by_its_own_date(self, seed_user, seed_periods):
        """Dated Jan 14 with the transfer in Jan 16's paycheck: offered by Jan 14, dated Jan 14.

        The control: offered by Jan 16, its paycheck's first day.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        transfer = _one_off(seed_user, card, seed_periods[1], "200.00", _JAN_14)
        _true_up(card, "600.00", _JAN_20)
        statement = _statement(seed_user, card)
        row = _the_row(card)

        assert _offered_rows(statement) == {
            row.id: _JAN_3, (transfer.id, card.id): _JAN_14,
        }
        assert _tick(statement, transfers={transfer.id}) == 1
        assert _sides(transfer)[card.id][0] == _JAN_14

    def test_one_dated_on_the_true_ups_day_takes_its_own(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 20, the true-up's own day: dated Jan 20 entered, the owner's date.

        A true-up closes its own day, so it holds money dated that day.  The
        control: Jan 20 asserted, the statement's day.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        transfer = _one_off(seed_user, card, seed_periods[1], "200.00", _JAN_20)
        _true_up(card, "600.00", _JAN_20)

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        assert _sides(transfer)[card.id][:2] == (_JAN_20, SettledDayBasisEnum.ENTERED)

    def test_one_dated_after_the_statement_is_not_offered(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 20 with the transfer in Jan 2's paycheck, trued up Jan 18: not offered.

        The Jan 18 statement cannot show money dated Jan 20, as it cannot show a
        bill dated so.  The control: offered by Jan 15, its paycheck's last day,
        and a tick dated it Jan 18 on both accounts, two days before its date,
        absorbing the $200 into the Jan 18 true-up.
        """
        card = _card(seed_user)
        transfer = _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_20)
        _true_up(card, "0.00", _JAN_18)
        statement = _statement(seed_user, card)

        assert _offered_rows(statement) == {}
        assert _tick(statement, transfers={transfer.id}) == 0
        db.session.expire_all()
        assert db.session.get(Transfer, transfer.id).status_id == (
            ref_cache.status_id(StatusEnum.PROJECTED)
        )


def _worked_out_offer(statement):
    """The one worked-out payment *statement*'s panel offers: ``(transfer id, (day, figure))``."""
    offers = [
        (group.settle.key, (group.settle.attributed_on, group.settle.amount))
        for group in outstanding_set_of(statement).groups
        if isinstance(group.settle.key, tuple)
    ]
    assert len(offers) == 1
    ((transfer_id, _), offer), = offers
    return transfer_id, offer


class TestAWorkedOutPaymentIntoTheCard:
    """Ticked, a worked-out payment keeps the day its fold dates it (plan#3046).

    $800 on the card Jan 3; the bank takes $500 of the payment, the card is trued
    up to $300 owed, and the owner ticks the payment with $500 typed.  **The
    control** is the statement's day on both accounts (measured on this tree
    before the fix).
    """

    def test_the_payoff_row_keeps_its_date(self, seed_user, seed_periods):
        """The Jan 16 payoff row, $300 trued up Jan 20, ticked at $500: dated Jan 16.

        Offered at min(800 owed at the end of Jan 15, 300 left now) = $300.00 by
        Jan 16.  Ticked at $500: the card owes 800 - 500 = $300.00 from Jan 16,
        which the Jan 20 true-up states, and checking is 1,000 - 500 = $500.00 from
        Jan 16.  The control: dated Jan 20, the card owing $800.00 Jan 16-19, interest
        on money paid, and checking $1,000.00 through Jan 19.
        """
        card = _card(seed_user)
        _payoff(seed_user, card)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        _true_up(card, "300.00", _JAN_20)
        statement = _statement(seed_user, card)
        transfer_id, offer = _worked_out_offer(statement)
        assert offer == (_JAN_16, Decimal("300.00"))

        assert _tick(
            statement, transfers={transfer_id},
            typed={transfer_id: Decimal("500.00")},
        ) == 1
        sides = _sides(db.session.get(Transfer, transfer_id))
        days = (_JAN_15, _JAN_16, _JAN_19, _JAN_20)
        assert (
            {side: sides[side][:2] for side in sides},
            [_card_at(seed_user, card, day) for day in days],
            [_checking_at(seed_user, day) for day in days],
        ) == (
            {
                card.id: (_JAN_16, SettledDayBasisEnum.ENTERED),
                seed_user["account"].id: (_JAN_16, SettledDayBasisEnum.BORROWED),
            },
            [
                Decimal("-800.00"), Decimal("-300.00"), Decimal("-300.00"),
                Decimal("-300.00"),
            ],
            [
                Decimal("1000.00"), Decimal("500.00"), Decimal("500.00"),
                Decimal("500.00"),
            ],
        )

    def test_one_moved_to_a_later_paycheck_takes_that_paychecks_day(
        self, seed_user, seed_periods,
    ):
        """The Jan 27 scheduled row moved into Jan 30's paycheck, ticked Feb 1 at $500: Jan 30.

        A moved worked-out row keeps its date (ruling plan#2991), and its fold
        dates and prices it by Jan 30, its paycheck's first day.  Offered at
        min(800 on the Jan 5 statement, 300 left now) = $300.00 by Jan 30.  Ticked at
        $500, read Feb 2: the card owes $800.00 on Jan 29 and 800 - 500 = $300.00 from
        Jan 30; checking is $1,000.00 on Jan 29 and $500.00 from Jan 30.  The
        controls: offered by its own date, Jan 27, a day the fold never uses; dated
        the statement's day, Feb 1, the card owing $800.00 on Jan 30.
        """
        card = _card(seed_user)
        template = _scheduled(seed_user, card)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        row = _row(template, _JAN_27)
        transfer_service.update_transfer(
            row.id, row.user_id, pay_period_id=seed_periods[2].id,
        )
        db.session.commit()
        _true_up(card, "300.00", _FEB_1)
        statement = _statement(seed_user, card)
        transfer_id, offer = _worked_out_offer(statement)
        assert (transfer_id, offer) == (row.id, (_JAN_30, Decimal("300.00")))

        assert _tick(
            statement, transfers={transfer_id},
            typed={transfer_id: Decimal("500.00")},
        ) == 1
        sides = _sides(row)
        days = (_JAN_29, _JAN_30, _FEB_1)
        assert (
            {side: sides[side][:2] for side in sides},
            [_card_at(seed_user, card, day, _FEB_2) for day in days],
            [_checking_at(seed_user, day, _FEB_2) for day in days],
        ) == (
            {
                card.id: (_JAN_30, SettledDayBasisEnum.ENTERED),
                seed_user["account"].id: (_JAN_30, SettledDayBasisEnum.BORROWED),
            },
            [Decimal("-800.00"), Decimal("-300.00"), Decimal("-300.00")],
            [Decimal("1000.00"), Decimal("500.00"), Decimal("500.00")],
        )


class TestAPaymentOnTheCardsBooks:
    """Where an account's books already hold a payment's date."""

    def test_one_inside_the_cards_opening_keeps_the_statements_day(
        self, seed_user, seed_periods,
    ):
        """Dated Jan 1, the day the card's books open: dated Jan 20 on both, not refused.

        The card's opening already holds it, so the card counts it from no day of
        its own; dated Jan 1 it would be refused as inside the card's opening.
        """
        card = _card(seed_user)
        transfer = _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_1)
        _true_up(card, "0.00", _JAN_20)

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        sides = _sides(transfer)
        assert {side: sides[side][:2] for side in sides} == {
            card.id: (_JAN_20, SettledDayBasisEnum.ASSERTED),
            seed_user["account"].id: (_JAN_20, SettledDayBasisEnum.BORROWED),
        }

    def test_one_the_paying_accounts_books_hold_is_refused(
        self, seed_user, seed_periods,
    ):
        """From an account whose books open Jan 10, dated Jan 5: the save is refused.

        Checking's side would borrow Jan 5, inside that account's opening, so the
        settle refuses it, naming the day, as it refuses Jan 5 stated from any door;
        nothing is written.  The control: ticked, dated Jan 20 on both.  That such a
        transfer can be made at all is finding plan#3050.
        """
        card = _card(seed_user)
        late = create_account_via_service(
            seed_user, db.session, "Checking", "Second Checking",
            Decimal("1000.00"), _JAN_10,
        )
        db.session.commit()
        transfer = transfer_service.create_transfer(transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=late.id,
            to_account_id=card.id,
            pay_period_id=seed_periods[0].id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("200.00")),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=seed_user["categories"]["Rent"].id,
            name="Extra to the Visa",
            due_date=_JAN_5,
        ))
        db.session.commit()
        _true_up(card, "0.00", _JAN_20)

        with pytest.raises(ValidationError, match="Money cannot have moved on 2026-01-05"):
            _tick(_statement(seed_user, card), transfers={transfer.id})
        db.session.rollback()
        db.session.expire_all()
        assert db.session.get(Transfer, transfer.id).status_id == (
            ref_cache.status_id(StatusEnum.PROJECTED)
        )


def _swipe_ticked(seed_user, seed_periods, card):
    """$800 swiped on *card* Jan 3 in a checking envelope, ticked on the Jan 20 statement."""
    row = _envelope(seed_user, seed_periods[0], "800.00")
    _swipe(seed_user, row, card, "800.00", _JAN_3)
    _true_up(card, "800.00", _JAN_20)
    swipe = _the_swipe(card)
    assert _tick(_statement(seed_user, card), purchases={swipe.id}) == 1
    return _the_swipe(card)


class TestASwipeOnTheCard:
    """Ticked, $800 swiped Jan 3 stays on the Jan 5 statement."""

    def test_the_statement_it_is_on_still_pays_it(self, seed_user, seed_periods):
        """Jan 27 $800.00 and Feb 27 $0.00; the card owes $800.00 from Jan 3.

        The control: Jan 27 $0.00, Feb 27 $800.00, the card $0.00 through Jan 19.
        """
        card = _card(seed_user)
        template = _scheduled(seed_user, card)
        _swipe_ticked(seed_user, seed_periods, card)

        assert _price(template, _JAN_27, _JAN_21) == Decimal("800.00")
        assert _price(template, _FEB_27, _JAN_21) == _ZERO
        assert _card_at(seed_user, card, _JAN_2) == _ZERO
        assert _card_at(seed_user, card, _JAN_3) == _OWED
        assert _card_at(seed_user, card, _JAN_19) == _OWED

    def test_it_is_dated_the_day_it_was_made(self, seed_user, seed_periods):
        """Moved on Jan 3, the owner's date, linked to the Jan 20 true-up.

        The control: Jan 20 on the asserted basis.
        """
        card = _card(seed_user)
        swipe = _swipe_ticked(seed_user, seed_periods, card)

        assert swipe.settled_on == _JAN_3
        assert recorded_settle_day(swipe).basis is SettledDayBasisEnum.ENTERED
        assert swipe.reconciled_by_id == cash_ledger.governing_anchor(
            card.id,
        ).anchor_id

    def test_each_swipe_in_one_save_takes_its_own_day(
        self, seed_user, seed_periods,
    ):
        """Jan 1, Jan 3 and Jan 7 ticked together: Jan 20, Jan 3 and Jan 7.

        One save dates each by its own day (the writer's ``CASE`` over the ids);
        Jan 1 is the day the card's books open, so it takes the statement's.  The
        review's mutation, every swipe taking the first one's day, read Jan 1 or
        Jan 3 for all three.
        """
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "300.00")
        for on in (_JAN_1, _JAN_3, _JAN_7):
            _swipe(seed_user, row, card, "100.00", on, name=f"Kroger {on.day}")
        _true_up(card, "300.00", _JAN_20)
        swipes = db.session.query(TransactionEntry).filter_by(account_id=card.id)
        ids = {swipe.description: swipe.id for swipe in swipes}

        assert _tick(_statement(seed_user, card), purchases=set(ids.values())) == 3
        db.session.expire_all()
        stamped = {
            name: (
                db.session.get(TransactionEntry, key).settled_on,
                recorded_settle_day(db.session.get(TransactionEntry, key)).basis,
            )
            for name, key in ids.items()
        }
        assert stamped == {
            "Kroger 1": (_JAN_20, SettledDayBasisEnum.ASSERTED),
            "Kroger 3": (_JAN_3, SettledDayBasisEnum.ENTERED),
            "Kroger 7": (_JAN_7, SettledDayBasisEnum.ENTERED),
        }

    def test_one_inside_the_opening_keeps_the_statements_day(
        self, seed_user, seed_periods,
    ):
        """Swiped Jan 1, the day the card's books open: dated Jan 20, not refused."""
        card = _card(seed_user)
        row = _envelope(seed_user, seed_periods[0], "800.00")
        _swipe(seed_user, row, card, "800.00", _JAN_1)
        _true_up(card, "800.00", _JAN_20)
        swipe = _the_swipe(card)

        assert _tick(_statement(seed_user, card), purchases={swipe.id}) == 1
        swipe = _the_swipe(card)
        assert swipe.settled_on == _JAN_20
        assert recorded_settle_day(swipe).basis is SettledDayBasisEnum.ASSERTED


def _checking_true_up(seed_user, balance, on):
    """Tell the app checking really holds *balance* at the end of *on*."""
    append_balance_assertion(
        db.session, seed_user["account"], Decimal(balance),
        datetime(on.year, on.month, on.day, 18, tzinfo=timezone.utc),
    )
    db.session.commit()


class TestCheckingIsUnchanged:
    """Checking's panel records the statement's day, as it did."""

    def test_a_bill_is_marked_on_the_statements_day(self, seed_user, seed_periods):
        """$800 on checking dated Jan 3, $200 stated Jan 20, ticked: marked Jan 20.

        Checking at the end of Jan 20: the stated $200.00, the bill inside it.
        """
        row = add_txn(
            db.session, seed_user, seed_periods[0], "Rent", Decimal("800.00"),
            due_date=_JAN_3,
        )
        db.session.commit()
        _checking_true_up(seed_user, "200.00", _JAN_20)

        assert _tick(_statement(seed_user, seed_user["account"]), rows={row.id}) == 1
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        assert row.settled_on == _JAN_20
        assert recorded_settle_day(row).basis is SettledDayBasisEnum.ASSERTED
        ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_21)
        assert balance_at(seed_user["account"], ctx, _JAN_20) == Decimal("200.00")

    def test_a_purchase_is_dated_the_statements_day(self, seed_user, seed_periods):
        """$60 swiped on checking Jan 3, ticked on the Jan 20 statement: dated Jan 20."""
        template = make_expense_template(
            db.session, seed_user, amount="100.00", name="Groceries",
            category_key="Groceries", is_envelope=True,
        )
        row = generate_row_of(template, seed_periods[0])
        db.session.commit()
        _swipe(seed_user, row, seed_user["account"], "60.00", _JAN_3)
        _checking_true_up(seed_user, "940.00", _JAN_20)
        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=row.id,
        ).one()

        assert _tick(
            _statement(seed_user, seed_user["account"]), purchases={entry.id},
        ) == 1
        db.session.expire_all()
        entry = db.session.get(TransactionEntry, entry.id)
        assert entry.settled_on == _JAN_20
        assert recorded_settle_day(entry).basis is SettledDayBasisEnum.ASSERTED

    def test_a_payment_into_the_card_is_dated_the_statements_day(
        self, seed_user, seed_periods,
    ):
        """$200 to the card dated Jan 5, $800 stated on checking Jan 20, ticked there: Jan 20.

        Checking's side takes the statement's day, asserted, and names checking's
        true-up, as before plan#3046, which rules the card's panel.  The card's side
        borrows it, which moves the card off its date: finding plan#3053.
        """
        card = _card(seed_user)
        transfer = _one_off(seed_user, card, seed_periods[0], "200.00", _JAN_5)
        _checking_true_up(seed_user, "800.00", _JAN_20)
        checking = seed_user["account"]

        assert _tick(_statement(seed_user, checking), transfers={transfer.id}) == 1
        assert _sides(transfer)[checking.id] == (
            _JAN_20, SettledDayBasisEnum.ASSERTED,
            cash_ledger.governing_anchor(checking.id).anchor_id,
        )
