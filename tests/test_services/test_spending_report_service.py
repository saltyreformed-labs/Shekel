"""
Shekel Budget App -- Unified Spending Report Service Tests (S-P1, D7 rebuild)

Hand-confirmed tests for :mod:`app.services.spending_report_service`: the
category breakdown and its shares, window filtering, the estimate-surprises
kernel (capped list + net), the hero band (vs-prior / vs-average with
None-safety and per-window-type prior arithmetic), the trailing window
series (the chart / hero one-source identity, and the twelve windows it
is derived from), the window-over-window deltas (items, groups, and the
By-change rows with their zero-current rider), and the empty / no-account
contracts.  Every value assertion carries the arithmetic that produces it.

**The chart's windows are DERIVED off the owner's pay calendar** since plan
step C2-f3d; ``TestTheChartReadsTheDerivedOrdinal`` is that step's firing
control, and it fails on the ``period_index`` queries it replaced.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app import ref_cache
from app.exceptions import AmountUnresolvable
from app.enums import StatusEnum, TxnTypeEnum
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import (
    match_withdrawal,
    movement_removal,
    spending_analysis,
    status_seam,
    template_amount_service,
    transfer_service,
)
from app.services.cash_flow_set import CashFlowSet
from app.services.pay_calendar import PayCalendar
from app.utils.dates import display_today
from app.services.cash_ledger import derived_amount_basis, resolve_amount
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import grid_transfer_leg, parent_of
from app.services.spending_report_service import (
    Comparison,
    SpendingWindow,
    compute_spending_report,
)
# The privates these tests exercise moved into the package's submodules at plan
# step X-au-c2b, when finding **N-270**'s 1000-line ceiling forced the split;
# they are imported from where they live rather than re-exported, so the test
# names the module that owns each rule.
from app.services.spending_report_service._hero import (
    _TRAILING_WINDOW_COUNT,
)
from app.services.spending_report_service._surprises import (
    _MAX_SURPRISES, _build_surprises,
)
from app.services.spending_report_service._breakdown import (
    _build_breakdown,
    _build_changes,
    _totals_by_category,
)
from app.services.spending_report_service._types import _CategoryTotal
from app.services.spending_report_service._types import _ScopeIds
from app.services.spending_report_service._window import (
    _CHART_WINDOW_COUNT,
    _series_windows,
    _shift_month,
    _spent_total,
)
from tests._test_helpers import (
    add_entry,
    cover_bare_settled_row,
    create_account_of_type,
    create_envelope_txn,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    default_settle_day,
    eras_of,
    generate_row_of,
    last_covered_day,
    make_expense_template,
    one_off_row_of,
    pay_periods_hydrated,
    record_paydays_across_a_hole,
    rhythm_of,
    set_default_grid_account,
    settle_day_columns,
    settlement_if_settling,
    state_template_price,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db, seed_user, period, name, category_key, estimated,
    *, actual=None, status_enum=StatusEnum.DONE, is_income=False,
    is_deleted=False, due_date=None, settled_on=None, account=None,
):
    """Create one transaction for report testing (settled expense by default).

    *account* places the row on another of the owner's accounts (a card, a
    savings account); the seed's checking account by default.

    A row in a SETTLED status carries the whole record -- the day, and the
    figure with who wrote it -- resolved through the one door a bare-built
    fixture uses (``settle_day_columns`` for the row's day pair, plan step
    X-az, and ``cover_bare_settled_row`` for the COVERING MOVEMENT that IS
    the record since plan step ``balance:X-bi-4b-2``).  *actual* is a
    figure a HUMAN typed (``typed``); with none the record is the row's own
    plan (``resolved``), which is what a settle with nothing to correct
    books.  The settle DAY defaults to the period's start where the caller
    names none, because a settled row carries one.
    """
    cat_id = (
        seed_user["categories"][category_key].id if category_key else None
    )
    type_enum = TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    planned = Decimal(str(estimated))
    settled_day = settled_on or default_settle_day(
        period, ref_cache.status_id(status_enum),
    )
    txn = one_off_row_of(
        period,
        name=name,
        amount=planned,
        user_id=period.user_id,
        account_id=(account or seed_user["account"]).id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(type_enum),
        category_id=cat_id,
        due_date=due_date or period.start_date,
    )
    txn.status_id = ref_cache.status_id(status_enum)
    txn.is_deleted = is_deleted
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(settled_day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    if settled_day is not None:
        cover_bare_settled_row(db.session, txn, planned, actual)
    return txn


def _pp_window(period):
    """Return a pay-period SpendingWindow for a period."""
    return SpendingWindow(window_type="pay_period", period_id=period.id)


@contextmanager
def _pay_period_selects(engine):
    """Capture every statement this block SELECTs FROM ``budget.pay_periods``.

    The engine rather than the session, because what is being counted is
    round trips to the database: a ``db.session.get`` served out of the
    identity map issues none, which is exactly how the retired ordinal
    walk's own row load stayed invisible while it ran eleven queries beside
    it (plan step C2-f3d).

    **It matches the FROM clause and so a JOIN is invisible to it**, which is
    deliberate and was measured at plan step C2-f3e: a query JOINing the table
    is captured as ZERO statements even while it hydrates ten ``PayPeriod``
    entities.  That is the right scope for the question here -- the assertions
    below count reads of the table as a SUBJECT, and
    ``query_settled_expenses_in_span`` KEEPS its join for the COALESCE
    attribution filter -- but it means this probe cannot grade a
    join-filtered read, and :func:`~tests._test_helpers.pay_periods_hydrated`
    beside it is what sees those.

    Args:
        engine: The SQLAlchemy engine to listen on, normally ``db.engine``.

    Yields:
        The list of flattened SQL statements, appended to as the block runs.
    """
    captured = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        flattened = " ".join(statement.split())
        if "FROM budget.pay_periods" in flattened:
            captured.append(flattened)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _group(report, group_name):
    """Return the named breakdown group row, or None."""
    return next(
        (g for g in report.breakdown if g.group_name == group_name), None,
    )


# ── Breakdown ────────────────────────────────────────────────────────


class TestBreakdown:
    """Category breakdown: amounts, shares, ordering, window filtering."""

    def test_shares_sum_to_one(self, app, seed_user, seed_periods, db):
        """Group/item shares are amount/total and sum to 1.0000.

        Window = period[0] with Rent 1200 (Home), Groceries 500 (Family),
        Car Payment 300 (Auto): total 2000.  Shares 0.60 / 0.25 / 0.15.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "1200.00")
            _txn(db, seed_user, seed_periods[0], "Food", "Groceries", "500.00")
            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("2000.00")
            # Ordered by amount descending.
            assert [g.group_name for g in report.breakdown] == [
                "Home", "Family", "Auto",
            ]
            home = _group(report, "Home")
            assert home.amount == Decimal("1200.00")
            assert home.share == Decimal("0.6")  # 1200/2000
            assert _group(report, "Family").share == Decimal("0.25")
            assert _group(report, "Auto").share == Decimal("0.15")
            # All group shares sum to exactly 1 (to the documented precision).
            share_sum = sum((g.share for g in report.breakdown), Decimal("0"))
            assert share_sum.quantize(Decimal("0.0001")) == Decimal("1.0000")
            # The single item under a group carries the same share.
            assert home.items[0].item_name == "Rent"
            assert home.items[0].share == Decimal("0.6")

    def test_multi_item_group_ordered(self, app, seed_user, seed_periods, db):
        """A group's items are amount-descending; the group sums them.

        Auto = Car Payment 300 + Gas 100 = 400; items ordered Car, Gas.
        """
        with app.app_context():
            gas = Category(
                user_id=seed_user["user"].id, group_name="Auto", item_name="Gas",
            )
            db.session.add(gas)
            db.session.flush()
            seed_user["categories"]["Gas"] = gas

            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00")
            _txn(db, seed_user, seed_periods[0], "Fuel", "Gas", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            auto = _group(report, "Auto")
            assert auto.amount == Decimal("400.00")
            assert [i.item_name for i in auto.items] == ["Car Payment", "Gas"]
            assert auto.items[0].amount == Decimal("300.00")
            assert auto.items[1].amount == Decimal("100.00")

    def test_tied_items_in_one_group_order_by_name_not_by_insertion(
        self, app, seed_user, seed_periods, db,
    ):
        """Two categories that spent the SAME are ordered by the data (**P74**).

        The item list starts in the query's row order -- ``current_by_cat`` is
        a dict built by iterating it, and ``list.sort`` is stable -- so before
        this ruling a tie was decided by the plan.

        **The two are created in REVERSE alphabetical order on purpose**, so
        the insertion order and the correct order disagree: a rank keyed on
        ``amount`` alone leaves ``Zephyr`` first, and the total key puts
        ``Awning`` there.  ``item_name`` is unique within a group by the
        ``(user_id, group_name, item_name)`` constraint, so it totally orders
        this list while also reading alphabetically to a person.
        """
        with app.app_context():
            for name in ("Zephyr", "Awning"):
                cat = Category(
                    user_id=seed_user["user"].id,
                    group_name="Home", item_name=name,
                )
                db.session.add(cat)
                db.session.flush()
                seed_user["categories"][name] = cat
                _txn(
                    db, seed_user, seed_periods[0], f"T{name}", name, "75.00",
                )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            home = _group(report, "Home")
            tied = [
                i.item_name for i in home.items
                if i.amount == Decimal("75.00")
            ]
            assert tied == ["Awning", "Zephyr"], (
                "a tied item ranking must be a function of the data, not of "
                "the order the rows happened to be inserted in"
            )

    def test_window_filters_non_measured_rows(self, app, seed_user, seed_periods, db):
        """Only settled expenses in the window count.

        Seeds one settled expense (included) plus a projected, a credit, a
        cancelled, a settled income, a deleted expense, and a settled expense
        in a DIFFERENT period -- all excluded.  Spent total is the one row.

        The deleted expense is built as the delete door leaves it (ruling
        R-CC75): settled, its payment taken off through the one removal act,
        then hidden -- the database refuses a payment under a hidden row
        (R-CC89) and a row hidden holding one (R-CC92).  Rule-5
        re-expression, developer-confirmed 2026-09-23.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Kept", "Rent", "500.00")
            _txn(db, seed_user, seed_periods[0], "Proj", "Rent", "999.00",
                 status_enum=StatusEnum.PROJECTED)
            _txn(db, seed_user, seed_periods[0], "Cred", "Rent", "999.00",
                 actual="999.00", status_enum=StatusEnum.CREDIT)
            _txn(db, seed_user, seed_periods[0], "Cxl", "Rent", "999.00",
                 status_enum=StatusEnum.CANCELLED)
            _txn(db, seed_user, seed_periods[0], "Inc", "Salary", "999.00",
                 actual="999.00", is_income=True, status_enum=StatusEnum.RECEIVED)
            deleted = _txn(db, seed_user, seed_periods[0], "Del", "Rent",
                           "999.00", actual="999.00")
            movement_removal.remove_movements(
                list(deleted.entries), seed_user["user"].id,
                because=match_withdrawal.LEFT_THE_BOOKS,
                press=None,
            )
            deleted.is_deleted = True
            _txn(db, seed_user, seed_periods[1], "Other", "Rent", "999.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("500.00")
            assert len(report.breakdown) == 1
            assert report.breakdown[0].items[0].item_name == "Rent"

    def test_month_window_attribution(self, app, seed_user, seed_periods, db):
        """A month window keeps rows whose COALESCE(due_date, start) is in-month.

        seed_periods span Jan-May 2026.  A month=1 window keeps only the
        January-attributed settled expenses.
        """
        with app.app_context():
            # period[0] starts 2026-01-02 (January); period[4] starts in Feb+.
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[4], "FebRent", "Rent", "800.00",
                 due_date=date(2026, 2, 28))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("1200.00")
            assert report.scope.window_label == "January 2026"

    def test_cross_boundary_due_date_belongs_to_its_month(self, app, seed_user,
                                                          seed_periods, db):
        """A bill due in month M funded from a period outside M belongs to M.

        period[1] spans 2026-01-16..29 (entirely January) and holds a bill
        with due_date 2026-02-05.  The period never overlaps February, so
        the former period-overlap pre-filter attributed the row to NO month
        window (January excluded it by date; February never loaded its
        period).  The attribution-day fetch puts it where it is due:
        February counts the 150.00; January does not.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[1], "EarlyFebBill", "Rent",
                 "150.00", due_date=date(2026, 2, 5))
            db.session.commit()

            february = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=2, year=2026),
                user_settings=None,
            )
            assert february.hero.spent_total == Decimal("150.00")
            assert february.breakdown[0].items[0].item_name == "Rent"

            january = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
                user_settings=None,
            )
            assert january.hero.spent_total == Decimal("0")


