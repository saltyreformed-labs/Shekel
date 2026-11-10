"""The worked-out ledger against the stored one, per owner and scenario, at every grain.

The comparison X-ew's first leaf's production run reports (plan step plan#2513, plan task
plan#2528); ``tests/manual/verify_worked_out_ledger.py`` ran it over a production copy, planted
its controls and said what it found, until plan task plan#3077 deleted it.  Per owner and
scenario, :func:`compare` reports:

* **Every (chart row, paycheck, day) net on each side**, and every cell where they differ: the
  suite's own comparison (``tests._ledger_parity.ledger_parity_differences``).  The run reads
  both ledgers again at a finer grain, and that read must reproduce the suite's cells exactly or
  the run stops (:class:`HarnessError`).  Both map a line onto its chart account by the suite's
  one rule (``tests._ledger_parity.ParityChart``): a stored leg's is the row it sits on, a
  worked-out line's the owner's row for its key, so no two stored rows net in one cell.
* **Every difference by SOURCE** -- per movement, per correction kind, and the store's
  sourceless residue -- each classified as one of the differences the leaf's spec expects, or
  UNEXPLAINED.  A source grain sees what a cell cannot: two movements whose stored lines
  swapped paychecks net equal per cell.
* **The census of each expected kind**, with its count and dollars, whether or not it moves a
  cell: the hard-delete residue, the corrections filed in a paycheck today's calendar files
  elsewhere, the transfer families the cash resync skips, and the stored lines on rows it
  does not walk (design report s.9.3's list).
* **Every chart account's total** on each side.
* **The labels** (ruling plan#2518, "statements show current names"): every stored counter
  and loan chart row whose snapshot name is not the worked-out chart's label, counted by
  cause, renamed or clipped at the column's length, as plan#2523's note asks.  Only those rows'
  statement labels can differ: a linked or category row's is already read live.

**Which differences are expected, and the one place one is excused**, is
:mod:`tests._worked_out_excuses`: an excuse is granted only where a censused stored entry of its
kind sits, and only by that kind's rule.  **Every grain reaches the exit code**: a source cell, a
cell or an account total that parts beyond the excused source cells is UNEXPLAINED
(:attr:`Outcome.unexplained`).  Only the source grain can FIND one: a cell's or a total's
remainder is the sum of its unexcused source cells, so either is UNEXPLAINED only where a source
cell already is, and is reported for the money each one moves.

**It reads no statement** (plan task plan#2791).  Its statement half compared the app's report
over the store with its own over the lines, and lost its stored side when the report moved onto
the lines (plan task plan#2771).  The two reports are compared by
``tests/manual/run_statements_on_dump.py`` (plan task plan#2773).

It writes nothing; the run's controls plant inside a savepoint of their own.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import or_

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.ledger_lines import chart_label, worked_out_lines
# The two families' kind tables are the chart's own, package-private since plan
# task plan#3155 deleted their public sets (the stored chart's writer filed the
# two under different columns); a family filter reads them by name so the fact
# keeps one spelling.
from app.services.ledger_lines._chart import _ACCOUNT_COUNTER_KINDS, _LOAN_KINDS
from app.utils.balance_predicates import settled_status_ids
from tests._ledger_parity import ledger_parity_differences, read_parity_chart
from tests._worked_out_excuses import (
    CORRECTION,
    EXPECTED_KINDS,
    MOVEMENT,
    RESIDUE,
    Contribution,
    account_of,
    cell_of,
    classify,
    correction_kind_ids,
    nets,
    paired,
    source_cell_of,
    take_census,
)

_ZERO = Decimal("0.00")
# The stored chart's ``name`` column length, the clip a snapshot label takes.
_LABEL_LIMIT = LedgerAccount.__table__.c.name.type.length


class HarnessError(RuntimeError):
    """The run cannot grade anything; the message says why (exit 2)."""


def money(value):
    """Return *value* as a two-place string, never a rounded one."""
    return f"{Decimal(value):.2f}"


def key_text(key):
    """Return a chart key as ``kind/class/record``."""
    return f"{key.kind.value}/{key.ledger_class.value}/{key.record_id}"


def mark(unexplained, parts):
    """Return the mark a report line opens with, for any grain.

    ``!`` an UNEXPLAINED difference (which may hide inside agreeing figures,
    an excused difference and an unexplained one cancelling), ``~`` one that
    parts by an expected difference alone, blank one that agrees.

    Args:
        unexplained: Whether the line's difference is UNEXPLAINED.
        parts: Whether its two figures part.
    """
    if unexplained:
        return "! "
    return "~ " if parts else "  "


@dataclass
class Ledger:  # pylint: disable=too-many-instance-attributes
    """Both sides of one owner's ledger in one scenario, read once.

    Pylint: ``too-many-instance-attributes`` (10/7) -- a record of one read,
    every field of which a reporter reads; grouping them would only rename
    them.

    Attributes:
        owner_id: The owner.
        scenario_id: The scenario.
        is_baseline: Whether the scenario is the owner's baseline.
        calendar: The owner's pay calendar.
        stored: Every stored posting, as a
            :data:`~tests._worked_out_excuses.Contribution`.
        worked: Every worked-out line, as a
            :data:`~tests._worked_out_excuses.Contribution`.
        chart: The stored chart rows the owner's postings sit on, and the
            owner's whole chart, by id.
        key_of: Each of those rows' chart key, read as stored.
        parent_of: Each movement either side names, mapped to its parent,
            ``("transaction", id)`` or ``("transfer", id)``.
        walked: The parents the cash resync walks in this scenario.
    """

    owner_id: int
    scenario_id: int
    is_baseline: bool
    calendar: object
    stored: list
    worked: list
    chart: dict
    key_of: dict
    parent_of: dict
    walked: set


@dataclass
class Outcome:
    """What one owner's comparison found, and the lines that say so.

    Attributes:
        lines: The report's lines for this owner and scenario.
        unexplained_sources: Every source cell ``(source, key, stored rows,
            paycheck, day)`` whose difference is no expected kind.
        unexplained_cells: Every ``(key, stored rows, paycheck, day)`` cell
            whose two nets part beyond its excused source cells.
        unexplained_totals: Every chart account ``(key, stored rows)`` whose
            two totals part beyond them.
        expected: Each expected kind's excused source-cell count.
        censused: Each stored entry the census holds, by id: its expected
            kind.
    """

    lines: list = field(default_factory=list)
    unexplained_sources: set = field(default_factory=set)
    unexplained_cells: set = field(default_factory=set)
    unexplained_totals: set = field(default_factory=set)
    expected: dict = field(default_factory=lambda: defaultdict(int))
    censused: dict = field(default_factory=dict)

    @property
    def unexplained(self):
        """Return how many things are UNEXPLAINED, at every grain: any fails the run."""
        return (
            len(self.unexplained_sources) + len(self.unexplained_cells)
            + len(self.unexplained_totals)
        )


def _source_text(source):
    """Return a source as a reader names it."""
    grain, ident = source
    if grain == MOVEMENT:
        return f"movement {ident}"
    return f"{grain} {_source_kind_names()[ident]}"


def _source_kind_names():
    """Return each posting source kind's name by its ref id."""
    return {
        ref_cache.posting_source_id(member): member.value
        for member in PostingSourceEnum
    }


