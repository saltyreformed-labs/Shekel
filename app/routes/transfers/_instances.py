"""
Shekel Budget App -- Transfer route package: a template's Transfer INSTANCES

What happens to the ``budget.transfers`` rows a :class:`TransferTemplate`
stands for -- creating them when the template is created, and carrying an edit
onto them afterwards.  The sibling module :mod:`app.routes.transfers.templates`
owns the template itself: its CRUD routes, its form payload, and its recurrence
rule.

The seam is the one :mod:`app.routes._recurrence_form_helpers` and
:mod:`app.routes._recurrence_conflict_chooser` already draw one layer up --
"what the definition IS" versus "what happens to the rows it produced" -- and
it was cut here for the same reason: plan step R2e-3 pushed
``transfers/templates.py`` past the 1,000-line module cap.

**The CREATE half exists because a NON-REPEATING transfer is not a
non-repeating transaction.**  A transaction template with no recurrence rule
generates nothing at creation, so there is nothing to create; the PROPAGATE
half has a transaction twin since plan step ``balance:X-bi-7a``
(:mod:`app.routes.templates._instances`), because the rows such a definition
holds -- a one-off's placed rows, a cleared cadence's survivors -- are
reached by nothing else.  A
transfer template with no rule still moves money exactly once: this module
materialises that single Transfer (one row since plan step
``balance:X-bi-6-4d-2`` deleted its two shadow transactions) and then keeps it
equal to the definition, because the
regeneration path that does that job for every recurring template
deliberately skips a rule-less one -- which is what stops a rename from
destroying it (defect D16).

Route-layer module (leading underscore = route-internal) rather than a service
because it consumes Flask ``flash`` / ``redirect`` / ``url_for`` and
``current_user``; ``CLAUDE.md::Architecture`` keeps services isolated from
Flask globals.
"""

from flask import flash, redirect, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import (
    NotFoundError,
    ValidationError as ShekelValidationError,
)
from app.extensions import db
from app.models.transfer import Transfer
from app.services.amount_ownership import derived_ownership
from app.services import transfer_recurrence, transfer_service
from app.services.scenario_resolver import require_baseline_scenario
from app.utils.balance_predicates import is_projected_clause
from app.routes._recurrence_conflict_chooser import flash_retained_notice
from app.routes._transfer_creation_helpers import (
    generate_transfers_for_all_periods,
)


ONE_TIME_TRANSFER_NEEDS_PERIOD: str = (
    "A transfer that does not repeat has to land in one pay period. Pick the "
    "pay period it belongs to, or choose a pattern to repeat it."
)
"""Refusal shown when a non-repeating transfer names no pay period."""


def _rollback_and_refuse(message):
    """Roll the pending create back, flash *message*, return to the form.

    The single refusal shape every branch of :func:`materialize_initial_transfers`
    shares.  The template and any rule are already FLUSHED by the time
    materialization runs, so a refusal has to roll back or it leaves a
    transfer definition behind that generated nothing.

    Args:
        message: The flash text explaining what was refused.

    Returns:
        A redirect ``Response`` to the create form.
    """
    db.session.rollback()
    flash(message, "danger")
    return redirect(url_for("transfers.new_transfer_template"))


