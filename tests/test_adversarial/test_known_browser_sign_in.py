"""Shekel Budget App -- Adversarial Tests: a browser that has signed in before.

Plan#2772.  A browser that finishes a sign-in holds one record and one cookie
(ruling **plan#2994**), for thirty days from its last sign-in (ruling
**plan#2995**).  These tests drive the real app through its forms:

* :class:`TestEveryFinishedSignIn` -- what writes the record and the cookie, and
  what never does: a password alone on an account that still owes its code.
* :class:`TestTheCookieIsReplaced` -- each sign-in replaces the token and keeps
  the row, so a stolen copy stops working at the real browser's next sign-in,
  and a trust keeps its own date.
* :class:`TestWhatEndsBeingKnown` -- age does.  No bump does (ruling
  **plan#2997**).
"""

from datetime import timedelta

import sqlalchemy

from app.extensions import db
from app.models.known_device import KnownDevice
from app.services import known_device_service
from tests._test_helpers import enable_mfa
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_SECOND_EMAIL = "second@shekel.local"
_SECOND_PASSWORD = "secondpass12"

_BACKUP_CODE = "aaaaaaaabbbbbbbbccccccccdddd"

#: Thirty days in seconds: the cookie's Max-Age, spelled here as an oracle.
_THIRTY_DAYS_IN_SECONDS = 30 * 24 * 60 * 60


def _password(client, email=SEED_USER_EMAIL, password=SEED_USER_PASSWORD, **kwargs):
    """POST the sign-in form; return the response."""
    return client.post(
        "/login", data={"email": email, "password": password}, **kwargs,
    )


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


def _cookie_header(response, user_id):
    """Return the ``Set-Cookie`` header naming *user_id*'s device cookie, or None."""
    for header in response.headers.getlist("Set-Cookie"):
        if header.split("=", 1)[0].endswith(f"shekel_device_{user_id}"):
            return header
    return None


def _token(browser, user_id):
    """Return the device token *browser* holds for *user_id*."""
    return browser.get_cookie(f"shekel_device_{user_id}").value


def _rows_of(user_id):
    """Return *user_id*'s device rows, oldest first, read fresh."""
    db.session.expire_all()
    return db.session.query(KnownDevice).filter_by(
        user_id=user_id,
    ).order_by(KnownDevice.id).all()


def _age_the_last_sign_in(user_id, interval):
    """Move *user_id*'s last sign-ins back by the SQL *interval*, committed."""
    db.session.execute(sqlalchemy.text(
        "UPDATE auth.known_devices "
        "SET last_signed_in_at = last_signed_in_at - CAST(:interval AS interval) "
        "WHERE user_id = :id"
    ), {"interval": interval, "id": user_id})
    db.session.commit()


class TestEveryFinishedSignIn:
    """A finished sign-in writes the record and the cookie; nothing short of one does."""

    def test_a_password_with_no_second_factor_writes_both(self, app, seed_user):
        """One row holding no trust, and the cookie with its flags, on the redirect."""
        user_id = seed_user["user"].id

        response = _password(app.test_client())

        assert _signed_in(response)
        (device,) = _rows_of(user_id)
        assert device.trusted_at is None
        assert device.failed_login_count == 0
        header = _cookie_header(response, user_id)
        assert header.startswith(f"shekel_device_{user_id}=")
        assert "HttpOnly" in header
        assert "SameSite=Strict" in header
        assert "Path=/" in header
        assert f"Max-Age={_THIRTY_DAYS_IN_SECONDS}" in header

    def test_a_companion_is_known_too(self, app, seed_companion):
        """An account with no two-factor and no trust is who this exists for."""
        companion_id = seed_companion["user"].id

        response = _password(
            app.test_client(), "companion@shekel.local", "companionpass",
        )

        assert _signed_in(response)
        assert len(_rows_of(companion_id)) == 1
        assert _cookie_header(response, companion_id) is not None

    def test_a_password_alone_earns_nothing_on_an_account_with_a_code(
        self, app, seed_user,
    ):
        """The password is right and the code is owed: no row and no cookie.

        So knowing the password never makes a browser known.
        """
        user_id = seed_user["user"].id
        enable_mfa(db.session, user_id)

        response = _password(app.test_client())

        assert _asked_the_code(response)
        assert _cookie_header(response, user_id) is None
        assert _rows_of(user_id) == []

    def test_a_wrong_code_earns_nothing(self, app, seed_user):
        """The code form refuses: still no row and no cookie."""
        user_id = seed_user["user"].id
        enable_mfa(db.session, user_id)
        browser = app.test_client()
        assert _asked_the_code(_password(browser))

        response = browser.post("/mfa/verify", data={"totp_code": "000000"})

        assert response.status_code == 200
        assert _cookie_header(response, user_id) is None
        assert _rows_of(user_id) == []

    def test_a_wrong_password_earns_nothing(self, app, seed_user):
        """No row and no cookie for a stranger's wrong guess."""
        user_id = seed_user["user"].id

        response = _password(app.test_client(), password="not-the-password")

        assert response.status_code == 200
        assert _cookie_header(response, user_id) is None
        assert _rows_of(user_id) == []

    def test_a_backup_code_finishes_a_sign_in(self, app, seed_user):
        """The browser is known, and survives the bump its own backup code makes."""
        user_id = seed_user["user"].id
        enable_mfa(db.session, user_id, backup_codes=[_BACKUP_CODE])
        browser = app.test_client()
        assert _asked_the_code(_password(browser))

        response = browser.post("/mfa/verify", data={"backup_code": _BACKUP_CODE})

        assert _signed_in(response)
        standing = known_device_service.find_browser(
            user_id, _token(browser, user_id),
        )
        assert standing is not None
        assert standing.trusted is False

    def test_two_people_on_one_browser_each_keep_their_own(
        self, app, seed_user, seed_second_user,
    ):
        """Two cookies and two rows; neither sign-in disturbs the other's."""
        first_id = seed_user["user"].id
        second_id = seed_second_user["user"].id
        browser = app.test_client()

        assert _signed_in(_password(browser))
        first_token = _token(browser, first_id)
        browser.post("/logout")
        assert _signed_in(_password(browser, _SECOND_EMAIL, _SECOND_PASSWORD))

        assert _token(browser, first_id) == first_token
        assert known_device_service.find_browser(first_id, first_token) is not None
        assert known_device_service.find_browser(
            second_id, _token(browser, second_id),
        ) is not None


