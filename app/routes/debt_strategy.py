"""
Shekel Budget App -- Debt Strategy Routes

Cross-account debt payoff strategy calculator.  Loads all active debt
accounts with loan parameters, computes current real principal and
minimum payments, then calls the debt_strategy_service to simulate
snowball, avalanche, and custom payoff strategies.

The GET endpoint renders the strategy form.  The POST endpoint
(HTMX) returns a comparison partial with aggregate metrics and
per-account payoff timelines.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from flask import Blueprint, render_template, request
from flask_login import current_user
from marshmallow import ValidationError

from app.utils.auth_helpers import require_owner

from app.models.account import Account
from app.schemas.validation import DebtStrategyCalculateSchema
from app.services.debt_strategy_service import (
    DebtAccount,
    STRATEGY_AVALANCHE,
    STRATEGY_CUSTOM,
    STRATEGY_SNOWBALL,
    StrategyRequest,
    StrategyResult,
    calculate_strategy,
)
from app.services import account_service, balance_at, liability_sign
from app.services.balance_at import BalanceContext

logger = logging.getLogger(__name__)

debt_strategy_bp = Blueprint("debt_strategy", __name__)

# Single Marshmallow schema instance per process -- safe for concurrent
# use by Marshmallow contract.  Replaces the per-request hand-parsing
# at lines 234, 251, and 261 (pre-C-27) so ``extra_monthly``,
# ``strategy``, and ``custom_order`` get the same field-level Range,
# OneOf, and Length validators a JSON caller would.
_calculate_schema = DebtStrategyCalculateSchema()


# ---------------------------------------------------------------------------
# Internal control-flow + result types
# ---------------------------------------------------------------------------

class _ResultsError(Exception):
    """Abort :func:`calculate` and render the error banner.

    The calculate endpoint has a single error contract: render
    ``_results.html`` with an ``error`` message and HTTP 200 so the
    HTMX form shows an inline banner.  Every user-input failure -- a
    schema rejection, a malformed custom order, no debt accounts, or a
    simulation that raises ``ValueError`` -- raises this so the
    failures funnel through one handler in :func:`calculate` instead of
    a separate early return per failure mode.  The exception's string
    value is the user-facing message.
    """


@dataclass(frozen=True)
class _StrategyResults:
    """The strategy simulations computed for one calculate request.

    Bundled so :func:`calculate` and its render helpers pass one named
    value instead of four positional results.

    Attributes:
        baseline: The no-extra-payment avalanche run, used as the
            comparison reference point for interest/months saved.
        avalanche: Avalanche run with the user's extra payment.
        snowball: Snowball run with the user's extra payment.
        custom: Custom-order run, or ``None`` when the user did not
            select the custom strategy.
    """

    baseline: StrategyResult
    avalanche: StrategyResult
    snowball: StrategyResult
    custom: StrategyResult | None


# ---------------------------------------------------------------------------
# Shared data loading
# ---------------------------------------------------------------------------

def _load_debt_accounts(user_id):
    """Load all active debt accounts with loan parameters for a user.

    Queries accounts where the account type has ``has_amortization=True``,
    reads each loan's terms and its seam-folded balance through ONE
    ``BalanceContext`` pass, and builds :class:`DebtAccount` instances from
    them.  The same balance / ``monthly_payment`` figures appear on the
    loan card, /savings debt card, and net-worth liability, so the
    strategy comparison cannot diverge from the per-loan displays
    (E-18 / Commit 15).

    Accounts without saved :class:`LoanParams` are silently skipped --
    a user created a debt account but has not yet filled in the loan
    details.  Accounts with zero resolver-derived balance OR zero
    resolver-derived monthly payment are also skipped (paid-off or
    degenerate).

    Args:
        user_id: The authenticated user's ID.

    Returns:
        Tuple of (debt_accounts, has_arm) where:
            debt_accounts: list[DebtAccount] ready for calculate_strategy().
            has_arm: bool indicating whether any loaded account is ARM.
    """
    accounts = (
        account_service.active_accounts_query(user_id, amortizing=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )

    # ONE read pass: each loan is resolved once, and what it OWES comes from the
    # seam (``balance_at.balance_at``, through ``liability_sign.owed``) rather
    # than off a ``LoanState`` -- this page renders that figure, so it is a
    # balance-at-T consumer like any other.
    ctx = BalanceContext.build(user_id)

    debt_accounts = []
    has_arm = False

    for account in accounts:
        terms = balance_at.loan_terms(account, ctx)
        if terms is None:
            # Account exists but loan details not yet configured.
            continue
        # The seam reports the loan HELD since plan step credit_card:CC-5-5c
        # (ruling R-CC47); read raw, every loan would fail the owes-nothing
        # test below and vanish from this page.
        current_owed = liability_sign.owed(
            balance_at.balance_at(account, ctx, ctx.as_of),
        )

        if terms.is_arm:
            has_arm = True

        # Skip debts with zero principal or zero payment (fully paid
        # off or degenerate loan parameters).  Seam-derived values: a
        # settled-to-zero loan disappears here.
        if (
            current_owed <= Decimal("0")
            or terms.monthly_payment <= Decimal("0")
        ):
            continue

        # DH-#56: DebtAccount.interest_rate is the loan's CURRENT rate
        # (``state.current_rate`` -- the rate in effect today, from the
        # resolver's rate periods), replacing the retired
        # ``LoanParams.interest_rate`` column.  For a fixed-rate loan this
        # is the origination rate; for an ARM it is the rate currently in
        # effect, which is the correct rate to accrue a payoff projection
        # at.  ``debt_strategy_service`` still treats the rate as fixed
        # for the projection (R-5: it does not re-apply future ARM
        # adjustments) -- the documented, in-scope limitation.
        debt_accounts.append(DebtAccount(
            account_id=account.id,
            name=account.name,
            current_principal=current_owed,
            interest_rate=terms.current_rate,
            minimum_payment=terms.monthly_payment,
            convention=terms.convention,
        ))

    return debt_accounts, has_arm


def _schema_first_validation_message(exc):
    """Return the first user-facing message from a ``ValidationError``.

    ``DebtStrategyCalculateSchema`` raises Marshmallow's standard
    ``ValidationError`` which carries a nested ``messages`` dict.
    The HTMX UX renders a single-line error banner inside the
    ``_results.html`` partial, so this helper picks the first
    available message in deterministic order: the cross-field
    ``_schema`` key first (for the ``custom`` + missing
    ``custom_order`` case which is the most common user mistake),
    then the per-field messages in alphabetical order so two
    test runs see the same response.  The name carries that policy
    deliberately: ``app/routes/auth/_helpers.py`` has a same-purpose
    ``_first_validation_message`` with DIFFERENT semantics
    (depth-first insertion order), and the distinct names keep a
    grep-driven refactor from conflating the two.

    The fallback "Invalid input" handles a future schema change
    that produces an empty ``messages`` dict (Marshmallow itself
    has never done this in practice; the fallback is purely
    defensive against regressions).

    Args:
        exc: The ``ValidationError`` raised by ``schema.load``.

    Returns:
        A single user-facing string suitable for the
        ``_results.html`` ``error`` slot.
    """
    messages = exc.messages
    if isinstance(messages, dict):
        # ``_schema`` carries cross-field errors; surface those first
        # because they almost always describe the user-input problem
        # in domain terms ("Custom strategy requires a priority
        # order") rather than a field-coercion error.
        schema_msgs = messages.get("_schema")
        if schema_msgs:
            return _flatten_message(schema_msgs)
        # Otherwise pick the first field's first message in
        # alphabetical order so HTML form testing is deterministic.
        for field_name in sorted(messages):
            field_msgs = messages[field_name]
            flat = _flatten_message(field_msgs)
            if flat:
                return flat
    elif isinstance(messages, list) and messages:
        return _flatten_message(messages)
    return "Invalid input."


def _flatten_message(value):
    """Return the first scalar string from a Marshmallow error value.

    Marshmallow nests messages as ``list[str]`` for simple validators
    and ``dict[str, list[str]]`` for nested schemas.  This helper
    walks one level of nesting so a single-string result reaches the
    template regardless of the validator that produced it.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            flat = _flatten_message(item)
            if flat:
                return flat
        return ""
    if isinstance(value, dict):
        for key in sorted(value):
            flat = _flatten_message(value[key])
            if flat:
                return flat
        return ""
    return ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@debt_strategy_bp.route("/debt-strategy")
