"""The mark-as-credit workflow: a payback in the next period leaving the balance unaffected, and
its edge cases (the payback's amount, a row already on credit, done or cancelled, the last
period with no next one, unmarking removing the payback, an income row refused).
"""

from decimal import Decimal

import pytest

from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services import credit_workflow
from app.services import balance_at
from app.services.balance_at import BalanceContext
from tests._test_helpers import one_off_row_of
from app.services.row_valuation import settled_contribution


class TestCreditPaybackBalance:
    """Credit workflow → payback in next period → balance unaffected."""

    def test_credit_creates_payback_and_zeroes_effective(self, app, db, seed_user, seed_periods):
        """Marking as credit creates payback and makes original effective_amount 0."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Dinner Out",
                amount=Decimal("75.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            # Mark as credit -- creates payback in next period.
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.commit()

            db.session.refresh(txn)

            # Original transaction is credit status → effective_amount is 0.
            assert txn.status.name == "Credit"
            assert settled_contribution(txn) == Decimal("0")

            # Payback exists in next period with matching amount.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("75.00")
            assert payback.credit_payback_for_id == txn.id

            # Balance: the Credit txn contributes 0, the payback the full
            # amount.  Read through the seam (plan step X-g4b) rather than by
            # handing a producer a hand-loaded row list -- the fold loads the
            # account's own rows, which is what makes the Credit exclusion a
            # property of the PRODUCER here rather than of this query.
            balances = balance_at.cash_balance_map(
                seed_user["account"],
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )
            # Period 0: credit txn → $0 effect, balance stays 1000.
            assert balances[seed_periods[0].id] == Decimal("1000.00")
            # Period 1: payback expense of $75 → balance drops.
            assert balances[seed_periods[1].id] == Decimal("925.00")


# ── Credit Workflow Tests ────────────────────────────────────────────


class TestCreditWorkflowEdgeCases:
    """Edge-case scenarios for the mark-as-credit workflow."""

    def test_mark_as_credit_creates_payback_with_correct_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """Marking as credit creates a payback in the next period with exact amount.

        Verifies the payback transaction's name, amount, status, and
        credit_payback_for_id link back to the original.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Grocery Run",
                amount=Decimal("75.43"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()
            txn_id = txn.id

            payback = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()

            db.session.expire_all()
            txn = db.session.get(Transaction, txn_id)

            # Original transaction status changed to credit.
            assert txn.status.name == "Credit"
            assert settled_contribution(txn) == Decimal("0")

            # Payback transaction created in next period.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("75.43")
            assert payback.status.name == "Projected"
            assert payback.credit_payback_for_id == txn_id
            assert payback.name == "CC Payback: Grocery Run"

    def test_mark_as_credit_on_already_credit_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """Marking as credit twice is idempotent -- returns existing payback.

        The source code has an idempotency guard: if already credit with
        an existing payback, it returns that payback instead of creating
        a second one.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Double Credit Test",
                amount=Decimal("50.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()
            txn_id = txn.id

            # First mark-as-credit.
            payback1 = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()
            payback1_id = payback1.id

            # Second mark-as-credit -- idempotent, returns same payback.
            payback2 = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()

            assert payback2.id == payback1_id

            # Exactly 1 payback exists.
            db.session.expire_all()
            payback_count = db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn_id,
            ).filter(Transaction.is_deleted.is_(False)).count()
            assert payback_count == 1

    def test_mark_as_credit_on_done_transaction(self, app, db, seed_user, seed_periods):
        """Cannot mark a 'done' transaction as credit -- raises ValidationError.

        Only projected transactions can be newly marked as credit. The
        source code raises ValidationError for any non-projected status.
        """
        with app.app_context():
            done = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Already Paid",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            txn.status_id = done.id
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="Cannot mark a 'Paid' transaction"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_as_credit_on_cancelled_transaction(self, app, db, seed_user, seed_periods):
        """Cannot mark a 'Cancelled' transaction as credit -- raises ValidationError.

        Cancelled transactions should not generate payback entries.
        """
        with app.app_context():
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Cancelled Order",
                amount=Decimal("60.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            txn.status_id = cancelled.id
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="Cannot mark a 'Cancelled' transaction"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_as_credit_last_period_no_next_period(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit on last period raises ValidationError -- no next period for payback.

        The source calls pay_period_service.get_next_period() which returns
        None for the last period. The source then raises ValidationError.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            last_period = seed_periods[-1]
            txn = one_off_row_of(
                last_period,
                name="Last Period Purchase",
                amount=Decimal("99.99"),
                user_id=last_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="No next pay period"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_unmark_credit_removes_payback(self, app, db, seed_user, seed_periods):
        """Unmarking credit reverts status to projected and deletes the payback.

        After unmark, the original transaction is 'projected' again and
        the auto-generated payback transaction no longer exists.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Unmark Test",
                amount=Decimal("45.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()
            txn_id = txn.id

            # Mark as credit → creates payback.
            payback = credit_workflow.mark_as_credit(txn_id, seed_user["user"].id)
            db.session.commit()
            payback_id = payback.id

            # Verify payback exists.
            db.session.expire_all()
            assert db.session.get(Transaction, payback_id) is not None

            # Unmark credit.
            credit_workflow.unmark_credit(txn_id, seed_user["user"].id)
            db.session.commit()

            # Verify original reverted to projected.
            db.session.expire_all()
            txn = db.session.get(Transaction, txn_id)
            assert txn.status.name == "Projected"

            # Verify payback is deleted.
            payback_check = db.session.get(Transaction, payback_id)
            assert payback_check is None

    def test_mark_as_credit_income_raises_validation_error(
        self, app, db, seed_user, seed_periods,
    ):
        """Cannot mark income as credit -- raises ValidationError.

        Marking a paycheck as 'credit' makes no financial sense.
        """
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Paycheck",
                amount=Decimal("2500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            db.session.commit()

            from app.exceptions import ValidationError as ShekelValidationError
            with pytest.raises(ShekelValidationError, match="Cannot mark income as credit"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
