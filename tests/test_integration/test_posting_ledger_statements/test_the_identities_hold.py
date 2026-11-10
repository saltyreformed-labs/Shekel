"""The accounting identity, articulation, and the period and calendar windows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.pay_period import PayPeriod
from app.services import ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for

from tests._test_helpers import create_account_of_type, create_settled_cash_transaction

from ._helpers import (
    _Y,
    _FIXTURE_OPENING,
    _noon,
    _windowed_equity_corrections,
    _assert_ledger_self_consistent,
    _true_up_at,
    _find_line,
    _labels,
    _reader_bs,
    _assert_reader_matches_independent,
)


# ---------------------------------------------------------------------------
# 3. A = L + E at multiple as-of dates (incl. inside an anchor period)
# ---------------------------------------------------------------------------


class TestAccountingIdentityAtMultipleAsOf:
    """The identity and tie-out hold at every as-of, including mid-anchor-period."""

    def test_identity_holds_before_and_after_a_true_up(
        self, app, db, seed_user,
    ):
        """A Savings true-up between two settles ties out at five as-of dates.

        Savings anchored $500.00, every event on its own noon-UTC civil day so
        an entry-date fold reconstructs each as-of ledger:

          - $200.00 Groceries expense paid 2099-06-05
          - true-up asserting $350.00 at 2099-06-10  (ledger_before = 500 - 200
            = 300, so the true-up delta is +50.00)
          - $100.00 Groceries expense paid 2099-06-15

        Savings walks 500 -> 300 -> 350 -> 250 across the as-of ladder, with
        THREE as-of dates placed EXACTLY on an event day so the balance sheet's
        inclusive ``<= as_of`` fold is pinned (a ``< as_of`` mutant mis-states
        Savings by that day's whole entry).  One as-of falls inside the anchor
        period BEFORE the assertion (2099-06-05, the first spend) and one AFTER
        it (2099-06-12) -- the balance-sheet case of the civil-day partition.  With
        the seeded Checking ($1000.00):

          2099-06-01  A = 1000 + 500 = 1500  (only openings folded)
          2099-06-05  A = 1000 + 300 = 1300  (EXACT first-spend day)
          2099-06-10  A = 1000 + 350 = 1350  (EXACT true-up day, re-based)
          2099-06-12  A = 1000 + 350 = 1350  (after the assertion)
          2099-06-15  A = 1000 + 250 = 1250  (EXACT second-spend day)

        At each, ``A == L + E`` and both tie-out halves hold, cross-checked
        against the independent entry-date re-derivation.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Ladder Savings",
                anchor_balance=Decimal("500.00"),
                observed_on=_FIXTURE_OPENING,
            )
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("200.00"), account=savings,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 6, 5),
            )
            _true_up_at(savings, "350.00", _noon(_Y, 6, 10))
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"), account=savings,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 6, 15),
            )
            db.session.commit()

            expected_savings = {
                date(_Y, 6, 1): Decimal("500.00"),
                date(_Y, 6, 5): Decimal("300.00"),   # exact first-spend day
                date(_Y, 6, 10): Decimal("350.00"),  # exact true-up day
                date(_Y, 6, 12): Decimal("350.00"),  # after the assertion
                date(_Y, 6, 15): Decimal("250.00"),  # exact second-spend day
            }
            for as_of, savings_balance in expected_savings.items():
                sheet = _reader_bs(user_id, as_of)
                assert _find_line(
                    sheet.assets.lines, "Ladder Savings",
                ).amount == savings_balance, as_of
                assert _find_line(
                    sheet.assets.lines, "Checking",
                ).amount == Decimal("1000.00"), as_of
                # The identity and both tie-out halves hold at this as-of.
                assert sheet.tie_out.assets == (
                    sheet.tie_out.liabilities_plus_equity
                ), as_of
                assert sheet.tie_out.ledger_net == Decimal("0.00"), as_of
                assert sheet.tie_out.in_balance is True, as_of
                # Independent entry-date fold agrees (valid for noon-UTC dates).
                _assert_reader_matches_independent(
                    scenario_id, sheet, through=as_of,
                )

            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 4. Articulation: income + windowed equity corrections == equity delta
