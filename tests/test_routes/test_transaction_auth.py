"""
Shekel Budget App -- Transaction Ownership Authorization Tests

Verifies that transaction routes enforce user ownership:
an authenticated user cannot read, modify, or delete another
user's transactions.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.ref import AccountType, TransactionType
from app.services.auth_service import hash_password
from app.services import pay_period_write
from app.services import account_service
from tests._test_helpers import one_off_row_of, pay_periods_hydrated, resolved_amount, rhythm_of


def _create_other_user_with_txn(seed_user, seed_periods_today):
    """Create a second user with their own period and transaction.

    Returns:
        dict with keys: user, period, transaction, scenario, category,
        account.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    # E-19 (Commit 3): pay periods must exist before the account so
    # the NOT NULL anchor columns can be populated at construction.
    other_periods = pay_period_write.record_paydays(
        user_id=other_user.id,
        first_payday=date(2026, 1, 2),
        num_periods=3,
        rhythm=rhythm_of(14),
    )
    db.session.flush()

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    category = Category(
        user_id=other_user.id,
        group_name="Home",
        item_name="Rent",
    )
    db.session.add(category)
    db.session.flush()

    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

    txn = one_off_row_of(
        other_periods[0],
        name="Other User Rent",
        amount=Decimal("1500.00"),
        user_id=other_periods[0].user_id,
        account_id=account.id,
        scenario_id=scenario.id,
        transaction_type_id=expense_type.id,
        category_id=category.id,
    )
    db.session.commit()

    return {
        "user": other_user,
        "period": other_periods[0],
        "transaction": txn,
        "scenario": scenario,
        "category": category,
        # The account is the fourth user-scoped id the cell fragments read and
        # the one no ownership test named until plan step C2-f3e.
        "account": account,
    }


