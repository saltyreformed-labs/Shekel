"""
Shekel Budget App -- Salary route package: line items (raises + payroll lines).

Add, edit, and delete the two parallel families of salary line item -- pay
raises and payroll LINES (deductions and, since plan step salary:R18-b,
earnings; ruling **R-SAL38**) -- both of which regenerate the linked
salary transactions on every change.  The two families are deliberately
co-located and kept as explicit parallel implementations: they differ on
the model, schema, percentage conversion, unique constraint, user-facing
messages, and HTMX partial, so a single generic CRUD helper would couple
two distinct domains behind a many-parameter interface (a worse
abstraction than the parallel code).  The shared cross-cutting concern --
committing the regenerated transactions and reporting every recoverable
failure (the stale-lock conflict, the expected unique-constraint
collision, and other DB errors) -- IS factored out, through
:func:`app.routes._commit_helpers.regenerate_commit_or_report`.
"""

import logging
from decimal import Decimal
from typing import Any

from flask import Response, abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.utils.auth_helpers import (
    get_or_404,
    get_owned_via_parent,
    require_owned_fk,
    require_owner,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_line import PaycheckLine
from app.models.account import Account
from app import ref_cache
from app.enums import CalcMethodEnum
from app.utils.db_errors import is_unique_violation
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    UniqueViolationContext,
    handle_db_error,
    regenerate_commit_or_report,
)
from app.routes._form_errors import load_form_or_redirect
from app.routes._recurrence_form_helpers import (
    author_recurrence_for_create,
    recurrence_spec_for_create,
    resolve_recurrence_rule_for_update,
)
from app.routes._recurrence_form_refusals import RecurrenceFormContext
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_END_BOUND_KEY,
    RECURRENCE_MAX_PER_MONTH_KEY,
    RECURRENCE_REPEATS_ON_KEY,
    RECURRENCE_STARTS_ON_KEY,
)
from app.services import paycheck_line_kinds, pay_stub_service, payroll_line_cadence
from app.services.balance_at import BalanceContext
from app.services.recurrence import MonthReading, NeverEnds, end_bound_from_columns
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _LINE_UPDATE_FIELDS,
    _PAYCHECK_LINES_UNIQUE_CONSTRAINT,
    _RAISE_UPDATE_FIELDS,
    _SALARY_RAISES_UNIQUE_CONSTRAINT,
    _line_schema,
    _line_update_schema,
    _raise_schema,
    _raise_update_schema,
    _regenerate_salary_transactions,
    _respond_after_line_change,
    _respond_after_raise_change,
)

logger = logging.getLogger(__name__)


def _edit_page(profile_id: int) -> RedirectTarget:
    """Where every refused or failed line-item submission sends the user: the profile's edit page.

    Twelve literal spellings of one target until plan step salary:R15-c
    added four more; one function is the answer to a review that counted them.
    """
    return RedirectTarget("salary.edit_profile", {"profile_id": profile_id})


