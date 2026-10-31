"""Shekel Budget App -- Adversarial Tests: a trusted device skips only the code.

Plan#2173, ruling **plan#2406**.  On ``/mfa/verify`` an authenticator code with
"Trust this device" ticked records the browser and sets its cookie; a later
``/login`` with the correct password on that browser, for that user, signs in
without the code.  Everything else still asks for it.  These tests drive the
real app through its two forms, with real TOTP codes:

* the trust, and the next sign-in that skips the code;
* what never trusts (an unticked box, a backup code even ticked);
* what the cookie cannot do (sign in without the password, with a wrong one,
  or for another user);
* what forgets the device (a sign-out of all sessions, a password change, a
  backup-code sign-in elsewhere, thirty days);
* ``/reauth`` still asking for the code, and the production cookie's flags.

**One code per user per test**, because replay prevention refuses a second
code inside its thirty-second step: every sign-in after the first either skips
the code or stops at the code form.
"""

import logging
import time

from datetime import datetime, timezone

import pyotp
import sqlalchemy

from app.config import ProdConfig
from app.extensions import db
from app.models.trusted_device import TrustedDevice
from app.models.user import MfaConfig
from app.services import mfa_service, trusted_device_service
from scripts.reset_mfa import reset_mfa
from tests._test_helpers import enable_mfa
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_SECOND_EMAIL = "second@shekel.local"
_SECOND_PASSWORD = "secondpass12"

_BACKUP_CODE = "aaaaaaaabbbbbbbbccccccccdddd"

_COMPANION_EMAIL = "companion@shekel.local"
_COMPANION_PASSWORD = "companionpass"

#: Thirty days in seconds: the cookie's Max-Age, spelled here as an oracle.
_THIRTY_DAYS_IN_SECONDS = 30 * 24 * 60 * 60


def _code(secret):
    """Return the authenticator code for *secret* at this instant."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _password(client, email, password, **kwargs):
    """POST the sign-in form; return the response."""
    return client.post(
        "/login", data={"email": email, "password": password}, **kwargs,
    )


def _sign_in_with_code(client, email, password, secret, trust, **kwargs):
    """Sign in through both forms with a real code; return the code response.

    Args:
        client: The browser.
        email: The account's email.
        password: Its password.
        secret: Its TOTP secret.
        trust: Whether "Trust this device" is ticked.
        **kwargs: Passed to both requests (``base_url``).
    """
    assert _asked_the_code(_password(client, email, password, **kwargs))
    form = {"totp_code": _code(secret)}
    if trust:
        form["trust_device"] = "1"
    response = client.post("/mfa/verify", data=form, **kwargs)
    assert _signed_in(response), (
        f"Setup failed: the code sign-in did not complete "
        f"({response.status_code}, {response.headers.get('Location')!r})"
    )
    return response


def _asked_the_code(response):
    """Return whether *response* sends the browser to the code form."""
    return (
        response.status_code == 302
        and response.headers["Location"].endswith("/mfa/verify")
    )


def _signed_in(response):
    """Return whether *response* is a completed sign-in's redirect."""
    if response.status_code != 302:
        return False
    location = response.headers["Location"]
    return "/login" not in location and "/mfa/verify" not in location


def _devices_of(user_id):
    """Return how many device rows *user_id* holds."""
    return db.session.query(TrustedDevice).filter_by(user_id=user_id).count()


def _device_cookie_header(response, user_id):
    """Return the ``Set-Cookie`` header naming *user_id*'s device cookie, or None."""
    for header in response.headers.getlist("Set-Cookie"):
        name = header.split("=", 1)[0]
        if name.endswith(f"shekel_device_{user_id}"):
            return header
    return None


def _move_the_trust_back(seed_user, interval):
    """Move the seed owner's device trust back by the SQL *interval*, committed."""
    db.session.execute(sqlalchemy.text(
        "UPDATE auth.trusted_devices "
        "SET trusted_at = trusted_at - CAST(:interval AS interval) "
        "WHERE user_id = :id"
    ), {"interval": interval, "id": seed_user["user"].id})
    db.session.commit()


def _enrol_a_new_authenticator(user_id):
    """Turn two-factor back on for *user_id* under a fresh secret, committed."""
    config = db.session.query(MfaConfig).filter_by(user_id=user_id).one()
    config.is_enabled = True
    config.totp_secret_encrypted = mfa_service.encrypt_secret(
        mfa_service.generate_totp_secret(),
    )
    config.confirmed_at = datetime.now(timezone.utc)
    db.session.commit()


