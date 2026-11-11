"""Setup shows the same key while a setup is pending (ruling plan#3470).

Plan#2407, task plan#3502.  Until the ruling every load of ``/mfa/setup`` made
a new key and threw the pending one away (pinned then by
``test_mfa_setup_replaces_previous_pending`` in ``test_auth.py``, which this
file replaces), so a reload between scanning the QR code and typing its code
killed the entry just added to the authenticator app.  Now, while a setup is
pending, every load shows its key: for 15 minutes from when the key was made,
not stretched by reloads.  A new key is made only when none is pending, the
pending one has expired, or it does not decrypt.

**One code per secret per test**: replay prevention refuses a second code
inside one thirty-second step, so each secret a test makes is typed once.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from scripts.reset_mfa import reset_mfa
from tests._two_factor_sign_in import sign_in_seed_companion_by_password
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_SETUP = "/mfa/setup"

#: A setup's window, the ruling's fifteen minutes, spelled here as an oracle
#: rather than read from the constant the route uses.
_WINDOW = timedelta(minutes=15)

#: How far a clock read in a test may sit from the route's own read.
_SLACK = timedelta(seconds=60)


def _key_on(response):
    """Return the manual key the setup page shows."""
    assert response.status_code == 200, response.status_code
    return re.search(r"<code>([A-Z2-7]+)</code>", response.get_data(as_text=True)).group(1)


def _config_of(user_id):
    """Return *user_id*'s two-factor row, read fresh."""
    db.session.expire_all()
    return db.session.query(MfaConfig).filter_by(user_id=user_id).one()


def _set_expiry(user_id, instant):
    """Move *user_id*'s pending setup's expiry to *instant*, committed."""
    config = _config_of(user_id)
    config.pending_secret_expires_at = instant
    db.session.commit()


def _owner_signed_in_again(app):
    """Return a fresh browser signed in as the seed owner with her password.

    A reset ends every session of the user it resets, so a test that resets
    the owner and then loads setup does so in a new sign-in.
    """
    browser = app.test_client()
    response = browser.post("/login", data={
        "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
    })
    assert urlsplit(response.headers["Location"]).path not in ("/login", "/mfa/verify")
    return browser


def _code(secret):
    """Return the authenticator code for *secret* at this instant."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _a_wrong_code(secret):
    """Return a six-digit code *secret* refuses at this instant.

    Not one of the three steps its drift window accepts, so the refusal is
    certain rather than likely.
    """
    now = int(time.time())
    accepted = {pyotp.TOTP(secret).at(t) for t in (now - 30, now, now + 30)}
    return next(
        code for code in (f"{n:06d}" for n in range(1_000_000)) if code not in accepted
    )


class TestThePendingKeyIsShownAgain:
    """Inside its window, every load shows the one pending key."""

    def test_two_loads_show_one_key_and_write_nothing(self, auth_client, seed_user):
        """The same key on both loads; the ciphertext and expiry are as the first left them."""
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        stored = _config_of(user_id)
        ciphertext, expiry = stored.pending_secret_encrypted, stored.pending_secret_expires_at

        second = _key_on(auth_client.get(_SETUP))

        assert second == first
        after = _config_of(user_id)
        assert after.pending_secret_encrypted == ciphertext
        assert after.pending_secret_expires_at == expiry

    def test_a_reload_does_not_stretch_the_window(self, auth_client, seed_user):
        """One minute left: the load shows the key, and one minute is still left."""
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        near_the_end = datetime.now(timezone.utc) + timedelta(minutes=1)
        _set_expiry(user_id, near_the_end)

        assert _key_on(auth_client.get(_SETUP)) == first

        assert _config_of(user_id).pending_secret_expires_at == near_the_end

    def test_a_wrong_code_brings_back_the_same_key_and_its_code_confirms(
        self, auth_client, seed_user,
    ):
        """A typo at confirm sends her back to the key she scanned, which then works."""
        user_id = seed_user["user"].id
        key = _key_on(auth_client.get(_SETUP))

        typo = auth_client.post("/mfa/confirm", data={"totp_code": _a_wrong_code(key)})

        assert urlsplit(typo.headers["Location"]).path == _SETUP
        assert _key_on(auth_client.get(_SETUP)) == key
        confirmed = auth_client.post("/mfa/confirm", data={"totp_code": _code(key)})
        assert confirmed.status_code == 200
        assert b"Save Your Backup Codes" in confirmed.data
        assert _config_of(user_id).is_enabled is True

    @pytest.mark.usefixtures("seed_companion")
    def test_a_companion_held_at_setup_sees_the_key_she_scanned(self, app):
        """Sent back to setup from her page, she meets the same key."""
        browser = sign_in_seed_companion_by_password(app)
        key = _key_on(browser.get(_SETUP))

        held = browser.get("/companion/")

        assert urlsplit(held.headers["Location"]).path == _SETUP
        assert _key_on(browser.get(_SETUP)) == key


class TestANewKeyIsMade:
    """No pending key to show: expired, unreadable, or none."""

    def test_after_the_window_closes_with_fresh_fifteen_minutes(
        self, auth_client, seed_user,
    ):
        """A load just past the expiry makes a new key, which the row now holds."""
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        _set_expiry(user_id, datetime.now(timezone.utc) - timedelta(seconds=1))

        before = datetime.now(timezone.utc)
        second = _key_on(auth_client.get(_SETUP))

        assert second != first
        after = _config_of(user_id)
        assert mfa_service.decrypt_secret(after.pending_secret_encrypted) == second
        assert (
            before + _WINDOW - _SLACK
            <= after.pending_secret_expires_at
            <= before + _WINDOW + _SLACK
        )

    def test_a_pending_key_that_does_not_decrypt_is_replaced(
        self, auth_client, seed_user,
    ):
        """Written under a key the app no longer holds: a new key, readable, fresh window."""
        user_id = seed_user["user"].id
        _key_on(auth_client.get(_SETUP))
        config = _config_of(user_id)
        config.pending_secret_encrypted = Fernet(Fernet.generate_key()).encrypt(
            b"JBSWY3DPEHPK3PXP",
        )
        db.session.commit()

        key = _key_on(auth_client.get(_SETUP))

        after = _config_of(user_id)
        assert mfa_service.decrypt_secret(after.pending_secret_encrypted) == key
        assert key != "JBSWY3DPEHPK3PXP"

    def test_after_a_reset_the_next_setup_makes_a_new_key(
        self, app, auth_client, seed_user,
    ):
        """``scripts/reset_mfa.py`` clears the pending key with the rest."""
        first = _key_on(auth_client.get(_SETUP))

        reset_mfa(SEED_USER_EMAIL)

        assert _config_of(seed_user["user"].id).pending_secret_encrypted is None
        assert _key_on(_owner_signed_in_again(app).get(_SETUP)) != first

    def test_after_confirming_and_a_reset_the_next_setup_makes_a_new_key(
        self, app, auth_client, seed_user,
    ):
        """Confirming spends the pending key; the setup after a reset is a new one."""
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        assert auth_client.post(
            "/mfa/confirm", data={"totp_code": _code(first)},
        ).status_code == 200
        assert _config_of(user_id).pending_secret_encrypted is None
        reset_mfa(SEED_USER_EMAIL)

        assert _key_on(_owner_signed_in_again(app).get(_SETUP)) != first
