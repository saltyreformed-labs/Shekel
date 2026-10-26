"""
Shekel Budget App -- A loan's LOADED context and its payment history.

One account's payments, rate changes, escrow and contractual P&I, loaded once
and shared by every consumer of an amortization schedule
(:func:`load_loan_context`), plus the PRICING of that payment feed
(:func:`get_payment_history`).

**It stopped owning the payment QUERY at plan step balance:X-bl-2a** (finding
**N-432**).  The rows, their chronology and their three dates are
:func:`app.services.loan_ledger.payment_installments` now; what is left here is
the amount, which is the only part of a payment record this tier is qualified to
answer.  That is what lets the schedule replay -- three dates, no amount -- reach
the feed without the amount model behind it.

Sits above :mod:`._engine_prep`, whose two corrections it applies to the feed
before returning it, and BELOW the amount model: since plan step X-au-g-2c both
functions here take the read pass's
:class:`~app.services.cash_ledger.AmountBasis` and
:func:`get_payment_history` prices its rows through it.  That import direction
is what plan step X-au-g-2a bought by moving amount rule 4's producer down into
``cash_ledger``; the argument is written once, in
:mod:`app.services.cash_ledger._loan_installment`.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.loan_params import LoanParams
from app.services import escrow_calculator
from app.services.amortization_engine import PaymentRecord, RateChangeRecord
from app.services.cash_ledger import (
    AmountBasis,
    planned_leg_contribution,
    transfer_pricing_load_options,
)
from app.services.loan_ledger import payment_installments
from app.services.loan_loaders import (
    _rate_change_records_from,
    load_escrow_lines,
    load_rate_history,
)
from app.services.row_valuation import leg_settled_contribution
from ._engine_prep import compute_contractual_pi, prepare_payments_for_engine


@dataclass
class LoanContext:
    """All context data needed for loan projection.

    Loaded once per account via load_loan_context(), shared across all
    projection consumers (loan dashboard, savings dashboard, year-end
    service, debt strategy).  Eliminates duplicated data loading logic.

    Attributes:
        payments: Prepared PaymentRecord list (escrow-subtracted,
            biweekly month-aligned).  Ready for the amortization engine.
        rate_changes: List of RateChangeRecord for the loan -- its
            origination row plus any ARM adjustments (DH-#56: every loan
            carries an origination row).  ``None`` only for a loan with
            no RateHistory rows at all, which the origination-row
            invariant forbids in production.
        escrow_components: The loan's escrow lines resolved to their in-effect
            version on today (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
            The loan CARD's escrow set: what the owner pays this month.  It is
            NOT an installment's escrow -- every tier that prices one resolves
            its own on that installment's due date (ruling **R-IJ**) -- and the
            amortization schedule stopped reading it at plan step X-au-g-2b
            (finding **N-410**).
        monthly_escrow: The sum of ``escrow_components``: the card's monthly
            escrow figure, on the same footing and with the same limit.
        contractual_pi: Standard monthly P&I payment (no escrow), resolved at
            ``date.today()``.  **The escrow-subtraction threshold, and it is a
            fence rather than a figure** -- see
            :func:`._engine_prep.prepare_payments_for_engine`, whose floor
            cannot tell a payment carrying no escrow from an underpaying PITI
            payment.  Finding **N-409** is owned by plan step
            ``balance:X-au-g-2c-3``, whose ruling deletes the floor by charging
            escrow ONCE PER INSTALLMENT in both producers (developer,
            2026-09-01).  Two earlier remedies were measured wrong first:
            re-keying this to the installment (tried at X-au-g-2b, a
            REGRESSION) and expecting the routing to make the subtraction an
            identity (X-au-g-2c-1 showed the feed carries settled, manual and
            non-payment rows over which no identity holds).
        rate_history: RateHistory ORM objects for rate display.  Carries
            the origination row for every loan plus any ARM adjustments;
            the loan dashboard shows the table only for ARM loans.
        escrow_lines: The account's escrow LINES with their full version
            history (:class:`~app.models.escrow_line.EscrowLine`), the raw
            source ``escrow_components`` is resolved from.  Kept so the loan
            dashboard builds the escrow card's version drawer
            (:func:`app.services.escrow_calculator.build_escrow_card`) off the
            same load, rather than re-querying the lines.
    """

    payments: list[PaymentRecord]
    rate_changes: list[RateChangeRecord] | None
    escrow_components: list  # list[ResolvedEscrowLine]
    monthly_escrow: Decimal
    contractual_pi: Decimal
    rate_history: list = field(default_factory=list)  # list[RateHistory]
    escrow_lines: list = field(default_factory=list)  # list[EscrowLine]


def load_loan_context(
    account_id: int,
    basis: AmountBasis | None,
    loan_params: LoanParams,
) -> LoanContext:
    """Load and prepare all context data for a loan account.

    Consolidates the data loading pattern repeated in loan routes,
    savings dashboard, year-end service, and debt strategy: payment
    history retrieval, escrow loading, payment preparation (escrow
    subtraction + biweekly redistribution), and rate change loading
    for ARM loans.

    This is a pure data-loading function -- no Flask request/session
    imports.  Callers pass the read pass explicitly.

    **It takes the read pass's AMOUNT BASIS where it took a scenario id, and
    that is one parameter rather than two** (plan step X-au-g-2c).  The basis
    STATES its scenario (:attr:`~app.services.cash_ledger.AmountBasis.scenario_id`),
    so the payment query is scoped from the same object that prices the rows it
    returns and the two cannot name different scenarios -- which is the mistake
    :func:`~app.services.cash_ledger.resolve_transaction_amount` refuses a row
    for, and which two arguments here could have stated.

    ``None`` is the DEGRADED state and keeps its meaning exactly: an owner with
    no baseline scenario loads no payments, and the loan's CONTRACT terms still
    resolve, which is what keeps escrow and rate editing working for them (plan
    step C8e, ruling **R-BX**).  A read pass spells it
    :meth:`~app.services.balance_at.BalanceContext.amounts_or_none`.

    Args:
        account_id: The loan account ID.
        basis: The read pass's :class:`~app.services.cash_ledger.AmountBasis`,
            which both scopes the payment history to its scenario and prices the
            rows it returns.  ``None`` means no payments are loaded (empty
            list) -- the no-baseline state, never a caller declining to price.
        loan_params: LoanParams model instance for the account.

    Returns:
        LoanContext with all data needed for amortization projection.
    """
    # Escrow -- loaded first because payment preparation needs the LINES.
    # ``escrow_components`` / ``monthly_escrow`` are a separate, CARD-only
    # resolution of those same lines on today, and the distinction is ruling
    # R-IJ's: what the owner pays this month is a today question, what an
    # installment costs is not.  Every consumer that prices an installment
    # takes ``escrow_lines`` and resolves its own (``prepare_payments_for_engine``
    # per payment, ``build_schedule_context`` per row).  ``monthly_escrow``
    # equals ``escrow_monthly_as_of(escrow_lines, today)`` by construction.
    escrow_lines = load_escrow_lines(account_id)
    escrow_components = escrow_calculator.resolve_active_lines(
        escrow_lines, date.today(),
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(
        escrow_components,
    )

    # Rate history for EVERY loan -- needed BEFORE contractual_pi so
    # the rate (origination plus any ARM adjustments) factors into the
    # SSOT monthly_payment.  DH-#56 retired LoanParams.interest_rate, so
    # the loan's base / period-0 rate now lives in its origination
    # RateHistory row; every loan carries one (create_params seeds it on
    # setup; the DH-#56 migration backfilled pre-existing loans).  The
    # load is therefore no longer ARM-gated -- a fixed-rate loan resolves
    # its single rate period from its one origination row.  The raw rows
    # are kept for the ``rate_history`` display field; the feed is the
    # same rows mapped for the engine.
    rate_history_records = load_rate_history(account_id)
    rate_changes = _rate_change_records_from(rate_history_records)

    # Payment history from the transfers into the loan.
    raw_payments = (
        get_payment_history(account_id, basis, loan_params)
        if basis is not None else []
    )

    # Prepare: subtract escrow and fix biweekly month overlaps.  The
    # ARM-aware contractual_pi makes the escrow-subtraction threshold match
    # LoanState.monthly_payment -- the SSOT property the user called out (P&I,
    # escrow, and monthly payment numbers must be the same across the loan
    # card, the schedule's projected rows, and the prepared-payment net
    # amount).
    #
    # **This resolves at the READ DATE while the escrow beside it resolves per
    # installment, and plan step X-au-g-2b MEASURED that re-keying it is a
    # REGRESSION rather than the fix finding N-409 assumed** (2026-09-01).  The
    # subtraction is ``amount - min(escrow, amount - contractual_pi)``, whose
    # answer equals the truth ``amount - escrow`` only while
    # ``contractual_pi <= amount - escrow``.  Raising the threshold to the
    # installment's own period pushes it ABOVE that after an upward ARM recast
    # the owner has not yet matched, and the escrow subtraction is then skipped
    # entirely: measured on the production Mortgage with one recorded recast,
    # ``$216.37`` a month of escrow booked as PRINCIPAL and ``$10,162.94`` of
    # lifetime interest understated -- the optimistic direction.  The floor is
    # the defect, not its date: it cannot tell a payment carrying NO escrow
    # from an underpaying PITI payment, and no choice of date can.
    #
    # **N-409's remedy was RULED on 2026-09-01 and it is not the one this
    # comment first named.**  It said routing the feed through the amount
    # resolver would make ``amount - escrow == period_pi(due)`` an IDENTITY,
    # after which the floor is a provable no-op.  That was measured FALSE at
    # plan step X-au-g-2c-1, which did the routing: the feed also carries
    # SETTLED payments (worth what the bank took), MANUAL-mode payments, a
    # standing extra, and plain transfers into the loan that are not loan
    # payments at all.  No identity holds over that set.
    #
    # What the floor actually is: a SECOND allocation rule for a question
    # ``app.utils.money.apply_payment_cash`` already answers, and the two
    # DISAGREE -- that one subtracts the full escrow and lets principal go
    # negative (plan D5, "surfaced, never clamped").  On a ``$1,700.00``
    # payment against this loan's ``$1,910.95`` installment the floor reports
    # ``$1,293.96``, exactly the contractual P&I, so a short payment reads as
    # on schedule and the ``$210.95`` shortfall vanishes in the OPTIMISTIC
    # direction.  The developer's ruling: escrow is charged ONCE PER
    # INSTALLMENT rather than once per payment, in BOTH producers, which
    # deletes this floor.  Plan step ``balance:X-au-g-2c-3`` owns it.
    contractual_pi = compute_contractual_pi(
        loan_params, rate_changes, date.today(),
    )
    payments = prepare_payments_for_engine(
        raw_payments, loan_params.payment_day,
        escrow_lines, contractual_pi,
    )

    return LoanContext(
        payments=payments,
        rate_changes=rate_changes,
        escrow_components=escrow_components,
        monthly_escrow=monthly_escrow,
        contractual_pi=contractual_pi,
        rate_history=rate_history_records,
        escrow_lines=escrow_lines,
    )


def get_payment_history(
    account_id: int, basis: AmountBasis, params: LoanParams,
) -> list[PaymentRecord]:
    """Price a debt account's payment installments into the engine's feed.

    Returns PaymentRecord instances for every non-deleted, non-excluded
    payment into the given account in the basis's scenario, each the leg of
    its transfer: a settled one at what it RECORDED, and a still-projected
    one at what its parent resolves to (plan step **balance:X-bi-6a**, ruling
    **R-BAL13**, for the projected half; plan step **balance:X-bi-6-4b** for
    the settled half, which was the income shadow row until then).

    **Two valuations, one per half, and the split is the record/plan split
    itself.**  A settled leg is worth what moved, which
    :func:`~app.services.row_valuation.leg_settled_contribution` answers from
    its record without reaching the amount model; a projected leg is worth its
    parent's resolved amount,
    :func:`~app.services.cash_ledger.planned_leg_contribution`.  WHICH half an
    installment is in is its dates' to say
    (:attr:`~app.services.amortization_engine.PaymentDates.is_confirmed`,
    true exactly for the producer's settled half), never a re-reading of the
    status column.  NOT :func:`~app.services.cash_ledger.leg_contribution_of`
    over both: a still-Projected transfer whose side's money has MOVED is
    settled (ruling **R-BAL140**), and that function would price it at its
    plan.  The settled half therefore states NO pricing load -- nothing on
    that path reads a relationship -- and the projected half states the
    transfer's own.

    **It is the JOIN of two producers since plan step balance:X-bl-2a, and owns
    neither** (finding **N-432**).  The rows, their order and their three dates
    are :func:`app.services.loan_ledger.payment_installments`; the figures are
    :func:`~app.services.row_valuation.leg_settled_contribution` and
    :func:`~app.services.cash_ledger.planned_leg_contribution`, one per half.
    What is left here is the pairing.  Splitting it that way is what lets a
    consumer that needs only the chronology -- the schedule replay, which
    reads three dates and no amount
    -- take the installments alone: this function's import closure is the amount
    model's -- **102 modules here, against the 46 the replay's own tier needs**
    (2026-09-09; the metric and its history are stated in
    :mod:`app.services.loan_ledger._installments`).  Plan step **X-bl-2b** moved
    the reconciliation oracle's independent reference, and five other suite
    copies of the un-seeded replay, off this door, so none of those 56 extra
    modules is one they have to be right about any more.

    **That split also deletes the SECOND producer of a loan's settled history.**
    This function read the status column itself (``txn.status.is_settled``) while
    :func:`app.services.loan_loaders.settled_income_legs` -- which calls
    itself the project's single such derivation -- filtered on
    ``settled_status_ids()``.  The two agreed by a pinned parity, which is rule
    14's tell rather than its answer; the installment producer reads that loader
    and the projected one, so settled-ness is decided by which set a row arrived
    in and this file states no rule about it at all.

    **It prices its rows through the AMOUNT MODEL, and routing this ONE reader
    is what finding N-266(a) was** (plan step X-au-g-2c).  Every other reader of
    a loan payment already went through the resolver; this one read the
    accessor now called
    :func:`~app.services.row_valuation.settled_contribution` -- then
    ``owned_contribution``, a name asserting the row owned its figure -- and
    that accessor REFUSED a row whose
    plan is DERIVED.  So the loan-side INCOME leg could not be declared derived
    while this call stood -- not because anything was circular, but because one
    reader had never been routed.  The bound was deleted rather than worked
    around: this asked
    :func:`~app.services.cash_ledger.contributions_by_id`, which answers a
    derived row from its producer and an OWN row from the plan column that
    accessor then read.  Since plan step balance:X-bi-6a that question is the
    projected half's alone, asked of each leg's parent through
    :func:`~app.services.cash_ledger.planned_leg_contribution`.

    **It is BYTE-IDENTICAL on every row that exists today, and that is a
    measurement rather than an expectation.**  Both accessors gate on
    :func:`~app.services.row_valuation.fixed_contribution` first -- ``0`` for a
    row that does not contribute, the SETTLEMENT for a row whose money has moved
    -- so they could differ only on an unsettled row, where one read the row's
    own column and the other dispatches.  A row carrying no
    ``amount_source_id`` dispatches to ``AmountRule.OWN``, whose answer is that
    same column read.  *Both sides CALLED ``owned_amount`` until plan step X-bu
    deleted that accessor, and the identity was STRUCTURAL then -- one function,
    two callers.  X-bu made it two textual copies over a shared leaf, a cost it
    took knowingly and named; plan step **X-bx** discharged it by deleting the
    accessor's copy outright, so the amount model's own arm is the one
    statement again and the leaf is private to it (``_amount_source
    ._own_figure``).  The unsettled row this paragraph turns on no longer has
    two answers to compare: the accessor REFUSES it and only
    ``contributions_by_id`` answers.*  Measured against production 2026-09-01 (stamp
    ``a4c6f1d92b73``): **all 58 loan-side income shadows** -- 29 Mortgage, 29
    Van Loan -- and all 175 transfers carry ``amount_source_id IS NULL``, so
    every row in this feed takes that arm.  What changes is only what a row the
    NEXT leaf declares derived does here: it resolves, where it used to raise.

    **One ORDERING is different and it is stated rather than left to be met.**
    Dating now happens WHOLLY BEFORE pricing, because the installment producer
    runs first.  On a feed carrying BOTH a row with a broken settle day and a
    row the amount model refuses, the exception a caller sees is
    ``UndatedSettleError`` whichever comes first in the feed.  Plan step
    X-au-g-2c had flipped that to ``AmountUnresolvable`` by pricing the whole
    feed before dating any of it, and X-bl-2a flips it back.  Both are loud, both
    are named below, and neither is recoverable -- but "byte identical" is a
    claim about VALUES, and this is the one place it is not also a claim about
    which refusal arrives first.

    **It asked the BATCH over the settled half until plan step
    balance:X-bi-6-4b**, because every other reader of a ROW set took
    ``contributions_by_id`` and a figure could not then differ by which caller
    asked.  The settled half is legs since, valued by the ONE per-leg accessor
    every leg reader asks (the Spending report's, the savings metric's), and
    the projected half has been per leg since X-bi-6a; what stops 29 projected
    legs resolving the same loan 29 times is the memo on the BASIS
    (``LoanPricing._loan``), which a batch over the same basis gets too.

    **There is no Decimal coercion below, and its removal is part of the
    route.**  The line here read *"Defensive: ensure Decimal even if the stored
    column somehow yields a non-Decimal"* -- padding around a raw column read.
    The amount model is TOTAL in its answer: every rule returns a ``Decimal`` or
    raises, and the OWN rule's leaf (``cash_ledger._amount_source._own_figure``,
    which plan step X-bx moved there from ``row_valuation``) refuses a missing
    figure rather than substituting one.  A coercion after it would convert a
    state the model refuses into a silent number.

    **That is not finding N-259**, which was a WRITE-BACK cycle one layer up and
    is CLOSED: a settle used to refresh the amount, so a settle / revert /
    settle compounded the standing extra.  Plan step ``balance:X-au-c3``
    (`3d1379d1`) made a settle RECORD what moved instead, so that compounding is
    no longer reproducible.  It is named only because conflating the two is what
    kept N-266(a)'s bound alive as a "cycle" long after the path was deleted.

    Each record carries all three of a loan payment's dates as the ONE value
    that holds them (:class:`~app.services.amortization_engine.PaymentDates`),
    taken WHOLE off its
    :class:`~app.services.loan_ledger.PaymentInstallment` and derived nowhere
    near here: ``period_start`` is the pay-period start (the funding basis),
    ``due_date`` is its own due date, from the ONE derivation the
    genesis write walk also uses
    (:func:`app.services.loan_loaders.loan_payment_due_date`), and
    ``settled_on`` is the day the cash moved, from the ONE derivation the
    genesis fold dates that payment's principal by
    (:func:`app.services.loan_ledger.payment_visible_on`).  *This copied the
    three fields ACROSS -- from a* ``period_start`` *to a* ``payment_date``,
    *two names for one fact -- until plan step* **balance:X-bl-2b** *made both
    types compose the dates.*

    The ``due_date`` here is the payment's OWN due date, never the schedule
    slot :func:`app.services.amortization_engine.schedule_dates` may invent for it: the
    slot is assigned by :func:`._engine_prep.prepare_payments_for_engine`, after
    the escrow subtraction has keyed on the real one.

    **Deriving the DUE date or the CASH day from the pay period has cost a
    defect each** -- the first mis-dated a late payment to the following
    month, the second was finding **N-187**, where an early payment was history
    to the ledger and a plan to the resolver at the same instant.  The funding
    basis has cost none: it decides only the replay's rate lookup, which finding
    **N-36** records as deliberate and step X-n owns.

    **Status and settle day are arbitrated ONE TIER DOWN, and since plan step
    balance:X-bl-2a they are arbitrated once rather than agreeably twice.**
    :func:`app.services.loan_ledger.payment_installments` reads the fold's own
    settled set, then REQUIRES the day of every record that exists -- a
    ``$0.00`` close has none and is dated by its installment (ruling
    **R-BAL139**) -- because
    :func:`~app.services.loan_ledger.payment_visible_on` refuses a settled
    record carrying none rather than inventing one (through
    :func:`~app.utils.balance_predicates.require_settled_day`, the one refusal
    of a missing day, since leaf balance:X-bi-6-4d-1; through the row accessor
    :func:`~app.utils.balance_predicates.settled_day` until then).  A payment
    broken the other way -- its transfer Projected
    while its covering movement still carries a day, which only a seam bypass
    can produce -- is a payment whose money MOVED (ruling **R-BAL140**, plan
    step balance:X-bi-6-4b): it arrives in the SETTLED half, dated and valued
    by that movement, as the cash fold counts it.  Until that step the shadow's
    own status decided, and a Projected row's stale day was never read.
    ``PaymentRecord.is_confirmed`` is the day's presence.  What this file used to hold
    was a second reading of the status column beside the loader's, and its
    defence was that the two ran in the same ORDER -- a maintenance contract
    where there is now one producer.

    Args:
        account_id: The debt account receiving payments.
        basis: The read pass's :class:`~app.services.cash_ledger.AmountBasis`.
            It SCOPES the query (``basis.scenario_id``) as well as pricing the
            rows, so the feed and its figures cannot come from two scenarios --
            the pairing ``resolve_transaction_amount`` refuses a row for, made
            unconstructible here rather than checked.
        params: The loan's :class:`~app.models.loan_params.LoanParams`, handed
            to :func:`app.services.loan_ledger.payment_installments`, which
            reads its ``payment_day`` and ``origination_date`` to date each
            payment (ruling **R-R107**).

    Returns:
        List of PaymentRecord instances sorted by payment date
        (ascending).  Empty list if no qualifying transactions exist.

    Raises:
        AmountUnresolvable: From the resolver, for a row whose rule cannot
            answer -- a DERIVE-mode payment whose loan will not resolve, or a
            row whose ownership CHECK is broken.  A refusal is never a fallback
            (see :mod:`app.services.cash_ledger._amount_source`).
        UndatedSettleError: When a settled payment's record carries no
            ``settled_on`` -- the settled-iff-dated invariant is broken on that
            payment, and dating it by a fallback would put real money on a day
            nothing recorded (see :func:`app.services.loan_ledger.payment_visible_on`,
            which names the transfer).
            **The mark-paid door no longer reaches this, and that is a
            refusal plan step X-au-g-1 DELETED rather than an edge it kept.**
            The paragraph here used to record the door arriving through
            :meth:`~app.services.cash_ledger.LoanPricing.live_cash` ->
            :func:`load_loan_context`; that edge is gone with the pricing cycle,
            so a settle now reaches pricing via ``transfer_service._settle`` ->
            ``amount_basis`` -> amount rule 4 and never loads a payment history.
            *That path named ``live_cash`` until plan step X-au-g-2c-2 deleted
            it with the read-time repair; the conclusion -- no payment history
            on the settle path -- is unchanged.*
            The three remaining callers of :func:`load_loan_context` are
            ``routes/loan/_helpers.py`` (twice) and
            ``balance_at/_resolution.py``.

            The payment is still refused by the loan fold at
            ``loan_ledger._visible``, so no loan carrying such a payment is
            silently healthy; what changed is only WHICH door reports it
            first.  (This paragraph also named a refusal at
            ``cash_ledger._events`` until leaf balance:X-bi-6-4d-1, which that
            module does not make: it refuses no un-dated movement, and folds
            an un-dated covering record in neither half.)
            Stated rather than quietly dropped, because a refusal that stops
            being reachable from a door is a behaviour change even when every
            other reader still refuses.
    """
    # The DATES, from the one producer that answers them (plan step
    # balance:X-bl-2a).  It owns the query, the chronological order, and the
    # three dates; this function's whole remaining job is to price the rows it
    # hands back and pair each figure with its installment.
    installments = payment_installments(
        account_id, basis.scenario_id, params,
        # This function PRICES every payment it is handed, so it is this call
        # that states the eager sets (plan step balance:X-bl-2a).  The settled
        # half is valued from its RECORD and walks no relationship, so it
        # states none; the projected half walks its parent's pricing graph.
        options=(),
        leg_options=transfer_pricing_load_options(),
    )

    return [
        PaymentRecord(
            dates=installment.dates,
            amount=(
                leg_settled_contribution(installment.source)
                if installment.dates.is_confirmed
                else planned_leg_contribution(installment.source, basis)
            ),
        )
        for installment in installments
    ]