@require_owner
def dashboard():
    """Render the debt strategy comparison page.

    Loads all active debt accounts with loan parameters, computes
    current real principal and minimum payment for each, and renders
    the strategy form.  An empty-state message is shown when no debt
    accounts exist.
    """
    debt_accounts, has_arm = _load_debt_accounts(current_user.id)

    return render_template(
        "debt_strategy/dashboard.html",
        debt_accounts=debt_accounts,
        has_arm=has_arm,
    )


@debt_strategy_bp.route("/debt-strategy/calculate", methods=["POST"])
@require_owner
def calculate():
    """Compute debt payoff strategies and return comparison results.

    HTMX endpoint.  Returns a partial template with the comparison
    table and per-account payoff timeline.  Always computes three
    scenarios (no-extra baseline, avalanche, snowball).  When the
    user selects custom, a fourth scenario is added.

    Input validation runs through ``DebtStrategyCalculateSchema``
    (commit C-27 / F-040): ``extra_monthly`` is range-bounded,
    ``strategy`` is restricted to the three known constants, and
    the cross-field rule rejects ``custom`` without a
    ``custom_order``.  Every user-input failure -- a schema error, a
    malformed custom order, no debt accounts, or a simulation that
    raises ``ValueError`` -- is signalled as a :class:`_ResultsError`
    and rendered into the legacy ``_results.html`` partial's error
    banner, preserving the existing HTMX UX (HTTP 200, inline error
    inside the form's results target).  A custom order that names an
    account the user does not own returns 404 (the IDOR response
    rule).
    """
    today = date.today()

    try:
        extra_monthly, strategy, custom_order = _parse_calculate_form(
            request.form,
        )
        # Re-load debt accounts each request (safe, no stale data).
        debt_accounts, has_arm = _load_debt_accounts(current_user.id)
        if not debt_accounts:
            raise _ResultsError("No active debt accounts found.")
        if custom_order is not None and _custom_order_has_unknown_account(
            custom_order, debt_accounts,
        ):
            return "Not found", 404
        results = _compute_strategies(
            debt_accounts, extra_monthly, strategy, custom_order, today,
        )
    except _ResultsError as exc:
        return render_template("debt_strategy/_results.html", error=str(exc))

    comparison = _build_comparison(results)
    selected_result = _select_result(results, strategy)
    chart_data_json = _prepare_chart_data(selected_result, today)

    return render_template(
        "debt_strategy/_results.html",
        comparison=comparison,
        selected_result=selected_result,
        selected_strategy=strategy,
        has_arm=has_arm,
        chart_data_json=chart_data_json,
    )


