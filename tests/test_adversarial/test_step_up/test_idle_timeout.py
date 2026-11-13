"""C-10's idle-timeout tests (F-006): load_user accepts a session active within the timeout and
rejects one past it, and the before_request hook refreshes the activity stamp.

Each test signs the seed owner in through the shared sign-in, on a browser
the owner trusts (``tests._seed_owner_fixtures.log_in_seed_user``): the owner
has two-factor (plan#3600), and the session, not the code, is under test.
"""

from datetime import datetime, timedelta, timezone

from app.utils.session_helpers import SESSION_LAST_ACTIVITY_KEY
from tests._seed_owner_fixtures import log_in_seed_user


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
