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

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import cash_ledger, reconcile_service
from app.services.balance_at import BalanceContext, balance_at, cash_balance_at
from app.services.pay_calendar import calendar_for
from app.services.settle_day import recorded_settle_day
from tests._test_helpers import (
    add_txn,
    append_balance_assertion,
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
    _spend,
)

_ZERO = Decimal("0.00")
_OWED = Decimal("-800.00")
_JAN_1 = date(2026, 1, 1)
_JAN_2 = date(2026, 1, 2)
_JAN_3 = date(2026, 1, 3)
_JAN_7 = date(2026, 1, 7)
_JAN_14 = date(2026, 1, 14)
_JAN_16 = date(2026, 1, 16)
_JAN_18 = date(2026, 1, 18)
_JAN_19 = date(2026, 1, 19)
_JAN_20 = date(2026, 1, 20)
_JAN_21 = date(2026, 1, 21)
_JAN_27 = date(2026, 1, 27)
_JAN_30 = date(2026, 1, 30)
_FEB_27 = date(2026, 2, 27)


def _statement(seed_user, account):
    """The statement of *account*'s latest true-up, as the panel's route builds it."""
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account.id,
        cash_ledger.governing_anchor(account.id),
    )


def _tick(statement, *, rows=(), purchases=(), transfers=()):
    """Tick rows, purchases and transfers on *statement*'s panel and save.

    Returns:
        How many of the ticks landed.
    """
    landed = reconcile_service.record_reconciliation(
        reconcile_service.ReconcileSubmission(
            statement=statement, entry_ids=set(purchases),
            transaction_ids=set(rows), corrections={},
            transfer_ids=set(transfers), transfer_corrections={},
        ),
    )
    db.session.commit()
    return landed


def _card_at(seed_user, card, day):
    """What the card holds at the end of *day*, read Jan 21: negative when owed."""
    ctx = BalanceContext.build(seed_user["user"].id, as_of=_JAN_21)
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


class TestAPaymentIntoTheCard:
    """A transfer's leg on the card is the transfer arm's, which this task leaves."""

    def test_its_tick_still_lands(self, seed_user, seed_periods):
        """$200 sent to the card dated Jan 5, $600 trued up Jan 20, ticked: Paid.

        The statement dates no transfer by its own day (``Statement.own_day``), so
        the tick settles it through the transfer arm and lands.  Which day it
        should take is plan#3041's, so this asserts none.
        """
        card = _card(seed_user)
        _spend(seed_user, card, seed_periods[0], "800.00", _JAN_3)
        transfer = _one_off(seed_user, card, seed_periods[0], "200.00", date(2026, 1, 5))
        _true_up(card, "600.00", _JAN_20)

        assert _tick(_statement(seed_user, card), transfers={transfer.id}) == 1
        db.session.expire_all()
        assert db.session.get(type(transfer), transfer.id).status_id == (
            ref_cache.status_id(StatusEnum.DONE)
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
