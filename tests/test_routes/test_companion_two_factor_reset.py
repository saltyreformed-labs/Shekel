"""The owner can reset a companion's two-factor, one act with the reset script.

Plan#2407, task plan#3447, ruling **plan#3441**: each active companion with
two-factor on has a "Reset two-factor" on Settings > Companions, for a lost or
replaced phone.  It runs the one reset act ``scripts/reset_mfa.py`` runs
(``mfa_service.reset_two_factor``): her secret and backup codes go, her
sessions end and her trusted devices are forgotten, and at her next sign-in
the login gate holds her at setup (ruling plan#3434).  It asks a re-confirmed
password first, as the other doors that change a companion's account do.

Another owner's companion and a user who is not a companion are one 404, each
paired with a case proving the URL still routes; a companion cannot reach the
door.

**One code per secret per test**: replay prevention refuses a second code
inside one thirty-second step, so each secret a test makes is typed once.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pyotp
import pytest

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import known_device_service, mfa_service
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from scripts.reset_mfa import reset_mfa
from tests._companions import (
    COMPANIONS_PAGE, another_owners_companion, companion_row,
    landed_on_companions, text_of,
)
from tests._test_helpers import enable_mfa
from tests._trusted_devices import FIREFOX_LINUX, SAFARI_IPHONE, trust
from tests._two_factor_sign_in import (
    SEED_COMPANION_EMAIL, SEED_COMPANION_PASSWORD,
    sign_in_seed_companion_by_password,
)
from tests.conftest import SEED_USER_EMAIL

#: Where a sign-in lands a companion, and where the hold sends her.
_HER_PAGE = "/companion/"
_SETUP = "/mfa/setup"


def _reset_url(companion_id):
    """Return the owner's Reset two-factor URL for a companion."""
    return f"/settings/companions/{companion_id}/reset-two-factor"


def _code(secret):
    """Return the authenticator code for *secret* at this instant."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _path(response):
    """Return the path a redirect sends the browser to, or ``""``."""
    return urlsplit(response.headers.get("Location", "")).path


def _her_password(browser):
    """POST the seed companion's correct password to the sign-in form."""
    return browser.post("/login", data={
        "email": SEED_COMPANION_EMAIL, "password": SEED_COMPANION_PASSWORD,
    })


def _config_of(user_id):
    """Return *user_id*'s two-factor row, read fresh, or ``None``."""
    db.session.expire_all()
    return db.session.query(MfaConfig).filter_by(user_id=user_id).one_or_none()


def _her_browser_with_two_factor(app, companion_id):
    """Return a browser signed in as her, her two-factor on with a backup code.

    She signs in with her password and gets two-factor after: the session
    then reaches her pages (the hold reads her two-factor on each request),
    and no code of her secret has been typed.
    """
    browser = sign_in_seed_companion_by_password(app)
    enable_mfa(db.session, companion_id, backup_codes=("1234-5678",))
    assert browser.get(_HER_PAGE).status_code == 200
    return browser


def _make_session_stale(client):
    """Put *client*'s last password check outside the fresh-login window."""
    stale = datetime.now(timezone.utc) - timedelta(minutes=6)
    with client.session_transaction() as session:
        session[FRESH_LOGIN_AT_KEY] = stale.isoformat()


