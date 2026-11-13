"""
Shekel Budget App -- Adversarial Tests for MFA Setup Secret Storage

Regression tests that lock down the C-05 contract: the unconfirmed
TOTP secret captured during ``/mfa/setup`` must live server-side in
``auth.mfa_configs.pending_secret_encrypted`` (encrypted under the
application's Fernet/MultiFernet key) rather than in
``flask_session["_mfa_setup_secret"]``.  Flask's default
``SecureCookieSessionInterface`` signs but does NOT encrypt the
session cookie, so any value placed in the session is recoverable by
anyone who can read the cookie -- the user's browser, a malicious
extension, or anyone with read access to a shared profile.

If any of these tests starts failing, the F-031 fix has regressed.
Related: ``shekel-audits/security-2026-04-15/remediation-plan.md`` C-05.

Setup is for an account without two-factor, and every shared test account is
made with it (plan#3600), so each test sets up as the seed owner after a reset
(``no_two_factor_client``).
"""

import hashlib
import time
from datetime import datetime, timedelta, timezone
from os import getenv

import pyotp
from cryptography.fernet import Fernet, MultiFernet

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from app.utils.session_helpers import SESSION_LAST_ACTIVITY_KEY
from tests._seed_owner_fixtures import (
    OWNERS_PAGE, SEED_USER_EMAIL, SEED_USER_PASSWORD,
)
from tests._two_factor_sign_in import (
    reset_two_factor_of,
    sign_in_by_password,
    sign_in_seed_companion_by_password,
)


def _decode_session_cookie(app, cookie_value: str) -> dict:
    """Deserialize a Flask session cookie using the app's signer.

    Mirrors the server-side path the app uses to read its own session
    cookie -- the test does the same itsdangerous-verified
    deserialization an attacker would need to do, so the assertion
    "the plaintext is not in the cookie" is checked at the same layer
    where the threat lives (post-base64, post-itsdangerous, JSON dict).

    Args:
        app: The Flask application whose session_interface is used to
            unpack the cookie.
        cookie_value: The raw value of the ``session`` cookie as set
            by the test client.

    Returns:
        The deserialized session dict, or an empty dict if the cookie
        is empty.  Never raises on a missing cookie -- the caller is
        responsible for handling that case explicitly.
    """
    if not cookie_value:
        return {}
    serializer = app.session_interface.get_signing_serializer(app)
    if serializer is None:
        return {}
    return serializer.loads(cookie_value)


def _get_session_cookie_value(client) -> str:
    """Return the raw value of the ``session`` cookie or '' if absent.

    The Werkzeug test client exposes cookies via ``get_cookie`` which
    returns a ``Cookie`` instance with a ``.value`` attribute.  Wrap
    the lookup so individual tests do not duplicate the None-handling.
    """
    cookie = client.get_cookie("session")
    return cookie.value if cookie else ""


