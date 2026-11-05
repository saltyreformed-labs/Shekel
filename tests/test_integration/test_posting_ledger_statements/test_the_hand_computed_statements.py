"""The rich fixture's statements and a loan's interest and escrow, by hand."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.pay_period import PayPeriod
from app.services import ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for

from tests._ledger_reads import account_net
from tests._test_helpers import (
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
)

from ._helpers import (
    _Y,
    _FIXTURE_OPENING,
    _assert_ledger_self_consistent,
    _find_line,
    _labels,
    _reader_bs,
    _assert_reader_matches_independent,
)


# ---------------------------------------------------------------------------
# 1. A rich hand-computed income statement + balance sheet
# ---------------------------------------------------------------------------


class TestRichFixtureStatements:
    """A multi-account fixture: every line hand-computed and independently tied."""

    def test_income_statement_and_balance_sheet_hand_computed(
        self, app, db, seed_user,
    ):
        """Categories, a fallback, a liability charge, and a transfer, tied out.

        On the seeded Checking ($1000.00 opening) plus a Rewards Card anchored
        -$500.00 (owed-as-negative Liability) and a Rainy Day Savings anchored
        $200.00, all settles pinned into year 2099 (noon UTC):

          - income  "Income: Salary"     $3000.00 -> Checking   (2099-03-10)
          - expense "Family: Groceries"  $ 400.00 -> Checking   (2099-03-15)
          - expense Uncategorized (none) $  50.00 -> Checking   (2099-03-20)
          - expense "Family: Groceries"  $ 120.00 -> Rewards Card (2099-03-16)
          - transfer                     $ 150.00  Checking -> Rainy Day (2099-04-01)

        INCOME STATEMENT (year 2099):
          income   "Income: Salary"                       3000.00
          expense  "Family: Groceries"  400 + 120 =        520.00
          expense  "Uncategorized Expense"                  50.00
          net income = 3000 - (520 + 50) =                2430.00
        Transfers never appear (both legs land on linked Asset accounts).

        BALANCE SHEET (full position):
          Assets      Checking 1000 + 3000 - 400 - 50 - 150 =   3400.00
                      Rainy Day 200 + 150 =                       350.00   -> 3750.00
          Liabilities Rewards Card -(-500 - 120) =                620.00   ->  620.00
          Equity      Checking -- Opening                        1000.00
                      Rainy Day -- Opening                         200.00
                      Rewards Card -- Opening                     -500.00
                      Retained Earnings (= net income)            2430.00   -> 3130.00
          A = L + E: 3750 == 620 + 3130.  Tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
                observed_on=_FIXTURE_OPENING,
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("200.00"),
                observed_on=_FIXTURE_OPENING,
            )
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("3000.00"), account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 3, 10),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("400.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("50.00"), account=checking, category=None,
                settled_on=date(_Y, 3, 20),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("120.00"), account=card,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 16),
            )
            create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
                settled_on=date(_Y, 4, 1),
            )
            db.session.commit()

            # --- Income statement (year 2099), hand-computed line by line.
            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert income.window_label == "2099"
            assert _labels(income.income.lines) == ["Income: Salary"]
            assert _find_line(
                income.income.lines, "Income: Salary",
            ).amount == Decimal("3000.00")
            assert income.income.total == Decimal("3000.00")
            assert _labels(income.expense.lines) == [
                "Family: Groceries", "Uncategorized Expense",
            ]
            assert _find_line(
                income.expense.lines, "Family: Groceries",
            ).amount == Decimal("520.00")
            assert _find_line(
                income.expense.lines, "Uncategorized Expense",
            ).amount == Decimal("50.00")
            assert income.expense.total == Decimal("570.00")
            assert income.net_income == Decimal("2430.00")

            # --- Balance sheet (full position), hand-computed line by line.
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("3400.00")
            assert _find_line(
                sheet.assets.lines, "Rainy Day",
            ).amount == Decimal("350.00")
            assert sheet.assets.total == Decimal("3750.00")
            assert _find_line(
                sheet.liabilities.lines, "Rewards Card",
            ).amount == Decimal("620.00")
            assert sheet.liabilities.total == Decimal("620.00")
            assert _find_line(
                sheet.equity.lines, "Checking -- Opening",
            ).amount == Decimal("1000.00")
            assert _find_line(
                sheet.equity.lines, "Rainy Day -- Opening",
            ).amount == Decimal("200.00")
            assert _find_line(
                sheet.equity.lines, "Rewards Card -- Opening",
            ).amount == Decimal("-500.00")
            assert _find_line(
                sheet.equity.lines, "Retained Earnings",
            ).amount == Decimal("2430.00")
            assert sheet.equity.total == Decimal("3130.00")
            assert sheet.tie_out.in_balance is True

            # --- Independent second opinion + whole-ledger self-consistency.
            _assert_reader_matches_independent(scenario_id, sheet)
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 2. A loan: interest + escrow reach the income statement; cash does not
# ---------------------------------------------------------------------------


