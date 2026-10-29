"""
Shekel Budget App -- Transaction route package: shared helpers.

The Marshmallow schema singletons, the credit-payback unique-index name
constant, and the private render / ownership / FK helpers shared across the
transaction route sub-modules.  The response-surface bundle and the phone
card they answer with are shared with the transfer routes
(:mod:`app.routes._mobile_card`, ruling **plan#1983**).  Schema
instances are constructed once at import time so every handler reuses the
same instance (Marshmallow contract), preserving the pre-split monolith's
behaviour.
"""

import logging

from functools import wraps

from flask import render_template
from flask_login import current_user

from app.extensions import db
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status
from app.routes._mobile_card import RenderTarget, render_mobile_card
from app.routes._render_helpers import render_transaction_cell
from app.routes.transactions._bp import transactions_bp
from app.schemas.validation import (
    MarkDoneSchema,
    ShownLinesSchema,
    TransactionDeleteSchema,
    TransactionItemUpdateSchema,
    TransactionUpdateSchema,
    TransactionCreateSchema,
    InlineTransactionCreateSchema,
)
from app.services import grid_view_service
from app.services.state_machine import finalised_edit_rejection
from app.services.pay_calendar import calendar_for
from app.utils.auth_helpers import (
    get_accessible_transaction,
    get_accessible_transaction_or_deleted,
    is_not_found_to_transaction_doors,
    log_refused_lookup,
)
from app.utils.db_errors import is_unique_violation
from app.utils.error_fragments import (
    INVALID_REFERENCE_MSG,
    designed_error,
    refusal_for_a_gone_row,
)

# Name of the partial unique index that backstops commit C-19's
# duplicate CC Payback fix.  Mirrors the literal in
# ``migrations/versions/b3d8f4a01c92_*.py`` and
# ``app.models.transaction.Transaction.__table_args__``; renaming
# the index requires a coordinated edit across all three sites.
_CREDIT_PAYBACK_UNIQUE_INDEX = "uq_transactions_credit_payback_unique"

# Package-local alias of the shared foreign-key rejection message so the
# mutation handlers import it alongside the other private helpers.
_INVALID_REFERENCE_MSG = INVALID_REFERENCE_MSG

# Money / period / category / due-date fields that the finalised-row edit
# lock (#26) protects.  Names match :class:`TransactionUpdateSchema` (the
# loaded ``data`` dict for both the regular and the transfer-shadow PATCH
# paths).  Display fields (``notes``, ``name``) and the ad-hoc visibility
# flags stay editable on a finalised row; the ``status_id`` transition is
# guarded separately by ``state_machine.verify_transition``.
#
# ``settled_on`` is deliberately NOT here (ruling **R-ED**, plan step X-f1c).
# Every member below is a BUDGET DECISION the user made, locked so a paid
# movement cannot be retroactively rewritten; the settle day is an OBSERVED
# FACT about their bank, and an observed fact gets corrected when the statement
# says otherwise.  Locking it would make the correction UNREACHABLE -- reverting
# to Projected and re-settling stamps TODAY, so "this actually cleared last
# Tuesday" would be inexpressible.  It is the same line ``TransactionEntry``
# draws one table over (``purchased_on`` guarded, ``settled_on`` freely
# editable on the inline form).
#
# ``settled_amount`` is NOT here either, and it sits on exactly the same side
# of that line as ``settled_on`` (developer ruling, 2026-08-17).  A draft of
# plan step X-au-c3 locked it, reasoning that "re-stating what a settle recorded
# is not an edit" -- but the two facts a settle records are the FIGURE and the
# DAY, both observations about the bank, and locking one while exempting the
# other split a pair that belongs together.  The estimate and the actual are
# different facts and get different boxes: the estimate is the budget decision
# the lock protects, and the actual is what the statement says.  Locking it made
# the only correction path revert-and-re-settle, which silently re-booked a
# retained figure over a re-planned amount -- so the lock did not merely
# inconvenience, it produced a wrong number.
_LOCKED_EDIT_FIELDS = frozenset({
    "estimated_amount", "category_id", "pay_period_id", "due_date",
})