def read_ledger(scenario, calendar):
    """Read both sides of *scenario*'s owner's ledger at source grain.

    Args:
        scenario: The :class:`Scenario`.
        calendar: Its owner's pay calendar.

    Returns:
        The :class:`Ledger`.
    """
    posted = _postings(scenario.user_id, scenario.id)
    chart = read_parity_chart(
        db.session, scenario.user_id,
        {row.ledger_account_id for row in posted},
    )
    lines = worked_out_lines(scenario.user_id, scenario.id, lambda: calendar)
    return Ledger(
        owner_id=scenario.user_id,
        scenario_id=scenario.id,
        is_baseline=scenario.is_baseline,
        calendar=calendar,
        stored=_stored_contributions(posted, chart),
        worked=[
            Contribution(
                (MOVEMENT, line.movement_id) if line.movement_id is not None
                else (CORRECTION, line.source_kind_id),
                *chart.worked_account(line.key), line.pay_period_id,
                line.entry_date, line.amount, None,
            )
            for line in lines
        ],
        chart=chart.rows,
        key_of=chart.key_of,
        parent_of=movement_parents(
            {row.transaction_entry_id for row in posted
             if row.transaction_entry_id is not None}
            | {line.movement_id for line in lines
               if line.movement_id is not None},
        ),
        walked=_walked_parents(scenario.id),
    )


