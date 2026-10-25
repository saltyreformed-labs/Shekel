"""
Shekel Budget App -- Recurring route package: recurring-transaction CRUD.

Create, edit, update, archive, unarchive and hard-delete a recurring
TRANSACTION definition (a :class:`~app.models.transaction_template.TransactionTemplate`)
and the recurrence rule it carries, plus the kind-agnostic recurrence-preview
fragment endpoint both template forms point at.  Updating a template triggers
recurrence regeneration.

The unified Recurring LIST page is the sibling module
:mod:`app.routes.templates.surface`: it spans both template kinds, so it is not
part of this one.
"""

import logging
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transaction import Transaction
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import TxnTypeEnum
from app.utils import archive_helpers
from app.schemas.validation import TemplateCreateSchema, TemplateUpdateSchema
from app.services import (
    account_service,
    category_service,
    definition_delete,
    definition_edit,
    definition_unarchive,
    posting_service,
    recurrence_engine,
    template_amount_service,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.utils.balance_predicates import is_projected_clause
from app.routes.templates._instances import propagate_to_non_repeating_rows
from app.routes.templates._validation import validate_template_form
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
    handle_stale_form_conflict,
)
from app.routes._amount_version_actions import (
    AmountVersionAction,
    withdraw_amount_version,
)
from app.routes._recurrence_preview import recurrence_preview_fragment
from app.services.cash_ledger import (
    derived_amount_basis,
    resolve_transaction_amount,
)
from app.routes._recurrence_conflict_chooser import (
    PreEditTemplateState,
    RecurrenceConflictKind,
    regenerate_or_conflict_chooser,
)
from app.routes._recurrence_form_helpers import (
    author_recurrence_for_create,
    recurrence_spec_for_create,
    resolve_recurrence_rule_for_update,
)
from app.routes._recurrence_form_refusals import (
    RecurrenceFormContext,
    StrandingCheck,
    refuse_stranding_save,
)
from app.routes._recurrence_form_render import (
    create_form_recurrence_state,
    edit_form_recurrence_state,
)
from app.routes._form_errors import load_form_or_redirect
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import RECURRENCE_END_BOUND_KEY
from app.routes.templates._bp import templates_bp

logger = logging.getLogger(__name__)


_create_schema = TemplateCreateSchema()
_update_schema = TemplateUpdateSchema()

# Where this kind's amount-history withdrawal reports back to; the act itself is
# shared with the transfer-template twin (plan step X-au-a).
_AMOUNT_VERSION_ACTION = AmountVersionAction(
    logger=logger,
    edit_endpoint="templates.edit_template",
    noun="recurring transaction",
)

# Query-param hint the Recurring surface's "New" picker passes so the
# creation form pre-selects the right transaction type.  "income" selects
# the income type; any other value (including the default Expense picker
# entry and a hand-crafted request) falls back to expense, the most common
# recurring definition.
_NEW_TYPE_INCOME = "income"

@templates_bp.route("/templates/new", methods=["GET"])
@require_owner
def new_template():
    """Display the template creation form.

    The Recurring surface's "New" picker offers Expense / Income / Transfer;
    the Income entry links here with ``?type=income`` so the form lands with
    the income type pre-selected (expense and income share this form).  Any
    other ``type`` value falls back to expense.
    """
    categories = category_service.list_active_categories(current_user.id)
    accounts = account_service.list_active_accounts(current_user.id)
    txn_types = db.session.query(TransactionType).all()

    if request.args.get("type") == _NEW_TYPE_INCOME:
        default_txn_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    else:
        default_txn_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    return render_template(
        "templates/form.html",
        template=None,
        categories=categories,
        accounts=accounts,
        # Every recurrence control's starting state as ONE value, so this
        # form and the transfer form cannot come to disagree about what the
        # same rule means.  A create form opens with no cadence selected, the
        # unbounded closing shape and neither bound locked.
        recurrence=create_form_recurrence_state(),
        txn_types=txn_types,
        default_txn_type_id=default_txn_type_id,
        # A template that does not exist yet has no amount history; passed so
        # the shared form never references an undefined value.
        amount_history_rows=[],
        amount_today=None,
        amount_version_delete_endpoint="templates.delete_amount_version",
    )


