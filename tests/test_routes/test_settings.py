"""
Shekel Budget App -- Settings Route Tests

Tests for user settings page:
  - Rendering settings, and REFUSING a missing row rather than
    auto-creating one (plan step balance:X-i3)
  - Updating all three fields
  - Validation errors for each field
  - Partial updates (blank fields skipped)
"""

from decimal import Decimal

import pytest
from markupsafe import escape

from app.exceptions import RequiredRecordMissing
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.user import UserSettings
from app.tax_law import TaxLaw
from tests._made_up_federal import made_up_federal
from tests._test_helpers import (
    MADE_UP_SOURCES,
    made_up_fica,
    made_up_state,
    made_up_year,
)


class TestSettingsShow:
    """Tests for GET /settings."""

    def test_settings_page_renders(self, app, auth_client, seed_user):
        """GET /settings renders the settings page with general section."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"General Settings" in resp.data
            assert b'name="grid_default_periods"' in resp.data
            assert b'name="default_inflation_rate"' in resp.data

    def test_settings_REFUSES_a_missing_row_rather_than_creating_one(
        self, app, auth_client, seed_user,
    ):
        """The render does not repair the owner's settings row.

        **This test asserted the auto-create until plan step balance:X-i3**,
        which deleted that branch on the developer's ruling -- the third
        instance of one shape, after the two on the account detail pages.
        ``render_settings_dashboard`` serves a GET and several POST re-renders,
        so a render that writes cost the GET its snapshot and could not be
        declared with a ``write_transaction`` block either.

        Every door that creates a user writes the row, so the state below is
        reachable only by deleting it out from under the application, which is
        what this test does.  The two POST handlers that create-if-missing keep
        that behaviour: a write door may author what it writes.
        """
        with app.app_context():
            # Delete existing settings created by seed_user.
            db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            with pytest.raises(RequiredRecordMissing, match="auth.user_settings"):
                auth_client.get("/settings")

            # And it manufactured NOTHING on the way out.
            assert db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).first() is None

    def test_the_settings_POST_still_creates_a_missing_row(
        self, app, auth_client, seed_user,
    ):
        """The other half: a WRITE door may author what it writes.

        Pairs with the test above so the deletion cannot be read as "a missing
        settings row is now unrecoverable".  It is the recovery path the
        refusal's own message names.
        """
        with app.app_context():
            db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).delete()
            db.session.commit()

            # NOT the column default (6): posting the default would pass
            # whether the door wrote the submitted value or merely created a
            # bare row and dropped the form on the floor.
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "9",
            }, follow_redirects=False)
            assert resp.status_code == 302

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == 9


class TestSettingsUpdate:
    """Tests for POST /settings."""

    def test_update_all_fields(self, app, auth_client, seed_user):
        """POST /settings updates all three settings fields."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "10",
                "default_inflation_rate": "4",
                "low_balance_threshold": "1000",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Settings updated" in resp.data

            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == 10
            assert settings.default_inflation_rate == Decimal("0.0400")
            assert settings.low_balance_threshold == 1000

    def test_invalid_grid_periods(self, app, auth_client, seed_user):
        """POST /settings with non-numeric grid_periods flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "abc",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_invalid_inflation_rate(self, app, auth_client, seed_user):
        """POST /settings with invalid Decimal for inflation flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_inflation_rate": "not-a-number",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_invalid_threshold(self, app, auth_client, seed_user):
        """POST /settings with non-numeric threshold flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "low_balance_threshold": "xyz",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_blank_fields_skipped(self, app, auth_client, seed_user):
        """POST /settings with blank fields preserves existing values."""
        with app.app_context():
            # Set known values first.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.grid_default_periods = 8
            settings.low_balance_threshold = 750
            db.session.commit()

            # Submit with all blank -- should not change anything.
            resp = auth_client.post("/settings", data={
                "grid_default_periods": "",
                "default_inflation_rate": "",
                "low_balance_threshold": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Settings updated" in resp.data

            db.session.refresh(settings)
            assert settings.grid_default_periods == 8
            assert settings.low_balance_threshold == 750


class TestGridAccountSetting:
    """Tests for the default grid account dropdown in settings."""

    def test_settings_renders_account_dropdown(self, app, auth_client, seed_user):
        """GET /settings renders the grid account dropdown with user's accounts."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"Default Grid Account" in resp.data
            assert b"Checking" in resp.data

    def test_set_grid_account(self, app, auth_client, seed_user):
        """POST /settings with valid account id sets default_grid_account_id."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_grid_account_id": str(seed_user["account"].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_grid_account_id == seed_user["account"].id

    def test_clear_grid_account(self, app, auth_client, seed_user):
        """POST /settings with blank grid account clears the setting."""
        with app.app_context():
            # Set it first.
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = seed_user["account"].id
            db.session.commit()

            resp = auth_client.post("/settings", data={
                "default_grid_account_id": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_grid_account_id is None

    def test_reject_invalid_grid_account(self, app, auth_client, seed_user):
        """POST /settings with invalid account id flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_grid_account_id": "999999",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid grid account" in resp.data


