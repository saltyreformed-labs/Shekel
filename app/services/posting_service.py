"""
Shekel Budget App -- Posting Service

The sole writer of the append-only double-entry posting ledger
(``budget.journal_entries`` + ``budget.account_postings``, Build-Order
Step 2; see :mod:`app.models.journal_entry`).  Step 2 piloted the mechanism
on settled transfers; later Build-Order steps added cash, loan, and paycheck
sources by calling the same private balanced-write path
(:func:`_emit_balanced_entry`), so an unbalanced entry can never be written
from any source.

**Flask-isolated** (``CLAUDE.md`` Architecture rule): this service takes
plain data / ORM objects, returns ORM objects or plain values, and never
imports ``request`` / ``session``.  It **flushes but never commits** -- the
caller (the transfer service in Commit 5, a test, or a future source
writer) owns the transaction boundary.

**Reconcile-to-target, not append-blindly.**  Every sync here makes the
ledger's NET posted effect for a source equal a single target, by emitting
one balanced delta entry PER (PAY PERIOD, ENTRY DATE) for the difference
between the target and what is already posted there.  That one design is
idempotent and covers every lifecycle path -- settle, revert, archive,
cancel, delete, restore -- through a single call:

* a repeat sync computes zero deltas and writes nothing (no double-post);
* a revert / delete reverses *exactly what was posted* (read back from the
  ledger), so an amount edited while Projected and re-settled posts the new
  amount and nothing stale survives.

**Corrections are attributed to what they correct** (the 2026-07-02
adversarial review's R2 rule): a reversal entry carries the PAY PERIOD of
the postings it reverses -- read back from the ledger per period, never the
source row's current period -- and inherits the latest ``entry_date`` it
reverses.  A revert-and-move PATCH therefore nets the ORIGINAL period to
zero instead of stamping the reversal into the new period, so a net-zero
pair never straddles periods (a later truncate of the new period cannot
strand half of it) and date-grouped reporting nets a reversal against the
entry it undoes.

**The signed amount is debit-positive and class-independent.**  The *from*
account's leg is ``-amount`` (a credit: money leaving) and the *to*
account's leg is ``+amount`` (a debit: money entering), so the entry sums to
zero whether a leg lands on an asset or a liability ledger account.  The
builder never branches on account class (see the
:mod:`app.models.journal_entry` module docstring).

**A transfer is TWO movements, each posted on its own bank day against the
owner's Transfers-in-transit account** (plan step ``balance:X-bi-6-3``,
rulings **R-BAL45** and **R-BAL101**).  Through that step the ledger booked
a settled transfer as ONE entry ``{from -figure, to +figure}`` off the income
shadow's record and settle day, which was sufficient only because the
transfer service mirrored one day onto both shadows (Transfer Invariant 3);
the ruled shape lets each side post when ITS bank shows it.  So a transfer's
legs are its covering movements, each booked under its transfer LEG
(:func:`app.services.transfer_legs.movement_parent`, leaf ``X-bi-6-4a``:
period, owner and gate the transfer's), and they post through the same
movement writer every purchase and covering movement posts through
(:mod:`app.services._posting_purchases`, the counter leg dispatched by the
parent's shape) -- :func:`sync_transfer_postings` is the pair's door, not a
second writer.  The one-entry ``transfer`` source was LEGACY from that step:
the deploy's first resync reversed whatever it posted, once (the shape
``balance:X-bi-4a`` gave the transaction source), and plan step
``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted its arm, the leaf
``_posting_legacy``, and the transaction source's, with every entry either
had posted (ruling plan#1867).  The amount is the transfer's
settled figure (``Transfer.settled_figure``), never ``transfers.amount``: a
side's record holds no figure, and ``cash_ledger.movement_cash_leg`` is the
one producer of each side's signed cash.
"""

import logging

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import posting_reads
from app.services.posting_reads import PostingError
from app.services.transfer_legs import (
    dated_leg_exists_clause,
    movement_parent,
    transfer_family_movements,
)
from app.services.user_write_lock import lock_every_user_writes
from app.services._posting_purchases import (
    emit_purchase_deltas,
    posted_purchase_exists_clause,
    purchase_posts,
)
from app.utils.balance_predicates import settled_status_ids

logger = logging.getLogger(__name__)

# Re-exported read-side API.  The reconciliation readers moved to
# :mod:`app.services.posting_reads` when this module crossed the size gate
# (the sibling-split convention); the ledger's one public surface stays HERE,
# so the oracles and the loan posting package keep reading them off the
# writer module.  ``PostingError`` above is a re-export of the same kind (this
# module also uses it itself); ``_ledger_account_for`` is one too, stated
# here since plan step ``balance:X-bi-6-3`` deleted this module's own last
# reader of it (the transfer target builds inside the movement writer now),
# and so are the balanced-write primitives imported from the
# :mod:`app.services._posting_write` leaf (held below every writer so the
# correction packages can share them without importing this module -- see
# that module's docstring for the cycle this breaks).
# Pylint: ``protected-access`` -- a re-export, not a reach-in: the chart lookup
# is this module's public surface and the loan package reads it here.
# pylint: disable-next=protected-access
_ledger_account_for = posting_reads._ledger_account_for
account_posting_total = posting_reads.account_posting_total
settled_transfer_effect = posting_reads.settled_transfer_effect
posted_purchase_effect = posting_reads.posted_purchase_effect


# ── Transaction (cash) posting helpers (Build-Order Step 3) ────────


