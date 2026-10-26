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
3. The current balance is derived by :func:`replay_schedule`, which
   advances one scheduled step per confirmed payment from the latest
   anchor: ``principal = period_pi - interest``.  The cash amount and
   escrow NEVER enter the balance, so an escrow change cannot drift it.

The monetary rounding boundary is :func:`app.utils.money.round_money`
(2dp, ROUND_HALF_UP) -- the convention every hand-computed financial
test in this project assumes.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    PaymentDates,
    calculate_monthly_payment,
)
from app.services.installment_calendar import due_in_following_month
from app.utils.dates import add_months, has_settled_by, months_between
from app.utils.money import (
    accrue_monthly_interest,
    apply_payment_cash,
    round_money,
)

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
class ScheduleReplay:
    """Result of replaying confirmed payments forward from an anchor.

    Derives each step's principal from the period's level P&I rather
    than from a per-payment cash amount.  The fields are exactly what
    a forward projection needs to pick up where the replay leaves off.

    Attributes:
        rows: One :class:`AmortizationRow` per confirmed payment
            consumed (``is_confirmed=True``), in payment-date order.
            Empty when no confirmed payment falls in
            ``(anchor_date, as_of]``.
        balance_as_of: Outstanding balance after the last consumed
            payment, quantized to cents.  Equals the anchor balance
            when no payment was consumed.
        next_pay_date: First payment date a forward projection should
            use -- the month after the last consumed payment, or the
            month after the anchor when none was consumed (day-clamped
            to ``payment_day``).
        remaining_months_as_of: Contractual months left before
            ``next_pay_date`` (``term - months consumed before it``),
            floored at 0.
        current_period: The :class:`RatePeriod` containing ``as_of`` --
            its ``annual_rate`` and ``period_pi`` are the loan's current
            rate and monthly P&I.
    """

    rows: list[AmortizationRow]
    balance_as_of: Decimal
    next_pay_date: date
    remaining_months_as_of: int
    current_period: RatePeriod


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


@dataclass(frozen=True)
class BalanceAnchor:
    """A dated balance assertion -- the starting point for a replay.

    The pure-data analogue of a ``LoanAnchorEvent``: the verified
    balance and the date it was verified always travel together, so
    they are one value object.

    Attributes:
        balance: Outstanding balance verified on ``as_of_date``.
        as_of_date: The date the balance was verified.  Confirmed
            payments at or before it are already reflected in
            ``balance`` and are not replayed again.
    """

    balance: Decimal
    as_of_date: date


def payment_number(origination_date: date, payment_date: date) -> int:
    """Return the scheduled-payment number (from origination) for a payment date.

    Payment N falls N whole calendar months after origination -- the first
    contractual payment, one month after origination, is payment 1.  Used
    to number the amortization schedule CONTINUOUSLY from origination so a
    mid-life loan's rows reflect total payments made (e.g. payment 90 for a
    loan in its 90th month) rather than the projected slice restarting at 1.

    This is the same figure the replay already stamps on each confirmed
    row's ``month``; exposing it lets the dashboard renumber the projected
    rows on the same scale.

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
    balance was asserted is already inside that balance.  The resolver's
    replay applies it through :func:`is_confirmed_payment_eligible`; the
    forward loan plan applies it to every payment it would fold and every
    charge it would raise (``balance_at._plan``, ruling **R-R72**); the
    settled walk reproduces it STRUCTURALLY, resetting at each anchor after
    the day's payments (:func:`app.services.loan_ledger.replay_loan_events`).

    Returns:
        ``True`` iff *due_date* is strictly after *anchor_date*.
    """
    return anchor_date < due_date


