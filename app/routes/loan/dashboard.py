"""
Shekel Budget App -- Loan route package: detail dashboard.

The loan detail page (GET): summary card, payment breakdown, multi-scenario
balance chart, escrow / rate-history panels, amortization-schedule tab, and the
recurring-transfer prompt.  The route assembles its template context by merging
the per-section dicts the private helpers below return.

It writes nothing: the recurrence ``end_date`` sync that once ran here as a
deliberate write on a GET (Risk R-4) moved to
:mod:`app.services.loan_recurrence_sync`, called from each payoff-affecting
MUTATION instead, and the payoff itself is no longer computed here at all -- it
is derived by the balance seam (plan step C8d) and read off the route context.
"""

from datetime import date
from decimal import Decimal, ROUND_DOWN

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.services.cash_ledger import DefinitionRow, definition_cash
from app.services.recurring_definition import read_definition
from app.services.recurring_transfer_query import (
    active_recurring_transfer_templates,
    loan_payment_config,
    tracking_definition,
)
from app.services.recurring_view import described, next_placement
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _forward_boundary,
    _load_loan_account,
    _load_route_context,
    _loan_inputs,
    band_chart_dates,
    build_band_chart,
    build_baseline_scenarios,
    planned_periods,
    render_loan_setup,
)
from app.services import (
    balance_at,
    escrow_calculator,
    loan_loaders,
    loan_recurrence_sync,
)
from app.services.loan_posting_service import (
    confirmed_loan_payment_history,
    loan_balance_anchor_history,
)
from app.services.balance_at import BalanceContext
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today


def _distribute_payment_percentages(parts, total_payment):
    """Compute portion percentages that sum to exactly 100.0%.

    Truncate-then-distribute: each part is truncated to one decimal
    place (ROUND_DOWN), then the residual needed to reach 100.0% is
    assigned to the largest part.  Guarantees the percentages sum to
    exactly 100.0% regardless of per-part rounding.

    Args:
        parts: List of ``(name, amount)`` tuples (Decimal amounts).
        total_payment: Decimal sum of the part amounts; must be > 0.

    Returns:
        dict mapping each part name to its Decimal percentage.
    """
    one_decimal = Decimal("0.1")
    truncated = {}
    for name, amount in parts:
        raw_pct = amount / total_payment * 100
        truncated[name] = raw_pct.quantize(one_decimal, rounding=ROUND_DOWN)

    residual = Decimal("100.0") - sum(truncated.values())
    # Assign residual to the largest portion.
    largest = max(truncated, key=truncated.get)
    truncated[largest] += residual
    return truncated


def _project_next_year_escrow(escrow_components, escrow_portion):
    """Project next year's monthly escrow when a component inflates.

    O-3: if any active line carries a positive ``inflation_rate`` and the current
    escrow portion is positive, compound today's escrow forward one whole annual
    step (:func:`~app.services.escrow_calculator.project_monthly_escrow`) so the
    dashboard can flag the likely increase.  A forward DISPLAY estimate only --
    recorded escrow is exact.

    The projection is one annual step from today (spec Sec. 8), matching the
    per-year meaning of ``inflation_rate`` -- NOT an elapsed span since a
    version's insert timestamp -- so the note is stable regardless of when the
    version was recorded or when the page is viewed.

    Args:
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`).
        escrow_portion: Decimal current monthly escrow.

    Returns:
        Decimal projected escrow when it differs from the current
        portion, otherwise None (no note shown).
    """
    has_inflation = any(
        c.inflation_rate is not None and c.inflation_rate > 0
        for c in escrow_components
    )
    if not has_inflation or escrow_portion <= Decimal("0.00"):
        return None

    next_year_escrow = escrow_calculator.project_monthly_escrow(
        escrow_components, 1,
    )
    # Only show the note if next year differs from current.
    if next_year_escrow == escrow_portion:
        return None
    return next_year_escrow