def _self_heal_account_anchor_corrections(
    account_ids: tuple, scenario_id: int, entries: list[JournalEntry],
) -> None:
    """Re-derive anchor corrections the just-emitted source deltas staled.

    The Build-Order Step 5 effect-time self-heal, shared by the tails of
    :func:`sync_transfer_postings` and :func:`sync_transaction_postings`
    (which every settle / revert / delete path routes through, including
    :func:`reverse_postings_before_delete` and
    :func:`reverse_transfer_postings_before_delete`), and called ONCE per
    scenario by :func:`resync_all_cash_postings` after its whole loop with
    every entry that loop emitted (ruling **R-BAL103**): when the emitted
    deltas touch a
    non-loan account whose latest anchor assertion sits at-or-after the
    earliest emitted ``entry_date``, that account's opening / true-up
    corrections are reconciled again in the same transaction -- see
    :func:`app.services.account_posting_service.self_heal_anchor_corrections`
    for the predicate's correctness argument.  A no-op when nothing was
    emitted, so the hot idempotent-resync paths pay nothing.

    Args:
        account_ids: The real accounts the deltas' LINKED legs can touch
            (immutable on their source rows).
        scenario_id: The scenario the deltas were emitted in.
        entries: The just-emitted delta entries (empty -> no-op).
    """
    if not entries:
        return
    # Pylint: ``import-outside-toplevel`` -- reverse dependency: the account
    # posting package imports this module's balanced-write path, so the
    # top-level import would be circular.  Mirrors the loan package's
    # function-local imports of the same shape.
    # pylint: disable-next=import-outside-toplevel
    from app.services import account_posting_service

    account_posting_service.self_heal_anchor_corrections(
        account_ids, scenario_id, entries,
    )


# ── Public API ─────────────────────────────────────────────────────


def sync_transfer_postings(xfer: Transfer) -> list[JournalEntry]:
    """Reconcile a transfer's posted ledger effect to its movements, idempotently.

    The PAIR's door (plan step ``balance:X-bi-6-3``, rulings **R-BAL45** and
    **R-BAL101**): a settled transfer is two entries, one per side's covering
    movement on its own bank day, each against the owner's Transfers-in-transit
    account -- ``{from-side account -figure, transit +figure}`` on the day the
    from-side bank showed it and ``{to-side account +figure, transit -figure}``
    on the day the to-side bank showed it, the transit account netting to zero
    once both have cleared.  Each movement posts through the ONE movement
    writer (:func:`~app.services._posting_purchases.emit_purchase_deltas`,
    keyed by ``transaction_entry_id`` and dated by the movement's own
    ``settled_on``), exactly as a bill's covering movement or an envelope's
    purchase does; what this door adds is reaching both sides from the parent
    and healing both endpoints' anchor corrections.

    **It takes no ``settled`` flag, and that is the point.**  Through
    ``X-bi-6-1b`` this was told whether to post or reverse, because the ONE
    entry it booked had no fact of its own to read.  A movement has: it posts
    iff it is dated under a contributing parent
    (:func:`~app.services._posting_purchases.purchase_posts`), a settle dates
    it and a revert UN-DATES it (ruling **R-BAL61**), a cancel or a soft delete
    makes its parent non-contributing.  So every transfer lifecycle path is
    one call with no argument to get wrong:

    ==========================================  ==============================
    Transition / action                         Net effect
    ==========================================  ==============================
    projected -> done (mark done)               both movements dated: post
    done -> projected (revert)                  both un-dated: reverse to zero
    projected -> cancelled                      no-op (nothing posted)
    restore of a soft-deleted xfer              no-op (holds no movement)
    settled ``settled_on`` edit (N-13)          reverse at the old day, post at
                                                the new (two keys, one pass)
    ==========================================  ==============================

    A DELETE is not here: it must reverse BEFORE the records come off, on
    every record either side holds, and that is
    :func:`reverse_transfer_postings_before_delete` -- the teardown twin the
    transaction side has had since plan step X-f3b, for the same reason.

    **The legacy arm is gone.**  From plan step ``balance:X-bi-6-3`` until
    plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845) this door
    also reconciled the one-entry ``transfer`` source to zero on every call;
    the deploy's first resync reversed every production legacy entry once (19
    transfers on the 2026-09-22 17:06 dump), and plan#1845's migration deleted
    those entries, every one at zero on its own key (ruling plan#1867).

    **It reads every record the transfer's two sides hold**
    (:func:`app.services.transfer_legs.transfer_family_movements`), each
    booked under its LEG; since plan step ``balance:X-bi-6-4d-2`` that is the
    whole family, every record hanging off the transfer by a side link and
    none under a shadow.  A soft-deleted transfer holds none: its delete
    takes each side's records off first (ruling **R-CC75**), and the
    database refuses a transfer hidden holding one or a record arriving
    under a hidden one (``app/deleted_row_infrastructure``'s transfer arm).
    So a sync of a hidden transfer, or of one just restored, has none to post.

    Flushes but does not commit (the caller owns the transaction).

    Args:
        xfer: The transfer to reconcile.  Must be flushed (``xfer.id`` set).

    Returns:
        The new delta :class:`~app.models.journal_entry.JournalEntry` list --
        the per-movement entries, in emission order -- or ``[]`` when the
        ledger is already at target (an idempotent no-op).

    Raises:
        PostingError: If a movement's account has no linked ledger account.
    """
    return _reconcile_transfer_family(xfer, purchase_posts)


