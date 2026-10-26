"""The delete door, transfer_service.delete_transfer: one case per state and kind of delete (soft
or hard), what each takes off and what it keeps, and the ownership refusal on a live or an
already hidden transfer.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services import transfer_service
from app.services.transfer_legs import planned_transfer_legs
from app.exceptions import NotFoundError
from tests._test_helpers import transfer_side_record, typed

from ._helpers import _create_basic_transfer, _reread


# ── Delete Tests ───────────────────────────────────────────────────


class TestDeleteTransfer:
    """``transfer_service.delete_transfer``, one case per state and kind of delete.

    A delete case is set by two things: the transfer's state before the
    delete (planned, paid, or already hidden) and which delete runs (a soft
    delete HIDES it, a hard delete removes it for good).  Each case checks
    what that delete must leave behind (ruling R-BAL290 part 2, the
    developer, 2026-10-14).  The group has been laid out this way since plan
    step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2a, which merged two groups
    into it: ``TestSoftDeleteHandling`` (the already-hidden cases) and
    ``TestADeleteReturnsAPaidTransferToAPlan`` (the paid cases).

    - PLANNED: hidden (:meth:`test_soft_hides_the_transfer`), or removed
      (:meth:`test_hard_removes_the_transfer`).
    - PAID: hidden.  The delete that takes a Paid transfer's payment records
      off (ruling credit_card:R-CC75) also sets it back to Projected, so a
      restore brings back a plan, never a ``$0.00`` close, which a loan reads
      as a missed installment (ruling R-BAL246, the developer 2026-10-09,
      "Back as a plan, now";
      :meth:`test_a_paid_transfer_comes_off_as_a_hidden_plan_and_restores_as_one`).
      The set-back has two edges: a ``$0.00`` close held nothing and keeps
      Paid (:meth:`test_a_zero_close_held_nothing_and_keeps_paid`), and a
      Cancelled transfer whose kept records came off stays Cancelled
      (:meth:`test_a_cancelled_transfer_whose_kept_records_came_off_stays_cancelled`).
      A paid transfer REMOVED for good is graded in two other files and is
      not written again here: ``test_transfer_posting_lifecycle.py``'s
      ``TestDeleteAndRestore::test_hard_delete_settled_reverses_and_pairs_survive_null_links``
      (the row is gone, its posted legs reversed) and
      ``test_cc5_4a3_movement_removal.py``'s
      ``TestEveryDoorWithdrawsTheActItself`` case
      ``test_a_hard_transfer_delete_withdraws_the_act_naming_its_leg`` (the
      statement match its payment was in is withdrawn).  That the row
      is gone is the first one's alone: a hard delete that kept a transfer
      whose sides held a payment failed it and no case in this file
      (measured at checkpoint 2e-2b-2a).
    - ALREADY HIDDEN: hidden again
      (:meth:`test_delete_soft_deleted_transfer_is_idempotent`), or removed
      (:meth:`test_hard_delete_soft_deleted_transfer_succeeds`, a planned
      transfer hidden first; a PAID one hidden and then removed is
      ``test_transfer_posting_lifecycle.py``'s ``TestDeleteAndRestore`` case
      ``test_hard_delete_of_a_soft_deleted_pair_finds_the_ledger_at_zero``).
    - Someone else's transfer, live or already hidden: not found, on either
      delete, and re-read after a COMMIT still there and still live or
      still hidden (:meth:`test_wrong_user_rejected`,
      :meth:`test_someone_elses_hidden_transfer_is_not_found`).  No transfer
      at all: not found, on the hard delete
      (:meth:`test_nonexistent_rejected`).

    Every case here holds an AD-HOC transfer.  The shape production hides
    is a transfer of a definition (the instance delete hides a linked
    transfer and removes an ad-hoc one; the template archive hides only
    linked ones), and a hide that skipped a linked transfer passes this
    group: it is graded at the route tier, by ``test_routes/test_transfers/``
    ``test_instance.py``'s ``test_delete_template_transfer_soft_deletes``
    among others (measured at checkpoint 2e-2b-2a's review).

    Editing a hidden transfer answers not found too, which is an EDIT
    refusal: :class:`TestUpdateTransfer`'s
    ``test_update_soft_deleted_transfer_raises_not_found``.  The paid cases
    read each side's record by its own SQL
    (:func:`tests._test_helpers.transfer_side_record`), never the producer.
    """

    @staticmethod
    def _sides(td, xfer_id):
        """Each side's ``(figure, settled_on)``, or ``None``, from Checking then Savings.

        ``None`` for a side holding no record.  A side holding one pairs the
        figure the TRANSFER stores, the figure's one home since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each record carried a copy until then, with the
        day its record holds.
        """
        db.session.expire_all()
        figure = db.session.get(Transfer, xfer_id).settled_figure
        sides = []
        for account in (td["account"], td["savings_account"]):
            record = transfer_side_record(db.session, xfer_id, account.id)
            sides.append(
                None if record is None else (figure, record.settled_on),
            )
        return tuple(sides)

    def test_soft_hides_the_transfer(self, app, transfer_data):
        """A planned transfer hidden is flagged deleted, stays Projected, and is returned.

        What the delete wrote, re-read from the database (ruling R-BAL290
        part 1): the flag, and the status a delete leaves alone on a
        transfer whose sides held no payment (read since checkpoint
        2e-2b-2a's review: a delete that cancelled a hidden plan passed this
        file).  The door returns the session's own row, the one it hid, by
        identity, so the returned object's flag is the stored one.  It
        also asked each endpoint's plan loader for a leg of the transfer,
        before the delete and after it, until plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2a.  That a hidden transfer
        leaves both accounts' plans is the LOADER's fact, whichever delete
        hid it, and it is asked of this case's own stored state (saved,
        ad-hoc, Projected, hidden, no side holding a record) on BOTH sides by
        :meth:`test_delete_soft_deleted_transfer_is_idempotent` below, and on
        the expense side alone by ``test_transfer_legs/test_the_plan_half.py``'s
        ``TestTransferLegs::test_a_settled_a_cancelled_and_a_deleted_parent_yield_no_leg``,
        which asks Checking's plan only (measured at that checkpoint: a
        loader letting a hidden transfer through on the income side alone
        fails the first and passes the second; on the expense side alone it
        fails both).  Renamed from
        ``test_soft_hides_the_transfer_and_both_legs``, and before that
        ``test_soft_marks_shadows_deleted``.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id

            hidden = transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True,
            )

            row = _reread(xfer_id)
            assert hidden is row, "the door returns the session's row, the one it hid"
            # Hidden, and still a plan: only a delete that took a payment
            # off a SETTLED transfer moves its status (ruling R-BAL246).
            assert (row.is_deleted, row.status_id) == (
                True, ref_cache.status_id(StatusEnum.PROJECTED),
            )

    def test_hard_removes_the_transfer(self, app, db, transfer_data):
        """Hard delete removes the transfer row.

        It also collected the transfer's two shadow rows and asserted each
        was gone, until plan step ``balance:X-bi-6-4d-2``; from that step's
        create door on the transfer holds none, so the collection was empty
        and the loop over it ran over none (a vacuous read, measured at its
        checkpoint 2e-2a-2), and it is dropped.  Renamed from
        ``test_hard_removes_shadows``.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transfer, xfer_id) is None

    def test_a_paid_transfer_comes_off_as_a_hidden_plan_and_restores_as_one(
        self, app, db, transfer_data,
    ):
        """Paid with $250.00 on both sides: deleted Projected, restored Projected, empty."""
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            transfer_service.update_transfer(
                xfer_id, td["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            paid_sides = self._sides(td, xfer_id)
            assert [side[0] for side in paid_sides] == [
                Decimal("250.00"), Decimal("250.00"),
            ], "precondition: the Paid transfer holds both $250.00 records"

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is True
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._sides(td, xfer_id) == (None, None)

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is False
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert self._sides(td, xfer_id) == (None, None)

    def test_a_zero_close_held_nothing_and_keeps_paid(
        self, app, db, transfer_data,
    ):
        """Paid at $0.00 (no record either side): deleted Paid, restored Paid."""
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            paid_id = ref_cache.status_id(StatusEnum.DONE)
            transfer_service.update_transfer(
                xfer_id, td["user"].id, status_id=paid_id,
            )
            transfer_service.update_transfer(
                xfer_id, td["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            assert self._sides(td, xfer_id) == (None, None), (
                "precondition: a $0.00 close keeps no record"
            )

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is True
            assert xfer.status_id == paid_id

            transfer_service.restore_transfer(xfer_id, td["user"].id)
            db.session.commit()

            assert db.session.get(Transfer, xfer_id).status_id == paid_id
            assert self._sides(td, xfer_id) == (None, None)

    def test_a_cancelled_transfer_whose_kept_records_came_off_stays_cancelled(
        self, app, db, transfer_data,
    ):
        """Only a SETTLED transfer is set back: a Cancelled one keeps its status.

        Paid, reverted (each side keeps its record un-dated and the transfer
        its $250.00 figure, ruling R-BAL61), then cancelled: the delete takes
        both records off, and the transfer it hides is still Cancelled.  The
        case a set-back keyed on the records alone would move to Projected.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            for status in (StatusEnum.DONE, StatusEnum.PROJECTED, StatusEnum.CANCELLED):
                transfer_service.update_transfer(
                    xfer_id, td["user"].id, status_id=ref_cache.status_id(status),
                )
            db.session.commit()
            assert self._sides(td, xfer_id) == (
                (Decimal("250.00"), None), (Decimal("250.00"), None),
            ), "precondition: the cancelled transfer kept both records un-dated"

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            xfer = db.session.get(Transfer, xfer_id)
            assert xfer.is_deleted is True
            assert xfer.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert self._sides(td, xfer_id) == (None, None)

    def test_delete_soft_deleted_transfer_is_idempotent(self, app, transfer_data):
        """Hiding a hidden transfer again does not raise; it stays hidden and out of both plans.

        A repeated soft delete must not break the template deactivation
        workflow, which may process the same transfers more than once.  The
        one copy of a pair: the delete group's ``test_soft_delete_idempotent``
        made the same two deletes, asked only the flag on the transfer the
        second one returned, and was deleted at plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2a (ruling R-BAL290 part 2,
        "the stronger check survives"), where this case moved into the delete
        group from ``TestSoftDeleteHandling``.  What the second delete left
        is read off the row re-read from the database since then (ruling
        R-BAL290 part 1), where it read the object the door returned, and off
        each endpoint's plan loader, a PROJECTION read that holds a leg of
        the transfer on both sides before the first delete (a guard on the
        input, moved here from the planned case,
        :meth:`test_soft_hides_the_transfer`, at that checkpoint) and none
        after the second.

        It read the shadow rows' flags afterwards ("still soft-deleted")
        until plan step ``balance:X-bi-6-4d-2``; from that step's create door
        on no row can name the transfer (``ck_transactions_names_no_transfer``,
        ruling R-BAL258), so ``all()`` ran over none after the second delete
        (a vacuous read; a probe counted such rows 0 before the first delete
        and after the second, at checkpoint 2e-2a-2).
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            endpoints = (xfer.from_account_id, xfer.to_account_id)
            for account_id in endpoints:
                assert [
                    leg.transfer.id for leg in planned_transfer_legs(
                        account_id, td["scenario"].id, options=(),
                    )
                ] == [xfer_id], "precondition: each endpoint plans its leg"

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            # Second call must not raise.
            again = transfer_service.delete_transfer(
                xfer_id, td["user"].id, soft=True
            )

            row = _reread(xfer_id)
            assert again is row, "the door returns the session's row, the one it hid"
            assert row.is_deleted is True
            for account_id in endpoints:
                assert planned_transfer_legs(
                    account_id, td["scenario"].id, options=(),
                ) == []

    def test_hard_delete_soft_deleted_transfer_succeeds(self, app, db, transfer_data):
        """A hidden transfer removed for good is gone.

        The hard-delete path must work regardless of the is_deleted flag.
        The one copy of a pair: the delete group's
        ``test_hard_on_already_soft_deleted`` was this case line for line and
        was deleted at plan step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2a
        (ruling R-BAL290 part 2), where this case moved into the delete group
        from ``TestSoftDeleteHandling``.

        It also counted the shadow rows naming the transfer afterwards (0)
        until plan step ``balance:X-bi-6-4d-2``; from that step's create door
        on no row can name one (``ck_transactions_names_no_transfer``, ruling
        R-BAL258), so the count it took after the last delete read none (a
        vacuous read; a probe counted such rows 0 before the first delete and
        after the last, at checkpoint 2e-2a-2), and it is dropped.
        """
        with app.app_context():
            td = transfer_data
            xfer = _create_basic_transfer(td)
            xfer_id = xfer.id
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=False)
            db.session.commit()
            db.session.expire_all()

            assert db.session.get(Transfer, xfer_id) is None

    @pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
    def test_wrong_user_rejected(self, app, db, transfer_data, second_user, soft):
        """A non-owner's delete of the owner's LIVE transfer is not found and leaves it live.

        On either delete: the service refuses someone else's transfer itself,
        whether or not any caller reaches the door with one today (the
        defense in depth the door's ``user_id`` is for).  The case ran the
        hard delete alone until plan step ``balance:X-bi-6-4d-2``'s
        vacuous-residue checkpoint, where a delete door letting a non-owner's
        soft delete of a live transfer through, as if its owner had asked,
        failed no test in the suite (measured there).  The
        refused delete is read off the row re-read after a COMMIT, which
        writes whatever it staged: the transfer is still there and still
        live.  The owner's same delete then lands on the same id, so the
        refusal is the ownership check's and not a wrong id's.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            db.session.commit()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.delete_transfer(
                    xfer_id, second_user["user"].id, soft=soft,
                )

            row = _reread(xfer_id)
            assert row is not None and row.is_deleted is False, (
                "the refused delete removed or hid the owner's transfer"
            )

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=soft)
            # The hard delete removes it; the soft delete leaves it in place.
            assert (_reread(xfer_id) is None) is not soft

    @pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
    def test_someone_elses_hidden_transfer_is_not_found(
        self, app, db, transfer_data, second_user, soft,
    ):
        """A non-owner's delete of the owner's HIDDEN transfer is not found and leaves it hidden.

        The delete reaches a hidden transfer on purpose (``allow_deleted``,
        so a repeated hide and a hard delete of a hidden transfer work), so
        its ownership check must hold for a hidden row, on either delete, as
        it does for a live one (:meth:`test_wrong_user_rejected`, on either
        delete since plan step ``balance:X-bi-6-4d-2``'s vacuous-residue
        checkpoint, where it gained the soft delete).  The refused
        delete is read off the row re-read after a COMMIT, which writes
        whatever it staged: the transfer is still there and still hidden.
        The owner's same delete then lands on the same id, so the refusal is
        the ownership check's and not a wrong id's.
        """
        with app.app_context():
            td = transfer_data
            xfer_id = _create_basic_transfer(td).id
            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=True)
            db.session.commit()

            with pytest.raises(NotFoundError, match="not found"):
                transfer_service.delete_transfer(
                    xfer_id, second_user["user"].id, soft=soft,
                )

            row = _reread(xfer_id)
            assert row is not None and row.is_deleted is True, (
                "the refused delete removed or un-hid the owner's transfer"
            )

            transfer_service.delete_transfer(xfer_id, td["user"].id, soft=soft)
            # The hard delete removes it; the soft delete leaves it in place.
            assert (_reread(xfer_id) is None) is not soft

    def test_nonexistent_rejected(self, app, db, transfer_data):
        """Delete of non-existent transfer raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                transfer_service.delete_transfer(
                    99999, transfer_data["user"].id
                )
