"""
Shekel Budget App -- the worked-out ledger against the stored one, cell by cell.

The comparison X-ew's first leaf proves its read by (plan step plan#2513): the
parity test (plan task plan#2527) runs it over the posting fixtures -- every
sweep of the posting reconciliation suites, and a world for each source kind
in ``tests/test_services/test_ledger_lines/test_parity_with_the_stored_ledger.py``
-- and the production run (plan task plan#2528) over a production copy.

Both ledgers are netted per (chart account, paycheck, entry day):

* **worked out**: :func:`app.services.ledger_lines.worked_out_lines`, each line
  summed under its own key's account, ``pay_period_id`` and ``entry_date``;
* **stored**: every ``budget.account_postings`` leg under a journal entry of the
  owner's (``journal_entries.user_id``) in the scenario, summed under the
  account of the chart row it sits on, the entry's ``pay_period_id`` and its
  ``entry_date``.

**A chart account is a key and the stored row it is booked on**
(:class:`ParityChart`, the one home of the rule): a stored leg's is the row it
sits on, read as stored (:func:`tests._stored_ledger.stored_chart_key`, which
repairs nothing); a worked-out line's is its key and the owner's stored row
for that key, none when the store holds none.  So every stored row is a cell
of its own, and a row no worked-out key lands on nets against nothing: another
owner's row, whose bucket key (Transfers in transit, Uncategorized) names no
owner, and each orphan, a deleted category's row, whose key has no record and
is shared by every orphan of its kind and class: ``orphan`` for the rows the
kind column's backfill classed, ``category`` for a row orphaned since (review
plan#2521, findings C-M3 and B8).

**Nets are compared, never lines**: the read drops every ``$0.00`` line and the
store can hold a ``$0.00`` net (a reversal pair; two same-day true-ups that
cancel), so a cell netting ``$0.00`` on both sides is no difference, and a cell
one side lacks is ``$0.00`` there.

It reads the stored journal and chart directly, as the posting reconciliation
oracles do: the stored side is the second opinion, so it is read by its own
query rather than through a reader the store's own package exports.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from collections import defaultdict, namedtuple
from decimal import Decimal
from functools import partial

from sqlalchemy import func, or_

from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.scenario import Scenario
from app.services.ledger_lines import worked_out_lines
from app.services.pay_calendar import calendar_for
from tests._stored_ledger import stored_chart_key

_ZERO = Decimal("0.00")

# One (chart account, paycheck, day) cell at which the worked-out ledger and
# the stored one hold different nets: what :func:`ledger_parity_differences`
# returns and :func:`assert_ledger_parity` names.  ``key`` and
# ``stored_row_ids`` are the cell's chart account (:class:`ParityChart`): the
# one stored row it is booked on, or none when the store holds no row of the
# owner's for ``key``.
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
        kind, class, record and stored row; empty when the two agree to the
        cent at every cell.
    """
    owner_id = db_session.get(Scenario, scenario_id).user_id
    posted = _posted_nets(db_session, owner_id, scenario_id)
    chart = read_parity_chart(
        db_session, owner_id, {row_id for row_id, _, _, _ in posted},
    )
    stored = {}
    for row_id, pay_period_id, entry_date, net in posted:
        cell = (*chart.stored_account(row_id), pay_period_id, entry_date)
        stored[cell] = stored.get(cell, _ZERO) + net
    worked_out = _worked_out_nets(owner_id, scenario_id, chart)
    differences = []
    for cell in stored.keys() | worked_out.keys():
        if stored.get(cell, _ZERO) == worked_out.get(cell, _ZERO):
            continue
        key, stored_row_ids, pay_period_id, entry_date = cell
        differences.append(LedgerParityDifference(
            key=key,
            stored_row_ids=stored_row_ids,
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

    A test whose subject is a door's act on the ledger ends on this line: the
    worked-out read follows the records and cannot see the door's sync, so
    this is the one assertion there that fails when the sync is deleted.  It
    goes with the sync (plan#2914).

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


class ParityChart:
    """The stored chart both ledgers are netted onto, and the rule they map by.

    The one home of the comparison's chart account, a ``(ChartKey, stored row
    ids)`` pair, which the production run's harness
    (``tests._worked_out_ledger``) maps both ledgers by too:

    * a **stored** leg's account is the row it sits on: the row's key as
      stored, and that row alone, so no two rows net in one cell;
    * a **worked-out** line's account is its key and the owner's stored rows
      for that key -- one row, or none when the store holds none.  The chart's
      unique indexes hold an owner to one row per key a constructor builds
      (a bucket's only while its writer flags every bucket row
      ``is_owner_bucket``, the index's predicate); only orphans share a key,
      and no worked-out key names one.  A store holding two rows for one
      such key would put a worked-out line on both, an account no stored leg
      sits on, so every cell of it would part.

    A worked-out key never lands on another owner's row or on an orphan, so
    the owner's nonzero net on either, in any cell, is a difference.

    Attributes:
        rows: Each chart row read, by id: the owner's whole chart, and every
            row of another owner's a posting of the owner's sits on.
        key_of: Each of those rows' key, read as stored.
    """

    def __init__(self, rows, owner_id):
        """Map *rows* for *owner_id*'s comparison.

        Args:
            rows: The :class:`LedgerAccount` rows, as :attr:`rows` describes.
            owner_id: The owner whose ledger is compared.
        """
        self.rows = {row.id: row for row in rows}
        self.key_of = {row.id: stored_chart_key(row) for row in rows}
        owned = defaultdict(list)
        for row in rows:
            if row.user_id == owner_id:
                owned[self.key_of[row.id]].append(row.id)
        self._owned = {key: tuple(sorted(ids)) for key, ids in owned.items()}

    def stored_account(self, row_id):
        """Return the account a stored leg on chart row *row_id* is netted in.

        Returns:
            ``(its key as stored, (row_id,))``.
        """
        return self.key_of[row_id], (row_id,)

    def worked_account(self, key):
        """Return the account a worked-out line keyed *key* is netted in.

        Returns:
            ``(key, the owner's stored row ids for key)``, ``()`` for none.
        """
        return key, self._owned.get(key, ())


def read_parity_chart(db_session, owner_id, posted_row_ids):
    """Read the :class:`ParityChart` an owner's comparison maps by.

    The owner's whole chart, so a worked-out key can name the row it lands
    on, and every row a posting of the owner's sits on, another owner's
    included, so that leg has a cell of its own.

    Args:
        db_session: The test ``db.session``.
        owner_id: The owner.
        posted_row_ids: The chart rows the owner's postings sit on.

    Returns:
        The :class:`ParityChart`.
    """
    rows = (
        db_session.query(LedgerAccount)
        .filter(or_(
            LedgerAccount.user_id == owner_id,
            LedgerAccount.id.in_(posted_row_ids),
        ))
        .all()
    )
    return ParityChart(rows, owner_id)


def _worked_out_nets(owner_id, scenario_id, chart):
    """Return the worked-out ledger's net per (chart account, paycheck, day).

    Args:
        owner_id: The owner.
        scenario_id: The scenario.
        chart: The :class:`ParityChart` to map each line's key by.

    Returns:
        ``{(ChartKey, stored row ids, pay_period_id, entry_date): net}``.
    """
    nets = {}
    for line in worked_out_lines(
        owner_id, scenario_id, partial(calendar_for, owner_id),
    ):
        cell = (
            *chart.worked_account(line.key), line.pay_period_id,
            line.entry_date,
        )
        nets[cell] = nets.get(cell, _ZERO) + line.amount
    return nets


def _posted_nets(db_session, owner_id, scenario_id):
    """Return the stored ledger's net per (chart row, paycheck, day).

    Args:
        db_session: The test ``db.session``.
        owner_id: The owner, whose journal entries are read by their
            ``user_id``.
        scenario_id: The scenario.

    Returns:
        Rows of ``(ledger_account_id, pay_period_id, entry_date, net)``, one
        per (chart row, paycheck, day) the owner's postings sit on.
    """
    return (
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


def _parity_order(difference):
    """Return the sort key a :data:`LedgerParityDifference` is listed by.

    Paycheck, day, then the key's kind, class and record (an owner bucket's
    ``None`` record first), then the stored row, so a list of cells reads in
    ledger order and two rows of one key (an orphan's) in a fixed one.

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
        difference.stored_row_ids,
    )


def _described_parity_difference(difference):
    """Return one :data:`LedgerParityDifference` as a line of a failure message.

    Args:
        difference: The cell.

    Returns:
        Its stored chart row (``(none stored)`` when the store holds no row
        of the owner's for its key), the key's kind, class and record, the
        paycheck and the day, then both nets.
    """
    rows = ", ".join(f"#{row_id}" for row_id in difference.stored_row_ids)
    key = difference.key
    return (
        f"chart row {rows or '(none stored)'} [{key.kind.value} / "
        f"{key.ledger_class.value} / record {key.record_id}], paycheck "
        f"{difference.pay_period_id}, day {difference.entry_date.isoformat()}: "
        f"stored {difference.stored}, worked out {difference.worked_out}"
    )