def reverse_transfer_postings_before_delete(xfer: Transfer) -> None:
    """Reverse a transfer's ledger postings before its rows are deleted.

    The transfer twin of :func:`reverse_postings_before_delete`, and it exists
    for the identical reason: a delete -- soft or hard -- must bring the pair's
    WHOLE posted family to zero FIRST, while every row still exists.  It cannot
    be :func:`sync_transfer_postings`, which reads each movement's own state
    and would find, at the moment the delete door calls it, two live,
    contributing, dated movements and leave them posted.  Since plan step
    ``balance:X-bi-6-4d-2`` the family is every record either side holds by
    its side link (:func:`app.services.transfer_legs.transfer_family_movements`),
    which the delete then takes off through the one removal act, the journal
    links SET-NULL with each (``journal_entries.transaction_entry_id``);
    reversing first leaves each posted entry and its reversal a net-zero pair.
    A hidden transfer holds none, so the idempotent hard delete of an already
    soft-deleted one (``delete_transfer(allow_deleted=True)``) finds nothing
    to reverse.  It reverses each record (``posted=False``).

    Idempotent no-op for a transfer that never posted.  Flushes but does not
    commit (the caller owns the transaction).

    Args:
        xfer: The transfer about to be deleted.  Must still be flushed
            (``xfer.id`` set) so the reversals can read the posted legs back.
    """
    _reconcile_transfer_family(xfer, _never_posts)


def _never_posts(_leg, _movement) -> bool:
    """Return ``False``: the teardown's answer to "does this movement post"."""
    return False


def _reconcile_transfer_family(xfer: Transfer, posts) -> "list[JournalEntry]":
    """Reconcile every movement of *xfer*'s family.

    The one body :func:`sync_transfer_postings` and
    :func:`reverse_transfer_postings_before_delete` share; they differ in
    nothing but *posts* -- the movement's own rule for the sync, ``False`` for
    the teardown -- so the two doors cannot drift on which movements a
    transfer's family holds or on the self-heal that follows.  The transfer
    twin of the loop in :func:`sync_transaction_postings` /
    :func:`reverse_postings_before_delete`, which walk ``txn.entries`` the
    same way.

    Args:
        xfer: The transfer whose family to reconcile.
        posts: ``(leg, movement) -> bool``, whether the ledger should hold
            the movement's leg.

    Returns:
        The emitted delta entries, in emission order; ``[]`` at target.
    """
    entries, accounts = _rebook_transfer_family(xfer, posts)
    _self_heal_account_anchor_corrections(accounts, xfer.scenario_id, entries)
    return entries


def _rebook_transfer_family(xfer: Transfer, posts) -> "tuple[list[JournalEntry], tuple]":
    """Bring *xfer*'s family to target; re-check no anchor correction.

    :func:`_reconcile_transfer_family`'s first half.  The deploy resync runs it
    for every transfer and re-checks the anchors ONCE after its loop (ruling
    **R-BAL103**; :func:`resync_all_cash_postings` says why).  *posts* is
    ``(leg, movement) -> bool``; returns ``(entries, accounts)``, the
    emitted deltas (``[]`` at target) and every real account their linked
    legs can touch.  The family and each movement's LEG are
    :func:`~app.services.transfer_legs.transfer_family_movements`' (leaf
    ``X-bi-6-4a``), so no line here reads a shadow row.
    """
    family = transfer_family_movements(xfer)
    entries: list[JournalEntry] = []
    for movement, leg in family:
        entries.extend(
            emit_purchase_deltas(movement, leg, posted=posts(leg, movement))
        )
    return entries, _transfer_family_accounts(
        xfer, [movement for movement, _leg in family],
    )


def _transfer_family_accounts(xfer: Transfer, movements) -> tuple:
    """Return every real account a transfer family's legs can touch.

    The transfer twin of :func:`_family_accounts`: both endpoints (the legacy
    entry's legs landed there until plan#1845 deleted that shape, the shadows'
    until ``X-bi-6-4d-2`` deleted them) and each movement's own account (its
    leg lands there, ruling **R-BAL75**; one with the endpoint until the
    settle door takes a tender of its own).
    Deduplicated, first-seen order, so the self-heal visits an account once.
    """
    seen: dict[int, None] = {
        xfer.from_account_id: None, xfer.to_account_id: None,
    }
    for movement in movements:
        seen.setdefault(movement.account_id, None)
    return tuple(seen)


