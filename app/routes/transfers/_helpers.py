"""
Shekel Budget App -- Transfer route package: shared helpers.

The Marshmallow schema singletons and the private ownership / cell-render
helpers shared across the transfer route sub-modules.  Schema instances are
constructed once at import time so every handler reuses the same instance
(Marshmallow contract), preserving the pre-split monolith's behaviour.
"""

import logging
from dataclasses import dataclass

from flask import request
from flask_login import current_user

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.transfer import Transfer
from app.schemas.validation import (
    SAME_ACCOUNT_TRANSFER_MESSAGE,
    ShownLinesSchema,
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
    TransferCreateSchema,
    TransferUpdateSchema,
)
from app.routes._mobile_card import RenderTarget, render_mobile_card
from app.routes._render_helpers import (
    render_transaction_cell,
    render_transfer_cell,
)
from app.services.transfer_legs import leg_of
from app.utils.digit_strings import parse_row_id
from app.utils.error_fragments import designed_error

logger = logging.getLogger(__name__)

# Marshmallow schema instances (one per process; Marshmallow contract).
_create_schema = TransferTemplateCreateSchema()
_update_schema = TransferTemplateUpdateSchema()
_xfer_create_schema = TransferCreateSchema()
_xfer_update_schema = TransferUpdateSchema()
# Mark Paid's one parameter: the bank lines the popover's captions named (plan
# step ``credit_card:CC-5-4a-5``, ruling **R-CC127**); absent from the grid
# leg's one-click, which is silent by ruling **R-CC56**.
_shown_lines_schema = ShownLinesSchema()


def _user_owns(model, pk):
    """Return True iff the row at *pk* exists and belongs to ``current_user``.

    Used by :func:`create_ad_hoc`, :func:`update_transfer`,
    :func:`create_transfer_template`, and
    :func:`update_transfer_template` to enforce route-boundary FK
    ownership without scattering ``db.session.get`` + null/owner
    boilerplate across each endpoint.  The helper is intentionally
    minimal -- it returns a boolean so the caller controls the 404
    response shape (HTMX text vs flash + redirect for the form
    routes); centralising the response would force every consumer
    onto a single shape and lose the existing UX parity with
    surrounding code.

    All consulted models carry a direct ``user_id`` column, so the check is a
    single ``db.session.get`` followed by an equality compare.  **``PayPeriod``
    left that list at plan step ``pay_calendar:C13-b``**: a submitted paycheck
    is resolved against the owner's derived CALENDAR now, at
    ``transfer_service._ownership._get_owned_period``.  What is left here is
    ``Account``, ``Scenario`` and ``Category``, whose tables no derivation
    owns.

    *A first version of this paragraph said the calendar is asked "once per
    request", which is FALSE in two directions and an adversarial review
    measured both*: a non-repeating template create asks at the route AND at
    the service, and a recurring one asks once per GENERATED ROW.  That
    function's own docstring carries the measurement.  Following the project
    security response rule, callers
    surface ownership failures as 404 -- identical to the missing-PK
    case -- so the response leaks no information about whether the
    row exists for someone else.

    Args:
        model: SQLAlchemy model class with a ``user_id`` column.
        pk: Primary key value.  ``None`` is treated as "no row" and
            returns ``False`` so a caller that passes an optional FK
            stays safe.

    Returns:
        True if the row exists and ``row.user_id == current_user.id``;
        False otherwise.
    """
    if pk is None:
        return False
    record = db.session.get(model, pk)
    if record is None:
        return False
    return record.user_id == current_user.id