# ── Surprises ────────────────────────────────────────────────────────


class TestSurprises:
    """Estimate surprises: differs vs equals vs no-actual, cap + net."""

    def test_surprises_signed_delta_and_net(self, app, seed_user, seed_periods, db):
        """Only rows whose actual differs surface; net sums their deltas.

        Rent est 1200 actual 1250 (+50); Car est 300 actual 280 (-20);
        Groceries est 400 actual 400 (0, not a surprise); Utility est 100
        actual None (resolves to estimate, delta 0, not a surprise).
        Sorted by |delta| desc: Rent(+50) then Car(-20).  Net = +30.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "1200.00",
                 actual="1250.00")
            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00",
                 actual="280.00")
            _txn(db, seed_user, seed_periods[0], "Food", "Groceries", "400.00",
                 actual="400.00")
            _txn(db, seed_user, seed_periods[0], "Util", "Rent", "100.00",
                 actual=None)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            rows = report.surprises.rows
            assert [r.name for r in rows] == ["Rent", "Car"]
            assert rows[0].delta == Decimal("50.00")
            assert rows[0].estimated == Decimal("1200.00")
            assert rows[0].actual == Decimal("1250.00")
            assert rows[1].delta == Decimal("-20.00")
            # Net = +50 + (-20) = +30.
            assert report.surprises.net == Decimal("30.00")

    def test_surprises_capped_but_net_is_full(self, app, seed_user, seed_periods, db):
        """The list caps at _MAX_SURPRISES; the net sums ALL surprises.

        Six surprises of +10..+60; the list keeps the 5 largest (+60..+20)
        but the net is +10+20+30+40+50+60 = +210.
        """
        with app.app_context():
            for i in range(1, 7):
                est = Decimal("100.00")
                _txn(db, seed_user, seed_periods[0], f"S{i}", "Rent",
                     est, actual=est + Decimal(str(i * 10)))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert len(report.surprises.rows) == _MAX_SURPRISES
            assert report.surprises.rows[0].delta == Decimal("60.00")
            # Net includes the capped-off +10 surprise too.
            assert report.surprises.net == Decimal("210.00")


# ── Hero band ────────────────────────────────────────────────────────


class TestHero:
    """Hero comparisons: vs-prior, vs-average, None-safety."""

    def test_vs_prior_pct(self, app, seed_user, seed_periods, db):
        """vs-prior compares the immediately preceding same-type window.

        period[0] spends 1000, period[1] spends 1500.  Window = period[1]:
        delta 500, pct 500/1000*100 = 50.00.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "1500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("1500.00")
            assert report.hero.vs_prior.baseline == Decimal("1000.00")
            assert report.hero.vs_prior.delta == Decimal("500.00")
            assert report.hero.vs_prior.pct == Decimal("50.00")

    def test_vs_prior_none_when_no_prior_period(self, app, seed_user, seed_periods, db):
        """The earliest period has no prior window -> a None comparison."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "1000.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_prior.delta is None
            assert report.hero.vs_prior.pct is None

    def test_vs_prior_empty_prior_is_pct_none(self, app, seed_user, seed_periods, db):
        """An empty (zero-spend) prior window gives a real delta but None pct.

        period[0] has no spend; period[1] spends 800.  Prior exists (a lean
        period) so baseline 0, delta 800, but pct is None (no percent of 0).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "800.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert report.hero.vs_prior.baseline == Decimal("0")
            assert report.hero.vs_prior.delta == Decimal("800.00")
            assert report.hero.vs_prior.pct is None

    def test_vs_average_over_six_windows(self, app, seed_user, seed_periods, db):
        """vs-average uses the trailing six same-type windows.

        period[0..5] spend 100,200,300,400,500,600; window = period[6] spends
        1000.  Trailing average = 2100/6 = 350.00; delta 650; pct
        650/350*100 = 185.71.
        """
        with app.app_context():
            for idx, amt in enumerate([100, 200, 300, 400, 500, 600]):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     f"{amt}.00")
            _txn(db, seed_user, seed_periods[6], "W", "Rent", "1000.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[6]),
                user_settings=None,
            )
            assert report.hero.vs_average.baseline == Decimal("350.00")
            assert report.hero.vs_average.delta == Decimal("650.00")
            assert report.hero.vs_average.pct == Decimal("185.71")

    def test_vs_average_none_when_no_prior(self, app, seed_user, seed_periods, db):
        """No trailing windows -> a None average comparison."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.vs_average.baseline is None
            assert report.hero.vs_average.pct is None

    def test_payment_timing_scoped_to_window(self, app, seed_user, seed_periods, db):
        """Payment timing reuses the year-end rule over the window's rows.

        Two settled bills in the window, both paid on their due date (0 days):
        on-time 2, late 0, avg 0.00.
        """
        with app.app_context():
            due = seed_periods[0].start_date
            # The settle day IS the due date, so days_paid_before_due == 0 (on
            # time).  It was a noon-UTC instant until plan step X-f1, chosen so
            # the display-timezone conversion landed back on the same civil day;
            # the column stores that day directly now, so the arithmetic is
            # exact rather than zone-dependent.
            paid = due
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "1200.00",
                 actual="1200.00", due_date=due, settled_on=paid)
            _txn(db, seed_user, seed_periods[0], "G", "Groceries", "400.00",
                 actual="400.00", due_date=due, settled_on=paid)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            timing = report.hero.payment_timing
            assert timing["total_bills_paid"] == 2
            assert timing["paid_on_time"] == 2
            assert timing["paid_late"] == 0
            assert timing["avg_days_before_due"] == Decimal("0.00")


# ── The chart's twelve windows (plan step C2-f3d) ────────────────────


def _calendar_scope(paydays, cadence_days=14, user_id=1):
    """Return a ``_ScopeIds`` whose calendar is *paydays*, with no database.

    ``_series_windows`` reads the scope's CALENDAR and nothing else, so the
    three window arms are exercised over hand-written paydays rather than
    through a fixture -- which is what lets the cases below name an exact
    expected id list per slot.  The cash-flow set and the scenario id are
    never read on this path: the set is one unsaved account row, the
    placeholder ``account_id=1`` was until plan step CC-4-3.
    """
    return _ScopeIds(
        user_id=user_id, cash_flow=CashFlowSet.single(Account(id=1)),
        scenario_id=1,
        calendar=PayCalendar.from_paydays(paydays, eras_of(paydays, cadence_days), user_id, history_opens_on=None),
    )


class TestSeriesWindows:
    """The twelve chart windows, derived in one pass off the calendar.

    ``_shift_month`` is exercised here rather than in a class of its own: it
    is the month arm's one primitive and has no other caller since plan step
    C2-f3d deleted ``_shift_window``.
    """

    def test_shift_month_january_rolls_to_prior_december(self):
        """One month before January 2026 is December 2025."""
        assert _shift_month(2026, 1, 1) == (2025, 12)
        # Two months before March 2026 is January 2026.
        assert _shift_month(2026, 3, 2) == (2026, 1)
        # Thirteen months before June 2026 is May 2025.
        assert _shift_month(2026, 6, 13) == (2025, 5)

    def test_year_series_is_twelve_descending_years(self):
        """A year window's series is 2015..2026, the chosen year last."""
        scope = _calendar_scope([(1, date(2026, 1, 2))])
        chosen = SpendingWindow(window_type="year", year=2026)

        windows = _series_windows(scope, chosen)

        assert len(windows) == _CHART_WINDOW_COUNT
        assert [w.year for w in windows] == list(range(2015, 2027))
        assert {w.window_type for w in windows} == {"year"}
        assert windows[-1] == chosen

    def test_month_series_rolls_the_year(self):
        """March 2026 trails back to April 2025, twelve slots inclusive."""
        scope = _calendar_scope([(1, date(2026, 1, 2))])
        chosen = SpendingWindow(window_type="month", month=3, year=2026)

        windows = _series_windows(scope, chosen)

        assert [(w.year, w.month) for w in windows] == [
            (2025, 4), (2025, 5), (2025, 6), (2025, 7), (2025, 8),
            (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2), (2026, 3),
        ]
        assert windows[-1] == chosen

    def test_pay_period_series_is_the_twelve_preceding_paychecks(self):
        """With 20 paydays, viewing #14 gives ids 3..14 and no blank slot.

        Paydays are ids 1..20 on a 14-day cadence from 2026-01-02, so the
        calendar's derived ordinals are 0..19 in that same order.  Viewing
        the period with ordinal 13 (id 14) fills every slot: ordinals
        2..13, which are ids 3..14.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(20)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=14)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert len(windows) == _CHART_WINDOW_COUNT
        assert [w.period_id for w in windows] == list(range(3, 15))
        assert windows[-1] == chosen

    def test_pay_period_series_pads_before_the_first_payday(self):
        """A schedule shorter than the chart leaves the LEADING slots blank.

        Four paydays, viewing the third (ordinal 2, id 3): the two earlier
        paychecks fill slots 9 and 10, the chosen one slot 11, and the nine
        slots below the owner's first payday are ``None`` rather than
        shortening the series -- which is what keeps ``series[-2]`` the
        prior window for every owner.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=3)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert [w.period_id if w else None for w in windows] == (
            [None] * 9 + [1, 2, 3]
        )

    def test_pay_period_series_of_the_first_paycheck_has_no_prior(self):
        """The earliest paycheck fills one slot; the other eleven are blank."""
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=1)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert windows[:-1] == [None] * (_CHART_WINDOW_COUNT - 1)
        assert windows[-1] == chosen

    def test_an_unmatched_period_id_leaves_every_earlier_slot_blank(self):
        """An id naming none of this calendar's periods resolves no predecessor.

        The ``None`` branch, stated at the unit level over a hand-written
        calendar; the case that MATTERS -- an id belonging to another owner
        rather than to nobody -- takes the same branch and is asserted
        end to end by
        ``TestTheChartReadsTheDerivedOrdinal.test_another_owners_period_id_buys_no_bar_of_this_owners_money``,
        which is where the money is.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=9999)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert windows == [None] * (_CHART_WINDOW_COUNT - 1) + [chosen]

    def test_the_series_never_reaches_past_the_chosen_window(self):
        """No slot names a paycheck LATER than the chosen one.

        The chart is retrospective: ``viewed_index`` is the last bar, so a
        later period appearing anywhere in the list would draw future
        spending beside the window the user asked for.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(20)
        ]
        scope = _calendar_scope(paydays)

        for period_id in range(1, 21):
            windows = _series_windows(
                scope,
                SpendingWindow(window_type="pay_period", period_id=period_id),
            )
            assert [w.period_id for w in windows if w is not None] == [
                earlier for earlier in range(1, period_id + 1)
            ][-_CHART_WINDOW_COUNT:]


