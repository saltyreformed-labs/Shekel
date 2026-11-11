"""
Shekel Budget App -- Paid on a bill that lives on the card dates it like Credit (plan#3234)

Ruling **plan#3225**: Paid means the bill was paid and money moved, from the
account in its "Paid from" box, and Credit means it went on a card.  On a bill
that lives on the card, Paid with its card picked charges the card, drawn
Credit, and records the day ``transaction_service.credit_day`` gives -- the
bill's date, or today while that date is still ahead -- not today, because by
ruling plan#2910 the day picks the statement, the due date and the interest.
Paid with checking picked pays it from checking, dated as Paid dates any
checking row: today.  ``transaction_service._card_day.settle_day_on_a_card``
is the one rule, asked by the settle verb every Paid door reaches.

**The one example**: $800.00 of groceries dated Jan 3 in the Jan 2 paycheck,
living on a Visa that closes on the 5th and falls due on the 27th, its books
opening Jan 1 with a $0.00 true-up on Jan 2.  **Paydays are Jan 2, Jan 16, Jan 30, Feb 13 and Feb 27.**  The
suite runs after those days, so a Paid pressed now is pressed after the Jan 5
close, as the ruling's "pressed Paid on Jan 6" is.  Checking opens at
$1,000.00.  Each figure is computed by hand in the test that asserts it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.cash_ledger import account_opening_fact
from app.utils.dates import display_today
from tests._cards import add_card, move_definition_to_checking
from tests._test_helpers import add_txn, generate_row_of, make_expense_template
from tests.test_services.test_card_payment_follows_what_is_owed import _scheduled
from tests.test_services.test_card_payment_price import _card, _price
from tests.test_services.test_credit_puts_the_row_on_the_card import _groceries

_EIGHT_HUNDRED = Decimal("800.00")
_TWENTY_FIVE = Decimal("25.00")
_JAN_3 = date(2026, 1, 3)
_JAN_6 = date(2026, 1, 6)
_JAN_16 = date(2026, 1, 16)
_JAN_27 = date(2026, 1, 27)
_FEB_27 = date(2026, 2, 27)


def _status(member):
    """The ``ref.statuses.id`` of *member*."""
    return ref_cache.status_id(member)


def _refund(seed_user, period, account):
    """A $25.00 refund in *period* on *account*, dated Jan 3, committed."""
    row = add_txn(
        db.session, seed_user, period, "Refund", _TWENTY_FIVE,
        due_date=_JAN_3, account=account, is_income=True,
    )
    db.session.commit()
    return row


def _paid(row, **kwargs):
    """Settle *row* through the verb every Paid door reaches, commit, and read it fresh."""
    transaction_service.settle_transaction(row, **kwargs)
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transaction, row.id)


def _payment(row):
    """The row's one covering movement, as ``(account id, amount, settled on)``."""
    movement = status_seam.covering_movement_of(row)
    return (movement.account_id, movement.amount, movement.settled_on)


