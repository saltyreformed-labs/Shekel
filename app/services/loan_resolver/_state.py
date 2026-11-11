"""Loan-resolver state: the (payment, rate, schedule, interest) producer.

:func:`resolve_loan` is the single-source-of-truth producer every loan-touching
surface reads through; :func:`compute_monthly_payment_baseline` is the
cheaper "what does the user pay each month" lookup that skips the schedule
generation.

There is deliberately no BALANCE here (plan step D2a).  ``LoanState`` carried a
``current_balance`` -- a balance-at-``as_of`` -- which made every holder of the
bundle one attribute read away from a loan balance that never passed the
``balance_at`` seam, and for a loan whose posting ledger cannot answer it fell
back to the anchor replay, which was BLIND TO MONEY (it advanced one scheduled
step per confirmed payment and discarded the cash; plan step recurrence:R16-e
deleted it).  The seam's readers fold the
loan's recorded events instead (``balance_at._fold``), so the balance a page
shows and the seed its projections start from are ONE derivation.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    RateChangeRecord,
)
from app.services.rate_period_engine import period_for_date
from app.utils.money import round_money

from ._payoff import compute_payoff_scenarios
from ._periods import (
    ZERO_MONEY,
    ConfirmedLedgerView,
    LoanInputs,
    resolve_periods,
)


@dataclass(frozen=True)
class LoanState:
    """Resolved loan state for a single ``as_of`` evaluation.

    Frozen because the resolver returns a snapshot the caller must
    not mutate.  Every consumer (loan dashboard card, /savings debt
    card, net-worth liability, debt-strategy, year-end summary) reads
    these four fields and renders them; the immutability guarantees
    the same instance cannot be silently amended between consumers.

    There is deliberately no ``current_balance`` here (plan step D2a).  A
    balance-at-``as_of`` on this bundle put a loan balance one attribute read
    from any holder with every gate silent, and for a loan whose posting
    ledger cannot answer it was the money-blind anchor replay -- while every
    displayed balance folds the loan's recorded events
    (``balance_at.positions``).  The seam's readers derive the balance from
    the fold (``balance_at._fold.fold_from_walk`` over the read pass's
    memoized walk), so the balance and its consumers cannot fork.

    Attributes:
        monthly_payment: P&I payment as of ``as_of``.  For an ARM
            inside its fixed-rate window this is held constant for
            every ``as_of`` in the window (E-02 invariant).  Outside
            the window or for a fixed-rate loan this is the
            contractual / re-amortized payment per the resolver
            algorithm in the module docstring.
        current_rate: Annual interest rate in effect on ``as_of`` (a
            decimal fraction, e.g. ``Decimal("0.06875")``) -- the
            governing rate period's rate.  The single source of truth
            for "the loan's current rate" that DH-#56 retired the
            ``LoanParams.interest_rate`` column in favour of: every
            display and money surface (loan card, /savings cards,
            debt-strategy accrual, payoff/refinance calculators) reads
            this instead of the stored column.
        schedule: Full amortization schedule, with confirmed rows
            reflecting actual paid amounts and projected rows paying
            each month's rate-period level P&I (recorded recast or
            schedule-derived -- the same figures this card displays).
            Generated once via the amortization engine; consumers
            read it without recomputing.
        total_interest: Sum of ``row.interest`` across the schedule
            (life-of-loan total).  ``Decimal("0.00")`` when the
            schedule is empty.

    There is deliberately no ``payoff_date`` here (plan step C8d).  It
    was the schedule walk's last row -- and that walk amortizes one
    contractual installment per month whether or not a payment stands
    behind it, and forces a final row at the contractual date for a
    loan paying short, so the "payoff" it reported was a property of
    the schedule rather than of the balance.  The payoff is now
    DERIVED from the fold that produces the balance itself
    (:func:`app.services.balance_at.loan_payoff_date` -- the date the
    balance reaches zero), so the payoff and the balance cannot
    disagree.  A consumer takes it from
    :attr:`~app.services.balance_at.LoanFigures.payoff_date`.
    """

    monthly_payment: Decimal
    current_rate: Decimal
    schedule: list[AmortizationRow]
    total_interest: Decimal


def compute_monthly_payment_baseline(
    loan_params,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
) -> Decimal:
    """Return the loan's current monthly P&I -- the rate-period level payment.

    Single source of truth for "what does the user pay each month"
    for a caller that needs the payment WITHOUT the full schedule
    generation :func:`resolve_loan` runs.  Returns the same value as
    ``resolve_loan(...).monthly_payment`` for the same inputs.

    **It has NO caller in ``app/`` since plan task plan#3388**, and this
    paragraph named the one it had: ``loan_payment_service
    .compute_contractual_pi``, which sized the escrow-subtraction floor's
    threshold and went with the floor (ruling **R-R93**, finding
    **N-409**).  What is left is a public, fence-ruled name with no
    production reader, as :func:`current_rate_baseline` beside it has
    been since plan step X-au-g-2b; whether the two should go is a
    decision of its own and is NOT taken here (rule 6).

    The monthly P&I is the level payment of the rate period containing
    ``as_of`` (see :func:`build_rate_periods`): held constant within the
    period and recast only at a rate adjustment.  It is independent of
    the running balance, so no anchor or payment feed is taken (the
    read-switch arc's final commit dropped the old unused
    compatibility parameters).

    Args:
        loan_params: Loan parameter object exposing the fields
            :func:`build_rate_periods` reads (origination, principal,
            base rate, term, ARM cadence).
        rate_changes: Optional ARM rate-history feeding each period's
            rate and any recorded recast P&I.  ``None`` or empty for a
            fixed-rate loan.
        as_of: Evaluation date; selects the governing rate period.

    Returns:
        Rounded Decimal monthly P&I, equal to
        ``resolve_loan(...).monthly_payment`` for the same inputs.
    """
    return period_for_date(
        resolve_periods(loan_params, rate_changes), as_of,
    ).period_pi


def current_rate_baseline(
    loan_params,
    rate_changes: list[RateChangeRecord] | None,
    as_of: date,
) -> Decimal:
    """Return the loan's current annual interest rate -- the rate-period rate.

    Single source of truth for "what rate is in effect on ``as_of``" for callers
    that need the rate WITHOUT the full schedule generation
    :func:`resolve_loan` runs.

    **It has NO caller in ``app/`` as of plan step X-au-g-2b**, and this
    paragraph named the one it had: the standalone amortization-schedule route,
    whose ARM rate column fell back to this for rows carrying no per-row rate.
    That fallback guarded a state no ``AmortizationRow`` can be in -- every
    construction sets ``interest_rate`` from a rate period -- so the step
    deleted the branch and the route's ``date.today()`` call with it.  What is
    left here is a public, fence-ruled name with no production reader; whether
    it should go is a decision on its own and is NOT taken here (rule 6).
    Returns the same value as
    ``resolve_loan(...).current_rate`` for the same inputs -- the governing rate
    period's annual rate (DH-#56: the resolver-derived rate that replaced the
    retired ``LoanParams.interest_rate`` column) -- by the same cheap rate-period
    lookup :func:`compute_monthly_payment_baseline` uses for the payment.

    Args:
        loan_params: Loan parameter object exposing the fields
            :func:`build_rate_periods` reads (origination, principal,
            base rate, term, ARM cadence).
        rate_changes: Optional ARM rate-history feeding each period's
            rate.  ``None`` or empty for a fixed-rate loan.
        as_of: Evaluation date; selects the governing rate period.

    Returns:
        The Decimal annual rate (a fraction, e.g. ``Decimal("0.06000")``),
        equal to ``resolve_loan(...).current_rate`` for the same inputs.
    """
    return period_for_date(
        resolve_periods(loan_params, rate_changes), as_of,
    ).annual_rate


def resolve_loan(
    loan_inputs: LoanInputs,
    as_of: date,
    confirmed_view: ConfirmedLedgerView | None = None,
) -> LoanState:
    """Resolve a loan to its (payment, rate, schedule, interest).

    Single-source-of-truth producer for every loan-touching surface.
    Computes the monthly payment per the ARM-window-aware rules documented at
    package scope; generates the schedule via
    :func:`._payoff.compute_payoff_scenarios` (the confirmed history plus the
    CONTRACT's forward, ``history_rows + original_forward``); derives the
    total interest from the same schedule.  **The schedule is the contract's
    since plan step R7d-g-3** (ruling **R-R88**): what the loan is projected
    to PAY is the balance seam's plan fold, and no reader takes a balance
    off this schedule.  Nor a date since plan step recurrence:R16-c-2: the
    net-worth trend's gate and the property chart's tracking start read the
    loan's recorded start instead (rulings **R-R111**, **R-R112**), and the
    one app reader left, the property chart's pre-tracking estimate, asks
    only whether it is empty (a retired loan).  The loan's BALANCE is not here
    (plan step D2a): the ``balance_at`` seam folds it from the loan's recorded
    events (see the :class:`LoanState` docstring).

    The function is pure: it takes plain data (the :class:`LoanInputs`
    bundle of model instances and plain Python lists), returns a frozen
    :class:`LoanState`, and performs no I/O.  This honors the services
    boundary so the resolver is safe to call from any layer (route,
    service, test) and produces deterministic output for a given input.

    Algorithm (see the package docstring for the full rationale):

    1. Generate the schedule via :func:`._payoff.compute_payoff_scenarios`:
       the confirmed view's rows, then the CONTRACT's remaining installments
       from the first one after the record's last event (ruling plan#3382),
       or from the loan's latest assertion when no view is supplied.  It
       replayed the payments settled by ``as_of`` to find that start until
       plan step recurrence:R16-e, and routed the projected rows through
       ``monthly_override`` and a loan-level ``extra_principal`` too until
       plan step R7d-g-3 -- a second forward walk beside the seam's plan
       fold, which paid a standing extra twice on every row-covered month.
       The resolver no longer reaches the engine directly.
    2. ``LoanState.schedule = history_rows + original_forward`` -- the
       confirmed past and the contract's forward.
    3. Compute the monthly payment per ARM-in-window vs.
       ARM-out-of-window vs. fixed-rate rules.
    4. Return the LoanState; consumers read its fields without
       recomputing.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``loan_params``, ``anchor_events``, ``rate_changes``).
            ``anchor_events`` must be non-empty where no view is supplied
            (the Commit-12 invariant); an empty list then raises a
            ValueError.
        as_of: The evaluation date.  Selects the governing rate period, and
            so the monthly payment and the current rate.
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch), or ``None``.  When supplied, its ``balance`` becomes the
            forward projection's starting balance, its ``last_recorded_on``
            the event the projection starts after, and its ``history_rows``
            the schedule's confirmed slice (threaded once to
            :func:`._payoff.compute_payoff_scenarios`) -- one bundle, so the
            schedule's history and the projection cannot desync
            off-schedule.  ``None`` seeds the projection from the loan's
            latest assertion with no history rows (no baseline scenario, or
            a read before the loan originated).

    Returns:
        A :class:`LoanState` with the four resolver fields.

    Raises:
        ValueError: When no view is supplied and
            ``loan_inputs.anchor_events`` is empty (the Commit-12 invariant
            is violated and the caller's data is bad).
    """
    periods = resolve_periods(
        loan_inputs.loan_params, loan_inputs.rate_changes,
    )

    # Schedule generation routes through the scenario composer, which reads
    # where the loan's record stands (the confirmed view, or the latest
    # assertion) and calls ``project_forward`` once for the CONTRACT's
    # forward.  ``LoanState.schedule`` is the confirmed-history rows plus
    # that contractual slice; what the loan is projected to PAY is the
    # balance seam's plan fold (plan step R7d-g-3, ruling R-R88), and no
    # reader of this schedule takes a balance off it.
    scenarios = compute_payoff_scenarios(
        loan_inputs=loan_inputs,
        confirmed_view=confirmed_view,
    )
    schedule = list(scenarios.history_rows) + list(
        scenarios.original_forward
    )

    # Monthly P&I is the current rate period's level payment, held
    # constant within the period and recast only at an adjustment
    # boundary -- independent of the anchor balance, so a balance
    # true-up never moves the displayed payment.  The same period's
    # rate is the loan's current rate (DH-#56: the resolver-derived
    # source of truth that replaced the LoanParams.interest_rate column).
    current_period = period_for_date(periods, as_of)
    monthly_payment = current_period.period_pi
    current_rate = current_period.annual_rate

    # Life-of-loan interest from the single schedule generation (DRY --
    # no second engine call).  The payoff date is NOT derived here: it
    # is a fold-to-zero over the loan's forward plan, produced by the
    # balance seam (see the :class:`LoanState` docstring, plan C8d).
    total_interest_full = sum(
        (row.interest for row in schedule), ZERO_MONEY,
    )

    return LoanState(
        monthly_payment=monthly_payment,
        current_rate=current_rate,
        schedule=schedule,
        total_interest=round_money(total_interest_full),
    )
