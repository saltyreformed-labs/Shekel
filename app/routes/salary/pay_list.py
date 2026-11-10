"""
Shekel Budget App -- Salary route package: the pay list's doors.

What one paycheck pays, from a dated payday on, is the salary's stored fact
(rulings **R-SAL59** and **R-SAL61**), and the salary page's Pay card writes
it through these doors: Record a pay change and Remove (plan step
salary:X-av-3b, ruling **R-SAL68** for the second) and Fix (plan step
salary:X-av-3a, moved here from :mod:`.profiles` at X-av-3b, its URL and
endpoint name unchanged).  The writes are
:mod:`app.services.pay_list_service`'s; each door here is the Flask half:
ownership, the form, the one read pass, the regeneration of the paychecks
the write moves, and the words of each refusal.
"""

import logging

from flask import Response, abort, flash
from flask_login import current_user

from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    UniqueViolationContext,
    regenerate_commit_or_report,
)
from app.routes._form_errors import load_form_or_redirect
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import _regenerate_salary_transactions
from app.schemas.validation import (
    SalaryPayEntryFixSchema,
    SalaryPayEntryRecordSchema,
    SalaryPayEntryRemoveSchema,
)
from app.services import pay_list_service
from app.services.balance_at import BalanceContext
from app.utils.auth_helpers import get_or_404, get_owned_via_parent, require_owner
from app.utils.dates import display_today

logger = logging.getLogger(__name__)

_pay_entry_record_schema = SalaryPayEntryRecordSchema()
_pay_entry_fix_schema = SalaryPayEntryFixSchema()
_pay_entry_remove_schema = SalaryPayEntryRemoveSchema()

#: The pay list's one-entry-per-payday key, named for the duplicate-key
#: report a Record or a Fix racing another onto one payday lands on.
_PAY_ENTRIES_UNIQUE_CONSTRAINT = "uq_pay_entries_profile_payday"


def _edit_page(profile: SalaryProfile) -> RedirectTarget:
    """The salary page every pay-list door answers on."""
    return RedirectTarget("salary.edit_profile", {"profile_id": profile.id})


