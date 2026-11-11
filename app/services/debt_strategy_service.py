"""
Shekel Budget App -- Debt Strategy Service

Pure-function service for cross-account debt payoff strategy calculations.
Implements three strategies for allocating extra payments across multiple
debts:

  1. Avalanche -- targets the highest interest rate first.  Minimizes
     total interest paid (mathematically optimal).
  2. Snowball -- targets the smallest balance first.  Provides faster
     initial payoffs for psychological motivation.
  3. Custom -- targets debts in a user-specified priority order.

The service receives debt data as DebtAccount instances and returns a
StrategyResult with per-account payoff timelines and aggregate metrics.
No database access, no Flask imports -- this is a pure function service
that the route layer calls with pre-fetched data.

ARM limitation (R-5): This service uses a fixed interest rate per debt.
ARM rate adjustments during the payoff period are not incorporated into
the strategy projection.  The current rate from LoanParams is used as-is.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.enums import InterestAccrualConventionEnum
from app.services.installment_calendar import accrual_window
from app.utils.dates import add_months
from app.utils.money import installment_interest, round_money

# Constants for Decimal arithmetic -- avoids constructing these per call.
ZERO = Decimal("0.00")
DEFAULT_MAX_HORIZON_MONTHS = 600

# Strategy name constants -- used by the route layer for form values.
STRATEGY_AVALANCHE = "avalanche"
STRATEGY_SNOWBALL = "snowball"
STRATEGY_CUSTOM = "custom"
_VALID_STRATEGIES = frozenset(
    {STRATEGY_AVALANCHE, STRATEGY_SNOWBALL, STRATEGY_CUSTOM}
)


@dataclass(frozen=True)
class DebtAccount:
    """A single debt account's parameters for strategy calculation.

    Represents one installment loan (mortgage, auto, student, personal,
    HELOC) with its current balance and payment terms.  The interest_rate
    follows the LoanParams convention: a decimal where 0.065 means 6.5%.

    Attributes:
        account_id: Unique identifier for the account.
        name: Display name (e.g., "Mortgage", "Auto Loan").
        current_principal: Current outstanding balance.  Must be >= 0.
        interest_rate: Annual interest rate as a decimal (0.065 = 6.5%).
            Must be >= 0.  Matches LoanParams.interest_rate storage
            convention (Numeric(7,5)).
        minimum_payment: Standard monthly P&I payment.  Must be >= 0.
            Active debts (principal > 0) require minimum_payment > 0,
            enforced by _validate_inputs before the algorithm runs.
        convention: How the debt's interest accrues
            (:class:`~app.enums.InterestAccrualConventionEnum`), the loan's own
            (:attr:`app.services.balance_at.LoanTerms.convention`, plan step
            recurrence:R16-d).  No default.
    """

    account_id: int
    name: str
    current_principal: Decimal
    interest_rate: Decimal
    minimum_payment: Decimal
    convention: InterestAccrualConventionEnum

    def __post_init__(self):
        """Validate field types and constraints at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the strategy loop.

        Raises:
            TypeError: If a field has the wrong type.
            ValueError: If a numeric field violates its constraint.
        """
        if not isinstance(self.account_id, int):
            raise TypeError(
                f"account_id must be an int, "
                f"got {type(self.account_id).__name__}"
            )
        if not isinstance(self.name, str):
            raise TypeError(
                f"name must be a str, "
                f"got {type(self.name).__name__}"
            )
        if not isinstance(self.current_principal, Decimal):
            raise TypeError(
                f"current_principal must be a Decimal, "
                f"got {type(self.current_principal).__name__}"
            )
        if not isinstance(self.interest_rate, Decimal):
            raise TypeError(
                f"interest_rate must be a Decimal, "
                f"got {type(self.interest_rate).__name__}"
            )
        if not isinstance(self.minimum_payment, Decimal):
            raise TypeError(
                f"minimum_payment must be a Decimal, "
                f"got {type(self.minimum_payment).__name__}"
            )
        if not isinstance(self.convention, InterestAccrualConventionEnum):
            raise TypeError(
                f"convention must be an InterestAccrualConventionEnum member, "
                f"got {self.convention!r}"
            )
        if self.current_principal < 0:
            raise ValueError(
                f"current_principal must be >= 0, "
                f"got {self.current_principal}"
            )
        if self.interest_rate < 0:
            raise ValueError(
                f"interest_rate must be >= 0, "
                f"got {self.interest_rate}"
            )
        if self.minimum_payment < 0:
            raise ValueError(
                f"minimum_payment must be >= 0, "
                f"got {self.minimum_payment}"
            )


@dataclass(frozen=True)
class AccountPayoff:
    """Per-account payoff results from a strategy calculation.

    Attributes:
        account_id: Account identifier.
        name: Display name.
        payoff_month: Month number (1-indexed) when this debt reaches
            zero.  Set to max_horizon_months if the debt is not paid
            off within the simulation horizon.
        payoff_date: Calendar date of payoff.
        total_interest: Total interest paid on this debt during the
            strategy period.
        total_paid: Total amount paid (principal + interest) on this
            debt.  Useful for the comparison table in 5.11-2.
        balance_timeline: Monthly ending balance for charting.  Index 0
            is the starting balance, index 1 is the balance after
            month 1, etc.  Padded with zero entries through the end
            of the strategy period so all accounts share the same
            timeline length.
    """

    account_id: int
    name: str
    payoff_month: int
    payoff_date: date
    total_interest: Decimal
    total_paid: Decimal
    balance_timeline: list[Decimal]


@dataclass(frozen=True)
class StrategyResult:
    """Aggregate results from a debt payoff strategy calculation.

    Attributes:
        per_account: Per-account results in strategy priority order.
        total_interest: Sum of all per-account total_interest.
        total_paid: Sum of all per-account total_paid.
        debt_free_date: Date when the last debt reaches zero.  When
            horizon_reached is True, this is start_date plus the
            horizon -- the user is NOT actually debt-free.
        total_months: Number of months until all debts are paid off,
            or the horizon value if not all debts are paid off.
        strategy_name: 'avalanche', 'snowball', or 'custom'.
        horizon_reached: True if the max horizon was reached before all
            debts were paid off.  Flags the result as potentially
            incomplete.
    """

    per_account: list[AccountPayoff]
    total_interest: Decimal
    total_paid: Decimal
    debt_free_date: date
    total_months: int
    strategy_name: str
    horizon_reached: bool


@dataclass(frozen=True)
class StrategyRequest:
    """Input parameters for a single debt payoff strategy calculation.

    Bundles the inputs to :func:`calculate_strategy` into one cohesive
    object.  Field order matches the natural call order: the required
    debt data and algorithm selection first, then the optional
    configuration (custom order and the simulation envelope).

    Attributes:
        debts: List of DebtAccount instances.  Debts with
            current_principal <= 0 are filtered out automatically.
        extra_monthly: Additional monthly payment toward debt reduction.
            Must be >= 0.  Zero means minimum payments only (freed
            minimums still cascade as debts are paid off).
        strategy: One of 'avalanche', 'snowball', 'custom'.
        custom_order: For custom strategy, account_ids in priority
            order.  Must contain exactly the same IDs as the active
            debts.  Ignored for avalanche and snowball.
        start_date: The date from which payoff dates are calculated.
            Resolved to date.today() by calculate_strategy when None.
            Pass a fixed date in tests for determinism.
        max_horizon_months: Maximum months to simulate before stopping.
            Default 600 (50 years).  Configurable for testing.
    """

    debts: list[DebtAccount]
    extra_monthly: Decimal
    strategy: str
    custom_order: list[int] | None = None
    start_date: date | None = None
    max_horizon_months: int = DEFAULT_MAX_HORIZON_MONTHS


def _validate_inputs(
    active_debts: list[DebtAccount],
    extra_monthly: Decimal,
    strategy: str,
    custom_order: list[int] | None,
) -> None:
    """Validate all preconditions before running the strategy algorithm.

    Checks business rules that __post_init__ cannot enforce: cross-field
    constraints, custom order validity, and extra_monthly sign.

    Args:
        active_debts: Debts with current_principal > 0 (already filtered).
        extra_monthly: Additional monthly payment toward debt reduction.
        strategy: One of 'avalanche', 'snowball', 'custom'.
        custom_order: For custom strategy, account_ids in priority order.

    Raises:
        TypeError: If extra_monthly is not a Decimal.
        ValueError: With a specific, descriptive message for each
            business rule violation.
    """
    if not active_debts:
        raise ValueError(
            "No active debts to process -- all debts have zero or "
            "negative principal, or the debts list is empty."
        )

    if not isinstance(extra_monthly, Decimal):
        raise TypeError(
            f"extra_monthly must be a Decimal, "
            f"got {type(extra_monthly).__name__}"
        )

    if extra_monthly < ZERO:
        raise ValueError(
            f"extra_monthly must be >= 0, got {extra_monthly}"
        )

    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"strategy must be one of {sorted(_VALID_STRATEGIES)}, "
            f"got {strategy!r}"
        )

    # Active debts must have a positive minimum payment -- otherwise
    # the debt can never be paid off (except via extra allocation).
    for debt in active_debts:
        if debt.minimum_payment <= ZERO:
            raise ValueError(
                f"minimum_payment must be > 0 for active debt "
                f"(account_id={debt.account_id}, name={debt.name!r}), "
                f"got {debt.minimum_payment}"
            )

    if strategy == STRATEGY_CUSTOM:
        _validate_custom_order(active_debts, custom_order)


def _validate_custom_order(
    active_debts: list[DebtAccount],
    custom_order: list[int] | None,
) -> None:
    """Validate custom_order matches the active debts exactly.

    Custom order must contain exactly the same set of account_ids as
    the active debts -- no missing, no extras, no duplicates.

    Args:
        active_debts: Debts with current_principal > 0.
        custom_order: User-provided priority list of account_ids.

    Raises:
        ValueError: If custom_order is None, empty, has duplicates,
            or does not match the active debts.
    """
    if custom_order is None:
        raise ValueError(
            "custom_order is required for custom strategy."
        )

    if not custom_order:
        raise ValueError(
            "custom_order must not be empty for custom strategy."
        )

    # Check for duplicates.
    if len(custom_order) != len(set(custom_order)):
        seen = set()
        duplicates = set()
        for aid in custom_order:
            if aid in seen:
                duplicates.add(aid)
            seen.add(aid)
        raise ValueError(
            f"custom_order contains duplicate account_ids: "
            f"{sorted(duplicates)}"
        )

    active_ids = {d.account_id for d in active_debts}
    order_ids = set(custom_order)

    missing = active_ids - order_ids
    if missing:
        raise ValueError(
            f"custom_order is missing account_ids that are in "
            f"the active debts list: {sorted(missing)}"
        )

    extra = order_ids - active_ids
    if extra:
        raise ValueError(
            f"custom_order contains account_ids not in the "
            f"active debts list: {sorted(extra)}"
        )


def _sort_debts(
    debts: list[DebtAccount],
    strategy: str,
    custom_order: list[int] | None,
) -> list[DebtAccount]:
    """Sort debts according to the selected strategy.

    Deterministic tiebreaking ensures stable ordering for tests:

    - Avalanche: highest interest_rate first.  Ties broken by smallest
      current_principal (optimal: smaller debt first among equal rates).
      Final tiebreaker: account_id ascending.
    - Snowball: smallest current_principal first.  Ties broken by
      highest interest_rate (optimal: save more interest among equal
      balances).  Final tiebreaker: account_id ascending.
    - Custom: ordered by position in custom_order.

    Args:
        debts: Active debts (principal > 0).
        strategy: One of 'avalanche', 'snowball', 'custom'.
        custom_order: For custom strategy, account_ids in priority order.

    Returns:
        A new list of DebtAccount sorted by the strategy's priority.
    """
    if strategy == STRATEGY_AVALANCHE:
        return sorted(
            debts,
            key=lambda d: (
                -d.interest_rate,
                d.current_principal,
                d.account_id,
            ),
        )

    if strategy == STRATEGY_SNOWBALL:
        return sorted(
            debts,
            key=lambda d: (
                d.current_principal,
                -d.interest_rate,
                d.account_id,
            ),
        )

    # Custom: sort by position in custom_order.
    position = {aid: idx for idx, aid in enumerate(custom_order)}
    return sorted(debts, key=lambda d: position[d.account_id])


def _snap_to_zero(balance: Decimal) -> Decimal:
    """Round a balance to cents and clamp negatives to zero.

    Rounding can produce sub-penny negative values (e.g. -0.004 from
    a quantize step in the calling math).  These must not propagate
    as negative balances.

    Routed through :func:`app.utils.money.round_money` so the
    boundary mode is the project default ROUND_HALF_UP -- the
    rounding convention every hand-computed debt-strategy assertion
    in this project assumes (E-26, HIGH-04 / F-017..F-023).

    Args:
        balance: The balance after a payment subtraction.

    Returns:
        ``balance`` rounded to two decimal places via
        :func:`round_money`, clamped to ZERO minimum.
    """
    result = round_money(balance)
    if result < ZERO:
        return ZERO
    return result


@dataclass(frozen=True)
class _SimulationState:
    """Mutable per-debt working arrays for the month-by-month simulation.

    All lists are parallel, indexed by position in ``sorted_debts``, so
    the simulation loop and its step helpers share one cohesive state
    object instead of five parallel locals threaded by hand.

    The dataclass is frozen to fix the array identities: the simulation
    evolves the list *contents* in place (balances shrink, totals
    accumulate, timelines grow), never the bindings themselves.

    Attributes:
        sorted_debts: Active debts in strategy priority order (read-only).
        balances: Current working balance per debt.
        interest_totals: Accumulated interest paid per debt.
        paid_totals: Accumulated total paid (principal + interest) per
            debt.
        payoff_months: Month each debt reached zero, or 0 while still
            unpaid (the not-yet-paid-off sentinel the step helpers test).
        timelines: Per-debt month-end balance history; index 0 is the
            starting balance, with one entry appended per simulated month.
    """

    sorted_debts: list[DebtAccount]
    balances: list[Decimal]
    interest_totals: list[Decimal]
    paid_totals: list[Decimal]
    payoff_months: list[int]
    timelines: list[list[Decimal]]

    @classmethod
    def initialize(
        cls,
        sorted_debts: list[DebtAccount],
    ) -> "_SimulationState":
        """Build zero-initialized working arrays for ``sorted_debts``.

        Each array is parallel to ``sorted_debts``: balances and
        timelines start at the current principal, the accumulators start
        at zero, and payoff_months start at 0 (the unpaid sentinel).

        Args:
            sorted_debts: Active debts already ordered by strategy
                priority.

        Returns:
            A fresh _SimulationState ready for the simulation loop.
        """
        num_debts = len(sorted_debts)
        return cls(
            sorted_debts=sorted_debts,
            balances=[d.current_principal for d in sorted_debts],
            interest_totals=[ZERO] * num_debts,
            paid_totals=[ZERO] * num_debts,
            payoff_months=[0] * num_debts,
            timelines=[[d.current_principal] for d in sorted_debts],
        )


def _accrue_interest(state: _SimulationState, window: tuple[date, date]) -> None:
    """Accrue one month of interest on all active debts.

    Modifies state.balances and state.interest_totals in place.

    Charges through :func:`~app.utils.money.installment_interest`, the ONE
    interest producer, under each debt's own convention -- the same call
    ``amortization_engine._projection``, ``rate_period_engine`` and the loan
    replay make, so this simulation and the loan surfaces accrue
    byte-identically by construction (plan step recurrence:R16-d moved all
    five onto it from ``accrue_monthly_interest``, which it deleted).

    **It was a FIFTH inline spelling until this step, and the two were not the
    same function.**  It computed ``round_money(balance * rate / 12)``, which
    associates as ``(balance * rate) / 12``, against the primitive's
    ``balance * (rate / 12)``: measured over 500,000 randomised draws they
    differ on one, ``$271,375.20`` at ``0.025`` giving ``$565.37`` against
    ``$565.36`` (2026-09-02).  Finding **D52** recorded the duplicate with the
    evidence *"the two orderings agree on 200,000 randomised draws"*, and that
    sentence is what this refutes -- an agreement measured at one sample size
    and quoted as a reason.

    **Its docstring also claimed "Matches the amortization engine's interest
    calculation convention", and that was FALSE about a named component**: that
    engine called the primitive (``_projection.py`` imported and called
    ``accrue_monthly_interest``), so the copy asserted conformance to a
    convention it did not implement.  A misstated formula is caught by reading
    the code beside it; a false conformance claim tells the reader the question
    is already settled, which is why five spellings of one rule survived review.

    Moves ``$0.00`` on production: walked over both live debts' full
    amortization at their own rates (Mortgage 6.875%, Van Loan 5.668%) the two
    spellings never part, so lifetime interest is identical to the cent.

    Args:
        state: The simulation working state; balances and interest_totals
            are updated in place.
        window: The ``(start, end)`` month this step charges for: the
            simulation's month ending ``month`` months after its start
            (:func:`_simulate_month`).
    """
    window_start, window_end = window
    for i, debt in enumerate(state.sorted_debts):
        if state.balances[i] <= ZERO:
            continue
        interest = installment_interest(
            state.balances[i], debt.interest_rate, window_start, window_end,
            debt.convention,
        )
        state.balances[i] += interest
        state.interest_totals[i] += interest


def _apply_minimum_payments(
    state: _SimulationState,
    month: int,
) -> tuple[Decimal, Decimal]:
    """Apply minimum payments to all active debts.

    When a minimum payment exceeds the remaining balance, the payment
    is capped at the balance.  The unused portion becomes surplus that
    is available for same-month redistribution.  The full minimum is
    freed for future months when the debt is paid off.

    Modifies state.balances, state.paid_totals, and state.payoff_months
    in place.

    Args:
        state: The simulation working state; balances, paid_totals, and
            payoff_months are updated in place.
        month: Current month number (1-indexed).

    Returns:
        (minimum_surplus, newly_freed) where:
            minimum_surplus: Unused portion of capped minimums,
                available for same-month extra redistribution.
            newly_freed: Sum of minimums from debts paid off this
                step, to be added to extra_pool for future months.
    """
    minimum_surplus = ZERO
    newly_freed = ZERO

    for i, debt in enumerate(state.sorted_debts):
        if state.balances[i] <= ZERO:
            continue

        min_pay = min(debt.minimum_payment, state.balances[i])
        state.balances[i] = _snap_to_zero(state.balances[i] - min_pay)
        state.paid_totals[i] += min_pay

        if state.balances[i] <= ZERO and state.payoff_months[i] == 0:
            state.payoff_months[i] = month
            # Unused portion of capped minimum -- available THIS month.
            minimum_surplus += debt.minimum_payment - min_pay
            # Full minimum freed for FUTURE months.
            newly_freed += debt.minimum_payment

    return minimum_surplus, newly_freed


def _cascade_extra_payments(
    state: _SimulationState,
    month: int,
    available_extra: Decimal,
) -> Decimal:
    """Cascade extra payment through target debts in priority order.

    Applies the available extra to the first unpaid debt.  If the extra
    exceeds that debt's remaining balance, the surplus cascades to the
    next debt in the same month.  This within-month redistribution is
    what makes snowball/avalanche effective -- money does not sit idle
    for a month after a payoff.

    Modifies state.balances, state.paid_totals, and state.payoff_months
    in place.

    Args:
        state: The simulation working state; balances, paid_totals, and
            payoff_months are updated in place.
        month: Current month number (1-indexed).
        available_extra: Total extra available this month (extra_pool
            plus any minimum surplus from capped payments).

    Returns:
        Sum of minimums freed by debts paid off during this step,
        to be added to extra_pool for future months.
    """
    newly_freed = ZERO
    remaining = available_extra

    for i, debt in enumerate(state.sorted_debts):
        if remaining <= ZERO:
            break
        if state.balances[i] <= ZERO:
            continue

        extra_pay = min(remaining, state.balances[i])
        state.balances[i] = _snap_to_zero(state.balances[i] - extra_pay)
        state.paid_totals[i] += extra_pay
        remaining -= extra_pay

        if state.balances[i] <= ZERO and state.payoff_months[i] == 0:
            state.payoff_months[i] = month
            newly_freed += debt.minimum_payment

    return newly_freed


def _simulate_month(
    state: _SimulationState,
    month: int,
    extra_pool: Decimal,
    start_date: date,
) -> Decimal:
    """Run one month of the simulation against ``state``.

    Performs the three payment steps in order -- accrue interest, apply
    minimum payments, cascade the extra pool -- then records each debt's
    month-end balance on its timeline.  All mutations land on ``state``
    in place.

    **Month ``m`` charges for the calendar month ending ``m`` months after
    the start** (plan step recurrence:R16-d): the window
    ``[add_months(start, m - 1), add_months(start, m))``, through the one
    window helper (:func:`~app.services.installment_calendar.accrual_window`)
    with the start's day as the meant day -- the same months the payoff
    dates are counted in (:func:`_build_result`).  A debt carries no payment
    day, so the months are the simulation's own; under ``MONTHLY_1_12``,
    which counts months, any one-month window charges the same.

    Args:
        state: The simulation working state, updated in place.
        month: Current month number (1-indexed).
        extra_pool: The standing extra pool carried into this month (the
            user's extra_monthly plus minimums freed by prior payoffs).
            Capped-minimum surplus from this month is added on top before
            the cascade.
        start_date: The simulation's start, whose months the steps count.

    Returns:
        Total minimum payments freed by debts paid off this month (from
        both the minimum-payment and extra-cascade steps), to be added to
        the caller's extra_pool for subsequent months.
    """
    # Step 1: Accrue one month of interest on all active debts.
    _accrue_interest(
        state, accrual_window(add_months(start_date, month), start_date.day),
    )

    # Step 2: Apply minimum payments.  Track surplus from capped
    # minimums (available this month) and freed minimums (available
    # starting next month).
    minimum_surplus, freed_from_min = _apply_minimum_payments(state, month)

    # Step 3: Cascade extra through target debts in priority order.
    # Available extra = standing pool + surplus from capped minimums.
    available_extra = extra_pool + minimum_surplus
    freed_from_extra = _cascade_extra_payments(state, month, available_extra)

    # Step 4: Record month-end balances for all debts (including those
    # already paid off, which append $0).
    for i, balance in enumerate(state.balances):
        state.timelines[i].append(balance)

    return freed_from_min + freed_from_extra


def calculate_strategy(request: StrategyRequest) -> StrategyResult:
    """Calculate a debt payoff strategy across multiple accounts.

    Simulates month-by-month debt reduction under the selected strategy.
    Each month: (1) interest accrues on all active debts, (2) minimum
    payments are applied to all active debts, (3) extra payment is
    cascaded through target debts in priority order.  When a debt is
    paid off, its freed minimum payment increases the extra pool for
    subsequent months.  Surplus from overpayment cascades to the next
    target within the same month.

    ARM limitation (R-5): Each debt uses a fixed interest rate.  ARM
    rate adjustments during the payoff period are not modeled.

    Args:
        request: The strategy inputs -- debts, extra_monthly, strategy,
            and the optional custom_order / start_date /
            max_horizon_months.  See :class:`StrategyRequest` for the
            per-field semantics and defaults.

    Returns:
        StrategyResult with per-account payoff timelines and aggregate
        metrics.

    Raises:
        TypeError: If request.extra_monthly is not a Decimal.
        ValueError: If inputs fail validation (empty debts, negative
            extra, invalid strategy, mismatched custom_order, zero
            minimum payment on active debt).
    """
    # Filter out zero/negative principal debts before validation.
    active_debts = [d for d in request.debts if d.current_principal > ZERO]

    _validate_inputs(
        active_debts, request.extra_monthly, request.strategy,
        request.custom_order,
    )
    sorted_debts = _sort_debts(
        active_debts, request.strategy, request.custom_order,
    )

    start_date = (
        request.start_date if request.start_date is not None
        else date.today()
    )
    max_horizon = request.max_horizon_months
    state = _SimulationState.initialize(sorted_debts)

    # Extra pool starts at the user's extra_monthly contribution.
    # It grows as debts are paid off and their minimums are freed.
    extra_pool = request.extra_monthly

    final_month = max_horizon
    horizon_reached = False

    for month in range(1, max_horizon + 1):
        # Stop once all debts were paid off in the previous month.
        if all(b <= ZERO for b in state.balances):
            final_month = month - 1
            break
        extra_pool += _simulate_month(state, month, extra_pool, start_date)
    else:
        # for-loop exhausted without break -- horizon reached if any
        # debts still have a positive balance.
        horizon_reached = any(b > ZERO for b in state.balances)

    # Debts not paid off within the horizon report the horizon month.
    for i, paid_month in enumerate(state.payoff_months):
        if paid_month == 0:
            state.payoff_months[i] = max_horizon

    return _build_result(
        state, start_date, final_month, request.strategy, horizon_reached,
    )


def _build_result(
    state: _SimulationState,
    start_date: date,
    final_month: int,
    strategy: str,
    horizon_reached: bool,
) -> StrategyResult:
    """Assemble the final StrategyResult from the simulation state.

    Quantizes monetary totals to 2 decimal places and computes
    payoff dates from start_date and payoff month numbers.

    Args:
        state: The completed simulation working state (the per-debt
            payoff_months, interest_totals, paid_totals, and timelines).
        start_date: Strategy start date for date arithmetic.
        final_month: Month when the last debt is paid off.
        strategy: Strategy name string.
        horizon_reached: Whether the horizon was reached.

    Returns:
        A fully populated StrategyResult.
    """
    per_account = []
    for i, debt in enumerate(state.sorted_debts):
        per_account.append(AccountPayoff(
            account_id=debt.account_id,
            name=debt.name,
            payoff_month=state.payoff_months[i],
            payoff_date=add_months(start_date, state.payoff_months[i]),
            total_interest=round_money(state.interest_totals[i]),
            total_paid=round_money(state.paid_totals[i]),
            balance_timeline=state.timelines[i],
        ))

    result_interest = sum(
        (a.total_interest for a in per_account), ZERO,
    )
    result_paid = sum(
        (a.total_paid for a in per_account), ZERO,
    )

    return StrategyResult(
        per_account=per_account,
        total_interest=result_interest,
        total_paid=result_paid,
        debt_free_date=add_months(start_date, final_month),
        total_months=final_month,
        strategy_name=strategy,
        horizon_reached=horizon_reached,
    )
