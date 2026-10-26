"""
Shekel Budget App -- Transfer route package: instance mutations.

Every state-changing route on a single transfer instance: the inline amount
edit (PATCH), the ad-hoc create (POST), the soft/hard delete (DELETE), and the
mark-done / cancel status actions.  The edit and status concerns share this one
module deliberately -- their service-update + commit + stale/cell response code
is near-identical parallel code; co-locating it keeps that intentional
parallelism intra-file (R0801 is cross-file only) rather than re-surfacing the
duplication the monolith hid.  Every URL and endpoint name is preserved
verbatim from the pre-split ``app/routes/transfers.py``.
"""

import logging
from functools import partial

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.category import Category
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app.models.ref import Status
from app import ref_cache
from app.enums import StatusEnum
from app.services import (
    status_seam,
    template_amount_service,
    transfer_legs,
    transfer_service,
)
from app.services.match_press import MARK_PAID, NOTHING_SHOWN, Press
from app.services.state_machine import finalised_edit_rejection
from app.exceptions import NotFoundError, ValidationError as ShekelValidationError
from app.utils.auth_helpers import require_owner
from app.utils.db_errors import is_unique_violation
from app.utils.error_fragments import (
    INVALID_REFERENCE_MSG,
    flatten_schema_errors,
)
from app.routes._authored_figure import figure_was_authored
from app.routes._typed_figure import typed_figure
from app.routes._refused_press import answer_refused_press
from app.routes._render_helpers import render_transfer_cell, transfer_side_boxes
from app.routes._shown_lines import Posted, read_posted
from app.utils.rendered_figure import as_rendered_field
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._helpers import (
    _error_transfer_response,
    _get_owned_transfer,
    _render_post_mutation_cell,
    _shown_lines_schema,
    _stale_transfer_response,
    _user_owns,
    _xfer_create_schema,
    _xfer_update_schema,
)
from app.routes.transfers.forms import redraw_full_edit

logger = logging.getLogger(__name__)

# Name of the partial unique index that backstops the ad-hoc transfer
# double-submit fix (F-050 / C-22).  Mirrors the literal in
# ``app/models/transfer.py:Transfer.__table_args__`` and
# ``migrations/versions/<C-22 revision>.py``; renaming the index
# requires a coordinated edit across all three sites.
_TRANSFER_ADHOC_UNIQUE_INDEX = "uq_transfers_adhoc_dedupe"

# The BUDGET DECISIONS the finalised-row edit lock (#26) protects: the
# amount, the category, the period and the due date.  Names match
# :class:`TransferUpdateSchema` (the route's loaded ``data`` dict).
#
# **The OBSERVED FACTS are deliberately absent** (developer ruling,
# 2026-08-17): each side's day the bank moved the money (``settled_on_from`` /
# ``settled_on_to`` since plan step balance:X-bi-6-4c-3), and
# ``settled_amount``, what it took.  A lock protects a decision from being
# rewritten; an observation gets corrected when the statement disagrees, and
# neither correction requires reverting the transfer.  That matters beyond
# convenience for the figure: revert-then-re-settle was the only path, and a
# revert RETAINS the recorded figure, so the re-settle silently re-booked the
# old number over the re-planned one.
#
# ``name`` / ``notes`` stay editable; the ``status_id`` transition is guarded
# by the service's :func:`verify_transition` call.
_LOCKED_EDIT_FIELDS = frozenset({
    "amount", "category_id", "pay_period_id", "due_date",
})


