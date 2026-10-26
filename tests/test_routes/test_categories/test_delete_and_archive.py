"""Deleting a category, archiving and unarchiving it, and what each of those doors refuses."""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import (
    current_pay_period,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    select_option_values,
)

from ._helpers import _create_other_user_category


# ── Delete Tests ─────────────────────────────────────────────────────


class TestCategoryDelete:
    """Tests for POST /categories/<id>/delete."""

    def test_delete_unused_category(self, app, auth_client, seed_user):
        """POST /categories/<id>/delete removes an unused category."""
        with app.app_context():
            # Create a fresh category not used by any template/transaction.
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="Deletable",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deleted" in resp.data

            # Verify actually deleted (hard delete).
            assert db.session.get(Category, cat.id) is None

    def test_delete_category_in_use_by_template(self, app, auth_client, seed_user):
        """POST /categories/<id>/delete for a category used by a template is rejected."""
        with app.app_context():
            category = seed_user["categories"]["Rent"]
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=txn_type.id,
                name="Rent Template",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data

            # Category should still exist.
            assert db.session.get(Category, category.id) is not None

    def test_delete_category_in_use_by_transaction(self, app, auth_client, seed_user, seed_periods_today):
        """POST /categories/<id>/delete for a category only a ROW uses is rejected.

        ``category_has_usage`` asks the definitions first, so a row whose
        definition names the category never reaches the transaction clause
        (a one-off's definition does, which made this case grade the
        template clause twice; found by 7c-3's adversarial review).  The
        row here is a recurring definition's, re-categorised ON THE ROW as
        the PATCH door writes it (act 1 of ``_apply_field_updates``, no
        flag), with its definition left on another category: the one state
        in which the transaction clause alone blocks the delete.
        """
        with app.app_context():
            category = seed_user["categories"]["Groceries"]
            template = make_expense_template(
                db.session, seed_user, amount="85.00", name="Grocery Trip",
                category_key="Rent",
            )
            row = generate_row_of(template, seed_periods_today[0])
            row.category_id = category.id
            db.session.commit()
            assert template.category_id != category.id

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data

    def test_delete_category_idor(self, app, auth_client, seed_user):
        """POST /categories/<id>/delete for another user's category returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify other user's category still exists.
            assert db.session.get(Category, other["category"].id) is not None

    def test_delete_nonexistent_category(self, app, auth_client, seed_user):
        """POST /categories/999999/delete for missing category returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/categories/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_delete_allowed_when_only_other_user_has_template(
        self, app, auth_client, seed_user, seed_second_user,
    ):
        """User A can delete a category even if User B has templates (M6).

        The in-use check must be scoped by user_id so that User B's
        templates do not block User A's category deletion.
        """
        with app.app_context():
            # Create a fresh deletable category for User A.
            cat_a = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="OnlyMine",
            )
            db.session.add(cat_a)
            db.session.flush()

            # User B creates a template referencing User B's OWN category.
            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            tpl_b = TransactionTemplate(
                user_id=seed_second_user["user"].id,
                account_id=seed_second_user["account"].id,
                category_id=seed_second_user["categories"]["Rent"].id,
                transaction_type_id=txn_type.id,
                name="B Rent Template",
                default_amount=Decimal("1000.00"),
            )
            db.session.add(tpl_b)
            db.session.commit()

            # User A deletes their own unused category -- should succeed.
            resp = auth_client.post(
                f"/categories/{cat_a.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deleted" in resp.data
            assert db.session.get(Category, cat_a.id) is None

    def test_delete_blocked_by_soft_deleted_transaction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Category cannot be deleted when even soft-deleted transactions reference it.

        The DB FK constraint blocks deletion regardless of is_deleted
        status, so the in-use check correctly includes soft-deleted
        transactions to give a friendly error instead of a DB crash.  The
        row is a recurring definition's re-categorised on the row, its
        definition on another category, so the transaction clause is the
        one that answers (see ``test_delete_category_in_use_by_transaction``).
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="SoftDeleteTest",
            )
            db.session.add(category)
            db.session.flush()

            template = make_expense_template(
                db.session, seed_user, amount="50.00",
                name="Soft Deleted Expense", category_key="Groceries",
            )
            txn = generate_row_of(template, seed_periods_today[0])
            txn.category_id = category.id
            txn.is_deleted = True
            db.session.commit()
            assert template.category_id != category.id

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data
            assert db.session.get(Category, category.id) is not None

    def test_delete_blocked_by_active_transaction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Category cannot be deleted when active transactions reference it.

        The row is a recurring definition's re-categorised on the row, its
        definition on another category, so the transaction clause is the
        one that answers (see ``test_delete_category_in_use_by_transaction``).
        """
        with app.app_context():
            category = Category(
                user_id=seed_user["user"].id,
                group_name="Temp",
                item_name="ActiveTxnTest",
            )
            db.session.add(category)
            db.session.flush()

            template = make_expense_template(
                db.session, seed_user, amount="100.00",
                name="Active Expense", category_key="Groceries",
            )
            row = generate_row_of(template, seed_periods_today[0])
            row.category_id = category.id
            db.session.commit()
            assert template.category_id != category.id

            resp = auth_client.post(
                f"/categories/{category.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data
            assert db.session.get(Category, category.id) is not None


# ── Category Archive/Delete Tests (5A.5-5) ─────────────────────────


class TestCategoryArchiveDelete:
    """Tests for archive, unarchive, and enhanced delete behavior."""

    def test_archive_category(self, app, auth_client, seed_user):
        """C-5A.5-29: Archiving a category sets is_active=False."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Archive",
                item_name="TestItem",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"archived" in resp.data

            db.session.refresh(cat)
            assert cat.is_active is False

    def test_unarchive_category(self, app, auth_client, seed_user):
        """C-5A.5-30: Unarchiving a category sets is_active=True."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Archive",
                item_name="Restore",
                is_active=False,
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"unarchived" in resp.data

            db.session.refresh(cat)
            assert cat.is_active is True

    def test_delete_category_no_usage(self, app, auth_client, seed_user):
        """C-5A.5-31: Category with no usage is permanently deleted."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Unused",
                item_name="Deletable",
            )
            db.session.add(cat)
            db.session.commit()
            cat_id = cat.id

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(Category, cat_id) is None

    def test_delete_category_with_usage_archives(self, app, auth_client, seed_user, db):
        """C-5A.5-32: Category in use by template is archived instead of deleted."""
        with app.app_context():
            category = seed_user["categories"]["Rent"]
            cat_id = category.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=cat_id,
                transaction_type_id=txn_type.id,
                name="Blocks Delete",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data
            assert b"archived instead" in resp.data

            reloaded = db.session.get(Category, cat_id)
            assert reloaded.is_active is False

    def test_archived_categories_hidden_from_settings(
        self, app, auth_client, seed_user, db,
    ):
        """C-5A.5-33: Settings page separates active and archived categories."""
        with app.app_context():
            # Seed categories are active. Archive one.
            rent_cat = db.session.get(Category, seed_user["categories"]["Rent"].id)
            rent_cat.is_active = False
            db.session.commit()

            resp = auth_client.get("/settings?section=categories")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Active categories in group cards (Rent is gone from active).
            assert "Car Payment" in html  # Active Auto category.

            # Archived section shows the archived category.
            assert "Archived Categories (1)" in html
            assert "Rent" in html

    def test_archived_categories_hidden_from_grid_dropdown(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """C-5A.5-34: Archived categories do not appear in grid Add Transaction dropdown.

        Scopes the assertion to the ``category_id`` select in the
        grid's add-transaction modal.  See
        ``test_archived_categories_hidden_from_template_dropdown``
        for the rationale -- the page renders ``value="N"`` for
        many other dropdowns (transaction types, pay periods, etc.)
        and a whole-HTML substring check fails deterministically
        when ``rent.id`` collides with any of them.
        """
        with app.app_context():
            # Archive one category.
            rent = db.session.get(Category, seed_user["categories"]["Rent"].id)
            rent.is_active = False
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            category_options = select_option_values(html, "category_id")

            # Active categories in the dropdown.
            salary = seed_user["categories"]["Salary"]
            assert str(salary.id) in category_options, (
                f"Active category {salary.id} ({salary.item_name}) "
                f"is missing from /grid category dropdown; got "
                f"options {category_options!r}"
            )

            # Archived category NOT in the dropdown.
            assert str(rent.id) not in category_options, (
                f"Archived category {rent.id} ({rent.item_name}) is "
                f"still present in /grid category dropdown; got "
                f"options {category_options!r}"
            )

    def test_archived_category_transactions_still_render(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """C-5A.5-35: Transactions with archived categories still render in the grid."""
        with app.app_context():
            category = db.session.get(Category, seed_user["categories"]["Rent"].id)
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # The txn must live inside the grid's default visible window so
            # the assertion targets the archived-category rendering path
            # rather than the period-scoping filter added in 8b67128.  Use
            # the period that contains today -- ``seed_periods_today`` is 10
            # biweekly periods starting 2026-01-02, so today's period is
            # always one of them.
            current_period = current_pay_period(seed_user["user"].id)
            assert current_period is not None, (
                "seed_periods_today must cover today so the txn lands in the "
                "default visible grid window"
            )

            one_off_row_of(
                current_period,
                name="Rent Payment",
                amount=Decimal("1200.00"),
                user_id=current_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=txn_type.id,
                category_id=category.id,
            )

            # Archive the category AFTER creating the transaction.
            category.is_active = False
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            # The transaction should still render with its name.
            assert b"Rent Payment" in resp.data

    def test_archive_category_idor(self, app, auth_client, seed_user):
        """C-5A.5-36: Archiving another user's category returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/archive",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            db.session.refresh(other["category"])
            assert other["category"].is_active is True

    def test_unarchive_category_idor(self, app, auth_client, seed_user):
        """Unarchiving another user's archived category returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_category()
            other["category"].is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/unarchive",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            db.session.refresh(other["category"])
            assert other["category"].is_active is False

    def test_delete_category_already_archived_with_usage(
        self, app, auth_client, seed_user, db,
    ):
        """Already-archived category with usage stays archived, no double-archive."""
        with app.app_context():
            category = db.session.get(Category, seed_user["categories"]["Rent"].id)
            cat_id = category.id
            txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=cat_id,
                transaction_type_id=txn_type.id,
                name="In-Use Template",
                default_amount=Decimal("500.00"),
            )
            db.session.add(template)

            category.is_active = False
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"in use" in resp.data

            reloaded = db.session.get(Category, cat_id)
            assert reloaded.is_active is False

    def test_delete_category_already_archived_no_usage(
        self, app, auth_client, seed_user,
    ):
        """Archived category with no usage is permanently deleted (clean up archive)."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Old",
                item_name="Cleanup",
                is_active=False,
            )
            db.session.add(cat)
            db.session.commit()
            cat_id = cat.id

            resp = auth_client.post(
                f"/categories/{cat_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"permanently deleted" in resp.data
            assert db.session.get(Category, cat_id) is None

    def test_archived_categories_hidden_from_template_dropdown(
        self, app, auth_client, seed_user, seed_periods_today, db,
    ):
        """Template creation form only shows active categories in dropdown.

        Scopes the assertion to the ``category_id`` select.  A naive
        ``f'value="{rent.id}" not in html`` check would falsely
        match the ``value="N"`` attributes of unrelated dropdowns on
        the same page -- transaction_type_id (1-2), start_period_id
        (per-test pay-period IDs), month_of_year (1-12), and
        recurrence_pattern (1-8) all share the namespace.  When
        rent.id collides with any of those values the original check
        failed despite the category dropdown being correctly filtered.
        """
        with app.app_context():
            rent = db.session.get(Category, seed_user["categories"]["Rent"].id)
            rent.is_active = False
            db.session.commit()

            resp = auth_client.get("/templates/new")
            assert resp.status_code == 200
            html = resp.data.decode()

            category_options = select_option_values(html, "category_id")

            # Active category present in the category dropdown.
            salary = seed_user["categories"]["Salary"]
            assert str(salary.id) in category_options, (
                f"Active category {salary.id} ({salary.item_name}) is "
                f"missing from /templates/new category dropdown; got "
                f"options {category_options!r}"
            )

            # Archived category absent from the category dropdown.
            assert str(rent.id) not in category_options, (
                f"Archived category {rent.id} ({rent.item_name}) is "
                f"still present in /templates/new category dropdown; "
                f"got options {category_options!r}"
            )
