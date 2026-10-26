"""Ruling R-BAL68: every reader that means a purchase reads purchases, so a kept movement is not
one.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services import (
    carry_forward_service,
    cash_ledger,
    entry_service,
    reconcile_service,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.balance_at import BalanceContext
from app.services.cash_ledger._amounts import _entry_aware_amount, _entry_checking_impact
from app.services.cash_ledger._amount_source import resolve_transaction_amount
from app.services.dashboard_service._bills import _entry_progress_fields
from app.services.dashboard_service._pulse import _row_still_due
from app.services.entry_service import EntryDetails
from app.services.entry_service._sums import build_entry_lists_dict, build_entry_sums_dict
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.reconcile_service._rows import wholly_spent_by
from app.services.settle_day import SettleDay
from tests._test_helpers import create_savings_account, generate_row_of, planted_basis, typed

from ._helpers import _bill, _settle, _revert, _only_movement, _legs_of, _settled_pair


def _reverted_manual_close(seed_user, period, *, budget="100.00", close="120.00",
                           settle_day=None):
    """R-BAL68's worked row: a purchase-tracked envelope, no purchases, closed at
    a typed figure and reverted -- so its kept movement is the only entry.

    Returns ``(envelope, survivor)``.
    """
    envelope = _bill(seed_user, period, budget, is_envelope=True)
    _settle(envelope, submitted=typed(Decimal(close)), settle_day=settle_day)
    db.session.flush()
    _revert(envelope)
    db.session.flush()
    survivor = _only_movement(envelope)
    assert survivor.settled_on is None
    assert survivor.amount == Decimal(close)
    assert [entry.id for entry in envelope.entries] == [survivor.id], (
        "the survivor is the row's only entry"
    )
    return envelope, survivor


def _statement_on(seed_user, observed_on):
    """The owner's Checking statement presented for *observed_on* (the
    account's real assertion, the test's day -- ``test_reconcile_service``'s
    own shape)."""
    account_id = seed_user["account"].id
    return reconcile_service.Statement(
        calendar_for(seed_user["user"].id), account_id,
        replace(cash_ledger.governing_anchor(account_id), observed_on=observed_on),
    )


class TestAKeptMovementIsNotAPurchase:
    """Ruling **R-BAL68**: every purchase-meaning reader reads ``purchases``.

    One control per reader class, each on the same worked row -- a `$100.00`
    envelope with no purchases, closed at a typed `$120.00` and reverted, so
    the movement the revert kept is its only entry -- and each a FIRING
    control: the family reading is asserted beside the purchases reading, so
    a reader that slid back onto ``entries`` fails by the worked figure
    rather than passing over a row where the two agree.
    """

    def test_the_fold_holds_the_plan_not_the_withdrawn_close(
        self, app, seed_user, seed_periods,
    ):
        """A reverted row is worth its PLAN (developer, 2026-08-17)."""
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            basis = planted_basis(envelope)
            assert _entry_aware_amount(envelope, basis) == Decimal("100.00")
            # Over the family the survivor would SPEND the budget -- the close
            # the owner withdrew read as a purchase -- and nothing of the plan
            # would be held back (the unspent-budget reservation, ruling
            # R-BAL77; through X-bi-3e the three-bucket floor read $120.00).
            assert _entry_checking_impact(envelope.entries, Decimal("100.00")) == Decimal("0")

    def test_the_reconcile_panel_neither_offers_nor_stamps_the_survivor(
        self, app, seed_user, seed_periods,
    ):
        """The panel's purchase arm: no phantom `$120.00` offer, no forged tick."""
        with app.app_context():
            close_day = seed_periods[0].start_date + timedelta(days=2)
            envelope, survivor = _reverted_manual_close(
                seed_user, seed_periods[0],
                settle_day=SettleDay(day=close_day, basis=SettledDayBasisEnum.ENTERED),
            )
            statement = _statement_on(seed_user, close_day + timedelta(days=3))
            # Every other clause of the outstanding scope admits it.
            assert survivor.settled_on is None
            assert survivor.is_credit is False
            assert survivor.purchased_on <= statement.observed_on
            offered = {
                group.key: group.purchases
                for group in reconcile_service.outstanding_set(statement).groups
            }
            assert offered.get(envelope.id, ()) == (), (
                "the reverted envelope is offered as a ROW at most, never its close"
            )
            assert reconcile_service.record_settled_days(statement, {survivor.id}) == 0
            db.session.flush()
            assert _only_movement(envelope).settled_on is None
            assert _only_movement(envelope).reconciled_by_id is None

    def test_the_statement_bound_ignores_the_survivors_day(
        self, app, seed_user, seed_periods,
    ):
        """A withdrawn close dated after the statement is no reason to hold the row back."""
        with app.app_context():
            close_day = seed_periods[0].start_date + timedelta(days=5)
            envelope, survivor = _reverted_manual_close(
                seed_user, seed_periods[0],
                settle_day=SettleDay(day=close_day, basis=SettledDayBasisEnum.ENTERED),
            )
            statement = _statement_on(seed_user, close_day - timedelta(days=3))
            assert survivor.purchased_on > statement.observed_on, (
                "over the family the bound would refuse the row"
            )
            assert wholly_spent_by(statement, envelope) is True

    def test_carry_forward_rolls_the_whole_budget(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            preview = carry_forward_service.preview_carry_forward(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            plan = next(p for p in preview.plans if p.item.id == envelope.id)
            assert plan.kind == carry_forward_service.PLAN_KIND_ENVELOPE
            assert plan.entries_sum == Decimal("0.00")
            assert plan.leftover == Decimal("100.00")

    def test_carry_forward_executes_the_whole_budget_and_the_close_withdraws(
        self, app, seed_user, seed_periods,
    ):
        """The execute arm reads the same purchases the preview did."""
        with app.app_context():
            envelope, survivor = _reverted_manual_close(seed_user, seed_periods[0])
            target = generate_row_of(envelope.template, seed_periods[1])
            db.session.flush()
            basis = planted_basis(envelope, target)
            assert resolve_transaction_amount(target, basis) == Decimal("100.00")

            count = carry_forward_service.carry_forward_unpaid(
                seed_periods[0].id, seed_periods[1].id, seed_user["scenario"].id,
                balance_ctx=BalanceContext.build(seed_user["user"].id),
            )
            db.session.flush()

            assert count == 1
            assert resolve_transaction_amount(
                target, planted_basis(envelope, target),
            ) == Decimal("200.00"), "the whole `$100.00` rolled"
            assert envelope.status.is_settled
            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            assert envelope.covering_movements == []
            assert db.session.get(TransactionEntry, survivor.id) is None

    def test_the_dashboards_still_due_reads_the_whole_budget(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            assert _row_still_due(
                envelope, Decimal("100.00"), Decimal("100.00"),
            ) == Decimal("100.00")

    def test_the_grids_sums_list_and_refresh_read_no_purchases(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            budgets = {envelope.id: Decimal("100.00")}
            assert build_entry_sums_dict([envelope], budgets) == {}
            periods = {
                envelope.pay_period_id: calendar_for(
                    seed_user["user"].id,
                ).require_period(FiledRow.for_row(envelope)),
            }
            listed = build_entry_lists_dict(
                [envelope], budgets, periods,
                resolve_owner_cash_flow_set(envelope.user_id),
                envelope.user_id,
            )
            assert listed[envelope.id]["entries"] == []
            assert entry_service.get_entries_for_transaction(
                envelope.id, seed_user["user"].id,
            ) == []

    def test_the_dashboards_progress_reads_no_purchases(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope, _ = _reverted_manual_close(seed_user, seed_periods[0])
            progress = _entry_progress_fields(envelope, Decimal("100.00"))
            assert progress["is_tracked"] is True
            assert progress["entry_count"] == 0
            assert progress["entry_total"] is None

    def test_a_re_settle_after_one_real_purchase_offers_and_books_the_purchase(
        self, app, seed_user, seed_periods,
    ):
        """The offer equals the booking: `$30.00`, not `$150.00` and `$30.00`."""
        with app.app_context():
            envelope, survivor = _reverted_manual_close(seed_user, seed_periods[0])
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("30.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            assert sum(e.amount for e in envelope.entries) == Decimal("150.00"), (
                "the family still holds the withdrawn close"
            )
            basis = planted_basis(envelope)
            assert transaction_service.settle_amount(envelope, basis) == Decimal("30.00")

            _settle(envelope)
            db.session.flush()

            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            # The purchase carries the `$30.00`; the row, closed from its
            # purchases, has no covering movement and is worth nothing of its
            # own (ruling R-BAL81).
            assert envelope.covering_movements == []
            assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0")
            assert [
                cash_ledger.movement_cash_leg(envelope, entry)
                for entry in envelope.entries
            ] == [Decimal("-30.00")]
            assert db.session.get(TransactionEntry, survivor.id) is None

    def test_a_settled_manual_close_envelope_lists_no_purchase(
        self, app, seed_user, seed_periods,
    ):
        """The live class: 8 production envelopes (`$794.79`) read their own close as a purchase."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            _settle(envelope)
            db.session.flush()
            close = _only_movement(envelope)
            assert close.amount == Decimal("100.00")
            budgets = {envelope.id: Decimal("100.00")}
            assert build_entry_sums_dict([envelope], budgets) == {}
            assert _entry_progress_fields(envelope, Decimal("100.00"))["entry_count"] == 0
            assert entry_service.get_entries_for_transaction(
                envelope.id, seed_user["user"].id,
            ) == []

    def test_an_endpoint_move_carries_a_projected_shadows_survivor(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL72: the movement's account is assigned on every side, settled or not.

        Each side is read as its transfer and its record (:func:`_legs_of`)
        since plan step balance:X-bi-6-4d-2, its shadow row until then
        (ruling R-BAL167 classes 1 and 4): the transfer says Projected and
        the to-side's kept record is un-dated.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            other = create_savings_account(
                seed_user, db.session, "Other Savings", Decimal("0.00"),
            )
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            expense, income = _legs_of(xfer)
            survivor = _only_movement(income)
            assert not income.status.is_settled
            assert survivor.settled_on is None
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, to_account_id=other.id,
            )
            db.session.flush()
            expense, income = _legs_of(xfer)
            assert income.account_id == other.id
            assert survivor.account_id == other.id, "in-session, before any expire"
            db.session.expire_all()
            assert db.session.get(TransactionEntry, survivor.id).account_id == other.id
            assert _only_movement(expense).account_id == seed_user["account"].id
