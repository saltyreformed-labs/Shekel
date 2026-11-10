"""A finished sign-in's clear never erases a wrong guess judged after its checks.

Plan#3120.  A stranger's wrong guess takes no owner lock (ruling plan#3110):
the guess record's own row lock serialises the count.  The three completions
that clear the count -- ``/login`` with no code, ``/mfa/verify`` and re-confirm
(``/reauth``) -- clear it once every check has passed (ruling plan#3007), so a
guess judged between a completion's checks and its clear would have been
deleted with the record, and every wrong guess counts.  Each clear now deletes
the record only while it holds what the checks saw: the ``/login`` judgment's
own record, or the code form's and re-confirm's read of the lock.

**The gap's guess is a real one**: a stranger's wrong password at ``/login``,
from a browser of its own, in a thread with an app context of its own (so a
session and a connection of its own, as a production request has), judged
and committed right after the door's checks are read -- the FIRST moment of
the gap: after ``/login``'s own judgment
(:func:`~app.services.auth_service.check_strangers_password`), and after the
code form's and re-confirm's read of the lock
(:func:`~app.services.auth_service.read_the_lock`).  So a door that read the
record again anywhere later, its clear's own argument included, would see the
guess and delete it; a guess placed just before the clear could not tell that
door from a right one.  Each race asserts the clear ran once, so a door that
never cleared cannot pass it by leaving the count alone.

Each door's control is the same flow with no guess in the gap: the record is
cleared.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pyotp
import pytest

from app.extensions import db
from app.services import auth_service, guess_record_service
from tests._guess_records import count_of, locked_until_of, record_of, set_record
from tests._lock_waits import THREAD_DEADLINE_S
from tests._sign_in import make_a_stranger
from tests._test_helpers import enable_mfa
from tests.conftest import SEED_USER_EMAIL, SEED_USER_PASSWORD

_WRONG = "not-the-password"
_REFUSED = b"Invalid email or password."

#: The run of wrong guesses on the account's email before the sign-in: what a
#: clear with no guess in the gap clears, and what stands with one.
_THE_RUN_BEFORE = 3

#: The threshold the lock-in-the-gap case judges with, set here so the case is
#: an oracle the configuration cannot move.
_THRESHOLD = 3

#: A threshold one guess reaches, so the gap's one guess opens a lock on an
#: account with no count and leaves the count where the checks saw it, at 0.
_ONE_GUESS_LOCKS = 1


def _a_strangers_wrong_guess(app) -> None:
    """A stranger's wrong password at ``/login`` for the seed user, judged and committed.

    Run in a thread with an app context of its own: in the request's own
    thread a second request would share its app context, and with it its
    session and transaction.

    Args:
        app: The application.
    """
    def guess():
        """The POST, in an app context of its own."""
        with app.app_context():
            return app.test_client().post(
                "/login", data={"email": SEED_USER_EMAIL, "password": _WRONG},
            )

    with ThreadPoolExecutor(max_workers=1) as worker:
        response = worker.submit(guess).result(timeout=THREAD_DEADLINE_S)
    assert response.status_code == 200 and _REFUSED in response.data


def _a_guess_after_the_checks(
    app, monkeypatch, checks: str,
) -> list[guess_record_service.Held | None]:
    """Judge and commit a stranger's wrong guess right after this sign-in's checks are read.

    Wraps *checks*, the ``auth_service`` function whose return is the moment
    the door's checks are judged, so the guess follows its first return, once;
    and records what each clear is handed.  A call that raises, as the gap's
    own wrong guess does at ``/login``, makes no guess.  Armed in the test
    body, after its fixtures' own sign-ins, so the guess falls only in the gap
    of the sign-in the test makes.

    Args:
        app: The application.
        monkeypatch: The test's monkeypatch.
        checks: ``"check_strangers_password"`` at ``/login``, or
            ``"read_the_lock"`` at the code form and re-confirm.

    Returns:
        What each clear was handed, appended as it runs.
    """
    real_checks = getattr(auth_service, checks)
    real_clear = auth_service.clear_failed_attempts
    guessed = []
    clears = []

    def checks_then_a_guess(*args, **kwargs):
        """The real checks, then the guess the first time they return."""
        result = real_checks(*args, **kwargs)
        if not guessed:
            guessed.append(True)
            _a_strangers_wrong_guess(app)
        return result

    def recorded_clear(checked):
        """The real clear, recording what it was handed."""
        clears.append(checked)
        real_clear(checked)

    monkeypatch.setattr(auth_service, checks, checks_then_a_guess)
    monkeypatch.setattr(auth_service, "clear_failed_attempts", recorded_clear)
    return clears


def _stage_the_run_before(count: int = _THE_RUN_BEFORE) -> None:
    """Write *count* wrong guesses on the seed user's email, with no lock, committed."""
    set_record(
        SEED_USER_EMAIL, count=count, locked_until=None,
        last_attempt_at=datetime.now(timezone.utc),
    )
    db.session.commit()


def _signed_in(response) -> bool:
    """Return whether *response* is a finished sign-in's or re-confirm's redirect."""
    return (
        response.status_code == 302
        and urlsplit(response.headers["Location"]).path
        not in ("/login", "/mfa/verify", "/reauth")
    )


def _password(client):
    """POST the seed user's right password at ``/login``."""
    return client.post(
        "/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD},
    )


