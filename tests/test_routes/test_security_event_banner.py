"""Route-level tests for the security-event "was this you?" banner.

Audit reference: F-091 (Low) / commit C-16 of the 2026-04-15 security
remediation plan.

These tests assert the integration between the four credential-changing
routes and the new security-event columns on ``auth.users``:

  * ``POST /change-password``      -> kind = ``password_changed``
  * ``POST /mfa/confirm``          -> kind = ``mfa_enabled``
  * ``POST /mfa/disable``          -> kind = ``mfa_disabled``
  * ``POST /mfa/regenerate-backup-codes`` -> kind = ``backup_codes_regenerated``

plus the dismiss endpoint:

  * ``POST /security-event/dismiss`` -> stamps acknowledged_at, hides banner

plus the banner-rendering side at ``GET /dashboard``.

Each route test asserts BOTH the row-level state change AND the
banner visibility on the next page load -- the contract is "after
this change, the user sees a banner explaining what happened" and
that contract spans the route's commit and the context processor's
read.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult
from app.utils.field_encryption import encrypt_secret


# --- Helpers ---------------------------------------------------------------


def _enable_mfa(user_id: int) -> str:
    """Create a confirmed MFA row and return the plaintext secret."""
    secret = "JBSWY3DPEHPK3PXP"
    config = MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=encrypt_secret(secret),
        backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa"]),
        confirmed_at=datetime.now(timezone.utc),
    )
    db.session.add(config)
    db.session.commit()
    return secret


def _reload(user_id: int) -> User:
    """Re-read the user row, dropping the identity-map cached copy."""
    db.session.expire_all()
    return db.session.get(User, user_id)


# --- Per-route stamping ----------------------------------------------------


class TestChangePasswordStampsSecurityEvent:
    """``/change-password`` writes ``password_changed`` to the user row."""

    def test_successful_password_change_stamps_event(
        self, app, auth_client, seed_user,
    ):
        """After a valid change, the row carries the right kind + timestamp."""
        user_id = seed_user["user"].id
        before = datetime.now(timezone.utc)

        response = auth_client.post("/change-password", data={
            "current_password": "testpass",
            "new_password": "newpassword12",
            "confirm_password": "newpassword12",
        }, follow_redirects=True)
        assert response.status_code == 200

        after = datetime.now(timezone.utc)
        user = _reload(user_id)
        assert user.last_security_event_kind == "password_changed"
        assert before <= user.last_security_event_at <= after
        assert user.last_security_event_acknowledged_at is None

    def test_failed_password_change_does_not_stamp(
        self, app, auth_client, seed_user,
    ):
        """A wrong-current-password failure leaves the columns untouched."""
        user_id = seed_user["user"].id
        auth_client.post("/change-password", data={
            "current_password": "wrongpassword",
            "new_password": "newpassword12",
            "confirm_password": "newpassword12",
        }, follow_redirects=True)

        user = _reload(user_id)
        assert user.last_security_event_at is None
        assert user.last_security_event_kind is None


class TestMfaConfirmStampsSecurityEvent:
    """``/mfa/confirm`` writes ``mfa_enabled`` to the user row."""

    def test_successful_mfa_confirm_stamps_event(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """Initial MFA enrolment surfaces an mfa_enabled banner."""
        user_id = seed_user["user"].id
        monkeypatch.setattr(
            mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
        )
        # Visit setup so the pending secret exists.
        auth_client.get("/mfa/setup")
        before = datetime.now(timezone.utc)

        response = auth_client.post(
            "/mfa/confirm", data={"totp_code": "123456"},
        )
        assert response.status_code == 200
        after = datetime.now(timezone.utc)

        user = _reload(user_id)
        assert user.last_security_event_kind == "mfa_enabled"
        assert before <= user.last_security_event_at <= after

    def test_invalid_mfa_confirm_does_not_stamp(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A bad code keeps the row clean."""
        user_id = seed_user["user"].id
        monkeypatch.setattr(
            mfa_service, "verify_totp_setup_code", lambda s, c: None,
        )
        auth_client.get("/mfa/setup")
        auth_client.post(
            "/mfa/confirm", data={"totp_code": "000000"},
            follow_redirects=False,
        )

        user = _reload(user_id)
        assert user.last_security_event_kind is None


