"""The month's daily running-balance projection, and a refunded day on the real calendar's producer
chain.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum

from app.services import calendar_service, status_seam
from tests._test_helpers import add_entry, create_envelope_txn, settlement_if_settling
from app.services.calendar_service import DailyView

from ._helpers import _add_transaction


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
    is ``contributions_by_key -> fixed_contribution -> settled_figure ->
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
