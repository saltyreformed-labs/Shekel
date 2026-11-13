"""
Shekel Budget App -- Salary route package: profile CRUD.

Create, list, edit, update, and soft-delete salary profiles, including
the auto-linked income transaction template created with each profile.
"""

import logging

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup
from sqlalchemy.exc import SQLAlchemyError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.category import Category
from app.models.ref import (
    CalcMethod,
    FilingStatus,
    RaiseType,
)
from app import ref_cache, tax_law
from app.enums import RecurrenceUnitEnum, TxnTypeEnum
from app.services import (
    account_service,
    pay_list_service,
    recurrence_engine,
    salary_pay_period,
    salary_paydays,
    salary_profile_service,
    template_amount_service,
)
from app.services import pay_schedule_service, pay_stub_service
from app.services.balance_at import BalanceContext
from app.services.obligations_aggregator import todays_price
from app.services.pay_calendar import cadence_on, calendar_at_schedule
from app.services.payroll_basis import PayrollBasis
from app.services.recurrence import RecurrenceSpec, author_rule
from app.services.generation_schedule import GenerationSchedule
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    commit_or_handle_stale,
    handle_db_error,
    regenerate_commit_or_report,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _PROFILE_UPDATE_FIELDS,
    _create_schema,
    _edit_page,
    _line_cadence_context,
    _get_investment_accounts,
    _raise_card_context,
    _regenerate_salary_transactions,
    _update_schema,
)

logger = logging.getLogger(__name__)


@salary_bp.route("/salary/new")
@require_owner
def new_profile():
    """Display the salary profile creation form.

    The first pay entry's payday defaults to the CURRENT payday (plan step
    salary:X-av-3a): the pay the owner types is what they are paid now, so a
    raise they already received is in it and is not added on top
    (:func:`~app.services.salary_paydays.first_entry_payday` states the rule,
    the first payday included for an owner not yet paid).  The day is the
    owner's civil day, read once (ledger row **SAL-572**): the day the create
    door's payday rule reads (``create_profile`` hands ``display_today()`` to
    the pay list, ruling **R-SAL90**), so the payday offered is one the door
    takes.  It was the read pass's ``as_of`` until task plan#3170, the
    process's day, which only the deployed ``TZ`` made the owner's.

    **Beside it the form states the paychecks a year in force on that payday**
    (ruling **R-SAL97**, "Today's rhythm"), read through
    :func:`~app.services.pay_calendar.cadence_on`, the one producer of a
    payday's rhythm, which the pay list's count for an entry on that payday
    also reaches (``pay_list_service.pay_rows`` through
    ``PayrollBasis.base_pay_on``).  It was the LATEST rhythm's count
    (``pay_schedule_service.resolve_cadence``), which for an owner with a
    later rhythm recorded is not the count the typed pay is multiplied by
    (rulings **R-SAL59** and **R-SAL66**).  The count is read-only (plan step
    R-F16): the pay-period settings own the rhythm, and a second control for
    it was the finding.

    **No payday to default to, no count**, and the template renders a pointer
    to the pay-period settings in its place: an owner with no schedule, or
    one whose schedule holds no payday.  Neither holds a payday the create
    door would take for the first entry.

    **The schedule row is read once, and softly** (``resolve_schedule``),
    with the calendar derived from it (``calendar_at_schedule``, the shape
    ``routes/settings`` uses for the same reason): a form must not 500 on the
    state it points the owner to the fix for, and
    :func:`~app.services.pay_calendar.calendar_for` REFUSES an owner with no
    schedule row (ruling **R-PC45**).
    """
    filing_statuses = db.session.query(FilingStatus).all()
    current_payday = paychecks_per_year = None
    facts = pay_schedule_service.resolve_schedule(current_user.id)
    if facts is not None:
        calendar = calendar_at_schedule(current_user.id, facts)
        current_payday = salary_paydays.first_entry_payday(
            calendar, display_today(),
        )
        if current_payday is not None:
            paychecks_per_year = int(
                cadence_on(calendar, current_payday).periods_per_year,
            )
    return render_template(
        "salary/form.html",
        profile=None,
        filing_statuses=filing_statuses,
        supported_states=tax_law.LAW.supported_states,
        raise_types=[],
        calc_methods=[],
        paychecks_per_year=paychecks_per_year,
        current_payday=current_payday,
        pay_period=None,
    )


