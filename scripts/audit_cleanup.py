"""
Shekel Budget App -- Audit Log Retention Cleanup

Deletes audit_log rows older than the configured retention period.
Intended to run as a daily cron job.

Usage:
    python scripts/audit_cleanup.py [--days N] [--dry-run]

Options:
    --days N     Override retention period (default: AUDIT_RETENTION_DAYS env
                 var, or 365 if not set).
    --dry-run    Print the count of rows that would be deleted without
                 actually deleting them.

Examples:
    python scripts/audit_cleanup.py                 # Delete rows older than 365 days
    python scripts/audit_cleanup.py --days 90       # Delete rows older than 90 days
    python scripts/audit_cleanup.py --dry-run       # Preview without deleting

Production invocation (the shekel-audit-cleanup.timer unit): a one-shot
container of the migrate service, the one that logs in as the database
owner.  Its entrypoint (scripts/owner_entrypoint.sh) loads the owner's
password and builds the owner's DATABASE_URL, and ``--no-deps`` runs no
deploy first --

    cd /opt/docker/shekel && docker compose run --rm --no-deps \
        --pull never migrate python scripts/audit_cleanup.py

``docker exec <app> python scripts/audit_cleanup.py`` does NOT work:
exec'd processes get the container's stored Config.Env, which holds the
``replaced_by_docker_secret`` placeholders, and the app container holds
no owner credential at all.
"""
import argparse
import logging
import os
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force the owner role.  DELETE on system.audit_log is deliberately
# not granted to the least-privilege ``shekel_app`` role (the audit
# log is tamper-resistant from the runtime app; init_db_role.sql
# grants it SELECT/INSERT only), so this script must connect as the
# owner.  In the migrate service, the production timer path, the
# deploy's configuration reads DATABASE_URL alone (app/config.py,
# DeployConfig), so this line changes nothing there.  It is for a host
# run, whose configuration prefers DATABASE_URL_APP when the
# environment or a ``.env`` line sets it.  Empty string rather than
# ``pop`` for the dotenv-resurrection reason documented in
# scripts/init_database.py: config.py's ``load_dotenv()`` re-inserts
# a ``.env`` value into an absent key, while an existing-but-empty
# key survives and the config resolver documents empty-as-unset.
os.environ["DATABASE_URL_APP"] = ""

# Pylint: wrong-import-position -- the sys.path bootstrap above must run
# first so ``scripts`` resolves when invoked as
# ``python scripts/audit_cleanup.py`` (sys.path[0] is scripts/, not the
# repo root, in that mode).
# pylint: disable=wrong-import-position
from scripts._script_lib import run_in_app_context
# pylint: enable=wrong-import-position


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace with ``days`` (int) and ``dry_run`` (bool).
    """
    parser = argparse.ArgumentParser(
        description="Delete audit_log rows older than the retention period."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("AUDIT_RETENTION_DAYS", "365")),
        help="Retention period in days (default: AUDIT_RETENTION_DAYS or 365).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print count of rows to delete without actually deleting.",
    )
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1 to prevent deleting the entire audit log.")
    return args


def execute_cleanup(db_session: Session, days: int, dry_run: bool = False) -> int:
    """Run the audit log cleanup against an existing database session.

    This is the core logic, separated from app creation so it can be
    called from tests with the test database session.

    Args:
        db_session: A SQLAlchemy session (e.g., ``db.session``).
        days: Number of days to retain.  Rows older than this are deleted.
            Must be at least 1 -- zero or negative values are rejected
            before any DB work because they would delete the entire
            audit log (a negative interval shifts the cutoff into the
            future, matching every row).  Same bound parse_args
            enforces at the CLI.
        dry_run: If True, only count rows without deleting.

    Returns:
        The number of rows deleted (or that would be deleted in dry-run mode).

    Raises:
        ValueError: If ``days`` is less than 1.
    """
    if days < 1:
        raise ValueError(
            f"days must be at least 1 (got {days}): a zero or negative "
            "retention period would delete the entire audit log -- "
            "now() - make_interval(days => negative) is a FUTURE cutoff "
            "that matches every row."
        )

    logger = logging.getLogger(__name__)

    if dry_run:
        result = db_session.execute(
            text("""
                SELECT COUNT(*)
                FROM system.audit_log
                WHERE executed_at < now() - make_interval(days => :days)
            """),
            {"days": days},
        )
        count = result.scalar()
        logger.info(
            "Dry run: %d audit_log rows older than %d days would be deleted.",
            count,
            days,
        )
    else:
        result = db_session.execute(
            text("""
                DELETE FROM system.audit_log
                WHERE executed_at < now() - make_interval(days => :days)
            """),
            {"days": days},
        )
        count = result.rowcount
        db_session.commit()
        logger.info(
            "Deleted %d audit_log rows older than %d days.",
            count,
            days,
        )

    return count


def run_cleanup(days: int, dry_run: bool = False) -> int:
    """Create the app and execute the audit log cleanup.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_cleanup()`` directly with the test db session.

    Args:
        days: Number of days to retain. Rows older than this are deleted.
            Must be at least 1 (see ``execute_cleanup``).
        dry_run: If True, only count rows without deleting.

    Returns:
        The number of rows deleted (or that would be deleted in dry-run mode).
    """
    return run_in_app_context(
        lambda session: execute_cleanup(session, days, dry_run=dry_run)
    )


def main() -> None:
    """Parse the CLI arguments, run the cleanup, and print the result."""
    args = parse_args()
    deleted = run_cleanup(args.days, dry_run=args.dry_run)
    print(f"{'Would delete' if args.dry_run else 'Deleted'}: {deleted} rows")


if __name__ == "__main__":
    main()
