"""The grid's legs: a leg's record is its covering movement, the loaders draw the cash-flow set's
side, and the producers price, value and date a leg beside the rows.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.exceptions import AmountUnresolvable
from app.services.cash_flow_set import (
    CashFlowSet,
    PlanItems,
    leg_accounts_shown,
    own_rows_clause,
    set_transfer_legs,
    set_transfer_legs_in_periods,
    touched_transfers_clause,
)
from app.services.cash_ledger import (
    amounts_by_key,
    contribution_of,
    contributions_by_key,
    settled_amounts_by_key,
)
from app.services.row_valuation import (
    fixed_contribution,
    settled_contribution,
    settled_figure,
)
from app.services.transaction_service import retained_amounts_by_key
from app.services.transfer_legs import (
    TransferLeg,
    cell_key,
    covering_movements_by_leg,
    grid_transfer_leg,
    grid_transfer_legs,
    parent_of,
)
from app.services.transfer_service import (
    delete_transfer,
    settle_transfer,
    update_transfer,
)
from app.utils.balance_predicates import is_projected_clause
from app.services.account_resolver import resolve_cash_flow_set
from tests._test_helpers import (
    basis_for,
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    transfer_side_settle_day,
    typed as typed_figure,
)

from ._helpers import _AMOUNT, _savings, _cancelled_transfer


def _closed_at_zero(seed_user, session, period):
    """Return the $250.00 checking -> savings transfer marked Paid at $0.00, and both its legs.

    Closed through the settle door with a typed ``$0.00``, which stores no
    record on either side (rulings **R-BAL82**, **R-BAL141**); each leg is
    loaded by the grid's own producer, the expense side first.
    """
    checking = seed_user["account"]
    savings = _savings(seed_user)
    closed = create_transfer(
        seed_user, session, checking, savings, period, amount=_AMOUNT,
    )
    session.commit()
    settle_transfer(
        closed.id, seed_user["user"].id,
        submitted=typed_figure(Decimal("0.00")),
    )
    session.commit()
    return closed, [
        grid_transfer_leg(closed, checking.id),
        grid_transfer_leg(closed, savings.id),
    ]


# ── The grid's legs (plan step balance:X-bi-6-1, ruling R-BAL87) ────────
#
# The leaf that widened this module's value from the fold's plan half to the
# grid's whole view of a transfer: a leg carries its RECORD (the covering
# movement, reached through the one join), the grid loader draws one leg per
# side the cash-flow set shows, and three producers price a leg beside the
# rows' maps.  Each case below is the state the byte-identity harness could
# not distinguish on the production restore.


class TestALegsRecordIsItsCoveringMovement:
    """``covering_movements_by_leg`` and the leg it hangs off."""

    def test_a_settled_transfers_legs_carry_their_movements(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Each side's record is the covering movement on ITS account, dated.

        What the records moved is their transfer's settled figure, stored
        once on the transfer since plan step ``balance:X-bi-6-4d-2``'s 4d-4
        half; each record carried it until then and states none now.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            projected = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            db.session.commit()

            records = covering_movements_by_leg([settled.id, projected.id])

            assert set(records) == {
                (settled.id, checking.id), (settled.id, savings.id),
            }
            for account in (checking, savings):
                movement = records[(settled.id, account.id)]
                assert movement.account_id == account.id
                assert movement.covers_settlement is True
                assert (
                    movement.expense_transfer or movement.income_transfer
                ).settled_figure == Decimal("240.00")
                assert movement.settled_on == date(2026, 2, 3)
            assert covering_movements_by_leg([]) == {}

    def test_a_soft_deleted_transfer_holds_no_legs_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settled transfer's soft delete takes both sides' records with it.

        The delete takes each side's payment off the books (ruling
        **R-CC75**, finding **BAL-532**), and the database refuses a hidden
        transfer still holding one (``app/deleted_row_infrastructure``'s
        transfer arm), so the join finds no record of it.  Until plan step
        ``balance:X-bi-6-4d-2`` the movements stayed under the hidden
        shadows and the join's "the shadow is live" term was what left them
        no leg's.  Renamed from
        ``test_a_deleted_shadows_movement_is_not_a_legs_record``.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            assert len(covering_movements_by_leg([settled.id])) == 2

            delete_transfer(settled.id, seed_user["user"].id, soft=True)
            db.session.commit()

            assert covering_movements_by_leg([settled.id]) == {}

    def test_grid_transfer_leg_loads_one_leg_with_its_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
            )
            db.session.commit()

            leg = grid_transfer_leg(settled, savings.id)

            assert leg == TransferLeg(
                transfer=settled, account_id=savings.id, is_income=True,
                record=leg.record,
            )
            assert leg.record is not None and leg.record.account_id == savings.id
            assert leg.record.settled_on == date(2026, 2, 3)
            assert leg.cell_key == (settled.id, savings.id)


class TestTheGridLoaderDrawsTheSetsSide:
    """``grid_transfer_legs`` under ``leg_accounts_shown`` (ruling R-CC23)."""

    def test_a_transfer_with_one_endpoint_in_the_set_shows_from_that_side(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            checking_set = CashFlowSet.single(checking)
            savings_set = CashFlowSet.single(savings)

            assert leg_accounts_shown(checking_set, transfer) == (checking.id,)
            assert leg_accounts_shown(savings_set, transfer) == (savings.id,)
            on_checking = grid_transfer_legs(
                [transfer], lambda t: leg_accounts_shown(checking_set, t),
            )
            assert [(leg.account_id, leg.is_income) for leg in on_checking] == [
                (checking.id, False),
            ]

    def test_a_transfer_between_two_members_shows_once_from_the_balance_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both endpoints in the set: the balance line's leg alone (the far leg is not drawn)."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            elsewhere = _savings(seed_user, name="Elsewhere")
            db.session.commit()
            both = CashFlowSet(balance=savings, members=(checking, savings))

            assert leg_accounts_shown(both, transfer) == (savings.id,)
            legs = grid_transfer_legs(
                [transfer], lambda t: leg_accounts_shown(both, t),
            )
            assert [(leg.account_id, leg.is_income) for leg in legs] == [
                (savings.id, True),
            ]
            # A transfer touching no member draws nowhere.
            assert leg_accounts_shown(CashFlowSet.single(elsewhere), transfer) == ()
            assert grid_transfer_legs(
                [transfer], lambda t: leg_accounts_shown(CashFlowSet.single(elsewhere), t),
            ) == []

    def test_both_endpoints_in_the_set_and_the_balance_line_on_neither_draws_nowhere(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The rule's fourth arm, reproduced: a checking -> B payment seen from A.

        Members ``(checking, A, B)`` with the balance line on A: both
        endpoints are members and neither is the balance account.  The row
        rule this replaced (``far_leg_clause``, deleted at leaf X-bi-6-1b)
        dropped both shadows there, so the payment was drawn NOWHERE on A's
        grid; the leg rule answers the same.  The first
        cut answered ``(A,)`` and ``leg_of`` refused it -- a 500 on the grid
        for every paycheck holding a payment to the other card (found by the
        leaf's adversarial review).  Whether "nowhere" is what R-CC23 means
        here is an open question for the developer, recorded at X-bi-6-1.
        """
        with app.app_context():
            checking = seed_user["account"]
            card_a = _savings(seed_user, name="Card A")
            card_b = _savings(seed_user, name="Card B")
            payment = create_transfer(
                seed_user, db.session, checking, card_b, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            seen_from_a = CashFlowSet(
                balance=card_a, members=(checking, card_a, card_b),
            )

            assert leg_accounts_shown(seen_from_a, payment) == ()
            assert grid_transfer_legs(
                [payment], lambda t: leg_accounts_shown(seen_from_a, t),
            ) == []


class TestTheLegMapProducers:
    """The three leg-keyed maps beside their row twins, over the three states a leg can be in."""

    def test_a_projected_leg_is_priced_and_records_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            leg = grid_transfer_leg(transfer, checking.id)
            key = leg.cell_key

            assert amounts_by_key(
                [leg], basis_for(checking, seed_user["scenario"]),
            ) == {key: _AMOUNT}
            assert settled_amounts_by_key([leg]) == {key: None}
            assert retained_amounts_by_key([leg]) == {key: None}

    def test_both_legs_of_a_settled_transfer_record_its_figure_beside_the_plan_and_retain_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The recorded figure is the transfer's, not the plan's, on both sides.

        ``settled_amounts_by_key`` values each leg by its parent through
        ``row_valuation.settled_figure``, which reads
        ``Transfer.settled_figure``.  Named
        ``test_a_settled_leg_records_its_movements_figure`` until plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half, when each side's record held the
        figure.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            legs = [
                grid_transfer_leg(settled, checking.id),
                grid_transfer_leg(settled, savings.id),
            ]

            assert settled_amounts_by_key(legs) == {
                (settled.id, checking.id): Decimal("240.00"),
                (settled.id, savings.id): Decimal("240.00"),
            }
            # What the amount IS is the PLAN, unconditionally -- the budget
            # map's rule (``amounts_by_key``, ruling E-21) -- so the cell can
            # strike the estimate through beside the record.
            assert amounts_by_key(legs, basis_for(checking, seed_user["scenario"])) == {
                (settled.id, checking.id): _AMOUNT,
                (settled.id, savings.id): _AMOUNT,
            }
            assert retained_amounts_by_key(legs) == {
                (settled.id, checking.id): None, (settled.id, savings.id): None,
            }

    def test_a_settled_leg_with_no_movement_is_the_zero_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Ruling R-BAL82's state on a leg: settled, no covering movement, ``$0``, on both sides.

        The transfer is closed at ``$0.00`` through its door
        (:func:`_closed_at_zero`).  Until plan step ``balance:X-bi-6-4d-2``'s
        vacuous-residue checkpoint the case hand-built the EXPENSE side's
        leg, record ``None``, over a transfer settled at $250.00 with a
        record on each side; it read no income side, and the cp2e-2b-2b
        review's LOW 2 found no case that read a ``$0.00`` close's income
        side on a destination that does not amortize.
        """
        with app.app_context():
            closed, legs = _closed_at_zero(seed_user, db.session, seed_periods[2])

            assert [leg.record for leg in legs] == [None, None]
            assert settled_amounts_by_key(legs) == {
                (closed.id, closed.from_account_id): Decimal("0"),
                (closed.id, closed.to_account_id): Decimal("0"),
            }

    def test_a_reverted_leg_retains_a_stated_figure_and_not_a_resolved_one(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A typed correction outlives the revert; the plan's own price does not.

        ``honoured_correction`` of the leg's transfer: only a STATED record is
        honoured (ruling R-BAL61), the one rule a row and a transfer are asked
        through (ruling plan#1983).
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            owner_id = seed_user["user"].id
            typed = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            resolved = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT, settled_on=date(2026, 2, 17),
            )
            db.session.commit()
            for transfer in (typed, resolved):
                update_transfer(
                    transfer.id, owner_id,
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                )
            db.session.commit()

            typed_leg = grid_transfer_leg(typed, checking.id)
            resolved_leg = grid_transfer_leg(resolved, checking.id)

            assert typed_leg.record is not None and typed_leg.record.settled_on is None
            assert retained_amounts_by_key([typed_leg, resolved_leg]) == {
                typed_leg.cell_key: Decimal("240.00"),
                resolved_leg.cell_key: None,
            }
            assert settled_amounts_by_key([typed_leg, resolved_leg]) == {
                typed_leg.cell_key: None, resolved_leg.cell_key: None,
            }


class TestTheLegContributionTwins:
    """The per-leg valuation twins leaf X-bi-6-1b gave the display readers.

    ``settled_figure`` / ``fixed_contribution`` / ``settled_contribution``
    asked of a leg's parent (ruling **plan#1983**; leaf X-bi-6-1b's leg twins
    of them until then), and ``contribution_of`` / ``contributions_by_key``
    of a leg's parent (the same ruling; ``contribution_of`` /
    ``contributions_by_key`` until then), over the four states a
    display leg can be in: planned, settled with a movement, settled with none
    (the $0.00 record), and excluded.
    """

    def test_a_planned_leg_is_worth_its_parents_plan_and_records_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            leg = grid_transfer_leg(transfer, checking.id)
            basis = basis_for(checking, seed_user["scenario"])

            assert settled_figure(parent_of(leg)) is None
            assert fixed_contribution(parent_of(leg)) is None
            assert contribution_of(parent_of(leg), basis) == _AMOUNT
            assert contributions_by_key([leg], basis) == {leg.cell_key: _AMOUNT}
            with pytest.raises(AmountUnresolvable):
                settled_contribution(parent_of(leg))

    def test_a_settled_from_side_leg_is_worth_the_transfers_figure_and_not_its_plan(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The from-side leg contributes the transfer's settled $240.00, not the plan's $250.00.

        Formerly ``test_a_settled_leg_is_worth_its_record_and_not_its_plan``:
        a side's record holds no figure since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half, and the case builds the from-side
        leg alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
                settled_on=date(2026, 2, 3),
            )
            db.session.commit()
            leg = grid_transfer_leg(settled, checking.id)
            basis = basis_for(checking, seed_user["scenario"])

            assert settled_figure(parent_of(leg)) == Decimal("240.00")
            assert fixed_contribution(parent_of(leg)) == Decimal("240.00")
            assert settled_contribution(parent_of(leg)) == Decimal("240.00")
            assert contribution_of(parent_of(leg), basis) == Decimal("240.00")
            assert amounts_by_key([leg], basis) == {leg.cell_key: _AMOUNT}

    def test_a_settled_leg_with_no_movement_is_the_zero_record(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A ``$0.00`` close's legs are worth ``$0``, figure and contribution, on both sides.

        Built and read as :class:`TestTheLegMapProducers`' case of the same
        name is, and changed with it at the same step: it hand-built the
        expense side's leg alone until then.
        """
        with app.app_context():
            _, legs = _closed_at_zero(seed_user, db.session, seed_periods[2])
            basis = basis_for(seed_user["account"], seed_user["scenario"])

            assert [leg.record for leg in legs] == [None, None]
            for leg in legs:
                assert settled_figure(parent_of(leg)) == Decimal("0")
                assert settled_contribution(parent_of(leg)) == Decimal("0")
                assert contribution_of(parent_of(leg), basis) == Decimal("0")

    def test_an_excluded_leg_is_worth_zero_whatever_it_remembers(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A Cancelled parent's leg contributes 0 and is not refused."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            cancelled = _cancelled_transfer(
                seed_user, checking, savings, seed_periods[2],
            )
            db.session.commit()
            leg = grid_transfer_leg(cancelled, checking.id)

            assert settled_figure(parent_of(leg)) is None
            assert fixed_contribution(parent_of(leg)) == Decimal("0")
            assert settled_contribution(parent_of(leg)) == Decimal("0")
            assert contribution_of(
                parent_of(leg), basis_for(checking, seed_user["scenario"]),
            ) == Decimal("0")