@templates_bp.route("/templates", methods=["POST"])
@require_owner
def create_template():
    """Create a new transaction template with optional recurrence rule."""
    payload = load_form_or_redirect(
        _create_schema, RedirectTarget("templates.new_template"),
    )
    if isinstance(payload, Response):
        return payload
    data = payload

    # Validate account/category ownership + expense-only tracking.
    invalid = validate_template_form(
        data, on_invalid=RedirectTarget("templates.new_template"),
    )
    if invalid is not None:
        return invalid

    # Read the cadence the form states.  The F-24 helper pops every
    # recurrence-related key from ``data`` so the TransactionTemplate
    # constructor below does not receive stray kwargs.  **It never answers
    # "no rule" for THIS kind since plan step balance:X-bi-7b** (ruling
    # R-BAL23): the form offers no "Does not repeat" and
    # ``TemplateCreateSchema.validate_a_cadence_is_chosen`` has refused an
    # empty unit before this line -- a transaction that does not repeat is a
    # ONE-OFF, made at the Budget grid through ``one_off.place_one_off``, so a
    # definition with no occurrence is not something this door can mint.  The
    # helper's ``None`` arm is the transfer form's.
    #
    # **The ``duplicate-code`` disable this call carried is GONE, and so is
    # the duplication** (plan step R7b-4).  The suppression's stated reason
    # was that this preamble could not be hoisted -- the transfers side reused
    # ``start_period_id`` afterwards and a wrapper popping it internally would
    # have had to thread it back out.  That field is the transfer form's alone
    # now, so the preamble hoisted into
    # :func:`recurrence_spec_for_create` and there is one copy of it.
    spec = recurrence_spec_for_create(
        data,
        user_id=current_user.id,
        redirect=RedirectTarget("templates.new_template"),
    )

    # Create the template.
    template = TransactionTemplate(
        user_id=current_user.id,
        **data,
    )
    db.session.add(template)
    db.session.flush()

    # **The definition comes FIRST and the rule is authored onto it** (plan
    # step R-F6).  A recurrence rule carries its owner's FK now, so it cannot
    # be written before there is an owner -- which is the same fact that makes
    # the orphan finding **F-6** measured inexpressible.  The order reversed
    # here; nothing else about the create did.
    rule = author_recurrence_for_create(
        spec, template, redirect=RedirectTarget("templates.new_template"),
    )
    if isinstance(rule, Response):
        return rule

    # Open the amount's dated series at today (plan step X-au-a).  The
    # constructor above also carries the figure because the column is NOT NULL;
    # this call is what makes the SERIES exist, and after plan step X-au-e the
    # series is the only thing that prices this definition's rows -- generation
    # writes no figure, so a template whose series never opened generates rows
    # ``_stated_amount`` REFUSES.  *An earlier version of this comment said
    # X-au-e "removes the redundancy by removing the column"; it does not, and
    # ``default_amount`` stays until plan step ``balance:X-bp`` deletes it -- see
    # ``template_amount_service.set_amount`` for what still reads it.*  A
    # template created today generates rows into historical pay periods too,
    # and those resolve by the series holding flat before its earliest version
    # (``template_amount_service.amount_as_of``).
    template_amount_service.set_amount(
        template, template.default_amount, effective_on=display_today(),
    )

    # Auto-generate transactions from the rule into future periods.  ONE read
    # pass carries the baseline scenario and the owner's schedule (plan step
    # R7d-c-1), where this held a ``get_baseline_scenario`` beside a
    # ``calendar_for`` -- the two facts a pass already pins.
    if rule:
        ctx = BalanceContext.build(current_user.id)
        if ctx.scenario is not None:
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
            )

    db.session.commit()
    flash(
        f"Recurring transaction '{template.name}' created. "
        "View it on the Budget grid.",
        "success",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/edit", methods=["GET"])
@require_owner
def edit_template(template_id):
    """Display the template edit form."""
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    categories = category_service.list_active_categories(current_user.id)
    accounts = account_service.list_active_accounts(current_user.id)
    txn_types = db.session.query(TransactionType).all()

    return render_template(
        "templates/form.html",
        template=template,
        categories=categories,
        accounts=accounts,
        # The options never vary; what an EDIT form adds is where the
        # controls START.  A rule whose stored pattern the application no
        # longer models resolves to ``None`` -- the controls render UNSET and
        # ``edit_form_cadence`` flashes why -- rather than to a stale selection
        # the browser would silently replace with the first option.  On this
        # kind that first option is a "Choose how it repeats" placeholder
        # (plan step balance:X-bi-7b) whose save the update schema REFUSES, so
        # an unchanged save can no longer delete the rule (R2e-1's hazard).
        # The pass is the form's one read pass (plan step R7d-f).
        recurrence=edit_form_recurrence_state(
            template, BalanceContext.build(current_user.id),
        ),
        txn_types=txn_types,
        # Unused when editing (the form reads the template's own type), but
        # passed so the shared template never references an undefined value.
        default_txn_type_id=None,
        # The amount's dated history (plan step X-au-a), precomputed into
        # display rows.  Empty for a salary-linked template, whose amount the
        # paycheck calculator derives and which therefore has no series at all.
        amount_history_rows=template_amount_service.build_amount_history(
            template, display_today(),
        ),
        # What the definition costs NOW, which is not the stored column whenever
        # a rise is SCHEDULED; the form's date input defaults to today, so the
        # two have to be the same question.
        amount_today=template_amount_service.current_amount(
            template, display_today(),
        ),
        amount_version_delete_endpoint="templates.delete_amount_version",
    )


# The transaction-template kind for the shared regenerate-or-chooser flow:
# how to regenerate, resolve, load, and re-edit an expense / income row.
_TXN_TEMPLATE_KIND = RecurrenceConflictKind(
    model=Transaction,
    # A transaction's amount rule, as a one-argument callable: the chooser
    # renders this figure as money and a derived row carries no column to read
    # (plan step X-au-c2b).  The basis is built from the row's own pins, which
    # is safe because the chooser prices ONE row at a time.
    resolve_amount=lambda row: resolve_transaction_amount(
        row, derived_amount_basis(row.account.user_id, row.scenario_id),
    ),
    regenerate_fn=recurrence_engine.regenerate_for_template,
    preview_fn=recurrence_engine.preview_regeneration_for_template,
    resolve_fn=recurrence_engine.resolve_conflicts,
    update_endpoint="templates.update_template",
)


@templates_bp.route("/templates/<int:template_id>", methods=["POST"])
@require_owner
def update_template(template_id):
    """Update a template and regenerate future transactions.

    Uses POST with _method=PUT for HTML form compatibility.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    payload = load_form_or_redirect(
        _update_schema,
        RedirectTarget("templates.edit_template", {"template_id": template_id}),
    )
    if isinstance(payload, Response):
        return payload
    data = payload

    # The load / version-guard / pop / resolve preamble below is the
    # standard parallel-CRUD update shape it shares with
    # ``transfers.update_transfer_template``.  Its substantive steps are
    # already DRY: the optimistic-lock guard (``handle_stale_form_conflict``)
    # and the recurrence-rule resolution (``resolve_recurrence_rule_for_update``)
    # live in the shared F-24 helper module.  What remains duplicated is only
    # the ORDER in which this route invokes those helpers; folding that call
    # sequence into a further helper would couple two separate template
    # domains (transaction-template envelope tracking + name propagation vs
    # transfer-template name-uniqueness + shadow invariants) behind awkward
    # multi-value returns for no real gain (coding-standards rule 13).
    # Pylint: ``duplicate-code`` -- one-sided disable; only the call sequence
    # is shared with ``transfers.update_transfer_template`` per the R0801
    # mechanics in ``docs/audits/pylint-cleanup/plan.md`` (Phase 2 working
    # notes).
    # pylint: disable=duplicate-code
    # Stale-form check (commit C-18 / F-010).  Routed through the
    # F-26 helper so the pre-flush optimistic-locking guard shares a
    # single implementation with the parallel transfer-template
    # update route.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        return handle_stale_form_conflict(
            StaleConflictContext(
                logger=logger,
                log_label="update_template",
                log_id=template_id,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="recurring transaction",
                ),
                redirect=RedirectTarget(
                    "templates.edit_template",
                    {"template_id": template_id},
                ),
            ),
            submitted=submitted_version,
            current=template.version_id,
        )

    effective_from = data.pop("effective_from", display_today())

    # ``start_period_id`` needs no pop here since plan step R7b-4: the
    # transaction-template schema no longer declares it, so no submission can
    # carry it into the field-update loop below.  It used to be popped
    # because the field was on the shared recurrence mixin and this form
    # collected it only at creation.
    # The closing bound, composed by the schema's ``@post_load`` into ONE
    # value under the mode key.  ABSENT when the form stated no bound --
    # a disabled control, or a partial update -- which the helpers read as
    # "leave the stored one alone" (plan step R7b-3).
    end_bound = data.pop(RECURRENCE_END_BOUND_KEY, None)

    # The template's before-image, captured BEFORE anything overwrites it
    # (plan step R2e-1).  ``had_recurrence_rule`` is what lets the
    # regeneration below tell "the user just cleared the recurrence" -- which
    # must sweep the instances the deleted rule generated -- from "this
    # template never recurred", which must not.
    before = PreEditTemplateState(
        amount=template.default_amount,
        had_recurrence_rule=template.recurs,
    )

    # Re-point, rebuild, or clear the recurrence rule from the update payload
    # (F-24).  The helper dispatches the existing-rule (mutate in place)
    # vs no-existing-rule (build + link) branches and pops every
    # recurrence key from ``data`` so the field-update loop below sees
    # none.  The pass is the PRE-WRITE one the refusals read (plan step
    # R7d-f); regeneration below builds its own after the write.
    pass_ctx = BalanceContext.build(current_user.id)
    # What the stranded-row refusal below reads of the definition as it
    # STANDS -- the rows its unarchive would restore -- asked before the edit
    # touches it (rulings R-PC93, R-PC95).
    stranding = StrandingCheck.before_the_edit(template, pass_ctx)
    redirect_response = resolve_recurrence_rule_for_update(
        template,
        data,
        ctx=RecurrenceFormContext(
            end_bound=end_bound,
            redirect=RedirectTarget(
                "templates.edit_template",
                {"template_id": template_id},
            ),
        ),
        pass_ctx=pass_ctx,
    )
    if redirect_response is not None:
        return redirect_response
    # pylint: enable=duplicate-code

    # Validate account/category ownership + expense-only tracking on the
    # resulting state.  Shared with create_template via _validate_template_form.
    invalid = validate_template_form(
        data,
        on_invalid=RedirectTarget(
            "templates.edit_template", {"template_id": template_id},
        ),
        template=template,
    )
    if invalid is not None:
        return invalid

    # State the amount through its one write door, which moves the scalar and
    # the dated series together (plan step X-au-a).  ``effective_from`` is the
    # form's "Amount effective from" date, which also bounds the regeneration
    # below -- ONE value, though the two apply it with different predicates: the
    # series answers by a row's own DUE date and the sweep selects by its pay
    # PERIOD's end.  **That divergence used to let an edit REWRITE a row whose
    # due date preceded the date it states (finding N-247); plan step X-au-e
    # dissolved it** -- the sweep writes a DECLARATION rather than a figure, so
    # which rows it reaches decides nothing about money and every row resolves
    # by its own due date whether the sweep touched it or not.  Absent from a
    # partial update means the amount was not restated, and the series is
    # untouched.
    #
    # **BEFORE the field loop, because that loop can FLUSH.**  A rename issues a
    # bulk UPDATE over this template's instances, which autoflushes whatever is
    # dirty; stating the amount afterwards would leave a second dirty write for
    # the commit and bump the optimistic-lock counter twice for one edit.
    if "default_amount" in data:
        template_amount_service.set_amount(
            template, data["default_amount"], effective_on=effective_from,
        )

    # Apply allowlisted field updates, propagating any rename to existing
    # instances -- the ONE act both definition doors call since plan step
    # balance:X-bi-7b (``definition_edit.apply_fields`` carries the rationale).
    definition_edit.apply_fields(template, data)

    # A definition that neither has nor had a rule does not regenerate at all
    # (the gate inside ``regenerate_or_conflict_chooser`` returns before
    # touching a row -- that is what closes defect D16), so the rows it holds
    # are reached HERE or nowhere (plan step balance:X-bi-7a; the transfer
    # twin's ``_regenerate_and_commit_template`` takes the same fork).  On
    # this door that is a one-off's definition being given a cadence (*make
    # this repeat*) or edited before one: a cadence can no longer be CLEARED
    # here (balance:X-bi-7b, R-BAL23), so the "cleared cadence" sweep the
    # regeneration below performs is reached from the transfer twin alone.
    if not before.had_recurrence_rule and not template.recurs:
        propagate_to_non_repeating_rows(template)

    # Regenerate future transactions, diverting to the conflict chooser when
    # an amount change would overwrite hand-edited upcoming instances (the
    # chooser rolls the pending edit back; its Apply re-runs this same edit)
    # -- unless the save would leave a still-projected row answering an
    # occurrence its books drop, or sitting inside the books of the account
    # it sits on, whatever field changed (rulings R-PC90 / R-PC91 / R-PC99),
    # which is refused first: the edit is whole now, and the regeneration
    # below (from ``effective_from``) is part of the state the save leaves,
    # so the refusal reads that pass's own preview of it.
    diverted = refuse_stranding_save(
        template, stranding,
        RedirectTarget("templates.edit_template", {"template_id": template_id}),
        kind=_TXN_TEMPLATE_KIND, effective_from=effective_from,
    ) or regenerate_or_conflict_chooser(
        template, before, effective_from, _TXN_TEMPLATE_KIND,
        amount_drives_instances=not template_amount_service.is_salary_linked_template(
            template,
        ),
    )

    # The chooser short-circuits (its pending edit is already rolled back);
    # otherwise commit the edit, subject to the stale-version guard.
    response = diverted or commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="update_template",
        log_id=template_id,
        flash_message=STALE_EDITING_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget(
            "templates.edit_template",
            {"template_id": template_id},
        ),
    ))
    if response is not None:
        return response
    # **No "no longer repeats" arm since plan step balance:X-bi-7b.**  A
    # transaction definition's cadence cannot be CLEARED from this form any
    # more (ruling R-BAL23: the schema refuses an empty unit, and a definition
    # with no rule is a one-off made at the grid), so the flash that named the
    # sweep such an edit performed described a state this door can no longer
    # produce.  The transfer twin keeps its arm; the sweep it names is graded
    # there.
    flash(f"Recurring transaction '{template.name}' updated.", "success")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route(
    "/templates/<int:template_id>/amount-versions/<int:version_id>/delete",
    methods=["POST"],
)
@require_owner
def delete_amount_version(template_id, version_id):
    """Withdraw one entry from a template's amount history.

    The correction path for a price stamped against the wrong DATE: restating
    the amount writes a version at the date it names and leaves the mis-dated
    one standing, so removing it is a separate act.  The EARLIEST entry is
    refused by the service -- it is what every date before the series answers
    from -- and the way to move it is to state the amount at the right date
    first, which makes the old one no longer earliest.

    Ownership is the ``get_or_404`` on the TEMPLATE: the shared action looks the
    version up inside that template's own collection, so a ``version_id``
    belonging to another user's template is simply not found and the refusal is
    indistinguishable from "no such entry" (the security response rule).  The
    act itself is shared with the transfer-template twin
    (:func:`app.routes._amount_version_actions.withdraw_amount_version`).
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)
    return withdraw_amount_version(template, version_id, _AMOUNT_VERSION_ACTION)




