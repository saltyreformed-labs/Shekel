"""
Shekel Budget App -- Entry-Level Credit Card Workflow Service

Manages aggregated CC Payback transactions generated from individual
credit entries on entry-capable transactions.  When entries are flagged
as credit card purchases, this service creates, updates, or deletes
a single CC Payback expense in the next pay period whose amount equals
the sum of all credit entries.

This is the per-entry counterpart to credit_workflow.py, which handles
the legacy per-transaction Credit status.  Both services create CC
Payback transactions with identical field structures; the difference is
the amount source (entry sum vs. transaction amount) and the trigger
(entry mutation vs. status change).
"""

import logging
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    match_press,
    match_withdrawal,
    movement_removal,
)
from app.services.amount_ownership import state_own_amount
from app.services.row_valuation import settled_figure
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.credit_workflow import (
    create_cc_payback_transaction,
    get_active_payback,
    get_or_create_cc_category,
)
from app.services.owned_transaction import load_owned_transaction
from app.exceptions import ValidationError
from app.utils.entry_partition import partition_entries
from app.utils.log_events import (
    BUSINESS,
    EVT_ENTRY_PAYBACK_CREATED,
    EVT_ENTRY_PAYBACK_DELETED,
    EVT_ENTRY_PAYBACK_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)


def card_total(
    purchases: "list[TransactionEntry]",
    leaving_ids: "frozenset[int] | set[int]" = frozenset(),
) -> Decimal:
    """Return what an envelope's card purchases sum to -- what its payback repays.

    ONE spelling for the sync below and for the screen that says, before a
    press, whether that press deletes the payback (:func:`payback_deleted_by`).
    The partition is the shared helper's, so "which entries are credits" has
    one definition (DH-#75); the sum starts at ``Decimal("0")`` so an empty
    one is a Decimal, not the integer ``sum()`` would give.

    Args:
        purchases: The envelope's PURCHASES (ruling R-BAL68: the seam's
            covering movement is never a credit, so the partition agrees
            either way, and the reading says what is being partitioned).
        leaving_ids: Ids of purchases a press would take out of the card set
            -- deleted, or un-ticked -- left out of the sum; empty for the sum
            as it stands.

    Returns:
        The card total.
    """
    _, credit_entries = partition_entries(purchases)
    return sum(
        (e.amount for e in credit_entries if e.id not in leaving_ids),
        Decimal("0"),
    )


def _settled_teardown_refusal(payback: Transaction) -> str | None:
    """Return why a payback that has SETTLED may not be deleted, or ``None``.

    The refusal :func:`sync_entry_payback` raises before it deletes a payback
    whose money has moved, and the reason :func:`payback_deleted_by` answers
    such a payback as kept: a press refused here deletes nothing, so a screen
    must not name what deleting it would free.

    Args:
        payback: The live payback.

    Returns:
        The sentence, or ``None`` when the payback has not settled.
    """
    recorded = settled_figure(payback)
    if recorded is None:
        return None
    return (
        f"The payback '{payback.name}' has settled at "
        f"${recorded:,.2f}, so it cannot be removed: that money has "
        "already left the "
        "account. Set the payback back to Projected first -- the "
        "figure it recorded is kept -- and then remove the purchase."
    )


def payback_deleted_by(
    txn: Transaction,
    leaving_ids: "frozenset[int] | set[int]",
    payback: Transaction | None,
) -> Transaction | None:
    """Return the payback a press taking *leaving_ids* off the card deletes, or ``None``.

    The read twin of :func:`sync_entry_payback`'s delete arm, for the
    purchase list's X on a card purchase and its CC un-tick (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC80**: *"deleting or
    un-crediting an envelope's last credit purchase"* names the bank line
    first).  The sync's three arms partition the card total by its sign --
    above zero the payback is kept or re-priced, below zero the write is
    refused (finding **N-411**), and at exactly zero a live payback is
    deleted unless it has settled, which is refused instead -- so the press
    deletes the payback exactly when the total it leaves is zero, a payback
    is live, and the settled refusal has nothing to say.

    Args:
        txn: The envelope, its ``purchases`` accessible.
        leaving_ids: Ids of the purchases the press takes off the card.
        payback: The envelope's live payback
            (:func:`~app.services.credit_workflow.active_paybacks`), or
            ``None``.

    Returns:
        *payback* when the press deletes it, else ``None``.
    """
    if payback is None or card_total(txn.purchases, leaving_ids) != 0:
        return None
    if _settled_teardown_refusal(payback) is not None:
        return None
    return payback


