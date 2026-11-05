"""
Shekel Budget App -- Adversarial Tests: no remember-me cookie signs anyone in

Finding S-02 of the 2026-10-29 security review, fixed by plan#2173 (ruling
plan#2406).  A "Remember me" cookie is Flask-Login's
``encode_cookie(str(user.id))`` -- ``"<id>|HMAC(SECRET_KEY, '<id>')"``, the
SAME value for every remembered sign-in of a user, with no server-side
expiry -- and Flask-Login restores a session from one on any request that
carries it, whether or not the app ever issues one again.  Whenever the
loader refuses the session's own user, or the session names none, the restore
writes the cookie's user id into the CURRENT session and calls ``load_user``
again.  On an empty session the ``session_invalidated_at`` check, which ran
only when ``_session_created_at`` was present, never ran: measured on the real
app on 2026-10-29, a client sending only a copied cookie stayed signed in
after "log out all sessions", three requests of three.

Deleting the checkbox could not reach a cookie already copied, so
``load_user`` now refuses any session no sign-in of the loaded user stamped
(``app.utils.session_helpers.sign_in_created_at``) and reads a session without
``_session_last_activity_at`` as stale.  The user stamp is the half a first
fix lacked: with the timestamps alone, a copied cookie riding ANOTHER user's
refused session (signed out elsewhere, or deactivated) borrowed that user's
timestamps and was signed in as the cookie's user (the 2026-10-31 review,
measured three of three).  These tests drive the real app: a cookie minted
exactly as Flask-Login minted every remember cookie is refused alone, before
and after a sign-out of all sessions, and on another user's refused session;
and a signed-in session that loses a stamp is signed out.
"""

from flask_login.config import COOKIE_NAME
from flask_login.utils import decode_cookie, encode_cookie

from app.utils.session_helpers import (
    SESSION_CREATED_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_USER_ID_KEY,
)
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_SECOND_EMAIL = "second@shekel.local"
_SECOND_PASSWORD = "secondpass12"


def _minted_cookie(app, user_id):
    """Return a remember-me cookie value for *user_id*, as Flask-Login minted one.

    The minted value is checked to be one Flask-Login accepts, so a refusal
    below is the loader's and not a malformed cookie's.

    Args:
        app: The Flask application under test.
        user_id: The user whose remembered sign-in the cookie stands for.

    Returns:
        The cookie's value.
    """
    with app.test_request_context():
        token = encode_cookie(str(user_id))
        assert decode_cookie(token) == str(user_id), (
            "Setup failed: the minted cookie is not one Flask-Login accepts"
        )
    return token


def _remembered_client(app, user_id):
    """Return a fresh client carrying only a remember-me cookie for *user_id*.

    Args:
        app: The Flask application under test.
        user_id: The user whose remembered sign-in the cookie stands for.

    Returns:
        A test client with no session and the one remember-me cookie.
    """
    remembered = app.test_client()
    remembered.set_cookie(COOKIE_NAME, _minted_cookie(app, user_id))
    return remembered


def _signed_in_client(app, email, password):
    """Return a fresh client signed in through the real form (no two-factor)."""
    browser = app.test_client()
    response = browser.post("/login", data={
        "email": email, "password": password,
    })
    assert response.status_code == 302, (
        f"Setup failed: sign-in as {email} returned {response.status_code}"
    )
    assert browser.get("/dashboard").status_code == 200, (
        f"Setup failed: {email} is not signed in"
    )
    return browser


def _assert_signed_out(response):
    """Assert *response* is the login gate's bounce to ``/login``.

    Args:
        response: The response to a GET of a signed-in page.
    """
    assert response.status_code == 302, (
        f"Expected the login gate's 302; got {response.status_code}"
    )
    assert "/login" in response.headers["Location"], (
        f"Expected a bounce to /login; got {response.headers['Location']!r}"
    )


class TestARememberCookieSignsNobodyIn:
    """A remember-me cookie alone never reaches a signed-in page."""

    def test_refused_with_no_sign_out_on_record(self, app, seed_user):
        """With ``session_invalidated_at`` never set, the cookie is refused.

        The case the old loader had no answer for at all: nothing to compare
        against, and no ``_session_created_at`` to compare.  Repeated, because
        an attacker who discards the response's session cookie arrives with an
        empty session every time.
        """
        assert seed_user["user"].session_invalidated_at is None
        for _ in range(3):
            remembered = _remembered_client(app, seed_user["user"].id)
            _assert_signed_out(remembered.get("/dashboard"))

    def test_refused_after_log_out_of_all_sessions(
        self, app, auth_client, seed_user,
    ):
        """After "log out all other sessions", the cookie is still refused.

        The sign-out runs through the real route from a signed-in client, so
        ``session_invalidated_at`` is set exactly as the owner's click sets
        it; the case the review measured signing in three requests of three.
        """
        response = auth_client.post("/invalidate-sessions")
        assert response.status_code == 302
        assert seed_user["user"].session_invalidated_at is not None, (
            "Setup failed: the sign-out did not stamp session_invalidated_at"
        )
        for _ in range(3):
            remembered = _remembered_client(app, seed_user["user"].id)
            _assert_signed_out(remembered.get("/dashboard"))

    def test_the_signing_in_client_is_unaffected(self, app, auth_client):
        """Control: a session the app's sign-in started still reads pages.

        Without it, a loader that refused EVERY session would pass both tests
        above while signing everybody out.
        """
        assert auth_client.get("/dashboard").status_code == 200