def _first_unowned_template_fk(data):
    """Return the label of the first submitted FK the user does not own, else None.

    Route-boundary FK ownership for the transfer-template update payload
    (commit C-27 / F-043), the :func:`_user_owns` loop
    ``transfers.templates.update_transfer_template`` runs.  **It lived in that
    route module until plan step R7d-f-4**, which needed the module's last
    lines for the step itself; a loop over :func:`_user_owns` belongs beside
    it in any case.  Each user-scoped FK is verified only when present
    in the partial-update ``data`` (the loaded dict carries only keys the user
    submitted -- BaseSchema's EXCLUDE meta drops stray form fields).
    ``category_id`` accepts ``None`` per the schema; ``None`` clears the
    category and skips the probe.

    Args:
        data: The loaded TransferTemplateUpdateSchema output (partial update).

    Returns:
        The human-readable label ("source account", "destination account" or
        "category") of the first FK that is present, non-``None``, and not
        owned by ``current_user``; ``None`` when every present FK is owned.
    """
    for field, model, label in (
        ("from_account_id", Account, "source account"),
        ("to_account_id", Account, "destination account"),
        ("category_id", Category, "category"),
    ):
        if field not in data:
            continue
        value = data[field]
        if value is None:
            continue
        if not _user_owns(model, value):
            return label
    return None


def _first_template_fk_refusal(template, data):
    """Return the flash for the first unusable account reference in *data*, else None.

    The two rules the transfer-template update route asks of its account
    keys, in the one order they can be asked: OWNERSHIP first
    (:func:`_first_unowned_template_fk`; an unowned id must be refused before
    anything reads it), then the pair the write would LEAVE -- source and
    destination, each the submitted value where present and the stored one
    where absent -- may not be one account.

    **The second rule grades the EFFECTIVE pair because the schema cannot**
    (plan step R7d-f-5).  ``_reject_same_account_transfer`` runs only when a
    submission carries BOTH keys, which a partial update need not -- and since
    R7d-f-5 a definition pinned to its loan (ruling **R-R76**) renders its
    destination control DISABLED, so its form posts no ``to_account_id`` at
    all.  Choosing the destination loan as the SOURCE on that form then passed
    the schema, reached ``ck_transfer_templates_different_accounts`` at the
    flush, and surfaced through ``flush_template_or_namedup_redirect`` as the
    name-collision flash: nothing written, and the owner told a reason that was
    false.  Found by this step's adversarial review; the same rule, spelled
    with the schema's own sentence, asked where the stored row is known.

    Args:
        template: The stored :class:`TransferTemplate`, owner-checked by the
            route; read for the two account columns an absent key leaves alone.
        data: The loaded TransferTemplateUpdateSchema output (partial update).

    Returns:
        ``"Invalid <label>."`` for the first unowned reference,
        :data:`SAME_ACCOUNT_TRANSFER_MESSAGE` when the effective pair is one
        account, else ``None``.
    """
    unowned = _first_unowned_template_fk(data)
    if unowned is not None:
        return f"Invalid {unowned}."
    source = data.get("from_account_id", template.from_account_id)
    destination = data.get("to_account_id", template.to_account_id)
    if source == destination:
        return SAME_ACCOUNT_TRANSFER_MESSAGE
    return None


def _get_owned_transfer(xfer_id):
    """Fetch a transfer and verify it belongs to the current user."""
    xfer = db.session.get(Transfer, xfer_id)
    if xfer is None:
        return None
    if xfer.user_id != current_user.id:
        return None
    return xfer


@dataclass(frozen=True)
class _LegRequest:
    """Which grid surface a transfer door's request came from, if any.

    A transfer door is reached from three places: the transfers page (no
    grid context), a transfer LEG's desktop grid cell, and a leg's mobile
    card (leaf ``X-bi-6-1``, ruling **R-BAL87**).  The form says which by
    ``leg_account_id`` -- the account the leg is on -- plus, for a card,
    the render-routing fields the card's one form posts for a row and a leg
    alike (``render=mobile_card``, ``card_prefix``, ``can_edit``).

    **It HOLDS the transaction routes' render target rather than re-reading
    its fields** (ruling **plan#1983**): until then this class re-read
    ``render`` and ``card_prefix`` off the form itself and the leg card
    hard-coded ``can_edit``, a second reading of one form beside
    :meth:`~app.routes._mobile_card.RenderTarget.from_form`.  What a leg
    adds to a row's request is its account, and that is all it states.

    Attributes:
        account_id: The leg's account, validated against the parent's
            endpoints.
        target: The surface the request asked for -- the leg's card or its
            cell -- read off the form by the one reader both packages call.
    """

    account_id: int
    target: RenderTarget


