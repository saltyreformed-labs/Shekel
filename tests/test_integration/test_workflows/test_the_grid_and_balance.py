"""Workflows that end in the grid or the balance: a salary profile's income in every period, a
monthly template's rows in the right periods, a transfer reducing its source's balance, an
anchor true-up shifting every later balance, and a whole budget created and then reconciled on
payday.
"""

from datetime import timedelta
from decimal import Decimal

from app.models.ref import AccountType, Status, TransactionType
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import account_service
from app.services import (
    carry_forward_service,
    credit_workflow,
    recurrence_engine,
    transaction_service,
)
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    typed,
    make_cadence_rule,
    make_every_period_rule,
    one_off_row_of,
    override_anchor,
    resolved_amount,
    settle_instant_on,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY
from app.services.row_valuation import settled_contribution, settled_figure
from app.models.amount_ownership import AmountOwnership


class TestSalaryToGrid:
    """Salary profile creation → income transactions appear in grid periods."""

    def test_salary_generates_income_per_period(self, app, db, seed_user, seed_periods):
        """Creating a salary template with every_period recurrence populates all periods."""
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            # Create recurrence rule and template (mimics salary profile creation).
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Salary"].id,
                transaction_type_id=income_type.id,
                name="Paycheck",
                default_amount=Decimal("2884.62"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, template)

            # Generate income transactions across all 10 periods.
            txns = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.commit()

            # One income transaction per period.
            assert len(txns) == 10
            for txn in txns:
                assert txn.transaction_type.name == "Income"
                assert resolved_amount(txn) == Decimal("2884.62")
                assert txn.status.name == "Projected"


class TestTemplateRecurrenceToGrid:
    """Template with monthly recurrence → transactions in correct periods."""

    def test_monthly_recurrence_hits_correct_periods(self, app, db, seed_user, seed_periods):
        """Monthly recurrence on day 15 places transactions in periods containing that day."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            # Authored through the write door (plan step R7c-b): "monthly on
            # the 15th" is stated as the first occurrence that description
            # produces, which is what ``first_occurrence_on_day`` translates.
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                name="Rent",
                default_amount=Decimal("1200.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            # The definition first, then the cadence onto it (plan step R-F6).
            make_cadence_rule(
                template,
                MONTHLY,
                fires_on_day=15,
            )

            txns = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.commit()

            # seed_periods starts 2026-01-02, 10 biweekly periods through 2026-05-21.
            # Monthly recurrence on day_of_month=15 hits:
            #   P0: Jan 2-15 → Jan 15, P3: Feb 13-26 → Feb 15,
            #   P5: Mar 13-26 → Mar 15, P7: Apr 10-23 → Apr 15,
            #   P9: May 8-21 → May 15
            # = 5 hits
            assert len(txns) == 5
            calendar = calendar_for(seed_user["user"].id)
            for txn in txns:
                assert txn.name == "Rent"
                assert resolved_amount(txn) == Decimal("1200.00")
                # Each transaction's period should contain the 15th of some
                # month.  The span is the CALENDAR's since plan step
                # ``pay_calendar:C4-c`` dropped ``pay_periods.end_date``.
                span = calendar.period_by_id(txn.pay_period_id)
                assert span.start_date.day <= 15 or span.end_date.day >= 15


class TestTransferToBalance:
    """A transfer moves its source account's cash balance, read off its one row."""

    def test_transfer_reduces_source_balance(self, app, db, seed_user, seed_periods):
        """A Projected transfer reduces its source account's balance by its amount."""
        with app.app_context():
            from app.services import transfer_service

            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="Projected").one()

            # Create a transfer via the service: one row, the money on both
            # accounts read off it.
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("200.00")),
                    status_id=projected.id,
                    category_id=seed_user["categories"]["Rent"].id,
                    name="Save $200",
                ),
            )
            db.session.commit()

            # What the app RENDERS (plan step X-g4b re-pointed this off the
            # deleted anchor-forward walk).  ``as_of`` is pinned inside period
            # 0 so ruling R-G's clamp does not carry the still-Projected transfer
            # past the window; the $1,000.00 anchor is the fixture's own.
            balances = balance_at.cash_balance_map(
                seed_user["account"],
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )

            # The $200 transfer reduces the balance: 1000 - 200 = 800.
            assert balances[seed_periods[0].id] == Decimal("800.00")


