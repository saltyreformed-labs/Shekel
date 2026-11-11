"""
Shekel Budget App -- Loan route package: shared helpers.

The Marshmallow schema singletons, the loan-account loader / ownership check,
the resolver-state and full-context loaders, and the chart-balance utilities
shared across the loan route sub-modules.  Schema instances are constructed
once at import time so every handler reuses the same instance (Marshmallow
contract), preserving the pre-split monolith's behaviour.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from flask import abort, flash, redirect, render_template, url_for
from flask_login import current_user

from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.schemas.validation import (
    EscrowComponentSchema,
    EscrowLineMergeSchema,
    EscrowLineRenameSchema,
    EscrowVersionSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    LoanPaymentExtraSchema,
    LoanPaymentTransferSchema,
    PayoffCalculatorSchema,
    RateChangeSchema,
    RefinanceSchema,
)
from app.services import (
    balance_at,
    cash_ledger,
    escrow_calculator,
    liability_sign,
    loan_resolver,
)
from app.services.amortization_engine import AmortizationRow
from app.services.balance_at import LoanFigures, LoanTerms
from app.services.installment_calendar import installment_dates
from app.services.loan_loaders import (
    latest_settled_payment_due_date,
    load_loan_anchor_facts,
)
from app.services.loan_payment_service import LoanContext, load_loan_context
from app.services.rate_period_engine import payment_number
from app.services.balance_at import BalanceContext
from app.utils.auth_helpers import get_or_404
from app.utils.dates import add_months, display_today
from app.utils.money import round_money


# Field allowlist for the loan-params update route -- the LoanParams
# columns the update form may set directly.  The balance is not one: the
# seam derives it from :class:`LoanAnchorEvent`, and the demoted
# ``current_principal`` seed (E-18 / D-C) was dropped at plan step
# ``recurrence:R20``.  ``interest_rate`` is excluded (DH-#56): the column
# was retired, and the form's rate field edits the loan's origination
# RateHistory row through ``update_params``'s ``_upsert_origination_rate``
# instead of a column set.
_PARAM_FIELDS = {
    "payment_day", "term_months",
    "is_arm", "arm_first_adjustment_months", "arm_adjustment_interval_months",
}

_create_schema = LoanParamsCreateSchema()
_update_schema = LoanParamsUpdateSchema()
_trueup_schema = LoanAnchorTrueupSchema()
_rate_schema = RateChangeSchema()
_escrow_schema = EscrowComponentSchema()
_escrow_version_schema = EscrowVersionSchema()
_escrow_rename_schema = EscrowLineRenameSchema()
_escrow_merge_schema = EscrowLineMergeSchema()
_payoff_schema = PayoffCalculatorSchema()
_refinance_schema = RefinanceSchema()
_transfer_schema = LoanPaymentTransferSchema()
_payment_extra_schema = LoanPaymentExtraSchema()


def _load_loan_account(account_id):
    """Load and validate a loan account for the current user.

    Verifies ownership and that the account type has has_amortization=True.

    Returns:
        (account, params, account_type) or (None, None, None) if invalid.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return None, None, None

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        return None, None, None

    params = (
        db.session.query(LoanParams)
        .filter_by(account_id=account.id)
        .first()
    )
    return account, params, account_type


def render_loan_setup(
    account: Account,
    account_type: AccountType,
    submitted: Mapping[str, str] | None = None,
) -> str:
    """Render the loan setup form for an account that has no ``LoanParams`` yet.

    **A refused POST the form itself can remedy comes back exactly as typed**
    (review 7c of plan step recurrence:R16-c-2): the schema's refusal, the
    term cap's, the pre-origination one and the tracking-start one each pass
    the POST's own form as *submitted*, and every field the template draws
    reads its posted string from it.  Until then a refusal re-rendered the
    defaults below over a blank form, so an owner who followed a refusal by
    changing only the date re-submitted the prefilled balance instead of the
    one typed -- measured: a prefilled ``0.00`` configured a loan owing
    $0.00.  The defaults serve the form's FIRST showing only.  Three
    answers redirect instead.  An account that is not a loan type goes to
    ``savings.dashboard`` and a loan already configured to its own page
    (``loan.dashboard``, which shows it configured): neither
    has a setup form to re-render.  The standing payment's two refusals
    (:func:`app.routes._standing_payment.sync_loan_payment_start_or_refuse`)
    roll the whole setup back and redirect to the loan's page, which shows
    this form's first showing, so what was typed is lost; their remedy is
    the recurring transfer's while the typed contract is right, and a
    mistyped origination can meet them too (REC-554).

    The ONE renderer of ``loan/setup.html`` -- the dashboard shows it for an
    unconfigured loan, and ``create_params`` re-shows it on a refused POST --
    so the two prefills are spelled once: the "balance today" field opens on
    what the account's latest cash assertion
    (:func:`app.services.cash_ledger.resolve_anchor`, the ONE answer to "what
    balance has this account been asserted to hold"; plan step X-f1c3a) says
    it OWES (:func:`app.services.liability_sign.shown_figure`, plan step
    credit_card:CC-5-5b), and
    its "as of" date on today, the setup date (plan step ``recurrence:R20``,
    ruling **R-R72** part 3: the stated balance is a dated assertion, dated by
    the owner and defaulting to the day it is typed).  Today is the DISPLAY
    day (:func:`app.utils.dates.display_today`), the civil day the owner is
    typing on; the form's ``max`` is the same day.

    Args:
        account: The loan :class:`Account` being configured.
        account_type: Its :class:`AccountType` row (labels, icon, term cap).
        submitted: The refused POST's form (``request.form``), or ``None``
            for the form's first showing.  Passed through whole: the
            template names its own fields, so no second list of them lives
            here, and a field the POST lacked comes back empty.  Each value
            is the raw posted string, autoescaped by the template.

    Returns:
        The rendered setup page.
    """
    return render_template(
        "loan/setup.html",
        account=account,
        account_type=account_type,
        # The field is the loan's balance OWED (``min="0"``, and the loan
        # domain's store is positive-owed), while the assertion it opens on is
        # HELD since plan step credit_card:CC-5-5b -- a car loan created owing
        # 5,000.00 holds -5,000.00 -- so the pre-fill crosses through the
        # door's one function (ruling R-CC52) and opens on 5,000.00.  Read
        # raw, it would pre-fill a negative figure the box refuses.  Read for
        # the first showing only: a refused POST re-renders what was typed.
        anchor_balance=(
            None if submitted is not None
            else liability_sign.shown_figure(
                account_type, cash_ledger.resolve_anchor(account).balance,
            )
        ),
        today_iso=display_today().isoformat(),
        submitted=submitted,
    )


