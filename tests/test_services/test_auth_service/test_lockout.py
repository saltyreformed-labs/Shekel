"""Auth service tests: the account lockout's settings and behaviour."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bcrypt
import pytest

from app.extensions import db
from app.models.known_device import KnownDevice
from app.models.user import User
from app.services import auth_service, known_device_service
from app.exceptions import AuthError
from app.utils.log_events import EVT_KNOWN_DEVICE_DELETED


# ---------------------------------------------------------------------------
# Account lockout (audit finding F-033 / commit C-11)
# ---------------------------------------------------------------------------


class TestAccountLockoutHelpers:
    """Tests for the lockout-config helpers in auth_service."""

    def test_default_threshold_is_ten(self, monkeypatch):
        """Without an env override, the threshold defaults to 10.

        10 was chosen in the C-11 plan to leave a comfortable margin
        for typo storms while clamping a credential-stuffing attack
        to a single attempt per lockout window per account.
        """
        monkeypatch.delenv("LOCKOUT_THRESHOLD", raising=False)
        assert auth_service._get_lockout_threshold() == 10

    def test_default_duration_is_15_minutes(self, monkeypatch):
        """Without an env override, the lockout duration is 15 minutes."""
        monkeypatch.delenv("LOCKOUT_DURATION_MINUTES", raising=False)
        assert auth_service._get_lockout_duration() == timedelta(minutes=15)

    def test_threshold_env_override_takes_effect(self, monkeypatch):
        """LOCKOUT_THRESHOLD env var is read at call time.

        Reading at call time (rather than at module import) is required
        so that the test suite and operators can change the threshold
        without restarting Python.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        assert auth_service._get_lockout_threshold() == 3

    def test_duration_env_override_takes_effect(self, monkeypatch):
        """LOCKOUT_DURATION_MINUTES env var is read at call time."""
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "1")
        assert auth_service._get_lockout_duration() == timedelta(minutes=1)

    def test_threshold_zero_rejected(self, monkeypatch):
        """A non-positive threshold is rejected with a clear error.

        Zero or negative would lock immediately on the first wrong
        password, locking out legitimate users on a single typo.
        Treat as a config error rather than a working config.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "0")
        with pytest.raises(ValueError, match="positive integer"):
            auth_service._get_lockout_threshold()

    def test_duration_zero_rejected(self, monkeypatch):
        """A non-positive duration is rejected with a clear error."""
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "0")
        with pytest.raises(ValueError, match="positive integer"):
            auth_service._get_lockout_duration()


class TestAccountLockoutBehaviour:
    """End-to-end tests for the lockout flow inside ``authenticate``.

    Every test re-fetches the seed user inside its own ``app_context``
    via ``db.session.get`` because the ``seed_user`` fixture's session
    closes after fixture exit; the User reference in
    ``seed_user["user"]`` is detached from the test's session and any
    in-place mutation on it would not propagate to the database.
    Mirrors the same pattern in ``test_authenticate_raises_auth_error_on_disabled_account``
    above.
    """

    def test_failed_login_increments_counter(self, app, db, seed_user):
        """Each wrong password bumps ``failed_login_count`` and commits.

        Verifies the counter survives the transactional boundary and
        the next call observes the persisted value.  Without the
        commit inside ``authenticate`` the counter would only live in
        the session and reset to 0 on the next request.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            with pytest.raises(AuthError):
                auth_service.authenticate(
                    auth_service.find_sign_in_user("test@shekel.local"), "wrong-password",
                )

            user = db.session.get(User, user_id)
            assert user.failed_login_count == 1
            assert user.locked_until is None

    def test_three_consecutive_failures_increment_to_three(
        self, app, db, seed_user, monkeypatch,
    ):
        """Counter increments monotonically on consecutive failures.

        Three failures with a threshold of 5 leaves the counter at 3
        and the account NOT locked.  Anchors the threshold-trip test
        below: at exactly 5 failures the lockout fires.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "5")
        user_id = seed_user["user"].id
        with app.app_context():
            for _ in range(3):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        auth_service.find_sign_in_user("test@shekel.local"), "wrong-password",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 3
            assert user.locked_until is None

    def test_threshold_failures_set_locked_until(
        self, app, db, seed_user, monkeypatch,
    ):
        """Reaching the threshold sets ``locked_until`` and zeroes the counter.

        With threshold=3 the third failure trips the lockout.  The
        plan specifies the counter is reset on lockout-trip so a second
        lockout cycle requires another threshold-many failures rather
        than just one extra after the window expires.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        monkeypatch.setenv("LOCKOUT_DURATION_MINUTES", "15")
        user_id = seed_user["user"].id
        with app.app_context():
            before = datetime.now(timezone.utc)
            for _ in range(3):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        auth_service.find_sign_in_user("test@shekel.local"), "wrong-password",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 0
            assert user.locked_until is not None
            # locked_until must be approximately now + 15 min.  Use a
            # tolerant range to absorb the test runtime; 14..16 minutes
            # is plenty of slack for any plausible CI box.
            delta = user.locked_until - before
            assert timedelta(minutes=14) < delta < timedelta(minutes=16)

    def test_locked_account_rejects_correct_password(
        self, app, db, seed_user,
    ):
        """A locked account refuses even the correct password.

        This is the timing-oracle defence: while ``locked_until > now``
        the service checks the password only against the dummy hash, so an
        attacker observing response timing cannot distinguish "locked +
        right" from "locked + wrong" and cannot use a captured-but-then-
        locked window to confirm a guessed password
        (:class:`TestALockedAttemptPaysTheCheck` spies that check).
        Verified here by feeding a known-correct password and confirming
        AuthError.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.locked_until = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            )
            db.session.commit()

            with pytest.raises(AuthError, match="Invalid email or password"):
                auth_service.authenticate(auth_service.find_sign_in_user("test@shekel.local"), "testpass")

    def test_locked_account_does_not_increment_counter(
        self, app, db, seed_user,
    ):
        """A locked account's counter is NOT incremented during the window.

        This matters because the service zeroes the counter at lockout-
        trip; we do not want re-locking to happen after a single retry
        during the window.  The retry-while-locked path returns early
        without touching the counter.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.locked_until = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            )
            user.failed_login_count = 0
            db.session.commit()

            for _ in range(5):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        auth_service.find_sign_in_user("test@shekel.local"), "anything",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 0

    def test_expired_lockout_allows_correct_password(
        self, app, db, seed_user,
    ):
        """A lockout in the past does not block authentication.

        ``locked_until`` is an exclusive upper bound: at the instant
        the column equals ``now`` the lockout is over.  Set the column
        a second into the past and verify a correct password succeeds,
        confirming the strict-greater-than gate.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.locked_until = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            )
            user.failed_login_count = 0
            db.session.commit()

            result = auth_service.authenticate(auth_service.find_sign_in_user("test@shekel.local"), "testpass")
            assert result.id == user_id

            # A correct password alone clears nothing (ruling plan#3007): the
            # run-out window stays, blocking nothing, until every check has
            # passed.  The clear itself is pinned at a finished sign-in in
            # tests/test_routes/test_every_guess_counts.py.
            assert db.session.get(User, user_id).locked_until is not None

    def test_a_correct_password_alone_does_not_clear_the_count(
        self, app, db, seed_user,
    ):
        """A correct password leaves the count where it was (ruling plan#3007).

        For an account with a second factor this is only the first check.
        Clearing here would let someone holding the password but not the
        second factor re-enter it between wrong codes, and the lock would
        never fire.  The clear belongs to a finished sign-in or re-confirm.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            for _ in range(3):
                with pytest.raises(AuthError):
                    auth_service.authenticate(
                        auth_service.find_sign_in_user("test@shekel.local"), "wrong-password",
                    )
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 3

            auth_service.authenticate(auth_service.find_sign_in_user("test@shekel.local"), "testpass")
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 3
            assert user.locked_until is None

    def test_the_clear_writes_nothing_on_a_clean_account(
        self, app, db, seed_user,
    ):
        """An account with no failure history pays no write when every check passes.

        Moved here from the password check when the clear moved
        (``test_successful_login_no_op_commit_on_clean_user``).  Assigning the
        same values would issue no UPDATE either, so ``updated_at`` could not
        tell this from a clear that always writes: the commit itself is what
        this pins.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            with patch.object(auth_service.db.session, "commit") as commit:
                auth_service.clear_failed_attempts(user)
            commit.assert_not_called()

    @pytest.mark.parametrize(
        "count, locked_for",
        [
            pytest.param(3, None, id="a-count-alone"),
            pytest.param(0, timedelta(seconds=-1), id="a-run-out-lock-alone"),
        ],
    )
    def test_the_clear_clears_and_commits(
        self, app, db, seed_user, count, locked_for,
    ):
        """Whatever is on the row is cleared, and the clear survives a rollback."""
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.failed_login_count = count
            user.locked_until = (
                None if locked_for is None
                else datetime.now(timezone.utc) + locked_for
            )
            db.session.commit()

            auth_service.clear_failed_attempts(user)
            db.session.rollback()

            db.session.expire_all()
            user = db.session.get(User, user_id)
            assert user.failed_login_count == 0
            assert user.locked_until is None

    def test_lockout_stores_timezone_aware_datetime(
        self, app, db, seed_user, monkeypatch,
    ):
        """``locked_until`` is timezone-aware (UTC), not a naive datetime.

        Comparing a naive datetime to ``datetime.now(timezone.utc)`` in
        the lockout-gate check would raise TypeError under Python's
        strict comparison rules.  This test guards against a future
        edit that drops the ``timezone.utc`` argument.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "1")
        user_id = seed_user["user"].id
        with app.app_context():
            with pytest.raises(AuthError):
                auth_service.authenticate(
                    auth_service.find_sign_in_user("test@shekel.local"), "wrong-password",
                )
            user = db.session.get(User, user_id)
            assert user.locked_until is not None
            assert user.locked_until.tzinfo is not None

    def test_check_constraint_rejects_negative_count(self, app, db, seed_user):
        """The CHECK constraint blocks writes of a negative count.

        Verifies the schema-level guard documented on
        ``ck_users_failed_login_count_non_negative``.  A future
        backfill or buggy migration that wrote a negative value would
        otherwise silently invert the lockout logic.  The test
        asserts the constraint name appears in the IntegrityError so a
        future rename is caught.
        """
        from sqlalchemy.exc import IntegrityError  # pylint: disable=import-outside-toplevel
        user_id = seed_user["user"].id
        with app.app_context():
            user = db.session.get(User, user_id)
            user.failed_login_count = -1
            with pytest.raises(
                IntegrityError,
                match="ck_users_failed_login_count_non_negative",
            ):
                db.session.commit()
            db.session.rollback()


