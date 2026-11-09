"""
Shekel Budget App -- Transaction route package: create handlers.

The POST routes that create a ONE-OFF: the inline grid-cell create and the
Add Transaction modal's full create.  Both verify every user-scoped FK
through the shared :func:`_resolve_owned_fks` IDOR probe, then hand what
the form said to the one producer of a one-off
(:func:`app.services.one_off.place_one_off`, plan step ``balance:X-bi-7b``):
a rule-less DEFINITION carrying the name, category, flags and price, plus
the one row it places in the submitted paycheck.  Neither route constructs a
``Transaction`` any more -- a bare ``Transaction(**data)`` was how a
link-less row came to carry its own flags and figure (ruling **R-BAL20**).
"""

import logging

from flask import request, jsonify
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.one_off import OneOffToPlace, place_one_off
from app.utils.auth_helpers import require_owner
from app.routes._render_helpers import render_transaction_cell
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _create_schema,
    _inline_create_schema,
    _resolve_owned_fks,
    _resolve_owned_period,
)

logger = logging.getLogger(__name__)

# The refusal body for a raw transaction typed onto an amortizing loan
# account (ruling D4 / finding N-11): a loan's balance is ledger-derived,
# not a transaction sum.  A payment is a transfer the app splits into
# interest / escrow / principal; a balance correction is a true-up on the
# loan's own page.
_LOAN_TRANSACTION_REFUSAL = (
    "A loan's balance is not a transaction sum. Record a payment as a "
    "transfer, or a balance correction as a true-up on the loan's page."
)


def _reject_transaction_on_loan(account: Account) -> tuple[str, int] | None:
    """Refuse a raw transaction typed onto an amortizing loan account.

    A loan's balance is ledger-derived, not a transaction sum (ruling D4).
    A raw transaction posted onto a loan account books a bare cash leg onto
    the loan's linked ledger that the sum-of-postings reader counts as a
    real paydown while the loan fold cannot see it -- finding N-11, the one
    balance shape where the two producers diverge with nothing to reconcile
    them.  So it is forbidden at the transaction-create chokepoint, exactly
    as a transfer OUT of a loan is forbidden at the transfer-create
    chokepoint
    (:func:`app.services.transfer_service._validation._reject_transfer_out_of_loan`,
    review R6).  The grid picker already refuses a loan account (step A1);
    this closes the ad-hoc and inline create endpoints the picker does not
    gate.

    Args:
        account: The resolved, ownership-checked destination
            :class:`~app.models.account.Account` for the new transaction.

    Returns:
        A ready-to-return ``(message, 422)`` Flask response tuple when
        *account* is an amortizing loan, else ``None``.
    """
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return _LOAN_TRANSACTION_REFUSAL, 422
    return None


def _place_submitted_one_off(data, period):
    """Mint the one-off the validated form *data* describes, in *period*.

    The one body both create doors share once their IDOR probes have run:
    what the form said about the plan item goes to the DEFINITION through
    :class:`~app.services.one_off.OneOffToPlace`, and what it said about the
    row -- the paycheck, the scenario, a due date if the form offered one, a
    note -- is placed beside it.  The owner is the SESSION's, exactly as the
    routes assigned ``user_id`` before this step: it is not the submitter's
    to state, and ``_resolve_owned_fks`` has already proved the account, the
    category and the scenario are ``current_user``'s (the paycheck through
    the owner's derived calendar), so this is the value all of them carry --
    were it not, the two composite keys would refuse the INSERT.

    **Born Projected** (the producer assigns it; ``status_id`` is not a
    schema field on either create schema, so a submitted value was already
    dropped): the sole path to a settled status is the status seam.  **Born
    priced by its definition** (**R-BAL21**): the submitted figure opens the
    definition's series with one version dated on the row's due date, and
    the row states no figure of its own -- where the routes used to write
    ``AmountOwnership.own(...)`` onto the row.  The flags the form posts are
    the definition's, not the row's (**R-BAL20**).

    Args:
        data: The schema-loaded POST payload.  ``name`` is present -- the
            inline door has already defaulted it -- and ``estimated_amount``
            is required on both schemas.
        period: The submitted paycheck, as ``_resolve_owned_period`` derived
            it from the owner's calendar.

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`.
    """
    txn = place_one_off(
        OneOffToPlace(
            user_id=current_user.id,
            account_id=data["account_id"],
            transaction_type_id=data["transaction_type_id"],
            name=data["name"],
            amount=data["estimated_amount"],
            category_id=data["category_id"],
            is_envelope=data["is_envelope"],
            companion_visible=data["companion_visible"],
        ),
        period,
        scenario_id=data["scenario_id"],
        due_date=data.get("due_date"),
    )
    # A note is the ROW's -- the popover edits it there -- and the one field
    # the producer does not state.
    txn.notes = data.get("notes")
    return txn


