"""
Shekel Budget App -- Auth Route Schema Wiring Tests (commit C-26)

These tests verify that every auth-blueprint POST handler routes its
form payload through Marshmallow before invoking auth_service or
mfa_service.  The schema layer is the only line of defence against:

  * F-041 -- inline ``request.form.get`` with no length, shape, or
    type validation, exposing the route to malformed inputs that
    would otherwise produce 500s or unbounded service calls.
  * F-163 -- megabyte-sized password / backup_code strings reaching
    bcrypt verification, where each comparison costs O(work_factor)
    seconds and an attacker can amplify request cost by orders of
    magnitude.

Each test exercises the route boundary (not the schema directly --
that file is ``tests/test_schemas/test_auth_validation.py``) and
asserts the externally visible response shape that an attacker would
see, plus -- where relevant -- that bcrypt was NOT called.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult
from tests._test_helpers import register_form_data


# ── /login ───────────────────────────────────────────────────────────


class TestLoginSchemaWiring:
    """POST /login validates payload through LoginSchema before bcrypt."""

    def test_oversized_password_rejected_before_bcrypt(self, app, client, seed_user):
        """F-163: a 1MB password is rejected without invoking bcrypt.

        The schema's Length(max=72) cap fires at the route boundary,
        so ``auth_service.verify_password`` (which calls bcrypt) must
        not be reached.  Patching it lets us assert that.
        """
        with app.app_context():
            with patch(
                "app.services.auth_service.bcrypt.checkpw",
                return_value=False,
            ) as mock_check:
                response = client.post("/login", data={
                    "email": "test@shekel.local",
                    "password": "x" * 100_000,
                }, follow_redirects=True)
                assert response.status_code == 200
                assert b"Invalid email or password" in response.data
                assert mock_check.call_count == 0, (
                    "bcrypt was reached despite oversized password; "
                    "the schema cap must run before authenticate()."
                )

    def test_oversized_email_rejected_generic_error(self, app, client):
        """An email > 255 chars produces the same generic error as bad creds.

        Distinct error messages would be an enumeration oracle ("yes
        the schema rejected this" vs. "no but the password was wrong"
        leak the same fact -- whether the email is valid for the
        schema -- in different ways).
        """
        with app.app_context():
            long_email = "a" * 250 + "@example.com"
            response = client.post("/login", data={
                "email": long_email,
                "password": "anything",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_stale_remember_on_signs_in_and_sets_no_cookie(
        self, app, client, seed_user,
    ):
        """A stale form's ``remember=on`` signs in and issues no remember cookie.

        "Remember me" is gone (plan#2173, ruling plan#2406), but a browser
        holding the old login page still posts the box.  The sign-in must
        succeed and must not set Flask-Login's ``remember_token``: the cookie
        could not be revoked, which is why it went.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
                "remember": "on",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/dashboard")
            set_cookies = response.headers.getlist("Set-Cookie")
            assert not [
                c for c in set_cookies if c.startswith("remember_token=")
            ], f"No remember_token may be issued; got {set_cookies!r}"

    def test_invalid_email_format_returns_generic_error(self, app, client):
        """Schema-level invalid email format produces generic flash."""
        with app.app_context():
            response = client.post("/login", data={
                "email": "not-an-email",
                "password": "testpass",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data


# ── /register ────────────────────────────────────────────────────────


class TestRegisterSchemaWiring:
    """POST /register validates payload through RegisterSchema."""

    def test_short_password_rejected_without_creating_user(self, app, client):
        """An 11-char password is rejected; no User row is created."""
        with app.app_context():
            count_before = db.session.query(User).count()
            response = client.post("/register", data=register_form_data(
                email="tooshort@example.com",
                display_name="Test",
                password="x" * 11,
                confirm_password="x" * 11,
            ), follow_redirects=True)
            assert response.status_code == 200
            assert b"at least 12 characters" in response.data
            assert db.session.query(User).count() == count_before

    def test_oversized_password_rejected_before_bcrypt(self, app, client):
        """F-163 on the register path: oversized password cannot reach hash_password."""
        with app.app_context():
            count_before = db.session.query(User).count()
            with patch(
                "app.services.auth_service.bcrypt.gensalt"
            ) as mock_gensalt:
                response = client.post("/register", data=register_form_data(
                    email="huge@example.com",
                    display_name="Big",
                    password="x" * 100_000,
                    confirm_password="x" * 100_000,
                ), follow_redirects=True)
                assert response.status_code == 200
                assert mock_gensalt.call_count == 0
            assert db.session.query(User).count() == count_before

    def test_password_mismatch_rejected(self, app, client):
        """Schema-level mismatch produces the canonical user-facing string."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="mismatch@example.com",
                display_name="Mismatch",
                password="longenoughpass",
                confirm_password="differentpass1",
            ), follow_redirects=True)
            assert response.status_code == 200
            assert b"Password and confirmation do not match" in response.data

    def test_invalid_email_rejected(self, app, client):
        """Schema-level invalid email format produces 'Invalid email format.'"""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="notanemail",
                display_name="X",
                password="longenoughpass",
                confirm_password="longenoughpass",
            ), follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email format" in response.data


# ── /change-password ─────────────────────────────────────────────────


class TestChangePasswordSchemaWiring:
    """POST /change-password validates payload through ChangePasswordSchema."""

    def test_oversized_current_password_rejected(self, app, auth_client, seed_user):
        """F-163: a 1MB current_password is rejected before bcrypt verify.

        ``auth_service.change_password`` calls bcrypt.checkpw on the
        current_password before any new-password work; the schema cap
        runs first and short-circuits the entire flow.
        """
        with app.app_context():
            with patch(
                "app.services.auth_service.bcrypt.checkpw",
                return_value=False,
            ) as mock_check:
                response = auth_client.post("/change-password", data={
                    "current_password": "x" * 100_000,
                    "new_password": "newvalidpass1",
                    "confirm_password": "newvalidpass1",
                }, follow_redirects=True)
                assert response.status_code == 200
                assert mock_check.call_count == 0

    def test_short_new_password_rejected(self, app, auth_client, seed_user):
        """An 8-char new_password produces 'at least 12 characters' flash."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "shortpw1",
                "confirm_password": "shortpw1",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"at least 12 characters" in response.data

    def test_missing_new_password_rejected(self, app, auth_client, seed_user):
        """Missing new_password is rejected with a field-level flash."""
        with app.app_context():
            response = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "confirm_password": "doesntmatter1",
            }, follow_redirects=True)
            assert response.status_code == 200
            # Flash present (some validation failure) and password not changed.
            user = (
                db.session.query(User)
                .filter_by(email="test@shekel.local")
                .first()
            )
            from app.services.auth_service import verify_password
            assert verify_password("testpass", user.password_hash)


# ── /reauth ──────────────────────────────────────────────────────────


class TestReauthSchemaWiring:
    """POST /reauth validates payload through ReauthSchema."""

    def test_oversized_password_rejected_before_bcrypt(self, app, auth_client, seed_user):
        """F-163: oversized password on reauth is rejected before bcrypt.

        ``auth_service.verify_password`` is the bcrypt entry point on
        the reauth path; the schema cap must run before any bcrypt
        comparison.
        """
        with app.app_context():
            with patch(
                "app.services.auth_service.bcrypt.checkpw",
                return_value=False,
            ) as mock_check:
                response = auth_client.post("/reauth", data={
                    "password": "x" * 100_000,
                }, follow_redirects=True)
                assert response.status_code == 200
                assert mock_check.call_count == 0
                assert b"Invalid password" in response.data


# ── /mfa/verify ──────────────────────────────────────────────────────


class TestMfaVerifySchemaWiring:
    """POST /mfa/verify validates payload through MfaVerifySchema."""

    def _start_mfa_pending_session(self, client, user_id):
        """Drop the user into the post-password / pre-MFA state."""
        with client.session_transaction() as sess:
            sess["_mfa_pending_user_id"] = user_id
            sess["_mfa_pending_next"] = None
            sess["_mfa_pending_at"] = (
                datetime.now(timezone.utc).isoformat()
            )

    def _enable_mfa(self, user_id):
        """Enable MFA with a known secret and three known backup codes."""
        secret = "JBSWY3DPEHPK3PXP"
        known_codes = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret(secret),
            backup_codes=mfa_service.hash_backup_codes(known_codes),
        )
        db.session.add(mfa_config)
        db.session.commit()
        return secret

    def test_oversized_backup_code_rejected_before_bcrypt(
        self, app, client, seed_user,
    ):
        """F-163: a 100KB backup_code never reaches bcrypt.

        Without the schema cap, ``mfa_service.verify_backup_code``
        would bcrypt-compare the oversized string against every stored
        backup hash (10 hashes by default).  The schema's
        Length(max=32) on backup_code rejects the submission first;
        ``bcrypt.checkpw`` must not be invoked.

        100,000 characters is the test payload size: comfortably above
        the schema's 32-char cap and well BELOW the request-body ceiling
        (``MAX_CONTENT_LENGTH``, 524,288 bytes, the one bound on a body:
        ruling R-BAL218), so the body reaches the schema and the schema
        is the layer being tested, not the WSGI framing layer.  *It said
        "above the WSGI 500KB form-size limit" until plan step
        ``balance:X-dk``, which was false (finding BAL-598): 100,000 was
        below that limit too, and a body above it would have been a 413
        before the schema ran.*
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            self._start_mfa_pending_session(client, seed_user["user"].id)

            with patch(
                "app.services.mfa_service.bcrypt.checkpw",
                return_value=False,
            ) as mock_check:
                response = client.post("/mfa/verify", data={
                    "backup_code": "x" * 100_000,
                }, follow_redirects=False)
                assert response.status_code == 200
                assert b"Invalid verification code" in response.data
                assert mock_check.call_count == 0, (
                    "bcrypt.checkpw was invoked despite the schema's "
                    "32-char backup_code cap.  F-163 regression."
                )

    def test_oversized_totp_code_rejected_before_pyotp(
        self, app, client, seed_user, monkeypatch,
    ):
        """A 100KB totp_code is rejected without invoking pyotp.

        The TOTP verifier already short-circuits non-6-digit inputs,
        but the schema cap saves the lookup-and-decrypt cost and the
        replay-table touch.  We assert the verifier is not called.
        """
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            self._start_mfa_pending_session(client, seed_user["user"].id)

            calls = []

            def _spy_verify(*args, **kwargs):
                calls.append((args, kwargs))
                return TotpVerificationResult.INVALID

            monkeypatch.setattr(
                mfa_service, "verify_totp_code", _spy_verify,
            )

            response = client.post("/mfa/verify", data={
                "totp_code": "1" * 100_000,
            }, follow_redirects=False)
            assert response.status_code == 200
            assert b"Invalid verification code" in response.data
            assert calls == [], (
                "verify_totp_code was invoked despite the schema's "
                "6-char totp_code cap.  F-163 regression."
            )

    def test_valid_backup_code_still_works(self, app, client, seed_user):
        """Happy path: a valid 8-char backup code consumes correctly."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            self._start_mfa_pending_session(client, seed_user["user"].id)
            response = client.post("/mfa/verify", data={
                "backup_code": "aaaaaaaa",
            }, follow_redirects=False)
            assert response.status_code == 302
            # Backup code consumed: only 2 of 3 hashes remain.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert len(config.backup_codes) == 2


