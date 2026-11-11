"""Loan-resolver payoff composer: the confirmed history and the CONTRACT's forward.

Reads where the loan's record stands and projects the contract's remaining
installments from there, so a loan page's x-axis, its confirmed half and its
contract-versus-plan comparison derive from one return value.

**It composed three scenarios -- Original / Committed / Accelerated -- until
plan step R7d-g-3** (ruling **R-R88**): the committed slice routed the loan's
projected transfer rows through ``project_forward``'s ``monthly_override`` and
priced the months no row covered from the contract plus ONE picked definition's
extra, and the accelerated slice added the pay-off-sooner lever's what-if on
top.  Both were a SECOND forward walk beside the balance seam's plan fold
(``balance_at._plan`` / ``_plan_fold``), which prices every definition's own
occurrences; the seam's fold is the one forward walk now, read by the loan
page through ``balance_at.loan_installments`` / ``positions`` /
``loan_what_if_owed_at_dates``.  What stays here is what the fold does not
produce: the ledger-derived confirmed rows and the pure contractual reference.

**It replayed the loan's payments to find where that reference starts, until
plan step recurrence:R16-e** (ruling plan#3382; finding D60's third walk).
``rate_period_engine.replay_schedule`` charged one contractual step per
payment RECORD from the latest anchor, and the composer kept two calendar
facts of it: the month after its last row, and the months left from there.
Two records paying one installment counted as two months, so the reference
started a month late; the loan's record states where it stands without a
walk, so it starts at the first installment after the record's last event
(:func:`_contract_start`).

Pure: no Flask, no ``db.session``; the caller loads the data and passes it in.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.amortization_engine import (
    AmortizationRow,
    ProjectionInputs,
    project_forward,
)
from app.services.installment_calendar import installment_after
from app.services.rate_period_engine import payment_number
from app.utils.money import round_money

from ._periods import (
    ConfirmedLedgerView,
    LoanInputs,
    _terms_from_periods,
    resolve_periods,
    select_latest_anchor,
)


@dataclass(frozen=True)
class PayoffScenarios:
    """The confirmed history and the contract's forward, from ONE view of the record.

    Frozen because the composer returns a snapshot the caller renders;
    every consumer reads from one instance, so the confirmed half and the
    contractual reference cannot diverge by construction.  Two of what were
    ten fields until plan step R7d-g-3 -- see the module docstring for the
    slices and metrics that moved to the balance seam's fold.

    Attributes:
        history_rows: The confirmed view's rows, from origination through
            the evaluation date, each ``is_confirmed=True``
            (:attr:`ConfirmedLedgerView.history_rows`).  Empty when the loan
            has no confirmed payment, and when no view was supplied.
        original_forward: Pure contractual amortization of the balance the
            record stands at, from the first installment after its last
            event -- no plan, no extra.  Models "what the lender would
            amortize the remaining balance to" if the user paid exactly the
            contractual P&I every month: the band chart's x-axis and the
            lever's "current plan vs. original" reference.
    """

    history_rows: list[AmortizationRow]
    original_forward: list[AmortizationRow]


@dataclass(frozen=True)
class _ProjectionPrep:
    """The record-derived inputs the payoff composer builds its result from.

    Produced once by :func:`_contract_start` so
    :func:`compute_payoff_scenarios` reads two values from one local,
    leaving the composer a thin "project, then bundle" orchestrator.

    Attributes:
        projection_inputs: The :class:`ProjectionInputs` the contractual
            forward projects from -- starting balance, date, remaining
            months, and the rate-period terms feed (each month's SSOT rate
            and contractual P&I).
        history_rows: The confirmed-payment history slice
            (:attr:`PayoffScenarios.history_rows`).
    """

    projection_inputs: ProjectionInputs
    history_rows: list[AmortizationRow]


def _contract_start(
    loan_inputs: LoanInputs,
    confirmed_view: ConfirmedLedgerView | None,
) -> _ProjectionPrep:
    """Read where the loan's record stands and assemble the contractual forward's inputs.

    **The contract line starts at the first installment after the record's
    LAST EVENT** (ruling plan#3382, 1A): its latest payment's own due date or
    its latest assertion's date, whichever is later, in contract time
    (:func:`app.services.installment_calendar.installment_after`).  That is
    the first installment the record has not charged, and the forward plan
    folds the same record, so the two begin on one installment.  The line
    runs every installment from there through the contract's last: number
    ``k`` of ``term`` leaves ``term - k + 1``
    (:func:`~app.services.rate_period_engine.payment_number`), and none for a
    record already past the term.

    Two sources, one shape:

    * **A confirmed view** (every read with a baseline scenario and a loan
      already originated): its balance seeds the line, its rows are the
      history, and its ``last_recorded_on`` is the event.
    * **None** (no baseline, a read before origination, or the property
      chart's from-origination contract): the latest assertion
      (:func:`._periods.select_latest_anchor`) seeds the line at its balance
      from its date, with no history rows -- the origination itself, for a
      loan the app records nothing about yet.

    *Until plan step recurrence:R16-e the start and the months left were a
    replay's* (``replay_schedule``: *one contractual step per payment record
    from the latest anchor, then the month after its last row*), *and the two
    disagree on a month exactly where records and installments do not pair
    one to one.*  Made-up figures, a $12,000.00 loan at 6% over 12 months
    from 2026-01-01, due the 1st, with the Jan 16 and Jan 30 paychecks both
    paying February's installment: the replay counted two months and started
    the line on Apr 1; the record charged February once, so the line starts
    Mar 1 and runs 11 months.  The same month moves for a payment due before
    the loan's day in its month and for a balance asserted before it.

    Args:
        loan_inputs: The loan's loaded input bundle; its ``anchor_events``
            are read only when *confirmed_view* is ``None``.
        confirmed_view: The loan's genesis-ledger confirmed view, or
            ``None`` (see above).

    Returns:
        A :class:`_ProjectionPrep` with the projection inputs and the
        confirmed-payment history slice.

    Raises:
        ValueError: When *confirmed_view* is ``None`` and
            ``loan_inputs.anchor_events`` is empty (via
            :func:`._periods.select_latest_anchor`).
    """
    params = loan_inputs.loan_params
    if confirmed_view is not None:
        starting_balance = confirmed_view.balance
        last_recorded_on = confirmed_view.last_recorded_on
        history_rows = list(confirmed_view.history_rows)
    else:
        anchor = select_latest_anchor(loan_inputs.anchor_events)
        starting_balance = round_money(Decimal(str(anchor.anchor_balance)))
        last_recorded_on = anchor.anchor_date
        history_rows = []
    starting_date = installment_after(
        params.origination_date, params.payment_day, last_recorded_on,
    )
    # The projection's terms feed is the loan's FULL rate-period set (past
    # periods included), so every forward month is governed by its true
    # period's rate AND level P&I.  The rate-period engine stays the single
    # producer of those figures (recorded recast or schedule-derived), which
    # is what makes the projected rows and the loan card agree at every date,
    # not just at ``as_of`` (DH-#1).
    projection_inputs = ProjectionInputs(
        starting_balance=starting_balance,
        starting_date=starting_date,
        remaining_months=max(
            0,
            params.term_months
            - payment_number(params.origination_date, starting_date) + 1,
        ),
        payment_day=params.payment_day,
        terms_schedule=_terms_from_periods(
            resolve_periods(params, loan_inputs.rate_changes),
        ),
    )
    return _ProjectionPrep(
        projection_inputs=projection_inputs,
        history_rows=history_rows,
    )


def compute_payoff_scenarios(
    *,
    loan_inputs: LoanInputs,
    confirmed_view: ConfirmedLedgerView | None = None,
) -> PayoffScenarios:
    """Return the confirmed past and the CONTRACT's forward from where the record stands.

    Reads the record's standing once (:func:`_contract_start`) and calls
    :func:`project_forward` once for the pure contractual reference.  Each
    month's contractual P&I and rate come from the loan's full rate-period
    terms feed -- the same figures the loan card reads via
    :func:`~app.services.rate_period_engine.period_for_date` -- so the
    projected P&I matches ``LoanState.monthly_payment`` by construction in
    every period, recorded recasts included (DH-#1).

    **It projected the owner's PLAN too until plan step R7d-g-3** (ruling
    **R-R88**): the committed and accelerated slices routed the projected
    transfer rows through ``monthly_override`` and applied a loan-level
    ``extra_principal`` and the lever's ``extra_monthly`` on top.  The
    override amounts were priced by amount rule 4 with each definition's
    extra INSIDE them (since ``balance:X-au-g-2c-1``), so the loan-level
    extra paid twice on every row-covered month (measured 2026-09-14: P&I
    ``$526.46`` + a ``$100`` extra arrived as ``$626.46`` and the month then
    paid ``$676.46``), and the months no row covered were priced from the
    contract plus ONE picked definition's extra (plan ledger row **D49**).
    The balance seam's plan fold already priced every definition's own
    occurrences; it is the one forward walk now
    (:func:`app.services.balance_at.loan_installments`,
    :func:`app.services.balance_at.loan_what_if_owed_at_dates`), and this
    composer keeps what the fold does not produce.

    **It takes no evaluation date since plan step recurrence:R16-e.**  The
    date bounded the replay, which is deleted; the record's standing on a
    date is the confirmed view's, built for the read pass's own ``as_of``
    (:func:`app.services.balance_at.confirmed_view`).

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle
            (``loan_params``, ``anchor_events``, ``rate_changes``).
        confirmed_view: The loan's genesis-ledger confirmed view (the read
            switch) -- its balance seeds the forward slice, its last event
            dates it, and its ledger-derived rows become ``history_rows`` --
            or ``None`` to seed from the loan's latest assertion with no
            history (see :func:`_contract_start`).  The caller reads it once
            (via ``balance_at.confirmed_view``) so the history and the
            contractual reference derive from the same real owed balance the
            loan card shows.

    Returns:
        A :class:`PayoffScenarios` with the confirmed history and the
        contractual forward.

    Raises:
        ValueError: When *confirmed_view* is ``None`` and
            ``loan_inputs.anchor_events`` is empty (via
            ``._periods.select_latest_anchor``).
    """
    prep = _contract_start(loan_inputs, confirmed_view)
    return PayoffScenarios(
        history_rows=prep.history_rows,
        original_forward=project_forward(prep.projection_inputs),
    )
