"""
Shekel Budget App -- Calendar Service Tests

Tests for the calendar service engine: month detail computation,
year overview aggregation, day assignment from due_date, large and
infrequent transaction detection, 3rd paycheck month identification,
and projected month-end balance calculation.
"""

from calendar import monthrange
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.services.cash_flow_set import CashFlowSet
from app.exceptions import BaselineMissingError
from app.enums import StatusEnum, TxnTypeEnum
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from unittest.mock import patch

import pytest

from app.services import (
    balance_at,
    calendar_infrequency,
    calendar_service,
    paycheck_calculator,
    status_seam,
    transfer_service,
)
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    eras_of,
    generate_row_of,
    last_covered_day,
    one_off_row_of,
    record_paydays_across_a_hole,
    restate_fixture_era,
    rhythm_of,
    set_default_grid_account,
    settle_day_columns,
    cover_bare_settled_row,
    start_test_pay_list,
    state_template_price,
)
from tests._test_helpers import default_settle_day, make_cadence_rule
from tests._test_helpers import (
    add_entry,
    create_envelope_txn,
    settlement_if_settling,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at import _context as resolution_context
from app.services.calendar_infrequency import is_infrequent as _is_infrequent
from app.services.pay_rhythm import FixedDays
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.calendar_service import (
    CalendarAccountNotResolvableError,
    DailyView,
    _query_transactions_for_range,
)
from app.services.pay_calendar import (
    PayCadence,
    PayCalendar,
    calendar_for,
    paydays_in_month_through,
    saved_paydays_in_month_through,
)

#: The cadence ``seed_periods`` builds: 14 days between paydays, 26 a year.
#: An explicit input to the infrequent badge since plan step R7a-2b, where the
#: predicate was an enumerated set of pattern names that could not vary by
#: owner at all.
_BIWEEKLY = PayCadence(FixedDays(14))

#: A monthly-paid owner: 30 days between paydays, 12 a year.  The cadence that
#: makes "every 2 paychecks" a DIFFERENT answer from the biweekly one.
_MONTHLY_PAID = PayCadence(FixedDays(30))


# ── Helpers ──────────────────────────────────────────────────────────


def _income_type_id(db_session):
    """Get the Income transaction type ID from the database."""
    return ref_cache.txn_type_id(TxnTypeEnum.INCOME)


def _expense_type_id(db_session):
    """Get the Expense transaction type ID from the database."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _add_transaction(
    db_session, seed_user, period, name, amount,
    is_income=False, due_date=None,
    is_deleted=False, status=StatusEnum.PROJECTED, settled_amount=None,
):
    """Create a ONE-OFF transaction for testing.

    It took a ``template`` to link until plan step balance:X-cf-3; a row of
    a RECURRING definition is the engine's (:func:`generate_row_of`), and
    since plan step balance:X-bi-7c this builder places a one-off through
    the producer (:func:`one_off_row_of`), dated on its paycheck's start
    unless *due_date* says otherwise -- a case that means an UNDATED row
    builds a row no definition prices (a transfer shadow here; a CC payback
    is the other, and the one production holds undated), the shapes left
    undated since the family's cutover (plan step balance:X-bi-7d-2).

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        period: PayPeriod to assign to.
        name: Transaction name.
        amount: Estimated amount (Decimal or str).
        is_income: Whether this is income (default expense).
        due_date: Optional due_date override.
        is_deleted: Soft-delete flag.
        status: StatusEnum member; defaults to PROJECTED.  Mixed-status
            calendar tests (F-3 / W-065) pass DONE, CANCELLED, CREDIT
            to assert the balance-contributing predicate filters them
            correctly.  They passed SETTLED -- the terminal archive -- until
            plan step **balance:X-am** deleted that status; the predicate
            under test reads ``excludes_from_balance`` and never a member, so
            any settled status is the same specimen.
        actual_amount: Optional realized amount.  Required for a SETTLED status
            so ``effective_amount`` returns the realized hit rather than
            falling back to ``estimated_amount``.

    Returns:
        The created Transaction.
    """
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    status_id = ref_cache.status_id(status)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=type_id,
        due_date=due_date,
    )
    txn.status_id = status_id
    txn.is_deleted = is_deleted
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(default_settle_day(period, status_id)).items():
        setattr(txn, _column, _value)
    db_session.flush()
    if default_settle_day(period, status_id) is not None:
        cover_bare_settled_row(db_session, txn, amount, settled_amount)
    return txn


def _make_template_with_cadence(
    db_session, seed_user, cadence, interval_n=1, *, name="Template",
):
    """Create a priced template with a recurrence rule of the given cadence.

    **The rule's first occurrence is the schedule's OPENING payday whatever
    the cadence**, so every definition built here -- annual, quarterly,
    every third paycheck -- names an occurrence in ``seed_periods[0]`` and
    :func:`generate_row_of` writes its row there (plan step balance:X-cf).
    The infrequency badge reads the CADENCE and nothing about the row's
    date, so which day the cadence anchors on is not a fact these cases
    grade; what they need is one paycheck every cadence fires in.

    Args:
        db_session: Active database session.
        seed_user: The seed_user fixture dict.
        cadence: A ``tests.oracles.recurrence_baseline`` cadence constant.
            A definition that does not repeat -- which since plan step
            R2e-3 means it names NO rule at all -- is built by a case that
            generates under a cadence and then CLEARS it, the way the edit
            door does (``_clear_recurrence_rule``), since a rule-less
            definition has rows only that way.
        interval_n: The authored interval, read only by the cadence that
            fixes none of its own.
        name: The definition's name, which its rows carry.

    Returns:
        The created TransactionTemplate.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=list(seed_user["categories"].values())[0].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=Decimal("100.00"),
    )
    db_session.add(template)
    db_session.flush()
    state_template_price(template)
    # The definition first, then the cadence onto it (plan step R-F6).
    make_cadence_rule(
        template, cadence, interval_n=interval_n,
        starts_on=calendar_for(seed_user["user"].id).opening_bound(),
    )
    return template


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
                seed_user, db.session, seed_user["account"], jar, p0,
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