logger = logging.getLogger(__name__)

# Marshmallow schema instances.
_update_schema = TransactionUpdateSchema()
_item_update_schema = TransactionItemUpdateSchema()
_create_schema = TransactionCreateSchema()
_inline_create_schema = InlineTransactionCreateSchema()

# Schema for the optional ``settled_amount`` form field on
# ``mark_done`` (the field was ``actual_amount`` until plan step X-au-c3
# replaced that column with a settlement record).  Single instance per
# process (Marshmallow contract); replaces the per-branch raw
# ``Decimal(request.form.get(...))``
# parse the route used before commit C-27 / F-042 / F-162 of the
# 2026-04-15 security remediation plan.
_mark_done_schema = MarkDoneSchema()

# The delete door's parameters, sent as a query string: the bank lines and the
# purchases its dialog named (plan step ``credit_card:CC-5-4a-5``, rulings
# **R-CC127** / **R-CC131**).
_delete_dialog_schema = TransactionDeleteSchema()

# Undo CC's, also a DELETE: the bank lines the card's caption named for the
# payback it deletes (plan step ``credit_card:CC-5-4a-5``, ruling **R-CC80**).
_unmark_credit_schema = ShownLinesSchema()


def _mark_done_success_response(txn, target):
    """Build the success response tuple for a mark_done request.

    Forks on the rendering surface the request came from:

      * ``target.render_mode == "mobile_card"``: return the single
        re-rendered mobile card + ``HX-Trigger: mobileCardSettled``.  The
        card swaps in place (no reload); the owner This Period summary
        blocks listen for ``mobileCardSettled`` and self-refresh, while
        the companion page has no summary blocks so only the card
        updates.
      * otherwise (desktop grid / full-edit popover): the desktop cell +
        ``HX-Trigger: balanceChanged`` -- a targeted swap, no reload.
        The freshly settled cell swaps in place (``hx-target`` is the
        cell), and ``balanceChanged from:body`` drives the self-refresh
        on the sticky ``<tfoot>`` balance row (grid/_balance_row.html)
        and the two summary subtotal ``<tbody>`` sections
        (grid/_subtotal_rows.html), so the daily desktop mark-paid feels
        instant.  The helper is reached solely from
        :func:`_mark_done_regular`; a transfer LEG's Mark Paid is the
        transfer's own door since leaf ``balance:X-bi-6-1``
        (``routes/transfers/_helpers._render_post_mutation_cell``), which
        keeps ``gridRefresh`` because the far leg's cell may change too and
        only a full reload re-renders it today;
        ``mark_credit`` / ``cancel_transaction`` / ``unmark_credit``
        likewise keep ``gridRefresh`` because they add or remove grid
        rows, which an in-place cell swap cannot express.

    Args:
        txn: The settled Transaction.
        target: The :class:`RenderTarget` describing the response
            surface.  ``render_mode`` selects the mobile-card vs desktop
            path; ``card_prefix`` / ``can_edit`` are forwarded to the
            card render and are only meaningful on the mobile_card path.

    Returns:
        A Flask ``(html, status, headers)`` response tuple.
    """
    if target.render_mode == "mobile_card":
        return (
            render_mobile_card(
                txn, card_prefix=target.card_prefix, can_edit=target.can_edit,
            ),
            200,
            {"HX-Trigger": "mobileCardSettled"},
        )
    return render_transaction_cell(txn), 200, {"HX-Trigger": "balanceChanged"}


def _credit_payback_idempotent_response(exc, txn_id):
    """Translate a credit-payback unique-index violation into a 200.

    Backstop for commit C-19 (audit finding F-008): if a writer that
    holds no owner lock races ``credit_workflow.mark_as_credit`` (the
    request's owner write lock serialises every request since plan step
    ``balance:X-bn``) and a duplicate payback INSERT reaches PostgreSQL,
    ``uq_transactions_credit_payback_unique`` rejects it and this
    helper rolls back, re-fetches the source row, and renders the
    cell at HTTP 200 -- matching what a serialised request would
    have produced.  Other ``IntegrityError`` constraint hits return
    the standard 400 so unrelated FK / check failures stay visible.
    """
    db.session.rollback()
    if not is_unique_violation(exc, _CREDIT_PAYBACK_UNIQUE_INDEX):
        return _error_transaction_response(txn_id, _INVALID_REFERENCE_MSG)
    logger.info(
        "Duplicate CC payback prevented on mark_credit id=%d "
        "(idempotent success)", txn_id,
    )
    refreshed = _get_owned_transaction(txn_id)
    if refreshed is None:
        return "Not found", 404
    return (
        render_transaction_cell(refreshed),
        200,
        {"HX-Trigger": "gridRefresh"},
    )