class TestTheChartAndTheHeroCountTheSameWindows:
    """The two window counts are held in step by a test, not by a comment.

    Both modules state the relation and both say it is unenforced.  It was:
    mutation testing of plan step C2-f3d set ``_TRAILING_WINDOW_COUNT`` to 12
    and the whole 10,236-test suite stayed green, while the vs-average chip
    silently began averaging over a window the chart does not draw -- a wrong
    figure given quietly, which is this project's worst failure shape.  The
    precedent is ``test_pay_schedule.TestTheCadenceBoundHasOneValue``, which
    holds the two cadence bounds equal the same way.
    """

    def test_the_average_reads_fewer_windows_than_the_chart_draws(self):
        """vs-average must derive from bars the chart actually draws.

        ``_build_hero`` averages ``series[-(N + 1):-1]``, so the baseline
        stays inside the drawn series only while ``_TRAILING_WINDOW_COUNT``
        is strictly below ``_CHART_WINDOW_COUNT``.
        """
        assert 1 <= _TRAILING_WINDOW_COUNT < _CHART_WINDOW_COUNT

    def test_the_chart_has_room_for_a_chosen_window_and_a_prior(self):
        """``[-1]`` and ``[-2]`` are named positions, so the count is >= 2.

        ``_series_windows``, ``_build_series``, ``_build_hero`` and the
        chart's ``compare_index`` all index the last two slots directly.
        """
        assert _CHART_WINDOW_COUNT >= 2


class TestTheChartReadsTheDerivedOrdinal:
    """The chart's paychecks come from payday order.

    **Two firing controls for plan step C2-f3d went at plan step
    ``pay_calendar:C4-c``, and neither was replaced.**  The retired walk
    selected each bar with ``WHERE period_index = <chosen> - <steps>``, so a
    stored ordinal out of payday order put the wrong paycheck in a bar
    silently, and a HOLE in the stored ordinal moved a MONEY figure -- the
    vs-average baseline read ``$1,300.00`` against a true ``$1,250.00``.  Both
    cases built their state by writing ``budget.pay_periods.period_index``
    directly, and C4-c dropped that column: the ordinal is a row's position in
    payday order, so it cannot be scrambled and it cannot gap.

    What survives here is the ownership case, which is about the chosen
    period's OWNER rather than about its ordinal.
    """

    def test_another_owners_period_id_buys_no_bar_of_this_owners_money(
        self, app, seed_user, second_user, seed_periods, db,
    ):
        """A FOREIGN period id resolves nothing, chips included.

        The walk read the chosen id with an unscoped ``db.session.get``, so
        another owner's period supplied an ordinal and THIS owner's paychecks
        were listed beneath it: five bars of real money under a window whose
        own total is ``None``, with both hero chips priced off them
        (measured on the merge base: vs-prior ``$500.00``, vs-average
        ``$300.00``, each at ``-100%``).  The calendar holds one owner's
        periods, so the id now matches none and every slot is blank.

        Unreachable from ``/analytics/spending``, which exposes only month
        and year -- this pins the door rather than closing a live leak, and
        it is the ownership case ``9999`` cannot make because an id belonging
        to NOBODY takes the same branch as one belonging to someone else.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=10,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            foreign = (
                db.session.query(PayPeriod)
                .filter_by(user_id=second_user["user"].id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            for idx in range(5):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     f"{(idx + 1) * 100}.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="pay_period",
                               period_id=foreign[5].id),
                               user_settings=None,
            )

            assert [point.window for point in report.series[:-1]] == (
                [None] * (_CHART_WINDOW_COUNT - 1)
            )
            assert report.series[-1].window.period_id == foreign[5].id
            assert report.series[-1].total is None
            assert report.hero.spent_total == Decimal("0")
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_average.baseline is None
            assert report.breakdown == [] and report.changes == []

    def test_the_report_reads_pay_periods_exactly_once(
        self, app, seed_user, seed_periods, db,
    ):
        """A report reads one ``budget.pay_periods`` row set and hydrates none.

        **Over ROWS.**  Since leaf ``balance:X-bi-6-1b`` a window's transfers
        load beside its rows, and a transfer's pricing chain carries its
        period (``transfer_pricing_load_options``: rule 4's derive arm reads
        it), so a fixture seeding a settled transfer would read one more
        statement and hydrate its period; this fixture seeds none and the
        claim is the row path's.

        **Measured on the merge base at TWELVE statements for this fixture and
        TWENTY-THREE on a clone of production**, and the difference is the
        point: the retired walk's eleven ``db.session.get`` calls issued no
        statement whenever the chosen window's own rows had already hydrated
        that period, and eleven statements when it had no rows to do so (the
        identity map holds weak references).  Either way the eleven
        ``period_index`` queries beside them always ran.

        **The two assertions catch different things and neither is redundant**
        (mutation testing of this step measured the gap).  A statement count
        alone is order-dependent and JOIN-blind: nine per-bar
        ``db.session.get`` calls placed AFTER the row loads emit no statement,
        and an eager load renders as ``LEFT OUTER JOIN budget.pay_periods``
        inside the transactions query, which the capture predicate cannot see.
        The identity map sees both -- a report that resolves a paycheck any
        way at all leaves the ORM entity behind, and this path is meant to
        leave none, because the calendar is loaded as a column tuple.

        ONE statement is the count for an owner with a ``budget.pay_schedule``
        row, which every owner a live door creates has had since plan step
        X-ad-a.  A legacy owner without one (plan finding **P8**) reads TWO,
        because ``pay_schedule_service.resolve_cadence`` infers the cadence
        from a second ``budget.pay_periods`` query -- one that C4-c deleted with
        the column it orders by.  ``seed_user`` has the row.
        """
        with app.app_context():
            for idx in range(10):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     "100.00")
            db.session.commit()
            with _pay_period_selects(db.engine) as selects, \
                    pay_periods_hydrated() as hydrated:
                compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[9]),
                    user_settings=None,
                )

            assert len(selects) == 1, "\n".join(selects)
            # The one surviving read may not be an ORDINAL search: C4-c dropped
            # that column, and ``_loader.calendar_for`` selects id + start_date
            # precisely so it already runs against the schema C4 leaves.
            assert "period_index" not in selects[0]
            assert hydrated == [], (
                f"the report hydrated {len(hydrated)} PayPeriod row(s); "
                f"it resolves every paycheck from the calendar it already "
                f"holds, which is loaded as a column tuple"
            )

    def test_no_settled_expense_row_query_loads_a_pay_period(
        self, app, seed_user, seed_periods, db,
    ):
        """Neither window's ROW query hydrates ``Transaction.pay_period``.

        The legs half (leaf ``balance:X-bi-6-1b``) loads the parent's period
        by design; no transfer is seeded here, so this grades the row query
        alone (the body says so where it counts).  The other half of the
        guard above, and it needs its own case because
        the statement counter cannot see this one: an eager load rides INSIDE
        the transactions query as a ``JOIN budget.pay_periods``, so re-adding
        one emits no statement of its own and moves no count.  What it does
        move is the identity map, which is what this asserts.

        Nothing on this read path asks a row which paycheck it sits in, so
        both queries were carrying a per-row ``PayPeriod`` for nobody.  The
        span query keeps its JOIN -- the COALESCE attribution filter runs on
        it -- and drops only the hydration.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "200.00")
            db.session.commit()

            # The set of one account is the old single-account filter, byte
            # for byte (plan step CC-4-3); the assertion is about the loader.
            cash_flow = CashFlowSet.single(seed_user["account"])
            with pay_periods_hydrated() as hydrated:
                by_period = spending_analysis.query_settled_expenses(
                    seed_user["scenario"].id,
                    [seed_periods[0].id, seed_periods[1].id],
                    cash_flow,
                )
                by_span = spending_analysis.query_settled_expenses_in_span(
                    seed_user["scenario"].id, cash_flow,
                    seed_user["user"].id,
                    seed_periods[0].start_date, last_covered_day(seed_periods[1]),
                )

            # Both loads must actually return rows, or "nothing was
            # hydrated" is true of a query that loaded nothing at all.  Each
            # answers a PlanItems since leaf balance:X-bi-6-1b; no transfer
            # is seeded, so the legs half loads nothing and the claim below
            # is about the ROW query (a leg's parent carries its period by
            # design: rule 4's derive arm reads it).
            assert len(by_period.items) == 2 and len(by_span.items) == 2
            assert hydrated == [], (
                "a settled-expense query hydrated a PayPeriod.  This asserts "
                "the QUERY's loader, not that nothing downstream reads the "
                "relationship: since the surprises list began pricing through "
                "cash_ledger (2026-09-05), rule 4's loan arm reads "
                "txn.pay_period, so the old 'no consumer reads it' wording was "
                "made false by that change and is corrected here rather than "
                "left standing"
            )


# ── Trailing series (the chart / hero one-source identity) ──────────