class TestTheCookieIsReplaced:
    """Each sign-in replaces the token and keeps the row."""

    def test_a_second_sign_in_keeps_the_row_and_changes_the_token(
        self, app, seed_user,
    ):
        """One row still, and a stolen copy of the old token now names nothing."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        old_token = _token(browser, user_id)
        (device,) = _rows_of(user_id)
        device_id = device.id
        browser.post("/logout")

        assert _signed_in(_password(browser))

        new_token = _token(browser, user_id)
        assert new_token != old_token
        assert [row.id for row in _rows_of(user_id)] == [device_id]
        assert known_device_service.find_browser(user_id, old_token) is None
        assert known_device_service.find_browser(user_id, new_token) is not None

    def test_a_sign_in_keeps_the_trusts_own_date(self, app, seed_user):
        """The token moves and ``trusted_at`` does not: thirty days from the code.

        Trusted through the service, so no second code is spent.
        """
        user_id = seed_user["user"].id
        enable_mfa(db.session, user_id)
        token = known_device_service.record_sign_in(
            user_id, None, "", trust=True,
        )
        db.session.commit()
        (device,) = _rows_of(user_id)
        trusted_at = device.trusted_at
        browser = app.test_client()
        browser.set_cookie(f"shekel_device_{user_id}", token)

        assert _signed_in(_password(browser))

        (device,) = _rows_of(user_id)
        assert device.trusted_at == trusted_at
        assert device.last_signed_in_at > trusted_at
        assert _token(browser, user_id) != token

    def test_a_sign_in_keeps_the_latest_browser_name(self, app, seed_user):
        """The row names the browser as it last signed in."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser, headers={"User-Agent": "First"}))
        browser.post("/logout")

        assert _signed_in(_password(browser, headers={"User-Agent": "Second"}))

        (device,) = _rows_of(user_id)
        assert device.user_agent == "Second"


class TestWhatEndsBeingKnown:
    """Age ends it, and each sign-in renews it; no bump ends it."""

    def test_a_sign_in_renews_the_thirty_days(self, app, seed_user):
        """Twenty-nine days old, signed in again: as good as new."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        browser.post("/logout")
        _age_the_last_sign_in(user_id, "29 days")
        (device,) = _rows_of(user_id)
        aged = device.last_signed_in_at

        assert _signed_in(_password(browser))

        (device,) = _rows_of(user_id)
        assert device.last_signed_in_at - aged >= timedelta(days=29)

    def test_thirty_days_makes_a_stranger(self, app, seed_user):
        """Thirty days and a minute: the token is worth nothing, and the row is purged.

        The control is the same browser a minute inside the thirty days.
        """
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        browser.post("/logout")
        old_id = _rows_of(user_id)[0].id
        token = _token(browser, user_id)
        _age_the_last_sign_in(user_id, "30 days - 1 minute")
        assert known_device_service.find_browser(user_id, token) is not None

        _age_the_last_sign_in(user_id, "2 minutes")
        assert known_device_service.find_browser(user_id, token) is None

        assert _signed_in(_password(browser))
        assert old_id not in [row.id for row in _rows_of(user_id)]
        assert len(_rows_of(user_id)) == 1

    def test_logging_out_all_sessions_leaves_it_known(self, app, seed_user):
        """``/invalidate-sessions`` writes the bump, and the token is still good."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))

        assert browser.post("/invalidate-sessions").status_code == 302

        assert known_device_service.find_browser(
            user_id, _token(browser, user_id),
        ) is not None

    def test_a_password_change_leaves_it_known(self, app, seed_user):
        """``/change-password`` writes the bump, and the token is still good."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        new_password = "a-brand-new-passphrase"

        response = browser.post("/change-password", data={
            "current_password": SEED_USER_PASSWORD,
            "new_password": new_password,
            "confirm_password": new_password,
        })

        assert response.status_code == 302
        db.session.expire_all()
        assert seed_user["user"] is not None
        assert known_device_service.find_browser(
            user_id, _token(browser, user_id),
        ) is not None