def _stale_transaction_response(txn_id, target=None):
    """Roll back the session and render the cell in conflict mode + 409.

    Used by every PATCH/POST/DELETE handler that can race a
    concurrent commit against the version-pinned UPDATE.  Re-fetches
    the transaction from the database so the user sees the winner's
    state -- never the loser's stale in-memory copy -- and tags the
    cell with ``conflict=True`` so the template surfaces a warning
    indicator.  When the winning request deleted the row -- hard, or soft:
    the re-fetch goes through the ownership door, which answers a
    soft-deleted row "not found" since plan step ``credit_card:CC-5-4a-4``
    (ruling **R-CC89**) -- there is no cell to render, and it raises
    :class:`_RowGone` for the door to answer.

    The mobile/companion Mark Paid path passes a :class:`RenderTarget`
    with ``render_mode == "mobile_card"`` so the 409 body is the
    re-rendered mobile card (latest state) rather than the desktop cell;
    the card's ``hx-target`` is the card wrapper, so a desktop-cell body
    would not swap.  That path re-fetches through
    :func:`get_accessible_transaction` so a companion's
    conflict resolves against the linked owner's row (the desktop path
    uses :func:`_get_owned_transaction`, which is owner-only).

    Args:
        txn_id: Primary key of the transaction the route was trying
            to mutate.  Used to re-fetch under ownership checks so
            the conflict UI renders the correct row.
        target: The :class:`RenderTarget` for the mobile/companion Mark
            Paid path, or ``None`` (the default) for the desktop-cell
            conflict every non-mobile caller wants.  When present and
            ``render_mode == "mobile_card"`` the 409 body is the mobile
            card; ``card_prefix`` / ``can_edit`` drive its wrapper id and
            the owner-vs-companion edit affordance.

    Returns:
        Flask response tuple ``(html, 409)``.

    Raises:
        _RowGone: When the re-fetch finds no row this surface may draw.
    """
    db.session.rollback()
    db.session.expire_all()
    if target is not None and target.render_mode == "mobile_card":
        txn = get_accessible_transaction(txn_id)
        if txn is None:
            raise _RowGone()
        return (
            render_mobile_card(
                txn, card_prefix=target.card_prefix, can_edit=target.can_edit,
            ),
            409,
        )
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        raise _RowGone()
    return render_transaction_cell(txn, conflict=True), 409


def _error_transaction_response(txn_id, message, target=None, status=400):
    """Roll back and render the request's surface as a designed error.

    The rejected-mutation twin of :func:`_stale_transaction_response`
    (the marker-header convention, closeout plan session 4): every
    transaction-mutation 400/422 that a user can reach re-renders the
    surface the request targeted -- the desktop cell, or the mobile /
    companion card when the request carried ``render=mobile_card`` --
    with CURRENT data plus the rejection message, and stamps the
    designed-fragment header so the body swaps instead of being
    silently dropped by the app-wide htmx config.

    Rolls back unconditionally: some callers reject before any mutation
    (a no-op rollback), others after the service staged changes; one
    rollback here keeps every caller safe and the re-fetch below then
    reads committed state.

    Args:
        txn_id: Primary key of the transaction the route was trying to
            mutate.  Re-fetched under the same ownership rules as the
            stale-conflict helper (accessible for the mobile-card path,
            owner-only for the desktop cell).
        message: The user-facing rejection message (shown in the cell's
            title/aria hint or the card's danger banner).
        target: The :class:`RenderTarget` for the mobile/companion
            path, or ``None`` for the desktop cell.
        status: The HTTP error status (400 domain rejection, 422
            validation failure).

    Returns:
        A designed-fragment Flask response tuple.

    Raises:
        _RowGone: When the re-fetch finds no row this surface may draw --
            a companion's request for the owner-only desktop cell -- and the
            blueprint's handler answers it "not found".
    """
    db.session.rollback()
    db.session.expire_all()
    if target is not None and target.render_mode == "mobile_card":
        txn = get_accessible_transaction(txn_id)
        if txn is None:
            raise _RowGone()
        return designed_error(
            render_mobile_card(
                txn, card_prefix=target.card_prefix,
                can_edit=target.can_edit, error=message,
            ),
            status,
        )
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        raise _RowGone()
    return designed_error(render_transaction_cell(txn, error=message), status)


