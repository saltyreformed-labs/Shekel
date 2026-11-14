"""
Shekel Budget App -- Force-Invalidate All User Sessions

One-shot operations utility that signs every user out of every browser.
Run it after rotating ``SECRET_KEY``, after a git history rewrite that
excised a leaked key, or after any other event that compromises
previously-issued session cookies.

A session is signed in only while its sign-in's record in
``auth.sign_ins`` is live (ruling plan#3925; ``load_user`` in
``app/__init__.py``).  This script runs the one sign-out-everywhere act
(``app.services.sign_in_service.sign_out_everywhere``, ruling plan#3927)
for every user, keeping no browser and from one instant: every record is
deleted, so every session is refused on its next request, whatever key
signed its cookie, and every active user signs in again.

It also forgets every trusted device, of every user: the act moves each
user's ``session_invalidated_at`` to that instant, and a device is trusted
only while it was trusted at or after it
(``app.services.known_device_service``, plan#2173), so the next sign-in on
each one asks for the two-factor code again.  The script writes nothing to
``auth.known_devices``: the rows stay, trusted by nothing, and each
browser stays known, which no sign-out ends (ruling plan#2997).

This docstring once said the script also revokes remember-me tokens.  It
never did: Flask-Login's remember-me restore loads the cookie's user on
whatever session the request carries, an empty one or another user's.
Since plan#2173 ``load_user`` refuses any session no sign-in of the loaded
user started (its token names a live record of that user, or nothing), so
every remember-me cookie ever issued is refused whatever this script does,
and the app issues none (ruling plan#2406).

Usage:
    python scripts/rotate_sessions.py --confirm

The ``--confirm`` flag is mandatory: running without it prints a
short usage hint and exits with code 1, never touching the database.

Test entry point:
    ``execute_rotation(db.session)`` returns the number of users signed
    out.  Tests call this directly with the test session and never
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
    """Sign every user out of every browser, and end every trust, from one instant.

    The core data operation, separated from app creation so tests can
    call it directly with the test database session.

    Args:
        db_session (sqlalchemy.orm.Session): The application's
            ``db.session``, bound to a database that already has the
            ``auth.users`` and ``auth.sign_ins`` tables: the act it runs
            for each user writes through that session.

    Returns:
        The number of users signed out.  Returns ``0`` for an empty
        ``auth.users`` table (also a valid post-condition).

    Side effects:
        - Deletes every row of ``auth.sign_ins`` and moves every user's
          ``session_invalidated_at`` to the one instant.
        - Commits the transaction.
        - Emits a structured log event ``sessions_invalidated_global``
          at ``WARNING`` level with the user count.
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
    from app.services import sign_in_service  # pylint: disable=import-outside-toplevel
    # Pylint: import-outside-toplevel -- same deferred app-import
    # reason as the import directly above.
    from app.utils.log_events import (  # pylint: disable=import-outside-toplevel
        AUTH,
        log_event,
    )

    logger = logging.getLogger(__name__)

    now = datetime.now(timezone.utc)
    users = db_session.query(User).all()
    for user in users:
        sign_in_service.sign_out_everywhere(user, keeping=None, now=now)
    db_session.commit()

    log_event(
        logger,
        logging.WARNING,
        "sessions_invalidated_global",
        AUTH,
        "Global session invalidation: signed every user out of every "
        "browser and ended every trust.",
        count=len(users),
    )
    return len(users)


def run_rotation() -> int:
    """Create the Flask app and execute the rotation.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_rotation()`` directly with the test db session.

    Returns:
        The number of users signed out.
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
            "Sign every user out of every browser and end every trusted "
            "device's trust.  Run after rotating SECRET_KEY or after a "
            "git history rewrite that exposed a previously-leaked key."
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
    print(f"Signed out {count} user(s) everywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
