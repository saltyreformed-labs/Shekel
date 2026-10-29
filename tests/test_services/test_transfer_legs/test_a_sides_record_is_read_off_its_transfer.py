"""A transfer side's record is read off its transfer's list for that side, and stored nowhere else.

Ruling **plan#1984** (finding **BAL-649**, plan#1780): ``TransferLeg.record``
was a field the loader filled, so ``None`` meant both "this side holds no
record" and "the loader did not ask".  It is a property over the transfer's
list for the side (``transfer_legs.side_movements``) since, so "not asked" is
not a state a leg can hold: a read finds the list a loader pre-loaded or loads
it.  These cases pin that, the one-statement pre-load that replaced
``covering_movements_by_leg``, and the fill a leg built from its own movement
makes.
"""

from datetime import date

import pytest
from sqlalchemy import inspect

from app import ref_cache
from app.enums import StatusEnum
from app.models.transaction_entry import TransactionEntry
from app.services.transfer_legs import (
    TransferLeg,
    leg_of,
    movement_parent,
    planned_transfer_legs,
    preload_side_records,
    recorded_transfer_legs,
    transfer_side_leg,
)
from app.services.transfer_legs._leg import side_movements_key
from app.services.transfer_service import update_transfer
from tests._test_helpers import (
    capture_sql_statements,
    create_settled_transfer,
    create_transfer,
    transfer_side_record,
)

from ._helpers import _AMOUNT, _savings


class TestASideReadsItsRecordOffItsTransfer:
    """No loader can hand a reader a side whose record it never asked for."""

    def test_a_reverted_sides_kept_record_reaches_the_folds_own_loader(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """BAL-649's case: the fold's plan loader never asked, and still reads the kept record.

        A transfer Paid on 02-03 and reverted keeps each side's record,
        un-dated (ruling **R-BAL61**).  ``planned_transfer_legs`` emits its
        sides (the transfer is Projected) and pre-loads nothing; its
        Checking side carried ``None`` until ruling plan#1984 -- the same
        value as a side holding no record -- and reads the kept record now,
        the one the independent SQL finds.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            reverted = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            update_transfer(
                reverted.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            db.session.expire_all()

            [leg] = planned_transfer_legs(
                checking.id, seed_user["scenario"].id, options=(),
            )

            kept = transfer_side_record(db.session, reverted.id, checking.id)
            assert kept is not None and kept.settled_on is None
            assert leg.record is kept
            assert leg.covering_movements == (kept,)
            assert leg.settled_on is None

    def test_a_side_holding_none_reads_none(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A planned transfer's side holds no record: ``None``, not a refusal or a guess."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            planned = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()

            assert leg_of(planned, checking.id).record is None
            assert leg_of(planned, savings.id).covering_movements == ()


class TestThePreloadReadsEverySideInOneStatement:
    """``preload_side_records``: the batch form of one side's lazy read."""

    def test_two_transfers_four_sides_one_statement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Four sides' lists load in ONE statement; every read after it issues none.

        Two transfers -- one settled (a record on each side), one planned
        (none) -- after the session forgot them.  The pre-load is one
        SELECT; reading all four sides' records then is zero, and each is the
        record the independent SQL finds (``None`` for the planned ones).
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            planned = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            db.session.commit()
            db.session.expire_all()
            transfers = [settled, planned]
            for xfer in transfers:
                assert xfer.id is not None  # refresh the expired rows first

            _none, statements = capture_sql_statements(
                lambda: preload_side_records(transfers),
            )
            assert len(statements) == 1

            legs = [
                leg_of(xfer, account.id)
                for xfer in transfers for account in (checking, savings)
            ]
            records, statements = capture_sql_statements(
                lambda: [leg.record for leg in legs],
            )
            assert statements == []
            assert records == [
                transfer_side_record(db.session, leg.transfer.id, leg.account_id)
                for leg in legs
            ]
            assert [record is None for record in records] == [
                False, False, True, True,
            ]

    def test_a_loaded_list_is_left_as_the_session_holds_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A list already loaded is the session's truth: the pre-load leaves it, and a second record is refused.

        The Checking side's list is loaded, then holds a second, unflushed
        entry the database would refuse
        (``uq_transaction_entries_one_expense_side_record``).  The pre-load
        finds both lists loaded and issues no statement, so it cannot
        overwrite the list with what is stored; and the side's record is
        refused as two, as ``status_seam.covering_movement_of`` refuses a
        row's second covering movement.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            with db.session.no_autoflush:
                settled.income_movements  # pylint: disable=pointless-statement
                settled.expense_movements.append(TransactionEntry())

                _none, statements = capture_sql_statements(
                    lambda: preload_side_records([settled]),
                )

                assert statements == []
                assert len(settled.expense_movements) == 2
                with pytest.raises(ValueError, match=(
                    rf"Transfer {settled.id}'s from-side holds 2 payment records"
                )):
                    _ = transfer_side_leg(settled, is_income=False).record
            db.session.rollback()


class TestALegBuiltFromItsMovementHoldsIt:
    """``recorded_transfer_legs`` and ``movement_parent`` fill the side's list with the movement in hand."""

    def test_the_settled_half_reads_its_legs_records_with_no_query(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Each leg the settled half builds reads its movement with no statement of its own."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            for period in (seed_periods[2], seed_periods[3]):
                create_settled_transfer(
                    seed_user, db.session, checking, savings, period,
                    amount=_AMOUNT, settled_on=period.start_date,
                )
            db.session.commit()
            db.session.expire_all()

            legs = recorded_transfer_legs(
                TransactionEntry.account_id == checking.id,
            )
            records, statements = capture_sql_statements(
                lambda: [leg.record for leg in legs],
            )

            assert len(legs) == 2
            assert statements == []
            assert records == [
                transfer_side_record(db.session, leg.transfer.id, checking.id)
                for leg in legs
            ]

    def test_a_movement_staged_for_delete_fills_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fill states only what the database would answer, so a staged delete leaves the list to load.

        The Checking side's record is staged for delete while its list is
        unloaded.  ``movement_parent`` still answers the side's leg, but the
        list stays unloaded: filled with the movement, it would answer a
        record the next flush removes, where a read that loads the list lands
        the delete first (its autoflush) and answers what is stored.  (The
        one removal act loads the list BEFORE it stages a delete and takes
        the movement out of it, ``movement_removal.remove_movements``.)
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            db.session.expire_all()
            movement = transfer_side_record(db.session, settled.id, checking.id)
            key = side_movements_key(is_income=False)
            db.session.delete(movement)

            leg = movement_parent(movement)

            assert leg == TransferLeg(
                transfer=settled, account_id=checking.id, is_income=False,
            )
            assert key in inspect(settled).unloaded
            db.session.rollback()
