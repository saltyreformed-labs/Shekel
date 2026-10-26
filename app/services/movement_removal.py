"""The ONE act that takes a payment or a purchase OFF THE BOOKS.

Plan step ``credit_card:CC-5-4a-3``, ruling **R-CC54** (developer
2026-09-22), verbatim in its first part: *"ONE act takes a payment or purchase
off the books: it reverses its ledger, takes it out of any match (withdrawing
a match left with nothing and saying which bank line that freed), then deletes
it.  Every door calls it, including the popover's two paths."*

**Why it is one act.**  A movement -- a purchase, or the covering movement a
settle wrote (ruling **R-BAL80**: the movement IS the money) -- was taken off
the books by each door's own spelling of the same three steps: the purchase
delete, the status seam's ``$0.00`` / ``purchases`` record, and the row,
payback and transfer deletes, each reversing the ledger and deleting in its
own code, and only some of them asking :mod:`app.services.match_withdrawal`.
The seam was one that did not (finding **CC-358**): a matched Paid bill
re-recorded at ``$0.00`` lost its payment while the act naming it kept its
bank line alone, the review screen and the day drill-down disagreed about
that line, and matching it again raised on
``uq_statement_match_members_line``.  A rule enforced by every door
remembering it is a rule the next door forgets; one act every door calls is
not.

**The order, and why each step is where it is** -- the ruling's own: reverse,
out of every match, delete.

1. **Reverse its ledger** (``posting_service.reverse_purchase_postings_before_delete``),
   while ``journal_entries.transaction_entry_id`` still links the legs -- it
   is ``ON DELETE SET NULL``, so reversing afterwards would strand them.
   **A row door reverses every leaving row's whole family FIRST**, before it
   calls this, ONE anchor re-check per row -- the delete verb over the row
   and its CC-payback chain and the payback teardowns over the payback
   (``posting_service.reverse_postings_before_delete``), the transfer delete
   over the pair through its own reconcile -- so this finds nothing left
   there: a movement already at zero emits nothing and re-checks nothing.
   It is the reversal for the doors that take a movement off a row that
   STAYS: a purchase, a payment a re-record withdraws.  Reversing a leaving
   row's movements one by one here instead would re-check the anchor once
   per movement, the churn ruling **R-BAL103** removed from the deploy
   resync.
2. **Out of every match** (:func:`app.services.match_withdrawal.take_out_of_matches`),
   while the movements still exist for their members to be read: an act left
   naming no app row is withdrawn with its freed lines logged, and one keeping
   another row loses this member and turns amber on the register.  Reversing
   first changes nothing it reads -- a reversal writes journal entries and
   touches neither a movement nor a member.
3. **Delete it** -- ``db.session.delete`` on the movement, then out of the
   list its parent loaded it into, so a reconcile that walks that list after
   the act returns (the settle verbs', the entry door's re-derivation) never
   meets a movement that is gone.  The list is asked of
   :func:`app.services.transfer_legs.parent_entries` since plan step
   ``balance:X-bi-6-4c-4``: its row's ``entries``, which for a transfer's
   payment is its SHADOW's until ``X-bi-6-4d`` re-parents the movement onto
   the transfer, so this act names no parent row itself and 6-4d re-points
   that one function.  **This is the ONLY delete of a movement there is**
   (plan step ``CC-5-4a-4``, ruling **R-CC64**): the row's ``entries``
   relationship carries no delete cascade and its database keys are
   ``NO ACTION``, so no row delete -- through the ORM or in bulk -- can take
   a movement with it.  DELETED, not merely removed: a child removed from a
   relationship with no ``delete-orphan`` and not deleted is NULLED at
   flush, which ``ck_transaction_entries_one_parent`` refuses (a movement with
   no parent; ``transaction_id``'s ``NOT NULL`` did until plan step
   ``balance:X-bi-6-4d-2``); deleted, the flush
   emits the ``DELETE`` alone whichever of the two came first (measured on
   SQLAlchemy 2.0.54, delete-then-remove here and remove-then-delete by
   CC-5-4a-4's first review).  The collection itself is read before either,
   so a lazy load's autoflush cannot land the ``DELETE`` under it first.
   **NOT flushed here**: a door that deletes the row
   next deletes it through the ORM, whose unit of work orders the
   movement's ``DELETE`` first; the transfer delete, whose shadows go by the
   database's cascade, flushes first itself, because a shadow's ``DELETE``
   landing before its movements' is now refused rather than cascaded; and
   the status seam runs this inside callers' ``no_autoflush`` blocks (the
   carry-forward batch), where a flush the old seam never made would write
   an intermediate state early.  Step 2 flushes only when a match named a
   movement.

**What it does NOT do** is anything about the ROW.  Whether the row goes, stays
as a tombstone, re-derives its figure or re-books its payback is the door's;
this act only takes movements off.  The re-point of a payment onto another
account is not a removal either -- the movement stays on the books -- so it
takes only step 2 (``match_withdrawal.withdraw_for_moved_movement``, ruling
**R-CC46**).

**The doors that are NOT row deletes do not reach a movement at all** (plan
step ``credit_card:CC-5-4a-4``, rulings **R-CC54**, **R-CC63**..**R-CC65**,
closing finding **CC-363**).  The template, transfer-template and account
permanent deletes destroyed movements by bulk statement or cascade, the
pay-period truncate / regenerate / reset by cascade, and the archives hid
them: a row holding a movement is now HISTORY each of those doors keeps --
the permanent deletes archive instead, the archives leave it live, the
pay-period doors refuse its period
(:mod:`app.utils.archive_helpers`' ``*_holding_movements`` family) -- and
the keys that let them try refuse instead.  The owner takes a movement off
the books by deleting it, or its row, through a door that calls this.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM rows in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
caller owns the unit of work; it flushes only as step 1's ledger writes and
step 2's match writes need.
"""

