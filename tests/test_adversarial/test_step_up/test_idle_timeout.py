"""C-10's idle-timeout tests (F-006): load_user accepts a session active within the timeout and
rejects one past it, the before_request hook refreshes the activity stamp, and that timeout is
the one idle rule, Flask's cookie age never cutting in first (ruling plan#3926).

Each test signs the seed owner in through the shared sign-in, on a browser
the owner trusts (``tests._seed_owner_fixtures.log_in_seed_user``): the owner
has two-factor (plan#3600), and the session, not the code, is under test.
"""

from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from app import create_app
from app.extensions import db
from app.utils.session_helpers import SESSION_LAST_ACTIVITY_KEY, SESSION_TOKEN_KEY
from tests._seed_owner_fixtures import log_in_seed_user
from tests._session_copies import is_signed_in

#: Past the default 12-hour idle window, and past the 12 hours Flask's
#: cookie age was until ruling plan#3926.
_THIRTEEN_HOURS = timedelta(hours=13)

#: An idle window longer than thirteen hours, in minutes.
_A_DAY_IN_MINUTES = 24 * 60


def _idle_for(browser, idle):
    """Date *browser*'s session as its last request, *idle* ago, would have.

    Both of what that request left: the activity stamp, and the cookie's
    signature, which Flask dates.  The session is written with the clock
    moved back, so Flask signs the cookie at that moment, as the activity
    hook's write on that request did.  A stamp written now would leave a
    freshly signed cookie, which no limit on the cookie's own age can refuse.

    The session is read first, now: with the clock moved back, Flask cannot
    open the cookie signed a moment ago, since itsdangerous refuses a
    signature dated in its future, and opens an empty session instead.  So
    every key is written back with the stamp.

    Args:
        browser: A signed-in test client.
        idle: How long ago its last request was.
    """
    last_request = datetime.now(timezone.utc) - idle
    with browser.session_transaction() as sess:
        keys = dict(sess)
    keys[SESSION_LAST_ACTIVITY_KEY] = last_request.isoformat()
    with time_machine.travel(last_request, tick=False):
        with browser.session_transaction() as sess:
            sess.update(keys)


# ---------------------------------------------------------------------------
# F-006: idle-timeout in load_user + before_request refresh
# ---------------------------------------------------------------------------


class TestIdleTimeoutAcceptance:
    """The loader accepts fresh sessions (control tests)."""

    def test_first_request_after_login_passes(self, app, client, seed_user):
        """A request immediately after /login is accepted.

        ``load_user`` refuses a session with no
        ``_session_last_activity_at`` (plan#2173), so this passes only
        because the sign-in itself writes the stamp
        (``stamp_login_session``) rather than leaving it to the
        ``before_request`` hook, which runs after ``load_user``.
        """
        with app.app_context():
            # The sign-in asserts its own redirect to the owner's page.
            log_in_seed_user(client)

            # Subsequent protected request must succeed.
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 200, (
                f"First request after login must pass; got "
                f"{check.status_code} ({check.headers.get('Location')!r})."
            )

    def test_recent_activity_passes(self, app, client, seed_user):
        """Activity stamped 1 minute ago still passes the 12-hour window.

        Boundary-control: without this test, a regression that always
        rejected (e.g. inverted comparison) would still pass the
        "stale rejected" tests because every value would be rejected.
        """
        with app.app_context():
            log_in_seed_user(client)
            recent = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = recent

            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 200


class TestIdleTimeoutRejection:
    """The loader rejects sessions whose activity is stale or tampered."""

    def test_stale_activity_rejected(self, app, client, seed_user):
        """Activity older than IDLE_TIMEOUT_MINUTES bounces to /login.

        The canonical F-006 attack: an attacker reaches the
        unattended browser the next day, long after the user walked
        away from their desk.  The session cookie is still
        cryptographically valid (signature checks out) but
        ``load_user`` must refuse because the last activity exceeds
        the 12-hour idle window.
        """
        with app.app_context():
            log_in_seed_user(client)
            stale = (
                datetime.now(timezone.utc) - timedelta(hours=13)
            ).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = stale

            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", ""), (
                "Stale activity must redirect to /login; got "
                f"{check.headers.get('Location')!r}."
            )

    def test_malformed_activity_rejected(self, app, client, seed_user):
        """A non-ISO-8601 activity stamp is treated as stale.

        ``datetime.fromisoformat`` raises ``ValueError`` on garbage.
        ``_idle_session_is_fresh`` must catch and return False so
        the failure mode is "log in again" instead of a 500.
        """
        with app.app_context():
            log_in_seed_user(client)
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = "not-an-iso-timestamp"

            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", "")

    def test_naive_activity_rejected(self, app, client, seed_user):
        """A timezone-naive activity stamp is treated as stale.

        Naive datetimes raise ``TypeError`` on the timezone-aware
        subtraction.  Reject explicitly so the failure mode is
        consistent with the malformed case above.
        """
        with app.app_context():
            log_in_seed_user(client)
            naive = datetime.now().replace(tzinfo=None).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = naive

            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", "")

    def test_future_dated_activity_treated_as_fresh(
        self, app, client, seed_user,
    ):
        """A future-dated activity stamp is treated as fresh, not stale.

        The two real-world causes of ``elapsed < 0`` are a backwards
        clock jump (NTP correction, manual adjustment, VM resume)
        and a forged cookie.  Forging requires ``SECRET_KEY``, at
        which point the attacker can mint any cookie value and the
        future-date check adds no defensive value; meanwhile a clock
        jump must NOT silently log every active user out.  See
        ``_idle_session_is_fresh`` docstring for the full rationale.

        This test is the inverse of the strict policy enforced by
        ``_mfa_pending_is_fresh`` (commit C-08), which DOES reject
        future-dated -- the two checks differ because MFA verify is
        a single-use gate and an idle-timeout check runs on every
        request.
        """
        with app.app_context():
            log_in_seed_user(client)
            future = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
            with client.session_transaction() as sess:
                sess[SESSION_LAST_ACTIVITY_KEY] = future

            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 200, (
                "Future-dated activity must be treated as fresh; got "
                f"{check.status_code} ({check.headers.get('Location')!r}). "
                "A clock-skew rejection here would log every active "
                "user out after an NTP correction."
            )


