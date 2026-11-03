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
about.  That module's docstring says what each is and how it is graded, and
``tests._worked_out_excuses``' which differences are expected: an excuse is granted only where
a censused stored entry of its kind sits, by that kind's rule, and a difference at any grain --
source cell, cell, account total or statement field -- that no excuse explains fails the run.

**First it re-runs the deploy's cash resync and rolls it back**
(``posting_service.resync_all_cash_postings``): its count says whether the store is at target,
and its own skip warning names the transfer families it skips, read rather than restated.

**Then eight planted controls, each SHOWN to fire** (the standard's rule 4): two the records do
not hold -- a stored movement line and a stored correction -- and one just outside each condition
of an excuse rule that a plant can reach (review plan#2521, findings C-H2 and C-L2, and its
second review's H1 and M1):

* residue: a sourceless reversal pair split across two paychecks, netting $0.00 on each account
  and in no cell;
* a re-filed correction: a true-up the store re-files, which the read files in a third paycheck
  rather than today's; and one the read files in today's paycheck at another amount;
* a skipped family: a stored line on a transfer family the resync does not skip;
* an unwalked row: store-only lines reversed to $0.00 that the read books; and a store-only line
  nothing reversed.

The rules before review plan#2521 excused four of these (the split residue pair, the read's
misfiled true-up and both unwalked-row plants); the two general controls, the family not skipped
and the true-up at another amount sit outside conditions those rules already had.  Each is
planted in a savepoint -- a read-side plant by patching the read in both its readers -- measured
and rolled back.  It fires only when every source cell it moved comes out UNEXPLAINED and nothing
else newly does, the census names its planted entries as the control states (the kind whose rule
it sits outside of, or no expected kind), and a statement field parts where its plant shows in
one.  Nothing here grades the INSIDE of a rule: a rule that excuses too little fails loudly, an
expected difference coming out UNEXPLAINED.  ``tests._worked_out_excuses`` names the one
condition no plant reaches.

**What a clean run does NOT say.**  The read and the writers apply the same target rules (plan
task plan#2526 moved them into ``app.services.ledger_lines``), so a wrong rule moves both sides
alike; the suite pins the rules by hand-computed literals.  This says the store, as
production's records and this tree's deploy leave it, is the ledger the records work out to.
**Nor is "every statement field agreed" the proof "every report byte-identical"** (review
plan#2521, finding C-L1; ``docs/plans/verification.md``; design report s.12.2): the statements it
works out are its own re-implementation over the lines (``tests._worked_out_statements``), graded
against the app's report over the store.  The app's report reading the lines, against itself
reading the store, is leaf 2's proof (plan step plan#2515); the report's header says so too.

**Usage** (from the repository root, on a copy nothing else uses)::

    DATABASE_URL=postgresql://.../copy \\
        .venv/bin/python tests/manual/verify_worked_out_ledger.py \\
        --report report.txt [--at 2026-11-02T12:00:00-05:00]

The report goes to its own file, since the app logs to stdout.  ``--at`` pins the clock
(``time_machine``, no tick) at the instant the copy's deploy step ran under.  It runs under the
developer's venv, not a release side's: the suite's helpers need ``pytest``.  Production
figures stay out of the repository (ruling R-BAL132): keep the report in the handoff folder.

Exit status: 0 every difference is an expected one and every control fired; 1 a source cell, a
cell, an account total or a statement field is UNEXPLAINED, each named in the report; 2 the run
measured nothing (a control did not fire, or the run's own read disagreed with the suite's).
Outside pytest's collection, like its ``verify_*`` siblings.
"""

import argparse
import logging
import pathlib
import sys
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from unittest import mock

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` and ``tests`` are not importable without this; the
# same bootstrap as ``verify_statement_baseline``.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
import time_machine
from sqlalchemy import or_, select

from app import create_app, ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import posting_service
from app.services.ledger_lines import (
    LedgerLine,
    purchase_source,
    worked_out_lines,
)
from app.services.pay_calendar import PayCalendarError, calendar_for
from app.services.transfer_legs import movement_parent
from tests import _ledger_parity, _worked_out_ledger
from tests._worked_out_excuses import (
    EXPECTED_FILING,
    EXPECTED_RESIDUE,
    EXPECTED_UNWALKED,
    filed_in,
)
from tests._worked_out_ledger import (
    HarnessError,
    compare,
    moved_source_cells,
    movement_parents,
    read_ledger,
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
# What the report says before anything it measured (review plan#2521,
# finding C-L1): what its statements grade, and what they cannot.
_HEADER = (
    "verify_worked_out_ledger: the worked-out ledger against the stored one "
    "(plan step plan#2513)",
    "   statements: worked out by tests._worked_out_statements, its own "
    "re-implementation of the store's report over the lines, graded against "
    "the app's report over the STORE.  Every field agreeing is NOT 'every "
    "report byte-identical' (docs/plans/verification.md; design report "
    "s.12.2): that is the app's report reading the lines, leaf 2's proof "
    "(plan step plan#2515, verify_statement_baseline.py before against "
    "after).",
)


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

# Each entry a plant writes copies a template's journal header, changed as its
# plant says; a read-side plant files and dates its lines by one too.
Header = namedtuple(
    "Header",
    "user_id scenario_id pay_period_id entry_date source_kind_id "
    "transaction_entry_id",
)

# What the plants copy (:func:`_templates`): a stored entry on a transaction
# the resync walks with a leg on a category row, so a plant of it moves an
# income statement even where nothing dates it; one on a transfer the resync
# walks and does not skip; an account true-up filed in the paycheck today's
# calendar files its day in; and ``(movement id, parent)``, a movement on a
# row the resync does not walk.
Templates = namedtuple("Templates", "purchase transfer trueup unwalked")

# What a control runs against: the baseline scenario, its owner's calendar,
# the transfer ids the resync skips, the unplanted :class:`Ledger` and its
# :class:`Outcome`, and the :data:`Templates`.
Bench = namedtuple("Bench", "scenario calendar skipped ledger outcome use")


def _header(entry, **changes):
    """Return *entry*'s journal header as a :data:`Header`, with *changes*."""
    return Header(
        entry.user_id, entry.scenario_id, entry.pay_period_id,
        entry.entry_date, entry.source_kind_id, entry.transaction_entry_id,
    )._replace(**changes)


def _plant(header, legs, amount):
    """Add one entry of *header*, balanced on two chart rows, and flush it.

    *amount* on the first of *legs*' rows and its negative on the second,
    each with its leg's posting kind: balanced, so it is the shape a writer
    could leave.  The control's savepoint takes it away again before
    anything commits.

    Args:
        header: The entry's :data:`Header`.
        legs: Two stored postings, on two chart rows, to plant on.
        amount: What the first leg carries.

    Returns:
        The planted entry's id.
    """
    entry = JournalEntry(
        **header._asdict(), description="verify_worked_out_ledger control",
    )
    for leg, sign in zip(legs, (1, -1)):
        entry.postings.append(Posting(
            ledger_account_id=leg.ledger_account_id,
            amount=amount * sign,
            posting_kind_id=leg.posting_kind_id,
        ))
    db.session.add(entry)
    db.session.flush()
    return entry.id


def _read_lines(ledger, legs, header, amount):
    """Return the worked-out lines a read-side plant adds: *legs*' twins.

    *amount* on the first leg's chart key and its negative on the second,
    filed, dated and sourced as *header* says.

    Args:
        ledger: The unplanted :class:`Ledger`, whose chart names each key.
        legs: Two stored postings, on two chart rows.
        header: The :data:`Header` the lines are filed, dated and sourced by.
        amount: What the first line carries.

    Returns:
        Two :class:`LedgerLine` s.
    """
    return [
        LedgerLine(
            key=ledger.key_of[leg.ledger_account_id],
            pay_period_id=header.pay_period_id,
            entry_date=header.entry_date,
            amount=amount * sign,
            source_kind_id=header.source_kind_id,
            posting_kind_id=leg.posting_kind_id,
            movement_id=header.transaction_entry_id,
        )
        for leg, sign in zip(legs, (1, -1))
    ]


@contextmanager
def _reading(rewrite):
    """Read the worked-out lines through *rewrite*, in both readers of them.

    The run's own read (``tests._worked_out_ledger``) and the suite's
    comparison it checks itself against (``tests._ledger_parity``) each
    import the read by name, so a read-side plant patches both: one patched
    alone would make the run's cross-check refuse (exit 2), never pass
    unseen.

    Args:
        rewrite: A function of the read's lines giving the planted lines, or
            ``None`` for a plant of stored entries alone.
    """
    if rewrite is None:
        yield
        return

    def planted(owner_id, scenario_id):
        """Return the worked-out read, rewritten by the plant."""
        return rewrite(worked_out_lines(owner_id, scenario_id))

    with mock.patch.object(_worked_out_ledger, "worked_out_lines", planted), \
            mock.patch.object(_ledger_parity, "worked_out_lines", planted):
        yield


def _other_paychecks(bench, count, *excluded):
    """Return *count* of the owner's paychecks, none of *excluded*, or refuse.

    Raises:
        HarnessError: The owner has too few paychecks to plant in.
    """
    others = [
        period.period_id for period in bench.calendar.periods
        if period.period_id is not None and period.period_id not in excluded
    ]
    if len(others) < count:
        raise HarnessError(
            f"owner {bench.ledger.owner_id} has {len(others)} paycheck(s) "
            f"beside {sorted(excluded)}, and a control plants in {count}"
        )
    return others[:count]


def _a_stored_purchase_line(bench):
    """Plant a copy of a walked purchase's entry: a line the records do not hold."""
    template = bench.use.purchase
    return [
        _plant(_header(template), _distinct_legs(template), _CONTROL_AMOUNT),
    ], None


def _a_stored_correction(bench):
    """Plant a copy of an account true-up: a correction the records do not hold."""
    template = bench.use.trueup
    return [
        _plant(_header(template), _distinct_legs(template), _CONTROL_AMOUNT),
    ], None


def _residue_split_across_paychecks(bench):
    """Plant a sourceless reversal pair whose two entries sit in two paychecks.

    It nets to $0.00 on each chart account, which the rule before review
    plan#2521 excused, and in neither cell: just outside the residue rule
    (finding C-L2), whose reversal pair nets to $0.00 inside its cell.
    """
    template = bench.use.purchase
    sourceless = _header(
        template,
        source_kind_id=ref_cache.posting_source_id(PostingSourceEnum.PURCHASE),
        transaction_entry_id=None,
    )
    (elsewhere,) = _other_paychecks(bench, 1, template.pay_period_id)
    legs = _distinct_legs(template)
    return [
        _plant(sourceless, legs, _CONTROL_AMOUNT),
        _plant(sourceless._replace(pay_period_id=elsewhere), legs,
               -_CONTROL_AMOUNT),
    ], None


def _a_correction_the_read_misfiles(bench):
    """Plant a true-up the store re-files and the read files in a third paycheck.

    The store's part, filed outside the paycheck today's calendar files its
    day in, is inside the re-filing rule; the read's twin, filed in a third
    paycheck rather than that one, puts it just outside (finding C-H2 (a)).
    The two still net equal across paychecks, which is all the rule before
    review plan#2521 asked.
    """
    template = bench.use.trueup
    stored_in, read_in = _other_paychecks(bench, 2, template.pay_period_id)
    legs = _distinct_legs(template)
    planted = _plant(
        _header(template, pay_period_id=stored_in), legs, _CONTROL_AMOUNT,
    )
    lines = _read_lines(
        bench.ledger, legs, _header(template, pay_period_id=read_in),
        _CONTROL_AMOUNT,
    )
    return [planted], lambda read: read + lines


def _a_refiled_correction_the_read_books_at_another_amount(bench):
    """Plant a true-up the store re-files and the read files today at another amount.

    The store's part, filed outside the paycheck today's calendar files its
    day in, and the read's twin, filed inside that one, are inside the
    re-filing rule but for their amounts: the read's is twice the store's,
    so the two do not net equal across paychecks, and the difference is
    money, not a paycheck (review plan#2521's second review, M1).
    """
    template = bench.use.trueup
    (stored_in,) = _other_paychecks(bench, 1, template.pay_period_id)
    legs = _distinct_legs(template)
    planted = _plant(
        _header(template, pay_period_id=stored_in), legs, _CONTROL_AMOUNT,
    )
    lines = _read_lines(
        bench.ledger, legs, _header(template), _CONTROL_AMOUNT * 2,
    )
    return [planted], lambda read: read + lines


def _a_stored_line_on_a_family_not_skipped(bench):
    """Plant a copy of a transfer side's entry on a family the resync walks.

    Just outside the skipped-family rule, which excuses only a family the
    resync skips.
    """
    template = bench.use.transfer
    return [
        _plant(_header(template), _distinct_legs(template), _CONTROL_AMOUNT),
    ], None


def _a_line_the_read_books_on_an_unwalked_row(bench):
    """Plant store-only lines on an unwalked row, reversed, that the read books.

    The two stored entries, a door's own sync posting a line and reversing
    it, are inside the unwalked-row rule; the read booking the line puts it
    just outside (finding C-H2 (b)), which the rule before review plan#2521
    excused.  The entries' source kind is the one the writer would stamp
    (``ledger_lines.purchase_source``).
    """
    movement_id, _parent = bench.use.unwalked
    template = bench.use.purchase
    kind = purchase_source(
        movement_parent(db.session.get(TransactionEntry, movement_id)),
    )
    header = _header(
        template, source_kind_id=ref_cache.posting_source_id(kind),
        transaction_entry_id=movement_id,
    )
    legs = _distinct_legs(template)
    planted = [
        _plant(header, legs, _CONTROL_AMOUNT),
        _plant(header, legs, -_CONTROL_AMOUNT),
    ]
    lines = _read_lines(bench.ledger, legs, header, _CONTROL_AMOUNT)
    return planted, lambda read: read + lines


def _a_store_only_line_nothing_reversed(bench):
    """Plant one store-only line on an unwalked row, which nothing reverses.

    The read books nothing for it, which is inside the unwalked-row rule;
    nothing reversing it puts it just outside (review plan#2521's second
    review, H1): it is money the store holds that the records do not.
    """
    movement_id, _parent = bench.use.unwalked
    template = bench.use.purchase
    kind = purchase_source(
        movement_parent(db.session.get(TransactionEntry, movement_id)),
    )
    header = _header(
        template, source_kind_id=ref_cache.posting_source_id(kind),
        transaction_entry_id=movement_id,
    )
    return [
        _plant(header, _distinct_legs(template), _CONTROL_AMOUNT),
    ], None


# A planted control: its title; the expected kind the census must name each
# entry it plants -- the rule it sits just outside of -- or ``None`` for one
# that must be no expected kind; how many source cells its plant moves;
# whether a statement field must part for it; and its plant, a function of
# the :data:`Bench` that writes its stored entries and returns ``(their ids,
# a rewrite of the read's lines or None)``.
Control = namedtuple("Control", "title at cells moves_statements plant")

_CONTROLS = (
    Control("a stored movement line the records do not hold", None, 2, True,
            _a_stored_purchase_line),
    Control("a stored correction the records do not hold", None, 2, True,
            _a_stored_correction),
    Control("residue netting to $0.00 per account, in no cell",
            EXPECTED_RESIDUE, 4, True, _residue_split_across_paychecks),
    Control("a re-filed correction the read files outside today's paycheck",
            EXPECTED_FILING, 4, False, _a_correction_the_read_misfiles),
    Control("a re-filed correction the read files at another amount",
            EXPECTED_FILING, 4, False,
            _a_refiled_correction_the_read_books_at_another_amount),
    Control("a stored line on a transfer family the resync does not skip",
            None, 2, True, _a_stored_line_on_a_family_not_skipped),
    Control("a line the read books on a row the resync does not walk",
            EXPECTED_UNWALKED, 2, True,
            _a_line_the_read_books_on_an_unwalked_row),
    Control("a store-only line on a row the resync does not walk, never "
            "reversed", EXPECTED_UNWALKED, 2, True,
            _a_store_only_line_nothing_reversed),
)


def _templates(ledger, skipped):
    """Return the :data:`Templates` the controls plant from, or refuse.

    Each the first of its kind by entry id (:func:`_template_kind`), the
    unwalked movement by its own id (:func:`_unwalked_movement`).

    Raises:
        HarnessError: The owner holds none of one, so its control cannot
            run; the message names which.
    """
    found = {}
    for entry in (
        db.session.query(JournalEntry)
        .filter(
            JournalEntry.user_id == ledger.owner_id,
            JournalEntry.scenario_id == ledger.scenario_id,
        )
        .order_by(JournalEntry.id)
    ):
        kind = _template_kind(ledger, skipped, entry)
        if kind is not None:
            found.setdefault(kind, entry)
    found["unwalked"] = _unwalked_movement(ledger)
    missing = [name for name in Templates._fields if found.get(name) is None]
    if missing:
        raise HarnessError(
            f"owner {ledger.owner_id} holds no {', '.join(missing)} to plant "
            "a control from"
        )
    return Templates(**{name: found[name] for name in Templates._fields})


def _template_kind(ledger, skipped, entry):
    """Return which of the :data:`Templates` *entry* can be, or ``None``.

    Only an entry with legs on two chart rows: a ``purchase`` on a walked
    transaction, one of them a category row; a ``transfer`` side on a walked
    family the resync does not skip; or a ``trueup``, an account true-up
    filed in the paycheck today's calendar files its day in.
    """
    if len({leg.ledger_account_id for leg in entry.postings}) < 2:
        return None
    parent = ledger.parent_of.get(entry.transaction_entry_id)
    if parent in ledger.walked:
        if parent[0] == "transaction" and any(
                ledger.key_of[leg.ledger_account_id].kind
                is LedgerAccountKindEnum.CATEGORY
                for leg in _distinct_legs(entry)):
            return "purchase"
        if parent[0] == "transfer" and parent[1] not in skipped:
            return "transfer"
        return None
    if entry.source_kind_id == ref_cache.posting_source_id(
            PostingSourceEnum.ACCOUNT_TRUEUP) and (
            entry.pay_period_id == filed_in(ledger, entry.entry_date)):
        return "trueup"
    return None


def _unwalked_movement(ledger):
    """Return the scenario's first movement on a row the resync does not walk.

    Returns:
        ``(movement id, parent)``, or ``None`` when every movement's parent
        is walked.
    """
    transactions = select(Transaction.id).where(
        Transaction.scenario_id == ledger.scenario_id,
    )
    transfers = select(Transfer.id).where(
        Transfer.scenario_id == ledger.scenario_id,
    )
    ids = [
        ident for (ident,) in db.session.query(TransactionEntry.id)
        .filter(
            TransactionEntry.owner_id == ledger.owner_id,
            or_(
                TransactionEntry.transaction_id.in_(transactions),
                TransactionEntry.expense_transfer_id.in_(transfers),
                TransactionEntry.income_transfer_id.in_(transfers),
            ),
        )
        .order_by(TransactionEntry.id)
    ]
    parents = movement_parents(ids)
    return next(
        (
            (movement_id, parents[movement_id]) for movement_id in ids
            if parents[movement_id] not in ledger.walked
        ),
        None,
    )


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
    ledger = read_ledger(scenario, calendar)
    bench = Bench(
        scenario, calendar, skipped, ledger, baseline,
        _templates(ledger, skipped),
    )
    lines, fired = [], True
    for control in _CONTROLS:
        line, ok = _run_control(bench, control)
        lines.append(f"   control '{control.title}' {line}")
        fired = fired and ok
    return lines, fired


def _run_control(bench, control):
    """Plant one control in a savepoint, measure, and roll it back.

    It FIRES when every source cell its plant moved -- as many as the control
    states -- comes out UNEXPLAINED, no other source cell newly does, the
    census names each entry it planted as the kind whose rule it sits just
    outside of (or as none), and, for a control a statement shows, a
    statement field comes out UNEXPLAINED beyond the run's own.

    Args:
        bench: The :data:`Bench`.
        control: The :data:`Control`.

    Returns:
        ``(the report's line, whether it fired)``.
    """
    savepoint = db.session.begin_nested()
    try:
        planted_ids, rewrite = control.plant(bench)
        with _reading(rewrite):
            planted = read_ledger(bench.scenario, bench.calendar)
            outcome = compare(planted, bench.skipped)
    finally:
        savepoint.rollback()
        db.session.expire_all()
    moved = moved_source_cells(bench.ledger, planted)
    named = moved & outcome.unexplained_sources
    others = (
        outcome.unexplained_sources - bench.outcome.unexplained_sources - moved
    )
    censused = [outcome.censused.get(entry_id) for entry_id in planted_ids]
    fields = outcome.unexplained_fields
    ok = (
        len(moved) == control.cells and named == moved and not others
        and all(kind == control.at for kind in censused)
        and (not control.moves_statements
             or fields > bench.outcome.unexplained_fields)
    )
    return (
        f"(planted entr(y/ies) {planted_ids}"
        f"{' and two read lines' if rewrite else ''}): "
        f"{'FIRED' if ok else 'DID NOT FIRE'}: {len(named)} of the "
        f"{len(moved)} source cell(s) it moved UNEXPLAINED (it moves "
        f"{control.cells}), {len(others)} other cell(s) newly UNEXPLAINED; "
        f"census {censused} (its rule's: {control.at}); {fields} unexplained "
        f"statement field(s) against the run's "
        f"{bench.outcome.unexplained_fields}"
        f"{'' if control.moves_statements else ' (none required)'}"
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

    say(*_HEADER)
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
    unexplained = outcome.unexplained
    say(f"== owner {scenario.user_id}, scenario {scenario.id}: {unexplained} "
        f"UNEXPLAINED ({len(outcome.unexplained_sources)} source cell(s), "
        f"{len(outcome.unexplained_cells)} cell(s), "
        f"{len(outcome.unexplained_totals)} account total(s), "
        f"{outcome.unexplained_fields} statement field(s)); excused: "
        f"{dict(outcome.expected) or 'none'}")
    if not (scenario.is_baseline and ledger.stored):
        return unexplained, None
    try:
        return unexplained, run_controls(scenario, calendar, skipped, outcome)
    except HarnessError as exc:
        # This owner's controls cannot run; every later owner is still
        # compared, and the run still exits 2 (its controls measured nothing).
        return unexplained, ([f"   controls REFUSED: {exc}"], False)


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
