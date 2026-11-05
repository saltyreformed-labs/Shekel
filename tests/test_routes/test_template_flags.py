"""
Shekel Budget App -- Template Tracking & Visibility Flag Tests

Tests for the is_envelope and companion_visible toggles on transaction
templates. These flags control which transactions support sub-entries
(purchase tracking) and which appear in the companion view.

Introduced in Section 9, Commit 6.  Column renamed from
track_individual_purchases to is_envelope in carry-forward aftermath
Phase 1 (revision cea9b9e31e88).
"""

from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from tests._test_helpers import (
    cadence_payload,
    make_cadence_rule,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD


# ── Helpers ──────────────────────────────────────────────────────────


def _make_template(seed_user, name="Test Template",
                   txn_type="Expense", track=False, companion=False,
                   recurs=False):
    """Create a template with configurable tracking and companion flags.

    Args:
        seed_user: The seed_user fixture dict.
        name: Template name.
        txn_type: 'Income' or 'Expense' (ref table name).
        track: Value for is_envelope.
        companion: Value for companion_visible.
        recurs: Whether to author an every-paycheck rule onto it.  The
            Recurring LIST shows definitions with a rule and only those since
            plan step ``balance:X-bi-7b`` (R-BAL23), so the badge cases that
            read the list ask for one; the edit-form and update cases read
            the definition directly and do not.

    Returns:
        TransactionTemplate: the created template.
    """
    txn_type_obj = (
        db.session.query(TransactionType).filter_by(name=txn_type).one()
    )
    category = seed_user["categories"]["Rent"]
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        transaction_type_id=txn_type_obj.id,
        name=name,
        is_envelope=track,
        companion_visible=companion,
    )
    db.session.add(template)
    db.session.flush()
    # Its price, through the one write door as the create route states it:
    # the Recurring page prices a definition by its series since plan step
    # X-bp.
    state_template_price(template, Decimal("100.00"))
    if recurs:
        make_cadence_rule(template, EVERY_PERIOD)
    db.session.commit()
    return template


def _base_form_data(seed_user, txn_type="Expense", **overrides):
    """Build minimal valid form data for creating a template.

    Carries a cadence, because a transaction template REQUIRES one since plan
    step ``balance:X-bi-7b`` (R-BAL23): a definition with no rule is a
    one-off's, made at the grid, and the form's schema refuses an empty unit.

    Args:
        seed_user: The seed_user fixture dict.
        txn_type: 'Income' or 'Expense' (ref table name).
        **overrides: Additional or overridden form fields.

    Returns:
        dict: Form data suitable for auth_client.post().
    """
    txn_type_obj = (
        db.session.query(TransactionType).filter_by(name=txn_type).one()
    )
    data = {
        "name": "Test Template",
        "amount": "100.00",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": txn_type_obj.id,
        "account_id": seed_user["account"].id,
        **cadence_payload(),
    }
    data.update(overrides)
    return data


def _update_form_data(seed_user, txn_type="Expense", **overrides):
    """Build form data for UPDATING a template: the create payload minus its cadence.

    The update schema is partial and an omitted ``recurrence_unit`` means
    "leave the stored cadence alone" (a present empty one is refused since
    plan step ``balance:X-bi-7b``, R-BAL23).  The update cases here grade the
    two flags on a definition ``_make_template`` built WITHOUT a rule, so a
    payload carrying a cadence would author one as a side effect -- the
    make-this-repeat path, which is not what these cases are about.
    """
    data = _base_form_data(seed_user, txn_type=txn_type, **overrides)
    for key in ("recurrence_unit", "recurrence_placement", "interval_n", "starts_on"):
        data.pop(key, None)
    return data


# ── Create Tests ─────────────────────────────────────────────────────


