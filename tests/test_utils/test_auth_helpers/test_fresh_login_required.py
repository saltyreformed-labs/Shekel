"""Auth helpers tests: the fresh_login_required decorator, its misuse guard and its freshness
window.
"""

from datetime import datetime, timedelta, timezone

import pytest
from flask import Blueprint, Flask

from app.utils.auth_helpers import fresh_login_required
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY


class TestFreshLoginRequiredMisuse:
    """``fresh_login_required`` MUST be called with parentheses.

    Without the runtime guard, a developer who writes
    ``@fresh_login_required`` (no parens) would silently bind the
    view function as ``max_age_minutes`` -- the wrapper would then
    call ``timedelta(minutes=<view_function>)`` on the first request,
    surfacing as a confusing TypeError far from the actual mistake.
    The guard catches the misuse at decoration time so the error
    points directly at the bad source line.
    """

    def test_no_parens_misuse_raises_typeerror(self):
        """Calling without parens (passing a function) raises TypeError."""
        def fake_view():
            """Fake view function used to trigger the misuse."""
            return "fake-view-body"

        with pytest.raises(TypeError, match="parentheses"):
            fresh_login_required(fake_view)

    def test_parens_with_no_args_works(self):
        """``@fresh_login_required()`` returns a usable decorator.

        Smoke test that the documented call form -- empty parens --
        produces a decorator that wraps a view without raising.
        """
        decorator = fresh_login_required()

        @decorator
        def view():
            """Fake view function."""
            return "ok"

        # Just verify the wrap succeeded and the wrapper is callable;
        # behavior is exercised in the integration tests in
        # ``tests/test_adversarial/test_step_up.py``.
        assert callable(view)

    def test_explicit_max_age_works(self):
        """``@fresh_login_required(max_age_minutes=N)`` is accepted."""
        decorator = fresh_login_required(max_age_minutes=10)

        @decorator
        def view():
            """Fake view function."""
            return "ok"

        assert callable(view)