# ── Raises ─────────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/raises", methods=["POST"])
@require_owner
def add_raise(profile_id):
    """Add a raise to a salary profile."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _raise_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _raise_schema.load(request.form)

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("percentage") is not None:
        data["percentage"] = Decimal(str(data["percentage"])) / Decimal("100")

    salary_raise = SalaryRaise(salary_profile_id=profile.id, **data)
    db.session.add(salary_raise)

    # Capture the requester id on the clean session up front; the failure
    # path builds its DbErrorContext after a failed flush, where reading the
    # expired current_user attribute would hit the rolled-back session.
    user_id = current_user.id

    try:
        _regenerate_salary_transactions(BalanceContext.build(user_id), profile)
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-raise double-submit (F-051 / C-23): the composite
        # unique ``uq_salary_raises_profile_type_date`` rejects
        # the second INSERT when the user clicks Save twice in a row,
        # the browser retries on a flaky network, or the back button
        # is used to re-submit the form.  Roll back and treat as
        # idempotent success: the user lands on the edit page with
        # the raise they intended to create regardless of which
        # request reached the database first, so neither path
        # surfaces the constraint name as a 500.
        db.session.rollback()
        if not is_unique_violation(exc, _SALARY_RAISES_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to add raise to profile %d "
                "(unexpected IntegrityError)",
                user_id, profile_id,
            )
            flash("Failed to add raise. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))
        logger.info(
            "Duplicate salary raise prevented on profile %d "
            "(idempotent success)", profile_id,
        )
        flash(
            "A raise with that type and effective date already "
            "exists on this profile.",
            "info",
        )
        return _respond_after_raise_change(profile)
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): the IntegrityError branch
        # above covers unique-constraint and other constraint
        # violations.  Remaining DB-tier errors (DataError on
        # numeric range, OperationalError on connection loss,
        # etc.) land here.  Non-SQLAlchemy exceptions propagate
        # to the 500 handler.
        return handle_db_error(DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to add raise to profile %d",
            log_args=(user_id, profile_id),
            flash_message="Failed to add raise. Please try again.",
            redirect=_edit_page(profile_id),
        ))

    logger.info("user_id=%d added raise to profile %d", current_user.id, profile_id)
    flash("Raise added.", "success")

    return _respond_after_raise_change(profile)


@salary_bp.route("/salary/raises/<int:raise_id>/delete", methods=["POST"])
@require_owner
def delete_raise(raise_id):
    """Remove a raise from a salary profile.

    Optimistic locking (commit C-18 / F-010): the DELETE statement is
    version-pinned by SQLAlchemy; a concurrent edit raises
    :class:`StaleDataError`, converted to a flash + redirect by the
    canonical :func:`regenerate_commit_or_report` guard.
    """
    salary_raise = get_owned_via_parent(
        SalaryRaise, raise_id, "salary_profile",
    )
    if salary_raise is None:
        abort(404)

    profile = salary_raise.salary_profile

    # Stage the deletion (no DB I/O yet); the flush + commit happen inside
    # the stale guard below, so a concurrent-edit StaleDataError raised by
    # the delete's flush is caught there.
    db.session.delete(salary_raise)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(BalanceContext.build(current_user.id), profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="delete_raise",
            log_id=raise_id,
            flash_message=(
                "This raise was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=_edit_page(profile.id),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to delete raise %d from profile %d",
            log_args=(current_user.id, raise_id, profile.id),
            flash_message="Failed to remove raise. Please try again.",
            redirect=_edit_page(profile.id),
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d deleted raise %d from profile %d",
        current_user.id, raise_id, profile.id,
    )
    flash("Raise removed.", "info")

    return _respond_after_raise_change(profile)


@salary_bp.route("/salary/raises/<int:raise_id>/edit", methods=["POST"])
@require_owner
def update_raise(raise_id):
    """Update an existing raise on a salary profile.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input populated by app.js.  A stale
    submission is rejected with a flash + redirect; the
    SQLAlchemy-tier check catches the truly-concurrent case at
    flush time and produces the same response.

    Recoverable failures during the regenerate + commit are delegated to
    :func:`regenerate_commit_or_report`, which returns the flash +
    redirect for each: the flush-time :class:`StaleDataError`
    (C-18/F-010), the expected duplicate-key
    :class:`~sqlalchemy.exc.IntegrityError` (F-051/C-23, surfaced as a
    warning), and any other DB error (C-46/F-145, a danger flash).  The
    route keeps only the input-validation and stale-form pre-check guard
    clauses.
    """
    salary_raise = get_owned_via_parent(
        SalaryRaise, raise_id, "salary_profile",
    )
    if salary_raise is None:
        abort(404)

    profile = salary_raise.salary_profile

    errors = _raise_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    data = _raise_update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != salary_raise.version_id:
        logger.info(
            "Stale-form conflict on update_raise id=%d "
            "(submitted=%d, current=%d)",
            raise_id, submitted_version, salary_raise.version_id,
        )
        flash(
            "This raise was changed by another action while you were "
            "editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("percentage") is not None:
        data["percentage"] = Decimal(str(data["percentage"])) / Decimal("100")

    for field_name, value in data.items():
        if field_name in _RAISE_UPDATE_FIELDS:
            setattr(salary_raise, field_name, value)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(BalanceContext.build(current_user.id), profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_raise",
            log_id=raise_id,
            flash_message=(
                "This raise was changed by another action while you were "
                "editing.  Please reload and try again."
            ),
            redirect=_edit_page(profile.id),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update raise %d on profile %d",
            log_args=(current_user.id, raise_id, profile.id),
            flash_message="Failed to update raise. Please try again.",
            redirect=_edit_page(profile.id),
        ),
        # Duplicate-key collision on update (F-051 / C-23): the user edited
        # this raise's (type, start date) pair onto one another active
        # raise on the same profile already holds -- a recoverable warning,
        # not a 500.  Any other IntegrityError falls through to error_ctx.
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_SALARY_RAISES_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-key conflict on update_raise id=%d "
                "(another raise already covers this profile/type/date)"
            ),
            log_args=(raise_id,),
            flash_message=(
                "Another raise on this profile already covers that "
                "type and effective date.  Edit or remove it before "
                "applying these changes."
            ),
            redirect=_edit_page(profile.id),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d updated raise %d on profile %d", current_user.id, raise_id, profile.id)
    flash("Raise updated.", "success")

    return _respond_after_raise_change(profile)


# ── Payroll lines (deductions and earnings) ────────────────────────




def _settle_line_cadence(
    data: dict[str, Any], ctx: BalanceContext, *, stored: RecurrenceRule | None,
) -> None:
    """Write into the payload what the line form leaves to the app about its rule.

    The line form authors a cadence through the shared recurrence controls
    (plan step salary:R15-c, ruling **R-SAL31**) and, since plan step
    salary:R18-c (ruling **R-SAL38** (2), amending R-SAL30 and R-SAL31), a
    SPAN: a "Starts on" that may be left blank and an optional closing bound.
    Before the recurrence seam reads the payload the way it reads a template
    form's, the two facts a payroll line's rule derives are written in,
    exactly as ``_loan_destination.settle_first_occurrence`` writes a loan
    payment's derived start into a transfer form's payload:

    * a BLANK ``starts_on`` -- the STORED unit's zero at the owner's opening
      payday (rulings **R-SAL30**, **R-SAL36**;
      :func:`~app.services.payroll_line_cadence.first_occurrence`, which reads
      the unit the door canonicalises to, so "every 12 months" and the
      ``YEAR`` its edit form reads back derive one day).  A STATED start is
      the user's and passes through untouched, so the seam's own start
      handling applies unchanged: a create authors it, an update re-points
      the rule onto it (PRESENT replaces), and a cleared box on an edit is
      back to the default.
    * the every-paycheck spelling -- ``every 1 paycheck, no ceiling``, with
      NO SPAN -- is rewritten as NO cadence (ruling **R-SAL29**;
      :func:`~app.services.payroll_line_cadence.is_every_paycheck`), which the
      seam reads as *author nothing* on a create and *delete the rule this
      line had* on an update.  A span is ANY stated start, or a closing bound
      other than *never*: an every-paycheck line that begins or ends
      mid-employment IS a rule, and keeps one.  A stated start is a span
      even when it names the opening payday, and an adversarial review of
      this leaf is why: below a STATED ``history_opens_on`` the engine
      replays backdated paydays for a capped line's year-to-date and the FICA
      cumulative, and there a line with NO rule answers *taken* while a rule
      from the opening answers *not taken* (the rule's walk runs forward from
      its start) -- so two spellings a first draft called equivalent price
      differently, and a typed date must not pick the side silently.  What
      *every paycheck* means below the opening is the developer's question,
      not this door's.  The ceiling and the bound are read the way the seam's
      update door reads them: a PRESENT key (an enabled control, possibly
      cleared) is what the form said, an ABSENT one (a control the form
      disabled, or a crafted POST) leaves the STORED value standing -- so a
      24 line re-saved with the key missing is still the 24 shape, and a
      bounded line re-saved with the bound's keys missing keeps its stop
      rather than losing its rule.
    * a derived start is read by its own day of the month: the derived zero
      -- a payday, the 1st, January 1st -- never clamps, and a "repeating on"
      choice posted beside a blank start (a crafted POST; the script disables
      the control while the box is blank) read no date at all, so a nominal
      day would reach the spec as a pair it refuses as a broken invariant
      and a weekday reading would read the derived date's weekday, which
      nobody chose (plan step recurrence:R8-c).

    A submission that names no cadence is left alone: ``None`` is the form's
    "Does not repeat", and an ABSENT unit is a form that said nothing about
    the cadence (a page cached from before this deploy, a crafted POST),
    which the update route reads as "leave the stored rule alone".  Nothing
    is derived for either, and the calendar is not loaded for either -- the
    pass memoises it, so the one call below is the only derivation the
    pre-write side makes.

    Args:
        data: The schema-loaded payload, mutated in place.
        ctx: The read pass the route built before any write; its
            ``calendar()`` is what the first occurrence is derived from.
        stored: The rule the line carries today, or ``None`` on a create or
            for a line with none -- read only for its ceiling, and only when
            the payload states none.
    """
    unit = data.get("recurrence_unit")
    if unit is None:
        return
    ceiling = (
        data[RECURRENCE_MAX_PER_MONTH_KEY]
        if RECURRENCE_MAX_PER_MONTH_KEY in data
        else (stored.max_per_month if stored is not None else None)
    )
    bound = (
        data[RECURRENCE_END_BOUND_KEY]
        if RECURRENCE_END_BOUND_KEY in data
        else (
            end_bound_from_columns(stored.end_date, stored.max_occurrences)
            if stored is not None else None
        )
    )
    stated_start = data.get(RECURRENCE_STARTS_ON_KEY)
    spans = stated_start is not None or (
        bound is not None and not isinstance(bound, NeverEnds)
    )
    if (
        not spans
        and payroll_line_cadence.is_every_paycheck(unit, data["interval_n"], ceiling)
    ):
        data["recurrence_unit"] = None
        return
    if stated_start is None:
        data[RECURRENCE_STARTS_ON_KEY] = payroll_line_cadence.first_occurrence(
            unit, data["interval_n"], ctx.calendar(),
        )
        data[RECURRENCE_REPEATS_ON_KEY] = MonthReading()


@salary_bp.route("/salary/<int:profile_id>/lines", methods=["POST"])
@require_owner
def add_line(profile_id):
    """Add a payroll line -- a deduction or an earning -- to a salary profile.

    ``add_deduction`` on ``/salary/<id>/deductions`` until plan step
    salary:R18-b (ruling **R-SAL38**): the one door authors a line of any of
    the five kinds, and the schema refuses the one field that differs by
    side (a target account on an earning).

    **The line's cadence is a recurrence rule authored onto it** (plan step
    salary:R15-c, rulings **R-SAL31**, **R-SAL35**): the payload is loaded
    through the one door the template forms use
    (:func:`~app.routes._form_errors.load_form_or_redirect`, so a refused
    cadence is heard in its own words rather than as the generic prompt),
    the derived facts are settled into it
    (:func:`_settle_line_cadence`), the create preamble every recurrence
    form runs reads the spec out
    (:func:`~app.routes._recurrence_form_helpers.recurrence_spec_for_create`),
    and the rule is written onto the flushed line through the write door
    itself -- inside the ``try`` below, because the line's flush precedes it
    there and the name-collision ``IntegrityError`` that flush can surface is
    the one this route already absorbs.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    payload = load_form_or_redirect(
        _line_schema, _edit_page(profile_id),
    )
    if isinstance(payload, Response):
        return payload
    data = payload
    # N-534 (salary:R14-a): a deduction's ``target_account_id`` is what makes
    # it a CONTRIBUTION FEED into an investment account, and the schema checks
    # only that the value is a positive integer -- so ownership is answered
    # here, or one owner points a payroll deduction at another owner's account.
    require_owned_fk(Account, data, "target_account_id")
    data["inflation_enabled"] = request.form.get("inflation_enabled") == "on"

    # ONE read pass for the pre-write side (the 2026-08-16 ruling: a route
    # builds it, producers below take it): the calendar the line's rule is
    # derived from and authored against.  The regeneration afterwards is
    # handed a second, built after the write; whether it may take this one
    # is finding plan#1589's.
    ctx = BalanceContext.build(current_user.id)
    _settle_line_cadence(data, ctx, stored=None)
    spec = recurrence_spec_for_create(
        data,
        user_id=current_user.id,
        redirect=_edit_page(profile_id),
    )

    # Convert percentage inputs (e.g. 6 → 0.06) for storage.
    if data["calc_method_id"] == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE):
        data["amount"] = Decimal(str(data["amount"])) / Decimal("100")
    if data.get("inflation_rate") is not None:
        data["inflation_rate"] = Decimal(str(data["inflation_rate"])) / Decimal("100")

    # Through the RELATIONSHIP, not the FK column (plan step salary:R15-c):
    # a line added by id joins ``profile.lines`` only if nothing has loaded
    # that collection yet in this session, while a line added through the
    # relationship joins it either way, so anything in this request that
    # prices the profile sees it.  The write door's owner check reads
    # ``deduction.user_id`` through the same relationship, so it answers
    # before any flush.  The regeneration below priced the paycheck to
    # re-state the template's amount until plan step X-bp, and that was the
    # reader this was measured against: by id, it re-stated the net WITHOUT
    # the line.
    deduction = PaycheckLine(salary_profile=profile, **data)
    db.session.add(deduction)

    # Capture the requester id on the clean session up front; the failure
    # path builds its DbErrorContext after a failed flush, where reading the
    # expired current_user attribute would hit the rolled-back session.
    user_id = current_user.id

    try:
        # The line first, then its rule ONTO it (plan step R-F6's order: the
        # rule carries the owner's FK).  The flush is the statement the
        # name-collision ``IntegrityError`` below surfaces from, as it was
        # when the regeneration's own flush was the first.
        db.session.flush()
        # Through the create helper every template form uses, for its ONE
        # refusal (plan step salary:R18-c): a stated stop before the first
        # occurrence the rule would actually have -- an end date under a
        # blank start's derived opening, or under the payday a stated start
        # normalises onto -- is the write door's EmptyAuthoredWindowError,
        # worded in the schema's sentence and rolled back, not a 500.
        authored = author_recurrence_for_create(
            spec, deduction, redirect=_edit_page(profile_id), calendar=ctx.calendar(),
        )
        if isinstance(authored, Response):
            return authored
        _regenerate_salary_transactions(BalanceContext.build(user_id), profile)
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-deduction double-submit (F-052 / C-23): the
        # composite unique ``uq_paycheck_lines_profile_name``
        # rejects the second INSERT when the user clicks Save
        # twice in a row, the browser retries on a flaky network,
        # or a deactivated deduction with the same name still
        # exists on the profile.  Roll back and treat as
        # idempotent success: the user lands on the edit page with
        # the deduction they intended to create regardless of
        # which request reached the database first.
        db.session.rollback()
        if not is_unique_violation(exc, _PAYCHECK_LINES_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to add payroll line to profile %d "
                "(unexpected IntegrityError)",
                user_id, profile_id,
            )
            flash("Failed to add payroll line. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))
        attempted_name = data.get("name", "")
        logger.info(
            "Duplicate payroll line prevented on profile %d "
            "(name=%r, idempotent success)",
            profile_id, attempted_name,
        )
        flash(
            f"A payroll line named '{attempted_name}' already exists "
            f"on this profile.  Edit or reactivate it instead of "
            f"creating a duplicate.",
            "info",
        )
        return _respond_after_line_change(profile)
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): the IntegrityError branch
        # above covers unique-constraint and other constraint
        # violations.  Remaining DB-tier errors (DataError on
        # numeric range, OperationalError on connection loss,
        # etc.) land here.  Non-SQLAlchemy exceptions propagate
        # to the 500 handler.
        return handle_db_error(DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to add payroll line to profile %d",
            log_args=(user_id, profile_id),
            flash_message="Failed to add payroll line. Please try again.",
            redirect=_edit_page(profile_id),
        ))

    logger.info("user_id=%d added payroll line to profile %d", current_user.id, profile_id)
    flash(f"Payroll line '{deduction.name}' added.", "success")

    return _respond_after_line_change(profile)