def _postings(owner_id, scenario_id):
    """Return every stored posting of the owner's in the scenario, with its entry.

    Read by the store's own columns, as ``tests._ledger_parity`` reads them,
    but per entry rather than per cell.

    Returns:
        Rows of ``(id, source_kind_id, transaction_entry_id, pay_period_id,
        entry_date, ledger_account_id, amount)``, by entry and posting id.
    """
    return (
        db.session.query(
            JournalEntry.id,
            JournalEntry.source_kind_id,
            JournalEntry.transaction_entry_id,
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
            Posting.ledger_account_id,
            Posting.amount,
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.user_id == owner_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .order_by(JournalEntry.id, Posting.id)
        .all()
    )


def _stored_contributions(posted, chart):
    """Return each stored posting as a :data:`~tests._worked_out_excuses.Contribution`.

    Its source is its movement when it links one, its kind when that kind
    is a correction's, and residue otherwise.

    Args:
        posted: :func:`_postings`' rows.
        chart: The :class:`~tests._ledger_parity.ParityChart` the comparison
            maps each leg's row by.
    """
    correction_kinds = correction_kind_ids()
    stored = []
    for entry_id, kind, movement_id, pay_period_id, day, row_id, amount in posted:
        if movement_id is not None:
            source = (MOVEMENT, movement_id)
        elif kind in correction_kinds:
            source = (CORRECTION, kind)
        else:
            source = (RESIDUE, kind)
        stored.append(Contribution(
            source, *chart.stored_account(row_id), pay_period_id, day, amount,
            entry_id,
        ))
    return stored


def movement_parents(movement_ids):
    """Return each movement's parent, by the rule the comparison reads.

    Args:
        movement_ids: The ``budget.transaction_entries`` ids to read.

    Returns:
        ``{id: ("transaction" | "transfer", parent id)}``; an id no row
        holds is absent.
    """
    rows = (
        db.session.query(
            TransactionEntry.id,
            TransactionEntry.transaction_id,
            TransactionEntry.expense_transfer_id,
            TransactionEntry.income_transfer_id,
        )
        .filter(TransactionEntry.id.in_(movement_ids))
        .all()
    )
    found = {}
    for movement_id, txn_id, expense_side, income_side in rows:
        if txn_id is not None:
            found[movement_id] = ("transaction", txn_id)
        else:
            found[movement_id] = ("transfer", expense_side or income_side)
    return found


def _holds_a_dated_debit_movement():
    """Return the SQL ``EXISTS`` "this transaction holds a dated debit movement".

    The cash resync's second filter on a transaction, a posted purchase: a
    recorded posting day, and a debit, correlated to ``Transaction``.  This
    harness named the resync's own clause
    (``app.services._posting_purchases.posted_purchase_exists_clause``) so it
    could not grade a copy of its own; the resync went at plan task plan#3077
    and left the clause no reader in ``app/``, so plan task plan#3132 gave this
    comparison its own spelling, the one that stays once the app's goes with
    the writers.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transaction``.
    """
    return (
        db.session.query(TransactionEntry.id)
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .exists()
    )


def _walked_parents(scenario_id):
    """Return the parents the cash resync walked in *scenario_id*.

    Its two filters (``posting_service.resync_all_cash_postings``, deleted at
    plan task plan#3077 with the deploy's hooks): a live
    transaction that is settled or holds a posted purchase, and a live
    settled transfer -- built from the same two clauses it filters by.

    Args:
        scenario_id: The scenario.

    Returns:
        ``{("transaction", id), ..., ("transfer", id), ...}``.
    """
    settled = settled_status_ids()
    transactions = (
        db.session.query(Transaction.id)
        .filter(
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            or_(
                Transaction.status_id.in_(settled),
                _holds_a_dated_debit_movement(),
            ),
        )
    )
    transfers = (
        db.session.query(Transfer.id)
        .filter(
            Transfer.scenario_id == scenario_id,
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled),
        )
    )
    return (
        {("transaction", ident) for (ident,) in transactions}
        | {("transfer", ident) for (ident,) in transfers}
    )


