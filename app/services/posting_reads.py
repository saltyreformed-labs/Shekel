"""
Shekel Budget App -- Posting Ledger Read-Side Helpers

The read-side companions of :mod:`app.services.posting_service` (the ledger's
sole writer), extracted when the writer crossed the module-size gate -- the
same sibling-split the loan posting package and ``transfer_service._loan_posting``
follow.  Three concerns live here:

* :class:`PostingError` -- the shared invariant-violation error both sides
  raise;
* :func:`_ledger_account_for` -- the chart-of-accounts pairing lookup the
  writer's target builders and the readers share;
* the reconciliation readers (:func:`account_posting_total`,
  :func:`settled_transfer_effect`, :func:`posted_purchase_effect`) -- the
  oracle-facing sums the integration oracles pit against each other.

``posting_service`` re-exports all five names, so every existing consumer
(the oracles, the loan posting package) keeps reading them off the writer
module -- the ledger's one public surface.

**Flask-isolated** and read-only: plain data in, plain values out; never
imports ``request`` / ``session``; performs no writes.
"""

from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import ShekelError
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import ledger_account_service
from app.services.transfer_legs import transfer_movement_rows
from app.utils.balance_predicates import balance_excluded_status_ids


def settled_figure_clause():
    """Return the SQL for what a SETTLED transaction records as having moved.

    The query-tier twin of :func:`app.services.row_valuation.settled_figure`, and
    the ONE spelling of it in SQL (plan step **X-au-c3**); two copies of a
    money rule is this arc's own root cause 1.  **It has no reader in
    ``app/`` since leaf ``balance:X-bi-6-4a``**, whose oracle
    (:func:`settled_transfer_effect`) reads a transfer's legs off the
    transfer rather than its shadows' records; only its parity test with
    ``settled_figure`` calls it.  (``settled_transaction_effect`` went at
    plan step ``balance:X-bi-4a`` with the row's own leg;
    ``posting_service._settle_effective`` at ``balance:X-bi-6-3``, when a
    transfer's legs became its movements' own entries and the writer stopped
    reading the pair's figure off the income shadow.)

    **The record is the row's entries, and the figure is their sum** (plan
    step ``balance:X-bi-4b-1``, ruling **R-BAL80**): ``COALESCE(SUM(amount),
    0)`` over ``budget.transaction_entries`` correlated to the row -- a
    transfer leg's one covering movement for the two readers above, an
    envelope's purchases, nothing for a close of nothing (ruling
    **R-BAL82**).  It was a ``CASE`` on ``settled_basis_id`` through
    ``X-bi-4a`` -- the stored ``settled_amount`` for a ``derived`` /
    ``corrected`` record, the entry sum for ``purchases``, ``NULL`` for a row
    recording nothing -- dispatching on the same column the Python twin
    read; both columns are the covering movement's stale cache, written by
    the seam and read by no money reader since this step, and deleted at
    ``X-bi-4b-2``.

    **The ``NULL`` arm is gone with the state it named.**  A settled row
    "recording nothing" -- no basis, no figure, typically no entries -- was
    the one row the Python tier REFUSED and this tier could only drop or
    zero; that history (the ``COALESCE(actual_amount, estimated_amount)``
    fallback to the PLAN, the freeze it forced, the undercount findings
    **N-242** and **N-298** a per-kind cutover would have produced through a
    silent ``SUM``) is the reason the record became mandatory at X-au-c3.
    Under R-BAL82 a settled row with no entries IS the ``$0.00`` record,
    which both tiers answer ``0`` -- an envelope closed empty cost nothing,
    which is what its records say -- and a settled row's figure is never its
    plan on either tier.

    Callers must still filter to settled rows themselves: the expression
    reads no status, so over an unsettled row it sums whatever the row holds
    (a reverted row's kept movement, an open envelope's purchases), which is
    not a figure that moved.

    Returns:
        A SQLAlchemy expression over :class:`~app.models.transaction.Transaction`
        evaluating to the recorded figure, ``0`` for a settled row holding no
        entry.
    """
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(TransactionEntry.amount), Decimal("0"))
        )
        .filter(TransactionEntry.transaction_id == Transaction.id)
        .correlate(Transaction)
        .scalar_subquery()
    )


class PostingError(ShekelError):
    """A posting-ledger invariant was violated and the write was refused.

    Raised for the should-never-happen data-integrity failures the posting
    services guard (a real account with no paired ledger account; a settled
    transfer with no active income shadow; a caller-supplied set of legs that
    does not balance; a ``None`` scenario in a reconciliation helper).  These
    are not user-input errors -- the chart-of-accounts pairing and the
    two-shadow transfer invariant are guaranteed upstream -- so a violation
    here means a broken invariant that must fail loudly rather than post a
    wrong or unbalanced entry.
    """


