"""
Shekel Budget App -- YTD Tax Checkpoint Route Tests (T-P2)

Route-level assertions for ``POST /salary/<id>/checkpoint``
(``salary.save_ytd_checkpoint``): the update-from-stub write behind the
analytics Taxes tab's checkpoint card.  Covers the happy-path insert, the
same-date upsert, the schema validation failures (negative, component >
gross, future date, garbage date), the cross-user IDOR 404, and the
non-HTMX redirect convention.

CSRF is disabled in the testing config (``WTF_CSRF_ENABLED = False``), so
the posts omit the token; the card template still emits it for production.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.routes.salary import checkpoint as checkpoint_module
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from tests._test_helpers import start_test_pay_list

_VALID_FORM = {
    "as_of_date": "2026-06-30",
    "ytd_gross": "60000.00",
    "ytd_federal": "6000.00",
    "ytd_state": "2400.00",
    "ytd_social_security": "3720.00",
    "ytd_medicare": "870.00",
}


def _make_profile(seed_user, name="Checkpoint Route Profile"):
    """Create and flush a minimal active salary profile for the seeded user.

    The checkpoint route does not regenerate transactions, so no template
    or recurrence rule is needed (unlike the raise / deduction routes).
    """
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("5000.00"))  # $130,000.00 a year / 26
    db.session.flush()
    return profile


def _count_checkpoints(profile_id):
    """Return the number of checkpoints for a profile."""
    return (
        db.session.query(YtdTaxCheckpoint)
        .filter_by(salary_profile_id=profile_id)
        .count()
    )


class TestHappyPath:
    """A valid HTMX post inserts a checkpoint and returns the card."""

    def test_insert_returns_card_partial(self, app, auth_client, seed_user):
        """Valid post -> 200, card shows the measured figures, row created."""
        with app.app_context():
            profile = _make_profile(seed_user)

            response = auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=dict(_VALID_FORM),
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b'id="ytd-checkpoint-card"' in response.data
            assert b"Measured through Jun 30, 2026" in response.data
            # money macro renders 60000.00 as $60,000.00.
            assert b"60,000.00" in response.data
            assert _count_checkpoints(profile.id) == 1

            cp = (
                db.session.query(YtdTaxCheckpoint)
                .filter_by(salary_profile_id=profile.id)
                .one()
            )
            assert cp.ytd_gross == Decimal("60000.00")
            assert cp.ytd_medicare == Decimal("870.00")
            assert cp.as_of_date == date(2026, 6, 30)


class TestUpsert:
    """Re-posting the same date updates the row in place."""

    def test_resave_same_date_updates(self, app, auth_client, seed_user):
        """Two posts for 2026-06-30 leave ONE row carrying the new figures."""
        with app.app_context():
            profile = _make_profile(seed_user)

            auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=dict(_VALID_FORM),
                headers={"HX-Request": "true"},
            )
            corrected = dict(_VALID_FORM)
            corrected["ytd_gross"] = "61000.00"
            corrected["ytd_federal"] = "6100.00"
            response = auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=corrected,
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert _count_checkpoints(profile.id) == 1
            cp = (
                db.session.query(YtdTaxCheckpoint)
                .filter_by(salary_profile_id=profile.id)
                .one()
            )
            assert cp.ytd_gross == Decimal("61000.00")
            assert cp.ytd_federal == Decimal("6100.00")

    def test_new_date_inserts_second_row(self, app, auth_client, seed_user):
        """A different date adds a second checkpoint (history-keeping)."""
        with app.app_context():
            profile = _make_profile(seed_user)
            auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=dict(_VALID_FORM),
                headers={"HX-Request": "true"},
            )
            earlier = dict(_VALID_FORM)
            earlier["as_of_date"] = "2026-03-31"
            earlier["ytd_gross"] = "30000.00"
            earlier["ytd_federal"] = "3000.00"
            earlier["ytd_state"] = "1200.00"
            earlier["ytd_social_security"] = "1860.00"
            earlier["ytd_medicare"] = "435.00"
            auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=earlier,
                headers={"HX-Request": "true"},
            )
            assert _count_checkpoints(profile.id) == 2


class TestValidationFailures:
    """Schema rejections return 422 and persist nothing (HTMX path)."""

    def _post_invalid(self, auth_client, profile_id, overrides):
        """POST a form with *overrides* applied to the valid baseline."""
        form = dict(_VALID_FORM)
        form.update(overrides)
        return auth_client.post(
            f"/salary/{profile_id}/checkpoint",
            data=form,
            headers={"HX-Request": "true"},
        )

    def test_negative_amount_rejected(self, app, auth_client, seed_user):
        """A negative gross is a 422 and inserts nothing.

        The 422 body is a designed fragment (the card re-rendered with
        field errors), so it must carry the marker header that opts it
        back into swapping (the app-wide htmx config drops unmarked 4xx
        bodies; the header replaced the tax_checkpoint.js shim).
        """
        with app.app_context():
            profile = _make_profile(seed_user)
            response = self._post_invalid(
                auth_client, profile.id, {"ytd_gross": "-5.00"},
            )
            assert response.status_code == 422
            assert response.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            assert _count_checkpoints(profile.id) == 0

    def test_component_exceeds_gross_rejected(self, app, auth_client, seed_user):
        """federal > gross is a 422 (the schema cross-field rule)."""
        with app.app_context():
            profile = _make_profile(seed_user)
            response = self._post_invalid(
                auth_client, profile.id,
                {"ytd_gross": "1000.00", "ytd_federal": "2000.00"},
            )
            assert response.status_code == 422
            assert b"exceeds ytd_gross" in response.data
            assert _count_checkpoints(profile.id) == 0

    def test_future_date_rejected(self, app, auth_client, seed_user):
        """An as_of_date after today is a 422 (no future stubs)."""
        with app.app_context():
            profile = _make_profile(seed_user)
            response = self._post_invalid(
                auth_client, profile.id, {"as_of_date": "2099-12-31"},
            )
            assert response.status_code == 422
            assert _count_checkpoints(profile.id) == 0

    def test_garbage_date_rejected(self, app, auth_client, seed_user):
        """A non-date as_of_date is a 422."""
        with app.app_context():
            profile = _make_profile(seed_user)
            response = self._post_invalid(
                auth_client, profile.id, {"as_of_date": "not-a-date"},
            )
            assert response.status_code == 422
            assert _count_checkpoints(profile.id) == 0

    def test_missing_required_field_rejected(self, app, auth_client, seed_user):
        """A missing money field is a 422."""
        with app.app_context():
            profile = _make_profile(seed_user)
            form = dict(_VALID_FORM)
            del form["ytd_gross"]
            response = auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=form,
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 422
            assert _count_checkpoints(profile.id) == 0


class TestHandledDbFailure:
    """The DB-tier failure path renders a designed 500 banner card."""

    def test_save_failure_returns_designed_500_card(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A SQLAlchemyError save renders the card + banner, marked to swap.

        Before the marker convention this handled 500 was DEAD UI: the
        client could not tell the designed banner card from an unhandled
        crash page, so the body was dropped and the failure was silent
        (flagged in the S5 as-built).  The marker header is what makes
        an unhandled crash page distinguishable -- it never carries one.
        """
        with app.app_context():
            profile = _make_profile(seed_user)

            def _boom(_profile_id, _figures):
                raise SQLAlchemyError("connection lost")

            monkeypatch.setattr(
                checkpoint_module.tax_withholding_service,
                "save_checkpoint",
                _boom,
            )
            response = auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=dict(_VALID_FORM),
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 500
            assert response.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            assert b"Failed to save checkpoint" in response.data
            assert _count_checkpoints(profile.id) == 0


