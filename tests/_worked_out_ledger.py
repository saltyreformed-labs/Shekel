"""The worked-out ledger against the stored one, per owner and scenario, at every grain.

The comparison X-ew's first leaf's production run reports (plan step plan#2513, plan task
plan#2528); ``tests/manual/verify_worked_out_ledger.py`` runs it over a production copy, plants
its controls and says what it found.  Per owner and scenario, :func:`compare` reports:

* **Every (chart row, paycheck, day) net on each side**, and every cell where they differ: the
  suite's own comparison (``tests._ledger_parity.ledger_parity_differences``).  The run reads
  both ledgers again at a finer grain, and that read must reproduce the suite's cells exactly or
  the run stops (:class:`HarnessError`).  Both map a line onto its chart account by the suite's
  one rule (``tests._ledger_parity.ParityChart``): a stored leg's is the row it sits on, a
  worked-out line's the owner's row for its key, so no two stored rows net in one cell.  The
  statements alone key a line by its chart key, summing two rows of one key; whatever that
  sums, the cell and source grains have already named apart.
* **Every difference by SOURCE** -- per movement, per correction kind, and the store's
  sourceless residue -- each classified as one of the differences the leaf's spec expects, or
  UNEXPLAINED.  A source grain sees what a cell cannot: two movements whose stored lines
  swapped paychecks net equal per cell.
* **The census of each expected kind**, with its count and dollars, whether or not it moves a
  cell: the hard-delete residue, the corrections filed in a paycheck today's calendar files
  elsewhere, the transfer families the cash resync skips, and the stored lines on rows it
  does not walk (design report s.9.3's list).
* **Every chart account's total** on each side.
* **The income statement for every paycheck, month and year, and the balance sheet at every
  paycheck end**, from the store and from the lines, field by field
  (:mod:`tests._worked_out_statements` says how a field is graded).
* **The labels** (ruling plan#2518, "statements show current names"): every stored counter
  and loan chart row whose snapshot name is not the worked-out chart's label, counted by
  cause, renamed or clipped at the column's length, as plan#2523's note asks.  Only those rows'
  statement labels can differ: a linked or category row's is already read live.

It writes nothing; the run's controls plant inside a savepoint of their own.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from collections import defaultdict, namedtuple
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
# Pylint: ``shekel-private-module-import`` -- the clause the cash resync
# filters its walked rows by, named rather than restated: a harness that
# restated it would grade its own copy.
# pylint: disable-next=shekel-private-module-import
from app.services._posting_purchases import posted_purchase_exists_clause
from app.services.ledger_lines import (
    ACCOUNT_COUNTER_KINDS,
    LOAN_KINDS,
    chart_label,
    worked_out_lines,
)
from app.utils.balance_predicates import settled_status_ids
from tests._ledger_parity import ledger_parity_differences, read_parity_chart
from tests._worked_out_statements import key_text, money, report_statements

_ZERO = Decimal("0.00")
# The stored chart's ``name`` column length, the clip a snapshot label takes.
_LABEL_LIMIT = LedgerAccount.__table__.c.name.type.length

# The source grains.  A movement-linked stored entry and a worked-out movement
# line are the movement's; a correction (opening, true-up, loan payment
# split) is its kind's, since a correction is derived and comes from no row;
# a stored entry of a movement kind that links nothing is hard-delete residue.
_MOVEMENT = "movement"
_CORRECTION = "correction"
RESIDUE = "residue"

# The expected kinds of difference (the leaf's spec, "The proof", item 2).
_EXPECTED_RESIDUE = "hard-delete residue"
_EXPECTED_FILING = "correction filed in another paycheck"
_EXPECTED_SKIPPED = "transfer family the cash resync skips"
_EXPECTED_UNWALKED = "row the cash resync does not walk"


class HarnessError(RuntimeError):
    """The run cannot grade anything; the message says why (exit 2)."""


# One stored posting or one worked-out line, at source grain.  ``source`` is
# ``(grain, id)``: a movement's id, or a correction's or residue's source kind
# id.  ``key`` and ``rows`` are its chart account
# (``tests._ledger_parity.ParityChart``): the stored row it sits on, or the
# owner's stored row for a worked-out line's key, ``()`` for none.
# ``attributed_on`` is the day a calendar window or a balance sheet
# places it on (the store's reader: a movement's bank day, a correction's own
# day; ``None`` for residue, which it drops); ``entry_id`` is the stored
# journal entry, ``None`` for a worked-out line.
Contribution = namedtuple(
    "Contribution",
    "source key rows pay_period_id entry_date amount attributed_on entry_id",
)


@dataclass
class Ledger:  # pylint: disable=too-many-instance-attributes
    """Both sides of one owner's ledger in one scenario, read once.

    Pylint: ``too-many-instance-attributes`` (10/7) -- a record of one read,
    every field of which a reporter reads; grouping them would only rename
    them.

    Attributes:
        owner_id: The owner.
        scenario_id: The scenario.
        is_baseline: Whether the scenario is the owner's baseline, the one
            the statements read.
        calendar: The owner's pay calendar.
        stored: Every stored posting, as a :data:`Contribution`.
        worked: Every worked-out line, as a :data:`Contribution`.
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
        unexplained_fields: How many statement fields part from the store
            beyond the expected differences in their window.
        expected: Each expected kind's source-cell count.
    """

    lines: list = field(default_factory=list)
    unexplained_sources: set = field(default_factory=set)
    unexplained_fields: int = 0
    expected: dict = field(default_factory=lambda: defaultdict(int))


def _source_text(source):
    """Return a source as a reader names it."""
    grain, ident = source
    if grain == _MOVEMENT:
        return f"movement {ident}"
    return f"{grain} {_source_kind_names()[ident]}"


def _source_kind_names():
    """Return each posting source kind's name by its ref id."""
    return {
        ref_cache.posting_source_id(member): member.value
        for member in PostingSourceEnum
    }