@transactions_bp.route("/transactions/inline", methods=["POST"])
@require_owner
def create_inline():
    """Create a transaction from inline grid interaction.

    The quick-create form's optional name field wins when provided
    (grid audit A5: ad-hoc rows are nameable at the Tier-1 entry
    point); otherwise the name is auto-derived from the category.
    Returns the new transaction cell wrapped in a div with a unique ID
    for HTMX targeting.

    Double-submit handling (F-102 / C-22): unlike the ad-hoc
    transfer create path (F-050), no database-level uniqueness
    constraint is enforced here.  Two transactions with identical
    (account_id, category_id, amount, pay_period_id) are a
    legitimate use case -- two $4 coffees on the same day, two
    identical fast-food charges, the user genuinely buying the
    same thing twice -- and rejecting them at the database layer
    would force the user to artificially differentiate amounts
    that match real-world receipts.  The mitigation is the
    client-side ``hx-disabled-elt`` HTMX directive on every
    transaction-create form (``_transaction_quick_create.html``,
    ``_transaction_full_create.html``,
    ``grid.html#addTransactionModal``): the submit control is
    disabled while the request is in flight, preventing accidental
    re-submits from a double-click or network retry.  The residual
    risk -- a user clicks rapidly enough to bypass the disable
    state, or replays the request via the back button -- is
    accepted as operator UX rather than a financial-correctness
    concern.
    """
    errors = _inline_create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 422

    data = _inline_create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write.  Order matches the historical per-FK checks so the first
    # invalid id returns the same 404 body as before; the resolved
    # Category drives the derived transaction name below.
    #
    # **The pay period is NOT one of these specs** since plan step
    # ``pay_calendar:C13-b``: it goes to :func:`._helpers._resolve_owned_period`
    # below, which asks the owner's derived CALENDAR.  Ordered AFTER the three
    # table-backed probes, the way ``_resolve_grid_cell`` orders its two, so a
    # request naming a foreign account, category or scenario is refused before
    # a derivation runs.  The only message order this moves is the period
    # against the SCENARIO -- account and category already preceded it -- and a
    # payload with one bad id reads exactly as before.
    objs, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (Category, data["category_id"], "Category not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err
    period, err = _resolve_owned_period(data["pay_period_id"])
    if err is not None:
        return err
    loan_refusal = _reject_transaction_on_loan(objs[Account])
    if loan_refusal is not None:
        return loan_refusal
    category = objs[Category]

    # A typed name wins; an omitted or blank one (the pre_load hook
    # drops empty submits) falls back to the category display name.
    data.setdefault("name", category.display_name)

    # **INSIDE the net, because the producer FLUSHES**: a foreign-key refusal
    # surfaces at the definition's or the row's flush, not at the commit, and
    # both must render the same designed 400.
    try:
        txn = _place_submitted_one_off(data, period)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "user_id=%d created inline transaction: %s (id=%d)",
        current_user.id, txn.name, txn.id,
    )

    # Return the cell wrapped in a div with a unique ID, matching
    # the pattern used in grid.html for existing transactions.
    response = render_transaction_cell(txn, wrap_div=True)
    return response, 201, {"HX-Trigger": "balanceChanged"}


@transactions_bp.route("/transactions", methods=["POST"])
@require_owner
def create_transaction():
    """Create a one-off from the Add Transaction modal.

    The full create: a name, a category and a paycheck the owner picks
    rather than the grid cell's.  It mints the same shape the inline door
    does -- a rule-less definition plus one placed row -- through the same
    producer (:func:`_place_submitted_one_off`); the two differ only in what
    the form offers.
    """
    errors = _create_schema.validate(request.form)
    if errors:
        return jsonify(errors=errors), 422

    data = _create_schema.load(request.form)

    # Verify every user-scoped FK belongs to the current user before any
    # write (same IDOR probe as create_inline).  ``category_id`` is a
    # required field on TransactionCreateSchema and lands on the DEFINITION
    # the producer mints, so it must be ownership-checked here too: a foreign
    # category_id otherwise satisfies the FK constraint (the row exists) and
    # links another user's category onto this owner's plan item.  The
    # resolved Account is checked for the loan-kind refusal below.
    #
    # **The pay period is NOT one of these specs** since plan step
    # ``pay_calendar:C13-b``: it goes to :func:`._helpers._resolve_owned_period`
    # below, which asks the owner's derived CALENDAR.  Ordered AFTER the three
    # table-backed probes, the way ``_resolve_grid_cell`` orders its two, so a
    # request naming a foreign account, category or scenario is refused before
    # a derivation runs.  The only message order this moves is the period
    # against the SCENARIO -- account and category already preceded it -- and a
    # payload with one bad id reads exactly as before.
    objs, err = _resolve_owned_fks([
        (Account, data["account_id"], "Not found"),
        (Category, data["category_id"], "Category not found"),
        (Scenario, data["scenario_id"], "Not found"),
    ])
    if err is not None:
        return err
    period, err = _resolve_owned_period(data["pay_period_id"])
    if err is not None:
        return err
    loan_refusal = _reject_transaction_on_loan(objs[Account])
    if loan_refusal is not None:
        return loan_refusal

    # Inside the net for the reason create_inline gives: the producer flushes.
    try:
        txn = _place_submitted_one_off(data, period)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "Invalid reference. Check that all referenced records exist.", 400
    logger.info(
        "user_id=%d created ad-hoc transaction: %s (id=%d)",
        current_user.id, txn.name, txn.id,
    )

    response = render_transaction_cell(txn)
    return response, 201, {"HX-Trigger": "balanceChanged"}