def _next_period_payments(installments):
    """Return the plan's payments in the next accrual period that meets a charge.

    The period the allocation bar reads: the first group of
    :func:`~app.routes.loan._helpers.planned_periods` (the schedule page's own
    grouping, so "this month" is spelled once) whose payments cleared any
    interest or escrow -- the first period whose charge the plan pays.  A
    payment that clears no charge precedes it: a catch-up the plan pays the
    day after the read for an occurrence due before it (ruling **R-R64**, the
    D1 clamp) against a charge the ledger's own payment already met, or an
    extra before the loan's first installment (ruling R-C); both are pure
    principal and neither is the coming month's payment.  A plan that meets no
    charge at all (a loan charging nothing) reads its first period.

    Args:
        installments: The seam's forward plan
            (:func:`~app.services.balance_at.loan_installments`), in the
            fold's DUE order.

    Returns:
        The period's installments in the fold's DUE order; empty for an empty plan.
    """
    periods = planned_periods(installments)
    for period in periods:
        if any(
            installment.interest > Decimal("0.00")
            or installment.escrow > Decimal("0.00")
            for installment in period
        ):
            return period
    return periods[0] if periods else []


def _compute_payment_breakdown(installments, escrow_components):
    """Build the payment allocation breakdown for the next accrual period's payments.

    The seam's fold has already split every installment the plan will pay
    (:func:`~app.services.balance_at.loan_installments`, plan step R7d-g-3,
    ruling **R-R88**): what a payment's cash does -- the interest it pays,
    the escrow it impounds, the principal it retires (any standing extra
    inside it) -- is read off those allocations, summed over the next
    accrual period the plan pays a charge in (:func:`_next_period_payments`),
    so the bar shows what the owner's own payments do that month rather than
    what the contract's would, and agrees with the band chart, the payoff
    chip and the schedule page, which fold and group the same plan.

    **A period that does not cover what has accrued is said, not drawn
    wrong.**  The fold charges every accrual period since the loan's last
    assertion, so a loan behind on its payments meets several periods'
    interest at its next installment, and the allocation's principal is
    NEGATIVE (the balance grows).  A composition bar cannot draw a negative
    segment: the principal segment is floored at zero, the interest segment
    is the cash that actually went to interest (the cash less the escrow),
    and ``shortfall`` carries how far short of the accrued charges the
    period's payments fell, for the card to say.

    Percentages are computed with a truncate-then-distribute algorithm
    to guarantee they sum to exactly 100.0%.

    Args:
        installments: The seam's forward plan, in the fold's DUE order.
        escrow_components: Today's active escrow lines, resolved
            (:class:`~app.services.escrow_calculator.ResolvedEscrowLine`),
            for the next-year escrow projection beside the bar.

    Returns:
        dict with breakdown data, or None when the plan holds no payment or
        the period's payments move nothing.
    """
    period = _next_period_payments(installments)
    if not period:
        return None
    cash = sum((installment.cash for installment in period), Decimal("0.00"))
    principal = sum(
        (installment.principal for installment in period),
        Decimal("0.00"),
    )
    interest = sum(
        (installment.interest for installment in period),
        Decimal("0.00"),
    )
    escrow_portion = sum(
        (installment.escrow for installment in period), Decimal("0.00"),
    )

    if principal < Decimal("0.00"):
        shortfall = -principal
        principal_portion = Decimal("0.00")
        interest_portion = cash - escrow_portion
    else:
        shortfall = Decimal("0.00")
        principal_portion = principal
        interest_portion = interest
    total_payment = principal_portion + interest_portion + escrow_portion

    if total_payment <= Decimal("0.00"):
        return None

    truncated = _distribute_payment_percentages(
        [
            ("principal", principal_portion),
            ("interest", interest_portion),
            ("escrow", escrow_portion),
        ],
        total_payment,
    )
    next_year_escrow = _project_next_year_escrow(
        escrow_components, escrow_portion,
    )

    return {
        "principal": principal_portion,
        "interest": interest_portion,
        "escrow": escrow_portion,
        "total": total_payment,
        "principal_pct": truncated["principal"],
        "interest_pct": truncated["interest"],
        "escrow_pct": truncated["escrow"],
        # The period's own installment date: the charge's, or the payment's
        # for one before the loan's first installment.
        "payment_date": period[0].charge_date or period[0].visible_on,
        "shortfall": shortfall,
        "next_year_escrow": next_year_escrow,
    }


