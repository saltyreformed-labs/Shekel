"""Auth route tests: POST /change-password and its edge cases, session invalidation, and the
password form's strength meter and toggle.
"""

from datetime import datetime, timezone

from app.extensions import db
from app.models.user import User


class TestPasswordChange:
    """Tests for POST /change-password."""

    def test_change_password_success(self, app, auth_client, seed_user):
        """POST /change-password with valid data changes the password."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Password changed successfully" in response.data

            # Verify the user can log in with the new password.
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "newpassword12",
            }, follow_redirects=False)
            assert login_resp.status_code == 302

    def test_change_password_wrong_current(self, app, auth_client, seed_user):
        """POST /change-password with wrong current password shows error."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "wrongpassword",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Current password is incorrect" in response.data

    def test_change_password_mismatch(self, app, auth_client, seed_user):
        """POST /change-password with mismatched new/confirm shows error."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "differentpass12",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"New password and confirmation do not match" in response.data

    def test_change_password_too_short(self, app, auth_client, seed_user):
        """POST /change-password with password under 12 chars shows error."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "short",
                "confirm_password": "short",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"at least 12 characters" in response.data

    def test_change_password_requires_login(self, app, client):
        """POST /change-password without login redirects to login."""
        with app.app_context():
            response = client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")


class TestSessionManagement:
    """Tests for session invalidation functionality."""

    def test_invalidate_sessions(self, app, auth_client, seed_user):
        """POST /invalidate-sessions sets trusts_ended_at on user."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            response = auth_client.post(
                "/invalidate-sessions", follow_redirects=True
            )
            after = datetime.now(timezone.utc)

            assert response.status_code == 200
            assert b"All other sessions have been logged out" in response.data

            # Reload user from database.
            user = db.session.get(User, seed_user["user"].id)
            # Verify timestamp is recent, not just non-null (audit section 43 smell fix).
            assert before <= user.trusts_ended_at <= after

    def test_invalidate_sessions_current_session_survives(self, app, auth_client, seed_user):
        """Current session remains valid after invalidation."""
        with app.app_context():
            auth_client.post("/invalidate-sessions")

            # The current session should still work -- not redirected to login.
            # The Security page is /security since ruling plan#3435.
            response = auth_client.get("/security", follow_redirects=False)
            assert response.status_code == 200

    def test_password_change_invalidates_sessions(self, app, auth_client, seed_user):
        """Password change sets trusts_ended_at."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            })
            after = datetime.now(timezone.utc)

            # Reload user from database.
            user = db.session.get(User, seed_user["user"].id)
            # Verify timestamp is recent, not just non-null (audit section 43 smell fix).
            assert before <= user.trusts_ended_at <= after

    def test_invalidate_sessions_requires_login(self, app, client):
        """POST /invalidate-sessions without login redirects to login."""
        with app.app_context():
            response = client.post(
                "/invalidate-sessions", follow_redirects=False
            )
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")


class TestPasswordChangeEdgeCases:
    """Edge-case tests for POST /change-password.

    Covers same-as-current password, double submission, and
    whitespace-only passwords to document security-relevant behavior.
    """

    def test_change_password_same_as_current(self, app, auth_client, seed_user):
        """Changing password to the same value succeeds (no reuse check).

        The auth service does not prevent password reuse. This test
        documents the current behavior. Consider rejecting same-as-current
        for improved security posture.
        """
        with app.app_context():
            # First change to a password that meets the 12-char minimum,
            # since the seed user's initial password ("testpass") is only 8 chars.
            resp1 = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "testpass1234",
                "confirm_password": "testpass1234",
            }, follow_redirects=True)
            assert resp1.status_code == 200
            assert b"Password changed successfully" in resp1.data

            # Now try to change to the exact same password.
            resp2 = auth_client.post("/change-password", data={
                "current_password": "testpass1234",
                "new_password": "testpass1234",
                "confirm_password": "testpass1234",
            }, follow_redirects=True)

            # App allows reusing current password (consider rejecting for security).
            assert resp2.status_code == 200
            assert b"Password changed successfully" in resp2.data

            # Verify the password still works for login.
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass1234",
            }, follow_redirects=False)
            assert login_resp.status_code == 302

    def test_change_password_double_submit(self, app, auth_client, seed_user):
        """Second password change fails because current password already changed.

        Simulates a double-submit scenario: the first change succeeds,
        making the second attempt's 'current_password' incorrect.
        """
        with app.app_context():
            # First change: testpass -> newpassword12.
            resp1 = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            }, follow_redirects=True)
            assert resp1.status_code == 200
            assert b"Password changed successfully" in resp1.data

            # Second change: tries to use "testpass" as current, but it's
            # now "newpassword12".
            resp2 = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "anotherpass12",
                "confirm_password": "anotherpass12",
            }, follow_redirects=True)
            assert resp2.status_code == 200
            assert b"Current password is incorrect" in resp2.data

            # Verify the password is still "newpassword12" (not "anotherpass12").
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "newpassword12",
            }, follow_redirects=False)
            assert login_resp.status_code == 302

    def test_change_password_whitespace_only(self, app, auth_client, seed_user):
        """Whitespace-only password of sufficient length is accepted.

        The auth service validates only minimum length (12 chars) and does
        not strip whitespace from passwords. This test documents that an
        all-spaces password is accepted. Consider adding whitespace
        validation for improved security.
        """
        with app.app_context():
            whitespace_password = "            "  # 12 spaces
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": whitespace_password,
                "confirm_password": whitespace_password,
            }, follow_redirects=True)

            # App accepts whitespace-only passwords (consider rejecting for security).
            assert response.status_code == 200
            assert b"Password changed successfully" in response.data

            # Verify the whitespace password works for login.
            auth_client.post("/logout")
            login_resp = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": whitespace_password,
            }, follow_redirects=False)
            assert login_resp.status_code == 302


