"""Setup keeps its key only for the browser that made it, 15 fresh minutes a load.

Plan#2407, tasks plan#3502 and plan#3531.  Until ruling plan#3470 every load of
``/mfa/setup`` made a new key and threw the pending one away (pinned then by
``test_mfa_setup_replaces_previous_pending`` in ``test_auth.py``, which this
file replaces), so a reload between scanning the QR code and typing its code
killed the entry just added to the authenticator app.  Ruling plan#3470 kept
the pending key for every signed-in browser of the account, for 15 minutes
from when it was made.  Ruling plan#3523 replaced that: the key is shown again
only in the browser that made it, which its session names by a fingerprint
that every sign-in clears, and each load there gives it 15 fresh minutes.  Any other browser gets a new
key, which replaces it, as does a load with no key pending, an expired one, or
one that does not decrypt.

**One code per secret per test**: replay prevention refuses a second code
inside one thirty-second step, so each secret a test makes is typed once.

**The clock**: a test that moves it travels with ``time_machine`` and
``tick=False``, which moves ``time.time()``, the clock the code verifier and
:func:`~tests._two_factor_sign_in.code_now` read, and ``datetime.now``, the
one the route's expiry is written and judged by.
"""

import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pyotp
import pytest
import sqlalchemy
import time_machine
from cryptography.fernet import Fernet

from app.extensions import db
from app.models.user import MfaConfig
from app.routes.auth import mfa as mfa_routes
from app.services import mfa_service
from scripts.reset_mfa import reset_mfa
from tests._two_factor_sign_in import (
    code_now, redirect_path, sign_in_seed_companion_by_password, two_factor_row_of,
)
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


def _pending_key_of(user_id):
    """Return the key *user_id*'s pending setup holds, decrypted."""
    return mfa_service.decrypt_secret(two_factor_row_of(user_id).pending_secret_encrypted)


def _set_expiry(user_id, instant):
    """Move *user_id*'s pending setup's expiry to *instant*, committed."""
    config = two_factor_row_of(user_id)
    config.pending_secret_expires_at = instant
    db.session.commit()


def _owner_signed_in_again(app):
    """Return a fresh browser signed in as the seed owner with her password.

    A second browser of the account, beside ``auth_client``'s; or the one a
    test signs in after a reset, which ends every session of the user it
    resets.
    """
    browser = app.test_client()
    response = browser.post("/login", data={
        "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
    })
    assert urlsplit(response.headers["Location"]).path not in ("/login", "/mfa/verify")
    return browser


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


def _a_code_only(secret, other):
    """Return a code *secret* accepts at this instant and *other* refuses.

    One of the three steps *secret*'s drift window accepts, and none of
    *other*'s, so a refusal of it is *secret*'s and cannot be a code that
    happened to match *other*.
    """
    now = int(time.time())
    steps = (now - 30, now, now + 30)
    accepted_by_other = {pyotp.TOTP(other).at(t) for t in steps}
    return next(
        code for code in (pyotp.TOTP(secret).at(t) for t in steps)
        if code not in accepted_by_other
    )


