"""Grid route tests: the rows named by transaction."""

from decimal import Decimal

from app.extensions import db
from app.models.category import Category
from app.models.ref import AccountType, Status, TransactionType
from app.services import account_service

from tests._test_helpers import (
    current_pay_period,
    derived_span,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
)
from app.models.amount_ownership import AmountOwnership


class TestTransactionNameRows:
    """Tests for Commit #15: transaction-name-based row headers.

    The grid now shows one row per unique (category, template, name) tuple
    instead of one row per category.  These tests verify that the restructure
    produces correct row headers, handles all transaction types, maintains
    deterministic ordering, and preserves subtotals and HTMX interactions.
    """

    def _get_current_period(self, seed_user):
        """Return the current period for the seed user."""
        return current_pay_period(seed_user["user"].id)

    def test_grid_separate_rows_for_same_category_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two templates in the same category produce two distinct grid rows,
        each with the transaction name in the row header.
        """
        with app.app_context():
            current = self._get_current_period(seed_user)

            # Create a second category item under "Auto" group.
            auto_insurance = Category(
                user_id=seed_user["user"].id,
                group_name="Auto",
                item_name="Insurance",
            )
            db.session.add(auto_insurance)
            db.session.flush()

            # Two definitions, same category, each with its own row in the
            # current paycheck (the engine's, plan step balance:X-cf-4).
            tmpl_sf = make_expense_template(
                db.session, seed_user, amount="150.00", name="State Farm",
                category=auto_insurance,
            )
            tmpl_geico = make_expense_template(
                db.session, seed_user, amount="120.00", name="Geico",
                category=auto_insurance,
            )
            generate_row_of(tmpl_sf, current)
            generate_row_of(tmpl_geico, current)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Both names appear as row headers in the expenses section.
            assert "State Farm" in html
            assert "Geico" in html

            # Verify they are in separate <th> elements.
            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "State Farm" in th_labels
            assert "Geico" in th_labels

    def test_grid_one_time_transaction_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A one-off (a rule-less definition's row) produces its own row with
        the transaction name in the row header.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Car Repair",
                amount=Decimal("450.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Car Payment"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "Car Repair" in th_labels

    def test_grid_transfer_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A transfer produces its own grid row with its leg's name in the header.

        It planted the transfer's expense TWIN beside it until plan step
        ``balance:X-bi-6-4d-2`` made a twin unstorable (ruling **R-BAL258**);
        the grid has drawn a transfer's row from the transfer itself since leaf
        ``balance:X-bi-6-1``, so the transfer alone is the subject now
        (re-expressed under ruling **R-BAL167** class 1).
        """
        with app.app_context():
            from app.models.transfer import Transfer

            projected = db.session.query(Status).filter_by(name="Projected").one()
            current = self._get_current_period(seed_user)

            # Create a savings account for the transfer destination.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings_acct)
            db.session.flush()

            # Create the outgoing category.
            out_cat = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers",
                item_name="Outgoing",
            )
            db.session.add(out_cat)
            db.session.flush()

            # Create the transfer from checking, filed under the outgoing
            # category its twin carried.
            transfer = Transfer(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=current.id,
                status_id=projected.id,
                name="To Savings",
                amount_ownership=AmountOwnership.own(Decimal("500.00")),
                from_account_id=seed_user["account"].id,
                to_account_id=savings_acct.id,
                category_id=out_cat.id,
            )
            db.session.add(transfer)
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            # "Transfer to" prefix is stripped -- row shows just "Savings".
            assert "Savings" in th_labels

    def test_grid_empty_cell_has_correct_category_id(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Empty cells pass the correct category_id for quick create,
        matching the row key's category.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a transaction only in the current period so adjacent
            # periods have empty cells for this row key.
            one_off_row_of(
                current,
                name="Electric Bill",
                amount=Decimal("120.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The empty cell's hx-get URL should contain the correct category_id.
            cat_id = seed_user["categories"]["Rent"].id
            assert f"category_id={cat_id}" in html

    def test_grid_group_headers_appear(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Group header rows appear before each category group's transactions
        with the group-header-row CSS class.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create expenses in two different groups.
            one_off_row_of(
                current,
                name="Rent Payment",
                amount=Decimal("1000.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            one_off_row_of(
                current,
                name="Car Loan",
                amount=Decimal("400.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Car Payment"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Group headers with correct class.
            assert 'class="group-header-row"' in html
            # Both groups present.
            assert "Home" in html
            assert "Auto" in html

    def test_grid_inline_edit_works_after_restructure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Inline quick-edit still works: GET returns form, PATCH updates
        the cell, and HX-Trigger fires balanceChanged.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Phone Bill",
                amount=Decimal("80.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # GI-1: GET quick edit form.
            resp = auth_client.get(f"/transactions/{txn.id}/quick-edit")
            assert resp.status_code == 200
            assert b"80" in resp.data

            # GI-2: PATCH updates amount.
            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "estimated_amount": "95.00",
                    "estimated_amount_as_rendered": "80.00",
                },
            )
            assert resp.status_code == 200
            assert b"95" in resp.data
            assert resp.headers.get("HX-Trigger") == "balanceChanged"

    def test_grid_empty_cell_quick_create_works(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GI-9 regression: clicking an empty cell loads the quick-create form."""
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            # Create a transaction so a row key exists with empty cells
            # in adjacent periods.
            one_off_row_of(
                current,
                name="Internet Bill",
                amount=Decimal("60.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            # Extract the quick-create URL from an empty cell.
            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            import re
            # Find quick-create hx-get URLs for the Rent category.
            # The route URL is /transactions/new/quick; HTML encodes & as &amp;.
            cat_id = seed_user["categories"]["Rent"].id
            pattern = rf'hx-get="(/transactions/new/quick\?[^"]*category_id={cat_id}[^"]*)"'
            urls = re.findall(pattern, html)
            assert urls, "No quick-create URL found for the Rent category"

            # Decode HTML entities so the test client can use the URL.
            url = urls[0].replace("&amp;", "&")

            # GET the quick-create form.
            resp = auth_client.get(url)
            assert resp.status_code == 200
            assert b"estimated_amount" in resp.data

    def test_grid_keyboard_nav_classes_correct(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Transaction rows do not have excluded CSS classes; group headers,
        subtotals, and banners do.

        The Income group header is intentionally suppressed under the INCOME
        banner (S12 D3+D4 ruling: a group header is skipped when it would
        merely restate the section banner). So a non-section-named group must
        be present to assert the ``group-header-row`` class renders: the
        expense-side "Home" group (Rent) is not named "expense"/"expenses",
        so its header is not suppressed.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            # Expense in the "Home" group so a real (non-suppressed) group
            # header renders -- the "Income" group header is dropped as
            # redundant with the INCOME banner.
            one_off_row_of(
                current,
                name="Rent",
                amount=Decimal("1500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            html = resp.data.decode()

            # Group header rows have the correct class.
            assert "group-header-row" in html
            # Subtotal rows have correct class.
            assert "subtotal-row" in html
            # Net cash flow row.
            assert "net-cash-flow-row" in html
            # Section banners.
            assert "section-banner-income" in html
            assert "section-banner-expense" in html

    def test_grid_empty_state_no_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Grid renders cleanly with no transactions -- section banners,
        subtotal rows with zeros, and no crash.
        """
        with app.app_context():
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Structure intact.
            assert "INCOME" in html
            assert "EXPENSES" in html
            assert "Total Income" in html
            assert "Total Expenses" in html
            assert "Net Cash Flow" in html
            assert "Projected End Balance" in html

    def test_grid_subtotals_unchanged_after_restructure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Subtotals iterate over all transactions per period, not row keys.
        Total Income shows $2,000, Total Expenses shows $1,500, Net shows $500.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            one_off_row_of(
                current,
                name="Rent",
                amount=Decimal("1000.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            one_off_row_of(
                current,
                name="Groceries",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Subtotals computed from all transactions.
            assert "$2,000" in html   # Total Income
            assert "$1,500" in html   # Total Expenses
            assert "$500" in html     # Net Cash Flow

    def test_grid_payday_workflow_complete(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Full payday workflow still works after the row restructure:
        true-up, mark received, carry forward, mark paid, mark credit.
        Identical to C-0-7 from regression suite.
        """
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            account = seed_user["account"]

            current = self._get_current_period(seed_user)
            past = next(
                p for p in seed_periods_today
                if derived_span(p).period_index == derived_span(current).period_index - 1
            )

            # Setup: past expense, current income + 2 expenses.
            one_off_row_of(
                past,
                name="Past Rent",
                amount=Decimal("150.00"),
                user_id=past.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            income_txn = one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=current.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            exp_done = one_off_row_of(
                current,
                name="Electric Bill",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Car Payment"].id,
            )
            exp_credit = one_off_row_of(
                current,
                name="Groceries",
                amount=Decimal("300.00"),
                user_id=current.user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            # Step 1: True-up.
            resp = auth_client.patch(
                f"/accounts/{account.id}/true-up",
                data={"anchor_balance": "5000.00"},
            )
            assert resp.status_code == 200

            # Step 2: Mark income received.
            resp = auth_client.post(f"/transactions/{income_txn.id}/mark-done")
            assert resp.status_code == 200

            # Step 3: Carry forward.
            resp = auth_client.post(f"/pay-periods/{past.id}/carry-forward")
            assert resp.status_code == 200

            # Step 4: Mark expense paid.
            resp = auth_client.post(f"/transactions/{exp_done.id}/mark-done")
            assert resp.status_code == 200

            # Step 5: Mark expense credit.
            resp = auth_client.post(f"/transactions/{exp_credit.id}/mark-credit")
            assert resp.status_code == 200

            # Verify balances.
            resp = auth_client.get(
                f"/grid/balance-row?periods=2&offset=0"
                f"&account_id={account.id}"
            )
            assert resp.status_code == 200
            # Hand-computed under the fold (ruling R-DH (a)): the true-up
            # asserts $5,000.00 and the paycheck (+$2,000.00) and electric bill
            # (-$500.00) are recorded on that SAME civil day, so the assertion
            # -- the day's closing balance -- already contains them.  The
            # carried-forward $150.00 rent is still projected, so ruling R-G
            # lands it tomorrow, inside the current period.
            # 5000 - 150 = $4,850.00, then the next period's $300.00 grocery
            # envelope = $4,550.00.
            #
            # These figures moved twice: $4,850 / $4,550 before plan step
            # X-c2b2's cutover, $6,350 / $6,050 at it, and back at ruling R-DH
            # (2026-07-31).  The cutover read "the row was recorded after the
            # anchor" as "the money moved after the anchor", but the settle was
            # stamped at the CLICK and the assertion carried no date at all, so
            # on the real workflow -- true up, then tick off what already
            # cleared -- that inference is backwards.  It rendered the
            # developer's own grid at -$4,021.37 against a true -$19.95.
            # Finding cash D1 survives for the case that is actually about
            # money: a settle dated a LATER DAY than the assertion still rides
            # on top (pinned in ``test_cash_walk``).  See
            # ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``.
            assert b"$4,850" in resp.data
            assert b"$4,550" in resp.data

    def test_grid_row_ordering_is_deterministic(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Row ordering is deterministic -- two requests produce identical
        row label sequences.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = self._get_current_period(seed_user)

            # Create multiple transactions across categories.
            for name, category_key, type_id, amount in [
                ("Paycheck", "Salary", income_type.id, "2000.00"),
                ("Rent", "Rent", expense_type.id, "1000.00"),
                ("Groceries", "Groceries", expense_type.id, "200.00"),
                ("Car Loan", "Car Payment", expense_type.id, "400.00"),
            ]:
                one_off_row_of(
                    current,
                    name=name,
                    amount=Decimal(amount),
                    user_id=current.user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=type_id,
                    category_id=seed_user["categories"][category_key].id,
                )
            db.session.commit()

            import re

            def extract_row_labels(html_str):
                """Extract row-label <th> text in order."""
                return re.findall(
                    r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                    html_str,
                )

            resp1 = auth_client.get("/grid?periods=3")
            labels1 = extract_row_labels(resp1.data.decode())

            resp2 = auth_client.get("/grid?periods=3")
            labels2 = extract_row_labels(resp2.data.decode())

            assert labels1 == labels2
            assert len(labels1) >= 4  # At least 4 transaction rows.

    def test_grid_credit_payback_gets_own_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """CC Payback transactions generated by the credit workflow appear
        in their own row with 'CC Payback: ...' in the row header.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Restaurant",
                amount=Decimal("75.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            # Mark as credit -- generates payback in next period.
            resp = auth_client.post(f"/transactions/{txn.id}/mark-credit")
            assert resp.status_code == 200

            # GET the grid showing the next period where the payback lives.
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            # "CC Payback:" prefix is stripped -- row shows original name.
            # The original transaction was "Restaurant", so the payback
            # row should show "Restaurant" (under Credit Card: Payback group).
            assert "Restaurant" in th_labels, (
                f"Expected 'Restaurant' row header for payback, got: {th_labels}"
            )

    def test_grid_cancelled_transaction_handling(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancelled transactions are excluded from the grid -- they do not
        generate row keys and do not appear as cells.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = self._get_current_period(seed_user)

            txn = one_off_row_of(
                current,
                name="Cancelled Item",
                amount=Decimal("50.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.commit()

            # Cancel it.
            resp = auth_client.post(f"/transactions/{txn.id}/cancel")
            assert resp.status_code == 200

            # GET the grid.
            resp = auth_client.get("/grid?periods=3")
            assert resp.status_code == 200
            html = resp.data.decode()

            import re
            th_labels = re.findall(
                r'<th[^>]*class="[^"]*row-label[^"]*"[^>]*>\s*(\S[^<]*?)\s*</th>',
                html,
            )
            assert "Cancelled Item" not in th_labels
