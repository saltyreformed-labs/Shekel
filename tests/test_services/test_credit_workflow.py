"""
Shekel Budget App -- Credit Workflow & Carry Forward Tests

Tests the credit card workflow (§4.5) and carry forward (§4.6)
services that are central to the payday workflow.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services.balance_at import BalanceContext
from app.services import carry_forward_service, credit_workflow, pay_period_write
from app.exceptions import NotFoundError, ValidationError
from tests._test_helpers import (
    cover_bare_settled_row,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    rhythm_of,
    settle_day_columns,
)


class TestCreditWorkflow:
    """Tests for the credit card status + auto-payback mechanism."""

    def _create_expense(
        self, seed_user, seed_periods, amount="100.00", *, is_envelope=False,
    ):
        """Helper: create a projected expense in the first period.

        ``is_envelope`` lands on the one-off's DEFINITION, which is the only
        place the flag lives (plan step ``balance:X-bi-7d-2``).
        """
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = one_off_row_of(
            seed_periods[0],
            name="Test Expense",
            amount=Decimal(amount),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
            is_envelope=is_envelope,
        )
        db.session.flush()
        return txn

    def test_mark_as_credit_creates_payback(self, app, db, seed_user, seed_periods):
        """Marking an expense as credit creates a payback in the next period."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)

            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Original is now 'credit' status.
            assert txn.status.name == "Credit"

            # Payback exists in the next period.
            assert payback.pay_period_id == seed_periods[1].id
            assert payback.estimated_amount == Decimal("100.00")
            assert payback.name == "CC Payback: Test Expense"
            assert payback.credit_payback_for_id == txn.id

    def test_unmark_credit_deletes_payback(self, app, db, seed_user, seed_periods):
        """Reverting credit status deletes the auto-generated payback."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Original reverted to projected.
            assert txn.status.name == "Projected"

            # Payback is deleted.
            assert db.session.get(Transaction, payback_id) is None

    def test_cannot_credit_income(self, app, db, seed_user, seed_periods):
        """Marking income as credit raises ValidationError."""
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            txn = one_off_row_of(
                seed_periods[0],
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            db.session.flush()

            with pytest.raises(ValidationError):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    # ``test_payback_uses_actual_amount_when_set`` lived here until plan step
    # X-au-c3.  It put ``actual_amount = 75.00`` on a PROJECTED ``$100.00``
    # expense and asserted the payback took the 75 -- a state the seam makes
    # unconstructible (``reject_settlement_without_settled_status``; the CHECK
    # ``ck_transactions_settled_amount_needs_basis`` said the same at the
    # storage tier through plan step X-bi-4b-1), because a figure now RECORDS
    # a settle and this row's money has not moved.
    # ``mark_as_credit`` refuses any status but Projected, so the column arm it
    # graded was unreachable the moment that CHECK landed and was deleted with
    # it; the payback is the row's RESOLVED amount, which
    # ``test_mark_as_credit_creates_payback`` above grades.

    def test_auto_creates_cc_category_if_missing(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit auto-creates the CC Payback category if missing."""
        with app.app_context():
            # Delete the pre-seeded CC Payback category by ID (re-fetch to
            # avoid cross-session issues).
            cc_cat = db.session.get(
                Category, seed_user["categories"]["Payback"].id
            )
            db.session.delete(cc_cat)
            db.session.flush()

            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # A new category should have been created.
            new_cat = (
                db.session.query(Category)
                .filter_by(
                    user_id=seed_user["user"].id,
                    group_name="Credit Card",
                    item_name="Payback",
                )
                .one()
            )
            # .one() above already raises if not found; accessing .id validates non-None
            assert payback.category_id == new_cat.id

    def test_no_next_period_raises_validation_error(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit raises ValidationError when no next period exists."""
        with app.app_context():
            # Create expense in the last period (no period follows it).
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            txn = one_off_row_of(
                seed_periods[-1],
                name="Last Period Expense",
                amount=Decimal("50.00"),
                user_id=seed_periods[-1].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            with pytest.raises(ValidationError):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_mark_as_credit_wrong_user_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: mark_as_credit with wrong user_id raises NotFoundError."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            db.session.flush()

            with pytest.raises(NotFoundError):
                credit_workflow.mark_as_credit(txn.id, user_id=999999)

            # Transaction status must be unchanged -- no partial modification.
            db.session.refresh(txn)
            assert txn.status.name == "Projected"

    def test_unmark_credit_wrong_user_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: unmark_credit with wrong user_id raises NotFoundError."""
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            with pytest.raises(NotFoundError):
                credit_workflow.unmark_credit(txn.id, user_id=999999)

            # Transaction status must be unchanged -- still 'credit'.
            db.session.refresh(txn)
            assert txn.status.name == "Credit"

            # Payback transaction must still exist.
            assert db.session.get(Transaction, payback_id) is not None

    def test_mark_as_credit_nonexistent_txn_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """mark_as_credit on a nonexistent transaction raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                credit_workflow.mark_as_credit(999999, seed_user["user"].id)

    def test_unmark_credit_nonexistent_txn_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """unmark_credit on a nonexistent transaction raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                credit_workflow.unmark_credit(999999, seed_user["user"].id)

    def test_delete_payback_on_source_delete_removes_live_payback(
        self, app, db, seed_user, seed_periods
    ):
        """The helper hard-deletes the live payback of a Credit source.

        The deletion-side invariant: when the source goes, its live
        payback must go in the same transaction -- otherwise the
        payback survives via the SET NULL FK and inflates the next
        period with no offsetting credit row.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            credit_workflow.delete_payback_on_source_delete(
                txn, seed_user["user"].id,
            )
            db.session.flush()

            assert db.session.get(Transaction, payback_id) is None

    def test_delete_payback_on_source_delete_noop_without_payback(
        self, app, db, seed_user, seed_periods
    ):
        """No live payback -> the helper is a silent no-op.

        This is the hot path for every ordinary delete; it must not
        raise or delete anything.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            before = db.session.query(Transaction).count()

            credit_workflow.delete_payback_on_source_delete(
                txn, seed_user["user"].id,
            )
            db.session.flush()

            assert db.session.query(Transaction).count() == before

    def test_delete_payback_on_source_delete_preserves_soft_deleted(
        self, app, db, seed_user, seed_periods
    ):
        """A soft-deleted prior payback stays for the audit trail.

        Mirrors the ``get_active_payback`` contract pinned for
        ``unmark_credit`` (#58): only the LIVE payback dies with the
        source; a previously soft-deleted payback row is untouched.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            payback.is_deleted = True
            db.session.flush()

            credit_workflow.delete_payback_on_source_delete(
                txn, seed_user["user"].id,
            )
            db.session.flush()

            survived = db.session.get(Transaction, payback_id)
            assert survived is not None
            assert survived.is_deleted is True

    def test_delete_payback_on_source_delete_clears_entry_links(
        self, app, db, seed_user, seed_periods
    ):
        """Entry-level credit source: payback deleted, entry links severed.

        An entry-tracking source's own status is NOT Credit, so a
        status-based guard would miss it -- the helper keys on
        ``get_active_payback`` instead.  An entry a direct call leaves in
        place must not point at the vanished payback: the delete verb
        takes every entry off first (plan step ``credit_card:CC-5-4a-4``,
        ruling R-CC75), and for this direct call the link's ``ON DELETE
        SET NULL`` severs it.
        """
        with app.app_context():
            from app.models.transaction_entry import TransactionEntry
            from app.services import entry_credit_workflow

            txn = self._create_expense(seed_user, seed_periods, is_envelope=True)
            assert txn.tracks_purchases is True
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("40.00"),
                description="Card purchase",
                is_credit=True,
            )
            db.session.add(entry)
            db.session.flush()

            payback = entry_credit_workflow.sync_entry_payback(
                txn.id, seed_user["user"].id,
            )
            db.session.flush()
            payback_id = payback.id
            assert entry.credit_payback_id == payback_id

            credit_workflow.delete_payback_on_source_delete(
                txn, seed_user["user"].id,
            )
            db.session.flush()

            # Payback gone; the source stays Projected throughout.
            assert db.session.get(Transaction, payback_id) is None
            assert txn.status.name == "Projected"

            # The entry survives with its payback link severed.
            db.session.refresh(entry)
            assert entry.credit_payback_id is None

    def test_delete_payback_on_source_delete_takes_chain_down(
        self, app, db, seed_user, seed_periods
    ):
        """A payback that was itself marked Credit dies with its own payback.

        A payback is an ordinary projected expense, so it can be
        marked Credit and grow a payback of its own.  Deleting the
        chain's root must take the whole live chain down -- a
        surviving grandchild payback would be the same orphan one
        level deeper.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, user_id)
            db.session.flush()
            grandchild = credit_workflow.mark_as_credit(payback.id, user_id)
            db.session.flush()
            payback_id, grandchild_id = payback.id, grandchild.id

            credit_workflow.delete_payback_on_source_delete(txn, user_id)
            db.session.flush()

            assert db.session.get(Transaction, payback_id) is None
            assert db.session.get(Transaction, grandchild_id) is None


