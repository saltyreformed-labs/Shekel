"""
Shekel Budget App -- Dashboard Entry Progress Tests (OP-1, re-pointed)

The dashboard's entry-progress figures ("$spent / $budget") for tracked
(entry-capable) transactions, exercised through the rebuilt Terminal Road
dashboard.

After the Loop B rebuild the dashboard no longer renders an "Upcoming
Bills" card, and the separate "Due Soon" list was likewise REMOVED by
developer ruling (``docs/design/dashboard_card_audit.md`` "Rebuild
decisions" anatomy item 3, locked 2026-06-12: per-bill rows live on the
grid).  The STREET band in ``_pulse.html`` is now the only per-bill
surface: a dated tracked row renders its dual ``$spent / $budget`` figure
in its street event label, and an undated tracked row renders it on the
"anytime this period" shelf -- both via the SAME money macro the retired
``_bill_row.html`` used.  These tests are re-pointed at that surface (the
sanctioned rule-5 exception): they drive ``GET /dashboard`` (and the
``balanceChanged`` swap target ``GET /dashboard/pulse``) and assert the
dual-amount figure in the rendered HTML.  Because the helpers below set
``due_date=period.start_date``, every tracked row is dated and lands on
the street axis.

The over-budget ``text-danger`` styling and the remaining/count title
tooltip belonged to the retired ``_bill_row.html`` and have no equivalent
in the STREET markup; the tests that asserted them are re-pointed at the
figure that DOES survive (the dual amount), preserving their intent that
the entry figures reach the dashboard response.

The service-level entry-field computation (``txn_to_bill_dict`` /
``_entry_progress_fields``) is covered by
``tests/test_services/test_dashboard_service.py::TestBillRowSingleBase``
and
``tests/test_services/test_dashboard_pulse/test_the_street_and_due_soon.py::TestPulseDueSoon``;
the retired ``compute_dashboard_data`` service tests that lived here were
removed with that producer (a sanctioned removal, not test-gaming).
"""

from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_entry import TransactionEntry

from tests._test_helpers import (
    current_pay_period,
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
)


# -- Helpers ---------------------------------------------------------


def _current_period_for(user_id, seed_periods_today):
    """Return the current period for the user.

    seed_periods_today guarantees that today falls in period 4, so the
    containment lookup always answers a real period.
    """
    # pylint: disable=unused-argument
    return current_pay_period(user_id)


def _create_tracked_txn_in_period(
    seed_user, period, name="Groceries",
    estimated=Decimal("500.00"),
):
    """Create a tracked (is_envelope) projected expense in the period.

    Builds a priced envelope definition, then its engine-generated row
    (:func:`generate_row_of`, plan step balance:X-cf-4) -- an every-paycheck
    row is due on the period start, so it lands as a dated event on the
    current period's STREET band.  Returns ``(Transaction, TransactionTemplate)``.
    """
    template = make_expense_template(
        db.session, seed_user, amount=estimated, name=name,
        category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, period)
    return txn, template


def _create_plain_txn_in_period(
    seed_user, period, name="Rent", estimated=Decimal("1200.00"),
):
    """Create a non-tracked one-off projected expense in the period.

    A rule-less definition's placed row whose ``is_envelope`` the producer
    leaves False -- the row renders with the standard single-amount display
    (no progress indicator).
    """
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

    category_key = name if name in seed_user["categories"] else "Rent"

    txn = one_off_row_of(
        period,
        name=name,
        amount=estimated,
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"][category_key].id,
        due_date=period.start_date,
    )
    db.session.flush()
    return txn


def _add_entry(
    txn, seed_user, amount, is_credit=False, description="Purchase",
):
    """Add a purchase entry to a transaction (dated inside the period)."""
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        purchased_on=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


# -- Route-level: progress display through the pulse STREET band -----