def _require_configured_loan(account_id):
    """Load a loan account that the owner has fully configured, or reject.

    The shared precondition for the parameter-mutation and
    payment-transfer routes: the account must be owned by the current
    user, be an amortizing type, AND already have ``LoanParams``.  On
    failure it raises (never returns) the appropriate response:

      * 404 (``abort``) for a cross-owner / non-existent / non-loan
        account -- the project's "404 for not-found and not-yours" rule.
      * a redirect to the dashboard with a warning flash when the owner
        reached the endpoint without configured params (a stale form,
        hand-crafted URL, or back-button reload after a deletion);
        ``abort(redirect(...))`` raises this as a 302 so callers do not
        repeat the load/guard, and it is never conflated with the IDOR
        404 above.

    Args:
        account_id: The loan account id from the route.

    Returns:
        (account, params, account_type) -- only on success; failure
        paths raise.
    """
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        abort(404)
    if params is None:
        flash("Loan parameters are not configured.", "warning")
        abort(redirect(url_for("loan.dashboard", account_id=account_id)))
    return account, params, account_type


@dataclass(frozen=True)
class _RouteLoanContext:
    """One read pass's loan state for the loan ROUTE surfaces, sourced from the seam.

    The loan detail page reads its displayed balance and rich figures from the
    :mod:`app.services.balance_at` seam like every other loan surface (plan step
    C4), instead of resolving a private :class:`LoanState` and rendering
    ``state.current_balance`` -- a balance-at-T produced outside the seam that,
    for a loan whose genesis ledger is missing, answered from the money-blind
    anchor replay while the seam folds the source facts (finding B-13).

    Composes three inputs from ONE ``BalanceContext``:

      * ``balance_ctx`` -- the read pass's context (memoizes the loan's single
        resolution + ledger walk); its ``as_of`` is today.
      * ``loan`` -- the service-loaded :class:`LoanContext` (prepared payments,
        rate changes, escrow lines / components, monthly escrow, rate history)
        the route's own schedule composer and escrow card need, which the seam
        does not publish.
      * ``figures`` -- the seam's :class:`LoanFigures` (payment, rate, payoff,
        arm), carrying deliberately NO balance.

    What the loan owes is a property that reads the seam on demand; the figures
    fields are exposed as properties so the dashboard / calculators read typed
    attributes (``ctx.current_owed`` / ``ctx.monthly_payment`` /
    ``ctx.current_rate`` / ``ctx.payoff_date``) exactly as they read the old
    ``ctx.state.*``.
    """

    account: Account
    balance_ctx: BalanceContext
    loan: LoanContext
    figures: LoanFigures

    @property
    def current_owed(self) -> Decimal:
        """What the loan OWES today, from the seam (the fold, plan C4).

        :func:`app.services.liability_sign.owed` of
        :func:`app.services.balance_at.balance_at` at the pass's ``as_of``
        (today) -- the same seam entry and the same flip the /savings tile, the
        net-worth hero and /debt-strategy read, so the loan card's figure is
        produced in the one tested place instead of off a private resolver.

        It was ``current_balance`` and returned the seam's figure raw until
        plan step credit_card:CC-5-5c, when that figure was the loan's owed
        amount; the seam reports a configured loan HELD since then (ruling
        R-CC47), so the property is named for what every consumer reads it as
        -- the balance hero, the true-up pre-fill, the payment summary and the
        payoff / refinance calculators' principal.
        """
        return liability_sign.owed(balance_at.balance_at(
            self.account, self.balance_ctx, self.balance_ctx.as_of,
        ))

    @property
    def monthly_payment(self) -> Decimal:
        """The loan's P&I payment as of today (the seam's resolved figure)."""
        return self.figures.terms.monthly_payment

    @property
    def current_rate(self) -> Decimal:
        """The annual interest rate in effect today (the seam's resolved figure)."""
        return self.figures.terms.current_rate

    @property
    def payoff_date(self) -> date | None:
        """The DERIVED payoff -- the date the balance folds to zero (plan C8d).

        ``None`` for a loan already RETIRED and for one that never pays off at
        its current payment; :attr:`is_retired` separates the two.  It used to be
        the committed schedule's last row, which reports a contractual date even
        for a loan paying short.
        """
        return self.figures.payoff_date

    @property
    def is_retired(self) -> bool:
        """Whether the loan has originated and now owes nothing (the seam figure).

        The disambiguator for a ``None`` :attr:`payoff_date`: retired (badge it
        "Paid off") versus never-pays-off (say so).
        """
        return self.figures.is_retired


