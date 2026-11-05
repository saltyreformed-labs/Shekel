"""Regression tests for the non-negative figure guarantees on a transaction.

Locks the guarantee that a row's estimated_amount and its settled figure
cannot hold negative values.  The plan's guard is the storage tier:
`ck_transactions_estimated_amount`, declared on the model
(`app/models/transaction.py`) and materialised by migration
`dc46e02d15b4_add_check_constraints_to_loan_params_.py`.  The RECORD's
guard is the constructor: `status_seam.Settlement.__post_init__` refuses a
negative figure, because the record lives on the covering movement since
plan step `balance:X-bi-4b-2` (migration `45f10b870c8b`, ruling R-BAL80),
whose own CHECK is `amount <> 0` (a merchant credit is a negative PURCHASE,
ruling bank_import:R-II), so the storage tier cannot say `>= 0` for it.
Through `X-bi-4b-1` the row's own `settled_amount` carried
`ck_transactions_settled_amount` (materialised by the same migration, renamed
with its column by `e4b8a71c0f36_settlement_record.py`), and the second and
third cases here graded that column; they grade the constructor and the
`$0.00` record now.

The original H-1 drift fix
(`migrations/versions/724d21236759_drop_redundant_transaction_check_.py`)
removed an older duplicate pair (`ck_transactions_positive_amount` /
`ck_transactions_positive_actual`) that the model never declared but
that the migration chain materialised under different names.  These
tests are the contract that the surviving constraints continue to
catch negative amounts -- if a future migration accidentally drops
both pairs, the test suite turns red here instead of letting a
negative-amount Transaction slip past the storage tier and into
balance projections.

Audit reference: H-1 of
shekel-audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import MovementFigureSourceEnum
from app.extensions import db
from app.models.ref import Status, TransactionType
from app.services import status_seam
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    repriced_by_the_owner,
    settle_day_columns,
)


def _placed_row(seed_user, seed_periods_today, status_name="Projected"):
    """Place an ordinary one-off in the first paycheck, *status_name* laid on BARE.

    The producer's row (plan step balance:X-bi-7c).  The row's plan figure
    is its definition's and never on the row, which is why the
    negative-estimate case takes the OWN arm instead (see it); the
    ``$0.00``-record case lays a settled status and a day on it.

    Args:
        seed_user: The seeded owner fixture.
        seed_periods_today: The seeded pay periods; the row lands in the first.
        status_name: The ``ref.statuses`` name to lay on the placed row.  A
            test that writes a settlement record must pass a SETTLED one,
            because a record on a row whose money has not moved is the state
            the write door refuses.

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    txn = one_off_row_of(
        seed_periods_today[0],
        name="Constraint Test",
        amount=Decimal("100.00"),
        user_id=seed_periods_today[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    txn.status_id = status.id
    return txn


class TestTransactionAmountCheckConstraints:
    """A negative plan is refused at flush; a negative record at construction."""

    def test_negative_estimated_amount_rejected(
        self, app, db, seed_user, seed_periods_today
    ):
        """Writing a row that OWNS estimated_amount < 0 raises IntegrityError.

        The ck_transactions_estimated_amount CHECK constraint pins
        storage to non-negative values.  Without the constraint, a
        negative-amount transaction would corrupt every balance
        projection that touched the period.

        The row that STORES a figure is the OWNER's: a recurring
        definition's row the owner re-priced (X-cf's idiom, ruling R-BAL17;
        ``repriced_by_the_owner`` flushes the two acts).  A one-off's figure
        is its definition's and never on the row (R-BAL29), so this CHECK
        binds on the OWN arm alone (plan step balance:X-bi-7c).
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Constraint Test",
                category_key="Groceries",
            )
            row = generate_row_of(template, seed_periods_today[0])
            with pytest.raises(IntegrityError) as exc_info:
                repriced_by_the_owner(row, "-1.00")
            assert "ck_transactions_estimated_amount" in str(exc_info.value)
            db.session.rollback()

    def test_a_negative_settled_figure_is_refused_at_construction(self, app):
        """``Settlement`` refuses a figure below zero before any row is reached.

        A settle verb takes a MAGNITUDE (``StatedFigure``), and every form
        field that feeds one validates non-negative; this is the one home of
        the rule since plan step ``balance:X-bi-4b-2`` deleted the row's own
        ``settled_amount`` with its CHECK.  A negative recorded figure would
        corrupt the balance the same way a negative estimate would, and the
        movement's own CHECK (``amount <> 0``) cannot say so for it.
        """
        with app.app_context():
            with pytest.raises(ValueError, match="negative figure"):
                status_seam.Settlement(
                    Decimal("-1.00"), MovementFigureSourceEnum.TYPED,
                )
            with pytest.raises(ValueError, match="negative figure"):
                status_seam.Settlement(
                    Decimal("-0.01"), MovementFigureSourceEnum.RESOLVED,
                )
            # Zero is a magnitude: the close of nothing (ruling R-BAL82).
            assert status_seam.Settlement(
                Decimal("0.00"), MovementFigureSourceEnum.TYPED,
            ).amount == Decimal("0.00")

    def test_a_settled_row_with_no_movement_is_the_zero_record(
        self, app, db, seed_user, seed_periods_today
    ):
        """A settled row holding no entry records ``$0.00``, and storage admits it.

        Through plan step X-au-c3 the CHECK's NULL branch admitted the
        ``purchases`` record, the one settled shape that stored no figure;
        since ``balance:X-bi-4b-2`` no settled row stores one, and a settled
        row with no entries at all IS the ``$0.00`` record (ruling R-BAL82)
        -- a routine state (an empty envelope closed, a bill budgeted at
        nothing marked paid) the storage tier must keep admitting.
        """
        with app.app_context():
            txn = _placed_row(seed_user, seed_periods_today, status_name="Paid")
            for column, value in settle_day_columns(
                seed_periods_today[0].start_date,
            ).items():
                setattr(txn, column, value)
            db.session.flush()
            db.session.refresh(txn)
            assert txn.entries == []
            assert settled_figure(txn) == Decimal("0.00")
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                None, None,
            )
            db.session.rollback()
