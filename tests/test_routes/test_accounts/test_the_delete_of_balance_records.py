"""A permanent delete takes an account's balance records (ruling plan#2784).

Worked out, an account's openings and true-ups book both halves on its own lines
(pinned by ``tests/test_services/test_ledger_lines/test_the_target_rules.py``), and
guards 2-4 archive it for every transaction or transfer, so the ledger-history guard only
kept balance history, which it was never for.  What these cases hold the door to:

* it **deletes** an account holding only balance records, of every projection kind and a
  loan, and each statement stays in balance without its lines (the ruling's own
  worked figures: a sheet loses the account's assets and equity, an income
  statement its change in value);
* a **loan's** per-loan chart rows, whose RESTRICT key would refuse the delete, go first;
* **what the stored ledger is left with** aborts no deploy: the single legs a transfer's
  entries leave on Transfers in transit net to zero, and all three deploy hooks run over
  them and write nothing.  The running app holds no DELETE on the journal, so they stay
  until X-ew's third leaf (plan#2516) deletes the store.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    LedgerAccountKindEnum,
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_params import LoanParams
from app.models.statement_import import StatementImport
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    anchor_service,
    ledger_report_service,
    loan_posting_service,
    posting_service,
    transfer_service,
)
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for
from app.utils.dates import display_today
from tests._ledger_parity import assert_ledger_parity
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
)


def _labels(section):
    """Return the labels of one statement section's lines."""
    return [line.label for line in section.lines]


def _sheet(seed_user):
    """Return the seed owner's balance sheet through today."""
    return ledger_report_service.compute_balance_sheet(
        seed_user["user"].id, display_today(),
    )


def _chart_rows_of(account_id):
    """Return how many stored chart rows name the account, through either link."""
    return _db.session.query(LedgerAccount).filter(or_(
        LedgerAccount.account_id == account_id,
        LedgerAccount.loan_account_id == account_id,
    )).count()


def _assert_deleted(response, account_id):
    """Assert the door deleted the account, rather than archiving it."""
    assert response.status_code == 200
    assert b"cannot be permanently deleted" not in response.data
    assert b"permanently deleted" in response.data
    _db.session.expire_all()
    assert _db.session.get(Account, account_id) is None
    assert _chart_rows_of(account_id) == 0


def _assert_the_deploy_hooks_write_nothing():
    """Run the three deploy hooks in deploy order, and commit what they leave.

    Each reports what it walked rather than what it wrote, so the journal's row
    count is the measure.  The commit is what fires the deferred balanced-journal
    trigger over anything they posted.
    """
    entries_before = _db.session.query(JournalEntry).count()
    posting_service.resync_all_cash_postings()
    loan_posting_service.backfill_all_loan_postings()
    account_posting_service.backfill_all_account_anchor_postings()
    _db.session.commit()
    assert _db.session.query(JournalEntry).count() == entries_before


