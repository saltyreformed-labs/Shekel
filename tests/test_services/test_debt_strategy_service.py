"""
Tests for the debt strategy service.

All monetary values use Decimal with string constructors.  Expected
values are hand-computed and documented in each test's docstring.
Tests use 0% interest rates where exact values are needed for
deterministic verification, and non-zero rates for relational
property assertions.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import InterestAccrualConventionEnum
from app.utils.money import (
    MONTHS_PER_YEAR,
    accrue_monthly_interest,
    round_money,
)
from app.services.debt_strategy_service import (
    DebtAccount,
    StrategyRequest,
    calculate_strategy,
    STRATEGY_AVALANCHE,
    STRATEGY_CUSTOM,
    STRATEGY_SNOWBALL,
)


FIXED_START = date(2026, 1, 1)


def _debt(account_id, name, principal, rate, minimum):
    """Create a DebtAccount with string-based Decimal construction.

    Convenience helper to reduce boilerplate in tests.  All numeric
    arguments are strings to ensure exact Decimal construction.
    """
    return DebtAccount(
        account_id=account_id,
        name=name,
        current_principal=Decimal(principal),
        interest_rate=Decimal(rate),
        minimum_payment=Decimal(minimum),
        convention=InterestAccrualConventionEnum.MONTHLY_1_12,
    )


def _three_debts():
    """Create the standard 3-debt test set.

    Returns debts with distinct rates and balances so avalanche and
    snowball orderings are unambiguous:

    - Debt 1: $10,000 at 18% (highest rate, largest balance)
    - Debt 2: $5,000 at 6% (lowest rate, middle balance)
    - Debt 3: $3,000 at 12% (middle rate, smallest balance)

    Avalanche order: 1 (18%), 3 (12%), 2 (6%)
    Snowball order: 3 ($3K), 2 ($5K), 1 ($10K)
    """
    return [
        _debt(1, "High Rate", "10000", "0.18", "250"),
        _debt(2, "Low Rate", "5000", "0.06", "150"),
        _debt(3, "Mid Rate", "3000", "0.12", "100"),
    ]


class TestAvalancheStrategy:
    """Tests for the avalanche (highest rate first) strategy."""

    def test_avalanche_highest_rate_first(self):
        """Avalanche targets the highest interest rate debt first.

        With distinct rates (18%, 12%, 6%), avalanche order is:
        Debt 1 (18%) -> Debt 3 (12%) -> Debt 2 (6%).
        The highest rate debt should be paid off first.
        """
        debts = _three_debts()
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        # Per-account results should be in avalanche priority order.
        assert result.per_account[0].account_id == 1  # 18% first
        assert result.per_account[1].account_id == 3  # 12% second
        assert result.per_account[2].account_id == 2  # 6% last

        # Highest rate debt should be paid off first.
        assert result.per_account[0].payoff_month < result.per_account[1].payoff_month
        assert result.per_account[1].payoff_month < result.per_account[2].payoff_month

    def test_avalanche_less_total_interest(self):
        """Avalanche pays less total interest than snowball.

        This is the mathematical guarantee of the avalanche strategy:
        by targeting the highest rate first, less interest accrues
        overall.
        """
        debts = _three_debts()
        avalanche = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))
        snowball = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        assert avalanche.total_interest <= snowball.total_interest

    def test_deterministic_tiebreaker_avalanche(self):
        """When two debts have the same rate, avalanche breaks ties
        by smallest balance first, then by account_id.

        Debts: both at 12%, balances $5,000 and $3,000.
        Expected order: Debt 2 ($3K, smaller) before Debt 1 ($5K).
        """
        debts = [
            _debt(1, "Larger", "5000", "0.12", "200"),
            _debt(2, "Smaller", "3000", "0.12", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("100"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        # Smaller balance breaks the tie.
        assert result.per_account[0].account_id == 2
        assert result.per_account[1].account_id == 1


class TestSnowballStrategy:
    """Tests for the snowball (smallest balance first) strategy."""

    def test_snowball_smallest_balance_first(self):
        """Snowball targets the smallest balance debt first.

        With distinct balances ($3K, $5K, $10K), snowball order is:
        Debt 3 ($3K) -> Debt 2 ($5K) -> Debt 1 ($10K).
        The smallest balance debt should be paid off first.
        """
        debts = _three_debts()
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        # Per-account results should be in snowball priority order.
        assert result.per_account[0].account_id == 3  # $3K first
        assert result.per_account[1].account_id == 2  # $5K second
        assert result.per_account[2].account_id == 1  # $10K last

        # Smallest balance debt should be paid off first.
        assert result.per_account[0].payoff_month < result.per_account[1].payoff_month
        assert result.per_account[1].payoff_month < result.per_account[2].payoff_month

    def test_snowball_earlier_first_payoff(self):
        """Snowball achieves the first payoff earlier than avalanche.

        This is the psychological advantage of snowball: by targeting
        the smallest balance, the user sees a debt eliminated sooner,
        even though total interest is higher.
        """
        debts = _three_debts()
        avalanche = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))
        snowball = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        snowball_first = min(
            a.payoff_month for a in snowball.per_account
        )
        avalanche_first = min(
            a.payoff_month for a in avalanche.per_account
        )

        assert snowball_first <= avalanche_first

    def test_deterministic_tiebreaker_snowball(self):
        """When two debts have the same balance, snowball breaks ties
        by highest interest rate first, then by account_id.

        Debts: both $5,000, rates 12% and 6%.
        Expected order: Debt 1 (12%, higher rate) before Debt 2 (6%).
        """
        debts = [
            _debt(1, "Higher Rate", "5000", "0.12", "200"),
            _debt(2, "Lower Rate", "5000", "0.06", "200"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("100"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        # Higher rate breaks the tie.
        assert result.per_account[0].account_id == 1
        assert result.per_account[1].account_id == 2


class TestCustomStrategy:
    """Tests for the custom (user-specified order) strategy."""

    def test_custom_order(self):
        """Custom strategy follows the user-specified priority order.

        Custom order [2, 3, 1] differs from both avalanche (1, 3, 2)
        and snowball (3, 2, 1).  Debt 2 should be targeted first.
        """
        debts = _three_debts()
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_CUSTOM,
            custom_order=[2, 3, 1],
            start_date=FIXED_START,
        ))

        # Results follow the custom priority order.
        assert result.per_account[0].account_id == 2
        assert result.per_account[1].account_id == 3
        assert result.per_account[2].account_id == 1

        # First debt in custom order should be paid off first.
        assert result.per_account[0].payoff_month < result.per_account[2].payoff_month

    def test_custom_order_missing_account(self):
        """Custom order that omits an active debt's account_id is rejected.

        Debt 3 is missing from the custom_order list.
        """
        debts = _three_debts()
        with pytest.raises(ValueError, match="missing account_ids"):
            calculate_strategy(StrategyRequest(
                debts, Decimal("200"), STRATEGY_CUSTOM,
                custom_order=[1, 2],
                start_date=FIXED_START,
            ))

    def test_custom_order_extra_account(self):
        """Custom order with an account_id not in debts is rejected.

        Account 99 does not exist in the debts list.
        """
        debts = _three_debts()
        with pytest.raises(ValueError, match="not in the active debts"):
            calculate_strategy(StrategyRequest(
                debts, Decimal("200"), STRATEGY_CUSTOM,
                custom_order=[1, 2, 3, 99],
                start_date=FIXED_START,
            ))

    def test_custom_order_duplicates(self):
        """Custom order with duplicate account_ids is rejected."""
        debts = _three_debts()
        with pytest.raises(ValueError, match="duplicate account_ids"):
            calculate_strategy(StrategyRequest(
                debts, Decimal("200"), STRATEGY_CUSTOM,
                custom_order=[1, 2, 2, 3],
                start_date=FIXED_START,
            ))


class TestFreedPaymentCascade:
    """Tests for freed payment rollover and surplus redistribution."""

    def test_freed_payment_rolls_to_next(self):
        """After a debt is paid off, its freed minimum payment increases
        the extra pool for subsequent months.

        Setup (0% rates for exact computation):
        - Debt A: $500, min $200.  Snowball targets A first.
        - Debt B: $1,000, min $100.
        - Extra: $100/month.

        Hand computation:
        Month 1: A = 500 - 200(min) - 100(extra) = $200.
                  B = 1000 - 100(min) = $900.
        Month 2: A = 200 - 200(min) = $0.  A paid off, freed $200.
                  B = 900 - 100(min) - 100(extra) = $700.
                  extra_pool becomes $300 (100 + 200 freed).
        Month 3: B = 700 - 100(min) - 300(extra) = $300.
        Month 4: B = 300 - 100(min) - 200(extra, capped) = $0.

        Without freed payment, B at $200/month takes 5 months from
        start.  With freed payment from month 3 onward, B finishes
        in month 4.
        """
        debts = [
            _debt(1, "Small", "500", "0", "200"),
            _debt(2, "Large", "1000", "0", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("100"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        a_result = result.per_account[0]  # Debt 1 (smallest, targeted first)
        b_result = result.per_account[1]  # Debt 2

        assert a_result.account_id == 1
        assert a_result.payoff_month == 2
        assert b_result.account_id == 2
        assert b_result.payoff_month == 4  # Accelerated by freed payment
        assert result.total_months == 4

        # Verify totals (0% interest).
        assert a_result.total_paid == Decimal("500.00")
        assert b_result.total_paid == Decimal("1000.00")
        assert result.total_interest == Decimal("0.00")

    def test_surplus_redistribution_within_month(self):
        """Surplus from a capped minimum payment is redistributed to
        the next target debt within the same month.

        Setup (0% rates for exact computation):
        - Debt A: $50, min $200.  Snowball targets A first.
        - Debt B: $1,000, min $100.
        - Extra: $300/month.

        Hand computation for month 1:
        Step 2 (minimums):
          A = 50 - min(200, 50) = $0.  surplus = $150.  freed = $200.
          B = 1000 - 100 = $900.
        Step 3 (extra cascade):
          available = 300 + 150(surplus) = $450.
          A at $0, skip.  Apply $450 to B: B = 900 - 450 = $450.
        extra_pool = 300 + 200 = $500 for month 2.

        Without surplus redistribution, B would be $600 after month 1
        (900 - 300 = 600).  With surplus, B is $450 -- the $150
        difference is exactly the surplus from A's capped minimum.
        """
        debts = [
            _debt(1, "Tiny", "50", "0", "200"),
            _debt(2, "Large", "1000", "0", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("300"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        b_result = result.per_account[1]  # Debt 2

        # B's balance after month 1 is $450 (not $600) due to surplus.
        assert b_result.balance_timeline[1] == Decimal("450.00")

        # A paid off in month 1.
        assert result.per_account[0].payoff_month == 1


class TestEdgeCases:
    """Tests for boundary conditions and special inputs."""

    def test_single_debt(self):
        """Single debt: all extra goes to it.

        Debt: $1,000 at 12% (0.12), min $200, extra $300.

        Hand computation:
        Month 1: interest = 1000 * 0.01 = $10.  Balance = $1010.
                  min = $200, balance = $810. extra = $300, balance = $510.
                  paid = $500, interest_total = $10.
        Month 2: interest = 510 * 0.01 = $5.10.  Balance = $515.10.
                  min = $200, balance = $315.10. extra = $300, balance = $15.10.
                  paid = $500, interest_total = $15.10.
        Month 3: interest = 15.10 * 0.01 = $0.15.  Balance = $15.25.
                  min = min(200, 15.25) = $15.25, balance = $0.
                  paid = $15.25, interest_total = $15.25.

        Payoff: month 3.  Total paid: $1015.25.  Total interest: $15.25.
        """
        debt = _debt(1, "Only Debt", "1000", "0.12", "200")
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("300"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        assert len(result.per_account) == 1
        acct = result.per_account[0]
        assert acct.payoff_month == 3
        assert acct.total_interest == Decimal("15.25")
        assert acct.total_paid == Decimal("1015.25")
        assert result.total_months == 3
        assert result.horizon_reached is False

        # Balance timeline: [1000.00, 510.00, 15.10, 0.00]
        assert acct.balance_timeline[0] == Decimal("1000.00")
        assert acct.balance_timeline[1] == Decimal("510.00")
        assert acct.balance_timeline[2] == Decimal("15.10")
        assert acct.balance_timeline[3] == Decimal("0.00")

    def test_zero_extra(self):
        """Extra = 0: freed minimums still cascade as debts are paid off.

        Setup (0% rates for exact computation):
        - C: $500, min $100.  Snowball targets C first.
        - A: $1,000, min $100.
        - B: $2,000, min $100.
        - Extra: $0.

        Hand computation:
        Months 1-5: C pays $100/month.  C payoff: month 5 (500/100).
                     A at month 5: 1000 - 500 = $500.
                     B at month 5: 2000 - 500 = $1500.
        Month 5: C freed, extra_pool = $100.
        Month 6-8: A gets $100 min + $100 extra = $200/month.
                    A payoff: month 8 (500/200 = 2.5 -> 3 months after 5).
        Month 8: A freed, extra_pool = $200.
        Month 9-12: B gets $100 min + $200 extra = $300/month.
                     B at month 8: 2000 - 800 = $1200.
                     Months 9-12: 1200/300 = 4 months.
                     B payoff: month 12.
        """
        debts = [
            _debt(1, "Medium", "1000", "0", "100"),
            _debt(2, "Large", "2000", "0", "100"),
            _debt(3, "Small", "500", "0", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("0"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        # Snowball order: C ($500), A ($1000), B ($2000).
        assert result.per_account[0].account_id == 3  # C first
        assert result.per_account[1].account_id == 1  # A second
        assert result.per_account[2].account_id == 2  # B last

        # C pays off at natural rate (no acceleration).
        assert result.per_account[0].payoff_month == 5

        # A and B are accelerated by freed payments.
        assert result.per_account[1].payoff_month == 8
        assert result.per_account[2].payoff_month == 12
        assert result.total_months == 12

        # 0% interest: total paid equals total principal.
        assert result.total_interest == Decimal("0.00")
        assert result.total_paid == Decimal("3500.00")

    def test_already_paid_off_debt_skipped(self):
        """Debts with zero principal are filtered out before processing.

        Three debts provided, but Debt 2 has $0 principal.  Only
        Debts 1 and 3 should appear in the results.
        """
        debts = [
            _debt(1, "Active High", "5000", "0.18", "200"),
            _debt(2, "Paid Off", "0", "0.12", "100"),
            _debt(3, "Active Low", "3000", "0.06", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        # Only 2 active debts in the result.
        assert len(result.per_account) == 2
        result_ids = {a.account_id for a in result.per_account}
        assert result_ids == {1, 3}

    def test_zero_interest_rate(self):
        """Debt with 0% APR: principal decreases by payment only.

        Debt: $1,000 at 0%, min $200, extra $100.

        Hand computation:
        Month 1: 1000 - 200 - 100 = $700.
        Month 2: 700 - 300 = $400.
        Month 3: 400 - 300 = $100.
        Month 4: 100 - min(200, 100) = $0.  (Extra not needed.)

        Payoff: month 4.  Total paid: $1,000.  Total interest: $0.
        """
        debt = _debt(1, "Zero Rate", "1000", "0", "200")
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("100"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        acct = result.per_account[0]
        assert acct.payoff_month == 4
        assert acct.total_interest == Decimal("0.00")
        assert acct.total_paid == Decimal("1000.00")
        assert acct.balance_timeline == [
            Decimal("1000.00"), Decimal("700.00"),
            Decimal("400.00"), Decimal("100.00"), Decimal("0.00"),
        ]

    def test_horizon_reached(self):
        """When debts cannot be paid off within the horizon, the result
        flags horizon_reached=True.

        Negative amortization: $10,000 at 24%, min $50.  Monthly
        interest = 10000 * 0.02 = $200, which exceeds the $50 minimum.
        Balance grows each month.  max_horizon=12.
        """
        debt = _debt(1, "Underwater", "10000", "0.24", "50")
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("0"), STRATEGY_AVALANCHE,
            start_date=FIXED_START, max_horizon_months=12,
        ))

        assert result.horizon_reached is True
        assert result.total_months == 12

        acct = result.per_account[0]
        # Balance should have grown (negative amortization).
        assert acct.balance_timeline[-1] > Decimal("10000")
        # Timeline has 13 entries: starting balance + 12 months.
        assert len(acct.balance_timeline) == 13
        assert acct.payoff_month == 12

    def test_minimum_payment_exceeds_balance(self):
        """When a debt's balance is less than its minimum payment,
        only the balance amount is paid -- not the full minimum.

        Debt: $50 at 0%, min $200, extra $0.

        Month 1: min = min(200, 50) = $50.  Balance = $0.
        Total paid = $50 (not $200).
        """
        debt = _debt(1, "Almost Done", "50", "0", "200")
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("0"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        acct = result.per_account[0]
        assert acct.payoff_month == 1
        assert acct.total_paid == Decimal("50.00")
        assert acct.total_interest == Decimal("0.00")

    def test_negative_amortization(self):
        """Debt where minimum payment is less than monthly interest.

        $10,000 at 24%, min $50, extra $0, max_horizon=6.
        Monthly interest starts at $200, min is $50.  The balance
        grows each month.  The algorithm must not loop forever.

        Month 1: interest = $200.  Balance = $10200.  min = $50.
                  Balance = $10150.
        Month 2: interest = 10150 * 0.02 = $203.00.  Balance = $10353.
                  min = $50.  Balance = $10303.
        """
        debt = _debt(1, "Growing", "10000", "0.24", "50")
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("0"), STRATEGY_AVALANCHE,
            start_date=FIXED_START, max_horizon_months=6,
        ))

        assert result.horizon_reached is True
        acct = result.per_account[0]

        # Verify specific balances for the first two months.
        assert acct.balance_timeline[0] == Decimal("10000.00")
        assert acct.balance_timeline[1] == Decimal("10150.00")
        assert acct.balance_timeline[2] == Decimal("10303.00")

        # Balance at end is higher than start.
        assert acct.balance_timeline[-1] > acct.balance_timeline[0]


class TestValidation:
    """Tests for input validation."""

    def test_negative_extra_rejected(self):
        """Negative extra_monthly is rejected with ValueError."""
        debt = _debt(1, "Debt", "1000", "0.12", "100")
        with pytest.raises(ValueError, match="extra_monthly must be >= 0"):
            calculate_strategy(StrategyRequest(
                [debt], Decimal("-100"), STRATEGY_AVALANCHE,
                start_date=FIXED_START,
            ))

    def test_empty_debts_list(self):
        """Empty debts list is rejected with ValueError."""
        with pytest.raises(ValueError, match="No active debts"):
            calculate_strategy(StrategyRequest(
                [], Decimal("100"), STRATEGY_AVALANCHE,
                start_date=FIXED_START,
            ))

    def test_all_zero_principal_debts(self):
        """A list where all debts have zero principal is rejected.

        After filtering, no active debts remain.
        """
        debts = [
            _debt(1, "Paid A", "0", "0.12", "100"),
            _debt(2, "Paid B", "0", "0.06", "50"),
        ]
        with pytest.raises(ValueError, match="No active debts"):
            calculate_strategy(StrategyRequest(
                debts, Decimal("100"), STRATEGY_AVALANCHE,
                start_date=FIXED_START,
            ))

    def test_invalid_strategy_rejected(self):
        """An unknown strategy name is rejected with ValueError."""
        debt = _debt(1, "Debt", "1000", "0.12", "100")
        with pytest.raises(ValueError, match="strategy must be one of"):
            calculate_strategy(StrategyRequest(
                [debt], Decimal("100"), "invalid_strategy",
                start_date=FIXED_START,
            ))

    def test_zero_minimum_payment_rejected(self):
        """An active debt with zero minimum payment is rejected.

        A zero minimum means the debt can never be paid off by its
        scheduled payments alone.
        """
        debt = _debt(1, "No Payment", "1000", "0.12", "0")
        with pytest.raises(ValueError, match="minimum_payment must be > 0"):
            calculate_strategy(StrategyRequest(
                [debt], Decimal("100"), STRATEGY_AVALANCHE,
                start_date=FIXED_START,
            ))

    def test_custom_without_order_rejected(self):
        """Custom strategy without custom_order is rejected."""
        debt = _debt(1, "Debt", "1000", "0.12", "100")
        with pytest.raises(ValueError, match="custom_order is required"):
            calculate_strategy(StrategyRequest(
                [debt], Decimal("100"), STRATEGY_CUSTOM,
                start_date=FIXED_START,
            ))

    def test_custom_empty_order_rejected(self):
        """Custom strategy with empty custom_order is rejected."""
        debt = _debt(1, "Debt", "1000", "0.12", "100")
        with pytest.raises(ValueError, match="must not be empty"):
            calculate_strategy(StrategyRequest(
                [debt], Decimal("100"), STRATEGY_CUSTOM,
                custom_order=[],
                start_date=FIXED_START,
            ))

    @pytest.mark.parametrize(
        "not_a_member", [1, None, "monthly_1_12"],
        ids=["a-stored-id", "none", "the-members-name"],
    )
    def test_a_debt_without_a_convention_member_is_refused(self, not_a_member):
        """A debt states how it accrues as the enum member, or is not built.

        The three values a caller could hand over by mistake -- the loan row's
        id, the cache's answer for an unknown id, the row's name -- are each
        refused at construction rather than at the first month's charge.
        """
        with pytest.raises(TypeError, match="convention must be an"):
            DebtAccount(
                account_id=1,
                name="Debt",
                current_principal=Decimal("1000.00"),
                interest_rate=Decimal("0.12"),
                minimum_payment=Decimal("100.00"),
                convention=not_a_member,
            )


class TestResultStructure:
    """Tests for result data types and structural correctness."""

    def test_all_decimal_arithmetic(self):
        """All monetary fields in the result are Decimal, not float.

        Verifies total_interest, total_paid, and every entry in every
        balance_timeline.
        """
        debts = [
            _debt(1, "A", "5000", "0.12", "200"),
            _debt(2, "B", "3000", "0.06", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("100"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        assert isinstance(result.total_interest, Decimal)
        assert isinstance(result.total_paid, Decimal)

        for acct in result.per_account:
            assert isinstance(acct.total_interest, Decimal)
            assert isinstance(acct.total_paid, Decimal)
            for balance in acct.balance_timeline:
                assert isinstance(balance, Decimal), (
                    f"balance_timeline entry is {type(balance).__name__}, "
                    f"not Decimal"
                )

    def test_balance_timeline_length(self):
        """Balance timelines have exactly total_months + 1 entries.

        All accounts share the same timeline length, with debts
        paid off early padded with $0 entries.

        Setup (0% rates):
        - A: $500, min $200.  Snowball first.  Payoff: month 2.
        - B: $1,000, min $100.  Payoff: month 4.
        - Extra: $100.

        Both timelines should have 5 entries (4 months + 1 start).
        A's entries after month 2 should be $0.
        """
        debts = [
            _debt(1, "Small", "500", "0", "200"),
            _debt(2, "Large", "1000", "0", "100"),
        ]
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("100"), STRATEGY_SNOWBALL,
            start_date=FIXED_START,
        ))

        expected_length = result.total_months + 1
        for acct in result.per_account:
            assert len(acct.balance_timeline) == expected_length, (
                f"Account {acct.account_id}: timeline has "
                f"{len(acct.balance_timeline)} entries, expected "
                f"{expected_length}"
            )

        # A (paid off in month 2) should have $0 from month 2 onward.
        a_result = result.per_account[0]
        assert a_result.payoff_month == 2
        for balance in a_result.balance_timeline[2:]:
            assert balance == Decimal("0.00")

    def test_strategy_name_in_result(self):
        """The strategy_name field matches the input strategy string."""
        debt = _debt(1, "Debt", "1000", "0", "100")

        for strategy in (STRATEGY_AVALANCHE, STRATEGY_SNOWBALL):
            result = calculate_strategy(StrategyRequest(
                [debt], Decimal("100"), strategy,
                start_date=FIXED_START,
            ))
            assert result.strategy_name == strategy

        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("100"), STRATEGY_CUSTOM,
            custom_order=[1], start_date=FIXED_START,
        ))
        assert result.strategy_name == STRATEGY_CUSTOM

    def test_start_date_used_for_payoff_dates(self):
        """Payoff dates are computed from the provided start_date.

        Uses the single-debt setup (payoff in 3 months) with a
        non-January start date to verify date arithmetic.

        Start: 2026-07-15.  Payoff month 3 -> 2026-10-15.
        """
        debt = _debt(1, "Debt", "1000", "0.12", "200")
        start = date(2026, 7, 15)
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("300"), STRATEGY_AVALANCHE,
            start_date=start,
        ))

        acct = result.per_account[0]
        assert acct.payoff_month == 3
        assert acct.payoff_date == date(2026, 10, 15)
        assert result.debt_free_date == date(2026, 10, 15)

    def test_total_paid_equals_principal_plus_interest(self):
        """For fully paid-off debts, total_paid = principal + interest.

        This is a mathematical invariant: every dollar paid goes to
        either principal or interest.
        """
        debt = _debt(1, "Debt", "5000", "0.12", "200")
        result = calculate_strategy(StrategyRequest(
            [debt], Decimal("200"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        acct = result.per_account[0]
        assert result.horizon_reached is False
        # total_paid = starting principal + total interest.
        expected_paid = Decimal("5000.00") + acct.total_interest
        assert acct.total_paid == expected_paid

    def test_per_account_totals_match_aggregate(self):
        """Aggregate total_interest and total_paid equal the sum of
        per-account values.
        """
        debts = _three_debts()
        result = calculate_strategy(StrategyRequest(
            debts, Decimal("200"), STRATEGY_AVALANCHE,
            start_date=FIXED_START,
        ))

        expected_interest = sum(
            a.total_interest for a in result.per_account
        )
        expected_paid = sum(
            a.total_paid for a in result.per_account
        )
        assert result.total_interest == expected_interest
        assert result.total_paid == expected_paid


class TestTheAccrualIsTheSharedPrimitive:
    """The simulation charges interest through the ONE monthly-accrual primitive.

    Plan step **X-au-g-2c-3c**.  ``_accrue_interest`` was a FIFTH inline
    spelling of the monthly accrual and it was not the same function as the
    primitive: it associated ``(balance * rate) / 12`` where
    :func:`~app.utils.money.accrue_monthly_interest` associates
    ``balance * (rate / 12)``.  Finding **D52** recorded the duplicate under the
    evidence *"the two orderings agree on 200,000 randomised draws"*; that
    sentence is refuted by the pinned case below.

    The divergent input is pinned rather than swept.  A random sweep that
    happened to miss it would report agreement and read exactly like proof of
    it.
    """

    # Measured 2026-09-02 over 500,000 randomised draws: the ONE input where
    # the two associations part.  (b*r)/12 -> 565.37;  b*(r/12) -> 565.36.
    DIVERGENT_BALANCE = Decimal("271375.20")
    DIVERGENT_RATE = Decimal("0.025")

    def test_the_two_associations_really_do_differ_on_the_pinned_case(self):
        """The premise, asserted rather than assumed.

        Without this, the test below would pass on inputs where the two
        spellings agree and prove nothing about which one is in use.
        """
        retired_spelling = round_money(
            self.DIVERGENT_BALANCE * self.DIVERGENT_RATE / MONTHS_PER_YEAR,
        )
        assert retired_spelling == Decimal("565.37")
        assert accrue_monthly_interest(
            self.DIVERGENT_BALANCE, self.DIVERGENT_RATE,
        ) == Decimal("565.36")
        assert retired_spelling != accrue_monthly_interest(
            self.DIVERGENT_BALANCE, self.DIVERGENT_RATE,
        )

    def test_the_simulation_charges_the_primitives_figure(self):
        """One month on the pinned case yields the primitive's cent.

        A single debt, the smallest legal minimum (the door refuses ``0.00``)
        and no extra, over a one-month horizon: nothing can cascade, and a
        one-cent payment cannot absorb the cent that separates the two
        spellings.
        """
        result = calculate_strategy(StrategyRequest(
            [_debt(1, "Pinned", str(self.DIVERGENT_BALANCE),
                   str(self.DIVERGENT_RATE), "0.01")],
            Decimal("0.00"),
            STRATEGY_AVALANCHE,
            start_date=FIXED_START,
            max_horizon_months=1,
        ))
        assert result.per_account[0].total_interest == Decimal("565.36"), (
            "the simulation charged the RETIRED spelling's figure -- "
            "_accrue_interest is no longer routing through "
            "accrue_monthly_interest"
        )