def payback_a_removal_deletes(
    txn: Transaction, leaving_ids: "frozenset[int] | set[int]",
) -> Transaction | None:
    """Return the live payback a press taking *leaving_ids* off the card deletes.

    :func:`payback_deleted_by` over the envelope's own live payback, for the
    purchase delete, which must take the payback's movements off the books
    in the SAME removal act as the purchase's (plan step
    ``credit_card:CC-5-4a-5``): the act grades one press against what its
    page named, and the payback is on the envelope's account, so a second
    act would meet the first's line and refuse it.

    Args:
        txn: The envelope.
        leaving_ids: Ids of the purchases the press takes off the card.

    Returns:
        The payback the press deletes, else ``None``.
    """
    return payback_deleted_by(txn, leaving_ids, get_active_payback(txn.id))


def payback_refusal(txn: Transaction) -> str:
    """Return the sentence a companion's press is refused with over the PAYBACK's line.

    Rulings **R-CC130** / **R-CC132** (developer 2026-10-04, "Refuse, shown
    first": *"The last-card-purchase case is refused on press the same
    way"*): a companion may free no line, and where the line a press would
    free is the envelope's CC payback's -- the last card purchase's X or its
    CC un-tick, a card refund or re-price that brings the card total to
    zero -- the sentence names the PAYBACK, by its envelope's name, never an
    id (ruling **R-CC98**), and says what is refused: THIS change, not every
    change to the envelope's card purchases (a companion may still add a
    card charge, re-price one above zero, or re-describe one).

    Args:
        txn: The envelope whose payback the press would delete.

    Returns:
        The sentence.
    """
    return (
        f"{txn.name}'s card payback is matched to a line on the bank "
        "statement, so only the account owner can make this change."
    )


def x_press_for_payback(
    press: match_press.Press | None,
    txn: Transaction,
    entry: TransactionEntry,
) -> match_press.Press | None:
    """Return a companion's press for an X that also deletes the payback.

    A companion's X carries its door's refusal sentence for the PURCHASE
    (ruling **R-CC132**: *"Kroger is matched to a line on the bank statement,
    so only the account owner can delete it"*), and one removal act takes
    the purchase and its envelope's payback together.  Where the purchase's
    own matches free nothing, a refusal could only be over the PAYBACK's
    line, so :func:`payback_refusal` is the true sentence -- the same save,
    reworded (:meth:`~app.services.match_press.Press.reworded`).  Read inside
    the request's owner lock, so it sees what the act will.  Any other press
    is returned as it is.

    Args:
        press: The X's save.
        txn: The envelope.
        entry: The purchase pressed.

    Returns:
        The press for the one removal act.
    """
    if press is None or not isinstance(press.shown, match_press.OwnerOnly):
        return press
    if match_withdrawal.pending_for_movements([entry]).frees_a_line:
        return press
    return press.reworded(payback_refusal(txn))