class TestALegsSettleDayAndTimeliness:
    """``settled_on`` and ``days_paid_before_due`` read the leg's MOVEMENT."""

    def test_a_settled_leg_reads_its_movements_day_and_agrees_with_the_row(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The leg's day is the movement's, and the arithmetic is the row's.

        Parity with the shadow row was a real check rather than one producer
        read twice: the row's ``settled_on`` was its own column and the leg's
        the covering movement's, two stored days the seam wrote together.
        Since plan step ``balance:X-bi-6-4d-2`` no writer keeps a twin's day
        (ruling **R-BAL88**), so the parity is with the side's RECORD, read
        by its own SQL, and the row's arithmetic -- the due date less the
        settle day -- is spelled here (ruling **R-BAL167** class 1).
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, settled_on=date(2026, 2, 3),
                due_date=date(2026, 2, 6),
            )
            db.session.commit()
            leg = grid_transfer_leg(settled, checking.id)
            side_day = transfer_side_settle_day(
                db.session, settled.id, checking.id,
            ).day

            assert leg.settled_on == date(2026, 2, 3)
            assert leg.days_paid_before_due == 3
            assert leg.days_paid_before_due == (settled.due_date - side_day).days
            assert leg.tracks_purchases is False

    def test_a_planned_and_an_undated_leg_answer_none(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            planned = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT, due_date=date(2026, 2, 6),
            )
            undated = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT, settled_on=date(2026, 2, 17),
            )
            db.session.commit()
            planned_leg = grid_transfer_leg(planned, checking.id)
            undated_leg = grid_transfer_leg(undated, checking.id)

            assert planned_leg.settled_on is None
            assert planned_leg.days_paid_before_due is None
            assert undated_leg.settled_on == date(2026, 2, 17)
            assert undated_leg.days_paid_before_due is None