class TestTheCalendarReadsTheCashFlowSet:
    """The day cells hold the paycheck's rows across the set (plan step CC-4-3).

    Developer ruling ``credit_card:R-CC16``: the phone bill that is always
    paid by card is a row ON the card, so a calendar that read one account's
    rows dropped it from the month it is due in.  The rows are the owner's
    cash-flow set's now, through the one clause
    (:func:`~app.services.cash_flow_set.own_rows_clause` for the rows and
    :func:`~app.services.cash_flow_set.leg_accounts_shown` for a transfer's leg,
    since leaf ``balance:X-bi-6-1b``; it was ``paycheck_rows_clause``); the balance
    line, the month-end figure and the scope name stay ONE member's, and
    ``account_id`` names that member the way the grid's override does.

    April flows, dated forward of the suite's frozen today (2026-03-20) so a
    projected row lands on its own day (ruling R-G): Rent $300.00 on
    checking due 04-02, Phone $45.00 on the card due 04-05, both in period 6
    (Mar 27 -- Apr 9).  Checking's anchor is $1000.00 (the seed); the card's
    is -$500.00 asserted the day before today (the factory).  Month-end on
    checking's line: 1000.00 - 300.00 = 700.00; on the card's:
    -500.00 - 45.00 = -545.00.  Every case plants a card, because an owner
    with none is a set of one and cannot tell the clause from the filter.
    """

    @staticmethod
    def _card(seed_user, db_session):
        """Create an active Credit Card account for *seed_user*."""
        return create_account_of_type(
            seed_user, db_session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )

    @staticmethod
    def _expense_on(seed_user, period, account, name, amount, due_date):
        """Place a projected one-off EXPENSE row on *account* in *period*."""
        return one_off_row_of(
            period, name=name, amount=Decimal(amount),
            user_id=seed_user["user"].id, account_id=account.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            due_date=due_date,
        )

    def _april(self, seed_user, seed_periods, db_session, card):
        """Plant the class docstring's two April rows."""
        self._expense_on(
            seed_user, seed_periods[6], seed_user["account"], "Rent",
            "300.00", date(2026, 4, 2),
        )
        self._expense_on(
            seed_user, seed_periods[6], card, "Phone", "45.00",
            date(2026, 4, 5),
        )
        db_session.commit()

    def test_a_bill_on_the_card_is_on_the_months_calendar(
        self, app, seed_user, seed_periods, db,
    ):
        """The card's row is a day cell and a month total; the line is checking's.

        total_expenses = 300.00 + 45.00 = 345.00; the 5th holds "Phone";
        ``account_name`` is Checking and the month-end balance is checking's
        700.00 -- the rows are the set's, the balance one member's.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            self._april(seed_user, seed_periods, db.session, card)

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 3, 20),
                user_settings=None,
            )
            assert result.total_expenses == Decimal("345.00")
            assert [e.name for e in result.day_entries[5]] == ["Phone"]
            assert result.account_name == "Checking"
            assert result.projected_end_balance == Decimal("700.00")
            assert result.daily.daily_balances[30] == Decimal("700.00")

    def test_a_member_named_by_account_id_goes_on_the_line(
        self, app, seed_user, seed_periods, db,
    ):
        """``account_id=<card>`` is the same paycheck, seen from the card.

        The rows are unchanged (345.00 of expenses, Rent still on the 2nd);
        the scope name and the month-end balance are the card's, -545.00.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            self._april(seed_user, seed_periods, db.session, card)

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                account_id=card.id, today=date(2026, 3, 20),
                user_settings=None,
            )
            assert result.total_expenses == Decimal("345.00")
            assert [e.name for e in result.day_entries[2]] == ["Rent"]
            assert result.account_name == "Rewards Card"
            assert result.projected_end_balance == Decimal("-545.00")
            assert result.daily.daily_balances[30] == Decimal("-545.00")

    def test_a_transfer_between_members_shows_once_from_the_lines_side(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-CC23 on the calendar: the far leg is not a day cell.

        A $165.00 checking -> card payment due 04-08: on checking's calendar
        it is one expense (165.00 out, 0.00 in); on the card's it is one
        income (165.00 in, 0.00 out).  Loading every member's rows without
        the far-leg arm would put BOTH shadows on both calendars -- 165.00
        in and 165.00 out on each -- the owner's own payment read as income.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            create_transfer(
                seed_user, db.session, seed_user["account"], card,
                seed_periods[6], amount=Decimal("165.00"),
                due_date=date(2026, 4, 8),
            )
            db.session.commit()

            checking_view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                user_settings=None,
            )
            card_view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                account_id=card.id,
                user_settings=None,
            )
            assert checking_view.total_expenses == Decimal("165.00")
            assert checking_view.total_income == Decimal("0")
            assert card_view.total_income == Decimal("165.00")
            assert card_view.total_expenses == Decimal("0")

    def test_an_account_outside_the_set_is_its_own_calendar(
        self, app, seed_user, seed_periods, db,
    ):
        """``account_id=<savings>`` is the single-account calendar as before.

        A $75.00 fee on savings due 04-03 beside the class's two rows: the
        savings calendar holds 75.00 of expenses and neither Rent nor Phone;
        checking's holds 345.00 and not the fee.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            self._expense_on(
                seed_user, seed_periods[6], savings, "Fee", "75.00",
                date(2026, 4, 3),
            )
            self._april(seed_user, seed_periods, db.session, card)

            savings_view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                account_id=savings.id,
                user_settings=None,
            )
            checking_view = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                user_settings=None,
            )
            assert savings_view.total_expenses == Decimal("75.00")
            assert savings_view.account_name == "Savings"
            assert checking_view.total_expenses == Decimal("345.00")

    def test_an_archived_owned_account_is_refused_not_fallen_through(
        self, app, seed_user, seed_periods, db,
    ):
        """An explicit id the admission test refuses raises, as a loan's does.

        The analytics policy the set twin keeps: a question about THAT
        account, never answered with the primary's calendar.  The loan case
        is graded end to end in ``test_routes/test_analytics.py``; this is
        the archived arm, which nothing else pins.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Old Savings", Decimal("0.00"),
            )
            savings.is_active = False
            db.session.commit()

            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id, year=2026, month=4,
                    account_id=savings.id,
                    user_settings=None,
                )

    def test_the_default_reads_the_saved_default_grid_account(
        self, app, seed_user, seed_periods, db,
    ):
        """The default is the set's PRIMARY, read off the settings row passed.

        ``resolve_analytics_account`` read "first active checking" and no
        settings row, so a saved ``default_grid_account_id`` moved the grid
        and not the calendar.  With the row passed, an HYSA saved as the
        default is the balance line; with ``None`` the chain runs without
        that layer and checking answers.
        """
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Rainy Day",
                anchor_balance=Decimal("1000.00"),
            )
            settings = set_default_grid_account(
                db.session, seed_user["user"].id, hysa.id,
            )
            db.session.commit()

            with_layer = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                user_settings=settings,
            )
            without = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                user_settings=None,
            )
            assert with_layer.account_name == "Rainy Day"
            assert without.account_name == "Checking"

    def test_the_year_overview_reads_the_same_set(
        self, app, seed_user, seed_periods, db,
    ):
        """The year's twelve months are the set's rows behind one line.

        April's summary carries 345.00 of expenses on checking's line and,
        with ``account_id=<card>``, the same 345.00 on the card's line with
        the card's name.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            self._april(seed_user, seed_periods, db.session, card)

            checking_year = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2026,
                user_settings=None,
            )
            card_year = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2026, account_id=card.id,
                user_settings=None,
            )
            assert checking_year.months[3].total_expenses == Decimal("345.00")
            assert checking_year.months[3].account_name == "Checking"
            assert card_year.months[3].total_expenses == Decimal("345.00")
            assert card_year.months[3].account_name == "Rewards Card"


class TestPaycheckDays:
    """Tests for paycheck_days population."""

    def test_paycheck_days_populated(self, app, seed_user, seed_periods):
        """Paycheck days reflect period start_dates in the target month.

        seed_periods: 10 biweekly periods starting Jan 2, 2026.
        Three periods start in January: Jan 2, Jan 16, Jan 30.
        """
        with app.app_context():
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert result.paycheck_days == [2, 16, 30]


class TestMonthEndBalance:
    """Tests for projected month-end balance calculation."""

    def test_month_end_balance(self, app, seed_user, seed_periods, db):
        """Projected end balance reflects anchor + income - expenses.

        Anchor balance = $1000 (seed_user).
        seed_periods are 10 biweekly starting 2026-01-02 so:
          Period 6: Mar 27 -- Apr 9
          Period 7: Apr 10 -- Apr 23
          Period 8: Apr 24 -- May 7  (contains Apr 30)
        The month-end balance is the seam's cash-flow scalar at
        ``2026-04-30``, which projects forward through the period
        CONTAINING Apr 30 (period 8), not the "last period whose end_date <=
        Apr 30" (period 7).  Period 8 has no transactions here so the
        projected balance carries forward unchanged from period 7's
        4000.00, which keeps this assertion valid; the next test
        proves the producer steps into period 8 when it has data.

        Period 6: 1000 + 2000 - 500 = 2500.
        Period 7: 2500 + 2000 - 500 = 4000.
        Period 8: 4000 + 0 - 0      = 4000  (no txns)
        Month-end (Apr 30, falls in period 8): 4000.00.

        **The flows are seeded AFTER the suite's frozen today (2026-03-20)
        deliberately** (plan step X-c2b2, ruling R-G).  A still-Projected row
        whose date has passed lands at ``max(its date, as_of + 1 day)`` -- a
        plan cannot have already happened -- so a January-dated projected bill
        read in March contributes to March, not to January, and a January
        month-end would correctly read the anchor flat.  Dating the fixture
        forward is what makes each row land on its own day and keeps the
        hand arithmetic above the arithmetic the producer actually performs.
        """
        with app.app_context():
            p6 = seed_periods[6]
            p7 = seed_periods[7]

            _add_transaction(
                db.session, seed_user, p6, "Pay 1", "2000.00",
                is_income=True, due_date=date(2026, 4, 1),
            )
            _add_transaction(
                db.session, seed_user, p6, "Rent", "500.00",
                due_date=date(2026, 4, 5),
            )
            _add_transaction(
                db.session, seed_user, p7, "Pay 2", "2000.00",
                is_income=True, due_date=date(2026, 4, 13),
            )
            _add_transaction(
                db.session, seed_user, p7, "Util", "500.00",
                due_date=date(2026, 4, 20),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=4,
                user_settings=None,
            )
            assert result.projected_end_balance == Decimal("4000.00")

    def test_month_end_balance_includes_straddling_period(
        self, app, seed_user, seed_periods, db,
    ):
        """C9-1 (calendar surface): month-end mid-period includes that period.

        HIGH-02 / W-277: pre-Commit-9 the calendar selected the last
        pay period whose ``end_date <= last_day_of_month`` and
        returned that period's end balance, missing the contribution
        of the period that straddles the month boundary.
        Post-Commit-9 the month-end balance flows through the seam's
        ``balance_at.cash_balance_at``, which values the target DATE itself
        (the cash fold sampled there) rather than a period boundary near it.

        seed_periods:
          Period 7: Apr 10 -- Apr 23
          Period 8: Apr 24 -- May 7  (contains Apr 30)

        Setup loads income/expense in BOTH periods so the
        pre-Commit-9 path (which would stop after period 7) and the
        post-Commit-9 path (which includes period 8) produce
        distinct values; the assertion locks the correct one.  The month is
        forward of the suite's frozen today for the reason the previous test
        documents (ruling R-G).

        Hand arithmetic (no entries, formula collapses to
        effective_amount; statuses are Projected so the
        balance-contributing predicate includes them):
          Anchor (1000.00):  1000
          Period 7:          1000 + 1500 - 200 = 2300
          Period 8:          2300 + 1500 - 200 = 3600

        Pre-Commit-9 would have returned 2300.00 (period 7 end);
        post-Commit-9 must return 3600.00.  Re-pinned per
        HIGH-02 / W-277.
        """
        with app.app_context():
            p7 = seed_periods[7]
            p8 = seed_periods[8]
            assert last_covered_day(p7) == date(2026, 4, 23)
            assert p8.start_date == date(2026, 4, 24)
            assert last_covered_day(p8) == date(2026, 5, 7)

            _add_transaction(
                db.session, seed_user, p7, "Mid-Apr Pay", "1500.00",
                is_income=True, due_date=date(2026, 4, 13),
            )
            _add_transaction(
                db.session, seed_user, p7, "Mid-Apr Bill", "200.00",
                due_date=date(2026, 4, 17),
            )
            _add_transaction(
                db.session, seed_user, p8, "Late-Apr Pay", "1500.00",
                is_income=True, due_date=date(2026, 4, 24),
            )
            _add_transaction(
                db.session, seed_user, p8, "Late-Apr Bill", "200.00",
                due_date=date(2026, 4, 24),
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=4,
                user_settings=None,
            )
            # 1000 + (1500-200) + (1500-200) = 3600.00.
            # Pre-Commit-9 returned 2300.00 -- HIGH-02 / W-277.
            assert result.projected_end_balance == Decimal("3600.00")


# ── Infrequency Tests ────────────────────────────────────────────────


class TestIsInfrequent:
    """Tests for the _is_infrequent helper.

    **The predicate is DERIVED since plan step R7a-2b** -- "fires fewer than
    twelve times a year" -- where it was a hand-enumerated set of three
    pattern members.  Every assertion below is unchanged: the derivation has
    to reproduce all six answers before it is allowed to extend past them,
    and ``TestInfrequencyIsDerivedNotEnumerated`` is where it does.
    """

    def test_infrequent_annual(self, app, seed_user, seed_periods, db):
        """Template with Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, ANNUAL, name="Annual",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_infrequent_quarterly(self, app, seed_user, seed_periods, db):
        """Template with Quarterly pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, QUARTERLY, name="Quarterly",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_infrequent_semi_annual(self, app, seed_user, seed_periods, db):
        """Template with Semi-Annual pattern is infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, SEMI_ANNUAL, name="Semi",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_non_repeating_not_infrequent(self, app, seed_user, seed_periods, db):
        """A template that does not repeat is NOT infrequent.

        "Infrequent" means "less frequent than monthly", which is a statement
        about a CADENCE; a definition that does not repeat has none, so the
        badge does not apply.  Until plan step R2e-3 the two template kinds
        disagreed about this: a one-time TRANSACTION was already rule-less and
        answered False here, while a one-time TRANSFER carried a ``Once``
        rule, which was a member of ``_INFREQUENT_PATTERNS`` and answered
        True.  Retiring the pattern is what made them agree.
        """
        with app.app_context():
            # A rule-less definition holds rows only one way: they were
            # generated under a cadence the owner then CLEARED, which is the
            # edit door's act (``_clear_recurrence_rule``: dis-associate,
            # and delete-orphan removes the rule).
            template = _make_template_with_cadence(
                db.session, seed_user, EVERY_PERIOD, name="One-time",
            )
            txn = generate_row_of(template, seed_periods[0])
            template.recurrence_rule = None
            db.session.commit()
            assert template.recurrence_rule is None
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_monthly_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Monthly pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, MONTHLY, name="Monthly",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_every_period_not_infrequent(self, app, seed_user, seed_periods, db):
        """Template with Every Period pattern is NOT infrequent."""
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, EVERY_PERIOD, name="Each Period",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_no_template_not_infrequent(self, app, seed_user, seed_periods, db):
        """Manual transaction (template=None) is NOT infrequent."""
        with app.app_context():
            txn = _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=date(2026, 1, 5),
            )
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False


