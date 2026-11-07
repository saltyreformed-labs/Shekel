"""The PATCH handler's PRE-MUTATION GATE CHAIN.

**Split out of :mod:`.mutations` at plan step X-au-j**, whose CC-payback
refusal pushed that module past ``max-module-lines``.  The cut is the seam
``_apply_field_updates``'s own docstring already names: these run BEFORE the
``setattr`` loop dirties the session, so an illegal transition reports ahead of
a finalised-field lock or an FK error and the row is left untouched on
rejection.  They share one error exit, which is what keeps the handler inside
pylint's return-count limit as the arc adds refusals to it.

**It holds TWO of the chain's three guards, and says which and why rather
than claiming the set.**  The third, ``_helpers._finalised_edit_response``,
stays where it is: it was shared with ``._shadow_mutations`` until leaf
``balance:X-bi-6-1`` deleted that module, and moving it here now would be a
change with no caller asking for it.  A first draft of this docstring said
"in one place", which an adversarial review measured as false.

Every guard here answers the same shape -- ``(txn, data) -> response | None``,
where ``None`` means *this gate passes*.  A route-tier guard is the
crafted-request and stale-form BACKSTOP for a rule the popover already obeys by
not rendering the control; the rule itself lives in the service or the model.

**The STALE-FORM check joined at plan step ``credit_card:CC-5-4a-5``**
(:func:`_stale_form_conflict`), when the popover's posted bank lines pushed
``mutations`` past the line cap again.  It runs AHEAD of the chain -- in the
door, before ``_apply_regular_update`` -- and answers a 409 rather than a
400, but it is the same shape over the same payload and writes nothing, which
is this module's whole membership test.

Boundary discipline: these read the request's already-schema-loaded ``data`` and
the loaded row, and write nothing.
"""

import logging

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.services.card_today import a_save_may_date
from app.services.state_machine import verify_transition
from app.services.transaction_service import repays_card_spend

from app.routes._render_helpers import render_transaction_cell
from app.routes.transactions._helpers import _error_transaction_response

logger = logging.getLogger(__name__)


def _resolve_status_change(txn, data):
    """Validate a PATCH status transition early, before any column is mutated.

    Runs the status-dependent guards for a regular (non-shadow)
    :func:`update_transaction` before the ``setattr`` loop dirties the session:
    verifies the requested transition through the state machine (F-161 / C-21)
    and blocks the Credit status on purchase-tracking transactions (credit is
    per-entry, scope doc 5.2).  Doing it here gives the precise 400 precedence
    (an illegal transition reports before a finalised-field lock or an FK error)
    and leaves the row untouched on rejection.  ``settled_on`` is NOT decided here:
    the status seam (:func:`status_seam.apply_status_change`, invoked
    once the field is applied) owns the stamp/clear and re-runs this same
    verification as the single source of truth -- this early call exists purely
    for error precedence.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        ``None`` when the status change is allowed (or absent), or a Flask
        ``(msg, 400)`` response tuple the caller returns directly when a guard
        rejects the request.
    """
    if "status_id" not in data:
        return None

    # Verify the transition BEFORE any other status-dependent work.  An illegal
    # transition -- for example settled -> projected -- short-circuits the
    # request with a 400 and leaves the row untouched.  Audit reference: F-161 /
    # commit C-21 of the 2026-04-15 security remediation plan.
    try:
        verify_transition(txn, data["status_id"])
    except ValidationError as exc:
        return _error_transaction_response(txn.id, str(exc))

    # Block Credit status on entry-capable transactions -- credit
    # handling is per-entry, not per-transaction (scope doc section 5.2).
    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    if data["status_id"] == credit_id and txn.tracks_purchases:
        return _error_transaction_response(
            txn.id,
            "Cannot set Credit status on transactions with individual "
            "purchase tracking. Use entry-level credit instead.",
        )

    return None


