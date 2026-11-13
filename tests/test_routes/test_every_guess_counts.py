"""
Shekel Budget App -- Every password and code guess counts toward the one lock.

The doors: the code form at sign-in, ``/reauth``, ``/change-password`` and
``/mfa/disable``.  Each counts a wrong answer on the record of the account's
email (ruling plan#3110) through the one function that also trips the lock,
each refuses even a right answer while locked, and only a finished sign-in or
re-confirm clears the count (ruling plan#3007).  The code form also has a
limit of its own by the pending account, so moving between addresses buys no
more guesses.

Every test here is a stranger's, whose guesses count on the account.  A
browser that finished a sign-in is known instead, and counts on its own record
(plan#2772), so the signed-in doors' tests guess from a signed-in stranger: a
browser whose record has gone (``stranger_client``).  The known browser's side
is ``tests/test_adversarial/test_known_browser_sign_in.py``.

The ``/login`` password and its lock have their own tests in
``tests/test_services/`` and ``tests/test_routes/test_auth/test_login_and_logout.py``.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pyotp
import pytest

from app.extensions import db
from app.models.user import MfaConfig, User
from app.routes.auth._helpers import _MFA_PENDING_MAX_AGE
from app.services import auth_service, mfa_service
from app.utils.log_events import (
    EVT_ACCOUNT_LOCKED,
    EVT_LOGIN_FAILED,
    EVT_REAUTH_FAILED,
)
from tests._guess_records import count_of, locked_until_of, set_record
from tests._limited_app import limited_app, through_nginx
from tests._seed_owner_fixtures import OWNERS_PAGE
from tests._sign_in import make_a_stranger
from tests._test_helpers import store_backup_codes
from tests._two_factor_sign_in import (
    code_now, sign_in_from_a_trusted_browser, two_factor_secret_of,
)

_EMAIL = "test@shekel.local"
_PASSWORD = "testpass"
_NEW_PASSWORD = "a-new-password-of-length"
_BACKUP_CODE = "abcdef0123456789abcdef012345"
_LOCKED_MESSAGE = auth_service.LOCKED_OUT_MESSAGE.encode()

# The code form's limit by address and by pending account, in ``mfa.py``.
_CODE_LIMIT = 5

# Fits the form and can match no secret, for a test about the limit, not the code.
_NEVER_A_CODE = "xxxxxx"


def _secret_and_one_backup_code(user_id: int) -> str:
    """Give *user_id*'s two-factor the one backup code :data:`_BACKUP_CODE`; return its secret.

    The account is a shared one, made with two-factor (plan#3600).

    Args:
        user_id: The account.

    Returns:
        The plaintext secret.
    """
    store_backup_codes(db.session, user_id, [_BACKUP_CODE])
    return two_factor_secret_of(user_id)


def _wrong_code(secret: str) -> str:
    """Return a code valid at no step near now, so a test cannot fail on a chance match.

    Args:
        secret: The plaintext secret.

    Returns:
        A six-digit code the verifier's drift window does not hold.
    """
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    near = {totp.at(now + step) for step in (-60, -30, 0, 30, 60)}
    return next(
        code for code in (f"{n:06d}" for n in range(10)) if code not in near
    )


def _reloaded(user_id: int) -> User:
    """Return *user_id*'s row as the database holds it now.

    Args:
        user_id: The account.

    Returns:
        The user.
    """
    db.session.expire_all()
    return db.session.get(User, user_id)


def _count(user_id: int) -> int:
    """Return the count on the record of *user_id*'s email, as the database holds it now."""
    return count_of(_reloaded(user_id).email)


def _locked_until(user_id: int) -> datetime | None:
    """Return when the lock on the record of *user_id*'s email ends, or ``None``."""
    return locked_until_of(_reloaded(user_id).email)


def _set_lock_state(user_id: int, count: int = 0, locked_for: timedelta | None = None):
    """Write a count and optionally a lock window onto the record of *user_id*'s email.

    Args:
        user_id: The account.
        count: The count to store.
        locked_for: How far from now ``locked_until`` falls; ``None`` for no value.
    """
    now = datetime.now(timezone.utc)
    set_record(
        db.session.get(User, user_id).email, count=count,
        locked_until=None if locked_for is None else now + locked_for,
        last_attempt_at=now,
    )
    db.session.commit()


def _type_password(client, **request_arguments):
    """POST the right password at ``/login``, reaching the code form.

    Args:
        client: The test client.
        **request_arguments: Passed to the POST.
    """
    response = client.post(
        "/login", data={"email": _EMAIL, "password": _PASSWORD},
        **request_arguments,
    )
    assert response.status_code == 302
    assert "/mfa/verify" in response.headers["Location"]


class TestTheCodeFormCounts:
    """A wrong answer at ``/mfa/verify`` counts, and a right password does not undo it."""

    def test_a_wrong_code_counts(self, app, client, seed_user):
        """One wrong code is one on the count, and the form stays up to retry."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _type_password(client)

            response = client.post(
                "/mfa/verify", data={"totp_code": _wrong_code(secret)},
            )

            assert response.status_code == 200
            assert b"Invalid verification code" in response.data
            assert _count(user_id) == 1

    def test_a_replayed_code_counts(self, app, client, seed_user):
        """A right code spent already is refused and counted like any other.

        The first browser finishes with the code; the second tries it again.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            code = code_now(secret)
            _type_password(client)
            assert client.post(
                "/mfa/verify", data={"totp_code": code},
            ).status_code == 302
            assert _count(user_id) == 0

            second_browser = app.test_client()
            _type_password(second_browser)
            response = second_browser.post(
                "/mfa/verify", data={"totp_code": code},
            )

            assert b"Invalid verification code" in response.data
            assert _count(user_id) == 1

    def test_a_wrong_backup_code_counts(self, app, client, seed_user):
        """A backup code is a guess at the same second factor."""
        with app.app_context():
            user_id = seed_user["user"].id
            _secret_and_one_backup_code(user_id)
            _type_password(client)

            response = client.post(
                "/mfa/verify", data={"backup_code": "0" * len(_BACKUP_CODE)},
            )

            assert b"Invalid verification code" in response.data
            assert _count(user_id) == 1

    def test_an_empty_form_counts(self, app, client, seed_user):
        """A submit with neither code is refused and counted like a wrong code."""
        with app.app_context():
            user_id = seed_user["user"].id
            _secret_and_one_backup_code(user_id)
            _type_password(client)

            response = client.post("/mfa/verify", data={})

            assert b"Invalid verification code" in response.data
            assert _count(user_id) == 1

    def test_a_form_the_schema_rejects_does_not_count(self, app, client, seed_user):
        """Seven digits cannot be a right code, so it is no guess at one."""
        with app.app_context():
            user_id = seed_user["user"].id
            _secret_and_one_backup_code(user_id)
            _type_password(client)

            response = client.post("/mfa/verify", data={"totp_code": "1234567"})

            assert b"Invalid verification code" in response.data
            assert _count(user_id) == 0

    def test_a_failed_encryption_key_does_not_count(self, app, client, seed_user):
        """The operator's key problem is not the person's guess."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _type_password(client)

            with patch.object(
                mfa_service, "decrypt_secret", side_effect=RuntimeError("no key"),
            ):
                response = client.post(
                    "/mfa/verify", data={"totp_code": _wrong_code(secret)},
                )

            assert response.status_code == 302
            assert _count(user_id) == 0

    def test_a_correct_password_does_not_clear_the_count(
        self, app, client, seed_user,
    ):
        """The ruling's case: re-entering the password between wrong codes clears nothing."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            for attempt in range(3):
                _type_password(client)
                client.post("/mfa/verify", data={"totp_code": _wrong_code(secret)})
                assert _count(user_id) == attempt + 1

            _type_password(client)

            assert _count(user_id) == 3

    def test_wrong_codes_trip_the_lock_despite_reentered_passwords(
        self, app, client, seed_user, monkeypatch, caplog,
    ):
        """The lock fires on code guesses, leaves its log line, and /login then refuses.

        This is what the old reset rule made impossible.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
                for _ in range(3):
                    _type_password(client)
                    client.post(
                        "/mfa/verify", data={"totp_code": _wrong_code(secret)},
                    )

            assert _locked_until(user_id) > datetime.now(timezone.utc)
            assert _count(user_id) == 0
            assert [
                r.user_id for r in caplog.records
                if getattr(r, "event", None) == EVT_ACCOUNT_LOCKED
            ] == [user_id]

            refused = client.post(
                "/login", data={"email": _EMAIL, "password": _PASSWORD},
            )
            assert refused.status_code == 200
            assert b"Invalid email or password" in refused.data

    def test_a_locked_account_refuses_the_right_code(self, app, client, seed_user):
        """A pending sign-in meets the lock: back to /login, signed out, code unspent."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _type_password(client)
            _set_lock_state(user_id, locked_for=timedelta(minutes=10))

            response = client.post(
                "/mfa/verify", data={"totp_code": code_now(secret)},
            )

            assert response.status_code == 302
            assert "/login" in response.headers["Location"]
            page = client.get("/login")
            assert _LOCKED_MESSAGE in page.data
            with client.session_transaction() as session:
                assert "_mfa_pending_user_id" not in session
                assert "_user_id" not in session
            config = db.session.query(MfaConfig).filter_by(user_id=user_id).one()
            db.session.refresh(config)
            assert config.last_totp_timestep is None

    def test_a_finished_sign_in_clears_the_count(self, app, client, seed_user):
        """Every check passed: the count goes back to zero."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _type_password(client)
            client.post("/mfa/verify", data={"totp_code": _wrong_code(secret)})
            assert _count(user_id) == 1

            response = client.post(
                "/mfa/verify", data={"totp_code": code_now(secret)},
            )

            assert response.status_code == 302
            assert _count(user_id) == 0

    def test_a_run_out_lock_stays_until_a_finished_sign_in(
        self, app, client, seed_user,
    ):
        """A window that has ended blocks nothing, and only finishing removes it."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _set_lock_state(user_id, locked_for=timedelta(seconds=-1))

            _type_password(client)
            assert _locked_until(user_id) is not None

            client.post("/mfa/verify", data={"totp_code": code_now(secret)})
            assert _locked_until(user_id) is None


@pytest.fixture(name="stranger_client")
def _stranger_client(auth_client, seed_user):
    """Return a signed-in browser that is a stranger: its record has gone.

    A signed-in browser stays known only while its record lives, which cannot
    age out while the session lives (ruling plan#3069); one whose record ten
    wrong guesses on its cookie deleted, its own or a copy's, is a stranger
    at every door (ruling plan#3034) and counts on the account.

    Args:
        auth_client: The signed-in test client.
        seed_user: Its account.

    Returns:
        *auth_client*, its account holding no record.
    """
    make_a_stranger(seed_user["user"].id)
    return auth_client


class TestReauthCounts:
    """``/reauth`` counts, refuses while locked, and clears when it finishes."""

    def test_a_wrong_password_counts(self, app, stranger_client, seed_user):
        """One wrong password is one on the count."""
        with app.app_context():
            user_id = seed_user["user"].id

            response = stranger_client.post("/reauth", data={"password": "wrong"})

            assert b"Invalid password" in response.data
            assert _count(user_id) == 1

    def test_a_wrong_code_counts(self, app, stranger_client, seed_user):
        """The right password and a wrong code is one on the count."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)

            response = stranger_client.post("/reauth", data={
                "password": _PASSWORD, "totp_code": _wrong_code(secret),
            })

            assert b"Invalid authentication code" in response.data
            assert _count(user_id) == 1

    def test_a_form_the_schema_rejects_does_not_count(
        self, app, stranger_client, seed_user,
    ):
        """No password at all is no guess at one."""
        with app.app_context():
            user_id = seed_user["user"].id

            response = stranger_client.post("/reauth", data={})

            assert b"Invalid password" in response.data
            assert _count(user_id) == 0

    def test_a_failed_encryption_key_does_not_count(
        self, app, stranger_client, seed_user,
    ):
        """The operator's key problem is not the person's guess."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)

            with patch.object(
                mfa_service, "verify_totp_code", side_effect=RuntimeError("no key"),
            ):
                response = stranger_client.post("/reauth", data={
                    "password": _PASSWORD, "totp_code": _wrong_code(secret),
                })

            assert b"encryption key" in response.data
            assert _count(user_id) == 0

    def test_a_locked_account_refuses_the_right_password_without_bcrypt(
        self, app, stranger_client, seed_user,
    ):
        """The lock comes first: the right password is refused and never checked."""
        with app.app_context():
            user_id = seed_user["user"].id
            _set_lock_state(user_id, locked_for=timedelta(minutes=10))

            with patch.object(auth_service, "verify_password") as verify:
                response = stranger_client.post(
                    "/reauth", data={"password": _PASSWORD},
                )

            verify.assert_not_called()
            assert _LOCKED_MESSAGE in response.data
            assert _count(user_id) == 0

    def test_success_clears_the_count_and_a_run_out_lock(
        self, app, stranger_client, seed_user,
    ):
        """A finished re-confirm is every check this account faces: its password and its code."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = two_factor_secret_of(user_id)
            _set_lock_state(
                user_id, count=3, locked_for=timedelta(seconds=-1),
            )

            response = stranger_client.post("/reauth", data={
                "password": _PASSWORD, "totp_code": code_now(secret),
            })

            assert response.status_code == 302
            assert _count(user_id) == 0
            assert _locked_until(user_id) is None

    def test_success_with_two_factor_clears_the_count(
        self, app, stranger_client, seed_user,
    ):
        """With a second factor, the clear waits for the code."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _set_lock_state(user_id, count=3)

            stranger_client.post("/reauth", data={
                "password": _PASSWORD, "totp_code": _wrong_code(secret),
            })
            assert _count(user_id) == 4

            response = stranger_client.post("/reauth", data={
                "password": _PASSWORD, "totp_code": code_now(secret),
            })

            assert response.status_code == 302
            assert _count(user_id) == 0


class TestChangePasswordCounts:
    """``/change-password`` counts and refuses while locked, and never clears."""

    @staticmethod
    def _post(client, current_password):
        """POST the form with *current_password* and a valid new password.

        Args:
            client: The signed-in client.
            current_password: What the person typed as their current password.

        Returns:
            The response, its redirect followed.
        """
        return client.post("/change-password", data={
            "current_password": current_password,
            "new_password": _NEW_PASSWORD,
            "confirm_password": _NEW_PASSWORD,
        }, follow_redirects=True)

    def test_a_wrong_password_counts_and_changes_nothing(
        self, app, stranger_client, seed_user,
    ):
        """One wrong current password is one on the count, and the hash stays."""
        with app.app_context():
            user_id = seed_user["user"].id
            before = _reloaded(user_id).password_hash

            response = self._post(stranger_client, "wrong")

            assert b"Current password is incorrect" in response.data
            assert _count(user_id) == 1
            assert _reloaded(user_id).password_hash == before

    def test_a_locked_account_refuses_the_right_password(
        self, app, stranger_client, seed_user,
    ):
        """The right current password while locked changes nothing."""
        with app.app_context():
            user_id = seed_user["user"].id
            before = _reloaded(user_id).password_hash
            _set_lock_state(user_id, locked_for=timedelta(minutes=10))

            response = self._post(stranger_client, _PASSWORD)

            assert _LOCKED_MESSAGE in response.data
            assert _reloaded(user_id).password_hash == before

    def test_success_does_not_clear_the_count(self, app, stranger_client, seed_user):
        """A password alone is not every check, so the count stays."""
        with app.app_context():
            user_id = seed_user["user"].id
            before = _reloaded(user_id).password_hash
            _set_lock_state(user_id, count=3)

            response = self._post(stranger_client, _PASSWORD)

            assert b"Password changed successfully" in response.data
            assert _reloaded(user_id).password_hash != before
            assert _count(user_id) == 3

    def test_a_form_the_schema_rejects_does_not_count(
        self, app, stranger_client, seed_user,
    ):
        """No current password is no guess at one."""
        with app.app_context():
            user_id = seed_user["user"].id

            stranger_client.post("/change-password", data={
                "new_password": _NEW_PASSWORD,
                "confirm_password": _NEW_PASSWORD,
            })

            assert _count(user_id) == 0


class TestDisableTwoFactorCounts:
    """``/mfa/disable`` counts both answers and refuses while locked (ruling plan#3008)."""

    @staticmethod
    def _still_enabled(user_id: int) -> bool:
        """Return whether two-factor is still on for *user_id*.

        Args:
            user_id: The account.

        Returns:
            True when its config says enabled.
        """
        config = db.session.query(MfaConfig).filter_by(user_id=user_id).one()
        db.session.refresh(config)
        return config.is_enabled

    def test_a_wrong_password_counts(self, app, stranger_client, seed_user):
        """One wrong password is one on the count, and two-factor stays on."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)

            response = stranger_client.post("/mfa/disable", data={
                "current_password": "wrong", "totp_code": code_now(secret),
            }, follow_redirects=True)

            assert b"Invalid password" in response.data
            assert _count(user_id) == 1
            assert self._still_enabled(user_id)

    def test_a_wrong_code_counts(self, app, stranger_client, seed_user):
        """The right password and a wrong code is one on the count."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)

            response = stranger_client.post("/mfa/disable", data={
                "current_password": _PASSWORD,
                "totp_code": _wrong_code(secret),
            }, follow_redirects=True)

            assert b"Invalid authentication code" in response.data
            assert _count(user_id) == 1
            assert self._still_enabled(user_id)

    def test_a_locked_account_refuses_the_right_answers(
        self, app, stranger_client, seed_user,
    ):
        """The right password and code while locked leave two-factor on."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _set_lock_state(user_id, locked_for=timedelta(minutes=10))

            response = stranger_client.post("/mfa/disable", data={
                "current_password": _PASSWORD,
                "totp_code": code_now(secret),
            }, follow_redirects=True)

            assert _LOCKED_MESSAGE in response.data
            assert self._still_enabled(user_id)

    def test_success_does_not_clear_the_count(self, app, stranger_client, seed_user):
        """Clearing is a finished sign-in's or re-confirm's, not this form's."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _set_lock_state(user_id, count=3)

            response = stranger_client.post("/mfa/disable", data={
                "current_password": _PASSWORD,
                "totp_code": code_now(secret),
            }, follow_redirects=True)

            assert b"has been disabled" in response.data
            assert not self._still_enabled(user_id)
            assert _count(user_id) == 3


class TestOneCountAcrossEveryDoor:
    """The doors share one count and one lock, and a lock leaves signed-in pages working."""

    def test_guesses_at_different_doors_add_up_to_one_lock(
        self, app, stranger_client, seed_user, monkeypatch,
    ):
        """Three wrong answers at three doors trip a threshold of three."""
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        with app.app_context():
            user_id = seed_user["user"].id
            _secret_and_one_backup_code(user_id)

            stranger_client.post("/reauth", data={"password": "wrong"})
            stranger_client.post("/change-password", data={
                "current_password": "wrong",
                "new_password": _NEW_PASSWORD,
                "confirm_password": _NEW_PASSWORD,
            })
            assert _count(user_id) == 2
            assert _locked_until(user_id) is None

            stranger_client.post("/mfa/disable", data={
                "current_password": "wrong", "totp_code": _NEVER_A_CODE,
            })

            assert _count(user_id) == 0
            assert _locked_until(user_id) > datetime.now(timezone.utc)

    def test_pages_already_signed_in_keep_working_while_locked(
        self, app, stranger_client, seed_user,
    ):
        """The lock refuses passwords and codes, never a session's ordinary pages."""
        with app.app_context():
            user_id = seed_user["user"].id
            _set_lock_state(user_id, locked_for=timedelta(minutes=10))

            # The Security page, /security since ruling plan#3435.
            response = stranger_client.get("/security")

            assert response.status_code == 200
            assert b"Two-Factor Authentication" in response.data


class TestARefusedGuessLogsItsAddress:
    """Each door logs who was refused, so a lock can be traced to an address."""

    _CLIENT = "8.8.8.8"

    def _sign_in(self, client):
        """Sign *client* in through nginx, keeping one address for its session.

        Through the shared sign-in, on a browser the owner trusts, so it types
        no code and every code of this step is the test's.

        Args:
            client: The test client.
        """
        sign_in_from_a_trusted_browser(
            client, _EMAIL, _PASSWORD, OWNERS_PAGE, **through_nginx(self._CLIENT),
        )

    @staticmethod
    def _addresses(caplog, event) -> list[str]:
        """Return the address on each logged *event*.

        Args:
            caplog: Pytest's log capture.
            event: The event's name.

        Returns:
            Each record's ``ip``, in order.
        """
        return [
            r.ip for r in caplog.records if getattr(r, "event", None) == event
        ]

    def test_a_wrong_code_at_sign_in(self, app, client, seed_user, caplog):
        """The code form logs a wrong code against the client's address."""
        with app.app_context():
            secret = _secret_and_one_backup_code(seed_user["user"].id)
            _type_password(client, **through_nginx(self._CLIENT))

            with caplog.at_level(logging.WARNING, logger="app.routes.auth.mfa"):
                client.post(
                    "/mfa/verify", data={"totp_code": _wrong_code(secret)},
                    **through_nginx(self._CLIENT),
                )

            assert self._addresses(caplog, EVT_LOGIN_FAILED) == [self._CLIENT]

    def test_a_locked_account_at_sign_in(self, app, client, seed_user, caplog):
        """The code form logs a refusal while locked, even for a right code."""
        with app.app_context():
            user_id = seed_user["user"].id
            secret = _secret_and_one_backup_code(user_id)
            _type_password(client, **through_nginx(self._CLIENT))
            _set_lock_state(user_id, locked_for=timedelta(minutes=5))

            with caplog.at_level(logging.WARNING, logger="app.routes.auth.mfa"):
                client.post(
                    "/mfa/verify", data={"totp_code": code_now(secret)},
                    **through_nginx(self._CLIENT),
                )

            assert self._addresses(caplog, EVT_LOGIN_FAILED) == [self._CLIENT]

    def test_a_wrong_password_at_change_password(
        self, app, client, seed_user, caplog,
    ):
        """Change password logs a wrong current password."""
        # Pylint: ``unused-argument`` -- ``seed_user`` is asked for so the
        # account exists to sign in to.
        # pylint: disable=unused-argument
        with app.app_context():
            self._sign_in(client)

            with caplog.at_level(
                logging.WARNING, logger="app.routes.auth.session_security",
            ):
                client.post("/change-password", data={
                    "current_password": "wrong",
                    "new_password": _NEW_PASSWORD,
                    "confirm_password": _NEW_PASSWORD,
                }, **through_nginx(self._CLIENT))

            assert self._addresses(caplog, EVT_REAUTH_FAILED) == [self._CLIENT]

    def test_a_wrong_password_at_disable(self, app, client, seed_user, caplog):
        """Disable two-factor logs a wrong password."""
        with app.app_context():
            self._sign_in(client)
            secret = _secret_and_one_backup_code(seed_user["user"].id)

            with caplog.at_level(logging.WARNING, logger="app.routes.auth.mfa"):
                client.post("/mfa/disable", data={
                    "current_password": "wrong", "totp_code": code_now(secret),
                }, **through_nginx(self._CLIENT))

            assert self._addresses(caplog, EVT_REAUTH_FAILED) == [self._CLIENT]

    def test_a_wrong_code_at_disable(self, app, client, seed_user, caplog):
        """Disable two-factor logs a wrong code."""
        with app.app_context():
            self._sign_in(client)
            secret = _secret_and_one_backup_code(seed_user["user"].id)

            with caplog.at_level(logging.WARNING, logger="app.routes.auth.mfa"):
                client.post("/mfa/disable", data={
                    "current_password": _PASSWORD,
                    "totp_code": _wrong_code(secret),
                }, **through_nginx(self._CLIENT))

            assert self._addresses(caplog, EVT_REAUTH_FAILED) == [self._CLIENT]


class TestTheCodeFormIsLimitedByThePendingAccount:
    """Beside the address limit, one budget per pending account, wherever it arrives from.

    A known browser spends its own: ``tests/test_adversarial/test_known_browser_code_tries.py``.
    """

    @pytest.fixture
    def rate_app(self, app, monkeypatch):
        """A fresh app with the limiter on from the start, as in production.

        Yields:
            The app.
        """
        # Pylint: ``unused-argument`` -- ``app`` is asked for so the suite's app is
        # built first: the last app built decides whether the one limiter is on.
        # pylint: disable=unused-argument
        with limited_app(monkeypatch) as limited:
            yield limited

    def _guess_from(self, rate_app, address, email=_EMAIL, password=_PASSWORD):
        """Type *email*'s password from *address*, then guess one wrong code.

        Args:
            rate_app: The app under test.
            address: The client's address.
            email: The account's address.
            password: The account's password.

        Returns:
            The code form's response.
        """
        browser = rate_app.test_client()
        request_arguments = through_nginx(address)
        response = browser.post(
            "/login", data={"email": email, "password": password},
            **request_arguments,
        )
        assert response.status_code == 302
        return browser.post(
            "/mfa/verify", data={"totp_code": _NEVER_A_CODE}, **request_arguments,
        )

    def test_moving_between_addresses_buys_no_more_guesses(
        self, rate_app, seed_user,
    ):
        """Five guesses from five addresses spend the account's one budget."""
        with rate_app.app_context():
            _secret_and_one_backup_code(seed_user["user"].id)
            for hit in range(_CODE_LIMIT):
                response = self._guess_from(rate_app, f"8.8.8.{hit}")
                assert response.status_code == 200
                assert b"Invalid verification code" in response.data

            assert self._guess_from(rate_app, "9.9.9.9").status_code == 429

    def test_another_account_keeps_its_own_budget(
        self, rate_app, seed_user, seed_second_user,
    ):
        """One account's budget running out does not reach another's."""
        with rate_app.app_context():
            _secret_and_one_backup_code(seed_user["user"].id)
            _secret_and_one_backup_code(seed_second_user["user"].id)
            for hit in range(_CODE_LIMIT):
                self._guess_from(rate_app, f"8.8.8.{hit}")

            response = self._guess_from(
                rate_app, "9.9.9.9", "second@shekel.local", "secondpass12",
            )

            assert response.status_code == 200
            assert b"Invalid verification code" in response.data

    def test_the_address_limit_still_holds_across_accounts(
        self, rate_app, seed_user, seed_second_user,
    ):
        """One address guessing at two accounts spends one address budget."""
        with rate_app.app_context():
            _secret_and_one_backup_code(seed_user["user"].id)
            _secret_and_one_backup_code(seed_second_user["user"].id)
            for _ in range(3):
                assert self._guess_from(rate_app, "8.8.8.8").status_code == 200
            for _ in range(2):
                assert self._guess_from(
                    rate_app, "8.8.8.8", "second@shekel.local", "secondpass12",
                ).status_code == 200

            # The login limit is 5 from this address too, so the sixth is
            # refused at /login: reach the code form from a pending session
            # another address made, and post it from this one.
            browser = rate_app.test_client()
            assert browser.post(
                "/login",
                data={"email": "second@shekel.local", "password": "secondpass12"},
                **through_nginx("9.9.9.9"),
            ).status_code == 302
            assert browser.post(
                "/mfa/verify", data={"totp_code": _NEVER_A_CODE},
                **through_nginx("8.8.8.8"),
            ).status_code == 429

    def test_requests_with_no_pending_account_share_no_count(self, rate_app):
        """With no pending sign-in there is nothing to count against: each just bounces."""
        for hit in range(_CODE_LIMIT + 1):
            response = rate_app.test_client().post(
                "/mfa/verify", data={},
                **through_nginx(f"8.8.8.{hit}"),
            )
            assert response.status_code == 302
            assert "/login" in response.headers["Location"]

    def _kept_cookie(self, rate_app, secret_holder_address):
        """Return a browser and its cookie from a password step past its five minutes.

        Args:
            rate_app: The app under test.
            secret_holder_address: The address the password step came from.

        Returns:
            ``(browser, cookie value)``, the cookie holding a pending sign-in
            whose time has run out.
        """
        browser = rate_app.test_client()
        assert browser.post(
            "/login", data={"email": _EMAIL, "password": _PASSWORD},
            **through_nginx(secret_holder_address),
        ).status_code == 302
        with browser.session_transaction() as pending:
            pending["_mfa_pending_at"] = (
                datetime.now(timezone.utc) - _MFA_PENDING_MAX_AGE
                - timedelta(seconds=1)
            ).isoformat()
        name = rate_app.config["SESSION_COOKIE_NAME"]
        return browser, browser.get_cookie(name).value

    def test_a_kept_cookie_spends_none_of_the_accounts_budget(
        self, rate_app, seed_user,
    ):
        """Replaying a timed-out pending sign-in leaves the owner's right code working."""
        with rate_app.app_context():
            secret = _secret_and_one_backup_code(seed_user["user"].id)
            kept, cookie = self._kept_cookie(rate_app, "6.6.6.6")
            name = rate_app.config["SESSION_COOKIE_NAME"]
            for hit in range(_CODE_LIMIT):
                # The view clears the pending state in its reply, so a replay
                # sends the kept cookie again each time.
                kept.set_cookie(name, cookie)
                response = kept.post(
                    "/mfa/verify", data={"totp_code": _NEVER_A_CODE},
                    **through_nginx(f"6.6.6.{hit}"),
                )
                assert response.status_code == 302
                assert "/login" in response.headers["Location"]

            owner = rate_app.test_client()
            assert owner.post(
                "/login", data={"email": _EMAIL, "password": _PASSWORD},
                **through_nginx("8.8.8.8"),
            ).status_code == 302
            response = owner.post(
                "/mfa/verify", data={"totp_code": code_now(secret)},
                **through_nginx("8.8.8.8"),
            )

            assert response.status_code == 302
            assert "/login" not in response.headers["Location"]
            assert "/mfa/verify" not in response.headers["Location"]

    def test_a_kept_cookie_still_spends_its_own_address(self, rate_app, seed_user):
        """The exemption is from the account's budget, never from the address's."""
        with rate_app.app_context():
            _secret_and_one_backup_code(seed_user["user"].id)
            kept, cookie = self._kept_cookie(rate_app, "6.6.6.6")
            name = rate_app.config["SESSION_COOKIE_NAME"]
            statuses = []
            for _ in range(_CODE_LIMIT + 1):
                kept.set_cookie(name, cookie)
                statuses.append(kept.post(
                    "/mfa/verify", data={"totp_code": _NEVER_A_CODE},
                    **through_nginx("7.7.7.7"),
                ).status_code)

            assert statuses == [302] * _CODE_LIMIT + [429]
