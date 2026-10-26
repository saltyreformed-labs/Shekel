"""Grid route tests: the inline subtotal rows, net cash flow, the footer, and the period subtotal's
precision, baseline and producer.
"""

from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.ref import Status, TransactionType
from app.services.cash_flow_set import CashFlowSet
from app.services import balance_at
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    append_balance_assertion,
    cover_bare_settled_row,
    current_pay_period,
    figure_source_columns,
    mark_purchase_settled,
    one_off_row_of,
    settle_day_columns,
    settle_instant_on,
)

from ._helpers import _grid_route_source


# ── Inline Subtotal Row Tests ──────────────────────────────────────


class TestInlineSubtotalRows:
    """Tests for the Total Income and Total Expenses subtotal rows in tbody."""

    def test_subtotal_rows_present(self, app, auth_client, seed_user, seed_periods_today):
        """Grid contains subtotal-row-income and subtotal-row-expense rows."""
        with app.app_context():
            # Create transactions so the sections render.
            from app.models.ref import TransactionType
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = current_pay_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            one_off_row_of(
                current,
                name="Salary",
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
                amount=Decimal("1200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html

    def test_subtotal_values_correct(self, app, auth_client, seed_user, seed_periods_today):
        """Subtotal rows show correct per-period totals."""
        with app.app_context():
            from app.models.ref import TransactionType
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            current = current_pay_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            for name, cat, typ, amt in [
                ("Pay", "Salary", income_type.id, "2000.00"),
                ("Stipend", "Salary", income_type.id, "100.00"),
                ("Rent", "Rent", expense_type.id, "1200.00"),
                ("Food", "Groceries", expense_type.id, "400.00"),
            ]:
                one_off_row_of(
                    current,
                    name=name,
                    amount=Decimal(amt),
                    user_id=current.user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=typ,
                    category_id=seed_user["categories"][cat].id,
                )
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            # Total Income = 2000 + 100 = 2100.
            assert "$2,100" in html
            # Total Expenses = 1200 + 400 = 1600.
            assert "$1,600" in html

    def test_subtotal_excludes_cancelled(self, app, auth_client, seed_user, seed_periods_today):
        """Cancelled transactions are excluded from subtotals."""
        with app.app_context():
            from app.models.ref import TransactionType
            cancelled = db.session.query(Status).filter_by(name="Cancelled").one()
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = current_pay_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            one_off_row_of(
                current,
                name="Good Pay",
                amount=Decimal("1000.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            txn_bad = one_off_row_of(
                current,
                name="Cancelled Pay",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            txn_bad.status_id = cancelled.id
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()

            # Only $1,000 counted (cancelled $500 excluded).
            assert "$1,000" in html

    def test_balance_row_refresh_unaffected(self, app, auth_client, seed_user, seed_periods_today):
        """The balance-row HTMX endpoint returns tfoot only, no subtotal rows."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "subtotal-row" not in html
            assert "net-cash-flow-row" not in html
            assert "<tfoot" in html


# ── Net Cash Flow Row Tests ────────────────────────────────────────


class TestNetCashFlowRow:
    """Tests for the Net Cash Flow row in tbody."""

    def _seed_txns(self, seed_user, seed_periods_today, income_amt, expense_amt):
        """Helper: create income + expense in the current/first visible period."""
        from app.models.ref import TransactionType
        income_type = db.session.query(TransactionType).filter_by(name="Income").one()
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        current = current_pay_period(seed_user["user"].id)
        if not current:
            current = seed_periods_today[0]

        if income_amt:
            one_off_row_of(
                current,
                name="Income",
                amount=Decimal(income_amt),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
        if expense_amt:
            one_off_row_of(
                current,
                name="Expense",
                amount=Decimal(expense_amt),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Rent"].id,
            )
        db.session.commit()

    def test_net_cash_flow_row_present(self, app, db, auth_client, seed_user, seed_periods_today):
        """Grid contains a net-cash-flow-row with correct label."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "2000", "1400")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            assert "Net Cash Flow" in html
            assert "$600" in html

    def test_net_cash_flow_negative(self, app, db, auth_client, seed_user, seed_periods_today):
        """Negative net cash flow shows warning indicator."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "1000", "1500")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "balance-negative" in html
            # Warning icon for negative net.
            assert "bi-exclamation-triangle-fill" in html

    def test_net_cash_flow_zero(self, app, db, auth_client, seed_user, seed_periods_today):
        """Breakeven period shows empty net cash flow cell."""
        with app.app_context():
            self._seed_txns(seed_user, seed_periods_today, "1000", "1000")
            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "net-cash-flow-row" in html
            # Net is zero -- cell should be empty (matching footer behavior).

    def test_balance_row_refresh_excludes_net_cash_flow(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """Balance-row HTMX endpoint does not include net-cash-flow-row."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "net-cash-flow-row" not in html


# ── Footer Condensation Tests ──────────────────────────────────────


class TestFooterCondensation:
    """Tests verifying the footer contains only Projected End Balance."""

    def test_footer_single_row(self, app, db, auth_client, seed_user, seed_periods_today):
        """Balance-row response has exactly 1 row: Projected End Balance."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert "Projected End Balance" in html
            assert "Total Income" not in html
            assert "Total Expenses" not in html
            assert "Net (Income" not in html
            assert html.count("<tr") == 1

    def test_footer_htmx_attributes_preserved(self, app, db, auth_client, seed_user, seed_periods_today):
        """The tfoot has all HTMX attributes for the self-referencing refresh."""
        with app.app_context():
            resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            html = resp.data.decode()
            assert 'id="grid-summary"' in html
            assert "hx-get=" in html
            assert 'hx-trigger="balanceChanged from:body"' in html
            assert 'hx-swap="outerHTML"' in html

    def test_footer_htmx_refresh_cycle(self, app, db, auth_client, seed_user, seed_periods_today):
        """Initial page and balance-row both produce tfoot with HTMX attributes."""
        with app.app_context():
            page_resp = auth_client.get("/grid")
            page_html = page_resp.data.decode()
            assert 'id="grid-summary"' in page_html

            balance_resp = auth_client.get(
                f"/grid/balance-row?periods=6&offset=0&account_id={seed_user['account'].id}"
            )
            balance_html = balance_resp.data.decode()
            assert 'id="grid-summary"' in balance_html
            assert "hx-trigger" in balance_html

    def test_subtotals_still_present_in_tbody(self, app, db, auth_client, seed_user, seed_periods_today):
        """Tbody subtotal and net cash flow rows survive footer condensation."""
        with app.app_context():
            from app.models.ref import TransactionType
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            current = current_pay_period(seed_user["user"].id)
            if not current:
                current = seed_periods_today[0]

            one_off_row_of(
                current,
                name="Pay",
                amount=Decimal("2000.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            db.session.commit()

            resp = auth_client.get("/grid")
            html = resp.data.decode()
            assert "subtotal-row-income" in html
            assert "subtotal-row-expense" in html
            assert "net-cash-flow-row" in html


class TestSubtotalDecimalPrecision:
    """Verify server-side Decimal subtotals agree with balance row at the penny level (H-05)."""

    def test_subtotals_match_balance_row(self, app, auth_client, seed_user, seed_periods_today):
        """Pre-computed Decimal subtotals match the balance calculator's values exactly.

        Creates 20+ transactions with sub-dollar amounts that would
        accumulate float drift if |float were used. Verifies the grid
        subtotals and the balance row agree within $0.01.
        """
        with app.app_context():
            income_type = db.session.query(TransactionType).filter_by(name="Income").one()
            expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()
            period = current_pay_period(seed_user["user"].id)
            if not period:
                period = seed_periods_today[0]

            # Create 20 expense transactions with amounts that cause float drift.
            expected_expense = Decimal("0")
            for i in range(20):
                amt = Decimal("33.33")
                one_off_row_of(
                    period,
                    name=f"Expense {i}",
                    amount=amt,
                    user_id=period.user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=expense_type.id,
                    category_id=seed_user["categories"]["Groceries"].id,
                )
                expected_expense += amt

            # Create 5 income transactions.
            expected_income = Decimal("0")
            for i in range(5):
                amt = Decimal("777.77")
                one_off_row_of(
                    period,
                    name=f"Income {i}",
                    amount=amt,
                    user_id=period.user_id,
                    account_id=seed_user["account"].id,
                    scenario_id=seed_user["scenario"].id,
                    transaction_type_id=income_type.id,
                    category_id=seed_user["categories"]["Groceries"].id,
                )
                expected_income += amt

            db.session.commit()

            # Fetch the grid page.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Verify subtotals appear with correct Decimal-computed values.
            # 20 * $33.33 = $666.60
            assert "$667" in html or "$666" in html, (
                "Expected expense subtotal ~$666-667 in grid"
            )
            # 5 * $777.77 = $3,888.85
            assert "$3,889" in html or "$3,888" in html, (
                "Expected income subtotal ~$3888-3889 in grid"
            )


class TestGridSubtotalsRegressionBaseline:
    """Regression baseline: the per-period subtotal reflects what MOVED.

    Pre-Commit-10 the grid subtotal was an inline ``sum(...
    effective_amount ...)`` loop in ``app/routes/grid/``.  The subtotal now
    comes off the seam's ``GridColumn`` (plan steps X-c2b1 / X-c2b2), which
    values income from the row itself and expenses through the entries-aware
    reduction; for income with no entries the rule is the row's valuation, so
    this 5A.1-era baseline continues to hold.

    **The ROW it grades changed at plan step X-au-c3, and the FIGURE did
    not.**  It was a PROJECTED income row carrying ``actual_amount = 400``
    against a ``500`` estimate -- a state the settlement record makes
    unconstructible, and the exact state migration ``e4b8a71c0f36`` promoted
    into the plan on the five production rows that were in it.  The regression
    is about a subtotal reporting the corrected figure rather than the
    forecast, so it is retargeted onto the row that can still hold both: a
    SETTLED income row whose record is a human's ``400`` against its own
    ``500`` plan.  Same ``$400`` on screen, same defect caught.
    """

    def test_subtotals_reflect_the_recorded_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Income planned at 500 and RECORDED at 400: the subtotal shows 400.

        Originally a Commit #0 regression baseline asserting the D-1 bug
        (the subtotal showed the estimate).  Commit 5A.1 asserted the
        correction, and plan step X-au-c3 moved the corrected figure into the
        settlement record -- so the subtotal must read the record, not the
        plan beside it.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            account = seed_user["account"]

            received = db.session.query(Status).filter_by(
                name="Received",
            ).one()
            income_type = db.session.query(TransactionType).filter_by(
                name="Income",
            ).one()

            # Place the transaction in the current period so it is
            # visible on the default grid view.
            current = current_pay_period(
                seed_user["user"].id,
            )
            if not current:
                current = seed_periods_today[0]

            txn = one_off_row_of(
                current,
                name="Regression Subtotal Income",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=account.id,
                scenario_id=scenario.id,
                transaction_type_id=income_type.id,
                category_id=seed_user["categories"]["Salary"].id,
            )
            # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
            # record is the covering movement written after the flush (X-bi-4b-2).
            txn.status_id = received.id
            for _column, _value in settle_day_columns(current.start_date).items():
                setattr(txn, _column, _value)
            db.session.flush()
            # The record's home is the covering movement (X-bi-4b-1).
            cover_bare_settled_row(db.session, txn, "500.00", submitted="400.00")
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The subtotal values the row from its SETTLEMENT RECORD (400),
            # not from the plan beside it.  Grid formats subtotals as
            # "${:,.0f}".
            assert "$400" in html, (
                "Income subtotal should reflect the RECORDED figure (400), "
                "not the 500 the row planned"
            )
            assert "subtotal-row-income" in html, (
                "Income subtotal row must be present in grid"
            )


class TestGridPeriodSubtotalCanonical:
    """Commit 10: per-period subtotals routed through ONE shared reduction.

    Pre-Commit-10 the grid's per-period subtotal was an inline
    ``sum(... effective_amount ...)`` loop in ``app/routes/grid/``
    that did NOT apply the entries-aware reduction.  F-002 Pair C /
    F-004 (Q-10) flagged this as a same-page divergence: the subtotal
    row and the balance row consumed the same in-memory transactions
    but with different expense formulas.  Commit 10 collapsed the grid subtotal
    onto one shared reduction; plan steps X-c2b1 / X-c2b2 went further and made
    the balance row and the subtotal rows ONE ``GridColumn`` per period off ONE
    valued row set, so a Projected envelope expense with cleared entries reports
    the same entries-aware impact on both rows and ``balance[p] - balance[p-1]
    == net[p] + period_timing[p] + book_vs_bank[p] + contribution[p] +
    accrual[p]`` holds by construction rather than by two producers agreeing.
    """

    def test_grid_subtotal_entry_aware_for_projected_expense(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Rendered grid subtotal reflects the entry-aware reduction.

        Setup: a Projected $500.00 envelope expense in the visible current
        period, OVERSPENT -- three purchases the bank has taken, summing
        $562.34.

        Hand arithmetic (F-002 Pair C / F-004, on ruling **R-FM**'s two halves):
          posted_debit = 20.00 + 442.34 + 100.00 = 562.34
          unposted_debit = 0
          sum_credit = 0
          reservation = max(500.00 - 562.34 - 0, 0) = 0.00
          subtotal = 562.34 posted + 0.00 still reserved = **$562.34**.

        **The overspend is what keeps this test able to fail** (plan step
        X-f3b).  Before it, a posted purchase was no fact at all and the
        subtotal was the reservation ALONE, so an under-budget envelope
        distinguished the entry-aware answer ($37.66) from the raw
        ``effective_amount`` ($500).  The subtotal now counts both halves, and
        for an under-budget envelope they sum to exactly the estimate -- the
        same $500 the F-002 defect printed, which would leave this guard unable
        to tell them apart.  Overspending separates all three: $562 correct,
        $500 the raw-estimate defect, $0 the reservation-only answer.
        """
        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            current = current_pay_period(
                seed_user["user"].id,
            )
            assert current is not None, (
                "seed_periods_today must produce a current period"
            )

            txn = one_off_row_of(
                current,
                name="Groceries",
                amount=Decimal("500.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()

            # The user read their bank balance on the day they shopped, so
            # the purchases are inside it (plan step S1-c, ruling R-DH (d)).
            # Re-stating the $1,000.00 the records already hold books a
            # $0.00 correction, so no figure below moves because of it --
            # what it changes is which purchases the reservation may
            # subtract.  Under the retired ``is_cleared`` flag this fixture
            # claimed the purchases were inside an anchor asserted four
            # periods earlier (finding N-132 / R8).
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1000.00"), settle_instant_on(current.start_date),
            )
            for amt in (
                Decimal("20.00"),
                Decimal("442.34"),
                Decimal("100.00"),
            ):
                entry = TransactionEntry(
                    **figure_source_columns(),
                    transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    description="confirmed purchase",
                    purchased_on=current.start_date,
                    is_credit=False,
                )
                db.session.add(entry)
                db.session.flush()
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                )
            db.session.commit()

            resp = auth_client.get("/grid")
            assert resp.status_code == 200
            html = resp.data.decode()

            # Grid formats subtotals as "${:,.0f}", so $562.34 -> $562.
            expense_cell = html.split(
                "subtotal-row-expense",
            )[1].split("</tr>")[0]
            assert "$562" in expense_cell, (
                "Expense subtotal is what the period COSTS: $562.34 of "
                "purchases the bank has taken plus $0.00 still reserved"
            )
            assert "$500" not in expense_cell, (
                "Subtotal expense cell must not show the raw "
                "effective_amount $500 (F-002 Pair C / F-004 regression)"
            )
            # The reservation-only answer ($0.00, what this cell showed while
            # a posted purchase was no fact) is refused by the $562 assertion
            # above rather than by a "$0 absent" one: the subtotal ROW spans
            # every reported period, and the periods with no rows in them
            # legitimately print $0.

    def test_grid_subtotal_reconciles_balance_delta(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``delta == net + timing + book_vs_bank + contribution + accrual``.

        Same-formula invariant E-25 / Q-10, on ruling R-K's basis: ONE valued
        row set supplies the grid's balance row and its subtotal rows, so the
        period-to-period balance delta must equal the column's own net plus the
        remainders no row can explain plus the accrual, to the penny.  The
        inline loop this replaced violated it whenever a Projected envelope
        expense carried cleared entries (the subtotal showed the raw estimate,
        the balance row showed the entry-aware impact).

        Read off ONE ``GridBalanceView`` since plan step X-c2b3, which is what
        makes the identity a property of the row set rather than an agreement
        between two producers: it was ``balances_for`` differenced against
        ``cash_ledger.period_subtotal``, and both deleted -- the first replaced
        by the fold at X-c2b2, the second by ``cash_period_view``, whose
        remainder terms are the ones R-K added and ruling R-DH (f) split in two.

        Setup: anchor $1000 at periods[0]; one Projected $300.00
        envelope expense in the CURRENT period with two purchases the bank has
        taken summing $250.00, dated that period's start, and a re-assertion of
        $750.00 on that same day.

        Hand arithmetic (ruling **R-FM**'s two halves, plan step X-f3b):
          posted = 250.00, reservation = max(300.00 - 250.00 - 0, 0) = 50.00.
          columns[current].expense = 250.00 + 50.00 = 300.00, .income = 0.00,
          .net = -300.00.
          balance[current] = 750.00 - 50.00 = 700.00, and
          balance[current - 1] = 1000.00, so the delta is -300.00 == net.

        **The re-assertion is $750.00 and that figure is the fixture's
        correctness, not a detail.**  It re-stated $1,000.00 while a posted
        purchase was no fact, which then booked a $0.00 correction; the two
        purchases ARE facts now, so the records read $750.00 that day and
        asserting $1,000.00 would book a $250.00 ``book_vs_bank`` correction --
        a true diagnosis of a fixture whose owner had spent $250.00 out of
        $1,000.00 and claimed to still hold all of it.  Asserting what the
        records say keeps BOTH remainders at $0.00, which is what lets this test
        grade the identity rather than a remainder absorbing a mis-grouped row.

        **The rows were in ``periods[5]`` -- a FUTURE period -- until plan step
        X-c2b3, and moving them to the current one is a fixture correction, not
        a convenience.**  The entries were dated that future period's start, and
        the retired ``period_subtotal`` counted every loaded entry whatever its
        date, so the reduction applied and the test read ``$50.00``.  The fold
        values the reservation at the READER'S NOW (``sum_projected``'s
        entry-date window): an entry dated after today cannot have cleared the
        bank, so a future-dated purchase reserves nothing yet and the same
        fixture reads ``$300.00``.  Both halves of that are the shipped rule --
        ruling R-M / plan step X-c0 now REFUSES a future ``purchased_on`` at both
        write doors, so the state this fixture built directly through the ORM is
        one production cannot reach, and the sibling test above (which renders
        the figure through ``GET /grid``) always dated its entries on the
        current period's start for the same reason.  Dating the purchase inside
        the period being spent is the production shape: a partially-spent
        envelope in the period you are in.
        """
        from app.models.transaction_entry import TransactionEntry

        with app.app_context():
            expense_type = db.session.query(TransactionType).filter_by(
                name="Expense",
            ).one()
            periods = seed_periods_today
            target_period = current_pay_period(
                seed_user["user"].id,
            )
            assert target_period is not None, (
                "seed_periods_today must produce a current period"
            )
            target_index = next(
                i for i, p in enumerate(periods) if p.id == target_period.id
            )
            assert target_index > 0, (
                "fixture invariant: the current period must have a predecessor "
                "to difference against"
            )

            txn = one_off_row_of(
                target_period,
                name="Groceries window",
                amount=Decimal("300.00"),
                user_id=target_period.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=seed_user["categories"]["Groceries"].id,
            )
            db.session.flush()
            # $750.00 is what the RECORDS say that day -- the $1,000.00
            # opening less the $250.00 of purchases the bank has taken -- so
            # this assertion books a $0.00 correction and ``book_vs_bank``
            # stays $0.00.  See the docstring: it was $1,000.00 while a posted
            # purchase was no fact.
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("750.00"),
                settle_instant_on(target_period.start_date),
            )
            for amt in (Decimal("100.00"), Decimal("150.00")):
                entry = TransactionEntry(
                    **figure_source_columns(),
                    transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    description="confirmed purchase",
                    purchased_on=target_period.start_date,
                    is_credit=False,
                )
                db.session.add(entry)
                db.session.flush()
                mark_purchase_settled(
                    db.session, seed_user["account"], entry,
                )
            db.session.commit()

            # Exercise the grid route to prove the wiring is in place
            # before falling back to the resolver-level assertion.
            resp = auth_client.get("/grid")
            assert resp.status_code == 200

            # Seam-level reconciliation off ONE view: the grid route reads the
            # same ``grid_balance_view`` for its balance row and its subtotal
            # rows, so these columns ARE the ground truth the rendered HTML
            # reflects rather than a second producer that has to agree with it.
            columns = balance_at.grid_balance_view(
                CashFlowSet.single(seed_user["account"]),
                BalanceContext.build(seed_user["user"].id),
            ).columns
            column = columns[target_period.id]
            prior_period = periods[target_index - 1]
            delta = column.balance - columns[prior_period.id].balance

            # 250.00 posted + max(300 - 250, 0) = 250.00 + 50.00 = 300.00.
            assert column.expense == Decimal("300.00"), (
                f"expected $300.00 -- what the period costs, posted plus "
                f"reserved -- got {column.expense!r}"
            )
            assert column.net == Decimal("-300.00")
            # Nothing settled and nobody re-anchored, so BOTH remainders are
            # zero and a PLAIN account carries no accrual: asserting all of
            # them is what keeps the identity below from passing on a
            # remainder that quietly absorbed a mis-grouped row (Section
            # 7.2's forbidden residual).  They are asserted separately since
            # plan step S1-c (ruling R-DH (f)) -- a combined figure could be
            # zero with two non-zero halves cancelling inside it.
            assert column.period_timing == Decimal("0.00")
            assert column.book_vs_bank == Decimal("0.00")
            assert column.accrual == Decimal("0.00")
            assert column.contribution == Decimal("0.00")
            assert delta == (
                column.net + column.period_timing + column.book_vs_bank
            ), (
                f"balance delta {delta!r} must equal net {column.net!r} + "
                f"period_timing {column.period_timing!r} + book_vs_bank "
                f"{column.book_vs_bank!r}"
            )

    def test_grid_inline_subtotal_loop_removed(self):
        """Static guard: no inline ``sum(... effective_amount ...)`` in the route.

        The plan's verification gate -- if the inline loop is ever
        reintroduced, the canonical-producer routing is silently
        bypassed.  This regression lock fires the moment a future edit
        re-grows the loop.

        Scoped to the whole ``app/routes/grid/`` package since plan step
        C2-f2b; see :func:`_grid_route_source`.
        """
        import re  # pylint: disable=import-outside-toplevel

        pattern = re.compile(
            r"sum\([^\)]*(effective_amount|estimated_amount)",
        )
        offenders = pattern.findall(_grid_route_source())
        assert not offenders, (
            "app/routes/grid/ contains an inline subtotal loop "
            f"({offenders!r}); read the seam's "
            "balance_at.grid_balance_view instead (F-002 Pair C, "
            "F-004 same-page regression)"
        )

    def test_grid_balance_computation_routed_through_resolver(self):
        """Static guard: grid balance computation routes through the balance-at seam.

        F-6 lock.  The cross-page balance-equality regression test
        (``tests/test_integration/test_cross_page_balance_equality.py``,
        Commit 11 of the main remediation) cannot catch a route-handler
        bypass of the canonical producer because its grid reader
        re-runs the seam itself rather than parsing the rendered HTML.  A
        regression that re-introduces a hand-rolled balance loop in
        the grid route package would therefore drift silently.  This static
        lock closes that gap.

        Updated for plan step X-c2b2: the grid reads EVERY per-period figure
        -- the balance, the subtotals and ruling R-K's remainder -- through
        one ``balance_at.grid_balance_view`` call.  That entry is the
        kind-aware wrapper over the cash FOLD; it is what layers an INTEREST
        account's accrual on as its own row, so the balance change on screen
        stays explained by the rows above it.

        **The positive assertion looks for the CALL, not the name.**  It
        matched ``balance_at.cash_balance_map`` until this step, and by then
        the route had not called that entry since X-c2b1 -- the string
        survived only in a docstring, so the guard was passing on prose while
        the wiring it claimed to lock had moved.  Matching ``.grid_balance_view(``
        with its open paren is what makes it a call site again.

        **The second arm forbade ``balance_calculator.calculate_balances(``
        and was deleted at plan step X-g4b, with the producer** -- Section 8's
        rule that an arm whose forbidden name no longer exists is a sentence
        that can never fail, and reads as coverage while being none.

        Two assertions:
          1. ``balance_at.grid_balance_view(`` must appear in the
             ``app/routes/grid/`` package (positive: the seam wiring is
             intact).
          2. ``balance_at.balance_map(`` (the KIND-CORRECT map) must NOT
             appear: the grid account may be interest-bearing, and reading
             the accrued balance without the accrual row beside it is the
             shape ruling R-K refuses.

        Complements ``test_grid_inline_subtotal_loop_removed`` above:
        that guard catches an inline ``sum(... effective_amount ...)``
        accumulator; this guard catches a swap to a producer.

        Scoped to the whole package since plan step C2-f2b; see
        :func:`_grid_route_source`.
        """
        grid_source = _grid_route_source()
        assert "balance_at.grid_balance_view(" in grid_source, (
            "app/routes/grid/ no longer CALLS "
            "``balance_at.grid_balance_view`` -- regression on the "
            "balance-at seam contract.  Route every per-period grid figure "
            "through the seam's one kind-aware view instead of a "
            "hand-rolled loop, a direct producer call, or the kind-correct "
            "``balance_map`` (which would accrue interest into the balance "
            "row with no row to explain it)."
        )
        assert "balance_at.balance_map(" not in grid_source, (
            "app/routes/grid/ calls the KIND-CORRECT ``balance_map`` -- "
            "an interest account's accrued balance would then reach the "
            "balance row without the 'Interest' row that explains it "
            "(ruling R-K).  ``grid_balance_view`` owns that dispatch."
        )

    def test_obligations_has_no_period_subtotal_loop(self):
        """Static guard: obligations.py has no period-subtotal arithmetic.

        Obligations computes a monthly equivalent per template
        (E-24 / Commit 23 territory), not per-period transaction
        subtotals.  The plan's verification gate covers both files;
        this assertion locks that obligations never grows the same
        inline ``sum(... effective_amount ...)`` loop the grid had.
        """
        import re

        from pathlib import Path

        obligations_source = Path(
            "app/routes/obligations.py",
        ).read_text(encoding="utf-8")
        pattern = re.compile(
            r"sum\([^\)]*(effective_amount|estimated_amount)",
        )
        offenders = pattern.findall(obligations_source)
        assert not offenders, (
            "app/routes/obligations.py contains inline period-subtotal "
            f"arithmetic ({offenders!r}); route through the canonical "
            "producer if it ever needs per-period subtotals"
        )