class TestDashboardEntryProgressDisplay:
    """The dashboard STREET band shows entry progress for tracked rows.

    Re-pointed off the removed Due Soon list to the STREET (audit "Rebuild
    decisions" anatomy item 3); the dual ``$spent / $budget`` figure
    renders in the street event label.
    """

    def test_tracked_with_entries_shows_progress(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tracked + projected + entries: the row shows '$X / $Y' format.

        Arithmetic: 150 + 80 debit + 100 credit = 330 total on 500 budget.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("150.00"))
            _add_entry(txn, seed_user, Decimal("80.00"))
            _add_entry(txn, seed_user, Decimal("100.00"), is_credit=True)
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert "$330.00 / $500.00" in resp.data.decode()

    def test_tracked_with_no_entries_shows_standard(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tracked txn with no entries: standard amount (no progress)."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            _create_tracked_txn_in_period(
                seed_user, period, name="Groceries",
                estimated=Decimal("500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Groceries" in html
            assert "$500.00" in html
            # No slash progress format when entries are absent.
            assert " / $500.00" not in html

    def test_non_tracked_shows_standard_regression(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Non-tracked txn: standard amount unchanged (regression)."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            _create_plain_txn_in_period(
                seed_user, period, name="Rent",
                estimated=Decimal("1200.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Rent" in html
            assert "$1,200.00" in html
            assert " / $1,200.00" not in html

    def test_tracked_over_budget_shows_over_budget_dual_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tracked txn over budget: street event shows the over-budget figure.

        Arithmetic: 300 + 250 = 550 spent on 500 budget, 50 over.

        Re-pointed under the audit's "Rebuild decisions" anatomy item 3:
        the ``text-danger fw-semibold`` over-budget styling belonged to the
        retired ``_bill_row.html`` and has no equivalent in the STREET
        markup, so that assertion is dropped.  The intent that survives --
        the over-budget figure reaches the dashboard response -- is asserted
        via the dual amount ``$550.00 / $500.00`` (spent above budget).
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("300.00"))
            _add_entry(txn, seed_user, Decimal("250.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Spent ($550) exceeds budget ($500): the dual amount discloses
            # the over-budget state on the street event.
            assert "$550.00 / $500.00" in html

    def test_tracked_under_budget_shows_under_budget_dual_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tracked txn under budget: street event shows spent below budget.

        Arithmetic: 200 spent on 500 budget, 300 remaining (under budget).

        Re-pointed under the audit's "Rebuild decisions" anatomy item 3:
        the ``text-danger fw-semibold`` styling (and its absence) belonged
        to the retired ``_bill_row.html``; the STREET draws no under/over
        budget styling distinction, so the surviving intent -- the
        under-budget figure reaches the response -- is asserted via the
        dual amount ``$200.00 / $500.00`` (spent below budget).
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Spent ($200) is below budget ($500).
            assert "$200.00 / $500.00" in html

    def test_tracked_credit_only_entries_show_progress(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Tracked txn with only credit entries: progress uses the credit sum."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("75.00"), is_credit=True)
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert "$75.00 / $500.00" in resp.data.decode()

    def test_multiple_entries_sum_into_dual_amount(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Multiple entries sum into the street event's dual amount.

        Arithmetic: 125 + 75 = 200 spent on 500 budget.

        Re-pointed under the audit's "Rebuild decisions" anatomy item 3:
        the remaining/count ``title`` tooltip ("$300.00 remaining",
        "2 entries") belonged to the retired ``_bill_row.html`` and has no
        equivalent in the STREET markup.  The surviving intent -- the
        summed entry figure reaches the dashboard response -- is asserted
        via the dual amount ``$200.00 / $500.00`` (the two entries summed).
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("125.00"))
            _add_entry(txn, seed_user, Decimal("75.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # 125 + 75 = 200 spent, rendered against the $500 budget.
            assert "$200.00 / $500.00" in html


# -- Regression: dashboard renders mixed rows + HTMX refresh ----------


class TestDashboardRegressionWithEntries:
    """The dashboard renders entry-capable rows without regression."""

    def test_dashboard_loads_no_tracked_transactions(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Dashboard loads when only non-tracked transactions exist.

        Re-pointed off the removed "Due Soon" header (audit "Rebuild
        decisions" anatomy item 3) to the street head; the dated plain bill
        renders as a street event.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            _create_plain_txn_in_period(seed_user, period, name="Rent")
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Rent" in resp.data
            assert b"This period, day by day" in resp.data

    def test_dashboard_loads_tracked_with_no_entries(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Dashboard loads when a tracked txn exists but has no entries."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            assert b"Groceries" in resp.data
            html = resp.data.decode()
            assert "$500.00" in html
            assert " / $500.00" not in html

    def test_dashboard_mixed_tracked_and_plain_bills(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Dashboard renders tracked and plain rows side-by-side correctly."""
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            tracked, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(tracked, seed_user, Decimal("200.00"))
            _create_plain_txn_in_period(
                seed_user, period, name="Rent",
                estimated=Decimal("1200.00"),
            )
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Tracked bill shows the progress format.
            assert "$200.00 / $500.00" in html
            # Plain bill shows the standard format, no progress.
            assert "Rent" in html
            assert "$1,200.00" in html
            assert " / $1,200.00" not in html

    def test_pulse_refresh_shows_progress(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The balanceChanged swap target re-renders progress for tracked rows.

        ``GET /dashboard/pulse`` (HX-Request) is the pulse region swap
        target; its STREET band must render the tracked row's dual amount.
        Re-pointed off the removed Due Soon list (audit "Rebuild decisions"
        anatomy item 3).
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("125.50"))
            db.session.commit()

            resp = auth_client.get(
                "/dashboard/pulse", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            assert "$125.50 / $500.00" in resp.data.decode()

    def test_dashboard_hero_present_with_tracked_entries(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The hero balance still renders when tracked entries exist.

        Ensures the entry pipeline did not break the hero figure.  Does not
        assert a specific balance (balance correctness with entries is
        covered by test_balance_calculator_entries.py); confirms the hero
        control and its label render.
        """
        with app.app_context():
            period = _current_period_for(seed_user["user"].id, seed_periods_today)
            txn, _ = _create_tracked_txn_in_period(
                seed_user, period, estimated=Decimal("500.00"),
            )
            _add_entry(txn, seed_user, Decimal("200.00"))
            db.session.commit()

            resp = auth_client.get("/dashboard")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "End of this period" in html
            assert 'id="balance-display"' in html