_EMAIL = "test@shekel.local"
_PASSWORD = "testpass"


def _known_browser(user_id: int) -> str:
    """Record a finished sign-in for *user_id* on a new browser; return its token.

    Args:
        user_id: The account.

    Returns:
        The browser's token.
    """
    token = known_device_service.record_sign_in(user_id, None, "")
    db.session.commit()
    return token


def _attempt(token: str | None, password: str) -> User:
    """Sign in as the route does: find, read the browser, then authenticate.

    The browser is read afresh on each attempt, as each request reads it.

    Args:
        token: The browser's token, or ``None`` for a browser holding none.
        password: The password typed.

    Returns:
        The user :func:`~app.services.auth_service.authenticate` returns.
    """
    user = auth_service.find_sign_in_user(_EMAIL)
    standing = known_device_service.find_browser(user.id, token)
    return auth_service.authenticate(
        user, password, None if standing is None else standing.device,
    )


def _lock(user_id: int, count: int) -> datetime:
    """Lock *user_id* for ten minutes with *count* on the account; return the end.

    Args:
        user_id: The account.
        count: The strangers' count to store.

    Returns:
        The stored ``locked_until``.
    """
    user = db.session.get(User, user_id)
    user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=10)
    user.failed_login_count = count
    db.session.commit()
    return user.locked_until


