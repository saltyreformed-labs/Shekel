"""Auth helpers tests: the two ownership lookups, get_or_404 and get_owned_via_parent."""

from datetime import date
from decimal import Decimal

from flask_login import login_user

from app.extensions import db
from app.models.account import Account
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.utils.auth_helpers import get_or_404, get_owned_via_parent
from tests._test_helpers import rhythm_of, one_off_row_of
from tests._test_helpers import record_paydays_across_a_hole


class TestGetOr404:
    """Tests for the get_or_404 ownership helper (Pattern A)."""

    def test_returns_owned_record(self, app, db, seed_user):
        """Happy path: returns the record when it belongs to the current user."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, seed_user["account"].id)
            assert result is not None
            assert result.id == seed_user["account"].id

    def test_returns_none_for_nonexistent_pk(self, app, db, seed_user):
        """Returns None when no record exists at the given PK."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, 999999)
            assert result is None

    def test_returns_none_for_other_users_record(self, app, db, seed_user, second_user):
        """Core security test: user A cannot load user B's record."""
        with app.test_request_context():
            login_user(seed_user["user"])
            # second_user's account belongs to a different user.
            result = get_or_404(Account, second_user["account"].id)
            assert result is None

    def test_returns_none_for_pk_zero(self, app, db, seed_user):
        """PK=0 does not exist in PostgreSQL autoincrement; must not crash."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_or_404(Account, 0)
            assert result is None

    def test_custom_user_id_field_nonexistent(self, app, db, seed_user):
        """Passing a nonexistent field name returns None (safe fallback)."""
        with app.test_request_context():
            login_user(seed_user["user"])
            # Account has user_id, but "nonexistent" does not exist --
            # getattr returns None which != current_user.id.
            result = get_or_404(Account, seed_user["account"].id,
                                user_id_field="nonexistent")
            assert result is None


class TestGetOwnedViaParent:
    """Tests for the get_owned_via_parent ownership helper (Pattern B)."""

    def _create_transaction(self, seed_user, period):
        """Helper: create a projected expense in the given period."""
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = one_off_row_of(
            period,
            name="Test Expense",
            amount=Decimal("50.00"),
            user_id=period.user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.flush()
        return txn

    def test_returns_owned_child_record(self, app, db, seed_user, seed_periods):
        """Happy path: returns the child when its parent belongs to the current user."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(Transaction, txn.id, "pay_period")
            assert result is not None
            assert result.id == txn.id

    def test_returns_none_for_nonexistent_pk(self, app, db, seed_user, seed_periods):
        """Returns None when no child record exists at the given PK."""
        with app.test_request_context():
            login_user(seed_user["user"])
            result = get_owned_via_parent(Transaction, 999999, "pay_period")
            assert result is None

    def test_returns_none_for_other_users_child(self, app, db, seed_user, second_user, seed_periods):
        """Core security test: user A cannot load user B's child record."""
        with app.test_request_context():
            login_user(seed_user["user"])

            # Create a pay period for the second user.
            from app.services import pay_period_write
            periods2 = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 3, 1),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            # Create a transaction owned by the second user.
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            txn2 = one_off_row_of(
                periods2[0],
                name="Other User Expense",
                amount=Decimal("99.00"),
                user_id=periods2[0].user_id,
                account_id=second_user["account"].id,
                scenario_id=second_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=second_user["categories"]["Rent"].id,
            )
            db.session.flush()

            result = get_owned_via_parent(Transaction, txn2.id, "pay_period")
            assert result is None

    def test_returns_none_when_parent_attr_missing(self, app, db, seed_user, seed_periods):
        """Bad parent_attr name returns None instead of crashing."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(
                Transaction, txn.id, "nonexistent_relationship",
            )
            assert result is None

    def test_returns_none_when_parent_user_id_attr_missing(self, app, db, seed_user, seed_periods):
        """Bad parent_user_id_attr name returns None instead of crashing."""
        with app.test_request_context():
            login_user(seed_user["user"])
            txn = self._create_transaction(seed_user, seed_periods[0])
            result = get_owned_via_parent(
                Transaction, txn.id, "pay_period",
                parent_user_id_attr="nonexistent",
            )
            assert result is None
