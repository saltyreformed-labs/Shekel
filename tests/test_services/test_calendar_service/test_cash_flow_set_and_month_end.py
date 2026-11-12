"""The month detail across the cash-flow set: the day cells hold the paycheck's rows across the set
(plan step CC-4-3), paycheck days, and the projected month-end balance.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum

import pytest

from app.services import calendar_service
from tests._test_helpers import (
    create_account_of_type,
    create_savings_account,
    create_transfer,
    last_covered_day,
    one_off_row_of,
    set_default_grid_account,
)
from app.services.calendar_service import CalendarAccountNotResolvableError

from ._helpers import _add_transaction


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
