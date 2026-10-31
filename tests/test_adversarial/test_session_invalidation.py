"""Shekel Budget App -- Adversarial Tests for Session Invalidation (C-08).

End-to-end coverage for the three cluster findings closed by commit
C-08 of the 2026-04-15 security remediation plan:

  * F-002 (CWE-613): pending-MFA session state has no time limit.
    Closed by ``_mfa_pending_at`` + ``_MFA_PENDING_MAX_AGE`` in
    ``app/routes/auth/_helpers.py``.

  * F-003 (CWE-613): backup-code consumption does not invalidate
    other sessions.  Closed by the ``invalidate_other_sessions``
    call inside the backup-code branch of :func:`mfa_verify`.

  * F-032 (CWE-613): MFA disable does not invalidate other sessions.
    Closed by the ``invalidate_other_sessions`` call at the end of
    :func:`mfa_disable_confirm`.

Tests in this file run two test clients side-by-side -- one
representing a "compromised" session that should be terminated, the
other representing the user's new trusted session.  A regression on
any of the three findings would leave the compromised session alive,
which these tests detect by GETting a protected page from the
compromised client and asserting a 302 to /login.

Why adversarial, not unit:  the C-08 contract is a composition of
``users.session_invalidated_at``, the cookie's
``_session_created_at``, the helper, and ``app.load_user``.  A unit
test can verify each piece in isolation (and ``test_session_helpers.py``
does), but only an end-to-end multi-client test catches a regression
where the pieces are correct individually but wired together wrong.
"""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult


_KNOWN_TOTP_SECRET = "JBSWY3DPEHPK3PXP"
_KNOWN_BACKUP_CODES = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]


def _enable_mfa(user_id, codes=None):
    """Persist an enabled MFA config with a known secret and codes.

    Mirrors the helper in
    ``tests/test_routes/test_auth.py::TestMfaLogin._enable_mfa`` but
    free-standing (this file does not subclass ``TestMfaLogin``).

    Args:
        user_id: The :attr:`User.id` to attach the config to.
        codes: List of plaintext backup codes to hash and store.
            Defaults to ``_KNOWN_BACKUP_CODES``; tests that need
            distinct codes for cross-client races pass their own.

    Returns:
        Tuple ``(plaintext_secret, plaintext_codes)`` for use by the
        caller (the secret is needed only by tests that decrypt and
        compare; most tests can ignore it).
    """
    if codes is None:
        codes = list(_KNOWN_BACKUP_CODES)
    config = MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(_KNOWN_TOTP_SECRET),
        backup_codes=mfa_service.hash_backup_codes(codes),
    )
    db.session.add(config)
    db.session.commit()
    return _KNOWN_TOTP_SECRET, codes


def _login_with_totp(app, email, password):
    """Run the two-step MFA login (password -> TOTP) on a fresh client.

    Returns the test client at the end of a successful login so the
    caller can reuse it for further requests.  ``mfa_service.
    verify_totp_code`` is left intact -- the caller is expected to
    have monkeypatched it before calling this helper, since both
    branches of the test (TOTP-only and backup-code) need to control
    that function's return value.
    """
    client = app.test_client()
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 302, (
        f"Setup error: password POST returned {resp.status_code}"
    )
    resp = client.post("/mfa/verify", data={"totp_code": "123456"})
    assert resp.status_code == 302, (
        f"Setup error: /mfa/verify TOTP returned {resp.status_code}; "
        f"location={resp.headers.get('Location')!r}"
    )
    return client


def _login_with_backup_code(app, email, password, backup_code):
    """Run the two-step MFA login (password -> backup code) on a fresh client.

    Returns the test client.  Unlike ``_login_with_totp``, this helper
    does NOT depend on a monkeypatched verify_totp_code -- backup-code
    verification uses bcrypt directly via mfa_service, which the
    fast_bcrypt fixture already accelerates.
    """
    client = app.test_client()
    resp = client.post("/login", data={"email": email, "password": password})
    assert resp.status_code == 302, (
        f"Setup error: password POST returned {resp.status_code}"
    )
    resp = client.post("/mfa/verify", data={"backup_code": backup_code})
    assert resp.status_code == 302, (
        f"Setup error: /mfa/verify backup-code returned {resp.status_code}; "
        f"location={resp.headers.get('Location')!r}"
    )
    return client


