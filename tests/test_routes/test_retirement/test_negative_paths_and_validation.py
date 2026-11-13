"""The retirement routes' negative paths and their render-on-error validation."""

from datetime import date
from decimal import Decimal

from app.models.pension_profile import PensionProfile
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from app.models.user import UserSettings

from ._helpers import _create_salary_profile, _create_pension


class TestRetirementNegativePaths:
    """Negative-path and boundary tests for retirement routes."""

    def test_create_pension_missing_name(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST without name is rejected by schema (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_missing_hire_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST without hire_date is rejected (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "State Pension",
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_negative_multiplier(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST with negative benefit_multiplier is rejected by Range(min=0, min_inclusive=False)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Pension",
            "benefit_multiplier": "-0.01",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_retirement_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date before hire date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
            "planned_retirement_date": "2019-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_retirement_in_past_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date in the past is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Past Retirement",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2010-01-01",
            "planned_retirement_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_earliest_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Earliest retirement date before hire date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Earliest",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
            "earliest_retirement_date": "2019-06-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_planned_before_earliest_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date before earliest retirement date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Backwards Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2010-01-01",
            "earliest_retirement_date": "2050-01-01",
            "planned_retirement_date": "2045-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_valid_dates_accepted(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid date ordering is accepted and pension is created."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Good Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2015-01-01",
            "earliest_retirement_date": "2045-01-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302

        pension = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id, name="Good Dates",
        ).first()
        assert pension is not None
        assert pension.hire_date == date(2015, 1, 1)
        assert pension.earliest_retirement_date == date(2045, 1, 1)
        assert pension.planned_retirement_date == date(2050, 1, 1)

    def test_update_pension_retirement_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Updating planned_retirement_date to before existing hire_date is rejected."""
        pension = _create_pension(seed_user, db.session)
        # pension.hire_date is 2018-07-01
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        assert b"Must be after hire date" in resp.data
        assert b"is-invalid" in resp.data

        # Pension should be unchanged.
        db.session.expire_all()
        after = db.session.get(PensionProfile, pension.id)
        assert after.planned_retirement_date == date(2048, 7, 1)

    def test_edit_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET edit for nonexistent pension returns 404 (security)."""
        resp = auth_client.get("/retirement/pension/999999/edit")
        assert resp.status_code == 404

    def test_update_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST update for nonexistent pension returns 404 (security)."""
        resp = auth_client.post("/retirement/pension/999999", data={
            "name": "Ghost",
            "benefit_multiplier": "2.0",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 404

    def test_delete_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST delete for nonexistent pension returns 404 (security)."""
        resp = auth_client.post("/retirement/pension/999999/delete")
        assert resp.status_code == 404

    def test_edit_pension_idor_no_data_leaked(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """IDOR GET to edit pension does not leak victim's pension data."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Secret Pension",
            benefit_multiplier=Decimal("0.02500"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 404
        assert b"Secret Pension" not in resp.data
        assert b"0.02500" not in resp.data

    def test_update_settings_invalid_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-numeric SWR is handled gracefully; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        # The route's try/except passes on conversion failure, leaving "abc"
        # as-is. The schema then rejects it as not a valid Decimal.
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "abc",
        })
        assert resp.status_code == 422
        # Designed fragment: the assumptions rail re-rendered with field
        # errors must carry the marker header so the global app.js
        # listener swaps it (replaced the retirement_controls.js shim).
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_negative_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Negative SWR as percentage: -5 converts to -0.05, rejected by Range(min=0)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_zero_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """SWR of 0% converts to 0.00, which is valid per Range(min=0)."""
        # 0 / 100 = 0.00, which passes Range(min=0, max=1).
        # The gap calculator would need to handle division by zero separately.
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "0",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        # 0 / 100 = 0.0000
        assert settings.safe_withdrawal_rate == Decimal("0.0000")

    def test_update_settings_invalid_tax_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-numeric tax rate is handled; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "abc",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_tax_rate_over_100(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Tax rate 150% converts to 1.50, rejected by Range(max=1)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_invalid_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-date retirement date is rejected; original date preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_date = settings.planned_retirement_date

        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": "not-a-date",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.planned_retirement_date == orig_date

    def test_create_pension_login_required(self, client, db):
        """Unauthenticated POST to create pension redirects to login."""
        resp = client.post("/retirement/pension", data={
            "name": "Sneaky",
            "benefit_multiplier": "2.0",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_update_settings_login_required(self, client, db):
        """Unauthenticated POST to update settings redirects to login."""
        resp = client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestRetirementValidationUX:
    """Tests for render-on-error validation UX with field highlights and data preservation."""

    def test_pension_validation_error_preserves_all_fields(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """All submitted field values are preserved in the re-rendered form on error."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "My Updated Name",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.500",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "earliest_retirement_date": "2040-01-01",
            "planned_retirement_date": "2017-01-01",  # Before hire -> error
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # Every submitted value must appear in the re-rendered form.
        assert 'value="My Updated Name"' in html
        assert 'value="2.500"' in html
        assert 'value="3"' in html
        assert 'value="2018-07-01"' in html
        assert 'value="2040-01-01"' in html
        assert 'value="2017-01-01"' in html

    def test_pension_validation_error_highlights_invalid_field(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Invalid field gets is-invalid class and invalid-feedback message."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "is-invalid" in html
        assert "invalid-feedback" in html
        assert "Must be after hire date" in html

    def test_pension_validation_error_does_not_highlight_valid_fields(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Only the invalid field gets is-invalid; valid fields do not."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "Must be after hire date" in html
        # Only planned_retirement_date should have is-invalid.
        assert html.count("is-invalid") == 1

    def test_pension_validation_multiple_errors(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Multiple date fields with errors are all highlighted."""
        pension = _create_pension(seed_user, db.session)
        # Both earliest and planned are before hire date (2018-07-01).
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "earliest_retirement_date": "2017-06-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # Both fields should have errors.
        assert html.count("is-invalid") >= 2
        assert html.count("invalid-feedback") >= 2

    def test_pension_valid_submission_still_works(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid pension update still redirects and persists changes (regression)."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Regression Test",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.000",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Regression Test"
        assert pension.benefit_multiplier == Decimal("0.02000")

    def test_pension_validation_returns_422_not_redirect(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Validation failure returns 422 HTML, not a 302 redirect."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        assert resp.content_type.startswith("text/html")
        assert "Location" not in resp.headers

    def test_pension_form_data_not_present_on_get(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET renders the form from the pension model with no error indicators."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "is-invalid" not in html
        assert "invalid-feedback" not in html
        assert "State Pension" in html

    def test_settings_validation_error_preserves_form_data(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Settings validation error re-renders with submitted values preserved."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "20",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # The submitted SWR should be in the form (original user input).
        assert 'value="-5"' in html
        assert "is-invalid" in html

    def test_settings_validation_error_highlights_field(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Settings error highlights the invalid field with error message."""
        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "is-invalid" in html
        assert "invalid-feedback" in html

    def test_settings_valid_submission_still_works(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid settings POST still redirects and persists (regression)."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "3.5",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "22",
        })
        assert resp.status_code == 302
        db.session.expire_all()
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert settings.safe_withdrawal_rate == Decimal("0.0350")
        assert settings.planned_retirement_date == date(2055, 1, 1)

    def test_pension_error_then_success(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A failed submission does not poison subsequent successful submissions."""
        pension = _create_pension(seed_user, db.session)
        # First: invalid submission.
        resp1 = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Attempt 1",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp1.status_code == 422

        # Second: corrected submission.
        resp2 = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Attempt 2",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp2.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Attempt 2"
        assert pension.planned_retirement_date == date(2050, 1, 1)

    def test_pension_select_fields_preserve_selection(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Select dropdown preserves the selected salary profile on error re-render."""
        import re

        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Select Test",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # The salary profile option should be selected.
        pattern = rf'value="{profile.id}"\s+selected'
        assert re.search(pattern, html), (
            f"Salary profile {profile.id} option not selected in re-rendered form"
        )

    def test_pension_empty_date_handled(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Empty planned_retirement_date does not crash -- treated as omitted."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "No Date",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "",
        })
        # Empty optional date is stripped by pre_load and omitted from update.
        assert resp.status_code == 302