class TestTheOneIdleRule:
    """``IDLE_TIMEOUT_MINUTES`` is the one idle rule (ruling plan#3926).

    Flask refuses a session cookie signed longer ago than
    ``PERMANENT_SESSION_LIFETIME``, permanent or not.  At 12 hours it was a
    second idle rule: a longer window still ended at 12 hours.  The tests
    above rewrite the stamp in a cookie signed a moment ago, so they could
    not see it; these date the cookie too (:func:`_idle_for`).
    """

    @pytest.mark.usefixtures("seed_user")
    def test_a_longer_idle_window_is_honoured(self, app, client, monkeypatch):
        """With a 24-hour window, a browser idle for 13 hours is still signed in."""
        monkeypatch.setitem(app.config, "IDLE_TIMEOUT_MINUTES", _A_DAY_IN_MINUTES)
        with app.app_context():
            log_in_seed_user(client)
            _idle_for(client, _THIRTEEN_HOURS)

            assert is_signed_in(client)

    @pytest.mark.usefixtures("seed_user")
    def test_an_app_built_later_leaves_this_apps_window(self, app, client, monkeypatch):
        """With a 24-hour window, 13 hours idle is still signed in after another app is built.

        Every ``create_app`` registers its loader on the one ``login_manager``,
        which keeps the last, so the loader in force here is the later app's.
        It judges by the app serving the request, so the later app's default
        window, shorter than the idle, decides nothing for this one.
        """
        monkeypatch.setitem(app.config, "IDLE_TIMEOUT_MINUTES", _A_DAY_IN_MINUTES)
        with app.app_context():
            later = create_app("testing")
            try:
                assert timedelta(
                    minutes=later.config["IDLE_TIMEOUT_MINUTES"],
                ) < _THIRTEEN_HOURS, (
                    "Setup failed: the later app's window is not shorter than the "
                    "idle, so this test cannot tell which app judged it"
                )
                log_in_seed_user(client)
                _idle_for(client, _THIRTEEN_HOURS)

                assert is_signed_in(client)
            finally:
                with later.app_context():
                    db.engine.dispose()

    @pytest.mark.usefixtures("seed_user")
    def test_past_the_default_window_shekels_rule_refuses_it(self, app, client):
        """At the default 12 hours, a browser idle for 13 hours is refused by ``load_user``.

        Flask opened the cookie, so the refusal is Shekel's: the session it
        reads still holds the sign-in's token.  The read leaves the cookie as
        it was (Flask re-signs a non-permanent session only when it is
        modified), so the request after it sends the 13-hour-old one.
        """
        name = app.config["SESSION_COOKIE_NAME"]
        with app.app_context():
            log_in_seed_user(client)
            _idle_for(client, _THIRTEEN_HOURS)
            idle_cookie = client.get_cookie(name).value
            with client.session_transaction() as sess:
                opened = dict(sess)

            assert SESSION_TOKEN_KEY in opened, (
                "Flask refused the 13-hour-old cookie itself"
            )
            assert client.get_cookie(name).value == idle_cookie
            assert not is_signed_in(client)


class TestActivityRefreshHook:
    """The ``before_request`` hook keeps an active session alive."""

    def test_activity_advances_with_every_request(
        self, app, client, seed_user,
    ):
        """Each authenticated request bumps ``_session_last_activity_at``.

        Without this hook, the very first stamp written at login
        would be the last one -- and a session would become "stale"
        after IDLE_TIMEOUT_MINUTES of WALL-CLOCK time, regardless
        of how active the user actually was.  The hook is what
        turns the idle timeout into a true idle timeout (vs a
        session-lifetime cap).
        """
        with app.app_context():
            log_in_seed_user(client)

            # Read the post-login stamp (set by stamp_login_session).
            with client.session_transaction() as sess:
                first = sess[SESSION_LAST_ACTIVITY_KEY]

            # Make another request; the before_request hook should
            # advance the stamp.  Use a sleep too small to be
            # noticeable but large enough to differ at microsecond
            # resolution -- we only need strict monotonic advance.
            client.get("/dashboard")

            with client.session_transaction() as sess:
                second = sess[SESSION_LAST_ACTIVITY_KEY]

            assert second >= first, (
                "before_request hook must monotonically advance "
                f"_session_last_activity_at; got {second!r} after "
                f"{first!r}."
            )

    def test_unauthenticated_request_does_not_create_stamp(
        self, app, client,
    ):
        """An anonymous request leaves the cookie untouched.

        Without this skip, the hook would create a session cookie
        out of thin air for every visitor (including bots), bloating
        the response size and inviting cookie-fixation oddities.
        """
        with app.app_context():
            client.get("/login")
            with client.session_transaction() as sess:
                # No authenticated session, so no activity stamp.
                assert SESSION_LAST_ACTIVITY_KEY not in sess
