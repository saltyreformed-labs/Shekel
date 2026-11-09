"""
Shekel Budget App -- Credit Card Workflow Service: the CC Payback rows.

The pre-card spelling of "this went on the card": a row in status Credit with
an auto-generated CC Payback expense in the next pay period, and an envelope's
card purchases each repaid the same way (``entry_credit_workflow``).  Since plan
task plan#2929 a Credit press writes no payback: it puts the row on the card
(``transaction_service._credit``, rulings plan#2887 and plan#2888), and the
card's own payment pays it off.  What stays here is what the rows ALREADY
holding a payback need until plan#1034's re-plan: finding a row's live payback,
Undo CC deleting it (:func:`unmark_credit`), a deleted source taking its chain
down, and the payback's shape for the envelope purchases that still write one.
"""

import logging
from decimal import Decimal

from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.category import Category
from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.services import (
    match_press,
    match_withdrawal,
    movement_removal,
    posting_service,
    status_seam,
)
from app.services.owned_transaction import load_owned_transaction
from app.services.pay_calendar import DerivedPeriod
from app.exceptions import ValidationError
from app.utils.balance_predicates import is_credit
from app.utils.log_events import (
    BUSINESS,
    EVT_CREDIT_UNMARKED,
    EVT_PAYBACK_DELETED_WITH_SOURCE,
    log_event,
)

logger = logging.getLogger(__name__)

# The category used for auto-generated credit card payback expenses.
CC_PAYBACK_GROUP = "Credit Card"
CC_PAYBACK_ITEM = "Payback"


def get_active_payback(source_txn_id: int) -> Transaction | None:
    """Return the live (non-soft-deleted) CC payback for a source txn.

    The single definition of "the payback for this transaction," shared
    by the unmark and entry-sync paths so they cannot disagree on
    which row counts.  The ``is_deleted == False`` filter mirrors the
    partial unique index ``uq_transactions_credit_payback_unique``
    (partial on ``credit_payback_for_id IS NOT NULL AND is_deleted =
    FALSE``) and the binding soft-delete query rule: a soft-deleted
    payback is kept for the audit trail but must never be treated as the
    live payback, so a re-mark after a soft-delete legally creates a
    fresh active row instead of resurrecting the dead one.

    Args:
        source_txn_id: The ``id`` of the credit source transaction.

    Returns:
        The active payback :class:`Transaction`, or ``None`` when no
        live payback exists for the source.
    """
    return active_paybacks([source_txn_id]).get(source_txn_id)


def active_paybacks(source_txn_ids: "list[int]") -> "dict[int, Transaction]":
    """Return the live CC payback of each of several source rows, in ONE query.

    :func:`get_active_payback` for a screen drawing many sources at once --
    the grid's purchase lists, whose X on an envelope's last card purchase
    names what deleting that payback frees (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC80**) -- and the one spelling
    of "live" both read, so the screen and the door that deletes it cannot
    disagree on which row counts.  At most one per source:
    ``uq_transactions_credit_payback_unique`` holds it.

    Args:
        source_txn_ids: The ``id`` values of the credit source rows.

    Returns:
        ``{source id: payback}`` for each source that has a live payback,
        its ``entries`` loaded.
    """
    if not source_txn_ids:
        return {}
    return {
        payback.credit_payback_for_id: payback
        for payback in db.session.query(Transaction)
        .filter(
            Transaction.credit_payback_for_id.in_(source_txn_ids),
            Transaction.is_deleted.is_(False),
        )
        .options(selectinload(Transaction.entries))
        .all()
    }


def pending_for_credit_revert(
    txn: Transaction,
) -> "match_withdrawal.MatchWithdrawal | None":
    """Return what reverting Credit row *txn* would withdraw, or ``None``.

    The read twin of :func:`delete_payback_on_credit_revert`, for the full-edit
    card's Undo CC and its Status leaving Credit (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC80**: *"all three card-payback
    buttons name the bank line before you press, using the same data the
    button acts on, as the popovers do"*).  The SAME payback and the SAME
    rows-leaving read the removal act makes over it
    (:func:`~app.services.match_withdrawal.pending_for_rows`), so the caption
    names exactly the lines the press frees.

    Args:
        txn: The Credit row the card is drawn for.

    Returns:
        A :class:`~app.services.match_withdrawal.MatchWithdrawal`, or
        ``None`` when the row has no live payback to delete.
    """
    payback = get_active_payback(txn.id)
    if payback is None:
        return None
    return match_withdrawal.pending_for_rows([payback])


