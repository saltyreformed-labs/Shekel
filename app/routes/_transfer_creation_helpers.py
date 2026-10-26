"""
Shekel Budget App -- Recurring-Transfer Creation Route Helpers

Shared building blocks for the routes that spin up a recurring
TransferTemplate and seed its Transfer instances:

* :func:`app.routes.investment.create_contribution_transfer` -- a
  biweekly contribution transfer into an investment / retirement
  account.
* :func:`app.routes.loan.payment_transfer.create_payment_transfer` -- a monthly P&I +
  escrow payment transfer into a debt account.
* :func:`app.routes.transfers.templates.create_transfer_template` -- the
  generic transfer-template create path (the restore path,
  ``lifecycle.unarchive_transfer_template``, ran it to fill forward until
  plan step R7d-g-2 and runs the MAINTAIN pass since).

Those routes were near-forks: the investment and loan creators ran a
byte-identical validate -> verify-source-account -> build-rule ->
build-template -> flush -> generate -> commit skeleton, diverging only
in the amount derivation, the recurrence pattern, the template name,
and the user-facing copy.  The four helpers here capture the shared
steps so each route keeps only its genuinely-distinct middle.

What a LOAN destination decides about a recurring transfer's validity bounds
-- the create door's ``settle_first_occurrence``, the update door's
``settle_destination_for_update`` and the form's ``LoanDestinationLocks`` --
accumulated here from plan step R7c-b until this module stood at pylint's
1,000-line cap, and lives in :mod:`app.routes._loan_destination` since plan
step R7d-f-5 (developer ruling **R-R78**).

Route-layer module (leading underscore = route-internal) rather than a
service because every helper consumes Flask globals (``request``,
``flash``, ``redirect``, ``url_for``, ``current_user`` -- the redirect /
url_for pair via :class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask.  None of
these helpers create or mutate a transfer directly -- every write stays
inside ``transfer_recurrence.generate_for_template`` and
``transfer_service`` -- so routing a call through this module keeps the
rule that every write of a transfer goes through ``transfer_service``
(the transfer paragraph of ``CLAUDE.md``'s Architecture).
"""
import logging
from decimal import Decimal
from typing import Any

from flask import Response, abort, flash, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_conflict_chooser import RecurrenceConflictKind
from app.routes._redirect_target import RedirectTarget
from app.services import transfer_recurrence
from app.services.cash_ledger import derived_amount_basis, resolve_transfer_amount
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.utils.auth_helpers import get_or_404

logger = logging.getLogger(__name__)


# Canonical name-collision flash for the partial-unique transfer-template
# name index.  Shared so the wording stays identical across every flush
# site (coding-standards DRY); the create-template path overrides it with
# its own non-"recurring" wording.
TRANSFER_NAME_DUP_MESSAGE: str = (
    "A recurring transfer with that name already exists."
)

# Shared validation-failure flash for the contribution / payment transfer
# forms.  Byte-identical between the investment and loan creators
# pre-extraction.
_TRANSFER_VALIDATION_FLASH: str = "Please correct the errors and try again."


