"""The calendar's edge cases, and a transfer drawn as a leg off its parent (leaf X-bi-6-1b)."""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.services.cash_flow_set import CashFlowSet
from app.services.transfer_legs import cell_key
from app.enums import StatusEnum

from app.services import calendar_service, transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    record_paydays_across_a_hole,
    rhythm_of,
)
from app.services.balance_at import BalanceContext
from app.services.calendar_service import _query_transactions_for_range

from ._helpers import _add_transaction


# ── Edge Case Tests ──────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for the calendar service."""

    def test_feb_leap_year(self, app, seed_user, db):
        """Txn due_date Feb 29, 2028 (leap year) appears in day_entries[29]."""
        with app.app_context():
            from app.services import pay_period_service
            # Create a period that overlaps Feb 2028.
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2028, 2, 18),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            db.session.commit()

            _add_transaction(
                db.session, seed_user, periods[0], "Leap Day", "100.00",
                due_date=date(2028, 2, 29),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2028,
                month=2,
                user_settings=None,
            )
            assert 29 in result.day_entries
            assert result.day_entries[29][0].name == "Leap Day"

    def test_a_projected_transfer_is_a_day_cell_on_its_due_day(
        self, app, seed_user, seed_periods, db,
    ):
        """A projected $500.00 checking -> savings transfer due 01-05 is one cell.

        On checking's calendar the transfer is its from-side LEG, on its due
        day, like any other entry: day 5 holds exactly that cell, keyed by the
        pair, $500.00 out and not yet paid, and it is the month's whole
        expense total (no other entry is seeded in January).

        **Re-expressed on a real transfer at plan step balance:X-bi-6-5**
        (finding **BAL-526**).  It was ``test_transfer_shadow_included``,
        which built a plain row named "Transfer Out" with no ``transfer_id``
        and graded that, so the case its name claimed was graded by nothing.
        No transaction names a transfer since that step, and the calendar
        has drawn a transfer as legs read off its parent since leaf
        ``X-bi-6-1b``.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("500.00"), due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            [entry] = result.day_entries[5]
            assert entry.item_key == (transfer.id, checking.id)
            assert entry.name == "Transfer to Savings"
            assert entry.amount == Decimal("500.00")
            assert entry.is_income is False
            assert entry.is_paid is False
            assert result.total_expenses == Decimal("500.00")


class TestATransferIsALegOfItsParent:
    """The calendar draws a transfer as a LEG read off its parent (leaf X-bi-6-1b).

    ``_query_transactions_for_range`` answers the set's OWN rows -- no
    shadow -- then one leg per transfer the set touches, on the side the
    set shows, as one list (plan step plan#2130), and the day fold prices a
    leg through ``contributions_by_key`` keyed by ``cell_key``.  The money
    cases above (R-CC23, the far leg) already grade what a transfer is WORTH
    here; these grade what it IS, and the two states a display leg adds to
    the fold's: settled with a record, and excluded.
    """

    def test_the_loader_answers_own_rows_and_both_sides_legs(
        self, app, seed_user, seed_periods, db,
    ):
        """Each account's load is its own rows and the transfer's leg on its side.

        Checking holds a $300.00 Rent row and the from-side of a $250.00
        checking -> savings transfer; savings holds no row of its own.
        Checking's load answers the Rent row alone and one EXPENSE leg on
        checking; savings' answers no row and one INCOME leg on savings.
        Each is one list, checked by ``cell_key`` (ruling plan#2218, where it
        read the two halves of ``PlanItems``): the same items, in the same
        order, rows then legs.
        Until plan step ``balance:X-bi-6-4d-2`` the case also read that no row
        checking's load answered named a transfer (a shadow); no row can since
        (``ck_transactions_names_no_transfer`` refused one until plan step
        balance:X-bi-6-5 dropped the column).  Renamed from
        ``test_the_loader_answers_own_rows_and_both_sides_legs_and_no_shadow``.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            rent = _add_transaction(
                db.session, seed_user, seed_periods[0], "Rent", "300.00",
                due_date=date(2026, 1, 5),
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"), due_date=date(2026, 1, 6),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            periods = ctx.calendar().overlapping(date(2026, 1, 1), date(2026, 1, 31))

            on_checking = _query_transactions_for_range(
                CashFlowSet.single(checking), ctx.scenario_id, periods,
            )
            assert [cell_key(item) for item in on_checking] == [
                rent.id, (transfer.id, checking.id),
            ]
            assert on_checking[0].name == "Rent"
            assert on_checking[1].is_income is False

            on_savings = _query_transactions_for_range(
                CashFlowSet.single(savings), ctx.scenario_id, periods,
            )
            assert [cell_key(item) for item in on_savings] == [
                (transfer.id, savings.id),
            ]
            assert on_savings[0].is_income is True

    def test_a_settled_leg_is_a_paid_cell_worth_its_record(
        self, app, seed_user, seed_periods, db,
    ):
        """$250.00 planned, $240.00 moved: the cell reads $240.00, paid, keyed by the pair."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
                settled_on=date(2026, 1, 6), due_date=date(2026, 1, 6),
            )
            db.session.commit()

            checking_view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                user_settings=None,
            )
            [entry] = checking_view.day_entries[6]
            assert entry.item_key == (settled.id, checking.id)
            assert entry.name == "Transfer to Savings"
            assert entry.amount == Decimal("240.00")
            assert entry.is_income is False
            assert entry.is_paid is True
            assert entry.is_infrequent is False
            assert checking_view.total_expenses == Decimal("240.00")

            savings_view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                account_id=savings.id, user_settings=None,
            )
            [entry] = savings_view.day_entries[6]
            assert entry.item_key == (settled.id, savings.id)
            assert entry.name == "Transfer from Checking"
            assert entry.amount == Decimal("240.00")
            assert entry.is_income is True
            assert savings_view.total_income == Decimal("240.00")

    def test_a_transfer_into_a_member_off_the_balance_line_is_that_members_cell(
        self, app, seed_user, seed_periods, db,
    ):
        """A $300.00 savings -> card transfer on checking's calendar (set
        ``(checking, card)``): one INCOME cell, the card's leg, 300.00 in --
        R-CC23's one-endpoint arm end to end, the reader-level twin of
        ``test_transfer_legs/test_the_grids_legs.py``'s case (the second
        review of leaf X-bi-6-1b)."""
        with app.app_context():
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("900.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, savings, card, seed_periods[0],
                amount=Decimal("300.00"), due_date=date(2026, 1, 6),
            )
            db.session.commit()

            view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                user_settings=None,
            )
            [entry] = view.day_entries[6]
            assert entry.item_key == (transfer.id, card.id)
            assert entry.is_income is True
            assert entry.amount == Decimal("300.00")
            assert view.total_income == Decimal("300.00")
            assert view.total_expenses == Decimal("0")

    def test_a_cancelled_transfers_leg_is_not_a_cell(
        self, app, seed_user, seed_periods, db,
    ):
        """A $250.00 checking -> savings transfer due 01-06, then cancelled: no cell on the 6th.

        The calendar loads the balance-contributing band
        (``StatusBand.BALANCE_CONTRIBUTING``), and since ruling plan#2262
        deleted the Python re-check in ``_assign_transactions_to_days`` the
        band's clause over ``Transfer`` is the one thing that keeps a
        cancelled transfer's leg off the calendar.  This is the calendar's
        only grade of that clause.  Measured 2026-10-30 with the clause
        removed for ``Transfer`` alone: this case failed and no other in the
        13 test files that drive the calendar did, beside the loader's own
        band case (``test_plan_item_loader.py``) under it.  It failed on the
        cell -- the 6th held a $0.00 "Transfer to Savings" entry -- while the
        month's expenses still read $0.00, because a cancelled leg is worth
        nothing, so the cell assertion is the one that grades the filter.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"), due_date=date(2026, 1, 6),
            )
            db.session.commit()
            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )
            db.session.commit()

            view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                user_settings=None,
            )
            assert 6 not in view.day_entries
            assert view.total_expenses == Decimal("0")
