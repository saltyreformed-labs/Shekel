"""Tests for the worked-out read (plan task plan#2526).

:func:`app.services.ledger_lines.worked_out_lines` answers, for one owner and
one scenario, every line of the ledger worked out from the records by the
rules the stored ledger's writers post from: each posting movement's two lines
in its parent's paycheck on its own bank day, and each account's and loan's
correction lines filed by their day.

What these cases pin:

* **it writes nothing and reads no stored ledger**: no statement it issues
  writes, or names the stored journal or chart, and nothing is left pending;
* **the lines are the rules' own**, written out by hand: a bill, a transfer,
  an account's opening and true-up, a loan's anchors and payment split;
* **a source that LEFT the settled set has no line** (finding N-162,
  plan#1356): after a revert, a revert then Credit, a revert then Cancel, a
  delete, and a transfer's soft delete, the read is what it was before the
  source settled;
* **a transfer's effect has one rule** (finding N-164, plan#1358): each side
  is its own movement's two lines, valued by the producer the cash fold
  values it by;
* **a refund, a card purchase and an over-posted envelope** (finding N-318,
  plan#1304): money back books as an inflow against the category, a card
  purchase books nothing, and a purchase past its envelope's figure books its
  whole figure OUT, never in;
* **its statements do not grow with the number of sources** (finding
  plan#2147's requirement, ruling plan#2136).

Whether the lines equal the STORED ledger's to the cent is the parity test's
(plan#2527) and the production run's (plan#2528).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.services import (
    credit_workflow,
    entry_service,
    transaction_service,
    transfer_service,
)
from app.services.cash_ledger import account_opening_fact, settled_cash_facts
from app.services.ledger_lines import (
    LedgerLine,
    account_counter_key,
    category_key,
    linked_key,
    loan_key,
    transit_key,
    worked_out_lines,
)
from app.services.pay_calendar import calendar_for
from app.services.transfer_legs import transfer_family_movements
from tests._test_helpers import (
    an_entered_day,
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    generate_row_of,
    make_expense_template,
    typed,
)

# Inside seed_periods' last paycheck (2026-05-08 - 2026-05-21) and after every
# paycheck a case files a row in, so a row in an early paycheck settles on a
# day its own paycheck does not hold.
_TODAY = date(2026, 5, 15)


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze the app's today at :data:`_TODAY` for every case."""
    freeze_today(monkeypatch, _TODAY)


def _read(seed_user):
    """Return the seed owner's worked-out lines in the baseline scenario."""
    return worked_out_lines(seed_user["user"].id, seed_user["scenario"].id)


def _lines_of(lines, movement_id):
    """Return the lines booked from one movement, in the read's order."""
    return [line for line in lines if line.movement_id == movement_id]


def _source(member):
    """Return the stored id of a ``PostingSourceEnum`` member."""
    return ref_cache.posting_source_id(member)


def _kind(member):
    """Return the stored id of a ``PostingKindEnum`` member."""
    return ref_cache.posting_kind_id(member)


def _bill(seed_user, period):
    """Return a fresh $148.32 Rent bill generated into *period*, Projected."""
    template = make_expense_template(
        _db.session, seed_user, amount="148.32", name="Electric",
        category_key="Rent",
    )
    return generate_row_of(template, period)


def _envelope(seed_user, period, amount):
    """Return a fresh Groceries envelope of *amount* generated into *period*."""
    template = make_expense_template(
        _db.session, seed_user, amount=amount, name="Groceries Run",
        category_key="Groceries", is_envelope=True,
    )
    return generate_row_of(template, period)


def _purchase(seed_user, txn, amount, *, is_credit=False):
    """Record a purchase of *amount* on *txn*, made and posted today."""
    return entry_service.create_entry(
        transaction_id=txn.id,
        user_id=seed_user["user"].id,
        details=entry_service.EntryDetails(
            figure=typed(Decimal(amount)),
            description="Market",
            purchased_on=_TODAY,
            is_credit=is_credit,
            settle_day=an_entered_day(_TODAY),
        ),
    )


def _statements_of(fn):
    """Run *fn* and return its result and every SQL statement it issued."""
    statements = []

    def _capture(_conn, _cursor, statement, *_args):
        """Record the statement's text."""
        statements.append(statement)

    event.listen(_db.engine, "before_cursor_execute", _capture)
    try:
        result = fn()
    finally:
        event.remove(_db.engine, "before_cursor_execute", _capture)
    return result, statements