def validate_and_resolve_source_account(
    schema: Any,
    *,
    dest_account_id: int,
    redirect: RedirectTarget,
) -> tuple[Account, dict[str, Any]] | Response:
    """Validate a transfer form and resolve + check its source account.

    Shared head of
    :func:`app.routes.investment.create_contribution_transfer` and
    :func:`app.routes.loan.payment_transfer.create_payment_transfer`.  Runs the four
    pre-conditions both routes enforce before building anything:

    1. The submitted form validates against ``schema``.
    2. The ``source_account_id`` it carries resolves to a row owned by
       the current user (``get_or_404`` -> ``abort(404)`` for both
       not-found and not-yours, per the security response rule).
    3. The source account is active.
    4. The source account is not the destination account.

    Args:
        schema: An instantiated Marshmallow schema exposing
            ``source_account_id`` and an optional ``amount`` (the
            investment / loan transfer schemas).  Validated and loaded
            against ``request.form``.
        dest_account_id: The destination account id from the route URL,
            compared against the submitted source to reject self-transfers.
        redirect: Where to redirect on any recoverable validation
            failure -- invalid form, inactive source, or self-transfer
            (each route's own dashboard).

    Returns:
        * ``(source_account, data)`` -- the owned, active source
          :class:`Account` and the loaded payload, when every check
          passes.
        * :class:`Response` -- a Flask redirect to ``redirect`` for a
          recoverable failure (invalid form, inactive source,
          self-transfer); the caller returns it directly.

    Raises:
        werkzeug.exceptions.NotFound: via ``abort(404)`` when the source
            account does not exist or is not owned by the current user.
    """
    errors = schema.validate(request.form)
    if errors:
        flash(_TRANSFER_VALIDATION_FLASH, "danger")
        return redirect.to_response()

    data = schema.load(request.form)
    source_account_id = data["source_account_id"]

    source_account = get_or_404(Account, source_account_id)
    if source_account is None:
        abort(404)

    if not source_account.is_active:
        flash("Source account is inactive.", "danger")
        return redirect.to_response()

    if source_account_id == dest_account_id:
        flash("Source and destination accounts must be different.", "danger")
        return redirect.to_response()

    return source_account, data


def build_recurring_transfer_template(
    *,
    source_account: Account,
    dest_account: Account,
    name: str,
    default_amount: Decimal,
) -> TransferTemplate:
    """Construct + session-add a recurring :class:`TransferTemplate`.

    Shared template-construction step of the investment and loan
    transfer creators.  Builds the row from the resolved accounts, adds it to
    the session, and returns it; the caller flushes (via
    :func:`flush_template_or_namedup_redirect`) so name-collision
    handling stays at the route layer.

    **It no longer takes the recurrence rule, since plan step R-F6.**  The rule
    carries the owning FK now, so it is authored ONTO this template
    (:func:`app.services.recurrence.author_rule`) once the template exists --
    which is after the caller's name-collision flush, because ``author_rule``
    flushes and an earlier one would surface a duplicate name as an unhandled
    ``IntegrityError``.  A template returned from here does not repeat yet, and
    both callers make it repeat one call later.

    Loan-payment settings are intentionally NOT set here: an investment
    contribution and every generic transfer get NO
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row, so every
    reader defaults them to non-derive (decision B).  The loan-payment creator
    -- the only caller that needs it -- attaches ``template.settings`` itself on
    the returned row before the flush, keeping that loan-only concern at the
    loan call site.

    Args:
        source_account: The owned, active funding account
            (``from_account``).
        dest_account: The investment / loan destination account
            (``to_account``).
        name: Display name for the template.
        default_amount: Per-period transfer amount (Decimal).

    Returns:
        The added (not yet flushed) :class:`TransferTemplate`, with no
        loan-payment settings row (the caller attaches one only for a loan
        payment).
    """
    template = TransferTemplate(
        user_id=current_user.id,
        from_account_id=source_account.id,
        to_account_id=dest_account.id,
        name=name,
        default_amount=default_amount,
    )
    db.session.add(template)
    return template


def flush_template_or_namedup_redirect(
    *,
    redirect: RedirectTarget,
    name_dup_message: str = TRANSFER_NAME_DUP_MESSAGE,
) -> Response | None:
    """Flush the session, translating a name-collision into flash+redirect.

    Wraps the
    ``try: db.session.flush() except IntegrityError`` idiom the transfer
    creators / updaters share, where the partial-unique index on the
    template name surfaces a concurrent or duplicate name as an
    :class:`IntegrityError`.  Rolls back and converts it into the
    canonical "name already exists" flash + redirect rather than a 500.

    Args:
        redirect: Where to redirect on collision.
        name_dup_message: Flash text for the collision; defaults to
            :data:`TRANSFER_NAME_DUP_MESSAGE`.  The generic create-
            template path passes its own non-"recurring" wording.

    Returns:
        * ``None`` -- the flush succeeded; the caller continues.
        * :class:`Response` -- the collision redirect; the caller
          returns it directly.
    """
    try:
        db.session.flush()
        return None
    except IntegrityError:
        db.session.rollback()
        flash(name_dup_message, "warning")
        return redirect.to_response()


