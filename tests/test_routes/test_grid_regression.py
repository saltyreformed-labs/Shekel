"""
Shekel Budget App -- Payday Workflow Regression Tests

Regression test suite for the payday workflow (project_requirements_v2.md
Section 3).  Created as Commit #0 of Section 4 (UX/Grid Overhaul) to
protect against breakage during schema changes, enum refactoring, and DOM
restructuring in Commits #1 through #16.

This file tests the application as it exists BEFORE any Section 4 changes.
All assertions use current status names, current route paths, current
template output, and current fixture patterns.  When Section 4 refactors
land, these tests will be updated alongside them.
"""

# ------------------------------------------------------------------
# Implementation Plan Verification Notes (Commit #0)
# ------------------------------------------------------------------
# This test suite was written against the actual codebase, not the
# implementation plan.  The following discrepancies were noted:
#
# - mark_done and mark_credit routes return HX-Trigger: "gridRefresh"
#   (consistent with the code; confirmed in app/routes/transactions.py
#   lines 246 and 267).
# - true_up route returns HX-Trigger: "balanceChanged" (confirmed in
#   app/routes/accounts.py line 503).
# - _balance_row.html tfoot contains exactly 1 <tr> element (confirmed
#   in app/templates/grid/_balance_row.html).
# - Status model does not yet have is_settled / is_immutable boolean
#   columns (those are Commit #1 of Section 4).
# - None of the planned renames (e.g., "done" -> "Paid") are in effect
#   yet.  Tests use the current status names: projected, done, received,
#   credit, cancelled, settled.
# ------------------------------------------------------------------

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.ref import Status, TransactionType
from app.services import cash_ledger, status_seam

from tests._test_helpers import (
    current_pay_period,
    derived_span,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
)
from tests._cards import add_card
from tests._legacy_credit import plant_old_credit


