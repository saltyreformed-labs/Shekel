"""The MOVEMENT posting sources: a row's purchases and covering movements as cash.

Ruling **R-FM** (plan step X-f3b), whose whole content is one sentence: *a
purchase that has cleared the bank is a cash posting, and its envelope's close
books only what its purchases did not*.  This module is that source's write
half -- what makes a movement postable, what its two legs are, and the
reconcile-to-target emission for one of them.  Since plan step
``balance:X-bi-3b`` a bill's or a paycheck's covering movement posts through
it too (a movement's legs are its parent's, ruling **R-BAL35**), and since
``balance:X-bi-6-3`` so does a transfer's (ruling **R-BAL101**): ONE
movement writer, the COUNTER LEG dispatched by the parent's shape.  A row's
movement books against the row's category under the ``purchase`` source; a
transfer LEG's books against the owner's Transfers-in-transit account under
the ``transfer_movement`` source with the ``transfer`` leg kind (ruling
**R-BAL45**'s shape C: two entries per settled transfer, each side on its own
bank day, the transit account netting to zero once both have cleared).  The
parent is what :func:`app.services.transfer_legs.movement_parent` resolves
(leaf ``X-bi-6-4a``): a plan row, or a transfer's leg, never the shadow row
a transfer movement still hangs off.

**Why a module of its own rather than more of :mod:`app.services.posting_service`.**
The purchase was a THIRD posting source beside the transfer and the transaction,
with its own concrete linkage (``journal_entries.transaction_entry_id``), its
own source kind (``ref.posting_sources`` ``purchase``), its own target rule and
its own day; the transfer movement shares the linkage, the rule and the day and
differs in the counter leg, the source kind and the leg kind alone.  Adding
the purchase inline took the writer module 319 lines past pylint's 1,000-line
ceiling; growing past a gate is a signal, and the seam the ceiling was
measuring is exactly this one.  The split follows the sibling-split
convention ``posting_reads`` was created by.

**It holds no public door, deliberately.**  The two doors a caller reaches --
``posting_service.sync_transaction_postings`` (the family reconcile, and since
plan step ``balance:X-bi-4a`` the whole of what an ordinary transaction posts)
and ``posting_service.reverse_purchase_postings_before_delete`` -- stay in the
writer module, because both must run the account anchor self-heal that module
owns, and because ``posting_service`` remains the ledger's ONE public surface.
What lives here is what those doors are made of.  (A per-purchase door,
``sync_purchase_postings``, stood beside them with zero callers until that
step deleted it -- ledger row **BAL-507**.)

**The dependency runs one way**: this module imports the balanced-write leaf
(:mod:`app.services._posting_write`) and the chart resolvers, and never
``posting_service`` itself -- the same direction, and for the same cycle
reason, that :mod:`app.services._posting_reconcile` keeps.

Flask-isolated and commit-free like its consumers: flushes so the caller sees
assigned ids; the caller owns the transaction boundary.
"""

from datetime import date
from decimal import Decimal

from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import ledger_account_service
from app.services._posting_write import (
    emit_typed_source_deltas,
    is_transfer_leg,
    ledger_class_of,
)
from app.services.cash_ledger import movement_cash_leg
from app.services.posting_reads import _ledger_account_for
from app.services.transfer_legs import PlanItem
from app.utils.balance_predicates import is_balance_contributing