class TestLogin:
    """``/login`` with no code clears against what its own try's judgment left."""

    @pytest.mark.usefixtures("seed_user")
    def test_a_guess_in_the_gap_stands(self, app, client, monkeypatch):
        """3 before, the right password counts none, 1 after its judgment: signed in, 4 stands."""
        _stage_the_run_before()
        clears = _a_guess_after_the_checks(app, monkeypatch, "check_strangers_password")

        assert _signed_in(_password(client))

        assert len(clears) == 1
        assert count_of(SEED_USER_EMAIL) == _THE_RUN_BEFORE + 1

    @pytest.mark.usefixtures("seed_user")
    def test_a_lock_opened_in_the_gap_stands(self, app, client, monkeypatch):
        """Threshold 3, 2 before, the gap's guess is the third: signed in, and its lock stands.

        The plan#3117 review's probe: a lock opened between the judgment and
        the confirm under the owner's lock was gone once the sign-in finished.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", str(_THRESHOLD))
        _stage_the_run_before(_THRESHOLD - 1)
        clears = _a_guess_after_the_checks(app, monkeypatch, "check_strangers_password")

        assert _signed_in(_password(client))

        assert len(clears) == 1
        assert count_of(SEED_USER_EMAIL) == 0
        assert locked_until_of(SEED_USER_EMAIL) is not None
        assert guess_record_service.read(SEED_USER_EMAIL).locked

    @pytest.mark.usefixtures("seed_user")
    def test_a_lock_opened_in_the_gap_on_a_clean_account_stands(
        self, app, client, monkeypatch,
    ):
        """No count before; the gap's one guess opens a lock, leaving 0: signed in, it stands.

        The right password's judgment left 0 and no lock, and the gap's guess
        leaves 0 again, so only the lock's end tells the record from the one
        the checks saw: the plan#3120 review's probe.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", str(_ONE_GUESS_LOCKS))
        clears = _a_guess_after_the_checks(app, monkeypatch, "check_strangers_password")

        assert _signed_in(_password(client))

        assert [(held.count, held.locked_until) for held in clears] == [(0, None)]
        assert count_of(SEED_USER_EMAIL) == 0
        assert guess_record_service.read(SEED_USER_EMAIL).locked

    @pytest.mark.usefixtures("seed_user")
    def test_no_guess_in_the_gap_clears_the_run_before(self, client):
        """The control: 3 before, signed in, the record gone."""
        _stage_the_run_before()

        assert _signed_in(_password(client))

        assert record_of(SEED_USER_EMAIL) is None


class TestTheCodeForm:
    """``/mfa/verify`` clears against what its read of the lock found."""

    @staticmethod
    def _sign_in_with_a_code(client, secret: str):
        """The right password, then the right code; return the code form's response."""
        response = _password(client)
        assert urlsplit(response.headers["Location"]).path == "/mfa/verify"
        return client.post(
            "/mfa/verify", data={"totp_code": pyotp.TOTP(secret).now()},
        )

    def test_a_guess_in_the_gap_stands(self, app, client, seed_user, monkeypatch):
        """3 before, 1 after the lock's read, before the code's check: signed in, 4 stands."""
        secret = enable_mfa(db.session, seed_user["user"].id)
        _stage_the_run_before()
        clears = _a_guess_after_the_checks(app, monkeypatch, "read_the_lock")

        assert _signed_in(self._sign_in_with_a_code(client, secret))

        assert len(clears) == 1
        assert count_of(SEED_USER_EMAIL) == _THE_RUN_BEFORE + 1

    def test_no_guess_in_the_gap_clears_the_run_before(self, client, seed_user):
        """The control: 3 before, signed in with the code, the record gone."""
        secret = enable_mfa(db.session, seed_user["user"].id)
        _stage_the_run_before()

        assert _signed_in(self._sign_in_with_a_code(client, secret))

        assert record_of(SEED_USER_EMAIL) is None


class TestReConfirm:
    """``/reauth`` clears against what its read of the lock found.

    Re-confirm clears the strangers' count only for a stranger, so each case
    first makes the signed-in browser one (:func:`tests._sign_in.make_a_stranger`).
    """

    @staticmethod
    def _re_confirm(client):
        """POST the right password at ``/reauth``; return the response."""
        return client.post("/reauth", data={"password": SEED_USER_PASSWORD})

    def test_a_guess_in_the_gap_stands(self, app, auth_client, seed_user, monkeypatch):
        """3 before, 1 after the lock's read, before the password's: re-confirmed, 4 stands."""
        make_a_stranger(seed_user["user"].id)
        _stage_the_run_before()
        clears = _a_guess_after_the_checks(app, monkeypatch, "read_the_lock")

        assert _signed_in(self._re_confirm(auth_client))

        assert len(clears) == 1
        assert count_of(SEED_USER_EMAIL) == _THE_RUN_BEFORE + 1

    def test_with_no_record_the_gaps_guess_stands(
        self, app, auth_client, seed_user, monkeypatch,
    ):
        """No record at the read, so nothing the checks saw to clear: the gap's 1 stands.

        An account with no failure history: the read finds no record, and the
        record the gap's guess wrote was written after it.
        """
        make_a_stranger(seed_user["user"].id)
        clears = _a_guess_after_the_checks(app, monkeypatch, "read_the_lock")

        assert _signed_in(self._re_confirm(auth_client))

        assert clears == [None]
        assert count_of(SEED_USER_EMAIL) == 1

    def test_no_guess_in_the_gap_clears_the_run_before(self, auth_client, seed_user):
        """The control: 3 before, re-confirmed, the record gone."""
        make_a_stranger(seed_user["user"].id)
        _stage_the_run_before()

        assert _signed_in(self._re_confirm(auth_client))

        assert record_of(SEED_USER_EMAIL) is None