def delete_payback_on_credit_revert(
    txn: Transaction, user_id: int, *,
    press: match_press.Press | None = None,
) -> None:
    """Delete the live auto-generated payback for a reverted credit row.

    The single cleanup rule for "a Credit transaction returned to
    Projected": the live payback (if any) is hard-deleted and the
    reversion is logged with ``EVT_CREDIT_UNMARKED``.  Shared by
    :func:`unmark_credit` and the transaction PATCH route's
    status-revert path so a credit reversion can never orphan its
    payback regardless of which endpoint performed it.  A soft-deleted
    prior payback stays in place for the audit trail (the
    :func:`get_active_payback` contract).

    The caller owns the status flip itself and must already have
    verified ownership and the transition's legality; this helper does
    not commit -- the deletion joins the caller's transaction so the
    status change and the payback removal land atomically.

    Args:
        txn: The credit transaction being reverted to Projected.
        user_id: The owning user's ID, recorded on the audit event.
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            the bank lines the card's caption named
            (:func:`pending_for_credit_revert`), posted back by Undo CC and
            by the Save that sets Status back to Projected.  A caller whose
            page names none passes ``None``, which refuses a press that would
            free a line (ruling **R-CC127**).

    Raises:
        PageOutOfDate: When the lines deleting the payback frees differ from
            what *press*'s page named (``match_withdrawal.take_out_of_matches``).
    """
    payback = get_active_payback(txn.id)
    deleted_payback_id = None
    if payback:
        deleted_payback_id = payback.id
        # Its movements off the books through the ONE act (plan step
        # ``credit_card:CC-5-4a-3``, ruling **R-CC54**), which reverses their
        # postings before it deletes them (Build-Order Step 3
        # reverse-before-delete: a payback settled -- and therefore posted --
        # before its source's Credit status is reverted must not leave its
        # legs stranded) and re-checks the anchor corrections once they are
        # gone (ruling **plan#2564**; this reversed the payback's family
        # itself, re-checking while its movements stood, until plan task
        # plan#2557).  A no-op for the usual still-Projected payback, whose
        # movements never posted.  A payback a bank line
        # was matched to stops existing here, so an act it was the last app row
        # of is withdrawn and that line is unexplained again (developer ruling
        # 2026-08-25, plan step ``bank_import:X-gb``).  Measured on the
        # developer's own dev database at 4 matched paybacks, every one of them
        # reachable from the Undo CC button on the grid card.  Both doors say
        # so first since plan step ``credit_card:CC-5-4a-5`` (ruling
        # **R-CC80**, closing finding **CC-367**): the card's caption names
        # the lines, and the press sends them back for the act to compare.
        movement_removal.remove_movements(
            list(payback.entries), user_id,
            because=match_withdrawal.LEFT_THE_BOOKS,
            press=press, rows_leaving=[payback],
        )
        db.session.delete(payback)

    log_event(
        logger, logging.INFO, EVT_CREDIT_UNMARKED, BUSINESS,
        "Credit reverted to Projected; payback deleted",
        user_id=user_id,
        transaction_id=txn.id,
        deleted_payback_id=deleted_payback_id,
    )