def _salary_category(user_id: int) -> Category:
    """Return this owner's ``Income: Salary`` category, creating it if absent.

    Every salary profile files its paycheck under one category, and a new owner
    has none until their first profile is created -- so the read and the create
    are one operation rather than a caller's two-step.

    Args:
        user_id: The owner.

    Returns:
        The persisted :class:`~app.models.category.Category`, flushed when
        newly created so it carries an id the caller can link.
    """
    existing = (
        db.session.query(Category)
        .filter_by(user_id=user_id, group_name="Income", item_name="Salary")
        .first()
    )
    if existing:
        return existing
    category = Category(
        user_id=user_id,
        group_name="Income",
        item_name="Salary",
        sort_order=0,
    )
    db.session.add(category)
    db.session.flush()
    return category


def _paycheck_template(
    data: dict, *, account_id: int, category_id: int, calendar,
) -> TransactionTemplate:
    """Create and flush the every-paycheck template a salary profile files through.

    The rule and the template are one operation: a salary profile's paycheck
    recurs every pay period by definition, so nothing chooses a cadence and
    nothing links the two afterwards.

    **The rule starts at the OPENING of the owner's schedule.**  Stated rather
    than implied since plan step R7c-b made ``starts_on`` required, and it is
    the same value an absent opening bound resolved to before -- so a new
    salary profile fans its paychecks across every pay period the owner has,
    closed ones included.  Plan ledger row **D34** carries whether it should.

    **The template states no price** (plan step X-bp, rulings R-IY and
    R-BAL16): the paycheck calculator prices each of its rows live, and a
    salary-linked definition owns no price series
    (``template_amount_service.owns_its_amount``).  It was born carrying a
    ``default_amount`` copy: the gross paycheck re-stated at the net a few
    lines later until plan step salary:X-av-3a, the net paycheck at a
    reference period from then until X-bp dropped the column (finding
    **N-446**).

    **The calendar is TAKEN rather than derived here** (pay-calendar plan step
    C2-f3c).  ``create_profile`` derives one anyway for the paycheck it then
    prices, so deriving a second one inside this helper made one POST answer
    "what is this owner's schedule" twice from two reads that a concurrent
    write could separate.

    Args:
        data: The validated create payload; read for the name.
        account_id: The deposit account the paychecks land in -- neither a
            loan nor a credit card (the picker in :func:`create_profile`).
        category_id: This owner's ``Income: Salary`` category.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            read for the schedule's opening payday.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        carrying an id the profile can link.
    """
    template = TransactionTemplate(
        user_id=current_user.id,
        account_id=account_id,
        category_id=category_id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        name=data["name"],
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()
    # **The paycheck cadence is authored ONTO the template** (plan step R-F6):
    # the rule carries its owner's FK, so the definition has to exist first.
    # The order reversed here; the cadence itself is unchanged.
    author_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=calendar.opening_bound(),
        ),
        calendar,
        template,
    )
    return template