def _build_payment_summary(
    current_owed, monthly_payment, installments, escrow_components,
):
    """Build the loan-card payment-summary template context.

    Bundles what the loan owes today (the seam's), the total monthly
    payment (P&I + escrow), the next period's allocation, and
    the escrow display list.  The allocation reads the seam's forward plan
    so it reflects the next planned payments, not the contractual one when
    the user is under-/over-paying.

    Args:
        current_owed: What the loan owes today (``ctx.current_owed`` --
            the seam's fold, plan C4, through ``liability_sign.owed``).
        monthly_payment: The loan's P&I payment (``ctx.monthly_payment`` -- the
            seam figure), the base the total payment adds escrow to.
        installments: The seam's forward plan
            (:func:`~app.services.balance_at.loan_installments`).
        escrow_components: Today's active escrow lines.

    Returns:
        dict of template vars: current_principal_display, total_payment,
        payment_breakdown.  The escrow card display model
        (``escrow_components``) is built separately in the route via
        :func:`app.services.escrow_calculator.build_escrow_card`, which needs the
        raw lines + the forward-only boundary rather than the resolved-today set.
    """
    return {
        # Plan C4: the seam's fold; equals the /savings debt card balance
        # and the net-worth liability (same seam, same resolution).
        "current_principal_display": current_owed,
        "total_payment": escrow_calculator.calculate_total_payment(
            monthly_payment, escrow_components,
        ),
        "payment_breakdown": _compute_payment_breakdown(
            installments, escrow_components,
        ),
    }


def _build_band_context(ctx, scenarios, installments, params):
    """Build the dashboard's band-chart template context.

    Wraps :func:`._helpers.build_band_chart` (the seam's balance at every
    date of the contractual x-axis, :func:`._helpers.band_chart_dates`,
    which the client splits at the confirmed / projected boundary) and
    derives ``has_chart`` -- the band renders the chart when the line has
    points, otherwise a "paid off" note.  The client (``loan_detail.js``)
    reads the serialized ``band_chart`` dict from ``data-chart`` and
    overlays the payoff lever's accelerated preview onto it.

    Args:
        ctx: The route context (:func:`._helpers._load_route_context`) --
            its pass and its derived payoff bound the grid.
        scenarios: The baseline :class:`PayoffScenarios` from
            :func:`._helpers.build_baseline_scenarios`, for the grid.
        installments: The plan as it stands
            (:func:`~app.services.balance_at.loan_installments`), read once
            by the route and shared with the allocation bar.
        params: The loan's :class:`LoanParams`, whose installment calendar
            extends the grid past the contract.

    Returns:
        dict of template vars: band_chart (the serializable dict), has_chart.
    """
    band_chart = build_band_chart(
        ctx.account, ctx.balance_ctx,
        band_chart_dates(
            scenarios, ctx.payoff_date, installments, params,
            ctx.figures.terms.recorded_start,
        ),
    )
    return {
        "band_chart": band_chart,
        "has_chart": bool(band_chart["balance"]),
    }


