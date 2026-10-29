"""Savings dashboard service tests: the dashboard data, grouping by category, emergency-fund
metrics, and a transfer's historical operand.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from decimal import Decimal

from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.services import savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext


class TestComputeDashboardData:
    """Tests for the top-level compute_dashboard_data orchestrator."""

    def test_returns_expected_keys(self, app, db, seed_user, seed_periods):
        """Return dict contains all template context keys."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            expected_keys = {
                "account_data", "grouped_accounts", "goal_data",
                "emergency_metrics", "total_savings",
                "avg_monthly_expenses", "savings_accounts",
                "archived_accounts", "debt_summary",
                # Loop B Phase 1: the net-worth cockpit region.
                "net_worth",
                # Loop B Phase 2: per-group grid subtotals and the
                # Property equity card data.
                "group_subtotals", "property_equity",
                # Loop B P3 slice 3c: the per-account card sparklines.
                # (The diverging allocation bar split retired with P-AC1:
                # the net-worth stream reads net_worth.series.composition
                # instead.)
                "sparklines",
            }
            assert set(result.keys()) == expected_keys

    def test_empty_user_returns_safe_defaults(self, app, db, seed_user):
        """User with no periods or goals gets safe zero-value defaults.

        The seed user has a Checking account ($1000) which is liquid,
        so total_savings reflects that even without pay periods.
        """
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert result["total_savings"] == Decimal("1000.00")
            assert result["avg_monthly_expenses"] == Decimal("0.00")
            assert result["goal_data"] == []

    def test_checking_account_appears_in_account_data(
        self, app, db, seed_user, seed_periods
    ):
        """The seed user's checking account appears in account_data."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            acct_names = [
                ad.account.name for ad in result["account_data"]
            ]
            assert "Checking" in acct_names

    def test_account_has_current_balance(
        self, app, db, seed_user, seed_periods
    ):
        """Every projection carries a current_balance of the declared type.

        The membership half of this assertion is gone with the dict (plan step
        X-t1): every field of an ``AccountProjection`` exists by construction,
        so "does the key exist" is no longer a question a test can ask or a
        consumer can get wrong.  What remains -- and what the key check never
        covered -- is the TYPE: a ``Decimal`` or the deliberate ``None``.
        """
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            for ad in result["account_data"]:
                assert isinstance(
                    ad.current_balance, (Decimal, type(None))
                )


class TestGroupAccountsByCategory:
    """Tests for the category grouping logic."""

    def test_checking_grouped_as_asset(
        self, app, db, seed_user, seed_periods
    ):
        """Checking accounts are grouped under the 'asset' category."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            grouped = result["grouped_accounts"]
            assert "asset" in grouped
            asset_names = [
                ad.account.name for ad in grouped["asset"]
            ]
            assert "Checking" in asset_names

    def test_savings_account_grouped_as_asset(
        self, app, db, seed_user, seed_periods
    ):
        """Savings accounts are grouped under 'asset'."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Emergency Fund",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(savings)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            grouped = result["grouped_accounts"]
            asset_names = [
                ad.account.name for ad in grouped.get("asset", [])
            ]
            assert "Emergency Fund" in asset_names


class TestEmergencyFundMetrics:
    """Tests for emergency fund coverage computation."""

    def test_total_savings_sums_savings_accounts(
        self, app, db, seed_user, seed_periods
    ):
        """total_savings includes savings + HYSA balances only."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("8000.00"),
                ),
            )
            db.session.add(savings)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            # Both Checking ($1000, liquid) and Savings ($8000, liquid)
            # contribute to total_savings.
            assert result["total_savings"] == Decimal("9000.00")


