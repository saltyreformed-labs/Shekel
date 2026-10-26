"""
Shekel Budget App -- Loan route package: recurring payment transfers.

Creates a recurring monthly transfer (RecurrenceRule + TransferTemplate +
generated Transfer records) from a source account to the debt account.  The
amount defaults to the resolver-derived monthly payment (P&I + escrow) with
live derivation, or a user-supplied override.

**The two settings doors are PER DEFINITION since plan step R7d-g-3** (ruling
**R-R83**): a loan can have any number of recurring transfers paying into it,
each with its own ``loan_payment_settings`` row, and the dashboard's payment
card offers the extra-principal and track-the-contract controls on every one.
Each door therefore names the TEMPLATE it writes in its URL and admits it only
through :func:`_require_loan_payment_definition` -- the template must be one
of the loan's own active recurring transfers, else 404.  Until that step both
doors took the loan alone and wrote whichever definition
``active_recurring_transfer_template`` picked (the oldest), which handed the
Track door a ``$50`` sweep created before the real payment and flipped THAT
to derive (plan ledger row **D49**).
"""

import logging
from datetime import date
from decimal import Decimal

from flask import Response, abort, flash, redirect, request, url_for
from flask_login import current_user

from app.enums import RecurrenceUnitEnum
from app.exceptions import (
    NotFoundError,
    ValidationError as ShekelValidationError,
)
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_creation_helpers import (
    build_recurring_transfer_template,
    flush_template_or_namedup_redirect,
    generate_transfers_for_all_periods,
    validate_and_resolve_source_account,
)
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _payment_extra_schema,
    _require_configured_loan,
    _total_payment_from_seam,
    _transfer_schema,
)
from app.services import (
    escrow_calculator,
    loan_loaders,
    loan_recurrence_sync,
    template_amount_service,
)
from app.services.pay_calendar import calendar_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.services.recurring_transfer_query import (
    active_recurring_transfer_templates,
    tracking_definition,
)
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today

logger = logging.getLogger(__name__)


def _contractual_monthly_payment(account) -> Decimal:
    """Return the loan's full contractual monthly payment today: P&I + active escrow.

    The "what the loan needs this month" figure for the create default
    (:func:`_resolve_transfer_amount`) and the auto-track switch
    (:func:`track_payment`): today's active escrow
    (:func:`~app.services.escrow_calculator.resolve_active_lines`) plus the seam
    P&I, summed by the shared
    :func:`app.routes.loan._helpers._total_payment_from_seam`.  It equals the loan
    card's displayed "Total Monthly" and the dashboard's drift comparison by
    construction -- they read the same seam ``monthly_payment`` and the same
    ``resolve_active_lines(load_escrow_lines(id), today)`` set
    (:func:`app.services.loan_payment_service.load_loan_context`) through the same
    leaf -- so the drift the dashboard shows and the amount this writes cannot
    disagree.

    Args:
        account: ORM :class:`Account` instance for the loan account (ownership
            already verified by the caller).

    Returns:
        The contractual monthly payment (P&I + escrow) as a ``Decimal``.
    """
    escrow_components = escrow_calculator.resolve_active_lines(
        loan_loaders.load_escrow_lines(account.id), date.today(),
    )
    return _total_payment_from_seam(account, escrow_components)


def _resolve_transfer_amount(account, data):
    """Resolve the loan-payment transfer amount and live-derivation flag.

    A user-supplied amount is respected verbatim (no live derivation);
    otherwise the amount defaults to the full monthly payment (P&I +
    escrow, :func:`_contractual_monthly_payment`) and opts into live derivation so
    the projected cash debit tracks the loan's monthly payment after an escrow or
    rate change instead of staying frozen at the default.

    Args:
        account: ORM :class:`Account` instance for the loan account.
        data: Validated transfer form data (mapping).

    Returns:
        Tuple of (Decimal transfer amount, bool derive_from_loan).
    """
    if "amount" in data and data["amount"] is not None:
        return data["amount"], False
    return _contractual_monthly_payment(account), True


def _created_transfer_flash(source_name, dest_name, base_amount, extra_principal):
    """Build the success flash for a created recurring loan payment.

    Names the total monthly cash (base + extra) so the operator sees what will
    actually debit, and appends the base / extra split only when a standing
    overpayment was set (the common no-extra case reads as before).

    Args:
        source_name: The funding account's display name.
        dest_name: The loan account's display name.
        base_amount: The stored base payment (P&I + escrow, or the typed amount).
        extra_principal: The standing extra principal (0.00 when none).

    Returns:
        The flash message string.
    """
    total = base_amount + extra_principal
    extra_note = (
        f" (${base_amount:,.2f} payment + ${extra_principal:,.2f} "
        f"extra principal)" if extra_principal > 0 else ""
    )
    return (
        f"Recurring monthly transfer of ${total:,.2f} created "
        f"from {source_name} to {dest_name}{extra_note}."
    )


