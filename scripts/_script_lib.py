"""
Shekel Budget App -- Shared helpers for the operational scripts.

The single home for the CLI boilerplate every ``scripts/*.py`` entry
point repeated: the optional ``--database-url`` environment override,
the deferred ``create_app`` bootstrap, the app-context execution of a
session-taking runner, and the operator-facing logging setup.  Extracted
during the Phase 5 step 5 ``scripts/`` pylint cleanup to dissolve the
cross-file ``duplicate-code`` clusters at the root instead of disabling
them.

Importable as ``scripts._script_lib`` once a script's sys.path
bootstrap has put the repo root on the path -- the same mechanism that
makes ``app`` importable (``scripts/`` is a namespace package; the test
suite imports script modules the same way).
"""

import argparse
import logging
import os
import sys


def run_in_app_context(runner, database_url=None):
    """Run ``runner(db.session)`` inside a freshly built app context.

    Owns the deferred-import dance every script entry point needs: an
    operator-supplied ``--database-url`` must land in the environment
    BEFORE the app config is imported (the config classes read
    ``os.environ`` at import time), so the ``app`` imports happen here,
    after the override, never at this module's import time.

    Args:
        runner: Callable taking the SQLAlchemy session
            (``db.session``); its return value is passed through.
        database_url: Optional database URL override for this run.
            ``None`` leaves the environment untouched.

    Returns:
        Whatever ``runner`` returns.
    """
    if database_url:
        os.environ["DATABASE_URL"] = database_url

    # Pylint: import-outside-toplevel -- create_app must be imported
    # AFTER the DATABASE_URL override above; the app config reads the
    # environment at import time, so a module-level import would bind
    # the default URL and silently ignore the operator's override.
    from app import create_app  # pylint: disable=import-outside-toplevel
    # Pylint: import-outside-toplevel -- same deferred-bootstrap reason
    # as create_app directly above.
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    app = create_app()
    with app.app_context():
        return runner(db.session)


def setup_script_logging():
    """Configure the operator-facing log format shared by the scripts.

    One definition of the ``[timestamp] [LEVEL] message`` console shape
    the ``__main__`` blocks previously each hand-rolled.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_confirm_args(
    argv: list[str] | None,
    description: str,
    acknowledgment: str,
) -> argparse.Namespace:
    """Parse the command line of a mandatory-``--confirm`` script.

    The shared argparse skeleton of the destructive one-shot scripts
    (``rotate_sessions``, ``rotate_field_key``): a script-specific
    description plus a single ``--confirm`` flag whose help text ends
    with the shared "Required" sentence.  ``--confirm`` is
    intentionally optional at the argparse level so the caller's
    ``main`` can refuse with its own exit code (via ``confirm_gate``)
    instead of argparse's ``SystemExit(2)``.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).
        description: The parser description shown by ``--help``.
        acknowledgment: Script-specific sentence describing the
            destructive effect the operator is acknowledging.

    Returns:
        ``argparse.Namespace`` with ``confirm`` (bool).
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            f"{acknowledgment} Required: the script refuses to run "
            "without this flag."
        ),
    )
    return parser.parse_args(argv)


def confirm_gate(args: argparse.Namespace, script_name: str) -> int | None:
    """Refuse a run whose parsed args lack the ``--confirm`` flag.

    Owns only the shared half of the scripts' exit-code contracts:
    "no ``--confirm`` means exit code 1 and an untouched database."
    Success-path exit codes (e.g. ``rotate_field_key``'s skipped-rows
    code 2) stay with each script's ``main``.

    Args:
        args: Parsed namespace carrying the ``confirm`` flag.
        script_name: The script's file name, used in the re-run hint.

    Returns:
        ``None`` when ``--confirm`` was supplied (caller proceeds), or
        exit code ``1`` after printing the refusal hint to stderr.
    """
    if args.confirm:
        return None
    print(
        "Refusing to run without --confirm.  Re-run as:\n"
        f"    python scripts/{script_name} --confirm",
        file=sys.stderr,
    )
    return 1
