"""Renaming and re-parenting a category, and the group dropdown on the add and edit forms."""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.ref import TransactionType
from tests._test_helpers import one_off_row_of

from ._helpers import _create_other_user_category


# ── Edit Tests (5A.4-1) ────────────────────────────────────────────


class TestCategoryEdit:
    """Tests for POST /categories/<id>/edit (rename and re-parent)."""

    def test_edit_category_rename(self, app, auth_client, seed_user):
        """Renaming item_name preserves group_name and updates the item."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "Fuel"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(cat)
            assert cat.item_name == "Fuel"
            assert cat.group_name == "Auto"

    def test_edit_category_reparent(self, app, auth_client, seed_user):
        """Changing group_name moves the category to a different group."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Toll Pass",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Travel", "item_name": "Toll Pass"},
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(cat)
            assert cat.group_name == "Travel"
            assert cat.item_name == "Toll Pass"

    def test_edit_category_rename_and_reparent(self, app, auth_client, seed_user):
        """Changing both group_name and item_name in a single edit."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Travel", "item_name": "Fuel"},
            )

            db.session.refresh(cat)
            assert cat.group_name == "Travel"
            assert cat.item_name == "Fuel"

    def test_edit_category_preserves_transaction_association(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Renaming a category does not break transaction FK references.

        Transactions reference categories by integer category_id, so
        changing group_name or item_name on the Category row leaves
        all transaction associations intact.
        """
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.flush()

            txn_type = db.session.query(TransactionType).filter_by(
                name="Expense"
            ).one()
            txn = one_off_row_of(
                seed_periods_today[0],
                name="Fill Up",
                amount=Decimal("45.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=txn_type.id,
                category_id=cat.id,
            )
            db.session.commit()
            cat_id = cat.id
            txn_id = txn.id

            auth_client.post(
                f"/categories/{cat_id}/edit",
                data={"group_name": "Travel", "item_name": "Fuel"},
            )

            db.session.refresh(txn)
            assert txn.category_id == cat_id
            assert txn.category.group_name == "Travel"
            assert txn.category.item_name == "Fuel"

    def test_edit_category_duplicate_blocked(self, app, auth_client, seed_user):
        """Editing a category to match an existing group+item is rejected."""
        with app.app_context():
            cat_gas = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            cat_insurance = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Insurance",
            )
            db.session.add_all([cat_gas, cat_insurance])
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat_gas.id}/edit",
                data={"group_name": "Auto", "item_name": "Insurance"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already exists" in resp.data

            db.session.refresh(cat_gas)
            assert cat_gas.item_name == "Gas"

    def test_edit_category_blank_name_rejected(self, app, auth_client, seed_user):
        """Empty or whitespace-only names are rejected after server-side strip."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            # Empty item_name.
            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "   "},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"cannot be blank" in resp.data

            db.session.refresh(cat)
            assert cat.item_name == "Gas"

    def test_edit_category_idor(
        self, app, auth_client, seed_user,
    ):
        """Editing another user's category returns 404 (security: same as nonexistent)."""
        with app.app_context():
            other = _create_other_user_category()

            resp = auth_client.post(
                f"/categories/{other['category'].id}/edit",
                data={"group_name": "Hacked", "item_name": "Pwned"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

            db.session.refresh(other["category"])
            assert other["category"].group_name == "Other"

    def test_edit_category_nonexistent(self, app, auth_client, seed_user):
        """Editing a nonexistent category returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/categories/999999/edit",
                data={"group_name": "Ghost", "item_name": "Phantom"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_edit_category_no_op_same_values(self, app, auth_client, seed_user):
        """Submitting the same values is not flagged as a duplicate."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "Gas"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(cat)
            assert cat.group_name == "Auto"
            assert cat.item_name == "Gas"

    def test_edit_category_strips_whitespace(self, app, auth_client, seed_user):
        """Leading and trailing whitespace is stripped before saving."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "  Travel  ", "item_name": "  Fuel  "},
            )

            db.session.refresh(cat)
            assert cat.group_name == "Travel"
            assert cat.item_name == "Fuel"

    def test_edit_category_max_length(self, app, auth_client, seed_user):
        """Item name exceeding 100 characters is rejected by schema validation."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
            )
            db.session.add(cat)
            db.session.commit()

            resp = auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "X" * 101},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.refresh(cat)
            assert cat.item_name == "Gas"

    def test_edit_category_preserves_sort_order(self, app, auth_client, seed_user):
        """Editing name fields does not reset the sort_order column."""
        with app.app_context():
            cat = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Gas",
                sort_order=5,
            )
            db.session.add(cat)
            db.session.commit()

            auth_client.post(
                f"/categories/{cat.id}/edit",
                data={"group_name": "Auto", "item_name": "Fuel"},
            )

            db.session.refresh(cat)
            assert cat.item_name == "Fuel"
            assert cat.sort_order == 5


# ── Group Dropdown Tests (5A.4-2) ──────────────────────────────────


