"""The labels, scenario and owner isolation, and a tie-out that can fail."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.services import ledger_report_service
from app.services.ledger_lines import linked_key, worked_out_lines
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for
import sqlalchemy as sa

from tests._test_helpers import (
    capture_sql_statements,
    create_account_of_type,
    create_settled_cash_transaction,
)

from ._helpers import (
    _checking_at_its_opening,
    _Y,
    _assert_ledger_self_consistent,
    _find_line,
    _labels,
    _reader_bs,
    _assert_reader_matches_independent,
)


# ---------------------------------------------------------------------------
# 8. Labels from the worked-out chart (ruling plan#2518)
# ---------------------------------------------------------------------------


class TestDisplayLabels:
    """Every label is today's name: a category, an account, its equity twin.

    A deleted category's lines, which read Uncategorized, are graded once, in
    the service suite (``test_ledger_report_service``'s ``TestDisplayLabels``).
    """

    def test_a_rename_relabels_the_category_and_the_equity_twin(
        self, app, db, seed_user,
    ):
        """A category and an account renamed: every line reads the new names.

        A $100.00 Groceries expense books on the "Family: Groceries" category
        line; renaming the category's item to "Snacks" makes the income line
        read "Family: Snacks".  Renaming the Checking ACCOUNT to "Primary
        Checking" relabels both its lines: its own Asset line and its equity
        twin, "Primary Checking -- Opening".

        **The twin kept its snapshot until plan task plan#2771**, and this test
        asserted that it did: the stored chart row was minted as
        ``"Checking -- Opening"`` and the statement read the row.  Ruling
        plan#2518 works the chart out from the records ("Statements show
        current names"), so the twin's label is the live account's name with
        its component, the one difference that ruling chose.  The figures are
        unmoved: Checking $1,000.00 - $100.00 = $900.00, its opening
        $1,000.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("100.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            db.session.commit()

            groceries = db.session.get(
                Category, seed_user["categories"]["Groceries"].id,
            )
            groceries.item_name = "Snacks"
            # Re-fetch into the current session -- the fixture reference is
            # attached to the fixture-setup session and would not persist.
            db.session.get(Account, checking.id).name = "Primary Checking"
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )
            assert _labels(income.expense.lines) == ["Family: Snacks"]

            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Primary Checking",
            ).amount == Decimal("900.00")
            assert _find_line(
                sheet.equity.lines, "Primary Checking -- Opening",
            ).amount == Decimal("1000.00")
            assert "Checking -- Opening" not in _labels(sheet.equity.lines)
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 9. Scenario and owner isolation
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Statements read the baseline only and never bleed across owners."""

    def test_non_baseline_scenario_never_appears(self, app, db, seed_user):
        """A what-if expense is invisible to the baseline statements.

        A $70.00 Groceries expense settled in a NON-baseline what-if scenario
        posts to that scenario only.  The baseline income statement shows no
        Groceries line and the baseline balance sheet's Checking stays on its
        $1000.00 opening -- the readers scope to ``get_baseline_scenario``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("70.00"), account=seed_user["account"], scenario=whatif,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 9, 1),
            )
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert not income.expense.lines
            _checking_at_its_opening(user_id)
            _assert_ledger_self_consistent()

    def test_owners_do_not_see_each_other(
        self, app, db, seed_user, seed_second_user,
    ):
        """Neither owner's statements show the other's accounts or sources.

        Owner 1 (seeded Checking $1000.00) settles a $60.00 expense; owner 2
        (seeded Checking $2000.00) settles an $80.00 expense.  Each owner's
        balance sheet lists only their own Checking (1000 - 60 = 940.00 and
        2000 - 80 = 1920.00) and neither income statement sees the other's
        expense.
        """
        with app.app_context():
            user1 = seed_user["user"].id
            user2 = seed_second_user["user"].id
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("60.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 1),
            )
            create_settled_cash_transaction(
                seed_second_user,
                seed_second_user["bootstrap_period"], Decimal("80.00"),
                account=seed_second_user["account"],
                category=seed_second_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 2),
            )
            db.session.commit()

            sheet1 = _reader_bs(user1)
            sheet2 = _reader_bs(user2)
            assert _labels(sheet1.assets.lines) == ["Checking"]
            assert _find_line(
                sheet1.assets.lines, "Checking",
            ).amount == Decimal("940.00")
            assert _labels(sheet2.assets.lines) == ["Checking"]
            assert _find_line(
                sheet2.assets.lines, "Checking",
            ).amount == Decimal("1920.00")

            income1 = ledger_report_service.compute_income_statement(
                user1, calendar_for(user1), StatementWindow("year", year=_Y),
            )
            income2 = ledger_report_service.compute_income_statement(
                user2, calendar_for(user2), StatementWindow("year", year=_Y),
            )
            assert income1.expense.total == Decimal("60.00")
            assert income2.expense.total == Decimal("80.00")
            _assert_reader_matches_independent(
                seed_user["scenario"].id, sheet1,
            )
            _assert_reader_matches_independent(
                seed_second_user["scenario"].id, sheet2,
            )
            _assert_ledger_self_consistent()

    def test_a_line_under_another_owners_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """A purchase moved under the other owner's category refuses, unlabelled.

        No key ties a transaction's category to its owner, so the row can exist; it is
        moved there by SQL after its settle, a shape left from the stored ledger's
        writer, which refused to book one until plan task plan#3133.  The labels
        are loaded under this owner alone, so the read finds no record for the
        line, where a load by id would label it with the other's name.
        """
        with app.app_context():
            theirs_id = seed_second_user["categories"]["Rent"].id
            purchase = create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("60.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 1),
            )
            db.session.execute(
                sa.text(
                    "UPDATE budget.transactions SET category_id = :theirs WHERE id = :id"
                ),
                {"theirs": theirs_id, "id": purchase.id},
            )
            db.session.commit()
            db.session.expire_all()

            with pytest.raises(ValueError, match=f"the Category id={theirs_id}"):
                _reader_bs(seed_user["user"].id)


