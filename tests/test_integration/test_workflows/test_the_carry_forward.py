"""Carry forward: projected items moved to the target period, and its edge cases (amounts kept
exact, a target that already holds rows, rows that are not projected left behind, an empty
source, a source that is the target).
"""

from decimal import Decimal

from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services import carry_forward_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import one_off_row_of, resolved_amount


class TestCarryForwardWorkflow:
    """Carry forward → projected items moved to target period."""

    def test_carry_forward_moves_projected_items(self, app, db, seed_user, seed_periods):
        """carry_forward_unpaid moves projected transactions from source to target.

        Verifies that moved transactions retain their estimated_amount,
        status, name, category_id, and transaction_type_id exactly.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            groceries_cat_id = seed_user["categories"]["Groceries"].id

            # Create 2 projected expenses in period 0.
            for name, amount in [("Groceries", "85.00"), ("Gas", "45.00")]:
                one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=Decimal(amount),
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=groceries_cat_id,
                )
            db.session.commit()

            # Carry forward from period 0 → period 1.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()

            assert count == 2

            # Period 0 should have no projected transactions.
            db.session.expire_all()
            remaining = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(
                Transaction.status.has(name="Projected"),
                Transaction.is_deleted.is_(False),
            ).count()
            assert remaining == 0

            # Period 1 should now have the 2 moved transactions.
            moved = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(moved) == 2

            # Verify every moved transaction retains original values.
            by_name = {t.name: t for t in moved}
            assert resolved_amount(by_name["Groceries"]) == Decimal("85.00")
            assert by_name["Groceries"].pay_period_id == seed_periods[1].id
            assert by_name["Groceries"].status.name == "Projected"
            assert by_name["Groceries"].category_id == groceries_cat_id
            assert by_name["Groceries"].transaction_type_id == expense_type.id

            assert resolved_amount(by_name["Gas"]) == Decimal("45.00")
            assert by_name["Gas"].pay_period_id == seed_periods[1].id
            assert by_name["Gas"].status.name == "Projected"
            assert by_name["Gas"].category_id == groceries_cat_id
            assert by_name["Gas"].transaction_type_id == expense_type.id


# ── Additional Carry Forward Tests ───────────────────────────────────


class TestCarryForwardEdgeCases:
    """Edge-case scenarios for carry-forward operations."""

    def test_carry_forward_preserves_amounts_exact(self, app, db, seed_user, seed_periods):
        """Three transactions with distinct amounts retain exact values after move.

        Ensures carry-forward does not round, truncate, or modify
        estimated_amount, name, category_id, or transaction_type_id.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            groceries_cat_id = seed_user["categories"]["Groceries"].id
            rent_cat_id = seed_user["categories"]["Rent"].id

            items = [
                ("Electric", Decimal("850.00"), rent_cat_id),
                ("Snacks", Decimal("125.50"), groceries_cat_id),
                ("Coffee", Decimal("43.99"), groceries_cat_id),
            ]
            for name, amount, cat_id in items:
                one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=amount,
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=cat_id,
                )
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 3

            db.session.expire_all()
            moved = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(moved) == 3
            by_name = {t.name: t for t in moved}

            for name, amount, cat_id in items:
                assert resolved_amount(by_name[name]) == amount
                assert by_name[name].pay_period_id == seed_periods[1].id
                assert by_name[name].status.name == "Projected"
                assert by_name[name].category_id == cat_id
                assert by_name[name].transaction_type_id == expense_type.id

            # Source period has 0 projected transactions remaining.
            source_count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(
                Transaction.status.has(name="Projected"),
                Transaction.is_deleted.is_(False),
            ).count()
            assert source_count == 0

    def test_carry_forward_target_has_existing_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Carry forward into a period that already has transactions.

        Existing 'done' transactions in the target period must be unmodified.
        Carried-forward 'projected' transactions are appended, not merged.
        """
        with app.app_context():
            done = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create 2 existing 'done' transactions in the TARGET period.
            existing_ids = []
            for name, amount in [("Internet", Decimal("500.00")), ("Phone", Decimal("200.00"))]:
                txn = one_off_row_of(
                    seed_periods[1],
                    name=name,
                    amount=amount,
                    user_id=seed_periods[1].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=seed_user["categories"]["Rent"].id,
                )
                txn.status_id = done.id
                db.session.flush()
                existing_ids.append(txn.id)

            # Create 3 projected transactions in the SOURCE period.
            for name, amount in [
                ("Electric", Decimal("850.00")),
                ("Snacks", Decimal("125.50")),
                ("Coffee", Decimal("43.99")),
            ]:
                txn = one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=amount,
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=seed_user["categories"]["Groceries"].id,
                )
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 3

            db.session.expire_all()

            # Target period now has exactly 5 transactions (2 existing + 3 moved).
            target_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(target_txns) == 5

            # Original 'done' transactions are unmodified.
            for eid in existing_ids:
                orig = db.session.get(Transaction, eid)
                assert orig.status.name == "Paid"
                assert orig.pay_period_id == seed_periods[1].id

            # Verify carried-forward items have correct amounts.
            carried = [t for t in target_txns if t.status.name == "Projected"]
            assert len(carried) == 3
            carried_amounts = sorted(resolved_amount(t) for t in carried)
            assert carried_amounts == sorted([
                Decimal("850.00"), Decimal("125.50"), Decimal("43.99"),
            ])

    def test_carry_forward_skips_non_projected_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Only 'projected' transactions move; done/cancelled/credit stay.

        Creates mixed-status transactions in the source period and verifies
        that only projected items are carried forward.
        """
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            done = db.session.query(Status).filter_by(name="Paid").one()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            credit = db.session.query(Status).filter_by(name="Credit").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # 2 projected (should move), 1 done, 1 cancelled, 1 credit (should stay).
            status_names = [
                ("Move1", Decimal("100.00"), projected),
                ("Move2", Decimal("200.00"), projected),
                ("Paid", Decimal("300.00"), done),
                ("Dropped", Decimal("50.00"), cancelled),
                ("OnCard", Decimal("75.00"), credit),
            ]
            for name, amount, status in status_names:
                txn = one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=amount,
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=seed_user["categories"]["Groceries"].id,
                )
                txn.status_id = status.id
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 2

            db.session.expire_all()

            # Target period gained exactly 2 transactions.
            target_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(target_txns) == 2
            assert {t.name for t in target_txns} == {"Move1", "Move2"}

            # Source period still has 3 non-projected transactions.
            source_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(source_txns) == 3
            assert {t.name for t in source_txns} == {"Paid", "Dropped", "OnCard"}

    def test_carry_forward_empty_source_period(self, app, db, seed_user, seed_periods):
        """Carry forward from a period with no projected transactions is a no-op.

        Source has 1 done and 1 cancelled -- nothing to move. Target is unchanged.
        """
        with app.app_context():
            done = db.session.query(Status).filter_by(name="Paid").one()
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Source has only non-projected transactions.
            for name, status in [("Paid", done), ("Nope", cancelled)]:
                txn = one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=Decimal("100.00"),
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=seed_user["categories"]["Groceries"].id,
                )
                txn.status_id = status.id
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 0

            db.session.expire_all()

            # Target has no transactions.
            target_count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).count()
            assert target_count == 0

            # Source unchanged.
            source_count = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(Transaction.is_deleted.is_(False)).count()
            assert source_count == 2

    def test_carry_forward_source_equals_target_period(
        self, app, db, seed_user, seed_periods,
    ):
        """Carry forward with source == target returns 0 (early guard in source).

        The carry_forward_service checks `if source_period_id == target_period_id`
        and returns 0 immediately. No transactions are duplicated.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            one_off_row_of(
                seed_periods[0],
                name="SelfCarry",
                amount=Decimal("99.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[0].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 0

            # Transaction is still in period 0 -- nothing changed.
            db.session.expire_all()
            txn_check = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id, name="SelfCarry",
            ).filter(Transaction.is_deleted.is_(False)).one()
            assert resolved_amount(txn_check) == Decimal("99.00")
            assert txn_check.status.name == "Projected"