class TestTheReadWritesNothing:
    """It reads the records alone, and nothing it does is a write."""

    def test_no_statement_writes_or_reads_the_stored_ledger(
        self, app, db, seed_user, seed_periods,
    ):
        """A bill, a transfer and a loan payment read in SELECTs of the records alone.

        Every statement is a SELECT, none names the stored journal
        (``journal_entries``, ``account_postings``) or chart
        (``ledger_accounts``), and the session holds nothing new, changed or
        deleted afterwards: the read works the ledger out, it does not look
        the stored one up.  The lines are non-empty, so the capture measured
        a read that did something.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, _db.session, seed_periods[0], Decimal("40.00"),
                category=seed_user["categories"]["Groceries"],
            )
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Quiet Savings",
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Quiet Loan",
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            _db.session.commit()

            lines, statements = _statements_of(lambda: _read(seed_user))

            assert lines
            assert statements
            not_a_select = [
                s for s in statements
                if not s.lstrip().upper().startswith("SELECT")
            ]
            assert not_a_select == []
            store = [
                s for s in statements
                if "journal_entries" in s or "account_postings" in s
                or "ledger_accounts" in s
            ]
            assert store == []
            assert not _db.session.new
            assert not _db.session.dirty
            assert not _db.session.deleted


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
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Lines Loan",
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            calendar = calendar_for(seed_user["user"].id)

            lines = [
                line for line in _read(seed_user)
                if line.key.record_id == loan.id and line.movement_id is None
            ]

            own = linked_key(loan)
            equity = loan_key(LedgerAccountKindEnum.EQUITY_OPENING, loan.id)
            opened = date(2025, 1, 1)
            trued = date(2026, 1, 10)
            opening = (
                _source(PostingSourceEnum.LOAN_OPENING),
                _kind(PostingKindEnum.OPENING),
            )
            trueup = (
                _source(PostingSourceEnum.LOAN_TRUEUP),
                _kind(PostingKindEnum.TRUEUP),
            )
            split = _source(PostingSourceEnum.LOAN_PAYMENT)
            assert lines == [
                LedgerLine(
                    own, calendar.filing_period(opened).period_id, opened,
                    Decimal("-250000.00"), *opening, None,
                ),
                LedgerLine(
                    equity, calendar.filing_period(opened).period_id, opened,
                    Decimal("250000.00"), *opening, None,
                ),
                LedgerLine(
                    own, calendar.filing_period(trued).period_id, trued,
                    Decimal("150000.00"), *trueup, None,
                ),
                LedgerLine(
                    equity, calendar.filing_period(trued).period_id, trued,
                    Decimal("-150000.00"), *trueup, None,
                ),
                LedgerLine(
                    own, seed_periods[1].id, _TODAY, Decimal("-500.00"),
                    split, _kind(PostingKindEnum.PRINCIPAL), None,
                ),
                LedgerLine(
                    loan_key(LedgerAccountKindEnum.LOAN_INTEREST, loan.id),
                    seed_periods[1].id, _TODAY, Decimal("500.00"), split,
                    _kind(PostingKindEnum.INTEREST), None,
                ),
            ]


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )


def _revert_then_credit(txn):
    """Revert *txn*, then mark it Credit through its door."""
    _revert(txn)
    _db.session.flush()
    credit_workflow.mark_as_credit(txn.id, txn.user_id)


def _revert_then_cancel(txn):
    """Revert *txn*, then cancel it through the status door."""
    _revert(txn)
    _db.session.flush()
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.CANCELLED),
    )


def _delete(txn):
    """Delete *txn* through the one removal act (soft: its definition recurs)."""
    transaction_service.delete_transaction(txn, txn.user_id)


class TestASourceThatLeftHasNoLine:
    """Finding N-162 (plan#1356): a source that left the settled set drops out.

    A walk that re-derives one account cannot see a source that LEFT it,
    because the walk reads only what is still there; the stored ledger needs
    the reversal a door writes as the source leaves.  The worked-out read has
    no store to reverse: it is the records each time it is read, so a source
    that left reads as one that never settled -- its movement lines AND what
    it had done to every account's corrections.
    """

    @pytest.mark.parametrize(
        "leave",
        [_revert, _revert_then_credit, _revert_then_cancel, _delete],
        ids=["revert", "revert-then-credit", "revert-then-cancel", "delete"],
    )
    def test_a_bill_that_left_reads_as_if_it_never_settled(
        self, app, db, seed_user, seed_periods, leave,
    ):
        """A $148.32 bill settled, then taken out: the read is as before it settled.

        Settled, its covering movement books Checking ``-148.32`` and Rent
        ``+148.32`` (the control: the source did enter).  Each exit takes the
        movement out of the dated set -- a revert un-dates it, Credit and
        Cancel follow a revert, a delete removes the row with its movements
        -- and the whole read, corrections included, is then the read from
        before the settle.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _db.session.flush()
            before = _read(seed_user)
            transaction_service.settle_transaction(txn)
            _db.session.flush()
            (movement,) = txn.covering_movements
            assert sorted(
                line.amount for line in _lines_of(_read(seed_user), movement.id)
            ) == [Decimal("-148.32"), Decimal("148.32")]

            leave(txn)
            _db.session.flush()

            assert _read(seed_user) == before

    def test_a_soft_deleted_transfer_reads_as_if_it_never_settled(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled $250.00 transfer soft-deleted: the read is as before it.

        Its soft delete takes both sides' records with it, so neither side's
        two lines stay and the read is the one taken before the transfer.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Left Savings",
            )
            _db.session.flush()
            before = _read(seed_user)
            xfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            assert len([
                line for line in _read(seed_user) if line.movement_id
            ]) == 4

            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            _db.session.flush()

            assert _read(seed_user) == before


class TestATransferHasOneRule:
    """Finding N-164 (plan#1358): a transfer's effect is the movement rule's alone."""

    def test_each_side_is_its_own_movements_two_lines_valued_as_the_fold_values_it(
        self, app, db, seed_user, seed_periods,
    ):
        """$250.00 Checking -> Savings: two movements, four lines, one rule.

        From-side ``{Checking -250.00, Transit +250.00}``, to-side
        ``{Savings +250.00, Transit -250.00}``, each under the
        ``transfer_movement`` source and the ``transfer`` leg kind, in the
        transfer's paycheck on its own record's day; the transit line nets
        to zero.  No other line comes from a movement.  Each side's cash line
        is the cash fold's fact for the same movement (``settled_cash_facts``,
        through ``movement_cash_leg``), so the posted effect and the balance
        are one valuation, not two rules that agree.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "One Rule Savings",
            )
            xfer = create_settled_transfer(
                seed_user, _db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"),
            )
            movements = {
                movement.account_id: movement
                for movement, _leg in transfer_family_movements(xfer)
            }
            out, back = movements[checking.id], movements[savings.id]

            lines = [line for line in _read(seed_user) if line.movement_id]

            moved = _source(PostingSourceEnum.TRANSFER_MOVEMENT)
            transfer = _kind(PostingKindEnum.TRANSFER)
            period = xfer.pay_period_id
            assert lines == [
                LedgerLine(
                    linked_key(checking), period, out.settled_on,
                    Decimal("-250.00"), moved, transfer, out.id,
                ),
                LedgerLine(
                    transit_key(), period, out.settled_on, Decimal("250.00"),
                    moved, transfer, out.id,
                ),
                LedgerLine(
                    linked_key(savings), period, back.settled_on,
                    Decimal("250.00"), moved, transfer, back.id,
                ),
                LedgerLine(
                    transit_key(), period, back.settled_on,
                    Decimal("-250.00"), moved, transfer, back.id,
                ),
            ]
            for account, movement, line in (
                (checking, out, lines[0]), (savings, back, lines[2]),
            ):
                (fact,) = [
                    fact for fact in settled_cash_facts(
                        account.id, seed_user["scenario"].id,
                    )
                    if fact.entry_id == movement.id
                ]
                assert line.amount == fact.delta


class TestRefundsCardsAndOverPostedEnvelopes:
    """Finding N-318 (plan#1304), re-measured through the read, and its kin."""

    def test_a_refund_books_money_back_against_its_category(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``-$28.29`` refund under a Groceries envelope: Checking +28.29.

        Money coming back is the cash leg ``+28.29`` and a contra-expense
        ``-28.29`` on Groceries -- the rule is arithmetic, not a case
        analysis over the sign (ruling bank_import:R-II).
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "200.00")
            refund = _purchase(seed_user, envelope, "-28.29")

            lines = _lines_of(_read(seed_user), refund.id)

            groceries = seed_user["categories"]["Groceries"].id
            assert [(line.key, line.amount) for line in lines] == [
                (linked_key(seed_user["account"]), Decimal("28.29")),
                (
                    category_key(groceries, LedgerAccountClassEnum.EXPENSE),
                    Decimal("-28.29"),
                ),
            ]

    def test_a_card_purchase_books_no_line(
        self, app, db, seed_user, seed_periods,
    ):
        """A $60.00 card purchase posted today: no line.

        A card purchase never touches the account its envelope pays from; it
        leaves later through its own CC Payback, which is a row of its own.
        The control: a $15.00 debit purchase on the same envelope books its
        two lines.
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "200.00")
            card = _purchase(seed_user, envelope, "60.00", is_credit=True)
            debit = _purchase(seed_user, envelope, "15.00")

            lines = _read(seed_user)

            assert _lines_of(lines, card.id) == []
            assert sorted(
                line.amount for line in _lines_of(lines, debit.id)
            ) == [Decimal("-15.00"), Decimal("15.00")]

    def test_a_purchase_past_its_envelopes_figure_books_its_whole_figure_out(
        self, app, db, seed_user, seed_periods,
    ):
        """A $367.62 purchase on a $163.95 envelope, then closed: -367.62, never +203.67.

        Finding N-318's own figures: through ``X-bi-3e`` the cash helper
        netted a settled row's purchases against its recorded figure and
        negated the result, so an envelope recording ``$163.95`` with a
        ``$367.62`` purchase read ``-(163.95 - 367.62) = +203.67``, an
        expense row publishing an inflow.  Worked out from the records each
        purchase books its own figure in its parent's direction and the row
        books nothing (ruling R-BAL80), so the closed envelope's one
        movement is Checking ``-367.62`` against Groceries ``+367.62`` and no
        line nets anything against the envelope's figure.
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "163.95")
            purchase = _purchase(seed_user, envelope, "367.62")
            transaction_service.settle_transaction(envelope)
            _db.session.flush()

            lines = [line for line in _read(seed_user) if line.movement_id]

            groceries = seed_user["categories"]["Groceries"].id
            assert [
                (line.movement_id, line.key, line.amount) for line in lines
            ] == [
                (
                    purchase.id, linked_key(seed_user["account"]),
                    Decimal("-367.62"),
                ),
                (
                    purchase.id,
                    category_key(groceries, LedgerAccountClassEnum.EXPENSE),
                    Decimal("367.62"),
                ),
            ]
            assert _db.session.query(TransactionEntry).filter_by(
                transaction_id=envelope.id,
            ).count() == 1


class TestItsStatementsDoNotGrowWithItsSources:
    """Finding plan#2147's requirement on the computed ledger (ruling plan#2136)."""

    def test_three_times_the_sources_read_in_the_same_statements(
        self, app, db, seed_user, seed_periods,
    ):
        """One bill, transfer and loan payment, then three of each: one count.

        The same accounts throughout (Checking, a Savings, a loan), so what
        grows is the number of movements, transfers and payments.  Each read
        starts from an expired session, so it loads what it reads itself, and
        the second reads twelve more lines than the first, so both measured
        a read that grew.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, _db.session, "Savings", "Counted Savings",
            )
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Counted Loan",
            )

            def _add_sources(periods):
                """One settled bill, transfer and loan payment per period."""
                for period in periods:
                    create_settled_cash_transaction(
                        seed_user, _db.session, period, Decimal("40.00"),
                    )
                    create_settled_transfer(
                        seed_user, _db.session, checking, savings, period,
                        amount=Decimal("250.00"),
                    )
                    create_settled_transfer(
                        seed_user, _db.session, checking, loan, period,
                        amount=Decimal("1000.00"),
                    )
                _db.session.commit()
                _db.session.expire_all()

            _add_sources(seed_periods[1:2])
            few, few_statements = _statements_of(lambda: _read(seed_user))
            _add_sources(seed_periods[2:4])
            many, many_statements = _statements_of(lambda: _read(seed_user))

            assert len(many) > len(few)
            assert len(many_statements) == len(few_statements)