class TestThePendingKeyIsShownAgain:
    """In the browser that made it, every load shows the one pending key."""

    def test_two_loads_show_one_key_and_the_second_gives_it_fifteen_fresh_minutes(
        self, auth_client, seed_user,
    ):
        """Five minutes on, the second load shows the same key and moves its expiry.

        Ruling plan#3523.  Under ruling plan#3470 this test pinned that the
        second load wrote nothing, so the expiry stayed where the first left
        it.
        """
        user_id = seed_user["user"].id
        start = datetime.now(timezone.utc)
        with time_machine.travel(start, tick=False):
            first = _key_on(auth_client.get(_SETUP))
        made = two_factor_row_of(user_id)
        ciphertext = made.pending_secret_encrypted
        assert made.pending_secret_expires_at == start + _WINDOW
        later = start + timedelta(minutes=5)

        with time_machine.travel(later, tick=False):
            second = _key_on(auth_client.get(_SETUP))

        assert second == first
        after = two_factor_row_of(user_id)
        assert after.pending_secret_encrypted == ciphertext
        assert after.pending_secret_expires_at == later + _WINDOW

    def test_a_key_reached_at_minute_fourteen_still_confirms_at_minute_sixteen(
        self, auth_client, seed_user,
    ):
        """The load at minute 14 gives the key until minute 29 (ruling plan#3523).

        Under ruling plan#3470 a reload did not stretch the window (this
        file's ``test_a_reload_does_not_stretch_the_window``), so this key
        died at minute 15.  The control is
        :meth:`test_a_key_not_shown_again_no_longer_confirms_at_minute_sixteen`,
        the same steps without the load at minute 14.
        """
        start = datetime.now(timezone.utc)
        with time_machine.travel(start, tick=False):
            key = _key_on(auth_client.get(_SETUP))
        with time_machine.travel(start + timedelta(minutes=14), tick=False):
            assert _key_on(auth_client.get(_SETUP)) == key

        with time_machine.travel(start + timedelta(minutes=16), tick=False):
            confirmed = auth_client.post("/mfa/confirm", data={"totp_code": code_now(key)})

        assert confirmed.status_code == 200
        assert b"Save Your Backup Codes" in confirmed.data
        assert two_factor_row_of(seed_user["user"].id).is_enabled is True

    def test_a_key_not_shown_again_no_longer_confirms_at_minute_sixteen(
        self, auth_client, seed_user,
    ):
        """The control: with no load since minute 0, the window closed at minute 15.

        ``/mfa/confirm`` refuses the right code, sends her back to setup and
        clears the expired key.
        """
        user_id = seed_user["user"].id
        start = datetime.now(timezone.utc)
        with time_machine.travel(start, tick=False):
            key = _key_on(auth_client.get(_SETUP))

        with time_machine.travel(start + timedelta(minutes=16), tick=False):
            refused = auth_client.post("/mfa/confirm", data={"totp_code": code_now(key)})

        assert redirect_path(refused) == _SETUP
        after = two_factor_row_of(user_id)
        assert after.is_enabled is False
        assert after.pending_secret_encrypted is None

    def test_a_wrong_code_brings_back_the_same_key_and_its_code_confirms(
        self, auth_client, seed_user,
    ):
        """A typo at confirm sends her back to the key she scanned, which then works."""
        user_id = seed_user["user"].id
        key = _key_on(auth_client.get(_SETUP))

        typo = auth_client.post("/mfa/confirm", data={"totp_code": _a_wrong_code(key)})

        assert urlsplit(typo.headers["Location"]).path == _SETUP
        assert _key_on(auth_client.get(_SETUP)) == key
        confirmed = auth_client.post("/mfa/confirm", data={"totp_code": code_now(key)})
        assert confirmed.status_code == 200
        assert b"Save Your Backup Codes" in confirmed.data
        assert two_factor_row_of(user_id).is_enabled is True

    @pytest.mark.usefixtures("seed_companion")
    def test_a_companion_held_at_setup_sees_the_key_she_scanned(self, app):
        """Sent back to setup from her page, she meets the same key."""
        browser = sign_in_seed_companion_by_password(app)
        key = _key_on(browser.get(_SETUP))

        held = browser.get("/companion/")

        assert urlsplit(held.headers["Location"]).path == _SETUP
        assert _key_on(browser.get(_SETUP)) == key


