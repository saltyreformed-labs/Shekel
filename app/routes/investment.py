"""
Shekel Budget App -- Investment & Retirement Account Routes

Thin HTTP layer for the investment / retirement dashboard, the HTMX
growth-chart fragment, the recurring-contribution-transfer creator,
and the investment-parameters POST handler.  The dashboard and
growth-chart data-assembly was extracted to
:mod:`app.services.investment_dashboard_service` in Commit 28
(MED-01 / S6-01): the route now matches the established
``savings.py`` thin-delegator shape.  The two POST handlers stay
here because they own HTTP-side validation, flash + redirect flows,
and the transactional unit-of-work boundary -- moving them to a
service would not collapse a duplication root.
"""

import logging
from decimal import Decimal

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, RecurrenceUnitEnum
from app.exceptions import NotFoundError, ValidationError as ShekelValidationError
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.ref import EmployerContributionType
from app.models.salary_profile import SalaryProfile
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    build_recurring_transfer_template,
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    validate_and_resolve_source_account,
)
from app.schemas.validation import (
    InvestmentContributionTransferSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.services import investment_dashboard_service, template_amount_service
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.utils.auth_helpers import (
    get_or_404,
    require_owned_fk,
    require_owner,
)
from app.utils.dates import display_today
from app.utils.digit_strings import integer_arg
from app.utils.money import round_money

logger = logging.getLogger(__name__)

investment_bp = Blueprint("investment", __name__)

_create_schema = InvestmentParamsCreateSchema()
_update_schema = InvestmentParamsUpdateSchema()
_transfer_schema = InvestmentContributionTransferSchema()

_DEFAULT_SUGGESTED_AMOUNT = Decimal("500.00")


@investment_bp.route("/accounts/<int:account_id>/investment")
@require_owner
def dashboard(account_id):
    """Investment/retirement account dashboard with growth projection."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    ctx = investment_dashboard_service.compute_dashboard_data(
        current_user.id, account,
    )
    ctx["salary_profile_url"] = _resolve_salary_profile_url(
        ctx.pop("_salary_profile_action", None),
        ctx.pop("_active_profile_id", None),
    )
    # #38: the employer-contribution-type <select> renders one option
    # per ref row (value = id), so the template never compares the
    # type name as a string.  ``default_*`` is the id pre-selected when
    # the account has no params row yet (the create case), so the
    # template defaults to NONE by id rather than a name literal.
    ctx["employer_contribution_types"] = (
        EmployerContributionType.query
        .order_by(EmployerContributionType.id)
        .all()
    )
    ctx["default_employer_contribution_type_id"] = (
        ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE,
        )
    )
    return render_template("investment/dashboard.html", **ctx)


@investment_bp.route("/accounts/<int:account_id>/investment/growth-chart")
@require_owner
def growth_chart(account_id):
    """HTMX fragment: growth projection chart with adjustable horizon.

    Accepts optional ``what_if_contribution`` query parameter to overlay
    a hypothetical contribution scenario.  When provided, returns a
    dual-dataset chart (committed vs. what-if) and a comparison card
    showing the balance difference at the projection horizon.

    Invalid or negative what-if values degrade gracefully to the
    single-line chart.  Zero is a valid what-if (growth-only scenario).
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("investment.dashboard", account_id=account_id))

    account = get_or_404(Account, account_id)
    if account is None:
        return "", 404

    horizon_years = request.args.get("horizon_years", type=integer_arg, default=2)
    what_if_raw = request.args.get("what_if_contribution", type=str)

    ctx = investment_dashboard_service.compute_growth_chart_data(
        current_user.id, account, horizon_years, what_if_raw,
    )
    return render_template("investment/_growth_chart.html", **ctx)