class TestSeries:
    """The trailing same-type window series the chart and hero share."""

    def test_series_shape_and_pay_period_totals(self, app, seed_user,
                                                seed_periods, db):
        """Twelve points, viewed last; each point totals its own window.

        period[0..2] spend 100/200/300; window = period[2].  The series is
        _CHART_WINDOW_COUNT (12) points ending at the viewed window: the
        last three total 300/200/100 walking back, and every earlier slot
        is an all-None point (no period exists before period[0]).
        """
        with app.app_context():
            for idx, amt in enumerate(["100.00", "200.00", "300.00"]):
                _txn(db, seed_user, seed_periods[idx], f"T{idx}", "Rent", amt)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[2]),
                user_settings=None,
            )
            series = report.series
            assert len(series) == _CHART_WINDOW_COUNT
            assert series[-1].window.period_id == seed_periods[2].id
            assert series[-1].total == Decimal("300.00")
            assert series[-2].total == Decimal("200.00")
            assert series[-3].total == Decimal("100.00")
            # Slots before the user's first period are all-None points.
            assert all(
                p.window is None and p.total is None for p in series[:-3]
            )

    def test_series_month_zero_vs_none(self, app, seed_user, seed_periods, db):
        """A tracked zero-spend month is 0; a pre-history month is None.

        seed_periods span 2026-01-02 .. 2026-05-21.  Viewing March 2026:
        the series is Apr 2025 .. Mar 2026.  Jan spends 1200, Feb nothing
        (tracked, so Decimal 0), Mar spends 300; Apr-Dec 2025 overlap no
        period, so their totals are None (excluded from averages; drawn as
        baseline ticks).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[5], "MarBill", "Rent", "300.00",
                 due_date=date(2026, 3, 15))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=3, year=2026),
                user_settings=None,
            )
            series = report.series
            # Apr 2025 .. Dec 2025: before the user's first period.
            assert [p.total for p in series[:9]] == [None] * 9
            assert (series[9].window.year, series[9].window.month) == (2026, 1)
            assert series[9].total == Decimal("1200.00")
            assert series[10].total == Decimal("0")   # Feb: tracked, no spend
            assert series[11].total == Decimal("300.00")  # March (viewed)

    def test_year_window_series_end_to_end(self, app, seed_user,
                                           seed_periods, db):
        """A YEAR window produces a full twelve-point series through the report.

        The arm had no end-to-end case: the only ``window_type="year"`` outside
        ``tests/manual/`` was the unit test on ``_series_windows``.  It is
        unrenderable by the S-P1 deferral (the route exposes month only) and
        computable, which is exactly the pair that goes untested by accident.

        ``seed_periods`` spans 2026-01-02..2026-05-21 with 1200 spent in
        January, so 2026 totals 1200.00, 2025 and earlier overlap no pay
        period and are ``None``.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="year", year=2026),
                user_settings=None,
            )

            assert [p.window.year for p in report.series] == list(
                range(2015, 2027)
            )
            assert report.series[-1].total == Decimal("1200.00")
            assert [p.total for p in report.series[:-1]] == [None] * 11
            assert report.hero.spent_total == Decimal("1200.00")
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_average.baseline is None

    def test_hero_baselines_read_the_series(self, app, seed_user,
                                            seed_periods, db):
        """The hero's baselines derive from the series (one data source).

        Same data as above, viewing March: vs-prior baseline is the
        series' Feb point (0 -> delta 300.00, pct None); vs-average
        averages the existing points among Sep 2025..Feb 2026 -- Jan 1200
        and Feb 0 (the None 2025 months are skipped) -> (1200 + 0) / 2 =
        600.00; delta 300 - 600 = -300.00; pct -300/600*100 = -50.00.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[5], "MarBill", "Rent", "300.00",
                 due_date=date(2026, 3, 15))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=3, year=2026),
                user_settings=None,
            )
            assert report.hero.vs_prior.baseline == Decimal("0")
            assert report.hero.vs_prior.delta == Decimal("300.00")
            assert report.hero.vs_prior.pct is None
            assert report.hero.vs_average.baseline == Decimal("600.00")
            assert report.hero.vs_average.delta == Decimal("-300.00")
            assert report.hero.vs_average.pct == Decimal("-50.00")


# ── Window-over-window deltas (breakdown + By-change rows) ──────────


class TestDeltas:
    """Item, group, and By-change deltas on the D7 change basis."""

    def test_item_and_group_deltas(self, app, seed_user, seed_periods, db):
        """Items and groups carry current minus prior; new categories flag.

        period[0] (prior): Rent 1000, Groceries 400.
        period[1] (viewed): Rent 900, Car Payment 250.
        Viewed breakdown: Home/Rent 900 (delta 900 - 1000 = -100, not new);
        Auto/Car Payment 250 (delta +250, is_new -- no prior spend).
        Groceries has no viewed row, so Family is absent from the breakdown.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Food0", "Groceries", "400.00")
            _txn(db, seed_user, seed_periods[1], "Rent1", "Rent", "900.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "250.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert [g.group_name for g in report.breakdown] == ["Home", "Auto"]
            home = _group(report, "Home")
            assert home.delta == Decimal("-100.00")   # 900 - 1000
            assert home.is_new is False
            assert home.items[0].delta == Decimal("-100.00")
            assert home.items[0].is_new is False
            auto = _group(report, "Auto")
            assert auto.delta == Decimal("250.00")    # 250 - 0
            assert auto.is_new is True
            assert auto.items[0].is_new is True

    def test_group_delta_includes_stopped_categories(self, app, seed_user,
                                                     seed_periods, db):
        """A group's prior side sums categories with no current spend.

        Home has two categories: Rent and Electricity (created here).
        period[0] (prior): Rent 1000, Electricity 200 -> Home prior 1200.
        period[1] (viewed): Electricity 150 only -> Home current 150.
        Group delta = 150 - 1200 = -1050 (the stopped Rent still counts);
        the Electricity ITEM delta is only 150 - 200 = -50.
        """
        with app.app_context():
            electricity = Category(
                user_id=seed_user["user"].id, group_name="Home",
                item_name="Electricity",
            )
            db.session.add(electricity)
            db.session.flush()
            seed_user["categories"]["Electricity"] = electricity

            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Elec0", "Electricity",
                 "200.00")
            _txn(db, seed_user, seed_periods[1], "Elec1", "Electricity",
                 "150.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            home = _group(report, "Home")
            assert home.amount == Decimal("150.00")
            assert home.delta == Decimal("-1050.00")  # 150 - (1000 + 200)
            assert home.items[0].delta == Decimal("-50.00")  # 150 - 200

    def test_changes_include_zero_current_rows_sorted(self, app, seed_user,
                                                      seed_periods, db):
        """Changes span both windows; stopped categories appear at $0.

        period[0] (prior): Rent 1000, Groceries 400.
        period[1] (viewed): Groceries 460, Car Payment 250.
        Rows by |delta| descending: Rent (0 - 1000 = -1000, the
        zero-current rider), Car Payment (+250, is_new), Groceries
        (460 - 400 = +60).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Food0", "Groceries", "400.00")
            _txn(db, seed_user, seed_periods[1], "Food1", "Groceries", "460.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "250.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            rows = report.changes
            assert [r.item_name for r in rows] == [
                "Rent", "Car Payment", "Groceries",
            ]
            rent, car, groceries = rows
            assert (rent.current, rent.prior) == (Decimal("0"), Decimal("1000.00"))
            assert rent.delta == Decimal("-1000.00")
            assert rent.is_new is False              # nothing new about a stop
            assert rent.group_name == "Home"         # labels from the prior row
            assert car.delta == Decimal("250.00")
            assert car.is_new is True
            assert groceries.delta == Decimal("60.00")
            assert groceries.is_new is False

    def test_changes_tie_breaks_on_current_then_name(self, app, seed_user,
                                                     seed_periods, db):
        """Equal |delta| rows order by current spend, then item name.

        period[0]: Rent 100 (stops -> delta -100).  period[1]: Car Payment
        100 (new -> delta +100).  |delta| ties at 100; Car Payment has the
        higher current spend (100 > 0) so it sorts first.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert [r.item_name for r in report.changes] == [
                "Car Payment", "Rent",
            ]

    def test_tied_groups_order_by_name_not_by_the_query_plan(
        self, app, seed_user, seed_periods, db,
    ):
        """Two groups that spent the SAME are ordered by the data (**P74**).

        Neither settled-expense query carries an ``ORDER BY``; the per-category
        totals land in a dict in whatever order the database returned them,
        ``items_by_group`` is built by iterating that dict, and ``list.sort``
        is stable -- so before this ruling a tie was broken by the query plan,
        and an edit to either SELECT list could silently reorder the screen.

        ``Home`` and ``Auto`` both spend ``$100.00``.  ``Auto`` sorts first
        because ``A`` precedes ``H``, not because of anything the planner did.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[0], "C", "Car Payment", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            tied = [
                row.group_name for row in report.breakdown
                if row.amount == Decimal("100.00")
            ]
            assert tied == ["Auto", "Home"], (
                "a tied group ranking must be a function of the data"
            )

    def test_tied_surprises_order_by_id_so_the_CAP_is_deterministic(
        self, app, seed_user, seed_periods, db,
    ):
        """The five shown surprises are chosen by the data, not the plan (**P74**).

        This is the worst of the three unstable ranks because a CAP follows it:
        keyed on ``abs(delta)`` alone, two rows that missed their estimate by
        the same amount were separated only by the order the query happened to
        return them in, so at the boundary the DATABASE decided which row is on
        the screen rather than merely in what order.

        **The reducer is fed a REVERSED list on purpose, and the first version
        of this test did not do that and was a tautology**: driven through
        ``compute_spending_report`` the rows arrive in id order anyway, so a
        stable sort with no tiebreaker produced the same answer as a total one
        and the test passed against the defect it names.  Reversing the input
        is what makes the two orderings distinguishable -- an unstable rank
        drops the LOWEST id, a total one drops the highest.
        """
        with app.app_context():
            for n in range(6):
                _txn(
                    db, seed_user, seed_periods[0], f"S{n}", "Groceries",
                    "50.00", actual="60.00",
                )
            db.session.commit()

            rows = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .order_by(Transaction.id.desc())
                .all()
            )
            surprises = _build_surprises(
                rows,
                derived_amount_basis(
                    seed_user["user"].id, seed_user["scenario"].id,
                ),
            )

            ids = [row.item_key for row in surprises.rows]
            assert len(ids) == _MAX_SURPRISES, "the cap is five"
            assert ids == sorted(ids), (
                "tied surprises must rank by a total key: fed in reverse, an "
                "unstable rank keeps reverse order and drops the wrong row"
            )
            assert surprises.net == Decimal("60.00"), (
                "the net sums EVERY surprise, not the shown five"
            )


# ── Scope facts, empty window, and the None contract ────────────────


class TestScopeAndContracts:
    """Scope labels, the empty-window shape, and the no-account None."""

    def test_scope_facts(self, app, seed_user, seed_periods, db):
        """The report carries the checking account label and settled basis."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.scope.account_id == seed_user["account"].id
            assert report.scope.account_name == "Checking"
            assert report.scope.settled_only is True
            assert report.scope.window_label  # non-empty pay-period label

    def test_empty_window_shape(self, app, seed_user, seed_periods, db):
        """A resolvable window with no settled spend is a zeroed report.

        Not None: an empty breakdown, empty change rows, a zero spent total,
        an empty surprises list with a zero net, no payment timing, and a
        full-length series whose viewed point is a real zero (the window
        exists; it just has no settled spend).
        """
        with app.app_context():
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report is not None
            assert report.breakdown == []
            assert report.changes == []
            assert report.hero.spent_total == Decimal("0")
            assert report.surprises.rows == []
            assert report.surprises.net == Decimal("0")
            assert report.hero.payment_timing is None
            assert len(report.series) == (
                _CHART_WINDOW_COUNT
            )
            assert report.series[-1].total == Decimal("0")

    def test_none_when_no_active_checking(self, app, seed_user, seed_periods):
        """No resolvable checking account -> None (empty state, not a crash)."""
        with app.app_context():
            with patch(
                "app.services.spending_report_service.resolve_cash_flow_set",
                return_value=None,
            ):
                report = compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[0]),
                    user_settings=None,
                )
            assert report is None

    def test_none_when_no_baseline_scenario(self, app, seed_user, seed_periods):
        """No baseline scenario -> None (empty state, not a crash)."""
        with app.app_context():
            with patch(
                "app.services.spending_report_service.get_baseline_scenario",
                return_value=None,
            ):
                report = compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[0]),
                    user_settings=None,
                )
            assert report is None

    def test_invalid_window_raises(self, app, seed_user):
        """An unknown window type raises ValueError (shared validator)."""
        import pytest
        with app.app_context():
            with pytest.raises(ValueError, match="Invalid window_type"):
                compute_spending_report(
                    seed_user["user"].id,
                    SpendingWindow(window_type="bogus"),
                    user_settings=None,
                )