def _require_figures(account, balance_ctx: BalanceContext) -> LoanFigures:
    """Return a configured loan's seam :class:`LoanFigures`, or fail loud.

    The loan route only reaches the seam for a loan the caller has already
    confirmed is configured (``_load_loan_account`` / ``_require_configured_loan``
    loaded its :class:`LoanParams`), so ``None`` here -- the seam's not-a-loan
    signal -- is a caller bug, not a display case.  Raising keeps the callers'
    ``figures`` non-nullable and matches the seam's fail-loud contract, rather
    than letting a ``None`` render an empty payment silently.

    Args:
        account: The configured loan account (ownership already verified by the
            caller -- the seam's trust-the-caller contract).
        balance_ctx: The read pass's :class:`BalanceContext`.

    Returns:
        The loan's :class:`LoanFigures`.

    Raises:
        ValueError: When *account* has no ``LoanParams`` (a caller error).
    """
    figures = balance_at.loan_figures(account, balance_ctx)
    if figures is None:
        raise ValueError(
            f"loan figures unavailable for account {account.id}: it has no "
            f"LoanParams. Load a configured loan before reaching the seam."
        )
    return figures


def _loan_terms_now(account) -> LoanTerms:
    """Return a configured loan's CONTRACT terms as of today, resolving it once.

    The accessor the loan route's non-balance WRITE surfaces read the monthly
    payment / current rate off -- the escrow total-payment recompute
    (:func:`_compute_total_payment`), the rate-history OOB swap
    (:func:`app.routes.loan.escrow_rates._render_rate_history`), and the
    payment-transfer default (:func:`app.routes.loan.payment_transfer._resolve_transfer_amount`).
    They build a FRESH :class:`BalanceContext` (an as-of-today read pass) so the
    figure reflects any just-committed rate / escrow change; the values are the
    same the loan card shows, produced by the one seam resolution rather than a
    private resolve.

    **It takes :class:`~app.services.balance_at.LoanTerms`, not the wider
    ``LoanFigures``** (plan step C8e).  These surfaces read only the payment and
    the rate -- both derived from the loan's params and rate history alone -- so
    binding them to the scenario-scoped bundle bound them to a baseline scenario
    they never needed.  Step C8d made that visible by giving the bundle its first
    scenario-scoped field: escrow editing began raising the seam's
    ``require_scenario`` for a user whose baseline is missing, which is a state a
    configured loan can outlive (see ``baseline_service``).  The narrow value
    needs no scenario, so these surfaces keep working while the fail-loud stays on
    the reads that genuinely need one.

    Args:
        account: The configured loan account (ownership already verified by the
            caller).

    Returns:
        The loan's :class:`LoanTerms` as of today.

    Raises:
        ValueError: When *account* has no ``LoanParams`` (a caller error -- these
            surfaces load a configured loan first).
    """
    terms = balance_at.loan_terms(account, BalanceContext.build(current_user.id))
    if terms is None:
        raise ValueError(
            f"loan terms unavailable for account {account.id}: it has no "
            f"LoanParams. Load a configured loan before reaching the seam."
        )
    return terms


def _load_route_context(account, params) -> _RouteLoanContext:
    """Build the loan ROUTE's read pass: one BalanceContext + the loaded context.

    The single loader the loan detail READ surfaces (the dashboard GET, the
    calculators, the standalone schedule) resolve a loan through.  It builds ONE
    :class:`BalanceContext` for the pass -- so the balance and the rich figures
    come from the same memoized resolution -- and loads the service
    :class:`LoanContext` (payments / escrow / rate) the route's own schedule
    composer and escrow card need.  It no longer runs the private resolver seeding
    the pre-C4 route did: what it owes is the seam's fold
    (:attr:`_RouteLoanContext.current_owed`), and the payment / rate / payoff
    are the seam's figures, so the loan tile is no longer the one surface whose
    balance was produced outside the seam.

    Ownership was already verified by ``_load_loan_account -> get_or_404`` before
    this runs, satisfying the seam's trust-the-caller contract.

    Args:
        account: ORM :class:`Account` instance for a configured loan.
        params: ORM :class:`LoanParams` instance (the escrow / payment loader's
            input; also the seam resolution's, loaded once inside the seam).

    Returns:
        The :class:`_RouteLoanContext` for this read pass.
    """
    balance_ctx = BalanceContext.build(current_user.id)
    loan = load_loan_context(account.id, balance_ctx.amounts(), params)
    return _RouteLoanContext(
        account=account,
        balance_ctx=balance_ctx,
        loan=loan,
        figures=_require_figures(account, balance_ctx),
    )


