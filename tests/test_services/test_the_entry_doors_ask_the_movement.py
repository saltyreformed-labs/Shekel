"""Plan step ``balance:X-bi-6-4c-4``: the entry doors ask the MOVEMENT, not its row.

``X-bi-6-4d`` re-parents a transfer's covering movements onto the transfer
(ruling **R-BAL88**), after which such a movement has no row at all.  The
purchase edit and delete doors read three things off the parent ROW that a
movement can answer for itself or through the one resolution of its parent
(``transfer_legs.movement_parent``), and each is moved here while the mirror
still holds, so 6-4d finds nothing to move in them:

* **ownership** reads the movement's own ``owner_id`` -- one value with the
  row's ``user_id`` by key (``fk_transaction_entries_owner_transaction``), so
  the case PLANTS the two apart around the key to show which one the door
  reads;
* **the payment-record refusal comes FIRST** in the edit door, as it always
  has in the delete door (ruling **R-BAL160**, a declared change): a crafted
  request re-pricing a Paid row's payment record was answered by the
  settled-row rule, which reads the row;
* **that refusal names the movement's parent** through ``movement_parent``,
  so a transfer's payment is named by its LEG's label -- the grid's, from the
  endpoints' current names -- not by a name stored when it was written (the
  shadow row's until plan step ``balance:X-bi-6-4d-2`` deleted it; ruling
  **R-BAL160**).
"""

from decimal import Decimal

import pytest

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, transaction_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    generate_row_of,
    make_expense_template,
    transfer_side_record,
    typed,
)


def _a_settled_bill(seed_user, period):
    """Settle a $148.32 Electric bill through the verb; return it and its payment.

    Returns:
        ``(row, covering_movement)``.
    """
    row = generate_row_of(
        make_expense_template(
            db.session, seed_user, amount="148.32", name="Electric",
            category_key="Rent",
        ),
        period,
    )
    transaction_service.settle_transaction(row)
    db.session.commit()
    (movement,) = row.covering_movements
    return row, movement


class TestOwnershipIsTheMovementsOwn:
    """Both doors read ``TransactionEntry.owner_id`` -- and nothing off the row.

    ``fk_transaction_entries_owner_transaction`` holds the movement's owner
    equal to its row's on every write, so each direction is PLANTED with the
    referential triggers off, inside the test's one transaction: the
    movement's owner moved away (the row's owner must get the 404), and the
    ROW's owner moved away (the movement's owner must still reach the
    refusals).  A door reading both columns would pass the first plant and
    fail the second; ``X-bi-6-4d`` needs the second, because a transfer's
    movement will have no row to read.
    """

    def _plant(self, sql, params):
        """Run *sql* with the referential triggers off, then turn them back on."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text(sql), params)
        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))
        db.session.expire_all()

    def _call(self, door, movement_id, user_id):
        """Ask *door* ("update" or "delete") to write the movement as *user_id*."""
        if door == "update":
            entry_service.update_entry(movement_id, user_id, description="Power")
        else:
            entry_service.delete_entry(movement_id, user_id)

    @pytest.mark.parametrize("door", ["update", "delete"])
    def test_the_movements_owner_moved_away_is_a_404_for_the_rows_owner(
        self, app, seed_user, seed_second_user, seed_periods, door,
    ):
        """The row's owner gets the 404 once the movement names someone else."""
        with app.app_context():
            _row, movement = _a_settled_bill(seed_user, seed_periods[0])
            movement_id = movement.id
            with pytest.raises(ValidationError):
                # The control: the row's owner reaches the refusals.
                self._call(door, movement_id, seed_user["user"].id)
            db.session.rollback()

            self._plant(
                "UPDATE budget.transaction_entries SET owner_id = :owner "
                "WHERE id = :id",
                {"owner": seed_second_user["user"].id, "id": movement_id},
            )

            with pytest.raises(NotFoundError):
                self._call(door, movement_id, seed_user["user"].id)
            assert db.session.get(TransactionEntry, movement_id) is not None

    @pytest.mark.parametrize("door", ["update", "delete"])
    def test_the_rows_owner_moved_away_changes_nothing_for_the_movements_owner(
        self, app, seed_user, seed_second_user, seed_periods, door,
    ):
        """The movement's owner still reaches the refusals when the ROW names another."""
        with app.app_context():
            row, movement = _a_settled_bill(seed_user, seed_periods[0])
            movement_id = movement.id

            self._plant(
                "UPDATE budget.transactions SET user_id = :owner WHERE id = :id",
                {"owner": seed_second_user["user"].id, "id": row.id},
            )
            # The plant landed: the refusal below is the same one the
            # unplanted state gives, so only this says the row moved away.
            assert db.session.get(Transaction, row.id).user_id == (
                seed_second_user["user"].id
            )

            with pytest.raises(ValidationError) as refused:
                self._call(door, movement_id, seed_user["user"].id)
            assert str(refused.value).startswith(
                "This is the payment record of Electric,"
            )


