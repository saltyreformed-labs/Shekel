"""The worked-out read on one copy: the statements it issues, per owner and scenario.

Plan task plan#2618's item 3 (plan step plan#2513).  It reads; it writes nothing.

**The read's statements** (ruling plan#2623: one load of the owner's records and pure folds, a
fixed number of statements).  For every owner and scenario the read runs twice, each from an
expired session so it loads everything it reads itself, under
``tests._test_helpers.capture_sql_statements``: the count each time, its lines, and each
statement by the tables it names.  The suite shows the count does not grow with the number of
sources, accounts or loans
(``tests/test_services/test_ledger_lines/test_the_worked_out_read/test_what_it_reads.py``);
this says what it is on production's owners, and a table read once per account or per loan
would show as that table named once for each.

Its item 4, where the STORED ledger filed its corrections against today's calendar, went at plan
task plan#3080 with the store's last writer, and with it the probe's runner,
``run_deploy_rewrites_on_dump.py``, which ran it on each copy that run deployed.

Usage, on a copy nothing else uses::

    DATABASE_URL=postgresql://.../copy \\
        .venv/bin/python tests/manual/measure_worked_out_read.py \\
        --report read.txt [--at 2026-11-02T12:00:00-05:00]

``--at`` pins the clock (``time_machine``, no tick) at the instant to read at, the instant the
copy's deploy ran under where it was deployed.  Production figures stay out of the repository
(ruling R-BAL132): keep the report in the handoff folder.  Outside pytest's collection, like its
``verify_*`` siblings.

Exit status: 0 every owner was measured and each read's count held across its reads; 1 a count
moved between two reads of the same records (named in the report); 2 the read refused an
owner (named in the report), so that owner was not measured, or the run could not
start or crashed part way (its traceback on standard error, ``exit_status``).
"""

import argparse
import pathlib
import re
import sys
from collections import Counter
from datetime import datetime

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` and ``tests`` are not importable without this.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position``, ``wrong-import-order`` -- these must
# follow the path bootstrap above, and ``exit_status`` must arm before every
# other import, a third party's too, so an import that fails exits 2 (its module
# docstring): no import order resolves ``app`` before the bootstrap runs, or
# arms before ``exit_status`` is imported.
# pylint: disable=wrong-import-position,wrong-import-order
from tests.manual import exit_status

exit_status.arm(__name__)

from tests._test_helpers import capture_sql_statements

import time_machine

from app import create_app
from app.extensions import db
from app.models.scenario import Scenario
from app.services.ledger_lines import worked_out_lines

# Each table a statement names, after FROM or JOIN, schema-qualified as the
# app's models are.
_TABLES = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+\.[a-z_]+)", re.IGNORECASE)
# How many times the read is run from an expired session: twice, so a count
# that moved between two reads of the same records is seen.
_READS = 2


def _read_statements(owner_id, scenario_id):
    """Run the read once from an expired session; its lines and statement texts."""
    db.session.expire_all()
    lines, statements = capture_sql_statements(
        lambda: worked_out_lines(owner_id, scenario_id),
    )
    return lines, [text for text, _params in statements]


def report_statements(scenario, say):
    """Report the read's statements for *scenario*'s owner, read :data:`_READS` times.

    Args:
        scenario: The :class:`Scenario`.
        say: Where the report's lines go.

    Returns:
        Whether every read issued the same number of statements.
    """
    owner_id, scenario_id = scenario.user_id, scenario.id
    reads = [_read_statements(owner_id, scenario_id) for _ in range(_READS)]
    say(f"-- the read's statements: {[len(texts) for _, texts in reads]} on "
        f"{_READS} reads of {len(reads[0][0])} line(s)")
    named = Counter()
    for number, text in enumerate(reads[0][1], start=1):
        tables = sorted(set(_TABLES.findall(text)))
        named.update(tables)
        say(f"   {number}. {', '.join(tables) or '(no table)'}")
    say("   tables, each by how many statements name it: " + ", ".join(
        f"{table} {count}" for table, count in sorted(named.items())))
    steady = len({len(texts) for _, texts in reads}) == 1
    if not steady:
        say("!  the count MOVED between two reads of the same records")
    return steady


def measure(say):
    """Measure every owner's scenarios; the exit status (the module docstring's)."""
    status = 0
    for scenario in db.session.query(Scenario).order_by(
            Scenario.user_id, Scenario.id):
        say(f"== owner {scenario.user_id}, scenario {scenario.id}"
            f"{' (baseline)' if scenario.is_baseline else ''}")
        try:
            if not report_statements(scenario, say):
                status = max(status, 1)
        except ValueError as exc:
            say(f"!  the read REFUSED: {exc}")
            status = 2
    return status


def main():
    """Parse the arguments, pin the clock if asked, and measure."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report", type=pathlib.Path, required=True,
        help="the file the report is written to (it must not exist)",
    )
    parser.add_argument(
        "--at", type=datetime.fromisoformat, default=None,
        help="the instant to pin the clock at (ISO 8601, with its offset)",
    )
    args = parser.parse_args()
    app = create_app()
    with app.app_context(), args.report.open("x", encoding="utf-8") as report:

        def say(*lines):
            """Write *lines* to the report (the app logs to stdout)."""
            report.write("".join(f"{line}\n" for line in lines))

        if args.at is None:
            return measure(say)
        with time_machine.travel(args.at, tick=False):
            return measure(say)


if __name__ == "__main__":
    sys.exit(main())
