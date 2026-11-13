"""Auth route tests: the two-factor setup and confirm flow, and its edge cases.

Every shared account is made with two-factor (plan#3600), so a test of setup
signs in through ``no_two_factor_client``, the seed owner after the reset act
took it away, and a test of an account with it uses ``auth_client``.
"""

import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from tests._test_helpers import store_backup_codes
from tests._two_factor_sign_in import two_factor_row_of


class TestMfaSetup:
    """Tests for the MFA setup flow."""

    def test_mfa_setup_page_renders(self, app, no_two_factor_client, seed_user):
        """GET /mfa/setup renders the QR code and manual key."""
        with app.app_context():
            response = no_two_factor_client.get("/mfa/setup")
            assert response.status_code == 200
            assert b"Set Up Two-Factor Authentication" in response.data
            assert b"mfa/confirm" in response.data
            # QR code data URI is present.
            assert b"data:image/png;base64," in response.data

    def test_mfa_setup_stores_encrypted_pending_server_side(
        self, app, no_two_factor_client, seed_user
    ):
        """GET /mfa/setup persists the pending secret in the DB, not the session.

        Locks down the C-05 contract: the unconfirmed TOTP secret lives
        in ``MfaConfig.pending_secret_encrypted`` (encrypted under the
        Fernet key) rather than in ``flask_session["_mfa_setup_secret"]``,
        because the Flask session cookie is signed but not encrypted.
        Verifies (a) the column is populated, (b) the bytes round-trip
        through ``mfa_service.decrypt_secret`` to a base32 string that
        matches the manual key shown on the page, and (c) the legacy
        session key is never written.
        """
        with app.app_context():
            response = no_two_factor_client.get("/mfa/setup")
            assert response.status_code == 200

            # The plaintext secret must NOT appear in the Flask session.
            with no_two_factor_client.session_transaction() as sess:
                assert "_mfa_setup_secret" not in sess

            # The encrypted pending secret IS in the database.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config is not None, "MfaConfig row must exist after setup"
            assert config.pending_secret_encrypted is not None
            assert config.pending_secret_expires_at is not None

            # The ciphertext decrypts to the manual key rendered in the
            # response body.  The base32 secret is wrapped in <code>...</code>.
            decrypted = mfa_service.decrypt_secret(config.pending_secret_encrypted)
            assert (f"<code>{decrypted}</code>").encode("utf-8") in response.data

    def test_mfa_setup_sets_expiry_within_window(
        self, app, no_two_factor_client, seed_user
    ):
        """GET /mfa/setup sets pending_secret_expires_at ~15 minutes ahead.

        The TTL constant is ``MFA_SETUP_PENDING_TTL = timedelta(minutes=15)``
        in ``app/routes/auth/_helpers.py``.  Allow a 60-second slack on either
        side so the test is stable under slow CI without permitting a
        regression that bumps the TTL by hours.
        """
        with app.app_context():
            before = datetime.now(timezone.utc)
            response = no_two_factor_client.get("/mfa/setup")
            after = datetime.now(timezone.utc)
            assert response.status_code == 200

            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            expires_at = config.pending_secret_expires_at
            assert expires_at is not None
            # 15 minutes minus a small slack and 15 minutes plus a small
            # slack bracket the legitimate window.
            lower = before + timedelta(minutes=15) - timedelta(seconds=60)
            upper = after + timedelta(minutes=15) + timedelta(seconds=60)
            assert lower <= expires_at <= upper, (
                f"pending_secret_expires_at={expires_at!r} not in "
                f"[{lower!r}, {upper!r}] -- the 15-minute TTL has "
                f"changed; update the test only if the change was deliberate."
            )

    def test_mfa_setup_redirects_if_already_enabled(self, app, auth_client, seed_user):
        """GET /mfa/setup redirects if MFA is already enabled, as the seed owner's is."""
        # Pylint: ``unused-argument`` -- ``seed_user`` is the account, made
        # with two-factor, that ``auth_client`` is signed in as.
        # pylint: disable=unused-argument
        with app.app_context():
            response = auth_client.get("/mfa/setup", follow_redirects=False)
            assert response.status_code == 302
            assert "security" in response.headers.get("Location", "")

    def test_mfa_confirm_valid_code(self, app, no_two_factor_client, seed_user, monkeypatch):
        """POST /mfa/confirm with valid code promotes pending to active.

        End-to-end check of the C-05 confirm path: the pending secret
        is decrypted, the code verifies, the secret is re-encrypted and
        stored as ``totp_secret_encrypted``, the pending columns are
        cleared, and the active credential is the same secret that was
        captured during setup (round-trip through encrypt -> decrypt).
        """
        with app.app_context():
            # /mfa/confirm verifies the *pending* secret via
            # verify_totp_setup_code (returns int|None), not the
            # active-secret path.  The fixed integer is the matched
            # step that the route persists as
            # ``mfa_config.last_totp_timestep``.
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
            )

            # Visit setup to write the pending secret to the DB.
            no_two_factor_client.get("/mfa/setup")
            pending_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            captured_secret = mfa_service.decrypt_secret(
                pending_config.pending_secret_encrypted
            )

            # Confirm with a mocked-valid code.
            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            # Reload to pick up the post-confirm row state.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config is not None
            assert config.is_enabled is True
            assert config.confirmed_at is not None
            # Pending columns must be cleared so a replay cannot
            # re-enrol against the same setup.
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None
            # Active secret matches the secret captured during setup.
            assert config.totp_secret_encrypted is not None
            assert (
                mfa_service.decrypt_secret(config.totp_secret_encrypted)
                == captured_secret
            )
            assert config.backup_codes is not None

    def test_mfa_confirm_invalid_code_keeps_pending(
        self, app, no_two_factor_client, seed_user, monkeypatch
    ):
        """POST /mfa/confirm with invalid code preserves pending state.

        A typo on the verification form must not destroy the pending
        secret -- otherwise the user would have to re-scan the QR for
        every wrong digit.  Asserts (a) the user sees the invalid-code
        flash, (b) MFA is not enabled, and (c) the pending columns are
        unchanged after the POST itself so the user can retry until the
        15-minute expiry.

        ``follow_redirects=False`` is essential here: the redirect
        target is /mfa/setup, which DOES rewrite pending state by
        design.  The contract we are testing is the behavior of the
        confirm route alone, not the side effects of the user's next
        navigation.
        """
        with app.app_context():
            # /mfa/confirm uses verify_totp_setup_code which returns
            # int (matched step) or None (no match).  None == invalid.
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: None,
            )

            # Visit setup to write a pending secret.
            no_two_factor_client.get("/mfa/setup")
            pending_before = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            ciphertext_before = pending_before.pending_secret_encrypted
            expires_before = pending_before.pending_secret_expires_at
            assert ciphertext_before is not None

            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "000000",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "mfa/setup" in response.headers.get("Location", "")

            db.session.refresh(pending_before)
            # Pending columns are unchanged -- the user can retry.
            assert pending_before.pending_secret_encrypted == ciphertext_before
            assert pending_before.pending_secret_expires_at == expires_before
            # MFA was NOT enabled.
            assert pending_before.is_enabled in (False, None)
            assert pending_before.totp_secret_encrypted is None
            assert pending_before.confirmed_at is None

            # Following the redirect manually delivers the flash to the
            # user, since the confirm route itself returned 302.
            follow_up = no_two_factor_client.get(
                response.headers["Location"], follow_redirects=False,
            )
            assert follow_up.status_code == 200
            assert b"Invalid code" in follow_up.data

    def test_mfa_confirm_oversized_code_rejected_before_verifier(
        self, app, no_two_factor_client, seed_user, db, monkeypatch,
    ):
        """POST /mfa/confirm with an over-length code is rejected at the schema.

        The C-26 length cap (``MfaConfirmSchema``) must reject an oversized
        ``totp_code`` BEFORE it reaches ``verify_totp_setup_code`` so an
        attacker cannot drive pyotp with a megabyte payload.  The schema-
        reject path funnels through the same "Invalid code." flash + /mfa/setup
        redirect as a wrong-but-valid-length code, and pending state is
        preserved for retry.  Guards the refactor that routed the schema
        rejection through a ``matched_step = None`` sentinel (the verifier is
        in the same ``try`` as the schema load, so a load failure must skip it).
        """
        with app.app_context():
            # Fail loudly if the over-length payload ever reaches the verifier:
            # the schema cap (and the route sentinel) must short-circuit first.
            def _must_not_run(_secret, _code):
                raise AssertionError(
                    "verify_totp_setup_code reached on an oversized code",
                )
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", _must_not_run,
            )

            no_two_factor_client.get("/mfa/setup")
            pending_before = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            ciphertext_before = pending_before.pending_secret_encrypted
            expires_before = pending_before.pending_secret_expires_at
            assert ciphertext_before is not None

            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "0" * 100,
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "mfa/setup" in response.headers.get("Location", "")

            db.session.refresh(pending_before)
            # Pending columns are unchanged -- the user can retry.
            assert pending_before.pending_secret_encrypted == ciphertext_before
            assert pending_before.pending_secret_expires_at == expires_before
            # MFA was NOT enabled.
            assert pending_before.is_enabled in (False, None)
            assert pending_before.totp_secret_encrypted is None
            assert pending_before.confirmed_at is None

            follow_up = no_two_factor_client.get(
                response.headers["Location"], follow_redirects=False,
            )
            assert follow_up.status_code == 200
            assert b"Invalid code" in follow_up.data

    def test_mfa_confirm_no_pending_state(self, app, auth_client, seed_user):
        """POST /mfa/confirm with no pending secret in DB shows expired flash.

        Covers two paths through the same branch: (a) the user posts
        directly to /mfa/confirm without ever visiting /mfa/setup, so
        no MfaConfig row exists; (b) a hypothetical row with cleared
        pending columns.  Both should redirect to /mfa/setup with the
        "session expired" flash.
        """
        with app.app_context():
            # No prior /mfa/setup visit -- no MfaConfig row.
            response = auth_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"MFA setup session expired" in response.data

    def test_mfa_confirm_rejects_expired_pending(
        self, app, no_two_factor_client, seed_user, monkeypatch
    ):
        """POST /mfa/confirm rejects pending state past its expiry.

        Sets up a pending secret with an expiry in the past and posts a
        valid code.  The route must (a) reject the submission with the
        "expired" flash, (b) clear the stale pending columns, and
        (c) NOT promote the secret to ``totp_secret_encrypted``.  This
        is the C-05 anti-staleness contract: an attacker who briefly
        compromises an account cannot revisit /mfa/confirm hours later
        to silently enrol their own device.

        ``follow_redirects=False`` is essential: the redirect target
        /mfa/setup writes new pending state by design and would mask
        the cleared columns we are asserting on.
        """
        with app.app_context():
            # The expired-pending guard runs BEFORE setup-code
            # verification; the patch is here only as a defensive
            # backstop in case the order is ever reordered.
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
            )

            # Give the reset row pending state already past expiry.
            mfa_config = two_factor_row_of(seed_user["user"].id)
            mfa_config.pending_secret_encrypted = mfa_service.encrypt_secret(
                "JBSWY3DPEHPK3PXP"
            )
            mfa_config.pending_secret_expires_at = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            )
            db.session.commit()

            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "mfa/setup" in response.headers.get("Location", "")

            db.session.refresh(mfa_config)
            # Stale pending state cleared.
            assert mfa_config.pending_secret_encrypted is None
            assert mfa_config.pending_secret_expires_at is None
            # MFA was NOT enabled.
            assert mfa_config.is_enabled in (False, None)
            assert mfa_config.totp_secret_encrypted is None
            assert mfa_config.confirmed_at is None

            # The flash arrives on the next page; verify it explicitly
            # so the user-visible message is locked down.  Use
            # follow_redirects=False on the GET so we do not chain
            # through into a fresh /mfa/setup that resets the page.
            follow_up = no_two_factor_client.get(
                response.headers["Location"], follow_redirects=False,
            )
            assert follow_up.status_code == 200
            assert b"MFA setup session expired" in follow_up.data

    def test_regenerate_backup_codes(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes generates new codes."""
        with app.app_context():
            store_backup_codes(db.session, seed_user["user"].id, ["aaaaaaaa"])

            response = auth_client.post("/mfa/regenerate-backup-codes")
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

    def test_regenerate_backup_codes_requires_mfa_enabled(self, app, no_two_factor_client, seed_user):
        """POST /mfa/regenerate-backup-codes without MFA enabled shows error."""
        with app.app_context():
            response = no_two_factor_client.post(
                "/mfa/regenerate-backup-codes", follow_redirects=True
            )
            assert response.status_code == 200
            assert b"Two-factor authentication is not enabled" in response.data

    def test_regenerate_backup_codes_renders_28_char_codes(self, app, auth_client, seed_user):
        """POST /mfa/regenerate-backup-codes renders 10 codes of 28 hex chars.

        Asserts the post-C-03 contract end-to-end: the route generates
        backup codes, hashes them, persists the hashes, and renders the
        plaintext codes once. Each rendered code must be 28 lowercase
        hex characters (112 bits of entropy). Without this end-to-end
        assertion, a regression in either the generator or the template
        could ship without breaking any unit test.
        """
        with app.app_context():
            store_backup_codes(db.session, seed_user["user"].id, ["legacy01"])

            response = auth_client.post("/mfa/regenerate-backup-codes")
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            body = response.get_data(as_text=True)
            # Match exactly 28 lowercase hex chars terminated by a non-hex
            # boundary (the surrounding markup) so partial matches inside
            # bcrypt hashes or similar can never fool the count.
            rendered_codes = re.findall(r"(?<![0-9a-f])[0-9a-f]{28}(?![0-9a-f])", body)
            assert len(rendered_codes) == 10, (
                f"Expected 10 28-char codes in response; found "
                f"{len(rendered_codes)}: {rendered_codes!r}"
            )

            stored_hashes = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
                .backup_codes
            )
            assert len(stored_hashes) == 10
            # The stored bcrypt hashes match the freshly rendered plaintext
            # codes -- the route persisted what it displayed and rotated
            # away from the legacy code.
            for plaintext in rendered_codes:
                idx = mfa_service.verify_backup_code(plaintext, stored_hashes)
                assert idx >= 0, (
                    f"Rendered code {plaintext!r} has no matching stored hash"
                )
            # The pre-existing legacy code must no longer verify -- this
            # endpoint regenerates, it does not append.
            assert mfa_service.verify_backup_code("legacy01", stored_hashes) == -1

    def test_mfa_confirm_renders_28_char_codes(self, app, no_two_factor_client, seed_user, monkeypatch):
        """POST /mfa/confirm renders 10 freshly generated 28-char backup codes.

        Mirrors test_regenerate_backup_codes_renders_28_char_codes for
        the initial enrollment path. Both routes call
        ``mfa_service.generate_backup_codes()`` and render
        ``auth/mfa_backup_codes.html``; both must show the upgraded
        format.
        """
        with app.app_context():
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
            )
            no_two_factor_client.get("/mfa/setup")

            response = no_two_factor_client.post("/mfa/confirm", data={"totp_code": "123456"})
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            body = response.get_data(as_text=True)
            rendered_codes = re.findall(r"(?<![0-9a-f])[0-9a-f]{28}(?![0-9a-f])", body)
            assert len(rendered_codes) == 10, (
                f"Expected 10 28-char codes in response; found "
                f"{len(rendered_codes)}: {rendered_codes!r}"
            )


class TestMfaSetupEdgeCases:
    """Edge-case tests for the MFA setup/confirm flow.

    Verifies that double-submitting the MFA confirmation code does not
    create duplicate MfaConfig rows or cause errors.
    """

    def test_mfa_confirm_double_submit(self, app, no_two_factor_client, seed_user, monkeypatch):
        """Submitting MFA confirmation twice does not create a duplicate MfaConfig.

        Under C-05, the first POST decrypts the pending secret, promotes
        it to ``totp_secret_encrypted``, and clears the pending columns.
        A second POST finds no pending secret and redirects to
        /mfa/setup with the "expired" flash.  Only one MfaConfig row
        should exist, and the existing active credential must NOT be
        re-rotated by the no-op second submission.
        """
        with app.app_context():
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
            )

            # Visit setup to write the pending secret to the DB.
            no_two_factor_client.get("/mfa/setup")

            # First confirm: promotes pending to active and shows backup codes.
            resp1 = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert resp1.status_code == 200
            assert b"Save Your Backup Codes" in resp1.data

            # Snapshot the active credential after the successful confirm.
            config_after_first = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).first()
            ciphertext_after_first = config_after_first.totp_secret_encrypted
            confirmed_at_after_first = config_after_first.confirmed_at

            # Second confirm: pending state was cleared; should redirect.
            resp2 = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert resp2.status_code == 302
            assert "mfa/setup" in resp2.headers.get("Location", "")

            # Verify exactly one MfaConfig exists.
            config_count = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).count()
            assert config_count == 1

            config = db.session.query(MfaConfig).filter_by(
                user_id=seed_user["user"].id
            ).first()
            assert config.is_enabled is True
            # The second submit must not have rotated or cleared the
            # active credential; pending state is still empty.
            assert config.totp_secret_encrypted == ciphertext_after_first
            assert config.confirmed_at == confirmed_at_after_first
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None