class TestAKnownBrowser:
    """The account's lock and count are the strangers'; a known browser has its own.

    Plan#2772: a browser that finished a sign-in in the last thirty days is not
    stopped by the lock strangers' wrong passwords put on the account, and its
    own wrong passwords count on its record, never on the account (ruling
    plan#2996).
    """

    def test_it_passes_the_lock_with_the_right_password(self, app, db, seed_user):
        """Locked by strangers with 4 on the count: the user comes back, both untouched."""
        user_id = seed_user["user"].id
        token = _known_browser(user_id)
        locked_until = _lock(user_id, 4)

        assert _attempt(token, _PASSWORD).id == user_id

        db.session.expire_all()
        user = db.session.get(User, user_id)
        assert user.locked_until == locked_until
        assert user.failed_login_count == 4

    def test_a_stranger_is_still_refused_the_right_password(
        self, app, db, seed_user,
    ):
        """The control: the same lock refuses a browser holding no token."""
        user_id = seed_user["user"].id
        _known_browser(user_id)
        _lock(user_id, 4)

        with pytest.raises(AuthError, match="Invalid email or password"):
            _attempt(None, _PASSWORD)

    def test_its_wrong_password_counts_on_its_record_alone(
        self, app, db, seed_user,
    ):
        """One wrong password: 1 on the browser's record, 0 on the account's."""
        user_id = seed_user["user"].id
        token = _known_browser(user_id)

        with pytest.raises(AuthError, match="Invalid email or password"):
            _attempt(token, "wrong-password")

        db.session.expire_all()
        user = db.session.get(User, user_id)
        assert user.failed_login_count == 0
        assert user.locked_until is None
        assert known_device_service.find_browser(
            user_id, token,
        ).device.failed_login_count == 1

    def test_the_accounts_threshold_deletes_its_record(
        self, app, db, seed_user, monkeypatch, caplog,
    ):
        """At a threshold of 3 the third wrong password deletes the record, with its event.

        The next wrong password from that browser is a stranger's, so it counts
        on the account: 1.  The account was never locked.
        """
        monkeypatch.setenv("LOCKOUT_THRESHOLD", "3")
        user_id = seed_user["user"].id
        token = _known_browser(user_id)
        device_id = known_device_service.find_browser(user_id, token).device.id

        with caplog.at_level(logging.WARNING, logger="app.services.auth_service"):
            for _ in range(3):
                with pytest.raises(AuthError):
                    _attempt(token, "wrong-password")

        assert db.session.query(KnownDevice).filter_by(user_id=user_id).count() == 0
        assert [
            (r.user_id, r.device_id) for r in caplog.records
            if getattr(r, "event", None) == EVT_KNOWN_DEVICE_DELETED
        ] == [(user_id, device_id)]
        db.session.expire_all()
        assert db.session.get(User, user_id).failed_login_count == 0

        with pytest.raises(AuthError):
            _attempt(token, "wrong-password")

        db.session.expire_all()
        user = db.session.get(User, user_id)
        assert user.failed_login_count == 1
        assert user.locked_until is None