def live_payback_chain(txn: Transaction) -> "list[Transaction]":
    """Return every live payback that goes down with *txn*, nearest first.

    **The walk, published, because THREE things need it and one of them may not
    write** (plan step ``bank_import:X-gb``).  A payback can itself be marked
    Credit, so a source can carry a chain rather than a single row:
    :func:`delete_payback_on_source_delete` takes the whole chain down, the
    delete verb withdraws the statement matches naming every row in it, and the
    confirm dialog on the delete control has to NAME them before the owner
    presses anything.  A read that could not see the chain would print a dialog
    the press then exceeds, which is the one thing a destructive dialog may not
    do.

    **It replaced a RECURSION inside the teardown**, which was the same walk
    expressed where nothing else could reach it.

    **Bounded against a cycle** by the ids it has already seen.  ``ck_transactions
    _one_pricing_link`` does not forbid one and the FK points at this same
    table, so a corrupt ``A repays B repays A`` is expressible; the recursion
    this replaced would have exhausted the stack on it.

    Args:
        txn: The row whose live paybacks to walk.

    Returns:
        The chain, nearest first -- ``[]`` for the ordinary row that has none.
        A soft-deleted prior payback is not in it (the
        :func:`get_active_payback` contract).
    """
    chain: "list[Transaction]" = []
    seen = {txn.id}
    current = txn
    while True:
        payback = get_active_payback(current.id)
        if payback is None or payback.id in seen:
            return chain
        chain.append(payback)
        seen.add(payback.id)
        current = payback


def delete_payback_on_source_delete(txn: Transaction, user_id: int) -> None:
    """Delete the live auto-generated payback chain when its source is deleted.

    The deletion-side sibling of :func:`delete_payback_on_credit_revert`:
    where that helper cleans up after a Credit row returning to
    Projected, this one cleans up after the source transaction being
    deleted outright (soft or hard) -- the strongest possible withdrawal
    of the credit assertion.  Without it a SOFT delete, which never fires
    ``credit_payback_for_id``, leaves the projected payback surviving its
    hidden source and silently inflating the next period with no offsetting
    credit row; a HARD delete would be REFUSED by that key, which is
    ``ondelete="RESTRICT"`` since plan step ``balance:X-bi-7d-2`` (finding
    **CC-352**).

    **It reverses no posting, because none is left to reverse**: the one
    caller's removal act reversed every level's movements, deleted them and
    re-checked the anchors before this runs (ruling **plan#2564**), and a
    level still holding a movement could not be deleted here at all -- the
    movement's key is NO ACTION (ruling **R-CC64**).  It called
    ``posting_service.reverse_postings_before_delete`` per level, a no-op
    for a row holding nothing, until plan task plan#2557 deleted that door.

    Keyed on :func:`get_active_payback` rather than Credit status
    because entry-level credit sources carry a live payback while their
    own ``status_id`` is NOT Credit -- a status guard would miss them.
    **It severs no entry link, because none is left to sever**: the one
    caller has taken every movement of the source and of each level off
    the books through the one removal act first, on both of its arms
    (plan step ``credit_card:CC-5-4a-4``; ruling **R-CC75**: a soft-deleted
    source no longer keeps its entries).  A link a direct caller leaves
    in place is ``TransactionEntry.credit_payback_id``'s ``ON DELETE SET
    NULL`` to clear.  No-op
    (and no log event) when no live payback exists -- the common case for
    every ordinary delete.

    **It walks :func:`live_payback_chain` where it used to RECURSE**, and
    the order is unchanged: DEEPEST FIRST, so each level is deleted before
    the level above it, which ``fk_transactions_credit_payback_for``
    (``ON DELETE RESTRICT``) requires: a level deleted while the one below
    it still names it is refused.  The order carries nothing for the
    ledger, because the one caller reversed every level's postings and
    took every movement off the books before this runs.
    The walk is published because the delete verb and its confirm dialog
    need the same chain and neither may write to get it (plan step
    ``bank_import:X-gb``).

    **It does NOT withdraw the statement matches naming the chain**, and
    that is the caller's job on purpose: ``transaction_service
    .delete_transaction`` takes the movements of the source AND its whole
    chain off the books in ONE act (``movement_removal.remove_movements``,
    plan step ``credit_card:CC-5-4a-3``) before it calls this, so the figure
    its confirm dialog printed and the figure its receipt reports are one
    derivation, and the rows this takes down hold no movement.  Withdrawing here as well would
    make the verb's own report exclude the chain it took down -- measured
    at a ``$200.00`` card payment silently un-explained while the dialog
    said nothing and the log line read ``matches_withdrawn=0`` (adversarial
    review, 2026-08-25).

    This helper does not commit -- the deletions join the caller's
    transaction so the source delete and the payback removal land
    atomically.

    Args:
        txn: The source transaction about to be deleted.
        user_id: The owning user's ID, recorded on the audit events.
    """
    chain = live_payback_chain(txn)
    for depth in range(len(chain) - 1, -1, -1):
        payback = chain[depth]
        parent = txn if depth == 0 else chain[depth - 1]
        db.session.delete(payback)

        log_event(
            logger, logging.INFO, EVT_PAYBACK_DELETED_WITH_SOURCE, BUSINESS,
            "Source transaction deleted; live payback deleted with it",
            user_id=user_id,
            transaction_id=parent.id,
            deleted_payback_id=payback.id,
        )


