"""Service tests for the confirmed-ledger reporting statements (Step 5, C9).

Hand-computed income statements and balance sheets over controlled fixtures,
each produced through the REAL go-forward primitives (``create_account``
records the opening; ``create_settled_cash_transaction`` /
``create_settled_transfer`` settle through the status seam and
``transfer_service``), so every settled figure is read off records the real
doors wrote.  The expected numbers are the test author's arithmetic over the
seeded anchors and amounts, owing nothing to the reader under test.

Scope is the reporting SERVICE (``ledger_report_service``), which reads the
ledger worked out from the records since plan task plan#2771: the reader
contract over its lines (whole bookings, by their civil day; pay-period
windows by the paycheck a line is filed in), the natural-balance presentation
and derived retained earnings, the two-part balance-sheet tie-out, and the
labels of the worked-out chart.
The exhaustive cross-year / loan / articulation oracle is Commit 13's
``tests/test_integration/test_posting_ledger_statements/`` package.

Balance-sheet "full position" cases use a far-future ``as_of`` so every posted
source (including the seed Checking's opening, whose ``entry_date`` is its
origination ``created_at``'s civil date) is folded regardless of the test clock
or timezone; a dedicated case pins the as-of cutoff itself.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.exceptions import BaselineMissingError

from app.models.account import AccountAnchorHistory
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.services import ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for
from app.utils.dates import pay_period_range_label
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    last_covered_day,
)

# A far-future as-of that folds every posted source into the balance sheet,
# clock- and timezone-independent (nothing is ever attributed after it).
_ALL_ACTIVITY = date.max

# A settle DAY after every account these fixtures use was opened, so a settle
# pinned here RIDES ON TOP of the opening rather than being absorbed into it.
# A settle dated before origination is instead already inside the asserted
# opening balance.  It was a noon-UTC instant until plan step X-f1; the column
# stores a civil day now, and handing an instant to the seam is refused
# (finding N-179) rather than silently truncated on the UTC session clock.
#
# **It was ``2099-06-01`` until ruling R-EJ**, chosen as "far future" so it
# would clear an origination stamped with the server clock.  R-EJ refuses a
# settle dated after today -- a settled row asserts that money HAS moved -- so
# that shape was only ever expressible while the guard was missing.  The fix is
# the production one and it runs the other way: open the account EARLY
# (:data:`_OPENED_BEFORE`) and settle in the past.
#
# 2025 is chosen because it is empty: ``seed_user``'s bootstrap period opens
# 2024-01-05 and ``seed_periods`` runs 2026-01-02 to 2026-05-21, so a "year"
# window here still sees only what the fixture put there, which is the property
# the far-future date was really bought for.  The services suite freezes today
# at 2026-03-20, so this day is in its past.
_RIDES_ON_TOP = date(2025, 6, 1)

#: The day accounts these fixtures create are OPENED on -- before
#: :data:`_RIDES_ON_TOP`, so the settle rides on top of the opening.  Passed to
#: the factory, which dates the origination assertion on it; the worked-out
#: ledger books the opening's correction on that day.
_OPENED_BEFORE = date(2025, 1, 1)


def _find_line(lines, label):
    """Return the single :class:`StatementLine` in *lines* with *label*."""
    matches = [line for line in lines if line.label == label]
    assert len(matches) == 1, (
        f"expected exactly one {label!r} line, got {[m.label for m in lines]}"
    )
    return matches[0]


def _labels(lines):
    """Return the ordered labels of *lines* (they are label-sorted)."""
    return [line.label for line in lines]


# ---------------------------------------------------------------------------
# 1. Income statement -- calendar windows (reader-contract C-3)
# ---------------------------------------------------------------------------


class TestIncomeStatementCalendarWindow:
    """A month/year window sections revenue and cost by display-timezone date."""

    def test_month_window_sections_and_totals(self, app, db, seed_user):
        """March 2026: income 2000, expense 300 + fallback 50 -> net 1650.

        Three settles pinned into March 2026 (noon UTC, so the display-tz civil
        day is the same March day):

          - income  "Income: Salary"       $2000.00  paid 2026-03-10
          - expense "Family: Groceries"    $ 300.00  paid 2026-03-15
          - expense Uncategorized (no cat) $  50.00  paid 2026-03-20

        Income line +2000 (a credit-normal Income account presents negated);
        expense lines +300 and +50 (debit-normal, as-is), sorted by label so
        "Family: Groceries" precedes "Uncategorized Expense".  net income =
        2000 - (300 + 50) = 1650.00.  The seed Checking opening is a correction
        on Asset/Equity accounts, never Income/Expense, so it never appears.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            create_settled_cash_transaction(
                seed_user, period, Decimal("2000.00"),
                account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(2026, 3, 10),
            )
            create_settled_cash_transaction(
                seed_user, period, Decimal("300.00"),
                account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2026, 3, 15),
            )
            create_settled_cash_transaction(
                seed_user, period, Decimal("50.00"),
                account=checking, category=None,
                settled_on=date(2026, 3, 20),
            )
            db.session.commit()

            report = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=2026),
            )

            assert report.window_label == "March 2026"
            assert _labels(report.income.lines) == ["Income: Salary"]
            assert _find_line(
                report.income.lines, "Income: Salary",
            ).amount == Decimal("2000.00")
            assert _labels(report.expense.lines) == [
                "Family: Groceries", "Uncategorized Expense",
            ]
            assert _find_line(
                report.expense.lines, "Family: Groceries",
            ).amount == Decimal("300.00")
            assert _find_line(
                report.expense.lines, "Uncategorized Expense",
            ).amount == Decimal("50.00")
            assert report.income.total == Decimal("2000.00")
            assert report.expense.total == Decimal("350.00")
            assert report.net_income == Decimal("1650.00")

    def test_window_bounds_month_excludes_year_includes(
        self, app, db, seed_user,
    ):
        """A March expense and an April expense: month sees one, year sees both.

        Groceries $300 paid 2026-03-15 and Rent $800 paid 2026-04-15.  The
        March 2026 month window totals 300.00 (April excluded); the 2026 year
        window totals 1100.00 (both), proving the calendar bound filters the
        lines by their day.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            create_settled_cash_transaction(
                seed_user, period, Decimal("300.00"),
                account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2026, 3, 15),
            )
            create_settled_cash_transaction(
                seed_user, period, Decimal("800.00"),
                account=checking, category=seed_user["categories"]["Rent"],
                settled_on=date(2026, 4, 15),
            )
            db.session.commit()

            march = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=2026),
            )
            assert _labels(march.expense.lines) == ["Family: Groceries"]
            assert march.expense.total == Decimal("300.00")

            year = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2026),
            )
            assert year.window_label == "2026"
            assert _labels(year.expense.lines) == [
                "Family: Groceries", "Home: Rent",
            ]
            assert year.expense.total == Decimal("1100.00")


# ---------------------------------------------------------------------------
# 2. Income statement -- pay-period window (reader-contract C-2)
# ---------------------------------------------------------------------------


class TestIncomeStatementPayPeriodWindow:
    """A pay-period window takes the lines filed in its paycheck, not by date."""

    def test_period_window_scopes_to_its_period(self, app, db, seed_user):
        """Groceries $100 in the bootstrap period, Rent $200 in a later period.

        The bootstrap-period window sees only the $100 Groceries expense; the
        second period's window sees only the $200 Rent -- the pay-period bound
        keys on the paycheck a line is filed in, independent of any paid date.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period1 = seed_user["bootstrap_period"]
            period2 = PayPeriod(user_id=user_id, start_date=date(2026, 2, 6))
            db.session.add(period2)
            db.session.flush()

            create_settled_cash_transaction(
                seed_user, period1, Decimal("100.00"),
                account=checking,
                category=seed_user["categories"]["Groceries"],
            )
            create_settled_cash_transaction(
                seed_user, period2, Decimal("200.00"),
                account=checking, category=seed_user["categories"]["Rent"],
            )
            db.session.commit()

            first = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id),
                StatementWindow("pay_period", period_id=period1.id),
            )
            assert _labels(first.expense.lines) == ["Family: Groceries"]
            assert first.expense.total == Decimal("100.00")

            second = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id),
                StatementWindow("pay_period", period_id=period2.id),
            )
            assert _labels(second.expense.lines) == ["Home: Rent"]
            assert second.expense.total == Decimal("200.00")

    def test_a_true_up_falls_in_the_paycheck_its_day_files_in(
        self, app, db, seed_user, seed_periods,
    ):
        """A Money Market trued up $1,000.00 -> $1,015.01 on Feb 3: that paycheck's.

        The true-up's ``1,015.01 - 1,000.00 = 15.01`` books on the account's
        Interest Income line (ruling R-FO), and its line names no paycheck, so
        the window files it by its day (finding plan#2875,
        :meth:`~app.services.ledger_lines.LedgerLine.filed_period_id`): Feb 3
        is inside the paycheck of Jan 30 - Feb 12, whose window reads it, and
        the paycheck before it reads none.  It opened Jan 5, in the first
        paycheck, which books only to equity.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = create_account_of_type(
                seed_user, db.session, "Money Market", "Filed MM",
                anchor_balance=Decimal("1000.00"), observed_on=date(2026, 1, 5),
            )
            trued_on = date(2026, 2, 3)
            db.session.add(AccountAnchorHistory(
                account_id=account.id, anchor_balance=Decimal("1015.01"),
                observed_on=trued_on, recorded_on=trued_on,
            ))
            db.session.commit()
            filed_in, before = seed_periods[2], seed_periods[1]
            assert filed_in.start_date <= trued_on <= last_covered_day(filed_in)

            report = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id),
                StatementWindow("pay_period", period_id=filed_in.id),
            )
            assert _labels(report.income.lines) == ["Filed MM -- Interest Income"]
            assert report.income.total == Decimal("15.01")

            earlier = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id),
                StatementWindow("pay_period", period_id=before.id),
            )
            assert not earlier.income.lines

    def test_another_owners_calendar_is_refused(
        self, app, seed_user, seed_second_user,
    ):
        """The seed owner's statement, handed the second owner's calendar: refused.

        A pay-period window files each opening and true-up on the calendar it
        is handed, so another owner's would file the seed owner's in a paycheck
        that is not theirs.  The statement refuses the pairing, naming both
        owners, whatever the window: a month window here.  It was the read's
        refusal (plan task plan#2771) until finding plan#2875 took the
        calendar off the read.  The CONTROL: handed the owner's own calendar,
        the same statement answers.
        """
        with app.app_context():
            owner_id = seed_user["user"].id
            other_id = seed_second_user["user"].id
            window = StatementWindow("month", month=1, year=2026)

            assert ledger_report_service.compute_income_statement(
                owner_id, calendar_for(owner_id), window,
            ).net_income == Decimal("0.00")
            with pytest.raises(
                ValueError,
                match=f"income statement of user {owner_id} was handed user {other_id}'s",
            ):
                ledger_report_service.compute_income_statement(
                    owner_id, calendar_for(other_id), window,
                )


# ---------------------------------------------------------------------------
# 3. The L9 display-timezone attribution (reader-contract C-3)
# ---------------------------------------------------------------------------


class TestDisplayTimezoneAttribution:
    """A late-evening-Eastern settle attributes to the wall-clock civil day."""

    def test_dec31_evening_et_attributes_to_prior_year(
        self, app, db, seed_user,
    ):
        """An 8:05pm-ET Dec-31 settle lands in that year, not the UTC Jan-1.

        A $500 Groceries expense paid 2026-12-31 8:05pm Eastern (EST,
        UTC-5), the instant 2027-01-01 01:05 UTC.  Its ``settled_on`` is the
        owner's civil day, Dec 31 (the L9 rule), and its lines are dated by
        it, so the 2026 year window includes it (expense 500.00) and the
        2027 window is empty.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("500.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2026, 12, 31),
            )
            db.session.commit()

            in_2026 = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2026),
            )
            assert in_2026.expense.total == Decimal("500.00")
            assert _labels(in_2026.expense.lines) == ["Family: Groceries"]

            in_2027 = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2027),
            )
            assert not in_2027.expense.lines
            assert in_2027.expense.total == Decimal("0.00")
            assert in_2027.net_income == Decimal("0.00")


