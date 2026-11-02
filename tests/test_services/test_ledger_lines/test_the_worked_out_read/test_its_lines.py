"""The worked-out read's lines are the rules' own, written out by hand: a bill, an account's
opening and true-up, an archived account, and a loan's anchors and payment split.
"""

from datetime import timedelta
from decimal import Decimal

from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.services.cash_ledger import account_opening_fact
from app.services.ledger_lines import (
    LedgerLine,
    account_counter_key,
    category_key,
    linked_key,
    loan_key,
)
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
)

from ._helpers import (
    _TODAY,
    _kind,
    _lines_of,
    _loan_anchor_lines,
    _read,
    _source,
    _trued_up_loan,
)


class TestTheLinesAreTheRules:
    """Each kind of line, written out by hand."""

    def test_a_paid_bill_is_two_lines_in_its_rows_paycheck_on_its_bank_day(
        self, app, db, seed_user, seed_periods,
    ):
        """$40.00 Groceries, budgeted in January, paid May 15: two lines.

        Checking ``-40.00`` and Groceries (Expense) ``+40.00``, both under the
        ``purchase`` source and the ``expense`` leg kind, filed in the ROW's
        January paycheck and dated the movement's own May 15 -- the paycheck
        is the owner's budgeting choice, never the day's (ruling
        pay_calendar:R-PC53), and the day is when the money moved.
        """
        with app.app_context():
            groceries = seed_user["categories"]["Groceries"]
            txn = create_settled_cash_transaction(
                seed_user, _db.session, seed_periods[0], Decimal("40.00"),
                category=groceries,
            )
            (movement,) = txn.entries
            assert movement.settled_on == _TODAY

            lines = _lines_of(_read(seed_user), movement.id)

            purchase = _source(PostingSourceEnum.PURCHASE)
            expense = _kind(PostingKindEnum.EXPENSE)
            assert lines == [
                LedgerLine(
                    linked_key(seed_user["account"]), seed_periods[0].id,
                    _TODAY, Decimal("-40.00"), purchase, expense, movement.id,
                ),
                LedgerLine(
                    category_key(groceries.id, LedgerAccountClassEnum.EXPENSE),
                    seed_periods[0].id, _TODAY, Decimal("40.00"), purchase,
                    expense, movement.id,
                ),
            ]

    def test_an_opening_and_a_true_up_are_filed_by_their_days(
        self, app, db, seed_user,
    ):
        """$1,000.00 asserted May 12, trued up to $1,150.00 May 13: four lines.

        The opening books ``+1,000.00`` on the account and ``-1,000.00`` on
        its equity counter (source ``account_opening``, kind ``opening``) on
        the day its books opened, the stored opening's own day; the true-up
        ``1,150.00 - 1,000.00 = +150.00`` and ``-150.00`` on Interest Income,
        a Money Market's true-up being interest (ruling R-FO).  The May 12
        assertion books ``1,000.00 - 1,000.00 = 0.00`` and so is no line.
        Each is filed in the paycheck its DAY files in on the owner's
        calendar as it is read (ruling plan#2519) and comes from no movement.
        """
        with app.app_context():
            asserted_on = _TODAY - timedelta(days=3)
            account = create_account_of_type(
                seed_user, _db.session, "Money Market", "Filed Money Market",
                anchor_balance=Decimal("1000.00"), observed_on=asserted_on,
            )
            opened_on = account_opening_fact(account.id).opened_on
            trued_on = asserted_on + timedelta(days=1)
            _db.session.add(AccountAnchorHistory(
                account_id=account.id, anchor_balance=Decimal("1150.00"),
                observed_on=trued_on, recorded_on=trued_on,
            ))
            _db.session.flush()
            calendar = calendar_for(seed_user["user"].id)

            lines = [
                line for line in _read(seed_user)
                if line.key.record_id == account.id
            ]

            opening = (
                _source(PostingSourceEnum.ACCOUNT_OPENING),
                _kind(PostingKindEnum.OPENING),
            )
            trueup = (
                _source(PostingSourceEnum.ACCOUNT_TRUEUP),
                _kind(PostingKindEnum.TRUEUP),
            )
            opened_in = calendar.filing_period(opened_on).period_id
            trued_in = calendar.filing_period(trued_on).period_id
            assert lines == [
                LedgerLine(
                    linked_key(account), opened_in, opened_on,
                    Decimal("1000.00"), *opening, None,
                ),
                LedgerLine(
                    account_counter_key(
                        LedgerAccountKindEnum.ANCHOR_EQUITY, account.id,
                    ),
                    opened_in, opened_on, Decimal("-1000.00"), *opening, None,
                ),
                LedgerLine(
                    linked_key(account), trued_in, trued_on,
                    Decimal("150.00"), *trueup, None,
                ),
                LedgerLine(
                    account_counter_key(
                        LedgerAccountKindEnum.INTEREST_INCOME, account.id,
                    ),
                    trued_in, trued_on, Decimal("-150.00"), *trueup, None,
                ),
            ]

    def test_two_cancelling_same_day_true_ups_are_no_line(
        self, app, db, seed_user,
    ):
        """$1,000.00 -> $1,150.00 -> $1,000.00 on one day: no true-up line.

        The two true-ups book ``+150.00`` and ``-150.00`` and share their
        source kind, paycheck and day, so their merged legs net to ``0.00``
        on both the account and its Interest Income counter -- a key the
        writer resolves and posts nothing under, and so no line here.
        """
        with app.app_context():
            opened_on = _TODAY - timedelta(days=3)
            account = create_account_of_type(
                seed_user, _db.session, "Money Market", "Cancel Money Market",
                anchor_balance=Decimal("1000.00"), observed_on=opened_on,
            )
            trued_on = opened_on + timedelta(days=1)
            for balance in ("1150.00", "1000.00"):
                _db.session.add(AccountAnchorHistory(
                    account_id=account.id, anchor_balance=Decimal(balance),
                    observed_on=trued_on, recorded_on=trued_on,
                ))
                _db.session.flush()

            sources = {
                line.source_kind_id for line in _read(seed_user)
                if line.key.record_id == account.id
            }

            assert sources == {_source(PostingSourceEnum.ACCOUNT_OPENING)}

    def test_an_archived_accounts_corrections_are_read(
        self, app, db, seed_user,
    ):
        """An archived Savings keeps its $500.00 opening's two lines.

        Archiving stops new activity and erases no posted fact, so the
        writers' account set keeps archived accounts and so does this read.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Archived Savings",
                anchor_balance=Decimal("500.00"),
                observed_on=_TODAY - timedelta(days=3),
            )
            account.is_active = False
            _db.session.flush()

            amounts = sorted(
                line.amount for line in _read(seed_user)
                if line.key.record_id == account.id
            )

            assert amounts == [Decimal("-500.00"), Decimal("500.00")]

    def test_a_loans_anchors_and_payment_split_are_its_lines(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,000.00 payment on a 6% loan trued up to $100,000.00.

        The opening ``-250,000.00`` owed against ``+250,000.00`` opening
        equity; the true-up ``+150,000.00`` / ``-150,000.00`` (owed
        ``250,000.00`` before it, ``100,000.00`` after); and the payment's
        split, ``100,000.00 x 0.06 / 12 = 500.00`` of interest moved off the
        loan's own line onto its Interest line, filed in the PAYMENT's
        paycheck on its day.  None comes from a movement: the payment's own
        cash is the transfer's movement lines.
        """
        with app.app_context():
            loan = _trued_up_loan(seed_user, "Lines Loan")
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            calendar = calendar_for(seed_user["user"].id)

            lines = [
                line for line in _read(seed_user)
                if line.key.record_id == loan.id and line.movement_id is None
            ]

            split = _source(PostingSourceEnum.LOAN_PAYMENT)
            assert lines == [
                *_loan_anchor_lines(loan, calendar),
                LedgerLine(
                    linked_key(loan), seed_periods[1].id, _TODAY,
                    Decimal("-500.00"), split, _kind(PostingKindEnum.PRINCIPAL),
                    None,
                ),
                LedgerLine(
                    loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id),
                    seed_periods[1].id, _TODAY, Decimal("500.00"), split,
                    _kind(PostingKindEnum.INTEREST), None,
                ),
            ]
