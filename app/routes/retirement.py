"""
Shekel Budget App -- Retirement Planning Routes

Retirement dashboard with pension management, income gap analysis,
and retirement planning settings.
"""

import logging
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.utils.auth_helpers import (
    get_or_404,
    log_refused_lookup,
    require_owned_fk,
    require_owner,
)
from app.utils.db_errors import is_unique_violation
from app.utils.error_fragments import designed_error

from app.extensions import db
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.user import UserSettings
from app.routes._commit_helpers import STALE_EDITING_MESSAGE
from app.routes._recurrence_conflict_chooser import flash_retained_notice
from app.schemas.validation import (
    PensionProfileCreateSchema,
    PensionProfileUpdateSchema,
    RetirementReadinessQuerySchema,
    RetirementSettingsSchema,
)
from app.schemas.validation.retirement import (
    errors_by_rail_control,
    raise_end_control,
)
from app.services import (
    retirement_dashboard_service,
    retirement_levers,
    retirement_plan,
    retirement_readiness,
    salary_regeneration,
)
from app.services.balance_at import BalanceContext
from app.services.retirement_dashboard_service import RetirementRows
from app.services.salary_raises import EndYearError, end_year_of

logger = logging.getLogger(__name__)

# Field allowlists for the retirement update routes: which submitted form
# fields may be written back to each model via setattr.
_PENSION_FIELDS = {
    "salary_profile_id", "name", "benefit_multiplier",
    "consecutive_high_years", "hire_date",
    "earliest_retirement_date", "planned_retirement_date",
}
_SETTINGS_FIELDS = {
    "safe_withdrawal_rate", "planned_retirement_date",
    "estimated_retirement_tax_rate",
}

# The element the assumptions rail swaps into: ``dashboard.html`` renders it,
# every rail Save form targets it (``retirement/_assumptions.html``,
# ``hx-target``) and ``retirement_controls.js`` watches it for the post-save
# reload.  Spelled here because the readiness what-if's REFUSAL is the rail
# too, and that request targets the readiness card, so its 422 is retargeted
# at this selector (plan step salary:S3-f-4, ruling **R-SAL33**).  The four
# spellings are held together by
# ``tests/test_routes/test_retirement/test_the_rail_probes.py``'s
# ``TestTheRailRegionIsSpelledOnce``.
_RAIL_REGION = "#assumptions-region"

# Name of the composite unique constraint that backstops the
# pension-profile double-submit fix (F-105 / C-22).  Mirrors the
# literal in ``app/models/pension_profile.py:PensionProfile.__table_args__``
# and ``migrations/versions/<C-22 revision>.py``; renaming the
# constraint requires a coordinated edit across all three sites.
_PENSION_PROFILE_UNIQUE_CONSTRAINT = "uq_pension_profiles_user_name"

retirement_bp = Blueprint("retirement", __name__)

_pension_create_schema = PensionProfileCreateSchema()
_pension_update_schema = PensionProfileUpdateSchema()
_settings_schema = RetirementSettingsSchema()
_readiness_query_schema = RetirementReadinessQuerySchema()


