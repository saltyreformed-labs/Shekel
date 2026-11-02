"""Tests for the worked-out read (plan task plan#2526).

:func:`app.services.ledger_lines.worked_out_lines` answers, for one owner and
one scenario, every line of the ledger worked out from the records by the
rules the stored ledger's writers post from: each posting movement's two lines
in its parent's paycheck on its own bank day, and each account's and loan's
correction lines filed by their day.

What these cases pin:

* **it issues no write of its own and reads no stored ledger**: on a clean
  session no statement it issues writes, or names the stored journal or
  chart, and nothing is left pending; on a dirty one its one write is the
  caller's pending change, autoflushed, and its lines read that change;
* **whose records it reads**: an owner with none reads nothing and asks no
  pay calendar, and another owner's movements are never read, even in that
  owner's scenario;
* **the lines are the rules' own**, written out by hand: a bill, a transfer,
  an account's opening and true-up, a loan's anchors and payment split;
* **a source that LEFT the settled set has no line** (finding N-162,
  plan#1356): after a revert, a revert then Credit, a revert then Cancel, a
  delete, and a transfer's soft delete, the read is what it was before the
  source settled -- the corrections a later balance check books included;
* **a transfer's effect has one rule** (finding N-164, plan#1358): each side
  is its own movement's two lines, valued by the producer the cash fold
  values it by;
* **a refund, a card purchase and an over-posted envelope** (finding N-318,
  plan#1304): money back books as an inflow against the category, a card
  purchase books nothing, and a purchase past its envelope's figure books its
  whole figure OUT, never in;
* **its statements do not grow with the number of sources** (finding
  plan#2147's requirement, ruling plan#2136), **nor with the number of
  accounts or loans** (ruling plan#2623).

Whether the lines equal the STORED ledger's to the cent is the parity test's
(plan#2527) and the production run's (plan#2528).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

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
from app.models.scenario import Scenario
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
from app.services.pay_calendar import PayCalendarError, calendar_for
from app.services.transfer_legs import transfer_family_movements
from tests._test_helpers import (
    an_entered_day,
    capture_sql_statements,
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


def _captured_read(seed_user):
    """Return the seed owner's read and every statement it issued.

    The owner's and scenario's ids are read before the capture starts, so a
    refresh of an expired fixture row is not counted as the read's own.
    """
    owner_id, scenario_id = seed_user["user"].id, seed_user["scenario"].id
    return capture_sql_statements(
        lambda: worked_out_lines(owner_id, scenario_id),
    )


def _lines_of(lines, movement_id):
    """Return the lines booked from one movement, in the read's order."""
    return [line for line in lines if line.movement_id == movement_id]


def _true_ups_of(lines, account):
    """Return the amounts of *account*'s own true-up lines, in the read's order."""
    return [
        line.amount for line in lines
        if line.key == linked_key(account)
        and line.source_kind_id == _source(PostingSourceEnum.ACCOUNT_TRUEUP)
    ]


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


def _loan_anchor_lines(loan, calendar):
    """Return the opening and true-up lines of the suite's trued-up loan.

    The loan ``create_loan_with_trueup`` builds from a $250,000.00 principal
    originated 2025-01-01 and trued up to $100,000.00 on 2026-01-10: the
    opening ``-250,000.00`` owed on its own line against ``+250,000.00`` on
    its opening equity, then the true-up ``250,000.00 - 100,000.00 =
    +150,000.00`` and ``-150,000.00`` (no payment falls before it), each filed
    by its day on *calendar* and from no movement.
    """
    own = linked_key(loan)
    equity = loan_key(LedgerAccountKindEnum.EQUITY_OPENING, loan.id)
    opened = date(2025, 1, 1)
    trued = date(2026, 1, 10)
    opening = (
        _source(PostingSourceEnum.LOAN_OPENING), _kind(PostingKindEnum.OPENING),
    )
    trueup = (
        _source(PostingSourceEnum.LOAN_TRUEUP), _kind(PostingKindEnum.TRUEUP),
    )
    return [
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
    ]