class TestCreateTemplateFlags:
    """Tests for tracking and companion flags during template creation."""

    def test_create_template_with_tracking(self, app, auth_client, seed_user):
        """POST /templates with is_envelope='on' sets flag to True."""
        with app.app_context():
            form = _base_form_data(
                seed_user, name="Groceries",
                is_envelope="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Groceries",
            ).one()
            assert template.is_envelope is True

    def test_create_template_tracking_default_false(self, app, auth_client, seed_user):
        """POST /templates without track field defaults to False."""
        with app.app_context():
            form = _base_form_data(seed_user, name="Rent Payment")
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Rent Payment",
            ).one()
            assert template.is_envelope is False

    def test_create_template_with_companion_visible(self, app, auth_client, seed_user):
        """POST /templates with companion_visible='on' sets flag to True."""
        with app.app_context():
            form = _base_form_data(
                seed_user, name="Gas Money",
                companion_visible="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Gas Money",
            ).one()
            assert template.companion_visible is True

    def test_create_template_with_both_flags(self, app, auth_client, seed_user):
        """POST /templates with both flags enabled sets both to True."""
        with app.app_context():
            form = _base_form_data(
                seed_user, name="Weekly Groceries",
                is_envelope="on",
                companion_visible="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200

            template = db.session.query(TransactionTemplate).filter_by(
                name="Weekly Groceries",
            ).one()
            assert template.is_envelope is True
            assert template.companion_visible is True

    def test_create_expense_tracking_allowed(self, app, auth_client, seed_user):
        """Purchase tracking on an expense template is the valid case."""
        with app.app_context():
            form = _base_form_data(
                seed_user, txn_type="Expense", name="Tracked Expense",
                is_envelope="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Purchase tracking is only available" not in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Tracked Expense",
            ).one()
            assert template.is_envelope is True

    def test_create_tracking_rejected_on_income(self, app, auth_client, seed_user):
        """POST /templates rejects tracking on an income template."""
        with app.app_context():
            form = _base_form_data(
                seed_user, txn_type="Income", name="Bad Income Track",
                is_envelope="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Purchase tracking is only available for expense templates" in resp.data

            # Template should not have been created.
            count = db.session.query(TransactionTemplate).filter_by(
                name="Bad Income Track",
            ).count()
            assert count == 0


# ── Update Tests ─────────────────────────────────────────────────────


class TestUpdateTemplateFlags:
    """Tests for tracking and companion flags during template update."""

    def test_update_toggle_tracking_on(self, app, auth_client, seed_user):
        """POST /templates/<id> with track='on' enables tracking."""
        with app.app_context():
            template = _make_template(seed_user, name="Groceries", track=False)

            form = _update_form_data(
                seed_user, name="Groceries",
                is_envelope="on",
            )
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(template)
            assert template.is_envelope is True

    def test_update_toggle_companion_on(self, app, auth_client, seed_user):
        """POST /templates/<id> with companion='on' enables companion visibility."""
        with app.app_context():
            template = _make_template(seed_user, name="Gas", companion=False)

            form = _update_form_data(
                seed_user, name="Gas",
                companion_visible="on",
            )
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(template)
            assert template.companion_visible is True

    def test_update_disable_both_flags(self, app, auth_client, seed_user):
        """Unchecking both checkboxes correctly sets both flags to False.

        This is the critical unchecking test: verifies that absent checkbox
        fields (not sent by browser) result in False, not silently ignored.
        """
        with app.app_context():
            template = _make_template(
                seed_user, name="Both Flags", track=True, companion=True,
            )
            assert template.is_envelope is True
            assert template.companion_visible is True

            # Submit form WITHOUT the checkbox fields -- simulates unchecking.
            form = _update_form_data(seed_user, name="Both Flags")
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(template)
            assert template.is_envelope is False
            assert template.companion_visible is False

    def test_update_tracking_stays_true_when_checked(self, app, auth_client, seed_user):
        """Submitting with checkbox checked preserves True value.

        Ensures the round-trip works: template has True, form sends 'on',
        template still has True after update.
        """
        with app.app_context():
            template = _make_template(
                seed_user, name="Stay Tracked", track=True,
            )

            form = _update_form_data(
                seed_user, name="Stay Tracked",
                is_envelope="on",
            )
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200

            db.session.refresh(template)
            assert template.is_envelope is True


# ── Expense-Only Validation Tests ────────────────────────────────────


class TestTrackingExpenseOnlyValidation:
    """Tests that is_envelope is rejected on income templates."""

    def test_update_income_enable_tracking_rejected(self, app, auth_client, seed_user):
        """Enabling tracking on an existing income template is rejected.

        Template type is income. User checks tracking without changing type.
        """
        with app.app_context():
            template = _make_template(
                seed_user, name="Income Template", txn_type="Income",
            )

            form = _update_form_data(
                seed_user, txn_type="Income", name="Income Template",
                is_envelope="on",
            )
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Purchase tracking is only available for expense templates" in resp.data

            db.session.refresh(template)
            assert template.is_envelope is False

    def test_update_expense_tracking_change_type_income_rejected(
        self, app, auth_client, seed_user,
    ):
        """Changing type to income while tracking is checked is rejected.

        Template has tracking=True and type=Expense. User changes type to
        Income but leaves tracking checked. The resulting state (income +
        tracking) is invalid.
        """
        with app.app_context():
            template = _make_template(
                seed_user, name="Was Expense", track=True,
            )

            form = _update_form_data(
                seed_user, txn_type="Income", name="Was Expense",
                is_envelope="on",
            )
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Purchase tracking is only available for expense templates" in resp.data

            # Original values should be unchanged.
            db.session.refresh(template)
            assert template.is_envelope is True
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            assert template.transaction_type_id == expense_type.id

    def test_update_income_partial_payload_rejected_by_route_fallback(
        self, app, auth_client, seed_user,
    ):
        """Partial update omitting the type is caught by the route fallback.

        deep-quality-hunt #22: when a POST sets ``is_envelope='on'`` but
        omits ``transaction_type_id``, the schema cross-field validator
        (``validate_envelope_only_on_expense``) returns early because the
        type is absent from the payload.  The route-layer fallback
        ``_is_tracking_on_non_expense`` is then the SOLE enforcer -- it reads
        the stored type via ``getattr(template, "transaction_type_id")`` and
        rejects when it is not Expense.

        Every other rejection test submits ``transaction_type_id`` (income),
        so the schema catches them first and this ``getattr`` branch is never
        exercised.  Posting against a stored income template with no type in
        the payload proves the defense-in-depth path actually rejects -- a
        regression there (dropped check or flipped ``getattr`` default) would
        otherwise let a partial update silently attach envelope semantics to
        an income flow, corrupting expense-only carry-forward rollover.
        """
        with app.app_context():
            template = _make_template(
                seed_user, name="Stored Income", txn_type="Income",
            )

            # Enable tracking but OMIT transaction_type_id so the schema
            # validator returns early and only the route fallback (against
            # the stored income type) can reject.
            form = _update_form_data(
                seed_user, name="Stored Income", is_envelope="on",
            )
            del form["transaction_type_id"]

            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"Purchase tracking is only available for expense templates" in resp.data
            assert b"updated" not in resp.data

            # The rejection short-circuits before any field is applied.
            db.session.refresh(template)
            assert template.is_envelope is False

    def test_update_change_type_income_and_disable_tracking_succeeds(
        self, app, auth_client, seed_user,
    ):
        """Changing type to income AND disabling tracking succeeds.

        Resulting state is (income + no tracking), which is valid.
        """
        with app.app_context():
            template = _make_template(
                seed_user, name="Type Change", track=True,
            )

            # Don't include is_envelope -- simulates unchecking.
            form = _update_form_data(
                seed_user, txn_type="Income", name="Type Change",
            )
            resp = auth_client.post(
                f"/templates/{template.id}", data=form,
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data
            assert b"Purchase tracking is only available" not in resp.data

            db.session.refresh(template)
            assert template.is_envelope is False
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            assert template.transaction_type_id == income_type.id

    def test_companion_visible_on_income_allowed(self, app, auth_client, seed_user):
        """companion_visible has no type restriction -- income templates can be visible.

        Per the scope doc design note, the flags are independent. companion_visible
        does not require expense type.
        """
        with app.app_context():
            form = _base_form_data(
                seed_user, txn_type="Income", name="Visible Income",
                companion_visible="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data
            assert b"Purchase tracking is only available" not in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Visible Income",
            ).one()
            assert template.companion_visible is True
            assert template.is_envelope is False

    def test_tracking_and_companion_independent(self, app, auth_client, seed_user):
        """track=True with companion=False succeeds -- flags are independent."""
        with app.app_context():
            form = _base_form_data(
                seed_user, txn_type="Expense", name="Track Only",
                is_envelope="on",
            )
            resp = auth_client.post(
                "/templates", data=form, follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            template = db.session.query(TransactionTemplate).filter_by(
                name="Track Only",
            ).one()
            assert template.is_envelope is True
            assert template.companion_visible is False


# ── Edit Form Display Tests ──────────────────────────────────────────


class TestEditFormFlagDisplay:
    """Tests that checkboxes reflect the template's current flag state."""

    def test_edit_form_tracking_checkbox_checked(self, app, auth_client, seed_user):
        """Edit form shows tracking checkbox as checked when flag is True."""
        with app.app_context():
            template = _make_template(
                seed_user, name="Tracked", track=True,
            )
            resp = auth_client.get(f"/templates/{template.id}/edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            # The checkbox input should have the "checked" attribute.
            assert 'id="is_envelope"' in html
            assert 'name="is_envelope"' in html
            # Find the checkbox and verify it has checked.
            # The template renders: checked if template.is_envelope
            assert "checked" in html.split('id="is_envelope"')[1].split(">")[0]

    def test_edit_form_tracking_checkbox_unchecked(self, app, auth_client, seed_user):
        """Edit form shows tracking checkbox as unchecked when flag is False."""
        with app.app_context():
            template = _make_template(
                seed_user, name="Untracked", track=False,
            )
            resp = auth_client.get(f"/templates/{template.id}/edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            # The checkbox input should NOT have "checked".
            track_section = html.split('id="is_envelope"')[1].split(">")[0]
            assert "checked" not in track_section

    def test_edit_form_companion_checkbox_checked(self, app, auth_client, seed_user):
        """Edit form shows companion checkbox as checked when flag is True."""
        with app.app_context():
            template = _make_template(
                seed_user, name="Visible", companion=True,
            )
            resp = auth_client.get(f"/templates/{template.id}/edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert 'id="companion_visible"' in html
            assert "checked" in html.split('id="companion_visible"')[1].split(">")[0]

    def test_edit_form_companion_checkbox_unchecked(self, app, auth_client, seed_user):
        """Edit form shows companion checkbox as unchecked when flag is False."""
        with app.app_context():
            template = _make_template(
                seed_user, name="Hidden", companion=False,
            )
            resp = auth_client.get(f"/templates/{template.id}/edit")
            assert resp.status_code == 200

            html = resp.data.decode()
            companion_section = html.split('id="companion_visible"')[1].split(">")[0]
            assert "checked" not in companion_section

    def test_new_form_shows_checkboxes(self, app, auth_client):
        """The new template form includes both checkbox fields."""
        with app.app_context():
            resp = auth_client.get("/templates/new")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert 'name="is_envelope"' in html
            assert 'name="companion_visible"' in html
            assert "Tracking &" in html


# ── Badge Display Tests ──────────────────────────────────────────────


class TestListBadges:
    """Tests for badge indicators on the template list page."""

    def test_badge_track_only(self, app, auth_client, seed_user):
        """Template with track=True shows tracking badge, not companion badge."""
        with app.app_context():
            _make_template(
                seed_user, recurs=True, name="Track Badge", track=True, companion=False,
            )
            resp = auth_client.get("/templates")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert "bi bi-cart" in html
            assert "Tracks purchases" in html
            # Companion badge should not appear.
            assert "bi bi-eye" not in html

    def test_badge_companion_only(self, app, auth_client, seed_user):
        """Template with companion=True shows companion badge, not tracking badge."""
        with app.app_context():
            _make_template(
                seed_user, recurs=True, name="Companion Badge", track=False, companion=True,
            )
            resp = auth_client.get("/templates")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert "bi bi-eye" in html
            assert "Companion visible" in html
            # Tracking badge should not appear.
            assert "bi bi-cart" not in html

    def test_badge_both(self, app, auth_client, seed_user):
        """Template with both flags shows both badges."""
        with app.app_context():
            _make_template(
                seed_user, recurs=True, name="Both Badges", track=True, companion=True,
            )
            resp = auth_client.get("/templates")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert "bi bi-cart" in html
            assert "bi bi-eye" in html

    def test_badge_neither(self, app, auth_client, seed_user):
        """Template with both flags False shows no badges."""
        with app.app_context():
            _make_template(
                seed_user, recurs=True, name="No Badges", track=False, companion=False,
            )
            resp = auth_client.get("/templates")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert "bi bi-cart" not in html
            assert "bi bi-eye" not in html

    def test_template_list_shows_badges(self, app, auth_client, seed_user):
        """Multiple templates with mixed flags show correct badges per template.

        Creates three templates: one tracked, one companion-visible, one plain.
        Verifies correct badges appear for each.
        """
        with app.app_context():
            _make_template(
                seed_user, recurs=True, name="Tracked Groceries",
                track=True, companion=False,
            )
            _make_template(
                seed_user, recurs=True, name="Visible Bill",
                track=False, companion=True,
            )
            _make_template(
                seed_user, recurs=True, name="Plain Rent",
                track=False, companion=False,
            )

            resp = auth_client.get("/templates")
            assert resp.status_code == 200

            html = resp.data.decode()
            # All template names present.
            assert "Tracked Groceries" in html
            assert "Visible Bill" in html
            assert "Plain Rent" in html
            # At least one tracking and one companion badge.
            assert "bi bi-cart" in html
            assert "bi bi-eye" in html


# ── Schema Tests ─────────────────────────────────────────────────────


class TestFlagSchemaValidation:
    """Tests for Marshmallow schema handling of boolean flag fields."""

    def test_schema_accepts_on_value(self):
        """Boolean field accepts 'on' (HTML checkbox value) as True.

        Uses the cached Expense type ID so the cross-field
        ``validate_envelope_only_on_expense`` rule passes -- this test
        verifies the Boolean coercion in isolation, not the cross-field
        rule (covered separately below).
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        schema = TemplateCreateSchema()
        data = schema.load({
            "name": "Test",
            "amount": "50.00",
            "category_id": "1",
            "transaction_type_id": str(expense_id),
            "account_id": "1",
            **cadence_payload(),
            "is_envelope": "on",
        })
        assert data["is_envelope"] is True

    def test_schema_missing_field_defaults_false(self):
        """Boolean field defaults to False when absent from form data."""
        schema = TemplateCreateSchema()
        data = schema.load({
            "name": "Test",
            "amount": "50.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            **cadence_payload(),
        })
        assert data["is_envelope"] is False
        assert data["companion_visible"] is False

    def test_schema_rejects_invalid_boolean_string(self):
        """Boolean field rejects unrecognized string values."""
        schema = TemplateCreateSchema()
        errors = schema.validate({
            "name": "Test",
            "amount": "50.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            **cadence_payload(),
            "is_envelope": "invalid",
        })
        assert "is_envelope" in errors

    def test_update_schema_inherits_flag_fields(self):
        """TemplateUpdateSchema inherits boolean fields from TemplateCreateSchema."""
        schema = TemplateUpdateSchema()
        data = schema.load({
            "is_envelope": "on",
            "companion_visible": "on",
        })
        assert data["is_envelope"] is True
        assert data["companion_visible"] is True

    def test_update_schema_missing_flags_default_false(self):
        """TemplateUpdateSchema defaults missing flags to False."""
        schema = TemplateUpdateSchema()
        data = schema.load({
            "name": "Updated Name",
        })
        assert data["is_envelope"] is False
        assert data["companion_visible"] is False


# ── Cross-Field Schema Validator Tests (Phase 2) ─────────────────────


class TestEnvelopeOnlyOnExpenseSchema:
    """Direct schema-level tests for ``validate_envelope_only_on_expense``.

    Phase 2 of the carry-forward aftermath plan moved the
    envelope-on-income rejection into the Marshmallow schema as the
    input boundary.  These tests exercise the validator directly so
    regressions surface at the schema layer rather than only via
    full route round-trips (which are exercised separately in
    ``TestTrackingExpenseOnlyValidation``).

    The validator's contract: when ``is_envelope`` is True AND
    ``transaction_type_id`` resolves to the Income type, raise
    ``ValidationError`` keyed under ``is_envelope``.  In every other
    case (envelope on expense, no envelope, partial-update payloads
    that omit one field) the validator must permit the payload.
    """

    def test_create_schema_rejects_envelope_on_income(self):
        """Create payload with is_envelope=True and Income type fails."""
        from marshmallow import ValidationError as _ValidationError  # pylint: disable=import-outside-toplevel
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel
        import pytest  # pylint: disable=import-outside-toplevel

        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        schema = TemplateCreateSchema()
        with pytest.raises(_ValidationError) as exc_info:
            schema.load({
                "name": "Bad",
                "amount": "100.00",
                "category_id": "1",
                "transaction_type_id": str(income_id),
                "account_id": "1",
            **cadence_payload(),
                "is_envelope": "on",
            })

        # The ValidationError attaches the message to is_envelope so the
        # form can highlight the offending checkbox.
        assert "is_envelope" in exc_info.value.messages
        msgs = exc_info.value.messages["is_envelope"]
        assert any(
            "expense templates" in m for m in msgs
        ), f"Expected 'expense templates' in error; got {msgs!r}"

    def test_create_schema_allows_envelope_on_expense(self):
        """Create payload with is_envelope=True and Expense type passes."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        schema = TemplateCreateSchema()
        data = schema.load({
            "name": "Groceries",
            "amount": "200.00",
            "category_id": "1",
            "transaction_type_id": str(expense_id),
            "account_id": "1",
            **cadence_payload(),
            "is_envelope": "on",
        })
        assert data["is_envelope"] is True
        assert data["transaction_type_id"] == expense_id

    def test_create_schema_allows_envelope_false_on_income(self):
        """is_envelope=False is valid on income templates."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        schema = TemplateCreateSchema()
        # Omit is_envelope -- defaults to False via load_default.
        data = schema.load({
            "name": "Salary",
            "amount": "3000.00",
            "category_id": "1",
            "transaction_type_id": str(income_id),
            "account_id": "1",
            **cadence_payload(),
        })
        assert data["is_envelope"] is False

    def test_update_schema_rejects_flipping_envelope_true_on_income(self):
        """Update payload changing is_envelope to True on income type fails."""
        from marshmallow import ValidationError as _ValidationError  # pylint: disable=import-outside-toplevel
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel
        import pytest  # pylint: disable=import-outside-toplevel

        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        schema = TemplateUpdateSchema()
        with pytest.raises(_ValidationError) as exc_info:
            schema.load({
                "transaction_type_id": str(income_id),
                "is_envelope": "on",
            })
        assert "is_envelope" in exc_info.value.messages

    def test_update_schema_partial_payload_skips_check_when_type_absent(self):
        """Partial update that omits transaction_type_id is permitted at the schema layer.

        The validator returns early so the route layer can fall back
        to the existing template's stored ``transaction_type_id`` via
        the ``_is_tracking_on_non_expense`` helper.  This split keeps
        the Marshmallow contract simple (decide from payload alone)
        while preserving end-to-end enforcement.
        """
        schema = TemplateUpdateSchema()
        data = schema.load({"is_envelope": "on"})
        assert data["is_envelope"] is True
        # No ValidationError raised -- the route layer is responsible
        # for the existing-template fallback.

    def test_update_schema_partial_payload_skips_check_when_envelope_absent(self):
        """Partial update that omits is_envelope is permitted (defaults to False)."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        schema = TemplateUpdateSchema()
        data = schema.load({"transaction_type_id": str(income_id)})
        # is_envelope deserializes to False via inherited load_default
        # so the validator's early-return path triggers.
        assert data["is_envelope"] is False
        assert data["transaction_type_id"] == income_id

    def test_validate_returns_errors_dict_for_envelope_on_income(self):
        """schema.validate() returns the same error dict shape as load() raises.

        Routes call ``schema.validate(form)`` first (returns dict) and
        only call ``schema.load(form)`` after the dict is empty.  The
        cross-field error must surface in the validate() dict so the
        route's ``_flash_message_for_errors`` helper finds it.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        schema = TemplateCreateSchema()
        errors = schema.validate({
            "name": "Bad",
            "amount": "100.00",
            "category_id": "1",
            "transaction_type_id": str(income_id),
            "account_id": "1",
            **cadence_payload(),
            "is_envelope": "on",
        })
        assert "is_envelope" in errors
        msgs = errors["is_envelope"]
        assert isinstance(msgs, list) and msgs, msgs
        assert any("expense templates" in m for m in msgs), msgs