class TestLoanInterestEscrowInStatements:
    """A loan payment's interest/escrow are Expense lines; its cash never is."""

    def test_loan_split_reaches_income_statement_and_balance_sheet(
        self, app, db, seed_user,
    ):
        """A $1000 Mortgage payment splits to Interest 500 / Escrow 100 / principal 400.

        A Mortgage originated at $250,000 @ 6% (2099-01-01), trued up to
        $100,000 (2099-01-10), carrying a $1,200/yr escrow component.  A single
        $1,000.00 Checking -> Mortgage payment settled in Feb 2099 splits (the
        running balance is the $100,000 anchor):

          interest = round(100000 * 0.06/12) = round(100000 * 0.005) =  500.00
          escrow   = round(1200 / 12)                                =  100.00
          principal = 1000 - 500 - 100                               =  400.00   (refund 0)

        A $2000.00 salary lands on Checking first (2099-02-05) so it stays
        nonzero after the payment.

        INCOME STATEMENT (year 2099):
          income   "Income: Salary"                 2000.00
          expense  "Mortgage -- Interest"            500.00
          expense  "Mortgage -- Escrow"              100.00
          net income = 2000 - (500 + 100) =         1400.00
        The transfer's two cash legs (Checking -1000, Mortgage linked +1000)
        land on Asset/Liability accounts, so they never touch the statement.

        BALANCE SHEET (full position):
          Assets      Checking 1000 + 2000 - 1000 =            2000.00  -> 2000.00
          Liabilities Mortgage -(cash 1000 - open 250000
                        + trueup 150000 - split 600) = -(-99600) = 99600.00 -> 99600.00
          Equity      Checking -- Opening                      1000.00
                      Mortgage -- Opening (250000 - 150000)  -100000.00
                      Retained Earnings (= net income)         1400.00  -> -97600.00
          A = L + E: 2000 == 99600 + (-97600).  Tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                # In the payment's own year, the month before its 2099-02-01
                # installment: every contractual installment from origination
                # is charged since plan step recurrence:R16-c-2, so the
                # 2025-01-01 origination and 2026-01-10 true-up it carried
                # until then left seventy-three years unpaid ahead of the
                # payment (ruling R-R103).
                anchor_date=date(_Y, 1, 10),
                rate=Decimal("0.06000"),
                origination_date=date(_Y, 1, 1),
                name="Mortgage", term=360,
                escrow_annual=Decimal("1200.00"),
            )
            pay_period = PayPeriod(user_id=user_id, start_date=date(_Y, 2, 1))
            db.session.add(pay_period)
            db.session.flush()

            create_settled_cash_transaction(
                seed_user, db.session, pay_period, Decimal("2000.00"),
                account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 2, 5),
            )
            create_settled_transfer(
                seed_user, db.session, checking, loan, pay_period,
                amount=Decimal("1000.00"), settled_on=date(_Y, 2, 10),
            )
            db.session.commit()

            # --- Income statement: interest + escrow present, cash absent.
            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert _labels(income.income.lines) == ["Income: Salary"]
            assert income.income.total == Decimal("2000.00")
            assert _labels(income.expense.lines) == [
                "Mortgage -- Escrow", "Mortgage -- Interest",
            ]
            assert _find_line(
                income.expense.lines, "Mortgage -- Interest",
            ).amount == Decimal("500.00")
            assert _find_line(
                income.expense.lines, "Mortgage -- Escrow",
            ).amount == Decimal("100.00")
            assert income.expense.total == Decimal("600.00")
            assert income.net_income == Decimal("1400.00")
            # No transfer / linked cash line ever leaks into the statement.
            for label in ("Checking", "Mortgage"):
                assert label not in _labels(income.income.lines)
                assert label not in _labels(income.expense.lines)

            # --- Balance sheet: the loan liability signs positive.
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("2000.00")
            assert _find_line(
                sheet.liabilities.lines, "Mortgage",
            ).amount == Decimal("99600.00")
            assert _find_line(
                sheet.equity.lines, "Mortgage -- Opening",
            ).amount == Decimal("-100000.00")
            assert _find_line(
                sheet.equity.lines, "Retained Earnings",
            ).amount == Decimal("1400.00")
            assert sheet.tie_out.in_balance is True

            # The production loan helper agrees on the linked liability net.
            assert account_net(
                db.session, loan.id, scenario_id,
            ) == Decimal("-99600.00")

            _assert_reader_matches_independent(scenario_id, sheet)
            _assert_ledger_self_consistent()
