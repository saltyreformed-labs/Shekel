"""Tests for custom error pages and production configuration."""

import logging

import pytest
from flask import abort

from app import create_app
from app.config import BaseConfig, ProdConfig
from app.utils.log_events import ACCESS, EVT_RATE_LIMIT_EXCEEDED


class TestErrorPages:
    """Tests for custom error page rendering and production config.

    The trigger routes below are registered on a throwaway app and hit
    anonymously, and since plan step ``bank_import:X-gi-4`` every route is
    gated by the login gate unless declared public -- an undeclared
    ``/test-400-trigger`` would answer the login redirect, never the 400.
    So each throwaway app sets ``LOGIN_DISABLED``, Flask-Login's own switch
    (the gate honours it exactly as ``login_required`` did): the subject here
    is the error page, and switching the gate off is the honest way to reach
    it without a session.  ``tests/test_routes/test_auth_required.py`` grades
    the switch itself.
    """

    def test_404_renders_custom_page(self, app, auth_client):
        """GET /nonexistent-path returns 404 with custom template."""
        response = auth_client.get("/this-page-does-not-exist")
        assert response.status_code == 404
        html = response.data.decode()
        assert "Page Not Found" in html

    def test_404_contains_navigation(self, app, auth_client):
        """404 page contains a recovery link back to the dashboard."""
        response = auth_client.get("/this-page-does-not-exist")
        html = response.data.decode()
        assert "/dashboard" in html
        assert "Back to Dashboard" in html

    def test_429_renders_custom_page(self, app, seed_user):
        """Rate-limited request returns 429 with custom template.

        ``try``/``finally`` cleanup: the limiter is reset and disabled
        even when the assertions below fail.  Without this guard a
        failed assertion would leave ``limiter.enabled = True`` and a
        populated rate-limit bucket on the Limiter singleton, which
        every subsequent test on the same xdist worker would
        inherit -- see ``phase1-flake-investigation.md`` failure
        shape #1 for the full failure mode.
        """
        with app.app_context():
            # Create a fresh app with rate limiting enabled
            # (TestConfig disables it).
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            from app.extensions import limiter  # pylint: disable=import-outside-toplevel
            limiter.enabled = True
            limiter.init_app(rate_app)

            try:
                rate_client = rate_app.test_client()

                with rate_app.app_context():
                    # Exceed the 5-per-15-minutes login rate limit.
                    for _ in range(6):
                        response = rate_client.post("/login", data={
                            "email": "test@shekel.local",
                            "password": "wrongpassword",
                        })

                    # The last response is guaranteed to be rate-limited.
                    assert response.status_code == 429
                    html = response.data.decode()
                    assert "Too Many Requests" in html
            finally:
                # Clean up: dispose the secondary app's engine to
                # release connections, clear the rate-limit storage's
                # counters (so a future test that flips
                # ``limiter.enabled = True`` without calling
                # ``init_app`` cannot inherit non-zero counts from
                # this test -- see Issue 2a of
                # shekel-audits/security-2026-04-15/c-38-followups.md),
                # and reset limiter for other tests.  The ``finally``
                # guarantees this runs even if an assertion above
                # raised.
                with rate_app.app_context():
                    from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                    _db.engine.dispose()
                if limiter._storage is not None:  # pylint: disable=protected-access
                    limiter.reset()
                limiter.enabled = False

    def test_429_includes_retry_after_header(self, app, seed_user):
        """429 response includes a Retry-After header near 15 minutes.

        The header is computed by Flask-Limiter as
        ``int(reset_at - time.time())`` where ``reset_at`` is the
        oldest hit timestamp plus 900 seconds (the 15-minute window
        for ``5 per 15 minutes``).  In isolation the six wrong-password
        logins below complete in well under a second and the integer
        rounds to 900, but under heavy ``-n 12`` parallel load the
        per-request latency can stretch and the value occasionally
        rounds to 899.  The tolerance below (``895..900``) is generous
        enough that no plausible timing pressure flakes the test and
        narrow enough that a real regression (e.g. the window
        downgraded to a few seconds) would still fire.  See
        ``phase1-flake-investigation.md`` failure shape #1.

        ``try``/``finally`` cleanup mirrors ``test_429_renders_custom_page``;
        see that test's docstring for the rationale.
        """
        with app.app_context():
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            from app.extensions import limiter  # pylint: disable=import-outside-toplevel
            limiter.enabled = True
            limiter.init_app(rate_app)

            try:
                rate_client = rate_app.test_client()

                with rate_app.app_context():
                    for _ in range(6):
                        response = rate_client.post("/login", data={
                            "email": "test@shekel.local",
                            "password": "wrongpassword",
                        })

                    assert response.status_code == 429
                    retry_after = int(response.headers["Retry-After"])
                    assert 895 <= retry_after <= 900, (
                        f"Retry-After should be ~900s (15-min window), "
                        f"got {retry_after}"
                    )
            finally:
                with rate_app.app_context():
                    from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                    _db.engine.dispose()
                if limiter._storage is not None:  # pylint: disable=protected-access
                    limiter.reset()
                limiter.enabled = False

    def test_429_emits_rate_limit_exceeded_event(self, app, seed_user):
        """429 handler emits a structured ``rate_limit_exceeded`` event.

        Audit Commit C-15 / finding F-146.  Without this event, a slow
        credential-stuffing campaign that gets rate-limited but does
        not propagate to any human-visible signal would proceed
        silently.  The Loki-side alerting rule fires on count of this
        event over a window, so its presence is the load-bearing
        signal -- not the 429 status code (which only the rate-limited
        client sees).
        """
        rate_app = create_app("testing")
        rate_app.config["RATELIMIT_ENABLED"] = True

        from app.extensions import limiter  # pylint: disable=import-outside-toplevel
        limiter.enabled = True
        limiter.init_app(rate_app)

        rate_client = rate_app.test_client()

        captured = []

        class _Capture(logging.Handler):
            """Append every record we see -- captures the rate_limit one."""
            def emit(self, record):
                captured.append(record)

        capture_handler = _Capture(level=logging.WARNING)
        # The 429 handler logs to ``app`` (the package logger; see
        # ``app/__init__.py`` ``_RATE_LIMIT_LOGGER``).  Hook the same
        # logger so the captured records include the emitted event.
        target_logger = logging.getLogger("app")
        target_logger.addHandler(capture_handler)
        try:
            with rate_app.app_context():
                # Five attempts succeed under the per-route 5/15min
                # ceiling; the sixth trips the rate limit.  We make
                # exactly one extra request after the ceiling so the
                # captured list contains exactly one ``rate_limit_exceeded``
                # record for an unambiguous assertion.
                for _ in range(6):
                    response = rate_client.post("/login", data={
                        "email": "test@shekel.local",
                        "password": "wrongpassword",
                    })

                assert response.status_code == 429
        finally:
            target_logger.removeHandler(capture_handler)
            with rate_app.app_context():
                from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                _db.engine.dispose()
            # Clear counters before disabling so a future test that
            # flips ``limiter.enabled = True`` without calling
            # ``init_app`` cannot inherit non-zero counts from this
            # test -- see Issue 2a of
            # shekel-audits/security-2026-04-15/c-38-followups.md.
            if limiter._storage is not None:  # pylint: disable=protected-access
                limiter.reset()
            limiter.enabled = False

        # Filter to the rate-limit events.  ``log_event`` annotates
        # the record with ``event`` and ``category`` extras so we can
        # match without scanning message strings.
        rate_records = [
            r for r in captured
            if getattr(r, "event", None) == EVT_RATE_LIMIT_EXCEEDED
        ]
        assert len(rate_records) == 1, (
            f"Expected exactly one rate_limit_exceeded record; got "
            f"{len(rate_records)}.  All captured events: "
            f"{[getattr(r, 'event', None) for r in captured]!r}"
        )

        record = rate_records[0]
        assert record.category == ACCESS, (
            f"rate_limit_exceeded should be ACCESS; got {record.category!r}"
        )
        assert record.levelno == logging.WARNING, (
            f"rate_limit_exceeded should be WARNING; got {record.levelno}"
        )
        assert record.path == "/login", (
            f"Expected path='/login' on the captured record, got {record.path!r}"
        )
        assert record.method == "POST", (
            f"Expected method='POST' on the captured record, got {record.method!r}"
        )
        assert record.remote_addr == "127.0.0.1", (
            f"Expected remote_addr='127.0.0.1' (Werkzeug test client), "
            f"got {record.remote_addr!r}"
        )

    def test_400_renders_custom_page(self):
        """400 error returns the custom error template, not Werkzeug default.

        Uses a temporary route that calls abort(400) to trigger the
        handler, because TestConfig disables CSRF validation (the most
        common real-world source of 400 errors).
        """
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-400-trigger")
        def trigger_400():
            """Intentional 400 for testing the error handler."""
            abort(400)

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-400-trigger")
            assert response.status_code == 400
            html = response.data.decode()
            assert "Bad Request" in html
            assert "werkzeug" not in html.lower()

        # Dispose the secondary app's engine to release connections.
        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_400_contains_navigation(self):
        """400 page contains a recovery link back to the dashboard."""
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-400-trigger")
        def trigger_400():
            """Intentional 400 for testing navigation link."""
            abort(400)

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-400-trigger")
            html = response.data.decode()
            assert "Back to Dashboard" in html

        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_400_does_not_leak_werkzeug_details(self):
        """400 response body does not contain Werkzeug version or debug info.

        This is the core security concern from H-001: without a custom
        handler, Werkzeug's default 400 page reveals the framework name
        and version, which aids attackers in identifying known
        vulnerabilities.
        """
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-400-trigger")
        def trigger_400():
            """Intentional 400 for testing information leakage."""
            abort(400)

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-400-trigger")
            html = response.data.decode()
            assert "werkzeug" not in html.lower()
            assert "Traceback" not in html
            assert "debugger" not in html

        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_403_renders_custom_page(self):
        """403 error returns the custom error template, not Werkzeug default.

        Uses a temporary route that calls abort(403) to trigger the
        handler.  In production, 403 would be triggered by permission
        checks in a multi-user context.
        """
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-403-trigger")
        def trigger_403():
            """Intentional 403 for testing the error handler."""
            abort(403)

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-403-trigger")
            assert response.status_code == 403
            html = response.data.decode()
            assert "Access Denied" in html
            assert "werkzeug" not in html.lower()

        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_403_contains_navigation(self):
        """403 page contains a recovery link back to the dashboard."""
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-403-trigger")
        def trigger_403():
            """Intentional 403 for testing navigation link."""
            abort(403)

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-403-trigger")
            html = response.data.decode()
            assert "Back to Dashboard" in html

        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_403_does_not_leak_werkzeug_details(self):
        """403 response body does not contain Werkzeug version or debug info."""
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-403-trigger")
        def trigger_403():
            """Intentional 403 for testing information leakage."""
            abort(403)

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-403-trigger")
            html = response.data.decode()
            assert "werkzeug" not in html.lower()
            assert "Traceback" not in html
            assert "debugger" not in html

        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_500_renders_custom_page(self):
        """500 error returns the custom error template."""
        # Create a fresh app so we can register a test route before any
        # requests are handled (Flask forbids late route registration).
        error_app = create_app("testing")
        error_app.config["PROPAGATE_EXCEPTIONS"] = False
        error_app.config["LOGIN_DISABLED"] = True

        @error_app.route("/test-500-trigger")
        def trigger_500():
            """Intentional error for testing the 500 handler."""
            raise RuntimeError("Intentional test error")

        error_client = error_app.test_client()

        with error_app.app_context():
            response = error_client.get("/test-500-trigger")
            assert response.status_code == 500
            html = response.data.decode()
            assert "Something Went Wrong" in html

        # Dispose the secondary app's engine to release connections.
        with error_app.app_context():
            from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
            _db.engine.dispose()

    def test_production_debug_false(self, monkeypatch):
        """ProdConfig has DEBUG=False."""
        # Class attributes are set at import time; patch them directly.
        # 64-char hex key passes the new minimum-length and
        # placeholder-rejection checks added by F-016/F-110.
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "0123456789abcdef" * 4
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://localhost/shekel"
        )
        config = ProdConfig()
        assert config.DEBUG is False

    def test_production_validates_secret_key(self, monkeypatch):
        """ProdConfig raises ValueError for default secret key."""
        monkeypatch.setattr(
            BaseConfig, "SECRET_KEY", "dev-only-change-me-in-production"
        )
        monkeypatch.setattr(
            ProdConfig, "SQLALCHEMY_DATABASE_URI", "postgresql://localhost/shekel"
        )
        with pytest.raises(ValueError, match="SECRET_KEY"):
            ProdConfig()