def unmark_credit(
    transaction_id, user_id, *,
    press: match_press.Press | None = None,
):
    """Revert a transaction from 'credit' back to 'projected' and delete its payback.

    Two precondition checks layered for clarity and defense-in-depth:

      1. **Bespoke source-state guard.** The action only makes sense
         on a row that is currently in ``Credit`` status -- that is
         the row that has an auto-generated payback to clean up.
         Calling on any other status would silently rewrite
         ``status_id`` to ``Projected`` (dropping a Paid row back to
         Projected, for example) and attempt to delete a non-existent
         payback.  The previous implementation had no such guard;
         this fixes that latent bug.

      2. **State-machine verification.** ``verify_transition`` is
         called as a final policy choke point so any future caller
         that adds a new ``Status`` row -- or any code path that
         skips the bespoke guard -- still cannot push a row through
         an illegal transition.  **After plan step balance:X-am this
         verifier can no longer refuse any RECOGNISED status here**, and that
         is worth stating rather than leaving a reader to find: this call site
         only ever asks for ``Projected``, and every state in the transaction
         map now has an edge to it.  What the check still catches is a corrupt
         current ``status_id``, and a future status added without that edge --
         which is what defense in depth is for.  It named
         ``Settled -> Projected`` until X-am deleted that status, and that
         refusal really did fire here.

    Args:
        transaction_id: The ID of the credited transaction.
        user_id: The ID of the user who owns the transaction.
            Defense-in-depth: ownership is verified against the row's own
            ``user_id`` column.
        press: The save's press over the bank lines Undo CC's caption named
            (:func:`delete_payback_on_credit_revert`).

    Raises:
        NotFoundError: If the transaction doesn't exist or doesn't
            belong to *user_id*.
        ValidationError: If the transaction is not currently in
            ``Credit`` status, or if the transition (in the unlikely
            case the bespoke guard is bypassed) is not allowed by
            the state machine.
        PageOutOfDate: When deleting the payback would free other bank
            lines than *press*'s page named.
    """
    # Defense-in-depth: ownership on the row's own owner column.
    txn = load_owned_transaction(transaction_id, user_id)

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    # Bespoke source-state guard.  Friendly user-facing message that
    # names the offending status.  The route layer surfaces this as
    # the response body on a 400.  Routed through the centralized
    # ``is_credit`` predicate (D6-09 / MED-02); the ``projected_id``
    # local binding above is for the state-machine call and the
    # post-guard status assignment below.
    if not is_credit(txn):
        current_name = txn.status.name if txn.status is not None else "<unset>"
        raise ValidationError(
            f"Cannot unmark credit on a '{current_name}' transaction.  "
            "Only Credit transactions can be unmarked."
        )

    # Revert the original transaction's status through the single status seam.
    # The seam runs the same state-machine verification (defense-in-depth,
    # redundant with the bespoke ``is_credit`` guard above except when a future
    # StatusEnum addition makes that guard incomplete) before assigning
    # ``status_id``; Projected is non-settled, so it also clears ``settled_on``.
    status_seam.apply_status_change(txn, projected_id)

    # Delete the live payback + write the audit event.  Shared with the
    # transaction PATCH route's status-revert path via the single
    # cleanup helper so the two endpoints cannot disagree.
    delete_payback_on_credit_revert(txn, user_id, press=press)

    # Posting ledger reconcile (Build-Order Step 3): reconcile the SOURCE row's
    # family as the final step (the transfer pattern: reconcile on every status
    # change).  Projected contributes again, so a dated purchase the Credit
    # status had reversed posts again here; a row with none is an idempotent
    # no-op -- keeping the "every status handler reconciles last" invariant
    # complete.  The payback's own postings were already reversed inside
    # delete_payback_on_credit_revert.
    posting_service.sync_transaction_postings(txn)


