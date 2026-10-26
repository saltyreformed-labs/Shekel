"""The transfer's hard delete takes EVERY entry the transfer holds (leaf ``X-bi-6-4d-1``).

The transfer's hard delete hands the one removal act every entry its transfer
holds, and since leaf ``X-bi-6-4d-1`` it asks
``transfer_legs.held_transfer_entries`` for them rather than walking each
shadow's ``entries`` -- so ``X-bi-6-4d``'s re-parent moves the collection with
the join.  The case grades the SCOPE that swap claims to keep, not the swap
itself (it passes on the old per-shadow walk too, by design).  Since plan step
``balance:X-bi-6-4d-2`` the scope is ANY record on either side of the
transfer, because that is what the side keys (NO ACTION, ruling **R-CC64**)
refuse the transfer's delete for; it was any entry under any shadow, live or
dead, under ``fk_transaction_entries_transaction_id`` until then.  A kept,
un-dated record a revert left behind counts: a collection narrowed to dated
movements, or to one side's, leaves such a record behind and the delete fails
at the flush.  The suite graded none of that before this leaf.

A second case hid both shadows over the two kept records first, until plan
step ``balance:X-bi-6-4d-2``'s vacuous-residue checkpoint deleted it (ruling
**R-BAL325**, the developer, 2026-10-19).  From that step's create door on a
transfer has no shadow to hide, so its two hides touched no row, and each half
of what was left is graded elsewhere: the database's refusal of a transfer
hidden holding a record is ``test_transfer_side_links.py``'s
``TestTheDeletedRowTransferArm::test_hiding_a_transfer_that_holds_a_record_is_refused_at_commit``,
and the hard delete after that refusal, which met the state the case below
builds, is the case below (measured there: with the refusal switched off the
first fails, and with the delete skipping the un-dated records, or either
side's, the second does).
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
)


def _a_reverted_transfer(seed_user, seed_periods):
    """A $250.00 checking -> Rainy Day transfer, settled then set back to Projected.

    The revert keeps each side's covering movement, un-dated (ruling
    **R-BAL61**), so the transfer still holds two entries and no dated one.
    They are read off the transfer's two side links (plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL88**; ruling **R-BAL167** class
    4), where they were read under its shadows until then.

    Returns:
        ``(transfer_id, the kept movements' ids)``.
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", "Rainy Day",
    )
    db.session.commit()
    transfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("250.00"),
    )
    db.session.commit()
    transfer_service.update_transfer(
        transfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()
    kept = (
        db.session.query(TransactionEntry)
        .filter(
            (TransactionEntry.expense_transfer_id == transfer.id)
            | (TransactionEntry.income_transfer_id == transfer.id),
        )
        .all()
    )
    assert len(kept) == 2
    assert all(m.covers_settlement and m.settled_on is None for m in kept)
    return transfer.id, {m.id for m in kept}


def _gone(transfer_id, movement_ids):
    """Assert the transfer and every one of *movement_ids* no longer exist."""
    db.session.expire_all()
    assert db.session.get(Transfer, transfer_id) is None
    assert (
        db.session.query(TransactionEntry)
        .filter(TransactionEntry.id.in_(movement_ids))
        .count()
    ) == 0


class TestTheHardDeleteTakesEveryEntryTheTransferHolds:
    """Kept un-dated records, a revert's, one on each side of the transfer."""

    def test_a_reverted_transfers_kept_records_leave_with_it(
        self, app, seed_user, seed_periods,
    ):
        """Hard-deleting the reverted $250.00 transfer takes both kept records."""
        with app.app_context():
            transfer_id, movement_ids = _a_reverted_transfer(
                seed_user, seed_periods,
            )

            transfer_service.delete_transfer(
                transfer_id, seed_user["user"].id, soft=False,
            )
            db.session.commit()

            _gone(transfer_id, movement_ids)
