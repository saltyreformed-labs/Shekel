"""
Shekel Budget App -- Transfer route package: template management.

CRUD for recurring transfer templates: list, create, edit and update, plus
the update-acceptance gate and the regenerate-and-commit step.  Every URL and
endpoint name is preserved verbatim from the pre-split
``app/routes/transfers.py``.

What happens to the ``budget.transfers`` ROWS a template stands for --
materializing them on create, and carrying an edit onto a non-repeating
template's single Transfer -- is the sibling module
:mod:`app.routes.transfers._instances`, split out at plan step R2e-3 when this
one reached the 1,000-line module cap.  The template's LIFECYCLE doors --
archive, unarchive and hard-delete -- are :mod:`app.routes.transfers.lifecycle`,
split out at plan step R7d-g-2 (ruling **R-R84**) when it reached the cap
again.
"""

import logging

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.utils.digit_strings import integer_arg
from app.extensions import db
from app.models.category import Category
from app.models.transfer_template import TransferTemplate
from app.models.account import Account
from app.services import (
    account_service,
    category_service,
    template_amount_service,
)
from app.services.pay_calendar import calendar_for
from app.routes._commit_helpers import (
    STALE_EDITING_MESSAGE,
    StaleConflictContext,
    handle_stale_conflict,
    handle_stale_form_conflict,
)
from app.routes._amount_version_actions import (
    AmountVersionAction,
    withdraw_amount_version,
)
from app.services.balance_at import BalanceContext
from app.routes._recurrence_conflict_chooser import PreEditTemplateState
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
from app.routes._loan_destination import (
    loan_destination_locks,
    loan_destination_locks_for_edit,
    settle_destination_for_update,
    settle_first_occurrence,
)
from app.routes._standing_payment import (
    regenerate_or_refuse,
    sync_loan_payment_start_or_refuse,
)
from app.routes._transfer_creation_helpers import (
    TRANSFER_TEMPLATE_KIND,
    flush_template_or_namedup_redirect,
)
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._instances import (
    materialize_initial_transfers,
    propagate_to_non_repeating_transfers,
)
from app.routes.transfers._helpers import (
    _create_schema,
    _first_template_fk_refusal,
    _update_schema,
    _user_owns,
)

logger = logging.getLogger(__name__)

# Field allowlist for the transfer-template update route: which submitted
# form fields may be written back to the template via setattr.
#
# ``default_amount`` is deliberately absent since plan step X-au-a: the amount
# is a dated SERIES as well as a column, and
# ``template_amount_service.set_amount`` is the one door that moves both
# together.  A setattr here would move one without the other.
#
# ``is_active`` is deliberately absent since plan step R7d-g-3 (plan ledger
# row **REC-525**), for the reason the transaction twin
# (``routes/templates/crud.py``) has always stated: the flag is owned by the
# dedicated archive / unarchive routes, which pair the flip with the
# projected-row cleanup this route does not perform and, since plan step
# R7d-g-2, with the promotion of the loan's next standing payment (ruling
# **R-R85**).  No update schema declares it (``BaseSchema`` drops unknown
# keys), so the entry was dead -- and allowlisting it invited a future
# schema field to archive a template through this door with neither.
_TEMPLATE_UPDATE_FIELDS = {
    "name", "from_account_id", "to_account_id",
    "category_id", "sort_order",
}

# Where this kind's amount-history withdrawal reports back to; the act itself is
# shared with the transaction-template twin (plan step X-au-a).
_AMOUNT_VERSION_ACTION = AmountVersionAction(
    logger=logger,
    edit_endpoint="transfers.edit_transfer_template",
    noun="recurring transfer",
)




@transfers_bp.route("/transfers")
@require_owner
def list_transfer_templates():
    """Redirect the retired /transfers list to the unified Recurring surface.

    Transfer templates are now listed and managed alongside recurring
    income and expenses on the unified ``/templates`` (Recurring) surface
    (Loop B).  This URL is kept as a redirect so old bookmarks -- and the
    transfer create/update routes' post-save redirects, which still target
    this endpoint -- land on the surface that replaced the standalone list.
    """
    return redirect(url_for("templates.list_templates"))


