"""A movement's place in the ledger: whether it books, on what, and as what.

**The movement rule, stated once for the worked-out read** (plan task
plan#2526).  Every dated movement -- a purchase
the bank was seen to take, a bill's or a paycheck's covering movement, a
transfer side's record -- books ONE balanced pair: its cash leg on the
account its money moved through, and its counter leg on its PARENT's category
line (in the parent's class) or, for a transfer leg, on the owner's Transfers
in transit (rulings **R-FM**, **R-BAL35**, **R-BAL45**, **R-BAL101**).  This
module is that rule:

* :func:`purchase_posts` -- whether the movement books at all (its parent
  contributes, it is not a card purchase, its bank day is recorded);
* :func:`purchase_target` -- its two legs, keyed by
  :class:`~._chart.ChartKey`;
* :func:`purchase_source` and :func:`posting_kind_of` -- the source kind and
  the leg kind its parent's shape decides, through :func:`is_transfer_leg`.

The rule lived in the stored ledger's writer's leaves (``_posting_purchases``
and ``_posting_write``) until plan task plan#2526 moved it here, so the read
summed the very legs the writer posted, and the rule outlived the writer when
plan task plan#3133 deleted it with what was the store's own: resolving each
key to a stored row, reading what it had posted, emitting the difference.

Reads the movement's account by its column (an identity-map hit for every
caller today) and writes nothing.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.services.cash_ledger import movement_cash_leg
from app.services.transfer_legs import PlanItem, TransferLeg
from app.utils.balance_predicates import is_balance_contributing

from ._chart import (
    ChartKey,
    category_key,
    ledger_class_of,
    linked_key,
    transit_key,
)


def is_transfer_leg(parent) -> bool:
    """Return whether *parent* is one leg of a transfer.

    The ONE spelling of the ledger's shape dispatch (plan step
    ``balance:X-bi-6-3``, ruling **R-BAL101**): a transfer's movement books
    against the owner's transit account under the ``transfer_movement``
    source with the ``transfer`` leg kind, and the sites that decide that --
    the counter leg, the source kind, the leg kind, whether it posts -- ask
    this rather than each testing the shape for themselves (the leaf's
    adversarial review counted three spellings).

    **It asks what the parent IS, not a shadow's column** (leaf
    ``X-bi-6-4a``, ruling **R-BAL106**): every door handed the stored
    ledger's writer the parent
    :func:`app.services.transfer_legs.movement_parent` resolved, a plan row
    or a :class:`~app.services.transfer_legs.TransferLeg`, so no door of that
    writer reached a movement's parent through ``transaction.transfer_id``.
    One reader of the column remained beside it: the deploy resync's row
    selector kept shadow rows out of its ROW arm
    (the fold's ``_movements_of`` twin) until plan step ``X-bi-6-5`` deleted
    that exclusion and then the column.
    The loan ledger's stale-movement probe (``loan_posting_service._sync``)
    was a second until leaf ``X-bi-6-4b``, which gave it
    :func:`~app.services.transfer_legs.movement_parent`, and spelled that
    function's shadow chain as its own loader options until leaf
    ``X-bi-6-4d-1`` (ledger row **BAL-579**).  Through ``X-bi-6-3`` this was
    ``txn.transfer_id is not None`` over the shadow row itself.

    Args:
        parent: A movement's parent: a plan row or a transfer leg.

    Returns:
        ``True`` for a transfer leg.
    """
    return isinstance(parent, TransferLeg)


def posting_kind_of(parent) -> int:
    """Return the ``ref.posting_kinds`` id every leg of *parent*'s money carries.

    Both legs of an ordinary-transaction entry carry the same kind, by the
    transaction type; no Step-3 reader differentiates per-leg kind.  A
    MOVEMENT's legs carry its PARENT's kind (plan step ``balance:X-bi-3b``): a
    purchase against an envelope is an ``expense`` posting and a paycheck's
    covering movement an ``income`` one, for the one reason
    :func:`~._chart.ledger_class_of` gives -- and a transfer
    leg's covering movement
    a ``transfer`` one (plan step ``balance:X-bi-6-3``, ruling **R-BAL101**),
    because a transfer between the owner's own accounts is neither income nor
    expense, which is why its counter leg is the transit account and not a
    category (ruling **R-BAL45**).  The kind the one-entry transfer shape
    carried is the kind its two per-movement entries carry.

    Args:
        parent: An income or expense transaction, or a transfer leg
            (:func:`is_transfer_leg`).

    Returns:
        The stored id of :attr:`PostingKindEnum.TRANSFER` for a leg's
        money, else of :attr:`PostingKindEnum.INCOME` or ``.EXPENSE``.
    """
    if is_transfer_leg(parent):
        return ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
    return ref_cache.posting_kind_id(
        PostingKindEnum.INCOME if parent.is_income else PostingKindEnum.EXPENSE
    )


def purchase_source(parent: PlanItem) -> PostingSourceEnum:
    """Return the source kind a movement's legs are booked under, by its PARENT.

    ``transfer_movement`` for a transfer LEG's covering movement and
    ``purchase`` for every other (a purchase against an envelope, a bill's or
    a paycheck's covering movement) -- plan step ``balance:X-bi-6-3``, ruling
    **R-BAL101**.  The worked-out read names each line's source by it, as the
    stored ledger's writer stamped it on each entry header and filtered its
    posted side by it until plan task plan#3133 deleted that writer.  It was spelled inline in
    ``_posting_purchases.emit_purchase_deltas`` until plan task plan#2526 gave
    the read the same answer from the same place.

    Args:
        parent: The movement's parent as
            :func:`app.services.transfer_legs.movement_parent` resolves it: a
            plan row, or a transfer leg.

    Returns:
        :attr:`PostingSourceEnum.TRANSFER_MOVEMENT` or ``.PURCHASE``.
    """
    if is_transfer_leg(parent):
        return PostingSourceEnum.TRANSFER_MOVEMENT
    return PostingSourceEnum.PURCHASE


def purchase_posts(parent: PlanItem, entry) -> bool:
    """Return whether *entry* books a cash leg of its own -- ruling **R-FM**.

    The ONE statement of "is this purchase in the ledger", read by the
    worked-out read (it was the stored ledger's writer's,
    ``_posting_purchases.purchase_posts``, until plan task plan#2526 moved it
    here, and the writer read it here until plan task plan#3133 deleted
    it), and the twin of the cash fold's own three narrowings
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

    **A transfer LEG posts its record by the same three narrowings** (leaf
    ``X-bi-6-4a``): its gate is the TRANSFER's, read through the leg, so the
    fold's leg arm (``settled_cash_facts``) and this answer from one parent.
    A fourth term asked whether *entry* was the leg's record, for a movement
    under a DEAD shadow of its transfer (no leg's record, so it posted
    nothing); every movement a side links is that side's record since plan
    step ``balance:X-bi-6-4d-2`` (a side holds at most one, and a record is
    its side's link), so the term answered ``True`` for every movement a door
    hands here and was deleted when the leg's record stopped being a field
    (ruling **plan#1984**, finding **plan#2013**).

    Args:
        parent: The movement's parent, as
            :func:`~app.services.transfer_legs.movement_parent` resolves it
            FOR *entry*: a plan row, or the transfer leg on *entry*'s side.
        entry: The movement.

    Returns:
        True when the ledger should hold a cash leg for *entry*.
    """
    return (
        is_balance_contributing(parent)
        and not entry.is_credit
        and entry.settled_on is not None
    )


def purchase_target(entry, parent: PlanItem) -> dict[ChartKey, Decimal]:
    """Return the debit-positive ledger target for a POSTED movement.

    The ONE ledger target a row's family has (plan step
    ``balance:X-bi-4a``, ruling **R-BAL80**: a plan row books nothing of its
    own, so the row-level ``_settled_target`` this was the analog of is
    gone): ``{cash: leg, counter: -leg}``, summing to zero by construction,
    where
    ``leg`` is :func:`app.services.cash_ledger.movement_cash_leg` -- the
    movement's whole figure in its PARENT's direction (ruling **R-BAL35**).

    **Keyed by chart key, and it touches no stored chart row** (ruling
    plan#2518, plan task plan#2524): the cash leg names the movement's
    account's own line (:func:`~._chart.linked_key`), the
    counter leg the parent's category line or the owner's transit bucket.  It
    found the cash row and got or created the counter row as it went until
    that task; the stored ledger's writer resolved the keys at its emit step
    after it (``_posting_write.resolve_chart_targets``, until plan task
    plan#3133 deleted it), so the worked-out ledger reads this very rule and
    creates nothing.  It was the
    writer's private ``_posting_purchases._purchase_target`` until plan task
    plan#2526 moved it here, beside the read that sums it.
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
    :func:`~._chart.ledger_class_of`).  A movement carries
    no category of its own, and the money it records is its parent's: booking
    it there recognises the expense or income in the right category on the
    day it happens.  Through ``X-bi-3e`` the parent's close then booked the
    remainder to the SAME account, so the two summed to the row's whole
    figure; since plan step ``balance:X-bi-4a`` the close books nothing, and
    a purchase not yet dated is in flight until it is (ruling **R-BAL77**).
    Rejected: booking it to Uncategorized until the close, which shows an
    open envelope's real spend as uncategorised on the income statement and
    makes every close write a reclassification pair.

    A re-category of the parent is therefore a re-category of its purchases:
    each purchase's counter leg is worked out from the parent's category as
    it stands, so its lines move with the parent and nothing is reversed.
    The stored ledger's sync read the OLD legs back and reversed them until
    plan task plan#3097 deleted the transaction doors' syncs.

    Args:
        entry: The purchase.  Its ``account_id`` is the account its money
            moved THROUGH -- its own, and free to differ from its parent's
            since plan step ``credit_card:CC-5-1`` (ruling **R-BAL75**: the
            ledger, the fold and the self-heal all read the movement's) --
            so the cash account is read straight off it: by the COLUMN,
            never the ``account`` relationship, because the seam re-points a
            covering movement by its column alone
            (``status_seam._covering``), and a relationship loaded before
            that would still name the old account.  The account is almost
            always in the session already (a plan row and a transfer each
            hold theirs, joined), so the read is an identity-map hit.
        parent: Its parent -- a plan row, or a transfer leg -- taken as an
            ARGUMENT rather than resolved here, so the caller that already
            holds it -- every caller does, through
            :func:`app.services.transfer_legs.movement_parent` -- pays no lazy
            load, and so every movement of one family is provably booked to
            one category.  Its ``user_id`` is the counter account's owner,
            the ONE home ``pay_calendar:C13-b`` gave a row's owner (a leg's is
            its transfer's), which the stored ledger's writer read when it
            resolved the keys (``_posting_write.emit_typed_source_deltas``,
            until plan task plan#3133 deleted it).

    Returns:
        ``{cash key: leg, counter key: -leg}``.
    """
    if is_transfer_leg(parent):
        counter = transit_key()
    else:
        counter = category_key(parent.category_id, ledger_class_of(parent))
    leg = movement_cash_leg(parent, entry)
    return {
        linked_key(db.session.get(Account, entry.account_id)): leg,
        counter: -leg,
    }
