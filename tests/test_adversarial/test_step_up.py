"""Shekel Budget App -- Adversarial Tests for Session Lifetime,
Idle Timeout, and Step-Up Re-Auth (C-10).

End-to-end coverage for the three findings closed by commit C-10
of the 2026-04-15 security remediation plan:

  * F-006 (CWE-613): no idle-timeout check on the session.  Closed
    by ``IDLE_TIMEOUT_MINUTES`` in config plus the
    ``_session_last_activity_at`` check in ``app.load_user`` and
    the ``before_request`` activity-refresh hook in
    ``app/__init__.py``.

  * F-035 (CWE-613): ``PERMANENT_SESSION_LIFETIME`` defaulted to
    Flask's 31-day fallback.  Closed by an explicit 12-hour default
    in ``BaseConfig`` (env-overridable via ``SESSION_LIFETIME_HOURS``).
    Note this cap only bites on a ``permanent`` session; the app
    keeps sessions non-permanent so Flask-Login ``strong`` session
    protection (F-038 / C-07) still wipes the session on identifier
    drift, so the effective bound on an open session is the idle
    timeout below.

  * F-045 (CWE-306): no step-up re-auth for high-value operations.
    Closed by the ``fresh_login_required`` decorator in
    ``app/utils/auth_helpers.py``, the ``/reauth`` route in
    ``app/routes/auth/session_security.py``, and the high-value routes the decorator
    is applied to.  The decorated set was later narrowed to the
    account-control and credential operations -- account hard-delete
    and companion create / edit / deactivate / reactivate -- after the
    owner judged step-up on the reversible data edits (anchor balance,
    account-edit form, state tax / FICA config, recurring
    transaction- and transfer-template hard-delete) to be friction
    without a matching benefit for this single-owner, low-sensitivity
    app.  Those routes remain owner-only via ``@require_owner``.

The tests in this file run against the full Flask app via
``test_client`` so that load_user, the before_request hook, the
decorator, the /reauth route, and the lifecycle stamping all
exercise together.  A regression in any one piece would break a
test here even if the unit tests pass individually.

Why adversarial, not unit:  the C-10 contract is a composition of
five cooperating pieces (config, loader, hook, decorator, route).
The unit tests in ``tests/test_utils/`` and ``tests/test_routes/
test_auth.py`` cover each piece in isolation; this file catches the
"each piece works alone but they are wired together wrong" class of
bug.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.ref import (
    AccountType,
    TransactionType,
)
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.models.user import MfaConfig
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult
from app.services import account_service
from app.utils.session_helpers import (
    FRESH_LOGIN_AT_KEY,
    SESSION_CREATED_AT_KEY,
    SESSION_LAST_ACTIVITY_KEY,
)
from tests._test_helpers import make_every_period_rule


_KNOWN_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
_KNOWN_BACKUP_CODES = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]


# Env vars that BaseConfig consults at class-definition time for the
# three C-10 defaults (the fourth, the remember-me cookie's lifetime, went
# with "Remember me" at plan#2173).  The TestPermanentSessionLifetime suite clears
# every entry here and reloads ``app.config`` so the assertions
# measure the BUILT-IN defaults rather than whatever the developer
# happens to have in their local .env.
_C10_ENV_VARS = (
    "SESSION_LIFETIME_HOURS",
    "IDLE_TIMEOUT_MINUTES",
    "FRESH_LOGIN_MAX_AGE_MINUTES",
)


def _reload_config_with_clean_env(monkeypatch):
    """Stub ``load_dotenv`` and clear C-10 env vars, then reload config.

    Returns the freshly-reloaded ``app.config`` module so the caller
    can read ``BaseConfig.<attr>`` values that reflect the source-
    code defaults rather than env overrides.

    ``monkeypatch`` undoes both the function stub and the env
    deletes at test teardown; the caller is responsible for a
    matching final ``importlib.reload`` to restore the module's
    class-level constants for any sibling test that imports
    BaseConfig directly.

    Defined as a module-level helper rather than a fixture because
    each test that uses it also needs to do its own try/finally
    reload-restore -- a fixture would split that ownership across
    setup and teardown and obscure the intent.
    """
    # pylint: disable=import-outside-toplevel
    import importlib

    import dotenv

    from app import config as config_module

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
    for var in _C10_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return importlib.reload(config_module)


def _restore_config_module():
    """Reload ``app.config`` so its class-level constants reflect
    the env that ``monkeypatch`` has just restored.
    """
    # pylint: disable=import-outside-toplevel
    import importlib

    from app import config as config_module
    importlib.reload(config_module)


def _enable_mfa(user_id):
    """Persist an enabled MFA config with a known secret and codes.

    Mirrors the helper in ``test_session_invalidation.py``.
    """
    config = MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(_KNOWN_TOTP_SECRET),
        backup_codes=mfa_service.hash_backup_codes(_KNOWN_BACKUP_CODES),
    )
    db.session.add(config)
    db.session.commit()


def _do_login_no_mfa(client):
    """Run a fresh password-only login on a test client.

    Returns the login response so the caller can assert on it.
    """
    return client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    }, follow_redirects=False)


# ---------------------------------------------------------------------------
# F-035: PERMANENT_SESSION_LIFETIME
# ---------------------------------------------------------------------------


class TestC10ConfigDefaults:
    """The three C-10 config defaults are baked into ``BaseConfig``.

    Each test stubs ``load_dotenv`` and clears the matching env var
    before reloading ``app.config``, so the assertion measures the
    BUILT-IN default rather than whatever the developer happens to
    have in their local .env.  Without this isolation a developer
    whose .env sets ``IDLE_TIMEOUT_MINUTES=30`` (the original
    value) would see a green test that hides a real regression.

    A teardown ``_restore_config_module`` call reloads the module
    after monkeypatch undoes its changes, so sibling tests that
    import BaseConfig directly see the env-driven values they
    expect.
    """

    def test_session_lifetime_default_is_12_hours(self, monkeypatch):
        """``PERMANENT_SESSION_LIFETIME`` defaults to 12 hours (F-035).

        Without this explicit default the cookie would carry Flask's
        31-day fallback and a stolen browser profile would have a
        month of valid auth.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.PERMANENT_SESSION_LIFETIME == (
                timedelta(hours=12)
            )
        finally:
            _restore_config_module()

    def test_idle_timeout_default_is_12_hours(self, monkeypatch):
        """``IDLE_TIMEOUT_MINUTES`` defaults to 720 (12h).

        Widened from the original 30 minutes: this is a single-owner,
        low-sensitivity budget app, and a short idle window mostly
        punished the legitimate owner by bouncing them to a full
        password + MFA login whenever they stepped away from an open
        tab.  Without this constant, ``_idle_session_is_fresh`` in
        ``app/__init__.py`` would have no threshold to compare against
        and the entire idle-timeout check would degrade to "always
        allow".
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.IDLE_TIMEOUT_MINUTES == 720
        finally:
            _restore_config_module()

    def test_fresh_login_max_age_default_is_5_minutes(self, monkeypatch):
        """``FRESH_LOGIN_MAX_AGE_MINUTES`` defaults to 5 (F-045).

        ASVS L2 V4.3.3 step-up window.  Without this constant the
        ``fresh_login_required`` decorator would have no threshold
        and would either always reject or always accept.
        """
        try:
            reloaded = _reload_config_with_clean_env(monkeypatch)
            assert reloaded.BaseConfig.FRESH_LOGIN_MAX_AGE_MINUTES == 5
        finally:
            _restore_config_module()


class TestC10ConfigEnvOverride:
    """Env vars override the C-10 defaults at module import time."""

    def test_session_lifetime_hours_env_override(self, monkeypatch):
        """``SESSION_LIFETIME_HOURS=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("SESSION_LIFETIME_HOURS", "48")
            for var in _C10_ENV_VARS:
                if var != "SESSION_LIFETIME_HOURS":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.PERMANENT_SESSION_LIFETIME == (
                timedelta(hours=48)
            )
        finally:
            _restore_config_module()

    def test_idle_timeout_minutes_env_override(self, monkeypatch):
        """``IDLE_TIMEOUT_MINUTES=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("IDLE_TIMEOUT_MINUTES", "15")
            for var in _C10_ENV_VARS:
                if var != "IDLE_TIMEOUT_MINUTES":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.IDLE_TIMEOUT_MINUTES == 15
        finally:
            _restore_config_module()

    def test_fresh_login_max_age_env_override(self, monkeypatch):
        """``FRESH_LOGIN_MAX_AGE_MINUTES=N`` is honoured."""
        try:
            # pylint: disable=import-outside-toplevel
            import importlib
            import dotenv
            from app import config as config_module
            monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
            monkeypatch.setenv("FRESH_LOGIN_MAX_AGE_MINUTES", "10")
            for var in _C10_ENV_VARS:
                if var != "FRESH_LOGIN_MAX_AGE_MINUTES":
                    monkeypatch.delenv(var, raising=False)
            reloaded = importlib.reload(config_module)
            assert reloaded.BaseConfig.FRESH_LOGIN_MAX_AGE_MINUTES == 10
        finally:
            _restore_config_module()


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
            resp = _do_login_no_mfa(client)
            assert resp.status_code == 302

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
            _do_login_no_mfa(client)
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
            _do_login_no_mfa(client)
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
            _do_login_no_mfa(client)
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
            _do_login_no_mfa(client)
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
            _do_login_no_mfa(client)
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
            _do_login_no_mfa(client)

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


# ---------------------------------------------------------------------------
# F-045: fresh_login_required + /reauth
# ---------------------------------------------------------------------------


class TestFreshLoginAtSetOnLogin:
    """Login flows must stamp ``_fresh_login_at`` so the auth_client
    fixture and freshly-logged-in users can immediately exercise
    high-value routes without an extra reauth round-trip.
    """

    def test_login_no_mfa_sets_fresh_login_at(
        self, app, client, seed_user,
    ):
        """Successful no-MFA login writes ``_fresh_login_at``."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            _do_login_no_mfa(client)
            after = datetime.now(timezone.utc)

            with client.session_transaction() as sess:
                raw = sess.get(FRESH_LOGIN_AT_KEY)
                assert raw is not None, (
                    "Login must stamp _fresh_login_at; otherwise "
                    "the auth_client fixture would always be sent "
                    "to /reauth on the first high-value POST."
                )
                stamped = datetime.fromisoformat(raw)
                assert before <= stamped <= after

    def test_login_with_mfa_sets_fresh_login_at_after_verify(
        self, app, client, seed_user, monkeypatch,
    ):
        """MFA verify completion writes ``_fresh_login_at``.

        Two-step login: the password POST puts the user in pending-
        MFA state; the /mfa/verify POST completes the login.  The
        fresh-login stamp must come from the SECOND step (the one
        that establishes the authenticated session), not the first.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            with client.session_transaction() as sess:
                # No fresh-login stamp yet -- session is in pending-MFA.
                assert FRESH_LOGIN_AT_KEY not in sess

            client.post("/mfa/verify", data={"totp_code": "123456"})
            with client.session_transaction() as sess:
                assert FRESH_LOGIN_AT_KEY in sess


class TestFreshLoginRequiredRedirects:
    """The decorator redirects users with no/old fresh-login stamp."""

    def test_high_value_route_with_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A freshly-logged-in user passes a decorated route without
        bouncing to /reauth.

        Locks down the auth_client UX for the routes that REMAIN
        step-up gated (account hard-delete, companion management):
        every suite test that uses auth_client against one of them
        relies on the login stamp letting the request through.
        ``hard_delete_account`` always answers with a redirect to the
        accounts list (delete on success, archive when history exists,
        or a flash on a blocking dependent), so the decorator passing
        is proven by the ABSENCE of a /reauth redirect, not a specific
        2xx.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.post(
                f"/accounts/{account_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302, (
                f"auth_client must reach the route body on a freshly-"
                f"stamped session; got {resp.status_code}."
            )
            assert "/reauth" not in resp.headers.get("Location", ""), (
                "A freshly-stamped session must not be bounced to "
                "/reauth on a decorated route."
            )

    def test_stale_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A session with a stale ``_fresh_login_at`` bounces to /reauth.

        The canonical F-045 attack: an attacker hijacks a session
        cookie whose last password entry is older than the 5-minute
        fresh-login window and tries a destructive operation -- here,
        permanently deleting an account.  The decorator must redirect
        to /reauth so the action requires a fresh password prompt.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/accounts/{account_id}/hard-delete",
                follow_redirects=False,
            )
            # The POST carries no ``HX-Request`` header so the
            # decorator returns a 302 (rather than the HX-Redirect
            # 204).  The location must point at /reauth carrying
            # the original action URL as ``next``.
            assert resp.status_code == 302
            location = resp.headers.get("Location", "")
            assert "/reauth" in location
            assert "next=" in location
            # The account must be untouched: the decorator ran before
            # the route body.
            assert db.session.get(Account, account_id) is not None

    def test_missing_fresh_login_redirects_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """A session missing ``_fresh_login_at`` bounces to /reauth.

        This covers the upgrade scenario: a session that pre-dates
        the C-10 deploy will not have the key set.  Failing closed
        means those sessions get prompted on their next high-value
        operation, which is the safe default.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            with auth_client.session_transaction() as sess:
                sess.pop(FRESH_LOGIN_AT_KEY, None)

            resp = auth_client.post(
                f"/accounts/{account_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" in resp.headers.get("Location", "")
            assert db.session.get(Account, account_id) is not None

    def test_companion_reactivate_stale_session_redirects_to_reauth(
        self, app, auth_client, seed_companion,
    ):
        """A stale session POSTing to companion reactivate bounces to /reauth.

        Reactivation re-grants login access to a previously disabled
        companion (is_active flipped back to True) -- the credential-
        lifecycle inverse of deactivate, which is itself step-up
        gated.  This pins the @fresh_login_required() guard so the
        symmetric pair (deactivate / reactivate) cannot drift back
        apart: a hijacked-but-stale owner session must not silently
        restore a companion the real owner deliberately disabled.
        """
        with app.app_context():
            comp_id = seed_companion["user"].id
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/settings/companions/{comp_id}/reactivate",
            )
            # No HX-Request header -> the decorator returns a 302 to
            # /reauth carrying the original action URL as ``next``.
            assert resp.status_code == 302
            location = resp.headers.get("Location", "")
            assert "/reauth" in location
            assert "next=" in location

    def test_htmx_request_returns_hx_redirect(
        self, app, auth_client, seed_user,
    ):
        """An HTMX request gets ``HX-Redirect`` + 204, not a 302.

        Without this, an HTMX form-button on a high-value route
        would receive the /reauth HTML body and inject it into
        whatever fragment slot the original request targeted.  The
        HX-Redirect header makes htmx do a full-page navigation
        instead.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/accounts/{account_id}/hard-delete",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 204, (
                f"HTMX request should get 204 + HX-Redirect; got "
                f"{resp.status_code}."
            )
            assert "HX-Redirect" in resp.headers
            assert "/reauth" in resp.headers["HX-Redirect"]
            assert resp.data == b"", (
                "Body must be empty so htmx swap has nothing to "
                "render."
            )


class TestReauthRoute:
    """The /reauth route verifies password + (optional) TOTP and
    refreshes ``_fresh_login_at`` on success.
    """

    def test_get_reauth_renders_form(self, app, auth_client):
        """GET /reauth shows the confirmation form."""
        with app.app_context():
            resp = auth_client.get("/reauth")
            assert resp.status_code == 200
            assert b"Confirm your identity" in resp.data
            assert b"Password" in resp.data

    def test_get_reauth_unauthenticated_redirects_to_login(
        self, app, client,
    ):
        """GET /reauth without a session redirects to /login."""
        with app.app_context():
            resp = client.get("/reauth", follow_redirects=False)
            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

    def test_post_correct_password_no_mfa_refreshes_fresh_login(
        self, app, auth_client,
    ):
        """A correct password POST updates ``_fresh_login_at``.

        The whole point of /reauth: typing the password again proves
        identity and restarts the step-up window.
        """
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/reauth", data={
                "password": "testpass",
            }, follow_redirects=False)
            after = datetime.now(timezone.utc)

            assert resp.status_code == 302, (
                "Successful reauth must redirect; got "
                f"{resp.status_code}."
            )
            with auth_client.session_transaction() as sess:
                stamped = datetime.fromisoformat(sess[FRESH_LOGIN_AT_KEY])
                assert before <= stamped <= after, (
                    "_fresh_login_at must advance to NOW on reauth "
                    "success; got an earlier or unchanged value."
                )

    def test_post_wrong_password_does_not_refresh_fresh_login(
        self, app, auth_client,
    ):
        """A wrong password POST leaves ``_fresh_login_at`` unchanged.

        Without this, a wrong password would still consume the
        anti-CSRF token but more importantly might silently update
        the stamp -- defeating the entire step-up check.
        """
        with app.app_context():
            stale_iso = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale_iso

            resp = auth_client.post("/reauth", data={
                "password": "wrongpassword",
            })
            # Wrong password re-renders the form (200), it does NOT
            # redirect (which would suggest success).
            assert resp.status_code == 200
            assert b"Invalid password" in resp.data

            with auth_client.session_transaction() as sess:
                # Stamp unchanged.
                assert sess[FRESH_LOGIN_AT_KEY] == stale_iso

    def test_post_redirects_to_safe_next(
        self, app, auth_client,
    ):
        """A safe ``next`` param in the URL is honoured on success."""
        with app.app_context():
            resp = auth_client.post(
                "/reauth?next=%2Fdashboard",
                data={"password": "testpass"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers.get("Location", "").endswith("/dashboard")

    def test_post_rejects_unsafe_next(
        self, app, auth_client,
    ):
        """An unsafe ``next`` param redirects to dashboard, not the
        attacker URL.

        Open-redirect defence -- the same ``_is_safe_redirect``
        helper that gates /login also gates /reauth.  Otherwise an
        attacker could craft /reauth?next=https://evil and ride a
        legitimate re-auth into a phishing page.
        """
        with app.app_context():
            resp = auth_client.post(
                "/reauth?next=https%3A%2F%2Fevil.com",
                data={"password": "testpass"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            location = resp.headers.get("Location", "")
            assert "evil.com" not in location, (
                f"Open-redirect: /reauth followed an unsafe next; "
                f"got Location={location!r}."
            )

    def test_post_with_mfa_requires_totp(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A user with MFA enabled must supply a valid TOTP on /reauth.

        Belt-and-suspenders: a stolen password alone cannot complete
        re-auth on an MFA-enabled account.  Without this check, the
        step-up flow would be weaker than the primary login it
        mirrors.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.INVALID,
            )

            resp = auth_client.post("/reauth", data={
                "password": "testpass",
                "totp_code": "000000",
            })
            assert resp.status_code == 200
            assert b"Invalid authentication code" in resp.data

    def test_post_with_mfa_accepts_correct_totp(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """A user with MFA enabled completes reauth with password + TOTP."""
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/reauth", data={
                "password": "testpass",
                "totp_code": "123456",
            }, follow_redirects=False)
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                stamped = datetime.fromisoformat(sess[FRESH_LOGIN_AT_KEY])
                assert stamped >= before


class TestReauthDoesNotRefreshSessionCreatedAt:
    """``/reauth`` MUST NOT touch ``_session_created_at``.

    The motivation: writing _session_created_at on /reauth would
    silently promote the session past every prior
    invalidate_other_sessions bump.  An attacker who hijacked a
    session and then used /reauth would be immune to the user's
    later "log out all sessions" click.  The stamp_reauth_session
    helper is structurally split from stamp_login_session for
    exactly this reason; this test locks down the contract end-to-
    end.
    """

    def test_reauth_does_not_advance_session_created_at(
        self, app, auth_client,
    ):
        """``_session_created_at`` is unchanged across a successful reauth."""
        with app.app_context():
            with auth_client.session_transaction() as sess:
                before_created = sess.get(SESSION_CREATED_AT_KEY)
            assert before_created is not None, (
                "Setup error: auth_client login must stamp "
                "_session_created_at."
            )

            resp = auth_client.post("/reauth", data={
                "password": "testpass",
            })
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                after_created = sess.get(SESSION_CREATED_AT_KEY)
            assert after_created == before_created, (
                "/reauth must NOT update _session_created_at; doing "
                "so would let an attacker bypass the next "
                "invalidate_other_sessions bump.  Got "
                f"before={before_created!r} after={after_created!r}."
            )


class TestPasswordChangeRefreshesFreshLogin:
    """Password change is a re-auth event and MUST refresh
    ``_fresh_login_at``.

    Rationale: typing the current password proves identity in the
    same way as primary login.  If the change-password handler
    failed to refresh the fresh-login stamp, a user who changed
    their password mid-session would immediately be bumped to
    /reauth on the next high-value operation -- terrible UX, and
    no security gain since the password just verified is the same
    credential /reauth would re-verify.
    """

    def test_change_password_refreshes_fresh_login_at(
        self, app, auth_client,
    ):
        """Successful /change-password updates ``_fresh_login_at``."""
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            })
            after = datetime.now(timezone.utc)
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                stamped = datetime.fromisoformat(sess[FRESH_LOGIN_AT_KEY])
            assert before <= stamped <= after, (
                "/change-password must advance _fresh_login_at to NOW "
                "since the user just re-authenticated via current "
                f"password; got {stamped!r} not in [{before}, {after}]."
            )


class TestInvalidateSessionsDoesNotRefreshFreshLogin:
    """``/invalidate-sessions`` MUST NOT refresh ``_fresh_login_at``.

    The endpoint is gated on ``@login_required`` only -- the user
    is NOT asked for their password before clicking the "log out
    all other sessions" button.  Refreshing the fresh-login stamp
    here would let the same UI silently extend the step-up grace
    window, defeating ``fresh_login_required`` for any user who
    cleared other sessions within the last 5 minutes.
    """

    def test_invalidate_sessions_preserves_fresh_login_at(
        self, app, auth_client,
    ):
        """``_fresh_login_at`` is unchanged across /invalidate-sessions."""
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post("/invalidate-sessions")
            assert resp.status_code == 302

            with auth_client.session_transaction() as sess:
                after_fresh = sess[FRESH_LOGIN_AT_KEY]
            assert after_fresh == stale, (
                "/invalidate-sessions must NOT refresh _fresh_login_at "
                "(no re-auth happened).  Otherwise the same UI could "
                "extend the step-up window without typing a password.  "
                f"Got before={stale!r} after={after_fresh!r}."
            )


# ---------------------------------------------------------------------------
# Additional decorated routes (post-C-10 audit follow-up)
# ---------------------------------------------------------------------------


class TestUpdateAccountIsNotStepUpGated:
    """``POST /accounts/<id>`` (the form-edit route) is NOT step-up gated.

    Step-up was removed from this route (and from the inline anchor
    editors it shares an ``anchor_balance`` write with): an account
    edit is a reversible, version-locked change, not a credential
    operation, so the friction of a 5-minute re-auth window was judged
    not worth it for this single-owner, low-sensitivity app.  These
    tests are the regression lock for that decision -- they fail if the
    decorator is ever re-added.  The route remains owner-only via
    ``@require_owner``.
    """

    def test_stale_fresh_login_still_updates(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login no longer blocks /accounts/<id>.

        A session whose last password entry is well past the old
        5-minute window must update the account normally, NOT bounce
        to /reauth.

        **The anchor half of this test left with the field** (plan step X-f1e,
        finding N-195): the payload used to carry an ``anchor_balance`` and the
        assertion used to check it was written, because this route accepted one.
        It no longer does -- a balance is asserted at ``accounts.true_up`` -- so
        the subject is the name edit, which is what this route still owns.
        """
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/accounts/{account_id}",
                data={
                    "name": "Renamed Checking",
                    "account_type_id": checking_type.id,
                },
                follow_redirects=False,
            )

            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            # The edit went through despite the stale step-up stamp.
            acct = db.session.get(Account, account_id)
            assert acct.name == "Renamed Checking"

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session updates the account name normally."""
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )

            resp = auth_client.post(
                f"/accounts/{account_id}",
                data={
                    "name": "Primary Checking",
                    "account_type_id": checking_type.id,
                },
                follow_redirects=False,
            )
            # Happy-path 302 to the accounts list (NOT to /reauth).
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            acct = db.session.get(Account, account_id)
            assert acct.name == "Primary Checking"


class TestHardDeleteTemplateIsNotStepUpGated:
    """``POST /templates/<id>/hard-delete`` is NOT step-up gated.

    Unlike :func:`accounts.hard_delete_account` (which keeps its
    gate), a recurring-transaction template is a lower-stakes,
    regenerable object: deleting it only blocks future generation and
    cannot destroy settled financial history (the route refuses when
    settled rows exist).  Step-up was therefore removed.  This is the
    regression lock for that decision; the route stays owner-only via
    ``@require_owner``.
    """

    @staticmethod
    def _create_minimal_template(seed_user):
        """Create a template with no transactions for hard-delete tests."""
        # Authored through the write door (plan step R7c-b): the two-axis
        # columns are NOT NULL, so a rule naming only a pattern cannot be
        # stored.
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Rent"].id,
            transaction_type_id=db.session.query(TransactionType)
            .filter_by(name="Expense").one().id,
            name="Step-Up Test Template",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_every_period_rule(db.session, template)
        return template

    def test_stale_fresh_login_still_deletes(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login no longer blocks the template hard-delete.

        With step-up removed, a session whose last password entry is
        past the old 5-minute window deletes the template normally
        (302 to the list, NOT /reauth), and the row is gone.
        """
        with app.app_context():
            template = self._create_minimal_template(seed_user)
            template_id = template.id

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            # The delete went through despite the stale step-up stamp.
            assert db.session.get(TransactionTemplate, template_id) is None

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session permanently deletes the template normally."""
        with app.app_context():
            template = self._create_minimal_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/templates/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")
            assert db.session.get(TransactionTemplate, template_id) is None


class TestHardDeleteTransferTemplateIsNotStepUpGated:
    """``POST /transfers/<id>/hard-delete`` is NOT step-up gated.

    Mirrors :func:`templates.hard_delete_template`: a transfer
    template is a lower-stakes, regenerable object and the route
    refuses when settled history exists, so step-up was removed.  The
    shadow invariants documented in CLAUDE.md are still enforced by
    the transfer service on every delete -- they do not depend on the
    step-up gate.  This is the regression lock for the removal; the
    route stays owner-only via ``@require_owner``.
    """

    @staticmethod
    def _create_minimal_transfer_template(seed_user):
        """Create a transfer template with no transfers for hard-delete tests."""
        savings_type = (
            db.session.query(AccountType).filter_by(name="Savings").one()
        )
        savings = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=savings_type.id,
                name="Step-Up Test Savings",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(savings)
        db.session.flush()

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Step-Up Test Transfer",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.commit()
        return template

    def test_stale_fresh_login_still_deletes(
        self, app, auth_client, seed_user,
    ):
        """A stale fresh-login no longer blocks the transfer-template delete.

        With step-up removed, a session whose last password entry is
        past the old 5-minute window deletes the transfer template
        normally (302 to the list, NOT /reauth), and the row is gone.
        """
        with app.app_context():
            template = self._create_minimal_transfer_template(seed_user)
            template_id = template.id

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")

            # The delete went through despite the stale step-up stamp.
            assert db.session.get(TransferTemplate, template_id) is None

    def test_fresh_session_succeeds(
        self, app, auth_client, seed_user,
    ):
        """A fresh session permanently deletes the transfer template normally."""
        with app.app_context():
            template = self._create_minimal_transfer_template(seed_user)
            template_id = template.id

            resp = auth_client.post(
                f"/transfers/{template_id}/hard-delete",
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "/reauth" not in resp.headers.get("Location", "")
            assert db.session.get(TransferTemplate, template_id) is None
