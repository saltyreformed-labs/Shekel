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

The Pay Stub page's button (:func:`use_stub_as_pay`, plan step
salary:X-av-4) is a door of the pay list too: it writes a Record or a Fix
from a saved stub, so it sits beside them and reaches their one report of a
failed commit (:func:`_reprice_or_report`) rather than a copy of it.
"""

import logging
from dataclasses import dataclass
from datetime import date

from flask import Response, abort, flash
from flask_login import current_user

from app.exceptions import ValidationError
from app.extensions import db
from app.models.pay_stub import PayStub
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.salary_profile import SalaryProfile
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    UniqueViolationContext,
    handle_stale_form_conflict,
    regenerate_commit_or_report,
)
from app.routes._form_errors import load_form_or_redirect
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _edit_page, _regenerate_salary_transactions, _stub_page,
)
from app.schemas.validation import (
    PayStubUseAsPaySchema,
    SalaryPayEntryFixSchema,
    SalaryPayEntryRecordSchema,
    SalaryPayEntryRemoveSchema,
)
from app.services import pay_list_service, stub_pay_offer
from app.services.balance_at import BalanceContext
from app.utils.auth_helpers import get_or_404, get_owned_via_parent, require_owner
from app.utils.dates import display_today

logger = logging.getLogger(__name__)

_pay_entry_record_schema = SalaryPayEntryRecordSchema()
_pay_entry_fix_schema = SalaryPayEntryFixSchema()
_pay_entry_remove_schema = SalaryPayEntryRemoveSchema()
_stub_use_as_pay_schema = PayStubUseAsPaySchema()

#: The pay list's one-entry-per-payday key, named for the duplicate-key
#: report a Record or a Fix racing another onto one payday lands on.
_PAY_ENTRIES_UNIQUE_CONSTRAINT = "uq_pay_entries_profile_payday"

#: The pay button's report of a page left open while its stub changed.
_STUB_CHANGED_MESSAGE = (
    "This pay stub was changed by another action, so your pay was not "
    "changed.  Please review it and try again."
)

#: The pay button's report of a page left open while the pay it offered to
#: change did: another Record or Fix of that payday, or a lost version race.
_PAY_CHANGED_MESSAGE = (
    "Your pay was changed by another action while this page was open, so "
    "this stub was not used.  Please review it and try again."
)


@dataclass(frozen=True)
class _PayWrite:
    """One staged pay-list write, as its door reports a commit of it that fails.

    The three doors that stage a pay entry -- Record, Fix and the Pay Stub
    page's button -- differ only in these; what each report does, and the
    words a race onto one payday is reported in, are
    :func:`_reprice_or_report`'s.

    Attributes:
        label: The door's endpoint name, which its log lines carry.
        row_id: The row those lines name: the profile a Record adds to, the
            entry a Fix corrects, the stub the button was pressed on.
        payday: The payday the write leaves an entry on, which a race onto
            ``uq_pay_entries_profile_payday`` is reported naming.
        stale_message: The flash for a lost version race.
        failure_message: The flash for any other database failure.
        redirect: Where every report sends the owner.
    """

    label: str
    row_id: int
    payday: date
    stale_message: str
    failure_message: str
    redirect: RedirectTarget


def _reprice_or_report(
    ctx: BalanceContext, profile: SalaryProfile, write: _PayWrite,
) -> Response | None:
    """Re-price the paychecks a staged pay-list write moves and commit, or report why not.

    **The one report of a failed commit for the doors that stage a pay
    entry**: Record and Fix each handed
    :func:`~app.routes._commit_helpers.regenerate_commit_or_report` the
    same three reports until plan step salary:X-av-4, whose Pay Stub page
    button would have spelled them a third time.  Remove stages no entry, so
    no race onto a payday is its to report, and it keeps its own two.  A
    lost version race is flashed as
    *write*'s stale message; a Record or a Fix racing another onto one payday
    meets ``uq_pay_entries_profile_payday`` at the guarded flush and is
    reported in :func:`~app.services.pay_list_service.held_payday_refusal`'s
    words, the sentence the service refuses a held payday in before the race;
    any other database failure is logged with its traceback and flashed as
    *write*'s failure message.  Each rolls the write back.

    Args:
        ctx: The door's one read pass, which the regeneration takes.
        profile: The profile whose paychecks the write moves.
        write: The door's names and words.

    Returns:
        ``None`` once the write and its re-priced paychecks are committed;
        else the redirect reporting the failure.
    """
    return regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(ctx, profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label=write.label,
            log_id=write.row_id,
            flash_message=write.stale_message,
            redirect=write.redirect,
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d: %s id=%d failed to commit on salary profile %d",
            log_args=(current_user.id, write.label, write.row_id, profile.id),
            flash_message=write.failure_message,
            redirect=write.redirect,
        ),
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_PAY_ENTRIES_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-key conflict on %s id=%d "
                "(another entry already holds %s)"
            ),
            log_args=(write.label, write.row_id, write.payday.isoformat()),
            flash_message=pay_list_service.held_payday_refusal(write.payday),
            redirect=write.redirect,
        ),
    )


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
    edit_page = _edit_page(profile.id)

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
    response = _reprice_or_report(ctx, profile, _PayWrite(
        label="record_pay_entry",
        row_id=profile.id,
        payday=entry.payday,
        stale_message=(
            "Your salary was changed by another action while you were "
            "recording.  Please reload and try again."
        ),
        failure_message="Failed to record the pay change. Please try again.",
        redirect=edit_page,
    ))
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
    reported as the recoverable warning it is, in the words the service
    refuses a held payday in before the race
    (:func:`~app.services.pay_list_service.held_payday_refusal`).

    The form is LOADED once, through the shared loader, since it moved here
    at plan step salary:X-av-3b: it validated and then loaded, the double pass
    :func:`~app.routes._form_errors.load_form_or_refuse` exists to delete.
    """
    entry = get_owned_via_parent(SalaryPayEntry, entry_id, "salary_profile")
    if entry is None:
        abort(404)
    profile = entry.salary_profile
    edit_page = _edit_page(profile.id)

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

    response = _reprice_or_report(ctx, profile, _PayWrite(
        label="fix_pay_entry",
        row_id=entry_id,
        # Only a CHANGED payday can meet the key (the entry holds its own),
        # and the payday the Fix moved onto is the posted one.
        payday=data["payday"],
        stale_message=stale_message,
        failure_message="Failed to fix the pay entry. Please try again.",
        redirect=edit_page,
    ))
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
    edit_page = _edit_page(profile.id)

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