@salary_bp.route("/salary", methods=["POST"])
@require_owner
def create_profile():
    """Create a new salary profile with auto-linked template."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.new_profile"))

    data = _create_schema.load(request.form)

    # ONE read pass for the whole POST (plan step R7d-c-1): the baseline
    # scenario this branch refuses on, the owner's schedule every step below
    # reads, and the pass the generate runs in are one value rather than three
    # lookups that have to agree.
    ctx = BalanceContext.build(current_user.id)
    if ctx.scenario is None:
        flash(Markup(
            "No baseline scenario found. Please "
            '<a href="/register" class="alert-link">register a new account</a> '
            "to set up your budget."
        ), "danger")
        return redirect(url_for("salary.cockpit"))

    salary_category = _salary_category(current_user.id)

    # Get the default deposit account -- a NON-LOAN (non-amortizing) account
    # that is not a credit card either.
    # A loan's balance is ledger-derived, not a transaction sum (ruling D4 /
    # finding N-11): depositing salary income onto a loan would have the
    # recurrence engine generate raw income transactions onto it
    # (``recurrence_engine.generate_for_template`` copies ``template.account_id``)
    # -- a cash leg the loan fold cannot see, the shape the transaction-create
    # routes (``_reject_transaction_on_loan``) and the template form also
    # refuse.  A CARD is excluded for a different reason (plan step
    # credit_card:CC-10, design 3.8): its balance IS the cash fold, so every
    # generated paycheck would be counted -- as income paying DOWN the card,
    # which a salary is not.  A card takes its payment (a transfer into it)
    # and a credit (a refund, a redemption; direct income stays allowed at
    # the transaction doors), not the owner's paycheck.
    # ``active_accounts_query`` is the shared kind-boundary composer the
    # grid's account pickers use (ruling D4 / A1); ``revolving`` is its
    # orthogonal second filter (CC-4-1).
    account = account_service.active_accounts_query(
        current_user.id, amortizing=False, revolving=False,
    ).first()
    if not account:
        flash(Markup(
            'You need an active account that is not a loan or a credit card '
            'before creating a salary profile. '
            '<a href="' + url_for("accounts.new_account") + '" class="alert-link">'
            'Create an account</a>.'
        ), "danger")
        return redirect(url_for("salary.cockpit"))

    # Capture the requester id before the DB work below: the failure path
    # builds its DbErrorContext after a failed flush, where re-reading the
    # then-expired current_user attribute would touch the rolled-back
    # session (PendingRollbackError) rather than yield the id.
    user_id = current_user.id

    # The template's opening bound, the first pay entry's payday check and the
    # generate pass all read the pass's own derivation of it (pay-calendar
    # plan step C2-f3c; plan step R7d-c-1 moved it onto the pass).
    calendar = ctx.calendar()

    try:
        # The job's pay periods from one stub's two dates (plan#3048, ruling
        # plan#3096), off the pass's calendar; a refusal is the owner's to
        # correct, reported below with the others.
        period_end_days = salary_pay_period.days_before_payday(
            calendar, data["stub_pay_date"], data["stub_period_end"],
        )
        # The profile FIRST, with its pay list (plan step salary:X-av-3a,
        # where it was priced before its template existed so the template
        # was born at the net paycheck; the template states no price since
        # plan step X-bp, so nothing prices it here).
        profile = SalaryProfile(
            user_id=current_user.id,
            scenario_id=ctx.scenario_id,
            filing_status_id=data["filing_status_id"],
            name=data["name"],
            state_code=data["state_code"],
            qualifying_children=data.get("qualifying_children", 0),
            other_dependents=data.get("other_dependents", 0),
            additional_income=data.get("additional_income", 0),
            additional_deductions=data.get("additional_deductions", 0),
            extra_withholding=data.get("extra_withholding", 0),
            stub_gross_includes_after_tax=data.get(
                "stub_gross_includes_after_tax", False,
            ),
            period_end_days_before_payday=period_end_days,
        )
        db.session.add(profile)
        db.session.flush()
        # The payday rule is the stub door's, and so is its "today": the
        # owner's civil day, as the stub route hands it (ruling R-SAL90).
        pay_list_service.start_pay_list(
            profile, ctx, data["pay_amount"], data["pay_payday"],
            display_today(),
        )

        template = _paycheck_template(
            data,
            account_id=account.id,
            category_id=salary_category.id,
            calendar=calendar,
        )
        profile.template = template
        db.session.flush()

        # Generate income transactions via recurrence engine.  The schedule
        # is the OWNER's whole one, off the same calendar the paycheck engine
        # prices against (plan step R4b-1).  ONE derivation answers both
        # (pay-calendar plan steps C2-f2d-3, C2-f3c).
        schedule = GenerationSchedule.for_pass(ctx)
        recurrence_engine.generate_for_template(
            template, schedule, ctx.scenario_id,
        )

        db.session.commit()
    except ValidationError as refused:
        db.session.rollback()
        flash(str(refused), "danger")
        return redirect(url_for("salary.new_profile"))
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): DB-tier failures (FK, CHECK,
        # NUMERIC range, OperationalError, etc.) produce the user-
        # facing flash + redirect.  Non-SQLAlchemy exceptions
        # (TypeError, AttributeError, decimal arithmetic) propagate
        # to the Flask 500 handler so they surface as bugs rather
        # than being silently swallowed.
        return handle_db_error(DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to create salary profile",
            log_args=(user_id,),
            flash_message="Failed to create salary profile. Please try again.",
            redirect=RedirectTarget("salary.new_profile"),
        ))

    logger.info("user_id=%d created salary profile %d", current_user.id, profile.id)
    flash(f"Salary profile '{profile.name}' created.", "success")
    return _edit_page(profile.id).to_response()


@salary_bp.route("/salary/<int:profile_id>/edit")
@require_owner
def edit_profile(profile_id):
    """Display the salary profile edit form with raises and deductions."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    filing_statuses = db.session.query(FilingStatus).all()
    raise_types = db.session.query(RaiseType).all()
    calc_methods = db.session.query(CalcMethod).all()
    investment_accounts = _get_investment_accounts(current_user.id)

    # The danger zone (restyled in P3) lists the user's deactivated
    # profiles with a Reactivate action.  Passed now so the producer
    # contract is complete; the current form template ignores it.
    inactive_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=False)
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )

    # The pay list and the changes of rhythm no pay is recorded from (plan
    # step salary:X-av-3a, rulings R-SAL61 and R-SAL82), off the one walk the
    # engine prices through.  The pass's pinned day is also the form's year,
    # the one read of "today" (ledger row SAL-572).
    ctx = BalanceContext.build(current_user.id)
    basis = PayrollBasis(profile, ctx.calendar())

    return render_template(
        "salary/form.html",
        profile=profile,
        filing_statuses=filing_statuses,
        supported_states=tax_law.LAW.supported_states,
        raise_types=raise_types,
        calc_methods=calc_methods,
        investment_accounts=investment_accounts,
        inactive_profiles=inactive_profiles,
        # No paychecks-a-year count: each pay-list row states its own
        # (ruling R-SAL97).
        pay_rows=pay_list_service.pay_rows(basis),
        rhythm_changes=basis.rhythm_changes_without_pay(),
        stub_summaries=pay_stub_service.stub_summaries(profile),
        pay_period=salary_pay_period.pay_period_view(
            ctx.calendar(), profile.period_end_days_before_payday, ctx.as_of,
        ),
        **_line_cadence_context(profile, ctx.calendar),
        # What recorded pay holds of each raise, off the basis the pay list
        # is priced from (task plan#3169).
        **_raise_card_context(profile, lambda: basis),
    )