def _reject_generated_due_date_edit(txn, data):
    """Refuse a due-date edit on a row a recurring definition generated.

    **A generated row's due date is its DEFINITION's** (plan step
    balance:X-au-e, developer 2026-09-03).  ``due_date`` is a member of
    ``recurrence_engine._amounts.DerivedRowFields``: generation computes it
    from the rule and the period, and the maintain splat rewrites it on every
    regeneration that reaches the row -- so an edit here never survived a later
    template save even before this step, on a row carrying no hold.  (A held row
    is routed away from that write, ``classify_maintain_work``, and keeps its date
    until "use" hands it back.)  What X-au-e added is that the same date now resolves the
    row's PRICE through amount rule 3, so the field has gone from an edit that
    did not last to one that can leave a row no rule is able to price.

    **Clearing it 500ed the whole grid**, and that is measured rather than
    argued: ``routes/grid/page`` prices every row it loads with no status
    predicate and no handler -- ``AmountUnresolvable`` has five handlers in
    ``app/`` and none of them is on that path -- so ONE dateless derived row
    takes out the grid, the dashboard and the companion until the row is
    repaired in the database.  Reproduced on a clone of production: the
    identical act leaves 926 rows pricing cleanly before the cutover and
    raising after it.

    The popover no longer renders the control for such a row (it shows the
    date as text and names where the edit belongs), so this is the
    crafted-request and stale-form backstop this module exists to be.

    **Keyed on PRESENCE rather than on emptiness**, for the reason the payback
    gate states one function down: what a generated row may not accept is the
    FIELD.  Moving the date is refused as well as clearing it -- a moved date
    re-prices the row against a different point in its definition's series,
    silently, and on a row carrying no hold the next regeneration puts it back.

    **The exception is a repeating row a move re-dated on a card** (ruling
    **plan#2991**): its date box opens, so a real charge date can be typed
    (:func:`~app.services.card_today.date_left_by_a_move`), and a date beside a
    move is the owner's too.  A row typed over and never moved stays refused until
    an edit of its rule's funding moves its paycheck out from under it, and the date
    may move but not clear, as a placed row's may not.  The price follows
    it, which the ruling names as a cost.

    **Which rows it refuses is keyed on ``Transaction.recurs`` since plan
    step ``balance:X-bi-7a``, not on the link**, and the three shapes get
    three answers.  A row a RULE generated: the FIELD is refused, above.  A
    LINK-LESS row (ad-hoc, a CC payback, a transfer shadow): untouched -- it
    owns its figure or is priced through its parent, so amount rule 1 or 5
    answers it and nothing reads its date; the form still offers it,
    clearable.  A RULE-LESS DEFINITION's row: the date is the OWNER's to
    state (ruling **R-BAL22** -- due on its placed paycheck's start, or on a
    card today once that paycheck has started, ruling **plan#2965**, unless
    the owner says otherwise), no cadence derives it and no regeneration puts
    it back, so MOVING it is allowed.  **The price follows the date**: the
    row is priced by its definition's series AS OF that day (amount rule 3),
    so a definition whose cadence the owner CLEARED and whose series still
    holds several versions re-prices a row moved across a version boundary --
    the amount model's own answer, stated rather than refused, and measured
    (``test_one_off_row_doors``); a one-off's series holds ONE version once
    the doors leaf mints it (**R-BAL21**), and then the move prices nothing
    differently.  CLEARING it is not allowed: the same rule 3 needs the day,
    which is what ``ck_transactions_template_row_needs_due_date`` (plan step
    X-bv-2) says in storage.  The CHECK would refuse the clear
    anyway; this arm exists so the owner reads *why* rather than the generic
    invalid-reference sentence a constraint hit renders, which is the
    screens-stating-what-is-false defect this arc keeps closing.  The popover
    renders the input ``required`` for such a row since leaf 7b-2 of
    ``X-bi-7b``, so this arm is the crafted-request and stale-form backstop
    every guard in this module is.  A MOVED date carries ``occurs_on`` with
    it at the field write (ruling **R-BAL25**), which is not this gate's to
    do.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if "due_date" not in data:
        return None
    date_box_open = txn.recurs and a_save_may_date(
        txn, data.get("pay_period_id", txn.pay_period_id),
    )
    if txn.recurs and not date_box_open:
        return _error_transaction_response(
            txn.id,
            "This instance's due date comes from its recurring transaction, "
            "which is also what prices it. Change the due day on the recurring "
            "transaction to move every instance, or type an amount here to make "
            "this month's figure its own.",
        )
    if (txn.is_placed or date_box_open) and data["due_date"] is None:
        return _error_transaction_response(
            txn.id,
            "This item's due date can be moved but not cleared: its price is "
            "resolved on that day.",
        )
    return None


def _reject_typed_payback_figure(txn, data):
    """Reject a hand-typed figure on a CC PAYBACK.

    **A payback's figure is not its own to state** (finding **N-252**): it
    repays the card spend of the row it names, so the figure is a fact about
    THAT row.  ``transaction_service.repays_card_spend``'s docstring carries
    the rule and what it cost -- ``$58.40`` on production payback 2590, edited
    to ``$123.18`` against ``$181.58`` of credit purchases and settled there,
    with no screen reporting the difference.

    Both render sites now withdraw the input (``budget_correctable`` on the
    full-edit popover and the inline quick-edit), so this is the
    crafted-request and stale-form BACKSTOP, which is what every guard in this
    module is.

    **It belongs HERE rather than beside the field writes, and an adversarial
    review moved it**: it reads ``credit_payback_for_id``, a stored column
    ``TransactionUpdateSchema`` cannot change, so nothing about it needs the
    post-loop row -- exactly the property ``_reject_tracking_on_income`` states
    for reading the stored type.  A first draft ran it after the ``setattr``
    loop and relied on ``_error_transaction_response``'s rollback to unstage
    the write; running it as a gate means the write is never staged at all.

    **Keyed on PRESENCE, not on the value.**  That reads stricter than the
    settled-actual refusal one module over (``data.get(...) is not None``) and
    resolves to the same set today: ``estimated_amount`` is not ``allow_none``,
    so ``_normalize_empty_inputs`` DROPS an empty submit instead of loading
    ``None``, and the key is present exactly when a figure was typed.  Written
    as a presence test anyway, because what this row may not accept is the
    FIELD -- a value test would start admitting ``None`` the day the schema
    gains ``allow_none``, and ``None`` on a source-less row is the state
    ``ck_transactions_amount_ownership`` forbids.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if "estimated_amount" not in data or not repays_card_spend(txn):
        return None
    return _error_transaction_response(
        txn.id,
        "This row repays what went on the card, so its figure has to stay "
        "equal to the card spend it repays. Change that instead -- a figure "
        "typed here would either be overwritten the next time the spend "
        "changes, or stay behind and stop matching the card.",
    )