class _RowGone(Exception):
    """A refused request's row is not one its surface may draw.

    Raised by :func:`_error_transaction_response` and
    :func:`_stale_transaction_response` when, after rolling back, their
    re-fetch through the ownership door finds nothing to render: the door
    refuses the row for this surface -- a companion's request for the
    owner-only desktop cell.  Those two helpers answer for every
    transaction door, so they hand the moment up to the blueprint's handler
    (:func:`_row_gone_is_not_found`), which answers ruling **R-CC89**'s "not
    found".

    *Until plan step ``balance:X-bn`` it also carried a row deleted WHILE the
    request ran -- the race ruling **R-CC96**'s row lock let a door see --
    and the doors ruling **R-CC101** names caught it to say which act the
    delete refused.  The catch was deleted with the row lock, and the moment
    it answered is STILL REACHABLE: the owner's write lock (ruling
    **R-CC106**) precedes the door's first read, but it belongs to the
    TRANSACTION, and both helpers roll back before they re-fetch.  A Delete
    queued on the lock commits in between, the re-fetch finds nothing, and
    the request answers this handler's bare "not found" (finding BAL-565,
    measured by review A of the step's ninth checkpoint).  The developer ruled
    to ship it so, money correct (**R-BAL156**); plan step ``balance:X-dc``
    makes each save one transaction, which closes the moment.*
    """


@transactions_bp.errorhandler(_RowGone)
def _row_gone_is_not_found(_exc):
    """Answer a gone row "not found" at every door that does not name one.

    Ruling **R-CC89**'s answer, and what the two helpers that raise
    :class:`_RowGone` returned themselves until plan step
    ``credit_card:CC-5-4a-4``.  Every transaction door answers it here,
    the two :func:`_door_naming_a_gone_row` opens among them.

    Returns:
        ``("Not found", 404)``.
    """
    return "Not found", 404


def _deleted_row_change_refusal(gone):
    """Return the sentence a Save on a hidden row is refused with.

    Plan step ``credit_card:CC-5-4a-4``, ruling **R-CC105** (developer
    2026-09-23, "One Save sentence"): *"Every Save on a deleted row, paid or
    unpaid, with or without an Actual, shows the red 'Deleted' cell. Hovering
    gives 'Hotel was deleted: this change cannot be saved.  Reload the
    page.' Each button names what it tried to do, and the words are true for
    every Save."*  It says "was archived" where the row's recurring item is
    (ruling **R-CC107**: "the same for a Save").  Mark Paid's is the status
    seam's ``deleted_row_payment_refusal`` and add purchase's
    ``entry_service.deleted_row_purchase_refusal``; this one lives with the
    route because no service refuses a Save as such.

    Args:
        gone: The :class:`~app.utils.hidden_row.HiddenRow` -- the row's name
            (ruling **R-CC98**: never its id), and whether its recurring item
            is archived.

    Returns:
        The refusal, naming the row.
    """
    return (
        f"{gone.name} {gone.went}: this change cannot be saved.  "
        "Reload the page."
    )