def render_balance_hero(account_id: int) -> str | None:
    """Draw the investment balance hero cell, or ``None`` if the page hides it.

    The investment / retirement page's DRAW (rulings R-CC74 / R-CC77, finding
    CC-365): the ONE function behind :func:`balance_hero` -- the Cancel /
    Escape target ``accounts.anchor._anchor_revert_url`` maps
    ``revert=investment`` to -- and behind the anchor save opened from the
    detail page's hero, which answered with the grid's cell until then.
    Renders ``investment/_balance_hero.html`` with the model-from-anchor
    balance the detail headline shows, so either answer restores the exact
    figure.  It never aborts, because the save calls it after its write has
    committed.

    Its producer opens its own read pass from the owner's id, as this GET's
    always has: that module is one of the service doors plan step
    pay_calendar:C11 closes (ledger row P56), and it is not moved here.

    **It keys on the id, and the producer is the ownership gate**: it answers
    ``None`` for an id that is not the current user's active account -- not
    found, not owned, or archived.  The save's account is already
    ownership-checked; the GET needs no second lookup.

    Args:
        account_id: The account whose hero to draw.

    Returns:
        The rendered cell, or ``None`` when the account is not among the
        owner's ACTIVE accounts -- which the GET answers with a 404 and the
        save (archived between page load and now) with an empty cell.
    """
    cell = investment_dashboard_service.compute_balance_hero_cell(
        current_user.id, account_id,
    )
    if cell is None:
        return None
    return render_template("investment/_balance_hero.html", **cell)


