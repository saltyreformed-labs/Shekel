"""
Shekel Budget App -- Recurrence Engine: applying the owner's conflict decisions

:func:`resolve_conflicts`, called by the route layer after the owner answers
the chooser a :class:`~app.exceptions.RecurrenceConflict` raised.

**Nothing here deletes.**  "Keep" leaves the row untouched, "use" clears the
override / soft-delete flags and hands the row back to its definition, and
"move" puts a row changed by hand on its definition's account and keeps
everything else about it, so a row that reaches the chooser survives whichever
branch the owner picks.  **"Use" un-deletes only what the definition's
unarchive would restore** (ruling **R-BAL253**, developer 2026-10-09): a
deleted row its books hold is left deleted and untouched, and named
(:func:`~app.services.definition_unarchive.restored_by_use`, the one rule the
transfer twin asks too).

**A row a move left on a card takes its figure back and keeps its hold** (ruling
**plan#2991**; :attr:`~app.services.definition_unarchive.UseRestore.keep_their_hold`),
**and so does a row the owner kept on another account than its definition's**
(ruling **plan#3230**): "use" answers the price, and the row stays where he put
it, so no later pass moves it unasked.

**"Move" is the account half of a bill's edit** (ruling **plan#3230**, task
plan#3237): a save that moves a bill to another account asks, for each unpaid
row changed by hand, whether it moves too, and a row he moves takes the
definition's account and keeps its hand change -- its figure, its paycheck,
its hold.  The account is the DEFINITION's, read off the row's own template,
never a value from the form.  **A deleted row "use" brings back returns on its
definition's account** for the same reason: it comes back as the definition's
row, so it is asked the books question where it will sit (R-BAL253) and no
later pass moves it.  Before this a row the chooser handed back kept the
account it had, and took the new price on the old account (plan#2931's review,
L1).

**"Use" WRITES NO FIGURE, and that is plan step X-au-e** (ruling **R-JD**).
Every generated transaction row is derived now, so "move this instance to the
template's amount" has no amount to move it to: the act is to stop overriding,
and the definition's own effective-dated series then prices the row as of its
OWN due date.  That is what closes finding **N-244** -- the old "use" wrote the
template's CURRENT ``default_amount`` onto a row whose due date could precede
the edit and cleared the flag that would have marked it, so a $100 history with
one row resolved to $120 read as THREE price changes where one occurred.  A
figure it does not write is a figure it cannot back-date.

**The DECISION survives even though the collision it once mediated cannot
occur** (ruling **R-JD**, developer 2026-09-03).  X-au-e's specification had it
deleted outright, on the ground that a hand-edited month owns its figure and a
regeneration that writes no figure cannot overwrite one.  Both halves of that
are true and neither reaches the offer: ``txn.is_override = False`` below is the
only writer in ``app/`` that clears that flag on a TEMPLATE-LINKED transaction,
and likewise its only per-row un-delete outside archiving and unarchiving the
whole template.

*The qualifier is load-bearing and a first draft omitted it.*  An AST census of
every writer of the two flags found nine that clear one, not six: besides the
create paths, ``transfer_service._update`` and ``._restore``
cleared ``is_override`` on a shadow through a VARIABLE
(``shadow.is_override = flag``), which a grep for the literal cannot see, and
``._restore`` un-deleted both legs.
Those doors reached SHADOWS only, and ``ck_transactions_one_pricing_link`` made
``transfer_id`` exclusive with ``template_id`` -- so the conclusion held by the
schema rather than by the count, which is the stronger ground anyway.  Since
plan step ``balance:X-bi-6-4d-2`` no transaction row names a transfer (plan
step ``balance:X-bi-6-5`` dropped the column), and those two doors write the flags
on the transfer itself, so they reach no transaction row at all.

Deleting the decision would have stranded every
overridden row permanently OWN, 40 of them on the 2026-09-03 production clone,
with no route back to the definition until plan step X-au-h reworks the flag.

**It answers the OVERRIDDEN and SOFT-DELETED conflicts only, never a RETAINED
one.**  ``RecurrenceConflict.retained`` (plan step R10-a) names rows a maintain
pass left untouched because the owner has records against them, and there is no
keep-vs-use question to put to the owner about such a row: the pass already
took the only safe outcome.  The route reports them with
``flash_retained_notice`` and does not render the chooser for them, and
``apply_conflict_decisions`` allow-lists ``overridden | deleted``, so a
retained id cannot reach this module even from a crafted form.
"""
import logging
from collections.abc import Sequence
from typing import NamedTuple