@retirement_bp.route("/retirement")
@require_owner
def dashboard():
    """The direction-D retirement readiness page.

    Derives the retirement picture ONCE and shapes the readiness picture
    from it (:func:`~app.services.retirement_readiness.readiness_from_picture`);
    the levers run on the same loaded inputs and the same memoized picture.
    The context carries exactly what the rebuilt template consumes: the
    readiness dict, the lever baselines, the per-account projections +
    salary profiles for the accounts table, the blended return for the
    what-if-only assumed-return row, and the settings row the included
    assumptions rail echoes its stored values from (P4 live-verify
    defect: the P3c slim dropped ``settings``, so every rail input
    rendered its empty/fallback state, the stored SWR invisible).  The rail
    also reads the owner's active salary profiles, whose recurring raises it
    states the end year of (plan step salary:S3-c).  The legacy gap-table
    context (gap analysis, chart data, SWR slider default) retired with
    the old page (P3c).

    **This route opens the render's ONE read pass and LOADS its inputs once**
    (plan steps C2-f2d-1 and C2-f2d-2, ledger rows **P43** and **P57**).  The
    readiness verdict and the lever card are two views of ONE retirement
    picture: they belong to one owner, one baseline scenario, one day and one
    plan.  Each used to build its own pass AND its own loaded inputs AND its
    own derivation of that picture -- 86 of the render's 179 queries on a
    production clone were the second copy, and the lever card's month-0 probe
    recomputed the verdict the hero had already drawn.

    **What that buys, stated exactly.**  ``picture_at(inputs, STORED_PLAN)``
    below and the lever solver's own month-0 probe are the SAME object, not two
    equal ones, so the two cards cannot state different figures for one plan.
    **And this render is single-clock as of pay-calendar plan step C2-f2e**,
    which closed ledger row **P55**: ``compute_pension_summary``,
    ``compute_gap_net_biweekly`` and ``build_employer_salary_basis`` (deleted
    at plan step salary:S3-e-2) each took ``date.today().year`` for
    themselves, once per plan point over about ten probes, so a render
    crossing a NEW YEAR could project the verdict card's salary path from
    year N and the lever card's from N+1.  Both survivors take the pass's day
    now.  The gates are
    ``tests/test_arch/test_one_read_pass_per_render/`` for the pass and
    ``test_retirement_dashboard_service.TestTheRenderDayOpensTheSalaryPath``
    for the day.
    """
    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(current_user.id),
    )
    picture = retirement_plan.picture_at(inputs, inputs.stored_plan)
    readiness = retirement_readiness.readiness_from_picture(picture)
    return render_template(
        "retirement/dashboard.html",
        # The rail's "Assumed return" row: the rate this page's own projection
        # actually grew at, scaled to the percent the template formats.  It was
        # a third derivation of that rate until plan step C2-f2d-2.
        current_return=picture.blended_return * retirement_plan.PCT_SCALE,
        readiness=readiness,
        levers=retirement_levers.compute_lever_data(inputs),
        retirement_account_projections=picture.projections,
        salary_profiles=inputs.gap.salary_profiles,
        settings=inputs.gap.settings,
        date_provenance=readiness["date_provenance"],
        raise_assumptions=(
            retirement_dashboard_service
            .resolve_recurring_raise_assumptions(inputs.gap.salary_profiles)
        ),
    )


def _pension_date_errors(eff_hire, eff_earliest, eff_planned):
    """Cross-field pension date rules, shared by both pension-date writers.

    Extracted from ``update_pension`` (acceptance r2 item 2) so the
    assumptions rail's date row -- which now writes through to the
    owning pension -- enforces the SAME rules as the pension form
    instead of duplicating them: planned/earliest must fall after the
    hire date, the planned date must be in the future, and the planned
    date cannot precede the earliest retirement date when one is set.

    Args:
        eff_hire: The effective hire date (submitted or stored).
        eff_earliest: The effective earliest retirement date, or ``None``.
        eff_planned: The effective planned retirement date, or ``None``.

    Returns:
        dict mapping field name to a list of error messages; empty when
        every rule passes.
    """
    date_errors = {}
    if eff_earliest and eff_hire and eff_earliest <= eff_hire:
        date_errors.setdefault("earliest_retirement_date", []).append(
            "Must be after hire date."
        )
    if eff_planned and eff_hire and eff_planned <= eff_hire:
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be after hire date."
        )
    if eff_planned and eff_planned <= date.today():
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be in the future."
        )
    if eff_planned and eff_earliest and eff_planned < eff_earliest:
        date_errors.setdefault("planned_retirement_date", []).append(
            "Must be on or after earliest retirement date."
        )
    return date_errors


# ── Pension CRUD ─────────────────────────────────────────────────


@retirement_bp.route("/retirement/pension")
@require_owner
def pension_list():
    """List pension profiles."""
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    return render_template(
        "retirement/pension_form.html",
        pension=None,
        pensions=pensions,
        salary_profiles=salary_profiles,
    )


