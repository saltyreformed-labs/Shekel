"""
Shekel Budget App -- Transfer route package: grid-cell GET partials.

Read-only HTMX partials that return a transfer's grid cell in display,
quick-edit, or full-edit mode.  Every URL and endpoint name is preserved
verbatim from the pre-split ``app/routes/transfers.py``.
"""

from flask import render_template, request
from flask_login import current_user

from app.extensions import db
from app.models.ref import Status
from app.services import (
    category_service,
    match_withdrawal,
    pay_period_service,
    transfer_legs,
)
from app.services.pay_calendar import calendar_for
from app.services.state_machine import allowed_transitions
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today
from app.routes._period_options import period_move_options
from app.routes._refused_press import RedrawnCard
from app.routes._render_helpers import (
    render_transfer_cell,
    transfer_budgets,
    transfer_settlement_amounts,
    transfer_side_boxes,
)
from app.routes.transfers._bp import transfers_bp
from app.routes.transfers._helpers import _get_owned_transfer
from app.utils.digit_strings import parse_row_id


@transfers_bp.route("/transfers/cell/<int:xfer_id>", methods=["GET"])
@require_owner
def get_cell(xfer_id):
    """HTMX partial: return the display-mode cell for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_transfer_cell(xfer)


@transfers_bp.route("/transfers/quick-edit/<int:xfer_id>", methods=["GET"])
@require_owner
def get_quick_edit(xfer_id):
    """HTMX partial: return the inline amount edit form for a transfer."""
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None:
        return "Not found", 404
    return render_template(
        "transfers/_transfer_quick_edit.html",
        xfer=xfer, budgets=transfer_budgets(xfer),
    )


@transfers_bp.route("/transfers/<int:xfer_id>/full-edit", methods=["GET"])
@require_owner
def get_full_edit(xfer_id):
    """HTMX partial: return the full edit popover form for a transfer.

    **Reached from two surfaces, and the form says which** (leaf
    ``X-bi-6-1``, ruling **R-BAL87**): the transfers page, and a transfer
    LEG's grid cell, which asks with ``?leg_account_id=<the account the leg
    is on>`` so the popover's form and quick buttons target that cell and
    post the id back for the leg's re-render.  Until this leaf the grid
    reached the same popover through ``transactions.get_full_edit`` on the
    SHADOW row, which returned this template with ``source_txn_id``; the leg
    has no row, so the cell asks the transfer's own door.  Whether the card
    may be drawn at all is :func:`_drawable_card`'s, which its redraw asks too.
    """
    card = _drawable_card(
        xfer_id, parse_row_id(request.args.get("leg_account_id")),
    )
    if card is None:
        return "Not found", 404
    return render_full_edit(*card, page_refusal=None)


def _drawable_card(xfer_id, leg_account_id):
    """Return the transfer and leg a full-edit card is drawn for, or ``None``.

    **ONE rule for the popover's GET and its REDRAW** (ruling **R-CC128**;
    review finding L8: the redraw fell back to the transfers page's
    ``#xfer-cell-`` target, absent on the grid, for the stale leg the GET
    refuses).  Three states have no card:

    * a transfer that is not the requester's, or missing -- one "not found";
    * a SOFT-DELETED transfer, which has no edit surface: "not found" per
      the project security response rule, a deleted row being invisible to
      normal operations (``transfer_service._validation._get_transfer_or_raise``).
      The refusal dates from plan step X-au-c3, when the card's figures came
      through a pair loader that refused a deleted parent and the request
      500'd.  The OTHER transfer routes still admit a deleted parent --
      notably the idempotent DELETE, which needs to -- so the refusal is
      scoped to the edit doors rather than pushed into ``_get_owned_transfer``;
    * a ``leg_account_id`` that is neither endpoint, which names a leg that
      does not exist (a stale page whose transfer was re-pointed meanwhile).

    Args:
        xfer_id: The transfer the request named.
        leg_account_id: The account of the grid leg the card is for, as the
            request carried it, or ``None`` for the transfers page.

    Returns:
        ``(transfer, leg_account_id)`` -- :func:`render_full_edit`'s first two
        arguments -- or ``None`` when there is no card to draw.
    """
    xfer = _get_owned_transfer(xfer_id)
    if xfer is None or xfer.is_deleted:
        return None
    if leg_account_id is not None and leg_account_id not in (
        xfer.from_account_id, xfer.to_account_id,
    ):
        return None
    return xfer, leg_account_id


def render_full_edit(xfer, leg_account_id, *, page_refusal):
    """Render the transfer full-edit popover from the pair's current state.

    **ONE render for the popover's GET and for its REDRAW** (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC128**, developer 2026-10-04,
    "Redraw all"): a Save or Paid the removal act refuses as out of date
    answers with this same card, drawn from what is true now, the refusal
    above it -- the transaction popover's
    (:func:`app.routes.transactions.forms.render_full_edit`) twin over the
    one decision both share (:mod:`app.routes._refused_press`).

    Args:
        xfer: The live transfer, owner-established by the caller.
        leg_account_id: The account of the grid leg the popover was opened
            from, validated as one of the transfer's endpoints, or ``None``
            for the transfers page.
        page_refusal: :attr:`~app.exceptions.PageOutOfDate.facts` for a
            redraw, else ``None``.

    Returns:
        The rendered ``transfers/_transfer_full_edit.html``.
    """
    statuses = db.session.query(Status).all()
    categories = category_service.list_active_categories(current_user.id)
    # Current + future periods power the in-popover period-move selector,
    # always including the transfer's own period so a transfer sitting in
    # a past period stays selected.  The service re-validates ownership of
    # the submitted id and moves the transfer; both legs read its period.
    periods = period_move_options(
        calendar_for(current_user.id), xfer.pay_period_id,
    )
    # The sides' records, loaded ONCE for both of their readers below -- the
    # two day boxes and the withdrawal caption (ledger row **BAL-530**: the
    # figures and the caption each loaded them) -- into the transfer's own
    # lists, which each side's record is read off (ruling **plan#1984**).
    # The figures read none since plan step ``balance:X-bi-6-4d-2``: both are
    # the transfer's.
    transfer_legs.preload_side_records((xfer,))
    # What the pair RECORDED and what a re-settle would RE-BOOK (plan step
    # X-au-c3), through the one helper, so the popover opened from the
    # transfers page and the one opened from a grid leg's cell cannot show
    # different figures for the same transfer.
    amounts = transfer_settlement_amounts(xfer, current_user.id)
    return render_template(
        "transfers/_transfer_full_edit.html",
        card_id=full_edit_dom_id(xfer.id),
        page_refusal=page_refusal,
        xfer=xfer, statuses=statuses, categories=categories, periods=periods,
        leg_account_id=leg_account_id,
        budgets=transfer_budgets(xfer),
        settled=amounts.settled, retained=amounts.retained,
        # One date box per side (ruling **R-BAL108**), each prefilled with
        # its side's OWN day or left empty for a side borrowing the other's
        # (ruling **R-BAL164**) -- the same producer the PATCH grades by.
        side_boxes=transfer_side_boxes(xfer),
        # The settle-day corrections' bounds -- ``max`` from ruling R-EJ,
        # ``min`` from ruling R-EL.  The USER's today via ``display_today()``,
        # never the process's UTC day: the input must not refuse a day
        # ``status_seam.reject_future_settle_day`` accepts.  The floor calls the
        # SAME function ``reject_settle_day_before_the_schedule`` refuses below,
        # so the browser bound and the server bound cannot drift.
        today=display_today(),
        settle_day_min=pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
        # Pre-hint (grid audit D2): the status dropdown disables
        # transitions the state machine would reject.
        allowed_status_ids=allowed_transitions(xfer),
        # **What a $0.00 Actual would WITHDRAW** (plan step
        # ``credit_card:CC-5-4a-3``, ruling **R-CC59**): the caption under
        # the Actual box, read through the same twin the seam's write uses.
        payment_withdraws=_payment_withdrawal(xfer),
    )


def full_edit_dom_id(xfer_id):
    """Return the ``id`` of a transfer's full-edit card -- what a redraw replaces.

    Args:
        xfer_id: The transfer's id.

    Returns:
        The DOM id, spelled once for the card's root and its redraw.
    """
    return f"xfer-full-edit-{xfer_id}"


def redraw_full_edit(xfer_id, facts):
    """Re-read a transfer after a refused press and draw its card again (ruling **R-CC128**).

    The transfer popover's half of
    :func:`app.routes._refused_press.answer_refused_press`, which has rolled
    the press back before calling this.  The popover posts its leg's account
    with every press (``leg_account_id``), so the card is redrawn for the
    same surface it was opened from -- or not at all, by the GET's own rule
    (:func:`_drawable_card`).

    Args:
        xfer_id: The transfer the press named.
        facts: :attr:`~app.exceptions.PageOutOfDate.facts`, drawn above the
            card.

    Returns:
        The :class:`~app.routes._refused_press.RedrawnCard`, or ``None`` where
        :func:`get_full_edit` answers "not found".
    """
    card = _drawable_card(
        xfer_id, parse_row_id(request.form.get("leg_account_id")),
    )
    if card is None:
        return None
    return RedrawnCard(
        full_edit_dom_id(xfer_id), render_full_edit(*card, page_refusal=facts),
    )


def _payment_withdrawal(xfer):
    """Return what taking BOTH sides' payments out of their matches would withdraw.

    The read twin of the seam's write on a transfer's ``$0.00`` figure: a
    close of nothing keeps no record on either side, so the seam takes every
    record the two sides hold off the books in ONE call of
    ``movement_removal.remove_movements`` (``status_seam._side``, ruling
    **R-CC54**).  An act names ONE side's record -- a member is held to its
    movement's account, and the sides are on two -- so the two sides' acts
    are disjoint and one read over both records is what that call
    withdraws.  Each side's record is read off its leg as the seam's write
    reads it (``status_seam._side._withdraw_sides``), from the popover's ONE
    load of them (``transfer_legs.preload_side_records``), which its day
    boxes read too (ledger row **BAL-530**, closed at plan step
    ``balance:X-bi-6-4c-3``: this read made a second call of that join).

    Args:
        xfer: The transfer, its sides' records pre-loaded.

    Returns:
        A :class:`~app.services.match_withdrawal.MatchWithdrawal`, or
        ``None`` when neither side holds a payment.
    """
    movements = [
        leg.record
        for leg in (
            transfer_legs.transfer_side_leg(xfer, is_income=False),
            transfer_legs.transfer_side_leg(xfer, is_income=True),
        )
        if leg.record is not None
    ]
    if not movements:
        return None
    return match_withdrawal.pending_for_movements(movements)