# ── Comparison value object ──────────────────────────────────────────


class TestTheReportReadsTheCashFlowSet:
    """Where the money went is the paycheck's across the set (plan step CC-4-3).

    Developer ruling ``credit_card:R-CC16``: the phone bill that is always
    paid by card is a row ON the card, so a report that read one account's
    settled rows left it out of where the money went.  Both window queries
    read the owner's cash-flow set now, through the one clause
    (:func:`~app.services.cash_flow_set.own_rows_clause` for the rows and
    :func:`~app.services.cash_flow_set.leg_accounts_shown` for a transfer's leg,
    since leaf ``balance:X-bi-6-1b``; it was ``paycheck_rows_clause``) -- graded
    once per query, a pay-period window and a month window -- and the scope
    names the BALANCE account, the primary.  Every case plants a card,
    because an owner with none is a set of one and cannot tell the clause
    from the filter it replaced.
    """

    @staticmethod
    def _card(seed_user, db_session):
        """Create an active Credit Card account for *seed_user*."""
        return create_account_of_type(
            seed_user, db_session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )

    def test_a_settled_bill_on_the_card_is_where_the_money_went(
        self, app, seed_user, seed_periods, db,
    ):
        """A pay-period window: Rent 500.00 on checking + Phone 45.00 on the card.

        spent_total = 500.00 + 45.00 = 545.00, both categories in the
        breakdown, and the scope still names Checking -- the rows are the
        set's, the name one member's.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "500.00")
            _txn(
                db, seed_user, seed_periods[0], "Phone", "Groceries", "45.00",
                account=card,
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("545.00")
            assert {g.group_name for g in report.breakdown} == {"Home", "Family"}
            assert report.scope.account_id == seed_user["account"].id
            assert report.scope.account_name == "Checking"

    def test_the_month_window_reads_the_same_set(
        self, app, seed_user, seed_periods, db,
    ):
        """The span query too: January 2026 holds both rows, 545.00.

        ``seed_periods[0]`` starts 2026-01-02, so both rows attribute to
        January by the COALESCE rule; the sibling query above is the
        pay-period arm, this is the one every live render runs.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "500.00")
            _txn(
                db, seed_user, seed_periods[0], "Phone", "Groceries", "45.00",
                account=card,
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("545.00")

    def test_a_transfer_between_members_is_counted_once_from_the_lines_side(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-CC23 on the report: the far leg is not spending.

        A settled $165.00 checking -> card payment is one settled expense,
        checking's expense leg (the card's is its income leg and the far
        leg).  A settled $40.00 card -> checking transfer is NO spending here:
        checking's leg is income and the card's expense leg is the far leg.
        spent_total = 165.00; without the far-leg arm the card's 40.00 leg
        would count too, 205.00.

        The transfer doors refuse a transfer OUT of a card (plan step CC-10),
        so the $40.00 card -> checking transfer is the representable-but-
        refused shape and its state is PLANTED, the way the legacy-source
        tests plant theirs: created Projected from a helper Savings account
        INTO checking (allowed), its source re-pointed onto the card by
        assignment, and only THEN settled through ``update_transfer`` -- the
        settle half of ``create_settled_transfer``, split out because the
        order decides where the POSTINGS land.  A side's payment record
        follows its endpoint whenever the endpoint moves
        (``fk_transaction_entries_expense_side`` is ON UPDATE CASCADE, ruling
        R-BAL168), but the settle books each record's postings on that
        record's account as it stands at the settle
        (``_posting_purchases``'s ``_ledger_account_for(entry.account_id)``),
        and a re-point by assignment passes no door that books them again:
        settled first, the card side's record follows the source onto the
        card while its postings stay on the helper's ledger.  Measured 10-15
        both ways; in this order the card side's record and its postings are
        on the card, checking's on checking, and the helper holds none.
        Neither reader here reads postings; the order is kept so the planted
        state is whole.  The re-point moved the transfer's expense
        shadow row too until plan step balance:X-bi-6-4d-2 made a transfer
        one row (ruling R-BAL258); the transfer is the whole plant now
        (ruling R-BAL167 class 1).  The savings account is not a member of
        the set.  The plant carries its own tell, the transfer's endpoints,
        because an UN-planted fixture (helper -> checking, never re-pointed)
        reads the same $165.00 with or without the far-leg arm and would
        grade nothing.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], card,
                seed_periods[0], amount=Decimal("165.00"),
            )
            helper = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            out_of_card = create_transfer(
                seed_user, db.session, helper, seed_user["account"],
                seed_periods[0], amount=Decimal("40.00"),
            )
            db.session.flush()
            out_of_card.from_account = card
            db.session.flush()
            transfer_service.update_transfer(
                out_of_card.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()
            assert (out_of_card.from_account_id, out_of_card.to_account_id) == (
                card.id, seed_user["account"].id,
            )

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("165.00")

    def test_the_default_reads_the_saved_default_grid_account(
        self, app, seed_user, seed_periods, db,
    ):
        """The scope is the set's PRIMARY, read off the settings row passed.

        ``resolve_analytics_account`` read "first active checking" and no
        settings row.  With the row passed, an HYSA saved as the default is
        the scope; with ``None`` the chain runs without that layer and
        checking answers.
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

            with_layer = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=settings,
            )
            without = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert with_layer.scope.account_name == "Rainy Day"
            assert without.scope.account_name == "Checking"


class TestATransferIsALegOfItsParent:
    """The report draws a settled transfer as a LEG read off its parent (leaf X-bi-6-1b).

    Both window loads answer the set's OWN rows -- no shadow -- beside the
    EXPENSE leg of every settled transfer the set touches, and the package's
    kernels (``settled_contribution`` and ``resolve_amount`` of a leg's
    parent, and ``resolved_actual_amount`` over it, ruling plan#1983) price
    a leg as its transfer.  The money case above (R-CC23) already
    grades what a transfer is WORTH here; these grade what it IS, and every
    figure the report derives from a leg.
    """

    def test_both_loaders_answer_own_rows_and_settled_expense_legs(
        self, app, seed_user, seed_periods, db,
    ):
        """$100.00 Rent and three transfers: settled out, planned out, settled in.

        Both settled-spend loaders (by period and by span) answer Rent as the
        one row and the settled $250.00 checking -> Savings transfer as its
        one expense leg; the planned $75.00 one is not settled and the
        settled $60.00 one INTO checking is income here, so neither is spend.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "100.00")
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"),
            )
            # A still-planned transfer is not spend; a settled transfer INTO
            # checking is its income leg on this set, not spend either.
            create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("75.00"),
            )
            create_settled_transfer(
                seed_user, db.session, savings, checking, seed_periods[0],
                amount=Decimal("60.00"),
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            by_period = spending_analysis.query_settled_expenses(
                seed_user["scenario"].id, [seed_periods[0].id], cash_flow,
            )
            by_span = spending_analysis.query_settled_expenses_in_span(
                seed_user["scenario"].id, cash_flow, seed_user["user"].id,
                seed_periods[0].start_date, last_covered_day(seed_periods[0]),
            )
            # "No shadow" is the database's since plan step
            # balance:X-bi-6-4d-2: a row naming a transfer is refused at the
            # write (ruling R-BAL258), so a per-row ``transfer_id is None``
            # check here passed with ``own_rows_clause``'s ``IS NULL`` term
            # deleted (measured 10-15) and is gone.
            for items in (by_period, by_span):
                assert [row.name for row in items.rows] == ["Rent"]
                assert [
                    (leg.transfer.id, leg.account_id, leg.is_expense)
                    for leg in items.legs
                ] == [(settled.id, checking.id, True)]

    def test_every_figure_the_report_derives_from_a_leg(
        self, app, seed_user, seed_periods, db,
    ):
        """$250.00 planned, $240.00 moved, due the day after it settled.

        spent_total 240.00 (the record, not the plan); the Uncategorized
        bucket holds it; the surprise is keyed by the pair with delta
        -10.00; timing counts one bill paid 1 day early.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
                settled_on=period.start_date,
                due_date=period.start_date + timedelta(days=1),
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.spent_total == Decimal("240.00")
            [group] = report.breakdown
            assert group.group_name == "Uncategorized"
            assert group.amount == Decimal("240.00")
            [surprise] = report.surprises.rows
            assert surprise.item_key == (settled.id, checking.id)
            assert surprise.name == "Transfer to Savings"
            assert (surprise.estimated, surprise.actual, surprise.delta) == (
                Decimal("250.00"), Decimal("240.00"), Decimal("-10.00"),
            )
            assert report.hero.payment_timing == {
                "total_bills_paid": 1,
                "paid_on_time": 1,
                "paid_late": 0,
                "avg_days_before_due": Decimal("1.00"),
            }

    def test_the_kernels_route_a_leg_to_its_producers(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled $250.00 transfer that moved $240.00 and a planned $75.00 one.

        Each is read through its checking leg's parent.  The settled one
        contributes its $240.00 record against its $250.00 plan and reads
        back $240.00 spent; the planned one reads back its $75.00 plan (zero
        variance), and ``settled_contribution`` refuses it: it recorded
        nothing.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
            )
            planned = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[1],
                amount=Decimal("75.00"),
            )
            db.session.commit()
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            settled_leg = grid_transfer_leg(settled, checking.id)
            planned_leg = grid_transfer_leg(planned, checking.id)

            assert settled_contribution(parent_of(settled_leg)) == Decimal("240.00")
            assert resolve_amount(parent_of(settled_leg), basis) == Decimal("250.00")
            assert spending_analysis.resolved_actual_amount(parent_of(settled_leg), basis) == Decimal("240.00")
            # An unsettled leg reads back its plan, so its variance is zero
            # by construction -- and it recorded nothing to spend.
            assert spending_analysis.resolved_actual_amount(parent_of(planned_leg), basis) == Decimal("75.00")
            with pytest.raises(AmountUnresolvable):
                settled_contribution(parent_of(planned_leg))

    def test_a_soft_deleted_settled_transfer_is_no_spend_on_either_arm(
        self, app, seed_user, seed_periods, db,
    ):
        """Both loaders' ``Transfer.is_deleted`` term (the review of leaf
        X-bi-6-1b): nothing here re-checks in Python, and a deleted settled
        transfer's leg would otherwise publish as a $0.00-versus-plan surprise
        with its name on the list (its shadow's movement is excluded by the
        record join, so the leg would read the $0.00 record)."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            deleted = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                deleted.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            cash_flow = CashFlowSet.single(checking)

            by_period = spending_analysis.query_settled_expenses(
                seed_user["scenario"].id, [period.id], cash_flow,
            )
            by_span = spending_analysis.query_settled_expenses_in_span(
                seed_user["scenario"].id, cash_flow, seed_user["user"].id,
                period.start_date, last_covered_day(period),
            )
            assert by_period.legs == [] and by_span.legs == []
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.spent_total == Decimal("0")
            assert report.surprises.rows == []

    def test_a_zero_record_transfer_is_spend_of_nothing_and_not_a_timed_bill(
        self, app, seed_user, seed_periods, db,
    ):
        """A transfer settled at $0.00 (ruling R-BAL82: a close with no
        movement) is $0.00 of spend, and its leg has no settle DAY --
        ``TransferLeg.settled_on`` is its movement's and it has none -- so it
        is not a timed bill.  The STANDING answer since ruling R-BAL90 (finding
        BAL-528, leaf X-bi-6-4a), which ruled the interval control this was
        (the review of leaf X-bi-6-1b, finding M1) permanent and renamed it;
        its row twin is the next test."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            zero = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("0.00"),
                settled_on=period.start_date,
                due_date=period.start_date + timedelta(days=1),
            )
            db.session.commit()

            [leg] = spending_analysis.query_settled_expenses(
                seed_user["scenario"].id, [period.id], CashFlowSet.single(checking),
            ).legs
            assert leg.transfer.id == zero.id and leg.record is None
            assert settled_contribution(parent_of(leg)) == Decimal("0")
            assert leg.settled_on is None and leg.days_paid_before_due is None
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.spent_total == Decimal("0")
            assert report.hero.payment_timing is None

    def test_a_zero_record_row_is_not_a_timed_bill_and_a_recorded_one_is(
        self, app, seed_user, seed_periods, db,
    ):
        """R-BAL90's row half (finding BAL-528, leaf X-bi-6-4a): a ROW closed
        at $0.00 holds no entry (ruling R-BAL82) and is not a timed bill,
        though it keeps its own ``settled_on`` -- the metric reads the RECORD
        of a row as of a leg.  The same row with a record is timed, which
        is what shows the gate is the record and not the day.  Through leaf
        X-bi-6-1b the $0.00 row counted, one day early."""
        with app.app_context():
            period = seed_periods[0]
            due = period.start_date + timedelta(days=1)
            _txn(
                db, seed_user, period, "Water", "Rent", "45.00",
                actual="0.00", due_date=due, settled_on=period.start_date,
            )
            db.session.commit()
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.payment_timing is None

            _txn(
                db, seed_user, period, "Power", "Rent", "60.00",
                due_date=due, settled_on=period.start_date,
            )
            db.session.commit()
            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )
            assert report.hero.payment_timing == {
                "total_bills_paid": 1, "paid_on_time": 1, "paid_late": 0,
                "avg_days_before_due": Decimal("1.00"),
            }

    def test_a_zero_record_close_moves_the_on_time_count_and_the_average_too(
        self, app, seed_user, seed_periods, db,
    ):
        """R-BAL90's declared change on a MIXED window (the leaf's review, M4).

        Both bills are due on the period's third day.  Water closes at
        ``$0.00`` on the first day (2 days early, no record); Power pays
        ``$60.00`` on the fifth (2 days late, recorded).  Through leaf
        X-bi-6-1b both were timed: 2 bills, 1 on time, 1 late, average
        ``(2 + -2) / 2 = 0.00``.  With Water out, Power alone is timed: 1 bill,
        0 on time, 1 late, average ``-2 / 1 = -2.00``.  So the hero's "Bills
        on time" chip reads ``0 / 1`` where it read ``1 / 2``, and its caption
        "avg 2.0 days late" where it read "avg 0.0 days early": the close moved
        the total, the on-time count it fell in, and the average, which it
        moves here (its +2 days against the rest's -2).  ``paid_late`` stays
        1.  What this pins is HOW MUCH the declared change moves the
        hero on a window holding a late bill; the gate itself is killed by
        the row twin above as well (``_holds_a_record`` forced ``True`` fails
        both).
        """
        with app.app_context():
            period = seed_periods[0]
            due = period.start_date + timedelta(days=2)
            _txn(
                db, seed_user, period, "Water", "Rent", "45.00",
                actual="0.00", due_date=due, settled_on=period.start_date,
            )
            _txn(
                db, seed_user, period, "Power", "Rent", "60.00",
                due_date=due, settled_on=period.start_date + timedelta(days=4),
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(period), user_settings=None,
            )

            assert report.hero.payment_timing == {
                "total_bills_paid": 1, "paid_on_time": 0, "paid_late": 1,
                "avg_days_before_due": Decimal("-2.00"),
            }

    def test_tied_surprises_rank_rows_before_legs_by_a_total_order(
        self, app, seed_user, seed_periods, db,
    ):
        """P74's total key over both shapes: a row's id and a leg's pair
        cannot be compared, so the tiebreak is ``key_order``."""
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"), settled_amount=Decimal("240.00"),
            )
            row = _txn(
                db, seed_user, period, "Rent", "Rent", "100.00", actual="90.00",
            )
            db.session.commit()
            items = spending_analysis.query_settled_expenses(
                seed_user["scenario"].id, [period.id], CashFlowSet.single(checking),
            ).items
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            surprises = _build_surprises(list(reversed(items)), basis)
            assert [s.delta for s in surprises.rows] == [
                Decimal("-10.00"), Decimal("-10.00"),
            ]
            assert [s.item_key for s in surprises.rows] == [
                row.id, (settled.id, checking.id),
            ]


class TestComparison:
    """The hero comparison value object's None-safety."""

    def test_of_none_baseline(self):
        """A None baseline yields an all-None comparison."""
        cmp = Comparison.of(Decimal("100.00"), None)
        assert cmp.baseline is None
        assert cmp.delta is None
        assert cmp.pct is None

    def test_of_zero_baseline(self):
        """A zero baseline yields a real delta but a None percent."""
        cmp = Comparison.of(Decimal("100.00"), Decimal("0"))
        assert cmp.baseline == Decimal("0")
        assert cmp.delta == Decimal("100.00")
        assert cmp.pct is None

    def test_of_NEGATIVE_baseline_reports_no_percent(self):
        """A refund-dominated prior window has no percent CHANGE to state.

        Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A
        window's spend could not be negative while ``_spent_total`` took an
        ``abs()`` per row; a merchant credit files as a NEGATIVE purchase now,
        so a window whose refunds exceeded its purchases comes to a negative
        total and can be a baseline.

        **The ratio against one reports the wrong DIRECTION.**  Spend rising
        from ``-50.00`` to ``100.00`` is a rise of ``150.00``, and
        ``150 / -50`` is ``-300%`` -- a fall of three hundred percent printed
        over a rise, on the hero chip the owner reads.  Asserts the figures
        SURVIVE beside the missing percent, because suppressing the delta too
        would hide a real movement.
        """
        cmp = Comparison.of(Decimal("100.00"), Decimal("-50.00"))

        assert cmp.baseline == Decimal("-50.00")
        assert cmp.delta == Decimal("150.00")
        assert cmp.pct is None

    def test_of_POSITIVE_baseline_still_reports_its_percent(self):
        """The control, without which the case above grades a chip that never
        computes a percent at all."""
        cmp = Comparison.of(Decimal("150.00"), Decimal("100.00"))

        assert cmp.delta == Decimal("50.00")
        assert cmp.pct == Decimal("50.00")


class TestARefundReducesSpendRatherThanAddingToIt:
    """A REFUND-dominated envelope must not report as SPENDING.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A merchant
    credit files as a NEGATIVE purchase against the envelope its merchant rule
    names, so a settled envelope's own figure -- ``sum(entries)`` on the
    ``purchases`` basis -- can be negative.

    **This class exists because an ``abs()`` was hiding exactly that**, at
    ``_breakdown._totals_by_category`` and ``_window._spent_total``.  Both were
    a provable no-op while ``ck_transaction_entries_positive_amount`` said
    ``amount > 0``, and both became a SIGN FLIP when that CHECK moved.
    Measured before the fix: ``-86.67`` reported as ``+86.67``, a `$173.34`
    error on a window in which the account RECEIVED the money.

    **The three shapes are asserted together deliberately.**  Only the
    refund-DOMINATED one inverts under ``abs()``; a partly-refunded envelope
    nets correctly either way.  A case staging only the ordinary or the
    partly-refunded shape would pass with the defect restored, which is why the
    boundary is pinned rather than a single example.
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
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(
                txn, ref_cache.status_id(StatusEnum.DONE),
            ),
        )
        db.session.flush()
        return txn

    def test_the_three_shapes_carry_their_own_signs(
        self, app, db, seed_user, seed_periods,
    ):
        """Ordinary, partly refunded, and refund dominated.

        Asserts the WINDOW total as well as the per-row figures, because the
        defect was in the summing and a per-row assertion alone would not have
        seen it.
        """
        with app.app_context():
            period = seed_periods[0]
            ordinary = self._settled_envelope(
                db, seed_user, period, "Ordinary", ["100.00"],
            )
            partly = self._settled_envelope(
                db, seed_user, period, "Partly refunded",
                ["100.00", "-30.00"],
            )
            dominated = self._settled_envelope(
                db, seed_user, period, "Refund dominated", ["-86.67"],
            )
            db.session.commit()

            assert settled_contribution(ordinary) == Decimal("100.00")
            assert settled_contribution(partly) == Decimal("70.00")
            # The one the ``abs()`` inverted.
            assert settled_contribution(dominated) == Decimal("-86.67")

            # 100.00 + 70.00 - 86.67.  Under the defect this read 256.67.
            assert _spent_total(
                [ordinary, partly, dominated],
            ) == Decimal("83.33")

    def test_a_refunded_category_total_is_NEGATIVE(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-category figure a refund-dominated window reports.

        A refund REDUCES what a category cost, which is what *did I stay in
        budget* has to mean.  Under the ``abs()`` this read ``+86.67`` and the
        category appeared as the window's largest spend.
        """
        with app.app_context():
            period = seed_periods[0]
            dominated = self._settled_envelope(
                db, seed_user, period, "Refund dominated", ["-86.67"],
            )
            db.session.commit()

            totals = _totals_by_category([dominated])

            assert totals[dominated.category_id].amount == Decimal("-86.67")


class TestTheShareIsASliceOfWhatMoved:
    """The "Where It Went" percent divides by MAGNITUDES, not by the net.

    Developer ruling 2026-09-01, plan step ``bank_import:X-gj-2b-3``.  Ruling
    **bank_import:R-II** let a category's refunds carry its window total below
    zero, and ``_share``'s denominator was the window's NET -- which refunds
    pull toward zero while a numerator does not.  Measured before the fix on
    ``Groceries 600.00`` beside ``Electronics -500.00``: a net of ``100.00``,
    so ``analytics/_spending.html`` printed **600%** and **-500%** beside
    figures of ``$600.00`` and ``-$500.00``.  The bars did not show it -- they
    scale to the largest row and floor at zero -- so the picture read correctly
    over numbers that did not.

    ``_share_base`` sums the categories' MAGNITUDES, which bounds every share
    in ``[-1, 1]`` outright.  These cases are built from ``_CategoryTotal``
    maps rather than from rows, because the defect is in the REDUCTION: a
    database-backed case would grade the query beside it and could pass on a
    window the query happened not to produce.
    """

    def _totals(self, *triples):
        """Return a ``{category_id: _CategoryTotal}`` map from label triples.

        Ids start at 1 so none of them is ``0``, which is the Uncategorized
        bucket and carries its own labelling rule.
        """
        return {
            index: _CategoryTotal(
                group_name=group, item_name=item, amount=Decimal(amount),
            )
            for index, (group, item, amount) in enumerate(triples, start=1)
        }

    def test_a_refunded_category_cannot_push_a_share_past_one(self):
        """600.00 beside -500.00: 55% and -45%, never 600% and -500%.

        The denominator is ``600 + 500 = 1100``, so the shares are
        ``600/1100 = 0.5455`` and ``-500/1100 = -0.4545`` (hand-computed, and
        quantized here so the assertion does not restate the producer's own
        division).  Restoring the net denominator makes them ``6`` and ``-5``
        and fails all four assertions.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Tech", "Electronics", "-500.00"),
            ),
            {},
        )
        by_group = {row.group_name: row for row in rows}
        cents = Decimal("0.0001")

        assert by_group["Food"].share.quantize(cents) == Decimal("0.5455")
        assert by_group["Tech"].share.quantize(cents) == Decimal("-0.4545")
        # The item shares divide by the same base, so the drill-down agrees
        # with the group line above it.
        assert by_group["Food"].items[0].share.quantize(cents) == Decimal(
            "0.5455",
        )
        assert by_group["Tech"].items[0].share.quantize(cents) == Decimal(
            "-0.4545",
        )

    def test_a_MIXED_SIGN_group_divides_by_the_same_base_as_its_items(self):
        """The case the bound's own argument turns on, and none staged it.

        Plan step ``bank_import:X-gj-2b-3``, found by adversarial test-quality
        review.  Every other case here gives each category its own group, so
        ``group_amount == items[0].amount`` and the group-level ``_share`` is
        never distinguished from the item-level one -- the four assertions
        above are two numbers asserted twice.

        A mixed-sign group is where ``|group| < sum of its items' magnitudes``
        is a STRICT inequality, which is exactly what makes the ``[-1, 1]``
        bound hold for groups: ``Food`` sums to ``100.00`` while holding
        ``600.00`` and ``-500.00``, so its share is ``100/1100`` and its items
        are ``600/1100`` and ``-500/1100``.  Nothing here would notice if
        ``_group_row`` divided by something else.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Food", "Electronics", "-500.00"),
            ),
            {},
        )
        cents = Decimal("0.0001")

        assert len(rows) == 1, "both categories belong to ONE group"
        assert rows[0].amount == Decimal("100.00")
        assert rows[0].share.quantize(cents) == Decimal("0.0909")
        by_item = {item.item_name: item for item in rows[0].items}
        assert by_item["Groceries"].share.quantize(cents) == Decimal("0.5455")
        assert by_item["Electronics"].share.quantize(cents) == Decimal("-0.4545")
        # THE BOUND, over every row this window renders.
        assert all(
            abs(share) <= 1
            for share in [rows[0].share] + [i.share for i in rows[0].items]
        )

    def test_a_window_with_no_refunds_is_unchanged(self):
        """The control the ruling turned on: magnitudes ARE the net there.

        ``Groceries 600.00`` and ``Dining 400.00`` sum to ``1000.00`` either
        way, so an ordinary month renders exactly what it always did.  Without
        this case the class above would pass on an implementation that divided
        by anything at all, including a constant.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Fun", "Dining", "400.00"),
            ),
            {},
        )
        by_group = {row.group_name: row for row in rows}

        assert by_group["Food"].share == Decimal("0.6")
        assert by_group["Fun"].share == Decimal("0.4")

    def test_a_window_that_nets_to_zero_still_states_its_shares(self):
        """``600.00`` against ``-600.00``: a net of ZERO, and real shares.

        The old guard returned ``0`` for every share of a non-positive total,
        so this whole window rendered ``0%`` on both rows.  The magnitude base
        is ``1200.00`` and neither row is zero, which is the honest reading:
        half of what moved went out and half came back.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Tech", "Electronics", "-600.00"),
            ),
            {},
        )
        by_group = {row.group_name: row for row in rows}

        assert by_group["Food"].share == Decimal("0.5")
        assert by_group["Tech"].share == Decimal("-0.5")

    def test_a_window_that_moved_nothing_has_no_shares(self):
        """The only base a sum of magnitudes can take that has no shares.

        A category present and worth exactly ``0.00`` -- an envelope settled
        with no purchases -- is the reachable shape, and the division is
        skipped rather than raising.
        """
        rows = _build_breakdown(
            self._totals(("Food", "Groceries", "0.00")), {},
        )

        assert rows[0].share == Decimal("0")
        assert rows[0].items[0].share == Decimal("0")