class TestAnchorTrueUpBalance:
    """Anchor balance change → all downstream period balances recalculate."""

    def test_anchor_change_shifts_all_balances(self, app, db, seed_user, seed_periods):
        """Changing anchor balance shifts all downstream period balances by the same delta."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create an expense in period 1.
            one_off_row_of(
                seed_periods[1],
                name="Rent",
                amount=Decimal("1200.00"),
                user_id=seed_periods[1].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # Plan step X-g4b: the anchor is an ASSERTION the account carries,
            # not an argument a caller passes -- so the true-up is performed
            # through the same helper production writes one with, and read back
            # through the seam.  That is what this test was always about; the
            # deleted producer merely let it be simulated with a parameter.
            # ONE read pass per true-up, built AFTER its commit.  A pass is a
            # memo whose lifetime is the one read it was built for
            # (``BalanceContext``), and since plan step X-i4 the cash fold is
            # one of the derivations it holds -- so a pass reused across these
            # two writes would answer the first anchor twice and the $1,000.00
            # shift would read $0.00.  Both passes are pinned to the same
            # ``as_of``, so the only thing that differs between them is the
            # assertion each was built to read.
            override_anchor(
                db.session, seed_user["account"], seed_periods[0],
                Decimal("2000.00"), at=settle_instant_on(seed_periods[0].start_date),
            )
            db.session.commit()
            balances_2k = balance_at.cash_balance_map(
                seed_user["account"],
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )

            override_anchor(
                db.session, seed_user["account"], seed_periods[0],
                Decimal("3000.00"), at=settle_instant_on(
                    seed_periods[0].start_date + timedelta(days=1),
                ),
            )
            db.session.commit()
            balances_3k = balance_at.cash_balance_map(
                seed_user["account"],
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[0].start_date,
                ),
            )

            # Every period's balance should differ by exactly $1000.
            for period in seed_periods[:3]:
                diff = balances_3k[period.id] - balances_2k[period.id]
                assert diff == Decimal("1000.00")


# ── Full Workflow Integration Test ───────────────────────────────────


class TestFullBudgetWorkflow:
    """End-to-end payday reconciliation workflow."""

    def test_full_workflow_create_budget_then_reconcile(
        self, app, db, seed_user, seed_periods,
    ):
        """Simulates a complete payday workflow: create → mark done →
        mark credit → carry forward. Verifies final state of all
        transactions across both periods with exact Decimal amounts.

        Steps:
          1. Create 3 projected expenses in period 0.
          2. Mark first as 'done' with actual_amount different from estimated.
          3. Mark second as 'credit' (creates payback in period 1).
          4. Leave third as 'projected'.
          5. Carry forward from period 0 → period 1 (moves the projected item).
          6. Assert final state of both periods.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Step 1: Create 3 projected expenses in period 0.
            txn1 = one_off_row_of(
                seed_periods[0],
                name="Rent",
                amount=Decimal("1200.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            txn2 = one_off_row_of(
                seed_periods[0],
                name="Dining Out",
                amount=Decimal("75.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            one_off_row_of(
                seed_periods[0],
                name="Gas Station",
                amount=Decimal("45.50"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()
            txn2_id = txn2.id

            # Step 2: Mark Rent as 'done' with a CORRECTION -- the figure a
            # human read off a statement, which the settle records as
            # ``corrected`` (plan step X-au-c3).  Through the real verb, because
            # a bare status assign leaves a state the record's CHECKs refuse.
            transaction_service.settle_transaction(
                txn1, submitted=typed(Decimal("1195.00")),
            )
            db.session.commit()

            # Step 3: Mark Dining Out as 'credit' → payback in period 1.
            credit_workflow.mark_as_credit(txn2_id, seed_user["user"].id)
            db.session.commit()

            # Step 4: Gas Station stays projected.
            # Step 5: Carry forward from period 0 → period 1.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.commit()
            assert count == 1  # Only Gas Station (projected) moves.

            # Step 6: Assert final state.
            db.session.expire_all()

            # Period 0: 2 transactions -- 1 done (Rent), 1 credit (Dining Out).
            period0_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[0].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(period0_txns) == 2

            by_name_p0 = {t.name: t for t in period0_txns}
            assert by_name_p0["Rent"].status.name == "Paid"
            assert resolved_amount(by_name_p0["Rent"]) == Decimal("1200.00")
            assert settled_figure(by_name_p0["Rent"]) == Decimal("1195.00")
            assert by_name_p0["Dining Out"].status.name == "Credit"
            assert resolved_amount(by_name_p0["Dining Out"]) == Decimal("75.00")
            assert settled_contribution(by_name_p0["Dining Out"]) == Decimal("0")

            # Period 0 has no projected transactions remaining.
            p0_projected = [t for t in period0_txns if t.status.name == "Projected"]
            assert len(p0_projected) == 0

            # Period 1: 2 transactions -- payback + carried-forward Gas Station.
            period1_txns = db.session.query(Transaction).filter_by(
                pay_period_id=seed_periods[1].id,
            ).filter(Transaction.is_deleted.is_(False)).all()
            assert len(period1_txns) == 2

            by_name_p1 = {t.name: t for t in period1_txns}
            assert by_name_p1["CC Payback: Dining Out"].estimated_amount == Decimal("75.00")
            assert by_name_p1["CC Payback: Dining Out"].status.name == "Projected"
            assert by_name_p1["CC Payback: Dining Out"].credit_payback_for_id == txn2_id

            assert resolved_amount(by_name_p1["Gas Station"]) == Decimal("45.50")
            assert by_name_p1["Gas Station"].status.name == "Projected"
            assert by_name_p1["Gas Station"].pay_period_id == seed_periods[1].id