def _gone_transaction_response(gone, refusal, txn_id, target=None):
    """Render the surface a request targeted for a row that is gone, saying why.

    The cell or the card, keyed by the id in the URL -- the only thing left
    of a one-off row its delete removed.  The desktop cell becomes the red
    "Deleted" of ruling **R-CC102** (``grid/_transaction_cell_gone.html``) --
    "Archived" for a row whose recurring item is archived, so the word agrees
    with the sentence behind it (ruling **R-CC108**) -- and the phone card the
    banner-only card the cancelled-row refusal already uses
    (``grid/_mobile_card_error.html``), each carrying the sentence
    :func:`~app.utils.error_fragments.refusal_for_a_gone_row` chooses (plan
    step ``credit_card:CC-5-4a-4``, rulings **R-CC101**, **R-CC104**,
    **R-CC107**).  Designed and a 404: the door answers the row "not found"
    (ruling **R-CC89**), and the body now says why rather than being dropped.

    Args:
        gone: The :class:`~app.utils.hidden_row.HiddenRow` the door may name,
            or ``None`` for a row it may not.
        refusal: The door's sentence for its act, taking the ``HiddenRow``.
        txn_id: The row id the request named.
        target: The :class:`RenderTarget`, or ``None`` for the desktop cell.

    Returns:
        A designed-fragment Flask response tuple at 404.
    """
    message = refusal_for_a_gone_row(gone, refusal)
    if target is not None and target.render_mode == "mobile_card":
        body = render_template(
            "grid/_mobile_card_error.html",
            card_id=grid_view_service.row_card_dom_id(
                txn_id, target.card_prefix,
            ),
            error=message,
        )
    else:
        body = render_template(
            "grid/_transaction_cell_gone.html", message=message,
            archived=gone is not None and gone.archived,
        )
    return designed_error(body, 404)


def _door_naming_a_gone_row(refusal):
    """Open the transaction door for a view, and say so when its row is gone.

    Plan step ``credit_card:CC-5-4a-4``: the door of the two transaction
    routes ruling **R-CC101** names (Mark Paid, the popover's Save), as a
    decorator, so each view states its sentence once and receives the live
    row it acts on.  A row that is gone is answered on the surface the
    request targeted (:func:`_gone_transaction_response`) before the view
    runs -- the page acted on a row deleted in another tab (ruling
    **R-CC104**): a deleted row the requester may reach is named by
    *refusal*, saying so where its recurring item is archived, however the
    row was hidden (ruling **R-CC107**), and anything else -- a one-off row
    its delete removed, a missing id, another user's row -- reads
    :data:`~app.utils.error_fragments.ROW_NO_LONGER_EXISTS_MSG`, the same
    words whichever it was.

    **This is the moment a Delete that committed BEFORE the request is
    answered.**  Since plan step ``balance:X-bn`` (ruling **R-CC106**) the
    request's transaction takes its owner's write lock before this door reads
    the row, so another tab's Delete either committed before the read or
    waits for THAT TRANSACTION to end -- not the request's.  A view that
    commits and then draws its answer, or rolls back and then redraws its
    refusal, opens a second transaction, and a queued Delete commits between
    the two (finding BAL-565): a one-off row's answer is then a server error,
    a refusal's the bare "not found" of :class:`_RowGone`, and a recurring
    row's successful save draws its cell as if the row were live.  The
    answer this
    decorator gave that moment (ruling **R-CC96**'s row-lock race) was
    deleted with the row lock at that step and stays deleted (**R-BAL156**);
    plan step ``balance:X-dc`` makes each save one transaction.  A row the
    view's refusal cannot redraw for another reason (:class:`_RowGone`) is the
    blueprint handler's "not found".  The view is called as
    ``view(txn, target)``: the row as the door served it, and the
    :class:`RenderTarget` read off the form.

    Args:
        refusal: The door's sentence for its act, taking a
            :class:`~app.utils.hidden_row.HiddenRow`.

    Returns:
        The decorator.
    """
    def decorator(view):
        @wraps(view)
        def door(txn_id):
            target = RenderTarget.from_form()
            answer = get_accessible_transaction_or_deleted(txn_id)
            if not isinstance(answer, Transaction):
                return _gone_transaction_response(
                    answer, refusal, txn_id, target,
                )
            return view(answer, target)
        return door
    return decorator