def sync_entry_payback(
    transaction_id: int, owner_id: int, *, moves_credit_total: bool = True,
    press: match_press.Press | None = None,
) -> Transaction | None:
    """Synchronize the aggregated CC Payback for a transaction's credit entries.

    Called after every entry mutation (create, update, delete, is_credit
    toggle).  Implements a 2x2 state matrix:

      - total_credit > 0, no payback:  CREATE payback in next period.
      - total_credit > 0, payback exists: UPDATE payback amount.
      - total_credit == 0, payback exists: DELETE payback.
      - total_credit == 0, no payback:  no-op.
      - total_credit < 0:  REFUSED (:func:`_reject_card_owing_the_owner`).

    **The last arm is finding N-411 and it was MISSING rather than wrong.**  The
    matrix was written when ``ck_transaction_entries_positive_amount`` made a
    negative purchase unwritable, so ``> 0`` and ``== 0`` really were total over
    the reachable states.  Ruling **bank_import:R-II** relaxed that CHECK to
    ``amount <> 0``, at which point "everything that is not ``> 0``" silently
    included a negative -- and the else-arm answers it by DELETING the payback.
    What partitions the states is the LIABILITY the card is owed, and it has
    three signs, not two.

    The payback is identified by credit_payback_for_id == transaction_id.
    All credit entries share the same credit_payback_id pointing to this
    payback.

    Args:
        transaction_id: The parent transaction's ID.
        owner_id: The resolved owner user ID (companion -> owner mapping
            already applied by the caller).
        moves_credit_total: Whether the write that triggered this sync can
            CHANGE the envelope's credit-entry sum (finding **N-323**).  Each
            of the three ``entry_service`` doors knows what it touched and
            says so; the default is the safe answer for any other caller.
            It gates the settled-payback refusal below and nothing else --
            the link maintenance and the figure both run either way.
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            the bank lines its page named, for the removal
            act when the sync deletes a payback whose payment a match names
            (plan step ``credit_card:CC-5-4a-5``, ruling **R-CC80**).  The
            edit form's CC un-tick names them (:func:`payback_deleted_by`).
            The X has already taken the payback's movements off in its own
            act (``entry_service._doors.delete_entry``), so for it this
            frees nothing.  A companion's edit and add are
            :class:`~app.services.match_press.OwnerOnly` with
            :func:`payback_refusal`'s sentence (ruling **R-CC132**); its X
            hands this whatever it declared, which frees nothing here.  An
            owner's door that names none passes ``None``, which
            refuses a press that would free a line (ruling **R-CC127**) --
            the add form's card refund, and any edit whose new card total is
            exactly zero (a re-price, or a CC tick on a refund), which no
            static caption can foresee: refused as "out of date" though the
            page was not, and again on every try (finding **CC-381**, filed
            at this step's tick, owned by plan step ``credit_card:CC-7``,
            which deletes this workflow).

    Returns:
        The CC Payback Transaction if one exists after sync, else None.

    Concurrency model (plan step ``balance:X-bn``, ruling **R-CC106**): the
    request's transaction took its owner's write lock before it read any of
    the owner's data (:mod:`app.db_transaction`), so two purchase writes on
    the same parent -- two tabs, or a double click -- run one after the
    other, and the second reads the first's payback instead of both falling
    through the existing-payback check and inserting two.  Until that step
    the parent row's own ``FOR NO KEY UPDATE`` lock did the serialising.
    ``budget.transactions`` carries ``uq_transactions_credit_payback_unique``
    as the database-level backstop for a writer that holds no request: a
    second payback surfaces as an ``IntegrityError`` that the route layer
    converts to idempotent success.  Audit reference: F-008 (High) / commit
    C-19.

    Raises:
        NotFoundError: If the transaction doesn't exist or doesn't
            belong to owner_id.
        ValidationError: If a payback needs to be created but no next
            pay period exists.
        PageOutOfDate: When deleting the payback would free other bank
            lines than *press* names.
    """
    txn = load_owned_transaction(transaction_id, owner_id)

    # Expire the entries relationship so we read fresh data from the
    # database.  Without this, a prior load of txn.entries in the same
    # session could be stale after an entry was added or deleted via
    # FK assignment rather than collection mutation.  The load above
    # hands back the session's own instance and refreshes nothing, so
    # the collection is expired by name.
    db.session.expire(txn, ["entries"])

    # The card total through the one spelling the screen's read shares
    # (:func:`card_total`); the credit entries themselves are linked below.
    _, credit_entries = partition_entries(txn.purchases)
    total_credit = card_total(txn.purchases)

    # Find the live payback (shared definition with credit_workflow;
    # excludes soft-deleted rows so a prior soft-deleted payback is not
    # resurrected and mutated -- a fresh one is created instead).
    existing_payback = get_active_payback(txn.id)

    _reject_card_owing_the_owner(txn, total_credit)

    if total_credit > 0:
        if existing_payback is None:
            return _create_payback(txn, owner_id, credit_entries, total_credit)
        # **REFUSED when the payback has already SETTLED and the new total
        # would change what it recorded** (plan step X-au-c3).  A settled row is
        # worth what it RECORDED, not what its plan says, so the
        # ``estimated_amount`` write below is inert for money once the payback
        # closes -- and the liability the user just added would leave the
        # projection with nothing booking it.  Measured before this guard: a
        # payback settled at ``$100.00``, a later ``$50.00`` card purchase, and
        # ``estimated_amount`` moved to ``$150.00`` while every balance went on
        # reading ``$100.00``.
        #
        # It is the sibling of the rule the developer ruled for the SOURCE row
        # (``entry_service._doors._reject_settled_parent``, 2026-08-17): money
        # that has moved is a record, and a record is changed by reverting the
        # row and settling it again, never by re-deriving it underneath.  The
        # comparison is against what the payback RECORDED rather than against
        # its plan, so a sync that changes nothing still passes.
        #
        # **The POLICY above is right and stays; the PREDICATE was wider than
        # the policy needs, and that is finding N-323.**  It fired whenever the
        # recorded figure ALREADY differed from ``total_credit`` at all -- so a
        # settled payback carrying pre-existing drift refused every later edit
        # on its envelope, including edits that cannot change that sum.
        # Stamping a DEBIT purchase's bank posting day is the case that
        # measured it: it touches no credit entry, so the sum is the same
        # before and after, and it was refused by a guard about credit.  On the
        # developer's own production clone that blocked **5 of 124 statement
        # proposals worth `$706.35`**, plus 6 debit purchases under the two
        # envelopes whose paybacks carry `$59.68` of drift.
        #
        # The question a write should be asked is whether IT moves the total,
        # which is what ``moves_credit_total`` carries.  Drift that already
        # exists is left alone rather than treated as a fresh offence -- it is
        # a finding the amount model reports, not something to punish the next
        # unrelated edit for.
        recorded = settled_figure(existing_payback)
        if moves_credit_total and recorded is not None and recorded != total_credit:
            raise ValidationError(
                f"The payback '{existing_payback.name}' has settled at "
                f"${recorded:,.2f}, so it cannot be re-derived to "
                f"${total_credit:,.2f}: a settled row "
                "records what MOVED. Set the payback back to Projected, then "
                "record this purchase -- the figure it recorded is kept, and "
                "marking it paid again books the new total.",
            )
        # UPDATE: adjust the payback amount and link any new entries.
        previous_amount = existing_payback.estimated_amount
        # **States the payback's OWNERSHIP, not just its figure** (plan step
        # X-au-k).  A payback carries ``credit_payback_for_id``, which
        # ``ck_transactions_one_pricing_link`` makes exclusive with the two
        # links any relation is declared through, so it owns its figure by
        # construction and this cannot un-derive anything today.  Finding
        # **N-437** is the class; plan step X-au-i, which would have given a
        # payback a relation of its own, was WITHDRAWN.
        state_own_amount(existing_payback, total_credit)
        for entry in credit_entries:
            if entry.credit_payback_id != existing_payback.id:
                entry.credit_payback_id = existing_payback.id
        # Clear stale links on entries that are no longer credit
        # (e.g. toggled from credit to debit since the last sync).
        for entry in txn.entries:
            if not entry.is_credit and entry.credit_payback_id == existing_payback.id:
                entry.credit_payback_id = None
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_ENTRY_PAYBACK_UPDATED, BUSINESS,
            "Entry-level payback amount updated",
            user_id=owner_id,
            transaction_id=txn.id,
            payback_id=existing_payback.id,
            previous_amount=str(previous_amount),
            new_amount=str(total_credit),
            credit_entry_count=len(credit_entries),
        )
        return existing_payback

    # ``total_credit == 0``: the card is owed NOTHING, so no payback should
    # stand.  Reached either because no credit purchase remains or because the
    # row's card refunds exactly cancel its card charges -- the second became
    # possible at plan step ``bank_import:X-gj-2b`` and is the same answer, since
    # what decides is the LIABILITY and not how many rows produced it.
    #
    # **This comment used to be the whole of the else-arm's guard, and it stated
    # a condition the code did not implement** (finding **N-411**).  The branch
    # above asks ``> 0`` and this one is everything else, so a NEGATIVE total
    # arrived here and was answered by DELETE: measured on a production clone,
    # an envelope carrying 4 card charges totalling ``$493.03`` lost its payback
    # outright when a larger card refund was recorded, leaving the charges with
    # nothing booking them.  It could not happen while the table forbade a
    # negative purchase; ruling **R-II** is what made the shape writable, and
    # :func:`_reject_card_owing_the_owner` is what now answers it.
    if existing_payback is not None:
        # **A SETTLED payback is not DELETED either, and this refusal is the
        # other half of the one above** (plan step X-au-c3, second pass).  That
        # guard refuses to RE-DERIVE a payback whose money has moved; without
        # this one the same function went on to DESTROY such a payback outright
        # the moment the last credit purchase was removed -- the larger harm
        # performed in silence beside the smaller one refused.
        #
        # Measured: a source row Projected with one ``$100.00`` credit purchase,
        # its payback created and marked Paid (the money really left the
        # account), then that purchase deleted or un-credited on the
        # still-Projected source.  A settled row carrying a ``derived``
        # ``$100.00`` record was hard-deleted and its postings reversed --
        # ``$100.00`` that had moved, erased with no refusal and no trace.
        #
        # The delete branch predates this step.  What makes it a defect NOW is
        # that the payback carries a settlement RECORD for the delete to throw
        # away, and that the developer's 2026-08-17 ruling states the rule it
        # breaks: money that has moved is a record, and a record is undone by
        # reverting the row, never underneath it.  The remedy named here is the
        # one the source row's own refusal names
        # (``entry_service._doors._reject_settled_parent``).  The sentence is
        # :func:`_settled_teardown_refusal`'s, which the screen's read
        # (:func:`payback_deleted_by`) asks too.
        refusal = _settled_teardown_refusal(existing_payback)
        if refusal is not None:
            raise ValidationError(refusal)
        # DELETE: clear entry links before deleting the payback.
        deleted_payback_id = existing_payback.id
        for entry in txn.entries:
            if entry.credit_payback_id == existing_payback.id:
                entry.credit_payback_id = None
        # Its movements off the books through the ONE act (plan step
        # ``credit_card:CC-5-4a-3``, ruling **R-CC54**), which reverses their
        # postings before it deletes them (Build-Order Step 3
        # reverse-before-delete) and re-checks the anchor corrections once they
        # are gone (ruling **plan#2564**; this reversed the payback's family
        # itself, re-checking while its movements stood, until plan task
        # plan#2557).  Idempotent for movements that never posted.  A match
        # naming this payback stops being true when the row goes, so it is
        # withdrawn and its bank line is unexplained again (developer ruling
        # 2026-08-25, plan
        # step ``bank_import:X-gb``) -- a reverted payback keeps its payment
        # un-dated, and an act may still name it.  The CC un-tick says so first
        # since plan step ``credit_card:CC-5-4a-5`` (ruling **R-CC80**, closing
        # finding **CC-367**), and the act compares what it named.  The last
        # card purchase's X has already taken these movements off in ITS act
        # (``entry_service._doors.delete_entry``), so here they are none and
        # this frees nothing.
        movement_removal.remove_movements(
            list(existing_payback.entries), owner_id,
            because=match_withdrawal.LEFT_THE_BOOKS,
            press=press,
            rows_leaving=[existing_payback],
        )
        db.session.delete(existing_payback)
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_ENTRY_PAYBACK_DELETED, BUSINESS,
            "Entry-level payback deleted (no credit entries remain)",
            user_id=owner_id,
            transaction_id=txn.id,
            payback_id=deleted_payback_id,
        )
    return None