@salary_bp.route("/salary/lines/<int:line_id>/delete", methods=["POST"])
@require_owner
def delete_line(line_id):
    """Remove a payroll line from a salary profile.

    Optimistic locking (commit C-18 / F-010): the DELETE statement is
    version-pinned by SQLAlchemy; a concurrent edit raises
    :class:`StaleDataError`, converted to a flash + redirect by the
    canonical :func:`regenerate_commit_or_report` guard.

    **A line a transcribed pay stub names is refused** (plan step
    salary:S11-b; fork 10, "Refuse; end it instead", worded by ruling
    **R-SAL51** (c)): the message names every such stub, newest first, and
    ending the line keeps it on past paychecks.  The database refuses the
    same delete (``fk_pay_stub_line_amounts_paycheck_line`` is ``RESTRICT``),
    so a stub entered between this check and the flush still cannot lose its
    line; that race reaches the generic failure below instead of the wording.
    """
    deduction = get_owned_via_parent(
        PaycheckLine, line_id, "salary_profile",
    )
    if deduction is None:
        abort(404)

    profile = deduction.salary_profile

    refusal = pay_stub_service.line_delete_refusal(deduction)
    if refusal is not None:
        logger.info(
            "user_id=%d refused to delete payroll line %d: a pay stub names it",
            current_user.id, line_id,
        )
        return _respond_after_line_change(profile, notice=refusal)

    # Stage the deletion (no DB I/O yet); the flush + commit happen inside
    # the stale guard below, so a concurrent-edit StaleDataError raised by
    # the delete's flush is caught there.
    db.session.delete(deduction)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(BalanceContext.build(current_user.id), profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="delete_line",
            log_id=line_id,
            flash_message=(
                "This payroll line was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=_edit_page(profile.id),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to delete payroll line %d from profile %d",
            log_args=(current_user.id, line_id, profile.id),
            flash_message="Failed to remove payroll line. Please try again.",
            redirect=_edit_page(profile.id),
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d deleted payroll line %d from profile %d",
        current_user.id, line_id, profile.id,
    )
    flash("Payroll line removed.", "info")

    return _respond_after_line_change(profile)


@salary_bp.route("/salary/lines/<int:line_id>/edit", methods=["POST"])
@require_owner
def update_line(line_id):
    """Update an existing payroll line on a salary profile.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input populated by app.js.  A stale
    submission is rejected with a flash + redirect; the
    SQLAlchemy-tier check catches the truly-concurrent case at
    flush time and produces the same response.

    Recoverable failures during the regenerate + commit are delegated to
    :func:`regenerate_commit_or_report`, which returns the flash +
    redirect for each: the flush-time :class:`StaleDataError`
    (C-18/F-010), the expected name-collision
    :class:`~sqlalchemy.exc.IntegrityError` (F-052/C-23, surfaced as a
    warning), and any other DB error (C-46/F-145, a danger flash).  The
    route keeps only the input-validation and stale-form pre-check guard
    clauses -- and, since plan step salary:R15-c, the cadence dispatch: the
    line's rule is re-pointed, authored or cleared from the shared recurrence
    controls before the field loop, the way every recurrence form's update
    does it (see :func:`add_line` for the create half).
    """
    deduction = get_owned_via_parent(
        PaycheckLine, line_id, "salary_profile",
    )
    if deduction is None:
        abort(404)

    profile = deduction.salary_profile

    payload = load_form_or_redirect(
        _line_update_schema, _edit_page(profile.id),
    )
    if isinstance(payload, Response):
        return payload
    data = payload
    # N-534: a re-point must land on the requester's own account too.
    # N-534 (salary:R14-a): a deduction's ``target_account_id`` is what makes
    # it a CONTRIBUTION FEED into an investment account, and the schema checks
    # only that the value is a positive integer -- so ownership is answered
    # here, or one owner points a payroll deduction at another owner's account.
    require_owned_fk(Account, data, "target_account_id")
    data["inflation_enabled"] = request.form.get("inflation_enabled") == "on"

    # The earning-kind target rule over the EFFECTIVE pair (plan step
    # salary:R18-b, an adversarial review of it).  The schema refuses a
    # POSTED target beside an earning kind; this door writes only the keys
    # the payload carries (an absent key leaves the stored value alone, the
    # cadence keys' contract), so a payload that flips a stored deduction's
    # kind to an earning and omits the target would have left the stored
    # target standing on an earning -- which the contribution feed reads as a
    # payroll contribution nobody makes.  The target the row WILL carry is
    # the posted one when the key is present, else the stored one.
    effective_target = data.get("target_account_id", deduction.target_account_id)
    if effective_target is not None and not paycheck_line_kinds.is_deduction(
        data["paycheck_line_kind_id"],
    ):
        flash(paycheck_line_kinds.EARNING_TARGET_REFUSAL, "danger")
        return _edit_page(profile.id).to_response()

    # Stale-form check (commit C-18 / F-010).
    #
    # **Blind to a cadence-only change**, and this is stated rather than
    # closed (plan step salary:R15-c): the pin is the DEDUCTION row's
    # version, and the rule is its own row, so an edit that re-authored the
    # cadence and touched no column of the line bumped nothing -- a second
    # tab holding the older cadence then saves its whole record over it
    # unchallenged.  The two template kinds' forms share the blind spot
    # (a cadence-only template edit issues no UPDATE on the template either);
    # it is one family and one remedy, reported to the developer at this
    # leaf's cut for a ledger row rather than patched on one owner.
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != deduction.version_id:
        logger.info(
            "Stale-form conflict on update_line id=%d "
            "(submitted=%d, current=%d)",
            line_id, submitted_version, deduction.version_id,
        )
        flash(
            "This payroll line was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    # Re-point, author, or clear the line's cadence rule from the payload
    # (plan step salary:R15-c), through the dispatcher every recurrence
    # form's update runs
    # (:func:`~app.routes._recurrence_form_helpers.resolve_recurrence_rule_for_update`)
    # once the derived facts are settled in (:func:`_settle_line_cadence`).
    # The pass is the PRE-WRITE one its refusals read (plan step R7d-f) and
    # the calendar the re-author resolves against; the regeneration below is
    # handed a second, built after the write (finding plan#1589).  The
    # dispatcher pops every recurrence key, so the field loop below sees none.
    ctx = BalanceContext.build(current_user.id)
    _settle_line_cadence(data, ctx, stored=deduction.recurrence_rule)
    refusal = resolve_recurrence_rule_for_update(
        deduction,
        data,
        ctx=RecurrenceFormContext(
            # The closing bound the form stated, composed by the schema
            # (plan step salary:R18-c); ``None`` when it stated nothing,
            # which leaves the stored bound alone.
            end_bound=data.pop(RECURRENCE_END_BOUND_KEY, None),
            redirect=_edit_page(profile.id),
        ),
        pass_ctx=ctx,
    )
    if refusal is not None:
        return refusal

    # Convert percentage inputs (e.g. 6 → 0.06) for storage.
    if data["calc_method_id"] == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE):
        data["amount"] = Decimal(str(data["amount"])) / Decimal("100")
    if data.get("inflation_rate") is not None:
        data["inflation_rate"] = Decimal(str(data["inflation_rate"])) / Decimal("100")

    for field_name, value in data.items():
        if field_name in _LINE_UPDATE_FIELDS:
            setattr(deduction, field_name, value)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(BalanceContext.build(current_user.id), profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_line",
            log_id=line_id,
            flash_message=(
                "This payroll line was changed by another action while you "
                "were editing.  Please reload and try again."
            ),
            redirect=_edit_page(profile.id),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update payroll line %d on profile %d",
            log_args=(current_user.id, line_id, profile.id),
            flash_message="Failed to update payroll line. Please try again.",
            redirect=_edit_page(profile.id),
        ),
        # Name-collision rename (F-052 / C-23): the user renamed this
        # deduction onto a name another active or inactive deduction on the
        # same profile already holds -- a recoverable warning, not a 500.
        # Any other IntegrityError falls through to error_ctx.
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_PAYCHECK_LINES_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-name conflict on update_line id=%d "
                "(another payroll line with this name exists on the profile)"
            ),
            log_args=(line_id,),
            flash_message=(
                "Another payroll line on this profile already uses that "
                "name.  Choose a different name or remove the existing "
                "line first."
            ),
            redirect=_edit_page(profile.id),
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d updated payroll line %d on profile %d",
        current_user.id, line_id, profile.id,
    )
    flash(f"Payroll line '{deduction.name}' updated.", "success")

    return _respond_after_line_change(profile)