class TestSettingsDashboard:
    """Tests for the settings dashboard sections and redirects."""

    def test_settings_dashboard_default_section(self, app, auth_client, seed_user):
        """GET /settings renders the dashboard with General section by default."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"Settings" in resp.data
            assert b"General" in resp.data

    def test_settings_dashboard_categories_section(self, app, auth_client, seed_user):
        """GET /settings?section=categories renders category management."""
        with app.app_context():
            resp = auth_client.get("/settings?section=categories")
            assert resp.status_code == 200
            assert b"Add Category" in resp.data
            assert b'name="group_name"' in resp.data

    def test_settings_dashboard_tax_section(self, app, auth_client, seed_user):
        """GET /settings?section=tax renders the tax law, read-only, as Tax Rates."""
        with app.app_context():
            resp = auth_client.get("/settings?section=tax")
            assert resp.status_code == 200
            assert b"Tax Rates" in resp.data
            assert b"Federal Tax Brackets" in resp.data

    def test_settings_dashboard_retirement_section_redirects(
        self, app, auth_client, seed_user,
    ):
        """GET /settings?section=retirement redirects to /retirement.

        Gate A ruling 6 (retirement rebuild P3a): the Settings >
        Retirement section retired -- the assumptions panel on the
        retirement page is the persistent home for its three fields plus
        the merit horizon, so an old bookmark 302s there instead of
        rendering a settings section.
        """
        with app.app_context():
            resp = auth_client.get("/settings?section=retirement")
            assert resp.status_code == 302
            assert "/retirement" in resp.headers["Location"]

    def test_settings_dashboard_account_types_section(self, app, auth_client, seed_user):
        """GET /settings?section=account-types renders account type management."""
        with app.app_context():
            resp = auth_client.get("/settings?section=account-types")
            assert resp.status_code == 200
            assert b"Account Types" in resp.data

    def test_settings_account_types_shows_categories(self, app, auth_client, seed_user):
        """Account types section includes the category dropdown with all four categories."""
        with app.app_context():
            resp = auth_client.get("/settings?section=account-types")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Asset" in html
            assert "Liability" in html
            assert "Retirement" in html
            assert "Investment" in html
            assert 'name="category_id"' in html

    def test_settings_account_types_shows_flags(self, app, auth_client, seed_user):
        """Account types section includes metadata flag checkboxes."""
        with app.app_context():
            resp = auth_client.get("/settings?section=account-types")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'name="has_parameters"' in html
            assert 'name="has_amortization"' in html
            assert 'name="has_interest"' in html
            assert 'name="is_pretax"' in html
            assert 'name="is_liquid"' in html
            assert 'name="icon_class"' in html

    def test_settings_create_full_type_via_form(self, app, auth_client, seed_user):
        """POST with all metadata fields creates a complete account type."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum
        from app.models.ref import AccountType

        with app.app_context():
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            resp = auth_client.post(
                "/accounts/types",
                data={
                    "name": "FormTestDebt",
                    "category_id": liability_id,
                    "has_parameters": "true",
                    "has_amortization": "true",
                    "max_term_months": "180",
                    "icon_class": "bi-house",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"created" in resp.data

            acct_type = db.session.query(AccountType).filter_by(
                name="FormTestDebt",
            ).one()
            assert acct_type.category_id == liability_id
            assert acct_type.has_parameters is True
            assert acct_type.has_amortization is True
            assert acct_type.max_term_months == 180
            assert acct_type.icon_class == "bi-house"

    def test_settings_edit_type_metadata(self, app, auth_client, seed_user):
        """POST update with metadata fields persists changes.

        ``user_id`` is set explicitly so the C-28 ownership guard
        (``ref.account_types.user_id == current_user.id``) accepts
        the update; a row left with ``user_id IS NULL`` would be
        treated as a seeded built-in and the route would reject the
        mutation with a 404-equivalent flash.
        """
        from app import ref_cache
        from app.enums import AcctCategoryEnum
        from app.models.ref import AccountType

        with app.app_context():
            custom_type = AccountType(
                name="EditMetaTest",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
                user_id=seed_user["user"].id,
            )
            db.session.add(custom_type)
            db.session.commit()

            resp = auth_client.post(
                f"/accounts/types/{custom_type.id}",
                data={
                    "name": "EditMetaTest",
                    "has_parameters": "true",
                    "has_interest": "true",
                    "is_liquid": "true",
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(custom_type)
            assert custom_type.has_parameters is True
            assert custom_type.has_interest is True
            assert custom_type.is_liquid is True

    def test_settings_dashboard_pay_periods_section(
        self, app, bare_auth_client, bare_user,
    ):
        """GET /settings?section=pay-periods renders the generation form.

        **Re-pointed from ``seed_user`` to ``bare_user`` at plan step
        ``pay_calendar:C14-f``** (ruling **R-PC63**).  The generate card and
        the manage card became EXCLUSIVE: the card asking for a First Payday is
        offered only to an owner who has none, because offering it to one who
        already holds a rhythm is how they could restate it (ledger row
        **P80**).  The SUBJECT is unchanged -- this section still renders the
        generation form -- and only the owner it is asked of moved.  The
        exclusive half is the case below.
        """
        with app.app_context():
            resp = bare_auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Generate Pay Periods" in resp.data
            assert b'name="start_date"' in resp.data

    def test_settings_pay_periods_offers_a_rhythm_holder_the_manage_card(
        self, app, auth_client, seed_user,
    ):
        """An owner who holds paydays is offered management, not generation.

        The other half of the exclusivity (plan step ``pay_calendar:C14-f``).
        Asserted as an ABSENCE as well as a presence: without the negative,
        rendering both cards again would leave this case green.
        """
        with app.app_context():
            resp = auth_client.get("/settings?section=pay-periods")
            assert resp.status_code == 200
            assert b"Generate Pay Periods" not in resp.data
            assert b'name="start_date"' not in resp.data
            assert b"/pay-periods/extend" in resp.data

    def test_settings_dashboard_invalid_section_defaults_to_general(self, app, auth_client, seed_user):
        """GET /settings?section=bogus defaults to the General section."""
        with app.app_context():
            resp = auth_client.get("/settings?section=bogus")
            assert resp.status_code == 200
            assert b"General" in resp.data

    def test_categories_get_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /categories returns 302 redirect to settings dashboard."""
        with app.app_context():
            resp = auth_client.get("/categories")
            assert resp.status_code == 302
            assert "section=categories" in resp.headers["Location"]

    def test_categories_post_still_works(self, app, auth_client, seed_user):
        """POST /categories still processes form submission (not redirected)."""
        with app.app_context():
            resp = auth_client.post("/categories", data={
                "group_name": "TestGroup",
                "item_name": "TestItem",
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"TestItem" in resp.data

    def test_settings_dashboard_security_section(self, app, auth_client, seed_user):
        """GET /settings?section=security renders the Security section."""
        with app.app_context():
            resp = auth_client.get("/settings?section=security")
            assert resp.status_code == 200
            assert b"Change Password" in resp.data


# ── Negative Path Tests ─────────────────────────────────────────────


class TestSettingsNegativePaths:
    """Tests for settings validation gaps, IDOR, and edge cases."""

    def test_grid_account_idor(self, app, auth_client, seed_user, second_user):
        """Setting grid account to another user's account is rejected."""
        with app.app_context():
            other_acct_id = second_user["account"].id

            resp = auth_client.post("/settings", data={
                "default_grid_account_id": str(other_acct_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid grid account." in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_grid_account_id != other_acct_id

    def test_grid_account_deactivated_account_rejected(self, app, auth_client, seed_user):
        """Setting grid account to a deactivated account is rejected."""
        with app.app_context():
            acct_id = seed_user["account"].id
            # Re-fetch in current session so the modification is tracked.
            account = db.session.get(Account, acct_id)
            account.is_active = False
            db.session.commit()

            resp = auth_client.post("/settings", data={
                "default_grid_account_id": str(acct_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid grid account." in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_grid_account_id is None

    def test_grid_account_non_numeric_id(self, app, auth_client, seed_user):
        """Non-numeric grid account ID triggers ValueError and flashes error."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "default_grid_account_id": "abc",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_negative_grid_periods(self, app, auth_client, seed_user):
        """Negative grid periods rejected by schema range check."""
        with app.app_context():
            original = seed_user["settings"].grid_default_periods

            resp = auth_client.post("/settings", data={
                "grid_default_periods": "-5",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == original

    def test_zero_grid_periods(self, app, auth_client, seed_user):
        """Zero grid periods rejected by server-side range check."""
        with app.app_context():
            original = seed_user["settings"].grid_default_periods

            resp = auth_client.post("/settings", data={
                "grid_default_periods": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == original

    def test_extreme_grid_periods(self, app, auth_client, seed_user):
        """Extreme grid periods rejected by schema range check."""
        with app.app_context():
            original = seed_user["settings"].grid_default_periods

            resp = auth_client.post("/settings", data={
                "grid_default_periods": "99999",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.grid_default_periods == original

    def test_negative_inflation_rate(self, app, auth_client, seed_user):
        """Negative inflation rate rejected by server-side range check."""
        with app.app_context():
            original = seed_user["settings"].default_inflation_rate

            resp = auth_client.post("/settings", data={
                "default_inflation_rate": "-5",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.default_inflation_rate == original

    def test_negative_low_balance_threshold(self, app, auth_client, seed_user):
        """Negative low balance threshold rejected by server-side range check."""
        with app.app_context():
            original = seed_user["settings"].low_balance_threshold

            resp = auth_client.post("/settings", data={
                "low_balance_threshold": "-100",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.low_balance_threshold == original


class TestSection8Settings:
    """Tests for Section 8 settings columns (dashboard & analytics)."""

    def test_save_large_txn_threshold(self, app, auth_client, seed_user):
        """POST with valid large_transaction_threshold=1000 persists the value.

        Verifies the integer threshold is stored directly (no conversion).
        """
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "large_transaction_threshold": "1000",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Settings updated" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.large_transaction_threshold == 1000

    def test_save_staleness_days(self, app, auth_client, seed_user):
        """POST with anchor_staleness_days=7 persists the value."""
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "anchor_staleness_days": "7",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Settings updated" in resp.data

            db.session.expire_all()
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            assert settings.anchor_staleness_days == 7

    def test_settings_validation_rejects_negative_threshold(self, app, auth_client, seed_user):
        """POST with negative large_transaction_threshold is rejected.

        Schema validates min=0; negative values should produce a
        validation error and redirect with flash.
        """
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "large_transaction_threshold": "-1",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_settings_validation_rejects_zero_staleness(self, app, auth_client, seed_user):
        """POST with anchor_staleness_days=0 is rejected.

        Schema validates min=1; zero means the anchor is always stale,
        which is nonsensical.
        """
        with app.app_context():
            resp = auth_client.post("/settings", data={
                "anchor_staleness_days": "0",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_settings_defaults_rendered_on_get(self, app, auth_client, seed_user):
        """GET /settings renders the threshold fields with their defaults.

        Default values: large_transaction_threshold=500,
        anchor_staleness_days=14.  (trend_alert_threshold was removed
        with the retired spending trend engine.)
        """
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Large transaction threshold field with default 500.
            assert 'name="large_transaction_threshold"' in html
            assert 'value="500"' in html

            # Anchor staleness days field with default 14.
            assert 'name="anchor_staleness_days"' in html
            assert 'value="14"' in html

            # The retired trend setting no longer renders a field.
            assert 'name="trend_alert_threshold"' not in html

    def test_settings_page_includes_dashboard_analytics_section(self, app, auth_client, seed_user):
        """GET /settings includes the Dashboard & Analytics section heading."""
        with app.app_context():
            resp = auth_client.get("/settings")
            assert resp.status_code == 200
            assert b"Dashboard &" in resp.data and b"Analytics" in resp.data


class TestDefaultGridAccountPicker:
    """The D4 / A1 gate on the Default Grid Account picker's options."""

    def test_picker_excludes_amortizing_loan(self, app, auth_client, seed_user):
        """The general section's picker offers cash kinds, never a loan.

        The resolver refuses an amortizing default (finding B-3), so the
        picker must not offer one -- a selectable option the resolver
        ignores would be a lie in the UI.
        """
        with app.app_context():
            from app.services import account_service  # pylint: disable=import-outside-toplevel
            from decimal import Decimal  # pylint: disable=import-outside-toplevel

            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Picker Gate Mortgage",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.commit()

            resp = auth_client.get("/settings")

            assert resp.status_code == 200
            # Positive control: the cash account IS offered.
            assert b"Checking" in resp.data
            # The loan is not.
            assert b"Picker Gate Mortgage" not in resp.data


class TestTheTaxRatesSectionShowsTheLawReadOnly:
    """The Settings tax section renders the law, read-only (plan step salary:X-at-1).

    The law lives in :mod:`app.tax_law` and nothing in the app writes it
    (ruling R-SAL74), so the section is a view of whatever law the app prices
    on: a made-up one installed here shows its own figures and source, and no
    form on the section posts anywhere.
    """

    def test_the_section_shows_the_installed_laws_figures_and_sources(
        self, app, auth_client, seed_user, tax_law,
    ):
        """Every figure is the installed law's, with the year's source listed."""
        tax_law(TaxLaw(years=(made_up_year(
            2031,
            federal=made_up_federal(),
            fica=made_up_fica(),
            states={"NC": made_up_state(Decimal("0.0612"))},
        ),)))
        with app.app_context():
            html = auth_client.get("/settings?section=tax").data.decode()

        assert "The newest tax year in the app is 2031" in html
        assert "6.12%" in html            # the state rate
        assert "$176,100" in html         # the Social Security wage base
        assert "$11,600" in html          # a federal rung boundary
        assert str(escape(MADE_UP_SOURCES[0])) in html  # autoescaped, as rendered

    def test_no_form_on_the_section_posts_anywhere(self, app, auth_client, seed_user):
        """The shipped law renders with no form: nothing on the page edits it."""
        with app.app_context():
            html = auth_client.get("/settings?section=tax").data.decode()

        section = html[html.index("Tax Rates</h5>"):]
        assert "Federal Tax Brackets" in section  # positive control: the section rendered
        assert "<form" not in section
