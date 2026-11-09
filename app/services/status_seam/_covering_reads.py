"""
Shekel Budget App -- Status seam: what READERS ask of the covering movement.

The read half of :mod:`._covering`, which writes a row's covering movement
(plan step **balance:X-bi-3a**, rulings **R-BAL39** and **R-BAL41**): the
movement asked of a QUERY -- the seam's mark (:func:`covering_clause`), a
row holding no purchase (:func:`holds_no_purchase_clause`), a payment kept
on another account (:func:`payment_recorded_elsewhere_clause`) -- and what a
settled row is WORTH on an account (:func:`covered_cash_leg`).  Moved out of
``_covering`` unchanged at plan task plan#3055, when that module neared
the 1,000-line ceiling: the writer is one responsibility and these four
readers, which write nothing, are another.

Pure: builds SQL expressions and sums a loaded row's movements.  No session,
no mutation, no Flask.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import and_

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services.cash_ledger import movement_cash_leg

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.transfer_legs import PlanItem


def covering_clause():
    """Return the SQL form of *this purchase is a covering movement*.

    :attr:`~app.models.transaction.Transaction.covering_movements` asks the
    question of ONE loaded row; this asks it of a query over
    ``TransactionEntry``, for a reader that must leave the seam's mirrors OUT
    of a row set -- the statement matcher's purchase candidates and the
    reconcile panel's outstanding purchases, which offer a person's purchases
    and never the row's own payment record.

    Returns:
        A SQLAlchemy boolean expression over ``TransactionEntry``.
    """
    return TransactionEntry.covers_settlement.is_(True)


def holds_no_purchase_clause():
    """Return the SQL form of *this row holds no purchase*.

    :attr:`~app.models.transaction.Transaction.purchases` empty, asked of a
    query over ``Transaction``: no entry under the row lacks the seam's mark
    (:func:`covering_clause`).  It is the settle verb's own branch predicate
    (``transaction_service.settles_from_entries``: a row holding purchases
    settles FROM them) in SQL, for the screens that must cut on it in a
    query -- the reconcile panel's "Paid from this account" list (ruling
    **R-CC113**) and :func:`payment_recorded_elsewhere_clause`.  It lived in
    ``reconcile_service._transactions`` as ``_holds_no_purchase`` until plan
    step ``credit_card:CC-5-4a-5`` (leaf 5c-2a), when the statement matcher
    needed the same cut (ruling **R-CC137**) and a private helper of one
    package could not be its one home.

    Returns:
        A SQLAlchemy boolean expression over ``Transaction``.
    """
    return ~Transaction.entries.any(~covering_clause())


def payment_recorded_elsewhere_clause(account_id: int):
    """Return the SQL form of *this row's payment is recorded on another account*.

    A row holding no purchase (:func:`holds_no_purchase_clause`) whose
    covering movement -- its payment, dated or kept un-dated by a revert --
    sits on an account other than *account_id*: a bill planned on Checking,
    marked paid from the card and reopened.  Such a row settles from its
    figure, and a settle naming *account_id* as the tender would move that
    payment onto it (``_covering._re_point``).

    **ONE predicate for every screen that reads a statement of**
    ``account_id`` (plan step ``credit_card:CC-5-4a-5``, leaf 5c-2a).  The
    reconcile panel leaves such a row off the account's own list (ruling
    **R-CC126**, developer 2026-09-30: *"Each list offers only money that
    moved on its own account, so reconciling never moves a payment. A bill
    paid by its own purchases stays on its own list."*), and the statement
    matcher does not offer it on that account's screen and says why (ruling
    **R-CC137**, developer 2026-10-04: *"Checking's statement screen does not
    offer Hotel while its payment is recorded on the Visa (the reconcile
    panel's test, R-CC126), and says so on that screen"*).  A row holding
    purchases is NOT one of these: it settles from them and never re-points,
    which is the ruling's "stays on its own list".  So the popover's 'Paid
    from' is the one door that moves a bill's payment between accounts.

    Args:
        account_id: The account whose statement is being read.

    Returns:
        A SQLAlchemy boolean expression over ``Transaction``.
    """
    return and_(
        holds_no_purchase_clause(),
        Transaction.entries.any(and_(
            covering_clause(),
            TransactionEntry.account_id != account_id,
        )),
    )


def covered_cash_leg(row: PlanItem, account_id: int) -> Decimal:
    """Return what a settled *row* is WORTH on *account_id*: its covering movement's cash there.

    **The one valuation of a settled row**, for every reader that asks what
    the row moves rather than what its movements do: the statement matcher's
    accepted register (``_accepted_view``, for a member naming a payment --
    the one app-side member shape since plan step
    ``credit_card:CC-5-4a-2``) and its undo dialog
    (``_release``, for a row an act created).  **A transfer's LEG is asked the same
    question since leaf ``balance:X-bi-6-4c-1``**: the register values a
    member through the movement's parent
    (``transfer_legs.movement_parent``), a plan row or the leg whose record
    the movement is, and a leg answers ``covering_movements`` as a row does
    (``TransferLeg.covering_movements``: its record, or nothing), so one
    producer values both with no shape branch -- a leg-shaped twin of this
    function would be a second spelling of the same rule.  Ruling **R-BAL81** (plan step
    ``balance:X-bi-4a``): a row is worth what its covering movement moves,
    so a covered bill, paycheck or transfer leg is worth its figure and a
    ``purchases``-basis envelope -- which has no covering movement, its
    purchases being the record -- is worth ``0``.  The matcher's OFFER read
    this too through plan step ``credit_card:CC-5-3``, when a settled row was
    its candidate; since ``credit_card:CC-5-4a-1`` (ruling **R-CC43**) the
    movement is the candidate and is priced as a movement
    (``statement_match._valuation.settlement_price``), so the offer no
    longer asks the row.

    **Worth it ON THE ACCOUNT ASKED ABOUT** (plan step ``credit_card:CC-5-3``,
    ruling **R-CC40**, developer 2026-09-21).  Every reader here is a
    statement-side reader holding one account -- the screen being reviewed,
    the match's own -- and a settled row's money is where its covering
    movement is, which since ``CC-5-3`` may be another account than the
    row's: a ``$120`` hotel bill on checking paid FROM THE CARD holds its
    movement on the card.  Checking's feed never shows that money, so on
    checking's screen the row is worth ``0`` and is not offered (a stray
    ``$120`` line there stays unexplained until the owner records it, rather
    than being paired with a bill whose money moved elsewhere); the card's
    screen is where the movement is the candidate (the ruling's second half,
    plan step ``credit_card:CC-5-4a-1``).  The account-blind sum had no
    reader left once every caller asked about one account, so the parameter
    is REQUIRED: a default would keep the wrong meaning as what a caller
    gets by saying nothing.  The fold reads the same fact the same way
    (``cash_ledger._events.settled_cash_facts`` folds a movement on its own
    account, ruling **R-BAL75**).  Through ``X-bi-3e``'s first cut those
    three readers spelled ``settled_cash_leg`` ALONE and two of them read
    every accepted bill as a match that stopped holding and every undo as
    moving no money (adversarial review, 2026-09-16); then
    ``settled_family_leg`` summed the row's leg and this, which priced an
    envelope ROW at its un-dated purchases beside the purchases themselves
    and let an accept date a day the fold no longer read (X-bi-4a's review
    H1).  One producer is the remedy, not three patches; and the MOVEMENT
    is the matcher's subject since ruling **R-CC43** (plan step
    ``credit_card:CC-5-4a-1``).  An UN-DATED movement -- a reverted row's, kept since plan step
    ``X-bi-3e-2`` -- is worth nothing here, as it posts nothing
    (``purchase_posts``) and folds to nothing (``_events.settled_cash_facts``):
    the same three-way agreement, stated by the day rather than by the row's
    status, so a member reverted since its match was accepted reads ``0.00``
    and the match stops holding by arithmetic.

    Each posted covering movement is worth
    :func:`app.services.cash_ledger.movement_cash_leg` -- the ONE valuation
    of a movement, its whole figure (for a transfer side's record, its
    transfer's) in its PARENT's direction (plan step
    ``X-bi-3b``, ruling **R-BAL35**), which is what the fact producer and the
    ledger writer book for it.  This read ``-movement.amount`` for itself
    until that step, a second spelling stated rather than hidden because a
    spelling that agrees today is still two spellings (rule 14); one producer
    is what deleted it.

    **TOTAL over the parent's contributing gate, as every reader of a
    purchase is** (ruling **R-FM**): a soft-deleted or Credit / Cancelled
    parent's purchases post nothing and fold to nothing
    (``_events.settled_cash_facts``, ``purchase_posts``), so its covering
    movement is worth nothing here too -- the producer's own gate, and this
    module's ``is_balance_contributing`` guard went with the spelling.  The
    first cut summed the movement regardless and the accepted register read a
    soft-deleted bill as still holding (``test_withdrawal``'s soft-delete
    case, 2026-09-16).  It raises nothing: a movement's figure is stored, so
    there is no amount model to refuse, and the three readers no longer guard
    a refusal that cannot come.

    Args:
        row: The transaction, with ``entries`` loaded or loadable -- or a
            :class:`~app.services.transfer_legs.TransferLeg`, whose contributing
            gate and direction are its transfer's and side's.
        account_id: The account whose statement is asking -- the only
            account a covering movement is worth anything on.

    Returns:
        The signed sum, ``Decimal("0")`` when nothing is covered, posted,
        contributing or on *account_id*.
    """
    return sum(
        (
            movement_cash_leg(row, movement)
            for movement in row.covering_movements
            if movement.settled_on is not None
            and movement.account_id == account_id
        ),
        Decimal("0"),
    )