# ---------------------------------------------------------------------------


class TestArticulation:
    """The statements articulate through the shared attribution core."""

    def test_income_plus_equity_corrections_equals_equity_delta(
        self, app, db, seed_user,
    ):
        """A year's income net plus its equity corrections is the equity delta.

        On the seeded Checking ($1000.00 opening, dated in a prior year), all in
        year 2099:

          - income  "Income: Salary"    $800.00  paid 2099-05-01
          - expense "Family: Groceries" $300.00  paid 2099-05-02
          - true-up asserting $1600.00 at 2099-05-03  (ledger_before =
            1000 + 800 - 300 = 1500, so the equity correction delta is +100.00)

        net income (2099)                 = 800 - 300              =  500.00
        windowed equity corrections(2099) = -(true-up equity -100) =  100.00

        Bounding sheets:
          equity total as of 2098-12-31 (only the prior-year opening) = 1000.00
          equity total as of 2099-12-31 (opening 1000 + true-up 100
              + retained earnings 500)                               = 1600.00
          equity delta = 1600 - 1000 = 600.00 = 500 (income) + 100 (corrections)

        The identity relates three INDEPENDENTLY-obtained quantities (two reader
        equity totals, one reader net income, one raw-posting equity-correction
        sum), so it fails if the income statement and balance sheet attribute
        inconsistently -- the whole point of the shared read core.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("800.00"), account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 5, 1),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("300.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 5, 2),
            )
            _true_up_at(checking, "1600.00", _noon(_Y, 5, 3))
            db.session.commit()

            start_sheet = _reader_bs(user_id, date(_Y - 1, 12, 31))
            end_sheet = _reader_bs(user_id, date(_Y, 12, 31))
            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )

            equity_delta = end_sheet.equity.total - start_sheet.equity.total
            corrections = _windowed_equity_corrections(
                scenario_id, date(_Y, 1, 1), date(_Y, 12, 31),
            )
            # Hand-computed anchors for the three quantities.
            assert income.net_income == Decimal("500.00")
            assert corrections == Decimal("100.00")
            assert equity_delta == Decimal("600.00")
            # The articulation identity.
            assert equity_delta == income.net_income + corrections

            # Both bounding sheets tie out.
            assert start_sheet.tie_out.in_balance is True
            assert end_sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 5. Period-vs-calendar agreement on an aligned fixture
# ---------------------------------------------------------------------------


class TestPeriodVsCalendarAgreement:
    """When a period's settles all fall in its calendar month, the two agree."""

    def test_aligned_period_and_month_windows_match(self, app, db, seed_user):
        """A pay period wholly inside March 2099 gives the same income statement.

        A period spanning 2099-03-02..2099-03-15, with both its settles paid
        inside it (noon UTC, so their display-timezone paid date is that same
        March day):

          - income  "Income: Salary"    $500.00  paid 2099-03-05
          - expense "Family: Groceries" $200.00  paid 2099-03-10

        The pay-period window (filtering ``pay_period_id``) and the March-2099
        calendar window (filtering the attribution date) see exactly this set,
        so their income statements are identical line for line: income 500.00,
        expense 200.00, net 300.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = PayPeriod(user_id=user_id, start_date=date(_Y, 3, 2))
            db.session.add(period)
            db.session.flush()

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("500.00"),
                account=seed_user["account"], is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 3, 5),
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("200.00"),
                account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 10),
            )
            db.session.commit()

            by_period = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("pay_period", period_id=period.id),
            )
            by_month = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )

            assert _labels(by_period.income.lines) == _labels(
                by_month.income.lines,
            ) == ["Income: Salary"]
            assert _labels(by_period.expense.lines) == _labels(
                by_month.expense.lines,
            ) == ["Family: Groceries"]
            assert by_period.income.total == by_month.income.total == Decimal(
                "500.00",
            )
            assert by_period.expense.total == by_month.expense.total == Decimal(
                "200.00",
            )
            assert by_period.net_income == by_month.net_income == Decimal(
                "300.00",
            )
            _assert_ledger_self_consistent()