def _transfer_prompt_context(account):
    """Resolve the set-up-a-recurring-payment prompt: the payment card's empty state.

    Rendered inside the payment card when NO active recurring transfer pays
    into this loan (plan step R7d-g-3 moved it there from a page-top alert):
    the eligible source accounts (active, non-amortizing, excluding this
    account), the default source (the checking account, if any) and the day
    the first payment would fall.  The caller renders it only for a loan with
    no definition, so nothing here tests for one.

    Args:
        account: The loan account (scopes the source-account picker).

    Returns:
        A dict of template vars: source_accounts, default_source_id,
        first_payment_on.
    """
    source_accounts = (
        db.session.query(Account)
        .join(AccountType)
        .filter(
            Account.user_id == current_user.id,
            Account.is_active.is_(True),
            Account.id != account.id,
            AccountType.has_amortization.is_(False),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    # Default to the checking account if one exists.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    default_source_id = next(
        (acct.id for acct in source_accounts
         if acct.account_type_id == checking_type_id),
        None,
    )
    # When the FIRST PAYMENT falls, from the same producer the create route
    # authors it with (plan step R7c-b).  The prompt's form collects no
    # recurrence controls at all -- the route derives the whole cadence -- so
    # without this the user clicks "Create Recurring Transfer" and learns when
    # it starts only by opening the template afterwards.  Reading
    # ``loan_cadence_start`` rather than restating ``origination_date +
    # payment_day`` is what keeps the promise and the write one statement.
    #
    # ``None`` for an unconfigured loan, which is also the state
    # ``create_payment_transfer`` refuses: the prompt renders without a date
    # rather than inventing one.
    params = loan_loaders.load_loan_params(account.id)
    return {
        "source_accounts": source_accounts,
        "default_source_id": default_source_id,
        "first_payment_on": None if params is None else (
            loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            ).starts_on
        ),
    }


def _payment_strips(definitions, total_payment, balance_ctx):
    """Build the payment card's strip per recurring transfer INTO the loan.

    One strip per definition (plan step R7d-g-3, ruling **R-R83**), each
    carrying what its controls act on and what its figure means: the
    definition itself (its name and the link to its edit form), its cadence
    in the Recurring surface's own words
    (:func:`~app.services.recurring_view.described` over the composed
    reading, so a definition reads the same on both pages), its MODE off the
    settings row (:func:`~app.services.recurring_transfer_query.loan_payment_config`:
    tracks the loan, or a fixed amount), what its NEXT payment will debit,
    the standing extra inside that, and the underpayment drift a fixed
    definition may show (:func:`_payment_drift`).

    **The next payment is priced by the amount model and nothing else.**
    The definition's next placement on or after the pass's as-of
    (:func:`~app.services.recurring_view.next_placement`, off the one reading
    the cadence words come from) is priced through
    :func:`~app.services.cash_ledger.definition_cash` -- rule 4 for a loan
    payment in either mode, rule 3 for a definition with no settings row --
    which is exactly what the row for that occurrence carries or would carry
    (ruling **R-R67**).  Today's contractual total and the stored
    ``default_amount`` were the first cut's two spellings of this figure, and
    an adversarial review named both wrong: rule 4 prices an installment on
    its OWN due date (an escrow version or a recast between today and then
    parts the two), and ``default_amount`` is the NEWEST price the series
    states, not the one in effect.  The drift a fixed definition shows
    compares the same priced base, so a strip's figure and its shortfall
    sentence are one value.  A definition whose reading places nothing on or
    after the as-of -- a stop already passed -- has no next payment, and the
    strip says so instead of pricing a date it will not pay on.

    Args:
        definitions: The loan's active recurring transfers, oldest first
            (:func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`,
            settings rows loaded).
        total_payment: The loan's contractual monthly payment today (P&I +
            escrow), the loan card's own figure and the drift's other side.
        balance_ctx: The read pass: its calendar places the occurrences, its
            amount basis prices them, its ``as_of`` is the display boundary.

    **One tracker per loan** (developer, 2026-09-14): while a definition
    tracks the loan (:func:`~app.services.recurring_transfer_query.tracking_definition`,
    the same read the track door refuses on) no other strip offers the Track
    control, and a fixed strip's shortfall sentence names the definition
    that tracks instead; ``can_track`` carries that per strip.

    Returns:
        A list of dicts, one per definition, in the definitions' order:
        template, recurrence, tracks_loan, can_track, tracking_name,
        next_due, per_payment, base, extra, drift -- ``next_due``,
        ``per_payment``, ``base`` and ``drift`` ``None`` for a definition
        with no upcoming payment; ``tracking_name`` the tracker's name or
        ``None``.

    Raises:
        AmountUnresolvable: A definition states no price for its next
            occurrence (an empty series) -- exactly where its written row
            would refuse, and where the page's own payoff chip already does.
    """
    tracking = tracking_definition(definitions)
    strips = []
    for template in definitions:
        tracks_loan, extra = loan_payment_config(template)
        reading = read_definition(template, balance_ctx)
        placed = next_placement(
            template.recurrence_rule, reading, balance_ctx.as_of,
        )
        if placed is None:
            next_due = per_payment = base = drift = None
        else:
            period, next_due = placed
            per_payment = definition_cash(
                DefinitionRow(
                    template=template,
                    due_date=next_due,
                    period_start=period.start_date,
                    to_account_id=template.to_account_id,
                ),
                balance_ctx.amounts(),
                subject=f"Recurring payment {template.id}'s next occurrence",
            )
            base = per_payment - extra
            # A definition that tracks the loan resolves to the contract on
            # every read (rule 4's derive arm), so it can never drift.
            drift = None if tracks_loan else _payment_drift(base, total_payment)
        strips.append({
            "template": template,
            "recurrence": described(template.recurrence_rule, reading.resolved),
            "tracks_loan": tracks_loan,
            "can_track": not tracks_loan and tracking is None,
            "tracking_name": None if tracking is None else tracking.name,
            "next_due": next_due,
            "per_payment": per_payment,
            "base": base,
            "extra": extra,
            "drift": drift,
        })
    return strips