# ---------------------------------------------------------------------------
# 4. Validation and the empty (no-baseline) statement
# ---------------------------------------------------------------------------


class TestThePeriodLabelCannotNameAnotherOwnersPeriod:
    """A foreign ``period_id`` resolves nothing, by construction.

    **This producer read the period UNSCOPED until plan step C2-f3a**:
    ``db.session.get(PayPeriod, window.period_id)``, which resolves any
    owner's row, and its dates went straight into the window LABEL.  Nothing
    here refused it.  What stood in the way was
    ``analytics._validate_owned_or_abort`` at the route boundary, and that
    route's own comment said so -- "the service reads the period for its
    window LABEL un-scoped, so a foreign ``period_id`` would otherwise leak
    the victim's period dates".  A guard one layer up is a guard the next
    caller has to remember.

    The producer takes the OWNER's calendar now, and a calendar carries one
    owner's periods, so the identity lookup cannot reach across.  **The route
    guard STAYS and is not replaced by this** -- it is what emits the
    ``access_denied_cross_user`` audit event the SOC dashboards read, and an
    empty label is not a refusal.  What changed is that the leak no longer
    depends on the guard being there.
    """

    def test_another_owners_period_id_yields_an_empty_label(
        self, app, db, seed_user, seed_periods, seed_second_user,
        seed_second_periods,
    ):
        """The victim's dates do not reach the attacker's window label."""
        with app.app_context():
            victim_period = seed_second_periods[0]
            attacker = seed_user["user"].id

            # The premise: the id really is another owner's, and really does
            # resolve a row when asked without a scope.
            assert victim_period.user_id != attacker
            assert db.session.get(PayPeriod, victim_period.id) is not None

            report = ledger_report_service.compute_income_statement(
                attacker,
                calendar_for(attacker),
                StatementWindow("pay_period", period_id=victim_period.id),
            )

            assert report.window_label == "", (
                "the foreign period's dates reached the window label"
            )
            for rendered in (
                victim_period.start_date.strftime("%b %d"),
                str(last_covered_day(victim_period).year),
            ):
                assert rendered not in report.window_label

    def test_the_owners_OWN_period_id_still_labels_the_window(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the empty label above is the scoping, not a break.

        Without this, a producer that answered ``""`` for every pay-period
        window would pass the case above and grade nothing.
        """
        with app.app_context():
            own = seed_periods[0]
            report = ledger_report_service.compute_income_statement(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id),
                StatementWindow("pay_period", period_id=own.id),
            )
            assert report.window_label == pay_period_range_label(
                own.start_date, last_covered_day(own),
            )
            assert report.window_label != ""


class TestIncomeStatementValidationAndEmpty:
    """Bad windows raise; a baseline-less user yields an empty statement."""

    def test_invalid_window_type_raises(self, app, seed_user):
        """An unknown window type is a ValueError, not a silent empty report."""
        with app.app_context():
            with pytest.raises(ValueError, match="Invalid window_type"):
                ledger_report_service.compute_income_statement(
                    seed_user["user"].id, calendar_for(seed_user["user"].id),
                    StatementWindow("weekly"),
                )

    def test_pay_period_without_period_id_raises(self, app, seed_user):
        """A pay-period window with no ``period_id`` is under-specified."""
        with app.app_context():
            with pytest.raises(ValueError, match="period_id is required"):
                ledger_report_service.compute_income_statement(
                    seed_user["user"].id, calendar_for(seed_user["user"].id),
                    StatementWindow("pay_period"),
                )

    def test_month_without_month_raises(self, app, seed_user):
        """A month window needs both month and year."""
        with app.app_context():
            with pytest.raises(ValueError, match="month and year"):
                ledger_report_service.compute_income_statement(
                    seed_user["user"].id, calendar_for(seed_user["user"].id),
                    StatementWindow("month", year=2026),
                )

    def test_no_baseline_scenario_refuses_rather_than_zeroing(
        self, app, bare_user_with_cadence,
    ):
        """A baseline-less user gets no statement -- NOT one reporting zeros.

        The balance sheet's twin (plan step X-v2, ruling R-BW).  An all-zero
        income statement reads as "you earned and spent nothing this period",
        which is a claim about the user's money; the truth is that this ledger
        cannot be read at all.  See the balance sheet's own test for the full
        argument and for how the review found both.

        **``bare_user_with_cadence`` rather than ``bare_user`` since plan step
        ``pay_calendar:C4-d``** (ruling R-PC45), and the swap keeps this case
        pointed at its own subject.  ``calendar_for`` refuses an owner with no
        ``budget.pay_schedule`` row, and that argument is evaluated BEFORE the
        call -- so a bare owner would raise ``PayCalendarError`` here and this
        case would report a pass for a refusal that is not the one it is
        about.  The fixture gives the owner a schedule row and no paydays,
        which builds an empty calendar and leaves the missing BASELINE as the
        only thing wrong with them.
        """
        with app.app_context():
            user_id = bare_user_with_cadence["user"].id
            with pytest.raises(BaselineMissingError):
                ledger_report_service.compute_income_statement(
                    user_id,
                    calendar_for(user_id),
                    StatementWindow("year", year=2026),
                )


# ---------------------------------------------------------------------------
# 5. Transfers never touch the income statement
# ---------------------------------------------------------------------------


class TestTransfersNeverInIncomeStatement:
    """A transfer moves assets on the balance sheet, absent from income."""

    def test_transfer_absent_from_income_present_on_balance_sheet(
        self, app, db, seed_user,
    ):
        """A $150 Checking -> Savings transfer: no income lines, assets shift.

        Both a transfer's legs land on linked Asset accounts, so the income
        statement stays empty.  Settled AFTER both accounts' origination (so it
        rides on top of the openings), on the balance sheet the money simply
        moves: Checking 1000 - 150 = 850, Savings 200 + 150 = 350; assets 1200
        == Checking-equity 1000 + Savings-equity 200 = 1200 equity, tie-out
        green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("200.00"),
                observed_on=_OPENED_BEFORE,
            )
            db.session.commit()
            create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
                settled_on=_RIDES_ON_TOP,
            )
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_RIDES_ON_TOP.year),
            )
            assert not income.income.lines
            assert not income.expense.lines
            assert income.net_income == Decimal("0.00")

            sheet = ledger_report_service.compute_balance_sheet(
                user_id, _ALL_ACTIVITY,
            )
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("850.00")
            assert _find_line(
                sheet.assets.lines, "Rainy Day",
            ).amount == Decimal("350.00")
            assert sheet.assets.total == Decimal("1200.00")
            assert sheet.tie_out.in_balance is True
            assert sheet.tie_out.ledger_net == Decimal("0.00")