def _finalised_edit_response(txn, data):
    """Reject locked-field edits on a finalised (is_immutable) transaction.

    Looks up the current and (if the PATCH transitions status) the new
    :class:`Status` BEFORE the caller's ``setattr`` loop dirties the
    session -- matching ``_resolve_status_change``'s autoflush-safe
    ordering -- and defers the policy decision to
    :func:`finalised_edit_rejection`.  Applies to the edit path
    (``mutations._apply_regular_update``), the one user edit entry point
    since leaf ``balance:X-bi-6-1`` (the transfer-shadow path it shared
    the guard with is gone; a transfer's PATCH has its own,
    ``routes/transfers/mutations._reject_finalised_transfer_edit``); the
    system mutation paths (recurrence, carry-forward, mark-done, cancel)
    deliberately bypass this lock.

    It lives HERE rather than beside either caller because those two now
    sit in different modules (plan step X-f1c's split), and a route helper
    both mutation modules need is exactly this module's purpose -- the
    alternative is one module importing the other, which closes a cycle.

    Args:
        txn: The Transaction (or transfer shadow) being edited.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).

    Returns:
        A designed 400 error-fragment response when a locked field is
        edited on a finalised row not being reverted to a mutable
        status, or ``None`` when the edit may proceed.
    """
    if not _LOCKED_EDIT_FIELDS & data.keys():
        return None
    current_status = db.session.get(Status, txn.status_id)
    new_status = (
        db.session.get(Status, data["status_id"])
        if "status_id" in data else None
    )
    message = finalised_edit_rejection(
        current_status, new_status, context="transaction",
    )
    if message is None:
        return None
    return _error_transaction_response(txn.id, message)


def _update_schema_for(txn):
    """Return the PATCH schema *txn*'s shape loads (plan step ``balance:X-bi-7b``).

    A PLACED row -- a rule-less definition's, whose tracking / visibility
    flags are the definition's and editable at the popover (ruling
    **R-BAL23**) -- loads
    :class:`~app.schemas.validation.TransactionItemUpdateSchema`, which
    declares them.  Every other row loads the row schema alone, which
    declares no flag, so a flag in a crafted PATCH is dropped by
    ``Meta.unknown = EXCLUDE`` before any code could write it: a RECURRING
    definition's row since leaf 7b-2 (the writer finding **BAL-484**
    recorded), and a transfer shadow or a CC payback since the family's
    cutover (plan step ``balance:X-bi-7d-2``, ruling **R-BAL73**) -- such a
    row has no definition and no cell of its own, so there is nothing for a
    flag to land on.  Until that cutover the item schema was loaded for
    every non-recurring row, because a legacy link-less one-off stated its
    flags in cells of its own.

    Args:
        txn: The row being edited.

    Returns:
        The schema instance to validate and load ``request.form`` with.
    """
    return _item_update_schema if txn.is_placed else _update_schema


