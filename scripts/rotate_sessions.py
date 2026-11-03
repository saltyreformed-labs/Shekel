"""
Shekel Budget App -- Force-Invalidate All User Sessions

One-shot operations utility that bumps ``users.session_invalidated_at``
to ``now()`` for every row in ``auth.users``.  Run it after rotating
``SECRET_KEY``, after a git history rewrite that excised a leaked key,
or after any other event that compromises previously-issued session
cookies.

The application's ``load_user`` callback (``app/__init__.py``) compares
``users.session_invalidated_at`` against the per-session
``_session_created_at`` timestamp on each request.  When this script
runs, every existing session becomes older than the new
``session_invalidated_at`` and is rejected on the next request,
forcing every active user to log in again.

It also forgets every trusted device, of every user.  A device is
trusted only while it was trusted at or after its user's
``session_invalidated_at`` (``app.services.trusted_device_service``,
plan#2173), so the bump leaves every device older than it, and the
next sign-in on each one asks for the two-factor code again.  The
script writes nothing to ``auth.trusted_devices``: the rows stay
until their user's next trust purges them, trusted by nothing.

This docstring once said the script also revokes remember-me tokens.  It
never did: Flask-Login's remember-me restore loads the cookie's user on
whatever session the request carries, an empty one with no
``_session_created_at`` (which the comparison above skipped) or another
user's, stamps and all.  Since plan#2173 ``load_user`` refuses any session
whose stamps no sign-in of the loaded user wrote
(``app.utils.session_helpers.sign_in_created_at``), so every remember-me
cookie ever issued is refused whatever this script does, and the app
issues none (ruling plan#2406).

Usage:
    python scripts/rotate_sessions.py --confirm

The ``--confirm`` flag is mandatory: running without it prints a
short usage hint and exits with code 1, never touching the database.

Test entry point:
    ``execute_rotation(db.session)`` returns the number of rows
    updated.  Tests call this directly with the test session and never
    create a separate Flask app.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# Ensure the project root is on sys.path so 'app' and 'scripts' are
# importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- this import must follow the sys.path
# bootstrap above; 'scripts' is only importable once the project root
# is on the path.
from scripts._script_lib import (  # pylint: disable=wrong-import-position
    confirm_gate,
    parse_confirm_args,
    run_in_app_context,
)


def execute_rotation(db_session) -> int:
    """Bump ``session_invalidated_at`` to ``now()`` for every user row.

    The core data operation, separated from app creation so tests can
    call it directly with the test database session.

    Args:
        db_session (sqlalchemy.orm.Session): A SQLAlchemy session
            bound to a database that already has the ``auth.users``
            table.

    Returns:
        The number of rows updated.  Returns ``0`` for an empty
        ``auth.users`` table (also a valid post-condition).

    Side effects:
        - Updates every row in ``auth.users``.
        - Commits the transaction.
        - Emits a structured log event ``sessions_invalidated_global``
          at ``WARNING`` level with the row count.
    """
    # Pylint: import-outside-toplevel -- importing anything under
    # ``app`` executes ``app.config``, which reads ``os.environ`` at
    # import time; deferring to call time keeps this module import
    # side-effect-free (``--help`` and a missing ``--confirm`` never
    # load app config) and preserves run_in_app_context's pre-import
    # DATABASE_URL override contract.
    from app.models.user import User  # pylint: disable=import-outside-toplevel
    # Pylint: import-outside-toplevel -- same deferred app-import
    # reason as the import directly above.
    from app.utils.log_events import (  # pylint: disable=import-outside-toplevel
        AUTH,
        log_event,
    )

    logger = logging.getLogger(__name__)

    now = datetime.now(timezone.utc)
    count = db_session.query(User).update(
        {User.session_invalidated_at: now},
        synchronize_session=False,
    )
    db_session.commit()

    log_event(
        logger,
        logging.WARNING,
        "sessions_invalidated_global",
        AUTH,
        "Global session invalidation: bumped session_invalidated_at "
        "for all users.",
        count=count,
    )
    return count


def run_rotation() -> int:
    """Create the Flask app and execute the rotation.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_rotation()`` directly with the test db session.

    Returns:
        The number of rows updated.
    """
    return run_in_app_context(execute_rotation)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).

    Returns:
        argparse.Namespace with ``confirm`` (bool).
    """
    return parse_confirm_args(
        argv,
        description=(
            "Force-invalidate every user session by bumping "
            "session_invalidated_at to now().  Run after rotating "
            "SECRET_KEY or after a git history rewrite that exposed "
            "a previously-leaked key."
        ),
        acknowledgment="Acknowledge that every active user will be logged out.",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).

    Returns:
        Process exit code.  ``0`` on success, ``1`` when ``--confirm``
        was not supplied.
    """
    refusal = confirm_gate(parse_args(argv), "rotate_sessions.py")
    if refusal is not None:
        return refusal
    count = run_rotation()
    print(f"Invalidated {count} session(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