class TestNewMeansTheCategoryWasNotThereBefore:
    """The "new" badge asks PRESENCE, and a zero total is not absence.

    Plan step ``bank_import:X-gj-2b-3``.  All three ``is_new`` fields read
    ``prior_amount == ZERO``, which is a different question from *the prior
    window held no row for this*.  Two reachable shapes total zero while being
    present: an envelope settled with NO purchases contributes ``0`` and is in
    the window's map, and since ruling **bank_import:R-II** so does one whose
    refunds exactly cancelled its purchases.  Both rendered the badge on a
    category that had been there all along.

    The maps' KEYS are what record which categories a window held, so absence
    is read off them.
    """

    def _one(self, amount):
        """Return a one-category map worth *amount*, under a stable label."""
        return {
            7: _CategoryTotal(
                group_name="Food", item_name="Groceries",
                amount=Decimal(amount),
            ),
        }

    def test_a_category_that_broke_even_before_is_not_new(self):
        """Prior window HELD it at ``0.00``; the badge must not render.

        Asserts the delta beside it, because the two read the same prior
        figure and only one of them changed: the delta is still ``100.00``.
        """
        rows = _build_breakdown(self._one("100.00"), self._one("0.00"))

        assert rows[0].is_new is False
        assert rows[0].items[0].is_new is False
        assert rows[0].items[0].delta == Decimal("100.00")

    def test_a_category_absent_before_is_new(self):
        """The control: a category the prior window did not hold at all."""
        rows = _build_breakdown(self._one("100.00"), {})

        assert rows[0].is_new is True
        assert rows[0].items[0].is_new is True

    def test_a_group_whose_only_category_broke_even_before_is_not_new(self):
        """The GROUP arm has its own map and its own defect.

        Its prior side was a ``defaultdict`` summed per group, so an absent
        group and a group that summed to zero were the same value.  A category
        that changed groups is the shape that separates them: the prior window
        held ``Food: Groceries`` at ``0.00``, so the group is not new even
        though this window's category id is different.
        """
        rows = _build_breakdown(
            {8: _CategoryTotal("Food", "Takeout", Decimal("40.00"))},
            self._one("0.00"),
        )

        assert rows[0].group_name == "Food"
        assert rows[0].is_new is False
        # The ITEM is new -- category 8 was not in the prior window -- which is
        # what makes this case grade the group arm rather than the item one.
        assert rows[0].items[0].is_new is True

    def test_a_change_row_for_a_category_that_broke_even_before_is_not_new(
        self,
    ):
        """The By-change lens's own arm, over the same two maps."""
        changes = _build_changes(self._one("100.00"), self._one("0.00"))

        assert len(changes) == 1
        assert changes[0].is_new is False

    def test_a_change_row_that_breaks_even_NOW_is_still_new(self):
        """The half the old spelling got wrong in the other direction.

        ``prior_amount == ZERO and current_amount > ZERO`` withheld the badge
        from a category that appeared this window and netted to zero -- a
        purchase and its refund in one window, which is exactly what ruling
        **bank_import:R-II** files.  The row exists only because one of the two
        windows held it, so the prior window's absence is the whole test.
        """
        changes = _build_changes(self._one("0.00"), {})

        assert len(changes) == 1
        assert changes[0].is_new is True