from __future__ import annotations

from app.extensions import db
from app.services import match_withdrawal, posting_service, transfer_legs
from app.services.match_press import Press
from app.services.match_withdrawal import MatchWithdrawal


def remove_movements(
    movements, owner_id: int, *, because: str, press: Press | None,
    rows_leaving=(),
) -> MatchWithdrawal:
    """Take *movements* off the books: reversed, out of their matches, deleted.

    The module docstring carries the order and why each step is where it is.
    *press* is REQUIRED, a keyword with no default (ruling **R-CC81**): a
    door taking a payment or purchase off the books says what its page named
    before the press, or what lets it stay silent, and step 2 refuses at once
    a line that page did not name (``match_withdrawal.take_out_of_matches``,
    rulings **R-CC127**, **R-CC135**); the press's close compares the save.
    The refusal comes after step 1's reversal has flushed, so it is the
    door's rollback that undoes the press -- as for every refusal raised
    inside the status seam.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        movements: The ``TransactionEntry`` rows leaving -- the WHOLE set one
            press removes, so a group act naming two of them is withdrawn once
            and the receipt is the one derivation the dialog printed.  Each
            must still be flushed (``id`` set) with its parent reachable.
        owner_id: The owner the caller proved owns them -- the OWNER, not
            necessarily the requester, so a companion's delete is filed under
            the books it changed.
        because: The withdrawal event's sentence
            (``match_withdrawal.LEFT_THE_BOOKS`` / ``RE_RECORDED``).
        press: The save's :class:`~app.services.match_press.Press` (ruling **R-CC135**), over
            what the door's page named or what lets it stay silent; ``None``
            when the door named nothing.
        rows_leaving: The rows the caller deletes in the same press, soft or
            hard (a recurring occurrence's tombstone counts, ruling
            **R-CC84**), when it is a row delete -- so a creation record naming
            one is not reported as kept.  Empty when no row is deleted.

    Returns:
        What the match step withdrew
        (:class:`~app.services.match_withdrawal.MatchWithdrawal`); all zeroes
        when no act named any of them, which is nearly every removal.

    Raises:
        ValidationError: When step 2 frees a line *press*'s page did not
            name.
    """
    movements = list(movements)
    for movement in movements:
        posting_service.reverse_purchase_postings_before_delete(movement)
    withdrawn = match_withdrawal.take_out_of_matches(
        movements, owner_id, because=because, press=press,
        rows_leaving=rows_leaving,
    )
    for movement in movements:
        # The collection is read FIRST: deleted before the collection is
        # loaded, the lazy load's autoflush would emit the DELETE first and
        # load a collection it is no longer in (step 2's flush expires it,
        # which is how a MATCHED purchase met that).  Then deleted AND out of
        # the collection -- removed without the delete, the flush would NULL
        # its ``transaction_id`` (the module docstring's step 3).
        family = transfer_legs.parent_entries(movement)
        db.session.delete(movement)
        family.remove(movement)
    return withdrawn