@retirement_bp.route("/retirement/pension", methods=["POST"])
@require_owner
def create_pension():
    """Create a new pension profile."""
    errors = _pension_create_schema.validate(request.form)
    if errors:
        pensions = (
            db.session.query(PensionProfile)
            .filter_by(user_id=current_user.id, is_active=True)
            .all()
        )
        salary_profiles = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=current_user.id, is_active=True)
            .all()
        )
        return render_template(
            "retirement/pension_form.html",
            pension=None,
            pensions=pensions,
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=errors,
        ), 422

    data = _pension_create_schema.load(request.form)
    require_owned_fk(SalaryProfile, data, "salary_profile_id")

    # F-17 / Commit 12: percent-to-fraction conversion happens in the
    # schema's @pre_load; ``benefit_multiplier`` arrives already
    # converted to its decimal-fraction storage form.
    pension = PensionProfile(user_id=current_user.id, **data)
    db.session.add(pension)
    try:
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-name double-submit (F-105 / C-22): the composite
        # unique ``uq_pension_profiles_user_name`` rejects the second
        # INSERT when the user clicks Save twice in a row.  Roll back
        # and treat as idempotent success: re-fetch the winning row
        # so the user lands on the retirement dashboard with the
        # pension they intended to create, regardless of which
        # request reached the database first.
        db.session.rollback()
        if not is_unique_violation(exc, _PENSION_PROFILE_UNIQUE_CONSTRAINT):
            raise
        existing = (
            db.session.query(PensionProfile)
            .filter_by(user_id=current_user.id, name=data["name"])
            .first()
        )
        if existing is None:
            # The winning row was deleted between the IntegrityError
            # and this lookup -- vanishingly unlikely.  Surface as a
            # warning and let the user retry.
            flash(
                "A pension profile with that name already exists.",
                "warning",
            )
            return redirect(url_for("retirement.dashboard"))
        logger.info(
            "Duplicate pension profile prevented; existing id=%d "
            "(idempotent success)", existing.id,
        )
        flash(f"Pension profile '{existing.name}' already exists.", "info")
        return redirect(url_for("retirement.dashboard"))

    logger.info("user_id=%d created pension profile %d", current_user.id, pension.id)
    flash(f"Pension profile '{pension.name}' created.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/edit")
@require_owner
def edit_pension(pension_id):
    """Display pension profile edit form."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )
    return render_template(
        "retirement/pension_form.html",
        pension=pension,
        pensions=[],
        salary_profiles=salary_profiles,
    )


@retirement_bp.route("/retirement/pension/<int:pension_id>", methods=["POST"])
@require_owner
def update_pension(pension_id):
    """Update a pension profile."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    # Context needed for error re-render (same as edit_pension GET).
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=True)
        .all()
    )

    errors = _pension_update_schema.validate(request.form)
    if errors:
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=errors,
        ), 422

    data = _pension_update_schema.load(request.form)
    require_owned_fk(SalaryProfile, data, "salary_profile_id")

    # F-17 / Commit 12: schema's @pre_load owns the percent-to-fraction
    # conversion; ``benefit_multiplier`` arrives already as a fraction.

    # Cross-field date validation: merge submitted values with existing
    # pension data so partial updates are validated against the full state.
    eff_hire = data.get("hire_date", pension.hire_date)
    eff_earliest = data.get("earliest_retirement_date", pension.earliest_retirement_date)
    eff_planned = data.get("planned_retirement_date", pension.planned_retirement_date)

    date_errors = _pension_date_errors(eff_hire, eff_earliest, eff_planned)
    if date_errors:
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors=date_errors,
        ), 422

    for field_name, value in data.items():
        if field_name in _PENSION_FIELDS:
            setattr(pension, field_name, value)

    try:
        db.session.commit()
    except IntegrityError as exc:
        # Name-collision rename (F-105 / C-22): renaming this profile
        # to a name another active pension already holds violates
        # ``uq_pension_profiles_user_name``.  Surface as a 422 with
        # the field-level error rather than crashing the request --
        # the user expects a form-level message, not a 500.
        db.session.rollback()
        if not is_unique_violation(exc, _PENSION_PROFILE_UNIQUE_CONSTRAINT):
            raise
        return render_template(
            "retirement/pension_form.html",
            pension=pension,
            pensions=[],
            salary_profiles=salary_profiles,
            form_data=dict(request.form),
            errors={"name": ["You already have a pension profile with this name."]},
        ), 422
    logger.info("user_id=%d updated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' updated.", "success")
    return redirect(url_for("retirement.dashboard"))


@retirement_bp.route("/retirement/pension/<int:pension_id>/delete", methods=["POST"])
@require_owner
def delete_pension(pension_id):
    """Deactivate a pension profile."""
    pension = get_or_404(PensionProfile, pension_id)
    if pension is None:
        abort(404)

    pension.is_active = False
    db.session.commit()
    logger.info("user_id=%d deactivated pension profile %d", current_user.id, pension_id)
    flash(f"Pension profile '{pension.name}' deactivated.", "info")
    return redirect(url_for("retirement.dashboard"))


# ── Readiness Fragment (P3a) ─────────────────────────────────────