def _reject_generated_due_date_edit(xfer, data):
    """Refuse a due-date edit a transfer's shape does not admit.

    **The transfer twin of ``routes/transactions/_gates
    ._reject_generated_due_date_edit``, and it arrived with the cutover that
    made it load-bearing** (finding **BAL-476**, plan step X-au-f).  A
    generated transfer's due date is its DEFINITION's: it is a member of
    ``transfer_recurrence.DerivedTransferFields``, computed from the rule and
    the period and rewritten by the maintain splat on every regeneration -- so
    an edit here never survived a later template save even before that step.
    What X-au-f added is that the same date now resolves the transfer's PRICE
    through amount rule 3, so the field has gone from an edit that did not last
    to one that can leave a row no rule is able to price.

    **The transaction side MEASURED what that costs**, and the measurement is
    the reason this exists rather than a symmetry argument: ``_gates`` records
    one dateless derived row taking out the grid, the dashboard and the
    companion on a production clone, because ``AmountUnresolvable`` has no
    handler on those paths.  A transfer reaches nine render sites through
    ``_render_helpers.render_transfer_cell`` with the same absence of handlers.

    **Three shapes get three answers, keyed on ``recurs`` and ``is_placed``
    since plan step ``balance:X-ci-1``** -- the twin's three arms.  It read
    ``Transfer.due_date_is_its_definitions``, a property that spelled
    ``transfer_template_id IS NOT NULL`` and lived on the row because the
    transfer had a SECOND edit door; leaf ``balance:X-bi-6-1`` deleted that
    door, and the link read as *generated* is what this leaf deletes
    (finding **BAL-493**'s family).  A transfer a RULE generated: the FIELD
    is refused, keyed on PRESENCE rather than emptiness exactly as its twin
    is, because moving the date re-prices the transfer against a different
    point in its definition's series, silently, and the next regeneration
    puts it back.  A ONE-TIME transfer -- a rule-less definition's placed
    transfer, or a cleared cadence's survivor: the date is the OWNER's to
    state (ruling **R-BAL93**: due on its placed paycheck's start unless the
    owner says otherwise, re-placed by a period move), no cadence derives it
    and no pass rewrites it, so MOVING it is allowed and the price follows
    the date (amount rule 3 reads the definition's series as of the new
    day); CLEARING it is not, because that same rule needs the day, which
    ``ck_transfers_template_row_needs_due_date`` says in storage -- this arm
    exists so the owner reads *why* rather than the generic invalid-reference
    sentence a constraint hit renders.  The form renders the input
    ``required`` for such a transfer, so this is the crafted-request and
    stale-form backstop.  An AD-HOC transfer (no definition, the shape
    ``X-ci-3`` deletes): untouched, clearable, as the developer ratified for
    its door.

    **A DERIVE-mode loan payment would survive a cleared date**, and it is
    refused here anyway.  Rule 4's derive arm dates its installment through
    ``loan_loaders.installment_for``, which is TOTAL: it falls back to the pay
    period's start.  Carving that class out would make the gate's predicate
    depend on a template's settings row, so the SAME act would be refused or
    accepted depending on a mode the operator cannot see from this form -- and
    the fallback is documented as correct only while the payment's period still
    contains its due date, so accepting the edit would move which installment
    the payment satisfies and with it the POSTED loan balance.

    Args:
        xfer: The Transfer being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if "due_date" not in data:
        return None
    if xfer.recurs:
        return _error_transfer_response(
            xfer.id,
            "This instance's due date comes from its recurring transfer, which "
            "is also what prices it. Change the due day on the recurring "
            "transfer to move every instance, or type an amount here to make "
            "this one's figure its own.",
        )
    if xfer.is_placed and data["due_date"] is None:
        return _error_transfer_response(
            xfer.id,
            "This transfer's due date can be moved but not cleared: its price "
            "is resolved on that day.",
        )
    return None


def _reject_finalised_transfer_edit(xfer, data):
    """Reject locked-field edits on a finalised (is_immutable) transfer.

    The transfer-side counterpart of the transaction lock (#26),
    enforced at this user edit boundary rather than inside
    ``transfer_service.update_transfer`` so the recurrence
    conflict-resolution and carry-forward paths -- which legitimately
    update finalised-aware transfers -- are unaffected.  Defers the
    status policy and the message to
    :func:`finalised_edit_rejection`.

    Args:
        xfer: The owned Transfer being edited.
        data: The loaded :class:`TransferUpdateSchema` payload.

    Returns:
        A designed 400 error-fragment response when a locked field is
        edited on a finalised transfer not being reverted to a mutable
        status, or ``None`` when the edit may proceed.
    """
    if not _LOCKED_EDIT_FIELDS & data.keys():
        return None
    new_status = (
        db.session.get(Status, data["status_id"])
        if "status_id" in data else None
    )
    message = finalised_edit_rejection(
        db.session.get(Status, xfer.status_id), new_status,
        context="transfer",
    )
    if message is None:
        return None
    return _error_transfer_response(xfer.id, message)


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["PATCH"])
@require_owner
def update_transfer(xfer_id):
    """Update a transfer (inline edit save).

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): when the schema accepts
    a ``category_id``, it is verified against ``current_user.id``
    here, before the service is invoked.  This layered defense
    matches :func:`create_ad_hoc` and
    :func:`app.routes.transactions.create_inline`; the underlying
    ``transfer_service.update_transfer`` already runs the same
    ownership check via ``_get_owned_category``, but enforcing the
    rule at the route layer keeps the security boundary visible
    where requests arrive and protects against a future refactor
    that bypasses the service helper.  :class:`TransferUpdateSchema`'s
    other user-scoped FK, ``pay_period_id``, is checked by the service
    alone since plan step ``pay_calendar:C13-b`` (the comment at the
    probe below says why), and this route answers its ``NotFoundError``
    with the same 404.  ``status_id`` is a reference table FK (not
    user-scoped) and so does not need an ownership check.  Setting
    ``category_id`` to ``None`` (clearing the category) is permitted
    and skips the ownership probe per the schema's ``allow_none=True``
    policy.

    Optimistic locking (commit C-18 / F-010): the cell ships
    ``version_id`` as a hidden input set to ``Transfer.version_id``
    at render time.  When the submitted value differs from the
    row's current counter, the handler short-circuits with a 409 +
    conflict cell partial and records nothing.  ``StaleDataError``
    raised at flush time -- the truly-concurrent case the form-side
    check cannot see -- is caught and converted to the same 409 +
    conflict cell so the user retries against fresh state instead
    of seeing a 500.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    errors = _xfer_update_schema.validate(request.form)
    if errors:
        # Designed fragment (marker-header convention): the requesting
        # cell re-rendered with the flattened field errors in its hint
        # instead of a JSON body the client silently drops.
        return _error_transfer_response(
            xfer.id, flatten_schema_errors(errors), status=422,
        )

    data = _xfer_update_schema.load(request.form)

    # Stale-form check.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != xfer.version_id:
        logger.info(
            "Stale-form conflict on update_transfer id=%d "
            "(submitted=%d, current=%d)",
            xfer_id, submitted_version, xfer.version_id,
        )
        return _stale_transfer_response(xfer_id), 409

    # --- Route-boundary FK ownership (commit C-27 / F-043) ---
    # ``category_id`` is verified only when present and non-``None`` --
    # ``allow_none=True`` on it means clearing it (NULL) is legitimate and the
    # service drops it through unchanged.  ``status_id`` references the ref
    # table and needs no ownership probe.  Single-return loop so adding a
    # future FK does not push the function past pylint's too-many-returns
    # threshold; all failures collapse to 404 per the project security
    # response rule.
    #
    # **``pay_period_id`` is NOT here any more** (plan step
    # ``pay_calendar:C13-b``, developer 2026-09-03).  This loop carried it,
    # duplicating ``transfer_service._ownership._get_owned_period``, which
    # ``update_transfer`` reaches whenever ``pay_period_id`` is submitted (its
    # trigger, ``_loan_posting._POSTING_RELEVANT_INSTALLMENT_FIELDS``, names
    # the period with the due date and the to-account) and whose ``NotFoundError``
    # this route already turns into the identical ``"Not found", 404``.  The
    # duplicate was F-043's deliberate defence in depth, written when the two
    # tiers had no shared producer to point at; the service tier now asks the
    # owner's derived CALENDAR, one answer with no comparison in it, and one
    # walk is what this step is for.  ``category_id`` stays because no
    # derivation owns ``budget.categories``.
    for model, field in ((Category, "category_id"),):
        value = data.get(field)
        if value is not None and not _user_owns(model, value):
            return "Not found", 404

    # A period move relocates the transfer, and both its legs, to another
    # period in the grid, which an in-place cell swap cannot express --
    # the response triggers a full grid refresh instead.  Computed before
    # the service call, while xfer.pay_period_id still holds the old value.
    period_changed = (
        "pay_period_id" in data and data["pay_period_id"] != xfer.pay_period_id
    )
    # The transfer's SHAPE, read here before anything is graded so the
    # definition's lazy load happens with nothing staged; the flip below reads
    # this local, and the gates re-read the accessors off the loaded row.
    recurs = xfer.recurs

    # Auto-set is_override when a RECURRING definition's transfer's amount or
    # period actually CHANGES, so transfer_recurrence does not regenerate over
    # the edited instance (mirrors the transaction move in
    # transactions.update_transaction and the carry-forward transfer move).
    #
    # **Keyed on ``recurs`` since plan step ``balance:X-ci-1``, not on the
    # link** (ruling **R-BAL93**, finding **BAL-493**): a ONE-TIME transfer --
    # a rule-less definition's placed transfer -- is never overridden against
    # a cadence it does not have, and the flag only hid it from
    # ``transfer_recurrence.propagate_to_unruled_template`` for good, so the
    # definition's rename or re-price reached nothing (transfer 409 on the
    # 2026-09-20 restore).  Its typed figure RESTATES the definition instead
    # (:func:`_execute_transfer_update`, **R-BAL92**) and its period move
    # RE-PLACES its date inside the service door (**R-BAL96**); an ad-hoc
    # transfer took no flag before and takes none now.  ``recurs`` is read
    # ABOVE, before the payload is graded, because the accessor lazy-loads the
    # definition.
    #
    # **It tested for the field's PRESENCE until plan step X-f2-c3, and the
    # difference was a money defect.**  This form renders the Amount input on
    # every editable row and an HTML form posts every input it renders, so
    # ``"amount" in data`` was true for EVERY save of a Projected
    # template-linked transfer -- including one that only moved the Status
    # dropdown.  Two consequences, both measured by this step's adversarial
    # review: every such save silently opted the row out of regeneration and
    # out of ``live_loan_transfer_amounts``; and because the flag is applied
    # before the settle dispatch and means "the OPERATOR owns this amount", it
    # SUPPRESSED the loan-payment freeze -- so a `$1,499.10` mortgage payment
    # settled through this door booked its stale `$1.00` estimate against a
    # `$1,000.00` interest + `$300.00` escrow split.  That is finding N-219's
    # own shape surviving inside the fix for it.
    #
    # **It compared against the STORED COLUMN until plan step X-au-h, and that
    # was finding N-448.**  ``data["amount"] != xfer.amount`` is right only
    # while the column holds what the form displayed.  Plan step X-au-f empties
    # ``budget.transfers.amount`` for a generated transfer, at which point
    # ``!= None`` is true for EVERY save -- so every notes-only or status-only
    # save through this door would have claimed a human re-priced the row and
    # detached it from its definition.  It fails by always answering YES, so no
    # test that expects the flag to be raised could have seen it.
    #
    # Ruling **R-JR**: the form posts the figure it RENDERED, and authorship is
    # that comparison rather than one against the database.  See
    # :mod:`app.routes._authored_figure` for why the payload carries it and why
    # a figure with no companion counts as authored.
    amount_authored = figure_was_authored(data, "amount")
    # The companion is popped HERE because it is not a field: ``update_transfer``
    # silently ignores unknown kwargs, so leaving it in the bag would let a key
    # this door is responsible for vanish without anyone noticing.
    #
    # **The unauthored AMOUNT is dropped later, in
    # :func:`_execute_transfer_update`, and the placement is deliberate.**
    # ``_reject_finalised_transfer_edit`` below intersects the payload's keys
    # against the locked-field set, so dropping the amount here would change
    # what that lock SEES -- a crafted request naming a finalised transfer's
    # amount would stop being refused and start being quietly ignored.  The row
    # is unharmed either way, but a lock that silently stops speaking is how the
    # next reader concludes it is not needed.  The gates grade what was
    # SUBMITTED; the service is handed what was MEANT.
    data.pop(as_rendered_field("amount"), None)
    if recurs and (amount_authored or period_changed):
        data["is_override"] = True

    # The settle-day grading, the finalised-row edit lock (#26) and the service
    # call share ONE error exit so none of them pushes the handler past pylint's
    # too-many-returns threshold.  ``or`` preserves precedence: a settle day the
    # schedule cannot hold is reported before the lock speaks, because the day
    # is not one of the fields the lock protects.
    # The Actual box's figure is graded by the SERVICE, not here: the rule is
    # echo-aware and the comparison needs the pair's own record, which only the
    # service has loaded.  A route-tier version read the STATUS alone and so
    # discarded a figure the user had just retyped (2026-08-18).
    error_response = (
        _grade_submitted_side_days(xfer, data)
        or _reject_finalised_transfer_edit(xfer, data)
        # AFTER the finalised lock, and the order is stated rather than
        # incidental: ``due_date`` IS one of ``_LOCKED_EDIT_FIELDS``, so a
        # finalised transfer submitting one must hear the LOCK -- the older and
        # more specific refusal -- rather than "it comes from your recurring
        # transfer".  The settle-day gate stays above both because the day is
        # not a field the lock protects, which its own comment says.
        or _reject_generated_due_date_edit(xfer, data)
        or _execute_transfer_update(xfer, data, amount_authored=amount_authored)
    )
    if error_response is not None:
        return error_response

    # When opened from a transfer leg's cell in the grid, render that leg's
    # cell so it remains interactive.  When opened from the transfer
    # management page, render the transfer cell.
    # A period move needs a full grid refresh so the relocated rows
    # appear under the new period; an in-place edit only needs balances
    # recomputed (gridRefresh reloads the page via app.js).  Both cell
    # paths carry the same trigger here -- it is the move, not the cell
    # kind, that decides between a refresh and a balance recompute.
    trigger = "gridRefresh" if period_changed else "balanceChanged"
    return _render_post_mutation_cell(
        xfer, leg_trigger=trigger, cell_trigger=trigger,
    )