def _total_payment_from_seam(account, escrow_components) -> Decimal:
    """Return P&I (the seam figure) + *escrow_components* -- the one total-payment sum.

    The single "total monthly payment" assembly: the loan's resolved P&I
    (:func:`_loan_terms_now`, which owns the payment for both ARM -- re-amortized
    from the latest anchor over the remaining term -- and fixed-rate loans) plus
    the supplied escrow set, quantized by
    :func:`~app.services.escrow_calculator.calculate_total_payment`.  Every
    "P&I + escrow" figure funnels through here -- the escrow / rate OOB partials
    (:func:`_compute_total_payment`) and the loan-payment default / auto-track
    switch (:func:`app.routes.loan.payment_transfer._contractual_monthly_payment`)
    -- so the number the loan card shows, the recurring-payment default, and the
    drift / track-payment comparison are ONE computation and cannot silently
    diverge.

    The caller supplies the escrow set (today's active lines for the loan card and
    the payment default; the drawer's set for an escrow OOB swap), so this stays a
    pure sum with no load.

    Args:
        account: The configured loan account (ownership verified by the caller;
            the seam resolves the P&I).
        escrow_components: The resolved escrow lines to add to P&I
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).

    Returns:
        The total monthly payment (P&I + escrow) as a ``Decimal``.
    """
    return escrow_calculator.calculate_total_payment(
        _loan_terms_now(account).monthly_payment, escrow_components,
    )


def _compute_total_payment(account, params, escrow_components):
    """Compute total monthly payment (P&I + escrow) for OOB updates.

    Reads the seam figure's ``monthly_payment`` (via the shared
    :func:`_total_payment_from_seam`) so the escrow / delete-escrow HTMX partials
    display the same P&I as the loan card.  Returns None when params are absent
    (no loan configured yet).

    Args:
        account: ORM :class:`Account` instance for the loan account.
            The seam resolves it (ownership already verified by the caller).
        params: ORM :class:`LoanParams` instance, or None.
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
    """
    if params is None:
        return None
    return _total_payment_from_seam(account, escrow_components)


def _forward_boundary(account_id, scenario_id):
    """Return the escrow forward-only guard boundary for a loan, or ``None``.

    The latest settled payment's DUE date
    (:func:`~app.services.loan_loaders.latest_settled_payment_due_date`) -- on
    or after the installment the genesis split resolves each payment's escrow
    at (ruling D5's contract time, finding N-34, as ruling R-R104 amends it:
    the installment the payment pays, wider than it needs to be for a payment
    due off the loan's day, finding REC-544), so a new or edited escrow version strictly
    after it cannot move any settled payment's split.
    ``None`` (nothing is frozen) when the user has no baseline scenario or the loan
    has no settled payment.  Shared by the escrow HTMX routes (which apply the guard
    and mark each drawer row editable / deletable) and the loan dashboard GET (which
    builds the same drawer inline), so both derive the boundary one way.

    Args:
        account_id: The loan account whose settled payments bound the guard.
        scenario_id: The baseline scenario id, or ``None``.

    Returns:
        The boundary date, or ``None`` when nothing is settled.
    """
    if scenario_id is None:
        return None
    return latest_settled_payment_due_date(account_id, scenario_id)