def _payment_drift(stored, total_payment):
    """Return a FIXED recurring payment's underpayment-drift context, or None.

    Warns (ruling D3, step C7) when a fixed payment's base -- what its next
    row is priced at less the standing extra, the amount model's figure
    (:func:`_payment_strips`); until plan step R7d-g-3 the stored
    ``default_amount``, the NEWEST stated price rather than the one in effect
    -- is LESS than today's contractual monthly payment (``total_payment`` =
    resolved P&I + active escrow), i.e. the transfer underfunds the loan after
    an escrow or rate change.  Both figures are extra-free (the standing
    ``extra_principal`` is added live on top of each and so cancels in the
    comparison), so this measures the base drift regardless of any
    overpayment.  **Per definition since plan step R7d-g-3**: the payment card
    asks it of every FIXED recurring transfer into the loan and renders the
    answer on that definition's own strip, where until then the page asked it
    of the one definition the tie-break picked and rendered a page-top alert.
    A definition that TRACKS the loan is never asked: its projected cash is
    recomputed to the contract on every read (amount rule 4, since plan step
    X-au-g-2c-2 -- the shadow is DERIVED and stores no figure to go stale), so
    :func:`_payment_strips` answers ``None`` for it before this runs.  Pure
    over the two figures, so the base a strip SHOWS and the base its shortfall
    sentence names are one value.

    Returns ``None`` -- no warning -- for a payment at or ABOVE contract:
    underpayment-only, so a deliberate overpayment never trips it.

    Args:
        stored: The fixed payment's base: its next row's priced cash less the
            standing extra.
        total_payment: The loan's contractual monthly payment today (P&I +
            escrow) -- the same figure the loan card displays and
            :func:`app.routes.loan.payment_transfer.track_payment` writes, so
            clicking the warning's action drives the drift to exactly zero.

    Returns:
        ``{"stored": Decimal, "contract": Decimal, "shortfall": Decimal}`` when the
        fixed payment is short, else ``None``.
    """
    if stored >= total_payment:
        return None
    return {
        "stored": stored,
        "contract": total_payment,
        "shortfall": total_payment - stored,
    }