# ---------------------------------------------------------------------------
# F-002: pending-MFA timestamp + 5-minute timeout
# ---------------------------------------------------------------------------


class TestPendingMfaTimeout:
    """The /login -> /mfa/verify window is bounded by _MFA_PENDING_MAX_AGE."""

    def test_login_stamps_mfa_pending_at_when_mfa_required(
        self, app, client, seed_user
    ):
        """POST /login with MFA-enabled user puts ``_mfa_pending_at``
        in the session.

        Without this stamp the freshness check on /mfa/verify would
        be a no-op and F-002 would re-open.  Assert the key exists,
        is a string, parses as ISO-8601, and the parsed value is
        within the wall-clock window of the request.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)

            before = datetime.now(timezone.utc)
            resp = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            after = datetime.now(timezone.utc)
            assert resp.status_code == 302
            assert "/mfa/verify" in resp.headers.get("Location", "")

            with client.session_transaction() as sess:
                raw = sess.get("_mfa_pending_at")
                assert raw is not None, (
                    "Login must stamp _mfa_pending_at when MFA is "
                    "required (F-002)."
                )
                assert isinstance(raw, str)
                stamped = datetime.fromisoformat(raw)
                assert before <= stamped <= after, (
                    f"_mfa_pending_at must be set to a now() in the "
                    f"request window; got {stamped} not in "
                    f"[{before}, {after}]"
                )

    def test_login_without_mfa_does_not_stamp_pending_at(
        self, app, client, seed_user,
    ):
        """A successful one-step login (no MFA) leaves no pending-MFA
        timestamp in the session.

        Belt-and-braces: catches a regression where the timestamp is
        unconditionally written, which would put the timestamp in
        every authenticated user's cookie for no reason.
        """
        # pylint: disable=unused-argument  # seed_user creates the
        # auth.users row that the /login POST below authenticates
        # against; not referenced directly in the test body.
        with app.app_context():
            # No MFA enabled for seed_user.
            resp = client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            assert resp.status_code == 302

            with client.session_transaction() as sess:
                assert "_mfa_pending_at" not in sess

    def test_post_mfa_verify_rejects_stale_pending(
        self, app, client, seed_user, monkeypatch
    ):
        """POST /mfa/verify with a >5 min old pending state redirects
        to /login and clears every pending key.

        This is the canonical F-002 attack:  an attacker captured the
        session cookie 6 minutes ago.  They submit a guessed/captured
        TOTP code now.  Without the timeout the route would accept it.
        With the timeout it must:

          1. Redirect to /login (not validate the code).
          2. Clear _mfa_pending_user_id, _next, _at so a
             second attempt with the same cookie also fails.
          3. Flash a user-visible reason for the bounce.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Step 1: legitimate password POST establishes pending.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            # Step 2: rewind _mfa_pending_at to 6 minutes ago so the
            # freshness check rejects without our needing to wait.
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with client.session_transaction() as sess:
                sess["_mfa_pending_at"] = stale

            # Step 3: attacker submits TOTP -- must be rejected.
            resp = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

            # Pending state fully cleared so a second attempt with
            # the same cookie cannot continue.
            with client.session_transaction() as sess:
                for key in (
                    "_mfa_pending_user_id",
                    "_mfa_pending_next",
                    "_mfa_pending_at",
                ):
                    assert key not in sess, (
                        f"Stale-pending rejection must clear {key!r}; "
                        f"still present as {sess[key]!r}."
                    )

            # User did NOT actually authenticate.
            check = client.get("/dashboard", follow_redirects=False)
            assert check.status_code == 302
            assert "/login" in check.headers.get("Location", "")

    def test_get_mfa_verify_rejects_stale_pending(
        self, app, client, seed_user
    ):
        """GET /mfa/verify on a stale pending state also bounces.

        Defence-in-depth coverage of GET, in addition to the POST
        coverage above.  A user who paused for 6 minutes on the
        verify form should be redirected with a flash on the next
        page load, not allowed to type a code into a form that the
        POST handler will then reject.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })

            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with client.session_transaction() as sess:
                sess["_mfa_pending_at"] = stale

            resp = client.get("/mfa/verify", follow_redirects=False)
            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

    def test_fresh_pending_is_accepted(
        self, app, client, seed_user, monkeypatch
    ):
        """A pending state under 5 minutes old completes login normally.

        Boundary check on the freshness window.  Without this control
        test, a regression that always returned False from
        ``_mfa_pending_is_fresh`` would still pass the staleness
        rejection tests above (every pending state would be rejected,
        including freshly-stamped ones).
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

            # Pending state is fresh by construction (just stamped).
            resp = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert resp.status_code == 302
            assert "/login" not in resp.headers.get("Location", "")

            check = client.get("/dashboard")
            assert check.status_code == 200

    def test_missing_pending_at_is_rejected(
        self, app, client, seed_user, monkeypatch
    ):
        """An ``_mfa_pending_at``-less pending state is treated as stale.

        Pre-C-08 sessions and tampered cookies both surface as a
        pending state with no timestamp.  Fail-closed: reject and
        force a fresh /login rather than honour a state we cannot
        date-bound.
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
                # Strip the timestamp; keep the user_id and remember
                # so we still have a "pending state" that the
                # _mfa_pending_user_id guard does not reject first.
                sess.pop("_mfa_pending_at", None)

            resp = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

    def test_malformed_pending_at_is_rejected(
        self, app, client, seed_user, monkeypatch
    ):
        """A non-ISO-8601 ``_mfa_pending_at`` is treated as stale.

        ``datetime.fromisoformat`` raises ValueError on garbage.  The
        helper catches it and returns False -- without that, a
        tampered cookie could 500 the route and leak a stack trace.
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
                sess["_mfa_pending_at"] = "not-a-real-iso-timestamp"

            resp = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

    def test_future_dated_pending_at_is_rejected(
        self, app, client, seed_user, monkeypatch
    ):
        """A future-dated ``_mfa_pending_at`` is treated as stale.

        Only reachable via a forged cookie (Flask signs but does not
        encrypt the cookie -- forgery requires SECRET_KEY) or via a
        backwards clock jump on the server.  Either way the value
        cannot be trusted; reject and force a fresh password step.
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

            future = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat()
            with client.session_transaction() as sess:
                sess["_mfa_pending_at"] = future

            resp = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")

    def test_naive_pending_at_is_rejected(
        self, app, client, seed_user, monkeypatch
    ):
        """A timezone-naive ``_mfa_pending_at`` is treated as stale.

        A naive datetime would raise TypeError on the timezone-aware
        subtraction in :func:`_mfa_pending_is_fresh`.  The helper
        rejects naive values explicitly so the failure mode is "log
        in again" instead of "500 with traceback."
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

            naive = datetime.now().replace(tzinfo=None).isoformat()
            with client.session_transaction() as sess:
                sess["_mfa_pending_at"] = naive

            resp = client.post("/mfa/verify", data={
                "totp_code": "123456",
            }, follow_redirects=False)

            assert resp.status_code == 302
            assert "/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# F-003: backup-code consume invalidates other sessions