class TestFreshLoginRequiredDecorator:
    """The decorator allows or redirects based on ``_fresh_login_at``.

    Tests use a tiny throwaway Flask app rather than the project's
    full app so the decorator is exercised in isolation -- a
    regression in any other middleware (CSRF, login_required,
    rate-limiting) cannot mask a regression in the decorator
    itself.
    """

    def _make_test_app(self):
        """Build a minimal Flask app with one decorated test route.

        The route is registered without ``login_required`` so the
        test can drive ``_fresh_login_at`` manipulation directly via
        ``session_transaction`` without first having to log in via
        a full ``/login`` round-trip.
        """
        # An import name that points at no module, so Flask roots this app and its
        # blueprint at the working directory; no case reads a root, instance path or logger.
        app = Flask("fresh_login_required_test_app")
        app.config["SECRET_KEY"] = "test-key-for-fresh-login-decorator-tests"
        app.config["FRESH_LOGIN_MAX_AGE_MINUTES"] = 5

        # Register a fake auth.reauth endpoint so url_for inside the
        # decorator can resolve it.  The decorator does not actually
        # call the view, only generates a URL pointing at it.
        bp = Blueprint("auth", app.import_name)

        @bp.route("/reauth")
        def reauth():
            """Fake reauth view; only here so url_for resolves."""
            return "fake-reauth"

        app.register_blueprint(bp)

        @app.route("/decorated", methods=["POST"])
        @fresh_login_required()
        def decorated():
            """Decorated test endpoint; returns marker on success."""
            return "decorated-body"

        @app.route("/decorated-strict", methods=["POST"])
        @fresh_login_required(max_age_minutes=1)
        def decorated_strict():
            """Decorated test endpoint with explicit 1-min window."""
            return "strict-body"

        return app

    def test_fresh_session_passes(self):
        """A fresh ``_fresh_login_at`` lets the request through."""
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc).isoformat()
            )

        resp = client.post("/decorated")
        assert resp.status_code == 200
        assert resp.data == b"decorated-body"

    def test_missing_fresh_login_redirects(self):
        """A missing ``_fresh_login_at`` redirects to /reauth."""
        app = self._make_test_app()
        client = app.test_client()

        resp = client.post("/decorated")
        assert resp.status_code == 302
        assert "/reauth" in resp.headers.get("Location", "")

    def test_stale_fresh_login_redirects(self):
        """A stale ``_fresh_login_at`` redirects to /reauth."""
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()

        resp = client.post("/decorated")
        assert resp.status_code == 302
        assert "/reauth" in resp.headers.get("Location", "")

    def test_explicit_max_age_overrides_config(self):
        """A view-level ``max_age_minutes`` overrides app config.

        ``decorated_strict`` declares ``max_age_minutes=1``.  A
        2-minute-old fresh-login passes the config-default 5-minute
        window but must FAIL the explicit 1-minute window.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat()

        # Default-window endpoint: 2 min < 5 min, passes.
        resp = client.post("/decorated")
        assert resp.status_code == 200

        # Strict endpoint: 2 min > 1 min, redirects.
        resp = client.post("/decorated-strict")
        assert resp.status_code == 302

    def test_htmx_request_returns_204_with_hx_redirect(self):
        """HTMX requests get ``204 + HX-Redirect``, not 302.

        See the integration test in ``test_step_up.py`` for the
        rationale: a 302 would render /reauth's HTML into whatever
        fragment slot the original request targeted.
        """
        app = self._make_test_app()
        client = app.test_client()

        resp = client.post("/decorated", headers={"HX-Request": "true"})
        assert resp.status_code == 204
        assert "HX-Redirect" in resp.headers
        assert "/reauth" in resp.headers["HX-Redirect"]
        assert resp.data == b""

    def test_malformed_fresh_login_treated_as_stale(self):
        """A non-ISO-8601 ``_fresh_login_at`` is treated as stale.

        Fail-closed for tampered cookies -- matches the policy of
        ``_idle_session_is_fresh`` and ``_mfa_pending_is_fresh``.
        Without this, a tampered cookie would 500 the request via
        a ``ValueError`` from ``fromisoformat``.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = "not-an-iso-timestamp"

        resp = client.post("/decorated")
        assert resp.status_code == 302
        assert "/reauth" in resp.headers.get("Location", "")

    def test_naive_fresh_login_treated_as_stale(self):
        """A timezone-naive ``_fresh_login_at`` is treated as stale.

        Naive datetimes raise ``TypeError`` on the timezone-aware
        subtraction.  Reject explicitly so the failure mode is
        consistent with the malformed case.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now().replace(tzinfo=None).isoformat()
            )

        resp = client.post("/decorated")
        assert resp.status_code == 302

    def test_future_dated_fresh_login_treated_as_fresh(self):
        """A future-dated ``_fresh_login_at`` is treated as fresh.

        Mirrors ``_idle_session_is_fresh``: a clock jump (NTP
        correction, manual adjustment, VM resume) must not silently
        force every active session through /reauth.  An attacker who
        forged a future-dated value would already need ``SECRET_KEY``,
        at which point the future-date check adds no defensive value
        beyond the cookie signature itself.

        This is the INVERSE of ``_mfa_pending_is_fresh`` (commit
        C-08), which DOES reject future-dated -- that gate is
        single-use and the strict-rejection cost is one extra
        password retry, vs. a 30-minute window of being bumped
        through /reauth on every request here.
        """
        app = self._make_test_app()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[FRESH_LOGIN_AT_KEY] = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()

        resp = client.post("/decorated")
        assert resp.status_code == 200, (
            "Future-dated _fresh_login_at must be accepted; "
            "rejecting it would log every user out after a clock "
            "jump."
        )

    def test_redirect_includes_next_param(self):
        """The /reauth URL carries the original request URL as ``next``."""
        app = self._make_test_app()
        client = app.test_client()

        resp = client.post("/decorated")
        location = resp.headers.get("Location", "")
        assert "next=" in location, (
            "Decorator must include the original URL as ``next`` so "
            "/reauth can return the user there after success.  "
            f"Got Location={location!r}."
        )
