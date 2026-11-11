"""
Shekel Budget App -- Loan route package: standalone amortization schedule.

The full month-by-month amortization schedule, demoted off the loan detail page
(the Loop B rebuild: the schedule is a statement table the developer reaches
occasionally, not the page's centre of gravity) into its own route linked from
the detail page's footer.  Renders the same planned trajectory the loan card
carries -- confirmed actuals from the genesis ledger plus the plan-aware
projected payments (each generated row at its own resolved cash, a standing
extra inside it; past the last generated row the CONTRACT's installment, until
plan step R7d-g-3's next leaf re-cuts this table onto the balance seam's forward
plan) -- so the table cannot diverge from the card.
"""

from flask import render_template

from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _load_route_context,
    _loan_inputs,
    _require_configured_loan,
    build_baseline_scenarios,
    build_schedule_context,
)
from app.services import balance_at
from app.utils.auth_helpers import require_owner


@loan_bp.route("/accounts/<int:account_id>/loan/schedule")
@require_owner
def schedule(account_id):
    """Standalone month-by-month amortization schedule for a loan.

    A schedule TABLE: the confirmed actuals off the same composer call the
    detail page's band chart shares (:func:`._helpers.build_baseline_scenarios`,
    ``history_rows``) followed by the balance seam's forward plan
    (:func:`~app.services.balance_at.loan_installments`, plan step R7d-g-3,
    ruling **R-R88**) -- the identical fold the card's chip and band read -- so
    the table cannot diverge from the page it is linked from.  Guards via
    :func:`._require_configured_loan`: a cross-owner / non-loan account 404s,
    an un-configured loan redirects to its detail page (the setup surface).

    **It reads no clock, and plan step X-au-g-2b is what removed the two reads
    it had** (ruling **R-IJ**).  It resolved
    ``current_rate_baseline(..., date.today())`` for the ARM rate column's
    per-row fallback -- a branch no rendered row could reach, since every
    ``AmortizationRow`` carries its period's rate -- and passed
    ``loan.monthly_escrow``, one escrow resolved at ``date.today()``, for every
    row of a 360-month table (finding **N-410**).  Both are the builder's
    business now, and the builder resolves each row on the installment it
    renders: the loan's escrow LINES go in, not one figure off them.
    """
    account, params, account_type = _require_configured_loan(account_id)
    ctx = _load_route_context(account)
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx.loan), account, ctx.balance_ctx,
    )
    context = {
        "account": account,
        "account_type": account_type,
    }
    context.update(build_schedule_context(
        scenarios.history_rows,
        balance_at.loan_installments(account, ctx.balance_ctx),
        ctx.loan.escrow_lines, params,
    ))
    return render_template("loan/schedule.html", **context)