def _load_collateral_candidates(user_id):
    """Return the user's active Property accounts for the secured-by picker.

    The loan dashboard's "Secured by" picker offers the physical assets a
    loan can be secured by (``has_appreciation`` types, i.e. Property), so a
    mortgage / HELOC can be grouped with the home it is secured by and
    equity rendered.  Empty when the user has none -- the template then
    shows a "create a property first" prompt instead of an empty dropdown.

    Args:
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        list[Account]: active Property accounts, ordered for display.
    """
    return (
        db.session.query(Account)
        .join(AccountType)
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
            AccountType.has_appreciation.is_(True),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )


def _build_measured_context(account, balance_ctx: BalanceContext, current_year):
    """Build the loan detail page's ledger-MEASURED template context.

    The Loop B rebuild surfaces the genesis ledger's real, paid facts the page
    previously computed but never showed (docs/design/loan_audit.md): the
    interest and principal actually PAID this calendar year (the two band chips),
    the confirmed payment-history rows (each real cash / principal / interest /
    escrow split), and the balance-anchor drift scorecard (each opening /
    true-up recorded balance vs the ledger's computed balance just before it).

    **The two YTD chips fold the loan's SOURCE events (step C6c).**  They read the
    balance seam's paid-in-year producers
    (:func:`app.services.balance_at.loan_interest_paid_in_year` /
    :func:`~app.services.balance_at.loan_principal_paid_in_year`), which sample the
    read pass's memoized walk (:meth:`BalanceContext.loan_walk`) the balance hero
    already folded -- so the loan is walked ONCE for the whole page.  Both are
    TOTAL (a configured loan always folds a real ``Decimal``, ``0.00`` when it has
    paid nothing this year), where the posting readers they replaced returned
    ``None`` for a cold posting cache and hid the chip; the loan-detail page
    renders only for a configured loan, so the chips always show the real figure.

    The payment history and the anchor scorecard read the loan's ledger walk and
    return ``None`` for an account with no terms; the template gates those two on
    truthiness, so ``[]`` (an originated loan with no confirmed payment yet) hides
    them exactly as ``None`` does.

    Args:
        account: The loan account (its ``id`` scopes the history / anchor readers;
            the object itself keys the seam's memoized walk for the chips).
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext` -- its
            ``scenario_id`` scopes every producer and its ``as_of``
            (``date.today()``) is the history / anchor display boundary.  Shared
            with the balance hero so the chips fold the memoized walk.
        current_year: The DISPLAY-timezone civil year (``display_today().year``)
            the two YTD chips sum interest / principal within.  The producers
            attribute each payment by its display-tz civil paid date (the L9
            rule), so the summing year must be the display-tz year -- NOT
            ``balance_ctx.as_of.year`` (backend UTC) -- to keep the chips in the
            same civil year as the analytics Taxes tab in the New Year window where
            UTC and Eastern differ.

    Returns:
        dict of template vars: interest_paid_ytd, principal_paid_ytd,
        payment_history, balance_anchors.
    """
    return {
        "interest_paid_ytd": balance_at.loan_interest_paid_in_year(
            account, balance_ctx, current_year,
        ),
        "principal_paid_ytd": balance_at.loan_principal_paid_in_year(
            account, balance_ctx, current_year,
        ),
        "payment_history": confirmed_loan_payment_history(
            account.id, balance_ctx.scenario_id, balance_ctx.as_of,
        ),
        "balance_anchors": loan_balance_anchor_history(
            account.id, balance_ctx.scenario_id, balance_ctx.as_of,
        ),
    }