def moved_source_cells(before, after):
    """Return every source cell whose net moved between two reads, either side.

    What a control's plant changed (``tests/manual/verify_worked_out_ledger``, until plan#3077):
    the cells it must come out UNEXPLAINED at.

    Args:
        before: The unplanted :class:`Ledger`.
        after: The same owner's :class:`Ledger` read with the plant in.

    Returns:
        The source cells, ``(source, key, stored rows, paycheck, day)``.
    """
    moved = set()
    for old, new in ((before.stored, after.stored),
                     (before.worked, after.worked)):
        moved |= {
            cell for cell, (was, now) in paired(
                nets(old, source_cell_of), nets(new, source_cell_of),
            ).items()
            if was != now
        }
    return moved


def _cell_order(cell):
    """Return a sort key for a cell ending in key, stored rows, paycheck, day."""
    key, rows, pay_period_id, day = cell[-4:]
    return (
        pay_period_id, day, key.kind.value, key.ledger_class.value,
        -1 if key.record_id is None else key.record_id, rows, str(cell[:-4]),
    )


def _account_text(key, rows):
    """Return a chart account as a reader names it: its stored rows and key."""
    shown = ",".join(f"#{row}" for row in rows)
    return f"row {shown or '(none stored)'} [{key_text(key)}]"


def compare(ledger, skipped):
    """Compare one owner's two ledgers in one scenario; the :class:`Outcome`.

    Args:
        ledger: The :class:`Ledger`.
        skipped: The transfer ids the cash resync skips.

    Returns:
        The :class:`Outcome`.

    Raises:
        HarnessError: The run's own cell read disagrees with the suite's.
    """
    outcome = Outcome()
    out = outcome.lines.append
    out(f"== owner {ledger.owner_id}, scenario {ledger.scenario_id}"
        f"{' (baseline)' if ledger.is_baseline else ''}")
    out(f"   stored postings {len(ledger.stored)}, worked-out lines "
        f"{len(ledger.worked)}")
    cells = paired(
        nets(ledger.stored, cell_of), nets(ledger.worked, cell_of),
    )
    _check_against_the_suite(ledger, cells)
    census = take_census(ledger, skipped)
    outcome.censused = {
        entry[0].entry_id: kind
        for kind, entries in census.items() for entry in entries
    }
    differing = _source_differences(ledger)
    expected = classify(ledger, differing, skipped, census)
    outcome.unexplained_cells = _unexplained(ledger, expected, cell_of)
    outcome.unexplained_totals = _unexplained(ledger, expected, account_of)
    _report_cells(cells, outcome.unexplained_cells, out)
    _report_sources(differing, expected, outcome)
    _report_census(census, outcome.expected, skipped, out)
    _report_totals(cells, outcome.unexplained_totals, out)
    _report_labels(ledger, out)
    return outcome


def _check_against_the_suite(ledger, cells):
    """Refuse a run whose cell read is not the suite's.

    Args:
        ledger: The :class:`Ledger`.
        cells: The run's ``{cell: (stored, worked out)}``.

    Raises:
        HarnessError: The two reads name different differing cells.
    """
    mine = {
        cell + pair for cell, pair in cells.items() if pair[0] != pair[1]
    }
    suites = {
        (cell.key, cell.stored_row_ids, cell.pay_period_id, cell.entry_date,
         cell.stored, cell.worked_out)
        for cell in ledger_parity_differences(db.session, ledger.scenario_id)
    }
    if mine != suites:
        raise HarnessError(
            f"scenario {ledger.scenario_id}: this run's cell read names "
            f"{sorted(map(str, mine - suites))} the suite's does not, and "
            f"misses {sorted(map(str, suites - mine))}"
        )