def _ledger_account_for(account_id: int) -> LedgerAccount:
    """Return the LINKED ledger account paired with a real account, or fail loudly.

    Every ``budget.accounts`` row has exactly one linked ledger account (the
    Commit-2 create hook pairs new accounts; the Commit-2 backfill paired
    historical ones; ``uq_ledger_accounts_account_kind`` permits only one per
    kind).  A missing pairing is a broken chart-of-accounts invariant, not a
    benign lookup miss, so this raises rather than returning ``None`` -- which
    is the WHOLE of what this adds over the chart's own lookup
    (:func:`app.services.ledger_account_service.find_linked_ledger_account`).

    **The query itself lives with the chart, not here** (plan step X-f3d).
    The ``linked``-kind filter is load-bearing since Step 5 -- an account may
    ALSO carry per-account counter rows on the same ``account_id``, and an
    unfiltered ``one_or_none`` would raise ``MultipleResultsFound`` the moment
    one exists -- and it was spelled out THREE times across two modules, so a
    reader and a writer could come to disagree about which row is an account's
    own.  The ``duplicate-code`` gate is what surfaced the third copy.

    Args:
        account_id: The real account whose linked ledger account to load.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`.

    Raises:
        PostingError: If no ledger account is linked to *account_id*.
    """
    ledger = ledger_account_service.find_linked_ledger_account(account_id)
    if ledger is None:
        raise PostingError(
            f"No ledger account is linked to account {account_id}; the "
            f"chart-of-accounts pairing is missing (every account is paired "
            f"by the account-create hook or the Step-2 backfill)."
        )
    return ledger


def account_posting_total(account_id: int, scenario_id: int) -> Decimal:
    """Return the net of all posting legs on an account's ledger in a scenario.

    Sums ``account_postings.amount`` over the account's linked ledger account
    for journal entries in *scenario_id* (the ``scenario_id`` denorm on the
    entry keeps scenarios isolated).  This is the ledger side of the Commit-6
    reconciliation oracle; it equals :func:`settled_transfer_effect` for the
    same account and scenario when the ledger is in sync.

    Args:
        account_id: The real account whose ledger postings to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net of the account's posting legs as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None`` (a scenario is required to
            isolate the sum), or the account has no linked ledger account.
    """
    if scenario_id is None:
        raise PostingError(
            "account_posting_total requires a scenario_id (postings are "
            "scenario-scoped); got None."
        )
    ledger = _ledger_account_for(account_id)
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger.id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def settled_transfer_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its settled transfer legs.

    The balance-side expectation the Commit-6 oracle reconciles the ledger
    against: over every DATED, debit covering movement ON the account (its
    own ``account_id``, ruling **R-BAL75**) that is a leg's record of a live,
    balance-contributing transfer in *scenario_id*, its transfer's settled
    figure (its one home since plan step ``balance:X-bi-6-4d-2``), ``+`` on
    the to-side (money in) and ``-`` on the from-side (money out) --
    exactly the debit-positive net :func:`account_posting_total`
    accumulates.  A settled leg's money IS its dated movement (rulings
    **R-BAL79** / **R-BAL80**), so "settled" here is the leg's, not the
    status's.  A transfer closed at ``$0.00`` holds no movement and adds
    nothing (ruling **R-BAL82**).

    **The narrowings are the writer's posting rule, restated** (leaf
    ``X-bi-6-4a``): ``_posting_purchases.purchase_posts`` books a movement
    iff it is dated, a debit, and its parent contributes, whatever the
    parent's status (ruling **R-BAL101**) -- and, for a transfer leg, iff it
    is the leg's RECORD, which the join this reads returns alone.  Through
    that leaf's first half
    this filtered on the transfer's settled STATUS instead, which agrees on
    every door-written state -- a settle dates both sides and a revert
    un-dates them (ruling **R-BAL61**), so a movement is dated exactly while
    its transfer is settled (all 38 covering movements on the 2026-09-22
    17:06 production dump are dated and under a settled transfer, none
    un-dated under one) -- and parted from the ledger only in a status
    drift, where it graded the writer against a rule the writer does not
    state.

    **The legs come off their TRANSFER since leaf ``X-bi-6-4a``** (ruling
    **R-BAL106**): the movement, its parent and its side through
    :func:`app.services.transfer_legs.transfer_movement_rows`, so gate,
    scope and direction are the transfer's.  Through ``X-bi-6-3`` this summed
    the settled SHADOW rows' records (``settled_figure_clause``) signed by the
    shadow's type.  The SIGN and the narrowings are restated here rather
    than shared with the writer (``cash_ledger.movement_cash_leg``,
    ``purchase_posts``): an oracle that imported the rule it grades could
    not grade it.  What IS shared is the base join and the transfer link:
    this reads a movement's side as the join's SQL
    (``transfer_legs._records._leg_is_income``), the writer as its Python
    twin over one loaded movement (``transfer_legs.movement_parent``, the two
    pinned by a parity test); a side link IS a record since plan step
    ``balance:X-bi-6-4d-2`` (``ck_transaction_entries_side_link_is_a_record``).

    **The parent's live and contributing gate is not restated, because no
    dated record can fail it** (finding **BAL-534**, deleted at plan step
    ``balance:X-bi-6-4d-2`` rather than graded): a record cannot arrive under
    a deleted transfer and a transfer holding one cannot be hidden
    (``app.deleted_row_infrastructure``'s transfer arm), and a side's record
    is dated exactly while its transfer is Paid or Received
    (``app.side_band_infrastructure``), neither of which is Credit or
    Cancelled.  The two Transfer-level filters this read handed the loader
    selected nothing the dated narrowing did not, on any storable state.

    Args:
        account_id: The real account whose settled transfer legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's settled transfer legs as a
        ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "settled_transfer_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    rows = transfer_movement_rows(
        TransactionEntry.account_id == account_id,
        Transfer.scenario_id == scenario_id,
        TransactionEntry.settled_on.isnot(None),
        TransactionEntry.is_credit.is_(False),
    ).all()
    return sum(
        (
            transfer.settled_figure if is_income else -transfer.settled_figure
            for _movement, transfer, is_income in rows
        ),
        Decimal("0"),
    )