def _browser_trusted_through_the_service(app, user_id):
    """Return a browser holding a device cookie the service trusted for *user_id*.

    What the code form's trust writes, without spending a code on it, for a
    test whose one code another act needs (replay prevention refuses a second
    inside its thirty-second step).
    """
    token = trusted_device_service.trust_device(user_id, "")
    db.session.commit()
    browser = app.test_client()
    browser.set_cookie(f"shekel_device_{user_id}", token)
    return browser


def _trusted_browser(app, seed_user):
    """Return a browser that trusted itself for the seed owner, signed out again."""
    user_id = seed_user["user"].id
    secret = enable_mfa(db.session, user_id)
    browser = app.test_client()
    _sign_in_with_code(
        browser, SEED_USER_EMAIL, SEED_USER_PASSWORD, secret, trust=True,
    )
    assert _devices_of(user_id) == 1
    browser.post("/logout")
    return browser


class TestTheTrust:
    """A ticked code trusts the browser; the next sign-in there skips the code."""

    def test_the_next_sign_in_on_that_browser_skips_the_code(
        self, app, seed_user,
    ):
        """Password only, straight to the dashboard, and signed in for real."""
        browser = _trusted_browser(app, seed_user)

        response = _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD)

        assert _signed_in(response)
        assert browser.get("/dashboard").status_code == 200

    def test_the_cookie_carries_its_flags_in_this_configuration(
        self, app, seed_user,
    ):
        """HttpOnly, SameSite=Strict, Path=/, thirty days; not Secure here."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)

        response = _sign_in_with_code(
            app.test_client(), SEED_USER_EMAIL, SEED_USER_PASSWORD, secret,
            trust=True,
        )

        header = _device_cookie_header(response, user_id)
        assert header is not None
        assert header.startswith(f"shekel_device_{user_id}=")
        assert "HttpOnly" in header
        assert "SameSite=Strict" in header
        assert "Path=/" in header
        assert f"Max-Age={_THIRTY_DAYS_IN_SECONDS}" in header
        assert "Secure" not in header

    def test_an_unticked_code_trusts_nothing(self, app, seed_user):
        """No row, no cookie, and the next sign-in asks for the code."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = app.test_client()

        response = _sign_in_with_code(
            browser, SEED_USER_EMAIL, SEED_USER_PASSWORD, secret, trust=False,
        )
        browser.post("/logout")

        assert _device_cookie_header(response, user_id) is None
        assert _devices_of(user_id) == 0
        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )

    def test_a_backup_code_never_trusts_even_ticked(self, app, seed_user):
        """A crafted post ticking the box beside a backup code trusts nothing."""
        user_id = seed_user["user"].id
        enable_mfa(db.session, user_id, backup_codes=[_BACKUP_CODE])
        browser = app.test_client()
        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )

        response = browser.post("/mfa/verify", data={
            "backup_code": _BACKUP_CODE, "trust_device": "1",
        })

        assert _signed_in(response)
        assert _device_cookie_header(response, user_id) is None
        assert _devices_of(user_id) == 0

    def test_the_trust_and_the_skip_are_logged(self, app, seed_user, caplog):
        """``device_trusted`` at the trust, ``trusted_device_sign_in`` at the skip."""
        with caplog.at_level(logging.INFO):
            browser = _trusted_browser(app, seed_user)
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD)

        events = [getattr(r, "event", None) for r in caplog.records]
        assert events.count("device_trusted") == 1
        assert events.count("trusted_device_sign_in") == 1

    def test_the_code_form_offers_the_box(self, app, seed_user):
        """The code form carries "Trust this device for 30 days"; the backup form does not."""
        enable_mfa(db.session, seed_user["user"].id)
        browser = app.test_client()
        _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD)

        page = browser.get("/mfa/verify").get_data(as_text=True)

        assert "Trust this device for 30 days" in page
        assert page.count('name="trust_device"') == 1
        code_form, backup_form = page.split("<hr>")
        assert 'name="trust_device"' in code_form
        assert 'name="trust_device"' not in backup_form