def _unexplained(ledger, expected, grain):
    """Return every *grain* cell whose two nets part beyond the expected differences.

    Each side's contributions to an excused source cell are set aside; a
    cell whose remaining nets still part holds a difference no expected kind
    explains -- even one whose whole nets agree, an excused difference and an
    unexplained one cancelling in it.

    Args:
        ledger: The :class:`Ledger`.
        expected: Each differing source cell's expected kind, or ``None``.
        grain: A function of one contribution giving its cell.

    Returns:
        The cells.
    """

    def unexcused(side):
        """Return *side*'s contributions to no excused source cell."""
        return [
            c for c in side if expected.get(source_cell_of(c)) is None
        ]

    return {
        cell for cell, (stored, worked) in paired(
            nets(unexcused(ledger.stored), grain),
            nets(unexcused(ledger.worked), grain),
        ).items()
        if stored != worked
    }


def _report_cells(cells, unexplained, out):
    """Print every cell's two nets, each marked, and how many part.

    Args:
        cells: ``{cell: (stored, worked out)}``.
        unexplained: The cells whose difference is UNEXPLAINED.
        out: Where each line of the report goes.
    """
    out(f"-- cells: {len(cells)} (chart row, paycheck, day) cells; each "
        "line: stored net | worked-out net")
    for cell in sorted(cells, key=_cell_order):
        stored, worked = cells[cell]
        key, rows, pay_period_id, day = cell
        out(f"{mark(cell in unexplained, stored != worked)} "
            f"{_account_text(key, rows)} "
            f"paycheck {pay_period_id} day {day.isoformat()}: "
            f"{money(stored)} | {money(worked)}")
    parted = sum(1 for stored, worked in cells.values() if stored != worked)
    zero_only = sum(
        1 for stored, worked in cells.values() if stored == worked == 0
    )
    out(f"-- cells: {parted} part, {len(unexplained)} UNEXPLAINED; "
        f"{zero_only} net $0.00 on both sides (stored entries reversed, or "
        "legs cancelling within one day)")


def _source_differences(ledger):
    """Return every source cell whose two nets part.

    Returns:
        ``{source cell: (stored net, worked-out net)}``.
    """
    return {
        cell: nets
        for cell, nets in paired(
            nets(ledger.stored, source_cell_of),
            nets(ledger.worked, source_cell_of),
        ).items()
        if nets[0] != nets[1]
    }


def _report_sources(differing, expected, outcome):
    """Print every differing source cell with its kind; tally the outcome."""
    out = outcome.lines.append
    out(f"-- sources: {len(differing)} source cell(s) part; each line: "
        "stored net | worked-out net")
    for cell in sorted(differing, key=_cell_order):
        stored, worked = differing[cell]
        source, key, rows, pay_period_id, day = cell
        kind = expected[cell]
        if kind is None:
            outcome.unexplained_sources.add(cell)
        else:
            outcome.expected[kind] += 1
        out(f"{mark(kind is None, True)} {_source_text(source)} "
            f"{_account_text(key, rows)} paycheck {pay_period_id} "
            f"day {day.isoformat()}: {money(stored)} | {money(worked)} -- "
            f"{kind or 'UNEXPLAINED'}")


def _report_census(census, excused, skipped, out):
    """Print each expected kind's stored entries and the source cells they excuse.

    Args:
        census: :func:`take_census`' entries.
        excused: Each kind's excused source-cell count.
        skipped: The transfer ids the cash resync skips.
        out: Where each line of the report goes.
    """
    out(f"-- census of the expected kinds (transfer families the resync "
        f"skips: {sorted(skipped) or 'none'})")
    for kind in EXPECTED_KINDS:
        _report_census_kind(kind, census[kind], excused.get(kind, 0), out)


