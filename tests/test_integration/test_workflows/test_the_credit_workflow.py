"""The mark-as-credit workflow: the row on the card and the card's payoff row paying it next
paycheck, leaving checking unaffected now, and its edge cases (a row already on credit, done or
cancelled, unmarking removing an old payback, an income row refused).

**Since plan task plan#2929 the press writes no payback**: it puts the row on the card (rulings
plan#2887, plan#2888), graded in ``tests/test_services/test_credit_puts_the_row_on_the_card.py``,
and since plan task plan#2933 the card is made with its payoff row, which pays it.  The first
test presses Credit and reads checking to the cent as it read when the press wrote a payback
(ruling plan#2888).  The edge cases of an OLD Credit row plant its payback shape
(``tests._legacy_credit``): 21 rows on production still hold one, and keep its path until
plan#1034's re-plan.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import credit_workflow, status_seam, transaction_service
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import resolve_transfer_amount
from tests._cards import add_card_with_its_payoff
from tests._legacy_credit import plant_old_credit
from tests._test_helpers import one_off_row_of


class TestCreditPaybackBalance:
    """Credit → the card's payoff row pays it next paycheck → checking unaffected now."""

    def test_credit_is_paid_off_next_paycheck_and_zeroes_effective(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit puts the $75.00 on the card, and the card's payoff row pays it next paycheck.

        Named ``test_credit_creates_payback_and_zeroes_effective`` while the
        press wrote a CC Payback row (until plan task plan#2929).  The card is
        added on Jan 2, the first payday, with its one payment (plan task
        plan#2933): a payoff row each payday paying what the card owes the day
        before.  Dinner Out has no date, so Credit puts it on the card on its
        paycheck's first day, Jan 2 (``credit_day``).  Checking reads as it read
        with the payback (ruling plan#2888, to the cent):

        * Jan 2's payoff row pays what the card owed at the end of Jan 1, $0.00,
          so period 0 is 1,000.00 - 0.00 = $1,000.00;
        * Jan 16's pays what it owed at the end of Jan 15, the $75.00, so
          period 1 is 1,000.00 - 75.00 = $925.00.
        """
        with app.app_context():
            card = add_card_with_its_payoff(
                seed_user, added_on=seed_periods[0].start_date,
            )
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

            transaction_service.mark_as_credit(txn)
            db.session.commit()

            db.session.refresh(txn)

            # Drawn as Credit, its $75.00 moved on the card and none on checking.
            assert status_seam.drawn_status_id(txn) == ref_cache.status_id(
                StatusEnum.CREDIT,
            )
            assert [(m.account_id, m.amount) for m in txn.entries] == [
                (card.id, Decimal("75.00")),
            ]
            assert credit_workflow.get_active_payback(txn.id) is None

            # The payoff row pays nothing in period 0 and the $75.00 in period 1.
            ctx = BalanceContext.build(
                seed_user["user"].id, as_of=seed_periods[0].start_date,
            )
            template = db.session.query(TransferTemplate).filter_by(
                to_account_id=card.id,
            ).one()
            payoff = {
                row.pay_period_id: resolve_transfer_amount(row, ctx.amounts())
                for row in db.session.query(Transfer).filter_by(
                    transfer_template_id=template.id,
                )
            }
            assert payoff[seed_periods[0].id] == Decimal("0.00")
            assert payoff[seed_periods[1].id] == Decimal("75.00")

            # Balance: read through the seam (plan step X-g4b), so the fold
            # loads the account's own rows and the payoff row's legs.
            balances = balance_at.cash_balance_map(seed_user["account"], ctx)
            # Period 0: the row is on the card, so checking stays 1000.
            assert balances[seed_periods[0].id] == Decimal("1000.00")
            # Period 1: the payoff row of $75 leaves checking.
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