@salary_bp.route("/salary/<int:profile_id>", methods=["POST"])
@require_owner
def update_profile(profile_id):
    """Update a salary profile and recalculate linked transactions.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return _edit_page(profile_id).to_response()

    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != profile.version_id:
        logger.info(
            "Stale-form conflict on update_profile id=%d "
            "(submitted=%d, current=%d)",
            profile_id, submitted_version, profile.version_id,
        )
        flash(
            "This salary profile was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return _edit_page(profile_id).to_response()

    # ONE read pass for the POST (task plan#3106): the calendar the pay-period
    # pair is checked against below and the regeneration after the edits are
    # one value.  It reads only the calendar before the edits, which they do
    # not move, so the regeneration may take it.
    ctx = BalanceContext.build(current_user.id)

    # The pay-period pair (plan#3048, ruling plan#3096), turned into the gap
    # BEFORE anything is staged, so a refusal leaves the profile as it was.
    # Neither posted leaves the stored gap alone; one alone is refused.
    if "stub_pay_date" in data or "stub_period_end" in data:
        try:
            profile.period_end_days_before_payday = (
                salary_pay_period.days_before_payday(
                    ctx.calendar(),
                    data.get("stub_pay_date"), data.get("stub_period_end"),
                )
            )
        except ValidationError as refused:
            flash(str(refused), "danger")
            return _edit_page(profile_id).to_response()

    for field_name, value in data.items():
        if field_name in _PROFILE_UPDATE_FIELDS:
            setattr(profile, field_name, value)

    # The linked template takes the profile's name.  ``profile.template`` is
    # eager (lazy="joined"), so this touches no DB and stages safely before
    # the guard below picks up the commit.  The GROSS amount written here
    # beside it went with the yearly salary (plan step salary:X-av-3a), and
    # the net the regeneration re-stated went at plan step X-bp: a salary
    # profile's template states no amount, its figure is today's paycheck.
    if profile.template and "name" in data:
        profile.template.name = data["name"]

    # Regenerate transactions and commit under the canonical optimistic-lock
    # guard (C-18 / F-010): the regeneration flushes, so it must run inside
    # the same stale-race guard as the commit.  ``StaleDataError`` and any
    # other DB error are reported by regenerate_commit_or_report (no
    # IntegrityError branch -- a profile edit has no expected unique
    # collision).
    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(ctx, profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action while "
                "you were editing.  Please reload and try again."
            ),
            redirect=_edit_page(profile_id),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update salary profile %d",
            log_args=(current_user.id, profile_id),
            flash_message="Failed to update salary profile. Please try again.",
            redirect=_edit_page(profile_id),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d updated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' updated.", "success")
    return _edit_page(profile_id).to_response()


@salary_bp.route("/salary/<int:profile_id>/delete", methods=["POST"])
@require_owner
def delete_profile(profile_id):
    """Soft-delete a salary profile and deactivate its template.

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts into a flash + redirect.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    # Today's paycheck, priced FIRST and while this profile still prices it
    # (plan step X-bp, an edge case decided under R-BAL207): the template's
    # price history opens at it below, because the Recurring and savings
    # pages already show it as the definition's figure
    # (``obligations_aggregator.todays_price``), so the archive moves no
    # figure there.  It opened at the stored ``default_amount`` until X-bp,
    # the paycheck as of the last salary save, which a raise whose date has
    # passed since then is missing from.  Before the freeze and the flag,
    # with nothing staged, so the pricer's profile query reads the profile
    # as active and flushes nothing.  The pass is pinned to the owner's
    # civil day, the day the version is dated, so the price and its date
    # are one day (ledger row SAL-572's rule).
    today = display_today()
    todays_paycheck = None
    if profile.template:
        todays_paycheck = todays_price(
            profile.template, BalanceContext.build(current_user.id, as_of=today),
        ).amount

    # **BEFORE the flag, and the ordering IS the fix** (finding **N-261**,
    # ruled 2026-09-02).  Archiving removes the producer behind amount rule 2,
    # and since plan step X-au-d the rows it priced hold no figure of their
    # own -- so they must record what they were last worth here, while this
    # profile is still what prices them.  ``is_salary_linked_template`` reads
    # the identity-mapped collection, so a pending ``is_active = False`` is
    # already visible to it: freezing after the flag would freeze every row
    # at the one price the series opens at below, where each row's paycheck
    # is its own.  Measured on the 2026-09-02 production clone, when that
    # price was the stored ``default_amount``: without it the archive
    # re-prices 50 of 59 rows and moves the projected balance by
    # ``-$9,677.24``.
    salary_profile_service.archive_profile(profile)

    profile.is_active = False
    if profile.template:
        profile.template.is_active = False
        # The template's amount stops being DERIVED the moment the profile is
        # archived (plan step X-au-a): with no ACTIVE profile its rows are
        # priced by its own series rather than by the paycheck engine, so
        # today's paycheck becomes the definition's stated price and the write
        # door opens its series at it.  Without this the template would satisfy
        # ``owns_its_amount`` while holding NO version -- an eligible
        # definition with an empty series, which is the one gap
        # ``amount_as_of`` reports as ``None`` and which the amount resolver is
        # specified to REFUSE rather than fall back on.  Found by adversarial
        # review; measured at 58 rows on production's one salary template.
        #
        # **It no longer decides what the EXISTING rows are worth**, which is
        # the half N-261 was about: the freeze above has already made every one
        # of them state its own figure, so this version is what a row generated
        # from the template AFTER the archive would read.
        template_amount_service.set_amount(
            profile.template, todays_paycheck, effective_on=today,
        )

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="delete_profile",
        log_id=profile_id,
        flash_message=(
            "This salary profile was changed by another action.  "
            "Please reload and try again."
        ),
        redirect=RedirectTarget("salary.cockpit"),
    ))
    if conflict is not None:
        return conflict
    logger.info("user_id=%d deactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' deactivated.", "info")
    return redirect(url_for("salary.cockpit"))