def is_confirmed_payment_eligible(
    *,
    settled_on: date | None,
    due_date: date,
    anchor_date: date,
    as_of: date,
) -> bool:
    """Return whether a payment falls in the post-anchor replay window.

    The single eligibility rule for "which payments has this loan actually made
    since its balance was last verified."  A payment counts iff BOTH boundaries
    clear, and each reads the date that is CORRECT for its job:

    * its ``due_date`` -- its own due date in contract time -- is strictly AFTER
      ``anchor_date`` (:func:`due_after_anchor`, the one spelling of that
      boundary): it came due after the anchor balance was verified, so it
      is not already baked into that balance; AND
    * it has HAPPENED by ``as_of``
      (:func:`app.utils.dates.has_settled_by`): its cash moved on
      or before that date, rather than being a forward projection.

    **The second term is also what excludes an UNSETTLED payment, so a caller
    hands over its whole feed and filters nothing** (plan step
    **balance:X-bl-2b**).  ``has_settled_by`` answers ``False`` for a missing
    day by its own documented contract, so a payment whose cash has not moved
    is not eligible here for the same reason a payment settled after ``as_of``
    is not: it has not happened yet.  ``replay_schedule`` pre-filtered on
    ``settled_on is not None`` through a dedicated ``ConfirmedPayment`` type
    until that step measured the two to be one predicate; the type and the
    filter were both deleted rather than kept as a second statement of it
    (``CLAUDE.md`` rule 14).

    Comparing the DUE date (not the cash day) against the anchor is what keeps a
    payment whose cash moved on or before a mid-period balance true-up but whose
    monthly installment is not due until after it (the mid-period-true-up
    subtlety).  Comparing the CASH DAY against ``as_of`` -- since plan step
    **X-an**, where it was the pay-period start -- is what keeps the replay's
    historical cut identical to the fold's, which counts a payment's principal
    from exactly that day.

    The due date is PASSED IN, never re-derived here from a pay period: it comes
    from :func:`app.services.loan_loaders.loan_payment_due_date` (the shadow's
    own stored ``due_date``), the same derivation the genesis write walk uses.
    A payment settled LATE sits in a pay period that no longer contains its due
    date, so re-deriving the due date from that period would report the NEXT
    month's installment -- and the walk and the replay would then disagree on
    which payments an anchor subsumes.

    **The genesis walk does not call this predicate, and saying it did was
    false.**  :func:`app.services.loan_ledger.compute_loan_payment_splits`
    splits EVERY settled payment with no bound at all
    (:func:`app.services.loan_loaders.settled_income_legs` states why), and
    reproduces this predicate's anchor half STRUCTURALLY instead: it walks
    anchors and payments in one chronological merge, so a payment due at or
    before an anchor is overwritten by that anchor's reset rather than filtered
    out (:func:`app.services.loan_ledger.replay_loan_events`).  The
    two are the same ``anchor_date < due_date`` boundary in two forms -- the
    walk's applied at EVERY anchor, this one at the latest only -- which is what
    they must not drift on.

    Args:
        settled_on: The civil day the payment's cash moved
            (:attr:`~app.services.amortization_engine.PaymentDates.settled_on`),
            or ``None`` for a payment that has not settled -- which this
            answers ``False`` for, per the paragraph above.
        due_date: The payment's own due date in contract time
            (:attr:`~app.services.amortization_engine.PaymentDates.due_date`).
        anchor_date: The latest balance anchor's verified date
            (``LoanAnchorEvent.anchor_date`` / ``BalanceAnchor.as_of_date``).
        as_of: The evaluation date; a payment whose cash has not moved by it is
            a projection, not history.

    Returns:
        ``True`` iff the payment is an eligible post-anchor historical payment.
    """
    return due_after_anchor(anchor_date, due_date) and has_settled_by(
        settled_on, as_of,
    )


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
    worth stating once, and the projected-row builder below is its twin.

    Unlike :func:`_replay_payment_row` (the CONTRACTUAL replay, where
    ``principal = period_pi - interest``), this takes the payment's REAL principal
    and interest and splits its P&I against the governing period's contractual
    ``period_pi`` under the schedule-row invariant
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


def _replay_payment_row(
    balance: Decimal,
    period: RatePeriod,
    pay_date: date,
    origination_date: date,
) -> AmortizationRow:
    """Apply one scheduled payment and return its row (carrying the new balance).

    Interest accrues on ``balance`` at the period rate
    (:func:`~app.utils.money.accrue_monthly_interest`); the cash -- here the
    period's contractual P&I -- is then divided against it by
    :func:`~app.utils.money.apply_payment_cash`, the ONE allocation.  A
    principal that would overrun the balance is capped so the loan closes
    exactly at zero.  The returned row's ``remaining_balance`` is the
    post-payment balance the caller carries forward.

    **It RESTATED that allocation inline until plan step X-au-g-2c-3a**, and
    the restatement was forced rather than chosen: the rule lived in
    ``loan_ledger._split`` (since folded into ``loan_ledger._replay``, which
    imports this module still), so reaching it from here was a cycle.  Moving
    the rule to :mod:`app.utils.money` -- beside the
    accrual it is always paired with, and below every walk -- is what let this
    call it.  Byte-identical, measured over 200,000 randomised trials before
    the swap.

    No escrow is charged here: this replays the CONTRACTUAL schedule, whose
    P&I is escrow-free by definition.  ``excess`` is not reported either -- a
    schedule row has no refund column, and a contractual payment that overruns
    the balance is the loan closing exactly, which ``payment`` already states.

    ``replay_schedule`` breaks on ``balance <= 0`` before reaching here, so the
    allocator's closed-loan arm is unreachable from this caller.

    Args:
        balance: Outstanding balance before this payment (> 0).
        period: The :class:`RatePeriod` governing ``pay_date``.
        pay_date: The payment's date.
        origination_date: Loan origination, for the row's month index.

    Returns:
        The :class:`AmortizationRow` for this confirmed payment.
    """
    interest = accrue_monthly_interest(balance, period.annual_rate)
    # The ONE allocation, not a fourth statement of it -- see the docstring.
    parts = apply_payment_cash(period.period_pi, balance, interest, ZERO_MONEY)
    principal = parts.principal
    payment = principal + interest
    new_balance = parts.balance_after
    return AmortizationRow(
        month=months_between(origination_date, pay_date),
        payment_date=pay_date,
        payment=round_money(payment),
        principal=round_money(principal),
        interest=interest,
        extra_payment=ZERO_MONEY,
        remaining_balance=new_balance,
        is_confirmed=True,
        interest_rate=period.annual_rate,
    )


