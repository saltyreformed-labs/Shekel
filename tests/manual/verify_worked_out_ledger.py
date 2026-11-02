"""Hold every owner's worked-out ledger against the stored one, to the cent, on a copy.

The production run of X-ew's first leaf (plan step plan#2513, plan task plan#2528;
``docs/plans/verification.md``).  The suite's parity test (plan#2527) grades the posting
fixtures; this grades production's own records, on a copy whose deploy step has brought the
store to target.  ``tests/manual/run_worked_out_ledger_on_dump.py`` makes that copy and runs
this on it.

**For every owner and scenario it reports** :func:`tests._worked_out_ledger.compare`: every
(chart row, paycheck, day) net on each side; every difference by source, each an expected kind
or UNEXPLAINED; the census of each expected kind with its count and dollars; every chart
account's total; every paycheck's, month's and year's income statement and the balance sheet
at every paycheck end, from the store and from the lines; and the labels plan#2523's note asks
about.  That module's docstring says what each is and how it is graded.

**First it re-runs the deploy's cash resync and rolls it back**
(``posting_service.resync_all_cash_postings``): its count says whether the store is at target,
and its own skip warning names the transfer families it skips, read rather than restated.

**Then three planted controls, each SHOWN to fire** (the standard's rule 4): a stored movement
line, a stored correction, and a residue entry that does not net to zero, none of which the
records hold.  Each is planted in a savepoint, measured and rolled back, and must come out as
its own two UNEXPLAINED source cells plus UNEXPLAINED statement fields.

**What a clean run does NOT say.**  The read and the writers apply the same target rules (plan
task plan#2526 moved them into ``app.services.ledger_lines``), so a wrong rule moves both sides
alike; the suite pins the rules by hand-computed literals.  This says the store, as
production's records and this tree's deploy leave it, is the ledger the records work out to.

**Usage** (from the repository root, on a copy nothing else uses)::

    DATABASE_URL=postgresql://.../copy \\
        .venv/bin/python tests/manual/verify_worked_out_ledger.py \\
        --report report.txt [--at 2026-11-02T12:00:00-05:00]

The report goes to its own file, since the app logs to stdout.  ``--at`` pins the clock
(``time_machine``, no tick) at the instant the copy's deploy step ran under.  It runs under the
developer's venv, not a release side's: the suite's helpers need ``pytest``.  Production
figures stay out of the repository (ruling R-BAL132): keep the report in the handoff folder.

Exit status: 0 every difference is an expected one and every control fired; 1 a difference or a
statement field is UNEXPLAINED, each named in the report; 2 the run measured nothing (a control
did not fire, or the run's own read disagreed with the suite's).  Outside pytest's collection,
like its ``verify_*`` siblings.
"""