class TestIdor:
    """A cross-user profile id 404s (404 for both 'not found' and 'not yours')."""

    def test_other_users_profile_404(
        self, app, auth_client, seed_user, seed_second_user,
    ):
        """seed_user posting to seed_second_user's profile gets 404, no write."""
        with app.app_context():
            other_profile = _make_profile(
                seed_second_user, name="Second User Profile",
            )
            response = auth_client.post(
                f"/salary/{other_profile.id}/checkpoint",
                data=dict(_VALID_FORM),
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 404
            assert _count_checkpoints(other_profile.id) == 0

    def test_nonexistent_profile_404(self, app, auth_client, seed_user):
        """A non-existent profile id 404s the same as a cross-user id."""
        with app.app_context():
            response = auth_client.post(
                "/salary/999999/checkpoint",
                data=dict(_VALID_FORM),
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 404


class TestNonHtmx:
    """A full-page (non-HTMX) post redirects, per the blueprint convention."""

    def test_success_redirects(self, app, auth_client, seed_user):
        """A valid non-HTMX post saves the row and 302-redirects to analytics."""
        with app.app_context():
            profile = _make_profile(seed_user)
            response = auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=dict(_VALID_FORM),
            )
            assert response.status_code == 302
            assert "/analytics" in response.headers["Location"]
            assert _count_checkpoints(profile.id) == 1

    def test_validation_failure_redirects(self, app, auth_client, seed_user):
        """A non-HTMX validation failure flashes + redirects, no write."""
        with app.app_context():
            profile = _make_profile(seed_user)
            form = dict(_VALID_FORM)
            form["ytd_gross"] = "-5.00"
            response = auth_client.post(
                f"/salary/{profile.id}/checkpoint",
                data=form,
            )
            assert response.status_code == 302
            assert "/analytics" in response.headers["Location"]
            assert _count_checkpoints(profile.id) == 0