def replay_schedule(
    *,
    periods: list[RatePeriod],
    anchor: BalanceAnchor,
    payments: list[PaymentDates],
    payment_day: int,
    as_of: date,
) -> ScheduleReplay:
    """Replay a loan's settled payments forward from the anchor along the schedule.

    Advances one scheduled step per payment that clears two eligibility
    boundaries, in due-date order (see :func:`_replay_payment_row` for the
    per-step math):

    * its ``due_date`` -- its own due date in contract time -- is strictly after
      ``anchor.as_of_date``: the payment came due after the balance was last
      verified, so it is not already baked into the anchor; and
    * its ``settled_on`` -- the day its cash moved -- is at or before ``as_of``,
      so it is historical rather than a forward projection.

    The cash amount and escrow are NOT inputs -- only the COUNT and dates
    of the eligible payments matter, so a payment that bundled escrow
    cannot over-reduce principal.  That is why this takes
    :class:`~app.services.amortization_engine.PaymentDates` and not the priced
    :class:`~app.services.amortization_engine.PaymentRecord` that composes it
    (plan step **balance:X-bl-2b**): a feed of dates is reachable without the
    amount model in scope, and an ``AmountUnresolvable`` cannot then break a
    reader that never looks at a figure.

    Three dates with distinct jobs:

    * The RATE (and therefore the interest/principal split and the running
      balance) is selected by the pay-period start, so this function's
      ``balance_as_of`` is independent of the dating choices below.
    * Each replayed ROW is dated by the payment's ``due_date``, so the
      schedule shows real statement dates and ``next_pay_date`` advances to
      the correct following month (a pay-period-start dating would print
      the biweekly date and land the projection one month early).
    * The as_of cap uses the SETTLED day (the replay-vs-projection split),
      through the one :func:`app.utils.dates.has_settled_by` predicate the
      balance seam's plan reads the same boundary through, so a payment is
      never in both halves and never dropped by disagreement -- and it is the
      same day the fold counts the payment's principal from, so the resolver and
      the ledger cannot disagree about which payments have happened (plan step
      **X-an**; the composer's own planning half, ``_build_monthly_override``,
      went at plan step R7d-g-3 when the seam's fold became the one planned
      walk).  The anchor bound below, and the payoff break in the loop, then
      drop candidates the plan does NOT take back: both are cases where the
      payment is already accounted for (subsumed by the anchor's verified
      balance, or made against a loan that owes nothing).

    Using the due date for the anchor boundary is what lets a true-up dated
    mid-pay-period (one day after a period's biweekly start but before that
    period's monthly payment is due) still replay that payment.

    All three dates are SUPPLIED
    (:class:`~app.services.amortization_engine.PaymentDates`), never derived
    from one another: a payment settled late sits in a period that no longer
    contains its due date, so deriving the due date from the period start would
    credit it to the NEXT month's installment and desync this replay from the
    genesis write walk, which reads the payment's stored due date.

    Args:
        periods: Non-empty list from :func:`build_rate_periods`.
            ``periods[0].start_date`` is the origination date and
            ``periods[0].term_months_at_start`` is the original term.
        anchor: The :class:`BalanceAnchor` to start from (the latest
            ``LoanAnchorEvent``).  Payments whose due date is at or before
            its date are already reflected in its balance and are skipped.
        payments: The loan's payment feed as
            :class:`~app.services.amortization_engine.PaymentDates`, SETTLED
            AND PROJECTED alike -- the caller filters nothing.  A payment is
            kept when its due date is after ``anchor.as_of_date`` and its cash
            moved at or before ``as_of``
            (:func:`is_confirmed_payment_eligible`), which is ``False`` for a
            payment that has not settled at all.
        payment_day: Day of month payments are due.  Drives ``next_pay_date``
            (the due dates themselves arrive on the records).
        as_of: Evaluation date; payments whose cash has not moved by it are
            not replayed.

    Returns:
        A :class:`ScheduleReplay` with the consumed rows, the balance as
        of ``as_of``, the next projection date, the remaining months,
        and the current period.

    Raises:
        ValueError: If ``periods`` is empty.
    """
    if not periods:
        raise ValueError("replay_schedule requires a non-empty period list.")

    origination_date = periods[0].start_date
    # Two different dates govern the two eligibility boundaries:
    #   * Anchor (lower) boundary -- the payment's DUE date.  A payment
    #     due after the anchor but whose cash moved on or before it must
    #     still be replayed; comparing the cash day here would strand it
    #     (the mid-period-true-up bug).
    #   * as_of (upper) cap -- the SETTLED day.  This is the
    #     replay-vs-projection split, and it is the day the fold counts the
    #     payment's principal from, so replay and ledger agree on what has
    #     happened.  The seam's plan reads the same ``has_settled_by``
    #     predicate, so a payment is never in both halves (plan step X-an,
    #     finding N-187).  It CAN be in neither -- the anchor
    #     bound above and the payoff break below drop candidates the plan does
    #     not take back, because both are already accounted for.  See the
    #     docstring; "exact complements" is the thing this is NOT.
    # Walked in DUE-date order, matching the genesis write walk's own event
    # merge, so the two consume the payments in ONE ORDER -- and only order:
    # they deliberately differ on the RATE key (see the loop below).
    eligible = sorted(
        (
            payment for payment in payments
            if is_confirmed_payment_eligible(
                settled_on=payment.settled_on,
                due_date=payment.due_date,
                anchor_date=anchor.as_of_date,
                as_of=as_of,
            )
        ),
        key=lambda payment: (payment.due_date, payment.period_start),
    )

    balance = Decimal(str(anchor.balance))
    rows: list[AmortizationRow] = []
    for payment in eligible:
        if balance <= 0:
            break
        # Rate (and therefore the interest/principal split and the running
        # balance) is selected by the pay-period start, so the replayed
        # balance is unchanged by the row's dating.  The ROW is dated by the
        # payment's due date, so the schedule shows the real statement
        # date and ``next_pay_date`` advances to the correct following
        # month rather than landing one month early.
        #
        # THIS IS A DIFFERENT RATE KEY FROM THE GENESIS WALK, deliberately
        # (finding N-36).  The walk keys the rate on the installment's DUE date
        # -- contract time, ruling D5 -- but it reads RAW payments, while this
        # replay consumes records that have been through
        # ``amortization_engine.schedule_dates``
        # (``loan_payment_service._redistribute_to_distinct_months`` until plan
        # step balance:X-bl-2a),
        # which INVENTS a due date for a payment colliding on an
        # already-allocated month.  Keying the rate on an invented date would let a schedule-
        # alignment artifact move a replayed balance, so this stays on the
        # pay-period start, which is always a fact.  Containment: this replay's
        # rows and balance are DISCARDED whenever a ``confirmed_view`` is
        # supplied (``_build_forward_inputs`` keeps only ``next_pay_date`` /
        # ``remaining_months_as_of``), which is every production read since plan
        # step E1d-b -- so the two keys can only differ on the unseeded
        # what-if path, and never inside one rendered figure.
        #
        # PLAN STEP X-an WIDENED THIS KEY'S INPUT DOMAIN and the sentence above
        # was written before it.  Eligibility used to require
        # ``period_start <= as_of``, so this was never asked about a date in the
        # read's future; now an EARLY-settled payment replays while its funding
        # period is still ahead, and on an ARM whose adjustment falls between
        # the two this prices it at the post-adjustment rate.  Contained by the
        # same discard (and ``next_pay_date`` / ``remaining_months_as_of`` are
        # rate-independent except through the payoff break above), so nothing
        # rendered moves -- but the domain is wider than "a fact at or before
        # the read", and finding N-36 owns the key.
        period = period_for_date(periods, payment.period_start)
        due_date = payment.due_date
        row = _replay_payment_row(balance, period, due_date, origination_date)
        balance = row.remaining_balance
        rows.append(row)

    if rows:
        next_pay_date = due_in_following_month(rows[-1].payment_date, payment_day)
    else:
        next_pay_date = due_in_following_month(anchor.as_of_date, payment_day)

    remaining_months_as_of = max(
        0,
        periods[0].term_months_at_start
        - max(0, months_between(origination_date, next_pay_date) - 1),
    )

    return ScheduleReplay(
        rows=rows,
        balance_as_of=round_money(balance),
        next_pay_date=next_pay_date,
        remaining_months_as_of=remaining_months_as_of,
        current_period=period_for_date(periods, as_of),
    )
