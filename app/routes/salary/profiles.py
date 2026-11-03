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

from app.utils.auth_helpers import get_or_404, get_owned_via_parent, require_owner
from app.utils.dates import display_today
from app.exceptions import ValidationError
from app.extensions import db
from app.models.salary_pay_entry import SalaryPayEntry
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
    salary_profile_service,
    template_amount_service,
)
from app.services import pay_schedule_service, pay_stub_service
from app.services.balance_at import BalanceContext
from app.services.obligations_aggregator import todays_price
from app.services.pay_calendar import PayCadence
from app.services.payroll_basis import PayrollBasis
from app.services.recurrence import RecurrenceSpec, author_rule
from app.schemas.validation import SalaryPayEntryFixSchema
from app.services.generation_schedule import GenerationSchedule
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    UniqueViolationContext,
    commit_or_handle_stale,
    handle_db_error,
    regenerate_commit_or_report,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _PROFILE_UPDATE_FIELDS,
    _create_schema,
    _line_cadence_context,
    _get_investment_accounts,
    _regenerate_salary_transactions,
    _update_schema,
)

logger = logging.getLogger(__name__)

_pay_entry_fix_schema = SalaryPayEntryFixSchema()

#: The pay list's one-entry-per-payday key, named for the duplicate-key
#: report a Fix racing another onto one payday lands on.
_PAY_ENTRIES_UNIQUE_CONSTRAINT = "uq_pay_entries_profile_payday"


def _paychecks_per_year() -> "int | None":
    """Return how many paychecks the owner receives a year, or ``None``.

    **The form's read-only replacement for the ``pay_periods_per_year``
    dropdown** (plan step R-F16).  It is the count of the owner's LATEST
    rhythm (``resolve_cadence``).  A yearly figure is a pay times the count
    in force on ITS payday (ruling **R-SAL59**; since plan step salary:X-av-2
    each payday's own, **R-SAL66**), which is this number for an owner with
    one rhythm and not, before the change, for an owner with a later one
    recorded; the pay list states each entry's own count beside it.  It is
    not the owner's to choose HERE: it derives from
    the owner's pay era's cadence (``budget.pay_eras`` since plan step
    ``pay_calendar:C17-a``; ``budget.pay_schedule.cadence_days`` until then),
    which the pay-period settings own, and offering a second control was the
    finding.

    ``None`` for an owner with no resolvable cadence, which the template
    renders as a pointer to generate a schedule.  Answered rather than raised:
    a form page must not 500 on the state the form itself is the fix for, and
    such an owner cannot create a profile either way (``_paycheck_template``
    needs a payday to seat the recurrence on).

    **Through ``resolve_cadence`` rather than ``cadence_for`` or a whole
    calendar**, and the two doors beside it are why each is wrong here.
    :func:`~app.services.pay_calendar.cadence_for` REFUSES the unresolvable
    owner, which is right for a producer of money and wrong for a form.
    **Since plan step ``pay_calendar:C4-d`` (ruling R-PC45) so does**
    :func:`~app.services.pay_calendar.calendar_for` -- this paragraph said it
    "answers without refusing", which was true of the empty cadence-less
    calendar that step deleted and is now false of both calendar doors.  It
    remains the wrong door here for its OTHER stated reason, which the step did
    not touch: it derives the owner's whole payday set to answer, 61 rows on
    production, on two pages that load a calendar for nothing else.
    ``resolve_cadence`` is the SOFT door and is what this form wants -- the one
    fact both of those read, asked directly, and answered rather than raised.
    *Since plan step salary:R15-b the same page DOES derive the owner's
    calendar whenever a deduction line carries a cadence rule
    (``_helpers._line_cadence_phrases``: a rule is described against
    it, and a rule is authored against one, so that derivation cannot meet
    the refusal).*  **And since plan step salary:X-av-3a the EDIT page
    derives it on every render**, for the pay list and the rhythm notice
    (``edit_profile``), so for that page this soft read no longer spares the
    owner with no schedule: the calendar door refuses one first.  No such
    owner reaches it -- the create door derives the same calendar and no
    door deletes a schedule -- and the NEW-profile page, which derives the
    calendar only after this read finds a cadence, is where it still
    answers rather than raises.

    Returns:
        The paycheck count as an ``int``, or ``None``.
    """
    cadence = pay_schedule_service.resolve_cadence(current_user.id)
    if cadence is None:
        return None
    return int(PayCadence(cadence).periods_per_year)


@salary_bp.route("/salary/new")
@require_owner
def new_profile():
    """Display the salary profile creation form.

    The first pay entry's payday defaults to the CURRENT payday (plan step
    salary:X-av-3a): the pay the owner types is what they are paid now, so a
    raise they already received is in it and is not added on top.  Only an
    owner with a pay schedule has one; the form points the rest at the
    schedule (:func:`_paychecks_per_year`).  The day is the pass's pinned day,
    the one read (ledger row **SAL-572**).
    """
    filing_statuses = db.session.query(FilingStatus).all()
    paychecks_per_year = _paychecks_per_year()
    ctx = BalanceContext.build(current_user.id)
    current_payday = None
    if paychecks_per_year is not None:
        current = ctx.calendar().period_containing(ctx.as_of)
        current_payday = current.start_date if current is not None else None
    return render_template(
        "salary/form.html",
        profile=None,
        filing_statuses=filing_statuses,
        supported_states=tax_law.LAW.supported_states,
        raise_types=[],
        calc_methods=[],
        paychecks_per_year=paychecks_per_year,
        current_payday=current_payday,
        now_year=ctx.as_of.year,
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
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


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
        paychecks_per_year=_paychecks_per_year(),
        now_year=ctx.as_of.year,
        pay_rows=pay_list_service.pay_rows(basis),
        rhythm_changes=basis.rhythm_changes_without_pay(),
        stub_summaries=pay_stub_service.stub_summaries(profile),
        **_line_cadence_context(profile, ctx.calendar),
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
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

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
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

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
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action while "
                "you were editing.  Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update salary profile %d",
            log_args=(current_user.id, profile_id),
            flash_message="Failed to update salary profile. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d updated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' updated.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


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
    """
    entry = get_owned_via_parent(SalaryPayEntry, entry_id, "salary_profile")
    if entry is None:
        abort(404)
    profile = entry.salary_profile
    edit_page = RedirectTarget("salary.edit_profile", {"profile_id": profile.id})

    errors = _pay_entry_fix_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))
    data = _pay_entry_fix_schema.load(request.form)

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
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    try:
        pay_list_service.fix_entry(
            entry, BalanceContext.build(current_user.id),
            data["amount"], data["payday"], display_today(),
        )
    except ValidationError as refused:
        db.session.rollback()
        flash(str(refused), "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
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
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


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
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    profile.is_active = True
    if profile.template:
        profile.template.is_active = True

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="reactivate_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to reactivate salary profile %d",
            log_args=(current_user.id, profile_id),
            flash_message="Failed to reactivate salary profile. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d reactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' reactivated.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))
