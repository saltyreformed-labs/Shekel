"""The OR-* checks: an orphaned template, an unused category (a transfer and a transfer definition
count as use), a goal on an inactive account, and OR-02, retired because its state cannot be
stored.
"""

from decimal import Decimal

import pytest

from app.models.category import Category
from app.models.ref import TransactionType
from app.models.savings_goal import SavingsGoal
from app.models.transaction_template import TransactionTemplate
from app.services import transfer_service
from tests._test_helpers import create_account_of_type, create_transfer, make_transfer_template
from scripts.integrity_check import CheckResult, check_orphaned_records


# ── Orphan Detection ─────────────────────────────────────────────


class TestOrphanDetection:
    """Tests for OR-* orphan detection checks."""

    @pytest.mark.usefixtures("seed_user", "seed_periods")
    def test_clean_database_no_orphans(self, db):
        """No orphans detected on a properly seeded database.

        Note: OR-03 (unused categories) and OR-04 (empty pay periods) will
        flag results on a minimal seed because categories have no templates
        and periods have no transactions. These are warnings, not errors.
        We verify the check runs without crashing; specific orphan detection
        is tested in dedicated methods below.
        """
        results = check_orphaned_records(db.session)
        # 5: OR-02 retired at plan step R-F6, when the owning FK moved onto
        # ``budget.recurrence_rules`` and made a rule with no definition a row
        # the database refuses rather than one a scan finds.
        assert len(results) == 5
        # All should return CheckResult objects regardless of pass/fail.
        assert all(isinstance(r, CheckResult) for r in results)

    def test_or02_is_retired_because_its_state_is_inexpressible(self, db):
        """OR-02 is GONE, and the schema is what replaced it (plan step R-F6).

        It scanned for recurrence rules no template referenced -- finding
        **F-6**, three rows on production -- and that state stopped being one
        the database will hold when the owning FK moved onto
        ``budget.recurrence_rules``: ``ck_recurrence_rules_one_owner`` refuses
        a rule with no definition, and ``ON DELETE CASCADE`` takes the rule
        when the definition goes.  A checker for a state the schema forbids
        reports on the constraint's behalf and can only ever say zero.

        Asserted as an ABSENCE rather than deleted silently: this is the arm
        that fails if a later edit reinstates the check, and the id is left
        retired rather than reused so an old report's OR-02 still means what
        it meant.
        """
        ids = {r.check_id for r in check_orphaned_records(db.session)}
        assert "OR-02" not in ids, (
            "OR-02 is retired -- its state is refused by "
            "ck_recurrence_rules_one_owner, so a check for it can only pass"
        )

    @pytest.mark.usefixtures("seed_user")
    def test_or03_detects_unused_category(self, db):
        """OR-03 detects a category not used by any template or transaction."""
        # The seed_user fixture creates categories that are unused by default.
        results = check_orphaned_records(db.session)
        or03 = next(r for r in results if r.check_id == "OR-03")
        # Seed categories are not referenced by any templates or transactions.
        assert not or03.passed
        # seed_user creates 5 categories (Salary, Rent, Car Payment, Groceries, Payback)
        # none referenced by any template or transaction
        assert or03.detail_count == 5

    def test_or03_counts_a_transfer_and_a_transfer_definition_as_use(
        self, db, seed_user, seed_periods,
    ):
        """A category a transfer or a recurring transfer names is not unused.

        Plan step ``balance:X-bi-6-4c-4`` (ruling **R-BAL160**, the report's
        twin of finding **BAL-545**).  One category is named only by a
        transfer DEFINITION that generated nothing; the other only by a
        transfer, moved onto it through the service.  Neither is listed; a
        category nothing names still is.

        It also re-categorised the transfer's shadow rows to no category
        around the service, so that only the transfer's own row named the
        category -- the state a shadowless transfer presents -- until plan
        step ``balance:X-bi-6-4d-2``: a transfer is one row and no row may
        name one (``ck_transactions_names_no_transfer``, ruling
        **R-BAL258**), so that state is every transfer's and the statement
        updated no row.
        """
        def fresh(name):
            category = Category(
                user_id=seed_user["user"].id, group_name="Temp", item_name=name,
            )
            db.session.add(category)
            db.session.flush()
            return category

        by_definition = fresh("Only A Transfer Rule")
        by_transfer = fresh("Only A Transfer")
        unused = fresh("Nothing")
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "OR-03 Savings",
        )
        make_transfer_template(
            db.session, seed_user, savings,
        ).category_id = by_definition.id
        db.session.commit()
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0],
        )
        transfer_service.update_transfer(
            transfer.id, seed_user["user"].id, category_id=by_transfer.id,
        )
        db.session.commit()

        or03 = next(
            r for r in check_orphaned_records(db.session) if r.check_id == "OR-03"
        )
        listed = {row["id"] for row in or03.details}
        assert unused.id in listed
        assert by_definition.id not in listed
        assert by_transfer.id not in listed

    def test_or01_detects_orphaned_template(self, db, seed_user):
        """OR-01: Template with no recurrence rule and no transactions."""
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        category = list(seed_user["categories"].values())[0]

        # Create a template with no recurrence_rule_id and no transactions.
        orphan_template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=category.id,
            transaction_type_id=txn_type.id,
            name="Orphaned Template",
            default_amount=Decimal("50.00"),
        )
        db.session.add(orphan_template)
        db.session.flush()
        # NO cadence and no transactions, which is the state OR-01 reports --
        # expressed as the ABSENCE of a rule row since plan step R-F6, where
        # it used to be a NULL ``recurrence_rule_id`` on this template.

        results = check_orphaned_records(db.session)
        or01 = next(r for r in results if r.check_id == "OR-01")
        assert not or01.passed
        assert or01.detail_count == 1
        assert or01.details[0]["name"] == "Orphaned Template"

    def test_or06_detects_goal_on_inactive_account(self, db, seed_user):
        """OR-06 flags a savings goal on an inactive account."""
        account = seed_user["account"]
        account.is_active = False
        db.session.flush()

        goal = SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=account.id,
            name="Bad Goal",
            target_amount=Decimal("1000.00"),
            is_active=True,
        )
        db.session.add(goal)
        db.session.flush()

        results = check_orphaned_records(db.session)
        or06 = next(r for r in results if r.check_id == "OR-06")
        assert not or06.passed
        assert or06.detail_count == 1  # 1 goal on inactive account
