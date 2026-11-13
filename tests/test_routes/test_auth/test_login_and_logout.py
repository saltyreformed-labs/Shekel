"""Auth route tests: the safe-redirect check, /login, /logout, and lockout through the /login
route.
"""

from datetime import datetime, timedelta, timezone

from app import create_app
from app.extensions import db
from app.models.user import User
from app.routes.auth._helpers import _is_safe_redirect
from tests._guess_records import count_of, locked_until_of, set_record


class TestIsSafeRedirect:
    """Unit tests for the _is_safe_redirect() helper function.

    This function is the sole defense against open redirect attacks in
    the login and MFA flows.  Every known bypass technique must be
    covered here.
    """

    def test_none_returns_false(self):
        """None input must be rejected -- no redirect target was provided."""
        assert _is_safe_redirect(None) is False

    def test_empty_string_returns_false(self):
        """Empty string must be rejected -- equivalent to no target."""
        assert _is_safe_redirect("") is False

    def test_whitespace_only_returns_false(self):
        """Whitespace-only strings must be rejected after stripping."""
        assert _is_safe_redirect("   ") is False

    def test_relative_path_returns_true(self):
        """A simple relative path is the expected legitimate input."""
        assert _is_safe_redirect("/templates") is True

    def test_relative_path_with_query_returns_true(self):
        """Relative paths with query strings are legitimate."""
        assert _is_safe_redirect("/settings?section=security") is True

    def test_absolute_http_url_returns_false(self):
        """Absolute https:// URLs are the primary open redirect vector."""
        assert _is_safe_redirect("https://evil.com") is False

    def test_absolute_http_url_with_path_returns_false(self):
        """Absolute URLs with paths are used for phishing pages."""
        assert _is_safe_redirect("https://evil.com/phishing") is False

    def test_protocol_relative_url_returns_false(self):
        """Protocol-relative URLs (//host) bypass scheme-only checks."""
        assert _is_safe_redirect("//evil.com") is False

    def test_backslash_url_returns_false(self):
        """Backslash-prefixed URLs are normalized to // by some browsers."""
        assert _is_safe_redirect("\\evil.com") is False

    def test_javascript_scheme_returns_false(self):
        """javascript: scheme can execute arbitrary code in the browser."""
        assert _is_safe_redirect("javascript:alert(1)") is False

    def test_data_scheme_returns_false(self):
        """data: scheme can render attacker-controlled HTML content."""
        assert _is_safe_redirect("data:text/html,<h1>phish</h1>") is False

    def test_newline_injection_returns_false(self):
        """Embedded newlines can confuse URL parsers and inject headers."""
        assert _is_safe_redirect("/safe\nevil.com") is False

    def test_tab_injection_returns_false(self):
        """Embedded tabs can confuse URL parsers."""
        assert _is_safe_redirect("/safe\tevil.com") is False

    def test_carriage_return_injection_returns_false(self):
        """Embedded carriage returns can inject HTTP headers."""
        assert _is_safe_redirect("/safe\revil.com") is False

    def test_leading_whitespace_with_scheme_returns_false(self):
        """Leading whitespace before a scheme must still be caught."""
        assert _is_safe_redirect("  https://evil.com") is False

    def test_mixed_case_scheme_returns_false(self):
        """urlparse handles mixed-case schemes (HTTP://) correctly."""
        assert _is_safe_redirect("HTTP://evil.com") is False

    def test_ftp_scheme_returns_false(self):
        """Non-HTTP schemes like ftp:// must also be rejected."""
        assert _is_safe_redirect("ftp://evil.com/file") is False


