"""Amortization projection primitives.

The forward half of the amortization engine: the value records
(:class:`RateChangeRecord`, :class:`PeriodTerms`, :class:`AmortizationRow`,
:class:`AmortizationSummary`, :class:`ProjectionInputs`), the standard
payment formula and :func:`project_forward` itself.  It writes no date
arithmetic of its own: a projected row's date is the one clamp's
(:func:`app.utils.dates.clamped_day`), since plan step recurrence:R25 deleted
this module's month step and clamp.  *The schedule-slot helpers
(``schedule_dates``, ``slotted_dates``) lived here until plan task plan#3389
deleted them with the payment-dates chain they served (plan step
recurrence:R16-e).*
Pure functions, no database access -- operates only on values passed in.

Per-month rate AND contractual P&I come from the projection's
``terms_schedule`` (:class:`PeriodTerms` entries, mapped 1:1 from the
rate-period engine's :class:`~app.services.rate_period_engine.RatePeriod`
set by the loan resolver), so the schedule's projected rows read the
SAME single source of truth the loan card displays -- a recorded recast
(``RateHistory.monthly_pi``) or the schedule-derived level payment.
The projection never re-amortizes a payment from its own what-if
balance (DH-#1: that re-derivation made the schedule diverge from the
card for any recast the replay had not yet consumed).

The payoff question layer (:mod:`._payoff`) consumes these primitives;
both are re-exported flat from the package ``__init__`` so consumers
keep importing from ``app.services.amortization_engine`` unchanged.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.enums import InterestAccrualConventionEnum
from app.services.installment_calendar import accrual_window
from app.utils.dates import clamped_day, month_ordinal, months_between
from app.utils.money import (
    MONTHS_PER_YEAR,
    apply_payment_cash,
    installment_interest,
    round_money,
)


@dataclass(frozen=True)
class RateChangeRecord:
    """A historical or scheduled rate change applied to an ARM loan.

    The transport record for a loan's rate history (one per
    ``RateHistory`` row): the rate-period engine consumes the list to
    place period boundaries and resolve each period's rate and recorded
    P&I (:func:`~app.services.rate_period_engine.build_rate_periods`),
    and the resulting periods feed projections as
    :class:`PeriodTerms`.  The projection itself no longer reads rate
    changes directly.

    Attributes:
        effective_date: The date the new rate takes effect.
        interest_rate: The new annual interest rate as a Decimal
            (e.g., Decimal("0.065") for 6.5%).  Must be >= 0.
        monthly_pi: Optional recorded recast P&I (principal + interest,
            no escrow) the lender set when this rate took effect.  The
            rate-period engine holds it constant for the period this
            change begins; ``None`` means that period's P&I is derived
            by amortization instead.  Must be > 0 when present.
    """

    effective_date: date
    interest_rate: Decimal
    monthly_pi: Decimal | None = None

    def __post_init__(self):
        """Validate rate change record fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the schedule loop.

        Raises:
            TypeError: If effective_date is not a date or interest_rate
                is not a Decimal.
            ValueError: If interest_rate is negative.
        """
        if not isinstance(self.effective_date, date):
            raise TypeError(
                f"effective_date must be a date, "
                f"got {type(self.effective_date).__name__}"
            )
        if not isinstance(self.interest_rate, Decimal):
            raise TypeError(
                f"interest_rate must be a Decimal, "
                f"got {type(self.interest_rate).__name__}"
            )
        if self.interest_rate < 0:
            raise ValueError(
                f"interest_rate must be >= 0, got {self.interest_rate}"
            )
        if self.monthly_pi is not None:
            if not isinstance(self.monthly_pi, Decimal):
                raise TypeError(
                    f"monthly_pi must be a Decimal or None, "
                    f"got {type(self.monthly_pi).__name__}"
                )
            if self.monthly_pi <= 0:
                raise ValueError(
                    f"monthly_pi must be > 0 when present, got {self.monthly_pi}"
                )


def calculate_remaining_months(
    origination_date: date, term_months: int, as_of: date | None = None,
) -> int:
    """Return how many payment months remain on a loan.

    Calculates months elapsed from *origination_date* to *as_of* (default
    today) and subtracts from *term_months*.  Never returns below 0.
    """
    if as_of is None:
        as_of = date.today()
    months_elapsed = months_between(origination_date, as_of)
    return max(0, term_months - months_elapsed)


@dataclass
class AmortizationRow:  # pylint: disable=too-many-instance-attributes
    """A single month in an amortization schedule.

    The is_confirmed flag distinguishes historical fact from projection:
    True when the row is a recorded payment off the loan's confirmed view
    (:func:`app.services.rate_period_engine.confirmed_amortization_row`),
    False when projected or computed from the contractual payment
    formula.

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed
    because this is a cohesive value record -- one amortization-table
    row -- consumed verbatim across the loan routes, year-end summary,
    and resolver.  Every field is an irreducible column of that row;
    splitting it would fragment a single domain concept and break every
    consumer for no design gain.
    """

    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal
    is_confirmed: bool = False
    interest_rate: Decimal | None = None


@dataclass
class AmortizationSummary:
    """The pay-off-sooner lever's metrics: the plan as it stands vs with an extra.

    Filled from the balance seam's fold since plan step R7d-g-3 (ruling
    **R-R88**; ``routes/loan/calculators._build_payoff_summary``).  A fold
    that never reaches zero has no payoff date, so both dates are optional,
    and so are the two savings -- a difference against a trajectory summed
    to the plan's horizon rather than to a payoff is not a saving -- and
    the partial says so.
    """
    monthly_payment: Decimal
    total_interest: Decimal
    payoff_date: date | None
    total_interest_with_extra: Decimal
    payoff_date_with_extra: date | None
    months_saved: int | None
    interest_saved: Decimal | None


def calculate_monthly_payment(
    principal: Decimal,
    annual_rate: Decimal,
    remaining_months: int,
) -> Decimal:
    """Standard amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1].

    Returns $0 if principal <= 0 or remaining_months <= 0.
    For zero-rate loans, returns principal / remaining_months.
    """
    if principal <= 0 or remaining_months <= 0:
        return Decimal("0.00")

    if annual_rate <= 0:
        return round_money(principal / remaining_months)

    monthly_rate = annual_rate / MONTHS_PER_YEAR
    factor = (1 + monthly_rate) ** remaining_months
    payment = principal * (monthly_rate * factor) / (factor - 1)
    return round_money(payment)


@dataclass(frozen=True)
class PeriodTerms:
    """The contractual terms governing one fixed-rate span of a projection.

    The projection-side mirror of
    :class:`app.services.rate_period_engine.RatePeriod`: the rate-period
    engine is the single producer of each period's rate AND level P&I
    (the recorded recast when the user supplied one, else the
    schedule-derived amortization), and the loan resolver maps every
    ``RatePeriod`` onto one ``PeriodTerms`` -- so :func:`project_forward`
    pays exactly the figures the loan card displays and the two cannot
    diverge (DH-#1).  Defined here rather than imported from the
    rate-period engine because that module already imports this package
    (the dependency points producer -> primitives).

    Attributes:
        start_date: First calendar day this entry's rate/payment govern.
            The entry governing a payment date is the LATEST one with
            ``start_date <= payment_date``; a date before every entry is
            governed by the first (mirroring
            :func:`~app.services.rate_period_engine.period_for_date`).
        annual_rate: Annual interest rate for the span (a decimal
            fraction, e.g. ``Decimal("0.06")``).  Must be >= 0.
        monthly_pi: Level P&I (principal + interest, no escrow) held
            constant for the span.  Must be >= 0 (zero only for the
            degenerate already-paid-off span).
        convention: How the loan's interest accrues
            (:class:`~app.enums.InterestAccrualConventionEnum`), mapped from
            the period's :attr:`~app.services.rate_period_engine.RatePeriod.convention`
            -- or, for the refinance calculator's hypothetical loan, the
            convention of the loan it refinances.  No default (plan step
            recurrence:R16-d).
    """

    start_date: date
    annual_rate: Decimal
    monthly_pi: Decimal
    convention: InterestAccrualConventionEnum

    def __post_init__(self):
        """Validate terms fields at construction time.

        Catches invalid data immediately rather than producing wrong
        results deep in the projection loop.

        Raises:
            TypeError: If ``start_date`` is not a date, either money
                field is not a Decimal, or ``convention`` is not an
                :class:`~app.enums.InterestAccrualConventionEnum` member
                (the stored id or ``None`` handed over by mistake).
            ValueError: If ``annual_rate`` or ``monthly_pi`` is negative.
        """
        if not isinstance(self.start_date, date):
            raise TypeError(
                f"start_date must be a date, "
                f"got {type(self.start_date).__name__}"
            )
        if not isinstance(self.annual_rate, Decimal):
            raise TypeError(
                f"annual_rate must be a Decimal, "
                f"got {type(self.annual_rate).__name__}"
            )
        if self.annual_rate < 0:
            raise ValueError(
                f"annual_rate must be >= 0, got {self.annual_rate}"
            )
        if not isinstance(self.monthly_pi, Decimal):
            raise TypeError(
                f"monthly_pi must be a Decimal, "
                f"got {type(self.monthly_pi).__name__}"
            )
        if self.monthly_pi < 0:
            raise ValueError(
                f"monthly_pi must be >= 0, got {self.monthly_pi}"
            )
        if not isinstance(self.convention, InterestAccrualConventionEnum):
            raise TypeError(
                f"convention must be an InterestAccrualConventionEnum member, "
                f"got {self.convention!r}"
            )


def _governing_terms(
    sorted_terms: list[PeriodTerms],
    payment_date: date,
) -> PeriodTerms:
    """Return the terms entry governing ``payment_date``.

    The latest entry with ``start_date <= payment_date``; the first
    entry when ``payment_date`` precedes them all.  Mirrors
    :func:`app.services.rate_period_engine.period_for_date` so the
    projection selects terms exactly the way the loan card does.

    Args:
        sorted_terms: Non-empty list of :class:`PeriodTerms` in
            ``start_date`` order.
        payment_date: The payment date for the current schedule month.

    Returns:
        The governing :class:`PeriodTerms`.
    """
    chosen = sorted_terms[0]
    for terms in sorted_terms:
        if terms.start_date <= payment_date:
            chosen = terms
        else:
            break
    return chosen


@dataclass(frozen=True)
class ProjectionInputs:
    """Immutable starting state and forward-only terms for a projection.

    Bundles the inputs that describe a loan's state at
    ``starting_date`` together with the parameters that stay constant
    across a related set of projections.  The target-date search builds
    one ``ProjectionInputs`` and runs its baseline and every bisection
    step from it varying only ``extra_monthly``, so the runs cannot
    diverge in their shared starting state -- the load-bearing
    single-source-of-truth invariant in
    :func:`~._payoff.required_extra_for_projection`.

    Attributes:
        starting_balance: Outstanding balance at ``starting_date``.
            ``<= 0`` yields an empty projection (loan already paid off).
        starting_date: The first ``payment_date`` of the projection
            (typically the replay's ``next_pay_date``).  Subsequent
            dates advance one month at a time, the day clamped to each
            month's length.
        remaining_months: Maximum number of months to project.  ``<= 0``
            yields an empty projection.
        payment_day: Day-of-month payments are due, clamped to each
            month's max days (e.g. day 31 in February).
        terms_schedule: Non-empty :class:`PeriodTerms` list -- the
            single source of each month's rate AND contractual P&I
            (see :func:`_governing_terms` for the selection rule).
            The resolver maps the loan's full
            :class:`~app.services.rate_period_engine.RatePeriod` set
            here, past periods included, so a ``starting_date`` behind
            ``as_of`` (a stale anchor) is still governed by its true
            period; entries need not be pre-sorted.
    """

    starting_balance: Decimal
    starting_date: date
    remaining_months: int
    payment_day: int
    terms_schedule: list[PeriodTerms]

    def __post_init__(self):
        """Reject an empty terms schedule at construction time.

        A projection with no terms has no payment or rate to apply --
        a caller bug that must surface loudly here, not as a wrong
        schedule downstream.

        Raises:
            ValueError: If ``terms_schedule`` is empty.
        """
        if not self.terms_schedule:
            raise ValueError(
                "terms_schedule must contain at least one PeriodTerms "
                "entry -- a projection has no payment or rate without one."
            )


def _apply_contractual_payment(
    balance: Decimal,
    interest: Decimal,
    monthly_payment: Decimal,
    extra_monthly: Decimal,
    is_last_month: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Apply one contractual (+ extra) payment; return its split.

    The remaining balance is absorbed exactly (and no extra applies)
    when the contractual principal already covers it OR this is the
    loop's last scheduled month.  Otherwise ``extra_monthly`` is clamped
    to ``[0, balance - principal_portion]`` so acceleration alone cannot
    drive the balance below zero -- which also guarantees the quantized
    new balance is non-negative without a clamp.

    Args:
        balance: Outstanding balance before this month's payment.
        interest: Period interest already computed for this month.
        monthly_payment: The month's contractual P&I (the governing
            terms entry's ``monthly_pi``).
        extra_monthly: Requested additional principal for the month.
        is_last_month: True on the loop's final scheduled month, which
            forces the remaining balance to be absorbed.

    **The split is :func:`~app.utils.money.apply_payment_cash`, the ONE
    allocation, since plan step X-au-g-2c-3a.**  This restated it inline
    before, and the restatement was forced rather than chosen: the rule lived
    in ``loan_ledger._split`` (the walk's split module, folded into
    ``loan_ledger._replay`` at plan step recurrence:R16-c-1), which this
    package sits BELOW in the import graph, so calling it was a cycle.
    ``is_last_month`` is not part of the
    allocation and stays here: it is a SCHEDULE-closing rule (the final row
    absorbs the residue), not a statement about how cash divides.

    Returns:
        ``(principal_portion, actual_payment, extra, new_balance)`` --
        Decimals; ``new_balance`` is quantized to cents and never
        negative.
    """
    parts = apply_payment_cash(monthly_payment, balance, interest, Decimal("0.00"))
    # Final row absorbs the residue: the contractual principal already
    # covers the balance, or this is the last scheduled month.
    if parts.excess > 0 or parts.balance_after <= 0 or is_last_month:
        return (
            balance,
            balance + interest,
            Decimal("0.00"),
            Decimal("0.00"),
        )
    # Cap extra so the balance cannot drop below zero from
    # acceleration alone.
    extra = min(extra_monthly, parts.balance_after)
    extra = max(extra, Decimal("0.00"))
    # extra is clamped to [0, balance_after] above, so balance_after - extra
    # is non-negative and round_money (ROUND_HALF_UP) cannot yield a
    # negative -- no clamp/fold is needed.
    new_balance = round_money(parts.balance_after - extra)
    return parts.principal, monthly_payment, extra, new_balance


def project_forward(
    inputs: ProjectionInputs,
    *,
    extra_monthly: Decimal = Decimal("0.00"),
) -> list[AmortizationRow]:
    """Pure forward projection of the CONTRACT from a known starting state.

    The forward half of the amortization-engine split (architectural
    plan: ``docs/plans/2026-05-21-amortization-engine-split-replay-projection.md``);
    pairs with the loan's confirmed view of the recorded past
    (:func:`app.services.balance_at.confirmed_view`, the fold of its one
    walk), which starts it at the first installment after the record's last
    event (:func:`app.services.loan_resolver.compute_payoff_scenarios`).  It
    paired with ``rate_period_engine.replay_schedule`` -- which superseded
    this module's original ``replay_confirmed_history`` primitive -- until
    plan step recurrence:R16-e deleted that replay.
    ``project_forward`` has no concept of history and cannot rewrite
    it -- its inputs describe a state at ``starting_date`` and a set of
    forward-only parameters (the rate/P&I terms schedule, a what-if
    extra).

    One payment path per month: the governing terms entry's ``monthly_pi``
    plus ``extra_monthly``.  Extra is clamped so it cannot push the balance
    below zero.  The final scheduled month absorbs whatever residue remains
    after the contractual P&I split, regardless of extra.

    **It walked the owner's PLAN too until plan step R7d-g-3** (ruling
    **R-R88**): a ``monthly_override`` map replaced the contractual payment
    on the months a projected transfer row covered, so the loan resolver's
    composer could draw a "committed" line.  That was a second forward walk
    beside the balance seam's plan fold (``balance_at._plan_fold``), which
    prices every definition's own occurrences and folds them with the
    period's charges; the seam's fold is the one planned walk now and this
    projection is the contract's alone.  ``extra_monthly`` stays for the
    contract-only what-if the target-date search asks
    (:func:`~._payoff.required_extra_for_projection`).

    ARM behavior: each month's rate and contractual P&I come from the
    governing ``terms_schedule`` entry (:func:`_governing_terms`), so
    the payment recasts exactly where the rate-period engine says it
    does and TO the value the rate-period engine says -- the recorded
    recast (``RateHistory.monthly_pi``) when the user supplied one,
    else the schedule-derived level payment.  The projection NEVER
    re-amortizes a payment from its own what-if balance: the
    contractual P&I is a property of the loan's rate-period structure,
    not of the balance path (the E-02 invariant -- the same reason a
    balance true-up does not move the displayed payment), and the
    prior balance-reactive re-derivation is what let the schedule
    diverge from the loan card (DH-#1).

    Every row carries ``is_confirmed=False`` because projection rows
    are not facts about the recorded past, and ``interest_rate`` is
    the governing entry's rate (the replay stamps its rows the same
    way, so schedule consumers see the rate populated consistently).

    Args:
        inputs: A :class:`ProjectionInputs` bundling the starting state
            (balance, date) and the forward-only terms (remaining
            months, payment day, the rate/P&I terms schedule) that stay
            constant across a related set of projections.  See
            :class:`ProjectionInputs` for per-field semantics.
        extra_monthly: Additional principal payment applied to EVERY
            forward month.  Clamped per month so the balance cannot drop
            below zero from extra alone.

    Returns:
        A list of ``AmortizationRow`` instances in payment-date order,
        each with ``is_confirmed=False``.  Empty when
        ``inputs.starting_balance <= 0`` or
        ``inputs.remaining_months <= 0``.

    Raises:
        Nothing.  ``PeriodTerms`` field validation and the non-empty
        ``terms_schedule`` guard happen at dataclass construction in
        the caller (:class:`ProjectionInputs` raises ``ValueError`` on
        an empty feed); this function trusts its inputs (per CLAUDE.md
        "Trust internal code and framework guarantees").
    """
    if inputs.starting_balance <= 0 or inputs.remaining_months <= 0:
        return []

    # One sort up front; _governing_terms scans in start-date order.
    terms_schedule = sorted(
        inputs.terms_schedule, key=lambda terms: terms.start_date,
    )

    # Defensive: coerce to Decimal even if the caller passes a value
    # whose representation could yield float-like surprises.
    balance = Decimal(str(inputs.starting_balance))

    # Row N falls on the payment day of the N-th month from the starting
    # date's, clamped through the ONE clamp and re-clamped from the meant
    # day every month (plan step recurrence:R25 deleted this module's own
    # copy of it), so a 31st returns after a February.
    first_month = month_ordinal(inputs.starting_date)
    rows: list[AmortizationRow] = []

    for month_num in range(1, inputs.remaining_months + 1):
        if balance <= 0:
            break
        pay_date = clamped_day(first_month + month_num - 1, inputs.payment_day)

        # Rate AND contractual P&I from the governing terms -- the
        # rate-period engine's figures, never re-derived from the
        # projection's own balance (DH-#1 / E-02).
        terms = _governing_terms(terms_schedule, pay_date)
        # The installment's own window, never one opened on the starting
        # day: row 1 falls in ``starting_date``'s month, and a window inside
        # one month charges nothing under ``MONTHLY_1_12`` (plan step
        # recurrence:R16-d).
        window_start, window_end = accrual_window(pay_date, inputs.payment_day)
        interest = installment_interest(
            balance, terms.annual_rate, window_start, window_end,
            terms.convention,
        )
        # Contractual + extra.  The final scheduled month absorbs the residue
        # regardless of extra.
        principal_portion, actual_payment, extra, balance = (
            _apply_contractual_payment(
                balance, interest, terms.monthly_pi,
                extra_monthly, month_num == inputs.remaining_months,
            )
        )

        rows.append(AmortizationRow(
            month=month_num,
            payment_date=pay_date,
            payment=round_money(actual_payment),
            principal=round_money(principal_portion),
            interest=interest,
            extra_payment=round_money(extra),
            remaining_balance=balance,
            is_confirmed=False,
            interest_rate=terms.annual_rate,
        ))

        if balance <= 0:
            break

    return rows