# ---------------------------------------------------------------------------


class TestBackupCodeInvalidatesOtherSessions:
    """The session that consumed a backup code survives; others die."""

    def test_other_session_logged_out_after_backup_code_consume(
        self, app, seed_user, monkeypatch,
    ):
        """A second client logged in as the same user is forced to
        re-authenticate after Client A consumes a backup code.

        Threat scenario reified by F-003: an attacker has the user's
        password and is mid-session on the user's lost device
        (Client A).  The user logs in on a trusted device using a
        backup code (Client B).  Backup-code consumption MUST
        terminate Client A's session immediately -- otherwise the
        attacker continues with full access on the lost device.

        Setup: enable MFA, log in Client A via TOTP (the existing
        compromised session), confirm A is authenticated, then log
        in Client B via a backup code, confirm B is authenticated,
        then re-check A and assert it is now redirected to /login.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Client A: existing session (pre-compromise scenario).
            client_a = _login_with_totp(
                app, "test@shekel.local", "testpass",
            )
            check_a = client_a.get("/dashboard")
            assert check_a.status_code == 200, (
                "Setup error: Client A must be authenticated before "
                "Client B's backup-code login."
            )

            # Client B: new session via backup code.
            client_b = _login_with_backup_code(
                app, "test@shekel.local", "testpass",
                _KNOWN_BACKUP_CODES[0],
            )
            check_b = client_b.get("/dashboard")
            assert check_b.status_code == 200, (
                "Client B's backup-code login should succeed."
            )

            # Client A: must now be invalidated.
            recheck_a = client_a.get("/dashboard", follow_redirects=False)
            assert recheck_a.status_code == 302, (
                "Backup-code consumption did NOT invalidate Client A's "
                f"session; got status {recheck_a.status_code}.  F-003 "
                "regression."
            )
            assert "/login" in recheck_a.headers.get("Location", ""), (
                "Client A should redirect to /login after invalidation."
            )

    def test_session_invalidated_at_advanced_after_backup_code(
        self, app, seed_user,
    ):
        """The DB column is the load-bearing artefact; lock it down
        directly.

        The cross-client test above covers behaviour; this test
        covers the underlying mechanism so a regression that broke
        the helper (e.g. removed the commit) is detected even if a
        bug elsewhere kept the cross-client test green.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            user_id = seed_user["user"].id

            before = datetime.now(timezone.utc)
            _login_with_backup_code(
                app, "test@shekel.local", "testpass",
                _KNOWN_BACKUP_CODES[0],
            )
            after = datetime.now(timezone.utc)

            # Reload the user row.  The DB session must be expired
            # because the autouse db fixture caches the seed_user
            # instance from before the request.
            db.session.expire_all()
            from app.models.user import User  # pylint: disable=import-outside-toplevel
            reloaded = db.session.get(User, user_id)
            assert reloaded.session_invalidated_at is not None, (
                "Backup-code consume must stamp session_invalidated_at."
            )
            assert before <= reloaded.session_invalidated_at <= after, (
                "session_invalidated_at must be set within the request "
                f"window; got {reloaded.session_invalidated_at} not in "
                f"[{before}, {after}]"
            )

    def test_totp_verify_does_not_invalidate_other_sessions(
        self, app, seed_user, monkeypatch,
    ):
        """A normal TOTP login does NOT bump session_invalidated_at.

        Negative control: the F-003 fix targets backup-code paths
        specifically.  An ordinary TOTP login should not invalidate
        the user's other sessions because the authenticator app is
        assumed available and uncompromised.  Without this control,
        a regression that fired the helper on every MFA verify
        (TOTP + backup) would still pass the F-003 cross-client test.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )
            user_id = seed_user["user"].id

            # Establish a baseline by logging in via TOTP.
            _login_with_totp(app, "test@shekel.local", "testpass")

            db.session.expire_all()
            from app.models.user import User  # pylint: disable=import-outside-toplevel
            reloaded = db.session.get(User, user_id)
            assert reloaded.session_invalidated_at is None, (
                "TOTP login must NOT bump session_invalidated_at; "
                "backup codes are the canonical 'lost device' signal "
                "and the bump is reserved for that case."
            )

    def test_backup_code_consume_current_session_survives(
        self, app, seed_user,
    ):
        """The client that USED the backup code stays authenticated.

        Pairs with ``test_other_session_logged_out_after_backup_code_consume``
        above: A is logged out, B (the backup-code user) is NOT.  The
        single-``now`` invariant in the helper (DB column equal to
        cookie value, strict-less-than comparison in load_user) is
        the load-bearing detail this test pins down.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)

            client = _login_with_backup_code(
                app, "test@shekel.local", "testpass",
                _KNOWN_BACKUP_CODES[0],
            )

            # Multiple subsequent requests must all succeed -- the
            # cookie this client owns must be valid against the bumped
            # column on every load_user call.
            r1 = client.get("/dashboard")
            assert r1.status_code == 200, (
                f"Backup-code-consuming client survived its own bump? "
                f"got {r1.status_code}"
            )
            r2 = client.get("/dashboard")
            assert r2.status_code == 200


