"""The worked-out read on one copy: its statements, and where the store files corrections.

Plan task plan#2618's items 3 and 4 (plan step plan#2513), which
``tests/manual/run_deploy_rewrites_on_dump.py`` runs from the measured commit's worktree on
each copy it makes: the dump as it was, and each deployed copy.  It reads; it writes nothing.

**The read's statements** (item 3; ruling plan#2623: one load of the owner's records and pure
folds, a fixed number of statements).  For every owner and scenario the read runs twice, each
from an expired session so it loads everything it reads itself, under
``tests._test_helpers.capture_sql_statements``: the count each time, its lines, and each
statement by the tables it names.  The suite shows the count does not grow with the number of
sources, accounts or loans
(``tests/test_services/test_ledger_lines/test_the_worked_out_read/test_what_it_reads.py``);
this says what it is on production's owners, and a table read once per account or per loan
would show as that table named once for each.

**Where the store files its corrections** (item 4: the leaf's spec counted, on production
2026-11-01, "the corrections filed in a paycheck today's calendar would file elsewhere: 4
entries, their 5 (account, paycheck) groups netting $0.00"; the run of plan task plan#2528
counted 2 after its deploy).  For every correction kind (``tests._worked_out_excuses``'
``correction_kind_ids``), how many stored entries it holds and how many sit in a paycheck that
is not the one today's calendar files their day in (``PayCalendar.filing_period``, the read's
rule, ruling plan#2519), each such entry named with its legs, and their (chart account,
paycheck) groups with each net.  A loan payment's split is counted too, though the harness's
census leaves it out (it is filed in its payment's paycheck, ruling R-BAL102), so a count that
included it can be told from one that did not.

Usage, on a copy nothing else uses (the run's driver sets the environment as it does for the
harness)::

    DATABASE_URL=postgresql://.../copy \\
        .venv/bin/python tests/manual/measure_worked_out_read.py \\
        --report read.txt [--at 2026-11-02T12:00:00-05:00]

``--at`` pins the clock (``time_machine``, no tick) at the instant the copy's deploy ran under.
Production figures stay out of the repository (ruling R-BAL132): keep the report in the handoff
folder.  Outside pytest's collection, like its ``verify_*`` siblings.

Exit status: 0 every owner was measured and each read's count held across its reads; 1 a count
moved between two reads of the same records (named in the report); 2 the read or the calendar
refused an owner (named in the report), so that owner was not measured.
"""

import argparse
import pathlib
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` and ``tests`` are not importable without this; the
# same bootstrap as ``verify_worked_out_ledger``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
import time_machine

from app import create_app, ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.scenario import Scenario
from app.services.ledger_lines import worked_out_lines
from app.services.pay_calendar import PayCalendarError, calendar_for
from tests._test_helpers import capture_sql_statements
from tests._worked_out_excuses import (
    CORRECTION,
    account_of,
    correction_kind_ids,
    filed_in,
    nets,
)
from tests._worked_out_ledger import read_ledger
from tests._worked_out_statements import key_text, money

# Each table a statement names, after FROM or JOIN, schema-qualified as the
# app's models are.
_TABLES = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+\.[a-z_]+)", re.IGNORECASE)
# How many times the read is run from an expired session: twice, so a count
# that moved between two reads of the same records is seen.
_READS = 2
_ZERO = Decimal("0.00")


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


def report_filing(scenario, calendar, say):
    """Report where the store files *scenario*'s corrections against today's calendar.

    Args:
        scenario: The :class:`Scenario`.
        calendar: Its owner's pay calendar.
        say: Where the report's lines go.
    """
    ledger = read_ledger(scenario, calendar)
    names = {
        ref_cache.posting_source_id(member): member.value
        for member in PostingSourceEnum
    }
    entries = defaultdict(list)
    for contribution in ledger.stored:
        grain, kind = contribution.source
        if grain == CORRECTION:
            entries[(kind, contribution.entry_id)].append(contribution)
    held, elsewhere = Counter(), defaultdict(list)
    for (kind, _entry_id), legs in sorted(entries.items()):
        held[kind] += 1
        if legs[0].pay_period_id != filed_in(ledger, legs[0].entry_date):
            elsewhere[kind].append(legs)
    say("-- corrections by kind: stored entries, and those filed in a paycheck "
        "today's calendar does not file their day in")
    for kind in sorted(correction_kind_ids(), key=names.get):
        say(f"   {names[kind]}: {held[kind]} stored, {len(elsewhere[kind])} "
            "filed elsewhere")
        for legs in elsewhere[kind]:
            _report_entry(ledger, legs, say)
        _report_groups(elsewhere[kind], say)


def _report_entry(ledger, legs, say):
    """Report one correction filed elsewhere: its paychecks, day and legs."""
    first = legs[0]
    today = filed_in(ledger, first.entry_date)
    say(f"     entry {first.entry_id} day {first.entry_date.isoformat()}: "
        f"stored in paycheck {_paycheck_text(ledger, first.pay_period_id)}, "
        f"today's calendar files it in {_paycheck_text(ledger, today)}: "
        + ", ".join(f"{key_text(leg.key)} {money(leg.amount)}" for leg in legs))


def _report_groups(kept, say):
    """Report the (chart account, paycheck) groups of *kept* entries and each net."""
    if not kept:
        return
    legs = [leg for entry in kept for leg in entry]
    grouped = nets(legs, lambda leg: (account_of(leg), leg.pay_period_id))
    nonzero = sum(1 for net in grouped.values() if net != _ZERO)
    say(f"     {len(grouped)} (chart account, paycheck) group(s), {nonzero} "
        "netting nonzero: " + ", ".join(
            f"{key_text(account[0])} paycheck {paycheck} {money(net)}"
            for (account, paycheck), net in sorted(
                grouped.items(), key=lambda item: (
                    key_text(item[0][0][0]), item[0][1]))))


def _paycheck_text(ledger, period_id):
    """Return a paycheck as its id and the days it covers on today's calendar."""
    for period in ledger.calendar.periods:
        if period.period_id == period_id:
            return (f"{period_id} ({period.start_date.isoformat()} to "
                    f"{period.end_date.isoformat()})")
    return f"{period_id} (not on today's calendar)"


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
            report_filing(scenario, calendar_for(scenario.user_id), say)
        except (PayCalendarError, ValueError) as exc:
            say(f"!  the read or the calendar REFUSED: {exc}")
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