@transfers_bp.route("/transfers/ad-hoc", methods=["POST"])
@require_owner
def create_ad_hoc():
    """Create an ad-hoc (one-time) transfer.

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): every FK accepted from
    the form but ``pay_period_id`` is verified against
    ``current_user.id`` before the service is invoked, mirroring the
    :func:`app.routes.transactions.create_inline` pattern.  The period
    is checked by the service alone since plan step
    ``pay_calendar:C13-b`` (the comment at the probe below says why),
    and its ``NotFoundError`` is answered with the same 404.  This
    layered defense is intentional: ``transfer_service`` already
    runs the same checks via its private ``_get_owned_*`` helpers,
    but a future refactor (or a new ``service`` consumer) that
    skips one of those calls would silently regress the IDOR
    protection.  Per the project security response rule, all
    ownership failures return 404 -- the same status as a missing
    record -- so the response leaks no information about whether
    the row exists for someone else.

    Double-submit handling (F-050 / C-22): the partial unique index
    ``uq_transfers_adhoc_dedupe`` on
    ``(user_id, from_account_id, to_account_id, amount, pay_period_id,
    scenario_id)`` rejects a second active ad-hoc transfer with
    identical parameters.  When two requests race past the form (a
    network retry, a double-click, the back-and-resubmit pattern), the
    first commits the transfer and the second's INSERT fires the index
    constraint.  Rather than surface the database error as a generic
    400, this handler treats the second request as idempotent success:
    rolls back the failed INSERT, re-fetches the winning transfer,
    and returns the same 201 + cell HTML the first request produced.
    The user sees the transfer they intended to create regardless of
    which request reached the database first.
    """
    errors = _xfer_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 422

    data = _xfer_create_schema.load(request.form)

    # --- Route-boundary FK ownership (commit C-27 / F-043) ---
    # Verify every FK the schema accepted belongs to the requester
    # BEFORE the service call so the security boundary is visible at
    # the route layer and a future refactor that bypasses
    # ``transfer_service._get_owned_*`` does not silently regress
    # IDOR protection.  All failures collapse to 404 per the
    # project's security response rule -- the loop body has a
    # single ``return`` so adding a sixth FK in the future does
    # not push the function past pylint's too-many-returns
    # threshold.
    #
    # **``pay_period_id`` left this tuple at plan step
    # ``pay_calendar:C13-b``** (developer 2026-09-03): it duplicated
    # ``transfer_service._ownership._get_owned_period``, which
    # ``create_transfer`` reaches unconditionally and whose ``NotFoundError``
    # the ``except`` below already answers with this same 404.  That service
    # call asks the owner's derived calendar now -- one answer, no comparison
    # -- and the four route-boundary copies of it collapsed into it.  The
    # other four entries stay: no derivation owns their tables, so the route
    # probe is still the first place the question can be asked.
    for model, pk in (
        (Account, data["from_account_id"]),
        (Account, data["to_account_id"]),
        (Scenario, data["scenario_id"]),
        (Category, data["category_id"]),
    ):
        if not _user_owns(model, pk):
            return "Not found", 404

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    # ``transfer_service.create_transfer`` calls ``db.session.flush()``
    # internally to obtain the transfer's primary key, which its log event
    # names, so the ``uq_transfers_adhoc_dedupe`` IntegrityError can fire
    # either during the service call (the flush) or at the subsequent
    # ``db.session.commit()``.
    # A single ``try`` spans both so the constraint hit is translated into
    # idempotent success (or a 400) from whichever statement trips it.
    # ``NotFoundError`` / ``ShekelValidationError`` originate only in the
    # service call and never at commit, so folding the commit into the same
    # ``try`` changes no behavior.
    try:
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=current_user.id,
                from_account_id=data["from_account_id"],
                to_account_id=data["to_account_id"],
                pay_period_id=data["pay_period_id"],
                scenario_id=data["scenario_id"],
                # An AD-HOC transfer OWNS its figure, and the schema says so:
                # ``ck_transfers_adhoc_owns_amount`` refuses a declaration on a
                # row carrying no template, because nobody generated it and no
                # definition states its price.
                amount_ownership=AmountOwnership.own(data["amount"]),
                status_id=projected_id,
                category_id=data["category_id"],
                name=data.get("name"),
                notes=data.get("notes"),
                due_date=data.get("due_date"),
            ),
        )
        db.session.commit()
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        return jsonify(errors={"_schema": [str(exc)]}), 400
    except IntegrityError as exc:
        return _handle_adhoc_integrity(exc, data)
    logger.info("user_id=%d created ad-hoc transfer (id=%d)", current_user.id, xfer.id)

    response = render_transfer_cell(xfer, wrap_div=True)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["DELETE"])