def get_or_create_cc_category(user_id: int) -> Category:
    """Find or create the 'Credit Card: Payback' category for a user.

    Args:
        user_id: The owning user's ID.

    Returns:
        The Category object.
    """
    category = (
        db.session.query(Category)
        .filter_by(
            user_id=user_id,
            group_name=CC_PAYBACK_GROUP,
            item_name=CC_PAYBACK_ITEM,
        )
        .first()
    )
    if category is None:
        category = Category(
            user_id=user_id,
            group_name=CC_PAYBACK_GROUP,
            item_name=CC_PAYBACK_ITEM,
        )
        db.session.add(category)
        db.session.flush()
    return category


def create_cc_payback_transaction(
    source_txn: Transaction,
    next_period: DerivedPeriod,
    cc_category: Category,
    amount: Decimal,
) -> Transaction:
    """Build, add, and flush the CC Payback expense for a credit transaction.

    Written by the entry-level credit payback in
    :mod:`app.services.entry_credit_workflow`, at the sum of the envelope's
    card purchases: a PROJECTED EXPENSE in the next pay period, categorised to
    the user's CC Payback category, linked back to the source transaction via
    ``credit_payback_for_id``.  The row-level Credit press wrote the same
    shape until plan task plan#2929, which puts the row on the card instead
    (``transaction_service._credit``).  PROJECTED status and EXPENSE type are
    invariants of a payback, so they are resolved here rather than passed in.

    **``next_period.period_id`` is an ``int`` by a PROPERTY of the search that
    produced it**, which is what makes the ``NOT NULL`` write below safe with
    no guard here (plan step C2-f1).
    :meth:`~app.services.pay_calendar.PayCalendar.period_starting_after`
    searches the MATERIALISED periods only -- the same enforcement
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period` carries, and
    for the same reason -- so it cannot answer a projection or an unsaved
    candidate, the two states in which
    :attr:`~app.services.pay_calendar.DerivedPeriod.period_id` is ``None``.
    Past the last payday it answers ``None`` outright and the caller raises a
    :class:`~app.exceptions.ValidationError` instead of reaching here.

    *An earlier draft argued this from a CALL-SITE census instead -- the
    callers built their calendar with ``calendar_for``, which reads saved rows
    only.  That was true and is not a property*, which is the distinction an
    adversarial review of C2-f1 made and which this package had already been
    corrected on once, at ``filing_period``.

    Args:
        source_txn: The credit transaction being paid back.  Supplies
            the account, scenario, name, and ``credit_payback_for_id``
            link.
        next_period: The pay period the payback lands in (the period
            after ``source_txn``'s), as the calendar's own
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        cc_category: The user's "Credit Card: Payback" category (from
            :func:`get_or_create_cc_category`).
        amount: The payback's ``estimated_amount`` (a Decimal).

    Returns:
        The newly created payback :class:`Transaction`, flushed so its
        ``id`` is available for entry linkage and logging.
    """
    payback = Transaction(
        # The payback belongs to whoever the credit row belongs to (plan step
        # ``pay_calendar:C13-a``); it is the same fact the account and the
        # period below already carry, and the composite keys refuse a
        # disagreement.
        user_id=source_txn.user_id,
        account_id=source_txn.account_id,
        template_id=None,
        pay_period_id=next_period.period_id,
        scenario_id=source_txn.scenario_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=f"CC Payback: {source_txn.name}",
        category_id=cc_category.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        amount_ownership=AmountOwnership.own(amount),
        credit_payback_for_id=source_txn.id,
    )
    db.session.add(payback)
    db.session.flush()
    return payback
