"""The worked-out read's contract: it issues no write of its own and reads no stored ledger, it
reads its own owner's records alone, and its statements do not grow with its sources, accounts
or loans.
"""

from decimal import Decimal

import pytest

from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.scenario import Scenario
from app.services.ledger_lines import (
    LedgerLine,
    accounts_hold_lines,
    linked_key,
    worked_out_lines,
)
from app.services.pay_calendar import PayCalendarError, calendar_for
from tests._test_helpers import (
    capture_sql_statements,
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    strip_owner_schedule,
)

from ._helpers import (
    _TODAY,
    _kind,
    _loan_anchor_lines,
    _read,
    _source,
    _trued_up_loan,
)


def _captured_read(seed_user):
    """Return the seed owner's read and every statement it issued.

    The owner's and scenario's ids are read before the capture starts, so a
    refresh of an expired fixture row is not counted as the read's own.
    """
    owner_id, scenario_id = seed_user["user"].id, seed_user["scenario"].id
    return capture_sql_statements(lambda: worked_out_lines(owner_id, scenario_id))


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
            loan = _trued_up_loan(seed_user, "Quiet Loan")
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
        a true-up of ``1,100.00 - 1,000.00 = +100.00`` on Checking, dated
        today and budgeted in no paycheck -- so a balance check a write has
        staged is read, not missed.
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
            assert LedgerLine(
                linked_key(checking), None, _TODAY, Decimal("100.00"),
                _source(PostingSourceEnum.ACCOUNT_TRUEUP),
                _kind(PostingKindEnum.TRUEUP), None,
            ) in lines


class TestWhoseRecordsItReads:
    """The owner's own records, and nothing when there are none."""

    def test_an_owner_with_no_records_reads_nothing(self, app, db, bare_user):
        """An owner holding no account, no paycheck and no pay schedule: no line.

        There is nothing to book.  The owner holds no pay schedule, so
        :func:`calendar_for` refuses them (the control, so the case reads the
        owner it is about), and the read asks no calendar.  The owner holds
        its baseline scenario alone, the one it is read in.
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

        No account but the loan, so the loan's walk alone has corrections, and
        the read is the loan's opening and true-up lines
        (:func:`_loan_anchor_lines`).  An owner can hold this: a fresh $0
        Checking can be deleted, and a loan added after it.
        """
        with app.app_context():
            owner_id = bare_user["user"].id
            scenario = Scenario(user_id=owner_id, name="Baseline", is_baseline=True)
            _db.session.add(scenario)
            _db.session.flush()
            loan = _trued_up_loan(bare_user, "Only Loan")

            lines = worked_out_lines(owner_id, scenario.id)

            assert lines == _loan_anchor_lines(loan)

    def test_an_owner_holding_a_correction_and_no_pay_schedule_reads_it(
        self, app, seed_user,
    ):
        """Checking opened at $1,000.00, and the owner's schedule stripped: two lines.

        The owner finding plan#2875 names: a correction (Checking's opening)
        and no pay schedule, which :func:`calendar_for` refuses (the control).
        The read answered that refusal while it filed each correction in a
        paycheck; it asks no calendar now, so it reads the opening's two lines,
        ``+1,000.00`` on Checking's own line and ``-1,000.00`` on its equity
        twin, each budgeted in no paycheck.  The re-type refusal's question
        reads the same lines (:func:`accounts_hold_lines`), and Checking holds
        one.
        """
        with app.app_context():
            owner_id = seed_user["user"].id
            checking = _db.session.get(Account, seed_user["account"].id)
            strip_owner_schedule(_db.session, owner_id)
            _db.session.commit()
            with pytest.raises(PayCalendarError):
                calendar_for(owner_id)

            lines = _read(seed_user)

            assert sorted(
                (line.key == linked_key(checking), line.amount,
                 line.budgeted_period_id)
                for line in lines
            ) == [
                (False, Decimal("-1000.00"), None),
                (True, Decimal("1000.00"), None),
            ]
            assert accounts_hold_lines([checking]) is True

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
            loan = _trued_up_loan(seed_user, "Counted Loan")

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
        records of each kind its walk folds.  The loans' terms, statements,
        rate history, escrow lines and payments are each loaded once whatever
        the number of loans (``loan_ledger.walk_loan_ledgers``, the terms by
        the loans' ids since finding plan#2691), so a per-loan
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
                    loan = _trued_up_loan(
                        seed_user, f"Counted Loan {number}",
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