class TestPaydayWorkflowRegression:
    """Regression tests covering the complete payday workflow.

    Each test maps to a step in the payday workflow documented in
    project_requirements_v2.md Section 3:

        C-0-1: Anchor balance true-up (Step 2)
        C-0-2: Mark paycheck as received (Step 3)
        C-0-3: Carry forward unpaid items (Step 4)
        C-0-4: Mark expense as done/paid (Step 5)
        C-0-5: Mark expense as credit card (Step 6)
        C-0-6: Balance row HTMX refresh
        C-0-7: Full payday sequence end-to-end

    All tests use the existing conftest fixtures (seed_user, seed_periods_today,
    auth_client) and are fully independent of each other.
    """

    # -- C-0-1 -------------------------------------------------------

    def test_trueup_anchor_balance(self, app, auth_client, seed_user,
                                   seed_periods_today):
        """Anchor balance true-up updates the balance, sets the anchor
        period to the current period, and returns display HTML with the
        balanceChanged HX-Trigger.

        Covers Step 2 of the payday workflow: the user logs their real
        checking balance so projections are anchored to reality.
        """
        with app.app_context():
            account = seed_user["account"]

            response = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "3500.00"},
            )
            assert response.status_code == 200

            # HX-Trigger drives balance row recalculation.
            # Verified: accounts.py true_up returns "balanceChanged".
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Response HTML: _anchor_edit.html display mode contains the
            # formatted balance inside an id="anchor-display" div.
            assert b"anchor-display" in response.data
            assert b"$3,500" in response.data

            # Database: anchor balance updated.
            # Re-fetch from DB because fixture-created objects may be
            # detached after request processing (scoped session recycling).
            account = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(account).balance == Decimal("3500.00")


    # -- C-0-2 -------------------------------------------------------

    def test_mark_paycheck_received(self, app, auth_client, seed_user,
                                    seed_periods_today):
        """Marking an income transaction as done sets its status to
        'received' (not 'done'), returns the badge-done indicator, and
        triggers balanceChanged.

        Covers Step 3: the user confirms their paycheck was deposited.

        The regular (non-transfer) desktop mark_done path now fires
        ``balanceChanged`` -- a targeted swap of the settled cell plus
        the self-refreshing balance row and summary subtotal rows --
        instead of the old full-page ``gridRefresh`` reload (grid
        rebuild Phase 6, audit item C3).
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )

            txn = one_off_row_of(
                seed_periods_today[0],
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done"
            )
            assert response.status_code == 200

            # Income uses 'received', not 'done'.
            # Verified: transactions.py mark_done checks is_income.
            db.session.refresh(txn)
            assert txn.status.name == "Received"

            # HX-Trigger is now balanceChanged (targeted swap) on the
            # regular desktop mark_done path (grid rebuild Phase 6, C3).
            assert response.headers.get("HX-Trigger") == "balanceChanged"

            # Response HTML: _transaction_cell.html renders badge-done
            # for both 'done' and 'received' statuses.
            assert b"badge-done" in response.data

    # -- C-0-3 -------------------------------------------------------

    def test_carry_forward_unpaid(self, app, auth_client, seed_user,
                                  seed_periods_today):
        """Carry forward moves projected transactions to the current
        period, flags template-linked items as overrides, and leaves
        done items in the source period.

        Covers Step 4: unpaid items from a past period are carried
        forward so they remain visible in the current view.
        """
        with app.app_context():
            done_status = (
                db.session.query(Status).filter_by(name="Paid").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            past_period = seed_periods_today[0]

            # Projected expense 1: a definition's own row (the engine's, plan
            # step balance:X-cf-4).  Carry forward sets is_override=True on
            # template-linked items because they moved from their
            # rule-assigned period.
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Rent Payment",
            )
            txn_template = generate_row_of(template, past_period)
            # Projected expense 2: a one-off (a rule-less definition's row).
            txn_one_off = one_off_row_of(
                past_period,
                name="Groceries",
                amount=Decimal("200.00"),
                user_id=past_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            # Done expense: should NOT be carried forward.
            txn_done = one_off_row_of(
                past_period,
                name="Car Payment",
                amount=Decimal("300.00"),
                user_id=past_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Car Payment"].id,
            )
            txn_done.status_id = done_status.id
            db.session.commit()

            response = auth_client.post(
                f"/pay-periods/{past_period.id}/carry-forward"
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "gridRefresh"

            # Resolve the target the same way the route does: the period
            # containing the owner's day.
            current_period = current_pay_period(
                seed_user["user"].id
            )

            db.session.refresh(txn_template)
            db.session.refresh(txn_one_off)
            db.session.refresh(txn_done)

            # Both projected transactions moved to current period.
            assert txn_template.pay_period_id == current_period.id
            assert txn_one_off.pay_period_id == current_period.id

            # Done transaction stays in the past period.
            assert txn_done.pay_period_id == past_period.id

            # Template-linked transaction is flagged as override.
            # Verified: carry_forward_service sets is_override when
            # txn.template_id is not None.
            assert txn_template.is_override is True

    # -- C-0-4 -------------------------------------------------------

    def test_mark_expense_done(self, app, auth_client, seed_user,
                               seed_periods_today):
        """Marking an expense as done sets its status to 'done', returns
        the badge-done indicator, and triggers balanceChanged.

        Covers Step 5: the user confirms a bill was paid from checking.

        The regular (non-transfer) desktop mark_done path now fires
        ``balanceChanged`` -- a targeted swap of the settled cell plus
        the self-refreshing balance row and summary subtotal rows --
        instead of the old full-page ``gridRefresh`` reload (grid
        rebuild Phase 6, audit item C3).
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            txn = one_off_row_of(
                seed_periods_today[0],
                name="Electric Bill",
                amount=Decimal("150.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-done"
            )
            assert response.status_code == 200

            # Expenses get 'done' (not 'received').
            # Verified: transactions.py mark_done else branch.
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            # HX-Trigger is now balanceChanged (targeted swap) on the
            # regular desktop mark_done path (grid rebuild Phase 6, C3).
            assert response.headers.get("HX-Trigger") == "balanceChanged"
            assert b"badge-done" in response.data

    # -- C-0-5 -------------------------------------------------------

    def test_mark_credit_puts_the_row_on_the_card(self, app, auth_client,
                                                  seed_user, seed_periods_today):
        """Marking an expense as credit puts it on the card, with no payback.

        Covers Step 6: the user indicates an expense was charged to a
        credit card rather than paid from checking.  Since plan task
        plan#2929 (rulings plan#2887 / plan#2888) the row settles with its
        $75.00 moved on the card and no CC Payback row is written; until then
        it took status Credit and a payback in the next pay period.
        """
        with app.app_context():
            card = add_card(seed_user)
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            txn = one_off_row_of(
                seed_periods_today[0],
                name="Restaurant",
                amount=Decimal("75.00"),
                user_id=seed_periods_today[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            response = auth_client.post(
                f"/transactions/{txn.id}/mark-credit"
            )
            assert response.status_code == 200
            assert response.headers.get("HX-Trigger") == "gridRefresh"

            db.session.refresh(txn)
            assert txn.status.name == "Paid"
            movement = status_seam.covering_movement_of(txn)
            assert (movement.account_id, movement.amount) == (
                card.id, Decimal("75.00"),
            )
            assert db.session.query(Transaction).filter_by(
                credit_payback_for_id=txn.id,
            ).count() == 0

    # -- C-0-6 -------------------------------------------------------

    def test_balance_row_refresh(self, app, auth_client, seed_user,
                                 seed_periods_today):
        """Balance row endpoint returns a tfoot partial with correct HTMX
        attributes, calculated balances, and a single <tr> element.

        Verifies the HTMX refresh mechanism that keeps projected balances
        current after any grid edit.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )

            # Create income and expense in the current period so the
            # calculated balance is deterministic.
            current_period = current_pay_period(
                seed_user["user"].id
            )
            one_off_row_of(
                current_period,
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=current_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            one_off_row_of(
                current_period,
                name="Rent",
                amount=Decimal("800.00"),
                user_id=current_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            response = auth_client.get(
                "/grid/balance-row?periods=6&offset=0"
            )
            assert response.status_code == 200

            # Structural assertions from _balance_row.html:
            # - tfoot id="grid-summary"
            # - HTMX self-refresh trigger
            # - Projected End Balance label
            assert b'id="grid-summary"' in response.data
            assert (
                b'hx-trigger="balanceChanged from:body"' in response.data
            )
            assert b"Projected End Balance" in response.data

            # Balance calculation:
            # Anchor = $1,000 at period 0 (set by seed_periods_today fixture).
            # Periods 1-5: no transactions, balance stays $1,000.
            # Period 6 (current): +$2,000 income -$800 expense = $2,200.
            assert b"$2,200" in response.data

            # The tfoot contains exactly 1 <tr> element.
            # Verified: _balance_row.html has one tr.balance-row-summary.
            html = response.data.decode("utf-8")
            tfoot_start = html.find('id="grid-summary"')
            tfoot_end = html.find("</tfoot>")
            tfoot_section = html[tfoot_start:tfoot_end]
            assert tfoot_section.count("<tr") == 1

    # -- C-0-7 -------------------------------------------------------

    def test_full_payday_sequence(self, app, auth_client, seed_user,
                                  seed_periods_today):
        """Full payday workflow executed as a sequence, verifying that all
        operations interact correctly and the final balance is correct.

        Setup:
          - Past period:    1 projected expense ($150)
          - Current period: 1 income ($2,000) + 2 expenses ($500, $300)
          - Anchor:         $1,000 at period 0 (from seed_periods_today)

        Steps:
          1. True-up anchor to $5,000 at current period
          2. Mark income as received
          3. Carry forward from past period (moves $150 to current)
          4. Mark $500 expense as done
          5. Mark $300 expense as credit (payback in future period)

        Hand-calculated balances after all steps (the FOLD, ruling R-DH (a)):

          Current period (assertion = $5,000, asserted THIS civil day):
              received income ($2,000) -- settled the SAME day as the
                                          assertion, so it is inside it
              done expense ($500) -- same day, so inside it too
              credit expense ($300) -- excluded (a credit purchase leaves via
                                       its CC Payback sibling, never checking)
              carried-forward expense ($150) -- still projected, so ruling R-G
                                       lands it at as_of + 1, inside this period
            balance = 5,000 - 150 = $4,850

          Future period:
              CC payback ($300) -- still projected, counted
            balance = 4,850 - 300 = $4,550

        **These figures moved TWICE, and the second move is ruling R-DH (a)
        (2026-07-31) overturning the first.**  They were $4,850 / $4,550 before
        plan step X-c2b2's cutover, went to $6,350 / $6,050 at it, and are back.
        The cutover's reasoning was that the anchor was asserted in step 1
        BEFORE the paycheck was marked received in step 2, so it could not have
        reflected it -- $2,500 of real money "lost" at the moment the user
        recorded it (finding cash D1).  That reasoning reads the ORDER OF TWO
        CLICKS as chronology, and on real data it is wrong far more often than
        right: the settle day was ``db.func.now()`` at the click and the assertion
        has no date column at all, so the user's actual workflow -- read the
        bank, enter the anchor, then tick off what already cleared -- files
        every one of those settles AFTER an anchor that already contains them.
        Measured across four months of the developer's Checking account, 65 of
        139 settled rows land within an hour of an assertion; absorbing them
        cuts the correction the model must plug from ``$40,554.34`` gross /
        ``-$6,998.90`` net to ``$14,286.82`` / ``-$940.06``.  Live, the instant
        partition rendered the grid at ``-$4,021.37`` against a hand-computed
        ``-$19.95``.

        **Finding cash D1 is NARROWED, not reopened.**  A settle dated a LATER
        DAY than the assertion still rides on top of it -- that is the real
        shape of "money the app loses", and
        ``test_cash_walk.TestEveryAssertionIsReplayed
        .test_three_assertions_each_reset_the_running_balance`` pins it (a
        -$300.00 settle on 04-01 reduces a $900.00 assertion made 03-01).  What
        R-DH removed is the SAME-DAY claim, which was never a fact about money.

        Full trace and measurements:
        ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``.
        """
        with app.app_context():
            income_type = (
                db.session.query(TransactionType)
                .filter_by(name="Income").one()
            )
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense").one()
            )
            account = seed_user["account"]

            # Resolve key periods.
            current_period = current_pay_period(
                seed_user["user"].id
            )
            # Past period: immediately before current.
            past_period = next(
                p for p in seed_periods_today
                if derived_span(p).period_index == derived_span(current_period).period_index - 1
            )
            # Future period: immediately after current (payback target).
            future_period = next(
                p for p in seed_periods_today
                if derived_span(p).period_index == derived_span(current_period).period_index + 1
            )

            # -- Create test transactions --

            # Past period: projected expense ($150).
            past_expense = one_off_row_of(
                past_period,
                name="Past Rent",
                amount=Decimal("150.00"),
                user_id=past_period.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            # Current period: income ($2,000).
            income_txn = one_off_row_of(
                current_period,
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=current_period.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            # Current period: expense to mark done ($500).
            expense_done = one_off_row_of(
                current_period,
                name="Electric Bill",
                amount=Decimal("500.00"),
                user_id=current_period.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Car Payment"].id,
            )
            # Current period: expense to mark credit ($300).
            expense_credit = one_off_row_of(
                current_period,
                name="Groceries",
                amount=Decimal("300.00"),
                user_id=current_period.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            # -- Step 1: True-up anchor to $5,000 --
            resp = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert resp.status_code == 200

            # -- Step 2: Mark income as received --
            resp = auth_client.post(
                f"/transactions/{income_txn.id}/mark-done"
            )
            assert resp.status_code == 200

            # -- Step 3: Carry forward from past period --
            resp = auth_client.post(
                f"/pay-periods/{past_period.id}/carry-forward"
            )
            assert resp.status_code == 200

            # -- Step 4: Mark expense as done --
            resp = auth_client.post(
                f"/transactions/{expense_done.id}/mark-done"
            )
            assert resp.status_code == 200

            # -- Step 5: the expense in the old Credit shape (no door writes it since plan task plan#2929; plan#2933 puts the press back here once the card's payoff row repays it) --
            plant_old_credit(expense_credit)
            db.session.commit()

            # -- Verify final state --

            # Re-fetch all objects from the database.  After 5 sequential
            # HTTP requests, the scoped session may have been recycled and
            # the original Python objects detached.
            account = db.session.get(Account, account.id)
            income_txn = db.session.get(Transaction, income_txn.id)
            past_expense = db.session.get(Transaction, past_expense.id)
            expense_done = db.session.get(Transaction, expense_done.id)
            expense_credit = db.session.get(Transaction, expense_credit.id)

            assert cash_ledger.resolve_anchor(account).balance == Decimal("5000.00")
            assert income_txn.status.name == "Received"
            assert past_expense.pay_period_id == current_period.id
            assert expense_done.status.name == "Paid"
            assert expense_credit.status.name == "Credit"

            # Verify CC payback exists in the future period.
            payback = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=expense_credit.id)
                .one()
            )
            assert payback.pay_period_id == future_period.id
            assert payback.estimated_amount == Decimal("300.00")

            # -- Verify balance via balance-row endpoint --
            resp = auth_client.get(
                f"/grid/balance-row?periods=2&offset=0"
                f"&account_id={account.id}"
            )
            assert resp.status_code == 200

            # See hand calculation in docstring above.
            assert b"$4,850" in resp.data
            assert b"$4,550" in resp.data