def _writes_in(statements):
    """Return the text of each captured statement that is not a SELECT."""
    return [
        text for text, _params in statements
        if not text.lstrip().upper().startswith("SELECT")
    ]


class TestTheReadWritesNothing:
    """It reads the records alone, and issues no write of its own."""

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

            lines, statements = _captured_read(seed_user)

            assert lines
            assert statements
            assert _writes_in(statements) == []
            store = [
                text for text, _params in statements
                if "journal_entries" in text or "account_postings" in text
                or "ledger_accounts" in text
            ]
            assert store == []
            assert not _db.session.new
            assert not _db.session.dirty
            assert not _db.session.deleted

    def test_a_pending_change_is_read_as_flushed_and_is_its_one_write(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,100.00 balance check staged, not flushed: read, and the one write.

        Checking opens at $1,000.00 and holds no movement.  The check,
        observed today, is added to the session and left pending, so the
        read's first query autoflushes it: the one statement among the
        read's that is not a SELECT is that row's INSERT, and the read
        issues no write of its own.  Its lines read the check as flushed --
        a true-up of ``1,100.00 - 1,000.00 = +100.00`` on Checking, filed by
        today -- so a balance check a write has staged is read, not missed.
        """
        with app.app_context():
            _db.session.commit()
            checking = seed_user["account"]
            checking_id = checking.id
            owner_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            pending = AccountAnchorHistory(
                account_id=checking_id, anchor_balance=Decimal("1100.00"),
                observed_on=_TODAY, recorded_on=_TODAY,
            )
            _db.session.add(pending)
            assert pending in _db.session.new

            lines, statements = capture_sql_statements(
                lambda: worked_out_lines(owner_id, scenario_id),
            )

            (write,) = _writes_in(statements)
            assert write.startswith("INSERT INTO budget.account_anchor_history")
            assert pending not in _db.session.new
            filed_in = calendar_for(owner_id).filing_period(_TODAY).period_id
            assert LedgerLine(
                linked_key(checking), filed_in, _TODAY, Decimal("100.00"),
                _source(PostingSourceEnum.ACCOUNT_TRUEUP),
                _kind(PostingKindEnum.TRUEUP), None,
            ) in lines


class TestWhoseRecordsItReads:
    """The owner's own records, and nothing when there are none."""

    def test_an_owner_with_no_records_reads_nothing(self, app, db, bare_user):
        """An owner holding no account, no paycheck and no pay schedule: no line.

        There is nothing to book and no correction to file, so the read asks
        no pay calendar -- which this owner could not answer: holding no pay
        schedule, :func:`calendar_for` refuses them (the control, so the case
        reads the owner it is about).  The writers load the calendar only
        when a correction is to be filed, and so does the read.  The owner
        holds its baseline scenario alone, the one it is read in.
        """
        with app.app_context():
            owner_id = bare_user["user"].id
            scenario = Scenario(user_id=owner_id, name="Baseline", is_baseline=True)
            _db.session.add(scenario)
            _db.session.flush()
            with pytest.raises(PayCalendarError):
                calendar_for(owner_id)

            assert worked_out_lines(owner_id, scenario.id) == []

    def test_an_owner_whose_one_account_is_a_loan_reads_its_loan_lines(
        self, app, db, bare_user, bare_periods,
    ):
        """An owner holding only a 6% loan trued up to $100,000.00: four lines.

        No account but the loan, so no account has a correction and the
        loan's walk alone has one to file: the owner's pay calendar is loaded
        for it (``LoanLedgerWalk.is_empty`` is false), and the read is the
        loan's opening and true-up lines (:func:`_loan_anchor_lines`).  An
        owner can hold this: a fresh $0 Checking can be deleted, and a loan
        added after it.
        """
        with app.app_context():
            owner_id = bare_user["user"].id
            scenario = Scenario(user_id=owner_id, name="Baseline", is_baseline=True)
            _db.session.add(scenario)
            _db.session.flush()
            loan = create_loan_with_trueup(
                bare_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=date(2026, 1, 10), rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Only Loan",
            )
            calendar = calendar_for(owner_id)

            lines = worked_out_lines(owner_id, scenario.id)

            assert lines == _loan_anchor_lines(loan, calendar)

    def test_another_owners_movements_are_never_read(
        self, app, seed_user, seed_periods, seed_second_user,
        seed_second_periods,
    ):
        """Two owners: a $11.11 bill, and a $77.77 bill and $250.00 transfer.

        The second owner's read holds its bill's two lines and each of its
        transfer's sides' two (the control: the records are there, in that
        scenario).  The first owner's read holds its own $11.11 bill's and
        nothing of the second's; and asked in the SECOND owner's scenario it
        reads no movement at all, of either arm.  That last read is the one
        the owner clause alone decides: in the first owner's own scenario the
        scenario clause keeps the second owner's rows out too, while in the
        second owner's scenario only ``TransactionEntry.owner_id`` does
        (``cash_ledger._movement_scope._of_owner``), over a plan row's
        movements and a transfer leg's alike.
        """
        with app.app_context():
            create_settled_cash_transaction(
                seed_user, _db.session, seed_periods[0], Decimal("11.11"),
            )
            create_settled_cash_transaction(
                seed_second_user, _db.session, seed_second_periods[0],
                Decimal("77.77"),
            )
            other_savings = create_account_of_type(
                seed_second_user, _db.session, "Savings", "Other Savings",
            )
            create_settled_transfer(
                seed_second_user, _db.session, seed_second_user["account"],
                other_savings, seed_second_periods[0], amount=Decimal("250.00"),
            )
            _db.session.commit()
            owner_id = seed_user["user"].id
            other_id = seed_second_user["user"].id
            other_scenario_id = seed_second_user["scenario"].id

            def _moved(lines):
                """Return the amounts of the lines booked from a movement."""
                return sorted(line.amount for line in lines if line.movement_id)

            # The bill's -77.77 / +77.77; each transfer side's -250.00 and
            # +250.00 against the owner's Transfers in transit.
            assert _moved(worked_out_lines(other_id, other_scenario_id)) == [
                Decimal("-250.00"), Decimal("-250.00"), Decimal("-77.77"),
                Decimal("77.77"), Decimal("250.00"), Decimal("250.00"),
            ]
            assert _moved(_read(seed_user)) == [
                Decimal("-11.11"), Decimal("11.11"),
            ]
            assert _moved(worked_out_lines(owner_id, other_scenario_id)) == []


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

        Checking opens at $1,000.00 and a $5,000.00 balance check is observed
        today, so before the settle its true-up is ``5,000.00 - 1,000.00 =
        4,000.00``.  Settled yesterday, the bill's covering movement books
        Checking ``-148.32`` and Rent ``+148.32``, and the check, the closing
        balance for a later day, absorbs it: the true-up becomes ``5,000.00
        - (1,000.00 - 148.32) = 4,148.32`` (the controls: the source entered
        the movement lines AND moved a correction).  Each exit takes the
        movement out of the dated set -- a revert un-dates it, Credit and
        Cancel follow a revert, a delete removes the row with its movements
        -- and the whole read, the true-up back at 4,000.00 included, is then
        the read from before the settle.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = _bill(seed_user, seed_periods[0])
            _db.session.add(AccountAnchorHistory(
                account_id=checking.id, anchor_balance=Decimal("5000.00"),
                observed_on=_TODAY, recorded_on=_TODAY,
            ))
            _db.session.flush()
            before = _read(seed_user)
            assert _true_ups_of(before, checking) == [Decimal("4000.00")]
            transaction_service.settle_transaction(
                txn, settle_day=an_entered_day(_TODAY - timedelta(days=1)),
            )
            _db.session.flush()
            (movement,) = txn.covering_movements
            settled = _read(seed_user)
            assert sorted(
                line.amount for line in _lines_of(settled, movement.id)
            ) == [Decimal("-148.32"), Decimal("148.32")]
            assert _true_ups_of(settled, checking) == [Decimal("4148.32")]

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
        line nets anything against the envelope's figure.  Every line booked
        under the ``purchase`` source is read, with a movement or without,
        so a line netted against the row itself -- the shape N-318 was, which
        comes from no movement -- would show here as a line with none.
        """
        with app.app_context():
            envelope = _envelope(seed_user, seed_periods[0], "163.95")
            purchase = _purchase(seed_user, envelope, "367.62")
            transaction_service.settle_transaction(envelope)
            _db.session.flush()

            purchase_source = _source(PostingSourceEnum.PURCHASE)
            lines = [
                line for line in _read(seed_user)
                if line.source_kind_id == purchase_source
            ]

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
    """Finding plan#2147's requirement (ruling plan#2136), and ruling plan#2623's."""

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
            few, few_statements = _captured_read(seed_user)
            _add_sources(seed_periods[2:4])
            many, many_statements = _captured_read(seed_user)

            assert len(many) > len(few)
            assert len(many_statements) == len(few_statements)

    def test_six_accounts_read_in_the_statements_two_do(
        self, app, db, seed_user, seed_periods,
    ):
        """Two non-loan accounts, then six: one count (ruling plan#2623).

        Each account holds its opening, its balance check and a settled
        transfer from Checking, so every account the second read walks has
        records of each kind the walk folds.  The owner's openings, balance
        checks and movements are each loaded once whatever the number of
        accounts (``cash_ledger.owner_cash_records``), so a per-account load
        -- a walk that loads its own account's records -- makes the second
        read issue four more statements per added account and fails this.
        Each read starts from an expired session, and the second reads more
        lines than the first, so both measured a read that grew.
        """
        with app.app_context():
            checking = seed_user["account"]

            def _add_accounts(count, first):
                """Add *count* Savings accounts, each paid into once."""
                for number in range(first, first + count):
                    savings = create_account_of_type(
                        seed_user, _db.session, "Savings",
                        f"Counted Savings {number}",
                    )
                    create_settled_transfer(
                        seed_user, _db.session, checking, savings,
                        seed_periods[1], amount=Decimal("25.00"),
                    )
                _db.session.commit()
                _db.session.expire_all()

            _add_accounts(1, 1)
            two, two_statements = _captured_read(seed_user)
            _add_accounts(4, 2)
            six, six_statements = _captured_read(seed_user)

            assert len(six) > len(two)
            assert len(six_statements) == len(two_statements)

    def test_three_loans_read_in_the_statements_one_does(
        self, app, db, seed_user, seed_periods,
    ):
        """One loan, then three: one count (ruling plan#2623, plan task plan#2649).

        Each loan holds its terms, a true-up, an escrow line and a settled
        payment from Checking, so every loan the second read walks has
        records of each kind its walk folds.  The loans' terms ride the
        account load (``Account.loan_params``), and their statements, rate
        history, escrow lines and payments are each loaded once whatever the
        number of loans (``loan_ledger.walk_loan_ledgers``), so a per-loan
        load -- a walk that loads its own loan's records -- makes the second
        read issue more statements per added loan and fails this.  Each read
        starts from an expired session, and the second reads more lines than
        the first, so both measured a read that grew.
        """
        with app.app_context():
            checking = seed_user["account"]

            def _add_loans(count, first):
                """Add *count* loans, each with escrow and paid into once."""
                for number in range(first, first + count):
                    loan = create_loan_with_trueup(
                        seed_user, _db.session,
                        origination_principal=Decimal("250000.00"),
                        anchor_balance=Decimal("100000.00"),
                        anchor_date=date(2026, 1, 10),
                        rate=Decimal("0.06000"),
                        origination_date=date(2025, 1, 1),
                        name=f"Counted Loan {number}",
                        escrow_annual=Decimal("1200.00"),
                    )
                    create_settled_transfer(
                        seed_user, _db.session, checking, loan,
                        seed_periods[1], amount=Decimal("1000.00"),
                    )
                _db.session.commit()
                _db.session.expire_all()

            _add_loans(1, 1)
            one, one_statements = _captured_read(seed_user)
            _add_loans(2, 2)
            three, three_statements = _captured_read(seed_user)

            assert len(three) > len(one)
            assert len(three_statements) == len(one_statements)