class TestItDeletesAnAccountHoldingOnlyBalanceRecords:
    """Every projection kind deletes, and each statement stays in balance."""

    def test_a_plain_accounts_opening_and_true_up_go_with_it(
        self, app, auth_client, seed_user,
    ):
        """$350.00 opened, trued up to $400.00: the sheet loses $400.00 each side.

        Before: Assets ``1,000.00 + 400.00 = 1,400.00``.  After: the seeded
        Checking's ``1,000.00`` alone, on both sides of the identity.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Plain Savings",
                anchor_balance=Decimal("350.00"),
                observed_on=display_today() - timedelta(days=3),
            )
            anchor_service.apply_anchor_true_up(
                account=account, new_balance=Decimal("400.00"),
                observed_on=display_today() - timedelta(days=1),
            )
            _db.session.commit()
            account_id = account.id
            before = _sheet(seed_user)
            assert before.assets.total == Decimal("1400.00")
            assert before.tie_out.in_balance is True

            response = auth_client.post(
                f"/accounts/{account_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, account_id)
            assert _db.session.query(AccountAnchorHistory).filter_by(
                account_id=account_id,
            ).count() == 0
            after = _sheet(seed_user)
            assert _labels(after.assets) == ["Checking"]
            assert after.assets.total == Decimal("1000.00")
            assert after.tie_out.liabilities_plus_equity == Decimal("1000.00")
            assert after.tie_out.in_balance is True
            assert_ledger_parity(_db.session, seed_user["scenario"].id)

    def test_an_investments_change_in_value_leaves_the_income_statement(
        self, app, auth_client, seed_user,
    ):
        """$1,000.00 opened, trued up to $1,200.00: $200.00 of change in value goes.

        The true-up's counter is the account's Unrealized line (ruling R-FO), so
        the year's Unrealized section reads ``1,200.00 - 1,000.00 = 200.00``
        before the delete and holds nothing after it.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Roth IRA", "Roth",
                anchor_balance=Decimal("1000.00"),
                observed_on=display_today() - timedelta(days=3),
            )
            anchor_service.apply_anchor_true_up(
                account=account, new_balance=Decimal("1200.00"),
                observed_on=display_today(),
            )
            _db.session.commit()
            account_id = account.id
            owner_id = seed_user["user"].id

            def _unrealized():
                """Return the Unrealized section of this year's income statement."""
                return ledger_report_service.compute_income_statement(
                    owner_id, calendar_for(owner_id),
                    StatementWindow("year", year=display_today().year),
                ).unrealized

            assert _unrealized().total == Decimal("200.00")

            response = auth_client.post(
                f"/accounts/{account_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, account_id)
            assert not _unrealized().lines
            assert _sheet(seed_user).tie_out.in_balance is True
            assert_ledger_parity(_db.session, seed_user["scenario"].id)

    def test_a_property_goes_and_its_loan_keeps_its_balance(
        self, app, auth_client, seed_user,
    ):
        """A $350,000.00 Home goes; the Mortgage it secured loses only the link.

        ``fk_accounts_collateral_account`` is SET NULL because the link is
        presentation: the loan's $250,000.00 owed stays on the sheet.
        """
        with app.app_context():
            home = create_account_of_type(
                seed_user, _db.session, "Property", "Home",
                anchor_balance=Decimal("350000.00"),
            )
            loan = create_loan_account(
                seed_user, _db.session, name="Mortgage",
                principal=Decimal("250000.00"), rate=Decimal("0.06500"),
                term=360, origination_date=date(2023, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            loan.collateral_account_id = home.id
            _db.session.commit()
            home_id, loan_id = home.id, loan.id

            response = auth_client.post(
                f"/accounts/{home_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, home_id)
            assert _db.session.get(Account, loan_id).collateral_account_id is None
            after = _sheet(seed_user)
            assert _labels(after.assets) == ["Checking"]
            assert after.liabilities.total == Decimal("250000.00")
            assert after.tie_out.in_balance is True

    def test_an_imported_statements_balance_goes_with_its_account(
        self, app, auth_client, seed_user,
    ):
        """A bank level is a balance record too, and the import goes with it.

        Both reach the account by a CASCADE, and the level also by its import, so
        the delete meets the two keys in whichever order PostgreSQL fires them.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Savings", "Imported Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=date(2026, 3, 1),
            )
            statement = StatementImport(
                account_id=account.id,
                user_id=account.user_id,
                source_id=ref_cache.statement_source_id(
                    StatementSourceEnum.SECU_CHECKING_CSV,
                ),
                file_name="seed.csv",
                file_digest="seed.csv".ljust(64, "0"),
                declared_start=date(2026, 3, 1),
                declared_end=date(2026, 3, 3),
                stated_balance=Decimal("125.00"),
                stated_balance_on=date(2026, 3, 3),
            )
            _db.session.add(statement)
            _db.session.flush()
            _db.session.add(AccountAnchorHistory(
                account_id=account.id,
                anchor_balance=Decimal("125.00"),
                observed_on=date(2026, 3, 3),
                evidence_id=ref_cache.statement_balance_evidence_id(
                    StatementBalanceEvidenceEnum.FILE_CHAIN,
                ),
                statement_import_id=statement.id,
            ))
            _db.session.commit()
            account_id, statement_id = account.id, statement.id

            response = auth_client.post(
                f"/accounts/{account_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, account_id)
            assert _db.session.get(StatementImport, statement_id) is None
            assert _sheet(seed_user).tie_out.in_balance is True


class TestItDeletesALoan:
    """A loan's per-loan chart rows go first, or their RESTRICT key refuses it."""

    def test_a_loan_holding_its_opening_and_a_true_up_is_deleted(
        self, app, auth_client, seed_user,
    ):
        """$250,000.00 originated, trued up to $100,000.00: the sheet loses both.

        Before: Liabilities ``100,000.00``, the true-up's ``250,000.00 - 150,000.00``.
        After: none, and the seeded Checking's ``1,000.00`` alone on each side.  The
        loan holds an ``equity_opening`` row, which ``loan_account_id`` names.
        """
        with app.app_context():
            loan = create_loan_with_trueup(
                seed_user, _db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                anchor_date=display_today() - timedelta(days=1),
                rate=Decimal("0.06000"),
                origination_date=date(2025, 1, 1), name="Trued Loan",
            )
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            _db.session.commit()
            loan_id = loan.id
            assert _db.session.query(LedgerAccount).filter_by(
                loan_account_id=loan_id,
            ).count() > 0
            assert _sheet(seed_user).liabilities.total == Decimal("100000.00")

            response = auth_client.post(
                f"/accounts/{loan_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, loan_id)
            assert _db.session.query(LoanParams).filter_by(
                account_id=loan_id,
            ).count() == 0
            after = _sheet(seed_user)
            assert not after.liabilities.lines
            assert after.assets.total == Decimal("1000.00")
            assert after.tie_out.in_balance is True
            assert_ledger_parity(_db.session, seed_user["scenario"].id)
            _assert_the_deploy_hooks_write_nothing()

    def test_a_loan_whose_only_payment_is_hidden_is_deleted(
        self, app, auth_client, seed_user,
    ):
        """A hidden payment goes through the transfer door, and the loan after it.

        The soft delete took the payment's records off the books, so guard 4 has
        nothing to archive on; step 1's hard delete of the hidden transfer
        re-reconciles the loan, and the delete takes the rows it reconciled.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, _db.session, name="Hidden Payment Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05000"),
                term=60, origination_date=date(2023, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.AUTO_LOAN,
            )
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], loan,
                seed_user["bootstrap_period"], amount=Decimal("400.00"),
            )
            _db.session.commit()
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            _db.session.commit()
            loan_id, transfer_id = loan.id, transfer.id

            response = auth_client.post(
                f"/accounts/{loan_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, loan_id)
            assert _db.session.get(Transfer, transfer_id) is None
            assert _labels(_sheet(seed_user).assets) == ["Checking"]
            assert _sheet(seed_user).assets.total == Decimal("1000.00")
            assert _sheet(seed_user).tie_out.in_balance is True
            _assert_the_deploy_hooks_write_nothing()


class TestWhatTheStoredLedgerIsLeftWith:
    """A transfer's entries outlive it, and their other legs outlive the account."""

    def test_the_legs_left_on_transit_net_to_zero_and_abort_no_deploy(
        self, app, auth_client, seed_user,
    ):
        """A $100.00 transfer settled, then hard-deleted, then its source deleted.

        The entries survive the transfer: each side's settle and its reversal, on
        the side's own line against Transfers in transit.  Deleting the source
        takes its line's legs and leaves its two entries a transit leg each, which
        is the single-leg state the app role cannot clean up (no DELETE on the
        journal).  The two net to ``100.00 - 100.00 = 0.00``; the statements never
        read them (Assets ``1,000.00 + 200.00 + 300.00 = 1,500.00``, then
        ``1,300.00`` without the source's opening); and all three deploy hooks run
        over them and write nothing.
        """
        with app.app_context():
            source = create_account_of_type(
                seed_user, _db.session, "Checking", "Cascade Source",
                anchor_balance=Decimal("200.00"),
            )
            dest = create_account_of_type(
                seed_user, _db.session, "Savings", "Cascade Dest",
                anchor_balance=Decimal("300.00"),
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, source, dest,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=False,
            )
            _db.session.commit()
            source_id, scenario_id = source.id, seed_user["scenario"].id
            assert _sheet(seed_user).assets.total == Decimal("1500.00")

            response = auth_client.post(
                f"/accounts/{source_id}/hard-delete", follow_redirects=True,
            )

            _assert_deleted(response, source_id)
            single_legged = (
                _db.session.query(Posting.journal_entry_id)
                .group_by(Posting.journal_entry_id)
                .having(func.count(Posting.id) == 1)
                .subquery()
            )
            left = _db.session.query(Posting).join(
                LedgerAccount, Posting.ledger_account_id == LedgerAccount.id,
            ).filter(Posting.journal_entry_id.in_(_db.select(single_legged)))
            assert left.count() > 0, "this world must leave a single leg to run over"
            assert {
                leg.ledger_account_id for leg in left
            } == {
                row.id for row in _db.session.query(LedgerAccount).filter_by(
                    kind_id=ref_cache.ledger_account_kind_id(
                        LedgerAccountKindEnum.TRANSIT,
                    ),
                )
            }
            assert sum(leg.amount for leg in left) == Decimal("0.00")
            after = _sheet(seed_user)
            assert after.assets.total == Decimal("1300.00")
            assert after.tie_out.in_balance is True
            assert_ledger_parity(_db.session, scenario_id)
            _assert_the_deploy_hooks_write_nothing()
            assert_ledger_parity(_db.session, scenario_id)