@retirement_bp.route("/retirement/readiness")
@require_owner
def readiness_fragment():
    """HTMX fragment: readiness verdict with optional what-if overrides.

    Optional ``swr`` / ``return_rate``
    query parameters recompute the readiness picture as a what-if against
    the stored-settings baseline and return the panel's delta facts
    (funded-ratio delta in points, shortfall delta in dollars); optional
    ``months`` / ``contribution`` set where the two lever steppers sit; and
    since plan step salary:S3-f-2b the rail's per-raise end-year pairs
    (``raise_end_mode_<id>`` / ``raise_end_year_<id>``) probe how long each
    recurring raise is believed.  All validated through
    :class:`RetirementReadinessQuerySchema` (bounds -> 422 on garbage); a
    probe naming a raise that is not this owner's, or a year before the
    raise's own effective year, is refused by ``plan_with`` against the ROWS.
    Renders the minimal ``_readiness.html`` stub P3b restyles, with the income
    panel and the lever card as out-of-band siblings so every figure the
    request moved updates in one round trip.

    **Every refusal is the assumptions RAIL, re-rendered at 422 with each
    message on its control** (plan step salary:S3-f-4, ruling **R-SAL33**,
    closing ledger row **SAL-548**) -- the one 422 shape the rail's Save
    answers with, delivered by :func:`_refused_rail`.  Until that step both
    refusal tiers answered a JSON the panel never swapped in (htmx swaps no
    bare 4xx), so a year typed before a raise's effective year left the card
    silently at its previous picture.  This request targets the readiness
    CARD, so the rail is RETARGETED at its own region; the card keeps the last
    picture it drew, exactly as it does on a Save's refusal.  The raise rows
    and the SWR box render their refusals; a refused assumed return or lever
    stepper -- controls outside the rail -- still renders nowhere (known, an
    opportunity for a step that lets each region render its own), and so
    does a probe on an id the rail does not list (a stale page or a URL
    edit): it is keyed to that id's mode control, which no row carries, and
    the rail comes back showing the rows as they are.

    **ONE set of assumptions per response, since plan step C2-f2d-4.**  The
    verdict, the chart, the income meter and both levers are computed at the
    SAME :class:`~app.services.retirement_plan.PlanPoint`.  The panel's
    ``baseline`` is deliberately the STORED plan beside them -- stating the
    delta is its whole product -- and it is the one figure here that is not at
    *point*.

    **One read pass and one LOADER for the fragment** (plan steps C2-f2d-1 and
    C2-f2d-2), built here and shared by the what-if's two pictures and by the
    levers beside them.  This request published up to three pictures from three
    passes and three loads -- the stored-settings baseline, the override, and
    the lever outcome -- and the panel's whole purpose is to state the DELTA
    between the first two, so every input the halves share had better BE
    shared.  They are, and since pay-calendar plan step C2-f2e that includes the
    DAY: the three producers that opened a salary path from
    ``date.today().year`` take the pass's ``as_of``, which closed row **P55**.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("retirement.dashboard"))

    ctx = BalanceContext.build(current_user.id)
    try:
        query_data = _readiness_query_schema.load(request.args)
    except ValidationError as exc:
        # A field-tier refusal answers before the picture's inputs are loaded;
        # the rail's three rows come through the pass all the same.
        return _refused_rail(
            retirement_dashboard_service.load_retirement_rows(ctx),
            errors_by_rail_control(exc.messages),
        )

    inputs = retirement_plan.load_retirement_inputs(ctx)
    # RESOLVED against the owner's settings and rows, not carried as
    # overrides: a saveable rail input is pre-filled with the stored value and
    # so submits it on every request, and an override that equals the stored
    # value is the stored plan.  ``plan_with`` is the door that makes those
    # one key -- and the one that refuses a probe on a raise this owner does
    # not have, which is why that refusal is caught here and not in the
    # schema: the schema has no rows to check against.
    try:
        point = inputs.plan_with(
            swr_override=query_data.get("swr"),
            return_rate_override=query_data.get("return_rate"),
            raise_probes=query_data.get("raise_probes"),
        )
    except retirement_plan.RaiseProbeError as exc:
        # The pass's bundle IS the rail's rows (plus the cadence); a second
        # load of them here would be the redundant read the pass exists to
        # remove.
        return _refused_rail(inputs.gap, _refused_probes_by_control(exc.errors))
    whatif = retirement_readiness.compute_readiness_whatif(inputs, point)
    return render_template(
        "retirement/_readiness.html",
        readiness=whatif["readiness"],
        baseline=whatif["baseline"],
        deltas=whatif["deltas"],
        # SOLVED AGAINST *point*, and computed on EVERY refresh (plan step
        # C2-f2d-4, ledger row P59).  Both halves of that changed together and
        # neither works alone: the levers used to ignore the what-if sliders
        # entirely, so this card stated the stored-settings plan beside a hero
        # stating the what-if -- two funded ratios on one screen, measured
        # differently, with no caption saying so.  And they used to be computed
        # only when a STEPPER moved, so refreshing them on a slider move is
        # what stops the card going stale in the newly-visible way.
        levers=retirement_levers.compute_lever_data(
            inputs, point,
            contribution_override=query_data.get("contribution"),
            months_override=query_data.get("months"),
        ),
    )


# ── The assumptions rail ─────────────────────────────────────────


def _render_rail(rows: RetirementRows, errors, form_data) -> str:
    """Render the assumptions rail with freshly resolved provenance.

    The ONE renderer of ``retirement/_assumptions.html`` outside the
    dashboard's include: the settings route's every answer and the readiness
    GET's refusals (ruling **R-SAL33**) all come through here, so the rail
    cannot be rendered two ways.  Returns the body only; the error callers
    wrap it in :func:`~app.utils.error_fragments.designed_error` so the
    fragment swaps despite the status.

    Args:
        rows: The owner's :class:`~app.services.retirement_dashboard_service
            .RetirementRows` -- loaded for the request, or the read pass's
            :class:`~app.services.retirement_dashboard_service.GapInputs`,
            which extends it.
        errors: ``{control name: [messages]}``, or ``None`` for a clean rail.
        form_data: The submitted payload to echo, or ``None`` to state every
            stored value.

    Returns:
        The rendered fragment.
    """
    return render_template(
        "retirement/_assumptions.html",
        settings=rows.settings,
        form_data=form_data,
        errors=errors,
        date_provenance=(
            retirement_dashboard_service
            .resolve_retirement_date_provenance(rows.pensions, rows.settings)
        ),
        raise_assumptions=(
            retirement_dashboard_service
            .resolve_recurring_raise_assumptions(rows.salary_profiles)
        ),
    )


def _refused_rail(rows: RetirementRows, errors):
    """The readiness what-if's refusal: the rail at 422, retargeted.

    The same body the rail's Save answers a refusal with -- the rail with
    each message on its control and the request's own values echoed -- sent
    to the rail's region rather than to the readiness card this request
    targets (ruling **R-SAL33**).  The echo is the query string: every rail
    what-if control rides on every refresh (``hx-include`` in
    ``dashboard.html``), so every raise row and the SWR box re-state what is
    on screen, and the rows the GET does not carry (the date, the tax rate)
    state their stored values, as on any rail 422.

    Args:
        rows: See :func:`_render_rail`.
        errors: ``{control name: [messages]}``.

    Returns:
        The designed 422, retargeted at :data:`_RAIL_REGION`.
    """
    return designed_error(
        _render_rail(rows, errors, dict(request.args)), 422, retarget=_RAIL_REGION,
    )


def _refused_probes_by_control(
    refusals: dict[int, tuple[str | None, str]],
) -> dict[str, list[str]]:
    """Key each refused end-year pair to the rail control it belongs to.

    The one spelling of where a raise row's refusal lands: a refusal of one
    HALF of the pair (:class:`~app.services.salary_raises.EndYearError`'s
    ``field``) on that half's control, and a refusal of the pair as a whole
    -- no half named -- on the row's first control, the mode select.  Both
    doors that resolve the pair against the rows report through it: the
    probe's :class:`~app.services.retirement_plan.RaiseProbeError` and the
    Save's :func:`_resolve_end_year_saves`, and the Save's failure arms key
    their one sentence per row the same way.

    Args:
        refusals: ``{raise_id: (field, message)}`` -- *field* ``"mode"`` or
            ``"year"``, or ``None`` for the whole pair.

    Returns:
        ``{control name: [message]}``.
    """
    return {
        raise_end_control(raise_id, field or "mode"): [message]
        for raise_id, (field, message) in refusals.items()
    }


# ── Retirement Settings ──────────────────────────────────────────


def _resolve_end_year_saves(
    raise_probes: dict[int, tuple[str, int | None]],
    salary_profiles: list[SalaryProfile],
) -> tuple[list[tuple[SalaryRaise, int | None]], dict[str, list[str]]]:
    """Resolve the rail's posted end-year pairs against the rows the rail lists.

    The Save's half of what :meth:`~app.services.retirement_plan
    .RetirementInputs.plan_with` does for the probe (plan step salary:S3-f-3,
    ruling **R-SAL22**), on the same two leaves: the membership walk
    :func:`~app.services.retirement_dashboard_service.recurring_raises` -- a
    pair is resolved against THIS owner's rows, never by querying the
    submitted id, so a foreign id has no row to be graded against -- and the
    ONE end-year rule :func:`~app.services.salary_raises.end_year_of` against
    the row's effective year.  Where the two doors differ is the refusal a
    non-member earns: the probe answers a designed 422 keyed to a control no
    row renders (a what-if reads nothing it should not, and the re-rendered
    rail shows the rows as they are), and this is a WRITE, so an id the rail
    does not list (missing, another owner's, a one-time raise, an archived
    profile's) is the project's 404 for "not found" and "not yours" alike,
    with :func:`~app.utils.auth_helpers.log_refused_lookup` leaving the trail
    a cross-user probe must leave.

    Args:
        raise_probes: ``{raise_id: (mode, year)}`` off the settings schema --
            every pair the POST carried; a rail row's form carries one.
        salary_profiles: The owner's active profiles, the rail's set.

    Returns:
        ``(writes, errors)``: *writes* is one ``(row, terminal_year)`` per
        pair the rule accepted; *errors* is ``{control name: [message]}`` for
        every pair it refused, keyed the way the rail keys every field error
        so the fragment renders each on its own control.  A non-empty
        *errors* means the caller writes nothing.

    Raises:
        werkzeug.exceptions.NotFound: (via ``abort(404)``) when a pair names
            a raise the rail does not list.
    """
    by_id = {
        row.id: row
        for row in retirement_dashboard_service.recurring_raises(salary_profiles)
    }
    writes: list[tuple[SalaryRaise, int | None]] = []
    refusals: dict[int, tuple[str | None, str]] = {}
    for raise_id, (mode, year) in raise_probes.items():
        row = by_id.get(raise_id)
        if row is None:
            log_refused_lookup("SalaryRaise", raise_id)
            abort(404)
        try:
            writes.append((row, end_year_of(mode, year, row.effective_date.year)))
        except EndYearError as exc:
            refusals[raise_id] = (exc.field, exc.message)
    return writes, _refused_probes_by_control(refusals)


def _pension_date_write(
    data: dict, pensions: list[PensionProfile], settings: UserSettings,
) -> dict[str, list[str]]:
    """Write a submitted planned date through to the pension that owns it.

    The date row's write-through arm (acceptance r2 item 2, developer
    ruling), lifted out of :func:`update_settings` when the raise arm joined
    it.  When a pension owns the resolved date, a submitted
    ``planned_retirement_date`` updates that owning (max-date) pension --
    enforcing the pension form's own cross-field rules via the shared
    :func:`_pension_date_errors` -- and never the settings column, which is
    why the key is POPPED from *data* on the write; otherwise nothing here
    applies and the settings save takes the key.

    Args:
        data: The loaded settings payload; mutated on the write.
        pensions: The owner's active pensions.
        settings: The owner's settings row.

    Returns:
        The pension rules' errors, keyed by field; empty when the arm did not
        apply or the date was written.
    """
    provenance = (
        retirement_dashboard_service.resolve_retirement_date_provenance(
            pensions, settings,
        )
    )
    if ("planned_retirement_date" not in data
            or provenance["source"] != "pension"):
        return {}
    # The schema already enforced must-be-future (M1); the shared pension
    # rules add after-hire and earliest-date constraints against the
    # OWNER's stored fields, exactly as the pension form would.
    owner = next(p for p in pensions if p.id == provenance["pension_id"])
    pension_errors = _pension_date_errors(
        owner.hire_date,
        owner.earliest_retirement_date,
        data["planned_retirement_date"],
    )
    if pension_errors:
        return pension_errors
    owner.planned_retirement_date = data.pop("planned_retirement_date")
    return {}


def _apply_end_year_writes(
    end_year_writes: list[tuple[SalaryRaise, int | None]], ctx: BalanceContext,
) -> list[int]:
    """Stage each resolved end year on its row and regenerate its profile.

    The write is the column on ``salary.salary_raises``; what follows it is
    what EVERY raise write is followed by (ruling **R-SAL24** put that walk
    below both routes): the request's ONE read pass, handed down -- a
    producer below the route takes the pass; only a route builds one -- and
    one regeneration per profile whose raise moved.  Flushes; the caller
    commits, catches the two failures the flushes can raise, and only THEN
    tells the owner what the regeneration kept (plan step R10-a) -- a notice
    flashed before the commit would survive a rollback and render, on the
    reload it asks for, for a write that never happened (an adversarial
    review of this step).

    Args:
        end_year_writes: ``[(row, terminal_year)]`` from
            :func:`_resolve_end_year_saves`; empty stages nothing.
        ctx: The request's read pass -- the one the route loaded the rail's
            rows through.

    Returns:
        The ids of the rows the regeneration declined to touch, across every
        profile it ran for; empty when nothing was written or nothing kept.
    """
    if not end_year_writes:
        return []
    for row, terminal_year in end_year_writes:
        row.terminal_year = terminal_year
    profiles = {row.salary_profile.id: row.salary_profile for row, _ in end_year_writes}
    retained: list[int] = []
    for profile in profiles.values():
        retained.extend(
            salary_regeneration.regenerate_salary_transactions(ctx, profile),
        )
    return retained


def _commit_with_end_year_writes(end_year_writes, ctx, rail_response):
    """Commit the request's writes, reporting the raise write's two failures.

    The settings and pension writes are staged already; the raise write is
    staged and regenerated here (:func:`_apply_end_year_writes`), inside the
    one ``try`` with the commit, because the regeneration flushes and either
    flush can raise.  Nothing else in the route carries a version counter or
    flushes a money row, so both arms are the raise write's: a stale race
    rolls back and re-renders the rail FRESH at 409 with the conflict on
    each row that was being written (the designed-conflict shape every
    fragment route answers with; the 409 swaps unconditionally and the
    post-save reload skips it); any other DB-tier failure rolls back and
    answers a designed 500 on the same rows, the fragment-shaped twin of the
    salary package's ``handle_db_error`` arm -- the service re-raises so the
    route's own handler reports it (C-46 / F-145), and an unhandled 500 here
    is a click that did nothing and said nothing (htmx swaps no bare error
    document).  On success the rows the regeneration kept are flashed, AFTER
    the commit, for the reload to render.

    Args:
        end_year_writes: ``[(row, terminal_year)]`` from
            :func:`_resolve_end_year_saves`; empty for a settings-only save.
        ctx: The request's read pass, for the regeneration.
        rail_response: The route's ``(errors, form_data) -> body`` renderer.

    Returns:
        ``None`` on a clean commit; otherwise the failure response.
    """
    # Read BEFORE the write is attempted: after a rollback every loaded row
    # is expired, and a raise deleted under this request would refresh to
    # nothing rather than to its id.
    written_raise_ids = [row.id for row, _ in end_year_writes]
    try:
        retained = _apply_end_year_writes(end_year_writes, ctx)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        logger.info(
            "Stale-data conflict on update_settings raise ids=%s", written_raise_ids,
        )
        return rail_response(
            _on_written_rows(written_raise_ids, STALE_EDITING_MESSAGE.format(noun="raise")),
            None,
        ), 409
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "user_id=%d failed to save raise end years %s",
            current_user.id, written_raise_ids,
        )
        return designed_error(rail_response(
            _on_written_rows(
                written_raise_ids, "Failed to save this end year. Please try again.",
            ),
            None,
        ), 500)
    flash_retained_notice(retained)
    return None


def _on_written_rows(raise_ids: list[int], message: str) -> dict[str, list[str]]:
    """*message* keyed to the mode control of every raise in *raise_ids*.

    The failure arms of the raise write report one sentence per row that was
    being written, on the row's first control, so the fragment renders it
    where the rail renders every other field error.

    Args:
        raise_ids: The ids captured before the write was attempted.
        message: The sentence the owner reads.

    Returns:
        ``{control name: [message]}``.
    """
    return _refused_probes_by_control({raise_id: (None, message) for raise_id in raise_ids})


@retirement_bp.route("/retirement/settings", methods=["POST"])
@require_owner
def update_settings():
    """Save retirement assumptions (per-field capable; P3a).

    The assumptions panel posts ONE field per save; a multi-field submit
    validates through the same all-optional schema.  Responses are
    fragment-shaped for the panel: a validation failure renders the
    ``_assumptions.html`` stub with field errors and the echoed input at
    422 (fragment-friendly for both HTMX and plain posts); success
    renders the refreshed panel for an HTMX request and falls back to a
    flash + redirect to the retirement page otherwise.

    Date write-through (acceptance r2 item 2, developer ruling): the date
    row is always editable and Save writes to the RESOLVED owner.  When a
    pension owns the resolved date, a submitted
    ``planned_retirement_date`` updates that owning (max-date) pension --
    enforcing the pension form's own cross-field rules via the shared
    :func:`_pension_date_errors` -- and never the settings column;
    otherwise the settings save applies unchanged.  Writes go through the
    ORM so the audited-table triggers capture them.

    **Raise end-year write-through** (plan step salary:S3-f-3, ruling
    **R-SAL22**), the second such arm: a recurring-raise row's Save posts
    the same ``raise_end_mode_<id>`` / ``raise_end_year_<id>`` pair its
    what-if sends, the schema gathers it as the readiness query does, and
    :func:`_resolve_end_year_saves` grades it against the ROW -- 404 for an
    id the rail does not list, a designed 422 with the message on that row's
    control for an answer the ONE end-year rule refuses.  The write is the
    column on ``salary.salary_raises`` (audit triggers, the row's optimistic
    lock), and it is followed by what EVERY raise write is followed by:
    :func:`~app.services.salary_regeneration.regenerate_salary_transactions`
    on the raise's profile (ruling **R-SAL24** put that walk below both
    routes), so the template's per-paycheck amount and the row set the
    salary page would leave behind are the ones this door leaves behind.
    A stale race in that write -- the raise, its template or its rows
    changed under the request -- rolls back and re-renders the rail FRESH
    with the conflict on the row at 409, the designed-conflict shape every
    fragment route answers with; nothing else in this route carries a
    version counter, so a :class:`StaleDataError` here is that write's.  Any
    other DB-tier failure of the same write rolls back and answers a designed
    500 on the row, the fragment-shaped twin of the salary package's
    ``handle_db_error`` arm.  **The rail row carries NO version counter**
    (developer ruling 2026-09-12): a one-column Save from a rail rendered
    before the same raise was edited elsewhere is last-write-wins on that
    column -- the owner typed the year while looking at the row, and the rail
    re-renders fresh after every Save -- where the salary form's whole-payload
    submit refuses the race with a version pre-check; the flush-time lock
    above still catches a true race.
    """
    # Preserve original user input for form re-display on error.
    raw_form_data = dict(request.form)

    # The request's ONE read pass: the rail's rows come through it (the same
    # loader the picture's inputs come through), and the raise arm's
    # regeneration takes it below.  The date row's provenance decides the
    # write-through target AND how the re-rendered rail captions the row,
    # and the raise rows are the rail's (plan step salary:S3-c); both are
    # resolved per render below -- the success branch must see the POST-save
    # state.
    ctx = BalanceContext.build(current_user.id)
    rows = retirement_dashboard_service.load_retirement_rows(ctx)
    settings, pensions, salary_profiles = rows.settings, rows.pensions, rows.salary_profiles

    def rail_response(rail_errors, form_data):
        """This request's rail, over the rows loaded above."""
        return _render_rail(rows, rail_errors, form_data)

    # F-17 / Commit 12: percent-to-fraction conversion is owned by the
    # schema's @pre_load (RetirementSettingsSchema._PERCENT_FIELDS); the
    # route forwards the raw form payload and reads back the loaded
    # fractions directly.
    errors = _settings_schema.validate(request.form)
    if errors:
        # Designed fragment: the rail re-rendered with field errors.
        # The marker header opts the 422 back into swapping; replaces
        # the swap shim that lived in retirement_controls.js.  A refused
        # raise pair is reported per control, as every other rail error is.
        return designed_error(
            rail_response(errors_by_rail_control(errors), raw_form_data), 422,
        )

    if settings is None:
        flash("Settings not found.", "danger")
        return redirect(url_for("retirement.dashboard"))

    data = _settings_schema.load(request.form)

    end_year_writes: list[tuple[SalaryRaise, int | None]] = []
    rail_errors: dict[str, list[str]] = {}
    if "raise_probes" in data:
        end_year_writes, rail_errors = _resolve_end_year_saves(
            data.pop("raise_probes"), salary_profiles,
        )
    if not rail_errors:
        rail_errors = _pension_date_write(data, pensions, settings)
    if rail_errors:
        return designed_error(rail_response(rail_errors, raw_form_data), 422)

    for field_name, value in data.items():
        if field_name in _SETTINGS_FIELDS:
            setattr(settings, field_name, value)
    failure = _commit_with_end_year_writes(end_year_writes, ctx, rail_response)
    if failure is not None:
        return failure
    logger.info("user_id=%d updated retirement settings", current_user.id)

    if request.headers.get("HX-Request"):
        return rail_response(None, None)
    flash("Retirement settings updated.", "success")
    return redirect(url_for("retirement.dashboard"))
