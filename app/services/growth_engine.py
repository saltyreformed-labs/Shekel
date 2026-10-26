"""
Shekel Budget App -- Compound Growth Engine Service

Pure function service that projects investment account balances forward
over time, handling compound growth, periodic contributions, employer
contributions, and annual contribution limits.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from app.utils.money import round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


@dataclass
class ProjectedBalance:  # pylint: disable=too-many-instance-attributes
    """A single period's projected investment balance.

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed
    because this is a cohesive value record -- one period's full
    projection row -- mirroring ``amortization_engine.AmortizationRow``.
    ``is_confirmed`` distinguishes confirmed contributions from projected
    ones (``docs/implementation_plan_section5.md``), the savings analogue
    of the loan schedule's confirmed/projected badge;
    ``contribution_limit_remaining`` and ``ytd_contributions`` are the
    row's running limit/YTD columns.  Every field is an irreducible
    column of the row; splitting it would fragment one domain concept and
    break every consumer for no design gain.

    **The row carries its PERIOD, not a copy of part of its key** (plan step
    C2-e, ledger row **P21**).  This field was ``period_id: int``, read off the
    axis period's ``.id``, and that partial copy is what made the collapse
    possible: every PROJECTED period past the owner's horizon carries
    ``period_id = None`` by design, so over a 20-year axis of 523 periods the
    471 projected ones all keyed one map entry and a chart's month captions
    every read as the last projected month.  Holding the period itself removes
    the question -- a consumer that wants the ordinal reads
    ``row.period.period_index`` (unique across a mixed saved/projected window,
    because the projection continues the sequence) and one that wants the
    caption reads ``row.period.start_date``, so neither has to rebuild a map
    of the axis it already passed in.

    Attributes:
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` this row
            projects.  Every other field is a figure AT that period.
    """
    period: DerivedPeriod
    start_balance: Decimal
    growth: Decimal
    contribution: Decimal
    employer_contribution: Decimal
    end_balance: Decimal
    ytd_contributions: Decimal
    contribution_limit_remaining: Decimal  # None if no limit
    is_confirmed: bool = False


@dataclass(frozen=True)
class ContributionRecord:
    """A single contribution to an investment account.

    Used to replay actual or committed contributions through the growth
    projection so projections reflect real contribution history rather
    than assuming the same amount every period.

    Attributes:
        contribution_date: The pay period start date this contribution
            maps to.  Matched to periods by exact start_date.
        amount: The contribution amount.  Must be >= 0.  A zero amount
            represents a period where no contribution was made (only
            growth accrues) -- not the same as a missing entry, which
            falls back to periodic_contribution.
        is_confirmed: True if the contribution is Paid/Received
            (historical fact).  False if Projected (future commitment).
    """

    contribution_date: date
    amount: Decimal
    is_confirmed: bool

    def __post_init__(self):
        """Validate contribution record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the projection loop.

        Raises:
            TypeError: If contribution_date is not a date, amount is not
                a Decimal, or is_confirmed is not a bool.
            ValueError: If amount is negative.
        """
        # Pylint: ``duplicate-code`` -- this field-validation body mirrors
        # ``amortization_engine.PaymentRecord.__post_init__`` -- both
        # reject a non-date date field, a non-Decimal/negative amount, and
        # a non-bool ``is_confirmed``.  The two are independent engine
        # dataclasses (savings contribution vs loan payment); a shared
        # validator parameterised on the date-field name would add
        # indirection without removing logic (coding-standards rule 13).
        # One-sided ``duplicate-code`` disable (see plan.md Phase 2 notes).
        # pylint: disable=duplicate-code
        if not isinstance(self.contribution_date, date):
            raise TypeError(
                f"contribution_date must be a date, "
                f"got {type(self.contribution_date).__name__}"
            )
        if not isinstance(self.amount, Decimal):
            raise TypeError(
                f"amount must be a Decimal, got {type(self.amount).__name__}"
            )
        if self.amount < 0:
            raise ValueError(
                f"amount must be >= 0, got {self.amount}"
            )
        if not isinstance(self.is_confirmed, bool):
            raise TypeError(
                f"is_confirmed must be a bool, "
                f"got {type(self.is_confirmed).__name__}"
            )
        # pylint: enable=duplicate-code


def cap_contribution_at_limit(
    contribution: Decimal,
    annual_contribution_limit: Decimal | None,
    ytd_contributions: Decimal,
) -> Decimal:
    """Cap a per-period employee contribution at the remaining annual limit.

    Mirrors the cap step in :func:`project_balance`'s per-period loop so
    callers that need a single point-in-time snapshot of the capped
    contribution (e.g. the investment dashboard's "Employer contribution
    per period" card; HIGH-07 / F-043 / F-055) produce the byte-identical
    value the engine would apply for the same period.  The capped value
    is what must be fed to :func:`calculate_employer_contribution` so the
    card, the chart, and the year-end summary all read one number.

    Args:
        contribution:               Decimal employee contribution proposed
                                    for the period.  Negatives are
                                    clamped to zero.
        annual_contribution_limit:  Decimal annual cap, or ``None`` for
                                    accounts with no IRS limit (e.g.
                                    Brokerage).  ``None`` means
                                    "uncapped"; a stored ``Decimal("0")``
                                    means "no contributions allowed this
                                    year" (E-12: zero is a value, not
                                    missing).
        ytd_contributions:          Decimal contributions already made in
                                    the current year.

    Returns:
        Decimal contribution capped at ``max(annual_limit - ytd, 0)``
        when a limit is set, else ``max(contribution, 0)``.
    """
    contribution = Decimal(str(contribution))
    if annual_contribution_limit is None:
        return max(contribution, ZERO)
    annual_contribution_limit = Decimal(str(annual_contribution_limit))
    ytd_contributions = Decimal(str(ytd_contributions))
    remaining_limit = max(annual_contribution_limit - ytd_contributions, ZERO)
    return max(min(contribution, remaining_limit), ZERO)


def calculate_employer_contribution(
    employer_params, employee_contribution, gross_override=None,
):
    """Calculate the employer contribution for a single pay period.

    Args:
        employer_params: dict with keys:
            - type_id: ``ref.employer_contribution_types.id`` -- the
              flat_percentage or match row (#38; the NONE row never
              reaches here -- ``employer_contribution_params`` in
              ``investment_projection`` returns None for it)
            - flat_percentage: Decimal (for flat_percentage type)
            - match_percentage: Decimal (for match type)
            - match_cap_percentage: Decimal (for match type)
            - gross_biweekly: Decimal (gross pay per period).  **No builder
              emits this key since plan step salary:R14-b** (finding
              **N-538**, plan#1787): ``investment_projection
              .employer_contribution_params`` is its only producer and that
              step removed it, so the
            fallback below reads a MISSING key and answers ``$0``.  Every
            production caller supplies ``gross_override`` (or ``salary_basis``,
            which becomes it); a caller that forgets gets silence rather than
            an error, which is why the finding exists.
        employee_contribution: Decimal amount the employee contributed.
        gross_override: The per-period gross a percentage is taken OF
            (P1b / finding D3 / fork F3).  **Effectively required since plan
            step salary:R14-b**: it replaced the constant
            ``employer_params["gross_biweekly"]``, which no builder emits any
            more, so ``None`` no longer means "keep the constant base" -- it
            means a basis of ``$0`` and therefore no employer money at all.
            Every production caller passes one, directly or through
            ``project_balance``'s ``salary_basis``.  Making that structural
            rather than documented is finding **N-538** (plan#1787).

    Returns:
        Decimal employer contribution amount.
    """
    if not employer_params:
        return ZERO

    emp_type_id = employer_params.get("type_id")
    gross = (
        Decimal(str(gross_override))
        if gross_override is not None
        else Decimal(str(employer_params.get("gross_biweekly", 0)))
    )

    flat_id = ref_cache.employer_contribution_type_id(
        EmployerContributionTypeEnum.FLAT_PERCENTAGE
    )
    match_id = ref_cache.employer_contribution_type_id(
        EmployerContributionTypeEnum.MATCH
    )

    if emp_type_id == flat_id:
        pct = Decimal(str(employer_params.get("flat_percentage", 0)))
        return round_money(gross * pct)

    if emp_type_id == match_id:
        match_pct = Decimal(str(employer_params.get("match_percentage", 0)))
        cap_pct = Decimal(str(employer_params.get("match_cap_percentage", 0)))
        matchable_salary = round_money(gross * cap_pct)
        matched_amount = min(employee_contribution, matchable_salary)
        return round_money(matched_amount * match_pct)

    return ZERO


def _build_contribution_lookup(contributions):
    """Build lookup dict mapping contribution_date to (amount, is_confirmed).

    Groups contributions by date, summing amounts.  is_confirmed is True
    only if ALL records on that date are confirmed.

    Args:
        contributions: Optional list of ContributionRecord instances.
            None or empty list returns None.

    Returns:
        dict mapping date to (Decimal amount, bool is_confirmed), or None
        if contributions is None or empty.
    """
    if not contributions:
        return None
    sorted_contribs = sorted(
        contributions, key=lambda c: c.contribution_date
    )
    lookup = {}
    for record in sorted_contribs:
        d = record.contribution_date
        if d in lookup:
            existing_amount, existing_confirmed = lookup[d]
            lookup[d] = (
                existing_amount + record.amount,
                # Confirmed only if ALL records on this date are confirmed.
                existing_confirmed and record.is_confirmed,
            )
        else:
            lookup[d] = (record.amount, record.is_confirmed)
    return lookup


def growth_rate_for_days(
    assumed_annual_return: Decimal, days: int,
) -> Decimal:
    """Scale an annual return down to a whole number of calendar days.

    **The one compound-growth formula in the app**, and the only place
    ``(1 + annual) ** (days / 365) - 1`` is written.  Everything that grows
    money over time reaches it: the forward projection
    (:func:`project_balance`), the reverse one
    (:func:`reverse_project_balance`), the retirement contribution solver's
    annuity factor (:mod:`app.services.retirement_levers`), the balance seam's
    per-day compound accrual (``balance_at._asset_fold``), the property equity
    chart and the /savings Horizon's asset bands.

    It takes a DAY COUNT rather than a period, and plan step **C2-e** is why.
    The parameter used to be a duck-typed "period object" read only for its two
    dates, and because anything with those two attributes satisfied it, three
    separate impostor types grew to be passed here -- ``SyntheticPeriod``
    (deleted at C2-e, ledger row **P17**), ``_asset_fold._ONE_DAY_SPAN`` and
    ``property_equity_chart._AppreciationSpan``, none of them a pay period and
    all of them existing only to reach this function.  A caller holding two
    real dates uses :func:`span_return_rate`; a caller that knows only the
    length of the span -- the seam's per-day rate is the same on every day of
    every year -- calls this directly and invents no date at all.

    Args:
        assumed_annual_return: Decimal annual return rate (e.g. 0.07 for 7%).
        days: The number of calendar days to scale to.  Must be at least 1.

    Returns:
        Decimal compound rate ``(1 + annual) ** (days / 365) - 1``.

    Raises:
        ValueError: *days* is below 1.  There is no such thing as growth over a
            zero-day or negative span, and the honest failure is louder than any
            number this could return: the branch this REPLACES silently
            substituted the 14-day biweekly rate, so a caller with its dates
            crossed was handed a plausible figure instead of an error.  Like
            :func:`span_return_rate`'s, it is a boundary guard rather than a
            live branch -- ``_asset_fold`` passes the literal 1, and the
            /savings Horizon's asset band selects strictly forward dates before
            calling.
    """
    if days < 1:
        raise ValueError(
            f"growth cannot be scaled to {days} day(s): a compound rate is "
            f"defined over at least one calendar day.  This used to fall back "
            f"to the 14-day biweekly rate, which turned a caller's crossed "
            f"dates into a believable return rather than a failure."
        )
    return (
        (1 + assumed_annual_return)
        ** (Decimal(str(days)) / Decimal("365"))
        - 1
    )


def span_return_rate(
    assumed_annual_return: Decimal, first_day: date, last_day: date,
) -> Decimal:
    """Compound return rate over ``[first_day, last_day]``, counted INCLUSIVELY.

    The date door onto :func:`growth_rate_for_days`, and it exists so the
    ``+ 1`` is written once.  Pay periods carry an inclusive last day: a
    standard 14-calendar-day period runs ``start`` .. ``start + 13`` and the
    next one starts the following day -- every period ends the day before its
    NEXT payday, which for the last one is the PROJECTED payday rather than a
    recorded neighbour (one rule since plan step ``pay_calendar:C14-c``).  The
    span is therefore ``(last_day - first_day).days + 1`` --
    14 for a standard biweekly period, not 13.  Counting exclusively (the old
    ``end - start`` without the ``+ 1``) dropped one calendar day per period;
    because consecutive periods tile the calendar with no gaps, that lost 1 day
    in 14 (~26 days a year, ~7.1%) of compounding, so a configured 10.5%
    annual return behaved as only ~9.69% effective.  Five call sites hold two
    dates; five open-coded inclusive counts would be five chances to
    reintroduce exactly that.

    A same-day span is one day of growth (``(0).days + 1 == 1``).

    Args:
        assumed_annual_return: Decimal annual return rate (e.g. 0.07 for 7%).
        first_day: Inclusive first day of the span.
        last_day: Inclusive last day of the span.

    Returns:
        Decimal compound rate over the inclusive span.

    Raises:
        ValueError: *last_day* precedes *first_day*.  **No call path in
        ``app/`` can produce one** since plan step C2-e -- every period reaching
        here is a
        :class:`~app.services.pay_calendar.DerivedPeriod`, whose end is the day
        before its next payday and so is never below its own start -- a
        cadence carrying a displacing convention is held to a floor no two
        paydays can be displaced onto one day under (ruling
        **pay_calendar:R-PC59**) -- and its one date-pair caller
        (``property_equity_chart``) selects strictly forward dates.  It is a
        boundary guard on a public function, not a live branch -- the same
        standing :meth:`~app.services.pay_calendar.PayCalendar.overlapping`'s
        crossed-range refusal has.
    """
    return growth_rate_for_days(
        assumed_annual_return, (last_day - first_day).days + 1,
    )


@dataclass(frozen=True)
class _PeriodInputs:
    """Per-projection constants shared by every period of :func:`project_balance`.

    Bundles the inputs that stay fixed for the whole forward walk so the
    per-period helper takes one cohesive object rather than a fistful of
    parallel constants.  Mirrors ``amortization_engine.ProjectionInputs``
    (a projection's fixed forward-only terms); the values that evolve
    period to period live in :class:`_ProjectionState`.

    Attributes:
        assumed_annual_return: Decimal annual return rate, normalized once.
        periodic_contribution: Decimal employee contribution used as the
            fallback when a period has no matching ``ContributionRecord``.
        employer_params: Optional employer-match configuration dict (see
            :func:`calculate_employer_contribution`).
        annual_contribution_limit: Decimal annual cap, normalized once, or
            ``None`` for accounts with no IRS limit.
        salary_basis: Per-period gross resolver (P1b / fork F3),
            ``period -> Decimal gross_biweekly``, supplying the
            employer-contribution base for each period.  **Every consumer
            that configures an employer contribution passes one since plan
            step salary:R14-b**; ``None`` no longer means a constant base,
            because there is no constant left to fall back to (**N-538**).
    """

    assumed_annual_return: Decimal
    periodic_contribution: Decimal
    employer_params: dict | None
    annual_contribution_limit: Decimal | None
    salary_basis: Callable | None = None


@dataclass
class _ProjectionState:
    """Mutable carry-forward state threaded across the per-period loop.

    Bundles the values that evolve together as :func:`project_balance`
    walks period by period, so the loop and its helper share one cohesive
    state object instead of parallel locals.

    Attributes:
        current_balance: Running balance after the latest applied period.
        ytd_contributions: Employee contributions so far in the current
            year (resets to zero at each year boundary).
        remaining_limit: Remaining room under the annual contribution
            limit, or ``None`` when the account has no limit.
        prev_year: Calendar year of the previously projected period, used
            to detect a year boundary; ``None`` before the first period.
    """

    current_balance: Decimal
    ytd_contributions: Decimal
    remaining_limit: Decimal | None
    prev_year: int | None = None


def _project_one_period(
    state: _ProjectionState,
    period: DerivedPeriod,
    inputs: _PeriodInputs,
    contribution_lookup: dict[date, tuple[Decimal, bool]] | None,
) -> ProjectedBalance:
    """Project one pay period forward, mutating ``state`` in place.

    Runs the five-step per-period walk -- year-boundary limit reset,
    growth on the opening balance (applied BEFORE the contribution),
    contribution resolution (the dated lookup or the periodic fallback),
    annual-limit capping, and the employer match -- then advances
    ``state`` and returns the period's row.

    Args:
        state: The mutable carry-forward state; advanced in place.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` to
            project.
        inputs: The per-projection constants.
        contribution_lookup: Optional ``start_date -> (amount, is_confirmed)``
            map from :func:`_build_contribution_lookup`; ``None`` uses the
            periodic fallback for every period.

    Returns:
        The :class:`ProjectedBalance` row for this period.
    """
    period_year = period.start_date.year

    # Year boundary reset: YTD and the remaining annual limit restart.
    if state.prev_year is not None and period_year != state.prev_year:
        state.ytd_contributions = ZERO
        if inputs.annual_contribution_limit is not None:
            state.remaining_limit = inputs.annual_contribution_limit
    state.prev_year = period_year

    start_balance = state.current_balance

    # Step 1: Growth on the existing balance, before this period's contribution.
    growth = round_money(
        start_balance * span_return_rate(
            inputs.assumed_annual_return, period.start_date, period.end_date,
        )
    )

    # Determine this period's contribution and confirmed status.  A dated
    # entry (even $0) wins; a missing entry falls back to the periodic
    # contribution and is treated as projected (not confirmed).
    if (contribution_lookup is not None
            and period.start_date in contribution_lookup):
        period_contrib_amount, period_is_confirmed = (
            contribution_lookup[period.start_date]
        )
    else:
        period_contrib_amount = inputs.periodic_contribution
        period_is_confirmed = False

    # Step 2: Cap the contribution at the remaining annual limit via the
    # shared helper.  HIGH-07 / F-043 / F-055: the same cap the investment
    # dashboard's per-period employer card applies, so the card, this
    # chart's employer line, and the year-end summary agree on one number.
    contribution = cap_contribution_at_limit(
        period_contrib_amount,
        inputs.annual_contribution_limit,
        state.ytd_contributions,
    )

    # Step 3: Employer contribution on the capped employee amount.  P1b /
    # fork F3: the per-period salary basis supplies this period's gross, so
    # the flat-percentage employer contribution tracks the paycheck rather
    # than freezing at one.  It REPLACED a constant
    # ``employer_params["gross_biweekly"]`` that no builder emits since plan
    # step salary:R14-b, so a caller that omits the basis now gets a $0
    # basis rather than a constant one -- finding N-538 (plan#1787).
    gross_override = (
        inputs.salary_basis(period)
        if inputs.salary_basis is not None
        else None
    )
    employer_contribution = calculate_employer_contribution(
        inputs.employer_params, contribution, gross_override,
    )

    # Step 4: Update balance.  Clamp to zero -- standard investment
    # accounts cannot go negative (M-06).
    state.current_balance = max(
        start_balance + growth + contribution + employer_contribution,
        ZERO,
    )

    # Step 5: Track limits.
    state.ytd_contributions += contribution
    if state.remaining_limit is not None:
        state.remaining_limit -= contribution
        state.remaining_limit = max(state.remaining_limit, ZERO)

    return ProjectedBalance(
        period=period,
        start_balance=start_balance,
        growth=growth,
        contribution=contribution,
        employer_contribution=employer_contribution,
        end_balance=state.current_balance,
        ytd_contributions=state.ytd_contributions,
        contribution_limit_remaining=state.remaining_limit,
        is_confirmed=period_is_confirmed,
    )


def project_balance(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    current_balance,
    assumed_annual_return,
    periods: PeriodWindow,
    periodic_contribution=ZERO,
    employer_params=None,
    annual_contribution_limit=None,
    ytd_contributions_start=ZERO,
    contributions=None,
    salary_basis=None,
):
    """Project investment balance forward across pay periods.

    Growth is applied to the balance BEFORE the period's contribution
    is added, modeling that existing investments grow while new money
    is contributed.

    **The axis is the owner's OWN pay calendar** since plan step C2-e.  It used
    to be a list of anything carrying two dates and an id, which is how a
    fabricated 14-day axis came to price a monthly-paid owner's contributions
    at ``365/14`` a year -- ``$1,300,344.92`` shown against a true
    ``$711,385.70`` over 20 years (ledger row **P20**).  What ended that is the
    DELETION of the producer that fabricated the axis, not the annotation below:
    Python does not check it, so a caller can still pass any sequence and this
    would run.  The annotation states the contract; there is no longer a
    producer in the tree that can supply anything else.

    Args:
        current_balance:          Decimal starting balance, valued the day
                                  BEFORE the first period opens -- the window
                                  and the seed's past are disjoint, so no
                                  contribution or day of growth is counted
                                  twice (rulings R-AB / R-AE / R-AF).
        assumed_annual_return:    Decimal annual return rate (e.g. 0.07 for 7%).
        periods:                  The :class:`~app.services.pay_calendar.PeriodWindow`
                                  to project over, ordered and contiguous by
                                  construction.
        periodic_contribution:    Decimal employee contribution per period.  Used as the
                                  fallback when contributions is None or a period has no
                                  matching ContributionRecord.
        employer_params:          dict for employer contribution calculation (see above).
        annual_contribution_limit: Decimal annual limit (None for no limit).
        ytd_contributions_start:  Decimal contributions already made this year.
        contributions:            Optional list of ContributionRecord instances providing
                                  per-period contribution amounts.  When provided, each
                                  period looks up its amount by start_date; periods without
                                  a matching record fall back to periodic_contribution.
                                  A record with amount=0 is an explicit "no contribution" --
                                  distinct from a missing entry.  None or [] uses the static
                                  periodic_contribution for all periods.
        salary_basis:             Optional ``period -> Decimal gross_biweekly`` resolver
                                  (P1b / fork F3).  Supplies the per-period
                                  employer-contribution base so the flat-percentage employer
                                  contribution tracks the paycheck.  It replaced a constant
                                  ``employer_params["gross_biweekly"]`` that no builder emits
                                  since plan step salary:R14-b, so None no longer means a
                                  constant base -- it means a $0 basis (finding N-538,
                                  plan#1787).
                                  Every consumer configuring an employer contribution passes
                                  one.

    Returns:
        List of ProjectedBalance, one per period.

    Pylint: ``too-many-arguments`` (9/5) / ``too-many-positional-arguments``
    (9/5) -- suppressed because this module's design is "all data passed in as
    arguments": it holds no session and reads no clock.  These nine are
    genuinely distinct projection inputs that callers vary independently
    -- the what-if overlay overrides ``periodic_contribution`` and nulls
    ``contributions``; the year-end full-year path forces
    ``ytd_contributions_start`` to zero; the retirement and investment
    projections supply ``salary_basis`` and the year-end path does not -- so
    bundling them into one object would be
    stamp coupling, not a cohesive concept.  Every call site passes these
    by keyword, so the positional count is moot in practice.
    """
    inputs = _PeriodInputs(
        assumed_annual_return=Decimal(str(assumed_annual_return)),
        periodic_contribution=Decimal(str(periodic_contribution)),
        employer_params=employer_params,
        annual_contribution_limit=(
            Decimal(str(annual_contribution_limit))
            if annual_contribution_limit is not None
            else None
        ),
        salary_basis=salary_basis,
    )
    ytd_start = Decimal(str(ytd_contributions_start))
    state = _ProjectionState(
        current_balance=Decimal(str(current_balance)),
        ytd_contributions=ytd_start,
        remaining_limit=(
            max(inputs.annual_contribution_limit - ytd_start, ZERO)
            if inputs.annual_contribution_limit is not None
            else None
        ),
    )

    # Build the contribution lookup once: each period looks up its amount
    # by start_date, falling back to periodic_contribution.  A $0 entry is
    # an explicit "no contribution" -- distinct from a missing entry.
    contribution_lookup = _build_contribution_lookup(contributions)

    results = []
    for period in periods:
        results.append(
            _project_one_period(state, period, inputs, contribution_lookup)
        )
    return results


def reverse_project_balance(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    anchor_balance,
    assumed_annual_return,
    periods: PeriodWindow,
    periodic_contribution=ZERO,
    employer_params=None,
    annual_contribution_limit=None,
    ytd_contributions_start=ZERO,
):
    """Reverse-project investment balance backward through pay periods.

    Given the balance at the END of the last period in the list, derives
    what the balance must have been at the START of each prior period by
    inverting the forward growth formula from :func:`project_balance`:

        Forward:  end = start * (1 + rate) + contribution + employer
        Reverse:  start = (end - contribution - employer) / (1 + rate)

    The per-period employee ``contribution`` here is the annual-limit-capped
    amount, NOT the raw ``periodic_contribution``: this function recovers the
    exact per-period contribution and employer match the forward projection
    applied by REPLAYING :func:`project_balance` itself over the same periods
    with a throwaway zero opening balance, then reverse-walks the balance
    using those figures.  The cap/YTD/employer recurrence reads only the
    periodic contribution, the annual limit, and the running YTD (resetting
    at each year boundary) -- never the balance -- so the replayed schedule
    is independent of the (zero) seed, making this an exact inverse of
    ``project_balance(periodic_contribution, annual_contribution_limit,
    ytd_contributions_start)`` (with no per-period ``contributions`` list),
    within the per-period $0.01 rounding tolerance (DH-#28).  Replaying the
    real engine -- rather than re-deriving the cap here -- guarantees the
    reverse cannot diverge from the forward cap rule.

    The one forward step that is NOT invertible is the M-06
    ``max(balance, 0)`` clamp; it cannot fire while every period balance
    stays non-negative (start >= 0, rate >= 0, contributions >= 0), which
    holds for the year-end investment Jan-1 derivation this serves, so the
    inverse is exact there.

    Args:
        anchor_balance:       Decimal balance at the end of the last period.
        assumed_annual_return: Decimal annual return rate (e.g. 0.105 for 10.5%).
        periods:              The :class:`~app.services.pay_calendar.PeriodWindow`
                              to walk back through, forward-ordered by
                              construction.  The anchor_balance corresponds to
                              the end of its final period.
        periodic_contribution: Decimal employee contribution per period (the
                              forward periodic fallback, capped per period).
        employer_params:      dict for employer contribution calculation.
        annual_contribution_limit: Decimal annual cap, or ``None`` for accounts
                              with no IRS limit (uncapped).  ``None`` reproduces
                              the prior uncapped behaviour.
        ytd_contributions_start: Decimal employee contributions already made in
                              the first period's calendar year BEFORE the window
                              begins.  The year-end callers pass ``ZERO`` because
                              their reverse window starts at the first period of
                              a calendar year (savings) or the user's earliest
                              period (net worth), before which no contribution
                              exists.

    Returns:
        List of ProjectedBalance in forward chronological order, one per
        period.  The start_balance of the first entry is the inferred
        balance before the first period (the "Jan 1 balance").

    Pylint: ``too-many-arguments`` (7/5) / ``too-many-positional-arguments``
    (7/5) -- suppressed for the same reason as :func:`project_balance` (which
    these now mirror so the forward and reverse share one input contract):
    these are genuinely distinct projection inputs callers vary independently,
    not a cohesive concept to bundle, and every call site passes them by
    keyword so the positional count is moot.
    """
    anchor_balance = Decimal(str(anchor_balance))
    assumed_annual_return = Decimal(str(assumed_annual_return))

    # Recover the exact per-period capped contribution, employer match, and
    # running YTD / remaining-limit the forward engine applies by replaying
    # it with a throwaway zero opening balance.  The cap/YTD/employer
    # recurrence is balance-independent, so the schedule does not depend on
    # the seed; with a non-negative balance and contributions the M-06
    # zero-clamp never fires, so the harvested figures match the real run
    # exactly (DH-#28).
    schedule = project_balance(
        current_balance=ZERO,
        assumed_annual_return=assumed_annual_return,
        periods=periods,
        periodic_contribution=periodic_contribution,
        employer_params=employer_params,
        annual_contribution_limit=annual_contribution_limit,
        ytd_contributions_start=ytd_contributions_start,
    )

    # Work backward: end_balance of each period is the start_balance of the
    # next.  For the last period, end_balance = anchor.  Each step subtracts
    # the replayed per-period contribution / employer (not a single constant).
    # Each replayed row CARRIES the period it priced (plan step C2-e), so the
    # walk reads its span off the row rather than zipping a second reversed
    # sequence beside it -- the row and the period it inverts cannot come apart.
    reversed_results = []
    end_balance = anchor_balance
    for forward_row in reversed(schedule):
        period = forward_row.period
        # Inverse of: end = start * (1 + rate) + contribution + employer
        start_balance = round_money(
            (end_balance - forward_row.contribution
             - forward_row.employer_contribution)
            / (1 + span_return_rate(
                assumed_annual_return, period.start_date, period.end_date,
            ))
        )
        start_balance = max(start_balance, ZERO)

        reversed_results.append(ProjectedBalance(
            period=period,
            start_balance=start_balance,
            # Derive growth from: end = start + growth + contribution + employer
            growth=(
                end_balance - start_balance - forward_row.contribution
                - forward_row.employer_contribution
            ),
            contribution=forward_row.contribution,
            employer_contribution=forward_row.employer_contribution,
            end_balance=end_balance,
            ytd_contributions=forward_row.ytd_contributions,
            contribution_limit_remaining=forward_row.contribution_limit_remaining,
            is_confirmed=False,
        ))

        # The start of this period is the end of the previous period.
        end_balance = start_balance

    # Return in forward chronological order.
    reversed_results.reverse()
    return reversed_results