def posted_purchase_exists_clause():
    """Return the SQL form of "this transaction holds a posted purchase".

    :func:`purchase_posts` asks the question of ONE loaded row; this asks it of
    a query, as a correlated ``EXISTS`` over ``budget.transaction_entries``.  It
    is the same two narrowings that predicate makes about the purchase itself --
    a recorded posting day, and a DEBIT -- and deliberately not the third: what
    the PARENT contributes is the caller's own filter, because the two callers
    want opposite answers.  ``resync_all_cash_postings`` wants every row that
    could hold a leg; a bulk archive wants the rows it is about to stop
    contributing.

    **It exists because the clause was written twice** (caught by pylint's
    ``duplicate-code`` at plan step X-f3b, which is what that checker is for):
    once in the deploy resync's WHERE and once in the template routes' bulk
    guard.  Two spellings of "can this row hold postings" is the shape ruling
    R-FM's whole family of fixes exists to prevent, one tier up.

    Returns:
        A SQLAlchemy ``EXISTS`` clause, correlated to ``Transaction``, for use
        in any query rooted there.
    """
    return (
        db.session.query(TransactionEntry.id)
        .filter(
            TransactionEntry.transaction_id == Transaction.id,
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .exists()
    )


def purchase_posts(parent: PlanItem, entry) -> bool:
    """Return whether *entry* books a cash leg of its own -- ruling **R-FM**.

    The ONE statement of "is this purchase in the ledger" on the write side,
    and the twin of the read side's own three narrowings
    (:func:`app.services.cash_ledger._events.settled_cash_facts`).  All
    three are load-bearing and each is stated there in full:

    * the PARENT contributes to a balance at all (not soft-deleted, not Credit
      or Cancelled) -- :func:`~app.services.cash_ledger.movement_cash_leg`'s
      totality rule, the family's since ruling **R-FM**;
    * the purchase is a DEBIT -- a card purchase leaves through its own CC
      Payback sibling and never touches this account;
    * its bank posting day is RECORDED -- the trigger itself.

    It reads no status of the parent beyond the contributing gate, deliberately:
    a purchase against a still-PROJECTED envelope has left the bank exactly as
    one against a closed envelope has.  The same three narrowings decide a
    transfer's covering movement (plan step ``balance:X-bi-6-3``, ruling
    **R-BAL101**), which is what let ``sync_transfer_postings`` drop the
    ``settled`` flag it used to be told: a settle dates the movement and a
    revert un-dates it (ruling **R-BAL61**), so the movement's own state says
    whether its leg is in the ledger.

    **A transfer LEG posts its RECORD alone** (leaf ``X-bi-6-4a``): its gate
    is the TRANSFER's, read through the leg, so the fold's leg arm
    (``settled_cash_facts``) and this answer from one parent.  Through the
    interval a movement under a DEAD shadow of its transfer is no leg's
    record (:func:`app.services.transfer_legs.movement_parent` gives its leg
    none), so it posts nothing and any leg it holds reverses: the answer the
    dead shadow's own gate gave while the writer read the shadow, and the
    fold's.  No door writes that state (0 on the 2026-09-22 17:06 dump); the
    term answers ``True`` for every movement from ``X-bi-6-4d``, where each
    movement a side links is that side's record, and goes then.

    Args:
        parent: The movement's parent, as
            :func:`~app.services.transfer_legs.movement_parent` resolves it
            FOR *entry*: a plan row, or a transfer leg carrying *entry* as its
            record when it is one.  A leg built without the movement
            (``transfer_legs.leg_of``, ``planned_transfer_legs``) carries no
            record, and this answers ``False`` for it -- a dated leg would
            silently reverse -- so every door resolves the parent through
            ``movement_parent`` and none hands the writer a leg of its own.
        entry: The movement.

    Returns:
        True when the ledger should hold a cash leg for *entry*.
    """
    return (
        is_balance_contributing(parent)
        and not entry.is_credit
        and entry.settled_on is not None
        and (not is_transfer_leg(parent) or parent.record is entry)
    )


def _purchase_target(entry, parent: PlanItem) -> dict[int, Decimal]:
    """Return the debit-positive ledger target for a POSTED movement.

    The ONE ledger target a row's family has (plan step
    ``balance:X-bi-4a``, ruling **R-BAL80**: a plan row books nothing of its
    own, so the row-level ``_settled_target`` this was the analog of is
    gone): ``{cash_ledger_id: leg, counter_ledger_id: -leg}``, summing to
    zero by construction, where
    ``leg`` is :func:`app.services.cash_ledger.movement_cash_leg` -- the
    movement's whole figure in its PARENT's direction (ruling **R-BAL35**).
    A purchase against an envelope books ``{cash: -amount, category:
    +amount}`` exactly as it did; a paycheck's covering movement books
    ``{cash: +amount, category: -amount}`` into an INCOME-class counter
    account.  The direction was spelled here as ``-amount`` until that step,
    so an income parent could not be covered before it.

    **The counter leg is dispatched by the PARENT's shape, and that is the
    whole of what a transfer adds** (plan step ``balance:X-bi-6-3``, rulings
    **R-BAL45** and **R-BAL101**).  A transfer LEG's covering movement
    books its cash leg on the movement's account -- ``-figure`` on the
    from-side, ``+figure`` on the to-side, the side the leg's (leaf
    ``X-bi-6-4a``; the shadow's type through ``X-bi-6-3``), through the same
    producer -- against the owner's Transfers-in-transit
    account rather than a category: a transfer between two of the owner's
    accounts is neither income nor expense, which is why the purchase source
    with its category counter was REJECTED for it at R-BAL45.  Worked on the
    production restore's transfer 53 (``$1,910.95`` Checking -> Mortgage,
    both movements 2026-04-01): ``{Checking -1,910.95, Transit +1,910.95}``
    and ``{Mortgage +1,910.95, Transit -1,910.95}``, the transit account
    netting to zero once both sides have cleared and each side free to post
    on its own bank day when the days part (the mirror ``X-bi-6-4`` deletes).

    **There is no sign branch HERE, and that is what makes a REFUND work**
    (ruling **bank_import:R-II**).  The rule is arithmetic rather than a case
    analysis over the figure, so a NEGATIVE purchase passes through it
    correctly: at ``-28.29`` under an expense it emits ``{cash: +28.29,
    category: -28.29}``, money coming back and a contra-expense, which is
    exactly what a merchant credit is.  Measured end-to-end on a production
    clone before the constraint moved, against a ``+28.29`` control that
    produced the mirror image.

    **The counter leg is the PARENT's own category, in the parent's class**
    (ruling **R-FM**, developer 2026-08-15; the class since X-bi-3b by
    :func:`~app.services._posting_write.ledger_class_of`).  A movement carries
    no category of its own, and the money it records is its parent's: booking
    it there recognises the expense or income in the right category on the
    day it happens.  Through ``X-bi-3e`` the parent's close then booked the
    remainder to the SAME account, so the two summed to the row's whole
    figure; since plan step ``balance:X-bi-4a`` the close books nothing, and
    a purchase not yet dated is in flight until it is (ruling **R-BAL77**).
    Rejected: booking it to Uncategorized until the close, which shows an
    open envelope's real spend as uncategorised on the income statement and
    makes every close write a reclassification pair.

    A re-category of the parent is therefore a re-category of its purchases, and
    it reconciles by the same mechanism the parent's own leg uses: the sync
    reads the OLD legs back from the ledger and reverses them
    (``routes/transactions/mutations`` lists ``category_id`` among the fields
    that raise a reconcile, on a Projected row as well as a settled one).

    Args:
        entry: The purchase.  Its ``account_id`` is the account its money
            moved THROUGH -- its own, and free to differ from its parent's
            since plan step ``credit_card:CC-5-1`` (ruling **R-BAL75**: the
            ledger, the fold and the self-heal all read the movement's) --
            so the cash account is read straight off it.
        parent: Its parent -- a plan row, or a transfer leg -- taken as an
            ARGUMENT rather than resolved here, so the caller that already
            holds it -- every caller does, through
            :func:`app.services.transfer_legs.movement_parent` -- pays no lazy
            load, and so every movement of one family is provably booked to
            one category.  Its ``user_id`` is the counter account's owner,
            the ONE home ``pay_calendar:C13-b`` gave a row's owner (a leg's is
            its transfer's): a parameter every caller binds from that home
            would be a second home one hop away.

    Returns:
        ``{cash_ledger_id: leg, category_ledger_id: -leg}``.

    Raises:
        PostingError: If the movement's account has no linked ledger account.
        ValueError: Propagated from the resolver if the parent's non-NULL
            ``category_id`` names no category owned by ``parent.user_id``.
    """
    cash_ledger = _ledger_account_for(entry.account_id)
    if is_transfer_leg(parent):
        counter_ledger = (
            ledger_account_service.get_or_create_transit_ledger_account(
                parent.user_id,
            )
        )
    else:
        counter_ledger = ledger_account_service.get_or_create_category_ledger_account(
            parent.user_id, parent.category_id, ledger_class_of(parent),
        )
    leg = movement_cash_leg(parent, entry)
    return {cash_ledger.id: leg, counter_ledger.id: -leg}


def emit_purchase_deltas(
    entry, parent: PlanItem, *, posted: bool,
) -> "list[JournalEntry]":
    """Emit the delta entries for ONE purchase's own cash leg -- ruling **R-FM**.

    The purchase analog of the row's own-leg reconcile
    (``posting_service._emit_transaction_deltas``, which plan#1845 deleted with
    the source), and deliberately the SAME shape: reconcile-to-target over the
    ``(pay period, entry date)``
    keys the purchase has already posted to, unioned with the one it should
    hold.  That is what makes every lifecycle act on a purchase one call -- a
    posting day recorded, corrected, or cleared; a parent cancelled or
    soft-deleted; an amount edited; a credit flag flipped -- and a repeat sync a
    no-op.  A ``settled_on`` MOVE reconciles as two keys, the old date reversing
    to zero and the new one posting fresh, exactly as finding **N-13**'s
    transaction twin does.

    **The PERIOD is the parent's and the DATE is the purchase's**, which is the
    same two-clock split every cash source keeps: the budget column a purchase
    spends is its envelope's, and the day its money moved is its own.  A
    transfer leg's movement keeps both: the leg's period is its transfer's
    (read off the TRANSFER since leaf ``X-bi-6-4a``, the shadow's through
    ``X-bi-6-3``, equal under Transfer Invariant 3), and the day is the
    side's own bank day (ruling **R-BAL45**).

    Args:
        entry: The movement.
        parent: Its parent as
            :func:`app.services.transfer_legs.movement_parent` resolves it: a
            plan row, or a transfer leg.
        posted: Whether the ledger should hold a cash leg for it
            (:func:`purchase_posts`; ``False`` also means "reverse it", which
            is what the teardown doors pass).

    Returns:
        The emitted delta entries; ``[]`` when the ledger is already at target.
    """
    targets: "dict[tuple[int, date], dict[int, Decimal]]" = {}
    if posted:
        targets[(parent.pay_period_id, entry.settled_on)] = _purchase_target(
            entry, parent,
        )
    # The PARENT types the legs (plan step X-bi-3b): ``expense`` under an
    # envelope or a bill, ``income`` under a paycheck, ``transfer`` under a
    # transfer leg -- and names the SOURCE the header carries and the
    # reconcile filters on (plan step ``balance:X-bi-6-3``):
    # ``transfer_movement`` for a leg's movement, ``purchase`` for every other.
    source = (
        PostingSourceEnum.TRANSFER_MOVEMENT if is_transfer_leg(parent)
        else PostingSourceEnum.PURCHASE
    )
    return emit_typed_source_deltas(
        parent,
        entry,
        targets=targets,
        source=source,
        log_label=f"{source.value} {entry.id} (posted={posted})",
    )
