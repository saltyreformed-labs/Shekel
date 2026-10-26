"""What a transfer pair already RECORDS is read by SIDE, not by account (leaf ``X-bi-6-4d-1``).

The settle and the update read the pair's record -- the retained correction a
re-settle honours, the figure an echoed Actual box is compared against -- off
its expense side, through ``transfer_legs`` (``TransferRows.expense_leg``).
An update may move an endpoint AND settle in one call (``update_transfer``'s
contract; ``_endpoints._apply_endpoint_move`` names the case), and the move
assigns the transfer's account RELATIONSHIP, whose ``from_account_id`` column
reads the OLD account until a flush.  A read keyed by that column found the
record only when some lazy load happened to autoflush first; with the shadows'
``entries`` already loaded, nothing did, and the settle raised.  Keyed by the
side -- the shadow's type through the interval, the side link from
``X-bi-6-4d`` -- the read names no account at all.  The leaf's adversarial
review measured the regression (1 failed at its cp1, 3 passed on the base);
this is that case.

Which side each read takes is graded directly too
(:class:`TestEachSideReadsItsOwnRecord`): on every door-written state both
sides share one figure and one source (the transfer's, since plan step
``balance:X-bi-6-4d-2``'s 4d-4 half; each side's record carried a copy until
then), so the settle and the update cannot tell the sides apart, and the leaf's delta review measured a mutant reading
the INCOME side for both passing the full suite.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import transfer_service
from app.services.transfer_legs import transfer_side_leg
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    typed,
)


class TestEachSideReadsItsOwnRecord:
    """``transfer_side_leg`` answers the side it is asked for, record and all."""

    def test_the_expense_side_is_the_source_and_the_income_side_the_destination(
        self, app, seed_user, seed_periods,
    ):
        """$250.00 checking -> Rainy Day, Paid: each side's leg holds its own account's movement.

        The two covering movements are distinct rows on distinct accounts, so
        a read that took the wrong side answers the other account's movement.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()

            expense = transfer_side_leg(transfer, is_income=False)
            income = transfer_side_leg(transfer, is_income=True)

            assert (expense.is_income, income.is_income) == (False, True)
            assert expense.record.account_id == seed_user["account"].id
            assert income.record.account_id == savings.id
            assert expense.record.id != income.record.id


class TestAMoveAndSettleHonoursTheRetainedCorrection:
    """One call re-points the source and settles; the typed $214.37 still books."""

    def test_the_move_and_settle_with_the_entries_loaded(
        self, app, seed_user, seed_periods,
    ):
        """$250.00 checking -> Rainy Day, corrected to $214.37, reverted, then moved and settled.

        Both sides' record lists are loaded before the update, so no lazy
        load in the move autoflushes the re-pointed account.  The settle must
        still find the expense side's kept record and honour the typed
        $214.37 kept beside it on both legs, the source side now booked on the
        new account.  The figure is the TRANSFER's, its one home since plan
        step balance:X-bi-6-4d-2's 4d-4 half; each side's record carried it
        until then and states none now.
        The records hang off the TRANSFER by their side links since plan step
        balance:X-bi-6-4d-2, so the lists loaded are the transfer's
        (``expense_movements`` / ``income_movements``) and each side's record
        is read back by its link; they were each shadow's ``entries`` until
        then (ruling R-BAL167 class 4).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            other = create_account_of_type(
                seed_user, db.session, "Checking", "Second Checking",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()
            transfer_service.update_transfer(
                transfer.id, user_id, figure=typed(Decimal("214.37")),
            )
            db.session.commit()
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            for side_records in (
                transfer.expense_movements, transfer.income_movements,
            ):
                assert len(list(side_records)) == 1

            transfer_service.update_transfer(
                transfer.id, user_id,
                from_account_id=other.id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            moved = db.session.get(Transfer, transfer.id)
            assert moved.from_account_id == other.id
            records = {
                record.account_id: record
                for record in db.session.query(TransactionEntry).filter(
                    (TransactionEntry.expense_transfer_id == transfer.id)
                    | (TransactionEntry.income_transfer_id == transfer.id),
                )
            }
            assert set(records) == {other.id, savings.id}
            assert records[other.id].expense_transfer_id == transfer.id
            for record in records.values():
                assert (
                    record.expense_transfer or record.income_transfer
                ).settled_figure == Decimal("214.37")
                assert record.settled_on is not None
