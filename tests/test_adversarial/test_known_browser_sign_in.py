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
* :class:`TestPastTheStrangersLock` -- strangers' wrong passwords lock the
  account, and a known browser still signs in, leaving their count and lock
  as it found them.  A stranger, a new browser, another user's cookie and a
  cookie thirty days old stay refused.
* :class:`TestTenWrongMakeAStranger` -- a known browser's wrong passwords
  count on its own record, and ten in a row delete it (ruling **plan#2996**),
  leaving the user's other browsers signing in.
* :class:`TestEveryDoorHasTheOneRule` -- the code form, re-confirm, Change
  password and Disable two-factor let a known browser past the strangers' lock
  and count its wrong answers on its own record (ruling **plan#3034**).  Their
  stranger's side is ``tests/test_routes/test_every_guess_counts.py``.
* :class:`TestAReConfirmReplacesTheCookie` -- a finished re-confirm gives a
  known browser a new token on the same row, as a sign-in does (ruling
  **plan#3070**), and moves neither its thirty days nor its trust; a stranger
  gets none; and a record a copy of the cookie deletes after the code's commit
  leaves the re-confirm finishing, with nothing renewed or cleared.

The code form's tries, which a known browser has of its own (ruling
**plan#3023**), are ``test_known_browser_code_tries.py``.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import pyotp
import sqlalchemy

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import MfaConfig, User
from app.routes.auth import _helpers as auth_route_helpers
from app.services import auth_service, known_device_service
from app.utils.log_events import EVT_KNOWN_DEVICE_DELETED
from app.utils.session_helpers import SESSION_SIGNED_IN_AT_KEY
from tests._guess_records import count_of, locked_until_of
from tests._test_helpers import enable_mfa
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_SECOND_EMAIL = "second@shekel.local"
_SECOND_PASSWORD = "secondpass12"

_BACKUP_CODE = "aaaaaaaabbbbbbbbccccccccdddd"

#: Thirty days in seconds: the cookie's Max-Age, spelled here as an oracle.
_THIRTY_DAYS_IN_SECONDS = 30 * 24 * 60 * 60

#: Wrong passwords in a row that lock the account, or delete a known browser's
#: record (ruling plan#2996): spelled here as an oracle.
_THRESHOLD = 10

_WRONG_PASSWORD = "not-the-password"

_REFUSED = b"Invalid email or password."

#: The length of one authenticator step.
_TOTP_STEP_SECONDS = 30


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


def _lock_state(user_id):
    """Return the ``(count, locked_until)`` on the record of *user_id*'s email, read fresh."""
    db.session.expire_all()
    email = db.session.get(User, user_id).email
    return count_of(email), locked_until_of(email)


def _strangers_guess(app, times, email=SEED_USER_EMAIL):
    """POST *times* wrong passwords for *email*, each from a new browser."""
    for _ in range(times):
        response = _password(app.test_client(), email, _WRONG_PASSWORD)
        assert _REFUSED in response.data


def _strangers_lock(app, user_id):
    """Lock *user_id* by strangers' wrong passwords; return its lock state.

    Ten of them, each from a new browser, so the lock is the one the real
    form puts on the account.
    """
    _strangers_guess(app, _THRESHOLD)
    count, locked_until = _lock_state(user_id)
    assert locked_until > datetime.now(timezone.utc)
    return count, locked_until


def _known(app, email=SEED_USER_EMAIL, password=SEED_USER_PASSWORD):
    """Return a browser that finished a sign-in with no code, then signed out."""
    browser = app.test_client()
    assert _signed_in(_password(browser, email, password))
    browser.post("/logout")
    return browser


def _code(secret):
    """Return the code valid now for *secret*."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _second_code(secret):
    """Return a code for *secret* one step past now's, for a second sign-in.

    A code's step must be later than the last one accepted, so a second code
    typed within the same thirty seconds is refused as a replay.  The next
    step's code is inside the verifier's one-step drift window and later than
    any step accepted before it.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + _TOTP_STEP_SECONDS)


def _known_with_a_code(app, secret, trust):
    """Return a browser that finished a sign-in with its code, then signed out.

    Args:
        app: The application.
        secret: The account's two-factor secret.
        trust: Whether to tick "Trust this device".
    """
    browser = app.test_client()
    assert _asked_the_code(_password(browser))
    form = {"totp_code": _code(secret)}
    if trust:
        form["trust_device"] = "1"
    assert _signed_in(browser.post("/mfa/verify", data=form))
    browser.post("/logout")
    return browser


def _device_count(user_id, token):
    """Return the wrong passwords counted on the record *token* names."""
    db.session.expire_all()
    return known_device_service.find_browser(user_id, token).device.failed_login_count


def _deleted_devices(caplog):
    """Return ``(user_id, device_id)`` of each ``known_device_deleted`` event."""
    return [
        (record.user_id, record.device_id) for record in caplog.records
        if getattr(record, "event", None) == EVT_KNOWN_DEVICE_DELETED
    ]


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
            user_id, None, "", now=datetime.now(timezone.utc), trust=True,
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
        assert known_device_service.find_browser(
            user_id, _token(browser, user_id),
        ) is not None


class TestPastTheStrangersLock:
    """Strangers lock the account; a browser that signed in before still signs in.

    Plan#2772's done-when.  The lock is put on by ten wrong passwords at the
    real form, each from a new browser, as a stranger who knows the email would.
    """

    def test_a_known_browser_signs_in_and_the_lock_stays(self, app, seed_user):
        """No two-factor: signed in, and the strangers' count and lock as they were."""
        user_id = seed_user["user"].id
        browser = _known(app)
        lock_before = _strangers_lock(app, user_id)

        assert _signed_in(_password(browser))

        assert _lock_state(user_id) == lock_before

    def test_a_trusted_browser_signs_in_with_two_factor(self, app, seed_user):
        """A browser trusted with a code skips it, locked or not, and the lock stays."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=True)
        lock_before = _strangers_lock(app, user_id)

        assert _signed_in(_password(browser))

        assert _lock_state(user_id) == lock_before

    def test_a_known_browser_owing_its_code_is_asked_for_it(self, app, seed_user):
        """Known but not trusted: the password gets past the lock to the code form."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=False)
        lock_before = _strangers_lock(app, user_id)

        assert _asked_the_code(_password(browser))

        assert _lock_state(user_id) == lock_before

    def test_a_stranger_and_a_new_browser_stay_refused(self, app, seed_user):
        """A stranger's guess and the right password on a new browser: both refused.

        Neither moves the count while the lock holds, and neither earns a
        cookie or a row: the one row is the known browser's.
        """
        user_id = seed_user["user"].id
        _known(app)
        lock_before = _strangers_lock(app, user_id)

        guess = _password(app.test_client(), password=_WRONG_PASSWORD)
        new_browser = _password(app.test_client())

        for response in (guess, new_browser):
            assert response.status_code == 200
            assert _REFUSED in response.data
            assert _cookie_header(response, user_id) is None
        assert _lock_state(user_id) == lock_before
        assert len(_rows_of(user_id)) == 1

    def test_a_cookie_alone_signs_nobody_in(self, app, seed_user):
        """The known browser with a wrong password is refused, and it counts there."""
        user_id = seed_user["user"].id
        browser = _known(app)
        lock_before = _strangers_lock(app, user_id)

        response = _password(browser, password=_WRONG_PASSWORD)

        assert response.status_code == 200
        assert _REFUSED in response.data
        assert _device_count(user_id, _token(browser, user_id)) == 1
        assert _lock_state(user_id) == lock_before

    def test_another_users_cookie_is_a_strangers(
        self, app, seed_user, seed_second_user,
    ):
        """The second user's token, sent under the first user's cookie name: refused.

        The record is the second user's, so it is worth nothing for the first.
        """
        user_id = seed_user["user"].id
        browser = _known(app, _SECOND_EMAIL, _SECOND_PASSWORD)
        browser.set_cookie(
            f"shekel_device_{user_id}",
            _token(browser, seed_second_user["user"].id),
        )
        lock_before = _strangers_lock(app, user_id)

        response = _password(browser)

        assert _REFUSED in response.data
        assert _lock_state(user_id) == lock_before

    def test_a_cookie_thirty_days_old_is_a_strangers(self, app, seed_user):
        """Thirty days and a minute since its last sign-in: refused like a stranger."""
        user_id = seed_user["user"].id
        browser = _known(app)
        _age_the_last_sign_in(user_id, "30 days 1 minute")
        _strangers_lock(app, user_id)

        response = _password(browser)

        assert _REFUSED in response.data

    def test_a_known_browsers_sign_in_leaves_the_strangers_count(
        self, app, seed_user,
    ):
        """Four strangers' guesses, then the known browser signs in: still four.

        So no sign-in refills a stranger's guesses.
        """
        user_id = seed_user["user"].id
        browser = _known(app)
        _strangers_guess(app, 4)

        assert _signed_in(_password(browser))

        assert _lock_state(user_id) == (4, None)

    def test_a_known_browsers_code_leaves_the_strangers_count(
        self, app, seed_user,
    ):
        """Four strangers' guesses, then the known browser types its code: still four."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=False)
        _strangers_guess(app, 4)
        assert _asked_the_code(_password(browser))

        response = browser.post(
            "/mfa/verify", data={"totp_code": _second_code(secret)},
        )

        assert _signed_in(response)
        assert _lock_state(user_id) == (4, None)


class TestTenWrongMakeAStranger:
    """A known browser's wrong passwords are its own, and ten in a row delete it."""

    def test_ten_wrong_leave_another_browser_signing_in(
        self, app, seed_user, caplog,
    ):
        """Ten on one browser delete its row, with the event; the other browser signs in.

        Nine leave the row; the tenth deletes it.  The account's count never
        moves, and the guessing browser's next wrong password is a stranger's,
        so it counts on the account.
        """
        user_id = seed_user["user"].id
        guessing = _known(app)
        other = _known(app)
        token = _token(guessing, user_id)
        guessing_id = known_device_service.find_browser(user_id, token).device.id

        with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
            for _ in range(_THRESHOLD - 1):
                assert _REFUSED in _password(guessing, password=_WRONG_PASSWORD).data
            assert _device_count(user_id, token) == _THRESHOLD - 1
            assert _deleted_devices(caplog) == []

            assert _REFUSED in _password(guessing, password=_WRONG_PASSWORD).data

        assert _deleted_devices(caplog) == [(user_id, guessing_id)]
        assert known_device_service.find_browser(user_id, token) is None
        assert guessing_id not in [row.id for row in _rows_of(user_id)]
        assert _lock_state(user_id) == (0, None)
        assert _signed_in(_password(other))

        _password(guessing, password=_WRONG_PASSWORD)
        assert _lock_state(user_id) == (1, None)

    def test_a_password_still_owing_its_code_does_not_break_the_row(
        self, app, seed_user,
    ):
        """Nine wrong, the right one with the code still owed, then one more: deleted.

        Only a finished sign-in clears a browser's count, as only one clears
        the account's (ruling plan#3007).
        """
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=False)
        token = _token(browser, user_id)
        for _ in range(_THRESHOLD - 1):
            _password(browser, password=_WRONG_PASSWORD)

        assert _asked_the_code(_password(browser))
        assert _device_count(user_id, token) == _THRESHOLD - 1
        _password(browser, password=_WRONG_PASSWORD)

        assert known_device_service.find_browser(user_id, token) is None

    def test_a_finished_sign_in_breaks_the_row(self, app, seed_user):
        """Nine wrong, a sign-in, nine more: the browser is still known, at nine."""
        user_id = seed_user["user"].id
        browser = _known(app)
        for _ in range(_THRESHOLD - 1):
            _password(browser, password=_WRONG_PASSWORD)
        assert _signed_in(_password(browser))
        browser.post("/logout")

        for _ in range(_THRESHOLD - 1):
            _password(browser, password=_WRONG_PASSWORD)

        assert _device_count(user_id, _token(browser, user_id)) == _THRESHOLD - 1


class TestEveryDoorHasTheOneRule:
    """Wherever a password or code is checked, a known browser passes the strangers' lock.

    Each case locks the account by strangers' wrong passwords at the real form
    while the known browser waits, then has that browser finish the door.
    """

    def test_a_known_browser_finishes_its_code_while_locked(self, app, seed_user):
        """Known but not trusted: the password and then the code both get past."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=False)
        lock_before = _strangers_lock(app, user_id)
        assert _asked_the_code(_password(browser))

        response = browser.post(
            "/mfa/verify", data={"totp_code": _second_code(secret)},
        )

        assert _signed_in(response)
        assert _lock_state(user_id) == lock_before

    def test_a_known_browsers_wrong_code_counts_on_its_record(self, app, seed_user):
        """One wrong code: 1 on the browser's record, 0 on the account's."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=False)
        assert _asked_the_code(_password(browser))

        response = browser.post("/mfa/verify", data={"totp_code": "xxxxxx"})

        assert b"Invalid verification code" in response.data
        assert _device_count(user_id, _token(browser, user_id)) == 1
        assert _lock_state(user_id) == (0, None)

    def test_a_known_browser_re_confirms_while_locked(self, app, seed_user):
        """Signed in, then locked by strangers: ``/reauth`` takes the right password."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        lock_before = _strangers_lock(app, user_id)

        response = browser.post("/reauth", data={"password": SEED_USER_PASSWORD})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/dashboard")
        assert _lock_state(user_id) == lock_before

    def test_a_re_confirm_counts_and_clears_on_the_browser(self, app, seed_user):
        """A wrong password counts 1 on the record; the right one clears it to 0.

        The strangers' four on the account stay four throughout.
        """
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        _strangers_guess(app, 4)
        token = _token(browser, user_id)

        refused = browser.post("/reauth", data={"password": _WRONG_PASSWORD})
        assert b"Invalid password." in refused.data
        assert _device_count(user_id, token) == 1

        assert browser.post(
            "/reauth", data={"password": SEED_USER_PASSWORD},
        ).status_code == 302

        assert _device_count(user_id, _token(browser, user_id)) == 0
        assert _lock_state(user_id) == (4, None)

    def test_a_known_browser_changes_its_password_while_locked(self, app, seed_user):
        """The current password gets past the lock, and the new one is stored."""
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        lock_before = _strangers_lock(app, user_id)
        new_password = "a-brand-new-passphrase"

        browser.post("/change-password", data={
            "current_password": SEED_USER_PASSWORD,
            "new_password": new_password,
            "confirm_password": new_password,
        })

        db.session.expire_all()
        user = db.session.get(User, user_id)
        assert auth_service.verify_password(new_password, user.password_hash)
        assert _lock_state(user_id) == lock_before

    def test_a_known_browser_turns_two_factor_off_while_locked(self, app, seed_user):
        """The password and the code get past the lock, and two-factor is off."""
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = app.test_client()
        assert _asked_the_code(_password(browser))
        assert _signed_in(browser.post("/mfa/verify", data={"totp_code": _code(secret)}))
        _strangers_lock(app, user_id)

        browser.post("/mfa/disable", data={
            "current_password": SEED_USER_PASSWORD,
            "totp_code": _second_code(secret),
        })

        db.session.expire_all()
        assert db.session.query(MfaConfig).filter_by(
            user_id=user_id,
        ).one().is_enabled is False


def _re_confirmed(response):
    """Return whether *response* is a finished re-confirm's redirect."""
    return (
        response.status_code == 302
        and response.headers["Location"].endswith("/dashboard")
    )


def _signed_in_with_a_code(app, secret):
    """Return a browser that finished a sign-in with its code, still signed in."""
    browser = app.test_client()
    assert _asked_the_code(_password(browser))
    assert _signed_in(browser.post("/mfa/verify", data={"totp_code": _code(secret)}))
    return browser


class TestAReConfirmReplacesTheCookie:
    """A finished re-confirm renews a known browser's cookie and nothing else (ruling plan#3070)."""

    def test_the_old_cookie_is_a_strangers_and_the_new_one_known(
        self, app, seed_user,
    ):
        """No two-factor: a new token on the same row, its thirty days unmoved.

        The re-confirm's response carries the cookie, so the browser holds the
        new token; a stolen copy of the old one now names nothing.  Neither the
        record's last sign-in nor the session's sign-in stamp moves: a
        re-confirm is not a sign-in (ruling plan#3069).
        """
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        old_token = _token(browser, user_id)
        (device,) = _rows_of(user_id)
        device_id, signed_in_at = device.id, device.last_signed_in_at

        response = browser.post("/reauth", data={"password": SEED_USER_PASSWORD})

        assert _re_confirmed(response)
        assert _cookie_header(response, user_id) is not None
        new_token = _token(browser, user_id)
        assert new_token != old_token
        assert known_device_service.find_browser(user_id, old_token) is None
        assert known_device_service.find_browser(
            user_id, new_token,
        ).device.id == device_id
        (device,) = _rows_of(user_id)
        assert device.last_signed_in_at == signed_in_at
        with browser.session_transaction() as sess:
            assert datetime.fromisoformat(sess[SESSION_SIGNED_IN_AT_KEY]) == signed_in_at

    def test_a_code_re_confirm_keeps_the_row_its_days_and_its_trust(
        self, app, seed_user,
    ):
        """Two-factor, a trusted browser: the password and code renew the token alone.

        The renewal reads the record after the code's commit.  The row, its
        last sign-in and its trust are as they were, and the new token is
        still trusted.
        """
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _known_with_a_code(app, secret, trust=True)
        assert _signed_in(_password(browser))
        old_token = _token(browser, user_id)
        (device,) = _rows_of(user_id)
        before = (device.id, device.last_signed_in_at, device.trusted_at)

        response = browser.post("/reauth", data={
            "password": SEED_USER_PASSWORD, "totp_code": _second_code(secret),
        })

        assert _re_confirmed(response)
        new_token = _token(browser, user_id)
        assert new_token != old_token
        assert known_device_service.find_browser(user_id, old_token) is None
        assert known_device_service.find_browser(user_id, new_token).trusted is True
        (device,) = _rows_of(user_id)
        assert (device.id, device.last_signed_in_at, device.trusted_at) == before

    def test_a_strangers_re_confirm_gets_no_cookie(self, app, seed_user):
        """A signed-in browser whose record has gone: no cookie, no row, the account cleared.

        Four strangers' guesses are on the account; the stranger's finished
        re-confirm clears them, as every finished check of a stranger's does
        (ruling plan#3007).
        """
        user_id = seed_user["user"].id
        browser = app.test_client()
        assert _signed_in(_password(browser))
        db.session.query(KnownDevice).filter_by(user_id=user_id).delete()
        db.session.commit()
        _strangers_guess(app, 4)

        response = browser.post("/reauth", data={"password": SEED_USER_PASSWORD})

        assert _re_confirmed(response)
        assert _cookie_header(response, user_id) is None
        assert _rows_of(user_id) == []
        assert _lock_state(user_id) == (0, None)

    def test_a_record_deleted_after_the_codes_commit_leaves_it_finishing(
        self, app, seed_user, monkeypatch,
    ):
        """The gap forced: the right password and code still finish, with no 500.

        The accepted code's commit ends the transaction that read the record
        and releases the owner's lock; here a copy of the cookie's tenth wrong
        guess deletes the record in that gap, on a connection of its own as
        another request's would.  The re-confirm finishes, gives the browser
        no cookie (there is no record to renew), and leaves the strangers' four
        on the account: their count is not cleared in the record's place.
        """
        user_id = seed_user["user"].id
        secret = enable_mfa(db.session, user_id)
        browser = _signed_in_with_a_code(app, secret)
        _strangers_guess(app, 4)
        real_check = auth_route_helpers._verify_totp_with_replay_logging
        accepted = []

        def _accept_then_lose_the_record(mfa_config, code, checked_user_id):
            """Check the code as the route does, then delete the record after its commit."""
            accepted.append(real_check(mfa_config, code, checked_user_id))
            with db.engine.begin() as connection:
                connection.execute(
                    sqlalchemy.delete(KnownDevice)
                    .where(KnownDevice.user_id == checked_user_id),
                )
            return accepted[-1]

        monkeypatch.setattr(
            auth_route_helpers, "_verify_totp_with_replay_logging",
            _accept_then_lose_the_record,
        )

        response = browser.post("/reauth", data={
            "password": SEED_USER_PASSWORD, "totp_code": _second_code(secret),
        })

        assert accepted == [True]
        assert _re_confirmed(response)
        assert _cookie_header(response, user_id) is None
        assert _rows_of(user_id) == []
        assert _lock_state(user_id) == (4, None)
