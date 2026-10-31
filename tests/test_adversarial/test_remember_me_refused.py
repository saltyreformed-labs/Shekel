"""
Shekel Budget App -- Adversarial Tests: no remember-me cookie signs anyone in

Finding S-02 of the 2026-10-29 security review, fixed by plan#2173 (ruling
plan#2406).  A "Remember me" cookie is Flask-Login's
``encode_cookie(str(user.id))`` -- ``"<id>|HMAC(SECRET_KEY, '<id>')"``, the
SAME value for every remembered sign-in of a user, with no server-side
expiry -- and Flask-Login restores a session from one on any request that
carries it, whether or not the app ever issues one again.  The restore calls
``load_user`` on an EMPTY session, so the ``session_invalidated_at`` check,
which ran only when ``_session_created_at`` was present, never ran: measured
on the real app on 2026-10-29, a client sending only a copied cookie stayed
signed in after "log out all sessions", three requests of three.

Deleting the checkbox could not reach a cookie already copied, so
``load_user`` now refuses any session the app's own sign-in did not start
(no ``_session_created_at``) and reads a session without
``_session_last_activity_at`` as stale.  These tests drive the real app: a
cookie minted exactly as Flask-Login minted every remember cookie is refused
before and after a sign-out of all sessions, and a signed-in session that
loses either stamp is signed out.
"""

from flask_login.config import COOKIE_NAME
from flask_login.utils import decode_cookie, encode_cookie

from app.utils.session_helpers import (
    SESSION_CREATED_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
)


def _remembered_client(app, user_id):
    """Return a fresh client carrying only a remember-me cookie for *user_id*.

    The cookie is minted the way Flask-Login minted every remember cookie this
    app ever issued, and the minted value is checked to be one Flask-Login
    accepts, so a refusal below is the loader's and not a malformed cookie's.

    Args:
        app: The Flask application under test.
        user_id: The user whose remembered sign-in the cookie stands for.

    Returns:
        A test client with no session and the one remember-me cookie.
    """
    with app.test_request_context():
        token = encode_cookie(str(user_id))
        assert decode_cookie(token) == str(user_id), (
            "Setup failed: the minted cookie is not one Flask-Login accepts"
        )
    remembered = app.test_client()
    remembered.set_cookie(COOKIE_NAME, token)
    return remembered


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