class TestAPaymentRecordIsRefusedFirst:
    """The edit door refuses a payment record before it weighs the settled row."""

    def test_repricing_a_paid_rows_payment_names_the_record_not_the_row(
        self, app, seed_user, seed_periods,
    ):
        """A crafted $1.00 figure for a Paid Electric bill's payment.

        Both refusals would refuse it; the one that answers says whose act it
        is.  Until plan step ``balance:X-bi-6-4c-4`` the settled-row rule
        answered first: "Electric has settled; its purchases are closed".
        """
        with app.app_context():
            _row, movement = _a_settled_bill(seed_user, seed_periods[0])

            with pytest.raises(ValidationError) as refused:
                entry_service.update_entry(
                    movement.id, seed_user["user"].id,
                    figure=typed(Decimal("1.00")),
                )

            message = str(refused.value)
            assert message.startswith("This is the payment record of Electric,")
            assert "has settled; its purchases are closed" not in message
            db.session.rollback()
            assert db.session.get(
                TransactionEntry, movement.id,
            ).amount == Decimal("148.32")


class TestATransfersPaymentIsNamedByItsLeg:
    """The refusal names a transfer's payment by the leg label the grid shows."""

    @pytest.mark.parametrize("door", ["update", "delete"])
    def test_the_label_reads_the_endpoints_current_names(
        self, app, seed_user, seed_periods, door,
    ):
        """A Paid $250.00 transfer checking -> Rainy Day, both endpoints since renamed.

        Checking is renamed "Everyday" and savings "Storm Fund" after the
        settle, the state a renamed account leaves: the transfer's stored name
        keeps the endpoint names it was written with, and each leg's label
        reads the endpoints' current names.  Each side's payment is then named
        by its own leg, through either door: the from-side's (on checking)
        "Transfer to Storm Fund" and never "Rainy Day", the to-side's (on
        savings) "Transfer from Everyday" and never the old checking name --
        the labels ``transfer_legs.leg_label`` composes.  Each payment is its
        side's record on the TRANSFER since plan step ``balance:X-bi-6-4d-2``
        (ruling **R-BAL88**), where it hung off the shadow until then (ruling
        **R-BAL167** class 4).  Until plan step ``balance:X-bi-6-4d-2``'s
        vacuous-residue checkpoint the stale name was planted on the shadow,
        which that step deleted, so the plant wrote no row and the case read
        one side's label no rename had moved.  Renamed from
        ``test_the_label_is_the_legs_not_the_shadows_stored_copy``.
        """
        with app.app_context():
            # Loaded into THIS session: the fixture's object is bound to
            # another, so a rename written on it would never be committed.
            checking = db.session.get(Account, seed_user["account"].id)
            old_checking_name = checking.name
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
            )
            db.session.commit()
            transfer = create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()
            checking.name = "Everyday"
            savings.name = "Storm Fund"
            db.session.commit()
            db.session.expire_all()
            assert transfer.name == f"{old_checking_name} to Rainy Day", (
                "the plant: the transfer's stored name is the one written at create"
            )

            for account, label, stale in (
                (checking, "Transfer to Storm Fund", "Rainy Day"),
                (savings, "Transfer from Everyday", old_checking_name),
            ):
                movement = transfer_side_record(
                    db.session, transfer.id, account.id,
                )
                with pytest.raises(ValidationError) as refused:
                    if door == "update":
                        entry_service.update_entry(
                            movement.id, seed_user["user"].id, description="x",
                        )
                    else:
                        entry_service.delete_entry(
                            movement.id, seed_user["user"].id,
                        )

                message = str(refused.value)
                assert message.startswith(
                    f"This is the payment record of {label},"
                ), (label, message)
                assert stale not in message, (label, message)


class TestTheAdviceIsTheParentsOwn:
    """Each parent's refusal advises the acts THAT parent has (leaf ``X-bi-6-4d-1``).

    A transfer's payment is corrected on the transfer -- its "Money moved
    on" box for that side and its Actual -- and a transfer has no purchases
    to be closed from, so the row's advice named acts no transfer has.  Both
    directions are graded, so neither parent can be handed the other's.
    """

    def test_a_transfers_payment_is_advised_onto_the_transfer(
        self, app, seed_user, seed_periods,
    ):
        """A Paid $250.00 transfer checking -> Rainy Day: the transfer's own words.

        The payment is the from-side's record on the TRANSFER since plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**), where it was read off
        the Checking shadow until then (ruling **R-BAL167** class 4).
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
            movement = transfer_side_record(
                db.session, transfer.id, seed_user["account"].id,
            )

            with pytest.raises(ValidationError) as refused:
                entry_service.delete_entry(movement.id, seed_user["user"].id)

            message = str(refused.value)
            assert message.startswith(
                "This is the payment record of Transfer to Rainy Day, "
                "written when that transfer was marked paid."
            ), message
            assert (
                'change it on the transfer itself, through its "Money moved '
                f'on" day for {seed_user["account"].name} and its Actual'
            ) in message
            assert "purchases" not in message
            assert "settle day" not in message

    def test_a_rows_payment_keeps_the_rows_advice(
        self, app, seed_user, seed_periods,
    ):
        """A Paid $148.32 Electric bill: the row's settle day and its purchases."""
        with app.app_context():
            _, movement = _a_settled_bill(seed_user, seed_periods[0])

            with pytest.raises(ValidationError) as refused:
                entry_service.delete_entry(movement.id, seed_user["user"].id)

            message = str(refused.value)
            assert message.startswith(
                "This is the payment record of Electric, written when that "
                "row was marked paid."
            ), message
            assert "edit the row's settle day" in message
            assert "closing the row from its own purchases" in message
            assert "Money moved on" not in message