# ---------------------------------------------------------------------------
# Password-form chrome (audit F-089 / F-090 / C-11)
# ---------------------------------------------------------------------------


class TestPasswordFormChrome:
    """Sanity checks on the rendered HTML for the strength meter and toggle.

    These tests assert presence of the data attributes that the JS
    binders look for, so a future template refactor that drops the
    attributes does not silently disable the meter or toggle.
    """

    def test_register_form_has_strength_meter_attributes(
        self, app, client,
    ):
        """The /register form exposes the meter wiring.

        ``data-password-input`` on the input is what
        ``password_strength.js::bind`` queries; the matching
        ``data-password-meter-for=password`` container hosts the
        progress bar.  Both must be present together.
        """
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"data-password-input" in response.data
            assert b'data-password-meter-for="password"' in response.data
            assert b"data-password-meter-bar" in response.data

    def test_register_form_has_password_toggle(self, app, client):
        """The /register form has show/hide toggle buttons.

        ``data-action='password-toggle'`` is the selector that
        ``password_toggle.js`` uses to bind the click listener; the
        ``data-target`` attribute names the input id to flip.
        """
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"data-action=\"password-toggle\"" in response.data
            assert b"data-target=\"password\"" in response.data
            assert b"data-target=\"confirm_password\"" in response.data

    def test_register_form_loads_zxcvbn_and_meter_script(
        self, app, client,
    ):
        """The /register form loads zxcvbn and the meter script.

        Without the vendored zxcvbn asset the meter falls back to
        empty (see ``password_strength.js`` clearMeter), which is a
        silent UX downgrade.  Asserting the script tags here catches
        a future template that loses the include.
        """
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"vendor/zxcvbn/zxcvbn.js" in response.data
            assert b"js/password_strength.js" in response.data
            assert b"js/password_toggle.js" in response.data

    def test_login_form_has_password_toggle(self, app, client):
        """The /login form carries the show/hide toggle and its loader.

        Login does not get the strength meter (no zxcvbn analysis on
        an existing password), but the toggle is in scope so a user
        verifying a typo sees the actual characters they typed.
        """
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"data-action=\"password-toggle\"" in response.data
            assert b"js/password_toggle.js" in response.data
            assert b"vendor/zxcvbn/zxcvbn.js" not in response.data

    def test_security_panel_has_strength_meter_and_toggle(
        self, app, auth_client,
    ):
        """The Security page exposes meter and toggle wiring.

        Mirror of the /register check for the change-password form.  The page
        is /security since ruling plan#3435 moved it out of Settings.
        """
        with app.app_context():
            response = auth_client.get("/security")
            assert response.status_code == 200
            assert b"data-password-input" in response.data
            assert b'data-password-meter-for="new_password"' in response.data
            assert b"data-action=\"password-toggle\"" in response.data
            # Scripts loaded on the Security page, not on every page.
            assert b"vendor/zxcvbn/zxcvbn.js" in response.data
            assert b"js/password_strength.js" in response.data
            assert b"js/password_toggle.js" in response.data
