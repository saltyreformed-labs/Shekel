"""Listing and creating categories, the create form's edge cases and escaping, and the section
5A.4/5A.5 management baseline.
"""

from app.extensions import db
from app.models.category import Category


# ── List Tests ───────────────────────────────────────────────────────


class TestCategoryList:
    """Tests for GET /categories."""

    def test_list_categories_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /categories returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = auth_client.get("/categories")
            assert resp.status_code == 302
            assert "/settings" in resp.headers["Location"]
            assert "section=categories" in resp.headers["Location"]


# ── Create Tests ─────────────────────────────────────────────────────


class TestCategoryCreate:
    """Tests for POST /categories."""

    def test_create_category_success(self, app, auth_client, seed_user):
        """POST /categories creates a category and redirects."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Utilities",
                "item_name": "Electric",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                item_name="Electric",
            ).one()
            assert cat.group_name == "Utilities"

    def test_create_category_htmx(self, app, auth_client, seed_user):
        """POST /categories with HX-Request returns partial HTML row."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Subscriptions",
                "item_name": "Netflix",
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            # HTMX response is a partial HTML row, not a redirect.
            assert b"Netflix" in resp.data

    def test_create_category_htmx_group_names_exclude_archived_only(
        self, app, auth_client, seed_user,
    ):
        """deep-quality-hunt #41: the HTMX create response's group_names
        dropdown excludes a group that exists only on archived
        categories, matching the active-only set the settings GET render
        shows (otherwise the two paths offer different selectable groups).
        """
        with app.app_context():
            # A group that lives ONLY on an archived (is_active=False)
            # category -- the user does not see it on a settings reload.
            db.session.add(Category(
                user_id=seed_user["user"].id,
                group_name="GhostGroup",
                item_name="OnlyArchived",
                is_active=False,
            ))
            db.session.commit()

            resp = auth_client.post("/categories", data={
                "group_name": "Home",
                "item_name": "Internet",
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            # The archived-only group must NOT appear in the row's
            # group dropdown.
            assert b"GhostGroup" not in resp.data

    def test_create_category_validation_error(self, app, auth_client, seed_user):
        """POST /categories with missing fields shows validation error."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                # Missing group_name and item_name.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_create_category_duplicate(self, app, auth_client, seed_user):
        """POST /categories with existing group+item shows duplicate warning."""
        with app.app_context():
            # "Home" / "Rent" already exists from seed_user.
            resp = auth_client.post("/categories", data={
                "group_name": "Home",
                "item_name": "Rent",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"already exists" in resp.data

    def test_create_category_htmx_validation_error(self, app, auth_client, seed_user):
        """POST /categories via HTMX with missing fields returns 400 JSON."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                # Missing required fields.
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 400
            data = resp.get_json()
            assert "errors" in data
            assert "group_name" in data["errors"]
            assert "item_name" in data["errors"]


# ── Negative Path Tests ─────────────────────────────────────────────


class TestCategoryNegativePaths:
    """Tests for category edge cases, validation, and XSS protection."""

    def test_create_category_double_submit(self, app, auth_client, seed_user):
        """Double-submitting the same category is caught by duplicate check."""
        with app.app_context():
            data = {"group_name": "Test", "item_name": "Double"}

            # First submit succeeds.
            resp1 = auth_client.post("/categories", data=data, follow_redirects=True)
            assert resp1.status_code == 200
            assert b"created" in resp1.data

            # Second submit detected as duplicate.
            resp2 = auth_client.post("/categories", data=data, follow_redirects=True)
            assert resp2.status_code == 200
            assert b"Category already exists." in resp2.data

            # Only one category with this group+item exists.
            count = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="Test",
                item_name="Double",
            ).count()
            assert count == 1

    def test_create_category_max_length_group_name(self, app, auth_client, seed_user):
        """Group name exceeding 100 chars is rejected by schema Length validator."""
        with app.app_context():
            long_name = "A" * 101
            resp = auth_client.post("/categories", data={
                "group_name": long_name,
                "item_name": "Valid",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                item_name="Valid",
            ).count()
            assert count == 0

    def test_create_category_max_length_item_name(self, app, auth_client, seed_user):
        """Item name exceeding 100 chars is rejected by schema Length validator."""
        with app.app_context():
            long_name = "B" * 101
            resp = auth_client.post("/categories", data={
                "group_name": "Valid",
                "item_name": long_name,
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors and try again." in resp.data

            count = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="Valid",
            ).count()
            assert count == 0

    def test_create_category_empty_group_name_after_trim(self, app, auth_client, seed_user):
        """Whitespace-only group name rejected after server-side strip."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "   ",
                "item_name": "ValidItem",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Category names cannot be blank." in resp.data

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                item_name="ValidItem",
            ).first()
            assert cat is None

    def test_create_category_empty_item_name_after_trim(self, app, auth_client, seed_user):
        """Whitespace-only item name rejected after server-side strip."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "ValidGroup",
                "item_name": "   ",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Category names cannot be blank." in resp.data

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="ValidGroup",
            ).first()
            assert cat is None

    def test_create_category_special_characters(self, app, auth_client, seed_user):
        """Special characters in category names are stored and auto-escaped on render."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Test & 'Quotes'",
                "item_name": 'Item "Special" <tag>',
            }, follow_redirects=True)

            assert resp.status_code == 200

            cat = db.session.query(Category).filter_by(
                user_id=seed_user["user"].id,
                group_name="Test & 'Quotes'",
            ).first()
            assert cat is not None
            assert cat.item_name == 'Item "Special" <tag>'

            # Verify Jinja2 auto-escaping on settings page.
            settings_resp = auth_client.get("/settings?section=categories")
            assert settings_resp.status_code == 200
            assert b"<tag>" not in settings_resp.data
            assert b"&lt;tag&gt;" in settings_resp.data


class TestCategoryManagementBaseline:
    """Regression baseline for Section 5A.4/5A.5.

    Locks down category management behavior before the category
    overhaul and CRUD consistency changes.
    """

    def test_category_delete_preserves_other_categories(
        self, app, auth_client, seed_user,
    ):
        """Deleting one category does not affect sibling categories
        in the same group.

        Guards against cascade bugs that could inadvertently remove
        related categories when one is deleted.  Important because
        Section 5A.5 introduces new delete/archive patterns.
        """
        with app.app_context():
            user = seed_user["user"]

            # Create two categories in the same group.
            cat_a = Category(
                user_id=user.id,
                group_name="TestGroup",
                item_name="ItemA",
            )
            cat_b = Category(
                user_id=user.id,
                group_name="TestGroup",
                item_name="ItemB",
            )
            db.session.add_all([cat_a, cat_b])
            db.session.commit()

            cat_a_id = cat_a.id
            cat_b_id = cat_b.id

            # Delete cat_a.
            resp = auth_client.post(
                f"/categories/{cat_a_id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deleted" in resp.data

            # cat_a is gone.
            assert db.session.get(Category, cat_a_id) is None, (
                "Deleted category should no longer exist in the database"
            )

            # cat_b is untouched.
            surviving = db.session.get(Category, cat_b_id)
            assert surviving is not None, (
                "Sibling category in the same group must not be affected "
                "by deleting another category"
            )
            assert surviving.group_name == "TestGroup"
            assert surviving.item_name == "ItemB"