def _parse_calculate_form(form):
    """Parse and validate the calculate form payload.

    Loads the form through :data:`_calculate_schema` and, for the
    custom strategy, coerces the comma-separated ``custom_order`` into
    a list of integers.  The schema validates ``custom_order``'s
    presence and length only; the per-element integer coercion lives
    here so a malformed entry is reported as a user-friendly error
    rather than a generic Marshmallow message.

    Args:
        form: The POSTed ``request.form`` MultiDict.

    Returns:
        Tuple of (extra_monthly, strategy, custom_order) where
        custom_order is a list[int] for the custom strategy or None
        otherwise.

    Raises:
        _ResultsError: When the schema rejects the payload (carrying
            the first user-facing validation message) or the custom
            order is not a comma-separated list of integers.
    """
    try:
        data = _calculate_schema.load(form)
    except ValidationError as exc:
        raise _ResultsError(_schema_first_validation_message(exc)) from exc

    extra_monthly: Decimal = data["extra_monthly"]
    strategy: str = data["strategy"]
    custom_order: list[int] | None = None
    if strategy == STRATEGY_CUSTOM:
        custom_raw: str | None = data.get("custom_order")
        try:
            custom_order = [int(x.strip()) for x in custom_raw.split(",")]
        except ValueError as exc:
            raise _ResultsError("Invalid custom order format.") from exc
    return extra_monthly, strategy, custom_order


def _custom_order_has_unknown_account(custom_order, debt_accounts):
    """Return True if any custom-order ID is not one of the user's debts.

    The IDOR guard for the custom strategy: ``debt_accounts`` is
    already filtered to the authenticated user's active debts, so an
    ID outside that set is either nonexistent or owned by another
    user.  Either way :func:`calculate` returns 404 (the security
    response rule: identical 404 for "not found" and "not yours").

    Args:
        custom_order: User-supplied list of account IDs.
        debt_accounts: The user's loaded :class:`DebtAccount` list.

    Returns:
        True if at least one ID is not owned by the user.
    """
    valid_ids = {d.account_id for d in debt_accounts}
    return any(aid not in valid_ids for aid in custom_order)


def _compute_strategies(debt_accounts, extra_monthly, strategy, custom_order,
                        today):
    """Run the baseline/avalanche/snowball (and optional custom) simulations.

    Always computes the three standard scenarios; adds the custom
    scenario only when the user selected it.  A simulation that raises
    ``ValueError`` is logged with its scenario label and re-raised as a
    :class:`_ResultsError` so :func:`calculate` renders the message in
    the ``_results.html`` error banner.

    Args:
        debt_accounts: The user's loaded :class:`DebtAccount` list.
        extra_monthly: The extra monthly payment to apply (the baseline
            run forces this to zero).
        strategy: The selected strategy constant.
        custom_order: User-supplied account-ID priority order, or None.
        today: The first month of every projection.

    Returns:
        A :class:`_StrategyResults` bundle; ``custom`` is None unless
        the user selected and the run produced a custom result.

    Raises:
        _ResultsError: When any simulation raises ``ValueError`` (the
            exception message is preserved for the error banner).
    """
    try:
        baseline = calculate_strategy(StrategyRequest(
            debt_accounts, Decimal("0"), STRATEGY_AVALANCHE,
            start_date=today,
        ))
        avalanche = calculate_strategy(StrategyRequest(
            debt_accounts, extra_monthly, STRATEGY_AVALANCHE,
            start_date=today,
        ))
        snowball = calculate_strategy(StrategyRequest(
            debt_accounts, extra_monthly, STRATEGY_SNOWBALL,
            start_date=today,
        ))
    except ValueError as exc:
        logger.warning("Strategy calculation failed: %s", exc)
        raise _ResultsError(str(exc)) from exc

    custom_result = None
    if strategy == STRATEGY_CUSTOM and custom_order is not None:
        try:
            custom_result = calculate_strategy(StrategyRequest(
                debt_accounts, extra_monthly, STRATEGY_CUSTOM,
                custom_order=custom_order, start_date=today,
            ))
        except ValueError as exc:
            logger.warning("Custom strategy calculation failed: %s", exc)
            raise _ResultsError(str(exc)) from exc

    return _StrategyResults(baseline, avalanche, snowball, custom_result)