@require_owner
def delete_transfer(xfer_id):
    """Soft-delete a template transfer or hard-delete an ad-hoc transfer.

    Routes through ``transfer_service`` so the delete meets the service's
    refusals (the matched bank line's, answered below, among them).

    Optimistic locking (commit C-18 / F-010): the soft-delete UPDATE
    and hard-delete DELETE are both version-pinned by SQLAlchemy.
    A concurrent commit that bumped the row's version raises
    ``StaleDataError`` which the handler converts into a 409 +
    conflict cell.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    soft = bool(xfer.transfer_template_id)
    try:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=soft)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_transfer id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    except ShekelValidationError as exc:
        # **No page names what this door frees** -- no template renders it --
        # so the service is told nothing was shown, and a delete -- hard, or
        # since plan step ``balance:X-bi-6-4d-2`` an occurrence's soft one,
        # which takes its sides' records off too (ruling **R-BAL229**) -- that
        # would leave a matched bank line unexplained again is refused (plan
        # step ``credit_card:CC-5-4a-5``, ruling **R-CC127**: "A button with
        # no warning sends nothing").  Uncaught until then, it was a 500 with
        # nothing to roll the press back; the fragment's rollback does.
        return _error_transfer_response(xfer_id, str(exc))
    logger.info("user_id=%d deleted transfer %d", current_user.id, xfer_id)
    return "", 200, {"HX-Trigger": "balanceChanged"}


@transfers_bp.route("/transfers/instance/<int:xfer_id>/mark-done", methods=["POST"])
@require_owner
def mark_done(xfer_id):
    """Mark a transfer as 'done' (settled).

    Optimistic locking (commit C-18 / F-010): no form-side
    ``version_id`` is shipped with the button click; the SQLAlchemy
    ``version_id_col`` lock catches concurrent races at flush time
    and the handler converts ``StaleDataError`` into a 409 +
    conflict cell.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    # What the popover's captions named; the grid leg's one-click and its
    # phone card post nothing and withdraw silently (ruling **R-CC56**).
    errors = _shown_lines_schema.validate(request.form)
    if errors:
        return _error_transfer_response(
            xfer_id, flatten_schema_errors(errors), status=422,
        )
    posted = read_posted(
        _shown_lines_schema.load(request.form), absent=MARK_PAID,
    )

    try:
        # **The named VERB, not a kwargs bag** (plan step X-f2-c3): this door
        # means "the bank took this transfer" and nothing else, so it says so.
        # ``settle_transfer`` owns what that costs -- the loan-payment freeze,
        # each side's settle day, and whether a submitted figure is a human's
        # correction -- and a door that assembled ``status_id`` itself is a
        # door that can be written without them.
        #
        # NO explicit settle day, and its absence is finding N-178's fix.
        # The day is still recorded -- with none stated, both sides borrow the
        # user's today (ruling **R-BAL142**; F-048 / C-22's requirement, met
        # by ``transfer_service`` rather than by this call site).
        # Passing one HERE defeated that: the explicit-day branch wrote both
        # shadows verbatim AFTER the seam had preserved, and ``done -> done``
        # is a legal transition this route does not gate -- so a replayed or
        # stale-page POST on an already-settled transfer re-stamped today, and
        # since plan step E1a that day IS the posted ``entry_date``.  Measured:
        # a transfer settled 7 days earlier moved 7.00 days, the ledger gaining
        # a reversal at its real settle day and a fresh posting at today.  That
        # is finding N-146 through a second door; the verb closes it twice over
        # now -- it passes no day, and an already-settled transfer is an
        # idempotent no-op that writes nothing at all.
        #
        # ONE press for the pair (ruling R-CC135): both sides' settles are
        # calls of it, compared whole at its close, before the commit.
        with Press(posted.shown) as one_save:
            transfer_service.settle_transfer(
                xfer.id, current_user.id, press=one_save,
            )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on transfer mark_done id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    except ShekelValidationError as exc:
        # The state machine rejecting an illegal transition (e.g. a
        # stale surface marking a settled/cancelled transfer done).
        # Previously UNCAUGHT here -- the rejection crashed the request
        # as a 500 instead of rendering a response (found during the
        # session-4 D2 sweep); now a designed 400 fragment.  A popover out
        # of date is redrawn (ruling **R-CC128**).
        return _refused(xfer_id, exc, posted)
    except IntegrityError:
        return _error_transfer_response(xfer_id, INVALID_REFERENCE_MSG)
    logger.info("user_id=%d marked transfer %d as done", current_user.id, xfer_id)

    # A grid leg's cell re-renders with gridRefresh; the
    # transfer-management page renders the transfer cell with
    # balanceChanged (matches the transaction route pattern for status
    # changes).
    return _render_post_mutation_cell(
        xfer, leg_trigger="gridRefresh", cell_trigger="balanceChanged",
    )