import argparse
import logging
import pathlib
import sys
from datetime import datetime
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` and ``tests`` are not importable without this; the
# same bootstrap as ``verify_statement_baseline``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
import time_machine

from app import create_app, ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.scenario import Scenario
from app.services import posting_service
from app.services.pay_calendar import PayCalendarError, calendar_for
from tests._worked_out_ledger import (
    RESIDUE,
    HarnessError,
    compare,
    read_ledger,
    source_cell_of,
)

# What each planted control posts: any nonzero cent figure; a distinctive one
# so a reader can find it in a report.
_CONTROL_AMOUNT = Decimal("1.23")
# The logger the cash resync warns its skipped transfer families through.
_RESYNC_LOGGER = posting_service.logger.name
# How the resync's skip warning opens (``resync_all_cash_postings``), and the
# position of the skipped transfer ids among its arguments.
_SKIP_WARNING = "Cash posting resync skipped"
_SKIP_IDS_ARGUMENT = 1


def resync_once_rolled_back():
    """Re-run the deploy's cash resync, roll it back, and say what it did.

    Returns:
        ``(transactions re-posted, transfers re-posted, skipped transfer
        ids)``: zeroes when the store is at target, and the ids the resync's
        own warning names.

    Raises:
        HarnessError: A skip warning whose arguments are not the shape the
            resync logs.
    """
    skipped = set()

    class _Skips(logging.Handler):
        """Collect the transfer ids the resync's skip warning names."""

        def emit(self, record):
            """Read the ids off one warning."""
            if not record.getMessage().startswith(_SKIP_WARNING):
                return
            ids = record.args[_SKIP_IDS_ARGUMENT]
            if not all(isinstance(ident, int) for ident in ids):
                raise HarnessError(
                    f"the resync's skip warning carried {record.args!r}, not "
                    f"the transfer ids at argument {_SKIP_IDS_ARGUMENT}"
                )
            skipped.update(ids)

    handler = _Skips(logging.WARNING)
    logger = logging.getLogger(_RESYNC_LOGGER)
    logger.addHandler(handler)
    try:
        transactions, transfers = posting_service.resync_all_cash_postings()
    finally:
        logger.removeHandler(handler)
        db.session.rollback()
    db.session.expire_all()
    return transactions, transfers, skipped


# -- controls ---------------------------------------------------------------

def _plant(template, *, linked, legs):
    """Add one balanced entry copying *template*'s header, and flush it.

    Balanced, so it is the shape a writer could leave; the caller's
    savepoint takes it away again before anything commits.

    Args:
        template: A stored :class:`JournalEntry` whose header to copy.
        linked: Whether the planted entry keeps the template's movement
            link and kind (``False`` plants it sourceless, of the purchase
            kind: hard-delete residue).
        legs: Two of the template's postings, on two chart rows, to plant
            opposite amounts on.

    Returns:
        The planted :class:`JournalEntry`.
    """
    entry = JournalEntry(
        user_id=template.user_id,
        scenario_id=template.scenario_id,
        pay_period_id=template.pay_period_id,
        entry_date=template.entry_date,
        source_kind_id=(
            template.source_kind_id if linked
            else ref_cache.posting_source_id(PostingSourceEnum.PURCHASE)
        ),
        transaction_entry_id=template.transaction_entry_id if linked else None,
        description="verify_worked_out_ledger control",
    )
    for leg, sign in zip(legs, (1, -1)):
        entry.postings.append(Posting(
            ledger_account_id=leg.ledger_account_id,
            amount=_CONTROL_AMOUNT * sign,
            posting_kind_id=leg.posting_kind_id,
        ))
    db.session.add(entry)
    db.session.flush()
    return entry


def _templates(ledger):
    """Return a movement entry and a correction entry to copy, or refuse.

    The first stored movement-linked entry whose parent the resync walks and
    the first stored account true-up, each with legs on two chart rows.

    Raises:
        HarnessError: The owner holds no such entry, so no control can run.
    """
    trueup = ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP)
    movement = correction = None
    for entry in (
        db.session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == ledger.owner_id,
            JournalEntry.scenario_id == ledger.scenario_id,
        )
        .order_by(JournalEntry.id)
    ):
        if len({leg.ledger_account_id for leg in entry.postings}) < 2:
            continue
        if movement is None and entry.transaction_entry_id is not None and \
                ledger.parent_of.get(entry.transaction_entry_id) in ledger.walked:
            movement = entry
        if correction is None and entry.source_kind_id == trueup:
            correction = entry
    if movement is None or correction is None:
        raise HarnessError(
            f"owner {ledger.owner_id} holds no walked movement entry or no "
            "account true-up to plant a control from"
        )
    return movement, correction


def run_controls(scenario, calendar, skipped, baseline):
    """Plant each control, measure, roll it back; the report's lines.

    Args:
        scenario: The baseline :class:`Scenario` the controls plant in.
        calendar: Its owner's calendar.
        skipped: The transfer ids the resync skips.
        baseline: The scenario's own :class:`Outcome`.

    Returns:
        ``(lines, every control fired)``.
    """
    movement, correction = _templates(read_ledger(scenario, calendar))
    controls = (
        ("a stored movement line the records do not hold", movement, True),
        ("a stored correction the records do not hold", correction, True),
        ("residue that does not net to zero", movement, False),
    )
    lines, fired = [], True
    for title, template, linked in controls:
        line, ok = _run_control(
            scenario, calendar, skipped, baseline, (template, linked),
        )
        lines.append(f"   control '{title}' {line}")
        fired = fired and ok
    return lines, fired


def _run_control(scenario, calendar, skipped, baseline, plant):
    """Plant one control in a savepoint, measure, and roll it back.

    It FIRES when its own two source cells come out UNEXPLAINED, no other
    cell does that the run's own measurement did not -- but for the residue
    control, whose plant takes the excuse from every residue cell, so a
    residue cell may join it -- and a statement field comes out UNEXPLAINED
    beyond the run's own.

    Args:
        scenario: The baseline :class:`Scenario`.
        calendar: Its owner's calendar.
        skipped: The transfer ids the resync skips.
        baseline: The scenario's own :class:`Outcome`.
        plant: ``(the entry to copy, whether the copy keeps its link)``.

    Returns:
        ``(the report's line, whether it fired)``.
    """
    template, linked = plant
    legs = _distinct_legs(template)
    savepoint = db.session.begin_nested()
    try:
        planted_id = _plant(template, linked=linked, legs=legs).id
        planted = read_ledger(scenario, calendar)
        outcome = compare(planted, skipped)
    finally:
        savepoint.rollback()
        db.session.expire_all()
    own = {
        source_cell_of(c) for c in planted.stored if c.entry_id == planted_id
    } & outcome.unexplained_sources
    others = outcome.unexplained_sources - baseline.unexplained_sources - own
    ok = (
        len(own) == len(legs)
        and all(not linked and cell[0][0] == RESIDUE for cell in others)
        and outcome.unexplained_fields > baseline.unexplained_fields
    )
    return (
        f"(copying entry #{template.id}): "
        f"{'FIRED' if ok else 'DID NOT FIRE'}: {len(own)} of its {len(legs)} "
        f"source cell(s) UNEXPLAINED, {len(others)} other cell(s) newly "
        f"UNEXPLAINED; {outcome.unexplained_fields} unexplained statement "
        f"field(s) against the run's {baseline.unexplained_fields}"
    ), ok


def _distinct_legs(entry):
    """Return the first two postings of *entry* on two different chart rows."""
    first = entry.postings[0]
    second = next(
        leg for leg in entry.postings
        if leg.ledger_account_id != first.ledger_account_id
    )
    return (first, second)


# -- the run ----------------------------------------------------------------

def measure(report):
    """Measure every owner's scenarios into *report*; the exit status.

    Args:
        report: The open text file the report is written to.

    Returns:
        0, 1 or 2 (the module docstring's exit status).
    """

    def say(*lines):
        """Write *lines* to the report (the app logs to stdout)."""
        report.write("".join(f"{line}\n" for line in lines))

    transactions, transfers, skipped = resync_once_rolled_back()
    say(f"cash resync re-run (rolled back): re-posted {transactions} "
        f"transaction(s) and {transfers} transfer(s); skipped transfer "
        f"families: {sorted(skipped) or 'none'}")
    status = 0 if transactions == transfers == 0 else 1
    if status:
        say("!  the store is NOT at target: the deploy's resync would "
            "re-post on a second run")
    control_lines, fired = [], True
    for scenario in db.session.query(Scenario).order_by(
            Scenario.user_id, Scenario.id):
        unexplained, controls = _measure_scenario(scenario, skipped, say)
        status = 1 if unexplained else status
        if controls is not None:
            control_lines.append(f"-- controls, owner {scenario.user_id}:")
            control_lines.extend(controls[0])
            fired = fired and controls[1]
    say(*control_lines)
    if not control_lines or not fired:
        say("!  a control did not fire, or none ran: this run measured "
            "nothing")
        return 2
    return status


def _measure_scenario(scenario, skipped, say):
    """Compare one scenario and, in a baseline that holds a ledger, run the controls.

    Args:
        scenario: The :class:`Scenario`.
        skipped: The transfer ids the resync skips.
        say: Where the report's lines go.

    Returns:
        ``(how many things are UNEXPLAINED, (control lines, all fired) or
        None when no control ran)``.
    """
    try:
        calendar = calendar_for(scenario.user_id)
        ledger = read_ledger(scenario, calendar)
    except (PayCalendarError, ValueError) as exc:
        say(f"!  owner {scenario.user_id}, scenario {scenario.id}: the "
            f"worked-out read REFUSED: {exc}")
        return 1, None
    outcome = compare(ledger, skipped)
    say(*outcome.lines)
    unexplained = len(outcome.unexplained_sources) + outcome.unexplained_fields
    say(f"== owner {scenario.user_id}, scenario {scenario.id}: {unexplained} "
        f"UNEXPLAINED; expected: {dict(outcome.expected) or 'none'}")
    if not (scenario.is_baseline and ledger.stored):
        return unexplained, None
    return unexplained, run_controls(scenario, calendar, skipped, outcome)


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
        try:
            if args.at is None:
                return measure(report)
            with time_machine.travel(args.at, tick=False):
                return measure(report)
        except HarnessError as exc:
            report.write(f"!  the run measured nothing: {exc}\n")
            return 2


if __name__ == "__main__":
    sys.exit(main())