@salary_bp.route("/salary/<int:profile_id>/reactivate", methods=["POST"])
@require_owner
def reactivate_profile(profile_id):
    """Reactivate a soft-deleted salary profile (inverse of delete_profile).

    Restores ``is_active`` on the profile and its linked template, then
    regenerates the salary transactions so the grid picks the income back
    up.  An already-active profile is a no-op with an info flash rather
    than a 404 (it is owned and simply needs no action).

    Optimistic locking (commit C-18 / F-010): the reactivation flushes
    (regeneration) then commits under the canonical
    :func:`regenerate_commit_or_report` guard, so a concurrent edit's
    ``StaleDataError`` converts to a flash + redirect like the sibling
    mutation routes.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    if profile.is_active:
        flash(f"Salary profile '{profile.name}' is already active.", "info")
        return _edit_page(profile_id).to_response()

    profile.is_active = True
    if profile.template:
        profile.template.is_active = True

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(BalanceContext.build(current_user.id), profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="reactivate_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=_edit_page(profile_id),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to reactivate salary profile %d",
            log_args=(current_user.id, profile_id),
            flash_message="Failed to reactivate salary profile. Please try again.",
            redirect=_edit_page(profile_id),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d reactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' reactivated.", "success")
    return _edit_page(profile_id).to_response()
