"""
Shekel Budget App -- Loan route package: payoff + refinance calculators.

The HTMX what-if calculators: the payoff calculator (extra-payment and
target-date modes) and the refinance comparison.  Both load the shared loan
context so their "current" baseline matches the dashboard's loan card, and
both render result partials.  The payoff lever's preview line and its
savings read the balance seam's plan fold through the same helpers the
dashboard's band chart reads (plan step R7d-g-3, ruling **R-R88**), so the
preview cannot diverge from the chart it overlays.
"""

from datetime import date
from decimal import Decimal, ROUND_CEILING

from flask import render_template, request

from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _load_loan_account,
    _load_route_context,
    _loan_inputs,
    _payoff_schema,
    _refinance_schema,
    accelerated_overlay,
    band_chart_dates,
    build_baseline_scenarios,
)
from app.services import amortization_engine, balance_at, loan_resolver
from app.services.amortization_engine import AmortizationSummary
from app.services.installment_calendar import first_installment_date
from app.utils.auth_helpers import require_owner
from app.utils.dates import months_between
from app.utils.money import round_money


def _plan_trajectory(installments):
    """Return ``(payoff date | None, remaining interest)`` of a folded plan.

    Read off the seam's own split (:func:`~app.services.balance_at.loan_installments`,
    plan step R7d-g-3, ruling **R-R88**): the payoff is the ONE rule
    :func:`~app.services.balance_at.installments_payoff` states -- the same
    the "Projected payoff" chip's fold applies -- and the interest is summed
    over the whole plan (a split past the payoff accrues on a zero balance
    and adds nothing) and rounded once at this boundary (ROUND_HALF_UP,
    E-26).  Pure over the installments the caller already holds, so the
    lever folds the plan once per trajectory, not once per figure.

    Args:
        installments: A folded plan -- as it stands, or with the lever's
            extra on top.

    Returns:
        The DUE date the balance first folds to zero (``None`` when the plan
        never clears the loan, or the loan is already retired) and the
        interest the plan pays from the pass's now.
    """
    interest = round_money(sum(
        (installment.interest for installment in installments),
        Decimal("0.00"),
    ))
    return balance_at.installments_payoff(installments), interest


def _plan_vs_contract(scenarios, committed):
    """Months and interest the owner's plan saves vs the pure contract, or ``None``.

    The plan's payoff and remaining interest (:func:`_plan_trajectory`,
    the seam's fold) against the contractual reference the composer
    projects from the same confirmed starting state
    (:attr:`~app.services.loan_resolver.PayoffScenarios.original_forward`),
    so the comparison quantifies what the owner's planned outlays save vs
    paying pure contractual from today onward.

    Args:
        scenarios: The baseline :class:`~app.services.loan_resolver.PayoffScenarios`.
        committed: ``(payoff, interest)`` of the plan as it stands.

    Returns:
        ``None`` when the contract has no forward row to compare with (a
        loan past its term still owing, which its plan is clearing);
        otherwise ``{"months": int | None, "interest": Decimal}`` -- the
        months the plan finishes AHEAD of the contract (negative when
        behind; ``None`` when the plan never clears the loan), and the
        interest saved (negative when the plan costs more), the latter
        routed through ``round_money`` (ROUND_HALF_UP, E-26).
    """
    if not scenarios.original_forward:
        return None
    committed_payoff, committed_interest = committed
    contract_interest = sum(
        (row.interest for row in scenarios.original_forward), Decimal("0.00"),
    )
    return {
        "months": (
            None if committed_payoff is None
            else months_between(
                committed_payoff, scenarios.original_forward[-1].payment_date,
            )
        ),
        "interest": round_money(contract_interest - committed_interest),
    }