from app.enums import AmountSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.services import posting_service
from app.services.amount_ownership import declare_derived
from app.services.balance_at import BalanceContext
from app.services._recurrence_common import (
    BLOCK_OVERRIDE,
    log_resource_access_denied,
    owner_hold_on,
)
from app.services.definition_unarchive import (
    UseRestore,
    restored_by_use,
    sits_off_its_definitions_account,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    log_event,
)

logger = logging.getLogger(__name__)


class ConflictReporting(NamedTuple):
    """How ONE engine's "use" resolution announces itself.

    The logger and the two strings the event still differs by between the
    engines -- the shape :class:`~app.services.recurrence_engine.PassReporting`
    gives a regeneration -- so :func:`log_use_resolved` states what the event
    REPORTS once.

    Attributes:
        logger: The ENGINE's own logger: the structured-log controls capture
            by logger NAME (``tests/test_services/test_service_log_events/_helpers.py``).
        event: The ``EVT_*`` constant the engine's resolution emits.
        message: The human sentence beside it.
    """

    logger: object
    event: str
    message: str


def log_use_resolved(
    reporting: ConflictReporting, ctx: BalanceContext, use: UseRestore,
    skipped_count: int,
) -> None:
    """Emit one engine's resolution event for the owner's "use" picks.

    **The ONE statement of what a resolution reports**, for both engines'
    ``resolve_conflicts``: the rows handed back to their definition, the ids
    skipped (gone, not the owner's, or naming no definition: the rule's
    :attr:`~app.services.definition_unarchive.UseRestore.orphaned`, ledger
    row **N-440**), and the deleted rows left deleted because the unarchive
    would not restore them (through the chooser: their books hold them,
    ruling **R-BAL253**).  The three sum to the ids handed in.  Spelled in
    each resolver, the tail of the two was one block -- pylint's
    ``duplicate-code`` measured it the moment the second count arrived --
    the lesson :meth:`~app.services._recurrence_common.MaintainOutcome.after`
    records.

    Args:
        reporting: The engine's :class:`ConflictReporting`.
        ctx: The edit's read pass; its ``user_id`` is logged.
        use: The rule's answer
            (:func:`~app.services.definition_unarchive.restored_by_use`).
        skipped_count: The ids that never reached the rule (gone, or not
            the owner's); the rule's orphaned rows are added here.
    """
    log_event(
        reporting.logger, logging.INFO, reporting.event, BUSINESS,
        reporting.message,
        user_id=ctx.user_id, action="update",
        resolved_count=len(use.handed_back),
        skipped_count=skipped_count + len(use.orphaned),
        kept_deleted_count=len(use.left_deleted),
    )


#: How this engine's resolution announces itself.
_RESOLVED = ConflictReporting(
    logger, EVT_RECURRENCE_CONFLICTS_RESOLVED,
    "Recurrence conflicts resolved (update)",
)