@salary_bp.route("/salary/stubs/<int:stub_id>/use-as-pay", methods=["POST"])
@require_owner
def use_stub_as_pay(stub_id):
    """Make a saved stub's base pay your pay from its payday: the Pay Stub page's button.

    Plan step **salary:X-av-4**, ruling **R-SAL61** ("the stub screen offers
    'Use this as my pay from this payday', which adds an entry"), as rulings
    **plan#3959**, **plan#3962** and **plan#3965** settle it.  Ownership is
    the stub's, through its profile (404 for not-found and not-yours).

    **The form posts no figure and no payday.**  The door reads both off the
    stub and asks the page's offer again
    (:func:`~app.services.stub_pay_offer.offer`), so it writes what the
    button offers now, never what was posted, and refuses, writing nothing:

    * a stub changed since the page rendered (its ``version_id``);
    * an offer that is no button now, or one that is not the button the
      page rendered (:func:`_refuse_the_press`).

    Then the write is the pay list's own act at the stub's payday,
    :func:`~app.services.pay_list_service.record_entry` or
    :func:`~app.services.pay_list_service.fix_entry`, refused in the
    service's words and re-priced and reported through
    :func:`_reprice_or_report`, as Record and Fix are.  Every answer lands
    back on the stub, where the gap then reads "the same".

    **One read pass for the POST, handed to the regeneration too.**  The
    offer prices the profile on it before the write, so the pass's pricer
    holds the pay list as it was; the regeneration reads only the pass's
    calendar, scenario and day and prices nothing (plan step X-bp,
    :func:`~app.routes.salary._helpers._regenerate_salary_transactions`), so
    that stale pricing is never read.  Finding plan#1589 is the open question
    of pre-write passes handed on; that is why this one serves.
    """
    stub = get_owned_via_parent(PayStub, stub_id, "salary_profile")
    if stub is None:
        abort(404)
    profile = stub.salary_profile
    stub_page = _stub_page(stub_id)

    data = load_form_or_redirect(_stub_use_as_pay_schema, stub_page)
    if isinstance(data, Response):
        return data
    if data["version_id"] != stub.version_id:
        return handle_stale_form_conflict(
            StaleConflictContext(
                logger=logger, log_label="use_stub_as_pay", log_id=stub_id,
                flash_message=_STUB_CHANGED_MESSAGE, redirect=stub_page,
            ),
            submitted=data["version_id"], current=stub.version_id,
        )

    ctx = BalanceContext.build(current_user.id)
    today = display_today()
    payday = stub.payday
    offer = stub_pay_offer.offer(profile, ctx, payday, stub.base_pay, today)
    refused = _refuse_the_press(offer, data.get("entry_version_id"), stub)
    if refused is not None:
        return refused

    # ``record_entry`` asks the Record door's refusals again.  Its payday half
    # answers as the offer's did, off the pass's one calendar, but it reads
    # the held payday live: another tab's Record of this payday, committed
    # after the offer's read, is refused here in the Record door's words, and
    # one committed after this read meets the unique key at the guarded
    # flush.  A Fix keeps its payday, the only change ``fix_entry`` refuses.
    if offer.entry is None:
        try:
            pay_list_service.record_entry(profile, ctx, stub.base_pay, payday, today)
        except ValidationError as refused:
            db.session.rollback()
            flash(str(refused), "danger")
            return stub_page.to_response()
        done = f"Pay recorded from {payday.isoformat()}."
    else:
        pay_list_service.fix_entry(offer.entry, ctx, stub.base_pay, payday, today)
        done = f"Your pay from {payday.isoformat()} is fixed."

    response = _reprice_or_report(ctx, profile, _PayWrite(
        label="use_stub_as_pay",
        row_id=stub_id,
        payday=payday,
        stale_message=_PAY_CHANGED_MESSAGE,
        failure_message="Failed to change your pay. Please try again.",
        redirect=stub_page,
    ))
    if response is not None:
        return response

    logger.info(
        "user_id=%d used pay stub %d as pay from %s on salary profile %d",
        current_user.id, stub_id, payday.isoformat(), profile.id,
    )
    flash(done, "success")
    return stub_page.to_response()


