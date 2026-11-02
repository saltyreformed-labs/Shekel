"""
Shekel Budget App -- the worked-out ledger against the stored one, cell by cell.

The comparison X-ew's first leaf proves its read by (plan step plan#2513): the
parity test (plan task plan#2527) runs it over the posting fixtures -- every
sweep of the posting reconciliation suites, and a world for each source kind
in ``tests/test_services/test_ledger_lines/test_parity_with_the_stored_ledger.py``
-- and the production run (plan task plan#2528) over a production copy.

Both ledgers are netted per (chart key, paycheck, entry day):

* **worked out**: :func:`app.services.ledger_lines.worked_out_lines`, each line
  summed under its own key, ``pay_period_id`` and ``entry_date``;
* **stored**: every ``budget.account_postings`` leg under a journal entry of the
  owner's (``journal_entries.user_id``) in the scenario, summed under its chart
  row's key (:func:`tests._test_helpers.stored_chart_key`, which reads the row
  as stored and repairs nothing), the entry's ``pay_period_id`` and its
  ``entry_date``.

**Nets are compared, never lines**: the read drops every ``$0.00`` line and the
store can hold a ``$0.00`` net (a reversal pair; two same-day true-ups that
cancel), so a cell netting ``$0.00`` on both sides is no difference, and a cell
one side lacks is ``$0.00`` there.

It reads the stored journal and chart directly, as the posting reconciliation
oracles do: the stored side is the second opinion, so it is read by its own
query rather than through a reader the store's own package exports.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from collections import namedtuple
from decimal import Decimal

from sqlalchemy import func, or_

from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.scenario import Scenario
from app.services.ledger_lines import worked_out_lines
from tests._test_helpers import stored_chart_key

_ZERO = Decimal("0.00")

# One (chart account, paycheck, day) cell at which the worked-out ledger and
# the stored one hold different nets: what :func:`ledger_parity_differences`
# returns and :func:`assert_ledger_parity` names.  ``stored_row_ids`` are the
# owner's stored chart rows that map onto ``key``: none when the store has no
# row for it, and more than one only for rows no worked-out key names (an
# orphan's, or a deleted category's), whose ``record_id`` is ``None``.
LedgerParityDifference = namedtuple(
    "LedgerParityDifference",
    "key stored_row_ids pay_period_id entry_date stored worked_out",
)


def ledger_parity_differences(db_session, scenario_id):
    """Return every cell where the worked-out ledger parts from the stored one.

    For the owner of *scenario_id*, in that scenario, by the module's
    comparison (see the module docstring).

    Args:
        db_session: The test ``db.session``.
        scenario_id: The ``budget.scenarios`` row to compare in; its owner is
            the ledger's.

    Returns:
        A list of :data:`LedgerParityDifference`, ordered by paycheck, day,
        kind, class and record; empty when the two agree to the cent at every
        cell.
    """
    owner_id = db_session.get(Scenario, scenario_id).user_id
    worked_out = _worked_out_nets(owner_id, scenario_id)
    stored, rows_of = _stored_nets(db_session, owner_id, scenario_id)
    differences = []
    for cell in stored.keys() | worked_out.keys():
        if stored.get(cell, _ZERO) == worked_out.get(cell, _ZERO):
            continue
        key, pay_period_id, entry_date = cell
        differences.append(LedgerParityDifference(
            key=key,
            stored_row_ids=tuple(sorted(rows_of.get(key, ()))),
            pay_period_id=pay_period_id,
            entry_date=entry_date,
            stored=stored.get(cell, _ZERO),
            worked_out=worked_out.get(cell, _ZERO),
        ))
    return sorted(differences, key=_parity_order)


def assert_ledger_parity(db_session, scenario_id, known=()):
    """Assert the worked-out ledger equals the stored one to the cent.

    Over every cell of the scenario owner's ledger in *scenario_id*
    (:func:`ledger_parity_differences`), but for the cells a caller names in
    *known*: a world whose fixture wrote a record directly, past every writer,
    leaves the store behind it, and the caller states each cell that leaves,
    by hand, rather than skip the comparison.  A known cell that does NOT
    differ as stated fails too, so a stated difference cannot go stale.  The
    message names each cell (:func:`_described_parity_difference`); the
    parity test's negative controls force each of its two sections to fire,
    so it is graded code rather than code a green suite never runs.

    Args:
        db_session: The test ``db.session``.
        scenario_id: The ``budget.scenarios`` row to compare in.
        known: The :data:`LedgerParityDifference` cells the caller expects,
            each exactly as :func:`ledger_parity_differences` would return
            it; none by default.
    """
    differences = set(ledger_parity_differences(db_session, scenario_id))
    sections = (
        ("parts from the stored one at {} cell(s) no caller expected",
         differences - set(known)),
        ("does not part from it as stated at {} cell(s) the caller expected",
         set(known) - differences),
    )
    failures = [
        f"the worked-out ledger in scenario {scenario_id} "
        + heading.format(len(cells)) + ":\n" + "\n".join(
            f"  {_described_parity_difference(cell)}"
            for cell in sorted(cells, key=_parity_order)
        )
        for heading, cells in sections
        if cells
    ]
    assert not failures, "\n".join(failures)


def _worked_out_nets(owner_id, scenario_id):
    """Return the worked-out ledger's net per (chart key, paycheck, day).

    Args:
        owner_id: The owner.
        scenario_id: The scenario.

    Returns:
        ``{(ChartKey, pay_period_id, entry_date): net}``.
    """
    nets = {}
    for line in worked_out_lines(owner_id, scenario_id):
        cell = (line.key, line.pay_period_id, line.entry_date)
        nets[cell] = nets.get(cell, _ZERO) + line.amount
    return nets


def _stored_nets(db_session, owner_id, scenario_id):
    """Return the stored ledger's net per (chart key, paycheck, day), and its rows.

    The chart read is the owner's whole chart, so a worked-out key can name
    the row it maps onto, and any row of another owner's that a posting of
    this owner's sits on -- a cross-owner leg, mapped all the same so it
    surfaces as a difference rather than a ``KeyError``.

    Args:
        db_session: The test ``db.session``.
        owner_id: The owner, whose journal entries are read by their
            ``user_id``.
        scenario_id: The scenario.

    Returns:
        ``({(ChartKey, pay_period_id, entry_date): net}, {ChartKey: [the
        owner's stored row ids mapping onto it]})``.
    """
    posted = (
        db_session.query(
            Posting.ledger_account_id,
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
            func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.user_id == owner_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .group_by(
            Posting.ledger_account_id,
            JournalEntry.pay_period_id,
            JournalEntry.entry_date,
        )
        .all()
    )
    chart = (
        db_session.query(LedgerAccount)
        .filter(or_(
            LedgerAccount.user_id == owner_id,
            LedgerAccount.id.in_({row_id for row_id, _, _, _ in posted}),
        ))
        .all()
    )
    key_of = {row.id: stored_chart_key(row) for row in chart}
    rows_of = {}
    for row in chart:
        if row.user_id == owner_id:
            rows_of.setdefault(key_of[row.id], []).append(row.id)
    nets = {}
    for row_id, pay_period_id, entry_date, net in posted:
        cell = (key_of[row_id], pay_period_id, entry_date)
        nets[cell] = nets.get(cell, _ZERO) + net
    return nets, rows_of


def _parity_order(difference):
    """Return the sort key a :data:`LedgerParityDifference` is listed by.

    Paycheck, day, then the key's kind, class and record (an owner bucket's
    ``None`` record first), so a list of cells reads in ledger order.

    Args:
        difference: The cell.

    Returns:
        The sort key.
    """
    key = difference.key
    return (
        difference.pay_period_id,
        difference.entry_date,
        key.kind.value,
        key.ledger_class.value,
        -1 if key.record_id is None else key.record_id,
    )


def _described_parity_difference(difference):
    """Return one :data:`LedgerParityDifference` as a line of a failure message.

    Args:
        difference: The cell.

    Returns:
        Its stored chart row (``(none stored)`` when the store holds no row
        for its key), the key's kind, class and record, the paycheck and the
        day, then both nets.
    """
    rows = ", ".join(f"#{row_id}" for row_id in difference.stored_row_ids)
    key = difference.key
    return (
        f"chart row {rows or '(none stored)'} [{key.kind.value} / "
        f"{key.ledger_class.value} / record {key.record_id}], paycheck "
        f"{difference.pay_period_id}, day {difference.entry_date.isoformat()}: "
        f"stored {difference.stored}, worked out {difference.worked_out}"
    )