@transfers_bp.route("/transfers/new", methods=["GET"])
@require_owner
def new_transfer_template():
    """Display the transfer template creation form.

    **The start-period ``<select>`` and its preselection come off ONE calendar
    derivation** (plan step C2-f3a).  They were
    ``pay_period_service.get_all_periods`` and ``get_current_period`` -- two
    reads of ``budget.pay_periods`` for one form, the second of them SQL with
    no ``ORDER BY`` (ledger row **P19**) resolved against the process clock
    (row **P49**).  The day is ``display_today()``, the owner's civil day and
    the one ``routes/_period_options.period_move_options`` already reads for
    the sibling ``<select>`` on the edit popover; a create form and an edit
    popover disagreeing about which paycheck is current would be visible on
    consecutive clicks.

    **The offer set is still EVERY saved period, closed ones included**, which
    is deliberately NOT ``period_move_options``' narrowed rule: this form
    places the first occurrence of a definition being created, and back-dating
    a one-time transfer into a closed paycheck is a legitimate thing to author
    where MOVING an existing row backwards is the workflow ledger row **P46**
    is about.  Stated because the two ``<select>``s now sit one derivation
    apart and the difference is a policy rather than an oversight.
    """
    accounts = account_service.list_active_accounts(current_user.id)
    categories = category_service.list_active_categories(current_user.id)
    calendar = calendar_for(current_user.id)
    periods = calendar.saved()
    current_period = calendar.period_containing(display_today())

    # Pre-fill account selection from query params (for quick-action links).
    prefill_from = request.args.get("from_account", type=integer_arg)
    prefill_to = request.args.get("to_account", type=integer_arg)

    return render_template(
        "transfers/form.html",
        template=None,
        accounts=accounts,
        categories=categories,
        # One value for every recurrence control (see the transaction-template
        # twin).  A CREATE form locks nothing on the SERVER -- there is no
        # template yet to ask ``is_standing_loan_payment`` about -- but this
        # form offers every active account as a destination, so the definition
        # it is about to create may be a loan payment.  Which accounts derive
        # which bound rides to the browser below and ``recurrence_form.js``
        # locks the "Starts on" row for any loan and the "Ends" row for a loan
        # holding no payment yet; the derivation and the refusal are the
        # route's (``settle_first_occurrence``), so the locks are affordances
        # rather than the enforcement.  The edit form ships the same value
        # computed for its definition (plan step R7d-f-5).
        recurrence=create_form_recurrence_state(),
        loan_locks=loan_destination_locks(current_user.id),
        periods=periods,
        current_period=current_period,
        prefill_from=prefill_from,
        prefill_to=prefill_to,
        # A template that does not exist yet has no amount history; passed so
        # the shared form never references an undefined value.
        amount_history_rows=[],
        amount_today=None,
        amount_version_delete_endpoint="transfers.delete_amount_version",
    )


