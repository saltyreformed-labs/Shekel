"""What a transaction door's press declares, and how its refusal is answered.

**Split out of :mod:`.mutations` at plan step ``credit_card:CC-5-4a-5``**,
whose popover wiring pushed that module past ``max-module-lines`` -- the
shape :mod:`._gates` took at X-au-j.  What it holds is the transaction
package's call of the ONE decision both route packages share
(:func:`app.routes._refused_press.answer_refused_press`): only what differs
here is spelled here -- the transaction popover's redraw
(:func:`~app.routes.transactions.forms.redraw_full_edit`) and the designed
error fragment of the cell or card the press targeted
(:func:`~app.routes.transactions._helpers._error_transaction_response`) --
and Mark Paid's declaration, the one door a companion presses
(:func:`_mark_paid_posted`).
"""

from functools import partial

from flask_login import current_user

from app.exceptions import NotFoundError
from app.routes._refused_press import answer_refused_press
from app.routes._shown_lines import Posted, read_posted
from app.services.match_press import MARK_PAID, OwnerOnly
from app.routes.transactions._helpers import _error_transaction_response
from app.routes.transactions.forms import redraw_full_edit


def _refused(txn_id, exc, posted: Posted, target=None):
    """Answer a press a service refused, on the surface the press came from.

    The transaction doors' call of the one decision
    (:func:`app.routes._refused_press.answer_refused_press`): a popover out
    of date is redrawn (ruling **R-CC128**), and every other refusal is the
    designed error fragment it always was -- the 404 for a "Paid from"
    account that is not the row owner's (plan step ``credit_card:CC-5-3``),
    else a 400.

    Args:
        txn_id: The row the press named.
        exc: What the service raised.
        posted: What the request said about its page.
        target: The
            :class:`~app.routes._mobile_card.RenderTarget`, or
            ``None`` for the desktop cell.

    Returns:
        A Flask response tuple.
    """
    return answer_refused_press(
        exc, posted,
        redraw=partial(redraw_full_edit, txn_id),
        refuse=lambda: _error_transaction_response(
            txn_id, str(exc), target,
            status=404 if isinstance(exc, NotFoundError) else 400,
        ),
    )


def _mark_paid_posted(txn, data):
    """Return what a Mark Paid request declares about the bank lines it frees.

    Three presses reach the door.  The popover's Paid / Received posts the
    lines its caption named; the OWNER's one-click -- the grid's cell and its
    phone card -- posts nothing and withdraws silently (ruling **R-CC56**);
    and a COMPANION's (ruling **R-CC11**: a companion settles the owner's
    row) may free no line at all, refused with its own sentence when it would
    (ruling **R-CC130**, developer 2026-10-04, "Companion refuses").  Who
    pressed is the request's, so it is read here; what a press that frees a
    line it may not means is the removal act's
    (:class:`~app.services.match_press.OwnerOnly`).

    **A companion's posted field is dropped, not read**: no companion surface
    renders it, so a request carrying one is crafted, and the owner's
    statement is never the companion's to name.

    Args:
        txn: The row pressed, on a request the door has already admitted.
        data: The schema-loaded payload; its ``shown_lines`` is taken out.

    Returns:
        The :class:`~app.routes._shown_lines.Posted`.
    """
    posted = read_posted(data, absent=MARK_PAID)
    if txn.user_id == current_user.id:
        return posted
    act = "received" if txn.is_income else "paid"
    return Posted(
        shown=OwnerOnly(
            refusal=(
                f"{txn.name} is matched to a line on the bank statement, so "
                f"only the account owner can mark it {act}."
            ),
        ),
        from_popover=False,
    )