class TestTheLabelsReadOneStatementPerTable:
    """The count does not grow with the accounts and categories the lines name."""

    def test_more_accounts_and_categories_read_in_the_same_statements(
        self, app, db, seed_user,
    ):
        """One account and category, then three of each more: one count, one load.

        Each read starts from an expired session, so it loads what it labels itself.
        The second sheet carries six more lines than the first, so both measured a
        label load that grew, and each loads the categories in one statement.
        """
        owner_id = seed_user["user"].id

        def _read():
            """Return an expired session's sheet, and its statements on the categories."""
            db.session.commit()
            db.session.expire_all()
            sheet, statements = capture_sql_statements(lambda: _reader_bs(owner_id))
            return sheet, statements, [
                text for text, _params in statements if "FROM budget.categories" in text
            ]

        def _settle(item):
            """Settle a purchase on the seeded Checking under the category *item*."""
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("10.00"), account=seed_user["account"],
                category=seed_user["categories"][item], settled_on=date(_Y, 10, 1),
            )

        with app.app_context():
            _settle("Groceries")
            few, few_statements, few_categories = _read()
            for number, item in enumerate(("Rent", "Car Payment", "Payback")):
                _settle(item)
                create_account_of_type(
                    seed_user, db.session, "Savings", f"Counted {number}",
                    anchor_balance=Decimal("100.00"), observed_on=date(_Y, 1, 1),
                )
            many, many_statements, many_categories = _read()

            assert len(many.assets.lines) == len(few.assets.lines) + 3
            assert len(many.equity.lines) == len(few.equity.lines) + 3
            assert len(few_categories) == len(many_categories) == 1
            assert len(many_statements) == len(few_statements)


# ---------------------------------------------------------------------------
# 10. Adversarial: the tie-out is not vacuous
# ---------------------------------------------------------------------------


class TestTieOutIsNotVacuous:
    """An unbalanced line turns the tie-out red."""

    def test_an_unbalanced_line_flips_in_balance_false(
        self, app, db, seed_user, monkeypatch,
    ):
        """One +$50.00 line with no counter leg: ledger net $50.00, tie-out red.

        A balanced book has a green tie-out (ledger_net 0.00, in_balance
        True).  The statements read the worked-out ledger (plan task
        plan#2771), whose every booking balances by its rule, so the tie-out's
        double-entry half is a check of those rules.  Planting one unmatched
        +$50.00 line on Checking into the read's answer (the read patched
        where the report calls it) makes the ledger net $50.00: Assets
        $1,000.00 + $50.00 = $1,050.00 against Liabilities + Equity
        $1,000.00, so ``in_balance`` flips to False -- the tie-out is a real
        check, not one the rules' construction makes vacuously true.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = db.session.get(Account, seed_user["account"].id)
            assert _reader_bs(user_id).tie_out.in_balance is True

            def planted(owner_id, scenario_id):
                """The read's lines, and one unmatched +$50.00 on Checking."""
                lines = worked_out_lines(owner_id, scenario_id)
                opening = next(
                    line for line in lines if line.key == linked_key(checking)
                )
                return [*lines, replace(opening, amount=Decimal("50.00"))]

            monkeypatch.setattr(
                "app.services.ledger_report_service._attribution"
                ".worked_out_lines",
                planted,
            )

            tampered = _reader_bs(user_id)
            assert tampered.tie_out.ledger_net == Decimal("50.00")
            assert tampered.tie_out.assets == Decimal("1050.00")
            assert tampered.tie_out.liabilities_plus_equity == Decimal("1000.00")
            assert tampered.tie_out.in_balance is False