class TestAnotherBrowserNeverSeesIt:
    """Another browser of the account gets a new key, which replaces the pending one.

    Ruling plan#3523's case: someone with her password opens setup on a
    laptop, then she opens it on her phone.  Under ruling plan#3470 the phone
    showed the laptop's key, and the laptop's user could make her codes once
    she scanned it.
    """

    def test_a_second_browser_gets_a_new_key_and_the_first_no_longer_confirms(
        self, app, auth_client, seed_user,
    ):
        """Her phone's key replaces the laptop's; the laptop's code confirms nothing.

        The phone's code then confirms, so the laptop's refusal is the
        replacement's and not a broken setup.
        """
        user_id = seed_user["user"].id
        laptop_key = _key_on(auth_client.get(_SETUP))
        phone = _owner_signed_in_again(app)

        phone_key = _key_on(phone.get(_SETUP))

        assert phone_key != laptop_key
        assert _pending_key_of(user_id) == phone_key
        refused = auth_client.post(
            "/mfa/confirm", data={"totp_code": _a_code_only(laptop_key, phone_key)},
        )
        assert redirect_path(refused) == _SETUP
        assert two_factor_row_of(user_id).is_enabled is False
        confirmed = phone.post("/mfa/confirm", data={"totp_code": code_now(phone_key)})
        assert confirmed.status_code == 200
        assert b"Save Your Backup Codes" in confirmed.data
        assert two_factor_row_of(user_id).is_enabled is True

    def test_the_first_browser_back_at_setup_sees_neither_key(
        self, app, auth_client, seed_user,
    ):
        """The laptop's next load makes a third key rather than showing her phone's.

        Its session still holds the fingerprint of the key it made, which
        names no key the row holds once her phone's replaced it.
        """
        user_id = seed_user["user"].id
        laptop_key = _key_on(auth_client.get(_SETUP))
        phone_key = _key_on(_owner_signed_in_again(app).get(_SETUP))

        again = _key_on(auth_client.get(_SETUP))

        assert again not in (laptop_key, phone_key)
        assert _pending_key_of(user_id) == again


class TestASignInStartsWithNoKeyToShow:
    """The key is kept for the sign-in that made it, not for the browser alone.

    A browser shared by two people is two sign-ins: someone who signs in as
    her on her browser after she signed out, with her password, must not be
    shown the key she scanned there.
    """

    def test_a_new_sign_in_in_the_same_browser_gets_a_new_key(
        self, auth_client, seed_user,
    ):
        """Signed out and in again on one browser: a new key, which the row now holds."""
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        assert redirect_path(auth_client.post("/logout")) == "/login"
        signed_in = auth_client.post("/login", data={
            "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
        })
        assert redirect_path(signed_in) not in ("/login", "/mfa/verify")

        again = _key_on(auth_client.get(_SETUP))

        assert again != first
        assert _pending_key_of(user_id) == again


@contextmanager
def _committed_first_on_its_own_connection(statement, real_block):
    """Commit *statement* as another request would, then open setup's block.

    Stands in for ``write_transaction`` in ``app.routes.auth.mfa``, so the
    other request's commit lands after everything setup did before its
    command and before everything inside it: where a deciding read taken
    before the block would miss it.
    """
    with db.engine.begin() as connection:
        connection.execute(statement)
    with real_block():
        yield


