"""
Shekel Budget App -- Status seam: the COVERING MOVEMENT a settle writes.

Plan step **balance:X-bi-3a**, rulings **R-BAL39** and **R-BAL41**.  A bill
ticked Paid used to record what moved on the bill row alone -- its own
``settled_amount`` beside how the figure was known -- while an envelope's
purchases were each a row of money that moved.  Every settle now records its
money the envelope's way: the MANUAL branch's settlement is recorded as ONE
covering movement, a ``budget.transaction_entries`` row carrying the figure
the settle booked, who wrote it, the day the money moved and how that day is
known, and the statement that showed it.  ``X-bi-3d`` cut every
already-settled row over to this shape, ``X-bi-4a`` re-pointed the fold and
the posting writer onto movements, ``X-bi-4b-1`` every other reader of the
record (``row_valuation.settled_figure`` sums the row's entries; ``_record``'s
retained reads take the movement's figure and source), and ``X-bi-4b-2``
deleted the row's own two columns, ``settled_amount`` / ``settled_basis_id``
(migration ``45f10b870c8b``), so the movement is the record's ONE home
(ruling **R-BAL80**).

**Why the seam, and not the settle verb.**  ``apply_status_change`` is the ONE
writer of a row's settlement record (plan step X-au-c3), reached by every
settle door of a row and the transaction settle verb
(``transaction_service._settle.settle_transaction``); a transfer's record is
written by the seam's Transfer arm (:mod:`._side`, through
``apply_status_to_all_three``).  The covering movement is that record's new
home, so it is written where the record is: a second call site in each verb
would be the maintenance contract rule 14 deletes, and a verb added later
would inherit the write with none of the seam's refusals.

**The movement IS the money** (plan step ``balance:X-bi-4a``, ruling
**R-BAL80**).  The fold's fact producer reads every dated movement and no row
(``cash_ledger._events.settled_cash_facts``), the posting writer posts each
one and nothing for the row (``posting_service.sync_transaction_postings``
walks ``txn.entries``), and the statement matcher offers a settled row AS
its covering movement, priced when dated at what it moves
(``statement_match._valuation.settlement_price``, rulings **R-BAL81** and
**R-CC43**), and values an accepted payment member at :func:`covered_cash_leg`
on the act's account (``statement_match._accepted_view._accepted_row``); the
mirror itself is kept out of the purchase candidates by
:func:`covering_clause`.
Through ``X-bi-3e`` the first two also read the row's own leg,
``cash_ledger.settled_cash_leg`` -- the recorded figure MINUS its posted
purchases, exactly zero for a covered bill by ruling **R-FM**'s identity --
so the write here was balance-neutral by construction; the matcher read it
one step longer, and it is deleted.  None of those readers
branches on the row's kind.

**A revert UN-DATES the covering movement and KEEPS it** (ruling
**R-BAL61**, plan step ``X-bi-3e-2``).  Leaving the settled band releases
the row's assertion and keeps what moved (plan step X-au-c3's rule, held by
the row's own ``settled_amount`` / ``settled_basis_id`` until ``X-bi-4b-2``
and by this movement since), and the next settle honours a retained STATED
record or re-prices a ``resolved`` one (``Settlement.from_settle``).  The
movement is the record and, since plan step ``X-bi-3e-1``, the only home
for WHO WROTE the figure, which the row's columns never held -- so the
movement follows the row's assertion: its day pair and clearing link are
released with the row's
(``_follow_assertion``), its figure, source and row survive, and the re-settle
re-dates the SAME row (``_cover`` through ``_mirror_assertion``; the id
survives).  A revert deleted it through ``X-bi-3e-1``, and the retained read
answered by R-BAL61's cutover mapping (``_record.recorded_settlement``,
ruling **R-BAL70**) for every reverted row; since plan step
``balance:X-bi-4b-1`` that read takes the kept movement's figure and source
and nothing else, and a row with none retains nothing (ruling **R-BAL82**),
so the mapping is retired.  What a kept, un-dated movement must NOT do is
read as a purchase -- an envelope closed EMPTY at the door, reverted and
then given real purchases would sum the stale close into them -- and ruling
**R-BAL68** answers that where the readers are: every purchase-meaning
reader asks :attr:`~app.models.transaction.Transaction.purchases`, the
entries less the mark, and the family readers named above keep ``entries``.
Two records still WITHDRAW the mirror outright (``_withdraw``): a ``$0.00``
figure, which ``ck_transaction_entries_positive_amount`` lets no movement
carry, and a ``purchases`` record, whose figure the row's own purchases
state -- through the ONE act that takes a movement off the books
(``movement_removal.remove_movements``, ruling **R-CC54**), which reverses
the payment's legs and takes it out of every match naming it before it
deletes it (finding **CC-358**).

**Which entry is the covering movement is a STORED fact of the movement**
(``transaction_entries.covers_settlement``), never a derivation over the
row.  A first cut read "a settled row that stores a figure holds no
purchases, so its entries are the seam's" -- and a door refuted it at the
time: *Track individual purchases* unticked on a settled envelope, then a
figure typed over it, left a ``corrected`` record beside real purchases
(``test_release``'s container-beyond-the-door case drove it end to end), and
under the derivation the seam would have overwritten one of those purchases
as its mirror.  Ruling **R-BAL78** (plan step ``balance:X-bi-4a``) refuses
that typed figure at the verb and at the seam, so the state is
unrepresentable now and the case grades the refusal; the mark stays a stored
fact anyway, because a reader that is correct only while every door keeps a
rule is a contract nobody can see.  So the seam marks what it writes, finds
it by the mark, the entry doors refuse to touch a marked row, and a partial
unique index holds the count at one per row.

**Every settled row is covered, whatever its kind, and the kinds arrived one
leaf at a time.**  ``X-bi-3a`` covered EXPENSE parents; ``X-bi-3b`` covered
INCOME parents once a movement's direction was its parent's everywhere it is
read (``cash_ledger.movement_cash_leg``, ruling **R-BAL35**) -- until then
six readers spelled *a purchase is money leaving* and a covered paycheck read
``-figure``; ``X-bi-3c`` covered the two shadows of a TRANSFER.  A
transfer's sides have their own arm since plan step ``balance:X-bi-6-4d-2``
(:mod:`._side`, through ``transfer_service._status.apply_status_to_all_three``),
which writes each side's record under the transfer's side link and asks this
module only :func:`raises_basis`.  Each side's record moves the transfer's
one figure in its own side's direction -- ``-figure`` off the from-account,
``+figure`` into the to-account -- through the same producer a bill's and a
paycheck's read.

**A transfer's movements POST, each as its own entry, since plan step
``balance:X-bi-6-3``** (ruling **R-BAL45**'s shape C, built under
**R-BAL101**).  The posted ledger books each side's record on its own day
against the owner's Transfers-in-transit account -- ``{from-side
account -figure, transit +figure}`` and ``{to-side account +figure, transit
-figure}`` -- through the ONE movement writer every purchase and covering
movement posts through (``_posting_purchases``, the counter leg dispatched by
the parent's shape), so the walk and the ledger read one movement each and
nothing is mirrored between them.  Through ``X-bi-6-1b`` the ledger booked
the pair as ONE entry off the income shadow's record and every
purchase-posting door returned for a shadow's entries -- the interval ruling
**R-BAL45** accepted, closed when that step gave the writer its per-movement
shape.  The two sides' days part since ``X-bi-6-4c-3`` (ruling **R-BAL142**:
each side keeps its own day, a side with no evidence borrows the other's), and
each side's record takes its own through the Transfer arm (:mod:`._side`).
Posting a shadow's movement through the purchase source with a CATEGORY
counter was rejected at R-BAL45: a transfer between two of the owner's
accounts is neither income nor expense, which is what the transit counter
says.

**A movement's account is its own -- where its money moved** (rulings
**R-BAL46**, **R-BAL75**, **R-BAL76**).  The co-located key that held it to
its parent's, ``fk_transaction_entries_parent_account``, is dropped since
plan step ``credit_card:CC-5-1``; a transfer side's record follows its
endpoint through its side key's ``ON UPDATE CASCADE`` when the endpoint moves
(``transfer_service._endpoints._apply_endpoint_move``, which lands it in the
session through ``_side.land_moved_side_record``).  **The movement this
module writes books on the TENDER** (plan step ``credit_card:CC-5-3``,
rulings **R-CC15** and **R-CC42**): the account the
:class:`~app.services.status_seam._record.Settlement` names when the door
named one -- the card, for a bill the owner charged to it -- else the seam's
one default, :func:`~._record.tender_account_id_of` (the kept movement's own
account, so a revert keeps where the money moved as it keeps the figure;
the row's own account for a first settle, where it is EXPECTED to be paid
from).  A named tender re-points a kept movement (``_re_point``: both
clearing links released, the day re-graded against the new account's
opening); the row's clearing link reaches only a movement on the row's
account (``_links_the_row``), and a statement's link reaches each fact on
that statement's own account (:func:`record_clearing`, plan step
``credit_card:CC-5-4b``: a card's statement links the card payment of a
bill planned on checking, and not the bill); and a settled row is worth its
covering movement's cash ON THE ACCOUNT ASKED ABOUT (:func:`covered_cash_leg`,
ruling **R-CC40**).

Services-boundary discipline (``CLAUDE.md`` Architecture): no Flask imports;
mutates in place and never commits; the withdraw arm's posting reversal
FLUSHES, as every ledger write does, and the caller owns the session
boundary.  The ledger is otherwise the DOOR's: a revert un-dates the mirror
here and the verb's family reconcile (``posting_service.
sync_transaction_postings``, which walks ``txn.entries`` after the seam
returns and posts nothing for an un-dated movement) reverses its legs --
the parent posts nothing of its own since plan step ``balance:X-bi-4a``
(ruling **R-BAL80**), so the movement's legs are the whole of what a revert
reverses -- every production revert of a transaction reaches
the seam through ``transaction_service.apply_requested_status``, and a
transfer's through ``transfer_service`` and the pair's own door
(``posting_service.sync_transfer_postings``, which reads each movement's
state since plan step ``balance:X-bi-6-3``); ``scripts/integrity_check.py``'s
DC-10 grades the state a caller of the bare seam would leave.  Money is
``Decimal`` throughout, read off the
:class:`~app.services.status_seam._record.Settlement` the seam was handed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import and_
from sqlalchemy.orm.attributes import flag_modified

from app import ref_cache
from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import match_withdrawal, movement_removal
from app.services.match_press import Press
from app.services.cash_ledger import (
    movement_cash_leg,
    reject_movement_before_books_open,
)
from app.services.settle_day import (
    SettleDay,
    is_evidence,
    record_settle_day,
    recorded_settle_day,
)
from app.services.status_seam._record import (
    Settlement,
    covering_movement_of,
    tender_account_id_of,
)

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
    payment onto it (:func:`_re_point`).

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


def _mirror_assertion(row: Transaction, movement: TransactionEntry) -> None:
    """Bring *movement*'s settle-day pair and statement link up to the row's.

    **A day that did not MOVE never lowers evidence, and a confirmation
    RAISES it** -- the seam's own rule for the row, applied to its movement.
    The ROW is what the matcher offers and the panel ticks (its mirror is
    kept out of the purchase candidates), so the row's assertion is the one
    that moves and the movement follows it:

    * the row's day DIFFERS from the movement's -- a settle, a re-settle
      after a revert, a settle-day correction: the movement takes the row's
      pair, through ``record_settle_day`` so the pair keeps its one writer and
      its books-boundary refusal, and the row's link with it (the row's own
      release logic already decided that link for this move).  A covering
      movement's purchase day IS its settle day (ruling R-BAL39), so the
      correction moves both;
    * the days are EQUAL and the row's basis RAISES the movement's -- a bank
      line confirmed the day the panel had only bounded (``_moving``, finding
      **N-332**): the movement takes the row's pair and the link stands, the
      same change of basis the row itself records.  A raise is the row's
      ``observed``, or any basis over a movement holding no evidence of its
      own (:func:`~app.services.settle_day.is_evidence`); the three evidence
      members are separated by provenance and not ranked, so nothing else
      moves.  **The movement's own evidence is never lowered**, the contract
      ``TestTheMirrorNeverLowersEvidence``
      (``tests/test_services/test_covering_movement/test_who_wrote_the_figure.py``)
      holds the seam to with a bank-observed movement planted under an ``entered`` row.  No
      door on this tree writes that state -- the statement matcher and the
      reconcile tick settle the ROW, a card tender only re-points its
      movement (:func:`_re_point`), and the entry doors refuse a payment
      record -- so on door-written data this arm and a copy of any differing
      basis agree, and the planted case is the one that tells them apart
      (measured at this leaf's review).  One rule for every row, not a
      transfer branch;
    * the days are EQUAL otherwise: the pair stands, and the movement takes
      the row's link only where it holds none -- the reconcile panel ticked
      the row on an asserted day, and the movement sits inside that assertion
      too.  An identity re-submit (a popover Save with the status untouched)
      lands here and changes nothing.

    **The row's link reaches only a movement on the ROW's account** (plan
    step ``credit_card:CC-5-3``, ruling **R-CC15**; :func:`_links_the_row`).
    A movement on another account -- the card, for a bill charged to it --
    carries its OWN clearing link, written by :func:`record_clearing` when
    that account's reconcile panel ticks the row from its "Paid from this
    account" list (plan step ``credit_card:CC-5-4b``; the statement matcher
    offers it since ``CC-5-4a-1`` and writes no link, ruling **R-FV**), and
    the row's link stays the
    row's: the checking statement the row's link names never showed money
    that moved on the card, and ``fk_transaction_entries_reconciled_by``
    holds the movement's link to the movement's account, so the copy would
    be unstorable as well as false.  **A day move still WITHDRAWS such a
    movement's own link** (ruling **R-FL**: the observation was of money on
    a named day), and only the COPY is gated: without that the revert of a
    card-cleared bill would un-date the movement with its link standing,
    which ``ck_transaction_entries_cleared_needs_settle_day`` refuses at the
    flush (the review of plan step ``CC-5-3``).
    """
    if movement.settled_on != row.settled_on:
        # A covering movement's purchase day IS its settle day (ruling
        # R-BAL39: a bill's payment has no other day), so a correction moves
        # both -- and ``ck_transaction_entries_settled_not_before_purchase``
        # holds as the equality it always was.
        if row.settled_on is not None:
            movement.purchased_on = row.settled_on
        record_settle_day(movement, recorded_settle_day(row))
        movement.reconciled_by_id = (
            row.reconciled_by_id if _links_the_row(row, movement) else None
        )
        return
    row_day = recorded_settle_day(row)
    if raises_basis(row_day, recorded_settle_day(movement)):
        record_settle_day(movement, row_day)
    if movement.reconciled_by_id is None and _links_the_row(row, movement):
        movement.reconciled_by_id = row.reconciled_by_id


def raises_basis(stated: SettleDay, held: SettleDay) -> bool:
    """Return whether *stated*, on the SAME day as *held*, raises what the movement knows.

    The equal-days arm of the mirror's rule, stated once for both arms of the
    seam -- :func:`_mirror_assertion` for a row's covering movement and
    ``_side`` for a transfer side's record (plan step
    ``balance:X-bi-6-4d-2``): a bank line's ``observed`` raises any basis, and
    any basis raises a movement holding no evidence of its own
    (:func:`~app.services.settle_day.is_evidence`, a ``borrowed`` day).  The
    three evidence members are separated by provenance and not ranked, so
    nothing else moves, and a movement's own evidence is never lowered.

    Args:
        stated: The day the act resolved for the movement, on the same civil
            day as *held*.
        held: What the movement records now.

    Returns:
        ``True`` when the movement should take *stated*'s pair.
    """
    return stated != held and (
        stated.basis is SettledDayBasisEnum.OBSERVED or not is_evidence(held)
    )


def _links_the_row(row: Transaction, movement: TransactionEntry) -> bool:
    """Return whether the row's clearing link describes *movement* too.

    The predicate :func:`_mirror_assertion` copies ``reconciled_by_id`` off
    the row by: a statement that showed the ROW's money on the row's account
    showed this movement exactly when the movement is on that account.  The
    composite key ``fk_transaction_entries_reconciled_by`` says the same in
    the storage tier; this is the seam's spelling, so the false state is
    refused before the flush rather than by it.  :func:`record_clearing`
    asked it too until plan step ``credit_card:CC-5-4b``, when it stopped
    COPYING the row's link and started linking each fact on the statement's
    own account -- the same rule said of the statement rather than of the
    row, which is what lets it link a payment whose row is elsewhere.

    Args:
        row: The settled transaction.
        movement: Its covering movement.

    Returns:
        ``True`` when the movement is on the row's own account.
    """
    return movement.account_id == row.account_id


def _record_moved(row: Transaction) -> None:
    """Move *row*'s optimistic-lock counter: its RECORD changed and it did not.

    **The record is part of the row's aggregate, and the row's counter is
    what a stale form is caught by** (developer ruling 2026-08-18 for the
    transfer; the same rule for a plain row).  Both full-edit popovers pin
    the row's ``version_id`` -- the transfer's own, which the seam's
    Transfer arm moves whenever a side's record moves (``_side``, plan step
    ``balance:X-bi-6-4d-2``) -- and since plan
    step ``balance:X-bi-4b-2`` the settled FIGURE lives on the covering
    movement alone: a figure correction writes this table and nothing of the
    row, so the row stays clean, its counter never moves, and a second tab
    holding the same pin overwrites the correction and reports success --
    the two-tab lost update the transfer's counter bump (then
    ``_update._bump_parent_version_if_a_leg_moved``) measured on the live
    route, back by another door the moment the row's
    own figure columns went (a ``$214.37`` correction, then a prefilled
    ``$200.00`` saved over it against the same pin, both 200).  Through
    ``X-bi-4b-1`` the seam's write of those columns dirtied the row for
    free.  So the writer that moves the record moves the row's counter,
    exactly when the record NETS a change (:func:`_cover`, :func:`_withdraw`):
    an identity re-submit that records the same figure again writes nothing
    and bumps nothing, or every second tab would meet a spurious 409.

    ``flag_modified`` on ``status_id`` for the reason ``_update`` gives: the
    row has no field of its own to change here, an assignment of an
    unchanged value is dropped from the UPDATE, and ``status_id`` is the one
    column every path through the seam has just written, so it is present
    in the object state (``flag_modified`` refuses an absent one).
    """
    flag_modified(row, "status_id")


def _record_onto(
    row: Transaction, movement: TransactionEntry, settlement: Settlement,
) -> bool:
    """Write what a settle RECORDS onto *movement*: figure, source, name, day.

    The source is the record's own -- WHO WROTE the figure, stated by the
    door that handed the verb a :class:`~app.services.stated_figure.
    StatedFigure` or by the verb's own ``resolved`` arm (ruling **R-BAL61**,
    plan step X-bi-3e-1).  It was inferred here from the day's basis beside
    the figure until that step, and the premise was measured false: a figure
    a person typed over a standing bank-observed day was labelled the bank's.

    Returns:
        Whether the RECORD -- the figure, its source or the name -- netted a
        change against what *movement* carried, for :func:`_record_moved`.
        The day pair is the row's own assertion and moves the row itself.
    """
    # The plan's name as it reads at the settle -- the movement's OWN fact
    # (ruling R-BAL39): a bill's payment has no receipt text, and a later
    # rename of the plan no more rewrites this than renaming an envelope
    # rewrites its purchases.  Its day, likewise its own, is the mirror's.
    changed = record_figure(movement, settlement, row.name)
    _mirror_assertion(row, movement)
    return changed


def record_figure(
    movement: TransactionEntry, settlement: Settlement, name: str,
) -> bool:
    """Write a settle's FIGURE onto a row's covering *movement*: amount, source and name.

    The figure half of what a settle records, for the ROW arm alone
    (:func:`_record_onto`).  A transfer side's record states no figure --
    the transfer holds it once (``_side._state_figure``; plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL366**) -- so ``_side`` writes only
    the side's name and day.

    Args:
        movement: The row's covering movement being written.
        settlement: The record, carrying a figure and its source.
        name: What the payment is called: the plan row's name.

    Returns:
        Whether the figure, its source or the name netted a change against
        what *movement* carried.
    """
    before = (movement.amount, movement.figure_source_id, movement.description)
    movement.amount = settlement.amount
    movement.figure_source_id = ref_cache.movement_figure_source_id(
        settlement.source,
    )
    movement.description = name
    return before != (
        movement.amount, movement.figure_source_id, movement.description,
    )


def _re_point(
    row: Transaction, movement: TransactionEntry, account_id: int,
    press: Press | None,
) -> bool:
    """Move *movement* onto *account_id* if it is not there; say whether it moved.

    The TENDER's write onto a movement that already exists (plan step
    ``credit_card:CC-5-3``): a re-settle naming an account other than the
    kept movement's, or a "Paid from" correction on a settled row.  Three
    consequences, in this order:

    * **both clearing links are RELEASED** -- the movement's and the row's.
      A link records that a named statement of a named account was seen to
      show this money; money that moved on another account was shown by no
      such statement, so the observation is withdrawn rather than left to
      contradict the record (the same rule a settle-day move applies, ruling
      **R-FL**).  The storage tier would refuse the movement's anyway
      (``fk_transaction_entries_reconciled_by`` holds the link to the
      movement's account); the row's is released HERE because the seam's
      own release arms are about the DAY and cannot see the tender;
    * **every MATCH naming the movement is withdrawn** (plan step
      ``credit_card:CC-5-4a-1``, ruling **R-CC46**, developer 2026-09-21),
      on the same argument one clause up: a match asserts that a named
      account's bank line IS this movement, and money that moved on another
      account was shown by no such line.  Every accepted match names the
      movement (ruling **R-CC43**) through a member the key
      ``fk_statement_match_members_entry_account`` holds to the movement's
      account, so the assertion is both false and unstorable once the
      account changes; ``match_withdrawal.withdraw_for_moved_movement`` takes
      the member as a delete would, withdraws an act left naming no app row,
      and FLUSHES before the assignment below -- the unit of work orders a
      DELETE after an UPDATE, and the key is checked at the flush.  The
      screen that offers the move discloses it first
      (``match_withdrawal.pending_for_movements``, the popover's "Paid from"
      caption); the refused alternative kept the correction from happening
      while a match stood;
    * the account is assigned;
    * **the day the movement will END with is graded against the new
      account's books boundary FIRST** (``cash_ledger.
      reject_movement_before_books_open``, the same producer
      ``settle_day.record_settle_day`` asks).  The ROW's day, which the seam
      has written before anything reaches :func:`_cover` and which
      ``_mirror_assertion`` copies onto the movement next: the day writer
      asks the boundary only when a day is written, the mirror writes one
      only when the days differ, and a tender correction on a settled row
      leaves them equal -- so without this line a payment dated before the
      card's opening could be moved onto the card unrefused.  The row's day
      rather than the movement's OLD one, because a settle-day move and a
      tender arrive in ONE popover Save and the old day is not what the
      movement ends up dated (the review of this leaf: a payment on the
      card's opening day, corrected in one Save to the day after AND the
      card, was refused for a day it was leaving).  Asked before this
      function's two assignments, so a refused re-point writes nothing here;
      what the seam wrote earlier in the same act (the status, the row's
      day pair) is the caller's rollback's, as every refusal raised inside
      ``sync_covering_movement`` is.

    Args:
        row: The settled or settling transaction, its day pair already
            written for this act.
        movement: Its covering movement.
        account_id: The account the record names, already gated.
        press: The save's press, or ``None`` when its door named nothing
            (ruling **R-CC81**; :func:`sync_covering_movement`).

    Returns:
        Whether the movement's account changed, for :func:`_record_moved`.

    Raises:
        ValidationError: When the row's day is on or before the new
            account's opening, or when the matches the move withdraws free
            other lines than *press*'s page named (ruling **R-CC127**).
    """
    if movement.account_id == account_id:
        return False
    if row.settled_on is not None:
        reject_movement_before_books_open(account_id, row.settled_on)
    match_withdrawal.withdraw_for_moved_movement(
        movement, row.user_id, press=press,
    )
    movement.reconciled_by_id = None
    row.reconciled_by_id = None
    movement.account_id = account_id
    return True


def _cover(
    row: Transaction, settlement: Settlement, press: Press | None,
) -> None:
    """Ensure *row* holds exactly one covering movement mirroring *settlement*.

    **A ``$0.00`` settlement writes NO movement** -- zero movements is a
    legal count and ``ck_transaction_entries_positive_amount`` (``<> 0``)
    says a movement of nothing is not one -- so a re-record to zero
    withdraws an existing mirror.  The same arm ruling **R-BAL40** gives the
    cutover.  Reachable: a bill budgeted at ``$0.00`` marked paid, or a typed
    ``$0.00`` on the panel or the popover (both schemas admit it).

    **A re-settle re-dates the movement the revert kept** (plan step
    ``X-bi-3e-2``): the survivor is found by its mark, the record is written
    onto it -- a re-priced figure and its source over the old ones, the
    plan's name as it reads now -- and ``_mirror_assertion`` dates it on the
    row's new day.  The id survives, so a match or a log line that named it
    still names it.

    **And books it on the TENDER** (plan step ``credit_card:CC-5-3``, rulings
    **R-CC15** and **R-CC42**): the account the record names when the door
    named one, else the seam's one default,
    :func:`~._record.tender_account_id_of` -- the kept movement's own account
    for a re-settle, the row's own for a first settle -- so a revert keeps
    where the money moved exactly as it keeps a stated figure, and only a
    NAMED tender re-points a kept movement (:func:`_re_point`).  Through
    ``CC-5-2`` this arm re-pointed a kept movement onto the ROW's account
    unconditionally (ruling **R-CC36**'s re-point clause, for a Projected row
    whose definition moved accounts while it held a kept record); R-CC42
    amends that clause, because under a tender the same columns cannot tell
    that case from a bill the owner charged to the card and reverted to edit,
    and the one-click re-settle of the latter must not move its ``$120`` back
    onto checking.  Assigned BEFORE the record is written, so
    ``record_settle_day``'s books boundary (read under ``no_autoflush`` off
    the column) grades the day against the account the record will book on.
    The lazy ``account`` relationship is not loaded here and is not assigned:
    the fold and the ledger read the column, and no reader of the
    relationship follows on this path.
    """
    if not settlement.amount:
        _withdraw(row, press)
        return
    account_id = (
        settlement.account_id if settlement.account_id is not None
        else tender_account_id_of(row)
    )
    movement = covering_movement_of(row)
    if movement is not None:
        re_pointed = _re_point(row, movement, account_id, press)
        if _record_onto(row, movement, settlement) or re_pointed:
            _record_moved(row)
        return
    movement = TransactionEntry(
        transaction_id=row.id,
        # The TENDER (above): the account the money moved through, which is
        # the row's own -- where it was EXPECTED to be paid from (ruling
        # **R-CC16**) -- unless the door named another (a bill charged to the
        # card is settled with its covering movement on the card, **R-CC15**).
        account_id=account_id,
        # The row's OWNER, which ``fk_transaction_entries_owner_transaction``
        # holds it to (plan step ``credit_card:CC-5-1``, ruling **R-BAL76**).
        owner_id=row.user_id,
        # The AUTHOR column names who recorded the movement; the seam records
        # it on the owner's behalf, and the owner is the row's.
        user_id=row.user_id,
        is_credit=False,
        # The mark the seam finds its own mirror by (module docstring).
        covers_settlement=True,
    )
    _record_onto(row, movement, settlement)
    # Appended to the relationship rather than only added to the session, so
    # the verbs' ledger reconcile -- which walks ``txn.entries`` after the seam
    # returns -- posts it in the same pass as the parent's now-zero leg.
    row.entries.append(movement)
    db.session.add(movement)
    _record_moved(row)


def _withdraw(row: Transaction, press: Press | None) -> None:
    """Take *row*'s covering movements off the books: a record that carries nothing.

    The two records that WITHDRAW a mirror rather than un-date it (module
    docstring): a ``$0.00`` figure, which
    ``ck_transaction_entries_positive_amount`` lets no movement carry, and a
    ``purchases`` record, whose figure the row's own purchases already state
    -- a mirror kept beside them would be a second statement of that money.
    Leaving the band is NOT one of these; that arm keeps the movement
    (``_follow_assertion``).

    **Through the ONE act that takes a movement off the books** (plan step
    ``credit_card:CC-5-4a-3``, ruling **R-CC54**;
    ``movement_removal.remove_movements``): its ledger reversed, out of
    every match naming it, taken out of ``row.entries`` -- the collection the
    verbs' ledger reconcile walks after the seam returns, whose
    ``delete-orphan`` issues the DELETE.  Until that step this deleted the
    payment itself and asked no match (finding **CC-358**): the act naming it
    kept its bank line alone, and matching that line again raised on
    ``uq_statement_match_members_line``.  The screen that offers either
    record says what it frees first (ruling **R-CC56**: the full-edit
    popover's Actual box and its Paid button, reading
    ``match_withdrawal.pending_for_movements``) and posts back what it
    named; the act refuses a press whose freed lines differ from what its page named
    (plan step ``credit_card:CC-5-4a-5``, ruling **R-CC127**), so a door with
    no caption withdraws only by naming what lets it stay silent -- the
    grid's one-click Mark Paid, ruling **R-CC56**.
    """
    movements = list(row.covering_movements)
    if not movements:
        return
    movement_removal.remove_movements(
        movements, row.user_id, because=match_withdrawal.RE_RECORDED,
        press=press,
    )
    _record_moved(row)


def _follow_assertion(row: Transaction) -> None:
    """Bring *row*'s covering movements' day pair and link up to the row's.

    The mirror follows the row's ASSERTION, through ``_mirror_assertion``'s
    one rule per movement, and the act that arm answers is the row's:

    * **a revert** (ruling **R-BAL61**, plan step ``X-bi-3e-2``): the seam
      has already cleared the row's day pair and clearing link, and the
      differing-day arm writes that release onto the movement --
      ``record_settle_day(movement, None)`` clears the pair
      (``ck_transaction_entries_settle_day_basis_pairing`` is a
      biconditional) and the link goes with it
      (``ck_transaction_entries_cleared_needs_settle_day``).  The figure,
      its source and the row itself STAY: what moved is retained across a
      revert (plan step X-au-c3's rule, this movement its one home since
      ``X-bi-4b-2``), and the next settle re-dates the same row
      (``_cover``);
    * **a settle-day correction**: the movement takes the row's new pair;
    * **an identity re-submit**, or a non-settled row that still carries a
      kept movement (re-submitted, cancelled, reactivated): nothing moves.

    The ledger is the door's.  An un-dated movement posts nothing
    (``_posting_purchases.purchase_posts`` needs a day), so the family
    reconcile every revert door runs after the seam reverses whatever the
    movement had posted -- the one walk, which reversed the parent's own leg
    beside it through ``X-bi-3e`` and posts nothing for the row since
    ``balance:X-bi-4a`` (ruling **R-BAL80**) -- one spelling rather than an
    explicit reversal here beside it.  The revert
    arm deleted the row and reversed its legs itself through ``X-bi-3e-1``,
    because ``journal_entries.transaction_entry_id`` is SET NULL on delete
    and legs left behind a deleted row could never be reversed; a kept row
    has no such hazard.
    """
    for movement in row.covering_movements:
        _mirror_assertion(row, movement)


def record_clearing(row: Transaction, anchor_id: int) -> None:
    """Record WHICH statement showed *row*'s money, on each fact of it that statement covers.

    The ONE writer of a transaction's ``reconciled_by_id`` outside the seam's
    own release arms (plan step **X-bi-3a**, ruling **R-FL**), for a plain
    row: the reconcile panel's transaction arm calls it.  A transfer side's
    record is linked by the Transfer arm's twin,
    ``_side.record_side_clearing`` (plan step ``balance:X-bi-6-4d-2``;
    through a transfer shadow and this function until then).  The reconcile panel
    records the link AFTER the settle verb returns -- the verb is shared with
    the grid's Mark Paid, which no statement has shown -- and the settle has
    by then mirrored the row's money onto its covering movement, whose fact
    is the one the fold and ``StatementCoverage`` read.  A link written on
    the row alone would leave that fact unlinked and the panel's own clearing
    rule inert for every bill it ticks (found by ``test_cash_walk``'s
    governing-assertion case, 2026-09-16).

    **ONE rule: a statement of account X links every fact on X** -- the row
    when the row is on X, and each covering movement when it is on X (plan
    step ``credit_card:CC-5-4b``, ruling **R-CC44**: "the PAYMENT takes the
    statement's link and the bill does not, so ``record_clearing`` learns
    which account's statement it records").  A link records that a named
    statement of a named account showed this money, and
    ``fk_transactions_reconciled_by`` / ``fk_transaction_entries_reconciled_by``
    hold each link to its fact's own account, so the rule is also what the
    storage tier would refuse to be told otherwise -- refused here, before
    the flush.  Its two shapes:

    * the row is on the statement's account -- a bill on the account's own
      list: the row takes the link, and so does each covering movement on
      that account (a bill's tick names the account as its tender).  Every
      caller before ``CC-5-4b`` passed this shape, and for it the rule writes
      what the old body wrote (the row unconditionally, a movement on the
      row's account);
    * the row is planned on ANOTHER account and its payment is on the
      statement's -- a bill planned on Checking, paid from the card and
      reopened, ticked on the card's "Paid from this account" list: the
      payment takes the link and the row does not, since Checking's
      statements never showed that money.  This is the shape the old body
      wrote wrongly (the row's link unconditionally, and the card payment's
      never), which the storage key would have refused at the flush.

    **A tick can leave NOTHING on the statement's account, and then nothing
    is linked.**  A typed ``$0.00`` on that list settles the row with no
    covering movement at all (``_cover`` withdraws the kept payment: a
    movement of nothing is not one), so no fact of the row is money this
    statement showed.  The old body linked the row regardless, which that
    same key refuses.

    **The account is read OFF THE ASSERTION the link names**, never passed
    beside it: a caller handing both an anchor and an account could pair one
    account's assertion with another account, which is a second statement of
    one fact (``CLAUDE.md`` rule 14).  One primary-key read
    (``Session.get``, answered from the identity map when the assertion is
    already loaded).

    Args:
        row: The settled transaction the statement showed.
        anchor_id: The ``account_anchor_history`` row that was being read.
    """
    statement_account_id = db.session.get(
        AccountAnchorHistory, anchor_id,
    ).account_id
    if row.account_id == statement_account_id:
        row.reconciled_by_id = anchor_id
    for movement in row.covering_movements:
        if movement.account_id == statement_account_id:
            movement.reconciled_by_id = anchor_id


def sync_covering_movement(
    row: Transaction,
    *,
    was_settled: bool,
    now_settled: bool,
    settlement: Optional[Settlement],
    press: Press | None = None,
) -> None:
    """Keep *row*'s covering movement in step with the record the seam wrote.

    Called by ``apply_status_change`` after it has written the row's status,
    settle-day pair, clearing link and settlement record, so every value
    mirrored here is the row's FINAL one for this act.  Two cases, total over
    what the seam can be asked to do:

    * **in the band with a record** -- the row is settling or re-settling on a
      ``derived`` / ``corrected`` basis: ensure one covering movement carries
      the record (``_cover``), or none for a ``$0.00`` figure.  A
      ``purchases`` record covers nothing (the row's purchases are the
      record), and withdraws a mirror it finds (``_withdraw``);
    * **otherwise the mirror follows the row's assertion** -- a revert
      (the row's pair and link were released, so the movement's are:
      ``_follow_assertion``, plan step ``X-bi-3e-2``), an identity re-submit
      or a settle-day correction.

    **A row in the band neither before nor after this act is skipped**: its
    assertion did not change, so a movement a revert kept has nothing to
    follow (the days already agree at ``None`` and the link is already
    ``None`` -- ``_mirror_assertion``'s equal-days arm would write nothing),
    and reading it would cost a lazy load of the row's ``entries`` and a
    mid-update autoflush for a no-op wherever the caller has not loaded them
    (a transaction's door loads them for its reconcile anyway).
    The seam refuses a record beside a non-settled status before it gets here
    (``reject_settlement_without_settled_status``), so *settlement* is
    ``None`` whenever *now_settled* is ``False``.  The status the row was
    LEAVING was a third CASE through ``X-bi-3e-1`` (leaving the band deleted
    the movement); it is a load gate now, and the act it gates is the same
    "follow the row" a correction runs.

    Args:
        row: The transaction the seam just wrote.
        was_settled: Whether the row was in the settled band BEFORE the seam
            assigned its new status.
        now_settled: Whether it is in the band after.
        settlement: The record the seam was handed for this act, or ``None``.
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            what the door's page named or what lets it stay silent: both arms
            that take a payment out of its matches (:func:`_withdraw`,
            :func:`_re_point`) are reached through here and the act asks it
            (plan step ``credit_card:CC-5-4a-5``, rulings **R-CC81** /
            **R-CC127**).  Defaults to ``None`` -- *nothing shown* -- as
            ``apply_status_change`` does, so a caller that says nothing is
            refused if the record would free a line.
    """
    if not was_settled and not now_settled:
        return
    if now_settled and settlement is not None:
        # A record that STATES a figure names who wrote it (``Settlement``
        # refuses one without the other), and only such a record has anything
        # to mirror: a ``purchases`` record has neither.
        if settlement.source is not None:
            _cover(row, settlement, press)
        else:
            _withdraw(row, press)
        return
    _follow_assertion(row)