# ---------------------------------------------------------------------------
# F-032: MFA disable invalidates other sessions
# ---------------------------------------------------------------------------


class TestMfaDisableInvalidatesOtherSessions:
    """``mfa_disable_confirm`` is an auth-factor state change."""

    def test_other_session_logged_out_after_mfa_disable(
        self, app, seed_user, monkeypatch,
    ):
        """A parallel session is forced out when MFA is disabled.

        Threat scenario per F-032: the user disables MFA because they
        suspect a session is compromised.  The disable itself does
        nothing to the suspect session unless we also bump
        session_invalidated_at -- exactly what the helper does at
        the end of mfa_disable_confirm.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            # Client A: the (potentially compromised) parallel session.
            client_a = _login_with_totp(
                app, "test@shekel.local", "testpass",
            )
            assert client_a.get("/dashboard").status_code == 200

            # Client B: the user disabling MFA from a trusted device.
            client_b = _login_with_totp(
                app, "test@shekel.local", "testpass",
            )
            disable_resp = client_b.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": "123456",
            })
            assert disable_resp.status_code == 302

            # Client A: must now be invalidated.
            recheck_a = client_a.get("/dashboard", follow_redirects=False)
            assert recheck_a.status_code == 302, (
                "MFA disable did NOT invalidate Client A's session; "
                f"got status {recheck_a.status_code}.  F-032 regression."
            )
            assert "/login" in recheck_a.headers.get("Location", "")

    def test_disabling_session_survives_mfa_disable(
        self, app, seed_user, monkeypatch,
    ):
        """The session that issued the MFA disable stays authenticated.

        Same single-``now`` invariant as the backup-code path:  the
        helper sets the DB column and the cookie to the same instant
        so the strict-less-than comparison in load_user lets the
        current session survive.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )

            client = _login_with_totp(
                app, "test@shekel.local", "testpass",
            )

            disable_resp = client.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": "123456",
            })
            assert disable_resp.status_code == 302

            # The client that disabled MFA must still be able to
            # access protected pages.
            r = client.get("/dashboard")
            assert r.status_code == 200, (
                "MFA-disabling client lost its own session; got "
                f"status {r.status_code}.  Single-now invariant in "
                "invalidate_other_sessions has regressed."
            )

    def test_session_invalidated_at_advanced_after_mfa_disable(
        self, app, seed_user, monkeypatch,
    ):
        """The DB column is bumped by mfa_disable_confirm.

        Direct-to-DB check on the load-bearing artefact, parallel to
        ``test_session_invalidated_at_advanced_after_backup_code``
        above.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            monkeypatch.setattr(
                mfa_service, "verify_totp_code",
                lambda mc, c: TotpVerificationResult.ACCEPTED,
            )
            user_id = seed_user["user"].id

            client = _login_with_totp(
                app, "test@shekel.local", "testpass",
            )

            before = datetime.now(timezone.utc)
            client.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": "123456",
            })
            after = datetime.now(timezone.utc)

            db.session.expire_all()
            from app.models.user import User  # pylint: disable=import-outside-toplevel
            reloaded = db.session.get(User, user_id)
            assert reloaded.session_invalidated_at is not None
            assert before <= reloaded.session_invalidated_at <= after

    def test_mfa_disable_failed_password_does_not_invalidate(
        self, app, seed_user,
    ):
        """Failed MFA disable (wrong password) does NOT bump the column.

        Negative control:  the helper must run only on a SUCCESSFUL
        disable, not on any POST to /mfa/disable.  A regression that
        ran the helper before the password check would let an
        attacker who has only a session cookie (no password) force-
        log-out the user's other sessions.
        """
        with app.app_context():
            _enable_mfa(seed_user["user"].id)
            user_id = seed_user["user"].id

            client = _login_with_backup_code(
                app, "test@shekel.local", "testpass",
                _KNOWN_BACKUP_CODES[0],
            )
            # The backup-code login already bumped the column;
            # capture the value so we can prove the failed disable
            # below does NOT bump it again.
            db.session.expire_all()
            from app.models.user import User  # pylint: disable=import-outside-toplevel
            stamp_before = db.session.get(
                User, user_id,
            ).session_invalidated_at
            assert stamp_before is not None

            resp = client.post("/mfa/disable", data={
                "current_password": "WRONG_PASSWORD",
                "totp_code": "123456",
            })
            # Wrong password -> 302 to /mfa/disable (not a 500).
            assert resp.status_code == 302

            db.session.expire_all()
            stamp_after = db.session.get(
                User, user_id,
            ).session_invalidated_at
            assert stamp_after == stamp_before, (
                "Failed MFA-disable must NOT bump session_invalidated_at; "
                "the helper must only run on a successful disable."
            )


# ---------------------------------------------------------------------------
# Cross-cutting regressions
# ---------------------------------------------------------------------------


class TestExistingInvariantsPreserved:
    """C-08 must not regress the password-change and explicit-logout
    paths that already implement the same invalidation pattern inline.

    These tests are belt-and-braces:  the inline implementations in
    change_password and invalidate_sessions were not refactored to
    use the new helper (out of scope for C-08), but their semantics
    are part of the same C-08 contract and a future refactor onto
    the helper must preserve them.
    """

    def test_password_change_still_invalidates_sessions(
        self, app, auth_client, seed_user
    ):
        """``change_password`` continues to bump session_invalidated_at.

        Locks down the existing pattern in
        ``app/routes/auth/session_security.py:change_password`` so a future refactor
        onto :func:`invalidate_other_sessions` cannot silently drop
        the bump (e.g. by forgetting the helper call).
        """
        with app.app_context():
            from app.models.user import User  # pylint: disable=import-outside-toplevel

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/change-password", data={
                "current_password": "testpass",
                "new_password": "newpassword12",
                "confirm_password": "newpassword12",
            })
            after = datetime.now(timezone.utc)
            assert resp.status_code == 302

            db.session.expire_all()
            reloaded = db.session.get(User, seed_user["user"].id)
            assert reloaded.session_invalidated_at is not None
            assert before <= reloaded.session_invalidated_at <= after

    def test_explicit_invalidate_sessions_still_works(
        self, app, auth_client, seed_user
    ):
        """``/invalidate-sessions`` continues to bump the column.

        Same protection as the password-change test, for the
        explicit user-initiated "log out all other sessions" route.
        """
        with app.app_context():
            from app.models.user import User  # pylint: disable=import-outside-toplevel

            before = datetime.now(timezone.utc)
            resp = auth_client.post("/invalidate-sessions")
            after = datetime.now(timezone.utc)
            assert resp.status_code == 302

            db.session.expire_all()
            reloaded = db.session.get(User, seed_user["user"].id)
            assert reloaded.session_invalidated_at is not None
            assert before <= reloaded.session_invalidated_at <= after