class TestLogin:
    """Tests for the /login endpoint."""

    def test_login_page_renders(self, app, client):
        """GET /login returns the login form."""
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Sign In" in response.data

    def test_successful_login(self, app, client, seed_user):
        """POST /login with valid credentials redirects to grid."""
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "/" in response.headers.get("Location", "")

    def test_failed_login(self, app, client, seed_user):
        """POST /login with wrong password shows error."""
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "wrongpassword",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_protected_routes_redirect_to_login(self, app, client):
        """Unauthenticated requests to protected routes redirect to /login."""
        with app.app_context():
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_login_disabled_account(self, app, client, seed_user):
        """POST /login with disabled account shows generic error message."""
        with app.app_context():
            # Disable the user account.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.commit()

            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)

            assert response.status_code == 200
            # Route shows generic message (doesn't reveal account status).
            assert b"Invalid email or password" in response.data

    def test_rate_limiting_after_5_attempts(self, app, seed_user):
        """POST /login is rate-limited to 5 attempts per 15 minutes.

        ``try``/``finally`` cleanup: the limiter is reset and disabled
        even when the assertion below fails.  Without this guard a
        failed assertion would leave ``limiter.enabled = True`` and a
        populated rate-limit bucket on the Limiter singleton, which
        every subsequent test on the same xdist worker would
        inherit -- see ``phase1-flake-investigation.md`` failure
        shape #1 for the full failure mode.
        """
        with app.app_context():
            # Create a fresh app with rate limiting enabled (TestConfig disables it).
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            # Re-initialize limiter with rate limiting enabled.
            from app.rate_limits import limiter
            limiter.enabled = True
            limiter.init_app(rate_app)

            try:
                rate_client = rate_app.test_client()

                with rate_app.app_context():
                    # Make 5 failed login attempts (within the limit).
                    for _ in range(5):
                        rate_client.post("/login", data={
                            "email": "test@shekel.local",
                            "password": "wrongpassword",
                        })

                    # 6th attempt should be rate-limited.
                    response = rate_client.post("/login", data={
                        "email": "test@shekel.local",
                        "password": "wrongpassword",
                    })
                    assert response.status_code == 429
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
                    from app.extensions import db as _db
                    _db.engine.dispose()
                if limiter._storage is not None:  # pylint: disable=protected-access
                    limiter.reset()
                limiter.enabled = False

    def test_login_nonexistent_email(self, app, client):
        """POST /login with nonexistent email shows the same generic error.

        Anti-enumeration: same message as wrong password to prevent
        email discovery attacks. If the message differs, an attacker can
        determine which emails have accounts.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "nobody@shekel.local",
                "password": "anything",
            }, follow_redirects=True)

            assert response.status_code == 200
            # Anti-enumeration: same message as wrong password.
            assert b"Invalid email or password" in response.data

    def test_login_missing_email_field(self, app, client):
        """POST /login with no email field returns generic error, not a crash.

        The route uses request.form.get('email', '') which defaults to
        empty string when the key is absent.
        """
        with app.app_context():
            response = client.post("/login", data={
                "password": "testpass",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            assert b"Internal Server Error" not in response.data

    def test_login_missing_password_field(self, app, client, seed_user):
        """POST /login with no password field returns generic error, not a crash.

        The route uses request.form.get('password', '') which defaults to
        empty string when the key is absent.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            assert b"Internal Server Error" not in response.data

    def test_login_empty_both_fields(self, app, client):
        """POST /login with empty email and password returns generic error.

        Edge case: both fields present but empty strings. Catches any
        difference in handling between missing and empty fields.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "",
                "password": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_login_xss_in_email_field(self, app, client):
        """POST /login with XSS payload in email does not render unescaped HTML.

        Verifies Jinja2 auto-escaping prevents script injection. The login
        template does not echo the submitted email, so the payload should
        not appear in the response at all.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": '<script>alert("xss")</script>',
                "password": "anything",
            }, follow_redirects=True)

            assert response.status_code == 200
            # The raw script tag must not appear unescaped in the response.
            assert b'<script>alert("xss")</script>' not in response.data
            assert b"Internal Server Error" not in response.data

    def test_login_xss_in_password_field(self, app, client, seed_user):
        """POST /login with XSS payload in password does not render unescaped HTML.

        Password fields are never echoed, but verify no injection path
        exists via error messages or logging output rendered in HTML.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": '<script>alert("xss")</script>',
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            assert b'<script>alert("xss")</script>' not in response.data

    def test_open_redirect_absolute_url_blocked(self, app, client, seed_user):
        """An attacker-supplied absolute URL in the next parameter must be rejected.

        The user must land on the grid after login, not on the attacker's
        site.  This is the primary open redirect attack vector: an attacker
        crafts /login?next=https://evil.com/phishing and sends it to a victim.
        """
        with app.app_context():
            response = client.post("/login?next=https://evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must NOT redirect to the attacker's site.
            assert "evil.com" not in location
            # Must redirect to the dashboard (home page).
            assert "/dashboard" in location or location.endswith("/")

    def test_open_redirect_protocol_relative_blocked(self, app, client, seed_user):
        """Protocol-relative URLs (//host) are a common bypass for scheme-only checks.

        //evil.com is parsed by browsers as a protocol-relative URL, inheriting
        the current page's scheme.  Must be rejected.
        """
        with app.app_context():
            response = client.post("/login?next=//evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "evil.com" not in location
            assert "/dashboard" in location or location.endswith("/")

    def test_open_redirect_backslash_blocked(self, app, client, seed_user):
        """Backslash-prefixed URLs are normalized to protocol-relative by some browsers.

        \\evil.com may be treated as //evil.com by older IE and some WebKit
        builds.  Must be rejected.
        """
        with app.app_context():
            response = client.post("/login?next=\\evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "evil.com" not in location

    def test_open_redirect_javascript_scheme_blocked(self, app, client, seed_user):
        """javascript: scheme URLs can execute arbitrary code in the browser.

        Must be rejected to prevent XSS via redirect.
        """
        with app.app_context():
            response = client.post(
                "/login?next=javascript:alert(1)", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "javascript" not in location

    def test_open_redirect_data_scheme_blocked(self, app, client, seed_user):
        """data: scheme URLs can render attacker-controlled HTML content.

        Must be rejected to prevent phishing via redirect.
        """
        with app.app_context():
            response = client.post(
                "/login?next=data:text/html,<h1>phish</h1>", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "data:" not in location

    def test_safe_next_redirect_allowed(self, app, client, seed_user):
        """Legitimate relative paths in the next parameter must still work.

        Users should be redirected to the page they originally requested
        after logging in.
        """
        with app.app_context():
            response = client.post("/login?next=/templates", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must redirect to the requested page, not the default grid.
            assert "/templates" in location

    def test_safe_next_with_query_string_allowed(self, app, client, seed_user):
        """Relative paths with query strings are legitimate and must be preserved.

        Common case: Flask-Login's unauthorized_handler adds
        ?next=/settings%3Fsection%3Dsecurity to the login URL.
        """
        with app.app_context():
            response = client.post(
                "/login?next=/settings%3Fsection%3Dsecurity", data={
                    "email": "test@shekel.local",
                    "password": "testpass",
                }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "/settings" in location

    def test_no_next_redirects_to_dashboard(self, app, client, seed_user):
        """When no next parameter is provided, the default redirect is the dashboard.

        This is the normal login flow -- no next parameter at all.
        """
        with app.app_context():
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Default redirect is the dashboard (home page).
            assert "/dashboard" in location or location.endswith("/")

    def test_deactivated_user_cannot_access_protected_routes(
        self, app, auth_client, seed_user
    ):
        """An active session becomes invalid when the user is deactivated.

        Flask-Login's UserMixin.is_authenticated always returns True, so
        the user_loader must explicitly check is_active. Without this
        check, a deactivated user's existing session remains valid for
        the entire cookie lifetime (up to 30 days).
        """
        with app.app_context():
            # Verify the session is currently valid.
            response = auth_client.get("/", follow_redirects=False)
            assert response.status_code == 200

            # Deactivate the user mid-session.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.commit()

            # Next request should be rejected -- user_loader returns None.
            response = auth_client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

            # Attempting to log in again should also fail.
            response = auth_client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data

    def test_reactivated_user_can_login_again(
        self, app, client, seed_user
    ):
        """Re-activating a deactivated user restores login access.

        After setting is_active = False (blocking login), setting it
        back to True should allow the user to log in with the same
        credentials.  This positive regression test ensures the
        is_active check is not a one-way door.
        """
        with app.app_context():
            # Deactivate the user.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = False
            db.session.commit()

            # Login must fail while deactivated.
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)
            assert b"Invalid email or password" in response.data

            # Re-activate the user.
            user = db.session.get(User, seed_user["user"].id)
            user.is_active = True
            db.session.commit()

            # Login must succeed again.
            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)
            assert response.status_code == 302
            assert "login" not in response.headers.get("Location", "")


class TestLogout:
    """Tests for the /logout endpoint."""

    def test_logout_redirects_to_login(self, app, auth_client):
        """POST /logout ends session and redirects."""
        with app.app_context():
            response = auth_client.post("/logout", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_logout_rejects_get(self, app, seed_user, client):
        """GET /logout returns 405 Method Not Allowed for authenticated users."""
        with app.app_context():
            # Log in with a fresh client so the session is still active.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            response = client.get("/logout")
            assert response.status_code == 405


# ---------------------------------------------------------------------------
# Account-lockout integration with the /login route (audit F-033 / C-11)
# ---------------------------------------------------------------------------


class TestLoginLockoutIntegration:
    """End-to-end tests that exercise lockout through the /login route.

    The service-level tests in
    ``tests/test_services/test_auth_service/test_lockout.py`` cover the lockout
    semantics in isolation; this class verifies the integration with
    the Flask route -- in particular that the route's flash message,
    redirect target, and the persistence of the count on the record of the
    account's email remain consistent under the new behaviour.
    """

    def test_failed_login_increments_db_counter(
        self, app, client, seed_user,
    ):
        """A bad-password POST persists the increment across requests.

        Verifies the ``db.session.commit`` inside ``authenticate``
        survives the request-response boundary.  Without the commit,
        the counter would only live in the request's session and
        reset on the next request.
        """
        with app.app_context():
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "wrong-password",
            }, follow_redirects=True)
            assert count_of("test@shekel.local") == 1

    def test_threshold_failures_lock_account_via_route(
        self, app, client, seed_user, monkeypatch,
    ):
        """Threshold-many bad POSTs through /login lock the account.

        Uses a low threshold (3) to avoid running into the @limiter
        rate limit (5/15min) that fires before the lockout would.
        Rate limiting is disabled in TestConfig so the limit is not
        actually enforced; the explicit threshold reduction keeps
        the test future-proof if rate limiting is enabled.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "15")
        with app.app_context():
            for _ in range(3):
                client.post("/login", data={
                    "email": "test@shekel.local",
                    "password": "wrong",
                }, follow_redirects=True)

            locked_until = locked_until_of("test@shekel.local")
            assert locked_until is not None
            assert locked_until > datetime.now(timezone.utc)

    def test_locked_account_login_returns_generic_error(
        self, app, client, seed_user,
    ):
        """A POST during lockout shows the generic invalid-credential message.

        The server MUST NOT distinguish the lockout state from a wrong
        password in the user-facing response: an attacker probing
        whether an account is locked would otherwise get a free signal
        about the lockout window.
        """
        with app.app_context():
            now = datetime.now(timezone.utc)
            set_record(
                "test@shekel.local", count=0,
                locked_until=now + timedelta(minutes=10), last_attempt_at=now,
            )
            db.session.commit()

            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=True)
            assert response.status_code == 200
            assert b"Invalid email or password" in response.data
            # The response body must NOT name the lockout state.
            assert b"locked" not in response.data.lower()

    def test_successful_login_after_typo_resets_counter(
        self, app, client, seed_user,
    ):
        """A correct password after a few typos zeroes the counter.

        Mirrors the service-level test but goes through the HTTP
        boundary so the route's commit-after-authenticate path is
        exercised.
        """
        with app.app_context():
            for _ in range(3):
                client.post("/login", data={
                    "email": "test@shekel.local",
                    "password": "wrong",
                }, follow_redirects=True)
            assert count_of("test@shekel.local") == 3

            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)
            assert response.status_code == 302

            assert count_of("test@shekel.local") == 0
            assert locked_until_of("test@shekel.local") is None