def _refuse_the_press(
    offer: "stub_pay_offer.PayOffer | stub_pay_offer.NoPayOffer | None",
    posted_entry_version: int | None, stub: PayStub,
) -> Response | None:
    """Refuse a press of the stub's pay button that the offer asked again does not take.

    Refused, back to the stub, writing nothing:

    * no offer: the app holds no paycheck on the stub's payday any more, or
      pays the stub's base there now (another tab's Record or Fix closed the
      gap);
    * no button: a raise now starts inside the paycheck (ruling
      **plan#3959**), or the Record door now refuses the payday, said in the
      offer's own words;
    * a button that is not the one the page rendered.  The form posts
      ``entry_version_id`` exactly when the page offered a Fix, as the
      version of the amount the Fix corrects
      (:class:`~app.schemas.validation.PayStubUseAsPaySchema`), so the offer
      has changed KIND when one of the two has an amount and the other none
      -- a Record pressed after another tab recorded that payday would now
      fix that tab's amount, a Fix pressed after its amount was removed
      would now record one -- and its AMOUNT when the two versions differ,
      since the confirm named the figure the Fix replaces.

    Args:
        offer: The offer asked again on the server.
        posted_entry_version: The ``entry_version_id`` the form posted, or
            ``None`` when it posted none.
        stub: The stub the button was pressed on.

    Returns:
        The redirect back to the stub, its refusal flashed, or ``None`` when
        the offer is the button the page rendered.
    """
    stub_page = _stub_page(stub.id)
    if offer is None:
        flash(
            f"Your pay was not changed: the app holds no paycheck on "
            f"{stub.payday.isoformat()} that differs from this stub.",
            "info",
        )
        return stub_page.to_response()
    if not offer.button:
        flash(offer.reason, "danger")
        return stub_page.to_response()
    if (offer.entry is None) != (posted_entry_version is None):
        logger.info(
            "Changed-offer conflict on use_stub_as_pay id=%d "
            "(the page offered a %s, the payday now offers a %s)",
            stub.id,
            "Record" if posted_entry_version is None else "Fix",
            "Record" if offer.entry is None else "Fix",
        )
        flash(_PAY_CHANGED_MESSAGE, "warning")
        return stub_page.to_response()
    if offer.entry is not None and posted_entry_version != offer.entry.version_id:
        return handle_stale_form_conflict(
            StaleConflictContext(
                logger=logger, log_label="use_stub_as_pay", log_id=stub.id,
                flash_message=_PAY_CHANGED_MESSAGE, redirect=stub_page,
            ),
            submitted=posted_entry_version, current=offer.entry.version_id,
        )
    return None