class TestTheActualHalfAsksTheAmountModel:
    """`resolved_actual_amount`'s fall-through, which had NO test at all.

    **The kernel was public, documented as the shared "what did this row
    actually cost" rule, and reached by exactly one settled-only caller** -- so
    every case in this file drove it through the arm that returns a settlement,
    and the arm below ran nowhere in the suite.  Two neutral adversarial reviews
    of plan step X-bu found what was hiding there: it read the row's own
    ``estimated_amount`` column and REFUSED a row carrying none, which is
    finding **BAL-462**'s failure mode on the UNSETTLED population instead of
    the settled one -- the same exception, the same page, the same class of row,
    held off only by a caller convention.  X-bu re-pointed it at the amount
    model (developer ruling 2026-09-05, superseding BAL-462's "is correct and
    stays" clause) and this class is the arm's first coverage.

    **Its fixture is an engine-generated row since plan step
    balance:X-bi-6-4d-2**, which made a transfer one row (ruling R-BAL258).
    It was a projected transfer's expense shadow until then: the twin was the
    class's FIXTURE for an unsettled derived row, replaced by another member
    of that population (an engine-generated row), assertions unchanged (the
    coordinator's ruling, 10-15; not an R-BAL167 class).  A transfer LEG
    takes the fall-through's other arm --
    :func:`~app.services.cash_ledger.resolve_amount` prices it off its
    parent -- and
    :meth:`TestATransferIsALegOfItsParent.test_the_kernels_route_a_leg_to_its_producers`
    grades that arm (an unsettled leg's actual is its plan, ``$75.00``).
    """

    @staticmethod
    def _unsettled_derived_row(db, seed_user, period):
        """Generate a definition's row in *period*: unsettled, priced $321.00 on its own date.

        The definition states TWO prices, so the cases grade which one the
        fall-through reads: ``$321.00`` effective thirty days before the row,
        the version the row's DATE selects, and ``$200.00`` on the owner's
        today (:func:`make_expense_template`'s own statement), the NEWEST
        price and so the one ``default_amount`` holds.  A fall-through that
        answered the definition's newest price rather than its price on the
        row's own due date would read ``$200.00``.  Both prices were
        ``$321.00`` when plan step ``balance:X-bi-6-4d-2`` first built this
        helper, and a fall-through reading ``default_amount`` passed both
        cases (measured by that step's delta review).

        The two facts hold on every clock later than the row's due date -- a
        seeded January 2026 day, so every day this suite runs from now on --
        and the guards below assert each, so a clock that broke one fails
        its guard by name rather than the figure (the guard shape of
        ``test_a_template_priced_row_reads_the_version_for_its_own_due_date``,
        which names the clock trap).
        """
        template = make_expense_template(
            db.session, seed_user, amount="200.00", name="Derived Bill",
        )
        db.session.flush()
        state_template_price(
            template, Decimal("321.00"),
            effective_on=period.start_date - timedelta(days=30),
        )
        db.session.flush()
        row = generate_row_of(template, period)
        db.session.commit()
        assert template.default_amount == Decimal("200.00"), (
            "the newest price must differ from the one the row's date "
            "selects, or a fall-through reading default_amount passes"
        )
        assert template_amount_service.amount_as_of(
            template, row.due_date,
        ) == Decimal("321.00"), (
            "the row's due date must select the $321.00 stated before it, "
            "which a today-version dated between the two would supersede"
        )
        return row

    def test_an_unsettled_derived_row_is_PRICED_not_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsettled row of a definition: no figure of its own, and a plan anyway.

        The fixture is a production shape rather than a contrivance: a row
        the recurrence engine generates from a recurring definition is
        DERIVED -- it stores no plan, and its definition's price series
        answers for it on its own due date -- which is exactly what the old
        column read could not answer.

        It asserts the FIGURE, not the absence of an exception: a test that only
        proved this returns would pass against a fix that priced the row from
        its settlement or from zero, and `$0.00` is precisely the answer that
        never looks wrong on a page.
        """
        with app.app_context():
            row = self._unsettled_derived_row(db, seed_user, seed_periods[0])
            assert row.estimated_amount is None, (
                "the precondition: a generated row stores no plan of its "
                "own, so the column read this arm used to make cannot answer it"
            )
            assert status_seam.recorded_settlement(row) is None, (
                "and it has recorded nothing, so this row reaches the "
                "fall-through rather than the settlement arm"
            )

            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert spending_analysis.resolved_actual_amount(
                row, basis,
            ) == Decimal("321.00"), (
                "an unsettled row's actual is what the amount model says its "
                "plan is -- its definition's price on its own due date"
            )

    def test_an_unsettled_rows_variance_is_zero_BY_CONSTRUCTION(
        self, app, db, seed_user, seed_periods,
    ):
        """Both halves are the same call, so there is nothing to disagree.

        The property the re-point buys, and the reason it is worth more than a
        second producer that happens to agree: the surprises list's two terms
        for an unsettled row are now ``resolved_actual_amount`` and
        ``resolve_transaction_amount`` over the SAME row and the SAME basis, so
        the delta cannot be non-zero.  Before X-bu the two were different
        producers over different sources -- the column and the amount model --
        and on a derived row they did not merely differ, one of them RAISED.

        The same two preconditions as the case above, so this cannot pass
        through the settlement arm: a row that had recorded its plan would
        also read a zero variance.
        """
        with app.app_context():
            row = self._unsettled_derived_row(db, seed_user, seed_periods[0])
            assert row.estimated_amount is None, (
                "the precondition: a generated row stores no plan of its own"
            )
            assert status_seam.recorded_settlement(row) is None, (
                "and it has recorded nothing, so both halves reach the "
                "amount model rather than the settlement arm"
            )
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            surprises = _build_surprises([row], basis)

            assert surprises.rows == [], (
                "an unsettled row is not a surprise: its actual and its "
                "estimate are one call on one row against one basis"
            )
            assert surprises.net == Decimal("0")


class TestASettledRowWhosePlanIsDerivedIsPriced:
    """A settled row carrying no figure of its OWN still has a plan."""

    def test_a_settled_transfer_leg_is_priced_from_its_parent_not_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The estimate is the PARENT's figure, and the delta is real.

        Regression for the release blocker measured on a production clone
        2026-09-05.  The amount-source cutovers declare a row DERIVED and empty
        its ``estimated_amount``; 116 of the rows they declared on production
        are SETTLED, and a settled EXPENSE is exactly what this window loads.
        This list read ``owned_amount``, a cheap accessor that answered a row's
        own column and REFUSED one carrying none -- so the first derived row it
        met raised ``AmountUnresolvable`` and ``/analytics/spending`` returned
        500 for every window.  Measured: 200 on the shipped image, 500 on the
        candidate, across 323 routes the only regression.  *That accessor no
        longer exists: plan step X-bu deleted it and folded its body into the
        resolver's own OWN arm, so the one-token answer this list reached for is
        gone.  X-bu also took the ACTUAL half's fall-through onto the same
        resolver, which is the sibling case ``TestTheActualHalfAsksTheAmountModel``
        below pins.*

        **It asserts the FIGURE, not the absence of an exception.**  A test that
        only proved this returns at all would pass against a fix that priced the
        row wrongly, and the production symptom the sibling defect carries is
        ``+$9.81`` on a paycheck -- exactly the size that never looks wrong on a
        page.  So the estimate is pinned to the parent's ``$321.00`` and the
        delta to the ``$79.00`` the settlement actually recorded.

        The reducer is handed the transfer's expense LEG -- what
        ``query_settled_expenses`` loads for a transfer -- where it was handed
        the expense twin row, which records nothing since plan step
        ``balance:X-bi-6-4d-2`` (the side's record hangs off the transfer and
        the transfer's status is the one status; ruling R-BAL167 class 1).
        Its precondition read that twin -- ``estimated_amount is None``, the
        shadow storing no plan of its own -- and went with it at plan step
        ``balance:X-bi-6-4d-2`` (ruling R-BAL258, class 1 with nothing
        re-expressed): no such row can be stored now
        (``test_a_transfer_is_one_row/test_the_upgrade.py::TestTheUpgrade::
        test_the_database_refuses_a_twin``),
        and a leg has no figure field at all, its plan being the parent's
        through :func:`~app.services.cash_ledger.resolve_amount`.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Surprises Savings",
                Decimal("1000.00"),
            )
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
                amount=Decimal("321.00"), settled_amount=Decimal("400.00"),
            )
            db.session.commit()

            expense_leg = grid_transfer_leg(xfer, seed_user["account"].id)

            surprises = _build_surprises(
                [expense_leg],
                derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id),
            )

            assert len(surprises.rows) == 1, (
                "a settled row whose actual differs from its plan is a surprise "
                "whether or not the plan is stored"
            )
            row = surprises.rows[0]
            assert row.estimated == Decimal("321.00"), (
                "the plan is the parent transfer's figure, resolved live -- not "
                "the settled amount (which would zero every delta) and not a "
                "refusal"
            )
            assert row.actual == Decimal("400.00")
            assert row.delta == Decimal("79.00")
            assert surprises.net == Decimal("79.00")

    def test_the_whole_report_renders_it_rather_than_500ing(
        self, app, db, seed_user, seed_periods,
    ):
        """Through ``compute_spending_report``, which is the door that 500'd.

        The sibling above pins the reducer; this pins the WIRING.  The
        production symptom was a 500 from ``/analytics/spending``, and a test
        that only calls ``_build_surprises`` directly proves the reducer can
        price a derived row while proving nothing about the basis being built
        and threaded in ``compute_spending_report`` -- which is the line the
        fix actually added.  Raised by an adversarial review of the fix.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "E2E Savings", Decimal("1000.00"),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
                amount=Decimal("321.00"), settled_amount=Decimal("400.00"),
            )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )

            assert report is not None
            shadow = [
                row for row in report.surprises.rows
                if row.delta == Decimal("79.00")
            ]
            assert len(shadow) == 1, (
                "the settled derived shadow reaches the rendered report, and "
                "before the fix this call raised AmountUnresolvable instead"
            )
            assert shadow[0].estimated == Decimal("321.00")

    def test_a_template_priced_row_reads_the_version_for_its_own_due_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The rule that prices 59 of production's 78 affected rows.

        The transfer shadow above is rule 5.  Rule 3 -- a row generated from a
        recurring definition, priced by that definition's dated series -- is a
        different path (``_rule_within_definition`` -> ``_stated_amount`` ->
        ``amount_as_of``) with refusal arms the transfer path never reaches, and
        it is the DOMINANT shape among the settled rows the cutovers declared.
        Raised by an adversarial review, which observed the first test covered
        the minority shape.

        It asserts the series is read on the ROW'S OWN DUE DATE and not at its
        newest version: the definition is re-priced to ``$200.00`` effective
        after this row's due date, so a resolver reading the latest version
        would answer ``$200.00`` and make the delta ``-$50.00`` instead of
        ``+$25.00``.

        **The ``$200.00`` version is dated TOMORROW, not sixty days after the
        row, and plan step balance:X-cf-3 measured why.**  ``make_expense_template``
        states the ``$100.00`` price effective TODAY, so a version sixty days
        after a January 2026 row stopped being the newest the day the suite
        ran past March 2026, and a resolver mutated to read the newest
        version -- ``default_amount``, which ``set_amount`` re-syncs onto it
        -- answered ``$100.00`` and passed.  Tomorrow is newest on every
        clock.  Red with that mutation, green without.
        """
        with app.app_context():
            period = seed_periods[0]
            template = make_expense_template(
                db.session, seed_user, amount="100.00", name="Series Priced",
            )
            db.session.flush()
            state_template_price(
                template, Decimal("100.00"),
                effective_on=period.start_date - timedelta(days=30),
            )
            state_template_price(
                template, Decimal("200.00"),
                effective_on=display_today() + timedelta(days=1),
            )
            db.session.flush()
            assert template.default_amount == Decimal("200.00"), (
                "the newest version must be the one the row's date does NOT "
                "select, or the mutation this case guards against passes"
            )

            # The engine's row, due on the paycheck's own start (what
            # ``compute_due_date`` derives for an every-paycheck definition),
            # then SETTLED on that day at $125.00 -- the record laid on bare,
            # through the one door a bare-built settlement goes through.
            txn = generate_row_of(template, period)
            due = txn.due_date
            assert due == period.start_date
            txn.status_id = ref_cache.status_id(StatusEnum.DONE)
            for column, value in settle_day_columns(due).items():
                setattr(txn, column, value)
            db.session.flush()
            cover_bare_settled_row(db.session, txn, "125.00")
            db.session.commit()

            surprises = _build_surprises(
                [txn],
                derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id),
            )

            assert len(surprises.rows) == 1
            row = surprises.rows[0]
            assert row.estimated == Decimal("100.00"), (
                "the plan is the series version in effect on the row's OWN due "
                "date; reading the newest version would answer $200.00"
            )
            assert row.actual == Decimal("125.00")
            assert row.delta == Decimal("25.00")