# ── /mfa/confirm ─────────────────────────────────────────────────────


class TestMfaConfirmSchemaWiring:
    """POST /mfa/confirm validates payload through MfaConfirmSchema."""

    def test_oversized_totp_code_rejected(self, app, auth_client, seed_user):
        """An oversized totp_code on enrolment is rejected as 'Invalid code'.

        The route would otherwise pass the value to
        ``mfa_service.verify_totp_setup_code``; the schema cap means
        the user-visible error is the same flash but the service is
        not invoked.
        """
        with app.app_context():
            # Trigger /mfa/setup so a pending secret exists in the DB.
            auth_client.get("/mfa/setup")
            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "1" * 100_000,
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid code" in response.data


# ── /mfa/disable ─────────────────────────────────────────────────────


class TestMfaDisableSchemaWiring:
    """POST /mfa/disable validates payload through MfaDisableSchema."""

    def _enable_mfa(self, user_id):
        secret = "JBSWY3DPEHPK3PXP"
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret(secret),
            backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa"]),
        )
        db.session.add(mfa_config)
        db.session.commit()
        return secret

    def test_oversized_current_password_rejected_before_bcrypt(
        self, app, auth_client, seed_user,
    ):
        """F-163: oversized current_password on disable rejected pre-bcrypt."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            with patch(
                "app.services.auth_service.bcrypt.checkpw",
                return_value=False,
            ) as mock_check:
                response = auth_client.post("/mfa/disable", data={
                    "current_password": "x" * 100_000,
                    "totp_code": "123456",
                }, follow_redirects=True)
                assert response.status_code == 200
                assert mock_check.call_count == 0
                # MFA should still be enabled.
                config = (
                    db.session.query(MfaConfig)
                    .filter_by(user_id=seed_user["user"].id)
                    .first()
                )
                assert config.is_enabled is True

    def test_missing_current_password_rejected(
        self, app, auth_client, seed_user,
    ):
        """Missing current_password is rejected as 'Invalid password.'"""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)
            response = auth_client.post("/mfa/disable", data={
                "totp_code": "123456",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid password" in response.data
            # MFA should still be enabled.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is True