def posted_purchase_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its DATED movements.

    The movement term of the oracle's per-account invariant, and since plan
    step ``balance:X-bi-4a`` the whole of its non-transfer half (ruling
    **R-BAL80**): a plan row posts nothing of its own, so every cash leg the
    ledger holds for an ordinary transaction's family is a movement's.  Over
    every non-card entry carrying a ``settled_on`` ON THIS ACCOUNT
    (``TransactionEntry.account_id``, the movement's own -- ruling
    **R-BAL75**) whose parent is a non-deleted, balance-contributing,
    NON-transfer transaction in *scenario_id*, sum each amount signed by the
    PARENT's type -- ``+amount`` under an income row, ``-amount`` under an
    expense -- whatever the parent's status.  **A movement's direction is
    its parent's** (plan step ``balance:X-bi-3b``, ruling **R-BAL35**).

    **It read purchases on UNSETTLED parents alone through ``X-bi-3e``**,
    because a settled parent's own leg (``settled_transaction_effect``, the
    term deleted with this step) summed ``effective - credit`` over the whole
    row and already contained its posted purchases.  That leg is gone, and
    so is the narrowing: the transaction SOURCE's posted net is expected to
    be zero on every account, and this term is what the family holds.

    For a real account A, ``account_posting_total(A) ==
    settled_transfer_effect(A) + posted_purchase_effect(A)`` once the ledger
    is in sync (the oracle's per-account invariant), the anchor corrections
    aside.

    **SIGNED, and the claim that it is always negative or zero left at plan
    step ``bank_import:X-gj-2b-3``**: that held only while
    ``ck_transaction_entries_positive_amount`` said ``amount > 0``, and ruling
    **bank_import:R-II** made a merchant credit a NEGATIVE purchase -- whose
    posted cash leg is POSITIVE, because the money arrived.  The expression
    needed nothing: the sign rule is arithmetic over the figure, total over
    both directions.

    **The narrowings and the sign are the write side's, restated in SQL
    rather than shared with it** -- the deliberate independence every oracle
    here keeps.  An oracle that imported
    ``_posting_purchases.purchase_posts`` or ``cash_ledger.movement_cash_leg``
    could not grade them.

    Args:
        account_id: The real account whose dated movements to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect as a ``Decimal``.

    Raises:
        PostingError: If *scenario_id* is ``None``.
    """
    if scenario_id is None:
        raise PostingError(
            "posted_purchase_effect requires a scenario_id (transactions "
            "are scenario-scoped); got None."
        )
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    signed_effect = case(
        (
            Transaction.transaction_type_id == income_type_id,
            TransactionEntry.amount,
        ),
        else_=-TransactionEntry.amount,
    )
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(signed_effect), Decimal("0"))
        )
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            TransactionEntry.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.notin_(balance_excluded_status_ids()),
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .scalar()
    )