class TestTheLoadDecidesUnderTheLock:
    """Setup reads the row it decides on inside its command, after any commit before it.

    ``/mfa/confirm`` takes the owner's lock at its request's start, and so
    does setup's block, so a confirm commits wholly before the block's read or
    wholly after its write.  Each test lands a confirm's commit in the one gap
    a read taken before the block would leave.
    """

    @staticmethod
    def _her_confirm_lands_as_the_block_opens(monkeypatch, user_id):
        """Make her confirm, from another tab, commit just before setup's block."""
        enabled = (
            sqlalchemy.update(MfaConfig)
            .where(MfaConfig.user_id == user_id)
            .values(
                is_enabled=True,
                totp_secret_encrypted=MfaConfig.pending_secret_encrypted,
                pending_secret_encrypted=None,
                pending_secret_expires_at=None,
            )
        )
        real_block = mfa_routes.write_transaction
        monkeypatch.setattr(
            mfa_routes, "write_transaction",
            lambda: _committed_first_on_its_own_connection(enabled, real_block),
        )

    def test_another_browsers_load_puts_no_key_on_her_just_enabled_row(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """The load sees her two-factor on and writes nothing, so no key waits there.

        Read before the block, this load made a new key and wrote it onto her
        enabled row, where a confirm with its code would replace her secret.
        """
        user_id = seed_user["user"].id
        _key_on(auth_client.get(_SETUP))
        laptop = _owner_signed_in_again(app)
        self._her_confirm_lands_as_the_block_opens(monkeypatch, user_id)

        response = laptop.get(_SETUP)

        assert redirect_path(response) == "/security"
        after = two_factor_row_of(user_id)
        assert after.is_enabled is True
        assert after.pending_secret_encrypted is None
        assert after.pending_secret_expires_at is None

    def test_her_own_reload_writes_no_expiry_onto_the_row_her_confirm_cleared(
        self, auth_client, seed_user, monkeypatch,
    ):
        """A reload in her own browser sees the key confirmed and renews nothing.

        Read before the block, the renewal wrote an expiry onto the row whose
        key her confirm had just spent, leaving a pending expiry with no key.
        """
        user_id = seed_user["user"].id
        _key_on(auth_client.get(_SETUP))
        self._her_confirm_lands_as_the_block_opens(monkeypatch, user_id)

        response = auth_client.get(_SETUP)

        assert redirect_path(response) == "/security"
        after = two_factor_row_of(user_id)
        assert after.is_enabled is True
        assert after.pending_secret_encrypted is None
        assert after.pending_secret_expires_at is None


class TestANewKeyIsMade:
    """No pending key to show: expired, unreadable, or none."""

    def test_after_the_window_closes_with_fresh_fifteen_minutes(
        self, auth_client, seed_user,
    ):
        """A load just past the expiry makes a new key, which the row now holds.

        The next load shows it again, so the session now names the new key.
        """
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        _set_expiry(user_id, datetime.now(timezone.utc) - timedelta(seconds=1))

        before = datetime.now(timezone.utc)
        second = _key_on(auth_client.get(_SETUP))

        assert second != first
        after = two_factor_row_of(user_id)
        assert mfa_service.decrypt_secret(after.pending_secret_encrypted) == second
        assert (
            before + _WINDOW - _SLACK
            <= after.pending_secret_expires_at
            <= before + _WINDOW + _SLACK
        )
        assert _key_on(auth_client.get(_SETUP)) == second

    def test_a_pending_key_that_does_not_decrypt_is_replaced(
        self, auth_client, seed_user,
    ):
        """Written under a key the app no longer holds: a new key, readable, fresh window."""
        user_id = seed_user["user"].id
        _key_on(auth_client.get(_SETUP))
        config = two_factor_row_of(user_id)
        config.pending_secret_encrypted = Fernet(Fernet.generate_key()).encrypt(
            b"JBSWY3DPEHPK3PXP",
        )
        db.session.commit()

        key = _key_on(auth_client.get(_SETUP))

        after = two_factor_row_of(user_id)
        assert mfa_service.decrypt_secret(after.pending_secret_encrypted) == key
        assert key != "JBSWY3DPEHPK3PXP"

    def test_after_a_reset_the_next_setup_makes_a_new_key(
        self, app, auth_client, seed_user,
    ):
        """``scripts/reset_mfa.py`` clears the pending key with the rest."""
        first = _key_on(auth_client.get(_SETUP))

        reset_mfa(SEED_USER_EMAIL)

        assert two_factor_row_of(seed_user["user"].id).pending_secret_encrypted is None
        assert _key_on(_owner_signed_in_again(app).get(_SETUP)) != first

    def test_after_confirming_and_a_reset_the_next_setup_makes_a_new_key(
        self, app, auth_client, seed_user,
    ):
        """Confirming spends the pending key; the setup after a reset is a new one."""
        user_id = seed_user["user"].id
        first = _key_on(auth_client.get(_SETUP))
        assert auth_client.post(
            "/mfa/confirm", data={"totp_code": code_now(first)},
        ).status_code == 200
        assert two_factor_row_of(user_id).pending_secret_encrypted is None
        reset_mfa(SEED_USER_EMAIL)

        assert _key_on(_owner_signed_in_again(app).get(_SETUP)) != first