def _settle_create_references(data, start_period_id):
    """Refuse, or settle, everything the create payload REFERS to.

    One step rather than three consecutive guards, and the reason is the route
    rather than the count: each of these asks whether a submitted reference is
    the current user's to use, and the last one READS the destination it has
    just checked.  Splitting them across the route left three
    ``return redirect`` arms in a function whose own docstring records the
    single-return FK loop it already grew for the same reason -- and plan step
    R7c-b's fourth arm pushed it past pylint's ``too-many-return-statements``.
    Decomposing is this project's answer to that count, never a disable.

    Three things, in the one order they can be asked in:

    1. **Every user-scoped FK is the owner's** (commit C-27 / F-043).  A
       single-return loop so a future FK adds a row rather than an arm; the
       message-per-FK detail rides on the label.
    2. **The pay period a NON-REPEATING transfer lands in** is the owner's too,
       and this is the ONE place the request asks (plan step
       ``pay_calendar:C13-b``).  It was ``_user_owns(PayPeriod, ...)`` -- fetch
       the row by primary key, compare its ``user_id`` -- and
       ``_materialize_one_time_transfer`` then did the same again on the row
       this one had already cleared.  Now the owner's derived CALENDAR is
       asked once, where an id another user holds is simply ABSENT, and the
       RESOLVED period is threaded to that materializer, which re-fetches
       nothing.  ``transfer_service`` still asks its own tier's question, which
       is the guarantee a caller skipping this route cannot escape.
       Owner-checked at the route since plan step R7b-4: the check used to live
       inside the recurrence-form helper (``recurrence_spec_from_form``
       since plan step R-F6), because the same ``<select>``
       was ALSO the recurrence's "First paycheck" and a cross-user period would
       have shifted this owner's generation timing.  The recurrence takes a
       DATE now, so the field has one job and one owner.  Guarded on presence
       rather than folded into the loop, because it is OPTIONAL: a repeating
       transfer submits no period at all.  Resolved unconditionally when
       present, so a crafted POST pairing a foreign period with a repeating
       cadence is refused rather than ignored.
    3. **A loan destination's first occurrence is DERIVED where the
       definition would be its standing payment**, settled before the rule
       is built so nothing is authored and then replaced (plan step R7c-b,
       developer ruling 2026-08-15; the ``bind_rule_to_loan`` that replaced
       it went at plan step R7d-g-2, ruling **R-R85**); a stop stated for
       such a loan is REFUSED there too (plan step R7d-f-3, ruling
       **R-R60**); and a SECOND transfer's owner-typed start is refused at
       or before the loan's origination (ruling **R-R81**).  It runs LAST
       because it reads the destination's loan parameters, which step 1 has
       just proved are the owner's -- reading them first would be an IDOR.

    Args:
        data: The validated payload, mutated in place by step 3.
        start_period_id: The submitted pay period, already popped from *data*
            by the caller, or ``None``.

    Returns:
        ``(start_period, refusal)``.  *start_period* is the resolved
        :class:`~app.services.pay_calendar.DerivedPeriod` the caller threads to
        :func:`~._instances.materialize_initial_transfers`, or ``None`` when
        the submission named no period.  *refusal* is ``None`` when every
        reference checks out and *data* is ready to build, else the redirect
        the caller returns verbatim.

    Raises:
        PayCalendarError: When the owner holds no pay schedule, from the
            calendar this resolves against.  Uncaught, as at every other
            caller: the form that posts here rendered the owner's periods to
            choose from (ruling **R-PC42** supplies the handler).
    """
    for model, pk, label in (
        (Account, data.get("from_account_id"), "source account"),
        (Account, data.get("to_account_id"), "destination account"),
        (Category, data.get("category_id"), "category"),
    ):
        if not _user_owns(model, pk):
            flash(f"Invalid {label}.", "danger")
            return None, redirect(url_for("transfers.new_transfer_template"))

    start_period = None
    if start_period_id is not None:
        start_period = calendar_for(current_user.id).period_by_id(
            start_period_id,
        )
        if start_period is None:
            flash("Invalid start period.", "danger")
            return None, redirect(url_for("transfers.new_transfer_template"))

    return start_period, settle_first_occurrence(
        data, redirect=RedirectTarget("transfers.new_transfer_template"),
    )