@investment_bp.route("/accounts/<int:account_id>/investment/balance-hero")
@require_owner
def balance_hero(account_id):
    """HTMX partial: the investment balance hero cell (Loop B P1 C4).

    The Cancel / Escape revert target for the detail page's click-to-edit
    anchor editor: ``accounts._anchor_revert_url`` maps ``revert=investment``
    here, mirroring how the cockpit's ``revert=accounts`` maps to
    ``savings.cockpit_balance``.  The cell is :func:`render_balance_hero`'s --
    the draw a save opened from the hero answers with too.

    The draw's ``None`` is the IDOR + active gate: a 404 for an account that
    is not among the user's active accounts (not found, not owned, or archived
    between page load and the revert), satisfying the 404-for-both security
    rule.  Non-HTMX requests redirect to the dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("investment.dashboard", account_id=account_id))
    cell = render_balance_hero(account_id)
    if cell is None:
        abort(404)
    return cell


def _resolve_salary_profile_url(action: str | None, profile_id: int | None):
    """Map service-side action hints to a flask URL string.

    The investment dashboard service does not import flask (boundary
    rule); it returns an action discriminator instead of a URL.
    Three states (mirroring the pre-Commit-28 inline route logic):

    * ``action == "edit"`` and ``profile_id is not None`` ->
      ``url_for("salary.edit_profile", profile_id=...)``.
    * ``action == "list"`` -> ``url_for("salary.cockpit")``.
    * Otherwise (no contribution prompt or transfer path) ->
      ``None``, matching the pre-extraction default.
    """
    if action == "edit" and profile_id is not None:
        return url_for("salary.edit_profile", profile_id=profile_id)
    if action == "list":
        return url_for("salary.cockpit")
    return None


@investment_bp.route(
    "/accounts/<int:account_id>/investment/create-contribution-transfer",
    methods=["POST"],
)
@require_owner
def create_contribution_transfer(account_id):
    """Create a recurring every-paycheck transfer to an investment account.

    Creates a RecurrenceRule (every-period pattern), a TransferTemplate
    (from the selected source to the investment account), and generates
    Transfer records for existing pay periods.

    The amount defaults to a suggested per-period contribution based on
    the annual limit and remaining periods.  The user may override it.
    """
    # Pylint: ``duplicate-code`` -- this route is the parallel near-fork of
    # ``loan.create_payment_transfer``: both run the same
    # validate-source -> compute-amount -> state-cadence -> build-template ->
    # flush -> author-cadence -> generate -> commit -> notify sequence,
    # diverging only in the
    # amount derivation, the recurrence pattern, and the user-facing copy.
    # The substantive shared logic is already extracted into
    # ``app/routes/_transfer_creation_helpers.py``; what remains duplicated
    # is only the ORDER in which this route calls those helpers, which is
    # not worth coupling two distinct account domains (investment
    # contribution caps vs loan P&I/escrow derivation) behind a multi-field
    # parameter object to dedupe (coding-standards rule 13).  One-sided
    # ``duplicate-code`` disable (see plan.md Phase 2 notes); the partner
    # ``loan.create_payment_transfer`` stays un-disabled.
    # pylint: disable=duplicate-code
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Validate the form and resolve + ownership-check the source account
    # (shared with loan.create_payment_transfer).
    result = validate_and_resolve_source_account(
        _transfer_schema,
        dest_account_id=account_id,
        redirect=RedirectTarget("investment.dashboard", {"account_id": account_id}),
    )
    if isinstance(result, Response):
        return result
    source_account, data = result

    # ONE schedule read for the whole request: the suggested amount spreads
    # the annual limit over the owner's paychecks, and the recurrence rule
    # below is anchored against the same calendar.  Two loads would be two
    # answers to one question (plan step R7a-2a).
    calendar = calendar_for(current_user.id)

    # Determine transfer amount: user override or suggested default.
    if "amount" in data and data["amount"] is not None:
        transfer_amount = data["amount"]
    else:
        # Compute suggested amount from annual limit and remaining periods.
        #
        # E-12 / HIGH-06 (Commit 24): ``is not None``, not
        # truthiness.  A stored zero cap means "no contributions
        # allowed this year" -- with no user-supplied amount we
        # refuse to create the transfer rather than fall back to
        # the pre-fix $500 default (which silently overrode the
        # user's explicit zero cap).  ``None`` continues to mean
        # "no cap configured" and falls to the $500 UX default.
        inv_params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=account_id)
            .first()
        )
        if inv_params and inv_params.annual_contribution_limit is not None:
            limit = inv_params.annual_contribution_limit
            if limit == Decimal("0"):
                flash(
                    "Annual contribution limit is $0. Set a positive "
                    "limit before creating a recurring transfer, or "
                    "supply an explicit amount.",
                    "warning",
                )
                return redirect(
                    url_for("investment.dashboard", account_id=account_id),
                )
            # The owner's OWN paycheck count, not a hardcoded 26 (plan step
            # R7a-2a): the transfer fires every paycheck, so spreading the
            # annual cap over 26 for a weekly-paid owner would suggest a
            # contribution that overshoots the cap by the end of the year.
            transfer_amount = round_money(
                calendar.cadence.annual_to_per_paycheck(limit),
            )
        else:
            transfer_amount = _DEFAULT_SUGGESTED_AMOUNT

    # State the every-period cadence (one occurrence per paycheck), starting
    # at the OPENING of the owner's schedule.  WRITTEN below, once the template
    # that owns it exists (plan step R-F6).
    #
    # That is what this route has always done and it is stated rather than
    # implied since plan step R7c-b made ``starts_on`` required: the spec
    # carried no opening bound before, and an absent bound resolved to
    # ``PayCalendar.opening_bound()`` -- so the contribution fans out across
    # every pay period the owner has, INCLUDING ones that have already closed.
    # The recurrence FORM defaults its own control to the current paycheck for
    # exactly that reason (``create_form_default_starts_on``, which measured
    # $10,000.00 of backdated rent).  Whether these two programmatic routes
    # should do the same is plan ledger row **D34**, opened rather than decided
    # here: changing it MOVES MONEY and is nothing this step was asked to do.
    contribution_cadence = RecurrenceSpec(
        user_id=current_user.id,
        unit=RecurrenceUnitEnum.PERIOD,
        starts_on=calendar.opening_bound(),
    )

    # Create transfer template via the shared builder (a contribution gets
    # no loan_payment_settings row, so every reader defaults it to non-derive;
    # only loan-payment transfers attach a settings row).
    template_name = f"{source_account.name} -> {account.name} Contribution"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
        name=template_name,
        default_amount=transfer_amount,
    )

    # Open the amount's dated series at today (plan step X-au-a).  A
    # contribution carries no loan-payment settings row, so nothing re-derives
    # its amount afterwards and the write door records it as stated; the builder
    # above also sets the column because it is NOT NULL, and plan step X-bp
    # removes that redundancy.  **The FIGURE above was computed** -- an annual
    # limit over the pay cadence, or the suggested default -- which is one of
    # the twelve stored-derived values finding **N-243** censuses (owned by
    # X-au-b); what makes recording it honest here is that no mechanism ever
    # recomputes it, so from creation on it behaves as a price the owner
    # accepted.
    template_amount_service.set_amount(
        template, transfer_amount, effective_on=display_today(),
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("investment.dashboard", {"account_id": account_id}),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # **The cadence is authored ONTO the template** (plan step R-F6): the rule
    # carries its owner's FK, so the definition has to exist first.  After the
    # name-collision flush rather than before it, because ``author_rule``
    # flushes and an earlier one would surface a duplicate name as an unhandled
    # ``IntegrityError`` instead of that helper's redirect.
    author_rule(contribution_cadence, calendar, template)

    # Generate transfers for existing pay periods.  The recurrence engine
    # fans out through ``create_transfer``, which refuses a transfer OUT of
    # an amortizing loan and, since plan step credit_card:CC-10, out of a
    # credit card -- and the dashboard's "From" picker offers every active
    # account, so a user can choose either.  Roll back the flushed rule /
    # template and flash, exactly as ``loan.create_payment_transfer`` does;
    # unhandled, the refusal was a 500 on a clean user action (found by
    # CC-10's adversarial review; the loan case had the same hole).
    try:
        generate_transfers_for_all_periods(template)
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not create the recurring transfer: {exc}", "danger")
        return redirect(url_for("investment.dashboard", account_id=account_id))

    db.session.commit()

    logger.info(
        "Created recurring contribution transfer for investment %d: "
        "$%s from account %d",
        account.id, transfer_amount, source_account.id,
    )
    flash(
        f"Recurring transfer of ${transfer_amount:,.2f} created "
        f"from {source_account.name} to {account.name}.",
        "success",
    )
    # pylint: enable=duplicate-code
    return redirect(
        url_for("investment.dashboard", account_id=account_id),
    )


@investment_bp.route("/accounts/<int:account_id>/investment/params", methods=["POST"])
@require_owner
def update_params(account_id):
    """Create or update investment parameters."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    params = (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )

    # Percent-to-fraction conversion is owned by the schemas' @pre_load
    # (F-17 / Commit 12 of the follow-up plan): the route forwards the
    # raw form payload and reads back the loaded fraction directly.
    if params:
        errors = _update_schema.validate(request.form)
        if errors:
            flash("Please correct the highlighted errors and try again.", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _update_schema.load(request.form)
        # R-SAL5: the funding profile must be the requester's own -- a
        # forged id would price this account's employer contribution off a
        # stranger's salary once salary:R14-b reads the column.
        require_owned_fk(SalaryProfile, data, "salary_profile_id")
        param_fields = {
            "assumed_annual_return", "annual_contribution_limit",
            "contribution_limit_year", "employer_contribution_type_id",
            "employer_flat_percentage", "employer_match_percentage",
            "employer_match_cap_percentage", "salary_profile_id",
        }
        for field_name, value in data.items():
            if field_name in param_fields:
                setattr(params, field_name, value)
        flash("Investment parameters updated.", "success")
    else:
        errors = _create_schema.validate(request.form)
        if errors:
            flash("Please correct the highlighted errors and try again.", "danger")
            return redirect(url_for("investment.dashboard", account_id=account_id))
        data = _create_schema.load(request.form)
        # R-SAL5: the funding profile must be the requester's own -- a
        # forged id would price this account's employer contribution off a
        # stranger's salary once salary:R14-b reads the column.
        require_owned_fk(SalaryProfile, data, "salary_profile_id")
        params = InvestmentParams(account_id=account_id, **data)
        db.session.add(params)
        flash("Investment parameters created.", "success")

    db.session.commit()
    logger.info(
        "user_id=%d updated investment params for account %d",
        current_user.id, account_id,
    )
    return redirect(url_for("investment.dashboard", account_id=account_id))
