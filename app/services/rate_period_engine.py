"""
Shekel Budget App -- Rate-Period Amortization Engine

Pure-function model of a loan as an ordered sequence of fixed-rate
periods.  No Flask, no ``db`` -- the caller loads the data and passes
plain values; every function returns plain data.

## Why this exists

A conventional monthly-accrual mortgage (and an ARM inside any one of
its fixed-rate periods) accrues interest as ``balance * annual_rate /
12`` and is repaid by a level payment that is fixed for the whole
period.  The level payment recasts ONLY at a rate adjustment, never
month to month, and never because the borrower prepaid or because a
displayed balance was corrected.  The earlier resolver modeled only
the first ARM window and, past it, re-amortized the payment every
month over a shrinking term -- the few-dollar "creep" the user
reported.  This module replaces that with the correct model:

1. A loan has fixed-rate periods bounded by ``origination`` then, for
   an ARM, ``origination + arm_first_adjustment_months`` and every
   ``arm_adjustment_interval_months`` after.  A fixed-rate loan is one
   period spanning the full term.
2. Each period carries the annual rate in effect at its start and a
   level P&I (principal + interest, NO escrow) held constant for the
   whole period.  The P&I is the recorded recast figure when the user
   supplied one (``RateHistory.monthly_pi``); otherwise it is derived
   by amortizing the period's contractual start balance over the term
   remaining at the period start.  Recording is required for a
   mid-life loan whose start balance is not reconstructable from the
   app's partial history -- the recorded value is on every statement.
3. The balance is NOT derived here.  Every payment is charged and
   allocated by the loan's one walk (``loan_ledger.replay_loan_events``),
   which reads each period's rate and level P&I from this module.  *Item 3
   was* ``replay_schedule`` *-- one scheduled step per confirmed payment
   from the latest anchor, ``principal = period_pi - interest`` -- until
   plan step recurrence:R16-e deleted it (ruling plan#3382, finding D60's
   third walk).*

The monetary rounding boundary is :func:`app.utils.money.round_money`
(2dp, ROUND_HALF_UP) -- the convention every hand-computed financial
test in this project assumes.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    calculate_monthly_payment,
)
from app.utils.dates import add_months, months_between
from app.utils.money import accrue_monthly_interest, round_money

ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class RatePeriod:
    """One fixed-rate period of a loan.

    Frozen because the period set is a derived snapshot consumers read
    and render; the immutability guarantees the same instance cannot be
    silently amended between the balance walk and the payment display.

    Attributes:
        index: 0-based position; period 0 always starts at origination.
        start_date: First calendar day the period's rate/payment apply.
            Period 0's ``start_date`` equals the loan's origination
            date.
        annual_rate: Annual interest rate in effect for the period (a
            decimal fraction, e.g. ``Decimal("0.06")``).
        period_pi: Level P&I held constant for the whole period -- the
            recorded recast figure when supplied, else the amortization
            of the period's contractual start balance over
            ``term_months_at_start``.  Display this as the loan's
            monthly P&I when ``as_of`` falls in this period.
        start_month_index: Whole calendar months from origination to
            ``start_date`` (period 0 is 0).
        term_months_at_start: Contractual months remaining at the
            period start (``original term - start_month_index``); the
            denominator the level payment amortizes over.
    """

    index: int
    start_date: date
    annual_rate: Decimal
    period_pi: Decimal
    start_month_index: int
    term_months_at_start: int


@dataclass(frozen=True)
class LoanTerms:
    """The immutable identity of a loan -- everything fixed at origination.

    Bundled so the period builder takes one cohesive value object rather
    than a long parameter list, and so the same identity threads through
    the resolver unchanged.  The mutable, dated facts (rate changes,
    recorded recasts, anchors, confirmed payments) are passed separately
    because they evolve over the life of the loan.

    Attributes:
        origination_date: Loan origination date (period 0 start).
        original_principal: Loan amount at origination; must be > 0.
        base_rate: Original annual rate (decimal fraction); the
            rate-lookup fallback when no rate change applies.
        term_months: Original term in months; must be > 0.
        is_arm: Whether the loan is adjustable-rate.
        arm_first_adjustment_months: Months from origination to the
            first rate adjustment (``None`` for a fixed-rate loan).
        arm_adjustment_interval_months: Months between subsequent
            adjustments (``None`` when only the first is modeled).
    """

    origination_date: date
    original_principal: Decimal
    base_rate: Decimal
    term_months: int
    is_arm: bool
    arm_first_adjustment_months: int | None
    arm_adjustment_interval_months: int | None


def payment_number(origination_date: date, payment_date: date) -> int:
    """Return the scheduled-payment number (from origination) for a payment date.

    Payment N falls N whole calendar months after origination -- the first
    contractual payment, one month after origination, is payment 1.  Used
    to number the amortization schedule CONTINUOUSLY from origination so a
    mid-life loan's rows reflect total payments made (e.g. payment 90 for a
    loan in its 90th month) rather than the projected slice restarting at 1.

    This is the same figure each confirmed row carries on its ``month``
    (:func:`confirmed_amortization_row`); exposing it lets the dashboard
    renumber the projected rows on the same scale, and the payoff composer
    count the contract's installments left from the one its line starts on.

    Args:
        origination_date: The loan's origination date.
        payment_date: The payment's date (true monthly due date).

    Returns:
        The 1-based payment number.  A payment dated in the origination
        month itself returns 0; callers display contractual schedules
        whose first row is one month after origination (payment 1).
    """
    return months_between(origination_date, payment_date)


def due_after_anchor(anchor_date: date, due_date: date) -> bool:
    """Return whether an installment due on *due_date* follows a balance assertion.

    THE post-anchor boundary, stated once: an installment due ON the date the
    balance was asserted is already inside that balance.  The forward loan
    plan applies it to every payment it would fold and every charge it would
    raise (``balance_at._plan``, ruling **R-R72**); the settled walk
    reproduces it STRUCTURALLY, resetting at each anchor after the day's
    payments (:func:`app.services.loan_ledger.replay_loan_events`).  The
    payoff composer's contract line keeps the same boundary in the calendar's
    own arithmetic rather than through this test: it starts at the first
    installment STRICTLY after the record's last event
    (:func:`app.services.installment_calendar.installment_after`, which sits
    below this module and cannot import it).  *The
    resolver's replay applied it through ``is_confirmed_payment_eligible``
    until plan step recurrence:R16-e deleted both (finding BAL-473, closed
    by that deletion).*

    Returns:
        ``True`` iff *due_date* is strictly after *anchor_date*.
    """
    return anchor_date < due_date


def _rate_at_date(
    rate_changes: list | None,
    target_date: date,
    base_rate: Decimal,
) -> Decimal:
    """Return the annual rate in effect on ``target_date``.

    The most recent rate change with ``effective_date <= target_date``,
    falling back to ``base_rate`` (the loan's original
    ``interest_rate``) when none qualifies.  Operates on the unsorted
    public :class:`RateChangeRecord` list; :func:`period_for_date` (and
    the projection engine's terms lookup that mirrors it) apply the
    same latest-at-or-before rule to the built periods.

    Args:
        rate_changes: Optional :class:`RateChangeRecord` list (unsorted).
        target_date: Date to query.
        base_rate: Fallback when no rate change applies.

    Returns:
        The applicable annual rate as a Decimal.
    """
    if not rate_changes:
        return base_rate
    applicable = base_rate
    for change in sorted(rate_changes, key=lambda r: r.effective_date):
        if change.effective_date <= target_date:
            applicable = change.interest_rate
        else:
            break
    return applicable


def _recorded_pi_at_date(
    recorded_period_pi: dict[date, Decimal] | None,
    target_date: date,
) -> Decimal | None:
    """Return the recorded recast P&I governing ``target_date``, or None.

    ``recorded_period_pi`` maps a recast effective date to the lender's
    stated P&I (from ``RateHistory.monthly_pi``).  Returns the value
    for the most recent key ``<= target_date`` so a recast recorded a
    few days off the computed period boundary still governs the period;
    ``None`` when no recorded recast applies (the period's P&I is then
    derived).

    Args:
        recorded_period_pi: Optional ``{effective_date: monthly_pi}``.
        target_date: The period start date being resolved.

    Returns:
        The governing recorded P&I, or ``None``.
    """
    if not recorded_period_pi:
        return None
    applicable: Decimal | None = None
    for effective_date in sorted(recorded_period_pi):
        if effective_date <= target_date:
            applicable = recorded_period_pi[effective_date]
        else:
            break
    return applicable


def _amortize_forward(
    balance: Decimal,
    annual_rate: Decimal,
    level_payment: Decimal,
    months: int,
) -> Decimal:
    """Walk ``months`` of level-payment amortization and return the balance.

    Pure contractual walk used to find a period's start balance from
    the prior period's start balance (so an unrecorded recast P&I can
    be derived).  Each month: ``interest = round_money(balance *
    rate/12)``, ``principal = level_payment - interest``, ``balance =
    round_money(balance - principal)``, floored at zero.  A zero rate
    accrues no interest.

    Args:
        balance: Starting balance.
        annual_rate: Annual rate (decimal fraction).
        level_payment: The period's level P&I.
        months: Number of months to walk (``<= 0`` returns ``balance``).

    Returns:
        The balance after ``months`` payments, quantized to cents.
    """
    for _ in range(max(0, months)):
        if balance <= 0:
            return ZERO_MONEY
        interest = accrue_monthly_interest(balance, annual_rate)
        principal = level_payment - interest
        if principal >= balance:
            return ZERO_MONEY
        balance = round_money(balance - principal)
        if balance < 0:
            return ZERO_MONEY
    return round_money(balance)


def _period_boundary_dates(
    terms: "LoanTerms",
    rate_changes: list | None,
) -> list[date]:
    """Return the dates (ascending) where a fixed-rate period starts.

    Always begins with the origination date.  Two sources add further
    boundaries:

    * **Recorded rate changes.**  A rate change recasts the payment, so
      each :class:`RateChangeRecord` whose ``effective_date`` is strictly
      after origination and before the term end begins a new period.
      This is what makes a loan whose rate history is recorded -- but
      whose ARM cadence columns are unset -- recast correctly at the
      adjustment instead of staying frozen at the origination payment.
    * **ARM cadence.**  For an ARM, ``arm_first_adjustment_months`` and
      ``arm_adjustment_interval_months`` project FUTURE boundaries past
      the last recorded rate change (where no rate-change row exists
      yet), so the forward schedule still recasts on schedule.

    A fixed-rate loan with no rate changes is a single period spanning
    the full term.  Boundaries are deduplicated, so a rate change that
    coincides with a cadence adjustment date counts once.

    A cadence boundary and the term's end are the origination date plus
    whole months, day-clamped (2026-01-31 + 1 month is 2026-02-28), through
    the project's one month step, :func:`app.utils.dates.add_months` -- this
    module wrote its own copy of that step and its clamp until plan step
    recurrence:R25 (ruling **R-R106**, finding **REC-546**).  The day kept is
    the ORIGINATION's, not the payment day: a boundary is when a rate takes
    effect, and a payment is priced on the period containing its
    installment (:func:`period_for_date`).

    Args:
        terms: The loan's :class:`LoanTerms`.
        rate_changes: Optional :class:`RateChangeRecord` list; each
            change's ``effective_date`` (after origination, within term)
            begins a period.

    Returns:
        Ascending, deduplicated list of boundary dates beginning with
        the origination date.
    """
    origination = terms.origination_date
    term_end = add_months(origination, terms.term_months)
    boundaries = {origination}
    for change in (rate_changes or []):
        if origination < change.effective_date < term_end:
            boundaries.add(change.effective_date)
    first = terms.arm_first_adjustment_months
    if terms.is_arm and first is not None and 0 < first < terms.term_months:
        interval = terms.arm_adjustment_interval_months
        offset = first
        while offset < terms.term_months:
            boundaries.add(add_months(origination, offset))
            if interval is None or interval <= 0:
                break
            offset += interval
    return sorted(boundaries)


def build_rate_periods(
    *,
    terms: LoanTerms,
    rate_changes: list | None,
    recorded_period_pi: dict[date, Decimal] | None,
) -> list[RatePeriod]:
    """Build the ordered fixed-rate periods spanning the whole loan term.

    Period boundaries come from the recorded rate changes (each recast
    is a boundary) and, for an ARM, the cadence
    (``arm_first_adjustment_months`` + ``arm_adjustment_interval_months``)
    for future boundaries -- see :func:`_period_boundary_dates`.  Each
    period's rate is sampled at its start via :func:`_rate_at_date`.
    Each period's level P&I is the
    recorded recast (:func:`_recorded_pi_at_date`) when supplied, else
    the amortization of the period's contractual start balance over the
    term remaining at the start.  Start balances are produced by walking
    the contractual schedule forward from ``original_principal`` period
    by period (:func:`_amortize_forward`), so a derived recast matches
    what a lender would set for an on-schedule loan; the recorded value
    overrides it where the contractual assumption does not hold (a
    mid-life loan, a prepayment).

    Pure: no I/O, deterministic for a given input tuple.

    Args:
        terms: The loan's immutable :class:`LoanTerms` (origination
            date, original principal, base rate, term, and ARM cadence).
        rate_changes: Optional :class:`RateChangeRecord` list giving the
            rate per period.
        recorded_period_pi: Optional ``{effective_date: monthly_pi}`` of
            lender-recorded recast payments.

    Returns:
        A non-empty list of :class:`RatePeriod` in start-date order.
    """
    boundaries = _period_boundary_dates(terms, rate_changes)
    periods: list[RatePeriod] = []
    balance = Decimal(str(terms.original_principal))
    for index, start_date in enumerate(boundaries):
        annual_rate = _rate_at_date(rate_changes, start_date, terms.base_rate)
        start_month_index = months_between(terms.origination_date, start_date)
        term_at_start = terms.term_months - start_month_index
        recorded = _recorded_pi_at_date(recorded_period_pi, start_date)
        if recorded is not None:
            period_pi = round_money(Decimal(str(recorded)))
        else:
            period_pi = round_money(
                calculate_monthly_payment(balance, annual_rate, term_at_start)
            )
        periods.append(RatePeriod(
            index=index,
            start_date=start_date,
            annual_rate=annual_rate,
            period_pi=period_pi,
            start_month_index=start_month_index,
            term_months_at_start=term_at_start,
        ))
        # Advance the contractual balance to the next period start so the
        # next period's derived recast amortizes the right remaining
        # balance.  Skipped after the final period.
        if index + 1 < len(boundaries):
            months_in_period = months_between(
                start_date, boundaries[index + 1],
            )
            balance = _amortize_forward(
                balance, annual_rate, period_pi, months_in_period,
            )
    return periods


def period_for_date(periods: list[RatePeriod], target: date) -> RatePeriod:
    """Return the period whose half-open span contains ``target``.

    The latest period with ``start_date <= target``; the first period
    when ``target`` precedes it (a payment dated before origination is
    governed by the origination period).

    Args:
        periods: Non-empty list from :func:`build_rate_periods`.
        target: The date to locate.

    Returns:
        The governing :class:`RatePeriod`.

    Raises:
        ValueError: If ``periods`` is empty.
    """
    if not periods:
        raise ValueError("period_for_date requires a non-empty period list.")
    chosen = periods[0]
    for period in periods:
        if period.start_date <= target:
            chosen = period
        else:
            break
    return chosen


@dataclass(frozen=True)
class ConfirmedRowInputs:
    """The inputs that position and value ONE confirmed amortization row.

    The cohesive argument bundle of :func:`confirmed_amortization_row`: the real
    economics of one settled payment (its ACTUAL ``principal`` and ``interest``),
    the installment it pays (its ``installment`` and governing ``period``), the
    loan's ``origination_date`` that numbers the row, and the running
    ``remaining_balance`` owed after it that the caller's own walk produced.
    Bundled so the shared builder the posted reader and the walk view both call
    takes ONE argument rather than six positional values.

    Attributes:
        origination_date: The loan's origination date, numbering the row
            (:func:`payment_number`).
        installment: The contractual installment this payment PAYS -- the
            row's date and number, and the date the schedule prices its escrow
            on -- NOT its settled date, and not its own due date when that
            falls off the contractual day: a payment due Mar 10 on a loan due
            the 22nd is row ``#1 Feb 22`` of a loan from Jan 22 (ruling
            **R-R109**, the caller places it with
            :func:`~app.services.installment_calendar.installment_paid_by`).
            It was the payment's due date until that ruling.
        principal: The payment's REAL principal paid down (may be NEGATIVE for an
            underpayment, plan D5).
        interest: The payment's REAL accrued interest.
        period: The :class:`RatePeriod` governing the installment -- its
            ``period_pi`` sizes the extra split, its ``annual_rate`` tags the row.
        remaining_balance: The balance owed AFTER this payment, from the caller's
            running walk (already cent-quantized).
    """

    origination_date: date
    installment: date
    principal: Decimal
    interest: Decimal
    period: RatePeriod
    remaining_balance: Decimal


def confirmed_amortization_row(row: ConfirmedRowInputs) -> AmortizationRow:
    """Build a CONFIRMED history row from a payment's ACTUAL principal and interest.

    The ONE construction of a confirmed schedule row, shared by the two producers
    that read a loan's real payment history so they could not drift on HOW a
    payment's actual economics become a row: the posted-ledger reader
    ``confirmed_loan_history_rows`` and the walk-based confirmed view
    (:func:`app.services.balance_at.confirmed_view`, plan step E1c).  Both read the
    SAME split from the SAME walk (the posted legs are a projection of it, plan
    step E1a), so their rows were byte-identical -- and extracting the emitter is
    what made that equality STRUCTURAL rather than two copies that happened to
    match, which is what let plan step E1d-b DELETE the reader and leave the view
    as this builder's SOLE caller.  It stays a named emitter because the row's
    shaping (its P&I / extra split, its numbering, its rate) is a display contract
    worth stating once.

    It takes the payment's REAL principal and interest -- the CONTRACTUAL replay
    that wrote ``principal = period_pi - interest`` instead went at plan step
    recurrence:R16-e -- and splits its P&I against the governing period's
    contractual ``period_pi`` under the schedule-row invariant
    ``principal + interest == payment + extra_payment``: the excess above
    contractual is ``extra_payment`` (an off-schedule or extra payment), the rest
    the contractual-shaped ``payment``.  So the schedule table's totals need no
    per-row special-casing, and an underpayment (``principal < 0``, plan D5) or a
    payoff overpayment surfaces in the row rather than being clamped.

    Pure: plain data in, one row out.  The caller supplies the post-payment
    ``remaining_balance`` from its own running walk, because the running balance is
    the caller's -- the posted reader prefix-sums the linked-ledger nets, the walk
    view folds the source splits -- while THIS row-shaping is identical for both.

    Args:
        row: The :class:`ConfirmedRowInputs` positioning and valuing this row.

    Returns:
        The confirmed :class:`~app.services.amortization_engine.AmortizationRow`.
    """
    extra = max(row.principal + row.interest - row.period.period_pi, ZERO_MONEY)
    return AmortizationRow(
        month=payment_number(row.origination_date, row.installment),
        payment_date=row.installment,
        payment=round_money(row.principal + row.interest - extra),
        principal=row.principal,
        interest=row.interest,
        extra_payment=round_money(extra),
        remaining_balance=row.remaining_balance,
        is_confirmed=True,
        interest_rate=row.period.annual_rate,
    )