def _reject_tracking_on_income(txn, data):
    """Reject enabling purchase tracking on an income row.

    Purchase tracking is expense-only.  The popover only renders the
    ``is_envelope`` checkbox for a PLACED expense row, so this is the crafted-
    request backstop -- the same layering every other route-tier guard here
    uses.  Checked against the STORED type because ``TransactionUpdateSchema``
    carries no ``transaction_type_id``, so a PATCH cannot change it.

    It is a function rather than an inline branch so it joins
    ``mutations._apply_regular_update``'s single pre-mutation gate chain: three
    guards sharing one error exit -- these two and
    ``_helpers._finalised_edit_response`` -- which is what keeps that handler
    inside pylint's return-count limit as the arc adds refusals to it.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.

    Returns:
        A designed 400 response tuple, or ``None`` when the edit may proceed.
    """
    if data.get("is_envelope") and txn.is_income:
        return _error_transaction_response(
            txn.id, "Purchase tracking is only available for expenses.",
        )
    return None


def _stale_form_conflict(txn, data):
    """Return the 409 conflict cell when the card that posted *data* is stale.

    The card pins the ROW's ``version_id`` (commit C-18 / F-010), and since
    plan step ``balance:X-bi-7b`` a PLACED row's card pins its DEFINITION's
    too: a one-off's name, category, flags and PRICE live on the definition
    and the card edits them there, so a save that touches only those bumps
    the definition's counter and not the row's -- and two cards rendered
    before either saved would both have answered 200, the second silently
    overwriting the first's price, where the row's own counter caught that
    race while the price lived on the row.  Found by adversarial review.
    Each pin is compared only when the card shipped it (a legacy row's card
    ships none for the definition; a client that omits both falls through to
    the SQLAlchemy-tier check at flush time), and both are POPPED so the
    field loop never sees them.

    Args:
        txn: The row being edited.
        data: The schema-loaded PATCH payload; ``version_id`` and
            ``template_version_id`` are removed from it.

    Returns:
        The conflict cell as a ``(html, 409)`` tuple, or ``None``.
    """
    submitted_version = data.pop("version_id", None)
    submitted_definition_version = data.pop("template_version_id", None)
    if submitted_version is not None and submitted_version != txn.version_id:
        logger.info(
            "Stale-form conflict on update_transaction id=%d "
            "(submitted=%d, current=%d)",
            txn.id, submitted_version, txn.version_id,
        )
        return render_transaction_cell(txn, conflict=True), 409
    if (
        submitted_definition_version is not None
        and txn.is_placed
        and submitted_definition_version != txn.template.version_id
    ):
        logger.info(
            "Stale-form conflict on update_transaction id=%d "
            "(definition submitted=%d, current=%d)",
            txn.id, submitted_definition_version, txn.template.version_id,
        )
        return render_transaction_cell(txn, conflict=True), 409
    return None