class TestTransactionOwnership:
    """Verify that all transaction routes reject access to other users' data."""

    def test_get_cell_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/<id>/cell returns 404 for another user's txn
        and does not leak the victim's transaction data."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/cell")
            assert resp.status_code == 404
            assert b"Other User Rent" not in resp.data
            assert b"1500.00" not in resp.data

    def test_quick_edit_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/<id>/quick-edit returns 404 for another user's txn
        and does not leak the victim's transaction data."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/quick-edit")
            assert resp.status_code == 404
            assert b"Other User Rent" not in resp.data
            assert b"1500.00" not in resp.data

    def test_full_edit_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """GET /transactions/<id>/full-edit returns 404 for another user's txn
        and does not leak the victim's transaction data."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.get(f"/transactions/{other['transaction'].id}/full-edit")
            assert resp.status_code == 404
            assert b"Other User Rent" not in resp.data
            assert b"1500.00" not in resp.data

    def test_update_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """PATCH /transactions/<id> returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.patch(
                f"/transactions/{other['transaction'].id}",
                data={"estimated_amount": "0.01"},
            )
            assert resp.status_code == 404

            # Verify the transaction was NOT modified.
            db.session.refresh(other["transaction"])
            # Unchanged, read through the resolver: the row is a ONE-OFF (plan step
            # balance:X-bi-7c), priced by its definition, so the raw column is None
            # and the app's resolver is the reader (ruling R-BAL60).
            assert resolved_amount(other["transaction"]) == Decimal("1500.00")

    def test_mark_done_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-done returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.post(f"/transactions/{other['transaction'].id}/mark-done")
            assert resp.status_code == 404

            # Verify status unchanged.
            db.session.refresh(other["transaction"])
            assert other["transaction"].status.name == "Projected"

    def test_mark_credit_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/mark-credit returns 404 for another
        user's txn and leaves the transaction status unchanged."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            txn_id = other["transaction"].id
            resp = auth_client.post(f"/transactions/{txn_id}/mark-credit")
            assert resp.status_code == 404

            # Verify status unchanged and no payback transaction created.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == "Projected", (
                "IDOR attack changed transaction status!"
            )
            # Credit workflow creates a payback txn; verify none exist.
            payback = (
                db.session.query(Transaction)
                .filter_by(
                    pay_period_id=txn_after.pay_period_id,
                    name="Other User Rent (payback)",
                )
                .first()
            )
            assert payback is None, (
                "IDOR attack created a payback transaction!"
            )

    def test_cancel_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/<id>/cancel returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.post(f"/transactions/{other['transaction'].id}/cancel")
            assert resp.status_code == 404

            db.session.refresh(other["transaction"])
            assert other["transaction"].status.name == "Projected"

    def test_delete_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id> returns 404 for another user's txn."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            txn_id = other["transaction"].id
            resp = auth_client.delete(f"/transactions/{txn_id}")
            assert resp.status_code == 404

            # Verify the transaction still exists and is not deleted.
            txn = db.session.get(Transaction, txn_id)
            assert txn is not None
            assert txn.is_deleted is False

    def test_unmark_credit_blocked(self, app, auth_client, seed_user, seed_periods_today):
        """DELETE /transactions/<id>/unmark-credit returns 404 for another
        user's txn and leaves the transaction status unchanged."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            txn_id = other["transaction"].id
            orig_status = other["transaction"].status.name

            resp = auth_client.delete(
                f"/transactions/{txn_id}/unmark-credit"
            )
            assert resp.status_code == 404

            # Verify status unchanged.
            db.session.expire_all()
            txn_after = db.session.get(Transaction, txn_id)
            assert txn_after.status.name == orig_status, (
                "IDOR attack changed transaction status!"
            )


class TestCreateOwnership:
    """Verify that transaction creation rejects foreign pay_period_id / category_id."""

    def test_inline_create_with_other_users_period(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/inline rejects another user's pay_period_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,  # Other user's period
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_inline_create_with_other_users_category(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions/inline rejects another user's category_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": other["category"].id,  # Other user's category
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_create_with_other_users_period(self, app, auth_client, seed_user, seed_periods_today):
        """POST /transactions rejects another user's pay_period_id."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky Expense",
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,  # Other user's period
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_create_with_other_users_category(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions rejects another user's category_id.

        ``category_id`` is required on TransactionCreateSchema and is
        persisted via ``Transaction(**data)``, so the ad-hoc create must
        ownership-check it (mirroring create_inline): a foreign category
        otherwise satisfies the FK constraint (the row exists) and links
        the victim's category onto the attacker's transaction.
        """
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky Category",
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": other["category"].id,  # Other user's category
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

            # No transaction should have been created referencing the
            # other user's category.
            txn = db.session.query(Transaction).filter_by(
                name="Sneaky Category"
            ).first()
            assert txn is None

    def test_create_with_other_users_scenario_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions with another user's scenario_id returns 404."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Sneaky Scenario",
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": other["scenario"].id,  # Other user's scenario
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

            # No transaction should have been created.
            txn = db.session.query(Transaction).filter_by(
                name="Sneaky Scenario"
            ).first()
            assert txn is None

    def test_inline_create_with_other_users_scenario_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/inline with another user's scenario_id returns 404."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "75.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "scenario_id": other["scenario"].id,  # Other user's scenario
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

            # No transaction should have been created under the other
            # user's scenario.
            txn = db.session.query(Transaction).filter_by(
                scenario_id=other["scenario"].id,
                estimated_amount=Decimal("75.00"),
            ).first()
            assert txn is None

    def test_create_with_nonexistent_scenario_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions with nonexistent scenario_id returns 404."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Ghost Scenario",
                "estimated_amount": "50.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": 999999,  # Nonexistent
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404

    def test_create_with_nonexistent_pay_period_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions with nonexistent pay_period_id returns 404."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Ghost Period",
                "estimated_amount": "100.00",
                "pay_period_id": 999999,
                "scenario_id": seed_user["scenario"].id,
                "category_id": seed_user["categories"]["Groceries"].id,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            # Verify no transaction was created.
            count = db.session.query(Transaction).filter_by(
                name="Ghost Period"
            ).count()
            assert count == 0

    def test_create_with_nonexistent_category_id(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions with nonexistent category_id returns 404.

        Behaviour corrected alongside the create_transaction category
        ownership fix: category_id is now resolved through the same
        ``_resolve_owned_fks`` IDOR probe as the other user-scoped FKs, so
        a nonexistent (or foreign) category returns 404 -- the security
        rule's "404 for both not-found and not-yours" -- consistent with
        the scenario_id / pay_period_id / account_id cases above and with
        create_inline.  (Previously it fell through to the IntegrityError
        handler and returned 400, a side effect of the missing check.)
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions", data={
                "name": "Ghost Category",
                "estimated_amount": "100.00",
                "pay_period_id": seed_periods_today[0].id,
                "scenario_id": seed_user["scenario"].id,
                "category_id": 999999,
                "transaction_type_id": expense_type.id,
                "account_id": str(seed_user["account"].id),
            })
            assert resp.status_code == 404
            assert b"Category not found" in resp.data

            count = db.session.query(Transaction).filter_by(
                name="Ghost Category"
            ).count()
            assert count == 0

    def test_inline_create_with_nonexistent_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """POST /transactions/inline with nonexistent pay_period_id returns 404."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()

            resp = auth_client.post("/transactions/inline", data={
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": 999999,
                "transaction_type_id": expense_type.id,
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            })
            # Inline route checks category first (passes), then period (404).
            assert resp.status_code == 404

            count = db.session.query(Transaction).filter_by(
                pay_period_id=999999
            ).count()
            assert count == 0


class TestFormRenderingOwnership:
    """Verify form-rendering GET endpoints reject other users' resources.

    These endpoints load Category and PayPeriod by ID from query params.
    Without ownership checks, an attacker could enumerate IDs to discover
    another user's category names and pay period dates.
    """

    def test_quick_create_rejects_other_users_category(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/quick returns 404 for another user's category."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": other["category"].id,
                "period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_quick_create_rejects_other_users_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/quick returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_quick_create_rejects_mixed_ownership(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/quick returns 404 when both resources are foreign."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": other["category"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_full_create_rejects_other_users_category(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/full returns 404 for another user's category."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/full", query_string={
                "category_id": other["category"].id,
                "period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_full_create_rejects_other_users_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/full returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/full", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_empty_cell_rejects_other_users_category(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/empty-cell returns 404 for another user's category."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/empty-cell", query_string={
                "category_id": other["category"].id,
                "period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_empty_cell_rejects_other_users_period(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/empty-cell returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/empty-cell", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": other["period"].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 404

    def test_quick_create_allows_own_resources(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/quick returns 200 for the user's own resources."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/quick", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 200

    def test_full_create_allows_own_resources(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/new/full returns 200 for the user's own resources."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/new/full", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 200

    def test_empty_cell_allows_own_resources(
        self, app, auth_client, seed_user, seed_periods_today
    ):
        """GET /transactions/empty-cell returns 200 for the user's own resources."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            resp = auth_client.get("/transactions/empty-cell", query_string={
                "category_id": seed_user["categories"]["Groceries"].id,
                "period_id": seed_periods_today[0].id,
                "transaction_type_id": expense_type.id,
                "account_id": seed_user["account"].id,
            })
            assert resp.status_code == 200


class TestCellFragmentsResolveOwnershipStructurally:
    """The three empty-cell fragments prove ownership without loading a row.

    Plan step **C2-f3e**, closing ledger row **P51**'s second half.  All
    three answered a submitted ``period_id`` by fetching
    ``budget.pay_periods`` by primary key and comparing ``row.user_id``
    against the requester -- correct, but a guard a later edit can drop with
    no test able to see the difference.  They ask the owner's derived pay
    calendar now: it holds ONE owner's whole schedule and nothing else, so
    another user's id is simply ABSENT and there is no comparison left to
    forget.  Same shape as ``grid.partials.mobile_this_period_summary``
    (C2-f2b) and ``transactions._resolve_carry_forward_context`` (C2-f3c).

    ``TestFormRenderingOwnership`` above still owns the three foreign-period
    refusals themselves; what is asserted here is the axes it left open -- an
    absent id, the identity of the three refusals' responses, and the account
    probe none of them covered -- plus the ONE structural property that says
    the check is the calendar: no ORM row is loaded.

    **"Derives the calendar exactly once" is asserted in the architecture
    gate, not here**, and a first draft of this class had its own copy.
    ``tests/_test_helpers.counting_calls`` is by its own docstring "the ONE
    instrument" for that question and
    ``test_one_read_pass_per_render.TestOneCalendarDerivationPerRender`` is
    where every other render is graded; the copy counted raw SQL text instead,
    which an adversarial review measured would break the moment plan step
    ``balance:X-x3`` -- ready NOW -- rewrites a neighbouring query to mention
    ``start_date``.

    **The property below is TRUE of an owner holding a ``budget.pay_schedule``
    row, which is every owner a live door has created since plan step
    X-ad-a.** A legacy owner without one (plan finding **P8**) makes
    ``pay_schedule_service.resolve_cadence`` fall back to
    ``db.session.query(PayPeriod)...first()``, a full entity load -- so these
    fragments hydrate one for such an owner, measured, and that read is C4's
    to delete with the column it orders by.  Stated because the guard would
    otherwise read as unconditional.
    """

    ENDPOINTS = (
        "/transactions/new/quick",
        "/transactions/new/full",
        "/transactions/empty-cell",
    )

    @staticmethod
    def _own_cell(seed_user, seed_periods_today):
        """Return the query string naming a real cell of the owner's grid."""
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        return {
            "category_id": seed_user["categories"]["Groceries"].id,
            "period_id": seed_periods_today[0].id,
            "transaction_type_id": expense_type.id,
            "account_id": seed_user["account"].id,
        }

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_rejects_other_users_account(
        self, app, auth_client, seed_user, seed_periods_today, endpoint,
    ):
        """A foreign account_id returns 404 on every cell fragment.

        The account is the axis the original H1 tests never probed on these
        three routes -- they covered the category and the period.  It is a
        real read: the account id rides into the create form's hidden fields
        and decides which account the row is booked against.
        """
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            args = self._own_cell(seed_user, seed_periods_today)
            args["account_id"] = other["account"].id
            resp = auth_client.get(endpoint, query_string=args)
            assert resp.status_code == 404

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_rejects_absent_period_id(
        self, app, auth_client, seed_user, seed_periods_today, endpoint,
    ):
        """An omitted period_id returns 404 rather than rendering a blank cell.

        These routes read their ids straight off the query string, so ``None``
        is reachable in a way it is not on the schema-validated create POSTs.
        ``PayCalendar.period_by_id`` answers ``None`` for ``None`` by
        contract, which is the refusal -- and the reason this case is asserted
        rather than assumed is that the alternative renders a form whose
        ``pay_period_id`` is the empty string.
        """
        with app.app_context():
            args = self._own_cell(seed_user, seed_periods_today)
            del args["period_id"]
            resp = auth_client.get(endpoint, query_string=args)
            assert resp.status_code == 404

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_the_three_period_refusals_are_indistinguishable(
        self, app, auth_client, seed_user, seed_periods_today, endpoint,
    ):
        """A foreign, an unknown and an absent period_id answer IDENTICALLY.

        **The security response rule is about the RESPONSE, and every other
        test here checks only the status code.**  "404 for both not found and
        not yours" is a statement that an attacker cannot tell the two apart,
        which a matching status with a differing body does not deliver -- and
        the three ids travel three different code paths to get here (a
        calendar miss on a real row, a calendar miss on no row, and the
        ``None``-in-``None``-out contract), so their agreement is a property
        rather than a tautology.

        A first draft of this case asserted only that an unknown id 404s.  An
        adversarial test-quality review measured that no single-line mutation
        could make it fail on its own -- under the calendar design "belongs to
        nobody" and "belongs to someone else" are the SAME branch -- so it was
        replaced with the assertion that branch is actually there to make.
        """
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            base = self._own_cell(seed_user, seed_periods_today)

            foreign = dict(base, period_id=other["period"].id)
            unknown = dict(base, period_id=999999)
            absent = {k: v for k, v in base.items() if k != "period_id"}

            answers = [
                auth_client.get(endpoint, query_string=args)
                for args in (foreign, unknown, absent)
            ]
            statuses = {r.status_code for r in answers}
            bodies = {r.get_data() for r in answers}
            assert statuses == {404}, statuses
            assert len(bodies) == 1, bodies

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today, endpoint,
    ):
        """No ORM ``PayPeriod`` is loaded while the fragment renders.

        The structural claim itself: an ownership check written as
        ``row.user_id != current_user.id`` needs the ROW, so an empty
        hydration count is the property that cannot hold while the old guard
        exists.  It fails on the pre-C2-f3e routes, which is what makes it a
        firing control rather than a restatement.

        A statement counter could not make this measurement -- a
        ``db.session.get`` served from the identity map issues none -- and
        counting identity-map survivors could not either, because the map is
        weak.  See :func:`tests._test_helpers.pay_periods_hydrated`.

        ``expunge_all`` FIRST, and it is what keeps the probe honest rather
        than a tidy-up: the fixture hands back live ORM ``PayPeriod`` rows, and
        an id already in the identity map is returned by ``session.get``
        without a load, so the event this counts would never fire and the
        guard would pass on the very code it exists to refuse.  It does fire
        without this line today (measured against the pre-step routes), so
        this pins a property the fixture currently supplies by accident.

        See the class docstring for the one owner shape this is NOT true of.
        """
        with app.app_context():
            args = self._own_cell(seed_user, seed_periods_today)
            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.get(endpoint, query_string=args)
            assert resp.status_code == 200
            assert hydrated == [], (
                f"{endpoint} hydrated {len(hydrated)} PayPeriod row(s); it "
                f"resolves the submitted id against the owner's derived "
                f"calendar, which is loaded as a column tuple"
            )


class TestCarryForwardOwnership:
    """Verify carry-forward rejects another user's period."""

    def test_carry_forward_other_users_period(self, app, auth_client, seed_user, seed_periods_today):
        """POST /pay-periods/<id>/carry-forward returns 404 for another user's period."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            resp = auth_client.post(f"/pay-periods/{other['period'].id}/carry-forward")
            assert resp.status_code == 404


class TestWriteDoorsResolveOwnershipStructurally:
    """The WRITE doors prove a submitted paycheck's owner without loading it.

    Plan step **pay_calendar:C13-b**, closing ledger row **P75**'s second
    half.  ``TestCellFragmentsResolveOwnershipStructurally`` above asserts the
    same property for the three READ fragments C2-f3e moved; these are the
    transaction doors it left behind -- the two creates and the PATCH.  Each
    fetched ``budget.pay_periods`` by primary key and compared ``row.user_id``
    against the requester, through
    ``transactions._helpers._resolve_owned_fks``.  The transfer family's three
    are graded the same way in ``test_transfers/test_negative_paths.py``.

    **Why the property is a hydration count and not a status code.**  The
    404s themselves are ``TestCreateOwnership``'s already, and they pass
    either way: plan step ``C13-a``'s ``fk_transactions_owner_period`` would
    refuse the same write with the guard deleted.  What tells a calendar
    lookup apart from a primary-key compare is that the calendar loads COLUMN
    TUPLES and never an entity, so an empty hydration list is the one
    assertion that cannot hold while the old guard exists.

    **The REFUSAL path, deliberately.**  A successful create renders the grid
    cell, and ``_render_helpers.render_transaction_cell`` reads
    ``pay_period.start_date`` for its due caption -- one hydration this step
    did not remove, only MOVED (see that function's docstring).  Asking the
    question on the refusal keeps the measurement about the ownership door.

    ``expunge_all`` FIRST, for the reason
    :func:`tests._test_helpers.pay_periods_hydrated` gives: the fixture hands
    back live ORM rows, and a ``session.get`` served from the identity map
    fires nothing, so without it the probe would read zero on the very code it
    exists to refuse.

    The same caveat ``TestCellFragmentsResolveOwnershipStructurally`` states
    applies: an owner with no ``budget.pay_schedule`` row makes
    ``pay_schedule_service.resolve_cadence`` fall back to a full entity load.
    Every owner these fixtures build has one.
    """

    @staticmethod
    def _expense_type_id():
        """Return the Expense transaction-type id."""
        return db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one().id

    def test_inline_create_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transactions/inline refuses a foreign period, loading none."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            payload = {
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,
                "transaction_type_id": self._expense_type_id(),
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            }
            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.post("/transactions/inline", data=payload)
            assert resp.status_code == 404
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the door resolves "
                f"the submitted id against the owner's derived calendar"
            )

    def test_adhoc_create_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transactions refuses a foreign period, loading none."""
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            payload = {
                "name": "Sneaky Expense",
                "estimated_amount": "50.00",
                "category_id": seed_user["categories"]["Groceries"].id,
                "pay_period_id": other["period"].id,
                "transaction_type_id": self._expense_type_id(),
                "scenario_id": seed_user["scenario"].id,
                "account_id": str(seed_user["account"].id),
            }
            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.post("/transactions", data=payload)
            assert resp.status_code == 404
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the door resolves "
                f"the submitted id against the owner's derived calendar"
            )

    def test_patch_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH /transactions/<id> refuses a foreign period, loading none.

        The row is the REQUESTER'S own, so the refusal can only come from the
        submitted ``pay_period_id``: a foreign row would 404 at the ownership
        door above and never reach the FK probe at all.
        """
        with app.app_context():
            other = _create_other_user_with_txn(seed_user, seed_periods_today)
            mine = one_off_row_of(
                seed_periods_today[0],
                name="Mine",
                amount=Decimal("10.00"),
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=self._expense_type_id(),
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()
            txn_id, foreign_period_id = mine.id, other["period"].id

            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.patch(
                    f"/transactions/{txn_id}",
                    data={"pay_period_id": str(foreign_period_id)},
                )
            assert resp.status_code == 404
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the door resolves "
                f"the submitted id against the owner's derived calendar"
            )


class TestTheOwnerIsReadOffTheRow:
    """The route-boundary door reads ``user_id``, not the paycheck.

    Plan step **pay_calendar:C13-b**, closing ledger row **P75**'s first half:
    ELEVEN reads walked ``X.pay_period.user_id`` to learn a value the row has
    carried since ``C13-a``.  ``utils.auth_helpers.get_accessible_transaction``
    is the canonical one, reached from eight route sites.

    ``GET /transactions/<id>/entries`` is the door that grades it: it proves
    ownership through that helper and then renders a purchase list whose
    every span comes from the owner's DERIVED calendar (column tuples), so
    after this step the whole request touches no ORM ``PayPeriod`` at all.
    Before it the ownership walk hydrated exactly one.

    The refusal half -- a foreign row answering 404 -- is
    ``TestTransactionOwnership``'s and is unchanged by this step; what is
    asserted here is that the answer no longer costs a relationship.
    """

    def test_entry_list_hydrates_no_pay_period_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /transactions/<id>/entries loads no ORM PayPeriod."""
        with app.app_context():
            txn = one_off_row_of(
                seed_periods_today[0],
                name="Groceries envelope",
                amount=Decimal("200.00"),
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=db.session.query(TransactionType)
                .filter_by(name="Expense").one().id,
                category_id=seed_user["categories"]["Groceries"].id,
                is_envelope=True,
            )
            db.session.commit()
            txn_id = txn.id

            db.session.expunge_all()
            with pay_periods_hydrated() as hydrated:
                resp = auth_client.get(f"/transactions/{txn_id}/entries")
            assert resp.status_code == 200
            assert hydrated == [], (
                f"hydrated {len(hydrated)} PayPeriod row(s); the ownership "
                f"door reads txn.user_id and the spans come from the derived "
                f"calendar"
            )