class TestCategoryGroupDropdown:
    """Tests for the group name dropdown on category add and edit forms."""

    def test_add_form_shows_group_dropdown(self, app, auth_client, seed_user):
        """Add form contains a select dropdown with existing groups and 'Add new group'."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # The add form has a <select> for group selection.
            assert 'id="add-group-select"' in html

            # Existing groups appear as options (seed_user has 5 groups).
            assert '<option value="Auto">' in html
            assert '<option value="Home">' in html
            assert '<option value="Income">' in html

            # Sentinel option for adding a new group.
            assert '__new__' in html
            assert "Add new group" in html

    def test_add_form_groups_sorted_alphabetically(self, app, auth_client, seed_user):
        """Dropdown options are sorted alphabetically, with 'Add new group' last."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # Isolate the add form's select to avoid matching edit form dropdowns.
            select_start = html.index('id="add-group-select"')
            select_end = html.index("</select>", select_start)
            select_html = html[select_start:select_end]

            # seed_user groups: Auto, Credit Card, Family, Home, Income
            auto_pos = select_html.index('value="Auto"')
            credit_pos = select_html.index('value="Credit Card"')
            family_pos = select_html.index('value="Family"')
            home_pos = select_html.index('value="Home"')
            income_pos = select_html.index('value="Income"')
            new_pos = select_html.index('value="__new__"')

            assert auto_pos < credit_pos < family_pos < home_pos < income_pos < new_pos

    def test_add_to_existing_group_via_dropdown(self, app, auth_client, seed_user):
        """Creating a category with an existing group name works via hidden input."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Auto",
                "item_name": "Insurance",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            auto_cats = (
                db.session.query(Category)
                .filter_by(user_id=seed_user["user"].id, group_name="Auto")
                .all()
            )
            assert len(auto_cats) == 2  # Car Payment + Insurance

    def test_add_with_new_group(self, app, auth_client, seed_user):
        """Creating a category with a new group name adds a new group."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Travel",
                "item_name": "Airline",
            }, follow_redirects=True)

            assert resp.status_code == 200

            groups = set(
                row[0] for row in
                db.session.query(Category.group_name)
                .filter_by(user_id=seed_user["user"].id)
                .distinct()
            )
            assert "Travel" in groups

    def test_edit_form_preselects_current_group(self, app, auth_client, seed_user):
        """Edit form dropdown pre-selects the category's current group."""
        with app.app_context():
            cat = seed_user["categories"]["Car Payment"]

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # Find the edit form's select for this category.
            select_id = f'id="edit-group-select-{cat.id}"'
            assert select_id in html

            # The "Auto" option within this category's edit select has 'selected'.
            # Locate the select, then find the selected option within it.
            select_start = html.index(select_id)
            # The closing </select> after this select.
            select_end = html.index("</select>", select_start)
            select_html = html[select_start:select_end]

            assert 'value="Auto" selected' in select_html

    def test_no_existing_groups(self, app, auth_client, seed_user):
        """With no categories, only 'Add new group' option and text field is visible."""
        with app.app_context():
            # Remove all seed categories (no templates reference them).
            db.session.query(Category).filter_by(
                user_id=seed_user["user"].id
            ).delete()
            db.session.commit()

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # The add form select exists with only the sentinel option.
            assert 'id="add-group-select"' in html
            assert '__new__' in html

            # The custom text field is visible (no d-none class on container).
            # When group_names is empty, the div should NOT have d-none.
            custom_div_start = html.index('id="add-group-custom"')
            # Walk back to find the opening tag.
            div_start = html.rfind("<div", 0, custom_div_start)
            div_tag = html[div_start:custom_div_start + len('id="add-group-custom"')]
            assert "d-none" not in div_tag

    def test_edit_form_dropdown_ids_unique_per_category(self, app, auth_client, seed_user):
        """Each edit form has a distinct set of dropdown element IDs."""
        with app.app_context():
            cats = list(seed_user["categories"].values())
            cat_a = cats[0]
            cat_b = cats[1]

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # Both categories have their own select elements.
            assert f'id="edit-group-select-{cat_a.id}"' in html
            assert f'id="edit-group-select-{cat_b.id}"' in html

            # Both have their own hidden inputs.
            assert f'id="edit-group-name-{cat_a.id}"' in html
            assert f'id="edit-group-name-{cat_b.id}"' in html

            # Both have their own custom divs.
            assert f'id="edit-group-custom-{cat_a.id}"' in html
            assert f'id="edit-group-custom-{cat_b.id}"' in html

    def test_add_form_hidden_input_name_is_group_name(self, app, auth_client, seed_user):
        """Only the hidden input has name='group_name'; the select does not."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            # The add form's select should NOT have name="group_name".
            select_start = html.index('id="add-group-select"')
            select_tag_start = html.rfind("<select", 0, select_start)
            select_tag = html[select_tag_start:select_start + len('id="add-group-select"')]
            assert 'name="group_name"' not in select_tag

            # The hidden input has both the id and name.
            assert 'id="add-group-name" name="group_name"' in html

    def test_group_dropdown_reflects_newly_created_group(self, app, auth_client, seed_user):
        """Creating a category with a new group adds it to the dropdown on next load."""
        with app.app_context():
            auth_client.post("/categories", data={
                "group_name": "NewGroup",
                "item_name": "NewItem",
            })

            resp = auth_client.get("/settings?section=categories")
            html = resp.data.decode()

            assert '<option value="NewGroup">' in html

    def test_htmx_row_partial_includes_dropdown(self, app, auth_client, seed_user):
        """HTMX-created category row includes group dropdown in its edit form."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "Auto",
                "item_name": "Tolls",
            }, headers={"HX-Request": "true"})

            assert resp.status_code == 200
            html = resp.data.decode()

            # The partial should contain a select for the edit form.
            assert "edit-group-select-" in html
            # Existing groups should appear as options.
            assert '<option value="Auto"' in html
            assert '__new__' in html
