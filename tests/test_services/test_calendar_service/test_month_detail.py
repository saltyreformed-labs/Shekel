"""The calendar's month detail: an empty month, income and expenses, the day each row lands on, no
duplicates, large rows, income and expense classification, deleted rows, category info and the
default account.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum

from app.services import calendar_service
from tests._test_helpers import create_savings_account, create_transfer, one_off_row_of

from ._helpers import _add_transaction


# ── Month Detail Tests ───────────────────────────────────────────────


class TestMonthDetailEmpty:
    """Tests for month detail with no data."""

    def test_month_detail_empty(self, app, seed_user, seed_periods):
        """MonthSummary has zero totals and empty collections when no txns exist."""
        with app.app_context():
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=4,
                user_settings=None,
            )
            assert result.total_income == Decimal("0")
            assert result.total_expenses == Decimal("0")
            assert result.net == Decimal("0")
            assert result.day_entries == {}


class TestMonthDetailIncomeAndExpenses:
    """Tests for month detail with income and expense transactions."""

    def test_month_detail_income_and_expenses(self, app, seed_user, seed_periods, db):
        """Two periods in January with $2000 income and $500 expense each.

        Expected: total_income=4000, total_expenses=1000, net=3000.
        """
        with app.app_context():
            # seed_periods starts Jan 2, 2026 with 10 biweekly periods.
            # Period 0: Jan 2 - Jan 15, Period 1: Jan 16 - Jan 29
            p0 = seed_periods[0]
            p1 = seed_periods[1]

            _add_transaction(
                db.session, seed_user, p0, "Paycheck 1", "2000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, p0, "Rent", "500.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p1, "Paycheck 2", "2000.00",
                is_income=True, due_date=date(2026, 1, 16),
            )
            _add_transaction(
                db.session, seed_user, p1, "Utilities", "500.00",
                due_date=date(2026, 1, 20),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert result.total_income == Decimal("4000.00")
            assert result.total_expenses == Decimal("1000.00")
            assert result.net == Decimal("3000.00")


class TestDayAssignment:
    """Tests for transaction-to-day assignment logic."""

    def test_day_assignment_from_due_date(self, app, seed_user, seed_periods, db):
        """Transaction with due_date=Jan 15 appears in day_entries[15]."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Bill", "100.00",
                due_date=date(2026, 1, 15),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert 15 in result.day_entries
            assert len(result.day_entries[15]) == 1
            assert result.day_entries[15][0].name == "Bill"

    def test_day_assignment_paycheck_pattern(self, app, seed_user, seed_periods, db):
        """Txn with due_date=period start_date appears on that day."""
        with app.app_context():
            p0 = seed_periods[0]  # starts Jan 2
            _add_transaction(
                db.session, seed_user, p0, "Paycheck", "2000.00",
                is_income=True, due_date=p0.start_date,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert 2 in result.day_entries
            assert result.day_entries[2][0].name == "Paycheck"

    def test_due_date_none_fallback(self, app, seed_user, seed_periods, db):
        """An UNDATED transfer's leg falls back to period.start_date.day.

        The undated entry is the from-side leg of a transfer made without a
        due date (the transfer form leaves it optional); a leg's due date is
        its parent's.  Every one-off is dated on its paycheck's start since
        plan step balance:X-bi-7b, and the cutover (balance:X-bi-7d-2) dated
        production's remaining undated one-offs the same way, so an undated
        transfer is what still reaches the ``None`` arm of
        ``attribution_day`` at this door.  (A LEGACY link-less row was the
        specimen until that cutover.  The calendar has drawn a transfer as
        legs read off its parent, no shadow, since plan step
        ``balance:X-bi-6-1b``; the case called its undated entry the
        transfer's expense shadow, and read that shadow's due date, until plan
        step ``balance:X-bi-6-4d-2``'s vacuous-residue checkpoint.)  The case
        reads checking's calendar, so it grades the from-side leg only.
        """
        with app.app_context():
            p0 = seed_periods[0]  # starts Jan 2
            jar = create_savings_account(
                seed_user, db.session, "Jar", Decimal("500.00"),
            )
            xfer = create_transfer(
                seed_user, seed_user["account"], jar, p0,
                amount=Decimal("50.00"),
            )
            db.session.commit()
            assert xfer.due_date is None, "the plant: the transfer is undated"

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # Should fall back to period start_date (Jan 2).
            assert 2 in result.day_entries
            names = [e.name for e in result.day_entries[2]]
            assert "Transfer to Jar" in names

    def test_day_entries_sorted_by_amount(self, app, seed_user, seed_periods, db):
        """Multiple txns on the same day sorted by abs(amount) descending."""
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Small", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p0, "Large", "500.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, p0, "Medium", "200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            entries = result.day_entries[5]
            amounts = [e.amount for e in entries]
            # Sorted descending by abs(amount): 500, 200, 100.
            assert amounts == [Decimal("500.00"), Decimal("200.00"), Decimal("100.00")]


class TestNoDuplicates:
    """Tests ensuring no double-counting across period boundaries."""

    def test_out_of_period_due_date_clamps_into_its_period_month(
        self, app, seed_user, seed_periods, db,
    ):
        """A due_date past its period end is counted once, in its period.

        The transaction belongs to period 1 (Jan 16 - Jan 29) but carries a
        stray Feb 1 due_date (outside its own period).  The clamped
        attribution rule pulls it to the period end (Jan 29), so it is
        counted ONCE, in January (its period's month) -- never in February,
        and never in both.  This is the locked clamp behavior that keeps the
        daily balance reconciling with the grid: a period's flow always
        closes by its own end_date.
        """
        with app.app_context():
            p1 = seed_periods[1]  # Jan 16 - Jan 29
            _add_transaction(
                db.session, seed_user, p1, "Stray Bill", "300.00",
                due_date=date(2026, 2, 1),
            )
            db.session.commit()

            jan = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            feb = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=2,
                user_settings=None,
            )
            # Clamped to period 1's end (Jan 29): counted in January...
            assert jan.total_expenses == Decimal("300.00")
            assert 29 in jan.day_entries
            # ...and NOT in February (its period does not reach February).
            assert feb.total_expenses == Decimal("0")

    def test_no_double_counting_same_month(self, app, seed_user, seed_periods, db):
        """Same txn in two overlapping periods counted exactly once."""
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Unique", "100.00",
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # Only one entry should exist.
            all_entries = [
                e for entries in result.day_entries.values() for e in entries
            ]
            ids = [e.item_key for e in all_entries]
            assert len(ids) == len(set(ids)), "Duplicate transaction IDs found"


