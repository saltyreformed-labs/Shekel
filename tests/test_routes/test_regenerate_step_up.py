"""
Shekel Budget App -- Regenerate backup codes takes a step-up.

New codes void the old ones, so a stolen session must not be able to mint them.
The route takes the same re-confirm screen as deleting an account (ruling
plan#3010): past the fresh-login window it sends the person to ``/reauth``, they
land on the dashboard, and they press Regenerate again.

That screen takes no backup code, so someone without their phone can replace
their codes only inside the window a sign-in opens.  A backup-code sign-in says
so, with the count left (ruling plan#3035).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY
from tests._test_helpers import store_backup_codes
from tests._two_factor_sign_in import code_now, two_factor_secret_of

_EMAIL = "test@shekel.local"
_PASSWORD = "testpass"
_ORIGINAL_CODE = "abcdef0123456789abcdef012345"
_ROUTE = "/mfa/regenerate-backup-codes"
_THREE_CODES = (
    "abcdef0123456789abcdef012345",
    "bcdef0123456789abcdef0123456",
    "cdef0123456789abcdef01234567",
)
_BACKUP_CODE_LINE = "You signed in with a backup code"


def _its_two_factor(user_id: int, codes: tuple[str, ...] = (_ORIGINAL_CODE,)) -> str:
    """Give a shared account's two-factor known backup codes; return its secret.

    The account is made with two-factor (plan#3600), so its secret is its
    row's.

    Args:
        user_id: The account.
        codes: The plaintext backup codes it holds; one known code by default.

    Returns:
        The plaintext secret.
    """
    store_backup_codes(db.session, user_id, codes)
    return two_factor_secret_of(user_id)


def _sign_in_with(client, code_field: str, code: str):
    """Pass the password step, then post *code* in *code_field* at the code form.

    Args:
        client: A signed-out test client.
        code_field: ``backup_code`` or ``totp_code``.
        code: The code typed.

    Returns:
        The page the finished sign-in lands on, its redirect followed.
    """
    password_step = client.post(
        "/login", data={"email": _EMAIL, "password": _PASSWORD},
    )
    assert "/mfa/verify" in password_step.headers["Location"]
    code_step = client.post("/mfa/verify", data={code_field: code})
    assert code_step.status_code == 302
    assert code_step.headers["Location"].endswith("/dashboard")
    return client.get(code_step.headers["Location"])


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
            _its_two_factor(user_id)
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
            _its_two_factor(user_id)
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
            _its_two_factor(user_id)
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
            _its_two_factor(user_id)
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
            secret = _its_two_factor(user_id)
            before = _stored_codes(user_id)
            _make_session_stale(auth_client)

            bounced = auth_client.post(_ROUTE)
            assert "/reauth" in bounced.headers["Location"]
            assert _stored_codes(user_id) == before

            confirmed = auth_client.post(bounced.headers["Location"], data={
                "password": _PASSWORD,
                "totp_code": code_now(secret),
            })
            assert confirmed.status_code == 302
            assert confirmed.headers["Location"].endswith("/dashboard")

            response = auth_client.post(_ROUTE)
            assert response.status_code == 200
            assert _stored_codes(user_id) != before


class TestABackupCodeSignInSaysHowLongToReplaceThem:
    """The one line a backup-code sign-in adds, and an authenticator sign-in does not."""

    @pytest.mark.parametrize(("window", "spelled"), [
        (7, "7 minutes"),
        (1, "1 minute"),
    ])
    def test_it_names_the_codes_left_and_the_configured_window(
        self, app, client, seed_user, monkeypatch, window, spelled,
    ):
        """Three codes, one spent: two left, inside the window Regenerate reads.

        The window is set away from five, so a second spelling of five shows.
        """
        monkeypatch.setitem(app.config, "FRESH_LOGIN_MAX_AGE_MINUTES", window)
        with app.app_context():
            user_id = seed_user["user"].id
            _its_two_factor(user_id, codes=_THREE_CODES)

            landed = _sign_in_with(client, "backup_code", _THREE_CODES[0])

            assert landed.status_code == 200
            assert len(_stored_codes(user_id)) == 2
            assert (
                f"{_BACKUP_CODE_LINE} and have 2 left. For the next {spelled} "
                "you can replace them on the Security page without your "
                "authenticator app."
            ) in landed.get_data(as_text=True)

    def test_the_last_code_says_none_are_left(self, app, client, seed_user):
        """One code, spent: the line says none are left, the case that matters most."""
        with app.app_context():
            user_id = seed_user["user"].id
            _its_two_factor(user_id)

            landed = _sign_in_with(client, "backup_code", _ORIGINAL_CODE)

            assert landed.status_code == 200
            assert _stored_codes(user_id) == []
            assert (
                f"{_BACKUP_CODE_LINE} and have 0 left."
            ) in landed.get_data(as_text=True)

    def test_the_window_it_names_is_the_one_regenerate_honours(
        self, app, client, seed_user,
    ):
        """Inside the window the line names, Regenerate needs no re-confirm."""
        with app.app_context():
            user_id = seed_user["user"].id
            _its_two_factor(user_id, codes=_THREE_CODES)
            _sign_in_with(client, "backup_code", _THREE_CODES[0])

            response = client.post(_ROUTE)

            assert response.status_code == 200
            assert len(_stored_codes(user_id)) == 10

    def test_an_authenticator_code_sign_in_adds_no_line(
        self, app, client, seed_user,
    ):
        """The phone is in hand, so there is nothing to warn about."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _its_two_factor(user_id, codes=_THREE_CODES)

            landed = _sign_in_with(
                client, "totp_code", code_now(secret),
            )

            assert landed.status_code == 200
            assert _BACKUP_CODE_LINE not in landed.get_data(as_text=True)