def band_chart_dates(
    scenarios, payoff, installments, params, start,
) -> list[date]:
    """Return the band chart's x-axis: the loan's installments, its record's start to the payoff.

    **Every installment the loan owes, whatever days its payments fall on**
    (ruling **R-R110**, plan step recurrence:R16-c-2): the loan's own
    installment calendar
    (:func:`~app.services.installment_calendar.installment_dates`, the ONE
    producer its charges are dated by, so a loan due on the 31st returns to
    the month's end after a February) from the first installment AFTER the
    day the app's record of the loan starts (*start*, ruling **R-R111**) --
    an origination is never itself an installment, and a tracking-start
    assertion dated on one already states the balance after it, since the
    walk applies an assertion after its day's charge and payments -- through
    the CONTRACT's last installment, extended month by month whenever
    the seam's DERIVED payoff falls later -- an underpaying plan clears the
    loan in the post-contractual extension, and the line must run to where
    the balance actually reaches zero rather than stop at the last labelled
    tick.  A plan that never clears it runs to the plan's last installment
    instead; a retired loan (no payoff, no plan) ends with its history.  Each
    point is the balance the ledger holds that day (:func:`build_band_chart`),
    so a payment shows as the drop into the first installment after its money
    moved, and two payments inside one installment's interval are one point.
    The same grid serves the lever's preview (:func:`accelerated_overlay`),
    which is what keeps the overlay aligned to the band one point to one.

    **The months were the rows' own dates until R-R110**: the confirmed rows'
    then the contractual forward's.  Once ruling R-R109 dated a confirmed row
    by the installment its payment pays, a payment due off the loan's day was
    plotted at an installment before its money moved and never after it, and
    the month after it vanished, because the forward started from the
    payment's own due date (``rate_period_engine.replay_schedule``, deleted
    at plan step recurrence:R16-e); two payments in one interval repeated a
    month, and a month nobody paid had no point at all.

    Pure over values the caller already holds -- the pass's payoff figure,
    the plan's installments and the loan's terms -- so the grid costs the page
    no fold of its own (the payoff is folded once per pass,
    ``balance_at.memoized_payoff``).

    Args:
        scenarios: The baseline :class:`~app.services.loan_resolver.PayoffScenarios`;
            its confirmed history and contractual forward say only where the
            contract ends and whether there is anything to chart.
        payoff: The seam's derived payoff
            (:attr:`~app.routes.loan._helpers._RouteLoanContext.payoff_date`),
            ``None`` for a retired loan or a plan that never clears.
        installments: The plan as it stands
            (:func:`~app.services.balance_at.loan_installments`), for the
            never-clears case's last date.
        params: The loan's :class:`~app.models.loan_params.LoanParams`, whose
            origination and due day name its installment calendar.
        start: The day the loan's record starts
            (:attr:`~app.services.balance_at.LoanTerms.recorded_start`).

    Returns:
        Ascending installment dates, one per month; empty for a loan whose
        history and contract both hold no row (a retired loan the composer
        drops), and empty when *start* is on or after the grid's last
        installment (a tracking start recorded at or after a loan's end), so
        no point ever falls past it.
    """
    rows = [*scenarios.history_rows, *scenarios.original_forward]
    if not rows:
        return []
    contract_end = rows[-1].payment_date
    if payoff is None:
        payoff = installments[-1].due_date if installments else contract_end
    # Through the contract's last installment, or the first installment on or
    # after a later payoff (a payoff on a definition's own cadence can fall
    # between two).  The end is tested whether or not an installment is
    # charted, so a start on or after it charts nothing past it.
    last = max(contract_end, payoff)
    dates: list[date] = []
    for due in installment_dates(
        params.origination_date, params.payment_day, add_months(last, 1),
    ):
        if due > start:
            dates.append(due)
        if due >= last:
            break
    return dates


def build_band_chart(account, balance_ctx, dates):
    """Serialize the loan-detail band chart: the seam's balance on the contractual axis.

    The Fable 5 loan-detail band chart (docs/design/loan_audit.md, locked
    anatomy) draws a SINGLE balance trajectory which the client splits at the
    confirmed / projected boundary via :func:`ShekelChart.splitSegment`
    (``current_index``).  **The line is the balance seam's own balance at
    every grid date since plan step R7d-g-3** (ruling **R-R88**):
    :func:`~app.services.balance_at.positions` reads a past date off the
    ledger and a future one off the forward plan fold -- the same producer the
    balance hero, the "Projected payoff" chip and the /savings debt card read
    -- so the band cannot disagree with any of them.  Until that step it drew
    ``loan_resolver.compute_payoff_scenarios``' committed slice, a second
    forward walk that priced the months no generated row covered from the
    contract plus one picked definition's extra, and lagged the chip beside it
    by years on a loan paying extra.  A loan with no recurring payment folds
    the contract's own installments, so there is no contractual-versus-committed
    switch any more: one line, whatever the plan is.

    Args:
        account: The loan account (the caller owns the ownership check).
        balance_ctx: The read pass.
        dates: The band's grid (:func:`band_chart_dates`).

    Returns:
        dict with ``labels`` (list[str]), ``balance`` (list[float], the seam's
        owed balance at each label -- ``float()`` only here, the Chart.js
        serialization boundary), and ``current_index`` (int -- the count of
        grid dates at or before the pass's as-of, i.e. the solid / dashed
        boundary: the ledger's points, whether or not anything was paid).
    """
    owed = balance_at.positions(account, balance_ctx, dates) if dates else {}
    return {
        "labels": [on_date.strftime("%b %Y") for on_date in dates],
        "balance": [float(owed[on_date]) for on_date in dates],
        "current_index": sum(
            1 for on_date in dates if on_date <= balance_ctx.as_of
        ),
    }


def accelerated_overlay(account, balance_ctx, dates, extra_monthly):
    """Forward-only what-if balances for the band chart's payoff-lever preview.

    The green dashed "pay off sooner" preview (docs/design/loan_audit.md, locked
    anatomy) the band chart overlays when the extra-payment lever runs: the
    seam's forward fold with *extra_monthly* added once per accrual period
    (:func:`~app.services.balance_at.loan_what_if_owed_at_dates`), on the
    band's own grid (:func:`band_chart_dates`), with the confirmed-history
    positions left ``None`` so the green line begins at Today and diverges
    from the projected line rather than redrawing the shared solid history.
    With ``0.00`` it IS the band's projected line, which is what makes the
    preview and the plan one walk apart by exactly the extra.

    Args:
        account: The loan account (the caller owns the ownership check).
        balance_ctx: The read pass.
        dates: The band's grid (:func:`band_chart_dates`).
        extra_monthly: The lever's hypothetical extra per accrual period.

    Returns:
        list of ``float | None`` whose length equals the band chart's balance
        array: ``None`` at every grid date at or before the pass's as-of, the
        what-if balance at every date after it.
    """
    future = [on_date for on_date in dates if on_date > balance_ctx.as_of]
    owed = (
        balance_at.loan_what_if_owed_at_dates(
            account, balance_ctx, future, extra_monthly,
        ) if future else {}
    )
    return [None] * (len(dates) - len(future)) + [
        float(owed[on_date]) for on_date in future
    ]


