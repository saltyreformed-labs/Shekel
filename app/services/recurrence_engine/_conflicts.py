"""
Shekel Budget App -- Recurrence Engine: applying the owner's conflict decisions

:func:`resolve_conflicts`, called by the route layer after the owner answers
the chooser a :class:`~app.exceptions.RecurrenceConflict` raised.

**Nothing here deletes.**  "Keep" leaves the row untouched and "use" clears the
override / soft-delete flags and hands the row back to its definition, so a row
that reaches the chooser survives whichever branch the owner picks.  **"Use"
un-deletes only what the definition's unarchive would restore** (ruling
**R-BAL253**, developer 2026-10-09): a deleted row its books hold is left
deleted and untouched, and named
(:func:`~app.services.definition_unarchive.restored_by_use`, the one rule the
transfer twin asks too).

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
every writer of the two flags finds nine that clear one, not six: besides the
create paths, ``transfer_service._update`` and ``._restore``
clear ``is_override`` on a shadow through a VARIABLE
(``shadow.is_override = flag``), which a grep for the literal cannot see, and
``._restore`` un-deletes both legs.
Those doors reach SHADOWS only, and ``ck_transactions_one_pricing_link`` makes
``transfer_id`` exclusive with ``template_id`` -- so the conclusion holds by the
schema rather than by the count, which is the stronger ground anyway.

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
from app.models.transaction import Transaction
from app.exceptions import ValidationError
from app.services import posting_service
from app.services.amount_ownership import declare_derived
from app.services.balance_at import BalanceContext
from app.services._recurrence_common import log_resource_access_denied
from app.services.definition_unarchive import UseRestore, restored_by_use
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_CONFLICTS_RESOLVED,
    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED,
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
        ctx:             The edit's read pass; its ``user_id`` is the
                         requesting owner's, and transactions it does not
                         own are skipped.

    Returns:
        The sentence naming the picked rows left deleted
        (:attr:`~app.services.definition_unarchive.UseRestore.notice`), or
        ``None`` when none is -- always ``None`` for "keep".

    Raises:
        ValidationError: A transfer shadow's id was handed in; nothing is
            changed.
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

    if action == "update":
        skipped_count = 0
        picked = []
        for txn_id in transaction_ids:
            txn = db.session.get(Transaction, txn_id)
            if txn is None:
                skipped_count += 1
                continue

            # Ownership check: the row's own ``user_id`` column.  It read
            # ``txn.pay_period.user_id`` -- Transaction -> PayPeriod ->
            # user_id -- until plan step ``pay_calendar:C13-b``.
            if txn.user_id != ctx.user_id:
                # Cross-user request: emit the IDOR-detection event so
                # SOC tooling sees the probe.  ACCESS-category is the
                # right home for this -- the requester does not own
                # the row even though we silently skip it.
                log_resource_access_denied(
                    logger,
                    user_id=ctx.user_id,
                    model="Transaction",
                    pk=txn_id,
                    owner_id=txn.user_id,
                )
                skipped_count += 1
                continue

            # Transfer shadow guard (CLAUDE.md Transfer invariant 4 / F-007).
            # Shadow rows (transfer_id IS NOT NULL) are owned by the transfer
            # service.  resolve_conflicts is reachable only from the
            # transaction-template regeneration flow, which never produces
            # shadow IDs in its conflict set; a shadow ID arriving here is
            # therefore an internal logic error or an attacker probe.
            # Mutating a shadow directly would desynchronise the parent
            # transfer's amount/status/period from its sibling shadow and
            # silently corrupt the user's balance projections.  Refuse.
            if txn.transfer_id is not None:
                log_event(
                    logger, logging.WARNING,
                    EVT_RESOLVE_CONFLICTS_SHADOW_REFUSED, BUSINESS,
                    "Refused to mutate transfer shadow via resolve_conflicts",
                    user_id=ctx.user_id,
                    transaction_id=txn_id,
                    transfer_id=txn.transfer_id,
                    action=action,
                )
                raise ValidationError(
                    "Cannot modify transfer shadow transactions via "
                    "resolve_conflicts.  Route transfer mutations through "
                    "transfer_service."
                )

            picked.append(txn)

        # **What the unarchive would leave deleted, "use" leaves deleted**
        # (ruling **R-BAL253**): asked once, of every picked row as it stands,
        # through the one rule the transfer twin asks too.  A row the answer
        # holds is left exactly as found -- flags, figure and all -- and so is
        # a row that names no definition (ledger row **N-440**, a guard this
        # resolver spelled for itself until it moved into the rule).
        use = restored_by_use(picked, ctx)
        for txn in use.handed_back:
            txn.is_override = False
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