@salary_bp.route("/salary/<int:profile_id>/pay", methods=["POST"])
@require_owner
def record_pay_entry(profile_id):
    """Record a pay change, and re-price the paychecks it covers.

    Plan step **salary:X-av-3b**, ruling **R-SAL61** ("'Record a pay change'
    adds one from a payday you pick").  Ownership is the profile's (404 for
    not-found and not-yours).  What the door refuses in words is the
    service's (:func:`~app.services.pay_list_service.record_entry`); a Record
    racing another onto one payday lands on ``uq_pay_entries_profile_payday``
    at the guarded flush and is reported in the same words, never a 500.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)
    edit_page = _edit_page(profile)

    data = load_form_or_redirect(_pay_entry_record_schema, edit_page)
    if isinstance(data, Response):
        return data

    # ONE read pass for the POST (task plan#3106): the calendar the payday is
    # checked against and the regeneration after the Record are one value.
    # ``record_entry`` reads only the calendar off it, which a Record does not
    # move, so the regeneration may take it.
    ctx = BalanceContext.build(current_user.id)
    try:
        entry = pay_list_service.record_entry(
            profile, ctx, data["amount"], data["payday"], display_today(),
        )
    except ValidationError as refused:
        db.session.rollback()
        flash(str(refused), "danger")
        return edit_page.to_response()

    payday = entry.payday.isoformat()
    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(ctx, profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="record_pay_entry",
            log_id=profile.id,
            flash_message=(
                "Your salary was changed by another action while you were "
                "recording.  Please reload and try again."
            ),
            redirect=edit_page,
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to record pay on profile %d",
            log_args=(current_user.id, profile.id),
            flash_message="Failed to record the pay change. Please try again.",
            redirect=edit_page,
        ),
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_PAY_ENTRIES_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-key conflict on record_pay_entry profile=%d "
                "(another entry already holds that payday)"
            ),
            log_args=(profile.id,),
            flash_message=(
                f"Your pay from {payday} is already recorded; fix that "
                f"entry instead."
            ),
            redirect=edit_page,
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d recorded pay from %s on salary profile %d",
        current_user.id, payday, profile.id,
    )
    flash(f"Pay recorded from {payday}.", "success")
    return edit_page.to_response()


@salary_bp.route("/salary/pay/<int:entry_id>/fix", methods=["POST"])
@require_owner
def fix_pay_entry(entry_id):
    """Correct one pay entry's amount or payday, and re-price what it covers.

    Plan step **salary:X-av-3a**, ruling **R-SAL61** ("'Fix' edits one").
    Ownership runs through the entry's profile (404 for not-found and
    not-yours).  Optimistic locking as the raise edit does it: the form ships
    the entry's ``version_id``; a stale one short-circuits with a flash, and a
    flush-time ``StaleDataError`` is caught by the same guard.  A Fix racing
    another onto one payday lands on ``uq_pay_entries_profile_payday`` and is
    reported as the recoverable warning it is.

    The form is LOADED once, through the shared loader, since it moved here
    at plan step salary:X-av-3b: it validated and then loaded, the double pass
    :func:`~app.routes._form_errors.load_form_or_refuse` exists to delete.
    """
    entry = get_owned_via_parent(SalaryPayEntry, entry_id, "salary_profile")
    if entry is None:
        abort(404)
    profile = entry.salary_profile
    edit_page = _edit_page(profile)

    data = load_form_or_redirect(_pay_entry_fix_schema, edit_page)
    if isinstance(data, Response):
        return data

    stale_message = (
        "This pay entry was changed by another action while you were "
        "editing.  Please reload and try again."
    )
    if data["version_id"] != entry.version_id:
        logger.info(
            "Stale-form conflict on fix_pay_entry id=%d "
            "(submitted=%d, current=%d)",
            entry_id, data["version_id"], entry.version_id,
        )
        flash(stale_message, "warning")
        return edit_page.to_response()

    # ONE read pass for the POST (task plan#3106): the calendar a changed
    # payday is checked against and the regeneration after the Fix are one
    # value.  ``fix_entry`` reads only the calendar off it, which a Fix does
    # not move, so the regeneration may take it.
    ctx = BalanceContext.build(current_user.id)
    try:
        pay_list_service.fix_entry(
            entry, ctx, data["amount"], data["payday"], display_today(),
        )
    except ValidationError as refused:
        db.session.rollback()
        flash(str(refused), "danger")
        return edit_page.to_response()

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(ctx, profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="fix_pay_entry",
            log_id=entry_id,
            flash_message=stale_message,
            redirect=edit_page,
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to fix pay entry %d on profile %d",
            log_args=(current_user.id, entry_id, profile.id),
            flash_message="Failed to fix the pay entry. Please try again.",
            redirect=edit_page,
        ),
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_PAY_ENTRIES_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-key conflict on fix_pay_entry id=%d "
                "(another entry already holds that payday)"
            ),
            log_args=(entry_id,),
            flash_message=(
                "Another pay entry already starts on that payday.  Fix that "
                "entry instead."
            ),
            redirect=edit_page,
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d fixed pay entry %d on salary profile %d",
        current_user.id, entry_id, profile.id,
    )
    flash("Pay entry fixed.", "success")
    return edit_page.to_response()


@salary_bp.route("/salary/pay/<int:entry_id>/remove", methods=["POST"])
@require_owner
def remove_pay_entry(entry_id):
    """Remove one pay entry, never the only one, and re-price what it covered.

    Plan step **salary:X-av-3b**, ruling **R-SAL68** ("Remove, except the last
    one").  Ownership runs through the entry's profile (404 for not-found and
    not-yours).  The form ships the entry's ``version_id``, so a Remove
    rendered before another tab's Fix of the entry is refused with a flash;
    a Fix or Remove landing between that check and the DELETE fails the
    version-pinned DELETE at the guarded flush, reported the same way.  The
    only-entry refusal is the service's, in its own words.
    """
    entry = get_owned_via_parent(SalaryPayEntry, entry_id, "salary_profile")
    if entry is None:
        abort(404)
    profile = entry.salary_profile
    edit_page = _edit_page(profile)

    data = load_form_or_redirect(_pay_entry_remove_schema, edit_page)
    if isinstance(data, Response):
        return data

    stale_message = (
        "This pay entry was changed by another action.  Please reload and "
        "try again."
    )
    if data["version_id"] != entry.version_id:
        logger.info(
            "Stale-form conflict on remove_pay_entry id=%d "
            "(submitted=%d, current=%d)",
            entry_id, data["version_id"], entry.version_id,
        )
        flash(stale_message, "warning")
        return edit_page.to_response()

    payday = entry.payday.isoformat()
    try:
        pay_list_service.remove_entry(entry)
    except ValidationError as refused:
        flash(str(refused), "danger")
        return edit_page.to_response()

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(
            BalanceContext.build(current_user.id), profile,
        ),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="remove_pay_entry",
            log_id=entry_id,
            flash_message=stale_message,
            redirect=edit_page,
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to remove pay entry %d on profile %d",
            log_args=(current_user.id, entry_id, profile.id),
            flash_message="Failed to remove the pay entry. Please try again.",
            redirect=edit_page,
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d removed pay entry %d on salary profile %d",
        current_user.id, entry_id, profile.id,
    )
    flash(f"Your pay from {payday} is removed.", "info")
    return edit_page.to_response()