def _load_configured_loan_or_404(account_id):
    """Load a configured loan for a detail-page HTMX fragment, or 404.

    The shared gate for the hero-cell partials below: ``_load_loan_account``
    resolves cross-owner / non-existent / non-loan accounts to ``None``
    (the project's "404 for not-found and not-yours" rule), and an
    owner's loan WITHOUT ``LoanParams`` also 404s here -- the fragments
    are cells of the configured dashboard, which does not render for an
    unconfigured loan (that page shows setup.html), so no fragment of it
    exists to serve.  This deliberately differs from
    ``_require_configured_loan``'s flash-and-redirect, which suits the
    full-page POST flows but would swap a whole redirected page into an
    HTMX hero slot.

    Args:
        account_id: The loan account id from the route.

    Returns:
        ``(account, params)`` -- only for a configured, owned loan.
    """
    account, params, _ = _load_loan_account(account_id)
    if account is None or params is None:
        abort(404)
    return account, params


@loan_bp.route("/accounts/<int:account_id>/loan/balance-hero")
@require_owner
def balance_hero(account_id):
    """HTMX partial: the loan balance hero cell (D14 click-to-edit port).

    The Cancel / Escape revert target for the loan detail page's
    click-to-edit dated true-up editor, mirroring
    :func:`investment.balance_hero`: renders ``loan/_balance_hero.html``
    with the seam's current balance (the fold), so a reverted cell
    restores the exact figure the page loaded with.  There is no
    save-path revert here -- a save posts :func:`loan.true_up_balance`'s
    full-page redirect flow (see the partial's docstring).

    Non-HTMX requests redirect to the loan dashboard page (the cell is
    a fragment, not a standalone page).
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("loan.dashboard", account_id=account_id))
    account, params = _load_configured_loan_or_404(account_id)
    ctx = _load_route_context(account, params)
    return render_template(
        "loan/_balance_hero.html",
        account=account,
        current_principal_display=ctx.current_owed,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/anchor-form")
@require_owner
def anchor_form(account_id):
    """HTMX partial: the loan hero's dated true-up editor (D14).

    Returns ``loan/_anchor_edit.html`` -- the inline as-of-date +
    balance form the click-to-edit hero swaps in.  The form posts the
    existing :func:`loan.true_up_balance` redirect flow (the whole page
    re-renders on save; every dependent figure recomputes together);
    Cancel / Escape swap back through :func:`balance_hero`.  The seam's
    current balance (the fold) prefills the balance field and
    ``origination_date`` floors the date input, matching the parameters
    card's "Record balance" form bounds.

    Non-HTMX requests redirect to the loan dashboard page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("loan.dashboard", account_id=account_id))
    account, params = _load_configured_loan_or_404(account_id)
    ctx = _load_route_context(account, params)
    return render_template(
        "loan/_anchor_edit.html",
        account=account,
        params=params,
        current_principal_display=ctx.current_owed,
        today_iso=date.today().isoformat(),
    )


