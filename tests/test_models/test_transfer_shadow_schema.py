"""
Shekel Budget App -- Transfer Shadow Transaction Schema Tests

Tests for the schema additions in Task 2 of the Transfer Architecture Rework:
  - Transfer.category_id (nullable FK to Category)
  - TransferTemplate.category_id (nullable FK to Category)
  - transfer_id exclusion from transaction Marshmallow schemas

Task 2 also added Transaction.transfer_id (a nullable FK to Transfer, ON
DELETE CASCADE) and its Transfer.shadow_transactions backref.  Plan step
balance:X-bi-6-4d-2 made a row naming a transfer unstorable and plan step
balance:X-bi-6-5 dropped both, so their four cases went with them (ruling
plan#1850).
"""

from decimal import Decimal

from app.models.ref import Status
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.schemas.validation import (
    InlineTransactionCreateSchema,
    TransactionCreateSchema,
)
from app.models.amount_ownership import AmountOwnership


class TestTransferCategoryId:
    """Tests for the category_id column on Transfer."""

    def test_transfer_model_has_category_id(self, app, db, seed_full_user_data):
        """Transfer with category_id saves and resolves the relationship."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            data = seed_full_user_data
            category = data["categories"]["Rent"]

            xfer = Transfer(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                pay_period_id=data["periods"][0].id,
                scenario_id=data["scenario"].id,
                status_id=projected.id,
                name="Categorized Transfer",
                amount_ownership=AmountOwnership.own(Decimal("500.00")),
                category_id=category.id,
            )
            db.session.add(xfer)
            db.session.flush()

            assert xfer.category_id == category.id
            assert xfer.category is not None
            assert xfer.category.item_name == "Rent"

    def test_transfer_category_id_nullable(self, app, db, seed_full_user_data):
        """Transfer with category_id=None saves without error."""
        with app.app_context():
            projected = db.session.query(Status).filter_by(name="Projected").one()
            data = seed_full_user_data

            xfer = Transfer(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                pay_period_id=data["periods"][0].id,
                scenario_id=data["scenario"].id,
                status_id=projected.id,
                name="No Category Transfer",
                amount_ownership=AmountOwnership.own(Decimal("300.00")),
                category_id=None,
            )
            db.session.add(xfer)
            db.session.flush()

            assert xfer.category_id is None
            assert xfer.category is None


class TestTransferTemplateCategoryId:
    """Tests for the category_id column on TransferTemplate."""

    def test_transfer_template_model_has_category_id(
        self, app, db, seed_full_user_data
    ):
        """TransferTemplate with category_id saves and resolves the relationship."""
        with app.app_context():
            data = seed_full_user_data
            category = data["categories"]["Rent"]

            tpl = TransferTemplate(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                name="Categorized Template",
                default_amount=Decimal("200.00"),
                category_id=category.id,
            )
            db.session.add(tpl)
            db.session.flush()

            assert tpl.category_id == category.id
            assert tpl.category is not None
            assert tpl.category.item_name == "Rent"

    def test_transfer_template_category_id_nullable(
        self, app, db, seed_full_user_data
    ):
        """TransferTemplate with category_id=None saves without error."""
        with app.app_context():
            data = seed_full_user_data

            tpl = TransferTemplate(
                user_id=data["user"].id,
                from_account_id=data["account"].id,
                to_account_id=data["savings_account"].id,
                name="No Category Template",
                default_amount=Decimal("150.00"),
                category_id=None,
            )
            db.session.add(tpl)
            db.session.flush()

            assert tpl.category_id is None
            assert tpl.category is None


class TestTransferIdNotInTransactionSchemas:
    """Verify transfer_id cannot be set via transaction Marshmallow schemas.

    User input must not be able to forge a shadow transaction link.  No
    transaction has had a transfer column since plan step balance:X-bi-6-5,
    so these grade only that the schemas drop the posted key (EXCLUDE mode).
    """

    def test_transfer_id_not_in_transaction_create_schema(self):
        """TransactionCreateSchema strips transfer_id (EXCLUDE mode)."""
        data = TransactionCreateSchema().load({
            "name": "Groceries",
            "estimated_amount": "85.50",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            "transfer_id": "999",
        })
        assert "transfer_id" not in data

    def test_transfer_id_not_in_inline_create_schema(self):
        """InlineTransactionCreateSchema strips transfer_id (EXCLUDE mode)."""
        data = InlineTransactionCreateSchema().load({
            "estimated_amount": "50.00",
            "account_id": "1",
            "category_id": "1",
            "pay_period_id": "1",
            "transaction_type_id": "1",
            "scenario_id": "1",
            "transfer_id": "999",
        })
        assert "transfer_id" not in data