def _hashes_checked(email: str, password: str) -> list[str]:
    """Return each hash bcrypt checked while ``authenticate`` refused *email*.

    Spies ``bcrypt.checkpw`` itself, the cost, so a branch that returns early
    shows as no check at all.

    Args:
        email: The address typed.
        password: The password typed.

    Returns:
        The hashes, in the order checked.
    """
    with patch.object(
        auth_service.bcrypt, "checkpw", wraps=bcrypt.checkpw,
    ) as checkpw:
        with pytest.raises(AuthError, match="Invalid email or password"):
            auth_service.authenticate(
                auth_service.find_sign_in_user(email), password,
            )
    return [call.args[1].decode("utf-8") for call in checkpw.call_args_list]


class TestALockedAttemptPaysTheCheck:
    """Every refusal at ``/login`` pays one password check (ruling plan#2999).

    A locked account used to skip it while an address with no account paid it,
    so ten wrong passwords and a faster reply told a stranger the address is a
    real account that has just locked.  An address with no account and a
    locked one check the dummy hash; a wrong password checks the account's.
    """

    def test_an_address_with_no_account_checks_the_dummy(self, app, db, seed_user):
        """One check, against the dummy hash."""
        assert _hashes_checked("nobody@shekel.local", _PASSWORD) == [
            auth_service._DUMMY_PASSWORD_HASH,
        ]

    def test_a_locked_account_checks_the_dummy(self, app, db, seed_user):
        """The right password and a wrong one: one check each, against the dummy.

        Never the account's hash, so the reply cannot depend on whether the
        password was right.
        """
        _lock(seed_user["user"].id, 0)

        assert _hashes_checked(_EMAIL, _PASSWORD) == [
            auth_service._DUMMY_PASSWORD_HASH,
        ]
        assert _hashes_checked(_EMAIL, "wrong-password") == [
            auth_service._DUMMY_PASSWORD_HASH,
        ]

    def test_a_wrong_password_checks_the_accounts_hash(self, app, db, seed_user):
        """One check, against the account's own hash: what the other two cost."""
        password_hash = db.session.get(User, seed_user["user"].id).password_hash

        assert _hashes_checked(_EMAIL, "wrong-password") == [password_hash]

