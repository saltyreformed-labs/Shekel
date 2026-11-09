"""
Shekel Budget App -- Transaction route package: the two Credit doors.

Credit puts a row on the credit card (``transaction_service.mark_as_credit``,
plan task plan#2929) and Undo CC takes it off again
(``transaction_service.undo_credit``, plan task plan#3055).  **Moved out of
:mod:`.mutations` at plan task plan#3056**, a pure move, because the press's
own day and card would take that module (997 lines) past this project's
1,000-line bound (ruling **balance:R-IR**: the session that breaks a module
splits it).  The two doors
are one concern -- the press and its undo, each posting what its caption
named about the bank lines it frees -- and share nothing with the PATCH save
or Mark Paid beyond the package helpers they already import.
"""

import logging

from flask import request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.routes._render_helpers import render_transaction_cell
from app.routes._shown_lines import read_posted
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _INVALID_REFERENCE_MSG,
    _credit_press_schema,
    _error_transaction_response,
    _get_owned_transaction,
    _stale_transaction_response,
    _undo_credit_schema,
)
from app.routes.transactions._press import _refused
from app.services import transaction_service
from app.services.match_press import NOTHING_SHOWN, Press
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import flatten_schema_errors

logger = logging.getLogger(__name__)


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@require_owner
def mark_credit(txn_id):
    """Put a row on the credit card: settle it with its money moved on the card.

    The HTTP shape of ``transaction_service.mark_as_credit`` (plan task
    plan#2929, rulings plan#2887 / plan#2888), which writes no CC Payback row,
    so the payback's unique-index backstop (commit C-19) has nothing left to
    catch here.  Putting a reverted row's kept payment on the card takes it
    out of a match naming it, and a press naming no line frees none (ruling
    **R-CC127**; only Mark Paid's one-click is silent, **R-CC56**).  The door
    is the owner's, so a companion's press is a 404.
    Optimistic locking (commit C-18 / F-010): ``StaleDataError`` -> 409.

    **The popover's press posts its day and its card** (plan task plan#3056):
    the day under its "Money moved on" box, as shown or as typed, and the
    card picked when the owner has two or more; the C key and the palette
    post neither, and the verb then reads the row's own day and its one card.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    errors = _credit_press_schema.validate(request.form)
    if errors:
        return _error_transaction_response(
            txn_id, flatten_schema_errors(errors), status=422,
        )
    press_data = _credit_press_schema.load(request.form)
    posted = read_posted(press_data, absent=NOTHING_SHOWN)
    try:
        with Press(posted.shown) as one_save:
            transaction_service.mark_as_credit(
                txn,
                card_account_id=press_data.get("card_account_id"),
                day=press_data.get("settled_on"),
                press=one_save,
            )
        db.session.commit()
    except StaleDataError:
        logger.info("Stale-data conflict on mark_credit id=%d", txn_id)
        return _stale_transaction_response(txn_id)
    except IntegrityError:
        return _error_transaction_response(txn_id, _INVALID_REFERENCE_MSG)
    except (NotFoundError, ValidationError) as exc:
        return _refused(txn_id, exc, posted)
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@require_owner
def unmark_credit(txn_id):
    """Undo CC: off the card (plan#3055), or an old Credit row's payback deleted.

    ``transaction_service.undo_credit``.  Optimistic locking: see
    :func:`mark_credit`.  The button posts the bank lines its caption named
    (plan step ``credit_card:CC-5-4a-5``, rulings **R-CC80** / **R-CC127**),
    and a card out of date is redrawn (**R-CC128**).
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    errors = _undo_credit_schema.validate(request.args)
    if errors:
        return _error_transaction_response(
            txn_id, flatten_schema_errors(errors), status=422,
        )
    posted = read_posted(
        _undo_credit_schema.load(request.args), absent=NOTHING_SHOWN,
    )

    try:
        with Press(posted.shown) as one_save:
            transaction_service.undo_credit(txn, press=one_save)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on unmark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Raised when the bespoke source-state guard or the
        # state-machine verification in ``undo_credit`` rejects the
        # request -- e.g. a Paid row on checking.  The fragment names
        # the offending status so the user understands why; a card out of
        # date is redrawn instead (ruling **R-CC128**).
        return _refused(txn_id, exc, posted)
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}