def _report_census_kind(kind, entries, excused, out):
    """Print one expected kind's stored entries, their dollars and their cells.

    Args:
        kind: The kind's name.
        entries: Each of its stored entries, as its list of contributions.
        excused: How many differing source cells its entries excuse.
        out: Where each line of the report goes.
    """
    legs = [leg for entry in entries for leg in entry]
    by_cell = nets(legs, cell_of)
    moving = sum(1 for net in by_cell.values() if net != 0)
    # Per chart account, not overall: every entry is balanced, so its legs
    # together always net to $0.00 and would say nothing.
    standing = {
        account: net for account, net in nets(legs, account_of).items()
        if net != 0
    }
    debited = sum((leg.amount for leg in legs if leg.amount > 0), _ZERO)
    out(f"   {kind}: {len(entries)} stored entr(y/ies), {money(debited)} "
        f"debited; {len(standing)} chart account(s) net nonzero; {moving} of "
        f"its {len(by_cell)} cell(s) net nonzero; excuses {excused} source "
        "cell(s)")
    for account in sorted(
        standing, key=lambda account: _cell_order((*account, 0, date.min)),
    ):
        out(f"     nets {money(standing[account])} on "
            f"{_account_text(*account)}")
    for entry in entries:
        first = entry[0]
        shown = ", ".join(
            f"{_account_text(c.key, c.rows)} {money(c.amount)}" for c in entry
        )
        out(f"     entry {first.entry_id} {_source_text(first.source)} "
            f"paycheck {first.pay_period_id} day "
            f"{first.entry_date.isoformat()}: {shown}")


def _report_totals(cells, unexplained, out):
    """Print every chart account's total on each side, each marked.

    Args:
        cells: ``{cell: (stored, worked out)}``.
        unexplained: The chart accounts whose difference is UNEXPLAINED.
        out: Where each line of the report goes.
    """
    totals = defaultdict(lambda: [_ZERO, _ZERO])
    for (key, rows, _pay_period_id, _day), (stored, worked) in cells.items():
        totals[(key, rows)][0] += stored
        totals[(key, rows)][1] += worked
    out(f"-- account totals: {len(totals)} chart account(s), "
        f"{len(unexplained)} UNEXPLAINED; each line: stored | worked-out")
    for account in sorted(
        totals, key=lambda account: _cell_order((*account, 0, date.min)),
    ):
        stored, worked = totals[account]
        out(f"{mark(account in unexplained, stored != worked)} "
            f"{_account_text(*account)} {_label_of(account[0])}: "
            f"{money(stored)} | {money(worked)}")


def _label_of(key):
    """Return the worked-out chart's label for *key*, or why it has none."""
    if key.record_id is None:
        try:
            return chart_label(key, None)
        except ValueError:
            return "(a stored-only key: no record behind it)"
    if key.kind is LedgerAccountKindEnum.CATEGORY:
        record = db.session.get(Category, key.record_id)
    else:
        record = db.session.get(Account, key.record_id)
    return chart_label(key, record)


# -- labels -----------------------------------------------------------------

def _report_labels(ledger, out):
    """Print every counter and loan row whose stored label is not today's.

    plan#2523's note: those rows snapshot ``"<account> -- <component>"`` when
    minted and clip it to the column's length; the worked-out chart reads
    today's name and clips nothing.
    """
    kinds = {
        ref_cache.ledger_account_kind_id(member)
        for member in (*_ACCOUNT_COUNTER_KINDS, *_LOAN_KINDS)
    }
    rows = [
        row for row in ledger.chart.values()
        if row.user_id == ledger.owner_id and row.kind_id in kinds
    ]
    renamed, clipped = [], []
    for row in sorted(rows, key=lambda r: r.id):
        key = ledger.key_of[row.id]
        label = chart_label(key, db.session.get(Account, key.record_id))
        if row.name == label:
            continue
        clip = len(label) > _LABEL_LIMIT and row.name == label[:_LABEL_LIMIT]
        (clipped if clip else renamed).append((row, label))
    out(f"-- labels: {len(rows)} counter and loan row(s); {len(renamed)} "
        f"renamed since minted, {len(clipped)} clipped at {_LABEL_LIMIT} "
        "characters")
    for row, label in renamed + clipped:
        out(f"   row #{row.id} [{key_text(ledger.key_of[row.id])}]: stored "
            f"{row.name!r} | worked-out {label!r}")