@loan_bp.route("/accounts/<int:account_id>/loan")
@require_owner
def dashboard(account_id):
    """Loan detail page: the balance band, what-if levers, and the section cards."""
    account, params, account_type = _load_loan_account(account_id)
    if account is None:
        abort(404)

    if params is None:
        return render_loan_setup(account, account_type)

    ctx = _load_route_context(account, params)
    scenario_id = ctx.balance_ctx.scenario_id
    today = date.today()
    # Every recurring transfer paying INTO the loan, oldest first, loaded ONCE
    # for the payment card's strips (plan step R7d-g-3, ruling R-R83).  R-4:
    # the recurring transfer's opening bound is NOT written here (that would
    # be a write on a GET); it is synced at every door that can move it.
    definitions = active_recurring_transfer_templates(
        account.id, current_user.id,
    )
    scenarios = build_baseline_scenarios(
        _loan_inputs(params, ctx.loan), account, ctx.balance_ctx,
    )
    # The seam's forward plan, split ONCE for the page (plan step R7d-g-3,
    # ruling R-R88): the allocation bar's next period and the band's grid
    # read these installments, and the band's line and the payoff chip fold
    # the same memoized plan, so the card, the chart and the chip cannot
    # diverge.
    installments = balance_at.loan_installments(account, ctx.balance_ctx)

    context = {
        "account": account,
        "account_type": account_type,
        "params": params,
        "monthly_payment": ctx.monthly_payment,
        # C8d: the "Projected payoff" chip's THREE states, from the seam's
        # DERIVED payoff -- the date the BALANCE folds to zero, not the last row
        # of the committed schedule walk this page used to read.  A date renders
        # the month; a RETIRED loan is badged "Paid off" (finding B-20, which
        # showed such a loan its ORIGINATION date as a payoff); and a loan whose
        # payment never clears it says so rather than hiding the chip, which is
        # the same underpayment C7's drift warning flags above.  ``is_retired``
        # is what separates the two ``None`` states.
        "payoff_date": ctx.payoff_date,
        "loan_is_retired": ctx.is_retired,
        "rate_history": ctx.loan.rate_history,
        # DH-#56: the rate columns the dashboard displays/edits, derived
        # from the resolver / RateHistory (the retired
        # ``LoanParams.interest_rate`` is gone).  ``current_rate`` is the
        # rate in effect today (the card display); ``origination_rate`` is
        # the loan's earliest RateHistory row -- the period-0 rate the
        # "Loan Parameters" form edits (and ``update_params`` upserts).
        # ``rate_history`` is ordered effective_date DESC, so the last
        # element is the earliest (origination) row; it is guaranteed
        # non-empty here because the seam resolved the loan (raising if no
        # origination row exists).
        "current_rate": ctx.current_rate,
        "origination_rate": ctx.loan.rate_history[-1].interest_rate,
        "monthly_escrow": ctx.loan.monthly_escrow,
        # E-18 / Commit 16: today's ISO date pre-fills the "Record Loan
        # Balance" form's as-of date and caps its ``max``.  Computed here
        # (not via a Jinja global) so a test that freezes ``date.today()``
        # sees the frozen value on the page.
        "today_iso": date.today().isoformat(),
        # Home-equity link: the Property accounts this loan can be secured
        # by drive the "Secured by" picker; the current selection is read
        # off ``account.collateral_account_id`` in the template.
        "collateral_candidates": _load_collateral_candidates(current_user.id),
    }
    context.update(_build_payment_summary(
        ctx.current_owed, ctx.monthly_payment, installments,
        ctx.loan.escrow_components,
    ))
    # The payment card: one strip per definition, each with its figure, its
    # mode and its C7 (D3) underpayment drift against the loan card's
    # already-computed total_payment (no re-fold); the set-up prompt is the
    # card's empty state.
    context["payment_strips"] = _payment_strips(
        definitions, context["total_payment"], ctx.balance_ctx,
    )
    if not definitions:
        context.update(_transfer_prompt_context(account))
    # Escrow card: the version-drawer model, built off the raw lines
    # (``ctx.loan.escrow_lines``, loaded with the same context) and keyed by the
    # forward-only boundary so each drawer row's edit / delete controls match the
    # HTMX routes' guard.  Same builder the escrow routes re-render on a swap.
    context["escrow_components"] = escrow_calculator.build_escrow_card(
        ctx.loan.escrow_lines, today, _forward_boundary(account.id, scenario_id),
    )
    # Merge candidates: every escrow line (incl. hidden removed ones) offered as a
    # source in each drawer, so a rename-split history can be reunified.
    context["merge_candidates"] = escrow_calculator.build_merge_candidates(
        ctx.loan.escrow_lines,
    )
    context.update(_build_band_context(ctx, scenarios, installments, params))
    # YTD chips sum by the user's display-tz civil year (matching the Taxes tab
    # + the L9 attribution rule), not the backend-UTC ``today.year``.  The chips
    # fold the read pass's memoized walk (ctx.balance_ctx), so the page walks the
    # loan once for the balance hero and both chips.
    context.update(_build_measured_context(
        account, ctx.balance_ctx, display_today().year,
    ))
    return render_template("loan/dashboard.html", **context)