class TestCarryForward:
    """Tests for the carry-forward-unpaid service."""

    def test_carry_forward_moves_projected_items(self, app, db, seed_user, seed_periods):
        """Carry forward moves projected items to the target period."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Create two projected expenses in the first period.
            for name in ("Expense A", "Expense B"):
                one_off_row_of(
                    seed_periods[0],
                    name=name,
                    amount=Decimal("50.00"),
                    user_id=seed_periods[0].user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=seed_user["categories"]["Groceries"].id,
                )
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 2

            # Verify they moved.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 0

            moved = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[1].id)
                .all()
            )
            assert len(moved) == 2

    def test_carry_forward_skips_done_items(self, app, db, seed_user, seed_periods):
        """Carry forward does NOT move done/received items."""
        with app.app_context():
            done = db.session.query(Status).filter_by(name="Paid").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # One projected, one done.
            t1 = one_off_row_of(
                seed_periods[0],
                name="Unpaid",
                amount=Decimal("100.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            t2 = one_off_row_of(
                seed_periods[0],
                name="Already Paid",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            t2.status_id = done.id
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            for _column, _value in settle_day_columns(seed_periods[0].start_date).items():
                setattr(t2, _column, _value)
            db.session.add_all([t1, t2])
            db.session.flush()
            cover_bare_settled_row(db.session, t2, Decimal("500.00"))

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Only the projected item moved.
            assert count == 1

            # Done item stays in original period.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].name == "Already Paid"

    def test_carry_forward_flags_template_items_as_override(
        self, app, db, seed_user, seed_periods
    ):
        """Template-linked items are flagged is_override when carried forward."""
        with app.app_context():
            # The definition's own row, written by the engine (plan step
            # balance:X-cf): the rule's row, not the owner's.
            template = make_expense_template(
                db.session, seed_user, amount="300.00",
                name="Car Payment", category_key="Car Payment",
            )
            txn = generate_row_of(template, seed_periods[0])
            assert txn.is_override is False

            carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Should now be flagged as override.
            assert txn.is_override is True
            assert txn.pay_period_id == seed_periods[1].id

    def test_carry_forward_skips_cancelled_items(self, app, db, seed_user, seed_periods):
        """Cancelled items stay in the source period and are not carried forward."""
        with app.app_context():
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # One projected (should move), one cancelled (should stay).
            t1 = one_off_row_of(
                seed_periods[0],
                name="Unpaid Expense",
                amount=Decimal("80.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            t2 = one_off_row_of(
                seed_periods[0],
                name="Cancelled Expense",
                amount=Decimal("200.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            t2.status_id = cancelled.id
            db.session.add_all([t1, t2])
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 1

            # Cancelled item stays in source period.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].name == "Cancelled Expense"

    def test_carry_forward_skips_received_items(self, app, db, seed_user, seed_periods):
        """Received income items stay in the source period and are not carried forward."""
        with app.app_context():
            received = db.session.query(Status).filter_by(name="Received").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()

            # One projected expense (should move), one received income (should stay).
            t1 = one_off_row_of(
                seed_periods[0],
                name="Unpaid Expense",
                amount=Decimal("60.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            t2 = one_off_row_of(
                seed_periods[0],
                name="Received Paycheck",
                amount=Decimal("2000.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            t2.status_id = received.id
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            for _column, _value in settle_day_columns(seed_periods[0].start_date).items():
                setattr(t2, _column, _value)
            db.session.add_all([t1, t2])
            db.session.flush()
            cover_bare_settled_row(db.session, t2, Decimal("2000.00"))

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 1

            # Received item stays in source period.
            remaining = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .all()
            )
            assert len(remaining) == 1
            assert remaining[0].name == "Received Paycheck"

    def test_carry_forward_skips_soft_deleted_items(self, app, db, seed_user, seed_periods):
        """Soft-deleted projected items are excluded from carry forward."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # Soft-deleted projected expense -- should NOT be moved.
            txn = one_off_row_of(
                seed_periods[0],
                name="Deleted Expense",
                amount=Decimal("40.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            txn.is_deleted = True
            db.session.flush()

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 0

            # Item stays in source period, untouched.
            assert txn.pay_period_id == seed_periods[0].id

    def test_carry_forward_source_not_found(self, app, db, seed_user, seed_periods):
        """NotFoundError is raised when the source period does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    999999, seed_periods[1].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )

    def test_carry_forward_target_not_found(self, app, db, seed_user, seed_periods):
        """NotFoundError is raised when the target period does not exist."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, 999999, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )

    def test_carry_forward_empty_source_returns_zero(self, app, db, seed_user, seed_periods):
        """Carry forward returns 0 when the source period has no transactions."""
        with app.app_context():
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )

            assert count == 0

    def test_carry_forward_another_owners_calendar_raises_not_found(
        self, app, db, seed_user, seed_second_user, seed_periods
    ):
        """A REAL second owner's calendar cannot reach these periods.

        **This graded a ``user_id=999999`` argument until pay-calendar plan
        step C2-f3c**, which deleted that argument: the door takes the owner's
        read pass -- their
        :class:`~app.services.pay_calendar.PayCalendar` until plan step
        R7d-c-1, their
        :class:`~app.services.balance_at.BalanceContext` since -- and reads
        the owner off it, so there is no second spelling of "whose periods are
        these" to get wrong.  What replaces the check is the same defence made
        structural -- a pass derives one owner's whole schedule and nothing
        else, so periods that are not in it are not found, whether they exist
        or not.

        **The second owner is REAL and has their own schedule**, which an
        adversarial review of that step is why: the first rewrite passed
        ``calendar_for(999999)``, a nonexistent user, whose calendar is EMPTY
        and therefore refuses every id trivially.  That cannot tell "not this
        owner's" from "this owner has no schedule", and it is a state the
        application cannot produce.  This is the mutating door's only
        cross-owner case; the preview door's lives in
        ``test_carry_forward_service``.
        """
        with app.app_context():
            other = BalanceContext.build(seed_second_user["user"].id)
            # The premise: the other owner HAS a schedule, so the refusal below
            # is about whose periods these are and not about an empty calendar.
            assert len(other.calendar().saved()) > 0, (
                "the second user needs their own periods, or this case is the "
                "empty-calendar tautology it replaced"
            )

            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id,
                    seed_user["scenario"].id,
                    balance_ctx=other,
                )

    def test_carry_forward_wrong_user_target_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Defense-in-depth: source passes but target belongs to another user."""
        with app.app_context():
            from app.models.user import User, UserSettings
            from app.models.scenario import Scenario
            from app.services.auth_service import hash_password
            from app.services import pay_period_service

            # Create a second user with their own pay period.
            user2 = User(
                email="second@shekel.local",
                password_hash=hash_password("secondpass1234"),
                display_name="Second User",
            )
            db.session.add(user2)
            db.session.flush()

            settings2 = UserSettings(user_id=user2.id)
            scenario2 = Scenario(
                user_id=user2.id, name="Baseline", is_baseline=True,
            )
            db.session.add_all([settings2, scenario2])
            db.session.flush()

            periods2 = pay_period_write.record_paydays(
                user_id=user2.id,
                first_payday=date(2026, 6, 1),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            # Source belongs to seed_user (passes), target belongs to user2 (fails).
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, periods2[0].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )

    def test_carry_forward_nonexistent_source_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Nonexistent source period raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    999999, seed_periods[0].id, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )

    def test_carry_forward_nonexistent_target_raises_not_found(
        self, app, db, seed_user, seed_periods
    ):
        """Nonexistent target period raises NotFoundError."""
        with app.app_context():
            with pytest.raises(NotFoundError):
                carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, 999999, seed_user["scenario"].id,
                    balance_ctx=BalanceContext.build(seed_user["user"].id),
                )


class TestNegativePaths:
    """Negative-path and edge-case tests for credit workflow and carry forward.

    Covers: unmark on projected transactions, double-mark idempotency,
    carry-forward source==target, and marking done transactions as credit.
    """

    def _create_expense(self, seed_user, seed_periods, amount="100.00",
                        status_name="Projected"):
        """Helper: create an expense in the first period with the given status."""
        status = db.session.query(Status).filter_by(name=status_name).one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = one_off_row_of(
            seed_periods[0],
            name="Test Expense",
            amount=Decimal(amount),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        txn.status_id = status.id
        db.session.flush()
        return txn

    def test_unmark_credit_on_projected_transaction(self, app, db, seed_user, seed_periods):
        """unmark_credit on a non-Credit row raises ValidationError.

        After the C-21 follow-up the helper guards against being
        called on a transaction that is not currently in Credit
        status -- the previous implementation silently rewrote the
        status to Projected on any caller-supplied row, which
        would (for a Paid row, for example) drop a settled
        transaction back to Projected and erase its place in the
        audit trail.

        The original intent of this test ("A user double-clicking
        'unmark credit' or calling the endpoint on the wrong
        transaction must not corrupt data") is now satisfied by a
        loud rejection rather than a silent no-op: the row stays
        unchanged AND the caller learns the request was rejected.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            original_status_id = txn.status_id

            with pytest.raises(ValidationError) as excinfo:
                credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            msg = str(excinfo.value)
            assert "Projected" in msg
            assert "Only Credit" in msg

            # Row unchanged -- the bespoke guard fires before any write.
            db.session.refresh(txn)
            assert txn.status.name == "Projected"
            assert txn.status_id == original_status_id

            # No payback transaction exists or was created.
            payback_count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert payback_count == 0

    def test_double_mark_as_credit_returns_existing_payback(
        self, app, db, seed_user, seed_periods
    ):
        """Double-marking a transaction as credit returns the existing payback idempotently.

        The function checks if the transaction's status is already 'credit'
        and, if so, returns the existing payback rather than creating a duplicate.
        This is critical: a duplicate payback would mean the user sees double
        the debt repayment on their grid.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)

            # First mark: creates the payback.
            first_payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            first_payback_id = first_payback.id

            # Second mark: should return the existing payback, not create a new one.
            second_payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Same payback returned.
            assert second_payback.id == first_payback_id

            # Only one payback exists for this transaction.
            payback_count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert payback_count == 1

    def test_mark_as_credit_idempotency_ignores_soft_deleted_payback(
        self, app, db, seed_user, seed_periods,
    ):
        """A soft-deleted payback is not returned by the idempotency gate (#58).

        ``uq_transactions_credit_payback_unique`` is partial on
        ``is_deleted = FALSE``, so a soft-deleted payback may legally
        coexist with a fresh one.  The idempotency lookup in
        ``mark_as_credit`` must therefore exclude soft-deleted rows: a
        source still in Credit whose only payback has been soft-deleted
        has no live payback, so re-marking must NOT return the dead row.
        With the ``is_deleted`` filter the lookup returns ``None`` and
        the already-not-Projected guard rejects the re-mark loudly
        instead of resurrecting an invisible payback.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Soft-delete the only payback while the source stays Credit --
            # the latent state the partial unique index anticipates.
            payback.is_deleted = True
            db.session.flush()

            # Without the filter this returned the soft-deleted payback as
            # idempotent "success"; with it, the source is Credit with no
            # live payback, so the "only projected" guard fires.
            with pytest.raises(ValidationError, match="Cannot mark a 'Credit'"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

    def test_unmark_credit_ignores_soft_deleted_payback(
        self, app, db, seed_user, seed_periods,
    ):
        """unmark_credit does not hard-delete a soft-deleted payback (#58).

        The payback lookup in ``unmark_credit`` must exclude soft-deleted
        rows to match the partial unique index.  When a source is Credit
        but its only payback has already been soft-deleted, unmark must
        revert the source to Projected and leave the soft-deleted payback
        untouched (preserved for the audit trail) rather than
        hard-deleting it as if it were the live payback.
        """
        with app.app_context():
            txn = self._create_expense(seed_user, seed_periods)
            payback = credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)
            db.session.flush()
            payback_id = payback.id

            payback.is_deleted = True
            db.session.flush()

            credit_workflow.unmark_credit(txn.id, seed_user["user"].id)
            db.session.flush()

            # Source reverted to Projected.
            assert txn.status.name == "Projected"

            # The soft-deleted payback is preserved (NOT hard-deleted):
            # with the filter the lookup returns None so nothing is
            # deleted; without it the dead row would be hard-deleted.
            survived = db.session.get(Transaction, payback_id)
            assert survived is not None
            assert survived.is_deleted is True

    def test_carry_forward_same_period_behavior(self, app, db, seed_user, seed_periods):
        """carry_forward with source == target returns 0 without modifying transactions.

        The guard ``if source_period_id == target_period_id: return 0``
        prevents the function from touching any transactions, which avoids
        the previous bug of setting is_override=True on items that were not
        actually carried forward.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

            # The rule's own row (is_override=False), written by the engine.
            template = make_expense_template(
                db.session, seed_user, amount="50.00",
                name="Template Expense", category_key="Groceries",
            )
            txn_with_template = generate_row_of(template, seed_periods[0])
            # Create a one-off (a rule-less definition's placed row).
            txn_adhoc = one_off_row_of(
                seed_periods[0],
                name="Ad-hoc Expense",
                amount=Decimal("30.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            # Carry forward with source == target -- early return.
            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[0].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            # Guard returns 0 -- no items processed.
            assert count == 0

            # Template-linked item is untouched (is_override stays False).
            db.session.refresh(txn_with_template)
            assert txn_with_template.is_override is False
            assert txn_with_template.pay_period_id == seed_periods[0].id

            # Ad-hoc item is also untouched.
            db.session.refresh(txn_adhoc)
            assert txn_adhoc.pay_period_id == seed_periods[0].id

    def test_mark_credit_on_done_transaction(self, app, db, seed_user, seed_periods):
        """Marking a 'done' transaction as credit raises ValidationError.

        Only projected transactions can be marked as credit. A done
        transaction (already paid) must not be retroactively put on credit,
        which would create a phantom payback for money already spent.
        """
        with app.app_context():
            txn = self._create_expense(
                seed_user, seed_periods, status_name="Paid",
            )

            with pytest.raises(ValidationError, match="Only projected transactions"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

            # Status unchanged -- still 'done'.
            db.session.refresh(txn)
            assert txn.status.name == "Paid"

            # No payback was created.
            payback_count = (
                db.session.query(Transaction)
                .filter_by(credit_payback_for_id=txn.id)
                .count()
            )
            assert payback_count == 0

    def test_mark_credit_on_cancelled_transaction(self, app, db, seed_user, seed_periods):
        """Marking a 'cancelled' transaction as credit raises ValidationError.

        Cancelled transactions must also be rejected by the status guard,
        not just done transactions.
        """
        with app.app_context():
            txn = self._create_expense(
                seed_user, seed_periods, status_name="Cancelled",
            )

            with pytest.raises(ValidationError, match="Only projected transactions"):
                credit_workflow.mark_as_credit(txn.id, seed_user["user"].id)

            # Status unchanged -- still 'cancelled'.
            db.session.refresh(txn)
            assert txn.status.name == "Cancelled"

    # ``test_mark_credit_rejects_shadow_transaction`` stood here until plan
    # step balance:X-bi-6-4d-2 (ruling **R-BAL300**, developer 2026-10-15:
    # "Delete all 27").  It handed ``mark_as_credit`` a transfer's expense
    # SHADOW row and asserted the service's own refusal (M-03, the
    # ``transfer_id`` guard in ``credit_workflow``).  No shadow can exist now:
    # no transaction has a transfer column since plan step X-bi-6-5, and
    # ``ck_transactions_names_no_transfer`` refused a row naming one until
    # then (ruling R-BAL258, pinned once, below that drop, by
    # ``TestTheUpgrade::test_the_database_refuses_a_twin`` in
    # ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``).
    # The guard was reachable by no row, and it went at that step.