def _build_payoff_summary(monthly_payment, committed, accelerated):
    """Assemble the AmortizationSummary for the extra-payment partial.

    monthly_payment from the seam figures (single source of truth); the
    committed and accelerated payoff dates and interest from the seam's
    fold (:func:`_plan_trajectory`), the same walk apart by exactly the
    lever's extra.  ``months_saved`` and ``interest_saved`` are the calendar
    months and the interest between the two trajectories, ``None`` when
    either never clears the loan.

    Args:
        monthly_payment: The loan's P&I (``ctx.monthly_payment``).
        committed: ``(payoff, interest)`` of the plan as it stands.
        accelerated: ``(payoff, interest)`` with the lever's extra on top.
    """
    committed_payoff, committed_interest = committed
    accelerated_payoff, accelerated_interest = accelerated
    clears = committed_payoff is not None and accelerated_payoff is not None
    return AmortizationSummary(
        monthly_payment=monthly_payment,
        total_interest=committed_interest,
        payoff_date=committed_payoff,
        total_interest_with_extra=accelerated_interest,
        payoff_date_with_extra=accelerated_payoff,
        months_saved=(
            months_between(accelerated_payoff, committed_payoff)
            if clears else None
        ),
        # A trajectory that never clears the loan is summed to the plan's
        # horizon, not to a payoff, so a difference against it is not a
        # saving: ``None``, said as "--" beside "Never".
        interest_saved=(
            round_money(committed_interest - accelerated_interest)
            if clears else None
        ),
    )


def _payoff_extra_payment_result(account, params, ctx, data, has_plan):
    """Render the extra-payment payoff scenario partial.

    The seam's plan fold, twice: as it stands and with the lever's extra on
    top (:func:`~app.services.balance_at.loan_installments`, then
    :func:`_plan_trajectory` over each), so the "months saved" and "interest
    saved" chips, the "current plan vs. original" line and the band chart's
    green dashed preview (:func:`._helpers.accelerated_overlay`, the same
    fold on the band's own grid) derive from ONE walk and cannot diverge from
    the chart the dashboard drew.  Until plan step R7d-g-3 this composed
    ``loan_resolver.compute_payoff_scenarios``' committed and accelerated
    slices -- a second forward walk that priced the months no generated row
    covered from the contract plus one picked definition's extra (ruling
    **R-R88**).

    Args:
        account: The loan account (ownership verified by the route).
        params: ORM :class:`LoanParams` instance.
        ctx: The route context from :func:`_load_route_context`.
        data: Validated :class:`PayoffCalculatorSchema` form data.
        has_plan: Whether the loan has a plan of its own to compare with the
            contract (:func:`payoff_calculate`).

    Returns:
        Rendered ``loan/_payoff_results.html`` response.
    """
    extra = Decimal(str(data.get("extra_monthly", "0")))
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx.loan), account, ctx.balance_ctx,
    )
    plan = balance_at.loan_installments(account, ctx.balance_ctx)
    committed = _plan_trajectory(plan)
    accelerated = _plan_trajectory(
        balance_at.loan_installments(account, ctx.balance_ctx, extra),
    )
    return render_template(
        "loan/_payoff_results.html",
        mode="extra_payment",
        payoff_summary=_build_payoff_summary(
            ctx.monthly_payment, committed, accelerated,
        ),
        overlay=accelerated_overlay(
            account, ctx.balance_ctx,
            band_chart_dates(
                scenarios, committed[0], plan, params,
                ctx.figures.terms.recorded_start,
            ),
            extra,
        ),
        has_plan=has_plan,
        plan_vs_contract=_plan_vs_contract(scenarios, committed),
    )