def _correction_kind_ids():
    """Return the source kinds a correction is booked under.

    The five the store's statement reader keeps as sourceless corrections
    (``ledger_report_service._attribution._correction_dated_nets``).
    """
    return {
        ref_cache.posting_source_id(member)
        for member in (
            PostingSourceEnum.LOAN_OPENING,
            PostingSourceEnum.LOAN_TRUEUP,
            PostingSourceEnum.LOAN_PAYMENT,
            PostingSourceEnum.ACCOUNT_OPENING,
            PostingSourceEnum.ACCOUNT_TRUEUP,
        )
    }


def _filed_by_day_kind_ids():
    """Return the correction kinds filed by their day on today's calendar.

    Every correction but a loan payment's split, which is filed in its
    payment's paycheck (ruling R-BAL102).  These are the kinds whose stored
    paycheck a calendar change can leave behind (ruling plan#2519).
    """
    return _correction_kind_ids() - {
        ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
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
    lines = worked_out_lines(scenario.user_id, scenario.id)
    movements = _movements(
        {row.transaction_entry_id for row in posted
         if row.transaction_entry_id is not None}
        | {line.movement_id for line in lines if line.movement_id is not None},
    )
    return Ledger(
        owner_id=scenario.user_id,
        scenario_id=scenario.id,
        is_baseline=scenario.is_baseline,
        calendar=calendar,
        stored=_stored_contributions(posted, chart, movements),
        worked=[
            Contribution(
                (_MOVEMENT, line.movement_id) if line.movement_id is not None
                else (_CORRECTION, line.source_kind_id),
                *chart.worked_account(line.key), line.pay_period_id,
                line.entry_date, line.amount, line.entry_date, None,
            )
            for line in lines
        ],
        chart=chart.rows,
        key_of=chart.key_of,
        parent_of={
            movement_id: parent
            for movement_id, (_, parent) in movements.items()
        },
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


def _stored_contributions(posted, chart, movements):
    """Return each stored posting as a :data:`Contribution`.

    Its source is its movement when it links one, its kind when that kind
    is a correction's, and residue otherwise; its ``attributed_on`` is the
    day the store's statements place it on: the movement's bank day, the
    correction's own day, none for residue (which they drop).

    Args:
        posted: :func:`_postings`' rows.
        chart: The :class:`~tests._ledger_parity.ParityChart` the comparison
            maps each leg's row by.
        movements: :func:`_movements`' map.
    """
    correction_kinds = _correction_kind_ids()
    stored = []
    for entry_id, kind, movement_id, pay_period_id, day, row_id, amount in posted:
        if movement_id is not None:
            source = (_MOVEMENT, movement_id)
            settled = movements.get(movement_id)
            attributed_on = None if settled is None else settled[0]
        elif kind in correction_kinds:
            source, attributed_on = (_CORRECTION, kind), day
        else:
            source, attributed_on = (RESIDUE, kind), None
        stored.append(Contribution(
            source, *chart.stored_account(row_id), pay_period_id, day, amount,
            attributed_on, entry_id,
        ))
    return stored


def _movements(movement_ids):
    """Return each movement's bank day and parent.

    Args:
        movement_ids: The ``budget.transaction_entries`` ids to read.

    Returns:
        ``{id: (settled_on, ("transaction" | "transfer", parent id))}``; an id
        no row holds is absent.
    """
    rows = (
        db.session.query(
            TransactionEntry.id,
            TransactionEntry.settled_on,
            TransactionEntry.transaction_id,
            TransactionEntry.expense_transfer_id,
            TransactionEntry.income_transfer_id,
        )
        .filter(TransactionEntry.id.in_(movement_ids))
        .all()
    )
    found = {}
    for movement_id, settled_on, txn_id, expense_side, income_side in rows:
        if txn_id is not None:
            parent = ("transaction", txn_id)
        else:
            parent = ("transfer", expense_side or income_side)
        found[movement_id] = (settled_on, parent)
    return found


def _walked_parents(scenario_id):
    """Return the parents the cash resync walks in *scenario_id*.

    Its two filters (``posting_service.resync_all_cash_postings``): a live
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
                posted_purchase_exists_clause(),
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


def _nets(contributions, grain):
    """Return each grain cell's net over *contributions*.

    Args:
        contributions: :data:`Contribution` s.
        grain: A function of one contribution giving its cell.

    Returns:
        ``{cell: net}``.
    """
    nets = defaultdict(lambda: _ZERO)
    for contribution in contributions:
        nets[grain(contribution)] += contribution.amount
    return nets


def _cell_of(contribution):
    """Return a contribution's (chart key, stored rows, paycheck, day) cell."""
    return (
        contribution.key, contribution.rows, contribution.pay_period_id,
        contribution.entry_date,
    )


def source_cell_of(contribution):
    """Return a contribution's (source, key, stored rows, paycheck, day) cell."""
    return (contribution.source,) + _cell_of(contribution)


def _paired(stored, worked):
    """Return ``{cell: (stored net, worked-out net)}`` over both maps' cells."""
    return {
        cell: (stored.get(cell, _ZERO), worked.get(cell, _ZERO))
        for cell in stored.keys() | worked.keys()
    }


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
    cells = _paired(
        _nets(ledger.stored, _cell_of), _nets(ledger.worked, _cell_of),
    )
    _check_against_the_suite(ledger, cells)
    _report_cells(cells, out)
    differing = _source_differences(ledger)
    expected = _classify(ledger, differing, skipped)
    _report_sources(differing, expected, outcome)
    _report_census(ledger, skipped, out)
    _report_totals(cells, out)
    if ledger.is_baseline:
        _report_statements(ledger, expected, outcome)
    else:
        out("-- statements: not read (the store's statements read the "
            "baseline only)")
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
        cell + nets for cell, nets in cells.items() if nets[0] != nets[1]
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


def _report_cells(cells, out):
    """Print every cell's two nets, and every cell where they part."""
    parted = 0
    out(f"-- cells: {len(cells)} (chart row, paycheck, day) cells; each "
        "line: stored net | worked-out net")
    for cell in sorted(cells, key=_cell_order):
        stored, worked = cells[cell]
        key, rows, pay_period_id, day = cell
        mark = "  "
        if stored != worked:
            mark, parted = "! ", parted + 1
        out(f"{mark} {_account_text(key, rows)} "
            f"paycheck {pay_period_id} day {day.isoformat()}: "
            f"{money(stored)} | {money(worked)}")
    zero_only = sum(
        1 for stored, worked in cells.values() if stored == worked == 0
    )
    out(f"-- cells: {parted} part; {zero_only} net $0.00 on both sides "
        "(stored entries reversed, or legs cancelling within one day)")


def _source_differences(ledger):
    """Return every source cell whose two nets part.

    Returns:
        ``{source cell: (stored net, worked-out net)}``.
    """
    return {
        cell: nets
        for cell, nets in _paired(
            _nets(ledger.stored, source_cell_of),
            _nets(ledger.worked, source_cell_of),
        ).items()
        if nets[0] != nets[1]
    }


def _residue_is_zero_sum(ledger):
    """Return whether the store's residue nets to $0.00 on every chart account."""
    residue = _nets(
        (c for c in ledger.stored if c.source[0] == RESIDUE),
        lambda c: (c.key, c.rows),
    )
    return all(net == 0 for net in residue.values())


def _classify(ledger, differing, skipped):
    """Return each differing source cell's expected kind, or ``None``.

    * residue: expected while the residue nets to zero on every chart
      account -- a reversal pair -- since the statements drop it whole;
      residue that does not net to zero is money the records lost;
    * a movement: expected when its parent is a transfer the resync skips,
      or a row it does not walk (design report s.9.3);
    * a correction: expected when its kind, chart account and day net to
      zero across paychecks -- the same correction, filed in another
      paycheck.  The worked-out side files it by today's calendar (ruling
      plan#2519), so the store's is the paycheck the calendar gave when it
      was written.

    Returns:
        ``{source cell: kind name | None}``.
    """
    zero_sum = _residue_is_zero_sum(ledger)
    moved = _nets(
        (
            Contribution(*cell, amount=stored - worked, attributed_on=None,
                         entry_id=None)
            for cell, (stored, worked) in differing.items()
            if cell[0][0] == _CORRECTION
        ),
        lambda c: (c.source, c.key, c.rows, c.entry_date),
    )
    kinds = {}
    for cell in differing:
        (grain, ident), key, rows, _pay_period_id, day = cell
        if grain == RESIDUE:
            kinds[cell] = _EXPECTED_RESIDUE if zero_sum else None
        elif grain == _MOVEMENT:
            kinds[cell] = _movement_kind(ledger, ident, skipped)
        else:
            kinds[cell] = (
                _EXPECTED_FILING if moved[((grain, ident), key, rows, day)] == 0
                else None
            )
    return kinds


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
        out(f"{'~ ' if kind else '! '} {_source_text(source)} "
            f"{_account_text(key, rows)} paycheck {pay_period_id} "
            f"day {day.isoformat()}: {money(stored)} | {money(worked)} -- "
            f"{kind or 'UNEXPLAINED'}")


def _movement_kind(ledger, movement_id, skipped):
    """Return the expected kind a movement's difference is, or ``None``.

    A transfer the cash resync skips, or a row it does not walk (design
    report s.9.3): the store's lines on either rest on a door's own sync.
    """
    parent = ledger.parent_of.get(movement_id)
    if parent is None:
        return None
    if parent[0] == "transfer" and parent[1] in skipped:
        return _EXPECTED_SKIPPED
    if parent not in ledger.walked:
        return _EXPECTED_UNWALKED
    return None


def _census_kind(ledger, contribution, skipped, filed_by_day):
    """Return the expected kind a stored entry belongs to, by its first leg.

    Residue; a correction filed outside the paycheck today's calendar files
    its day in; or a movement of :func:`_movement_kind`'s.  ``None`` for any
    other entry.
    """
    grain, ident = contribution.source
    if grain == RESIDUE:
        return _EXPECTED_RESIDUE
    if grain == _MOVEMENT:
        return _movement_kind(ledger, ident, skipped)
    filed = ledger.calendar.filing_period(contribution.entry_date).period_id
    if ident in filed_by_day and contribution.pay_period_id != filed:
        return _EXPECTED_FILING
    return None


def _report_census(ledger, skipped, out):
    """Print each expected kind's stored entries, with count and dollars."""
    entries = defaultdict(list)
    for contribution in ledger.stored:
        entries[contribution.entry_id].append(contribution)
    filed_by_day = _filed_by_day_kind_ids()
    held = defaultdict(list)
    for entry_id in sorted(entries):
        kind = _census_kind(ledger, entries[entry_id][0], skipped, filed_by_day)
        if kind is not None:
            held[kind].append(entries[entry_id])
    out(f"-- census of the expected kinds (transfer families the resync "
        f"skips: {sorted(skipped) or 'none'})")
    for kind in (_EXPECTED_RESIDUE, _EXPECTED_FILING, _EXPECTED_SKIPPED,
                 _EXPECTED_UNWALKED):
        _report_census_kind(kind, held[kind], out)


def _report_census_kind(kind, entries, out):
    """Print one expected kind's stored entries, their dollars and their cells.

    Args:
        kind: The kind's name.
        entries: Each of its stored entries, as its list of contributions.
        out: Where each line of the report goes.
    """
    legs = [leg for entry in entries for leg in entry]
    by_cell = _nets(legs, _cell_of)
    moving = sum(1 for net in by_cell.values() if net != 0)
    debited = sum((leg.amount for leg in legs if leg.amount > 0), _ZERO)
    out(f"   {kind}: {len(entries)} stored entr(y/ies), {money(debited)} "
        f"debited, nets {money(sum(by_cell.values(), _ZERO))}; {moving} of "
        f"its {len(by_cell)} cell(s) net nonzero")
    for entry in entries:
        first = entry[0]
        shown = ", ".join(
            f"{_account_text(c.key, c.rows)} {money(c.amount)}" for c in entry
        )
        out(f"     entry {first.entry_id} {_source_text(first.source)} "
            f"paycheck {first.pay_period_id} day "
            f"{first.entry_date.isoformat()}: {shown}")


def _report_totals(cells, out):
    """Print every chart account's total on each side."""
    totals = defaultdict(lambda: [_ZERO, _ZERO])
    for (key, rows, _pay_period_id, _day), (stored, worked) in cells.items():
        totals[(key, rows)][0] += stored
        totals[(key, rows)][1] += worked
    out(f"-- account totals: {len(totals)} chart account(s); each line: "
        "stored | worked-out")
    for account in sorted(
        totals, key=lambda account: _cell_order((*account, 0, date.min)),
    ):
        stored, worked = totals[account]
        out(f"{'! ' if stored != worked else '  '} {_account_text(*account)} "
            f"{_label_of(account[0])}: {money(stored)} | {money(worked)}")


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


# -- statements -------------------------------------------------------------

def _report_statements(ledger, expected, outcome):
    """Compare every statement on both sides into *outcome*.

    The comparison is :func:`tests._worked_out_statements.report_statements`,
    handed the contributions of every source cell whose difference is an
    expected one.

    Args:
        ledger: The :class:`Ledger`.
        expected: Each differing source cell's expected kind, or ``None``.
        outcome: The :class:`Outcome` to print into and tally.
    """
    lines, unexplained = report_statements(ledger, [
        c for c in ledger.stored + ledger.worked
        if expected.get(source_cell_of(c)) is not None
    ])
    outcome.lines.extend(lines)
    outcome.unexplained_fields += unexplained


# -- labels -----------------------------------------------------------------

def _report_labels(ledger, out):
    """Print every counter and loan row whose stored label is not today's.

    plan#2523's note: those rows snapshot ``"<account> -- <component>"`` when
    minted and clip it to the column's length; the worked-out chart reads
    today's name and clips nothing.
    """
    kinds = {
        ref_cache.ledger_account_kind_id(member)
        for member in ACCOUNT_COUNTER_KINDS | LOAN_KINDS
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