@transfers_bp.route("/transfers/instance/<int:xfer_id>/cancel", methods=["POST"])
@require_owner
def cancel_transfer(xfer_id):
    """Mark a transfer as 'cancelled'.

    Optimistic locking: see :func:`mark_done`.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)
    try:
        transfer_service.update_transfer(
            xfer.id, current_user.id, status_id=cancelled_id,
        )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transfer id=%d", xfer_id,
        )
        return _stale_transfer_response(xfer_id), 409
    except ShekelValidationError as exc:
        # Same previously-uncaught state-machine rejection as
        # :func:`mark_done` -- e.g. cancelling a settled transfer from
        # a stale surface crashed as a 500; now a designed 400.
        return _error_transfer_response(xfer_id, str(exc))
    except IntegrityError:
        return _error_transfer_response(xfer_id, INVALID_REFERENCE_MSG)
    logger.info("user_id=%d cancelled transfer %d", current_user.id, xfer_id)

    # A grid leg's cell re-renders with gridRefresh; the
    # transfer-management page renders the transfer cell with
    # balanceChanged (matches the transaction route pattern for status
    # changes).
    return _render_post_mutation_cell(
        xfer, leg_trigger="gridRefresh", cell_trigger="balanceChanged",
    )


def _refused(xfer_id, exc, posted: Posted):
    """Answer a press a service refused, on the surface the press came from.

    The transfer doors' call of the one decision
    (:func:`app.routes._refused_press.answer_refused_press`): a popover out
    of date is redrawn (ruling **R-CC128**), and every other refusal is the
    designed 400 fragment of the cell or card the press targeted.

    Args:
        xfer_id: The transfer the press named.
        exc: What the service raised.
        posted: What the request said about its page.

    Returns:
        A Flask response tuple.
    """
    return answer_refused_press(
        exc, posted,
        redraw=partial(redraw_full_edit, xfer_id),
        refuse=lambda: _error_transfer_response(xfer_id, str(exc)),
    )


def _grade_submitted_side_days(xfer, data):
    """Resolve each submitted side's day against the status the PATCH leaves.

    Ruling **R-ED**'s correction door, one box per side since plan step
    ``balance:X-bi-6-4c-3`` (ruling **R-BAL108**), each graded by the ONE
    shared rule :func:`app.services.status_seam.settle_day_for_status` so this
    door and the two transaction-side doors cannot answer differently.
    Mutates *data* in place: both schema keys always go, and a day the rule
    KEEPS for a side comes back as that side's
    :class:`~app.services.transfer_service.SideDay`, under ``side_days`` -- the
    key the service reads (it silently ignores any other).

    **Each box is graded against what THAT box was prefilled with**
    (:func:`~app.routes._render_helpers.transfer_side_boxes`, the producer the
    popover rendered it from): an untouched box is an ECHO and states nothing,
    so an untouched Save re-dates no side and relabels no basis.  A box for a
    side BORROWING the other's day was rendered EMPTY and is graded against
    nothing (ruling **R-BAL164**), so any day typed there -- the borrowed one
    included -- is that side's own, ``entered``.

    **Why a drop rather than a refusal on a revert** (ruling **R-EG**): both
    full-edit forms re-submit the row's whole state, and the documented way to
    unlock a finalised transfer is to set Status to Projected in that same
    form -- so a revert arrives carrying the days the boxes held, which the
    rule drops.

    Args:
        xfer: The transfer being PATCHed, for its current status (the fallback
            when the form submitted none), its legs' records and its id (for
            the error fragment).
        data: The schema-loaded payload, mutated in place.

    Returns:
        A designed 400 error-fragment response when a submitted day precedes
        the budget's schedule (ruling **R-EL**), or ``None`` when the edit may
        proceed -- the shape this handler's other gates use, so they can share
        one error exit.  A day that has not happened yet (ruling **R-EJ**) is
        the seam's refusal, raised inside the service call and answered by
        :func:`_execute_transfer_update`.
    """
    boxes = [
        box for box in transfer_side_boxes(
            xfer, transfer_legs.covering_movements_by_leg([xfer.id]),
        )
        if box.field in data
    ]
    side_days = []
    try:
        for box in boxes:
            day = status_seam.settle_day_for_status(
                current_user.id, data.get("status_id", xfer.status_id),
                data.pop(box.field), box.prefill,
            )
            if day is not None and day != box.prefill:
                side_days.append(transfer_service.SideDay(box.account_id, day))
    except ShekelValidationError as exc:
        return _error_transfer_response(xfer.id, str(exc))
    if side_days:
        data["side_days"] = tuple(side_days)
    return None


def _execute_transfer_update(xfer, data, *, amount_authored):
    """Apply an inline transfer edit via the service and commit it.

    Runs ``transfer_service.update_transfer`` (which writes the transfer, and
    its sides' records where the edit reaches them) and commits, translating
    each failure mode into the HTTP response :func:`update_transfer` would
    otherwise inline: a stale-form/flush race to a 409 conflict cell, a
    missing or unowned reference to 404, and a domain validation error or
    foreign-key ``IntegrityError`` to a designed 400 error fragment.

    Args:
        xfer: The owned Transfer being edited.
        data: The loaded TransferUpdateSchema payload (``is_override`` may
            already be set by the caller for a recurring definition's moves,
            and the rendered-figure companion already removed by it).  **Mutated in
            place here**: an ``amount`` no human authored is dropped below,
            after the caller's gates have graded the payload as submitted.
        amount_authored: Whether the ``amount`` in *data*, if any, is a figure a
            HUMAN typed (ruling **R-JR**).  Passed rather than re-derived
            because the fact lives in the payload the caller already consumed.
            It is what this door TRANSLATES into the service's parameter:
            an authored figure becomes an ownership the pair OWNS -- or, on a
            one-time transfer, the definition's price to restate
            (``definition_price``, **R-BAL92**) -- and an echoed one becomes
            no statement about the amount at all.

    Returns:
        ``None`` on success -- the caller renders the updated cell -- or a
        Flask response tuple describing the failure.
    """
    # An UNAUTHORED figure is not forwarded, the idiom
    # ``_update._grade_submitted_figure`` already applies to an echoed
    # ``settled_amount`` one box over: a figure nobody typed carries no
    # information, so it is not an update.  It matters beyond tidiness -- an
    # ownership stating a figure CLEARS the relation that prices the row, so
    # forwarding an echo would un-derive a generated transfer on a save that
    # touched only its notes.
    #
    # Dropped HERE rather than at the caller so the two gates above still grade
    # the payload as SUBMITTED; see the caller for why that ordering is
    # load-bearing.  ``data`` is the caller's dict and this mutates it, which
    # matches ``_grade_submitted_figure``'s documented in-place contract.
    #
    # **The TRANSLATION is this door's, since ruling R-BAL11** (plan step
    # X-au-f): the service takes ONE ``amount_ownership`` where it took a
    # figure plus a claim about who authored it.  A door is exactly the layer
    # that knows the difference -- it compares what came back against what it
    # rendered -- so it says *the pair OWNS this figure* rather than handing
    # down two facts for the service to combine.  Saying NOTHING is spelled by
    # omitting the key, which is why the echo is popped rather than sent as an
    # empty ownership.
    #
    # **A ONE-TIME transfer's typed figure RESTATES its definition's price in
    # place, and the pair stays priced by it** (plan step ``balance:X-ci-1``,
    # ruling **R-BAL92**, the twin of ``one_off.restate_price``): a rule-less
    # definition's one placed transfer IS the definition, so the figure is a
    # statement about the definition and not about one occurrence among many
    # -- which is why a RECURRING transfer's typed figure detaches that row
    # (OWN, ``is_override``) and this one never does.  The door says so with
    # the figure -- ``definition_price``, an explicit act the service performs
    # (``_placed.restate_definition_price``: the version the transfer's FINAL
    # due date reads is corrected, the transfer is declared priced by the
    # definition, the flag comes off) -- rather than a ``derived_ownership``
    # beside a restate of its own, because the service both RE-PLACES the
    # date the restate reads and SETTLES the pair on the price it reads, and
    # only inside the door can the act sit between the two (**R-BAL96** as
    # amended 2026-09-21: as first built the popover restated after the
    # door, and a figure typed beside Status = Paid booked the old price on
    # both legs).  A rule-less definition that owns no price series -- a
    # derive-mode loan payment, 0 on the 2026-09-20 restore -- keeps ruling
    # **R-JM**'s arm: the pair's OWN figure at the parent, with no flag, so
    # the definition's other fields still reach it.
    #
    # **The FLAG comes off with the figure, whatever it was** -- the twin's
    # R-BAL37 heal: a one-time transfer the pre-X-ci flip detached (OWN with
    # ``is_override`` beside it, transfer 409's state) is declared the ruled
    # shape on its next typed figure, so the propagation reaches it again;
    # on a transfer never detached the write is idempotent.  A rename, a
    # move or a date edit alone leaves a detached one as it stands, exactly
    # as on the transaction side; ``X-ci-3`` re-attaches the residue nobody
    # re-priced.  The service's ``restate_in_effect`` raises ``ValueError``
    # for a definition holding no version, which every producer of one opens
    # through ``set_amount``: a state the app does not write, so it is not
    # caught here, as its twin does not catch it.
    figure = data.pop("amount", None)
    typed_on_placed = amount_authored and figure is not None and xfer.is_placed
    if typed_on_placed and template_amount_service.owns_its_amount(xfer.template):
        data["definition_price"] = figure
    elif typed_on_placed:
        data["amount_ownership"] = AmountOwnership.own(figure)
        data["is_override"] = False
    elif amount_authored and figure is not None:
        data["amount_ownership"] = AmountOwnership.own(figure)
    # **The Actual box's figure is a PERSON's statement of what the bank
    # took**, and the route says so with the figure (plan step X-bi-3e-1,
    # ruling R-BAL61): the service takes the amount and who wrote it as one
    # ``figure`` value, the way it takes each side's day as ``side_days``.
    # The key is REPLACED for the reason ``_grade_submitted_side_days`` gives:
    # the service would silently ignore a ``settled_amount`` kwarg.  An empty box
    # loads as ``None`` and is no statement, so no key.
    actual = typed_figure(data.pop("settled_amount", None))
    if actual is not None:
        data["figure"] = actual
    # **What the popover's captions named** (plan step
    # ``credit_card:CC-5-4a-5``, ruling **R-CC127**), taken out of the bag the
    # service reads as keywords and handed to it as its own: the removal act
    # refuses a save whose page named other lines than it frees, and a
    # request with no field named none.  Read HERE, beside the one call that
    # uses it; none of the gates above reads it.  ONE press for the pair
    # (ruling R-CC135), compared whole at its close, before the commit.
    posted = read_posted(data, absent=NOTHING_SHOWN)
    try:
        with Press(posted.shown) as one_save:
            transfer_service.update_transfer(
                xfer.id, current_user.id, press=one_save, **data,
            )
        db.session.commit()
    except StaleDataError:
        logger.info("Stale-data conflict on update_transfer id=%d", xfer.id)
        return _stale_transfer_response(xfer.id), 409
    except NotFoundError:
        return "Not found", 404
    except ShekelValidationError as exc:
        # State-machine and domain rejections (e.g. an illegal status
        # transition from the grid transfer card) render as designed
        # fragments so the reason is visible (grid audit D2); a popover out
        # of date is redrawn (ruling **R-CC128**).
        return _refused(xfer.id, exc, posted)
    except IntegrityError:
        return _error_transfer_response(xfer.id, INVALID_REFERENCE_MSG)
    logger.info("user_id=%d updated transfer %d", current_user.id, xfer.id)
    return None


def _adhoc_dedupe_idempotent_response(data):
    """Return the winning ad-hoc transfer's cell as idempotent success.

    Called from ``create_ad_hoc`` when ``uq_transfers_adhoc_dedupe``
    rejects a second concurrent INSERT.  Re-fetches the active
    transfer matching the submitted parameters and returns the same
    201 + ``_transfer_cell.html`` payload the first request produced
    so the user's view matches the database state regardless of which
    request reached PostgreSQL first.

    The lookup uses the same predicate as the index (matching
    ``transfer_template_id IS NULL`` and ``is_deleted = FALSE``) so it
    only ever matches the row that triggered the violation.  A
    missing match indicates the row was deleted between the
    IntegrityError and this lookup -- vanishingly unlikely under
    normal use, but treated as a 409 with a clear message rather than
    a 500.

    Args:
        data: The deserialised ``TransferCreateSchema`` output for
            the failed request.

    Returns:
        Flask response tuple: ``(html, 201, {"HX-Trigger": ...})`` on
        success, or a 409 string on the unrecoverable race.
    """
    existing = (
        db.session.query(Transfer)
        .filter(
            Transfer.user_id == current_user.id,
            Transfer.from_account_id == data["from_account_id"],
            Transfer.to_account_id == data["to_account_id"],
            Transfer.amount == data["amount"],
            Transfer.pay_period_id == data["pay_period_id"],
            Transfer.scenario_id == data["scenario_id"],
            Transfer.transfer_template_id.is_(None),
            Transfer.is_deleted.is_(False),
        )
        .first()
    )
    if existing is None:
        # Vanishingly rare: the winning row was soft-deleted or hard-
        # deleted between the IntegrityError and this lookup.  Surface
        # a 409 so the operator retries against the post-delete state
        # instead of seeing a 500 from the missing record.
        return (
            "Duplicate ad-hoc transfer detected but the winning row "
            "is no longer active.  Reload and try again.",
            409,
        )
    logger.info(
        "Duplicate ad-hoc transfer prevented; returning existing id=%d "
        "(idempotent success)", existing.id,
    )
    response = render_transfer_cell(existing, wrap_div=True)
    return response, 201, {"HX-Trigger": "balanceChanged"}


def _handle_adhoc_integrity(exc, data):
    """Translate an ad-hoc transfer ``IntegrityError`` into the right response.

    The ``uq_transfers_adhoc_dedupe`` partial unique index (F-050 / C-22)
    rejects a second active ad-hoc transfer with identical parameters when two
    requests race (a double-click, a network retry).  That case is treated as
    idempotent success -- the winning row's cell is returned (see
    :func:`_adhoc_dedupe_idempotent_response`).  Any other ``IntegrityError``
    is a genuine bad reference and becomes a 400.  The session is rolled back
    before the violation is inspected.

    Args:
        exc: The caught ``IntegrityError``.
        data: The loaded TransferCreateSchema payload for the failed request.

    Returns:
        The idempotent 201 cell response on a dedupe-index hit, or a 400
        ``(body, status)`` tuple otherwise.
    """
    db.session.rollback()
    if is_unique_violation(exc, _TRANSFER_ADHOC_UNIQUE_INDEX):
        return _adhoc_dedupe_idempotent_response(data)
    return "Invalid reference. Check that all referenced records exist.", 400