class TestTheHistoricalOperandReadsATransferAsItsFromLeg:
    """A settled transfer out of checking is one expense: its FROM-side leg.

    The historical operand of the emergency-fund denominator
    (``_metrics._recent_settled_expenses_monthly``) reads a transfer as its
    legs since leaf ``balance:X-bi-6-4a`` (ruling **R-BAL106**): the
    transfers are selected in SQL (out of checking, in the window, in the
    scenario, settled, live) and their legs filtered to the from side by
    ``expense_legs``.  No test put a settled transfer through it (the leaf's
    adversarial review, finding M3), so deleting any of those clauses, or
    the leg arm whole, left the suite green.  This class grades three of the
    clauses (out of checking, the window, the scenario), ``expense_legs`` and
    the leg arm.  **The settled and live clauses stay ungraded here**: every
    transfer in it is settled and live.  A soft-deleted parent's legs are
    dropped with its shadows already (``transfer_legs
    ._covering_movements_query`` reads live shadows only) and valued ``0``
    by ``row_valuation.fixed_contribution`` of their parent.
    """

    def test_a_transfer_out_of_checking_counts_once_and_nothing_else_does(
        self, app, db, seed_user, seed_periods,
    ):
        """Five settled transfers; two count, once each.

        The pass is pinned to the first period's start, so the window is that
        period alone (``len(recent_periods) == 1``), and the owner is paid
        biweekly (26 paychecks a year).  In the window period, baseline
        scenario:

        * ``$200.00`` checking -> savings counts.
        * ``$150.00`` checking -> a SECOND checking account counts ONCE.  Both
          of its legs sit on the checking set, so this is the case that
          grades the SIDE rather than the account: filtering legs by account
          instead of by ``expense_legs`` counts it twice.
        * ``$300.00`` savings -> checking does not count: it arrives in
          checking, it does not leave it.

        Out of scope, each checking -> savings, neither counts:

        * ``$40.00`` in the SECOND period, which the one-period window ends
          before (the window clause).
        * ``$25.00`` in a what-if scenario (the scenario clause).

        Per period: ``200.00 + 150.00 = 350.00``.  Monthly, unquantized, in
        production's own order (``per_period * 26 / 12``):
        ``350.00 * 26 / 12 = 758.333...``.  What each lost clause reads
        instead, per period: no ``expense_legs``, every to-side leg too,
        ``200 + 200 + 150 + 150 = 700``; legs filtered by ACCOUNT,
        ``200 + 150 + 150 = 500``; no leg arm, ``0``; no out-of-checking
        clause, the savings -> checking transfer's from-side leg too,
        ``350 + 300 = 650``; no window clause, ``350 + 40 = 390``; no
        scenario clause, ``350 + 25 = 375``.
        """
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._metrics import (
            _recent_settled_expenses_monthly,
        )
        from tests._test_helpers import (
            create_account_of_type,
            create_settled_transfer,
        )

        with app.app_context():
            checking = seed_user["account"]
            second_checking = create_account_of_type(
                seed_user, db.session, "Checking", "Joint Checking",
                anchor_balance=Decimal("500.00"),
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("5000.00"),
            )
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What if",
                is_baseline=False,
            )
            db.session.add(what_if)
            db.session.flush()
            in_window, after_window = seed_periods[0], seed_periods[1]
            for from_account, to_account, amount, period, scenario in (
                (checking, savings, "200.00", in_window, None),
                (checking, second_checking, "150.00", in_window, None),
                (savings, checking, "300.00", in_window, None),
                (checking, savings, "40.00", after_window, None),
                (checking, savings, "25.00", in_window, what_if),
            ):
                create_settled_transfer(
                    seed_user, db.session, from_account, to_account, period,
                    amount=Decimal(amount), settled_on=period.start_date,
                    scenario=scenario,
                )
            db.session.commit()

            ctx = BalanceContext.build(
                seed_user["user"].id, as_of=in_window.start_date,
            )
            calendar = ctx.calendar()
            monthly = _recent_settled_expenses_monthly(
                [checking.id, second_checking.id],
                ctx.reported_periods(),
                calendar.period_containing(ctx.as_of),
                ctx.scenario_id,
                calendar.cadence,
            )

            assert monthly == Decimal("350.00") * 26 / 12, (
                f"expected $350.00 a period ($200.00 + $150.00, each from-side "
                f"leg once) as 350.00 * 26 / 12 a month, got {monthly}"
            )