def resolve_conflicts(
    transaction_ids: Sequence[int], action: str, ctx: BalanceContext,
) -> str | None:
    """Resolve override/delete conflicts after a regeneration.

    Called by the route layer after the user responds to the conflict prompt.
    Each transaction is ownership-checked against its own ``user_id`` column
    before any modification -- transactions the pass's owner does not own
    are silently skipped (defense-in-depth against IDOR).  It walked
    ``txn.pay_period.user_id`` until plan step ``pay_calendar:C13-b``.

    **It took a ``new_amount`` until plan step X-au-e** (ruling **R-JD**), and
    the parameter is gone rather than defaulted: "use" hands the row back to
    its definition and the definition prices it, so there is no figure for a
    caller to supply and no arm left that would read one.  The transfer twin
    ``transfer_recurrence.resolve_conflicts`` lost its own at plan step
    X-au-f, so the two kinds take one shape.

    **It takes the edit's READ PASS rather than an owner id** (ruling
    **R-BAL253**): a deleted row is un-deleted only where its definition's
    unarchive would restore it, which is asked of the books, the rule and the
    calendar the save leaves -- the pass its regeneration resolved against
    (:func:`~app.services.definition_unarchive.restored_by_use`) -- and the
    owner is that pass's, never an id beside it (developer ruling
    2026-08-16).  Every picked row is checked before any is changed, so the
    books are asked once, of rows as they stand.

    Args:
        transaction_ids: List of Transaction IDs to resolve.
        action:          'update' -- clear override/delete and hand the row
                         back to its definition; a deleted row its books
                         hold stays deleted, untouched.
                         'keep' -- leave the transaction unchanged.
                         'move' -- put a live, unpaid row changed by hand on
                         its definition's account, keeping its hand change
                         (ruling **plan#3230**, :func:`_move_to_their_definitions_account`).
        ctx:             The edit's read pass; its ``user_id`` is the
                         requesting owner's, and transactions it does not
                         own are skipped.

    Returns:
        The sentence naming the picked rows left deleted
        (:attr:`~app.services.definition_unarchive.UseRestore.notice`), or
        ``None`` when none is -- always ``None`` for "keep" and "move".
    """
    if action == "keep":
        # Nothing to do -- the user wants to keep their overrides.
        log_event(
            logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
            "Recurrence conflicts kept (no mutation)",
            user_id=ctx.user_id, action=action,
            transaction_id_count=len(transaction_ids),
        )
        return None

    if action == "move":
        picked, skipped_count = _owned(transaction_ids, ctx)
        _move_to_their_definitions_account(picked, ctx, skipped_count)
        return None

    if action == "update":
        picked, skipped_count = _owned(transaction_ids, ctx)
        # **A deleted row brought back returns on its definition's account**
        # (ruling **plan#3230**): it comes back as the definition's row, and
        # the books question below is asked where it will sit.  Moved and
        # flushed BEFORE that question, which reads the stored account; a row
        # the books then hold stays deleted on that account, which no balance
        # counts.
        for txn in _deleted_off_their_definitions_account(picked):
            _put_on_its_definitions_account(txn)
        db.session.flush()

        # **What the unarchive would leave deleted, "use" leaves deleted**
        # (ruling **R-BAL253**): asked once, of every picked row as it stands,
        # through the one rule the transfer twin asks too.  A row the answer
        # holds is left exactly as found -- flags, figure and all -- and so is
        # a row that names no definition (ledger row **N-440**, a guard this
        # resolver spelled for itself until it moved into the rule).
        use = restored_by_use(picked, ctx)
        for txn in use.handed_back:
            txn.is_override = txn.id in use.keep_their_hold
            txn.is_deleted = False
            # **The whole of "use the template's amount"** since plan step
            # X-au-e: the row stops owning a figure and DECLARES the definition
            # that prices it, which is the same statement
            # ``_amounts._derive_row_fields`` makes about a row generation
            # writes.  One producer of "what ownership does a row of this
            # definition take", so generation, the maintain splat and this
            # chooser cannot come to disagree (``CLAUDE.md`` rule 14).
            declare_derived(txn, AmountSourceEnum.TEMPLATE)
        db.session.flush()
        # **Restoring a row restores its purchases' cash legs** (plan step
        # X-f3b, ruling **R-FM**).  This loop un-deletes rows and may re-price
        # them, and both moves are ledger acts now that a PROJECTED envelope can
        # hold postings: ``delete_transaction`` reversed a soft-deleted row's
        # family on the way out, so without this the read fold re-acquires a
        # movement the ledger no longer holds.  Idempotent and empty-handed for
        # a row whose family never posted, which is every other row here.
        for txn in use.handed_back:
            posting_service.sync_transaction_postings(txn)
        log_use_resolved(_RESOLVED, ctx, use, skipped_count)
        return use.notice
    return None


def _owned(
    transaction_ids: Sequence[int], ctx: BalanceContext,
) -> tuple[list[Transaction], int]:
    """Return the rows among *transaction_ids* the pass's owner owns, and how many were skipped.

    The ownership check every mutating action runs first, stated once.  Each
    transaction is checked against its own ``user_id`` column; it walked
    ``txn.pay_period.user_id`` until plan step ``pay_calendar:C13-b``.  A
    row of another owner is skipped and logged as an IDOR probe
    (ACCESS-category: the requester does not own the row, though the skip is
    silent to them), and a vanished id is skipped too.

    Args:
        transaction_ids: The picked Transaction ids.
        ctx: The edit's read pass; its ``user_id`` is the requesting owner's.

    Returns:
        ``(rows, skipped_count)``: the owner's rows in the order given, and
        the ids that were gone or not theirs.
    """
    skipped_count = 0
    picked = []
    for txn_id in transaction_ids:
        txn = db.session.get(Transaction, txn_id)
        if txn is None:
            skipped_count += 1
            continue
        if txn.user_id != ctx.user_id:
            log_resource_access_denied(
                logger,
                user_id=ctx.user_id,
                model="Transaction",
                pk=txn_id,
                owner_id=txn.user_id,
            )
            skipped_count += 1
            continue
        picked.append(txn)
    return picked, skipped_count


