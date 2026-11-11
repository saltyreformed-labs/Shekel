"""Loan-resolver shared foundation: rate periods, anchor selection, replay.

The pure machinery both the resolver state (:mod:`._state`) and the payoff
composer (:mod:`._payoff`) build on: constructing a loan's rate periods from
its params + rate-change feed, selecting the governing anchor event, replaying
confirmed payments forward from it, and the :class:`LoanInputs` bundle that
carries a loan's loaded data through every entry point.

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import (
    AmortizationRow,
    PaymentDates,
    PaymentRecord,
    PeriodTerms,
    RateChangeRecord,
)
from app.services.rate_period_engine import (
    BalanceAnchor,
    LoanTerms,
    RatePeriod,
    ScheduleReplay,
    build_rate_periods,
    replay_schedule,
)
from app.utils.dates import anchor_chronology_key

if TYPE_CHECKING:
    # Typing-only import: keeps this resolver a runtime model-free leaf
    # (no Flask, no db.session, duck-typed ``loan_params``) while still
    # giving ``LoanInputs.loan_params`` a precise hint.  ``from __future__
    # import annotations`` makes the reference a lazy string, so the model
    # layer is never imported at runtime.
    from app.models.loan_params import LoanParams

ZERO_MONEY = Decimal("0.00")


def _origination_rate(rate_changes: list | None) -> Decimal:
    """Return the loan's origination (period-0) rate from the rate-change feed.

    DH-#56 retired ``LoanParams.interest_rate``, so the engine's base /
    period-0 rate is now the earliest :class:`RateChangeRecord` in the
    feed -- the origination :class:`RateHistory` row every loan carries
    (``create_params`` seeds it on setup; the DH-#56 migration backfilled
    every pre-existing loan).  Because that origination-dated row always
    covers period 0 via :func:`_rate_at_date`, this value is what
    :func:`build_rate_periods` resolves for period 0 anyway; populating
    ``LoanTerms.base_rate`` from it keeps the (now-unreached) fallback
    consistent with the feed.

    Raises:
        ValueError: When ``rate_changes`` is empty/``None``.  Every loan
            must carry an origination :class:`RateHistory` row, so an
            empty feed is a data-invariant violation the caller must
            surface loudly rather than paper over with a silent default.
    """
    if not rate_changes:
        raise ValueError(
            "loan rate resolution requires at least one RateHistory row "
            "(the origination rate) -- received an empty rate-change "
            "feed.  create_params seeds the origination row on setup and "
            "the DH-#56 migration backfilled every pre-existing loan; an "
            "empty feed means that invariant was violated."
        )
    earliest = min(rate_changes, key=lambda change: change.effective_date)
    return Decimal(str(earliest.interest_rate))


def accrual_convention(loan_params: LoanParams) -> InterestAccrualConventionEnum:
    """Return how *loan_params*'s note accrues interest, as the enum member.

    THE one reading of ``LoanParams.interest_accrual_convention_id`` (plan step
    recurrence:R16-d, finding **D52**): the stored id becomes the
    :class:`~app.enums.InterestAccrualConventionEnum` member the one interest
    producer (:func:`app.utils.money.installment_interest`) takes.  The
    resolver's terms read it here (:func:`_loan_terms_from`), and so do the
    two readers that hold a loan's params outside the resolver's periods --
    the payoff strategy's terms
    (:func:`app.services.balance_at.loan_terms`) and the refinance
    calculator's hypothetical loan -- so the conversion is spelled once.

    **An id that names no member is refused HERE**, where it is read: a
    ``LoanParams`` built in memory without the column reads ``None``, and
    handed on it would fail only at a walk's first charge, or never on a path
    that charges nothing.  A row the database holds cannot reach the refusal
    (the column is NOT NULL under a foreign key onto the list).  A
    ``KeyError``, the :func:`app.ref_cache.ledger_class_is_debit_normal`
    precedent for a lookup that drives logic, and not a ``ValueError``, which
    the debt-strategy page's handlers turn into a form message.

    Args:
        loan_params: A LoanParams-shaped object exposing
            ``interest_accrual_convention_id``.

    Returns:
        The member the id names.

    Raises:
        KeyError: When the id names no member: ``None``, or an id the ref
            cache does not hold.  The message names the id read.
    """
    convention_id = loan_params.interest_accrual_convention_id
    member = ref_cache.interest_accrual_convention_member(convention_id)
    if member is None:
        raise KeyError(
            f"interest_accrual_convention_id {convention_id!r} names no "
            f"InterestAccrualConventionEnum member.  A loan row the database "
            f"holds cannot (NOT NULL, a foreign key onto "
            f"ref.interest_accrual_conventions), so a LoanParams built in "
            f"memory must state the column."
        )
    return member


def _loan_terms_from(loan_params, base_rate: Decimal) -> LoanTerms:
    """Build the rate-period engine's :class:`LoanTerms` from a LoanParams.

    Reads the immutable origination fields a loan's amortization is
    defined by.  ``base_rate`` is the loan's origination (period-0) rate,
    supplied by the caller from the origination :class:`RateHistory` row
    (see :func:`_origination_rate`) since DH-#56 retired the
    ``LoanParams.interest_rate`` column; the :class:`RateHistory`-layered
    rate changes override it per period.  The ARM cadence columns drive
    the fixed-rate period boundaries, and the accrual convention
    (:func:`accrual_convention`) rides every period the terms build.

    Args:
        loan_params: A LoanParams-shaped object exposing
            ``origination_date``, ``original_principal``,
            ``term_months``, ``is_arm``,
            ``arm_first_adjustment_months``,
            ``arm_adjustment_interval_months`` and
            ``interest_accrual_convention_id``.
        base_rate: The origination annual rate (decimal fraction) the
            caller resolved from the loan's earliest rate-change record.

    Returns:
        The corresponding :class:`LoanTerms`.
    """
    return LoanTerms(
        origination_date=loan_params.origination_date,
        original_principal=Decimal(str(loan_params.original_principal)),
        base_rate=base_rate,
        term_months=loan_params.term_months,
        is_arm=bool(getattr(loan_params, "is_arm", False)),
        arm_first_adjustment_months=getattr(
            loan_params, "arm_first_adjustment_months", None,
        ),
        arm_adjustment_interval_months=getattr(
            loan_params, "arm_adjustment_interval_months", None,
        ),
        convention=accrual_convention(loan_params),
    )


def _recorded_pi_from(
    rate_changes: list[RateChangeRecord] | None,
) -> dict[date, Decimal]:
    """Extract the recorded recast-P&I map from the rate-change feed.

    A rate change's ``monthly_pi`` (when present) is the lender's
    recorded recast payment for the fixed-rate period that change
    begins.  Keying by ``effective_date`` lets
    :func:`rate_period_engine.build_rate_periods` hold that exact figure
    constant for the period instead of deriving it.

    Args:
        rate_changes: Optional :class:`RateChangeRecord` list; entries
            without a ``monthly_pi`` are omitted (their period's P&I is
            derived).

    Returns:
        A ``{effective_date: monthly_pi}`` dict (empty when none recorded).
    """
    if not rate_changes:
        return {}
    return {
        change.effective_date: Decimal(str(change.monthly_pi))
        for change in rate_changes
        if change.monthly_pi is not None
    }


def resolve_periods(loan_params, rate_changes):
    """Build the loan's rate periods from its params and rate-change feed.

    One construction shared by every resolver entry point
    (:func:`._state.resolve_loan`,
    :func:`._state.compute_monthly_payment_baseline`,
    :func:`._payoff.compute_payoff_scenarios`) so the period set they read
    cannot drift apart.  Re-exported from the package so the out-of-package
    Build-Order Step 4 split walk
    (:func:`app.services.loan_ledger.compute_loan_payment_splits`) reads the
    identical period set -- its accrued-interest rate per payment is sampled
    from these very periods, so a posted loan-payment split can never disagree
    with the resolver's replayed balance on the rate in effect.

    Args:
        loan_params: The loan's :class:`LoanParams`-shaped object.
        rate_changes: Optional :class:`RateChangeRecord` feed.

    Returns:
        The ordered :class:`~app.services.rate_period_engine.RatePeriod`
        list for the loan.
    """
    return build_rate_periods(
        terms=_loan_terms_from(loan_params, _origination_rate(rate_changes)),
        rate_changes=rate_changes,
        recorded_period_pi=_recorded_pi_from(rate_changes),
    )


def _terms_from_periods(periods: list[RatePeriod]) -> list[PeriodTerms]:
    """Map the loan's rate periods onto the projection engine's terms feed.

    One :class:`~app.services.amortization_engine.PeriodTerms` per
    :class:`~app.services.rate_period_engine.RatePeriod`, carrying the
    period's start date, rate, level P&I and accrual convention verbatim -- the rate-period
    engine stays the single producer of those figures and
    ``project_forward`` consumes them unchanged, so the schedule's
    projected rows and the loan card cannot diverge (DH-#1).  The full
    period set is mapped, past periods included: a projection whose
    ``starting_date`` lags ``as_of`` (a stale anchor with no confirmed
    payments) is still governed month by month by its true periods.

    Args:
        periods: The ordered :class:`RatePeriod` list from
            :func:`resolve_periods`.

    Returns:
        The corresponding :class:`PeriodTerms` list, in the same order.
    """
    return [
        PeriodTerms(
            start_date=period.start_date,
            annual_rate=period.annual_rate,
            monthly_pi=period.period_pi,
            convention=period.convention,
        )
        for period in periods
    ]


def engine_terms(loan_params, rate_changes) -> list[PeriodTerms]:
    """Build the projection engine's terms schedule for a loan.

    Public entry point for callers OUTSIDE the resolver that drive the
    amortization engine directly (the loan route's raw target-date
    :class:`~app.services.amortization_engine.PayoffRequest`), so their
    projections read the same rate-period figures the resolver and the
    loan card do.  Resolver-internal callers map their already-built
    period list via :func:`_terms_from_periods` instead.

    Args:
        loan_params: The loan's :class:`LoanParams`-shaped object.
        rate_changes: Optional :class:`RateChangeRecord` feed (must
            contain the origination row, per :func:`_origination_rate`).

    Returns:
        The loan's full :class:`PeriodTerms` schedule.
    """
    return _terms_from_periods(resolve_periods(loan_params, rate_changes))


@dataclass(frozen=True)
class ConfirmedLedgerView:
    """A loan's genesis-ledger confirmed state: the balance AND its history rows.

    The read switch's ONE injection value (superseding the C8 scalar
    ``forward_seed_balance``): the ledger-confirmed balance as of the
    evaluation date plus the ledger-derived confirmed schedule rows that
    produced it.  Bundled -- never threaded as two parameters -- so the
    headline balance, the forward projection's seed, and the amortization
    table's confirmed rows can never desync: they either ALL come from the
    ledger (a view is supplied) or ALL come from the anchor replay (``None``),
    the same one-value-threaded-once lesson the C8 seam recorded.

    Produced only by
    :func:`app.services.balance_at.confirmed_view` (the single
    reader call site); the pure resolver consumes it blind.

    Attributes:
        balance: The record-confirmed balance owed as of the evaluation date
            (folded from the loan's walk by the seam's ``confirmed_view`` since
            plan step E1d-b; it was the posting reader's sum until then).
            Becomes the schedule composer's forward starting balance (the
            loan's displayed balance folds in the ``balance_at`` seam, plan
            step D2a).
        history_rows: The record-derived confirmed schedule rows (the seam's
            :func:`app.services.balance_at.confirmed_view`, folded from the loan's
            walk since plan step E1d-b),
            chronological, each carrying its payment's ACTUAL principal /
            interest and the real running balance.  Becomes the confirmed
            slice of every schedule surface (``LoanState.schedule``,
            ``PayoffScenarios.history_rows``).  May be empty (a configured
            loan with no confirmed payment yet).
    """

    balance: Decimal
    history_rows: list[AmortizationRow]


@dataclass(frozen=True)
class LoanInputs:
    """The loaded input data for a single loan, shared by every resolver entry point.

    Bundles the four pieces of loan data that :func:`._state.resolve_loan`
    and :func:`._payoff.compute_payoff_scenarios` both consume into one
    immutable argument.  Every caller already loads exactly these four
    together (the ``LoanParams`` row, its anchor events, and the payment +
    rate-change feeds from
    :func:`app.services.loan_payment_service.load_loan_context`), so naming
    the clump lets the two entry points share one cohesive parameter instead
    of threading the same four values by hand.  The evaluation date
    (``as_of``) is deliberately NOT bundled here -- it is the per-call
    question asked of a given loan, not part of the loan's data.

    Frozen so a caller cannot mutate a bundle another consumer is holding.

    *The parenthetical here read "the resolver passes a confirmed-only*
    ``payments`` *view to the composer this way", and no caller has done that
    for some time: the composer takes the WHOLE feed and hands the replay the
    dates settled by* ``as_of`` *(:func:`._payoff._build_forward_inputs`); the
    projected remainder is the balance seam's plan since plan step R7d-g-3.  A
    grep for a* ``dataclasses.replace`` *over this class finds none in*
    ``app/`` *or* ``tests/`` *(2026-09-09).  Corrected rather than left,
    because a stale example is read as a live contract.*

    Attributes:
        loan_params: A :class:`LoanParams`-shaped object exposing the
            origination / principal / rate / term / ARM-cadence fields
            and ``payment_day``.  Plain SQLAlchemy ``LoanParams`` rows
            work unchanged; duck-typed test fixtures work too (the type
            hint is a typing-only forward reference, not a runtime
            constraint).
        anchor_events: Non-empty list of anchor-shaped objects
            (``anchor_date``, ``anchor_balance``, ``created_at``,
            ``event_id``) -- in production the
            :class:`~app.services.loan_loaders.LoanAnchorFact`
            list from ``load_loan_anchor_facts`` (the synthesized
            origination fact plus stored true-ups), so a configured loan
            always has at least one; an empty list raises ``ValueError``
            when the governing anchor is selected.  ``event_id`` is what
            makes :func:`select_latest_anchor`'s key TOTAL -- see there.
        payments: Prepared :class:`PaymentRecord` list from
            :func:`app.services.loan_payment_service.prepare_payments_for_engine`
            (escrow subtracted, biweekly redistributed).  ``None`` or
            empty when the loan has no payment history.
        rate_changes: Optional :class:`RateChangeRecord` ARM
            rate-history.  ``None`` or empty for a fixed-rate loan.
    """

    loan_params: LoanParams
    anchor_events: list
    payments: list[PaymentRecord] | None
    rate_changes: list[RateChangeRecord] | None


def select_latest_anchor(anchor_events: list) -> object:
    """Return the anchor that governs, by ``(anchor_date, created_at, event_id)``.

    **The key is not spelled here.**  It is
    :func:`app.utils.dates.anchor_chronology_key`, the ONE definition of "which
    of a loan's assertions is later", and
    :func:`app.services.loan_loaders.load_loan_anchor_facts` sorts by the same
    function -- so the greatest element here IS the last element there, by
    construction rather than by two tuples agreeing.  That identity is what the
    fold's walk (:func:`app.services.loan_ledger.walk_loan_ledger`) depends on:
    it resets the running balance at each anchor in turn, so the anchor it
    reaches LAST must be the one this seeds from.  See that function for why all
    three terms are load-bearing; the third (``event_id``) is what plan step
    X-an-b added, closing finding **N-196**.

    **The bound is the WALK's, which takes no as-of, and NOT a reader's.**  This
    names the loan's latest anchor outright; every reader of the walk applies its
    own ``as_of`` bound afterwards
    (:func:`app.services.balance_at.confirmed_view`,
    ``loan_posting_service.loan_balance_anchor_history``), so for a PAST read
    date the last anchor a reader has applied is not necessarily this one.  That
    gap is finding **N-207** -- this resolves against the loan's latest anchor
    whatever date the pass asked about -- and it is owned by X-i2, not fixed
    here.

    **It is deliberately order-INSENSITIVE where the walk is not.**  The walk
    calls the loader itself and consumes its order; this resolver is a pure leaf
    whose contract is that the CALLER loads the data and passes it in -- and 68
    of its test call sites hand-build the list -- so it re-derives the greatest
    element rather than trusting a position.

    *A previous version of this docstring said it mirrored the ORM
    ``backref(order_by=...)`` on :class:`LoanAnchorEvent`, and that it existed
    as a package export so the Build-Order Step 4 split walk
    (``loan_ledger.compute_loan_payment_splits``) could seed from the identical
    anchor.  Both were false when X-an-b measured them: that walk resets at
    EVERY anchor from a zero seed and has never called this, and the backref was
    a third, incomplete statement of the order with no reader at all -- X-an-b
    deleted its ``order_by`` rather than growing it an ``id`` term.  A first
    draft of the REPLACEMENT then claimed the two spellings were "pinned equal by
    a test, the same treatment ``cash_ledger.resolve_anchor`` and
    ``cash_anchor_facts`` get": no such cash test exists, and an adversarial
    review caught the invented citation.  There are no longer two spellings to
    pin.*

    Args:
        anchor_events: Non-empty list of
            :class:`~app.utils.dates.DatedAssertion`-shaped objects (
            ``anchor_date`` / ``created_at`` / ``event_id``) -- in production the
            :class:`~app.services.loan_loaders.LoanAnchorFact` list from
            ``load_loan_anchor_facts``.  Any order: the greatest element is
            returned regardless, PROVIDED the keys are distinct.  They are for
            every list the loader builds (``event_id`` is a primary key), and a
            hand-built fixture that repeats one gets ``max()``'s first maximal.

    Returns:
        The single governing anchor.

    Raises:
        ValueError: If ``anchor_events`` is empty.  A configured loan's
            origination anchor is synthesized from its immutable params
            (``loan_loaders.load_loan_anchor_facts``), so an empty list
            signals the caller bypassed the shared loader -- a data
            invariant violation to surface, not silently paper over.
    """
    if not anchor_events:
        raise ValueError(
            "loan_resolver requires at least one anchor fact; a "
            "configured loan's origination anchor is synthesized from "
            "its LoanParams via loan_loaders.load_loan_anchor_facts -- "
            "received an empty list."
        )
    return max(anchor_events, key=anchor_chronology_key)


def _replay_from_anchor(
    *,
    anchor_events: list,
    periods: list[RatePeriod],
    payments: list[PaymentDates],
    payment_day: int,
    as_of: date,
) -> ScheduleReplay:
    """Replay a loan's settled payments forward from its latest anchor.

    ONE production caller: :func:`._payoff.compute_payoff_scenarios`, which
    reads the full replay -- rows, balance, next pay date, remaining months --
    as the deterministic-past slice.  Under the read switch the rows and the
    balance are the None-view FALLBACK; the starting date and remaining months
    are taken from it always.
    :func:`._state.resolve_loan` reaches this only THROUGH that composer.

    *This paragraph opened "Shared by* ``._state.resolve_loan`` *(which reads*
    ``balance_as_of`` *for the current balance)" until plan step
    **balance:X-bl-2b**.  That module neither imports this function nor names
    ``balance_as_of``, and the "current balance" it described is the*
    ``LoanState`` *field plan step D2a DELETED.  Corrected rather than carried,
    on the same ground as the two other stale claims this step fixed: a stale
    example is read as a live contract.*

    **Keyword-only, because three of its five arguments are LISTS.**
    ``anchor_events``, ``periods`` and ``payments`` are adjacent and
    type-indistinguishable at a call site, so a positional signature would let
    two of them swap silently and answer a wrong balance rather than raise.  Its
    own callee (:func:`~app.services.rate_period_engine.replay_schedule`) is
    keyword-only for the same reason.

    **It takes the five values it READS, not the :class:`LoanInputs` bundle**
    (plan step **balance:X-bl-2b**, finding **N-432**).  The bundle carries the
    loan's PRICED payment feed because the forward override needs the figures;
    this replay reads three dates per payment and no amount, so taking the
    bundle made a caller that has only the dates unable to reach it -- which is
    what put the reconciliation oracle's amount-free reference on a 101-module
    import closure against the 45 its own arithmetic needs.  Its production
    caller passes ``[payment.dates for payment in loan_inputs.payments or []]``;
    the suite's un-seeded replays pass the loader's installment dates directly.

    Only settled payments reduce the balance.  An unconfirmed payment
    is a Projected transfer the user has not yet marked received; it is
    a future commitment, not historical fact (the forward projection picks it
    up via the override map).  **Nothing is filtered here to achieve that**:
    ``replay_schedule`` owns the anchor boundary AND the as-of cap, and that cap
    (:func:`~app.services.rate_period_engine.is_confirmed_payment_eligible` ->
    :func:`app.utils.dates.has_settled_by`) answers ``False`` for a payment
    carrying no settle day, so an unsettled payment is excluded by the same
    rule that excludes one settled after ``as_of``.  This function pre-filtered
    on ``settled_on is not None`` and projected onto a ``ConfirmedPayment``
    type until plan step **balance:X-bl-2b** measured the two to be one
    predicate; both were deleted rather than kept as a second statement of it
    (``CLAUDE.md`` rule 14).

    Args:
        anchor_events: The loan's anchor facts; must be non-empty (the
            Commit-12 invariant).
        periods: The loan's ordered rate periods, built once by the
            caller via :func:`resolve_periods`.
        payments: The loan's payment feed as
            :class:`~app.services.amortization_engine.PaymentDates`, settled and
            projected alike -- the caller filters nothing.
            **Every caller reading a loan's real feed must apply
            :func:`~app.services.amortization_engine.slotted_dates` first**, so
            the due dates carry the schedule SLOT the forward override also
            plans by; the priced path applies it inside
            :func:`~app.services.loan_payment_service.prepare_payments_for_engine`
            and the amount-free path at the call.  *A feed of HAND-BUILT dates
            whose due months are already distinct needs no slotting, which is
            why the pure resolver unit tests pass one unslotted:*
            ``slotted_dates`` *returns such a feed unchanged.*
        payment_day: The loan's contractual day-of-month due day, which drives
            the forward projection's first date.
        as_of: Evaluation date; replay stops at the latest payment whose CASH
            had moved by it (plan step **X-an**; it was the pay period until
            then, which is the funding basis, not the day the money left).

    Returns:
        The :class:`~app.services.rate_period_engine.ScheduleReplay` for
        the settled-payment history through ``as_of``.

    Raises:
        ValueError: When ``anchor_events`` is empty (via
            :func:`select_latest_anchor`).
    """
    anchor = select_latest_anchor(anchor_events)
    return replay_schedule(
        periods=periods,
        anchor=BalanceAnchor(
            balance=Decimal(str(anchor.anchor_balance)),
            as_of_date=anchor.anchor_date,
        ),
        payments=payments,
        payment_day=payment_day,
        as_of=as_of,
    )