@transfers_bp.route("/transfers", methods=["POST"])
@require_owner
def create_transfer_template():
    """Create a new transfer template with optional recurrence rule.

    Route-boundary FK ownership checks (commit C-27 / F-043 of the
    2026-04-15 security remediation plan): every user-scoped FK
    accepted from the form -- ``from_account_id``, ``to_account_id``,
    ``category_id``, and the optional ``start_period_id`` -- is verified
    against ``current_user.id`` before the row is persisted, by
    :func:`_settle_create_references`.  That helper also settles a LOAN
    destination's derived first occurrence, which is why the three steps are
    one call: it reads the destination it has just proved is the owner's.  The
    period is resolved THERE and threaded down, so
    :func:`~._instances.materialize_initial_transfers` re-fetches nothing --
    it re-verified the same row a second time until plan step
    ``pay_calendar:C13-b``.  The flash +
    redirect UX matches the existing template-form pattern; the security
    response rule (404 for both not-found and not-yours) is preserved
    indirectly by re-rendering the same form page rather than confirming
    whether the FK exists for someone else.
    """
    payload = load_form_or_redirect(
        _create_schema, RedirectTarget("transfers.new_transfer_template"),
    )
    if isinstance(payload, Response):
        return payload
    data = payload

    start_period_id = data.pop("start_period_id", None)
    start_period, refusal = _settle_create_references(data, start_period_id)
    if refusal is not None:
        return refusal

    # Create the recurrence rule via the F-24 preamble, or NO rule when the
    # form says "Does not repeat".  ``rule is None`` is the one-time transfer
    # since plan step R2e-3 -- the same shape a one-time transaction template
    # has always had -- and it is the create form's DEFAULT selection.
    #
    # This dereferenced ``rule.id`` unguarded until R2e-3, on a comment
    # claiming ``recurrence_pattern`` was ``required`` on
    # ``TransferTemplateCreateSchema``.  It is not: the field is
    # ``allow_none``, so any POST omitting or emptying it reached
    # ``AttributeError: 'NoneType' object has no attribute 'id'`` -- a 500
    # (defect **D13**), measured on both the absent and the empty spelling.
    new_form = RedirectTarget("transfers.new_transfer_template")
    spec = recurrence_spec_for_create(
        data,
        user_id=current_user.id,
        redirect=new_form,
    )

    template = TransferTemplate(
        user_id=current_user.id,
        **data,
    )
    db.session.add(template)

    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget("transfers.list_transfer_templates"),
        name_dup_message="A transfer with that name already exists.",
    )
    if namedup_redirect is not None:
        return namedup_redirect

    # **The definition comes FIRST and the rule is authored onto it** (plan
    # step R-F6): a recurrence rule carries its owner's FK now, so it cannot be
    # written before there is an owner.  AFTER the name-collision flush
    # specifically -- the helper flushes, and a flush before
    # ``flush_template_or_namedup_redirect``'s ``try`` would surface
    # ``uq_transfer_templates_user_name`` as an unhandled ``IntegrityError``
    # where the user gets a "name already exists" redirect today.
    rule = author_recurrence_for_create(spec, template, redirect=new_form)
    if isinstance(rule, Response):
        return rule

    # Open the amount's dated series at today (plan step X-au-a).  The
    # constructor above also carries the figure because the column is NOT NULL;
    # this call is what makes the SERIES exist, and plan step X-bp removes the
    # redundancy by removing the column.
    template_amount_service.set_amount(
        template, template.default_amount, effective_on=display_today(),
    )

    # Create the initial transfer instance(s) for the new template: a single
    # Transfer when it does not repeat, or a recurrence-engine fan-out when it
    # does.  Returns a redirect Response on a missing / invalid period or a
    # service rejection, which is propagated verbatim.
    materialize_redirect = materialize_initial_transfers(
        template, rule, start_period,
    )
    if materialize_redirect is not None:
        return materialize_redirect

    db.session.commit()
    flash(f"Transfer '{template.name}' created.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/edit", methods=["GET"])
@require_owner
def edit_transfer_template(template_id):
    """Display the transfer template edit form."""
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    accounts = account_service.list_active_accounts(current_user.id)
    categories = category_service.list_active_categories(current_user.id)
    # The form's ONE read pass (plan step R7d-f); both readers below take the
    # standing identity off its loan-resolution memo, the second for free.
    pass_ctx = BalanceContext.build(current_user.id)

    return render_template(
        "transfers/form.html",
        template=template,
        accounts=accounts,
        categories=categories,
        # The EDIT controls' starting state: see ``templates.edit_template``.
        # Since plan step R2e-3 this form offers the same empty "Does not
        # repeat" option the transaction form does, and it is FIRST -- so a
        # cadence left unselected would default to the DESTRUCTIVE clear, not
        # to a wrong cadence.  ``edit_form_cadence`` is what selects it.
        recurrence=edit_form_recurrence_state(template, pass_ctx),
        # What the script may lock as the destination changes, computed for
        # THIS definition the way ``settle_destination_for_update`` decides it
        # (plan step R7d-f-5, ruling **R-R79**), and whether the destination
        # may change at all (ruling **R-R76**: the disabled control's help).
        loan_locks=loan_destination_locks_for_edit(template, pass_ctx),
        # A LOAN PAYMENT's stop is the loan's payoff, resolved through the
        # composed door: its control renders disabled and displays that.
        periods=[],
        current_period=None,
        # The amount's dated history (plan step X-au-a), precomputed into
        # display rows.  Empty for a DERIVE-mode loan payment, whose
        # ``default_amount`` is a P&I + escrow snapshot and which therefore has
        # no series at all.
        amount_history_rows=template_amount_service.build_amount_history(
            template, display_today(),
        ),
        # What the definition costs NOW; see the transaction twin.
        amount_today=template_amount_service.current_amount(
            template, display_today(),
        ),
        amount_version_delete_endpoint="transfers.delete_amount_version",
    )


