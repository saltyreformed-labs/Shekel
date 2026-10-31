"""
Shekel Budget App -- Tests for scripts/rotate_sessions.py

Covers ``execute_rotation`` and ``main`` entry points of the
session-invalidation script.  ``execute_rotation`` is exercised
directly with the test database session; ``main`` is exercised via
``parse_args`` and the ``argv`` parameter to keep the tests
independent of ``sys.argv``.

Related audit findings: F-001 (SECRET_KEY history), F-016 (runtime
SECRET_KEY default).  The rotate_sessions script is the operational
control that closes the residual cookie-replay window after a
SECRET_KEY rotation.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from app.extensions import db
from app.models.trusted_device import TrustedDevice
from app.models.user import User
from app.services import trusted_device_service
from app.services.auth_service import hash_password
from scripts.rotate_sessions import execute_rotation, main, parse_args


def _make_user(email: str) -> User:
    """Insert a minimal user row and return it.

    Tests need users that exist in ``auth.users`` but do not need the
    full ``seed_user`` payload (settings, account, scenario, etc.).
    Creating only the User row also keeps the tests fast.

    Args:
        email: Unique email address for the user.

    Returns:
        The flushed (but not committed) User instance.
    """
    user = User(
        email=email,
        password_hash=hash_password("testpass12"),
        display_name=email.split("@")[0],
    )
    db.session.add(user)
    db.session.flush()
    return user


class TestExecuteRotation:
    """Tests for ``execute_rotation`` -- the core data operation."""

    def test_rotate_sessions_bumps_every_user(self, app, db):
        """Every user row's ``session_invalidated_at`` is set to a
        timestamp at or after the call time, and the return value
        equals the row count (3 in this case).

        Uses three users so the test catches any bug that updates only
        the first row matched (a common SQLAlchemy ``update()`` pitfall
        when ``synchronize_session`` is not set).
        """
        _make_user("u1@example.com")
        _make_user("u2@example.com")
        _make_user("u3@example.com")
        db.session.commit()

        start = datetime.now(timezone.utc)
        count = execute_rotation(db.session)

        assert count == 3
        # ``execute_rotation`` commits and detaches the previous instances.
        # Re-query to read post-commit state.
        users = db.session.query(User).all()
        assert len(users) == 3
        for user in users:
            assert user.session_invalidated_at is not None
            # The bump timestamp must be at or after the call time.
            # Allow microsecond drift -- ``datetime.now`` and the
            # SQL bump can differ by a single ULP.
            assert user.session_invalidated_at >= start - timedelta(seconds=1)

    def test_rotate_sessions_preserves_other_columns(self, app, db, seed_user):
        """The bump must not touch other user columns.

        Regression guard: an UPDATE that mistakenly omits the WHERE
        clause or names too many columns could trash ``email`` or
        ``password_hash``.  We snapshot every observable column on
        ``seed_user`` before the bump and compare after.
        """
        user_id = seed_user["user"].id
        before = {
            "email": seed_user["user"].email,
            "password_hash": seed_user["user"].password_hash,
            "display_name": seed_user["user"].display_name,
            "is_active": seed_user["user"].is_active,
            "role_id": seed_user["user"].role_id,
            "linked_owner_id": seed_user["user"].linked_owner_id,
        }

        execute_rotation(db.session)

        # ``execute_rotation`` commits, expiring the seed_user instance.
        # Re-fetch the row to read post-commit state.
        user = db.session.get(User, user_id)
        for column, original in before.items():
            assert getattr(user, column) == original, (
                f"Column {column!r} mutated by rotate_sessions: "
                f"{original!r} -> {getattr(user, column)!r}"
            )
        assert user.session_invalidated_at is not None

    def test_rotate_sessions_overwrites_older_timestamp(self, app, db, seed_user):
        """An existing ``session_invalidated_at`` is replaced, not
        skipped.  The new timestamp must be strictly greater than the
        old one.
        """
        user_id = seed_user["user"].id
        old_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        seed_user["user"].session_invalidated_at = old_timestamp
        db.session.commit()

        execute_rotation(db.session)

        user = db.session.get(User, user_id)
        assert user.session_invalidated_at > old_timestamp

    def test_rotate_sessions_forgets_every_trusted_device(
        self, app, db, seed_user, seed_second_user,
    ):
        """Every user's trusted device stops being trusted, with no write to it.

        The module docstring's claim (plan#2173): a device is live only while
        it was trusted at or after its user's ``session_invalidated_at``, so
        the bump leaves every device older than it.  Each is trusted an hour
        back, so the bump is later than the trust whatever the clock's
        resolution, and found live first, so its loss is the rotation's.
        The rows stay: the script writes nothing to the table.
        """
        an_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        tokens = {}
        with time_machine.travel(an_hour_ago, tick=False):
            for user in (seed_user["user"], seed_second_user["user"]):
                tokens[user.id] = trusted_device_service.trust_device(
                    user.id, "Firefox",
                )
        db.session.commit()
        for user_id, token in tokens.items():
            assert trusted_device_service.find_live_device(
                user_id, token,
            ) is not None

        execute_rotation(db.session)

        for user_id, token in tokens.items():
            assert trusted_device_service.find_live_device(
                user_id, token,
            ) is None
        assert db.session.query(TrustedDevice).count() == 2

    def test_rotate_sessions_on_empty_db_returns_zero(self, app, db):
        """An empty ``auth.users`` table returns 0 with no error.

        The ``db`` fixture truncates ``auth.users`` between tests, so
        this scenario actually triggers in CI -- not just a contrived
        edge case.
        """
        assert db.session.query(User).count() == 0
        count = execute_rotation(db.session)
        assert count == 0

    def test_rotate_sessions_emits_log_event(self, app, db, seed_user, caplog):
        """A WARNING-level structured log event names the row count.

        Operations alerting depends on the audit log capturing this
        event; if the event fires under a different name or category,
        log filters built against ``sessions_invalidated_global`` will
        miss it.
        """
        with caplog.at_level(logging.WARNING):
            execute_rotation(db.session)

        # Find the structured event by its name attribute, not by
        # message text -- the event is what downstream filters key on.
        matching = [
            r for r in caplog.records
            if getattr(r, "event", None) == "sessions_invalidated_global"
        ]
        assert len(matching) == 1, (
            f"Expected exactly one sessions_invalidated_global record; "
            f"got {len(matching)}.  All records: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in caplog.records]}"
        )
        record = matching[0]
        assert record.levelno == logging.WARNING
        assert getattr(record, "category", None) == "auth"
        assert getattr(record, "count", None) == 1


class TestMain:
    """Tests for the CLI entry point ``main(argv)``.

    Only the missing-confirm path is unit-tested here.  The
    ``--confirm`` happy path goes through ``run_rotation`` which calls
    ``create_app()`` with the runtime ``FLASK_ENV`` -- a different DB
    from the one ``conftest.py`` configures.  The data operation that
    matters is ``execute_rotation``; it has full coverage above.
    """

    def test_main_requires_confirm_flag(self, app, db, seed_user, capsys):
        """Calling ``main([])`` without ``--confirm`` returns 1 and
        leaves the database untouched.

        This is the operational guard against an accidental run.  If
        ``--confirm`` were to become optional the script would silently
        log everyone out, which is exactly the destructive operation we
        want a friction step in front of.
        """
        user_id = seed_user["user"].id
        initial_invalidated_at = seed_user["user"].session_invalidated_at

        exit_code = main([])

        assert exit_code == 1
        # ``execute_rotation`` was never called, so the row is
        # untouched.  Re-fetch so the assertion sees committed state.
        user = db.session.get(User, user_id)
        assert user.session_invalidated_at == initial_invalidated_at
        captured = capsys.readouterr()
        assert "Refusing to run without --confirm" in captured.err


class TestParseArgs:
    """Tests for the argparse wrapper used by ``main``."""

    def test_parse_args_default_confirm_false(self):
        """``parse_args([])`` returns ``confirm=False`` (the safe
        default) without raising.

        ``--confirm`` is intentionally optional at the argparse level
        so ``main([])`` can return 1 cleanly.  If it were
        ``required=True``, ``parse_args`` would call ``sys.exit(2)``
        and ``main([])`` could not return 1.
        """
        args = parse_args([])
        assert args.confirm is False

    def test_parse_args_confirm_true(self):
        """``parse_args(["--confirm"])`` returns ``confirm=True``."""
        args = parse_args(["--confirm"])
        assert args.confirm is True

    def test_parse_args_unknown_arg_exits(self):
        """An unknown flag triggers argparse's ``SystemExit(2)``.

        Catches a regression where an over-permissive parser silently
        accepts typos like ``--confrim`` (no flag fires, script
        no-ops, operator thinks the rotation succeeded).
        """
        with pytest.raises(SystemExit):
            parse_args(["--unknown-flag"])
