"""The mark-as-credit workflow: a payback in the next period leaving the balance unaffected, and
its edge cases (a row already on credit, done or cancelled, unmarking removing the payback, an
income row refused).

**Since plan task plan#2929 the press writes no payback**: it puts the row on the card (rulings
plan#2887, plan#2888), graded in ``tests/test_services/test_credit_puts_the_row_on_the_card.py``.
The payback shape here is an OLD Credit row's, planted (``tests._legacy_credit``), and its
balance is the one the new press must reproduce to the cent once the card's payoff row repays
it (plan#2933, which puts the press back in this file's first test).  The payback's own figure
and the last paycheck's refusal went with the payback.
"""

from decimal import Decimal

import pytest

from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services import credit_workflow, transaction_service
from app.services import balance_at
from app.services.balance_at import BalanceContext
from tests._legacy_credit import plant_old_credit
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

            # The old Credit shape -- a payback in the next period.
            payback = plant_old_credit(txn)
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

    def test_mark_as_credit_on_already_credit_transaction(
        self, app, db, seed_user, seed_periods,
    ):
        """A press on an old Credit row is idempotent -- its payback stays the one.

        The old Credit row is planted (``tests._legacy_credit``); the press
        changes nothing on it, so no second payback appears and the row is
        not put on the card as well (plan task plan#2929).
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

            # The old Credit shape.
            payback1 = plant_old_credit(txn)
            db.session.commit()
            payback1_id = payback1.id

            # The press on it -- idempotent, nothing written.
            transaction_service.mark_as_credit(db.session.get(Transaction, txn_id))
            db.session.commit()

            assert credit_workflow.get_active_payback(txn_id).id == payback1_id
            assert not db.session.get(Transaction, txn_id).entries

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
                transaction_service.mark_as_credit(txn)

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
                transaction_service.mark_as_credit(txn)

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
            payback = plant_old_credit(db.session.get(Transaction, txn_id))
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
                transaction_service.mark_as_credit(txn)
