"""
Shekel Budget App -- Transfer Service: the RESTORE verb

The inverse of a soft delete.  It was also the one place the transfer
invariants were REPAIRED rather than merely maintained -- a shadow that had
drifted from its parent while hidden was re-synced on the way back -- until
plan step ``balance:X-bi-6-4d-2`` deleted the shadows and its leaf 3b deleted
the repairs: a transfer is one row, so nothing beside it can drift.

It refuses before it moves (:func:`assert_restorable`, ruling **R-DR**), so a
restore onto an archived account leaves the transfer untouched rather than
half-restored.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging

from app.extensions import db
from app.services import posting_service
from app.services.transfer_service._loan_posting import (
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._validation import (
    _get_transfer_or_raise,
    assert_restorable,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RESTORED,
    log_event,
)

logger = logging.getLogger(__name__)


def restore_transfer(transfer_id, user_id):
    """Restore a soft-deleted transfer.

    This is the inverse of ``delete_transfer(soft=True)``: it sets
    ``is_deleted=False`` on the transfer.  It un-deleted both shadows too and
    re-synced their period, category, due date and override flag from the
    parent until plan step ``balance:X-bi-6-4d-2`` deleted the shadows (its
    leaf 3b deleted the loop).

    **It repairs no STATUS and no day, since plan step
    ``balance:X-bi-6-4d-2``.**  A shadow's status and day are no longer kept
    or read: the transfer's status is one column, and each side's day is its
    payment record's.  Nor is there a record to bring back: the soft delete
    took each side's records off the books (ruling **R-CC75**, "un-archiving
    brings a deleted occurrence back empty"), and the database refuses to
    hide a transfer still holding one.  **What comes back is a plan, a
    Cancelled transfer or a ``$0.00`` close**: the delete set a settled
    transfer whose records it took off back to Projected (ruling
    **R-BAL246**, "Back as a plan, now"), so a loan reads a planned payment
    rather than a missed installment; a Projected or Cancelled transfer
    keeps its status, and a ``$0.00`` close held nothing and comes back as
    it was.  Until that step this verb re-applied
    the pair's status through the seam, repairing a drifted shadow's status
    and borrowing its day from the sibling (ruling **R-BAL142**).

    Idempotent: calling on an already-active transfer is a no-op.

    Args:
        transfer_id: The primary key of the transfer to restore.
        user_id:     The expected owner (defense-in-depth).

    Returns:
        The restored (or already-active) Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If either the source or destination account has
            been archived (``is_active = False``) since the transfer was
            soft-deleted (F-164).  Reactivate the account before restoring.
    """
    # Must allow deleted transfers since that is the expected input.
    xfer = _get_transfer_or_raise(transfer_id, user_id, allow_deleted=True)

    # Idempotent: if the transfer is already active, return unchanged.
    # Matches the idempotent pattern of delete_transfer(soft=True).
    if not xfer.is_deleted:
        logger.debug(
            "restore_transfer called on active transfer %d; no-op.",
            transfer_id,
        )
        return xfer

    # ── Refuse before anything moves (X-aj1) ────────────────────────
    # Archived endpoints (F-164); the twin count and type pairing went with
    # the twins at plan step ``balance:X-bi-6-4d-2``.  Run BEFORE
    # the un-delete, which is a change from the code this replaced:
    # that version flipped ``is_deleted`` first and then hand-restored it on
    # each failing branch, so the rollback was written out three times and the
    # fourth check would have had to remember it too.
    assert_restorable(xfer, user_id)

    xfer.is_deleted = False

    db.session.flush()

    # ── Posting ledger reconcile (Build-Order Step 2) ──────────────
    # Re-sync the ledger to what the restored transfer's movements say, AFTER
    # the transfer is un-deleted (plan step
    # ``balance:X-bi-6-3``, ruling **R-BAL101**: the door reads the
    # movements, it is told no settled sense).  Since plan step
    # ``balance:X-bi-6-4d-2`` a hidden transfer holds no movement (ruling
    # **R-CC75**) and returns Projected, Cancelled or a ``$0.00`` close
    # (ruling **R-BAL246**), so this posts nothing.
    posting_service.sync_transfer_postings(xfer)
    # Posting ledger: re-reconcile the loan's genesis ledger -- its split
    # correction plus the opening / true-up corrections.  A restored
    # ``$0.00`` close is a settled loan payment holding no cash (ruling
    # **R-BAL140**), which moves the split; a restored plan, a restored
    # Cancelled payment or a non-loan transfer moves nothing.
    _sync_loan_postings_if_loan(xfer)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_RESTORED, BUSINESS,
        "Transfer restored from soft-delete",
        user_id=user_id,
        transfer_id=transfer_id,
    )
    return xfer
