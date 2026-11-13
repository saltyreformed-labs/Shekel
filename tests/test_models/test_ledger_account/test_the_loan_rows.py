"""The Step-4 per-loan rows: their shape CHECK and the per-(loan, kind) unique."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.ledger_account import LedgerAccount
from tests._stored_ledger import chart_row_or_plant
from tests._test_helpers import create_account_of_type

from ._helpers import _class_id, _kind_id


class TestLoanLedgerShapeAndUnique:
    """The Step-4 per-loan rows: shape CHECK + per-(loan, kind) unique.

    ``ck_ledger_accounts_loan_shape`` keeps a ``loan_account_id`` row from also
    being a linked / category / fallback row, and ``uq_ledger_accounts_loan``
    permits at most one interest / escrow / refund / opening account per
    (owner, loan).  Together they confine the four per-loan kinds to their own
    column shape and cardinality while leaving every other kind untouched (the
    per-loan rows carry NULL ``loan_account_id`` nowhere else, so they fall
    outside the linked / category / fallback uniques).
    """

    def test_valid_loan_row_accepted(self, app, db, seed_user):
        """A well-formed per-loan interest row commits and coexists with linked.

        ``loan_account_id`` set, ``account_id`` / ``category_id`` NULL,
        ``is_owner_bucket`` False, a snapshot ``name``, the ``loan_interest`` kind,
        Expense class -- the shape the Step-4 resolver created.  It coexists
        with the loan account's own linked row, which the test plants
        (different shape, different index), proving the loan unique does not
        collide with ``uq_ledger_accounts_account_kind``.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Loan Shape OK",
            )
            chart_row_or_plant(_db.session, loan, "linked", "Liability")
            _db.session.add(LedgerAccount(
                user_id=loan.user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.LOAN_INTEREST),
                loan_account_id=loan.id,
                name="Mortgage Interest",
            ))
            _db.session.commit()
            loan_rows = (
                _db.session.query(LedgerAccount)
                .filter_by(loan_account_id=loan.id)
                .all()
            )
            assert len(loan_rows) == 1
            assert loan_rows[0].kind_id == _kind_id(
                LedgerAccountKindEnum.LOAN_INTEREST,
            )
            # The loan's own linked row survives, a distinct chart entry.
            linked = (
                _db.session.query(LedgerAccount)
                .filter_by(account_id=loan.id)
                .one()
            )
            assert linked.id != loan_rows[0].id
            assert linked.kind_id == _kind_id(LedgerAccountKindEnum.LINKED)

    def test_loan_row_with_account_id_rejected(self, app, db, seed_user):
        """A per-loan row that ALSO links a real account trips the shape CHECK.

        ``ck_ledger_accounts_loan_shape`` forbids ``account_id`` on a
        ``loan_account_id`` row.  The row is kind ``loan_interest``, so
        ``uq_ledger_accounts_account_kind`` (keyed ``(account_id, kind_id)``)
        cannot fire on it whatever else the loan holds: the only constraint a
        (loan AND account) row can violate is the loan-shape CHECK.  *It
        deleted the loan's linked row first until plan task plan#3133, for a
        collision that could not happen; no door pairs one since.*
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Loan Bad Acct",
            )
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=loan.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    kind_id=_kind_id(LedgerAccountKindEnum.LOAN_INTEREST),
                    loan_account_id=loan.id,
                    account_id=loan.id,
                    name="bad loan row",
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_loan_shape" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_loan_row_with_is_owner_bucket_rejected(self, app, db, seed_user):
        """A per-loan row flagged ``is_owner_bucket`` trips the loan-shape CHECK.

        ``loan_account_id`` set with ``is_owner_bucket`` True violates
        ``ck_ledger_accounts_loan_shape`` (which requires ``NOT is_owner_bucket`` on
        a loan row); ``account_id`` / ``category_id`` stay NULL so the sibling
        ``ck_ledger_accounts_owner_bucket_shape`` is satisfied and the loan-shape
        CHECK is the surface.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Loan Bad Fallback",
            )
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=loan.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    kind_id=_kind_id(LedgerAccountKindEnum.LOAN_ESCROW),
                    loan_account_id=loan.id,
                    is_owner_bucket=True,
                    name="bad loan fallback",
                ))
                _db.session.commit()
            assert "ck_ledger_accounts_loan_shape" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_second_same_kind_loan_row_rejected(self, app, db, seed_user):
        """A second loan row of the SAME kind for the SAME loan trips the unique.

        ``uq_ledger_accounts_loan`` permits one interest / escrow / refund
        account per (owner, loan); a second ``loan_interest`` row for the same
        loan must raise on exactly that index.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Loan Dup Kind",
            )
            _db.session.add(LedgerAccount(
                user_id=loan.user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.LOAN_INTEREST),
                loan_account_id=loan.id,
                name="Mortgage Interest",
            ))
            _db.session.commit()
            with pytest.raises(IntegrityError) as excinfo:
                _db.session.add(LedgerAccount(
                    user_id=loan.user_id,
                    class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                    kind_id=_kind_id(LedgerAccountKindEnum.LOAN_INTEREST),
                    loan_account_id=loan.id,
                    name="Mortgage Interest duplicate",
                ))
                _db.session.commit()
            assert "uq_ledger_accounts_loan" in str(excinfo.value), (
                str(excinfo.value)
            )
            _db.session.rollback()

    def test_four_loan_kinds_one_loan_coexist(self, app, db, seed_user):
        """Interest, escrow, refund, and opening rows for ONE loan coexist.

        ``uq_ledger_accounts_loan`` keys on ``kind_id`` too, so a single loan's
        four distinct per-loan accounts are each a separate chart entry -- the
        edge case the (user, loan, kind) natural key is designed for.  Interest
        and escrow are Expense; refund is Asset; the read switch's opening
        account is Equity -- proving a per-loan Equity row satisfies the
        columns-only ``ck_ledger_accounts_loan_shape`` CHECK (which is
        class-agnostic) and takes its own slot under the unique.  A single
        commit of all four raising no IntegrityError is the proof.
        """
        with app.app_context():
            loan = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Loan Four Kinds",
            )
            _db.session.add(LedgerAccount(
                user_id=loan.user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.LOAN_INTEREST),
                loan_account_id=loan.id, name="Mortgage Interest",
            ))
            _db.session.add(LedgerAccount(
                user_id=loan.user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.LOAN_ESCROW),
                loan_account_id=loan.id, name="Mortgage Escrow",
            ))
            _db.session.add(LedgerAccount(
                user_id=loan.user_id,
                class_id=_class_id(LedgerAccountClassEnum.ASSET),
                kind_id=_kind_id(LedgerAccountKindEnum.LOAN_REFUND),
                loan_account_id=loan.id, name="Mortgage Refund",
            ))
            _db.session.add(LedgerAccount(
                user_id=loan.user_id,
                class_id=_class_id(LedgerAccountClassEnum.EQUITY),
                kind_id=_kind_id(LedgerAccountKindEnum.EQUITY_OPENING),
                loan_account_id=loan.id, name="Mortgage Opening",
            ))
            _db.session.commit()
            assert (
                _db.session.query(LedgerAccount)
                .filter_by(loan_account_id=loan.id)
                .count() == 4
            )