def _get_owned_transaction(txn_id):
    """Fetch a transaction and verify it belongs to the current user.

    Ownership is the row's OWN ``user_id`` column since plan step
    ``pay_calendar:C13-b``.  This read was ``txn.pay_period.user_id``, and the
    docstring here said "since transactions don't have a direct user_id
    column" -- a premise plan step ``C13-a`` retired.  The two values cannot
    disagree: ``fk_transactions_owner_period`` holds the row's owner equal to
    its paycheck's on every write.

    **Dropping the walk drops a HYDRATION the callers used to inherit**, and
    :func:`app.routes._render_helpers.render_transaction_cell` is the one that
    names it -- see its own docstring, which this step re-measured.

    **A DELETED row answers ``None`` too**, through the one predicate
    :func:`~app.utils.auth_helpers.is_not_found_to_transaction_doors` states
    for both ownership doors (ruling **R-CC89**): it takes no money, and a
    stale popover's Save or Mark Credit on one wrote a payment record or a
    card payback under a row no screen shows.  (A transfer's SHADOW row did
    too, leaf ``balance:X-bi-6-1``'s interval fence, until plan step
    ``balance:X-bi-6-5`` dropped the ``transfer_id`` column that named one.)

    Returns:
        Transaction if found, owned by current_user and not deleted, else
        None.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    if txn.user_id != current_user.id:
        return None
    if is_not_found_to_transaction_doors(txn):
        return None
    return txn


def _resolve_owned_period(period_id, not_found_msg="Pay period not found"):
    """Answer ``current_user``'s calendar for *period_id*, or refuse.

    **The ONE way this blueprint asks whether a submitted paycheck is the
    requester's** (plan step ``pay_calendar:C13-b``).  Three doors -- both
    create routes and the PATCH door's :func:`_verify_owned_fks_in_update` --
    passed a ``PayPeriod`` spec to :func:`_resolve_owned_fks` instead: fetch
    ``budget.pay_periods`` by primary key, compare ``row.user_id`` against the
    requester.  Those are three of the EIGHT primary-key refetches finding
    **P75** counts.

    **Why the calendar and not the composite key.**  ``C13-a`` made a
    transaction filed in a stranger's paycheck UNSTORABLE
    (``fk_transactions_owner_period``), so deleting these checks outright
    would still refuse the write -- but as an ``IntegrityError`` the create
    routes translate into ``400 "Invalid reference..."``, where the security
    response rule asks for the 404 that "not found" gets.  The key answers
    STORAGE; this answers INPUT, and they are two different questions.  A
    calendar holds ONE owner's whole schedule, so a foreign id is ABSENT
    rather than present-and-rejected: the two answers the rule wants
    indistinguishable are one answer, with no comparison left for a later edit
    to drop.  It is the shape plan step C2-f3e ruled for the READ doors, and
    the developer ruled this step to extend it to the write doors (2026-09-03).

    **It LOGS where ``_resolve_owned_fks`` is silent**, and that is
    :func:`~app.utils.auth_helpers.log_refused_lookup`'s stated rule rather
    than an addition: an owner-scoped lookup cannot tell "no such row" from
    "not yours", which is the stronger security property and exactly why it
    must not also mean no trail.

    Args:
        period_id: A submitted ``budget.pay_periods.id``, or ``None``.
        not_found_msg: The 404 body.  Defaults to the write doors' wording;
            the grid-cell fragments pass their own uniform ``"Not found"`` so
            no fragment's body says which of its four ids was wrong.

    Returns:
        ``(period, None)`` with the resolved
        :class:`~app.services.pay_calendar.DerivedPeriod`, or
        ``(None, (not_found_msg, 404))`` -- a Flask response tuple the caller
        returns directly to HTMX.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar (see
            :func:`~app.services.pay_calendar.calendar_for`).  Uncaught, as at
            every other route that derives one -- but note it CHANGES what a
            schedule-less owner meets at these three write doors: the
            application handler (**R-PC42**) renders the recovery card at
            **200** where the primary-key compare answered
            ``("Pay period not found", 404)``.  Nothing leaks -- that card
            names no submitted id -- and the state is unreachable for an owner
            registration has served since plan step ``balance:X-ad-a``.
    """
    period = calendar_for(current_user.id).period_by_id(period_id)
    if period is None:
        log_refused_lookup("PayPeriod", period_id)
        return None, (not_found_msg, 404)
    return period, None


def _resolve_owned_fks(specs):
    """Fetch and ownership-check a sequence of user-scoped FK ids.

    Centralizes the IDOR probe shared by the transaction create routes
    (:func:`create_inline`, :func:`create_transaction`) and the grid
    form-partial routes (:func:`get_quick_create`,
    :func:`get_full_create`, :func:`get_empty_cell`, which reach it through
    their one shared ``_resolve_grid_cell``) and the PATCH door's own
    :func:`_verify_owned_fks_in_update`.  **No ``PayPeriod`` spec arrives here
    any more**: plan step C2-f3e answered the period from the owner's derived
    pay calendar on the three form-partial routes, and plan step
    ``pay_calendar:C13-b`` moved the last three -- the two create doors and
    the PATCH door -- onto :func:`_resolve_owned_period`, which is that same
    lookup given a name.  What is left here fetches by primary key and
    compares, because no derivation owns ``budget.accounts``,
    ``budget.categories`` or ``budget.scenarios``.  For each
    ``(model, obj_id, not_found_msg)`` spec the row is fetched by
    primary key and confirmed to belong to ``current_user``; a missing
    row and a cross-user row return the identical 404 so an attacker
    cannot distinguish the two (security response rule: "404 for both
    not found and not yours").  Specs are checked in order and the
    first failure short-circuits, so callers list them so the most
    specific message surfaces first -- mirroring the sequential per-FK
    checks these routes used before the extraction.

    A ``None`` ``obj_id`` resolves to the 404 without issuing a
    NULL-primary-key query (which SQLAlchemy warns cannot load a row);
    the create routes never pass ``None`` (schema-required fields), but
    the form-partial routes read ids straight off the query string.

    Args:
        specs: ordered ``(model, obj_id, not_found_msg)`` tuples.  Use a
            distinct model class per spec: the returned map is keyed by
            model, so two specs sharing one model collapse to the last
            row fetched.  That precondition holds for every caller today;
            note it touches only the convenience map -- every spec is
            still ownership-checked, so a collision could never weaken
            the 404 gate.

    Returns:
        ``(resolved, None)`` on success, where *resolved* maps each
        spec's model class to its fetched row, or ``(None, (msg, 404))``
        on the first ownership failure -- a Flask response tuple the
        caller returns directly to HTMX.
    """
    resolved = {}
    for model, obj_id, not_found_msg in specs:
        obj = db.session.get(model, obj_id) if obj_id is not None else None
        if obj is None or obj.user_id != current_user.id:
            return None, (not_found_msg, 404)
        resolved[model] = obj
    return resolved, None


def _verify_owned_fks_in_update(data):
    """Verify cross-user FK ownership for the PATCH update payload.

    Used by :func:`update_transaction` to reject ``pay_period_id``
    and ``category_id`` values that belong to another user before
    any state-changing work runs.  The period goes through
    :func:`_resolve_owned_period` since plan step ``pay_calendar:C13-b`` --
    the owner's calendar, asked FIRST so the message order this door's 404
    strings depend on is unchanged -- and the category through
    :func:`_resolve_owned_fks`.  Without this probe an
    authenticated owner could submit a victim's ``pay_period_id``
    or ``category_id`` and the unfiltered ``setattr`` loop in
    :func:`update_transaction` would silently re-parent the
    transaction into the victim's namespace -- the FK constraint
    passes because the row exists, just under another user, and
    PostgreSQL never raises ``IntegrityError``.

    Audit reference: F-029 / commit C-29 of the 2026-04-15
    security remediation plan.  Mirrors the route-boundary FK
    probes already in :func:`create_inline` and
    :func:`create_transaction`; ``status_id`` is a reference table
    FK (not user-scoped) and so does not need an ownership check.
    The 404 strings deliberately match the messages used by the
    create routes so the client cannot tell whether the row does
    not exist or belongs to another user (security response rule:
    "404 for both not found and not yours").

    **It hands the TARGET paycheck back since plan step ``balance:X-bi-7b``**
    rather than discarding it: a period move re-places a one-off on that
    paycheck's start (ruling **R-BAL33**), and the calendar has already
    answered which paycheck that is.  Deriving it again at the field write
    would be the second derivation of one owner's calendar in one request
    that ledger row **P68** measured and C2-f3c closed.

    Args:
        data: The schema-loaded PATCH payload.  ``pay_period_id``
            and ``category_id`` are the only user-scoped FK keys
            inspected; absent keys are skipped.

    Returns:
        ``(target_period, None)`` on success -- the submitted paycheck as
        the owner's calendar derived it, or ``None`` when the payload names
        none.  On failure, ``(None, (body, 404))``, a Flask response tuple
        the caller returns directly to HTMX.
    """
    target_period = None
    if "pay_period_id" in data:
        target_period, error = _resolve_owned_period(data["pay_period_id"])
        if error is not None:
            return None, error
    specs = []
    if "category_id" in data:
        specs.append((Category, data["category_id"], "Category not found"))
    _, error = _resolve_owned_fks(specs)
    if error is not None:
        return None, error
    return target_period, None