def sync_transaction_postings(txn: Transaction) -> list[JournalEntry]:
    """Reconcile a transaction's whole posted FAMILY to its target, idempotently.

    The ordinary-transaction analog of :func:`sync_transfer_postings`: ensures
    the ledger holds, for *txn*, exactly one cash leg per DATED movement of
    its family and NOTHING for the row itself, by emitting one balanced delta
    journal entry PER (pay period, entry date) whose posted legs differ from
    the target (:func:`~app.services._posting_write.emit_source_deltas`), then
    a no-op (returns ``[]``) on any repeat.  See the module docstring for the
    reconcile-to-target rationale and the debit-positive sign convention.

    **A plan row posts nothing of its own** (plan step ``balance:X-bi-4a``,
    rulings **R-BAL77** and **R-BAL80**).  Through ``X-bi-3e`` the row booked
    its own leg -- ``settled_cash_leg``, the recorded figure less the row's
    own dated movements, which was ZERO for every covered bill and paycheck
    by ruling **R-FM**'s identity and, for a ``purchases``-basis envelope,
    the envelope's UN-DATED purchases on the close day.  Those are movements
    in flight now: they post when they are dated, on their own day, and the
    close books nothing.  So the row books nothing: the TRANSACTION source
    it booked under stopped posting at that step, and plan step
    ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted that source
    with the arm that reversed what it had posted, its migration every entry
    the source wrote, each pair at zero on its own key (ruling plan#1867).
    The cash walk reads the same family
    (``cash_ledger.settled_cash_facts``: every dated movement, on the
    movement's own account, ruling **R-BAL75**), so the two cannot price
    one movement two ways.

    **A FAMILY since plan step X-f3b, and that is ruling R-FM** (finding
    **N-274**).  A purchase whose bank posting day the owner recorded is a cash
    movement of its own, on its own day, so this reconciles one entry per such
    movement (:func:`~app.services._posting_purchases.emit_purchase_deltas`)
    in a single pass.  **Owning the whole family here is what makes the
    trigger set complete** -- every door that already reconciles a
    transaction (a settle, a revert, a re-category, an amount edit, a period
    move, a status change, a purchase recorded or re-dated) reconciles its
    movements too, with no second list of call sites to keep in step.  The
    per-purchase door this once had beside it (``sync_purchase_postings``)
    had zero callers since plan step X-au-c3 and is deleted (ledger row
    **BAL-507**).

    **Reconciles over the accounts, periods, AND entry dates each movement
    has ALREADY posted to**, read from the ledger by its
    ``transaction_entry_id`` (:func:`~app.services._posting_write.posted_by_period`),
    unioned with the target -- NOT a single fixed pair.  This is what makes a
    revert-and-recategorize correct (the reversal lands on the OLD category
    -- the one in the ledger -- not the new ``txn.category_id``; plan Section
    2.8 CRITICAL), and what makes a revert-and-MOVE correct (the reversal
    lands in the OLD period at the exact date of the postings it reverses --
    the 2026-07-02 adversarial review's R2 attribution rule, per-date since
    plan step E1a -- so the net-zero pair never straddles periods and a later
    period truncate cannot strand half of it).  Within each (period, date)
    key the non-zero deltas always sum to zero (a target sums to zero, and
    the posted side sums to zero because every prior entry balanced and lives
    in exactly one key), so each emitted entry is balanced and has >= 2 legs
    by construction -- :func:`_emit_balanced_entry` never sees a single leg.

    Every ordinary-transaction lifecycle action is one call to this function,
    and none of them says whether the row has settled: a movement posts iff
    it is dated under a contributing parent
    (:func:`~app.services._posting_purchases.purchase_posts`), whatever the
    parent's status, and a revert, a cancel or a delete reverses what the
    ledger holds for movements the act un-dated or the parent's exclusion
    made worthless.  The ``settled`` flag this took through ``X-bi-3e`` chose
    the row's OWN target, and a row has none.

    **No transfer shadow reaches here since plan step ``balance:X-bi-6-4d-2``
    deleted them** (ruling **R-BAL166**; plan step ``balance:X-bi-6-5``
    dropped the column a row named one by).  A transfer side's payment record hangs
    off the TRANSFER by its side link since plan step ``balance:X-bi-6-4d-2``
    (ruling **R-BAL88**), and the pair's own door, :func:`sync_transfer_postings`,
    reaches both sides' records from the parent and books each under its
    transfer LEG against the owner's transit account (every movement's parent
    is :func:`~app.services.transfer_legs.movement_parent`'s answer).  From
    plan step ``balance:X-bi-6-3`` (ruling **R-BAL101**) a shadow was a row
    like any other here, its TRANSACTION source target empty, and until 4d-2
    its covering movement posted here, under its leg since leaf ``X-bi-6-4a``.
    The guard that returned ``[]`` for a shadow (ruling **R-BAL45**'s
    interval) is gone with the interval.
    Idempotency rests on the delta math plus the row's ``version_id``
    optimistic lock (a concurrent double mark-done collides on the version,
    surfacing as a 409).

    Flushes but does not commit (the caller owns the transaction).

    Args:
        txn: The transaction to reconcile.  Must be flushed (``txn.id`` set).
            Its ``transaction_type_id`` is immutable, so the income/expense
            sign is stable; its ``category_id`` may have changed, which the
            over-posted-accounts reconcile handles; its movements' accounts
            are read off each movement.

    Returns:
        The new delta :class:`~app.models.journal_entry.JournalEntry` list,
        one per (period, entry date) reconciled, or ``[]`` when the ledger is
        already at target (an idempotent no-op).

    Raises:
        PostingError: If a movement's account (or its resolved category
            account) has no ledger account.
    """
    entries = _rebook_transaction_family(txn)
    _self_heal_account_anchor_corrections(
        _family_accounts(txn), txn.scenario_id, entries,
    )
    return entries


def _rebook_transaction_family(txn: Transaction) -> "list[JournalEntry]":
    """Bring *txn*'s family to target; re-check no anchor correction.

    :func:`sync_transaction_postings`' first half, run by the deploy resync
    for every row before its one anchor re-check (ruling **R-BAL103**).
    Returns the emitted deltas, ``[]`` at target.
    """
    entries: list[JournalEntry] = []
    for purchase in txn.entries:
        parent = movement_parent(purchase)
        entries.extend(
            emit_purchase_deltas(
                purchase, parent, posted=purchase_posts(parent, purchase),
            )
        )
    return entries