class TestACookieOnAnotherUsersRefusedSessionSignsNobodyIn:
    """Another user's refused session lends the cookie's user nothing.

    Each case is a session the loader refuses for its own user, sent with the
    first user's cookie: the restore then asks the loader for the cookie's
    user on that same session.  No case needs a control that the session is
    refused for its own user: were it not, the page would answer 200 as that
    user and the signed-out assertion would fail.
    """

    def test_another_users_session_signed_out_elsewhere(
        self, app, seed_user, seed_second_user,
    ):
        """B is signed out from another browser; A's cookie on B's session is refused."""
        browser = _signed_in_client(app, _SECOND_EMAIL, _SECOND_PASSWORD)
        elsewhere = _signed_in_client(app, _SECOND_EMAIL, _SECOND_PASSWORD)
        assert elsewhere.post("/invalidate-sessions").status_code == 302

        browser.set_cookie(COOKIE_NAME, _minted_cookie(app, seed_user["user"].id))

        for _ in range(3):
            _assert_signed_out(browser.get("/dashboard"))

    def test_after_the_cookies_user_signed_out_everywhere(
        self, app, auth_client, seed_user, seed_second_user,
    ):
        """A signs out of every session, then B signs in: still refused.

        The order the review measured: A's ``session_invalidated_at`` is OLDER
        than B's ``_session_created_at``, so the timestamp comparison passes
        for A on B's session, and only the user stamp refuses it.
        """
        assert auth_client.post("/invalidate-sessions").status_code == 302
        assert seed_user["user"].session_invalidated_at is not None
        browser = _signed_in_client(app, _SECOND_EMAIL, _SECOND_PASSWORD)
        elsewhere = _signed_in_client(app, _SECOND_EMAIL, _SECOND_PASSWORD)
        assert elsewhere.post("/invalidate-sessions").status_code == 302

        browser.set_cookie(COOKIE_NAME, _minted_cookie(app, seed_user["user"].id))

        for _ in range(3):
            _assert_signed_out(browser.get("/dashboard"))

    def test_a_deactivated_companions_session(
        self, app, auth_client, seed_user, seed_companion, companion_client,
    ):
        """The owner deactivates the companion; the owner's cookie on her session is refused."""
        companion_id = seed_companion["user"].id
        response = auth_client.post(
            f"/settings/companions/{companion_id}/deactivate",
        )
        assert response.status_code == 302
        assert seed_companion["user"].is_active is False, (
            "Setup failed: the companion was not deactivated"
        )

        companion_client.set_cookie(
            COOKIE_NAME, _minted_cookie(app, seed_user["user"].id),
        )

        for _ in range(3):
            _assert_signed_out(companion_client.get("/dashboard"))

    def test_the_sign_in_stamps_its_user(self, app, seed_user):
        """The real sign-in writes the user stamp, as the id's string."""
        browser = _signed_in_client(app, SEED_USER_EMAIL, SEED_USER_PASSWORD)

        with browser.session_transaction() as sess:
            assert sess[SESSION_USER_ID_KEY] == str(seed_user["user"].id)


class TestASessionMissingAStampIsSignedOut:
    """A signed-in session that loses either stamp is no longer signed in."""

    def test_no_session_created_at(self, auth_client):
        """Without ``_session_created_at`` the session is refused."""
        assert auth_client.get("/dashboard").status_code == 200
        with auth_client.session_transaction() as sess:
            del sess[SESSION_CREATED_AT_KEY]
        _assert_signed_out(auth_client.get("/dashboard"))

    def test_no_session_last_activity_at(self, auth_client):
        """Without ``_session_last_activity_at`` the session is stale."""
        assert auth_client.get("/dashboard").status_code == 200
        with auth_client.session_transaction() as sess:
            del sess[SESSION_LAST_ACTIVITY_KEY]
        _assert_signed_out(auth_client.get("/dashboard"))

    def test_no_user_stamp(self, auth_client):
        """Without ``_session_user_id`` the session is refused.

        The shape of every session signed in before the stamp existed.
        """
        assert auth_client.get("/dashboard").status_code == 200
        with auth_client.session_transaction() as sess:
            del sess[SESSION_USER_ID_KEY]
        _assert_signed_out(auth_client.get("/dashboard"))
