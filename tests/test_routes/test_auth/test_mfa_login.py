"""Auth route tests: the two-step two-factor sign-in and /mfa/verify's security cases.

No account can turn two-factor off (ruling plan#3604), so the tests of
``/mfa/disable`` went with it.
"""

from app import create_app
from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult
from tests._seed_owner_fixtures import OWNERS_PAGE, SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests._test_helpers import store_backup_codes
from tests._two_factor_sign_in import reset_two_factor_of, two_factor_secret_of


class TestMfaLogin:
    """Tests for the two-step MFA login flow."""

    def _its_two_factor(self, user_id, known_codes=None):
        """Give the shared account's two-factor known backup codes; return them and its secret.

        The account is made with two-factor (plan#3600), so its secret is its
        row's.

        Args:
            user_id: The user's primary key.
            known_codes: Optional list of plaintext backup codes. Defaults to
                         a standard set of 3 codes.

        Returns:
            tuple: (plaintext_secret, plaintext_backup_codes)
        """
        if known_codes is None:
            known_codes = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
        store_backup_codes(db.session, user_id, known_codes)
        return two_factor_secret_of(user_id), known_codes

    def test_login_with_mfa_redirects_to_verify(self, app, client, seed_user):
        """POST /login with MFA enabled redirects to /mfa/verify instead of grid."""
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)

            response = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "mfa/verify" in response.headers.get("Location", "")

            # User should NOT be logged in -- a protected page should redirect.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_page_renders(self, app, client, seed_user):
        """GET /mfa/verify renders the verification form when pending user exists."""
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)

            # POST to /login to set up pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            response = client.get("/mfa/verify")
            assert response.status_code == 200
            assert b"Two-Factor Verification" in response.data

    def test_mfa_verify_no_pending_redirects_to_login(self, app, client):
        """GET /mfa/verify without pending user redirects to login."""
        with app.app_context():
            response = client.get("/mfa/verify", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_mfa_verify_valid_totp(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with valid TOTP code completes login."""
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Step 1: enter pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: submit TOTP code.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            # Should redirect to grid, not back to login or verify.
            location = response.headers.get("Location", "")
            assert "login" not in location
            assert "mfa" not in location

            # User is now logged in -- protected page should return 200.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 200

    def test_mfa_verify_invalid_totp(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify with invalid TOTP code shows generic error."""
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.INVALID,
            )

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            response = client.post("/mfa/verify", data={
                "totp_code": "000000",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

            # User should NOT be logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_totp_code_in_another_digit_script(
        self, app, client, seed_user,
    ):
        """Finding N-136 on the LOGIN path, and it is not an ``int()`` guard.

        ``_find_matching_step`` short-circuited on
        ``len(code) != 6 or not code.isdigit()``, and ``isdigit()`` is true
        for every non-Latin digit script.  A six-character code of Eastern
        Arabic numerals therefore passed the shape check and reached
        ``hmac.compare_digest``, which raises
        ``TypeError: comparing strings with non-ASCII characters is not
        supported`` -- unhandled, on the door every user walks through.
        ``MfaVerifySchema`` validates only ``Length(max=6)`` and neither
        ``verify_totp_code`` nor the route wrapper catches it.

        It always failed CLOSED, so this asserts the same refusal the
        surrounding tests do; what changes is that the refusal is now an
        answer rather than a 500.  ``verify_totp_code`` is deliberately NOT
        monkeypatched here -- the real service is the code under test.
        """
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            eastern_arabic = "١٢٣٤٥٦"
            # The premise: this is exactly what the retired guard accepted.
            assert len(eastern_arabic) == 6
            assert eastern_arabic.isdigit()

            response = client.post("/mfa/verify", data={
                "totp_code": eastern_arabic,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

            # Still unauthenticated -- the refusal did not become an opening.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_missing_totp_key(self, app, client, seed_user, monkeypatch):
        """POST /mfa/verify redirects to login when TOTP key is missing.

        When TOTP_ENCRYPTION_KEY is removed after MFA was enabled,
        decrypt_secret() raises RuntimeError.  The route must catch
        this, clear pending session state, and redirect to login
        instead of returning a 500 error.
        """
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)

            # Step 1: enter pending state via login.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Remove the key so decrypt_secret raises RuntimeError.
            monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)

            # Step 2: attempt MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_mfa_verify_valid_backup_code(self, app, client, seed_user):
        """POST /mfa/verify with valid backup code completes login and consumes the code."""
        with app.app_context():
            _, known_codes = self._its_two_factor(seed_user["user"].id)

            # Step 1: enter pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: submit the first backup code.
            response = client.post("/mfa/verify", data={
                "backup_code": known_codes[0],
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "login" not in location
            assert "mfa" not in location

            # User is logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 200

            # The used code should be removed from the database.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert len(config.backup_codes) == len(known_codes) - 1

    def test_mfa_verify_invalid_backup_code(self, app, client, seed_user):
        """POST /mfa/verify with invalid backup code shows generic error."""
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            response = client.post("/mfa/verify", data={
                "backup_code": "zzzzzzzz",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

            # User should NOT be logged in.
            grid_resp = client.get("/", follow_redirects=False)
            assert grid_resp.status_code == 302
            assert "login" in grid_resp.headers.get("Location", "")

    def test_mfa_verify_backup_code_consumed(self, app, client, seed_user):
        """A used backup code cannot be reused."""
        with app.app_context():
            _, known_codes = self._its_two_factor(seed_user["user"].id)

            # First login cycle: use the first backup code.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            client.post("/mfa/verify", data={
                "backup_code": known_codes[0],
            })

            # Log out.
            client.post("/logout")

            # Second login cycle: try the same backup code again.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            response = client.post("/mfa/verify", data={
                "backup_code": known_codes[0],
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid verification code." in response.data

    def test_login_without_mfa_signs_in_and_is_held_at_setup(self, app, client, seed_user):
        """POST /login without MFA enabled signs in in one step, and setup is next.

        The account's two-factor is reset first, so it has none, as after
        ``scripts/reset_mfa.py``.  The password alone finishes the sign-in:
        no code form.  Every account must have two-factor (plan#3452), so the
        signed-in browser's next page is setup, which an anonymous browser is
        refused: the sign-in did finish.
        """
        with app.app_context():
            reset_two_factor_of(seed_user["user"].id)
            response = client.post("/login", data={
                "email": SEED_USER_EMAIL,
                "password": SEED_USER_PASSWORD,
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Lands where an owner's sign-in lands, not on /mfa/verify.
            assert location.endswith(OWNERS_PAGE), location

            held = client.get(OWNERS_PAGE, follow_redirects=False)
            assert held.status_code == 302
            assert held.headers["Location"].endswith("/mfa/setup")
            setup = client.get("/mfa/setup", follow_redirects=False)
            assert setup.status_code == 200
            assert b"Verify and Enable" in setup.data

    def test_mfa_open_redirect_absolute_url_blocked(
        self, app, client, seed_user, monkeypatch
    ):
        """Open redirect via the MFA flow must be blocked.

        Attack scenario: attacker puts a malicious next on the login URL.
        The next value is stored in the session during the MFA pending step
        and used after TOTP verification.  It must be validated at storage
        time AND at redirect time (defense in depth).
        """
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Step 1: login with malicious next parameter.
            client.post("/login?next=https://evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: complete MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must NOT redirect to the attacker's site.
            assert "evil.com" not in location
            # Must redirect to the dashboard (home page).
            assert "/dashboard" in location or location.endswith("/")

    def test_mfa_open_redirect_protocol_relative_blocked(
        self, app, client, seed_user, monkeypatch
    ):
        """Protocol-relative URLs must be blocked in the MFA flow.

        //evil.com bypasses scheme-only checks and inherits the current
        page's protocol.  The MFA flow stores next in the session and
        uses it after TOTP verification -- both steps must validate.
        """
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Step 1: login with protocol-relative next.
            client.post("/login?next=//evil.com", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: complete MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            assert "evil.com" not in location
            assert "/dashboard" in location or location.endswith("/")

    def test_mfa_safe_next_redirect_allowed(
        self, app, client, seed_user, monkeypatch
    ):
        """Legitimate next values must survive the MFA two-step flow.

        A safe relative path set on the login URL must be preserved
        through the MFA pending session state and used as the redirect
        target after successful TOTP verification.
        """
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Step 1: login with a safe next parameter.
            client.post("/login?next=/templates", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: complete MFA verification.
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            location = response.headers.get("Location", "")
            # Must redirect to the safe requested page.
            assert "/templates" in location


class TestMfaVerifySecurity:
    """Security tests for the /mfa/verify endpoint.

    Covers rate limiting, session isolation (IDOR prevention), and
    rejection of requests with no pending MFA state.
    """

    def _its_two_factor(self, user_id):
        """Give the shared account's two-factor one known backup code; return its secret.

        The account is made with two-factor (plan#3600), so its secret is its
        row's.

        Args:
            user_id: The user's primary key.

        Returns:
            str: The plaintext TOTP secret.
        """
        store_backup_codes(db.session, user_id, ["aaaaaaaa"])
        return two_factor_secret_of(user_id)

    def test_mfa_verify_rate_limiting(self, app, seed_user):
        """POST /mfa/verify is rate-limited to 5 attempts per 15 minutes.

        Without rate limiting, a 6-digit TOTP code (1,000,000 possibilities)
        could be brute-forced in hours. This test verifies the limiter blocks
        the 6th failed attempt with a 429 status.

        ``try``/``finally`` cleanup: the limiter is reset and disabled
        even when any of the per-step assertions below fail.  Without
        this guard a failed assertion would leave ``limiter.enabled =
        True`` and a populated rate-limit bucket on the Limiter
        singleton, which every subsequent test on the same xdist
        worker would inherit -- see
        ``phase1-flake-investigation.md`` failure shape #1 for the
        full failure mode.
        """
        with app.app_context():
            # Create a fresh app with rate limiting enabled (TestConfig disables it).
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            # Re-initialize limiter with rate limiting enabled.
            from app.rate_limits import limiter  # pylint: disable=import-outside-toplevel
            limiter.enabled = True
            limiter.init_app(rate_app)

            try:
                rate_client = rate_app.test_client()

                with rate_app.app_context():
                    # Enable MFA for the seed user.
                    self._its_two_factor(seed_user["user"].id)

                    # Login to reach MFA pending state.
                    login_resp = rate_client.post("/login", data={
                        "email": "test@shekel.local",
                        "password": "testpass",
                    }, follow_redirects=False)
                    assert login_resp.status_code == 302
                    assert "mfa/verify" in login_resp.headers.get("Location", "")

                    # Submit 5 wrong TOTP codes (within the limit).
                    for i in range(5):
                        resp = rate_client.post("/mfa/verify", data={
                            "totp_code": "000000",
                        })
                        # Each attempt should succeed at the HTTP level (invalid code, not rate-limited).
                        assert resp.status_code == 200, \
                            f"Attempt {i + 1} should return 200, got {resp.status_code}"
                        assert b"Invalid verification code." in resp.data

                    # 6th attempt should be rate-limited.
                    response = rate_client.post("/mfa/verify", data={
                        "totp_code": "000000",
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
                    from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
                    _db.engine.dispose()
                if limiter._storage is not None:  # pylint: disable=protected-access
                    limiter.reset()
                limiter.enabled = False

    def test_mfa_verify_idor_other_users_pending_session(self, app, client, seed_user):
        """Session isolation: a second client cannot access another session's MFA state.

        The _mfa_pending_user_id is stored in the Flask session cookie, so a
        separate client (different session) should have no access to it. This
        prevents cross-session MFA completion.
        """
        with app.app_context():
            self._its_two_factor(seed_user["user"].id)

            # Client 1: Login to enter MFA pending state.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Client 2: A separate test client with its own session.
            client2 = app.test_client()

            # Client 2 has no pending MFA state -- should be redirected to login.
            response = client2.get("/mfa/verify", follow_redirects=False)
            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")

    def test_mfa_verify_no_pending_user_post_rejected(self, app, client):
        """POST /mfa/verify with no pending MFA user redirects to login.

        Prevents abuse of the verify endpoint when no authentication flow
        is in progress. The route checks for _mfa_pending_user_id before
        processing any submitted code.
        """
        with app.app_context():
            response = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert response.status_code == 302
            assert "login" in response.headers.get("Location", "")