def _family_accounts(txn: Transaction) -> tuple:
    """Return every real account the family's linked legs can touch.

    The row's own account and each movement's -- one set while every door
    writes a movement on its parent's account, and two the moment one does
    not (the key that held them equal,
    ``fk_transaction_entries_parent_account``, went at plan step
    ``credit_card:CC-5-1``, ruling **R-BAL76**): a movement's leg lands on
    the movement's account
    (``_posting_purchases._purchase_target``), so the anchor self-heal must
    look wherever a leg can land rather than at the parent alone (plan step
    ``balance:X-bi-4a``, ruling **R-BAL75**).  Deduplicated, in first-seen
    order, so the self-heal visits an account once.
    """
    seen: dict[int, None] = {txn.account_id: None}
    for movement in txn.entries:
        seen.setdefault(movement.account_id, None)
    return tuple(seen)


def reverse_purchase_postings_before_delete(entry) -> None:
    """Reverse one purchase's ledger postings before the row is deleted.

    The purchase twin of :func:`reverse_postings_before_delete`, and it exists
    for the identical reason: ``journal_entries.transaction_entry_id`` is ``ON
    DELETE SET NULL``, so once the purchase row is gone the link is severed and
    its legs would be stranded on their ledger accounts with no offsetting
    reversal -- breaking per-account reconciliation and leaving RESIDUE the
    posted walk can only absorb, never explain.  Reversing FIRST leaves the
    original entry and its reversal as an immutable net-zero pair.

    Idempotent no-op for a movement that never posted (no recorded posting
    day, a card purchase, a non-contributing parent).

    Args:
        entry: The movement about to be deleted -- a purchase, or a covering
            movement the seam withdraws.  Must still be flushed
            (``entry.id`` set) so the reversal can read its posted legs back.
    """
    # A transfer's covering movement posts against the transit account since
    # plan step ``balance:X-bi-6-3`` (ruling **R-BAL101**), so its withdrawal
    # -- a ``$0.00`` record landing on a settled leg, the seam's delete arm,
    # or the pair's hard delete -- reverses here like any movement's, typed
    # by its LEG (leaf ``X-bi-6-4a``); the guard that returned for a shadow
    # (ruling **R-BAL45**'s interval) is gone with the interval.
    parent = movement_parent(entry)
    entries = emit_purchase_deltas(entry, parent, posted=False)
    _self_heal_account_anchor_corrections(
        (entry.account_id,), parent.scenario_id, entries,
    )


def reverse_postings_before_delete(txn: Transaction) -> None:
    """Reverse a transaction's ledger postings before the row is deleted.

    The delete-side reconcile every transaction-delete path runs FIRST, while
    ``txn.id`` still exists: it brings the row's WHOLE posted family -- every
    one of its movements' legs -- to zero, emitting a balanced reversal entry
    for whatever the ledger currently holds.  Running it before the delete is
    load-bearing for a HARD delete: ``journal_entries.transaction_entry_id`` is
    ``ON DELETE SET NULL`` (and a row's ``transaction_entries`` are deleted
    just before it, by the one removal act -- their keys are NO ACTION since
    migration ``c4a4e7d1b9f2``), so once the movements are gone the links are
    severed and the original legs would be stranded on their ledger accounts
    with no offsetting reversal -- breaking per-account reconciliation.
    Reversing first leaves each original entry and its reversal as an
    immutable net-zero pair (their links SET-NULL together on the delete), so
    every ledger account still nets correctly.  The row's OWN cash leg was a
    second link here until plan step ``balance:X-bi-6-5``'s second leaf (card
    plan#1845) deleted that source with its ``transaction_id``.  The transaction analog of
    :func:`reverse_transfer_postings_before_delete`, which
    ``transfer_service.delete_transfer`` runs first for the same reason.  No
    transfer shadow reaches here since plan step ``balance:X-bi-6-4d-2``
    deleted them (ruling **R-BAL166**): a transfer side's payment record
    hangs off the transfer since plan step ``balance:X-bi-6-4d-2``, and
    :func:`reverse_transfer_postings_before_delete` reverses the pair's.
    Until 4d-2 a shadow's movement was reversed here, typed by its transfer
    LEG since leaf ``X-bi-6-4a`` (``transfer_legs.movement_parent``); the
    guard that returned for one (ruling **R-BAL45**'s interval) is gone with
    the interval.

    **It is NOT :func:`sync_transaction_postings`, and since plan step X-f3b
    it cannot be.**  That reconcile leaves a DATED movement posted whatever
    the parent's status -- a revert must leave an envelope's posted purchases
    exactly where they are, because the money really did leave the bank.  A
    teardown means something else entirely: every movement of the row is
    about to be deleted with it, so every one is reversed, dated or not.

    Idempotent no-op for a transaction whose family has never posted (a
    Projected row with no posted purchases).  Shared by the delete route
    (``delete_transaction``), the regeneration sweep
    (``recurrence_engine.regenerate_for_template``) and the three payback-delete
    paths (``credit_workflow.delete_payback_on_credit_revert`` /
    ``delete_payback_on_source_delete`` / ``entry_credit_workflow
    .sync_entry_payback``'s DELETE branch) so no delete path can strand a
    posting.  Flushes but does not commit (the caller owns the transaction).

    Args:
        txn: The transaction about to be deleted (soft or hard).  Must still be
            flushed (``txn.id`` set) so its movements' reversal entries can
            link by ``transaction_entry_id`` and read the already-posted legs
            back.
    """
    entries: list[JournalEntry] = []
    for purchase in txn.entries:
        entries.extend(
            emit_purchase_deltas(
                purchase, movement_parent(purchase), posted=False,
            )
        )
    _self_heal_account_anchor_corrections(
        _family_accounts(txn), txn.scenario_id, entries,
    )