def _materialize_one_time_transfer(template, start_period):
    """Create the single Transfer a NON-REPEATING transfer template stands for.

    A transfer that does not repeat still moves money exactly once, so unlike
    a rule-less transaction template -- which generates nothing and waits for
    the user to add rows by hand -- this materializes one Transfer in the
    chosen period, through ``transfer_service``, which every write of a
    transfer goes through (``CLAUDE.md``, Architecture).

    **It TAKES the resolved period, and re-fetches nothing** (plan step
    ``pay_calendar:C13-b``).  This ran ``db.session.get(PayPeriod, ...)``
    followed by ``period.user_id != current_user.id`` -- one of the EIGHT
    primary-key refetches finding **P75** counts, and the SECOND resolution of
    an id :func:`~.templates._settle_create_references` had already resolved in
    the same request.  The route resolves it once, against the owner's derived
    calendar where a foreign id is simply absent, and hands the value down.
    ``transfer_service`` still asks its own tier's question
    (``_ownership._get_owned_period``), which is the guarantee a caller that
    skips this route cannot escape.

    An ABSENT period is still refused here rather than at the route, so the two
    "this transfer has nowhere to land" answers sit together.  Both refusals
    roll back: this runs after the template is flushed.

    **The due date is the CHOSEN period's start** -- the period *start_period*
    names, which is not necessarily the one the transfer sits in
    later.  Before plan step R2e-3 this ran ``compute_due_date`` against the
    template's ``Once`` RULE, which returned exactly that: a form-authored
    ``Once`` rule carries no ``day_of_month`` (verified: all four live ``Once``
    rules have it NULL), and ``compute_due_date`` falls back to the period's
    start when there is none.  Verified against both live ``Once`` transfers on
    production -- id 154 due 2026-04-23 and id 409 due 2026-07-30, each equal
    to its CHOSEN period's start.  Transfer 409 has since been moved to an
    earlier period and its ``due_date`` no longer matches the period it sits
    in, which is why the property is stated against the chosen period and not
    the current one.

    It differed only when a day LEAKED in -- the day input is hidden for a
    non-repeating pattern but a hidden input still submits, so switching the
    picker away from Monthly left the typed day on the payload and silently
    re-dated the transfer.  With no rule there is nothing to read it from.

    **The transfer records that day as the occurrence it answers** (plan
    step ``balance:X-ci-1``, ruling **R-BAL94**: ``occurs_on = due_date`` on
    a one-time transfer, the twin of ``one_off.place_row_of``'s birth
    write).  It was born answering NO occurrence until then -- the one-time
    branch was this table's last writer of the undated, definition-linked
    shape finding **REC-516** names, and the rule ``recurrence:R19-b``
    waited on for this table -- so a cadence added to the definition later
    that names this day adopts the transfer as its occurrence, and one that
    does not retains it when it holds the owner's records and retires
    (hard-deletes) a record-free Projected one that is not overridden
    (R-BAL94, REC-516's stated cost).  Every later writer of the date keeps the pair
    equal inside ``transfer_service.update_transfer`` (**R-BAL96**); the
    two transfers born before this leaf are backfilled at ``X-ci-3``.

    Args:
        template: The persisted (flushed) TransferTemplate, with no rule.
        start_period: The paycheck the transfer lands in, already resolved
            against the owner's calendar by the route, or ``None`` when the
            submission named none.

    Returns:
        A redirect ``Response`` on an absent period or a service rejection
        (e.g. a loan as the source account) -- the caller returns it verbatim;
        ``None`` on success so the caller proceeds to commit.

    Raises:
        BaselineMissingError: When the owner has no baseline scenario, so the
            transfer has no scenario to be created IN.  Answered by the one
            application-level handler, which rolls the pending create back --
            the same guarantee :func:`_rollback_and_refuse` gives.
            Unreachable through any door today.
    """
    if start_period is None:
        return _rollback_and_refuse(ONE_TIME_TRANSFER_NEEDS_PERIOD)

    # The REQUIRING form (ruling R-BW), and the nullable one it replaces was
    # ledger row F-9: this returned ``None`` -- the caller's signal for
    # "created, go ahead and commit" -- having materialised nothing, so the
    # user was told a transfer existed that did not.  A transfer that does not
    # repeat moves money exactly once, so absence has no defined answer here
    # and the app already has one place that says so.
    scenario = require_baseline_scenario(current_user.id)

    try:
        transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=current_user.id,
                from_account_id=template.from_account_id,
                to_account_id=template.to_account_id,
                pay_period_id=start_period.period_id,
                scenario_id=scenario.id,
                # Its DEFINITION prices it (plan step X-au-f, ruling R-FI).
                # It stored ``template.default_amount`` until then, which is
                # the copy this cutover deletes: this row is template-linked,
                # so it is generated exactly like a repeating one and reads its
                # definition's series as of the ``due_date`` below.
                amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=template.category_id,
                name=template.name,
                transfer_template_id=template.id,
                due_date=start_period.start_date,
                occurs_on=start_period.start_date,
            ),
        )
    except (NotFoundError, ShekelValidationError) as exc:
        return _rollback_and_refuse(f"Could not create transfer: {exc}")
    return None