def _resolve_leg_request(xfer) -> "_LegRequest | None":
    """Return the leg a grid-origin request came from, or ``None``.

    When the transfer full-edit popover, the cell's Mark Paid or the card's
    action bar posts from a LEG's grid cell, the form carries
    ``leg_account_id`` so the handler renders the leg's own cell (or card)
    back -- ``grid/_transaction_cell.html`` drawing a
    :class:`~app.services.transfer_legs.TransferLeg`, targeted by
    ``leg_dom_id`` -- rather than ``_transfer_cell.html``, whose
    ``#xfer-cell-`` target exists only on the transfers page.  Until leaf
    ``X-bi-6-1`` the marker was ``source_txn_id``, the SHADOW row's id, and
    the response was that row's cell; the leg has no row, so the marker is
    the one fact that names it beside the transfer: its account.

    Validates that the account is one of the transfer's two endpoints.  A
    stale page whose transfer was re-pointed meanwhile can post an account
    that no longer is one; the request then falls back to the transfer
    cell, as a bad ``source_txn_id`` did -- the ``gridRefresh`` a status
    change carries redraws the grid from the parent regardless.

    Args:
        xfer: The Transfer object the door acted on.

    Returns:
        The :class:`_LegRequest`, or ``None`` when the request came from the
        transfer management page (no ``leg_account_id``).
    """
    # The shared rule, not ``type=int`` (plan step X-ae): Werkzeug catches the
    # ValueError so this never crashed, but the coercion is ``int()``, which
    # read an id spelled in any digit script, padded, or signed.
    account_id = parse_row_id(request.form.get("leg_account_id"))
    if account_id is None:
        return None
    if account_id not in (xfer.from_account_id, xfer.to_account_id):
        logger.warning(
            "leg_account_id=%d is neither endpoint of transfer %d "
            "(from %d to %d); falling back to transfer cell response.",
            account_id, xfer.id, xfer.from_account_id, xfer.to_account_id,
        )
        return None
    return _LegRequest(account_id=account_id, target=RenderTarget.from_form())


def _render_leg_surface(xfer, leg: _LegRequest, **extra):
    """Render the leg's cell or card, whichever the request came from.

    The leg is built here (:func:`~app.services.transfer_legs.leg_of`)
    rather than kept from before the act, because the act may have just
    moved it (a settle does).  Its record is not loaded: neither renderer
    reads one (ruling **plan#2136**; it was pre-loaded until then, a
    statement per fragment that nothing read).  It is drawn by the renderers a row's door draws
    a row with (ruling **plan#1983**):
    :func:`~app.routes._mobile_card.render_mobile_card` for the card, which
    takes only the refusal of *extra*, as a row's card does, and
    :func:`~app.routes._render_helpers.render_transaction_cell` for the
    cell.  :func:`_resolve_leg_request` has already confirmed the account is
    one of the transfer's endpoints, so the build does not refuse it.

    Args:
        xfer: The transfer the door acted on, owner-established.
        leg: The request's :class:`_LegRequest`.
        **extra: Forwarded to the cell (``conflict=True``,
            ``error=<message>``); the card reads ``error`` alone.

    Returns:
        Rendered HTML string.
    """
    side = leg_of(xfer, leg.account_id)
    if leg.target.render_mode == "mobile_card":
        return render_mobile_card(
            side, card_prefix=leg.target.card_prefix,
            can_edit=leg.target.can_edit, error=extra.get("error"),
        )
    return render_transaction_cell(side, **extra)


