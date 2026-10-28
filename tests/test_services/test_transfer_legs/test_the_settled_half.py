"""The settled half: a paid leg's money through one loader, and the ledger writer's answer to what
a movement's parent is.
"""

from datetime import date
from decimal import Decimal

from app.models.transfer import Transfer
from app.services.cash_ledger import settled_cash_facts
from app.services.transfer_legs import (
    TransferLeg,
    movement_parent,
    recorded_transfer_legs,
    transfer_family_movements,
    transfer_movement_rows,
)
from tests._test_helpers import (
    create_settled_cash_transaction,
    create_settled_transfer,
    create_transfer,
    side_records_by_key,
)

from ._helpers import _AMOUNT, _savings


class TestTheSettledHalfReadsTheLegOffItsTransfer:
    """Leaf ``balance:X-bi-6-4a`` (ruling **R-BAL106**): a paid leg's money through ONE loader."""

    def test_recorded_legs_carry_their_transfer_side_and_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One leg per covering movement: the side is the link's, the account its endpoint.

        What a leg moved is its transfer's settled figure, stored once on the
        transfer since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half; the
        leg's record carried it until then and states none now.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            db.session.commit()

            legs = recorded_transfer_legs(Transfer.from_account_id == checking.id)

            assert [
                (leg.transfer.id, leg.account_id, leg.is_income,
                 leg.record.account_id, leg.transfer.settled_figure,
                 leg.record.settled_on)
                for leg in legs
            ] == [
                (settled.id, checking.id, False, checking.id,
                 Decimal("240.00"), date(2026, 2, 3)),
                (settled.id, savings.id, True, savings.id,
                 Decimal("240.00"), date(2026, 2, 3)),
            ]
            assert all(
                leg.record.covers_settlement and leg.pay_period_id == settled.pay_period_id
                for leg in legs
            )

    def test_a_legs_fact_names_its_transfer_and_a_rows_names_none(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The fold's two arms: a leg's fact carries ``transfer_id``, signed by its side."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [out_fact] = settled_cash_facts(checking.id, scenario_id)
            [in_fact] = settled_cash_facts(savings.id, scenario_id)

            assert (out_fact.transfer_id, out_fact.is_income, out_fact.delta) == (
                settled.id, False, -_AMOUNT,
            )
            assert (in_fact.transfer_id, in_fact.is_income, in_fact.delta) == (
                settled.id, True, _AMOUNT,
            )
            assert out_fact.pay_period_id == settled.pay_period_id


class TestTheWritersParentIsTheLeg:
    """Leaf ``balance:X-bi-6-4a``'s second half: the ledger writer's parent resolution.

    The writer asks ONE function what a movement's parent is
    (``movement_parent``) and walks a transfer's family through ONE loader
    (``transfer_family_movements``).  The answer reads the movement's side
    links in Python (``transfer_legs._side_of``), the twin of the join's SQL
    expressions, so these cases pin the twin to the join.  Until plan step
    ``balance:X-bi-6-4d-2`` they also pinned it over a hidden shadow and a
    stray entry under a live one, and pinned the one dated-leg clause its
    three readers share (the plan half, the loan settled half and the deploy
    resync); those cases went there with the shadows (ruling **R-BAL313**).
    """

    def test_a_rows_movement_answers_its_row_and_a_transfers_its_leg(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A plan row's movement is booked under the row; a transfer's under its LEG."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            row = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[2], Decimal("30.00"),
                account=checking, name="a row",
            )
            db.session.commit()
            [row_movement] = row.entries
            out_movement = side_records_by_key(settled)[
                (settled.id, checking.id)
            ]

            assert movement_parent(row_movement) is row
            leg = movement_parent(out_movement)
            assert leg == TransferLeg(
                transfer=settled, account_id=checking.id, is_income=False,
            )
            assert leg.record is out_movement
            assert leg.user_id == seed_user["user"].id

    def test_the_python_twin_agrees_with_the_join(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``movement_parent`` against ``transfer_movement_rows`` over a whole family.

        Two settled ``$250.00`` Checking -> Savings transfers.  The family
        loader (``transfer_family_movements``, each movement's leg resolved
        by ``movement_parent`` off its side links in Python) returns all four
        movements, and the four it gives a record are exactly the four the
        ONE join (``transfer_movement_rows``, its side read in SQL) returns,
        each on the side the join says; the second transfer's from-side
        movement is its leg's record, on Checking; and each family is walked
        in movement order.  Renamed from
        ``test_the_python_twin_agrees_with_the_join_live_and_dead_shadows_alike``:
        until plan step ``balance:X-bi-6-4d-2`` the second transfer's
        Checking shadow was soft-deleted by SQL first, and the case read
        that the side under the hidden twin still kept its record, which
        neither implementation reads off a twin (ruling **R-BAL167** class
        3).  A transfer came with two shadow rows until
        that step made it one row (ruling **R-BAL258**), so the hide and its
        two reads went: the shadow's own soft-delete, and the movement naming
        no row, which ``ck_transaction_entries_one_parent`` makes true of
        every side record.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            first = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            second = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT, settled_on=date(2026, 2, 17),
            )
            db.session.commit()
            db.session.expire_all()
            transfers = [db.session.get(Transfer, t.id) for t in (first, second)]

            joined = {
                movement.id: is_income
                for movement, _transfer, is_income in transfer_movement_rows(
                    Transfer.id.in_([first.id, second.id]),
                )
            }
            family = [
                pair for transfer in transfers
                for pair in transfer_family_movements(transfer)
            ]

            assert (len(family), len(joined)) == (4, 4)
            assert {
                movement.id for movement, leg in family
                if leg.record is movement
            } == set(joined)
            assert all(
                leg.is_income == joined[movement.id]
                for movement, leg in family if movement.id in joined
            )
            [(second_out, second_out_leg)] = [
                (movement, leg) for movement, leg in family
                if leg.transfer.id == second.id and not leg.is_income
            ]
            assert (
                second_out_leg.record, second_out_leg.is_income,
                second_out_leg.account_id,
            ) == (second_out, False, checking.id)
            for transfer in transfers:
                ids = [m.id for m, _leg in transfer_family_movements(transfer)]
                assert ids == sorted(ids), "a family is walked in movement order"
