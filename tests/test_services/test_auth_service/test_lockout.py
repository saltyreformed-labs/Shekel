"""Auth service tests: the account lockout's settings and behaviour."""

from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.user import User
from app.services import auth_service
from app.exceptions import AuthError


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
        the service does NOT call ``verify_password``, so an attacker
        observing response timing cannot distinguish "locked + right"
        from "locked + wrong" and cannot use a captured-but-then-
        locked window to confirm a guessed password.  Verified by
        feeding a known-correct password and confirming AuthError.
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

            user = db.session.get(User, user_id)
            # Successful authentication clears any residual lockout.
            assert user.locked_until is None
            assert user.failed_login_count == 0

    def test_successful_login_resets_counter(self, app, db, seed_user):
        """A successful login zeroes the counter built up by typos.

        Without this reset, a user who typoed three times then logged
        in would still be three failures away from a fresh lockout
        instead of starting from zero.
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
            assert user.failed_login_count == 0
            assert user.locked_until is None

    def test_successful_login_no_op_commit_on_clean_user(
        self, app, db, seed_user,
    ):
        """A login on an account with no failure history does not write.

        The service skips the commit when both ``failed_login_count``
        and ``locked_until`` are already at their cleared values.  This
        is a small efficiency concern: every login on a fresh account
        would otherwise trigger an unnecessary UPDATE.  Verified by
        comparing ``updated_at`` before and after; the TimestampMixin
        updates it on every flush, so an absence of change proves no
        UPDATE was issued.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            user_before = db.session.get(User, user_id)
            original_updated_at = user_before.updated_at
            db.session.expunge(user_before)

            auth_service.authenticate(auth_service.find_sign_in_user("test@shekel.local"), "testpass")

            user_after = db.session.get(User, user_id)
            assert user_after.updated_at == original_updated_at

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