def _payoff_target_date_result(params, ctx, data, has_plan):
    """Render the target-date payoff scenario partial.

    Computes two answers (F-27, developer-selected "fix + reframe,
    show both" 2026-06-11):

    * The RAW required extra -- the engine's binary search against the
      contractual schedule alone, anchored at the seam's current
      balance (the fold) with the loan's rate-period terms feed
      (``loan_resolver.engine_terms``), whose governing entry today IS
      the loan card's P&I -- so the rendered ``total_monthly =
      monthly_payment + required_extra`` is internally consistent (D-2
      closure, now structural).  For a user with no recurring payment
      plan this is the only number.
    * The PLAN-AWARE answer -- when the loan has a plan of its own,
      :func:`app.services.balance_at.loan_required_extra` folds the loan's
      forward PLAN (plan step C8f): what extra is needed ON TOP of the
      payments the user is already making.  Without it, a user
      already paying $500/mo over contractual was told they need the
      full extra again (the F-27 overstatement).

      It no longer reports "when the current plan pays off" beside that
      figure.  That is the payoff CHIP's question, answered on the same
      page by the same fold; rendering it twice from two producers is how
      the panel came to contradict the chip for a delinquent loan (the
      schedule walk retires debt nobody paid -- finding B-9).  One
      question, one producer, one place on the page.

    ``calculate_remaining_months`` supplies the raw origination-to-today
    month count the raw search needs and that the resolver does not
    expose on :class:`LoanState`.

    Args:
        params: ORM :class:`LoanParams` instance (the RAW search's
            origination / term source).
        ctx: The route context from :func:`_load_route_context`.
        data: Validated :class:`PayoffCalculatorSchema` form data.
        has_plan: Whether the loan has a plan of its own
            (:func:`payoff_calculate`) -- the plan-aware answer's gate.

    Returns:
        Rendered ``loan/_payoff_results.html`` response.
    """
    target_date = data.get("target_date")
    if not target_date:
        return render_template(
            "loan/_payoff_results.html",
            error="Target date is required.",
        )

    monthly_payment = ctx.monthly_payment
    remaining_months = amortization_engine.calculate_remaining_months(
        params.origination_date, params.term_months,
    )
    required_extra = amortization_engine.calculate_payoff_by_date(
        amortization_engine.PayoffRequest(
            current_principal=ctx.current_owed,
            remaining_months=remaining_months,
            target_date=target_date,
            origination_date=date.today().replace(day=1),
            payment_day=params.payment_day,
            terms_schedule=loan_resolver.engine_terms(
                params, ctx.loan.rate_changes,
            ),
        )
    )

    plan_extra = None
    if has_plan:
        # The plan-aware answer folds the loan's forward PLAN through the seam,
        # off the SAME BalanceContext the page's payoff chip reads (step C8f), so
        # the two cannot rest on different forward models.
        plan_extra = balance_at.loan_required_extra(
            ctx.account, ctx.balance_ctx, target_date,
        )

    total_monthly = (
        round_money(monthly_payment + required_extra)
        if required_extra is not None and required_extra > 0
        else None
    )
    return render_template(
        "loan/_payoff_results.html",
        mode="target_date",
        required_extra=required_extra,
        monthly_payment=monthly_payment,
        total_monthly=total_monthly,
        has_plan=has_plan,
        plan_extra=plan_extra,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/payoff", methods=["POST"])
@require_owner
def payoff_calculate(account_id):
    """Calculate payoff scenario (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _payoff_schema.validate(request.form)
    if errors:
        return render_template(
            "loan/_payoff_results.html",
            error="Please correct the highlighted errors and try again.",
        )

    data = _payoff_schema.load(request.form)
    mode = data["mode"]

    # Shared loan context: payments, rate changes, seam balance + figures.
    # Identical to the dashboard's data loading so calculations are
    # consistent.  ``ctx.current_owed`` is the same dollar figure
    # rendered on the loan card (the seam's fold, plan C4).
    ctx = _load_route_context(account, params)

    # A retired loan has no plan to accelerate: the seam's plan is empty for
    # it and its payoff ``None``, and a "Never" beside a "Paid off" chip
    # would be the fold's answer to a question the page should not ask.
    if ctx.is_retired:
        return render_template(
            "loan/_payoff_results.html",
            error="This loan is paid off. No payoff scenario available.",
        )

    # Whether the loan has a plan of ITS OWN to compare with the contract --
    # a payment in its record, a recurring definition into it, or a projected
    # row its forward plan folds -- is the seam's answer, read off the loan's
    # one record (``LoanFigures.has_own_plan``).  A loan with none folds the
    # contract's own installments, and a comparison would read "on track,
    # $0.00 saved" of a plan that is the contract.
    has_plan = ctx.figures.has_own_plan
    if mode == "extra_payment":
        return _payoff_extra_payment_result(
            account, params, ctx, data, has_plan,
        )
    if mode == "target_date":
        return _payoff_target_date_result(params, ctx, data, has_plan)
    return render_template(
        "loan/_payoff_results.html",
        error="Invalid mode.",
    )


def _project_refinance(refi_principal, refi_rate, refi_term, payment_day):
    """Project a hypothetical refinance schedule and summarize it.

    Commit 7 of the amortization-engine split: a pure forward projection
    from a known starting state (``refi_principal`` at next month's pay
    date) that maps directly onto
    :func:`amortization_engine.project_forward` -- no replay, no extra;
    the contractual P&I drives every row.

    Args:
        refi_principal: Decimal starting balance for the refinance.
        refi_rate: Decimal annual rate (storage-domain fraction).
        refi_term: New term in months.
        payment_day: Day-of-month the payment falls on.

    Returns:
        Tuple of (refi_monthly P&I, refi_total_interest, refi_payoff
        date).
    """
    refi_monthly = amortization_engine.calculate_monthly_payment(
        refi_principal, refi_rate, refi_term,
    )
    # The refinance is a new loan originating this month, so its first payment
    # is that loan's first installment on the one calendar rule.
    schedule_start = date.today().replace(day=1)
    starting_date = first_installment_date(schedule_start, payment_day)
    refi_schedule = amortization_engine.project_forward(
        amortization_engine.ProjectionInputs(
            starting_balance=refi_principal,
            starting_date=starting_date,
            remaining_months=refi_term,
            payment_day=payment_day,
            # A refinance is one fixed-rate span: a single terms entry
            # carries the new rate and its derived level payment.
            terms_schedule=[amortization_engine.PeriodTerms(
                start_date=starting_date,
                annual_rate=refi_rate,
                monthly_pi=refi_monthly,
            )],
        ),
        extra_monthly=Decimal("0.00"),
    )
    refi_total_interest = sum(
        (row.interest for row in refi_schedule), Decimal("0.00"),
    )
    refi_payoff = (
        refi_schedule[-1].payment_date if refi_schedule
        else schedule_start
    )
    return refi_monthly, refi_total_interest, refi_payoff


def _refinance_break_even(closing_costs, monthly_savings):
    """Months to recoup closing costs from monthly savings, or None.

    Standard consumer-facing approximation assuming constant monthly
    savings: ceil(closing_costs / monthly_savings) when both are
    positive; None when there are no costs to recoup or no monthly
    savings (refinancing to a higher payment).

    Returns:
        int months, or None.
    """
    if (
        closing_costs <= Decimal("0.00")
        or monthly_savings <= Decimal("0.00")
    ):
        return None
    return int(
        (closing_costs / monthly_savings).to_integral_value(
            rounding=ROUND_CEILING,
        )
    )


def _build_refinance_comparison(current_owed, ctx, scenarios, data, params):
    """Build the refinance side-by-side comparison from validated form data.

    Compares the current loan's CONTRACTUAL forward trajectory against a
    hypothetical refinance.  The loan's PLAN (the balance seam's fold, which
    the band chart and the payoff lever read) carries its standing extra; a
    refinance comparison must instead be like-for-like -- minimum-payment
    current vs minimum-payment refi -- because a borrower could pay the same
    extra on either loan (step 8, ``docs/design/escrow_line_identity_refactor.md``
    Sec. 16).  So the current side reads the pure-contractual
    ``scenarios.original_forward`` slice (extra-free; this builder is handed
    no fold at all), while ``ctx`` supplies the current monthly P&I (the seam
    figure) and real balance (the seam's fold, both independent of
    plan-vs-contract).  The refinance principal defaults to the current real
    balance + closing costs; the user may override for cash-out refinances.
    The principal delta and its absolute magnitude are pre-computed server-side
    (MED-04 / E-16).

    The current side is measured FORWARD-ONLY -- ``original_forward`` is already
    the from-today contractual remainder -- because the refinance side is
    inherently forward-only (a brand-new loan from today): "Remaining Term" is
    the count of payments still ahead, and "Total Interest" the interest still to
    be paid.  Counting sunk history rows would skew the comparison against a
    from-today refinance.

    Args:
        current_owed: What the loan owes today (the seam's fold), read
            ONCE by the caller and threaded in so this and the paid-off gate do
            not each re-sample the seam.
        ctx: The :class:`~app.routes.loan._helpers._RouteLoanContext` for the
            current loan (its ``monthly_payment``, and its DERIVED
            ``payoff_date`` as the empty-slice fallback -- which may be ``None``
            for a retired loan or one that never clears, so the template renders
            the absence; the plan is NOT read).
        scenarios: The loan's :class:`loan_resolver.PayoffScenarios`; its
            ``original_forward`` slice is the contractual current-side baseline.
        data: Validated :class:`RefinanceSchema` form data.  ``new_rate``
            is already the storage-domain fraction (schema ``@pre_load``).
        params: ORM :class:`LoanParams` instance (payment_day source).

    Returns:
        dict of comparison fields consumed by
        ``loan/_refinance_results.html``.
    """
    closing_costs = data["closing_costs"]
    if data["new_principal"] is not None:
        refi_principal = data["new_principal"]
    else:
        refi_principal = current_owed + closing_costs
    refi_term = data["new_term_months"]

    refi_monthly, refi_total_interest, refi_payoff = _project_refinance(
        refi_principal, data["new_rate"], refi_term, params.payment_day,
    )

    # Forward-only CONTRACTUAL baseline: what is still ahead on the current loan
    # at the minimum payment (the like-for-like basis against a from-today
    # refinance; see the docstring).  ``original_forward`` is already forward-only.
    forward_rows = scenarios.original_forward
    current_remaining_interest = round_money(sum(
        (row.interest for row in forward_rows), Decimal("0.00"),
    ))

    monthly_savings = ctx.monthly_payment - refi_monthly
    principal_diff = refi_principal - current_owed

    return {
        "current_monthly": ctx.monthly_payment,
        "current_total_interest": current_remaining_interest,
        "current_payoff": (
            forward_rows[-1].payment_date if forward_rows
            else ctx.payoff_date
        ),
        "current_remaining_months": len(forward_rows),
        "current_principal": current_owed,
        "refi_monthly": refi_monthly,
        "refi_total_interest": refi_total_interest,
        "refi_payoff": refi_payoff,
        "refi_term": refi_term,
        # Term delta (new term minus current remaining), pre-computed
        # server-side so the template renders without inline arithmetic
        # (MED-04 / E-16, same rationale as principal_diff above).
        "term_diff": refi_term - len(forward_rows),
        "refi_principal": refi_principal,
        "monthly_savings": monthly_savings,
        "interest_savings": current_remaining_interest - refi_total_interest,
        "break_even_months": _refinance_break_even(closing_costs, monthly_savings),
        "closing_costs": closing_costs,
        "principal_diff": principal_diff,
        "principal_diff_abs": abs(principal_diff),
    }


@loan_bp.route("/accounts/<int:account_id>/loan/refinance", methods=["POST"])
@require_owner
def refinance_calculate(account_id):
    """Compute refinance what-if comparison scenario (HTMX).

    Compares the current loan's CONTRACTUAL forward trajectory against a
    hypothetical refinance with user-specified rate, term, closing
    costs, and optional principal override.  Returns a side-by-side
    comparison partial with monthly savings, interest savings, and
    break-even calculation.

    The "current" baseline is deliberately contractual
    (``scenarios.original_forward``), NOT the plan-aware committed schedule the
    resolver seam now produces (step 8, Sec. 16): a refinance is a like-for-like
    minimum-vs-minimum comparison, since any standing extra could be paid on
    either loan.  See :func:`_build_refinance_comparison`.

    The refinance principal defaults to the current real balance +
    closing_costs.  The user may override for cash-out refinances.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _refinance_schema.validate(request.form)
    if errors:
        return render_template(
            "loan/_refinance_results.html",
            error="Please correct the highlighted errors and try again.",
        )

    data = _refinance_schema.load(request.form)

    # Shared loan context: seam balance + figures.  Identical to the dashboard's
    # data loading so the "current" refinance baseline matches the card.
    ctx = _load_route_context(account, params)

    # Paid-off loan: no refinance comparison is meaningful.  Read the seam's
    # balance (the fold, plan C4) ONCE, and gate on it: a loan that owes nothing
    # needs no refinance.  This gate replaces the pre-C4 ``not state.schedule or
    # state.current_balance <= 0``; the schedule half was redundant for every
    # normal loan (an empty committed schedule implies a zero balance) and its
    # only divergent case -- a past-term balloon still owing a positive balance
    # with no forward rows -- is more honestly served BY a refinance comparison
    # than blocked as "paid off", so gating on the balance alone is correct.
    current_owed = ctx.current_owed
    if current_owed <= Decimal("0.00"):
        return render_template(
            "loan/_refinance_results.html",
            error=(
                "This loan is paid off. "
                "No refinance comparison available."
            ),
        )

    # Contractual current-side baseline (step 8 / Sec. 16): a like-for-like
    # comparison holds any standing extra constant on both sides, so the current
    # side reads the pure-contractual ``original_forward`` slice -- NOT the
    # owner's plan -- against a from-today minimum-payment refi.  The seam's
    # confirmed view (the fold, plan step E1d-b) seeds it from the real owed
    # balance the loan card shows.
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx.loan), account, ctx.balance_ctx,
    )

    comparison = _build_refinance_comparison(
        current_owed, ctx, scenarios, data, params,
    )
    return render_template(
        "loan/_refinance_results.html",
        comparison=comparison,
    )