def build_baseline_scenarios(loan_inputs, account, balance_ctx):
    """Run the baseline composer call for the loan detail page: history + contract.

    One ``compute_payoff_scenarios`` call whose ``history_rows`` (the
    confirmed actuals, ledger-derived) and ``original_forward`` (the CONTRACT's
    remaining installments) the band chart's x-axis, the amortization table's
    confirmed half and the lever's contract-versus-plan comparison read.
    **It composes no planned trajectory since plan step R7d-g-3** (ruling
    **R-R88**): what the loan is projected to PAY is the balance seam's forward
    plan, read through :func:`~app.services.balance_at.loan_installments` and
    :func:`~app.services.balance_at.positions`, so the page's projected
    figures and its balance hero come from one fold.

    Read switch: reads the genesis-ledger confirmed view ONCE via the seam's
    :func:`app.services.balance_at.confirmed_view` -- the FOLD of the loan's
    recorded events since plan step E1d-b, the SAME producer the seam's whole-loan
    read seeds every resolution with -- and threads it into the composer as
    ``confirmed_view``, so the chart / summary derive from the same real owed
    balance AND confirmed history the loan card's seam figure
    (:attr:`_RouteLoanContext.current_owed`) shows.  They cannot desync
    off-schedule, and a loan whose posting cache is cold no longer drops to the
    money-blind anchor replay here (finding B-12).

    Shared by the dashboard GET, the ARM rate-change band producer
    (:func:`build_loan_band_chart`) and the standalone schedule route, so the
    single composer call lives in exactly one place.

    Args:
        loan_inputs: The loan's :class:`loan_resolver.LoanInputs` bundle.
        account: The loan :class:`~app.models.account.Account` the confirmed view
            is built for.
        balance_ctx: The read pass's :class:`BalanceContext` -- its ``scenario``
            scopes the confirmed seed and its ``as_of`` is the date the seed
            folds the record to, the composer's one clock since plan step
            recurrence:R16-e took its own ``as_of`` away with the replay.

    Returns:
        The baseline :class:`loan_resolver.PayoffScenarios`.
    """
    return loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_inputs,
        confirmed_view=balance_at.confirmed_view(account, balance_ctx),
    )


def _loan_inputs(params, loan_context: LoanContext) -> loan_resolver.LoanInputs:
    """Bundle a loan's resolver inputs from its params + loaded :class:`LoanContext`.

    The single :class:`loan_resolver.LoanInputs` constructor for the loan ROUTE
    surfaces (the dashboard GET and the band-chart producer), so the
    (params, anchor facts, rate changes) assembly lives in one place.

    Args:
        params: ORM :class:`LoanParams` instance (also the anchor-fact
            synthesis source).
        loan_context: The service-loaded :class:`LoanContext` (``ctx.loan`` for a
            route surface, or a directly-loaded one) carrying the rate changes.

    Returns:
        The :class:`loan_resolver.LoanInputs` bundle.
    """
    return loan_resolver.LoanInputs(
        loan_params=params,
        anchor_events=load_loan_anchor_facts(params),
        rate_changes=loan_context.rate_changes,
    )


def build_loan_band_chart(account, params):
    """Recompute the loan-detail band chart dict from the current loan state.

    The band's balance-over-time chart is a function of the loan's plan and
    ledger, so a mutation that RE-AMORTIZES the loan (an ARM rate change --
    :func:`app.routes.loan.escrow_rates.add_rate_change`) leaves the band stale
    until the chart is rebuilt.  This is the single producer both the dashboard
    GET path and that HTMX rate route share (:func:`_load_route_context` +
    :func:`build_baseline_scenarios` + :func:`band_chart_dates` +
    :func:`build_band_chart`), so the refreshed chart cannot diverge from the
    initially-rendered one.  Ownership
    is verified by the caller before this runs (``add_rate_change`` is
    ``require_owner``-gated), satisfying the seam's trust-the-caller contract.

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.

    Returns:
        The serializable band-chart dict (``labels`` / ``balance`` /
        ``current_index``) -- identical in shape to the dashboard's initial
        ``band_chart`` -- for the rate route to hand to ``loan_detail.js``.
    """
    ctx = _load_route_context(account, params)
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx.loan), account, ctx.balance_ctx,
    )
    return build_band_chart(
        account, ctx.balance_ctx,
        band_chart_dates(
            scenarios, ctx.payoff_date,
            balance_at.loan_installments(account, ctx.balance_ctx),
            params, ctx.figures.terms.recorded_start,
        ),
    )