class TestInfrequencyIsDerivedNotEnumerated:
    """What the three-member set could not answer, and now does (R7a-2b).

    The badge asked "is this pattern one of Quarterly / Semi-Annual /
    Annual".  It now asks "does this fire fewer than twelve times a year",
    which is the same answer for those three and a DIFFERENT one for two
    classes the enumeration structurally could not reach.
    """

    def test_every_three_paychecks_is_infrequent(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 3 paychecks fires 8.67 times a year, so it IS infrequent.

        The enumeration said otherwise -- ``EVERY_N_PERIODS`` was not one of
        its three members, so an every-3-paychecks bill rendered as a frequent
        flow whatever its interval.  Hand-computed: 26 / 3 = 8.67 < 12.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=3, name="Every 3rd",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is True

    def test_every_two_paychecks_is_frequent_at_a_biweekly_cadence(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 2 paychecks is 13 a year biweekly -- just over the line.

        The boundary case, and the control on the one above: 26 / 2 = 13,
        which is NOT fewer than 12, so the badge does not apply.  A rule that
        tested ``<=`` would fail here.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2, name="Every 2nd",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False

    def test_the_same_rule_answers_differently_for_a_monthly_paid_owner(
        self, app, seed_user, seed_periods, db,
    ):
        """Every 2 paychecks is 6 a year when you are paid monthly.

        The badge is per-OWNER now, which an enumeration of pattern names
        could not be: the identical row is frequent for a biweekly owner
        (13 a year) and infrequent for a monthly-paid one (12 / 2 = 6).  The
        PAIR is the assertion -- either alone would pass against a constant.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2, name="Every 2nd",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            assert _is_infrequent(txn, _BIWEEKLY) is False
            assert _is_infrequent(txn, _MONTHLY_PAID) is True

    def test_a_monthly_bill_is_never_infrequent_at_any_cadence(
        self, app, seed_user, seed_periods, db,
    ):
        """A MONTH-unit cadence fires 12 times a year whoever owns it.

        The control that the per-owner half is scoped to paycheck space: a
        monthly bill is monthly for a weekly-paid owner too, so no cadence may
        move this answer.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user, MONTHLY, name="Monthly",
            )
            txn = generate_row_of(template, seed_periods[0])
            db.session.commit()
            for cadence in (_BIWEEKLY, _MONTHLY_PAID, PayCadence(FixedDays(7))):
                assert _is_infrequent(txn, cadence) is False


class TestTheBadgeReadsTheOWNERSStoredCadence:
    """The thread from the stored schedule to the rendered day entry.

    **Every other test in this file hands ``_is_infrequent`` a hand-built
    ``PayCadence``, so all of them would still pass if ``badge_cadence``
    returned a hardcoded biweekly value** -- which is exactly the defect class
    plan step R7a-2a existed to remove, one layer up.  An adversarial review of
    R7a-2b named the hole; this is the test that closes it, by moving the
    STORED ``pay_schedule.cadence_days`` and asserting the rendered badge
    follows.
    """

    def test_the_same_row_badges_differently_when_the_schedule_moves(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-2-paychecks bill: frequent at 14 days, infrequent at 30.

        Hand-computed: 26 / 2 = 13 a year biweekly (not fewer than 12, so no
        badge) against 12 / 2 = 6 a year for a monthly-paid owner (badged).
        Nothing about the transaction changes between the two reads -- only
        the owner's stored cadence -- so a hardcoded count fails here whatever
        value it is hardcoded to.
        """
        with app.app_context():
            template = _make_template_with_cadence(
                db.session, seed_user,
                EVERY_N_PERIODS, interval_n=2, name="Every 2nd",
            )
            generate_row_of(template, seed_periods[0])
            db.session.commit()
            month = seed_periods[0].start_date

            def _badges():
                summary = calendar_service.get_month_detail(
                    seed_user["user"].id, month.year, month.month,
                    user_settings=None,
                )
                return [
                    entry.is_infrequent
                    for entries in summary.day_entries.values()
                    for entry in entries
                    if entry.name == "Every 2nd"
                ]

            restate_fixture_era(seed_user["user"].id, date(2026, 1, 2), 14)
            db.session.commit()
            assert _badges() == [False]

            restate_fixture_era(seed_user["user"].id, date(2026, 1, 2), 30)
            db.session.commit()
            assert _badges() == [True]

    def test_a_month_of_manual_rows_resolves_no_cadence_at_all(
        self, app, db, seed_user, seed_periods,
    ):
        """Nothing repeats, so the build must not read the pay schedule.

        ``badge_cadence``'s contract is "a page must not fail for a fact it
        does not use", and the predicate that enforces it tests the recurrence
        RULE rather than the transaction list -- an earlier draft tested the
        list, which would have resolved a cadence for a month of purely manual
        entries.  Patching the loader to explode is what makes that a control
        rather than a claim.
        """
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Manual",
                "100.00", due_date=seed_periods[0].start_date,
            )
            db.session.commit()
            month = seed_periods[0].start_date

            def _explode(_user_id):
                raise AssertionError(
                    "badge_cadence read the pay schedule for a month with "
                    "nothing to badge",
                )

            with patch.object(
                calendar_infrequency, "cadence_for", _explode,
            ):
                summary = calendar_service.get_month_detail(
                    seed_user["user"].id, month.year, month.month,
                    user_settings=None,
                )
            assert [
                entry.is_infrequent
                for entries in summary.day_entries.values()
                for entry in entries
            ] == [False]


# ── Third Paycheck Tests ─────────────────────────────────────────────


class TestThirdPaycheckDetection:
    """A month's paycheck count, off the ONE producer the engine also reads.

    ``calendar_service._detect_third_paycheck_months`` counted paydays over the
    window a caller happened to hold, which was a SECOND implementation of the
    question ``paycheck_calculator`` asks to decide whether a 24-per-year
    deduction is skipped -- one rule, two scans, two period sets.  Plan step
    **balance:X-bh-1** deleted it for
    :func:`~app.services.pay_calendar.paydays_in_month_through`, so these cases
    grade that producer and the flag the year overview renders from it.
    """

    @staticmethod
    def _three_paycheck_months(calendar, year):
        """The months of *year* holding three or more paydays."""
        return {
            month for month in range(1, 13)
            if len(saved_paydays_in_month_through(
                calendar,
                date(year, month, monthrange(year, month)[1]),
            )) >= 3
        }

    def test_twenty_six_biweekly_paydays_give_two_third_paycheck_months(
        self, app, seed_user, db,
    ):
        """26 biweekly paydays from 2026-01-02 land three times in two months.

        Hand-computed: Jan 2 / 16 / 30 and Jul 3 / 17 / 31.  Every other 2026
        month holds exactly two.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            calendar = calendar_for(seed_user["user"].id)
            assert self._three_paycheck_months(calendar, 2026) == {1, 7}

    def test_an_empty_calendar_holds_no_paydays_in_any_month(self, app):
        """An owner with no payday has no three-paycheck month.

        A real answer rather than an error.  *This said "a companion holds no
        schedule, and production has one such user", which plan step
        ``pay_calendar:C4-d`` made the wrong owner to name: a companion holds
        no schedule ROW and therefore no calendar at all.  The owner this
        builds is the one who HAS a rhythm and has recorded no payday under
        it.*
        """
        with app.app_context():
            calendar = PayCalendar.from_paydays([], eras_of([], 14), user_id=1, history_opens_on=None,
            )
            assert self._three_paycheck_months(calendar, 2026) == set()

    def test_a_month_is_counted_in_its_own_year(self, app, seed_user, db):
        """January 2025 and January 2026 are counted separately.

        The schedule runs 2025-07-04 through 2027, so both Januaries hold
        paydays.  Hand-computed from a 14-day rhythm anchored on 2025-07-04:
        2026 opens Jan 2 / 16 / 30 -- three -- while 2025's own January holds
        none at all, because the schedule does not reach it.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2025, 7, 4),
                num_periods=40,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            calendar = calendar_for(seed_user["user"].id)
            assert 1 in self._three_paycheck_months(calendar, 2026)
            assert saved_paydays_in_month_through(
                calendar, date(2025, 1, 31),
            ) == ()

    def test_the_year_overview_flags_exactly_those_months(
        self, app, seed_user, db,
    ):
        """THE FIRING CONTROL: the rendered flag is the producer's answer.

        Every assertion above would still pass if ``MonthSummary`` were built
        from the old window scan, so this one drives the real surface and
        checks the twelve flags against the same producer the paycheck engine
        reads.  Without it the fold is asserted about a function nothing on a
        page calls.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            overview = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2026,
                user_settings=None,
            )
            flagged = {
                summary.month for summary in overview.months
                if summary.is_third_paycheck_month
            }
            assert flagged == {1, 7}

    def test_the_card_counts_only_paydays_it_can_show_the_money_for(
        self, app, seed_user, db,
    ):
        """The analytics side is BOUNDED to the saved schedule (**balance:R-IB**).

        The shared producer projects the owner's rhythm past the horizon,
        because the paycheck engine needs a total answer -- a payday it cannot
        place would take ordinal 0, which silently drops a 12-per-year
        deduction.  The CARD reads the bounded twin instead, and the reason is
        measured: everything else on it -- income, expenses, net, month-end
        balance -- folds from SAVED periods, so a projected payday renders
        beside a ``$0.00`` net and a balance frozen at the horizon.

        A first cut of this step shipped the unbounded count here.  Measured
        2026-08-30 on the developer's own data: **29 month cards**, 2028-08
        through 2030-12, read "3 paychecks, income ``$0.00``, balance
        ``$9,539.92``".  The bound comes off with ledger row **N-394**, which
        projects the cash tier so both halves of the card light up together.

        This schedule runs 2026-01-02 to 2026-12-18, so 2027 lies wholly past
        its horizon and the owner IS really paid in it -- which is exactly the
        month the two producers answer differently.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            calendar = calendar_for(seed_user["user"].id)

            # The two producers disagree about January 2027, and that is the
            # point: one answers what the owner is paid, the other what the
            # app holds.
            assert paydays_in_month_through(
                calendar, date(2027, 1, 31),
            ) == (date(2027, 1, 1), date(2027, 1, 15), date(2027, 1, 29))
            assert saved_paydays_in_month_through(
                calendar, date(2027, 1, 31),
            ) == ()

            detail = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2027, month=1,
                user_settings=None,
            )
            assert detail.paycheck_days == []
            assert detail.is_third_paycheck_month is False
            # The card is bounded because its money is: nothing is recorded
            # past the horizon for a marker to stand beside.
            assert detail.total_income == Decimal("0")
            assert detail.net == Decimal("0")

            overview = calendar_service.get_year_overview(
                user_id=seed_user["user"].id, year=2027,
                user_settings=None,
            )
            assert overview.months[0].is_third_paycheck_month is False

    def test_the_engine_and_the_year_overview_agree_on_a_month(
        self, app, seed_user, db,
    ):
        """The two consumers agree on every month the schedule covers.

        The paycheck engine skips a 24-per-year deduction on a month's third
        payday; the year overview flags the month.  They were two independent
        scans over two period sets before plan step **balance:X-bh-1**, and
        they are now two named SETS over one span search -- the engine's total
        and the card's bounded (**balance:R-IB**).

        **So this is a real equality and not a tautology**, which an
        adversarial review of this step is why: while both surfaces called one
        function the equality could not fail, and the review said so.  They
        call different functions now, and the schedule below is fully saved,
        which is exactly the domain where the two must not diverge -- the case
        above pins where they deliberately do.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            user_id = seed_user["user"].id
            calendar = calendar_for(user_id)
            profile = SalaryProfile(
                user_id=user_id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=1,
                name="Third-paycheck agreement",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(
                profile, Decimal("2307.69"),  # $60,000.00 a year / 26
                date(2026, 1, 2),
            )
            db.session.flush()

            configs = load_tax_configs_for_year(profile, 2026)
            breakdowns = paycheck_calculator.project_salary(
                PayrollBasis(profile, calendar),
                [p for p in calendar.saved() if p.start_date.year == 2026],
                configs,
            )
            engine_months = {
                period.start_date.month
                for period, breakdown in zip(
                    [p for p in calendar.saved() if p.start_date.year == 2026],
                    breakdowns,
                )
                if breakdown.period.is_third_paycheck
            }
            overview = calendar_service.get_year_overview(
                user_id=user_id, year=2026,
                user_settings=None,
            )
            flagged = {
                summary.month for summary in overview.months
                if summary.is_third_paycheck_month
            }
            assert engine_months == flagged == {1, 7}


# ── Year Overview Tests ──────────────────────────────────────────────


class TestYearOverview:
    """Tests for the year overview aggregation."""

    def test_year_overview_12_months(self, app, seed_user, seed_periods):
        """YearOverview has exactly 12 MonthSummary entries."""
        with app.app_context():
            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            assert len(result.months) == 12
            # Months are ordered January through December.
            for i, ms in enumerate(result.months):
                assert ms.month == i + 1
                assert ms.year == 2026

    def test_year_overview_marks_third_paycheck(self, app, seed_user, db):
        """Year overview with 26 periods marks exactly 2 third-paycheck months."""
        with app.app_context():
            from app.services import pay_period_service
            # The BINDING went with the ``current_anchor_period_id`` line it
            # fed (ruling R-EH); the CALL is fixture setup and stays -- these
            # 26 periods ARE the third-paycheck year under test.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=26,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            third_paycheck_count = sum(
                1 for ms in result.months if ms.is_third_paycheck_month
            )
            assert third_paycheck_count == 2

    def test_year_overview_annual_totals(self, app, seed_user, seed_periods, db):
        """Annual totals equal the sum of all monthly totals."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Income", "5000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Expense", "1000.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            sum_income = sum(ms.total_income for ms in result.months)
            sum_expenses = sum(ms.total_expenses for ms in result.months)
            sum_net = sum(ms.net for ms in result.months)

            assert result.annual_income == sum_income
            assert result.annual_expenses == sum_expenses
            assert result.annual_net == sum_net

    def test_year_overview_empty_months_have_zeros(self, app, seed_user, seed_periods, db):
        """Months without data have zero totals and empty day_entries."""
        with app.app_context():
            # Only add data in January.
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Jan Item", "100.00",
                due_date=date(2026, 1, 5),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            # April (index 3) should be empty.
            apr = result.months[3]
            assert apr.total_income == Decimal("0")
            assert apr.total_expenses == Decimal("0")
            assert apr.net == Decimal("0")
            assert apr.day_entries == {}

    def test_year_overview_no_double_counting(self, app, seed_user, seed_periods, db):
        """Sum of all month totals equals total across all unique transactions."""
        with app.app_context():
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Inc", "3000.00",
                is_income=True, due_date=date(2026, 1, 2),
            )
            _add_transaction(
                db.session, seed_user, seed_periods[0], "Exp", "500.00",
                due_date=date(2026, 1, 10),
            )
            db.session.commit()

            result = calendar_service.get_year_overview(
                user_id=seed_user["user"].id,
                year=2026,
                user_settings=None,
            )
            # All transactions are in January -- no cross-month leakage.
            assert result.annual_income == Decimal("3000.00")
            assert result.annual_expenses == Decimal("500.00")
            assert result.annual_net == Decimal("2500.00")


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
    shadow -- beside one leg per transfer the set touches, on the side the
    set shows, and the day fold prices a leg through ``leg_contributions_by_key``
    keyed by ``cell_key``.  The money cases above (R-CC23, the far leg)
    already grade what a transfer is WORTH here; these grade what it IS, and
    the two states a display leg adds to the fold's: settled with a record,
    and excluded.
    """

    def test_the_loader_answers_own_rows_and_both_sides_legs(
        self, app, seed_user, seed_periods, db,
    ):
        """Each account's load is its own rows and the transfer's leg on its side.

        Checking holds a $300.00 Rent row and the from-side of a $250.00
        checking -> savings transfer; savings holds no row of its own.
        Checking's load answers the Rent row alone and one EXPENSE leg on
        checking; savings' answers no row and one INCOME leg on savings.
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
            _add_transaction(
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
            assert [row.name for row in on_checking.rows] == ["Rent"]
            assert [
                (leg.transfer.id, leg.account_id, leg.is_income)
                for leg in on_checking.legs
            ] == [(transfer.id, checking.id, False)]

            on_savings = _query_transactions_for_range(
                CashFlowSet.single(savings), ctx.scenario_id, periods,
            )
            assert on_savings.rows == []
            assert [
                (leg.transfer.id, leg.account_id, leg.is_income)
                for leg in on_savings.legs
            ] == [(transfer.id, savings.id, True)]

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

    def test_the_predicate_guards_a_leg_on_both_surfaces(
        self, app, seed_user, seed_periods, db, monkeypatch,
    ):
        """C10-4's lock over a LEG: drop the predicate from the SQL filter and
        the Python re-check together and the cancelled transfer's leg leaks
        into the day cell; either alone keeps it out (the belt-and-suspenders
        design), so this is the one mutation that can grade the SQL arm over
        ``Transfer``."""
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

            from app.services import calendar_service as cs
            monkeypatch.setattr(
                cs, "balance_contributing_clause",
                lambda model_class=Transaction: model_class.is_deleted.is_(False),
            )
            monkeypatch.setattr(cs, "is_balance_contributing", lambda _txn: True)

            leaked = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                user_settings=None,
            )
            assert [e.item_key for e in leaked.day_entries[6]] == [
                (transfer.id, checking.id),
            ]


# ── F-3 / W-065 balance-contributing predicate ─────────────────────


class TestBalanceContributingPredicate:
    """F-3 / HIGH-02 / W-065: calendar per-day filter via the locked
    Choice-2 ``balance-contributing`` predicate (Projected + settled,
    excludes Cancelled + Credit).

    Locks the post-Commit-10 (follow-up) behaviour so a future change
    that drops the predicate from either the SQL filter in
    ``_query_transactions_for_range`` or the Python re-check in
    ``_assign_transactions_to_days`` fails loud with a concrete
    arithmetic divergence rather than a silent display regression.
    """

    def test_c10_1_projected_and_settled_both_contribute(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-1: Projected $500 + Paid $200 -> day total $700.

        Hand arithmetic: 500 (Projected expense, effective = estimated)
        + 200 (Paid expense, effective = actual_amount) = 700.
        Both statuses are balance-contributing: Projected because it
        is not in the {Credit, Cancelled} exclusion set; Paid for
        the same reason -- the calendar's locked Choice-2 predicate
        intentionally includes realized payments at their settled date.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # F-3 / W-065: 500 + 200 = 700.00 (both contribute).
            assert result.total_expenses == Decimal("700.00")
            assert len(result.day_entries[5]) == 2
            names = sorted(e.name for e in result.day_entries[5])
            assert names == ["Paid Bill", "Projected Bill"]

    def test_c10_2_cancelled_and_credit_excluded(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-2: Cancelled + Credit excluded from day total.

        Same day as C10-1 plus a Cancelled $100 expense and a Credit
        $50 expense.  Hand arithmetic: 500 (Projected) + 200 (Paid)
        = 700.00; the Cancelled and Credit rows are filtered out by
        ``balance_contributing_clause`` (their status carries
        ``excludes_from_balance=True``) and never reach the day
        assignment, so they neither inflate totals nor appear as
        day entries.

        A pre-Commit-10-follow-up calendar would have included all
        four rows in ``day_entries[5]``; their amount contribution
        collapses to zero via ``effective_amount`` so totals stay at
        700.00, but the visible-entries regression is the user-facing
        defect F-3 names.  This test locks both the arithmetic AND
        the entry-count contract.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # F-3 / W-065: 500 + 200 = 700.00; Cancelled + Credit excluded.
            assert result.total_expenses == Decimal("700.00")
            # Day cell shows only the two contributing rows.
            assert len(result.day_entries[5]) == 2
            names = sorted(e.name for e in result.day_entries[5])
            assert names == ["Paid Bill", "Projected Bill"]

    def test_c10_3_grid_period_subtotal_excludes_cancelled_and_credit(
        self, app, seed_user, seed_periods, db,
    ):
        """F-3 / W-065 C10-3: Cancelled and Credit never reach the grid column.

        Same fixture as C10-2 (Projected $500 + Paid $200 +
        Cancelled $100 + Credit $50 on Jan 5).

        **Ruling R-K changed what a subtotal COUNTS, and this test's figure
        moved with it** (plan step X-c2b2; the read moved to the shipped
        ``GridColumn`` when plan step X-c2b3 deleted
        ``cash_ledger.period_subtotal``).  The retired subtotal was
        Projected-ONLY -- it gated every row through ``is_projected``, so a row
        that had actually been PAID contributed nothing and a past column read
        ``$0.00`` while thousands of dollars moved through it (finding N-41).
        The grid column now counts every row attributed to the period: a settled
        row at its confirmed cash leg, a projected row at its entries-aware
        reservation.

        Hand arithmetic on the new basis:

            Projected $500, no entries -> reservation      500.00
            Settled $200 (actual 200.00), no credit entries
              -> its covering movement                    200.00
            Cancelled $100 -> neither projected nor settled  0.00
            Credit $50     -> neither projected nor settled  0.00
                                                          -------
            expense                                        700.00

        So the exclusion this test locks is the CANCELLED / CREDIT pair, which
        R-K did not touch: they are excluded from the projected half by
        ``is_projected`` and from the settled half by the settled-status
        narrowing in SQL.  The old "locked Choice-2 divergence" between the
        calendar day total (700.00, C10-2) and this column is gone -- both count
        the settled row now.  That agreement is this FIXTURE's, not a general
        identity: every row here sits in one period on one day, while the
        calendar places a chip on its budget attribution date and steps its
        balance on the day the money moved (finding N-58).
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            column = balance_at.grid_balance_view(
                CashFlowSet.single(seed_user["account"]),
                BalanceContext.build(seed_user["user"].id),
            ).columns[p0.id]
            # 500.00 reservation + 200.00 confirmed cash leg; the Cancelled
            # $100 and the Credit $50 contribute nothing to either half.
            assert column.expense == Decimal("700.00")
            assert column.income == Decimal("0.00")

    def test_c10_4_regression_lock_predicate_drop_visible(
        self, app, seed_user, seed_periods, db, monkeypatch,
    ):
        """F-3 / W-065 C10-4: regression lock for predicate removal.

        Simulates the regression where the locked predicate is
        dropped from BOTH the SQL filter and the Python re-check by
        monkey-patching ``balance_contributing_clause`` to a
        trivially-true predicate (only ``is_deleted=False``, the
        pre-fix gate) and ``is_balance_contributing`` to ignore the
        excludes_from_balance flag.  With the predicate dropped the
        Cancelled and Credit rows leak into ``day_entries[5]`` and
        the day cell renders four entries instead of the two the
        locked Choice-2 semantic mandates.

        The post-Commit-10-follow-up code MUST reject this
        regression: with the production predicate in place this test
        confirms the day shows exactly two contributing entries.
        Then the monkey-patched regression run confirms the locked
        behaviour: with the predicate removed, four entries appear.
        A diff between the two locks the predicate's contribution.

        Hand arithmetic on the four totals through ``effective_amount``:
            Projected $500: effective = 500
            Settled $200: effective = 200
            Cancelled $100: excludes_from_balance=True, effective = 0
            Credit $50: excludes_from_balance=True, effective = 0
            Sum = 700.00 either way; the visible regression is the
            entry-count contract (2 vs 4), which this test locks.
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Projected Bill", "500.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.PROJECTED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Paid Bill", "200.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.DONE, settled_amount="200.00",
            )
            _add_transaction(
                db.session, seed_user, p0, "Cancelled Bill", "100.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CANCELLED,
            )
            _add_transaction(
                db.session, seed_user, p0, "Credit Bill", "50.00",
                due_date=date(2026, 1, 5),
                status=StatusEnum.CREDIT,
            )
            db.session.commit()

            # Production predicate: exactly two entries on Jan 5.
            real = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            assert len(real.day_entries[5]) == 2
            assert real.total_expenses == Decimal("700.00")

            # Simulated regression: drop the predicate from both
            # surfaces.  The SQL filter degrades to is_deleted-only;
            # the Python re-check is short-circuited to always
            # contribute.
            from app.services import calendar_service as cs
            # The stub takes the model class the real clause takes since leaf
            # balance:X-bi-6-1b (the calendar asks it over ``Transfer`` too).
            monkeypatch.setattr(
                cs, "balance_contributing_clause",
                lambda model_class=Transaction: model_class.is_deleted.is_(False),
            )
            monkeypatch.setattr(
                cs, "is_balance_contributing", lambda _txn: True,
            )

            regressed = calendar_service.get_month_detail(
                user_id=seed_user["user"].id,
                year=2026,
                month=1,
                user_settings=None,
            )
            # Predicate dropped: all four rows leak into the day cell.
            assert len(regressed.day_entries[5]) == 4
            # The visible-totals regression for a hypothetical future
            # change that ALSO replaces effective_amount with
            # estimated_amount would be 500 + 200 + 100 + 50 = 850;
            # today's effective_amount zeroes Cancelled + Credit so
            # the total stays 700.00 even with the predicate dropped.
            # The entry-count contract above is the load-bearing
            # regression lock.
            assert regressed.total_expenses == Decimal("700.00")


# ── Anchor-None contract (F-2 / Commit 11) ──────────────────────────


class TestUnresolvableAccountOrScenario:
    """Tests for the F-2 / Commit 11 contract.

    After Commits 3-8 of the main remediation locked the E-19 /
    CRIT-01 invariant, the calendar service must raise
    :class:`CalendarAccountNotResolvableError` when
    :func:`resolve_analytics_cash_flow_set` (``resolve_analytics_account``
    until plan step CC-4-3) returns ``None`` -- the pre-F-2
    behaviour of silently substituting a zeroed
    :class:`MonthSummary` / :class:`YearOverview` masked the
    upstream defect behind a ``$0.00`` calendar.

    **The no-BASELINE half of that contract moved at plan step X-v2** (ruling
    R-BW).  Both conditions used to raise this one exception, and the route
    turned both into a 404; they are two different problems (a deleted
    analytics account, versus a repairable missing baseline) and they get two
    different answers now.  The baseline half raises
    :class:`~app.exceptions.BaselineMissingError` from the seam, which one
    application-level handler answers with the repair card -- asserted below,
    and end-to-end in ``tests/test_routes/test_no_baseline_policy.py``.
    """

    def test_month_detail_raises_when_account_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-1 (service): None account -> CalendarAccountNotResolvableError."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "resolve_analytics_cash_flow_set",
                lambda _user_id, _user_settings, _account_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id,
                    year=2026,
                    month=1,
                    user_settings=None,
                )

    def test_month_detail_raises_when_scenario_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """A None baseline raises the SEAM's named exception, not this module's."""
        with app.app_context():
            # The baseline scenario is now resolved inside the balance context,
            # so that is where an unresolvable baseline is simulated.
            monkeypatch.setattr(
                resolution_context, "get_baseline_scenario",
                lambda _user_id: None,
            )
            # The seam's own named exception, NOT the calendar's: a missing
            # baseline is answered by one application-level handler, and a
            # producer that translated it into its own error is what made this
            # state 404 here while it 500'd on the loan page (ruling R-BW).
            with pytest.raises(BaselineMissingError):
                calendar_service.get_month_detail(
                    user_id=seed_user["user"].id,
                    year=2026,
                    month=1,
                    user_settings=None,
                )

    def test_year_overview_raises_when_account_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-1 (service, year view): None account -> error."""
        with app.app_context():
            monkeypatch.setattr(
                calendar_service, "resolve_analytics_cash_flow_set",
                lambda _user_id, _user_settings, _account_id: None,
            )
            with pytest.raises(CalendarAccountNotResolvableError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                    user_settings=None,
                )

    def test_year_overview_raises_when_scenario_unresolvable(
        self, app, seed_user, db, monkeypatch,
    ):
        """C11-2 (service, year view): None scenario -> error."""
        with app.app_context():
            # The baseline scenario is now resolved inside the balance context,
            # so that is where an unresolvable baseline is simulated.
            monkeypatch.setattr(
                resolution_context, "get_baseline_scenario",
                lambda _user_id: None,
            )
            # The seam's own named exception, NOT the calendar's: a missing
            # baseline is answered by one application-level handler, and a
            # producer that translated it into its own error is what made this
            # state 404 here while it 500'd on the loan page (ruling R-BW).
            with pytest.raises(BaselineMissingError):
                calendar_service.get_year_overview(
                    user_id=seed_user["user"].id,
                    year=2026,
                    user_settings=None,
                )


class TestCalendarDailyView:
    """The month's daily running-balance projection (DailyView).

    Uses the standard ``seed_periods`` scenario (biweekly from 2026-01-02,
    anchor = period 0 at $1000).  April flows: Rent -500 due 04-05, Salary
    +2000 due 04-09 (period 6); Car -800 due 04-20, Salary +2000 due 04-23
    (period 7).  The hand-computed running balance is 04-05 500, 04-09 2500,
    04-15 2500, 04-20 1700, 04-23 3700, 04-30 3700; the month trough is $500
    on the 5th.

    **April, not January, because a plan cannot have already happened**
    (ruling R-G, wired at plan step X-c2b2).  The suite freezes today to
    2026-03-20, and a still-Projected row dated before that lands at
    ``as_of + 1 day`` rather than on its own date -- so a January month would
    draw a FLAT line at the anchor, which is the honest answer (none of those
    bills was ever recorded as paid) but pins no ramp arithmetic.  Dating the
    flows forward of the frozen today keeps every hand-computed figure below
    exactly what it was and makes each land on its own day.
    """

    def _seed_april(self, db, seed_user, seed_periods):
        p6, p7 = seed_periods[6], seed_periods[7]
        _add_transaction(
            db.session, seed_user, p6, "Rent", "500.00",
            due_date=date(2026, 4, 5),
        )
        _add_transaction(
            db.session, seed_user, p6, "Salary", "2000.00",
            is_income=True, due_date=date(2026, 4, 9),
        )
        _add_transaction(
            db.session, seed_user, p7, "Car", "800.00",
            due_date=date(2026, 4, 20),
        )
        _add_transaction(
            db.session, seed_user, p7, "Salary", "2000.00",
            is_income=True, due_date=date(2026, 4, 23),
        )
        db.session.commit()

    def test_daily_is_none_without_today(self, app, seed_user, seed_periods, db):
        """Omitting ``today`` yields no daily view (year-overview parity)."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                user_settings=None,
            )
        assert result.daily is None

    def test_daily_balances_and_trough(
        self, app, seed_user, seed_periods, db,
    ):
        """The daily view carries per-day balances and the month trough."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 4, 20),
                user_settings=None,
            )
        daily = result.daily
        assert isinstance(daily, DailyView)
        assert daily.daily_balances[5] == Decimal("500.00")
        assert daily.daily_balances[9] == Decimal("2500.00")
        assert daily.daily_balances[30] == Decimal("3700.00")
        # Trough is the earliest day of the month minimum ($500 on the 5th).
        assert daily.trough_day == 5
        assert daily.trough_balance == Decimal("500.00")

    def test_balance_today_and_elapsed_remaining_split(
        self, app, seed_user, seed_periods, db,
    ):
        """balance_today and the elapsed/remaining split key off ``today``."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 4, 20),
                user_settings=None,
            )
        daily = result.daily
        # End-of-day balance on the 20th (after the Car payment): $1700.
        assert daily.balance_today == Decimal("1700.00")
        # Elapsed (days 1-20): Salary 2000 in, Rent 500 + Car 800 = 1300 out.
        assert daily.elapsed_income == Decimal("2000.00")
        assert daily.elapsed_expense == Decimal("1300.00")
        # Remaining (days 21-30): the second Salary; no more expenses.
        assert daily.remaining_income == Decimal("2000.00")
        assert daily.remaining_expense == Decimal("0.00")

    def test_future_month_is_all_remaining(
        self, app, seed_user, seed_periods, db,
    ):
        """A month entirely after today is all remaining, no balance_today."""
        with app.app_context():
            self._seed_april(db, seed_user, seed_periods)
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=4,
                today=date(2026, 3, 15),
                user_settings=None,
            )
        daily = result.daily
        assert daily.balance_today is None
        assert daily.elapsed_income == Decimal("0.00")
        assert daily.elapsed_expense == Decimal("0.00")
        assert daily.remaining_income == Decimal("4000.00")
        assert daily.remaining_expense == Decimal("1300.00")

    def test_income_first_then_expense_by_magnitude(
        self, app, seed_user, seed_periods, db,
    ):
        """Day cells order income first, then expenses by descending size."""
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Small Income", "100.00",
                is_income=True, due_date=date(2026, 1, 6),
            )
            _add_transaction(
                db.session, seed_user, p0, "Big Expense", "500.00",
                due_date=date(2026, 1, 6),
            )
            db.session.commit()
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                today=date(2026, 1, 6),
                user_settings=None,
            )
        names = [e.name for e in result.day_entries[6]]
        # Income leads even though the expense is larger.
        assert names == ["Small Income", "Big Expense"]

    def test_day_overflow_collapses_beyond_three_flows(
        self, app, seed_user, seed_periods, db,
    ):
        """A day with more than three flows yields a "+N more" residual.

        Day has 1 income (+3000) and 4 expenses (500/400/300/200).  Ordered
        income-first then expense-desc, the visible three are the income and
        the two largest expenses; the residual is the two smallest expenses
        (-300 and -200 = -500).
        """
        with app.app_context():
            p0 = seed_periods[0]
            _add_transaction(
                db.session, seed_user, p0, "Pay", "3000.00",
                is_income=True, due_date=date(2026, 1, 7),
            )
            for name, amt in [
                ("E500", "500.00"), ("E400", "400.00"),
                ("E300", "300.00"), ("E200", "200.00"),
            ]:
                _add_transaction(
                    db.session, seed_user, p0, name, amt,
                    due_date=date(2026, 1, 7),
                )
            db.session.commit()
            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                today=date(2026, 1, 7),
                user_settings=None,
            )
        assert len(result.day_entries[7]) == 5
        overflow = result.day_overflow[7]
        assert overflow.count == 2
        assert overflow.net == Decimal("-500.00")
        # Days at or under the cap carry no overflow entry.
        assert 5 not in result.day_overflow


class TestARefundedDayOnTheRealCalendar:
    """The PRODUCER CHAIN, which is what makes a negative day entry possible.

    Plan step ``bank_import:X-gj-2b-3``.  ``test_calendar_day_flows`` grades
    the three day-level rules as pure arithmetic; this grades the claim those
    rules rest on -- that ``DayEntry.amount`` reaches them NEGATIVE.  The chain
    is ``contributions_by_id -> fixed_contribution -> settled_figure ->
    purchases_total(txn.entries)``, and ruling **bank_import:R-II** relaxed
    ``ck_transaction_entries_positive_amount`` to ``amount <> 0`` so a settled
    envelope can be worth less than nothing.

    **Without this case the pure tests grade a shape nothing produces**, which
    is the hazard a fold tested only on hand-built records always has.
    """

    def _settled_envelope(self, db, seed_user, period, name, amounts):
        """Settle an envelope worth exactly the entries handed to it."""
        txn = create_envelope_txn(
            seed_user, db.session, period, name, Decimal("0.00"),
        )
        for amount in amounts:
            add_entry(
                db.session, seed_user, txn, Decimal(amount),
                period.start_date,
            )
        done = ref_cache.status_id(StatusEnum.DONE)
        status_seam.apply_status_change(
            txn, done,
            settlement=settlement_if_settling(txn, done),
        )
        db.session.flush()
        return txn

    def test_a_refund_dominated_envelope_reaches_the_fold_NEGATIVE(
        self, app, seed_user, seed_periods, db,
    ):
        """The entry, the fold and the month headline, in one pass.

        Asserts the DayEntry's own amount as well as the totals: a chain that
        clamped the figure on the way in would leave a correct-looking month
        while the defect had merely moved upstream.
        """
        with app.app_context():
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Amazon", ["-86.67"],
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                user_settings=None,
            )

            refunds = [
                entry
                for entries in result.day_entries.values()
                for entry in entries
                if entry.name == "Amazon"
            ]
            assert len(refunds) == 1, "the envelope must reach a day cell"
            assert refunds[0].amount == Decimal("-86.67")
            assert refunds[0].is_income is False, (
                "an expense that came back is not income -- booking it on the "
                "income leg grosses up both sides of the month"
            )
            # THE HEADLINE: under the abs() this read +86.67 and net moved by
            # $173.34 on a month in which the account RECEIVED the money.
            assert result.total_expenses == Decimal("-86.67")
            assert result.net == Decimal("86.67")

    def test_a_partly_refunded_envelope_NETS(
        self, app, seed_user, seed_periods, db,
    ):
        """The ordinary shape, which nets correctly either way.

        Staged beside the case above deliberately: only the refund-DOMINATED
        envelope inverts under ``abs()``, so a suite holding only this one
        would pass with the defect restored.
        """
        with app.app_context():
            self._settled_envelope(
                db, seed_user, seed_periods[0], "Groceries",
                ["100.00", "-30.00"],
            )
            db.session.commit()

            result = calendar_service.get_month_detail(
                user_id=seed_user["user"].id, year=2026, month=1,
                user_settings=None,
            )

            assert result.total_expenses == Decimal("70.00")