def materialize_initial_transfers(template, rule, start_period):
    """Create the initial transfer instance(s) for a freshly built template.

    Two shapes, and ``rule`` is what tells them apart since plan step R2e-3:

    * ``rule is None`` -- the template does not repeat, so exactly one
      Transfer lands in the chosen period (:func:`_materialize_one_time_transfer`).
      This used to be the ``Once`` PATTERN, whose rule is what made a rename
      destroy the transfer: regeneration swept the row and the pattern's own
      suppression guard generated nothing back (defect **D16**, measured
      1 transfer + 2 shadows -> 0 + 0).  A rule-less template is skipped by
      that sweep's gate entirely, which is what closes it.
    * a rule -- hand the template to the recurrence engine to fan out across
      every period.

    Args:
        template: The persisted (flushed) TransferTemplate.
        rule: The template's RecurrenceRule, or ``None`` when it does not
            repeat.
        start_period: The submitted start period, already resolved against
            the owner's calendar by the route (``None`` when none was
            submitted).  Required by the rule-less path, which refuses without
            it; ignored for a recurring rule, which fans out across the whole
            schedule.

    Returns:
        A redirect ``Response`` when either path hits an invalid period or the
        service rejects the transfer (e.g. a loan as the source account) -- the
        caller returns it verbatim; ``None`` on success so the caller proceeds
        to commit.
    """
    if rule is None:
        return _materialize_one_time_transfer(template, start_period)

    # **Nothing binds the rule to the destination loan here any more** (plan
    # step R7d-g-2, ruling **R-R85**).  From plan step C9a to R7d-g-1 this
    # called ``loan_recurrence_sync.bind_rule_to_loan`` before generating --
    # the transfer form offers every active account as a destination, so a
    # loan payment can be set up here as readily as on the loan page, and
    # unbounded it generated an installment into every materialized pay
    # period, including those preceding origination (3 pre-origination
    # payments on a mortgage closing 2026-04-15, each a phantom cash debit
    # and an erased payment once settled).  The create door derives that
    # start BEFORE the rule is built now (``_loan_destination
    # .settle_first_occurrence``, ruling 2026-08-15), so for the loan's
    # standing payment the bind found the date already right; and for a
    # SECOND transfer into a paid loan, whose start is its OWNER's (ruling
    # **R-R81**), it silently overwrote the date the owner typed with the
    # contract's -- the one thing it still did.
    #
    # Recurring transfer: delegate to the recurrence engine.  Wrap in the
    # SAME guard the one-time branch uses: the recurrence engine fans out
    # through ``create_transfer``, which rejects a loan as the source account
    # (a transfer OUT of an amortizing loan), so an unhandled rejection here
    # would 500 on a clean, user-reachable action (the transfer form offers
    # every active account as a source).
    try:
        generate_transfers_for_all_periods(template)
    except (NotFoundError, ShekelValidationError) as exc:
        return _rollback_and_refuse(f"Could not create transfer: {exc}")
    return None