def _compute_schedule_totals(schedule, row_escrow):
    """Sum payment, principal, interest, escrow, and extra from a schedule.

    The Payment column in the schedule shows P&I + escrow for each month.
    Totals are computed from the actual schedule rows so the footer row
    matches the individual data rows exactly -- including the escrow column,
    which is the SUM of the per-row figures rather than one figure times the
    row count (ruling **R-IJ**, plan step X-au-g-2b, finding **N-410**): once
    escrow is effective-dated, a schedule spanning a version boundary has no
    single monthly escrow to multiply.

    Args:
        schedule: List of AmortizationRow objects.
        row_escrow: The index-parallel per-row monthly escrow, each resolved on
            that row's own installment date (:func:`build_schedule_context`).

    Returns:
        dict with keys: total_payment, total_principal, total_interest,
        total_escrow, total_extra, has_extra.  Empty dict if schedule
        is empty.
    """
    if not schedule:
        return {}
    total_pi = sum((row.payment for row in schedule), Decimal("0.00"))
    total_principal = sum((row.principal for row in schedule), Decimal("0.00"))
    total_interest = sum((row.interest for row in schedule), Decimal("0.00"))
    total_extra = sum((row.extra_payment for row in schedule), Decimal("0.00"))
    total_escrow = sum(row_escrow, Decimal("0.00"))
    return {
        "total_payment": total_pi + total_escrow + total_extra,
        "total_principal": total_principal,
        "total_interest": total_interest,
        "total_escrow": total_escrow,
        "total_extra": total_extra,
        "has_extra": total_extra > Decimal("0.00"),
    }


def planned_periods(installments) -> list[list]:
    """Group the plan's installments by accrual period, through the payoff.

    The ONE grouping the loan page reads the plan by: the schedule's
    month-by-month rows and the allocation bar's "this month" both take a
    group from here, so a period is spelled once -- the installment whose
    charge the payment meets
    (:attr:`~app.services.loan_ledger.PaymentOutcome.charge_date`, ruling
    **R-R89**'s contract interval; ``None`` for every payment before the
    loan's first installment, which share that one interval).  Until plan
    step recurrence:R16-c-2 it grouped by the charge's CALENDAR month (finding
    **D55**'s key), which named the same groups for every charged payment and
    split the first interval at a month boundary.  A tracking payment and a
    fixed sweep due inside one interval are one period's payments; a
    catch-up -- an occurrence due before the read that no row answers, which
    the plan pays the day after it (ruling **R-R64**, the D1 clamp) --
    belongs to the period whose charge it meets: its own installment's, or,
    behind a later settled payment, the installment that payment cleared.
    The plan runs past the payoff into the post-contractual
    extension (installments there carry a zero balance and pay nothing
    down), so the groups stop with the one whose balance reaches zero; a
    plan that never clears the loan yields every period of the extension,
    which is the honest length of what it would pay.

    Args:
        installments: :func:`~app.services.balance_at.loan_installments`'s
            answer, in the fold's DUE order.

    Returns:
        The period groups, each a non-empty list in the fold's DUE order.
    """
    periods: list[list] = []
    for installment in installments:
        if periods and periods[-1][0].charge_date == installment.charge_date:
            periods[-1].append(installment)
        else:
            periods.append([installment])
        if installment.balance_after <= Decimal("0.00"):
            break
    return periods


def planned_schedule_rows(installments, params) -> tuple[list, list[Decimal]]:
    """Render the seam's forward plan as the schedule's month-by-month display rows.

    One :class:`~app.services.amortization_engine.AmortizationRow` per
    accrual period the plan pays into (:func:`planned_periods`) -- the
    sanctioned display class the confirmed history already comes in, so the
    table reads one shape from origination to payoff -- with, beside it, the
    escrow that period's payments impounded (the fold's own
    :attr:`~app.utils.money.PaymentCashSplit.escrow`, summed).  The row
    holds the period's principal and interest summed and its last
    installment's balance, and splits the P&I it paid the way a confirmed
    row does (``rate_period_engine.confirmed_amortization_row``): what the
    period paid above its contractual P&I is ``extra_payment``, the rest
    ``payment``, under the schedule-row invariant ``principal + interest ==
    payment + extra_payment`` -- so a refund past the payoff (the
    allocation's ``excess``) is in neither, as the ledger's row keeps it
    out.  The row is dated by the period's first payment and numbered by the
    period's own installment (``0`` for a payment before the loan's first
    installment, ruling R-C's early extra).

    Args:
        installments: :func:`~app.services.balance_at.loan_installments`'s
            answer, in the fold's DUE order.
        params: The loan's :class:`~app.models.loan_params.LoanParams`, for
            the payment number from origination.

    Returns:
        ``(rows, row_escrow)``: the projected rows, ``is_confirmed=False``,
        and the index-parallel escrow each row impounded.
    """
    rows = []
    row_escrow = []
    for period in planned_periods(installments):
        first, last = period[0], period[-1]
        principal = sum((i.principal for i in period), Decimal("0.00"))
        interest = sum((i.interest for i in period), Decimal("0.00"))
        extra = max(principal + interest - first.period.period_pi, Decimal("0.00"))
        rows.append(AmortizationRow(
            month=payment_number(
                params.origination_date,
                first.charge_date or first.visible_on,
            ),
            payment_date=first.visible_on,
            payment=round_money(principal + interest - extra),
            principal=principal,
            interest=interest,
            extra_payment=round_money(extra),
            remaining_balance=last.balance_after,
            is_confirmed=False,
            interest_rate=first.period.annual_rate,
        ))
        row_escrow.append(
            sum((i.escrow for i in period), Decimal("0.00")),
        )
    return rows, row_escrow


