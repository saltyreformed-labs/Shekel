"""
Shekel Budget App -- Transfer Service: the DELETE verb

Removing a transfer, soft or hard.  It removed both shadow
:class:`~app.models.transaction.Transaction` rows with it -- Transfer Invariant
2, that a shadow is never orphaned, applied in the one direction that could
orphan one -- until plan step ``balance:X-bi-6-4d-2`` deleted the shadows; its
leaf 3b deleted the delete's last query and flags over them.

The ORDER inside is the whole of the module: every record the pair holds
goes off the books through the one removal act -- out of its matches, then
deleted -- and is flushed before the transfer goes, because the side keys are
NO ACTION and a transfer still holding a record refuses its own delete; and a
soft delete that took a settled transfer's payments off returns it to a plan
only once it is hidden (ruling **R-BAL246**, through
``_status.return_to_plan``), because the books' revert refusal does not reach
a hidden row (ruling **R-BAL248**).  Until plan task plan#3097 that act also
reversed the pair's stored postings while the rows still existed to link
against and re-checked the anchor corrections once the records were gone
(ruling **plan#2564**); the loan-payment split links no row (ruling
**R-BAL102**) and was re-derived after the payment was gone until plan task
plan#3075 deleted that re-sync (the loan's walk reads its remaining payments
as they stand).

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import (
    match_withdrawal,
    movement_removal,
    status_seam,
    transfer_legs,
)
from app.services.transfer_service._status import return_to_plan
from app.services.transfer_service._validation import _get_transfer_or_raise
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_HARD_DELETED,
    EVT_TRANSFER_SOFT_DELETED,
    log_event,
)

logger = logging.getLogger(__name__)


def delete_transfer(transfer_id, user_id, soft=False, *, press=None):
    """Delete a transfer.

    Args:
        transfer_id: The primary key of the transfer to delete.
        user_id:     The expected owner (defense-in-depth).
        soft:        If True, set is_deleted=True on the transfer -- a
                     tombstone that keeps its place and
                     none of its sides' payment records (ruling R-CC75);
                     a settled transfer that held one goes back to
                     Projected, so a restore brings back a plan, while a
                     ``$0.00`` close keeps its status (ruling R-BAL246).
                     If False, physically remove the transfer.
        press:       The save's press over the bank lines the door's page
                     named before the delete, on either arm, or ``None``
                     (rulings R-CC127, R-CC135, R-BAL229).  Every caller
                     passes the default, ``None``: no page captions this
                     delete, and they reach only a pair holding no payment
                     (ruling R-CC65), save a hand-built request to the
                     instance DELETE no template renders -- refused if it
                     would free a line.

    Returns:
        The soft-deleted Transfer if soft=True, or None if hard-deleted.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
    """
    # allow_deleted=True so that idempotent soft-delete and hard-delete
    # of already-soft-deleted transfers continue to work.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # ── Statement matches (developer ruling 2026-08-25, bank_import:X-gb) ──
    # A payment a bank line was matched to stops existing when its transfer
    # is deleted, so an act it was the last app row of is withdrawn and that
    # line is unexplained again.  Through the ONE act that takes a movement off
    # the books (plan step ``credit_card:CC-5-4a-3``, ruling **R-CC54**): both
    # sides' records out of their matches and deleted (the act also reversed
    # the pair's stored postings and re-checked the anchor corrections after,
    # from plan task plan#2557, ruling **plan#2564**, until plan task
    # plan#3097) -- and FLUSHED before the
    # transfer goes.  Since plan step ``balance:X-bi-6-4d-2`` the records hang
    # off the TRANSFER by side keys that are NO ACTION (ruling **R-CC64**), so
    # a transfer still holding one REFUSES its own delete, and an order that
    # went wrong here would fail loud rather than cascade; until then they
    # hung off the shadows, whose cascade the transfer's delete fires
    # (measured on the developer's own dev database at 16 matched shadows).
    #
    # **A SOFT delete takes them off too** (plan step ``balance:X-bi-6-4d-2``,
    # finding **BAL-532**; ruling **R-CC75** for a row's occurrence, "Same as a
    # one-off": "Deleting the occurrence takes its payments and purchases off
    # the books through the one removal act ... un-archiving brings a deleted
    # occurrence back empty").  Until then a soft delete withdrew nothing and
    # a hidden occurrence KEPT its sides' payments and their matches; the
    # database now refuses to hide a transfer still holding one
    # (``app/deleted_row_infrastructure``'s transfer arm), so the act runs on
    # both arms.  A matched record frees its line only where the press NAMED
    # it, else nothing saves (ruling **R-BAL229**): no page captions this
    # delete today, so a matched record refuses it.  The template archive
    # soft-deletes only transfers holding nothing
    # (``archive_helpers.transfer_holds_nothing``), so for it the act takes
    # nothing off.
    #
    # **Which movements go is asked of ``transfer_legs``** (leaf
    # ``balance:X-bi-6-4d-1``): every record either side of the transfer
    # holds by its side key -- the scope the side keys, NO ACTION on delete,
    # would refuse the delete for -- through the one join.  It read
    # ``shadow.entries`` per shadow until ``X-bi-6-4d-1``, and the entries
    # under any shadow live or dead until ``X-bi-6-4d-2`` moved the join onto
    # the side links.  In movement-id order, so the act walks them
    # deterministically.
    held = (
        transfer_legs.held_transfer_entries(Transfer.id == transfer_id)
        .order_by(TransactionEntry.id)
        .all()
    )
    movement_removal.remove_movements(
        held, user_id, because=match_withdrawal.LEFT_THE_BOOKS, press=press,
    )
    db.session.flush()

    if soft:
        xfer.is_deleted = True
        # **A settled transfer whose payments just came off returns as a
        # PLAN** (ruling **R-BAL246**, "Back as a plan, now"); a ``$0.00``
        # close held nothing and keeps Paid.  After the hide, because the
        # books' revert refusal does not reach a hidden row (ruling
        # **R-BAL248**, "Plan, kept deleted"); the hard arm below removes
        # the row and has no status to set.
        return_to_plan(xfer, took_off=bool(held))
        if held:
            # The figure leaves with the records (the build plan's O4): a
            # settled transfer's return to plan released it through the
            # writer, and a Projected or Cancelled one holding records a
            # revert kept is not moved by that writer, so the delete releases
            # it here.
            status_seam.release_side_figure(xfer)
        db.session.flush()
        log_event(
            logger, logging.INFO, EVT_TRANSFER_SOFT_DELETED, BUSINESS,
            "Transfer soft-deleted",
            user_id=user_id,
            transfer_id=transfer_id,
        )
        result = xfer
    else:
        # Hard delete.  It counted the twins the ``transactions.transfer_id``
        # CASCADE should have taken, and logged any left behind, until plan
        # step ``balance:X-bi-6-4d-2`` deleted the twins and made the database
        # refuse a new one (ruling **R-BAL258**): there is nothing left to
        # orphan.
        db.session.delete(xfer)
        db.session.flush()

        log_event(
            logger, logging.INFO, EVT_TRANSFER_HARD_DELETED, BUSINESS,
            "Transfer hard-deleted",
            user_id=user_id,
            transfer_id=transfer_id,
        )
        result = None

    # The loan a deleted payment leaves owes it nothing: the loan's walk reads
    # its payment set as the transfers INTO it, as they stand, so the later
    # payments' splits and any true-up it moved are worked out again on the
    # next read.  The stored ledger's re-sync of that loan stood here until
    # plan task plan#3075.
    return result