class TestTheSetsTransferHalf:
    """``touched_transfers_clause``, ``set_transfer_legs`` and ``PlanItems``."""

    def test_the_clause_selects_a_transfer_touching_any_member_and_no_other(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            other = _savings(seed_user, name="Other")
            touching = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            elsewhere = create_transfer(
                seed_user, db.session, savings, other, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            selected = (
                db.session.query(Transfer.id)
                .filter(touched_transfers_clause(cash_flow)).all()
            )
            assert {row.id for row in selected} == {touching.id}
            assert elsewhere.id not in {row.id for row in selected}

    def test_set_transfer_legs_draws_the_sets_side_and_plan_items_derives_items(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            legs = set_transfer_legs(cash_flow, [transfer])
            assert [(leg.transfer.id, leg.account_id, leg.is_expense) for leg in legs] == [
                (transfer.id, checking.id, True),
            ]
            rows = db.session.query(Transaction).filter(
                own_rows_clause(cash_flow), Transaction.is_deleted.is_(False),
            ).all()
            items = PlanItems.of(rows, legs)
            assert items.items == [*rows, *legs]
            assert [cell_key(item) for item in items.items] == [
                *[row.id for row in rows], (transfer.id, checking.id),
            ]


    def test_both_endpoints_members_draws_the_balance_lines_leg_from_either_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-CC23 from BOTH balance lines: the from-side's expense leg on
        checking's line, the to-side's income leg on the card's (the two
        cases ``TestPaycheckRowsClause`` graded as rows until leaf X-bi-6-1b)."""
        with app.app_context():
            checking = seed_user["account"]
            card = _savings(seed_user, name="Card")
            payment = create_transfer(
                seed_user, db.session, checking, card, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            from_checking = CashFlowSet(balance=checking, members=(checking, card))
            from_card = CashFlowSet(balance=card, members=(checking, card))

            assert [(leg.account_id, leg.is_expense) for leg in set_transfer_legs(
                from_checking, [payment],
            )] == [(checking.id, True)]
            assert [(leg.account_id, leg.is_income) for leg in set_transfer_legs(
                from_card, [payment],
            )] == [(card.id, True)]

    def test_one_endpoint_in_the_set_shows_from_that_endpoint_even_off_the_balance_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """R-CC23's second arm under a set of TWO, through the resolver.

        A savings -> card transfer is drawn on the CARD, a member that is not
        the balance line; a checking -> savings one on checking.  Sets of
        one cannot grade this arm (the one member IS the line), and the
        rejected option 3 -- hide every transfer leg on a non-balance member
        -- passes them; the deleted row case graded it, and this is its leg
        twin (the second review of leaf X-bi-6-1b).
        """
        with app.app_context():
            checking = seed_user["account"]
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            savings = _savings(seed_user)
            into_card = create_transfer(
                seed_user, db.session, savings, card, seed_periods[2],
                amount=Decimal("300.00"),
            )
            into_savings = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            cash_flow = resolve_cash_flow_set(seed_user["user"].id, None)
            assert cash_flow.member_ids == (checking.id, card.id)
            assert cash_flow.balance.id == checking.id

            assert leg_accounts_shown(cash_flow, into_card) == (card.id,)
            assert leg_accounts_shown(cash_flow, into_savings) == (checking.id,)
            assert [
                (leg.account_id, leg.is_income)
                for leg in set_transfer_legs(cash_flow, [into_card, into_savings])
            ] == [(card.id, True), (checking.id, False)]

    def test_the_period_loader_windows_by_membership_and_takes_the_readers_filter(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            in_window = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            create_transfer(
                seed_user, db.session, checking, savings, seed_periods[4],
                amount=_AMOUNT,
            )
            # A second endpoint: ``uq_transfers_adhoc_dedupe`` refuses two
            # identical ad-hoc transfers in one period.
            cancelled = _cancelled_transfer(
                seed_user, checking, _savings(seed_user, name="Other"),
                seed_periods[2],
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)
            scenario_id = seed_user["scenario"].id
            period_ids = [seed_periods[2].id]

            every = set_transfer_legs_in_periods(cash_flow, scenario_id, period_ids)
            assert [leg.transfer.id for leg in every] == sorted(
                [in_window.id, cancelled.id],
            )
            projected = set_transfer_legs_in_periods(
                cash_flow, scenario_id, period_ids, is_projected_clause(Transfer),
            )
            assert [leg.transfer.id for leg in projected] == [in_window.id]
            assert set_transfer_legs_in_periods(cash_flow, scenario_id, []) == []