def _reject_card_owing_the_owner(
    txn: Transaction, total_credit: Decimal,
) -> None:
    """Refuse an envelope whose card REFUNDS exceed its card purchases.

    Finding **N-411**, opened by ruling **bank_import:R-II**.  A CC Payback is
    an EXPENSE row recording what the owner owes the card, and
    ``ck_transactions_estimated_amount`` (``estimated_amount >= 0``) says so in
    the schema -- so a negative total has nowhere to go.  It is refused HERE,
    before any branch acts on it, rather than left to the arm below: that arm
    reads a non-positive total as "nothing is owed" and DELETES the payback,
    which for a negative total destroys a liability the row's card charges
    still carry.

    **Refusing rather than substituting**, which is the choice this service
    already makes for a settled payback two arms down: the app cannot represent
    a card that owes the OWNER, and inventing a representation for it -- a zero
    payback, a deleted one, an income row -- would each be filing money
    somewhere the model does not mean.  The owner is told what state they have
    described and what to do instead.

    **The credit-card arc may make this representable and this refusal
    removable** (a card with statements of its own is an account, and a credit
    balance on it is an ordinary fact).  Until then a non-representable state is
    refused where it is created, not stored in a shape that reads as something
    else.

    Args:
        txn: The parent envelope, for the message's sake.
        total_credit: The sum of its credit purchases, which this refuses when
            negative.

    Raises:
        ValidationError: When the card refunds exceed the card purchases.
    """
    if total_credit >= 0:
        return
    raise ValidationError(
        f"The card refunds on '{txn.name}' now exceed its card purchases by "
        f"${-total_credit:,.2f}, which would mean the card owes YOU rather than the "
        "other way round. Shekel records a CC Payback as money you owe, so it "
        "cannot hold that. Record the refund against the envelope it came from "
        "as an ordinary (non-card) purchase, or split it across the card "
        "purchases it actually reverses."
    )