@transfers_bp.route("/transfers/<int:template_id>", methods=["POST"])
@require_owner
def update_transfer_template(template_id):
    """Update a transfer template and regenerate future transfers.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent transfer-template edit that races past
    the form-side check -- is caught and converted to the same
    flash + redirect.

    **The destination is settled BEFORE the recurrence is resolved** (plan
    step R7d-f-4, plan ledger row **REC-521**): an edit that makes this
    definition a recurring transfer into a loan -- a cadence added to a
    one-time transfer into one, or a repeating transfer moved onto one --
    takes the create door's two loan-destination rules through
    :func:`~app.routes._loan_destination.settle_destination_for_update`,
    which derives the first occurrence into the payload and decides the
    closing bound the write states; and a loan's standing payment cannot be
    moved off its loan at all (ruling **R-R76**).  The FK ownership check
    therefore runs FIRST, because that settle reads the destination's loan
    terms.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    payload = load_form_or_redirect(
        _update_schema,
        RedirectTarget(
            "transfers.edit_transfer_template", {"template_id": template_id},
        ),
    )
    if isinstance(payload, Response):
        return payload
    data = payload

    # Stale-form check (commit C-18 / F-010).  Routed through the
    # F-26 helper so the pre-flush optimistic-locking guard shares a
    # single implementation with the parallel transaction-template
    # update route.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != template.version_id:
        return handle_stale_form_conflict(
            StaleConflictContext(
                logger=logger,
                log_label="update_transfer_template",
                log_id=template_id,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="recurring transfer",
                ),
                redirect=RedirectTarget(
                    "transfers.edit_transfer_template",
                    {"template_id": template_id},
                ),
            ),
            submitted=submitted_version,
            current=template.version_id,
        )

    effective_from = data.pop("effective_from", display_today())
    # Dropped, not read: an EDIT never re-materialises the one-time Transfer
    # this field places, so the only thing a submitted value could do here is
    # reach the field-update loop as a stray kwarg.  It is still on THIS
    # schema (plan step R7b-4 moved it there from the shared recurrence
    # mixin), so a submission can still carry it and the pop is still needed.
    data.pop("start_period_id", None)
    # The closing bound, composed by the schema's ``@post_load`` into ONE
    # value under the mode key.  ABSENT when the form stated no bound --
    # a disabled control, or a partial update -- which the helpers read as
    # "leave the stored one alone" (plan step R7b-3).
    end_bound = data.pop(RECURRENCE_END_BOUND_KEY, None)

    # The template's before-image, captured BEFORE anything overwrites it
    # (plan step R2e-1).  ``had_recurrence_rule`` is what lets the
    # regeneration below tell "the user just cleared the recurrence" -- which
    # must sweep the instances the deleted rule generated -- from "this
    # template never recurred", which must not: a RULE-LESS transfer
    # template's single Transfer is an ordinary auto-generated row, so a
    # rename would otherwise delete it -- which is exactly what a
    # ``Once``-ruled transfer suffered until plan step R2e-3 made it
    # rule-less (defect D16).
    before = PreEditTemplateState(
        amount=template.default_amount,
        had_recurrence_rule=template.recurs,
    )

    # Route-boundary FK ownership (commit C-27 / F-043), asked BEFORE anything
    # below reads or writes: the field loop writes the submitted FKs, and the
    # destination settle just under this reads the submitted destination's
    # loan terms, which read of a foreign loan would be an IDOR (it stood
    # after the recurrence step until plan step R7d-f-4, when that step
    # began reading the destination).  And, since plan step R7d-f-5, the pair
    # the write would LEAVE may not be one account -- the schema grades only
    # a submission carrying both keys, and a pinned definition's form posts
    # no destination (``_first_template_fk_refusal`` says why).
    #
    # **A second refusal stood here until plan step R10-b**: a template that
    # neither had nor has a recurrence rule could not change its source or
    # destination ACCOUNT while the Transfer it created was still live, because
    # the shadow-safe propagation door accepted amount, name and category and
    # not the two account columns (plan step R2e-3).  That was a limit of the
    # door rather than a rule about transfers -- a RECURRING template with the
    # identical edit had it applied, by a sweep that destroyed and rebuilt every
    # generated row -- so one edit meant two different things depending on
    # whether the transfer repeated.  ``transfer_service.update_transfer`` can
    # move a transfer between accounts now (ruling **R-BAL168**: a moving
    # side's payment record goes with it), so the refusal has no cause left
    # and :func:`propagate_to_non_repeating_transfers` states the accounts with
    # the rest of the definition; a transfer holding the owner's records (a
    # note or a payment record) is retained there rather than moved
    # (``transfer_recurrence.propagate_to_unruled_template``).
    edit_form = RedirectTarget(
        "transfers.edit_transfer_template", {"template_id": template_id},
    )
    refused = _first_template_fk_refusal(template, data)
    if refused is not None:
        flash(refused, "danger")
        return edit_form.to_response()

    # ONE read pass for the pre-write side (plan step R7d-f): the destination
    # settle's standing-payment identity and the refusals' both read its
    # loan-resolution memo.  Regeneration afterwards builds its own, as a
    # writer must.
    pass_ctx = BalanceContext.build(current_user.id)
    # What the stranded-row refusal reads of the definition as it STANDS --
    # the rows its unarchive would restore -- asked before the settle or the
    # recurrence step below touches it (rulings R-PC93, R-PC95).
    stranding = StrandingCheck.before_the_edit(template, pass_ctx)
    # The pre-write recurrence step, in two halves that share one refusal.
    # FIRST what the destination the edit LEAVES decides about the rule's
    # bounds (plan step R7d-f-4): the derived first occurrence is written
    # into ``data`` for the second half to read, and the closing bound the
    # write states comes back settled (rulings **R-R76**, **R-R77**).  THEN
    # re-point, rebuild, or clear the recurrence rule from the update payload
    # (F-24): the helper dispatches the existing-rule (mutate in place) vs
    # no-existing-rule (build + link) branches and pops every recurrence key
    # from ``data``.
    end_bound, refusal = settle_destination_for_update(
        template, data,
        end_bound=end_bound, pass_ctx=pass_ctx, redirect=edit_form,
    )
    if refusal is None:
        refusal = resolve_recurrence_rule_for_update(
            template,
            data,
            ctx=RecurrenceFormContext(
                end_bound=end_bound,
                redirect=edit_form,
            ),
            pass_ctx=pass_ctx,
        )
    if refusal is not None:
        return refusal

    _apply_update_fields(template, data)

    # State the amount through its one write door, which moves the scalar and
    # the dated series together (plan step X-au-a).  ``effective_from`` is the
    # form's "Amount effective from" date, which also bounds the regeneration
    # below -- ONE value, applied by two different predicates (the series reads
    # a transfer's DUE date, the sweep its pay PERIOD's end).  Finding
    # **N-247** filed that seam: an edit could rewrite a row whose due date
    # preceded the date it states.  Plan step X-au-e dissolved it on the
    # template twin (``routes/templates/crud.py``), and plan step X-au-f-2
    # here, where generation copied ``default_amount`` onto every transfer it
    # made until then: the sweep writes a DECLARATION
    # (``derived_ownership(TEMPLATE)``) rather than a figure, so which
    # transfers it reaches decides nothing about money: a transfer it declares
    # resolves by its own due date.  Absent from a partial update means the
    # amount was not restated.
    if "default_amount" in data:
        template_amount_service.set_amount(
            template, data["default_amount"], effective_on=effective_from,
        )

    # Flush template changes first so name-uniqueness violations are caught
    # before regeneration dirties the session with transfer deletes/creates.
    namedup_redirect = flush_template_or_namedup_redirect(
        redirect=RedirectTarget(
            "transfers.edit_transfer_template",
            {"template_id": template_id},
        ),
    )
    if namedup_redirect is not None:
        return namedup_redirect

    return _regenerate_and_commit_template(
        template, before, effective_from, template_id, stranding,
    )


@transfers_bp.route(
    "/transfers/<int:template_id>/amount-versions/<int:version_id>/delete",
    methods=["POST"],
)
@require_owner
def delete_amount_version(template_id, version_id):
    """Withdraw one entry from a transfer template's amount history.

    The correction path for a price stamped against the wrong DATE: restating
    the amount writes a version at the date it names and leaves the mis-dated
    one standing, so removing it is a separate act.  The EARLIEST entry is
    refused by the service -- it is what every date before the series answers
    from.

    Ownership is the ``get_or_404`` on the TEMPLATE; the act itself is shared
    with the transaction-template twin
    (:func:`app.routes._amount_version_actions.withdraw_amount_version`).
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)
    return withdraw_amount_version(template, version_id, _AMOUNT_VERSION_ACTION)