class TestTheButton:
    """Her row offers Reset two-factor while hers is on, and not before."""

    def test_with_two_factor_on_her_row_has_the_reset(
        self, auth_client, seed_companion,
    ):
        """The form posts to her reset URL and asks to confirm first."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)

        row = companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True))

        assert f'action="{_reset_url(companion_id)}"' in row
        assert "Reset two-factor" in text_of(row)
        assert (
            'data-confirm="Reset two-factor authentication for Companion User? '
            "They are signed out everywhere, their trusted devices are "
            "forgotten, and they set it up again with their authenticator app "
            'the next time they use Shekel."'
        ) in row

    @pytest.mark.usefixtures("seed_companion")
    def test_without_two_factor_there_is_nothing_to_reset(self, auth_client):
        """No reset form while hers was never set up."""
        row = companion_row(auth_client.get(COMPANIONS_PAGE).get_data(as_text=True))

        assert "reset-two-factor" not in row
        assert "Reset two-factor" not in text_of(row)


class TestTheReset:
    """The reset ends her sessions and trusts, clears her secret, and sends her to setup."""

    def test_her_session_ends_her_secret_goes_and_she_meets_setup(
        self, app, auth_client, seed_companion,
    ):
        """Refused at her next request; row cleared; her next sign-in is held at setup."""
        companion_id = seed_companion["user"].id
        browser = _her_browser_with_two_factor(app, companion_id)
        before = _config_of(companion_id)
        assert before.is_enabled and before.backup_codes

        response = auth_client.post(_reset_url(companion_id), follow_redirects=True)

        page = landed_on_companions(response)
        assert (
            "Two-factor authentication reset for Companion User. They are "
            "signed out everywhere and will set it up again the next time "
            "they use Shekel."
        ) in text_of(page)
        assert "Two-factor not set up" in text_of(companion_row(page))
        # Her live session is refused.
        assert _path(browser.get(_HER_PAGE)) == "/login"
        # Her secret and backup codes are gone, with every other material.
        after = _config_of(companion_id)
        assert after.is_enabled is False
        assert after.totp_secret_encrypted is None
        assert after.backup_codes is None
        assert after.confirmed_at is None
        assert after.last_totp_timestep is None
        # Her next sign-in takes her password alone and is held at setup.
        again = sign_in_seed_companion_by_password(app)
        assert _path(again.get(_HER_PAGE)) == _SETUP

    def test_her_trusted_device_no_longer_skips_the_code(
        self, app, auth_client, seed_companion,
    ):
        """Trusted before the reset; after it and her new setup, that browser asks the code.

        The first sign-in after the trust is the pair: password alone signs
        her in, so the device was trusted and the code form at the end is the
        reset's doing.
        """
        companion_id = seed_companion["user"].id
        old_secret = enable_mfa(db.session, companion_id)
        browser = app.test_client()
        browser.environ_base["HTTP_USER_AGENT"] = FIREFOX_LINUX
        assert _path(_her_password(browser)) == "/mfa/verify"
        trusted = browser.post("/mfa/verify", data={
            "totp_code": _code(old_secret), "trust_device": "1",
        })
        assert _path(trusted) == _HER_PAGE
        browser.post("/logout")
        assert _path(_her_password(browser)) == _HER_PAGE
        browser.post("/logout")
        trust(companion_id, SAFARI_IPHONE)

        assert auth_client.post(_reset_url(companion_id)).status_code == 302

        assert not known_device_service.list_trusted_devices(companion_id)
        # She sets two-factor up again on the same browser.
        assert _path(_her_password(browser)) == _HER_PAGE
        assert browser.get(_SETUP).status_code == 200
        new_secret = mfa_service.decrypt_secret(
            _config_of(companion_id).pending_secret_encrypted,
        )
        confirmed = browser.post("/mfa/confirm", data={"totp_code": _code(new_secret)})
        assert b"Save Your Backup Codes" in confirmed.data
        browser.post("/logout")

        assert _path(_her_password(browser)) == "/mfa/verify"

    @pytest.mark.usefixtures("seed_companion")
    def test_nothing_to_reset_is_said_and_nothing_written(self, auth_client):
        """Hers never set up: told so, her sessions left alone."""
        companion = db.session.query(User).filter_by(email=SEED_COMPANION_EMAIL).one()

        response = auth_client.post(_reset_url(companion.id), follow_redirects=True)

        assert response.status_code == 200
        assert (
            "Companion User has no two-factor authentication to reset."
        ) in text_of(response.get_data(as_text=True))
        db.session.expire_all()
        assert db.session.get(User, companion.id).session_invalidated_at is None
        assert _config_of(companion.id) is None

    def test_midway_through_setup_is_nothing_to_reset_and_her_key_survives(
        self, app, auth_client, seed_companion,
    ):
        """Her setup opened once: told so, her session and her pending key kept.

        Every companion the login gate holds has a pending key after her
        first page, which the reset act counts as material; the door reads
        whether her two-factor is on instead (plan#3515).
        """
        companion_id = seed_companion["user"].id
        browser = sign_in_seed_companion_by_password(app)
        assert browser.get(_SETUP).status_code == 200
        pending = _config_of(companion_id).pending_secret_encrypted
        key = mfa_service.decrypt_secret(pending)

        response = auth_client.post(_reset_url(companion_id), follow_redirects=True)

        assert (
            "Companion User has no two-factor authentication to reset."
        ) in text_of(landed_on_companions(response))
        db.session.expire_all()
        assert db.session.get(User, companion_id).session_invalidated_at is None
        assert _config_of(companion_id).pending_secret_encrypted == pending
        # Still signed in, and her next setup shows the key she scanned.
        again = browser.get(_SETUP)
        assert again.status_code == 200
        assert f"<code>{key}</code>" in again.get_data(as_text=True)

    def test_the_script_and_the_door_run_the_one_act(
        self, auth_client, seed_user, seed_companion, monkeypatch,
    ):
        """``scripts/reset_mfa.py`` and the owner's door each call ``reset_two_factor``.

        The act is wrapped, so each still resets: the script's own tests
        (``tests/test_scripts/test_reset_mfa.py``) grade what it does, and
        this grades that the two run the one function rather than copies.
        """
        companion_id = seed_companion["user"].id
        owner_id = seed_user["user"].id
        act = mfa_service.reset_two_factor
        reset = []

        def recording(user):
            reset.append(user.id)
            return act(user)

        monkeypatch.setattr(mfa_service, "reset_two_factor", recording)
        enable_mfa(db.session, companion_id)
        enable_mfa(db.session, owner_id)

        assert auth_client.post(_reset_url(companion_id)).status_code == 302
        reset_mfa(SEED_USER_EMAIL)

        assert reset == [companion_id, owner_id]
        assert _config_of(companion_id).is_enabled is False
        assert _config_of(owner_id).is_enabled is False

    def test_the_reset_is_logged(
        self, auth_client, seed_user, seed_companion, caplog,
    ):
        """``mfa_reset``: the owner pressed it, the two-factor was hers."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)

        with caplog.at_level(logging.INFO):
            auth_client.post(_reset_url(companion_id))

        (record,) = [
            r for r in caplog.records if getattr(r, "event", None) == "mfa_reset"
        ]
        assert record.levelno == logging.WARNING
        assert record.user_id == seed_user["user"].id
        assert record.reset_user_id == companion_id


class TestTheRefusals:
    """A stale owner session re-confirms first; every other door is one 404."""

    def test_a_stale_session_is_sent_to_reauth_and_nothing_resets(
        self, auth_client, seed_companion,
    ):
        """Past the fresh-login window: to ``/reauth``, her two-factor kept."""
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        _make_session_stale(auth_client)

        response = auth_client.post(_reset_url(companion_id))

        assert response.status_code == 302
        assert _path(response) == "/reauth"
        assert _config_of(companion_id).is_enabled is True

    def test_another_owners_companion_is_404(
        self, auth_client, seed_companion, seed_second_user,
    ):
        """Refused and hers kept; the owner's own companion's URL routes."""
        foreign = another_owners_companion(seed_second_user["user"].id)
        enable_mfa(db.session, foreign)
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)

        response = auth_client.post(_reset_url(foreign))

        assert response.status_code == 404
        assert _config_of(foreign).is_enabled is True
        # The pair: the same URL, with the owner's own companion.
        assert auth_client.post(_reset_url(companion_id)).status_code == 302
        assert _config_of(companion_id).is_enabled is False

    def test_a_user_who_is_not_a_companion_is_404(
        self, auth_client, seed_user, seed_companion,
    ):
        """The owner's own id names no companion: refused, the owner's kept."""
        owner_id = seed_user["user"].id
        enable_mfa(db.session, owner_id)
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)

        response = auth_client.post(_reset_url(owner_id))

        assert response.status_code == 404
        assert _config_of(owner_id).is_enabled is True
        # The pair.
        assert auth_client.post(_reset_url(companion_id)).status_code == 302
        assert _config_of(companion_id).is_enabled is False

    def test_a_companion_cannot_reach_the_door(
        self, auth_client, companion_client, seed_companion,
    ):
        """Her own reset URL: 404 to her, a reset to the owner."""
        companion_id = seed_companion["user"].id

        response = companion_client.post(_reset_url(companion_id))

        assert response.status_code == 404
        assert _config_of(companion_id).is_enabled is True
        # The pair: the owner's browser, the same URL.
        assert auth_client.post(_reset_url(companion_id)).status_code == 302
        assert _config_of(companion_id).is_enabled is False