class TestWhatTheCookieCannotDo:
    """The cookie skips the code for its own user's correct password, and nothing else."""

    def test_with_a_wrong_password_it_is_refused(self, app, seed_user):
        """A wrong password on a trusted browser is refused, as anywhere."""
        browser = _trusted_browser(app, seed_user)

        response = _password(browser, SEED_USER_EMAIL, "not-the-password")

        assert response.status_code == 200
        assert "Invalid email or password." in response.get_data(as_text=True)
        assert browser.get("/dashboard").status_code == 302
        # Control: the same browser, with the right password, skips the code.
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))

    def test_alone_it_signs_nobody_in(self, app, seed_user):
        """A browser holding only the cookie, and no password, is signed out."""
        user_id = seed_user["user"].id
        trusted = _trusted_browser(app, seed_user)
        name = f"shekel_device_{user_id}"
        token = trusted.get_cookie(name).value
        stranger = app.test_client()
        stranger.set_cookie(name, token)

        assert stranger.get("/dashboard").status_code == 302
        assert "/login" in stranger.get("/dashboard").headers["Location"]
        assert stranger.get("/mfa/verify").headers["Location"].endswith("/login")

    def test_another_users_cookie_asks_the_code(
        self, app, seed_user, seed_second_user,
    ):
        """The second user, on a browser trusted by the first, is asked for a code.

        Both ways the cookie could be presented: under the first user's name,
        which the second user's sign-in never reads, and renamed to the second
        user's, whose token names a device of the first user only.
        """
        first_id = seed_user["user"].id
        second_id = seed_second_user["user"].id
        enable_mfa(db.session, second_id)
        browser = _trusted_browser(app, seed_user)

        assert _asked_the_code(
            _password(browser, _SECOND_EMAIL, _SECOND_PASSWORD),
        )

        renamed = app.test_client()
        renamed.set_cookie(
            f"shekel_device_{second_id}",
            browser.get_cookie(f"shekel_device_{first_id}").value,
        )
        assert _asked_the_code(
            _password(renamed, _SECOND_EMAIL, _SECOND_PASSWORD),
        )
        # Control: the browser is trusted, for the user who trusted it.
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))

    def test_reauth_still_asks_the_code(self, app, seed_user):
        """On a trusted sign-in, the step-up re-auth refuses a password alone."""
        browser = _trusted_browser(app, seed_user)
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))

        response = browser.post("/reauth", data={
            "password": SEED_USER_PASSWORD, "totp_code": "",
        })

        assert response.status_code == 200
        assert "Invalid authentication code." in response.get_data(as_text=True)