def build_schedule_context(history_rows, installments, escrow_lines, params):
    """Build the amortization-schedule template context.

    The standalone schedule route's (:mod:`app.routes.loan.schedule`) template
    context: the confirmed actuals (the composer's ledger-derived
    ``history_rows``) followed by the seam's forward plan
    (:func:`planned_schedule_rows` over
    :func:`~app.services.balance_at.loan_installments`, plan step R7d-g-3,
    ruling **R-R88**).  Four index-parallel lists are computed server-side
    (consumed via ``loop.index0``) so the schedule template renders without
    inline Jinja arithmetic (MED-04 / E-16): the row's own monthly escrow, its
    total outflow (P&I + that escrow + extra), the ARM display rate
    (storage-domain fraction times 100), and a continuous payment number from
    origination so a mid-life loan's "#" column keeps counting up instead of
    restarting at 1.

    **Every row's escrow is the one its installment was charged** (ruling
    **R-IJ**, plan step X-au-g-2b, finding **N-410**): a confirmed row's on
    its own installment date (:func:`~app.services.escrow_calculator.escrow_monthly_as_of`),
    a planned row's the accrual period's charge the fold impounded
    (:attr:`~app.utils.money.PaymentCashSplit.escrow`, summed over the
    month's payments) -- the same derivation on the same date family, and
    for a second payment inside one period the ``0.00`` the fold charged
    rather than a month's escrow resolved twice.

    **The ARM rate column reads the row's own rate and has no fallback**: a
    confirmed row carries its rate period's ``annual_rate``
    (``rate_period_engine.confirmed_amortization_row``), a planned row its
    governing period's (:attr:`~app.services.loan_ledger.PaymentOutcome.period`
    -- the standing charge's, or the calendar's for a payment no charge
    stands over), which the plan cannot leave empty.  A control asserts it:
    ``test_loan.test_schedule_terms.TestScheduleRowsResolveTheirOwnTerms``'s
    ``test_every_rendered_row_carries_its_own_rate``.

    Args:
        history_rows: The confirmed history
            (:attr:`~app.services.loan_resolver.PayoffScenarios.history_rows`).
        installments: The forward plan
            (:func:`~app.services.balance_at.loan_installments`).
        escrow_lines: The loan's escrow lines with their full version history
            (:attr:`~app.services.loan_payment_service.LoanContext.escrow_lines`);
            each CONFIRMED row resolves its own monthly escrow from them.
        params: The loan's :class:`~app.models.loan_params.LoanParams` -- its
            ``is_arm`` decides the rate column and its ``origination_date``
            numbers the rows.

    Returns:
        dict of template vars: amortization_schedule, show_rate_column,
        show_escrow_column, schedule_totals, schedule_row_escrow,
        schedule_row_totals, schedule_row_rates_pct, schedule_row_numbers.
    """
    show_rate_column = bool(params.is_arm)
    planned_rows, planned_escrow = planned_schedule_rows(installments, params)
    planned_schedule = list(history_rows) + planned_rows
    schedule_row_escrow = [
        escrow_calculator.escrow_monthly_as_of(escrow_lines, row.payment_date)
        for row in history_rows
    ] + planned_escrow
    schedule_row_totals = [
        round_money(row.payment + escrow + row.extra_payment)
        for row, escrow in zip(planned_schedule, schedule_row_escrow)
    ]
    schedule_row_rates_pct = [
        row.interest_rate * Decimal("100") for row in planned_schedule
    ] if show_rate_column else None
    schedule_row_numbers = [
        payment_number(params.origination_date, row.payment_date)
        for row in planned_schedule
    ]
    totals = _compute_schedule_totals(planned_schedule, schedule_row_escrow)
    return {
        "amortization_schedule": planned_schedule,
        "show_rate_column": show_rate_column,
        # The column is shown when the schedule CHARGES escrow anywhere in it,
        # not when the loan escrows something TODAY.  A loan whose first escrow
        # version starts mid-schedule used to render no column at all; one
        # whose escrow ends part-way through used to render today's figure on
        # every row after it ended.
        "show_escrow_column": totals.get("total_escrow", Decimal("0.00")) > 0,
        "schedule_totals": totals,
        "schedule_row_escrow": schedule_row_escrow,
        "schedule_row_totals": schedule_row_totals,
        "schedule_row_rates_pct": schedule_row_rates_pct,
        "schedule_row_numbers": schedule_row_numbers,
    }