class TestPaidWithItsCardPicked:
    """The payment lands on the card, so the bill keeps its own date."""

    def test_paid_records_the_bills_date_and_is_drawn_credit(self, seed_user, seed_periods):
        """$800 dated Jan 3 on the Visa, Paid with nothing picked: dated Jan 3, on the Visa, CC.

        "Paid from" is preselected with the bill's own account, the Visa
        (``status_seam.tender_account_id_of``), so a one-click Paid picks it.
        """
        card = _card(seed_user)
        row = _paid(_groceries(seed_user, seed_periods[0], account=card))

        assert row.status_id == _status(StatusEnum.DONE)
        assert row.settled_on == _JAN_3
        assert row.settled_day_basis_id == ref_cache.settled_day_basis_id(
            SettledDayBasisEnum.ENTERED,
        )
        assert _payment(row) == (card.id, _EIGHT_HUNDRED, _JAN_3)
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.CREDIT)

    def test_the_card_named_records_the_same_day(self, seed_user, seed_periods):
        """The Visa picked by name, as the popover's Paid posts it: the same Jan 3."""
        card = _card(seed_user)
        row = _groceries(seed_user, seed_periods[0], account=card)
        row = _paid(row, tender_account_id=card.id)

        assert _payment(row) == (card.id, _EIGHT_HUNDRED, _JAN_3)

    def test_it_stays_on_the_statement_due_jan_27(self, seed_user, seed_periods):
        """Read Jan 6: the Jan 27 payment is $800.00 before Paid and after it; Feb 27's $0.00.

        The Jan 5 statement is the card's balance at the end of Jan 4, which
        holds the $800 from Jan 3: 0 + 800 = $800.00, due Jan 27.  Nothing
        else is charged, so the Feb 5 statement is 800 - 800 paid Jan 27 =
        $0.00.  Dated today, the $800 would leave the Jan 5 statement for
        today's, and Jan 27 would read $0.00.
        """
        card = _card(seed_user)
        template = _scheduled(seed_user, card)
        row = _groceries(seed_user, seed_periods[0], account=card)
        assert _price(template, _JAN_27, _JAN_6) == _EIGHT_HUNDRED, "precondition: marked or not"

        _paid(row)

        assert _price(template, _JAN_27, _JAN_6) == _EIGHT_HUNDRED
        assert _price(template, _FEB_27, _JAN_6) == Decimal("0.00")

    def test_a_date_that_has_not_come_is_today(self, seed_user, seed_periods):
        """Dated ten days ahead: today, since money cannot move in the future."""
        card = _card(seed_user)
        ahead = display_today() + timedelta(days=10)
        row = _paid(_groceries(seed_user, seed_periods[0], account=card, due_date=ahead))

        assert row.settled_on == display_today()

    def test_an_undated_bill_takes_its_paycheck_s_first_day(self, seed_user, seed_periods):
        """No date, in Jan 16's paycheck: Jan 16, or today if that has not come."""
        card = _card(seed_user)
        row = _paid(_groceries(seed_user, seed_periods[1], account=card, due_date=None))

        assert row.settled_on == min(_JAN_16, display_today())

    def test_a_bill_inside_the_cards_opening_is_refused(self, seed_user, seed_periods):
        """Dated the day the Visa's books open: inside its opening already, so refused.

        Today's date would have charged it a second time: the opening balance
        holds it, and the movement would count it again from today.
        """
        card = _card(seed_user)
        opened_on = account_opening_fact(card.id).opened_on
        row = _groceries(seed_user, seed_periods[0], account=card, due_date=opened_on)

        with pytest.raises(ValidationError, match=f"Money cannot have moved on {opened_on}"):
            transaction_service.settle_transaction(row)
        db.session.rollback()
        db.session.expire_all()
        row = db.session.get(Transaction, row.id)
        assert row.status_id == _status(StatusEnum.PROJECTED)
        assert not row.entries

    def test_a_payment_kept_on_a_card_under_a_row_on_checking(self, seed_user, seed_periods):
        """A Visa bill paid, reopened, moved to Checking: Paid books on the Visa, by its date.

        The payment kept on the Visa is where a one-click Paid books it
        (``status_seam.tender_account_id_of``, finding plan#3105), so it lands
        on a card and takes the row's own date, not today.
        """
        card = add_card(seed_user)
        template = make_expense_template(
            db.session, seed_user, amount="800.00", name="Groceries",
            category_key=next(iter(seed_user["categories"])), account=card,
        )
        row = generate_row_of(template, seed_periods[0])
        db.session.commit()
        row = _paid(row)
        transaction_service.apply_requested_status(row, _status(StatusEnum.PROJECTED))
        db.session.commit()
        move_definition_to_checking(seed_user, row)
        row = db.session.get(Transaction, row.id)
        assert (row.account_id, _payment(row)[0]) == (seed_user["account"].id, card.id), (
            "precondition: the row is on Checking, its payment kept on the Visa"
        )

        row = _paid(row)

        assert _payment(row) == (card.id, _EIGHT_HUNDRED, row.due_date)
        assert row.due_date < display_today(), "precondition: today would differ"


    def test_an_envelope_with_no_purchases_keeps_today(self, seed_user, seed_periods):
        """A $200.00 Gas envelope on the Visa, nothing bought, Paid: today, not its due date.

        Its unspent budget has no date of its own on the card (the fold lands
        it by ruling R-G), and dated by its due date a true-up on or after
        that day would absorb the $200.00 (the review of plan#3234).
        """
        card = _card(seed_user)
        template = make_expense_template(
            db.session, seed_user, amount="200.00", name="Gas",
            category_key=next(iter(seed_user["categories"])), is_envelope=True, account=card,
        )
        row = generate_row_of(template, seed_periods[0])
        db.session.commit()
        assert row.due_date < display_today(), "precondition: its date would differ"

        row = _paid(row)

        assert _payment(row) == (card.id, Decimal("200.00"), display_today())


class TestPaidWithCashPicked:
    """The payment leaves a cash account, dated as Paid dates any checking row."""

    def test_checking_picked_is_today(self, seed_user, seed_periods):
        """The Visa bill, Paid from Checking: today, on Checking, drawn Paid.

        Checking pays it that month, so nothing changes from a checking row.
        """
        card = _card(seed_user)
        checking = seed_user["account"]
        row = _groceries(seed_user, seed_periods[0], account=card)
        row = _paid(row, tender_account_id=checking.id)

        assert _payment(row) == (checking.id, _EIGHT_HUNDRED, display_today())
        assert status_seam.drawn_status_id(row) == _status(StatusEnum.DONE)

    def test_a_bill_on_checking_is_today(self, seed_user, seed_periods):
        """A Groceries row on Checking dated Jan 3, Paid: today, with a Visa in the app."""
        _card(seed_user)
        checking = seed_user["account"]
        row = _paid(_groceries(seed_user, seed_periods[0], account=checking))

        assert _payment(row) == (checking.id, _EIGHT_HUNDRED, display_today())


class TestMoneyComingInOntoTheCard:
    """Received into a card takes the row's own date too (decided under R-BAL207)."""

    def test_a_refund_living_on_the_card_keeps_its_date(self, seed_user, seed_periods):
        """A $25.00 refund on the Visa dated Jan 3, Received: dated Jan 3, on the Visa."""
        card = _card(seed_user)
        row = _paid(_refund(seed_user, seed_periods[0], card))

        assert row.status_id == _status(StatusEnum.RECEIVED)
        assert _payment(row) == (card.id, _TWENTY_FIVE, _JAN_3)

    def test_a_refund_on_checking_received_into_the_card_keeps_its_date(
        self, seed_user, seed_periods,
    ):
        """A $25.00 refund on Checking, Received into the Visa (ruling plan#3310): Jan 3."""
        card = _card(seed_user)
        refund = _refund(seed_user, seed_periods[0], seed_user["account"])
        row = _paid(refund, tender_account_id=card.id)

        assert _payment(row) == (card.id, _TWENTY_FIVE, _JAN_3)