class TestWhatForgetsTheDevice:
    """Every writer of ``session_invalidated_at`` forgets it, and so do thirty days."""

    def test_a_sign_out_of_all_sessions(self, app, seed_user):
        """"Log out all other sessions", then the next sign-in asks the code."""
        browser = _trusted_browser(app, seed_user)
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))

        assert browser.post("/invalidate-sessions").status_code == 302
        browser.post("/logout")

        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )

    def test_a_password_change(self, app, seed_user):
        """A password change, then the new password still asks the code."""
        browser = _trusted_browser(app, seed_user)
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))
        new_password = "a-brand-new-passphrase"

        response = browser.post("/change-password", data={
            "current_password": SEED_USER_PASSWORD,
            "new_password": new_password,
            "confirm_password": new_password,
        })
        assert response.status_code == 302
        browser.post("/logout")

        assert _asked_the_code(_password(browser, SEED_USER_EMAIL, new_password))

    def test_a_backup_code_sign_in_elsewhere(self, app, seed_user):
        """A backup code spent on another browser, then this one asks the code."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id, backup_codes=[_BACKUP_CODE])
        browser = app.test_client()
        _sign_in_with_code(
            browser, SEED_USER_EMAIL, SEED_USER_PASSWORD, secret, trust=True,
        )
        browser.post("/logout")
        # Control: before the backup code, this browser skips the code.
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))
        browser.post("/logout")
        elsewhere = app.test_client()
        assert _asked_the_code(
            _password(elsewhere, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )
        assert _signed_in(
            elsewhere.post("/mfa/verify", data={"backup_code": _BACKUP_CODE}),
        )

        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )

    def test_turning_two_factor_off(self, app, seed_user):
        """``/mfa/disable``, then a new authenticator: the code is asked."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _browser_trusted_through_the_service(app, user_id)
        # Control: before two-factor is turned off, this browser skips the code.
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))

        response = browser.post("/mfa/disable", data={
            "current_password": SEED_USER_PASSWORD, "totp_code": _code(secret),
        })
        assert response.status_code == 302
        assert db.session.query(MfaConfig).filter_by(
            user_id=user_id,
        ).one().is_enabled is False, "Setup failed: two-factor is still on"
        browser.post("/logout")
        _enrol_a_new_authenticator(user_id)

        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )

    def test_the_owners_password_reset_of_a_companion(
        self, app, auth_client, seed_companion,
    ):
        """The owner sets the companion a new password: her device asks the code.

        One of the two writers on the database's clock (``now()``).
        """
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        browser = _browser_trusted_through_the_service(app, companion_id)
        # Control: before the reset, her browser skips the code.
        assert _signed_in(
            _password(browser, _COMPANION_EMAIL, _COMPANION_PASSWORD),
        )
        browser.post("/logout")
        new_password = "a-new-companion-passphrase"

        response = auth_client.post(
            f"/settings/companions/{companion_id}/edit",
            data={
                "email": _COMPANION_EMAIL,
                "display_name": "Companion User",
                "password": new_password,
                "password_confirm": new_password,
            },
        )
        assert response.status_code == 302

        assert _asked_the_code(
            _password(browser, _COMPANION_EMAIL, new_password),
        )

    def test_the_owners_deactivation_of_a_companion(
        self, app, auth_client, seed_companion,
    ):
        """Deactivated and reactivated: her device asks the code.

        The other writer on the database's clock.  Reactivation writes nothing
        to ``session_invalidated_at``, so the deactivation's bump is what the
        next sign-in meets.
        """
        companion_id = seed_companion["user"].id
        enable_mfa(db.session, companion_id)
        browser = _browser_trusted_through_the_service(app, companion_id)
        # Control: before the deactivation, her browser skips the code.
        assert _signed_in(
            _password(browser, _COMPANION_EMAIL, _COMPANION_PASSWORD),
        )
        browser.post("/logout")

        for act in ("deactivate", "reactivate"):
            response = auth_client.post(
                f"/settings/companions/{companion_id}/{act}",
            )
            assert response.status_code == 302

        assert _asked_the_code(
            _password(browser, _COMPANION_EMAIL, _COMPANION_PASSWORD),
        )

    def test_the_emergency_reset_of_two_factor(self, app, seed_user):
        """``scripts/reset_mfa.py``, then a new authenticator: the code is asked.

        The lost-authenticator case: a browser trusted under the old
        authenticator must not skip the new one's code.  The new one is
        enrolled on the user's one MFA row, as ``/mfa/confirm`` enrols it.
        """
        browser = _trusted_browser(app, seed_user)
        # Control: before the reset, this browser skips the code.
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))
        browser.post("/logout")

        reset_mfa(SEED_USER_EMAIL)
        _enrol_a_new_authenticator(seed_user["user"].id)

        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )

    def test_thirty_days(self, app, seed_user):
        """Trusted 30 days less a minute ago it skips; a minute more, it asks.

        The trust is moved back in the database, the only clock the sign-in
        and the record share; the boundary to the second is the service
        tests'.
        """
        browser = _trusted_browser(app, seed_user)
        _move_the_trust_back(seed_user, "30 days - 1 minute")
        assert _signed_in(_password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD))
        browser.post("/logout")

        _move_the_trust_back(seed_user, "1 minute")

        assert _asked_the_code(
            _password(browser, SEED_USER_EMAIL, SEED_USER_PASSWORD),
        )


class TestTheProductionCookie:
    """Wherever the session cookie is Secure, the device cookie is too, and ``__Host-``."""

    def test_production_sets_the_session_cookie_secure(self):
        """The one source the device cookie reads is set in production."""
        assert ProdConfig.SESSION_COOKIE_SECURE is True

    def test_a_secure_session_cookie_makes_a_host_prefixed_secure_one(
        self, app, seed_user, monkeypatch,
    ):
        """Over HTTPS, with ``SESSION_COOKIE_SECURE``: ``__Host-``, Secure, the rest kept."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)
        https = {"base_url": "https://localhost"}

        response = _sign_in_with_code(
            app.test_client(), SEED_USER_EMAIL, SEED_USER_PASSWORD, secret,
            trust=True, **https,
        )

        header = _device_cookie_header(response, user_id)
        assert header is not None
        assert header.startswith(f"__Host-shekel_device_{user_id}=")
        assert "Secure" in header
        assert "HttpOnly" in header
        assert "SameSite=Strict" in header
        assert "Path=/" in header
        assert "Domain=" not in header
        assert f"Max-Age={_THIRTY_DAYS_IN_SECONDS}" in header
