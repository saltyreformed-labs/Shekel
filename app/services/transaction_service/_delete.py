"""What removing a transaction MEANS: the one sequence, and its order.

Plan step ``bank_import:X-gb``.  This sequence existed TWICE -- once in
``routes.transactions.mutations.delete_transaction`` and once in
``statement_match._release._remove``, whose own docstring says it "takes the
transaction delete sequence WHOLE" -- and a third caller would have made three.
Each step of it is a money rule with an ORDER that is load-bearing, so two
copies is two places for that order to drift.

**The order, and why each step is where it is.**  ``0`` is OWNERSHIP and it
is numbered from zero because it is not part of the sequence -- it is the
door's own precondition, asked before the sequence starts.

0. **Reconcile the ``owner_id`` the caller states against the row it hands in**
   (finding **N-373**, landed at plan step ``pay_calendar:C13-b``).  This door
   took an owner for its EVENTS and checked it against the row nowhere, while
   its sibling ``entry_service.delete_entry`` re-validated -- so of the two
   doors ``statement_match._release._remove`` calls, one asked and one
   trusted, and it was that asymmetry that let the by-id refetch look safe.
   It is one comparison because the owner is a COLUMN since plan step
   ``pay_calendar:C13-a``; before that it was a join walk through the paycheck
   and the finding was SEQUENCED behind the key for exactly that reason.
   FIRST, ahead of ``deletion_refusal``, so a caller naming the wrong owner is
   told nothing about the row -- the ordering ``entry_service._doors`` states
   for the same pair of guards.  Nothing here locks the row: since plan step
   ``balance:X-bn`` (ruling **R-CC106**) the request's transaction took its
   owner's write lock before it read any of the owner's data, so a purchase
   another tab added either committed before this delete read the row, or
   waits for the delete to commit.  Until that step the row's own lock did it
   (ruling **R-CC96**, plan step ``credit_card:CC-5-4a-4``).  **One that
   committed first is not deleted unnamed** (ruling **R-CC131**, developer
   2026-10-04, "Refuse and redraw", fulfilling R-CC96's clause "a later step
   makes Delete refuse when it would remove more than its dialog said"): the
   dialog sends back the purchases it named, and a row holding others is
   refused before anything is written, its popover redrawn under ruling
   **R-CC128**.
1. **Reverse the postings** (``posting_service``), while
   ``journal_entries.transaction_id`` and ``.transaction_entry_id`` still link
   them.  Both are ``ON DELETE SET NULL``: reversing afterwards is impossible
   and the original legs would be stranded on their ledger accounts with
   nothing to offset them.  Each row's WHOLE family at once -- the row's, and
   each live CC payback's in its chain -- so the anchor is re-checked once per
   row rather than once per movement (the churn ruling **R-BAL103** removed
   from the deploy resync); FIRST since plan step ``credit_card:CC-5-4a-3``,
   so step 2 finds every leaving movement's legs already at zero.  The row is
   reversed on the soft arm too: a tombstone contributes to no balance.
2. **Take the movements off the books through the ONE act**
   (:mod:`app.services.movement_removal`, plan step ``credit_card:CC-5-4a-3``,
   ruling **R-CC54**) -- the row's purchases and payment AND those of its live
   CC-payback chain, because all of them go in this one commit.  The act takes
   them out of every match, withdrawing one left naming no app row
   (developer ruling 2026-08-25): a match asserts that a bank line IS these
   rows, and when the last of them stops existing the line is unexplained
   again.  Over the WHOLE set in one call, so the dialog's figure and the
   receipt's are one derivation.  **On BOTH arms** (ruling **R-CC75**,
   developer 2026-09-23: "Deleting the occurrence takes its payments and
   purchases off the books through the one removal act, exactly as deleting
   a one-off does"): the soft arm's tombstone stays in the table, but what
   it held does not.  Until then a soft-deleted row KEPT its movements and
   its matches, and the balance skips a hidden row's movements -- so
   deleting one occurrence of a recurring envelope holding a bank-matched
   `$40.00` purchase left settled cash `$40.00` above the bank while the
   line read explained (measured 2026-09-23; ledger **N-290**'s soft-delete
   half), and plan step ``credit_card:CC-5-4a-4``'s doors then refused to
   remove that hidden row's period or definition with a sentence naming a
   row the grid does not show.  A row this door hides holds nothing now; one
   hidden before this release refuses the release's migration
   (``c4a4e7d1b9f2``, ruling **R-CC82**), and a TRANSFER's soft delete takes
   its sides' payments off the same way since plan step
   ``balance:X-bi-6-4d-2`` (finding **BAL-532**).
3. **Take down the live CC payback chain** (``credit_workflow``), on either
   arm and for a different reason on each.  A SOFT delete never fires
   ``transactions.credit_payback_for_id``, so without this a projected
   payback survives its hidden source and inflates the next period with no
   offsetting credit row; a HARD delete would be REFUSED by that key, which
   is ``ON DELETE RESTRICT`` since plan step ``balance:X-bi-7d-2`` (finding
   **CC-352**).  Step 2 has already taken the chain's movements off, so that
   helper takes down rows holding none.
4. **Remove the row**, soft or hard by whether its definition RECURS.
5. **Dispose of the definition the row was the LAST of** (plan step
   ``balance:X-bi-7b``, rulings **R-BAL23** / **R-BAL27**): a one-off is a
   rule-less definition plus its placed row, and a rule-less definition with
   no row defines nothing -- so its series and it go, through
   ``definition_delete.permanently_delete_definition``, the ONE act both
   definition doors already call.  AFTER the row, and asked of the row's
   definition rather than assumed: a definition holding any other row (a
   bank-born envelope's other paychecks, a cleared cadence's survivors, a
   soft-deleted one-off) stays.  Where a standing merchant rule names the
   definition the delete never gets here -- ``deletion_refusal`` refused it
   at step 0's neighbour, because the rule would cascade away with it.

**Why the FORK at step 4 is about the cadence and not about the status.**  A
row of a recurring definition is one instance of a rule that keeps generating:
deleting it hard would let the next regeneration put it straight back, so the
row stays as a tombstone the engine reads (``recurrence_engine`` skips the
OCCURRENCE a ``is_deleted`` row answers -- ``_recurrence_common.OccurrenceClaims``
counts every state -- and both generation indexes exclude it so a replacement
can be created only where the owner has not said no).  A row nothing
regenerates -- a link-less one, or one whose definition has no rule -- answers
to nobody, so it goes.  **Keyed on ``Transaction.recurs`` since plan step
``balance:X-bi-7a``**, not on the link: a rule-less definition's row is a
one-off (ruling **R-BAL20**), and a soft arm there would keep a tombstone no
pass will ever read, restorable from an archive drawer for a definition that
generates nothing -- the twin's live defect on its own table (finding
**N-386**).

Boundary discipline (``CLAUDE.md`` Architecture): an ORM row in, a frozen
dataclass out, no Flask import.  It MUTATES and does NOT commit -- the caller
owns the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.exceptions import NotFoundError, PageOutOfDate, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import (
    credit_workflow,
    definition_delete,
    match_withdrawal,
    movement_removal,
    posting_service,
)
from app.services.match_press import Press
from app.services.match_withdrawal import MatchWithdrawal
from app.services.transaction_service._row_rules import deletion_refusal
from app.utils.balance_predicates import is_projected


@dataclass(frozen=True)
class RowDeletion:
    """What deleting one row takes with it -- READ or DONE, one shape.

    **The dialog prints this and the press performs it**, which is the shape
    :func:`~app.services.statement_match.planned_removals` already has one door
    over: two derivations would let a destructive control promise one thing and
    do another.  A first build derived the dialog from the ROW alone while the
    press also tore down the row's CC-payback chain -- measured at a `$200.00`
    card payment silently un-explained, over a dialog that named no bank line
    at all and a log line reading ``matches_withdrawn=0`` (adversarial review,
    2026-08-25).

    Attributes:
        soft: Whether the row stays as a tombstone (its definition recurs,
            ``Transaction.recurs``) rather than leaving the table.  The two
            are one act to the owner and two facts to the engine, so the door
            reports which it was
            instead of the caller re-deriving it from a column on a row it may
            no longer hold.
        paybacks: The names of the live CC payback rows that go down with it,
            nearest first -- ``()`` for the ordinary row that has none.  NAMED
            rather than counted, because they are budget lines in a future
            period that the owner will otherwise find missing.
        withdrawn: The matches withdrawn across the WHOLE set
            (:class:`~app.services.match_withdrawal.MatchWithdrawal`), so a
            receipt can name the bank lines that are unexplained again without
            asking a relation the delete has already destroyed.
        purchase_ids: The row's own PURCHASES that go with it
            (``Transaction.purchases``, the one reading of them) -- what the
            dialog's question counts and posts back, and what the press
            compares that against (ruling **R-CC131**).
        disposes_definition: Whether the row's DEFINITION goes with it --
            a one-off's, of which this row is the last (step 5 of the module
            docstring's order; rulings **R-BAL23** / **R-BAL27**, plan step
            ``balance:X-bi-7b``).  Carried so the dialog can say the ITEM
            goes and not only the row, off the same answer the press acts
            on (``definition_delete.is_last_row_of_its_definition``).
        comes_back_on_unarchive: Whether this row is one archiving and then
            un-archiving its item would bring back -- a soft delete of a row
            still Projected, which is un-archive's CANDIDATE set (ruling
            **R-CC86**, developer 2026-09-23: "Whether to show it is read from
            the same 'not yet paid' rule un-archive uses";
            ``balance_predicates.is_projected`` is the Python twin of the
            ``is_projected_clause`` that set is built on,
            ``definition_unarchive._hidden_rows``).  Un-archive restores a
            candidate UNLESS the books have moved over it by then (rulings
            **R-PC95**, **R-PC99**: its two checks, where the row sits and
            where its item's schedule puts it), and the dialog states that
            condition in words rather than computing it (ruling **R-CC112**,
            developer 2026-09-24, "State the condition": "True whenever you
            read it, before or after a later books move, and nothing new is
            computed").  A check at the moment the card opens could not see a
            books move made after the delete, which is the case the ruling
            was asked about.
    """

    soft: bool
    paybacks: "tuple[str, ...]"
    withdrawn: MatchWithdrawal
    purchase_ids: "frozenset[int]"
    disposes_definition: bool
    comes_back_on_unarchive: bool


def _leaves_the_books(
    txn: Transaction,
) -> "tuple[bool, bool, list[Transaction]]":
    """Return *txn*'s two delete facts and every row this press takes off the books.

    **ONE set, on either arm** (rulings **R-CC75**, **R-CC84**): *txn* and its
    live CC-payback chain -- every row whose payments and purchases leave the
    books in this press, which is what the ONE removal act runs over AND what
    it and the dialog are told is going.  A recurring row stays in the table
    as a tombstone holding nothing, and it is still GOING to the owner: they
    deleted it and the grid no longer shows it, so a creation record naming it
    is not reported as a row that stays (ruling **R-CC84**, developer
    2026-09-23: "The hidden row counts as leaving, like any deleted row").
    Until R-CC75 the act ran over the rows leaving the TABLE alone, so a
    soft-deleted row kept its movements and its matches (the module
    docstring's step 2 carries the measurement); R-CC75 made that two sets,
    and R-CC84 made them one again.

    **The payback chain goes either way**, because
    :func:`~app.services.credit_workflow.delete_payback_on_source_delete` hard-
    deletes every level whatever arm the source takes -- a projected payback
    that outlived a soft-deleted source would inflate the next period with no
    offsetting credit row.

    **Whether the row is one un-archiving would bring back is asked HERE,
    once** (ruling **R-CC86**; CC-5-4a-4's third review, L2): the dialog's
    read and the press both call this, so the value has one producer rather
    than a copy in each.  Read before anything is written, while the row's
    status is the one the owner saw.  It names un-archive's candidate set,
    and the dialog's words carry the exception (ruling **R-CC112**).

    Args:
        txn: The row being deleted.

    Returns:
        ``(soft, comes_back_on_unarchive, rows)`` -- whether the row stays as
        a tombstone, whether it is one archiving and then un-archiving its
        item would bring back unless the books have moved over it (a soft
        delete of a row still Projected), and every row whose movements go
        (*txn* first).
    """
    soft = txn.recurs
    return (
        soft,
        soft and is_projected(txn),
        [txn, *credit_workflow.live_payback_chain(txn)],
    )


def preview_deletion(
    txn: Transaction, *, last_row_of_definition: "bool | None" = None,
) -> RowDeletion:
    """Return what deleting *txn* WOULD take, without taking any of it.

    The read half of :func:`delete_transaction`, over the same row set, for the
    confirm dialog on a delete control.  Writes nothing.

    Args:
        txn: The row a screen is offering to delete.
        last_row_of_definition: Whether deleting *txn* would leave a
            rule-less definition with no row, when the caller has already
            asked (the card render asks once for this and for
            :func:`~._row_rules.deletion_refusal`); ``None`` asks here.

    Returns:
        Its :class:`RowDeletion`.  ``soft`` says which arm the press would
        take, so a dialog can promise the right thing about permanence, and
        ``disposes_definition`` whether the item goes with the row.
    """
    if last_row_of_definition is None:
        last_row_of_definition = definition_delete.is_last_row_of_its_definition(txn)
    soft, comes_back, rows = _leaves_the_books(txn)
    return RowDeletion(
        soft=soft,
        paybacks=tuple(row.name for row in rows[1:]),
        withdrawn=match_withdrawal.pending_for_rows(rows),
        purchase_ids=_purchase_ids(txn),
        disposes_definition=last_row_of_definition,
        comes_back_on_unarchive=comes_back,
    )


def _purchase_ids(txn: Transaction) -> "frozenset[int]":
    """Return the ids of *txn*'s own purchases -- the set a delete dialog names.

    Args:
        txn: The row being deleted, or offered for it.

    Returns:
        ``Transaction.purchases``' ids.
    """
    return frozenset(entry.id for entry in txn.purchases)


def _refuse_unnamed_purchases(
    txn: Transaction, purchases_named: "frozenset[int]",
) -> "frozenset[int]":
    """Refuse a delete whose row holds other purchases than its dialog named.

    Ruling **R-CC131** (developer 2026-10-04, "Refuse and redraw"):
    *"The dialog also sends back the purchases it named. At 10:02 they
    differ, so nothing is deleted and the popover redraws under your 'Redraw
    all' ruling ... You decide with it in view."*  Equality both ways, as the
    bank-line comparison is (ruling **R-CC127**): a purchase added since the
    dialog was drawn would leave the books unnamed, and one named and gone was
    removed elsewhere.

    **What the refusal says.**  It opens with ruling **R-CC128**'s banner
    words ("nothing was saved because the page was out of date") as every
    out-of-date press does, then names the purchases the page did not -- by
    description and day, never an id (ruling **R-CC98**), and only those, so
    a swap reads as what changed -- and counts the named ones that are gone.
    Their figures are not spelled here: the redrawn card's purchase list
    prints each through the app's one money formatter (the ``money`` macro),
    and a second spelling of it in a sentence would print a refund as
    ``$-28.29``.

    Args:
        txn: The row being deleted.
        purchases_named: The purchase ids its dialog posted back -- owner
            input, compared as a set and never used as a scope.

    Returns:
        The row's purchase ids, as read once for the comparison -- what the
        press then reports it took.

    Raises:
        PageOutOfDate: When the two sets differ.
    """
    purchase_ids = _purchase_ids(txn)
    if purchase_ids == purchases_named:
        return purchase_ids
    facts = "Nothing was saved: this page was out of date."
    unnamed = [
        entry for entry in txn.purchases if entry.id not in purchases_named
    ]
    if unnamed:
        listed = "; ".join(
            f"{entry.description}, {entry.purchased_on.month}/"
            f"{entry.purchased_on.day}"
            for entry in unnamed
        )
        facts += (
            f" {txn.name} now holds {len(unnamed)} purchase"
            f"{'' if len(unnamed) == 1 else 's'} the page did not name "
            f"({listed})."
        )
    gone = len(purchases_named - purchase_ids)
    if gone:
        facts += (
            f" {gone} purchase{'' if gone == 1 else 's'} the page named "
            f"{'is' if gone == 1 else 'are'} no longer under {txn.name}."
        )
    raise PageOutOfDate(facts)


def delete_transaction(
    txn: Transaction, owner_id: int, *, press: Press | None = None,
    purchases_named: "frozenset[int]" = frozenset(),
) -> RowDeletion:
    """Remove *txn* from the books, soft or hard, with everything it holds.

    The module docstring carries the order and why each step is where it is.

    Does NOT commit -- the caller owns the session boundary, which is what lets
    the whole sequence land atomically with whatever else that request writes.

    Args:
        txn: The row to delete.  Must still be flushed (``txn.id`` set) so the
            reversal entries can link by it and the match members can be read
            back.
        owner_id: The user the caller proved owns it, recorded on the events
            AND reconciled against the row (step 0 of the module docstring's
            order, finding **N-373**).
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            the bank lines the delete dialog named
            (:func:`preview_deletion`'s ``withdrawn``), posted back with it;
            the act refuses a press freeing other lines (plan step
            ``credit_card:CC-5-4a-5``, ruling **R-CC127**).  The match
            Undo's ``_release`` passes the default, ``None``: it deletes and
            flushes the act before it reaches here, so nothing is left to
            free.
        purchases_named: The row's purchases the dialog named
            (:func:`preview_deletion`'s ``purchase_ids``), posted back with
            the press (ruling **R-CC131**).  The default names none, which is
            the match Undo's truth too: it removes a created container's own
            purchases first and never reaches here for one holding another's
            (``statement_match._release._remove``).

    Returns:
        What the delete did, as :class:`RowDeletion` -- the same shape
        :func:`preview_deletion` returned for the same row.

    Raises:
        NotFoundError: When *owner_id* is not *txn*'s owner.  Both callers
            prove ownership before reaching here -- the route through its own
            helper, ``_release`` through a ``StatementMatch`` filtered on
            ``user_id`` AND ``account_id`` -- so this is defence in depth and
            not a live defect (finding **N-373** measured ``$0.00``).  What it
            stops being is a PERMISSION a third caller inherits.  The message
            is the row's id and nothing else, per the security response rule:
            "not yours" and "not found" read alike.
            **NEITHER caller catches it, deliberately** (CLAUDE.md rule 13): the
            route's own helper reads the SAME attribute off the SAME instance
            one line earlier with no write between, so an arm here would be
            handling a state that cannot occur.  A third caller that has not
            proved ownership gets a loud 500, which is the right answer to a
            bug in a door that deletes budget rows.
        ValidationError: When :func:`~._row_rules.deletion_refusal` names a
            reason this row may not be deleted on its own -- a transfer shadow
            or a CC payback.  It fires BEFORE anything is written, so a refused
            delete leaves the database exactly as it was.  Also when the
            lines the press frees differ from what its page named (ruling **R-CC127**):
            that one is raised after the reversal has flushed, and the
            route's rollback undoes the press.  And as
            :class:`~app.exceptions.PageOutOfDate` when the row holds other
            purchases than *purchases_named* (ruling **R-CC131**), before
            anything is written.
        PostingError: From the ledger reconcile, on a broken invariant.
    """
    if txn.user_id != owner_id:
        raise NotFoundError(f"Transaction {txn.id} not found.")

    # Asked ONCE and threaded: the refusal's third arm and step 5 below want
    # the same answer, and one request asks a producer once.
    last_row_of_definition = definition_delete.is_last_row_of_its_definition(txn)
    refusal = deletion_refusal(
        txn, last_row_of_definition=last_row_of_definition,
    )
    if refusal is not None:
        raise ValidationError(refusal)
    purchase_ids = _refuse_unnamed_purchases(txn, purchases_named)

    soft, comes_back, rows = _leaves_the_books(txn)
    paybacks = tuple(row.name for row in rows[1:])
    # The definition is read off the row BEFORE the row is deleted: the
    # relationship may not be loaded yet, and a lazy load on an instance the
    # session has already deleted is not a read this door may rely on.
    definition = txn.template if last_row_of_definition else None
    for row in rows:
        posting_service.reverse_postings_before_delete(row)
    # Over every row, on both arms (rulings R-CC75, R-CC84): a tombstone keeps
    # its place in the table and nothing it held, and counts as gone.
    withdrawn = movement_removal.remove_movements(
        [movement for row in rows for movement in row.entries],
        owner_id, because=match_withdrawal.LEFT_THE_BOOKS, press=press,
        rows_leaving=rows,
    )
    credit_workflow.delete_payback_on_source_delete(txn, owner_id)

    if soft:
        txn.is_deleted = True
    else:
        db.session.delete(txn)
    if definition is not None:
        # Step 5.  The act's own read autoflushes the row's DELETE first, so
        # its scan of the definition's non-settled rows finds none and what
        # is left to remove is the definition and its series.
        definition_delete.permanently_delete_definition(definition)
    return RowDeletion(
        soft=soft, paybacks=paybacks, withdrawn=withdrawn,
        purchase_ids=purchase_ids,
        disposes_definition=last_row_of_definition,
        comes_back_on_unarchive=comes_back,
    )