# ---------------------------------------------------------------------------
# 6. Balance sheet -- position, retained earnings, sign, as-of cutoff
# ---------------------------------------------------------------------------


class TestBalanceSheetPosition:
    """The posted position ties out; income closes into retained earnings."""

    def test_seed_opening_ties_out(self, app, seed_user):
        """A fresh $1000 Checking: assets 1000 == equity (opening) 1000, RE 0.

        The origination opening posts linked +1000 (Asset) and anchor-equity
        -1000 -> presented Equity +1000.  Assets 1000 == Liabilities 0 +
        Equity 1000 (opening 1000 + retained earnings 0); the mechanical
        ledger net is 0.  Both tie-out halves hold, so ``in_balance`` is True.
        """
        with app.app_context():
            sheet = ledger_report_service.compute_balance_sheet(
                seed_user["user"].id, _ALL_ACTIVITY,
            )
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            assert sheet.assets.total == Decimal("1000.00")
            assert not sheet.liabilities.lines
            assert sheet.liabilities.total == Decimal("0.00")
            assert _find_line(
                sheet.equity.lines, "Checking -- Opening",
            ).amount == Decimal("1000.00")
            assert _find_line(
                sheet.equity.lines, "Retained Earnings",
            ).amount == Decimal("0.00")
            assert sheet.equity.total == Decimal("1000.00")
            assert sheet.tie_out.assets == Decimal("1000.00")
            assert sheet.tie_out.liabilities_plus_equity == Decimal("1000.00")
            assert sheet.tie_out.ledger_net == Decimal("0.00")
            assert sheet.tie_out.in_balance is True

    def test_income_and_expense_flow_to_retained_earnings(
        self, app, db, seed_user,
    ):
        """Income 2000, expense 300: Checking 2700, retained earnings 1700.

        Checking linked = 1000 (opening) + 2000 (income cash) - 300 (expense
        cash) = 2700.  The Income + Expense accounts close into retained
        earnings = -((-2000) + 300) = 1700, so Equity = opening 1000 + RE 1700
        = 2700 == assets 2700.  The income/expense accounts themselves never
        appear on the balance sheet.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            create_settled_cash_transaction(
                seed_user, period, Decimal("2000.00"),
                account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
            )
            create_settled_cash_transaction(
                seed_user, period, Decimal("300.00"),
                account=checking,
                category=seed_user["categories"]["Groceries"],
            )
            db.session.commit()

            sheet = ledger_report_service.compute_balance_sheet(
                user_id, _ALL_ACTIVITY,
            )
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("2700.00")
            assert _find_line(
                sheet.equity.lines, "Retained Earnings",
            ).amount == Decimal("1700.00")
            assert sheet.equity.total == Decimal("2700.00")
            assert _labels(sheet.assets.lines) == ["Checking"]  # no I/E lines
            assert sheet.tie_out.in_balance is True

    def test_negatively_anchored_liability_signs_positive(
        self, app, db, seed_user,
    ):
        """A Credit Card anchored -500 presents as a +500 Liability (no -abs).

        The owed-as-negative opening books linked -500 (Liability) and
        anchor-equity +500.  The statement presents the ledger FAITHFULLY: the
        credit-normal Liability line is the negated debit net -> +500 (a
        positive Liabilities line), and the equity opening presents -500.  With
        the seed Checking: assets 1000 == Liabilities 500 + Equity (Checking
        1000 - Card 500 + RE 0) 500; tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            sheet = ledger_report_service.compute_balance_sheet(
                user_id, _ALL_ACTIVITY,
            )
            assert _find_line(
                sheet.liabilities.lines, "Rewards Card",
            ).amount == Decimal("500.00")
            assert sheet.liabilities.total == Decimal("500.00")
            assert _find_line(
                sheet.equity.lines, "Rewards Card -- Opening",
            ).amount == Decimal("-500.00")
            assert sheet.assets.total == Decimal("1000.00")
            assert sheet.equity.total == Decimal("500.00")
            assert sheet.tie_out.liabilities_plus_equity == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True

    def test_as_of_folds_on_the_settle_date(self, app, db, seed_user):
        """The as-of bound includes a settle exactly on its attribution day.

        A $100 Groceries expense riding on top (paid 2099-06-01).  As of the
        day BEFORE (2099-05-31) the settle is excluded whole -- Checking stays
        at its 1000 opening.  As of the settle day itself (2099-06-01) it is
        folded in -- Checking = 1000 - 100 = 900.  The tie-out is green in both,
        because the settle's two legs (cash and expense) attribute to the same
        day and so cross the bound together.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("100.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=_RIDES_ON_TOP,
            )
            db.session.commit()

            before = ledger_report_service.compute_balance_sheet(
                user_id, _RIDES_ON_TOP - timedelta(days=1),
            )
            assert _find_line(
                before.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            assert before.tie_out.in_balance is True

            on_day = ledger_report_service.compute_balance_sheet(
                user_id, _RIDES_ON_TOP,
            )
            assert _find_line(
                on_day.assets.lines, "Checking",
            ).amount == Decimal("900.00")
            assert on_day.tie_out.in_balance is True

    def test_no_baseline_scenario_refuses_rather_than_tying_out(
        self, app, bare_user,
    ):
        """A baseline-less user gets no statement -- NOT a green tie-out.

        **Changed at plan step X-v2** (ruling R-BW), and the answer this
        replaces is why.  It used to return assets ``$0.00``, liabilities
        ``$0.00``, equity ``$0.00`` and ``tie_out.in_balance is True``: the
        application ASSERTING that a user's books balance over a ledger it
        cannot read.  A statement that cannot be produced is not a statement of
        zeros, and "the two sides agree" is the single strongest claim this
        report makes -- making it up is the same defect finding N-113 recorded
        for the net-worth hero, one screen over.

        Found by X-v2's adversarial correctness review, which reached it by
        walking the call graph rather than the predicate's spelling: this
        producer resolves the baseline DIRECTLY, so neither X-v's AST census
        (which followed ``BalanceContext``) nor its route sweep (which graded
        only 5xx) could see it.

        One application-level handler now renders the setup-recovery card for
        this state, so the user is told what is wrong and given the repair.
        """
        with app.app_context():
            with pytest.raises(BaselineMissingError):
                ledger_report_service.compute_balance_sheet(
                    bare_user["user"].id, _ALL_ACTIVITY,
                )


# ---------------------------------------------------------------------------
# 7. Labels from the worked-out chart (ruling plan#2518)
# ---------------------------------------------------------------------------


class TestDisplayLabels:
    """A category line reads its category as it is today, or Uncategorized."""

    def test_category_rename_reflected_live(self, app, db, seed_user):
        """Renaming a live category updates the line label (renames reflect).

        A $100 Groceries expense books on the Groceries category's line.
        Renaming the category's item to "Snacks" makes the income-statement
        line read the LIVE ``category.display_name`` --> "Family: Snacks", not
        the "Family: Groceries" a stored chart row's name snapshot held (the
        worked-out chart keeps no snapshot, ruling plan#2518).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("100.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2026, 3, 15),
            )
            db.session.commit()

            groceries = db.session.get(
                Category, seed_user["categories"]["Groceries"].id,
            )
            groceries.item_name = "Snacks"
            db.session.commit()

            report = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=2026),
            )
            assert _labels(report.expense.lines) == ["Family: Snacks"]
            assert report.expense.total == Decimal("100.00")

    def test_a_deleted_categorys_lines_read_uncategorized(
        self, app, db, seed_user,
    ):
        """A $100.00 row under a category that is then deleted: Uncategorized.

        Ruling plan#2518: the chart is worked out from the records, so a line
        is booked on its row's category as the row holds it today.  A
        category's delete SET-NULLs the row's ``category_id``, so the row's
        line is the owner's Uncategorized Expense bucket's -- where a stored
        chart row kept the deleted category's name snapshot and this test
        once asserted that.

        **The state is STAGED by a raw delete, as the database performs it.**
        The category door never hard-deletes a category any transaction uses
        (``archive_helpers.category_has_usage`` archives it instead), and
        ``transaction_templates.category_id`` is RESTRICT, so the row is
        settled under Groceries' definition and then re-pointed to a category
        of the test's own, which no definition names.  The CONTROL first: the
        line follows the re-pointed category ("Chart: Doomed", $100.00), so
        the Uncategorized line after the delete is the delete's, not a line
        that never read the row's category.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            doomed = Category(
                user_id=user_id, group_name="Chart", item_name="Doomed",
            )
            db.session.add(doomed)
            db.session.flush()
            txn = create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2026, 3, 15),
            )
            txn.category_id = doomed.id
            db.session.commit()
            march = StatementWindow("month", month=3, year=2026)

            control = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), march,
            )
            assert _labels(control.expense.lines) == ["Chart: Doomed"]

            deleted = db.session.execute(sa.text(
                "DELETE FROM budget.categories WHERE id = :cid"
            ), {"cid": doomed.id}).rowcount
            db.session.commit()
            db.session.expire_all()
            assert deleted == 1

            report = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), march,
            )
            assert _labels(report.expense.lines) == ["Uncategorized Expense"]
            assert report.expense.total == Decimal("100.00")
            # The line moved between two Expense keys, so the sheet's
            # retained earnings, and its tie-out, are untouched.
            assert ledger_report_service.compute_balance_sheet(
                user_id, _ALL_ACTIVITY,
            ).tie_out.in_balance is True
