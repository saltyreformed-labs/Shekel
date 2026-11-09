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

from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import posting_reads
from app.services.ledger_lines import purchase_posts
from app.services.transfer_legs import (
    movement_parent,
    transfer_family_movements,
)
from app.services._posting_purchases import emit_purchase_deltas

# Re-exported read-side API.  The reconciliation readers moved to
# :mod:`app.services.posting_reads` when this module crossed the size gate
# (the sibling-split convention); the ledger's one public surface stays HERE,
# so the oracles and the loan posting package keep reading them off the
# writer module.  ``PostingError`` is a re-export of the same kind (this
# module caught it itself until plan task plan#3077 deleted the deploy
# resync, its last use here); ``_ledger_account_for`` is one too, stated
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
PostingError = posting_reads.PostingError
account_posting_total = posting_reads.account_posting_total
settled_transfer_effect = posting_reads.settled_transfer_effect
posted_purchase_effect = posting_reads.posted_purchase_effect


# ── Transaction (cash) posting helpers (Build-Order Step 3) ────────


def _self_heal_account_anchor_corrections(
    scenario_id: int, entries: list[JournalEntry],
) -> None:
    """Re-derive anchor corrections the just-emitted source deltas staled.

    The Build-Order Step 5 effect-time self-heal, shared by the tails of
    :func:`sync_transfer_postings` and :func:`sync_transaction_postings`
    (which every settle and revert path routes through), and called ONCE per
    scenario after a batch by :func:`re_check_held_anchor_corrections` --
    after the one removal act's deletes with every reversal it emitted
    (ruling **plan#2564**), and after the deploy resync's whole loop with
    every entry that loop emitted (ruling **R-BAL103**) until plan task
    plan#3077 deleted the resync.  It
    QUEUED onto an act's hold rather than walking, and the act's close ran
    it once (``holding_the_anchor_re_check``, ruling **plan#2574**), until
    plan task plan#3075 deleted the hold with its last caller, the
    reconcile.  When the emitted deltas post on a
    non-loan account whose latest anchor assertion sits at-or-after the
    earliest emitted ``entry_date``, that account's opening / true-up
    corrections are reconciled again in the same transaction -- see
    :func:`app.services.account_posting_service.self_heal_anchor_corrections`
    for the predicate's correctness argument, and for why the accounts are
    the ones the deltas' own legs post on (plan task plan#2613).  A no-op
    when nothing was emitted, so the hot idempotent-resync paths pay
    nothing.

    Args:
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

    account_posting_service.self_heal_anchor_corrections(scenario_id, entries)


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
    (:func:`~app.services.ledger_lines.purchase_posts`), a settle dates
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
    every record either side holds, and re-check the anchors only AFTER they
    have, and both are the one removal act's
    (:func:`app.services.movement_removal.remove_movements`, which the
    transfer delete hands every record the pair holds; ruling **plan#2564**).
    This door would find two live, dated movements at that moment and leave
    them posted.

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
    entries = _rebook_transfer_family(xfer)
    _self_heal_account_anchor_corrections(xfer.scenario_id, entries)
    return entries


def _rebook_transfer_family(xfer: Transfer) -> "list[JournalEntry]":
    """Bring *xfer*'s family to target; re-check no anchor correction.

    :func:`sync_transfer_postings`' first half, split off so the deploy
    resync could run it for every transfer and re-check the anchors ONCE
    after its loop (ruling **R-BAL103**), until plan task plan#3077 deleted
    the resync.  Returns the
    emitted deltas, ``[]`` at target.  The family and each movement's LEG
    are
    :func:`~app.services.transfer_legs.transfer_family_movements`' (leaf
    ``X-bi-6-4a``), so no line here reads a shadow row.  It took a *posts*
    rule beside the transfer until plan task plan#2557 deleted the teardown
    twin that passed ``False`` (ruling **plan#2564**): the one removal act
    reverses a leaving record now (:func:`reverse_leaving_movements`).
    """
    family = transfer_family_movements(xfer)
    entries: list[JournalEntry] = []
    for movement, leg in family:
        entries.extend(
            emit_purchase_deltas(
                movement, leg, posted=purchase_posts(leg, movement),
            )
        )
    return entries


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
    (:func:`~app.services.ledger_lines.purchase_posts`), whatever the
    parent's status, and a revert or a cancel reverses what the ledger holds
    for movements the act un-dated or the parent's exclusion made worthless
    (a delete is the one removal act's, :func:`reverse_leaving_movements`,
    since a movement it deletes is still dated and contributing when it is
    reversed).  The ``settled`` flag this took through ``X-bi-3e`` chose
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
    _self_heal_account_anchor_corrections(txn.scenario_id, entries)
    return entries


def _rebook_transaction_family(txn: Transaction) -> "list[JournalEntry]":
    """Bring *txn*'s family to target; re-check no anchor correction.

    :func:`sync_transaction_postings`' first half, run by the deploy resync
    for every row before its one anchor re-check (ruling **R-BAL103**) until
    plan task plan#3077 deleted the resync.  Returns the emitted deltas,
    ``[]`` at target.
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


def reverse_leaving_movements(movements) -> "dict[int, list[JournalEntry]]":
    """Reverse every leaving movement's ledger postings; re-check no anchor yet.

    The one removal act's first step
    (:func:`app.services.movement_removal.remove_movements`, ruling
    **plan#2564**), and its one caller: every movement that leaves the books
    leaves through that act (ruling **R-CC64**: the keys onto a movement are
    NO ACTION, so no other delete can take one), and each is reversed here
    while ``journal_entries.transaction_entry_id`` still links its legs.  That
    key is ``ON DELETE SET NULL``, so once the movement is gone its legs would
    be stranded on their ledger accounts with no offsetting reversal;
    reversing FIRST leaves each original entry and its reversal an immutable
    net-zero pair.  Every movement is reversed, dated or not: it is about to
    be deleted, which no sync's "does it post" rule
    (:func:`~app.services.ledger_lines.purchase_posts`) can say of a
    movement still standing.  A transfer side's record reverses here like any
    movement, typed by its LEG (:func:`~app.services.transfer_legs.movement_parent`).

    **The anchor re-check is HELD, not run, and that is the ruling.**  The
    re-check walks the RECORDS since plan task plan#2525, and at this moment
    they still hold every leaving movement, so a walk here re-derives the
    corrections of the state the act is about to end.  Each row and transfer
    door reversed its own family and re-checked here until plan task
    plan#2557 deleted those doors (``reverse_postings_before_delete``,
    ``reverse_transfer_postings_before_delete``, and this function's
    per-movement predecessor ``reverse_purchase_postings_before_delete``):
    measured, a $200.00 Paid transfer into a Savings account opened at
    $0.00, settled on its first assertion's day (true-up -$200.00), then
    deleted -- Savings' posted ledger read -$200.00 against the records'
    $0.00.  The act re-checks once its deletes have flushed
    (:func:`re_check_held_anchor_corrections`).

    **No account is held beside the reversals**: the re-check reads the
    accounts off their legs (plan task plan#2613), which are where each
    movement was posted, the only accounts whose records its leaving
    changes.

    Idempotent no-op for a movement that never posted (no recorded posting
    day, a card purchase, a non-contributing parent).  Flushes when it emits
    (the balanced write does) but does not commit.

    Args:
        movements: The ``TransactionEntry`` rows leaving, each still flushed
            (``id`` set) so its reversal can read its posted legs back.

    Returns:
        The held re-check: a scenario id -> the reversal entries emitted
        there, empty when nothing posted.
    """
    held: dict[int, list[JournalEntry]] = {}
    for movement in movements:
        parent = movement_parent(movement)
        entries = emit_purchase_deltas(movement, parent, posted=False)
        if entries:
            _hold_for_the_re_check(held, parent.scenario_id, entries)
    return held


def re_check_held_anchor_corrections(
    held: "dict[int, list[JournalEntry]]",
) -> None:
    """Run a batch's held anchor re-check: once per scenario, scenarios ascending.

    The ONE re-check after a batch: the one removal act's (ruling
    **plan#2564**: after its deletes have flushed,
    :func:`reverse_leaving_movements` held it), so the walk reads the
    records as the batch leaves them.  The close of an act's hold
    (``holding_the_anchor_re_check``, ruling **plan#2574**) ran it too until
    plan task plan#3075 deleted the hold, and the deploy resync (ruling
    **R-BAL103**: after every source is re-booked) until plan task plan#3077
    deleted that.  A no-op when *held* is empty.

    Args:
        held: A scenario id -> the entries emitted there, as
            :func:`_hold_for_the_re_check` builds it.

    Raises:
        PostingError: If a re-checked account owes a correction and has no
            linked ledger to book it on (the reconcile's emit step refuses).
    """
    for scenario_id, entries in sorted(held.items()):
        _self_heal_account_anchor_corrections(scenario_id, entries)


def _hold_for_the_re_check(
    held: "dict[int, list[JournalEntry]]",
    scenario_id: int,
    entries: list[JournalEntry],
) -> None:
    """Add one source's re-book or reversal to a batch's one re-check.

    *held* maps a scenario id to the entries emitted there, and
    :func:`re_check_held_anchor_corrections` runs it; the accounts it
    re-checks are read off those entries' legs (plan task plan#2613).
    """
    held.setdefault(scenario_id, []).extend(entries)