class TestMfaDisableStampsSecurityEvent:
    """``/mfa/disable`` writes ``mfa_disabled`` to the user row."""

    def test_successful_mfa_disable_stamps_event(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """Disable surfaces an mfa_disabled banner."""
        user_id = seed_user["user"].id
        _enable_mfa(user_id)
        monkeypatch.setattr(
            mfa_service, "verify_totp_code",
            lambda mc, c: TotpVerificationResult.ACCEPTED,
        )
        before = datetime.now(timezone.utc)

        response = auth_client.post("/mfa/disable", data={
            "current_password": "testpass",
            "totp_code": "123456",
        }, follow_redirects=True)
        assert response.status_code == 200

        after = datetime.now(timezone.utc)
        user = _reload(user_id)
        assert user.last_security_event_kind == "mfa_disabled"
        assert before <= user.last_security_event_at <= after


class TestRegenerateBackupCodesStampsSecurityEvent:
    """``/mfa/regenerate-backup-codes`` writes ``backup_codes_regenerated``."""

    def test_successful_regenerate_stamps_event(
        self, app, auth_client, seed_user,
    ):
        """Regen surfaces a backup_codes_regenerated banner."""
        user_id = seed_user["user"].id
        _enable_mfa(user_id)
        before = datetime.now(timezone.utc)

        response = auth_client.post("/mfa/regenerate-backup-codes")
        assert response.status_code == 200

        after = datetime.now(timezone.utc)
        user = _reload(user_id)
        assert user.last_security_event_kind == "backup_codes_regenerated"
        assert before <= user.last_security_event_at <= after


# --- Dismiss endpoint ------------------------------------------------------


class TestDismissEndpoint:
    """``POST /security-event/dismiss`` clears the banner."""

    def _stamp(self, user_id: int, when=None):
        """Mark a security event so the banner is visible."""
        from app.utils.security_events import (  # pylint: disable=import-outside-toplevel
            SecurityEventKind,
            record_security_event,
        )
        user = db.session.get(User, user_id)
        record_security_event(
            user, SecurityEventKind.PASSWORD_CHANGED, now=when,
        )
        db.session.commit()

    def test_dismiss_writes_acknowledged_at(
        self, app, auth_client, seed_user,
    ):
        """A POST stamps acknowledged_at to the current moment."""
        user_id = seed_user["user"].id
        self._stamp(user_id, when=datetime.now(timezone.utc) - timedelta(minutes=5))
        before = datetime.now(timezone.utc)

        response = auth_client.post("/security-event/dismiss")
        assert response.status_code in (204, 302)

        after = datetime.now(timezone.utc)
        user = _reload(user_id)
        assert user.last_security_event_acknowledged_at is not None
        assert before <= user.last_security_event_acknowledged_at <= after

    def test_htmx_dismiss_returns_204_empty_body(
        self, app, auth_client, seed_user,
    ):
        """An HTMX request gets a 204 with an empty body for outerHTML swap."""
        self._stamp(seed_user["user"].id)

        response = auth_client.post(
            "/security-event/dismiss",
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 204
        assert response.data == b""

    def test_non_htmx_dismiss_redirects_to_referer(
        self, app, auth_client, seed_user,
    ):
        """A normal POST redirects to a safe Referer or the dashboard."""
        self._stamp(seed_user["user"].id)

        response = auth_client.post(
            "/security-event/dismiss",
            headers={"Referer": "http://localhost/settings"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        # Local path only -- absolute URLs are stripped to the path.
        assert location.endswith("/settings") or location.endswith(
            "/dashboard"
        )

    def test_dismiss_with_unsafe_referer_falls_back_to_dashboard(
        self, app, auth_client, seed_user,
    ):
        """A javascript: Referer is rejected and the user lands on dashboard."""
        self._stamp(seed_user["user"].id)

        response = auth_client.post(
            "/security-event/dismiss",
            headers={"Referer": "javascript:alert(1)"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "javascript" not in location.lower()
        assert location.endswith("/dashboard")

    def test_dismiss_without_visible_banner_is_noop(
        self, app, auth_client, seed_user,
    ):
        """No banner -> no DB write; response still succeeds.

        The handler's defensive guard short-circuits the
        acknowledgement when ``banner_visible_for`` returns False.
        Idempotency: a duplicate dismiss POST after the first one
        succeeded must not raise and must not flip any state.
        """
        user_id = seed_user["user"].id
        # No event has been recorded -- banner not visible.
        before_ack = _reload(user_id).last_security_event_acknowledged_at

        response = auth_client.post(
            "/security-event/dismiss",
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 204

        after_ack = _reload(user_id).last_security_event_acknowledged_at
        assert before_ack == after_ack  # Both None.

    def test_dismiss_requires_login(self, app, client):
        """Anonymous request is bounced to login."""
        response = client.post(
            "/security-event/dismiss", follow_redirects=False,
        )
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "")


# --- Banner rendering on GET requests --------------------------------------


class TestBannerRendering:
    """The banner element appears on authenticated pages when visible."""

    def test_banner_present_after_password_change(
        self, app, auth_client, seed_user,
    ):
        """``GET /`` renders the banner after a fresh password change.

        The C-16 banner partial is included from base.html, so any
        authenticated page is sufficient to test the rendering side.
        Asserts the title copy from KIND_DISPLAY appears, NOT just the
        wrapper -- otherwise an empty inner template would still pass.
        """
        auth_client.post("/change-password", data={
            "current_password": "testpass",
            "new_password": "newpassword12",
            "confirm_password": "newpassword12",
        }, follow_redirects=True)

        response = auth_client.get("/dashboard")
        assert response.status_code == 200
        assert b"Your password was changed" in response.data
        assert b"security-event-banner" in response.data

    def test_banner_absent_when_no_event_recorded(
        self, app, auth_client, seed_user,
    ):
        """No event -> banner element absent from rendered HTML."""
        response = auth_client.get("/dashboard")
        assert response.status_code == 200
        assert b"security-event-banner" not in response.data

    def test_banner_absent_after_dismiss(
        self, app, auth_client, seed_user,
    ):
        """After dismiss, the next GET no longer carries the banner."""
        # Stamp an event then dismiss it.
        from app.utils.security_events import (  # pylint: disable=import-outside-toplevel
            SecurityEventKind,
            record_security_event,
        )
        user = seed_user["user"]
        record_security_event(user, SecurityEventKind.MFA_DISABLED)
        db.session.commit()

        # First GET: banner present.
        first = auth_client.get("/dashboard")
        assert b"security-event-banner" in first.data

        # Dismiss.
        auth_client.post(
            "/security-event/dismiss",
            headers={"HX-Request": "true"},
        )

        # Second GET: banner gone.
        second = auth_client.get("/dashboard")
        assert b"security-event-banner" not in second.data

    def test_banner_kind_specific_copy_renders(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """The MFA-enabled banner uses the mfa_enabled copy, not generic."""
        monkeypatch.setattr(
            mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
        )
        auth_client.get("/mfa/setup")
        auth_client.post("/mfa/confirm", data={"totp_code": "123456"})

        response = auth_client.get("/dashboard")
        assert response.status_code == 200
        # The MFA-enabled title must appear ...
        assert b"Two-factor authentication was enabled" in response.data
        # ... and the password-changed title (a different kind's copy)
        # must NOT, so we know the dispatch is keyed correctly.
        assert b"Your password was changed" not in response.data

    def test_banner_dismiss_form_includes_csrf_token(
        self, app, auth_client, seed_user,
    ):
        """The dismiss form carries a CSRF token field.

        Locates the form element by its action URL (which is unique to
        this endpoint) and asserts the CSRF input is inside it.  Form-
        scoped rather than banner-scoped so a future template tweak
        that nests / restructures the banner div tree does not break
        this assertion -- only a regression in the form's CSRF wiring
        would.
        """
        from app.utils.security_events import (  # pylint: disable=import-outside-toplevel
            SecurityEventKind,
            record_security_event,
        )
        record_security_event(
            seed_user["user"], SecurityEventKind.PASSWORD_CHANGED,
        )
        db.session.commit()

        response = auth_client.get("/dashboard")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        # Match the dismiss form by its action; capture everything up
        # to its closing tag.  The form's action URL is not used by
        # any other element on the page, so this anchor is stable.
        form_match = re.search(
            r'<form[^>]*action="[^"]*security-event/dismiss[^"]*"[^>]*>'
            r'(.*?)</form>',
            body,
            flags=re.DOTALL,
        )
        assert form_match is not None, (
            "dismiss form not found in response body"
        )
        assert 'name="csrf_token"' in form_match.group(1), (
            "dismiss form missing CSRF token field"
        )


# --- Cross-user banner isolation ------------------------------------------


class TestBannerCrossUserIsolation:
    """One user's banner does not appear on another user's pages.

    The helper-level isolation is exercised in
    ``test_utils/test_security_events.py`` against the same database
    session.  Here we additionally assert the route-level case via a
    fresh ``test_client`` that logs in as the second user explicitly
    so cookie-jar behaviour is unambiguous (the project's pre-existing
    ``second_auth_client`` fixture has a known interaction with the
    primary ``auth_client`` cookie state -- documented in
    ``test_integration/test_fixture_validation.py`` -- that we side-
    step here to keep the test focused on banner visibility, not
    fixture quirks).
    """

    def test_logged_in_second_user_does_not_see_first_users_banner(
        self, app, db, seed_user, second_user,
    ):
        """Stamping seed_user's row does not surface a banner for second_user.

        Builds a dedicated test_client, logs in as the SECOND user
        (independent cookie jar from any other fixture), and asserts
        the dashboard for that user is banner-free even though
        seed_user's row carries an unacknowledged event.
        """
        from app.utils.security_events import (  # pylint: disable=import-outside-toplevel
            SecurityEventKind,
            record_security_event,
        )
        # Stamp ONLY the primary user's row.
        record_security_event(
            seed_user["user"], SecurityEventKind.PASSWORD_CHANGED,
        )
        db.session.commit()

        # Fresh test_client, fresh cookie jar.
        client = app.test_client()
        login_resp = client.post("/login", data={
            "email": "other@shekel.local",
            "password": "otherpass",
        })
        assert login_resp.status_code == 302, (
            f"second-user login failed; got status {login_resp.status_code}"
        )

        response = client.get("/dashboard")
        assert response.status_code == 200
        # Confirm we ARE rendering as the second user.
        assert b"Other User" in response.data, (
            "Dashboard not rendered as the second user; cookie jar "
            "isolation issue or login failed silently."
        )
        # Banner-specific copy and the wrapper element must both be
        # absent for the second user, regardless of what the primary
        # user's row carries.
        assert b"Your password was changed" not in response.data
        assert b"security-event-banner" not in response.data