def _deleted_off_their_definitions_account(
    rows: Sequence[Transaction],
) -> list[Transaction]:
    """Return the deleted rows among *rows* on another account than their definition's.

    Read by "use", which brings such a row back on its definition's account
    (ruling **plan#3230**).  **A row re-priced and then deleted is one too**:
    a delete keeps ``is_override``, so the regeneration files it under its
    overridden conflicts (``_recurrence_common.owner_hold_on``) and the
    account question, which asks nothing of a deleted row, never reaches it.
    Excluding it here brought it back on the account it was deleted from and
    held it there for good (the adversarial review of task plan#3237, H1).

    Args:
        rows: The owner's picked rows.

    Returns:
        The deleted rows naming a definition whose account differs from theirs.
    """
    return [
        txn for txn in rows
        if txn.is_deleted and sits_off_its_definitions_account(txn)
    ]


def _put_on_its_definitions_account(txn: Transaction) -> None:
    """Put *txn* on the account its definition names now, column and relationship together.

    **The relationship is assigned, not only the column** (the adversarial
    review of task plan#3237, M1): a reader of ``txn.account`` later in the
    same request -- the card-move hold in
    :func:`~app.services.definition_unarchive.restored_by_use` reads it -- would
    otherwise see the account the row left until the session expired it.  The
    account is read by the definition's COLUMN, which the edit set.

    Args:
        txn: The owner's row naming a definition.
    """
    txn.account = db.session.get(Account, txn.template.account_id)


def _move_to_their_definitions_account(
    rows: Sequence[Transaction], ctx: BalanceContext, skipped_count: int,
) -> None:
    """Put each live, unpaid row changed by hand on its definition's account.

    **The account half of a bill's edit** (ruling **plan#3230**, task
    plan#3237): the owner answered "Move to <account>" for these rows, so each
    takes the account its definition names now and keeps everything else --
    its figure, its paycheck and its hold (``is_override`` stays), because the
    hand change is still his.  The account is read off the row's OWN
    definition, never handed in, so no form value can name it.

    **Only a row the question can be asked of moves**: one held by its owner's
    hand change (``_recurrence_common.owner_hold_on`` answers
    ``BLOCK_OVERRIDE``), live, and on another account than its definition's
    (:func:`~app.services.definition_unarchive.sits_off_its_definitions_account`).
    A settled row is history and never moves ("Paid rows never move"), and a
    soft-deleted one is on no balance; this refuses those whatever ids it is
    handed.  **It does not ask the books question**: whether the new
    account's books would hold the row's day (ruling **R-PC99**) is the
    caller's, asked where the page is built
    (``routes._recurrence_account_question.AccountQuestion.movable``, which
    is the only list of ids the chooser hands here).  A caller handing ids it
    has not asked that of can place a row inside those books.

    Its postings are reconciled after the flush, as the maintain pass does
    for a row it moves (``_maintain._apply_maintain_work``): a row's own
    covering movement follows it, while a purchase stays on the account it
    was paid from (ruling **R-CC36**).

    Args:
        rows: The owner's picked rows (:func:`_owned`).
        ctx: The edit's read pass; its ``user_id`` is logged.
        skipped_count: The ids :func:`_owned` skipped; those this declines
            are added.
    """
    moved = [
        txn for txn in rows
        if owner_hold_on(txn) == BLOCK_OVERRIDE and not txn.is_deleted
        and sits_off_its_definitions_account(txn)
    ]
    for txn in moved:
        _put_on_its_definitions_account(txn)
    db.session.flush()
    for txn in moved:
        posting_service.sync_transaction_postings(txn)
    log_event(
        logger, logging.INFO, EVT_RECURRENCE_CONFLICTS_RESOLVED, BUSINESS,
        "Recurrence conflicts moved to the definition's account",
        user_id=ctx.user_id, action="move",
        moved_count=len(moved),
        skipped_count=skipped_count + len(rows) - len(moved),
    )