def _apply_update_fields(template, data):
    """Write the allowlisted fields of *data* onto *template*.

    The update route's field loop, over :data:`_TEMPLATE_UPDATE_FIELDS` --
    the columns a submission may set directly; the amount, the rule and
    ``is_active`` each have their own door.  Its own function since plan
    step ``pay_calendar:C18-a`` gave the route one more value to hold
    (its :class:`~app.routes._recurrence_form_refusals.StrandingCheck`),
    past pylint's local-variable threshold.

    Args:
        template: The owner-checked :class:`TransferTemplate` being edited.
        data: The validated payload, the recurrence keys already popped.
    """
    for field, value in data.items():
        if field in _TEMPLATE_UPDATE_FIELDS:
            setattr(template, field, value)


def _regenerate_and_commit_template(
    template, before, effective_from, template_id, stranding,
):
    """Regenerate a transfer template's future transfers, then commit.

    FIRST brings the standing payment of the destination the edit LEAVES
    onto the loan's contract (plan step R7d-g-2, ruling **R-R85**; the same
    entry helper every lifecycle door calls, and the one write this function
    makes before regenerating).  The edit it is for: the standing payment's
    cadence UNIT moving -- every paycheck to monthly would otherwise store a
    payday as the monthly day until the next params edit healed it.  For
    every other edit the producer finds the start in step, or no loan at
    all, and writes nothing: a rename costs it one lookup.  Its one refusal
    is the window CHECK's (ruling **R-R82**), worded whole, and it sends the
    user back to the edit form.

    THEN refuses a save that would STRAND a still-projected transfer of this
    definition -- one answering an occurrence the books drop, the later
    opening of its two accounts, or sitting inside the books of the accounts
    it sits on (ruling **R-PC99**) -- whatever field the edit changed (plan
    step ``pay_calendar:C18-a``, rulings **R-PC90** / **R-PC91**;
    :func:`app.services.planned_rows_books.definition_edit_refusal`), because
    a maintain pass reaching a dropped transfer retires it -- the one below,
    for a paycheck ending on or after *effective_from* -- and one inside the
    books is counted twice.  The regeneration below is part of the state
    the save leaves, so the refusal reads that pass's own preview
    (:data:`~app.routes._transfer_creation_helpers.TRANSFER_TEMPLATE_KIND`'s
    ``preview_fn``) and asks a transfer it rewrites where the rewrite moves
    it (the round-7 review's M1).  AFTER the sync, because
    the sync can move the rule's first occurrence, and that moves which
    occurrences the save would leave: graded before it, the refusal would
    read a rule the save does not keep.  A refusal rolls the whole pending
    write back, the sync's included.

    Then re-runs ``transfer_recurrence.regenerate_for_template`` against the
    baseline scenario, diverting to the recurrence-conflict chooser when an
    amount change would overwrite hand-edited upcoming transfers, then
    commits.  Optimistic-lock and name-uniqueness failures at flush time are
    converted to the same flash + redirect the form-side guards produce, so a
    concurrent edit never surfaces as a 500.

    Args:
        template: The TransferTemplate whose field changes are already staged
            in the session.
        before: The template's pre-edit state
            (:class:`~app.routes._recurrence_form_helpers.PreEditTemplateState`)
            -- its amount gates the chooser and its ``had_recurrence_rule``
            gates the sweep; see :func:`regenerate_or_conflict_chooser`.
        effective_from: Date from which regeneration applies.
        template_id: The template's id, used for redirect kwargs and logging.
        stranding: The route's
            :class:`~app.routes._recurrence_form_refusals.StrandingCheck`,
            built before the edit: the rows the definition's unarchive would
            restore as it stood.  The refusal builds its own read pass after
            the sync, and the regeneration its own after that.

    Returns:
        A ``Response`` -- the chooser, or the edit form on a stale-data or
        name-duplicate conflict, or a redirect to the template list on
        success.
    """
    edit_form = RedirectTarget(
        "transfers.edit_transfer_template", {"template_id": template_id},
    )
    # The standing payment's sync first, then the stranded-row refusal: the
    # sync may move the rule's first occurrence, and the refusal grades the
    # rule the save would leave (the edit is whole and flushed by now).
    # ``rows_follow=False``: the pass below is the one that brings this
    # definition's rows along, and the standing payment is the only
    # definition the sync can move from this door (see the helper).
    refused = sync_loan_payment_start_or_refuse(
        template.to_account_id, redirect=edit_form, rows_follow=False,
    ) or refuse_stranding_save(
        template, stranding, edit_form,
        kind=TRANSFER_TEMPLATE_KIND, effective_from=effective_from,
    )
    if refused is not None:
        return refused

    # A template that neither has nor had a rule does not regenerate at all
    # (the gate below returns before touching a row -- that is what closes
    # defect D16), so its already-created Transfer is reached HERE or nowhere.
    if not before.had_recurrence_rule and not template.recurs:
        refused = propagate_to_non_repeating_transfers(template)
        if refused is not None:
            return refused

    # Regenerate future transfers, diverting to the conflict chooser when an
    # amount change would overwrite hand-edited upcoming instances -- and,
    # since plan step R7d-g-2, translating the transfer service's own refusal
    # of a row (ruling R-C: a second transfer's every-paycheck row dated at
    # or before its loan's origination) into this form's flash rather than a
    # 500 (``_standing_payment.regenerate_or_refuse``).
    diverted = regenerate_or_refuse(
        template, before, effective_from, redirect=edit_form,
    )
    # The chooser short-circuits (its pending edit is already rolled back),
    # and so does a refusal.
    if diverted is not None:
        return diverted

    try:
        db.session.commit()
    except StaleDataError:
        return handle_stale_conflict(StaleConflictContext(
            logger=logger,
            log_label="update_transfer_template",
            log_id=template_id,
            flash_message=STALE_EDITING_MESSAGE.format(
                noun="recurring transfer",
            ),
            redirect=RedirectTarget(
                "transfers.edit_transfer_template",
                {"template_id": template_id},
            ),
        ))
    except IntegrityError:
        db.session.rollback()
        flash("A recurring transfer with that name already exists.", "warning")
        return redirect(url_for("transfers.edit_transfer_template", template_id=template_id))
    # An edit that ended the recurrence removed this template's upcoming
    # projected transfers; "updated." alone would report a destructive change
    # as a routine one.
    #
    # **"were removed" was unqualified until plan step R10-b**, and an
    # adversarial review of that step caught it: the maintain pass RETAINS a
    # projected transfer carrying the owner's own records, and flashes its own
    # notice beside this one -- so the two sentences contradicted each other on
    # a single save.  This one now says what it can promise, and the retained
    # notice says which rows it did not reach.  (``routes/templates/crud.py``
    # carries the same wording on the transaction side, stale since plan step
    # R10-a; reported, not fixed here.)
    if before.had_recurrence_rule and not template.recurs:
        flash(
            f"'{template.name}' no longer repeats. Its upcoming projected "
            "transfers were removed, except any you have records against; "
            "settled and hand-edited ones were kept.",
            "success",
        )
    else:
        flash(f"Recurring transfer '{template.name}' updated.", "success")
    return redirect(url_for("transfers.list_transfer_templates"))
