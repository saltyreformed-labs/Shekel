"""
Shekel Budget App -- Data Integrity Check

Validates referential integrity, detects orphaned records, flags balance
anomalies, and checks data consistency across all database schemas.

The checks themselves live one module per family (``_integrity_referential``,
``_integrity_orphan``, ``_integrity_balance``, ``_integrity_consistency``;
ruling **R-BAL161**, ``scripts/_integrity_core.py``); this module is the
command line and the orchestration.

Designed to be:
    - Run standalone via CLI: python scripts/integrity_check.py
    - Called from verify_backup.sh against a temporary database
    - Tested by pytest against the test database

Usage:
    python scripts/integrity_check.py [--database-url URL] [--verbose] [--category CAT]

Options:
    --database-url URL   Override the database URL (for verify_backup.sh)
    --verbose            Print details for each check, not just failures
    --category CAT       Run only checks in this category
                         (referential, orphan, balance, consistency)

Exit codes:
    0   All checks passed
    1   One or more CRITICAL checks failed
    2   One or more WARNING checks flagged issues (no critical failures)
    3   Script error (bad arguments, database connection failure)

Cron example (weekly, after backup verification), as the web login, in a
one-shot container of the app service (docs/runbook.md 1, Script Inventory):
    30 3 * * 0 cd /opt/docker/shekel && docker compose run --rm --no-deps \
        --pull never app python scripts/integrity_check.py
"""

import argparse
import logging
import os
import sys

from sqlalchemy.exc import SQLAlchemyError

# Ensure the project root is on sys.path so 'app' and 'scripts' are importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- these imports must follow the sys.path
# bootstrap above: the repo root is not on the path when the script is
# invoked as ``python scripts/integrity_check.py``.
# pylint: disable=wrong-import-position
from scripts._integrity_balance import check_balance_anomalies
from scripts._integrity_consistency import check_data_consistency
from scripts._integrity_core import CheckResult
from scripts._integrity_orphan import check_orphaned_records
from scripts._integrity_referential import check_referential_integrity
from scripts._script_lib import (
    run_in_app_context,
    setup_script_logging,
)
# pylint: enable=wrong-import-position

logger = logging.getLogger(__name__)


# ── Orchestration ─────────────────────────────────────────────────


# Map category names to their check functions.
CATEGORY_FUNCTIONS = {
    "referential": check_referential_integrity,
    "orphan": check_orphaned_records,
    "balance": check_balance_anomalies,
    "consistency": check_data_consistency,
}


def run_all_checks(session, categories=None, verbose=False) -> list[CheckResult]:
    """Execute all integrity checks against the given database session.

    Args:
        session: A SQLAlchemy session connected to the target database.
        categories: Optional list of category names to filter checks.
            Valid values: 'referential', 'orphan', 'balance', 'consistency'.
            If None, all categories are run.
        verbose: If True, log details for passing checks too.

    Returns:
        List of CheckResult objects, one per check executed.
    """
    all_results = []
    target_categories = categories or list(CATEGORY_FUNCTIONS.keys())

    for cat_name in target_categories:
        check_fn = CATEGORY_FUNCTIONS.get(cat_name)
        if check_fn is None:
            logger.warning("Unknown category: %s (skipping)", cat_name)
            continue

        results = check_fn(session)
        for result in results:
            if result.passed:
                if verbose:
                    logger.info(
                        "[PASS] %s: %s", result.check_id, result.description
                    )
            else:
                logger.log(
                    logging.ERROR if result.severity == "critical" else logging.WARNING,
                    "[FAIL] %s: %s (%d violation(s))",
                    result.check_id, result.description, result.detail_count,
                )
                if verbose and result.details:
                    for detail in result.details[:10]:
                        logger.info("       %s", detail)
                    if result.detail_count > 10:
                        logger.info(
                            "       ... and %d more",
                            result.detail_count - 10,
                        )

        all_results.extend(results)

    return all_results


def summarize_results(results: list[CheckResult]) -> tuple[int, int]:
    """Log a summary of all check results.

    Args:
        results: List of CheckResult objects.

    Returns:
        Tuple of (critical_failures, warning_failures) counts.
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    critical = sum(1 for r in results if not r.passed and r.severity == "critical")
    warnings = sum(1 for r in results if not r.passed and r.severity == "warning")

    logger.info("=" * 50)
    logger.info("  INTEGRITY CHECK SUMMARY")
    logger.info("=" * 50)
    logger.info("  Total checks:  %d", total)
    logger.info("  Passed:        %d", passed)
    logger.info("  Critical:      %d", critical)
    logger.info("  Warnings:      %d", warnings)

    if critical == 0 and warnings == 0:
        logger.info("  Status:        ALL PASSED")
    elif critical > 0:
        logger.error("  Status:        CRITICAL FAILURES")
    else:
        logger.warning("  Status:        WARNINGS ONLY")

    logger.info("=" * 50)
    return critical, warnings


# ── CLI ───────────────────────────────────────────────────────────


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace with database_url, verbose, and category.
    """
    parser = argparse.ArgumentParser(
        description="Validate Shekel database integrity."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL (for verify_backup.sh).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print details for each check, not just failures.",
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORY_FUNCTIONS.keys()),
        default=None,
        help="Run only checks in this category.",
    )
    return parser.parse_args(argv)


def run_cli(
    database_url: str | None = None,
    categories: list[str] | None = None,
    verbose: bool = False,
) -> int:
    """CLI entry point: create app, run checks, print results, exit.

    If database_url is provided, it overrides the Flask config.
    This allows verify_backup.sh to point at a temporary database.

    Args:
        database_url: Optional override for DATABASE_URL.
        categories: Optional list of categories to check.
        verbose: Print details for passing checks.

    Returns:
        Exit code (0, 1, 2, or 3).
    """
    def _check_and_summarize(session) -> tuple[int, int]:
        """Run the requested checks and return (critical, warning) counts."""
        results = run_all_checks(session, categories, verbose)
        return summarize_results(results)

    try:
        critical, warnings = run_in_app_context(
            _check_and_summarize, database_url=database_url
        )
    # The exit-code-3 contract covers script errors: bad configuration
    # (ValueError from create_app / config validation), a missing or
    # broken app package (ImportError), Flask context problems
    # (RuntimeError), filesystem/socket failures (OSError), and any
    # database connection or query failure (SQLAlchemyError).
    except (ImportError, OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:
        logger.error("Integrity check failed: %s", exc)
        return 3

    if critical > 0:
        return 1
    if warnings > 0:
        return 2
    return 0


if __name__ == "__main__":
    setup_script_logging()
    args = parse_args()
    sys.exit(run_cli(
        database_url=args.database_url,
        categories=[args.category] if args.category else None,
        verbose=args.verbose,
    ))