def _create_payback(
    txn: Transaction,
    owner_id: int,
    credit_entries: list[TransactionEntry],
    total_credit: Decimal,
) -> Transaction:
    """Create a new CC Payback transaction in the next pay period.

    Sets every field identically to credit_workflow.mark_as_credit:
    account_id, template_id (None), pay_period_id (next period),
    scenario_id, status_id (PROJECTED), name, category_id (CC Payback),
    transaction_type_id (EXPENSE), estimated_amount, and
    credit_payback_for_id.

    Args:
        txn: The parent transaction.
        owner_id: The resolved owner user ID.
        credit_entries: Credit entries to link to the new payback.
        total_credit: Sum of credit entry amounts.

    Returns:
        The newly created payback Transaction (flushed, id available).

    Raises:
        ValidationError: If no next pay period exists.
    """
    # **The payday this counts FROM comes out of the same calendar** since
    # plan step ``pay_calendar:C13-b`` -- the transaction-level twin
    # (``credit_workflow.mark_as_credit``) carries the same argument.
    # It was ``txn.pay_period.start_date``, read off a relationship the entry
    # doors hydrated while walking it for their ownership check; those doors
    # read ``entry.transaction.user_id`` now, so the walk would have become a
    # lazy load for a span this derivation already holds.
    #
    # **The READ ORDER, stated because ``require_period`` requires every caller
    # to state its own**: the ROW is read first, the paydays second, so a
    # concurrent DESTRUCTIVE pay-period door -- reset, regenerate or truncate
    # -- committing between them raises rather than answering off a stale
    # picture.  That is balance finding **N-358**, whose remedy is
    # `balance:X-i5`.
    calendar = calendar_for(owner_id)
    next_period = calendar.period_starting_after(
        calendar.require_period(FiledRow.for_row(txn)).start_date,
    )
    if next_period is None:
        raise ValidationError(
            "No next pay period exists. Generate more periods first."
        )

    cc_category = get_or_create_cc_category(owner_id)

    # Shared factory; see credit_workflow for the transaction-level twin.
    payback = create_cc_payback_transaction(
        txn, next_period, cc_category, total_credit,
    )

    # Link all credit entries to the new payback.
    for entry in credit_entries:
        entry.credit_payback_id = payback.id
    db.session.flush()

    log_event(
        logger, logging.INFO, EVT_ENTRY_PAYBACK_CREATED, BUSINESS,
        "Entry-level payback created from credit entries",
        user_id=owner_id,
        transaction_id=txn.id,
        payback_id=payback.id,
        next_period_id=next_period.period_id,
        amount=str(total_credit),
        credit_entry_count=len(credit_entries),
    )
    return payback