@loan_bp.route("/accounts/<int:account_id>/loan/create-transfer", methods=["POST"])
@require_owner
def create_payment_transfer(account_id):
    """Create a recurring monthly transfer to a debt account.

    Creates a RecurrenceRule (monthly pattern), a TransferTemplate
    (from the selected source account to the debt account), and
    generates Transfer records for existing pay periods.

    The amount defaults to the computed monthly payment (P&I + escrow).
    The user may override with a custom amount.
    """
    account, params, _ = _require_configured_loan(account_id)

    # Validate the form and resolve + ownership-check the source account
    # (shared with investment.create_contribution_transfer).
    result = validate_and_resolve_source_account(
        _transfer_schema,
        dest_account_id=account_id,
        redirect=RedirectTarget("loan.dashboard", {"account_id": account_id}),
    )
    if isinstance(result, Response):
        return result
    source_account, data = result

    # Determine the transfer amount and whether it auto-derives.  A
    # user-supplied amount is respected verbatim; the computed default
    # opts into live derivation so the projected cash debit tracks the
    # loan's monthly payment after an escrow or rate change instead of
    # staying frozen at default_amount.
    transfer_amount, derive_from_loan = _resolve_transfer_amount(
        account, data,
    )
    # The standing overpayment (spec Sec. 6): stored on the settings row and
    # added live to every payment, in BOTH modes -- NOT baked into
    # ``default_amount``, which stays the base P&I + escrow (derive) or typed
    # base (manual).  Schema default 0.00 when the field is blank.
    extra_principal = data["extra_principal"]

    # Create the monthly recurrence rule, ALREADY bounded by the loan's own
    # contract (developer ruling 2026-08-15, plan step R7c-b).  Its first
    # occurrence is the first contractual installment, and ``nominal_day``
    # carries a servicer's day-31 payment through a 30-day origination month --
    # both from ``loan_recurrence_sync.loan_cadence_start``, the ONE producer of
    # that answer.  It used to be typed here as ``day_of_month=payment_day`` and
    # then overwritten by a sync a few lines below (the ``bind_rule_to_loan``
    # that went at plan step R7d-g-3), which is the shape that let the GENERIC
    # transfer form discard a user's typed date without saying so.
    cadence_start = loan_recurrence_sync.loan_cadence_start(
        RecurrenceUnitEnum.MONTH, params,
    )
    # Create transfer template via the shared builder.
    template_name = f"{source_account.name} -> {account.name} Payment"
    template = build_recurring_transfer_template(
        source_account=source_account,
        dest_account=account,
        name=template_name,
        default_amount=transfer_amount,
    )
    # Loan-payment transfers carry their loan-payment settings in a 1:1
    # ``loan_payment_settings`` row (decision B) rather than a column on the
    # generic template: ``derive_from_loan`` opts the projected cash debit into
    # live derivation so it tracks the loan's monthly payment after an escrow or
    # rate change.  Attached via the relationship so it flushes with the template
    # (the shared builder leaves generic / investment transfers with no settings
    # row, which every reader defaults to non-derive).
    template.settings = LoanPaymentSettings(
        derive_from_loan=derive_from_loan, extra_principal=extra_principal,
    )

    # Open the amount's dated series, AFTER the settings row is attached and for
    # exactly that reason (plan step X-au-a): a DERIVE-mode payment's
    # ``default_amount`` is a P&I + escrow snapshot, so it owns no stated amount
    # and must get no version -- and the write door reads the mode off the
    # settings row this line has just set.  A MANUAL payment (the operator typed
    # the base) does open a series here.
    template_amount_service.set_amount(
        template, transfer_amount, effective_on=display_today(),
    )

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("loan.dashboard", {"account_id": account_id}),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # **The cadence is authored ONTO the template** (plan step R-F6): the rule
    # carries its owner's FK, so the definition has to exist first.  After the
    # name-collision flush rather than before it, because ``author_rule``
    # flushes and an earlier one would surface a duplicate name as an unhandled
    # ``IntegrityError`` instead of that helper's redirect.  The rule is
    # reached through ``template.recurrence_rule`` from here on, so the
    # return value is not held.
    author_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            unit=RecurrenceUnitEnum.MONTH,
            starts_on=cadence_start.starts_on,
            nominal_day=cadence_start.nominal_day,
        ),
        calendar_for(current_user.id),
        template,
    )

    # The rule's START is already the loan's first contractual installment:
    # the spec above was built from ``loan_cadence_start``, the ONE producer
    # of that answer, so nothing may generate before origination (C9a) and
    # nothing here re-derives it.  A ``bind_rule_to_loan`` call stood here
    # until plan step R7d-g-3 (plan ledger row **REC-526**): it re-derived
    # the same pair through the same producer and returned at its
    # ``wanted == current`` check, a no-op by construction, and it was that
    # function's last application caller.  The CLOSING bound is not written
    # at all (plan step R7d-g): generation reads the loan's payoff through
    # the composed door, so no payment is generated past it, and the payments
    # generated below move that payoff without any stored copy to lag behind
    # them.

    # Generate transfers for existing pay periods.  ``create_transfer`` refuses
    # a payment dated before the loan originates (R-C) and a transfer OUT of a
    # loan, and the recurrence engine fans out through it -- so an unbounded or
    # mis-set rule surfaces here as a rejection rather than a 500 on a clean
    # user action.  Roll back the flushed rule / template and flash, mirroring
    # the generic transfer-template path.
    try:
        generate_transfers_for_all_periods(template)
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not create the recurring payment: {exc}", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    db.session.commit()

    logger.info(
        "Created recurring payment transfer for loan %d: $%s + $%s extra "
        "from account %d",
        account.id, transfer_amount, extra_principal, source_account.id,
    )
    flash(
        _created_transfer_flash(
            source_account.name, account.name,
            transfer_amount, extra_principal,
        ),
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


#: The track door's refusal of a second tracker (developer, 2026-09-14):
#: ONE definition tracks a loan; the card hides the control while one does,
#: and a crafted POST meets this sentence instead of a second full payment.
ANOTHER_PAYMENT_TRACKS_THE_LOAN = (
    "'{tracking}' already tracks this loan, so '{name}' cannot track it too: "
    "two tracking payments would each pay the full contractual amount."
)


def _require_loan_payment_definition(account, template_id):
    """Return the recurring transfer *template_id* names, if it pays into *account*.

    The one gate the two per-definition settings doors share (plan step
    R7d-g-3, ruling **R-R83**): the template is admitted only when it is in
    :func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`
    for THIS loan and the current owner -- owned, paying INTO the loan, active
    and carrying a rule -- which is exactly the set the dashboard's payment
    card renders a strip for.  Anything else 404s: a foreign owner's template,
    a template paying into another account, an archived or rule-less one, or
    an id that names nothing, all read the same (the project's "404 for
    not-found and not-yours" rule), so the door leaks no fact about a row it
    will not write.

    Args:
        account: The owner-checked, configured loan account
            (:func:`~app.routes.loan._helpers._require_configured_loan`).
        template_id: The template id from the route.

    Returns:
        ``(template, definitions)`` -- the
        :class:`~app.models.transfer_template.TransferTemplate`, its
        ``settings`` row loaded, and the loan's whole active set it was found
        in, so a door that must judge the template AGAINST its siblings (the
        track door's one-tracker rule) reads the list this gate already
        holds rather than querying it again.
    """
    definitions = active_recurring_transfer_templates(
        account.id, current_user.id,
    )
    for template in definitions:
        if template.id == template_id:
            return template, definitions
    abort(404)


@loan_bp.route(
    "/accounts/<int:account_id>/loan/payments/<int:template_id>/settings",
    methods=["POST"],
)
@require_owner
def update_payment_settings(account_id, template_id):
    """Update ONE recurring payment's standing extra principal.

    The dashboard's payment card posts here, once per definition.  Updates
    that definition's ``loan_payment_settings.extra_principal`` (creating the
    settings row when a legacy manual payment has none).  A changed extra
    moves the projected payoff, which the recurring payment's closing bound
    is derived from on every read (plan step R7d-g), so nothing is re-synced.
    The extra is a LIVE parameter -- applied at display, settle, and
    projection from this one value -- so no generated payment is rewritten.

    404s a cross-owner / non-loan account (``_require_configured_loan``) and
    a template that is not one of this loan's active recurring transfers
    (:func:`_require_loan_payment_definition`).
    """
    account, _params, _ = _require_configured_loan(account_id)
    template, _definitions = _require_loan_payment_definition(
        account, template_id,
    )

    dashboard = RedirectTarget("loan.dashboard", {"account_id": account_id})
    errors = _payment_extra_schema.validate(request.form)
    if errors:
        flash("Please enter a valid extra principal amount.", "danger")
        return dashboard.to_response()

    data = _payment_extra_schema.load(request.form)
    extra_principal = data["extra_principal"]

    # Update the extra on the settings row, creating it for a legacy manual
    # payment that never had one (a template with no settings row resolves to
    # non-derive, which is exactly a manual payment).
    if template.settings is None:
        template.settings = LoanPaymentSettings(
            derive_from_loan=False, extra_principal=extra_principal,
        )
    else:
        template.settings.extra_principal = extra_principal

    db.session.commit()

    logger.info(
        "Updated extra principal for loan %d payment %d to $%s",
        account.id, template.id, extra_principal,
    )
    flash(
        f"Extra principal on '{template.name}' set to "
        f"${extra_principal:,.2f} per payment.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route(
    "/accounts/<int:account_id>/loan/payments/<int:template_id>/track",
    methods=["POST"],
)
@require_owner
def track_payment(account_id, template_id):
    """Switch ONE recurring payment to auto-track the contractual amount (D3 / C7).

    The one-click resolution for the payment card's drift warning: when a
    MANUAL recurring payment has fallen short of the contractual monthly
    payment (P&I + today's escrow) after an escrow or rate change, this flips
    it to ``derive_from_loan`` so its projected cash always equals the
    contract, and resets the stored base (``default_amount``) to today's
    contract so every surface that reads it shows the current figure.  The
    card offers it on every fixed-amount definition, short or not, and the
    definition it flips is the one the URL names -- never a picked one.

    **No payment row is rewritten, and that is STRUCTURAL rather than a
    property of a read-time override.**  It used to rest on
    ``LoanPricing.live_cash`` superseding each shadow's stored figure on every
    read; from plan step X-au-g-2c-2 a transfer shadow stored no figure (it
    declared ``PARENT_TRANSFER`` from birth), and plan step
    ``balance:X-bi-6-4d-2`` deleted the shadows.  A generated payment its
    owner has not overridden stores no figure either: it declares its
    definition (``TEMPLATE``, since plan step X-au-f), and which rule prices
    it is read live off the definition (ruling **R-FK**,
    ``cash_ledger._amount_rule._rule_within_transfer_definition``): amount
    rule 4 once a settings row exists, in either mode, the mode picking the
    arm; rule 3, the definition's series, for a legacy manual payment with no
    settings row.  So the flip moves a legacy payment's rows from rule 3 to
    rule 4 by creating the row, and otherwise changes only rule 4's arm;
    either way the declaration it would otherwise have had to write is
    already there.  A payment whose figure the owner typed owns it, and the
    flip leaves it standing (the own arm is asked first, ruling **R-JM**).  A
    higher tracked payment moves the projected payoff, which the recurring
    payment's closing bound is derived from on every read (plan step R7d-g),
    so nothing is re-synced.

    404s a cross-owner / non-loan account (``_require_configured_loan``) and
    a template that is not one of this loan's active recurring transfers
    (:func:`_require_loan_payment_definition`).  **REFUSES a second
    tracker** (developer, 2026-09-14): while another definition already
    tracks the loan
    (:func:`~app.services.recurring_transfer_query.tracking_definition`) the
    flip is refused with :data:`ANOTHER_PAYMENT_TRACKS_THE_LOAN` and nothing
    is written -- the card offers no Track control in that state, so only a
    crafted POST reaches this arm.  A definition that already tracks is not
    refused by it: asking it to track again writes the same mode and today's
    contract, as before.
    """
    account, _params, _ = _require_configured_loan(account_id)
    template, definitions = _require_loan_payment_definition(
        account, template_id,
    )
    dashboard = RedirectTarget("loan.dashboard", {"account_id": account_id})

    tracking = tracking_definition(definitions)
    if tracking is not None and tracking.id != template.id:
        flash(
            ANOTHER_PAYMENT_TRACKS_THE_LOAN.format(
                tracking=tracking.name, name=template.name,
            ),
            "warning",
        )
        return dashboard.to_response()

    contract = _contractual_monthly_payment(account)
    # Flip to derive FIRST, creating the settings row for a legacy manual payment
    # that never had one (a missing settings row IS manual mode); the standing
    # extra is preserved, added live on top of the tracked base exactly as
    # before.  **The order is load-bearing since plan step X-au-a**: the stored
    # base written below is a DERIVED figure (P&I + today's escrow), so it must
    # not be recorded as a stated price in the amount series -- and the write
    # door reads the mode off the settings row, so flipping after the write
    # would stamp exactly the fake history ruling R-FI refuses.  Versions the
    # template recorded while it was manual stay as the record of what was
    # stated then.
    if template.settings is None:
        template.settings = LoanPaymentSettings(
            derive_from_loan=True, extra_principal=Decimal("0.00"),
        )
    else:
        template.settings.derive_from_loan = True
    template_amount_service.set_amount(
        template, contract, effective_on=display_today(),
    )

    # Flipping manual->derive MOVES the projected payoff whenever the two
    # figures differ (the PLANNED tier folds each projected payment's RESOLVED
    # cash, amount rule 4), which is the drift C7 flagged to get the user
    # here.  Nothing stores that payoff since plan step R7d-g: the recurring
    # payment's closing bound is derived from it on every read.
    db.session.commit()

    logger.info(
        "Switched loan %d payment %d to auto-track ($%s)",
        account.id, template.id, contract,
    )
    flash(
        f"'{template.name}' now tracks the loan automatically "
        f"(${contract:,.2f} this month).",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))