@templates_bp.route("/templates/<int:template_id>/archive", methods=["POST"])
@require_owner
def archive_template(template_id):
    """Archive a template (stops future generation, keeps history).

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts to a flash + redirect so the user retries against
    fresh state.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    template.is_active = False
    deleted_count, kept = _soft_delete_projected_rows(template)

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget("templates.list_templates"),
    ))
    if conflict is not None:
        return conflict

    stays = kept.stays("row", named=True)
    flash(
        f"Recurring transaction '{template.name}' archived. "
        f"{deleted_count} projected transaction(s) removed"
        + (f"; {stays}." if stays else "."),
        "info",
    )
    return redirect(url_for("templates.list_templates"))


def _soft_delete_projected_rows(template):
    """Hide a definition's projected rows that hold NOTHING; keep the rest.

    The archive's write, shared by :func:`archive_template` and the permanent
    delete's archive fallback so the two cannot come to hide different rows.
    **A row holding a payment or purchase is NOT hidden** (plan step
    ``credit_card:CC-5-4a-4``, ruling **R-CC63**: "Archive hides only rows
    that hold nothing"): a soft-deleted row contributes to no balance, so
    hiding one took its money off the books -- measured 2026-09-22 on a copy
    of production: archiving a definition whose row held a recorded purchase
    moved its account's projection for that period, exactly as the permanent
    delete did.  The row stays as it is, visible,
    for the owner to close, lower or delete through its own door.

    **No posting reversal precedes the statement any more, and none is
    owed.**  One ran here because a soft-deleted row's purchase postings
    would strand (plan step X-f3b, ruling **R-FM**); a row holds a posting
    only through a DATED purchase, which is a movement
    (``_posting_purchases.posted_purchase_exists_clause`` is an ``EXISTS``
    over its entries), so every row this hides holds none by construction.
    Routed through ``is_projected_clause`` (D6-09 / MED-02) as the
    unarchive's restore scope is.

    Args:
        template: The definition being archived.

    Returns:
        ``(hidden, kept)`` -- how many rows the statement hid, and the
        :class:`~app.utils.archive_helpers.HeldMovements` of the projected
        rows it kept.
    """
    projected = (
        Transaction.template_id == template.id,
        is_projected_clause(Transaction),
        Transaction.is_deleted.is_(False),
    )
    # **Both reads see every purchase another tab added** (plan step
    # ``balance:X-bn``, ruling **R-CC106**).  A purchase added to one of these
    # rows while the archive ran was invisible to them -- each reads committed
    # data only -- so the row was hidden over it and the database's hiding arm
    # refused the whole commit as a raw error.  The request's transaction
    # took its owner's write lock before it read any of the owner's data
    # (:mod:`app.db_transaction`), so that purchase either committed before
    # these reads, and its row is KEPT and named in the flash with the rest,
    # or waits for the archive to commit.  Until that step the rows' own locks
    # did it here (ruling **R-CC96**: the archive was one of the two hiders
    # that took the row lock before anything else).
    kept = archive_helpers.rows_holding_movements(*projected)
    hidden = db.session.query(Transaction).filter(
        *projected, archive_helpers.holds_nothing(),
    ).update({"is_deleted": True}, synchronize_session="fetch")
    return hidden, kept


@templates_bp.route("/templates/<int:template_id>/unarchive", methods=["POST"])
@require_owner
def unarchive_template(template_id):
    """Unarchive a template and restore projected transactions.

    **Restores the rows the archive hid, less any its books now drop**
    (rulings **R-PC93**, **R-PC95** and **R-PC96**,
    :class:`~app.services.definition_unarchive.UnarchiveScope`): a row its
    owner deleted by hand is soft-deleted exactly as the archive hides one,
    and a row the books have passed would come back inside the opening
    balance, so it stays deleted and the flash names it.  **An unarchive of a
    template that is not archived restores nothing**: its soft-deleted rows
    are its owner's own deletions, and a stale tab's button is the only way
    to post here for one.

    Optimistic locking: see :func:`archive_template`.
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)
    if template.is_active:
        flash(
            f"Recurring transaction '{template.name}' is not archived, so "
            "nothing was restored.",
            "info",
        )
        return redirect(url_for("templates.list_templates"))

    # What comes back and what stays deleted: the ONE scope the books
    # refusals count too (``definition_unarchive``), read on a PRE-WRITE pass
    # before anything is restored.  Generation below builds its own.
    unarchive = definition_unarchive.unarchive_scope_on(
        template, BalanceContext.build(current_user.id),
    )
    stays_deleted = definition_unarchive.stays_deleted_notice(unarchive)

    template.is_active = True

    restore_scope = unarchive.restores()
    restored = definition_delete.rows_holding_purchase_postings(*restore_scope)
    restored_count = db.session.query(Transaction).filter(
        *restore_scope,
    ).update({"is_deleted": False}, synchronize_session="fetch")
    # The mirror of the archive: a restored row contributes again, so its
    # purchases' legs go back.  AFTER the update, and read through the session
    # the bulk statement synchronised, so each row's ``is_deleted`` is the value
    # the reconcile must price it at.
    for txn in restored:
        posting_service.sync_transaction_postings(txn)

    # Regenerate to fill in any missing future periods, on the one read pass
    # this restore's generate runs in (plan step R7d-c-1).
    if template.recurs:
        ctx = BalanceContext.build(current_user.id)
        if ctx.scenario is not None:
            recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
                effective_from=date.today(),
            )

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget("templates.list_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(
        f"Recurring transaction '{template.name}' unarchived. "
        f"{restored_count} projected transaction(s) restored."
        + (f" {stays_deleted}" if stays_deleted else ""),
        "success",
    )
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/<int:template_id>/hard-delete", methods=["POST"])
@require_owner
def hard_delete_template(template_id):
    """Permanently delete a transaction template if it has no settled history.

    Two-path logic:
      1. If the template has any settled transaction (Paid or Received --
         anything with ``Status.is_settled = True``), OR any of its rows
         holds a payment or purchase whatever its status
         (``archive_helpers.template_holding_movements``, plan step
         ``credit_card:CC-5-4a-4``, rulings **R-CC54** / **R-CC66**), OR any
         standing merchant rule files a merchant's bank spending into it
         (``archive_helpers.template_has_standing_rule``, plan step
         ``bank_import:X-gd-2``), permanent deletion is blocked.  The template
         is archived instead (if not already) -- the archive's own write,
         which keeps every row holding a movement live (ruling **R-CC63**) --
         and the user is warned, with the sentence naming which of the three
         reasons applied.
      2. If no settled history exists, all linked NON-SETTLED transactions
         are deleted first, then the template itself is permanently
         removed -- ``definition_delete.permanently_delete_definition``, the
         one act, shared with the account hard-delete since plan step
         ``balance:X-bi-7a``.  ``Transaction.template_id`` is a FK with ON
         DELETE RESTRICT (plan step ``balance:X-bi-7d-2``; SET NULL before
         it), so any row that survived the filtered delete would refuse the
         definition's delete rather than keep its financial data with a
         NULL link; guard 1 is what makes that set empty (ruling **R-JE**),
         so the refusal is a designed 4xx rather than the database's.

    Defense in depth (CRIT-05 / E-22): the bulk delete is constrained to
    non-settled rows via the semantic ``Status.is_settled`` boolean.
    Even if the guard predicate above regresses, is bypassed, or races a
    concurrent mark-done that lands between the guard check and the
    delete, settled financial history (Paid, Received) cannot
    be physically destroyed by this route.  The pre-fix code enumerated
    ``[DONE, SETTLED]`` and silently omitted RECEIVED, then bulk-deleted
    unconditionally -- the irreversible data-loss path CRIT-05 documents.
    (``SETTLED`` is quoted as written; plan step **balance:X-am** deleted it.)
    """
    template = get_or_404(TransactionTemplate, template_id)
    if template is None:
        abort(404)

    # The paid-history-blocked branch below (flash + archive toggle) is
    # the byte-identical sibling of
    # ``transfers.hard_delete_transfer_template``; only the
    # ``*_has_paid_history`` guard name and the divergent projected-row
    # soft-delete that follows differ.  The shared part is too thin and
    # too coupled to its two parallel routes to extract without
    # indirection that removes no logic (coding-standards rule 13).
    # Pylint: ``duplicate-code`` -- one-sided disable; the paid-history-blocked
    # branch mirrors ``transfers.hard_delete_transfer_template`` but is too
    # thin and too coupled to extract (see plan.md Phase 2 notes).
    # pylint: disable=duplicate-code
    # **THREE reasons a permanent delete is refused, and each gets its own
    # sentence** (plan step ``bank_import:X-gd-2`` added the rule's).  The
    # rule's was missing: ``fk_merchant_rules_template_account`` is ON DELETE
    # CASCADE, so deleting a template destroyed every standing merchant rule
    # filing into it, under a flash that mentioned only the template.  Under
    # ruling R-GS a rule is never un-stated by its owner, which made that
    # cascade the only way one could disappear at all.  Measured 2026-08-26
    # on the developer's DEV data: template 19 carried a rule there.  *A copy
    # of production taken 2026-09-22 carries none, so its permanent arm WAS
    # live there -- and destroyed, on that copy, the recorded purchase its
    # current row holds, which the second reason now refuses.*
    #
    # **The movement reason is plan step ``credit_card:CC-5-4a-4``'s**
    # (rulings **R-CC54**, **R-CC66**, closing finding **CC-363**): a row
    # holding a payment or purchase is history whatever its status, and the
    # permanent delete below removes every non-settled row, so it deleted the
    # money with the row.  Asked AFTER the settled question, whose sentence is
    # the truer one when both hold, and it names what is held.
    #
    # The REASON is resolved before the branch rather than inside it, because
    # the archive body below is long and identical for all three -- and
    # telling an owner their template "has payment history" when what it has
    # is a merchant rule, or an unpaid envelope's purchase, is the
    # screens-stating-what-is-false defect this arc keeps closing.
    refusal = None
    held = None
    if archive_helpers.template_has_paid_history(template.id):
        refusal = (
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead."
        )
    else:
        held = archive_helpers.template_holding_movements(template.id)
    if held:
        stays = held.stays("row", named=False)
        refusal = (
            f"'{template.name}' holds {held.noun} and cannot be permanently "
            "deleted. It has been archived instead"
            + (f"; {stays}." if stays else ".")
        )
    elif refusal is None and archive_helpers.template_has_standing_rule(
        template.id,
    ):
        refusal = (
            f"'{template.name}' is where a merchant's bank spending goes and "
            "cannot be permanently deleted -- that answer would go with it. "
            "It has been archived instead."
        )
    if refusal is not None:
        flash(refusal, "warning")
        if template.is_active:
            template.is_active = False
            # pylint: enable=duplicate-code
            # The archive's own write, so the fallback hides exactly the rows
            # the archive button would (:func:`_soft_delete_projected_rows`).
            _soft_delete_projected_rows(template)
            conflict = commit_or_handle_stale(StaleConflictContext(
                logger=logger,
                log_label="hard_delete_template archive-fallback",
                log_id=template_id,
                flash_message=STALE_ACTION_MESSAGE.format(
                    noun="recurring transaction",
                ),
                redirect=RedirectTarget("templates.list_templates"),
            ))
            if conflict is not None:
                return conflict
        return redirect(url_for("templates.list_templates"))

    # No settled history, no row holding a movement and no standing rule --
    # the one act that permanently removes a definition, shared with the
    # account hard-delete since plan step balance:X-bi-7a (its docstring
    # carries the order: the non-settled rows deleted, then the definition).
    template_name = template.name
    definition_delete.permanently_delete_definition(template)
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_template",
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(
            noun="recurring transaction",
        ),
        redirect=RedirectTarget("templates.list_templates"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Recurring transaction '{template_name}' permanently deleted.", "info")
    return redirect(url_for("templates.list_templates"))


@templates_bp.route("/templates/preview-recurrence", methods=["GET"])
@require_owner
def preview_recurrence():
    """Fragment: show the next 5 occurrences for a recurrence pattern.

    Routing only.  The fragment is fetched by ``recurrence_form.js`` and built
    by :func:`app.routes._recurrence_preview.recurrence_preview_fragment`,
    beside the helpers it composes -- the endpoint is kind-agnostic (both the
    transaction-template and transfer-template forms point at it), so its body
    does not belong in the transaction-template CRUD module.
    """
    return recurrence_preview_fragment()
