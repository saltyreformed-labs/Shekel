"""Shared read primitives over a loan's linked-ledger postings.

The low-level queries the display's configured-loan guard in :mod:`._display`
and the sync's E1a checks in :mod:`._sync` are built on:

* :func:`_has_opening_posting` -- the "is this loan configured in this scenario"
  sentinel a posting read guards on before it trusts a ``$0.00`` (an
  unconfigured loan answers ``None``, not a misleading zero).
* :func:`_visible_nets` -- the one grouped ``(entry_date, net)`` load that is the
  posted side of the checked-projection assert (plan step E1a).
* :func:`_movement_nets_by_date` -- the per-``(loan-side movement, entry_date)``
  nets the sync's lineage staleness probe compares against each settled
  payment's expected settle date and cash (step E1a; keyed by the movement
  since plan step ``balance:X-bi-6-3``).

Kept in one module so the consumers share a single definition of each rather
than re-issuing the query (the two grouped loads share one query core,
:func:`_linked_posting_nets`); scoped throughout to a loan's LINKED ledger (its
resolved per-loan liability account,
:func:`app.services.posting_service._ledger_account_for`) and the budget
scenario.

Reads only -- no writes, no commit.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting


def _has_opening_posting(linked_ledger_id: int, scenario_id: int) -> bool:
    """Return whether an OPENING leg is posted on a loan's linked ledger.

    The configured-loan test the ``None`` sentinel rests on.  A loan gets
    exactly one OPENING-kind leg on its linked ledger per scenario -- the
    origination anchor correction, whose ``owed_before`` is zero and whose
    linked leg is ``-original_principal`` (always non-zero for a real loan, so
    always posted; :func:`._anchors._loan_anchor_correction_target`).  Its
    absence means the loan is not configured in this scenario (no
    :class:`~app.models.loan_params.LoanParams`, or a what-if the opening was
    never posted into), which the reader reports as ``None`` -- routing the
    caller to its needs-setup path, never to a misleading ``$0``.

    Scoped to the linked ledger so the opening's OTHER leg (the
    ``+original_principal`` on the per-loan opening-equity account, same kind) is
    not what matches; scoped to the scenario so a loan opened in the baseline
    does not read as configured in a what-if it was never posted into.

    Args:
        linked_ledger_id: The loan's linked ledger account id
            (:func:`app.services.posting_service._ledger_account_for`).
        scenario_id: The budget scenario to scope to.

    Returns:
        ``True`` when an OPENING-kind posting exists on the linked ledger in the
        scenario, else ``False``.
    """
    opening_kind_id = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
    return db.session.query(
        db.session.query(Posting.id)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            Posting.posting_kind_id == opening_kind_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .exists()
    ).scalar()


def _linked_posting_nets(
    group_columns: list, linked_ledger_id: int, scenario_id: int, filters: list,
):
    """Build the grouped SUM(amount) query over one linked ledger's postings.

    The ONE query core behind both grouped loads here
    (:func:`_visible_nets` / :func:`_movement_nets_by_date`): postings on the
    loan's linked ledger in the scenario, summed per the caller's group key.
    Shared so the two cannot drift on the join or the scoping filters.

    Args:
        group_columns: The group-key columns, prepended to the SELECT and
            GROUP BY (the summed amount is appended after them).
        linked_ledger_id: The loan's linked ledger account id.
        scenario_id: The budget scenario to scope to.
        filters: Extra filter expressions (empty for all sources).

    Returns:
        A SQLAlchemy ``Query``, NOT executed.  Each row unpacks as
        ``(*group_columns, net)``.
    """
    return (
        db.session.query(
            *group_columns,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked_ledger_id,
            JournalEntry.scenario_id == scenario_id,
            *filters,
        )
        .group_by(*group_columns)
    )


def _visible_nets(
    linked_ledger_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return ``(entry_date, net)`` per date, ascending -- the one grouped load.

    Each posting's ``entry_date`` (the day the event it records happened -- step
    C2's one clock) with that date's net movement on the loan's linked ledger.
    The checked-projection assert (plan step E1a) compares it against the walk's
    dated deltas after every sync -- the ONE consumer since plan step E1e deleted
    the per-period balance reader that also prefix-summed it.

    Args:
        linked_ledger_id: The loan's linked ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``[(entry_date, net), ...]`` ascending by date.
    """
    return (
        _linked_posting_nets(
            [JournalEntry.entry_date], linked_ledger_id, scenario_id, [],
        )
        .order_by(JournalEntry.entry_date)
        .all()
    )


def _movement_nets_by_date(
    linked_ledger_id: int, scenario_id: int,
) -> dict[int, dict[date, Decimal]]:
    """Return each loan-side movement's non-zero per-date nets on the linked ledger.

    The E1a lineage staleness probe's posted side
    (:func:`._sync._reconcile_lineage_transfer_entries`): one grouped query
    over the linked ledger's TRANSFER-MOVEMENT-source entries -- a settled
    payment's cash leg is its loan-side covering movement's own entry since
    plan step ``balance:X-bi-6-3`` (ruling **R-BAL45**'s shape C) -- keyed
    ``{transaction_entry_id: {entry_date: net}}`` with zero-net dates dropped
    (a reconciled reversal pair nets its date to zero, which is the clean
    state).  The source-kind filter names the ONE source that is cash: the
    payment's SPLIT correction lands on the same linked ledger under the
    ``loan_payment`` kind and links no row at all (ruling **R-BAL102**), and
    it is not cash.  Entries whose
    ``transaction_entry_id`` is NULL -- a hard-deleted movement's ``SET
    NULL`` residue -- are excluded: there is no row left to re-sync, so any
    cross-date residue there is an F1-class data item the checked-projection
    assert surfaces rather than something the probe's consumer can heal.  The
    LEGACY one-entry ``transfer`` source, which this never read, is gone with
    every entry it posted (plan step ``balance:X-bi-6-5``'s second leaf, card
    plan#1845, ruling plan#1867).

    **Date-keyed, deliberately period-blind.**  A right-date / right-amount
    posting filed under the WRONG pay period would read clean here: no app
    flow can produce one (``pay_period_id`` has been posting-relevant and
    reconciled since Step 2), and the fold and the checked-projection assert
    key on dates alone, so the probe vouches for WHEN and HOW MUCH -- never
    for per-period statement attribution.

    Args:
        linked_ledger_id: The loan's linked ledger account id.
        scenario_id: The budget scenario to scope to.

    Returns:
        ``{transaction_entry_id: {entry_date: non-zero net}}``; a fully-clean
        reverted payment does not appear (all its dates net zero).
    """
    rows = _linked_posting_nets(
        [JournalEntry.transaction_entry_id, JournalEntry.entry_date],
        linked_ledger_id,
        scenario_id,
        [
            JournalEntry.source_kind_id
            == ref_cache.posting_source_id(PostingSourceEnum.TRANSFER_MOVEMENT),
            JournalEntry.transaction_entry_id.isnot(None),
        ],
    ).all()
    posted: dict[int, dict[date, Decimal]] = {}
    for movement_id, entry_date, net in rows:
        if net != 0:
            posted.setdefault(movement_id, {})[entry_date] = net
    return posted