class TestLargeTransactions:
    """Tests for large transaction flagging."""

    def test_large_transaction_flagging(self, app, seed_user, seed_periods, db):
        """Txn $600 with threshold=500 is flagged as large."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Big Bill", "600.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                large_threshold=500,
                user_settings=None,
            )
            entry = result.day_entries[5][0]
            assert entry.is_large is True

    def test_large_threshold_boundary(self, app, seed_user, seed_periods, db):
        """Txn exactly $500 with threshold=500 is flagged (>= not >)."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Exact", "500.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                large_threshold=500,
                user_settings=None,
            )
            assert result.day_entries[5][0].is_large is True

    def test_below_threshold_not_large(self, app, seed_user, seed_periods, db):
        """Txn $499 with threshold=500 is NOT flagged."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Small", "499.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                large_threshold=500,
                user_settings=None,
            )
            assert result.day_entries[5][0].is_large is False


class TestIncomeExpenseClassification:
    """Tests for income vs expense classification."""

    def test_income_vs_expense_classification(self, app, seed_user, seed_periods, db):
        """Income txn counted in total_income, expense in total_expenses."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Salary", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Rent", "1200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert result.total_income == Decimal("3000.00")
            assert result.total_expenses == Decimal("1200.00")

            # Check is_income flags on entries.
            income_entries = [
                e for entries in result.day_entries.values()
                for e in entries if e.is_income
            ]
            expense_entries = [
                e for entries in result.day_entries.values()
                for e in entries if not e.is_income
            ]
            assert len(income_entries) == 1
            assert len(expense_entries) == 1

    def test_day_totals_fold_per_day(self, app, seed_user, seed_periods, db):
        """day_totals holds the per-day (income, expense) fold; month derives from it.

        Day 2 carries a $3,000 income and a $400 expense; day 5 carries a
        $1,200 expense only.  Per-day folds: day 2 = (3000.00, 400.00),
        day 5 = (0, 1200.00).  The expense-only day's income leg must be a
        Decimal, not an int 0 (money is always Decimal).  The month
        headline totals are the sum of the per-day folds: income 3000.00,
        expenses 400.00 + 1200.00 = 1600.00 -- so the per-day cells the
        route renders and the month total cannot diverge.
        """
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Salary", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Groceries", "400.00",
                due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Rent", "1200.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert result.day_totals[2] == (Decimal("3000.00"), Decimal("400.00"))
            assert result.day_totals[5] == (Decimal("0"), Decimal("1200.00"))
            # The expense-only day's income leg is a Decimal zero, not int 0.
            income_leg, _expense_leg = result.day_totals[5]
            assert isinstance(income_leg, Decimal)
            # Month headline totals are the sum of the per-day folds.
            assert result.total_income == Decimal("3000.00")
            assert result.total_expenses == Decimal("1600.00")


class TestDeletedTransactions:
    """Tests for soft-deleted transaction exclusion."""

    def test_deleted_transactions_excluded(self, app, seed_user, seed_periods, db):
        """Soft-deleted transactions do not appear in results."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Active", "100.00",
                due_date=date(2026, 1, 5),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Deleted", "200.00",
                due_date=date(2026, 1, 5), is_deleted=True,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            all_entries = [
                e for entries in result.day_entries.values() for e in entries
            ]
            names = [e.name for e in all_entries]
            assert "Active" in names
            assert "Deleted" not in names


class TestCategoryInfo:
    """Tests for category info on DayEntry."""

    def test_category_info_on_day_entry(self, app, seed_user, seed_periods, db):
        """DayEntry carries category group and item from the transaction."""
        with app.app_context():
            cat = seed_user["categories"]["Car Payment"]
            one_off_row_of(
                seed_periods[0],
                name="Car Payment",
                amount=Decimal("350.00"),
                user_id=seed_periods[0].user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=cat.id,
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            entry = result.day_entries[10][0]
            assert entry.category_group == "Auto"
            assert entry.category_item == "Car Payment"


class TestDefaultAccount:
    """Tests for default account resolution."""

    def test_month_detail_default_account(self, app, seed_user, seed_periods, db):
        """Uses default checking account when no account_id is passed."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Test", "100.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            # No account_id passed -- should use seed_user's checking account.
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert result.total_expenses == Decimal("100.00")
