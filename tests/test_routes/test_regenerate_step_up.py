"""
Shekel Budget App -- Regenerate backup codes takes a step-up.

New codes void the old ones, so a stolen session must not be able to mint them.
The route takes the same re-confirm screen as deleting an account (ruling
plan#3010): past the fresh-login window it sends the person to ``/reauth``, they
land on the dashboard, and they press Regenerate again.
"""

import time
from datetime import datetime, timedelta, timezone

import pyotp

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY

_PASSWORD = "testpass"
_ORIGINAL_CODE = "abcdef0123456789abcdef012345"
_ROUTE = "/mfa/regenerate-backup-codes"


def _enable_mfa(user_id: int) -> str:
    """Enable two-factor for *user_id* with one known backup code.

    Args:
        user_id: The account.

    Returns:
        The plaintext secret.
    """
    secret = mfa_service.generate_totp_secret()
    db.session.add(MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(secret),
        backup_codes=mfa_service.hash_backup_codes([_ORIGINAL_CODE]),
        confirmed_at=datetime.now(timezone.utc),
    ))
    db.session.commit()
    return secret


def _stored_codes(user_id: int) -> list[str]:
    """Return the backup-code hashes the database holds for *user_id*.

    Args:
        user_id: The account.

    Returns:
        The stored hashes.
    """
    db.session.expire_all()
    return (
        db.session.query(MfaConfig).filter_by(user_id=user_id).one().backup_codes
    )


def _make_session_stale(client) -> None:
    """Put *client*'s last password check outside the fresh-login window.

    Args:
        client: The signed-in test client.
    """
    stale = datetime.now(timezone.utc) - timedelta(minutes=6)
    with client.session_transaction() as session:
        session[FRESH_LOGIN_AT_KEY] = stale.isoformat()


class TestRegenerateTakesAStepUp:
    """A session past the window cannot mint codes; a fresh one can."""

    def test_a_stale_session_is_sent_to_reauth_and_the_codes_stay(
        self, app, auth_client, seed_user,
    ):
        """Past the window: to /reauth, the old codes intact, no security event."""
        with app.app_context():
            user_id = seed_user["user"].id
            _enable_mfa(user_id)
            before = _stored_codes(user_id)
            _make_session_stale(auth_client)

            response = auth_client.post(_ROUTE)

            assert response.status_code == 302
            assert "/reauth" in response.headers["Location"]
            assert _stored_codes(user_id) == before
            assert db.session.get(User, user_id).last_security_event_kind is None

    def test_a_session_with_no_stamp_is_sent_to_reauth(
        self, app, auth_client, seed_user,
    ):
        """No fresh-login stamp at all is treated as stale."""
        with app.app_context():
            user_id = seed_user["user"].id
            _enable_mfa(user_id)
            before = _stored_codes(user_id)
            with auth_client.session_transaction() as session:
                session.pop(FRESH_LOGIN_AT_KEY, None)

            response = auth_client.post(_ROUTE)

            assert response.status_code == 302
            assert "/reauth" in response.headers["Location"]
            assert _stored_codes(user_id) == before

    def test_an_htmx_caller_gets_the_redirect_header(
        self, app, auth_client, seed_user,
    ):
        """HTMX swaps bodies, so the redirect is a 204 and a header, codes intact."""
        with app.app_context():
            user_id = seed_user["user"].id
            _enable_mfa(user_id)
            before = _stored_codes(user_id)
            _make_session_stale(auth_client)

            response = auth_client.post(_ROUTE, headers={"HX-Request": "true"})

            assert response.status_code == 204
            assert "/reauth" in response.headers["HX-Redirect"]
            assert _stored_codes(user_id) == before

    def test_a_fresh_session_regenerates(self, app, auth_client, seed_user):
        """Just signed in: the page shows new codes and the old one is void."""
        with app.app_context():
            user_id = seed_user["user"].id
            _enable_mfa(user_id)
            before = _stored_codes(user_id)

            response = auth_client.post(_ROUTE)

            assert response.status_code == 200
            after = _stored_codes(user_id)
            assert after != before
            # Ten codes: mfa_service.generate_backup_codes's default count.
            assert len(after) == 10
            assert mfa_service.verify_backup_code(_ORIGINAL_CODE, after) < 0

    def test_after_the_reconfirm_the_person_lands_on_the_dashboard_and_can_regenerate(
        self, app, auth_client, seed_user,
    ):
        """What the person actually does: bounced, re-confirm, dashboard, press again.

        ``fresh_login_required`` sends an absolute ``next``, which ``/reauth``'s
        open-redirect check refuses, so they land on the dashboard rather than
        back on a route that only answers POST.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _enable_mfa(user_id)
            before = _stored_codes(user_id)
            _make_session_stale(auth_client)

            bounced = auth_client.post(_ROUTE)
            assert "/reauth" in bounced.headers["Location"]
            assert _stored_codes(user_id) == before

            confirmed = auth_client.post(bounced.headers["Location"], data={
                "password": _PASSWORD,
                "totp_code": pyotp.TOTP(secret).at(int(time.time())),
            })
            assert confirmed.status_code == 302
            assert confirmed.headers["Location"].endswith("/dashboard")

            response = auth_client.post(_ROUTE)
            assert response.status_code == 200
            assert _stored_codes(user_id) != before