def _select_result(results, strategy):
    """Pick the strategy result shown in the per-account timeline + chart.

    Mirrors the user's selection: the custom run when chosen (and
    successfully computed), otherwise snowball or avalanche.

    Args:
        results: The computed :class:`_StrategyResults` bundle.
        strategy: The selected strategy constant.

    Returns:
        The :class:`StrategyResult` to render in the timeline + chart.
    """
    if strategy == STRATEGY_CUSTOM and results.custom is not None:
        return results.custom
    if strategy == STRATEGY_SNOWBALL:
        return results.snowball
    return results.avalanche


def _build_comparison(results):
    """Build the comparison metrics dict for the template.

    Computes interest saved and months saved relative to the no-extra
    baseline for each strategy.

    Args:
        results: The computed :class:`_StrategyResults` bundle
            (baseline + avalanche + snowball + optional custom).

    Returns:
        Dict with keys for each column in the comparison table.
    """
    baseline = results.baseline
    avalanche = results.avalanche
    snowball = results.snowball
    custom_result = results.custom
    return {
        "baseline": {
            "debt_free_date": baseline.debt_free_date,
            "total_interest": baseline.total_interest,
            "total_paid": baseline.total_paid,
            "total_months": baseline.total_months,
            "interest_saved": Decimal("0.00"),
            "months_saved": 0,
        },
        "avalanche": {
            "debt_free_date": avalanche.debt_free_date,
            "total_interest": avalanche.total_interest,
            "total_paid": avalanche.total_paid,
            "total_months": avalanche.total_months,
            "interest_saved": baseline.total_interest - avalanche.total_interest,
            "months_saved": baseline.total_months - avalanche.total_months,
        },
        "snowball": {
            "debt_free_date": snowball.debt_free_date,
            "total_interest": snowball.total_interest,
            "total_paid": snowball.total_paid,
            "total_months": snowball.total_months,
            "interest_saved": baseline.total_interest - snowball.total_interest,
            "months_saved": baseline.total_months - snowball.total_months,
        },
        "custom": {
            "debt_free_date": custom_result.debt_free_date,
            "total_interest": custom_result.total_interest,
            "total_paid": custom_result.total_paid,
            "total_months": custom_result.total_months,
            "interest_saved": baseline.total_interest - custom_result.total_interest,
            "months_saved": baseline.total_months - custom_result.total_months,
        } if custom_result is not None else None,
    }


def _prepare_chart_data(result, start_date):
    """Serialize strategy balance timelines to JSON for Chart.js.

    Converts Decimal balance_timeline values to floats (presentation
    boundary -- matching loan.py _build_chart_data pattern) and
    generates month labels from start_date.

    Returns None if there are no accounts or no months to chart.

    Args:
        result: The StrategyResult for the selected strategy.
        start_date: The first month of the projection.

    Returns:
        JSON string for embedding in a data-* attribute, or None.
    """
    if not result.per_account or result.total_months == 0:
        return None

    # Generate "Mon YYYY" labels for each month in the timeline.
    # Index 0 = start_date (starting balance), index N = month N.
    labels = []
    for i in range(result.total_months + 1):
        total = start_date.month - 1 + i
        lbl_year = start_date.year + total // 12
        lbl_month = total % 12 + 1
        labels.append(date(lbl_year, lbl_month, 1).strftime("%b %Y"))

    # Build one dataset per account with floats (not Decimals).
    datasets = []
    for idx, acct in enumerate(result.per_account):
        # Presentation boundary: float() for Chart.js JSON serialization.
        data = [float(b) for b in acct.balance_timeline]
        datasets.append({
            "label": acct.name,
            "data": data,
            "colorIndex": idx,
        })

    return json.dumps({"labels": labels, "datasets": datasets})
