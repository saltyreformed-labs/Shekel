"""C-10's step-up tests (F-045): login stamps the fresh-login time, fresh_login_required redirects
a stale session to /reauth, and /reauth, a password change and a session invalidation each move
or keep the stamps they should.

The seed owner is made with two-factor (plan#3600), and its shared sign-in
(``auth_client``, ``tests._seed_owner_fixtures.log_in_seed_user``) is on a
browser it trusts and types no code.  So ``/reauth`` asks for the owner's code
too, and a test types the current step's (:func:`_reauth_form`).
"""

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.account import Account
from app.services import mfa_service
from app.services.mfa_service import TotpVerificationResult
from app.utils.bearer_tokens import token_hash
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from tests._seed_owner_fixtures import SEED_USER_PASSWORD, log_in_seed_user
from tests._session_copies import sign_ins_of, token_of
from tests._two_factor_sign_in import code_now, two_factor_secret_of


def _reauth_form(user_id):
    """Return the ``/reauth`` form *user_id* fills in: the right password and code.

    Args:
        user_id: The seed owner's id, whose two-factor secret the code is for.

    Returns:
        The form's fields, for ``client.post("/reauth", data=...)``.
    """
    return {
        "password": SEED_USER_PASSWORD,
        "totp_code": code_now(two_factor_secret_of(user_id)),
    }


# ---------------------------------------------------------------------------
# F-045: fresh_login_required + /reauth
# ---------------------------------------------------------------------------


class TestFreshLoginAtSetOnLogin:
    """Login flows must stamp ``_fresh_login_at`` so the auth_client
    fixture and freshly-logged-in users can immediately exercise
    high-value routes without an extra reauth round-trip.
    """

    def test_login_with_no_code_sets_fresh_login_at(
        self, app, client, seed_user,
    ):
        """A login with no code, on a browser the owner trusts, writes ``_fresh_login_at``."""
        with app.app_context():
            before = datetime.now(timezone.utc)
            log_in_seed_user(client)
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

    def test_post_correct_password_and_code_refreshes_fresh_login(
        self, app, auth_client, seed_user,
    ):
        """A correct password and code POST updates ``_fresh_login_at``.

        The whole point of /reauth: typing the password and the code again
        proves identity and restarts the step-up window.
        """
        with app.app_context():
            stale = (
                datetime.now(timezone.utc) - timedelta(minutes=6)
            ).isoformat()
            with auth_client.session_transaction() as sess:
                sess[FRESH_LOGIN_AT_KEY] = stale

            before = datetime.now(timezone.utc)
            resp = auth_client.post(
                "/reauth", data=_reauth_form(seed_user["user"].id),
                follow_redirects=False,
            )
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
        self, app, auth_client, seed_user,
    ):
        """A safe ``next`` param in the URL is honoured on success."""
        with app.app_context():
            resp = auth_client.post(
                "/reauth?next=%2Fdashboard",
                data=_reauth_form(seed_user["user"].id),
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers.get("Location", "").endswith("/dashboard")

    def test_post_rejects_unsafe_next(
        self, app, auth_client, seed_user,
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
                data=_reauth_form(seed_user["user"].id),
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


class TestReauthIsNotASignIn:
    """``/reauth`` MUST NOT touch the session's sign-in.

    The motivation: a re-confirm is not a sign-in (ruling plan#3069), so it
    neither renews the session's token nor records a new sign-in, whose
    thirty days would start again.  The stamp_reauth_session helper is
    structurally split from stamp_login_session for exactly this reason;
    this test locks down the contract end-to-end.
    """

    def test_reauth_leaves_the_token_and_the_record(
        self, app, auth_client, seed_user,
    ):
        """The session's token, and the user's one record, are unchanged."""
        with app.app_context():
            user_id = seed_user["user"].id
            before = token_of(auth_client)
            assert sign_ins_of(user_id) == {token_hash(before)}

            resp = auth_client.post(
                "/reauth", data=_reauth_form(user_id),
            )
            assert resp.status_code == 302

            assert token_of(auth_client) == before
            assert sign_ins_of(user_id) == {token_hash(before)}


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
