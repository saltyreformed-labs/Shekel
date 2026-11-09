"""The OLD Credit shapes, planted: a Credit row, and a purchase carrying the CC flag.

**The row**: status Credit and a live CC Payback next paycheck.

What ``credit_workflow.mark_as_credit`` wrote until plan task plan#2929, which
makes a Credit press put the row on the card instead (rulings plan#2887,
plan#2888) and writes no payback.  The rows already holding a payback keep the
old path until plan#1034's re-plan -- Undo CC deletes the payback, deleting the
source takes its chain down, a matched payback's line is freed -- and production
holds 21 of them (the 2026-11-04 dump), so the tests of that path keep grading
it.  No door writes the shape any more, so they plant it here, by the same
steps in the same order the deleted writer took, and every assertion after the
plant is the one it graded before.

**Its first step writes the status column itself** since plan task plan#3057
took the state machine's edge into Credit away (finding plan#2133), so the
seam refuses the move the deleted writer made.  The column and the ``status``
expiry are what the seam did on that move: Projected to Credit settles
nothing, so it wrote no day and no record.

**The purchase**: a purchase on its row's account carrying ``is_credit``, the
envelope's CC Payback next paycheck summing every such purchase.  What the add
door wrote for a CC tick until plan task plan#2930, which files a ticked
purchase ON the card instead (ruling plan#2888) and writes no flag.  Production
holds 26 such lines on 17 envelopes (the 2026-11-04 dump), and they keep the
flag and its payback arms until plan#1034's re-plan, so the tests of those arms
plant the line here (:func:`plant_old_flagged_purchase`).
"""

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, posting_service
from app.services.settle_day import record_settle_day
from app.services.cash_ledger import derived_amount_basis, resolve_transaction_amount
from app.services.entry_credit_workflow import sync_entry_payback
from app.services.credit_workflow import (
    create_cc_payback_transaction,
    get_active_payback,
    get_or_create_cc_category,
)
from app.services.pay_calendar import FiledRow, calendar_for
from app.utils.balance_predicates import is_credit, is_projected


def plant_old_credit(txn: Transaction) -> Transaction:
    """Put *txn* in the old Credit shape and return its live payback.

    The deleted writer's acts, in its order: the status to Credit (the
    column, as the seam wrote it), the user's CC Payback category, the
    paycheck after the row's off the owner's calendar, the payback at the
    row's resolved figure, and the source's postings reconciled.  A row
    already in that shape returns its payback, as the writer's idempotency
    did.

    Args:
        txn: A Projected expense row whose purchases are not tracked.

    Returns:
        The payback :class:`~app.models.transaction.Transaction`, flushed.

    Raises:
        AssertionError: The row has no paycheck after its own, or is not
            Projected, each of which the deleted writer refused (the second
            through the seam's state machine); a test that needs that
            refusal has no old shape to plant.
    """
    if txn.status_id == ref_cache.status_id(StatusEnum.CREDIT):
        existing = get_active_payback(txn.id)
        if existing is not None:
            return existing
    if not is_credit(txn):
        assert is_projected(txn), "the old Credit was pressed on a Projected row"
        txn.status_id = ref_cache.status_id(StatusEnum.CREDIT)
        db.session.flush()
        db.session.expire(txn, ["status"])
    category = get_or_create_cc_category(txn.user_id)
    calendar = calendar_for(txn.user_id)
    next_period = calendar.period_starting_after(
        calendar.require_period(FiledRow.for_row(txn)).start_date,
    )
    assert next_period is not None, "the old Credit needed a next paycheck"
    amount = resolve_transaction_amount(
        txn, derived_amount_basis(txn.account.user_id, txn.scenario_id),
    )
    payback = create_cc_payback_transaction(txn, next_period, category, amount)
    posting_service.sync_transaction_postings(txn)
    return payback


def plant_old_flagged_purchase(
    transaction_id: int, user_id: int, details: "entry_service.EntryDetails",
) -> TransactionEntry:
    """Record the purchase *details* says under the row, carrying the old CC flag.

    The deleted writer's acts, in its order: the purchase written on its row's
    account carrying the flag (the row's owner its owner, *user_id* its
    author, the figure and its source, the posting day pair through its one
    writer), flushed; the envelope's CC Payback synced (the sync the 26 lines
    still have, which creates, re-prices or refuses it as it did); and the
    family's postings reconciled ONCE, with the flag already set, so a posted
    line books no checking leg (the review of plan#2930, L2: written through
    today's door and flagged after, it booked one and then reversed it).  The
    door's refusals are not asked: what is planted is a shape the old door
    admitted.  A tick on the card is not this shape, so *details* names no
    card and no other account.

    Args:
        transaction_id: The envelope's id.
        user_id: The recording user (the owner, or a companion).
        details: The purchase, its ``on_card`` unset.

    Returns:
        The purchase, flushed.
    """
    assert not details.on_card and details.account_id is None, (
        "an old flagged line sat on its row's account, named by no tick"
    )
    txn = db.session.get(Transaction, transaction_id)
    entry = TransactionEntry(
        transaction_id=txn.id,
        account_id=txn.account_id,
        owner_id=txn.user_id,
        user_id=user_id,
        amount=details.figure.amount,
        description=details.description,
        purchased_on=details.purchased_on,
        is_credit=True,
        figure_source_id=ref_cache.movement_figure_source_id(details.figure.source),
    )
    record_settle_day(entry, details.settle_day)
    db.session.add(entry)
    db.session.flush()
    db.session.expire(txn, ["entries"])
    sync_entry_payback(txn.id, txn.user_id)
    posting_service.sync_transaction_postings(txn)
    return entry