class TestMfaSetupSecretIsServerSide:
    """The pending TOTP secret must never appear in the session cookie."""

    def test_flask_session_does_not_contain_plaintext_secret(
        self, app, no_two_factor_client, seed_user
    ):
        """The session cookie has no ``_mfa_setup_secret`` key after setup.

        Walks both the high-level (``session_transaction``) and
        low-level (raw cookie + ``itsdangerous`` deserializer) views.
        Either layer alone could regress without the other noticing,
        so both must hold.  Also asserts that the plaintext base32
        secret rendered on the page does not appear anywhere in the
        deserialized session payload (a defence against a typo that
        moved the value to a differently-named session key).
        """
        with app.app_context():
            response = no_two_factor_client.get("/mfa/setup")
            assert response.status_code == 200

            # Pull the secret from the database where C-05 says it
            # should live.  The same value is rendered into the QR
            # response body as the manual key.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config is not None
            assert config.pending_secret_encrypted is not None
            plaintext_secret = mfa_service.decrypt_secret(
                config.pending_secret_encrypted
            )

            # High-level: Flask test client's session view.
            with no_two_factor_client.session_transaction() as sess:
                assert "_mfa_setup_secret" not in sess, (
                    "Legacy session key found -- C-05 has regressed."
                )
                assert plaintext_secret not in sess.values(), (
                    "Plaintext TOTP secret found in a Flask session value."
                )

            # Low-level: itsdangerous-verified deserialization of the
            # actual ``session`` cookie value the browser would send.
            cookie_value = _get_session_cookie_value(no_two_factor_client)
            assert cookie_value, "Test client did not set a session cookie."
            payload = _decode_session_cookie(app, cookie_value)
            assert isinstance(payload, dict)
            assert "_mfa_setup_secret" not in payload
            # Catch-all: the secret must not appear in any session
            # value, even under a renamed key.
            for value in payload.values():
                assert plaintext_secret not in str(value), (
                    "Plaintext TOTP secret leaked into a Flask session "
                    f"value: {value!r}"
                )

    def test_one_pending_secret_at_a_time(
        self, app, no_two_factor_client, seed_user
    ):
        """A second ``/mfa/setup`` keeps the one pending secret; a new one replaces it.

        The C-05 design: each user's setup owns the pending columns, so no
        two pending secrets are ever valid together.  Since ruling plan#3470 a
        second visit inside the window shows the SAME pending secret (it was
        a new one per visit until then), and since ruling plan#3523 only in
        the sign-in that made it; once the window closes the next visit
        replaces it, and the replaced secret no longer confirms.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            no_two_factor_client.get("/mfa/setup")
            config = db.session.query(MfaConfig).filter_by(user_id=user_id).one()
            first_ciphertext = config.pending_secret_encrypted
            first_secret = mfa_service.decrypt_secret(first_ciphertext)

            no_two_factor_client.get("/mfa/setup")
            db.session.refresh(config)
            # The same pending secret, untouched: one row, one ciphertext.
            assert config.pending_secret_encrypted == first_ciphertext
            assert db.session.query(MfaConfig).filter_by(user_id=user_id).count() == 1

            config.pending_secret_expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            db.session.commit()
            no_two_factor_client.get("/mfa/setup")
            db.session.refresh(config)
            second_secret = mfa_service.decrypt_secret(config.pending_secret_encrypted)
            assert second_secret != first_secret
            assert db.session.query(MfaConfig).filter_by(user_id=user_id).count() == 1

            # The replaced secret's code confirms nothing.  The code is one the
            # replaced secret accepts this instant (a step of its drift window)
            # and the new one does not, so a refusal is the replacement's.
            now = int(time.time())
            steps = (now - 30, now, now + 30)
            accepted_now = {pyotp.TOTP(second_secret).at(t) for t in steps}
            stale = next(
                code for code in (pyotp.TOTP(first_secret).at(t) for t in steps)
                if code not in accepted_now
            )
            replaced = no_two_factor_client.post("/mfa/confirm", data={"totp_code": stale})
            assert replaced.status_code == 302
            db.session.refresh(config)
            assert config.is_enabled is False

    def test_setup_adds_only_the_keys_fingerprint_to_the_session(
        self, app, client, seed_user
    ):
        """The session gains one value at setup: a SHA-256 digest of the ciphertext.

        Ruling plan#3523: ``/mfa/setup`` shows the pending key again only in
        the sign-in that made it, which it tells by a fingerprint it stores in
        that browser's session.  The cookie is signed but not encrypted, so
        the C-05 rule holds for the fingerprint too.  The payload after setup
        is the payload before it, with the activity stamp every request moves
        and with the digest added, and nothing else: no value holds the key or
        its ciphertext in any encoding, under any name.  The digest is spelled
        here with ``hashlib``, never read from the route's helper.

        The browser opens the sign-in form before it signs in, as a person's
        does, so its session holds the form token every page with a form
        writes the first time it renders one.  Setup is the first page this
        account can open, since the login gate holds an account without
        two-factor there (plan#3452), so without that the token would be
        added at setup beside the digest.
        """
        with app.app_context():
            reset_two_factor_of(seed_user["user"].id)
            assert client.get("/login").status_code == 200
            sign_in_by_password(client, SEED_USER_EMAIL, SEED_USER_PASSWORD, OWNERS_PAGE)
            before = _decode_session_cookie(app, _get_session_cookie_value(client))

            assert client.get("/mfa/setup").status_code == 200
            after = _decode_session_cookie(app, _get_session_cookie_value(client))

            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            added = after.keys() - before.keys()
            assert len(added) == 1, f"Setup added {sorted(added)} to the session."
            (fingerprint_name,) = added
            assert after == {
                **before,
                SESSION_LAST_ACTIVITY_KEY: after[SESSION_LAST_ACTIVITY_KEY],
                fingerprint_name: hashlib.sha256(
                    config.pending_secret_encrypted,
                ).hexdigest(),
            }
            # The one value let change is a timestamp, nothing else.
            assert datetime.fromisoformat(after[SESSION_LAST_ACTIVITY_KEY]).tzinfo

    def test_cross_user_cannot_consume_pending(
        self, app, no_two_factor_client, seed_user, seed_companion
    ):
        """User B cannot complete /mfa/confirm using user A's pending secret.

        ``current_user.id`` scopes every MfaConfig query in the route,
        so the pending row belonging to user A is invisible to a
        request authenticated as user B.  This test exists as a
        regression check: any future refactor that accidentally
        replaces ``filter_by(user_id=current_user.id)`` with a global
        lookup would let user B silently inherit user A's
        in-progress credential.  User B is the seed companion, whom her
        owner has just made: a user with no two-factor row at all, signed
        in with her password alone.
        """
        with app.app_context():
            user_b_client = sign_in_seed_companion_by_password(app)
            # User A initiates setup -- writes pending state.
            no_two_factor_client.get("/mfa/setup")
            user_a_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            user_a_pending_before = user_a_config.pending_secret_encrypted
            user_a_expiry_before = user_a_config.pending_secret_expires_at
            assert user_a_pending_before is not None

            # User B attempts to confirm.  User B has no MfaConfig at
            # all (no /mfa/setup call as user B), so the route should
            # treat this as an expired/missing setup.
            response = user_b_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "mfa/setup" in response.headers.get("Location", "")

            # User B has no MfaConfig.
            user_b_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_companion["user"].id)
                .first()
            )
            assert user_b_config is None, (
                "User B's confirm must not auto-create an MfaConfig row."
            )

            # User A's pending state is untouched -- the confirm by
            # user B did not consume, mutate, or clear A's secret.
            db.session.refresh(user_a_config)
            assert user_a_config.pending_secret_encrypted == user_a_pending_before
            assert user_a_config.pending_secret_expires_at == user_a_expiry_before
            assert user_a_config.is_enabled in (False, None)
            assert user_a_config.totp_secret_encrypted is None

    def test_pending_decryptable_after_key_rotation(
        self, app, no_two_factor_client, seed_user, monkeypatch
    ):
        """Pending secret survives a TOTP_ENCRYPTION_KEY rotation mid-setup.

        Simulates the C-04 rotation procedure between /mfa/setup and
        /mfa/confirm: the pending ciphertext was written under the
        original primary, the operator promoted a new primary and
        moved the original to ``TOTP_ENCRYPTION_KEY_OLD``.
        ``mfa_service.get_encryption_key`` returns a ``MultiFernet``
        that decrypts under either key, so the user can still finish
        their setup, and the route re-encrypts the secret under the
        new primary so the active credential never depends on the
        retired key.

        Without the C-05 + C-04 combination -- specifically, without
        re-encryption on confirm -- a user who happened to start
        setup just before a rotation would end up with an active
        credential keyed to the OLD key, and the operator could not
        safely prune ``TOTP_ENCRYPTION_KEY_OLD`` without breaking
        their MFA.
        """
        with app.app_context():
            # Capture the original primary that the setup call will
            # use: the one key the ``set_totp_key`` autouse fixture
            # holds for the test process (ruling plan#3625).
            old_primary = mfa_service.get_encryption_key()
            # Perform setup under the original primary.
            no_two_factor_client.get("/mfa/setup")
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            pending_before_rotation = config.pending_secret_encrypted
            assert pending_before_rotation is not None
            captured_secret = old_primary.decrypt(
                pending_before_rotation
            ).decode("utf-8")

            # Rotate: promote a new primary, demote the original to
            # TOTP_ENCRYPTION_KEY_OLD.  This mirrors steps 1-2 of
            # ``docs/runbook_secrets.md``.
            old_primary_key_str = getenv("TOTP_ENCRYPTION_KEY")
            assert old_primary_key_str, (
                "Test setup error: TOTP_ENCRYPTION_KEY must be set."
            )
            new_primary_key_str = Fernet.generate_key().decode()
            assert new_primary_key_str != old_primary_key_str
            monkeypatch.setenv("TOTP_ENCRYPTION_KEY", new_primary_key_str)
            monkeypatch.setenv("TOTP_ENCRYPTION_KEY_OLD", old_primary_key_str)

            # Confirm.  Decrypt should succeed via the retired key,
            # the route should re-encrypt under the new primary.
            # /mfa/confirm now goes through verify_totp_setup_code
            # (returns matched step or None) instead of the active-
            # secret verify_totp_code path.
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
            )
            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            assert response.status_code == 200
            assert b"Save Your Backup Codes" in response.data

            # The active credential round-trips to the same plaintext.
            db.session.refresh(config)
            assert config.is_enabled is True
            assert config.totp_secret_encrypted is not None
            new_primary_only = Fernet(new_primary_key_str)
            assert new_primary_only.decrypt(
                config.totp_secret_encrypted
            ).decode("utf-8") == captured_secret, (
                "Active credential must decrypt back to the originally "
                "captured pending secret -- promotion lost or mutated "
                "the value."
            )

            # The active record decrypts under the new primary alone,
            # which is the precondition for the operator to safely
            # prune TOTP_ENCRYPTION_KEY_OLD on the next deploy.  This
            # is the property C-04's rotate_totp_key.py is designed
            # to deliver, and C-05 must not undo it.
            new_only_multi = MultiFernet([new_primary_only])
            new_only_multi.decrypt(config.totp_secret_encrypted)


class TestMfaSetupExpiryEnforcement:
    """The 15-minute pending TTL is the second half of the C-05 contract."""

    def test_confirm_just_before_expiry_succeeds(
        self, app, no_two_factor_client, seed_user, monkeypatch
    ):
        """Confirm one second before expiry still works.

        Boundary check on ``pending_secret_expires_at``: the
        comparison in ``mfa_confirm`` is strictly less-than
        (``expires_at < now``), so a row whose expiry is one second
        in the future must be accepted.  Without this test a future
        refactor could change the comparison to <= and silently lock
        users out of their last-second confirms.
        """
        with app.app_context():
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: 12345,
            )

            # The owner's row, which the reset left disabled, holds a
            # pending secret one second from its expiry.
            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            mfa_config.pending_secret_encrypted = mfa_service.encrypt_secret(
                "JBSWY3DPEHPK3PXP"
            )
            mfa_config.pending_secret_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=1)
            )
            db.session.commit()

            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            }, follow_redirects=False)
            # Either the response is the backup codes page (200) or
            # the request raced past expiry (302 to /mfa/setup).
            # The first outcome is what we are asserting; if the
            # second occurs the test ran on an extraordinarily slow
            # machine.  Treat the 200 case as the locked-down path.
            assert response.status_code == 200, (
                "Confirm one second before expiry must succeed; got "
                f"{response.status_code}.  If this is flaky on slow "
                "CI, widen the boundary, do NOT change the comparison "
                "in mfa_confirm."
            )
            assert b"Save Your Backup Codes" in response.data

            db.session.refresh(mfa_config)
            assert mfa_config.is_enabled is True
            assert mfa_config.pending_secret_encrypted is None
