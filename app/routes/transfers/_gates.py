"""
Shekel Budget App -- Transfer route package: three of the edit door's refusals.

**Split out of :mod:`.mutations` at task plan#3699**, whose refusal of a typed
$0.00 would have pushed that module past ``max-module-lines``: the transfer twin
of ``routes/transactions/_gates``, cut at the same seam.  Each refusal here
reads the edit's schema-loaded payload and the loaded transfer, writes nothing
to either, and answers ``(xfer, data, ...) -> response | None``, ``None``
meaning *this one passes*, so :func:`.mutations.update_transfer` chains them
through one error exit and stays inside pylint's return-count limit.

**Each stands in a different relation to the rest of the app**, so each says
its own:

* :func:`_reject_a_typed_amount_of_nothing` is the FIRST refusal a typed $0.00
  meets: the popover lets one through, so the door's own words answer it.
* :func:`_reject_finalised_transfer_edit` is the finalised-row lock (#26), a
  rule of this door alone: no service, model or database holds it, so the
  recurrence and carry-forward paths are unaffected.
* :func:`_reject_generated_due_date_edit` is the crafted-request and stale-form
  BACKSTOP: the popover offers no date box where it refuses.

**The door's other refusals stay in :mod:`.mutations`**: the schema's 422, the
stale-form 409, the category's 404, and ``_grade_submitted_side_days``, which
shares the chain but RESOLVES each side's day into the payload the service
reads, where these only refuse.
"""

from flask.typing import ResponseReturnValue

from app.extensions import db
from app.models.ref import Status
from app.models.transfer import Transfer
from app.services.card_today import a_save_may_date
from app.services.state_machine import finalised_edit_rejection
from app.utils.error_fragments import flatten_schema_errors
from app.routes.transfers._helpers import _error_transfer_response

#: What a typed transfer amount of $0.00 is told, in the words the edit's
#: schema used while its exclusive minimum graded every figure the popover
#: posted (task plan#3699): ``Range``'s own sentence, under the field it names.
#: Ruling plan#3854's words for it ("To pay nothing this paycheck, cancel it")
#: are plan#3858's to give every single item's doors at once.
TYPED_AMOUNT_MUST_BE_ABOVE_ZERO = flatten_schema_errors(
    {"amount": ["Must be greater than 0."]},
)


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
    the period and rewritten by the maintain splat on every regeneration that
    reaches the row (a held one is routed away, and keeps its date) -- so an
    edit here never survived a later template save on a row carrying no hold.
    What X-au-f added is that the same date now resolves the transfer's PRICE
    through amount rule 3, so the field has gone from an edit that did not last
    to one that can leave a row no rule is able to price.

    **The transaction side MEASURED what that costs**, and the measurement is
    the reason this exists rather than a symmetry argument:
    ``routes/transactions/_gates`` records one dateless derived row taking out
    the grid, the dashboard and the companion on a production clone, because
    ``AmountUnresolvable`` has no handler on those paths.  A transfer reaches
    nine render sites through ``_render_helpers.render_transfer_cell`` with the
    same absence of handlers.

    **Three shapes get three answers, keyed on ``recurs`` and ``is_placed``
    since plan step ``balance:X-ci-1``** -- the twin's three arms.  It read
    ``Transfer.due_date_is_its_definitions``, a property that spelled
    ``transfer_template_id IS NOT NULL`` and lived on the row because the
    transfer had a SECOND edit door; leaf ``balance:X-bi-6-1`` deleted that
    door, and the link read as *generated* is what this leaf deletes
    (finding **BAL-493**'s family).  A transfer a RULE generated: the FIELD
    is refused, keyed on PRESENCE rather than emptiness exactly as its twin
    is, because moving the date re-prices the transfer against a different
    point in its definition's series, silently, and on a row carrying no hold
    the next regeneration puts it back.  **Except a standing transfer into a
    card a move re-dated** (ruling **plan#2991**), as its twin says.  A ONE-TIME
    transfer -- a rule-less definition's placed
    transfer, or a cleared cadence's survivor: the date is the OWNER's to
    state (ruling **R-BAL93**: due on its placed paycheck's start, or into a
    card today once that paycheck has started, ruling **plan#2965**, unless the
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
    date_box_open = xfer.recurs and a_save_may_date(
        xfer, data.get("pay_period_id", xfer.pay_period_id),
    )
    if xfer.recurs and not date_box_open:
        return _error_transfer_response(
            xfer.id,
            "This instance's due date comes from its recurring transfer, which "
            "is also what prices it. Change the due day on the recurring "
            "transfer to move every instance, or type an amount here to make "
            "this one's figure its own.",
        )
    if (xfer.is_placed or date_box_open) and data["due_date"] is None:
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


def _reject_a_typed_amount_of_nothing(
    xfer: Transfer, data: dict[str, object], *, amount_authored: bool,
) -> ResponseReturnValue | None:
    """Refuse a transfer amount a person TYPED that is not above $0.00.

    **Money that moves is never $0** (ruling plan#3854: "to skip one, cancel
    it"), and a figure the app works out may be $0.00: a card's worked-out
    payment when nothing is owed.  The popovers post the Amount box on every
    Save, so an untouched one ECHOES that $0.00; the schema's exclusive minimum
    graded the echo as a figure and refused the row's notes, its status and its
    untouched Save alike (finding plan#3028).  The bound is graded HERE, on the
    one answer to whether a person typed the figure
    (:func:`~app.routes._authored_figure.figure_was_authored`), which only the
    door has: an echo states nothing about the amount and is dropped
    unforwarded (``mutations._execute_transfer_update``).

    **What stands behind it differs by arm** (``mutations._execute_transfer_update``
    picks the arm).  A figure the pair would OWN is refused again by the
    service, ``transfer_service._amount.grade_amount_ownership`` through
    ``_validate_positive_amount``, with its own sentence and a 400.  A typed
    figure on a ONE-TIME transfer whose definition states a price restates that
    price instead (``definition_price``), and no app-tier refusal stands behind
    this one:
    ``ck_template_amount_versions_transfer_positive_amount`` refuses the write,
    and nothing on this path catches that, so the save would answer 500.  On
    that arm this is the check.  The schema still refuses a negative figure in
    either field.

    Args:
        xfer: The Transfer being edited, for the cell the refusal re-renders.
        data: The schema-loaded PATCH payload.
        amount_authored: Whether ``data["amount"]`` is a figure a person typed,
            the door's answer before it took the rendered companion out.

    Returns:
        A designed 422 response tuple, or ``None`` when the edit may proceed.
    """
    if not amount_authored or data["amount"] > 0:
        return None
    return _error_transfer_response(
        xfer.id, TYPED_AMOUNT_MUST_BE_ABOVE_ZERO, status=422,
    )
