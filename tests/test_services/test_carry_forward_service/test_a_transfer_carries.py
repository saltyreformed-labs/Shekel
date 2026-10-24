"""A transfer in the source period carries forward once, beside the period's other rows: flagged
when its definition recurs and not when it is ad hoc (R-BAL93), and left behind when done,
cancelled or soft-deleted.
"""

from decimal import Decimal

from app.models.amount_ownership import AmountOwnership
from app.models.ref import AccountType, Status
from app.services import account_service, carry_forward_service, transfer_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import generate_transfer_of, make_transfer_template

from ._helpers import _create_transaction, _create_savings, _create_transfer_in_period


class TestCarryForwardShadowTransactions:
    """Carry-forward's transfer arm: one ``update_transfer`` call per transfer.

    A transfer is ONE row, and both of its legs read its period
    (``transfer_legs``), so nothing moves with it.  These cases read the
    transfer; until plan step balance:X-bi-6-4d-2 deleted its two twin
    rows, they read those too.
    """

    def test_sets_is_override_on_a_recurring_definitions_transfer_and_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """Carry forward flags a RECURRING definition's transfer.

        The flag is what keeps the maintain and generate passes off a row the
        owner placed elsewhere, so it is a recurring definition's transfer's
        alone.  **This case flagged an AD-HOC transfer until plan step
        ``balance:X-ci-1``** (ruling **R-BAL93**, the developer's explicit
        re-ruling of the expected behaviour: carry-forward's transfer move
        keys on ``recurs``, and a transfer no cadence generated takes no
        flag); the transfer is the engine's now, and the ad-hoc arm is the
        case below.
        """
        with app.app_context():
            savings = _create_savings(seed_user)
            template = make_transfer_template(db.session, seed_user, savings)
            xfer = generate_transfer_of(template, seed_periods[0])
            db.session.flush()
            assert xfer.recurs is True

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[1].id
            assert xfer.is_override is True

    def test_does_not_flag_a_transfer_no_cadence_generated(
        self, app, db, seed_user, seed_periods
    ):
        """An ad-hoc transfer moves whole, and takes no flag (R-BAL93).

        No pass will ever write over it, and a flag would only hide it from
        a definition it might one day be given; this is the arm the
        discrete branch already takes for a transaction no rule
        generated.  The one-time transfer's arm -- the same rule plus the
        re-placing -- is pinned in ``tests/test_routes/test_one_time_transfer_doors``.
        """
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.flush()
            assert xfer.recurs is False

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[1].id
            assert xfer.is_override is False

    def test_ignores_done_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """A Paid transfer is not carried forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            done = db.session.query(Status).filter_by(name="Paid").one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=done.id
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 0
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[0].id

    def test_ignores_cancelled_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """A Cancelled transfer is not carried forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, status_id=cancelled.id
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 0
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[0].id

    def test_ignores_soft_deleted_shadows(
        self, app, db, seed_user, seed_periods
    ):
        """A soft-deleted transfer is not carried forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 0

    def test_mixed_regular_and_shadow(
        self, app, db, seed_user, seed_periods
    ):
        """Mixed period: 2 regular + 1 transfer + 1 done regular."""
        with app.app_context():
            reg1 = _create_transaction(seed_user, seed_periods, name="Reg 1")
            reg2 = _create_transaction(seed_user, seed_periods, name="Reg 2")
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            done_txn = _create_transaction(
                seed_user, seed_periods, status_name="Paid", name="Done"
            )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 3  # 2 regular + 1 transfer

            db.session.refresh(reg1)
            db.session.refresh(reg2)
            db.session.refresh(xfer)
            db.session.refresh(done_txn)
            assert reg1.pay_period_id == seed_periods[1].id
            assert reg2.pay_period_id == seed_periods[1].id
            assert xfer.pay_period_id == seed_periods[1].id
            assert done_txn.pay_period_id == seed_periods[0].id

    def test_multiple_transfers(
        self, app, db, seed_user, seed_periods
    ):
        """Multiple transfers in one period are each carried forward once."""
        with app.app_context():
            # Need a second savings account for the second transfer.
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings"
            ).one()
            savings2 = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="CF Savings 2",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings2)
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="Projected").one()

            xfer1 = _create_transfer_in_period(seed_user, seed_periods, 0)
            xfer2 = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings2.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("150.00")),
                    status_id=projected.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    name="CF Transfer 2",
                ),
            )
            _create_transaction(seed_user, seed_periods, name="Reg")
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 3  # 1 regular + 2 transfers

            db.session.refresh(xfer1)
            db.session.refresh(xfer2)
            assert xfer1.pay_period_id == seed_periods[1].id
            assert xfer2.pay_period_id == seed_periods[1].id

    def test_only_shadows_in_period(
        self, app, db, seed_user, seed_periods
    ):
        """A period holding only a transfer (no regular row) carries it forward."""
        with app.app_context():
            xfer = _create_transfer_in_period(seed_user, seed_periods, 0)
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 1
            db.session.refresh(xfer)
            assert xfer.pay_period_id == seed_periods[1].id

    def test_preserves_regular_transaction_behavior(
        self, app, db, seed_user, seed_periods
    ):
        """Regular transactions behave identically to pre-rework (regression)."""
        with app.app_context():
            reg1 = _create_transaction(seed_user, seed_periods, name="Reg A")
            reg2 = _create_transaction(seed_user, seed_periods, name="Reg B")
            reg3 = _create_transaction(seed_user, seed_periods, name="Reg C")
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 3
            for txn in [reg1, reg2, reg3]:
                db.session.refresh(txn)
                assert txn.pay_period_id == seed_periods[1].id