def non_repeating_live_transfers(template):
    """Return the Transfers a NON-REPEATING template's definition still owns.

    Projected, not hand-edited, not soft-deleted -- the same three conditions
    ``_recurrence_common.classify_maintain_work`` uses to decide which
    rows a recurring template's regeneration may rewrite.  A settled transfer
    is immutable history and an overridden one is a deliberate per-instance
    change; neither follows the definition, here or there.

    Args:
        template: The ``TransferTemplate``.

    Returns:
        The matching ``Transfer`` rows, newest last.  Normally exactly one --
        the row :func:`_materialize_one_time_transfer` created -- but a
        template whose recurrence was CLEARED keeps whatever survived that
        sweep, and this must be correct for both.
    """
    return (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            is_projected_clause(Transfer),
            Transfer.is_override.is_(False),
            Transfer.is_deleted.is_(False),
        )
        .order_by(Transfer.id)
        .all()
    )


def propagate_to_non_repeating_transfers(template):
    """Push a NON-REPEATING template's edited definition onto its Transfers.

    The counterpart of regeneration for the one shape that does not
    regenerate.  A recurring template's edit reaches its rows through the
    maintain pass, which needs a RULE to say which periods it still names; a
    template with no rule names none, so a regeneration would retire every row
    it has (that is defect **D16**).  Its already-materialised Transfer is
    therefore brought into line here instead -- through
    ``transfer_service.update_transfer``, the transfer service's edit door
    (every write of a transfer goes through ``transfer_service``:
    ``CLAUDE.md``, Architecture).

    **Without this the template and its Transfer diverge silently.**  Measured
    before it existed, on the transfer create form's DEFAULT selection: a
    definition created at $500.00 and edited to $700.00 left the Recurring
    page reading $700.00 while the Transfer, both shadows, and therefore every
    balance the grid projects still read $500.00 -- under a plain "updated."
    flash.  The shape has existed since plan step R2e-1's clear branch; plan
    step R2e-3 is what made it reachable from the form, and so what owns it.

    **Every definition field a Transfer carries propagates, including the two
    ACCOUNTS** -- which is plan step R10-b, and which retires a refusal rather
    than adding a field.  The two account columns used to be excluded here and
    an account change was REFUSED at the route, for one reason: the shadow-safe
    door could not express a move, because a shadow's ``account_id`` is derived
    from the pair's endpoints when it is built and nothing re-derived it
    afterwards.  That made the same edit mean two different things -- a
    RECURRING template's account change was applied, by a regeneration that
    destroyed and rebuilt every generated row to do it.  ``update_transfer``
    moves a transfer and both legs between accounts now
    (:mod:`app.services.transfer_service._endpoints`), so this door states the
    whole definition and the refusal is gone.

    **WHAT to write is the engine's decision, not this route's**, and an
    adversarial review of R10-b is why it says so: this door applied the
    definition unconditionally, so the moment the accounts became propagable a
    non-repeating transfer holding a retained settlement record had its pair
    moved in SILENCE -- while the identical edit on a recurring template was
    retained and reported.  One rule, asked in one place
    (``transfer_recurrence.propagate_to_unruled_template``); what stays here is
    the FLASHING, which is a route's job.

    Args:
        template: The updated ``TransferTemplate``, its new field values
            already applied and flushed.

    Returns:
        ``None`` on success -- flashing the retained notice when the pass left
        a row alone; a redirect ``Response`` when the service refuses an update
        (rolled back, so nothing is half-applied).
    """
    try:
        retained = transfer_recurrence.propagate_to_unruled_template(
            template, non_repeating_live_transfers(template),
        )
    except (NotFoundError, ShekelValidationError) as exc:
        db.session.rollback()
        flash(f"Could not update transfer: {exc}", "danger")
        return redirect(url_for(
            "transfers.edit_transfer_template", template_id=template.id,
        ))
    flash_retained_notice(retained)
    return None


__all__ = [
    "ONE_TIME_TRANSFER_NEEDS_PERIOD",
    "materialize_initial_transfers",
    "non_repeating_live_transfers",
    "propagate_to_non_repeating_transfers",
]