def _stale_transfer_response(xfer_id):
    """Roll back the session and render the transfer cell in conflict mode.

    Used by every transfer-mutating HTMX route to convert a stale
    form or ``StaleDataError`` flush failure into a coherent UI
    response instead of a 500.  Re-fetches the transfer so the
    user's view shows the winner's state (never the loser's stale
    in-memory copy) and tags the cell with ``conflict=True``.
    Falls back to a 404 string when the row was hard-deleted by
    the winning request.

    Note: caller adds the 409 status code -- this helper returns
    only the rendered HTML so it can be reused both before and
    after the database commit.

    Args:
        xfer_id: Primary key of the transfer the route was trying
            to mutate.

    Returns:
        Rendered HTML string, or the literal string ``"Not found"``
        when the row no longer exists.
    """
    db.session.rollback()
    db.session.expire_all()
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found"

    # Render the leg's cell or card when the request came from the grid
    # (``leg_account_id`` present and validated), or the transfer cell
    # otherwise.  Mirrors the leg-context handling in
    # :func:`update_transfer`.
    leg = _resolve_leg_request(xfer)
    if leg is not None:
        return _render_leg_surface(xfer, leg, conflict=True)

    return render_transfer_cell(xfer, conflict=True)


def _error_transfer_response(xfer_id, message, status=400):
    """Roll back and render the request's surface as a designed error.

    The rejected-mutation twin of :func:`_stale_transfer_response`
    (the marker-header convention, closeout plan session 4): every
    transfer-mutation 400/422 a user can reach re-renders the surface
    the request targeted -- the leg's grid cell or mobile card when
    ``leg_account_id`` marks a grid-origin request, the transfer cell
    otherwise -- with CURRENT data plus the rejection message, and
    stamps the designed-fragment header so the body swaps instead of
    being silently dropped by the app-wide htmx config.

    Args:
        xfer_id: Primary key of the transfer the route was trying to
            mutate.  Re-fetched after the rollback so the fragment
            shows committed state.
        message: The user-facing rejection message (the cell's
            title/aria hint).
        status: The HTTP error status (400 domain rejection, 422
            validation failure).

    Returns:
        A designed-fragment Flask response tuple, or
        ``("Not found", 404)`` when the row vanished.
    """
    db.session.rollback()
    db.session.expire_all()
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404

    leg = _resolve_leg_request(xfer)
    if leg is not None:
        return designed_error(
            _render_leg_surface(xfer, leg, error=message), status,
        )

    return designed_error(render_transfer_cell(xfer, error=message), status)


def _render_post_mutation_cell(xfer, *, leg_trigger, cell_trigger):
    """Render the grid cell for a transfer after a successful mutation.

    The transfer-mutating HTMX routes (:func:`update_transfer`,
    :func:`mark_done`, :func:`cancel_transfer`) share one response shape: when
    the request originated from a transfer LEG's grid cell or mobile card
    (``leg_account_id`` present and validated), the leg's
    ``grid/_transaction_cell.html`` -- or its card -- is re-rendered so the
    surface stays interactive; otherwise the transfer's own
    ``_transfer_cell.html`` is returned.  The two paths can carry different
    HX-Trigger events (a status change refreshes the grid but renders the
    transfer cell with a balance recompute), so each trigger is supplied
    explicitly; a card's Mark Paid carries ``mobileCardSettled`` in place of
    the leg trigger, the event the transaction twin emits, so the This Period
    summary's self-refresh blocks on it and the page is not reloaded under
    the swapped card.

    Args:
        xfer: The mutated Transfer.
        leg_trigger: HX-Trigger event for the leg-cell response.
        cell_trigger: HX-Trigger event for the transfer-cell response.

    Returns:
        A Flask response tuple ``(html, 200, {"HX-Trigger": ...})``.
    """
    leg = _resolve_leg_request(xfer)
    if leg is not None:
        db.session.refresh(xfer)
        response = _render_leg_surface(xfer, leg)
        trigger = (
            "mobileCardSettled" if leg.target.render_mode == "mobile_card"
            else leg_trigger
        )
        return response, 200, {"HX-Trigger": trigger}

    response = render_transfer_cell(xfer)
    return response, 200, {"HX-Trigger": cell_trigger}