#: The transfer-template kind for the shared regenerate-or-chooser flow.
#: Mutations route through transfer_recurrence (shadow-safe resolve).  It
#: lived in ``transfers/templates.py`` beside the update door until plan step
#: R7d-g-2, when the entry helper that brings a moved standing payment's rows
#: along (``_standing_payment.sync_loan_payment_start_or_refuse``, ruling
#: **R-R85**) needed the same pass from a module the update door imports; the
#: kind sits here beside :func:`generate_transfers_for_all_periods` -- the
#: leaf both readers reach without a cycle, and the module whose subject is
#: how a transfer template's rows come to exist.
TRANSFER_TEMPLATE_KIND = RecurrenceConflictKind(
    model=Transfer,
    # A transfer's amount rule, as a one-argument callable and the exact twin of
    # the transaction kind's.  It passed the resolver BARE until plan step
    # X-au-f-2, under a comment claiming a parent transfer needs no basis --
    # made false by R-BAL10; pinned off the row, one row at a time -- so N
    # conflicted loan payments resolve their loan N times, bounded by the
    # conflicted set and free for every other kind (the basis is lazy).
    resolve_amount=lambda row: resolve_transfer_amount(
        row, derived_amount_basis(row.user_id, row.scenario_id),
    ),
    regenerate_fn=transfer_recurrence.regenerate_for_template,
    preview_fn=transfer_recurrence.preview_regeneration_for_template,
    resolve_fn=transfer_recurrence.resolve_conflicts,
    update_endpoint="transfers.update_transfer_template",
)


def generate_transfers_for_all_periods(template: TransferTemplate) -> None:
    """Seed a template's Transfer instances across the user's pay periods.

    The shared ``resolve baseline scenario -> load the owner's schedule ->
    transfer_recurrence.generate_for_template`` idiom used by the
    investment / loan / transfers create paths.  Shadow-transaction
    atomicity is owned by ``generate_for_template``; this helper only
    orchestrates its inputs.  Its ``effective_from`` went at plan step
    R7d-g-2 with its one caller, the unarchive door's fill-forward.

    **It REQUIRES the baseline scenario (ruling R-BW), and the silent no-op it
    replaces was ledger row F-9.**  Every caller is a CREATE that reports
    success to the user afterwards, so "generate nothing and return normally"
    told them a recurring transfer existed that did not -- the same outcome the
    adjacent missing-period branch is written to refuse
    (``transfers/_instances.ONE_TIME_TRANSFER_NEEDS_PERIOD``).  The raise is
    answered by the one application-level handler, which rolls the pending
    create back and renders the repair.

    **The scenario and the schedule come off ONE read pass since plan step
    R7d-c-1**, where they were a ``require_baseline_scenario`` beside a
    ``calendar_for``.  ``BalanceContext.scenario_id`` makes the same R-BW
    refusal with the same exception, so the raise below is unchanged.

    Args:
        template: The flushed :class:`TransferTemplate` whose recurrence
            rule drives generation.

    Raises:
        BaselineMissingError: When the owner has no baseline scenario, so
            there is nothing to generate INTO.  Unreachable through any door
            today -- registration writes one and nothing deletes one -- which
            is why this changes no live behaviour.
    """
    ctx = BalanceContext.build(current_user.id)
    # Dereferenced BEFORE the schedule is built, so the R-BW refusal still
    # runs ahead of any derivation -- argument evaluation is left to right, so
    # naming it inline would derive the owner's calendar and then raise.
    scenario_id = ctx.scenario_id
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_pass(ctx),
        scenario_id,
    )


__all__ = [
    "TRANSFER_NAME_DUP_MESSAGE",
    "TRANSFER_TEMPLATE_KIND",
    "validate_and_resolve_source_account",
    "build_recurring_transfer_template",
    "flush_template_or_namedup_redirect",
    "generate_transfers_for_all_periods",
]