def _hold_for_the_re_check(held: dict, scenario_id: int, accounts, entries) -> None:
    """Add one source's re-book to :func:`resync_all_cash_postings`' one re-check.

    *held* maps a scenario id to (accounts touched, entries emitted).
    """
    held_accounts, held_entries = held.setdefault(scenario_id, (set(), []))
    held_accounts.update(accounts)
    held_entries.extend(entries)


def _skip_reasons(skipped: "dict[int, str]") -> str:
    """Return the resync's skipped transfers and each one's reason, as one line.

    The skip warning's reason clause.  *skipped* maps a transfer id to the
    ``PostingError`` message its family raised, and is never empty here: the
    warning is logged only when the pass skipped something.  It was shared
    with ruling **R-BAL104**'s refusal, which could fire with nothing skipped,
    until plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845)
    deleted the refusal with the legacy entries it asked about.
    """
    return "Why: " + "; ".join(
        f"transfer {transfer_id}: {reason.rstrip('.')}"
        for transfer_id, reason in skipped.items()
    ) + "."


def resync_all_cash_postings() -> tuple[int, int]:
    """Re-reconcile every settled cash source's postings (deploy resync).

    The transaction / transfer twin of
    :func:`app.services.loan_posting_service.backfill_all_loan_postings` and
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`,
    and the third of the three deploy-time reconciles that between them cover
    every journal entry the app writes.  It exists because those two do NOT
    reach an ordinary transaction or a NON-loan transfer: the loan package's
    staleness detector is scoped to one loan's linked ledger
    (``loan_posting_service._sync._reconcile_lineage_transfer_entries``) and the anchor
    backfill reconciles only the corrections, so a checking-to-savings transfer
    and every ordinary settled row were maintained per-mutation and by nothing
    else.

    **What it is FOR, and why it is a permanent hook rather than a one-off**
    (ruling R-DH (b), ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).
    ``journal_entries.entry_date`` is the movement's own ``settled_on`` for
    every movement-sourced entry (``_posting_purchases.emit_purchase_deltas``;
    a transfer's two since plan step ``balance:X-bi-6-3``), a day that moved
    from the UTC civil day to the user's on 2026-07-31.  Every entry written before that
    carries the old day, so the STORED ledger and the two folds that now read
    the new one disagree for any settle recorded between midnight UTC and the
    user's midnight -- on production, one ``$1,910.95`` mortgage payment
    stamped 2026-07-02 00:38:53 UTC that belongs to the evening of 2026-07-01.
    This walks every source back through the SAME go-forward sync, so a
    re-dated entry is identical to a freshly posted one by construction; there
    is no second implementation of the rule and no SQL restatement of it,
    which is the property this whole arc exists to hold.  **It is also how
    two source shapes were retired**: the first deploy after plan step
    ``balance:X-bi-4a`` reversed the TRANSACTION source to zero (ruling
    **R-BAL80**: a plan row posts nothing of its own), and the first after
    ``balance:X-bi-6-3`` RE-BOOKED every settled transfer into its ruled shape
    (rulings **R-BAL45**, **R-BAL98**: the one-entry ``transfer`` source
    reversed to zero, the two per-movement entries posted against the owner's
    transit account, 19 transfers on the 2026-09-22 17:06 production dump) --
    each through the code every settle runs, so no SQL restated it.  Plan
    step ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted both
    retired arms, and its migration every entry they had reversed (ruling
    plan#1867).

    **It re-books EVERY source first and re-checks the anchor corrections
    ONCE, after both arms** (ruling **R-BAL103**, developer 2026-09-22).  The
    per-source doors re-check at once, right for one settle and wrong for a
    batch: a source not yet reached still holds what it posted before this
    pass, so a walk inside the loop books a true-up for money only waiting its
    turn and that source's own re-check reverses it -- both permanent in an
    append-only ledger, 40 of the 97 entries the first 6-3 deploy wrote on its
    rehearsal over the 2026-09-22 production dump, where the waiting money
    was the legacy one-entry transfer postings.  So both arms run the doors'
    re-book halves
    (:func:`_rebook_transaction_family`, :func:`_rebook_transfer_family`) and
    the one re-check per scenario (one owner's; the self-heal takes no lock,
    and this function took every owner's at its start) reads the finished
    ledger.  The union's earliest day can only make the re-check run where
    one source alone would skip it.

    **From plan step ``balance:X-bi-6-3`` it REFUSED to finish while any
    transfer still held a nonzero legacy net** (ruling **R-BAL104**, amended
    by **R-BAL105**): a family it skipped kept its legacy posting, which the
    account walk could not see, so a later true-up would have booked a wrong
    correction.  Plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845)
    deleted the refusal: its migration deleted every legacy entry, each pair
    at zero on its own key, so there is no money left the walk cannot see
    (ruling plan#1867).

    It stays wired on every deploy rather than being deleted after one run, for
    the same reason its two siblings are: reconcile-to-target makes it a no-op
    at target, so it costs one pass and converts any future drift -- a rule
    change, a hand-edited row, a half-applied migration -- into a self-heal
    instead of a silent divergence.

    Idempotent and self-healing.  A settled row already at target posts nothing;
    a row whose target DATE moved gets its old-date legs reversed and its new
    -date legs posted in one balanced pair by the re-book halves of
    :func:`sync_transaction_postings` / :func:`sync_transfer_postings`, which
    reconcile over the ``(period, entry_date)`` keys already in the ledger
    unioned with the target (plan step E1a's per-date attribution) -- so a
    moved date is an ordinary reconcile, not a special case this function has to
    know about.

    Loan payment transfers are re-synced here too and that is deliberate
    duplication of effort, not of RULE: the loan package would reach the same
    ones through its own detector, and both paths reach this module's
    :func:`_rebook_transfer_family`, so whichever runs first leaves the other at
    target.  **Its transfer half is total over the family the ledger holds
    for the same reason its transaction half is** (below): it walks every
    live transfer that is settled OR holds a dated record on either side,
    and hands each to the pair's door, which reads each movement's own
    state.

    Flushes but does NOT commit -- the caller owns the transaction boundary
    (``scripts.init_database.initialise_database``, whose ONE commit covers the
    migrations and all three deploy hooks, and which initialises ``ref_cache``
    first because the migration host does not).

    **The counts are sources CHANGED, not sources walked** (finding N-133 / F8).
    A hook that rewrites the whole production ledger on every deploy and reports
    the same number whether it moved every date or nothing at all tells the
    operator only that it ran.  Both sync functions return the journal entries
    they emitted -- empty when already at target -- so "changed" is observable
    without a second query, and a healthy deploy logs ``0, 0``.  The FIRST
    deploy after a dating rule moves is the one that logs a non-zero count, and
    that line is the only evidence the one-time re-date happened.

    **The re-date is ONE-WAY, and that is a stated risk rather than a
    discovered one.**  ``entrypoint.sh`` runs ``set -eEuo pipefail`` and calls
    ``scripts/init_database.py``, so a failure here aborts the container
    before anything commits -- the release's migrations included, since plan
    step ``balance:X-cv`` runs them and the three deploy hooks in ONE
    transaction -- so the stamp is unmoved and ``deploy/shekel-deploy.sh``
    re-pins the previous image.  And if the healthcheck
    fails AFTER this commits, the rolled-back image reads a display-dated
    ledger with the previous image's UTC rules, and only the entries whose two
    days differ are affected (on production at the cutover: one payment, one
    day).  Rolling back ACROSS a re-date or a re-book therefore follows
    ``deploy/shekel-deploy.sh``'s rollback instructions; for a
    migration-bearing release that is the pre-deploy dump it names.

    **It is the THIRD multi-owner transaction, and it takes every per-user
    write lock up front** (plan step X-f1c3c, finding N-193).  It iterates every
    owner's settled rows in ID order, and each one reached the anchor
    self-heal and so, until plan step ``balance:X-bn`` deleted the per-service
    acquisitions, that owner's lock -- an unordered multi-key acquisition,
    which is exactly what two concurrent sweeps deadlock on.  "Up front" is
    this FUNCTION's start, not the transaction's: the deploy runs the release's
    migrations and reference seed earlier in the same transaction
    (``scripts/init_database.py``), before any owner's lock is held.  Taking
    every owner's lock ascending here is what now covers every write this
    reconcile makes, since no service below takes one.  A
    first version of the lock's docstring called the two backfill functions
    "the only multi-owner transactions" and missed this one, which is the FIRST
    of the three deploy hooks to run.

    **Its transaction half is no longer settled-only** (ruling **R-FM**, plan
    step X-f3b): it walks every non-transfer row that is settled OR carries a
    purchase with a recorded bank posting day, and passes each row its own
    settled truth.  That is what makes the hook total over the family the ledger
    now holds -- a purchase against a still-Projected envelope is real cash that
    left the bank -- and it is what moves the existing rows onto the new split
    without a backfill: the first deploy after migration ``b7c3d9e1f204``
    reverses the part of each envelope's cash leg its posted purchases now own
    and posts those purchases at their own days, in one balanced pass per row.

    Returns:
        ``(transactions_changed, transfers_changed)`` -- how many sources this
        pass actually re-posted, for the deploy log.

    Raises:
        PostingError: If a ROW's family cannot post (the transaction arm
            does not skip), or if the one anchor re-check
            refuses an account (anchor history with no linked ledger, or a
            posted net whose source it cannot resolve).  In every case the
            caller's transaction must not commit.
    """
    lock_every_user_writes()
    settled_ids = settled_status_ids()
    transactions = (
        db.session.query(Transaction)
        .options(
            selectinload(Transaction.entries),
            # **``joinedload(Transaction.pay_period)`` is GONE, and both readers
            # it was added for went first** (plan step ``pay_calendar:C13-b``).
            # It was here for finding N-133 / F9 -- 122 extra SELECTs on
            # production's settled set -- because the row's own entry date
            # read the period's ``start_date`` as a NULL fallback and the
            # row's own target read ``pay_period.user_id``.  Plan step X-f1
            # deleted the first when ``settled_on`` replaced ``paid_at``;
            # C13-b moved the second onto ``txn.user_id``; plan step
            # ``balance:X-bi-4a`` deleted both readers with the row's own
            # leg.  The comment outlived the first two and named live
            # dereferences that no longer existed, which is what an
            # adversarial review caught.
            # **Measured 2026-09-03 rather than deduced**: with the option this
            # walk hydrates ONE ``PayPeriod``, without it ZERO -- so nothing
            # lazy-loads in its place and no autoflush moves into the loop.
            # The whole walk is enumerable here because no row can name a
            # transfer (the table has no column for one since plan step
            # ``balance:X-bi-6-5``), so the transfer arm that made
            # ``reconcile_service._rows``' twin undecidable does not apply.
        )
        .filter(
            Transaction.is_deleted.is_(False),
            # SETTLED, or carrying a purchase that has posted a leg of its own
            # (ruling **R-FM**, plan step X-f3b).  The second arm is what makes
            # this hook TOTAL over the family the ledger now holds: a purchase
            # against a still-PROJECTED envelope is real cash that left the bank,
            # and without it that envelope's legs would be maintained by
            # per-mutation calls and by nothing else -- the exact gap this
            # function exists to close for transactions.  The first arm
            # reached every row the TRANSACTION source ever posted for (a row
            # posted its own leg only while settled), so the resync after plan
            # step ``balance:X-bi-4a`` brought that source to zero; plan#1845
            # deleted that source, and the arm still walks every settled
            # row's family.  It is an EXISTS rather than a join so a row with
            # several posted purchases is walked once.
            db.or_(
                Transaction.status_id.in_(settled_ids),
                posted_purchase_exists_clause(),
            ),
        )
        .order_by(Transaction.id)
        .all()
    )
    # Every source's re-book is HELD for the one anchor re-check after both
    # arms (ruling **R-BAL103**): scenario -> (accounts touched, entries).
    held: dict[int, tuple[set[int], list[JournalEntry]]] = {}
    transactions_changed = 0
    for txn in transactions:
        entries = _rebook_transaction_family(txn)
        if entries:
            transactions_changed += 1
            _hold_for_the_re_check(
                held, txn.scenario_id, _family_accounts(txn), entries,
            )

    transfers = (
        db.session.query(Transfer)
        .filter(
            # LIVE and SETTLED, or live and holding a dated record on either
            # side (plan step ``balance:X-bi-6-3``, ruling **R-BAL101**; by
            # the side links since ``X-bi-6-4d-2``) -- the transaction arm's
            # totality argument: this reaches every transfer that could hold
            # a leg today.  A second disjunct reached every transfer, live or
            # not, the legacy one-entry source ever posted for, until
            # plan#1845 deleted that source and its entries.
            Transfer.is_deleted.is_(False),
            db.or_(
                Transfer.status_id.in_(settled_ids),
                dated_leg_exists_clause(),
            ),
        )
        .order_by(Transfer.id)
        .all()
    )
    # **A broken FAMILY is skipped and reported, not allowed to abort the
    # batch** (developer ruling, 2026-08-17).  The pair's door raises
    # ``PostingError`` -- correctly -- for a movement whose account has no
    # linked ledger account, a broken chart-of-accounts pairing.
    #
    # That refusal is right for a SINGLE write path, where a caller asking about
    # one transfer must not get a fabricated posting.  It is wrong for a batch
    # self-heal that walks every row in the database and runs at container start
    # (``scripts/init_database.py``): one repairable row would make the app
    # unbootable for every user, and the operator could not even reach the
    # screen that shows which row it was.  Skipping keeps the failure loud in
    # the log and bounded to the family that caused it.
    #
    # **Skipped WHOLE, under a SAVEPOINT** (the leaf-1 adversarial review of
    # plan step ``balance:X-bi-6-3``, finding 1).  The pair's door writes in
    # sequence -- one side, then the other -- and a refusal on the second side
    # would otherwise leave the first side committed by the deploy: the
    # from-account debited into transit
    # with nothing arriving, a trial balance that still closes, and no reader
    # to trip.  The one-entry door resolved every input before its first
    # write, so its skip was clean by construction; a per-movement door has no
    # such moment, and the savepoint gives the batch the atomicity the door
    # cannot.
    transfers_changed = 0
    # Transfer id -> why its family could not post: the door's own message
    # (which names the account whose pairing is broken) is the operator's
    # only pointer to the repair, so it is carried to the log.
    skipped: dict[int, str] = {}
    for xfer in transfers:
        try:
            with db.session.begin_nested():
                entries, accounts = _rebook_transfer_family(xfer, purchase_posts)
        except PostingError as exc:
            skipped[xfer.id] = str(exc)
            continue
        if entries:
            transfers_changed += 1
            _hold_for_the_re_check(held, xfer.scenario_id, accounts, entries)
    # The ONE anchor re-check, after every source is re-booked (ruling
    # **R-BAL103**; the docstring says why).  Outside the per-transfer
    # SAVEPOINT on purpose: the walk refuses only for the ACCOUNT
    # (anchor history with no linked ledger, or a posted net whose source it
    # cannot resolve), never for one transfer's re-book, and the deploy's third
    # hook walks every non-loan account with no skip
    # (``account_posting_service.backfill_all_account_anchor_postings``), so
    # such an account aborts the deploy there regardless.
    for scenario_id, (accounts, entries) in sorted(held.items()):
        _self_heal_account_anchor_corrections(
            tuple(sorted(accounts)), scenario_id, entries,
        )
    if skipped:
        logger.warning(
            "Cash posting resync skipped %d transfer(s) whose family could not "
            "be posted: %s.  %s  Each is a broken chart-of-accounts pairing on "
            "a movement's account -- repair it, then re-run the resync.",
            len(skipped), list(skipped), _skip_reasons(skipped),
        )

    return transactions_changed, transfers_changed
