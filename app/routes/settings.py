"""
Shekel Budget App -- Settings Routes

User preferences management (grid defaults, inflation rate, etc.).
Settings dashboard consolidating all configuration sections.
"""

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import ref_cache, tax_law
from app.enums import RoleEnum
from app.exceptions import RequiredRecordMissing
from app.utils.auth_helpers import fresh_login_required, require_owner
from app.utils.digit_strings import parse_row_id

from app.extensions import db
from app.models.account import Account
from app.schemas.validation import (
    CompanionCreateSchema,
    CompanionEditSchema,
    UserSettingsSchema,
    rhythm_to_wire,
)
from app.models.category import Category
from app.models.ref import AccountType, AccountTypeCategory
from app.models.trusted_device import TRUST_LIFETIME
from app.models.user import MfaConfig, User, UserSettings
from app.services import (
    account_resolver,
    account_service,
    pay_period_gates,
    pay_period_locks,
    pay_schedule_service,
    trusted_device_service,
)
from app.services.auth_service import hash_password
from app.services.pay_calendar import calendar_at_schedule
from app.utils.browser_label import browser_label
from app.utils.dates import display_today
from app.utils.trusted_device_cookie import device_token

logger = logging.getLogger(__name__)

settings_bp = Blueprint("settings", __name__)

_VALID_SECTIONS = [
    "general", "categories", "pay-periods", "tax", "account-types",
    "companions", "security",
]


# The scalar UserSettings fields the POST handler copies straight from the
# validated form.  All are storage-domain values: UserSettingsSchema's
# ``@pre_load`` already divided ``default_inflation_rate`` from form percent
# into the [0, 1] decimal fraction the DB CHECK enforces (E-28 / HIGH-06 /
# PA-02, Commit 24), so the route stores them verbatim.  Zero is a valid
# value (E-12 "zero is a value"), so each field is applied on
# ``is not None``, never on truthiness.  ``default_grid_account_id`` is
# deliberately NOT in this list -- it needs a route-level ownership check.
_SIMPLE_SETTINGS_FIELDS = (
    "grid_default_periods",
    "default_inflation_rate",
    "low_balance_threshold",
    "large_transaction_threshold",
    "anchor_staleness_days",
)

# Bootstrap icon classes offered when creating/editing a custom account type.
# Immutable static data, so it is a module-scope tuple rather than a list rebuilt
# per request (the template only iterates it -- it is never mutated).
_ACCOUNT_TYPE_ICON_CHOICES = (
    "bi-bank", "bi-wallet2", "bi-piggy-bank", "bi-cash-stack",
    "bi-safe", "bi-heart-pulse", "bi-credit-card", "bi-house",
    "bi-car-front", "bi-mortarboard", "bi-cash-coin",
    "bi-graph-up-arrow", "bi-bar-chart-line", "bi-building",
    "bi-briefcase", "bi-coin", "bi-currency-exchange",
)

# Pay-period lock-reason -> (chip label, flag-chip CSS classes) for the
# manage UI.  This display-only mapping lives in the route layer so the
# template renders a precomputed chip instead of branching on the enum.
# Chip semantics (S11 settings retheme; the raw Bootstrap badges were
# off-palette, polish audit RC3): Editable is the actionable state and
# rides the accent chip; Settled / Posted mean hands-off because money
# moved (the D11 caution role, amber); the remaining lock reasons are
# informational and stay neutral.  Labels, not color, tell rows apart.
#
# It carried an "Anchor" chip until plan step X-f1c3c (ruling R-EO): a period
# an account's balance anchor pointed at.  Neither an account nor a balance
# ASSERTION references a pay period any more, so the reason is unreachable and
# is deleted rather than left as a chip nothing can render.  The map is TOTAL
# over the enum and subscripted, so a member added without a chip fails the
# render rather than showing a blank badge -- which is what makes deleting the
# member and the chip together the only safe order.
_PP_LOCK_BADGES = {
    pay_period_locks.PeriodLockReason.HISTORICAL: (
        "Past", "flag-chip flag-chip--neutral",
    ),
    pay_period_locks.PeriodLockReason.SETTLED_TXN: (
        "Settled", "flag-chip flag-chip--warning",
    ),
    # Money moved, so the caution role (plan step credit_card:CC-5-4a-4; the
    # label is ruling R-CC66's, verbatim).
    pay_period_locks.PeriodLockReason.HOLDS_MOVEMENT: (
        "Holds a recorded payment or purchase", "flag-chip flag-chip--warning",
    ),
    pay_period_locks.PeriodLockReason.LEDGER_POSTINGS: (
        "Posted", "flag-chip flag-chip--warning",
    ),
}
_PP_MUTABLE_BADGE = ("Editable", "flag-chip")


@settings_bp.route("/settings", methods=["GET"])
@require_owner
def show():
    """Display the settings dashboard."""
    section = request.args.get("section", "general")
    # Gate A ruling 6 (retirement rebuild P3a): the Settings > Retirement
    # section retired -- the assumptions panel on /retirement is the
    # persistent home for SWR, the planned retirement date and the estimated
    # retirement tax rate.  (It held the merit-raise horizon too until plan
    # step salary:S3-c deleted that setting -- ruling **R-SAL11**: a raise's
    # end year is a fact on the raise.)  Old bookmarks and links land on the
    # retirement page.
    if section == "retirement":
        return redirect(url_for("retirement.dashboard"))
    if section not in _VALID_SECTIONS:
        section = "general"
    return render_settings_dashboard(section)


def _section_context(section):
    """Per-section template data for the settings dashboard.

    Returns only the active section's slice; the caller overlays it on
    the full default context so the template's "all keys always supplied"
    contract holds.  An unknown section falls through to an empty dict.
    """
    loaders = {
        "general": lambda: {
            # The Default Grid Account picker offers only grid-eligible
            # accounts (ruling D4 / step A1: the grid refuses an
            # amortizing loan, so the picker must never offer one).
            "accounts": account_resolver.list_grid_accounts(current_user.id),
        },
        "categories": _load_categories_context,
        "tax": _load_tax_context,
        "account-types": _load_account_types_context,
        "companions": lambda: _load_companions_context(request.args.get("edit")),
        "security": _load_security_context,
        "pay-periods": lambda: _load_pay_periods_context(current_user.id),
    }
    loader = loaders.get(section)
    return loader() if loader is not None else {}


def render_settings_dashboard(section, extra=None, status=200):
    """Render the settings dashboard for one section.

    The single render path shared by :func:`show` and the pay-period
    management routes (``app/routes/pay_periods.py``), so the full
    template context (every section's keys plus the active section's data)
    is assembled in one place.  ``extra`` overlays error / confirm
    context; ``status`` lets a route re-render with a 422 (e.g. the
    discard-confirm panel) instead of redirecting.

    **It used to AUTO-CREATE the owner's settings row, and plan step
    balance:X-i3 deleted that branch** -- the third instance of one shape, after
    the two on the account detail pages.  This is a render, shared by a GET and
    by POST re-renders, and a render that repairs data is a write inside a read:
    it cost ``/settings`` the one snapshot the figures on it are computed
    against, and no ``write_transaction`` block could declare it here, because
    the same function serves commands where such a block is meaningless.

    Every door that creates a user writes the row -- registration
    (``registration_service.register_user``), the companion invite below, and
    ``scripts/seed_companion.py`` -- so a missing one means the data was
    changed outside the application.  The two POST handlers that also
    create-if-missing keep doing so: a write door may author what it writes,
    which is the standing this render does not have.  Measured before the
    branch was deleted: **0 of 2 production users** without a settings row.

    Args:
        section: The active settings section.
        extra: Optional dict overlaid on the context after the section
            data (errors, a discard-confirm payload, etc.).
        status: HTTP status for the rendered response.

    Returns:
        A Flask ``(body, status)`` response tuple.

    Raises:
        RequiredRecordMissing: The owner carries no ``auth.user_settings`` row.
    """
    settings = current_user.settings
    if settings is None:
        raise RequiredRecordMissing(
            f"user {current_user.id} carries no auth.user_settings row, so "
            f"there are no preferences to render a settings page from. Every "
            f"door that creates a user writes one -- registration, and the "
            f"companion invite -- so reaching this means the row was removed "
            f"outside the application. POST /settings rebuilds it, but the "
            f"form that posts there is on the page this refusal replaces, so "
            f"there is no path to it through the browser: repair the row "
            f"directly."
        )

    context = _empty_section_context()
    context.update(_section_context(section))
    if extra:
        context.update(extra)

    return render_template(
        "settings/dashboard.html",
        active_section=section,
        settings=settings,
        **context,
    ), status


def _load_pay_periods_context(user_id):
    """Load the pay-periods section: the period list with lock badges.

    Each row carries a :class:`~app.services.pay_calendar.DerivedPeriod`,
    whether it is ``locked`` (immutable), and the display badge text/CSS the
    manage UI renders -- the lock-reason-to-badge mapping is resolved here
    so the template only displays, never computes.  ``pp_schedule`` is the
    persisted schedule (cadence) when one exists.  ``pp_can_reset`` gates
    the full-reset control to users with no settled, non-deleted row or
    transfer and none holding a payment or purchase (the same bounds the
    service enforces), so the destructive action is only offered when it
    would be accepted.

    **The list is the DERIVATION since plan step C2-f3b**, where it was
    ``pay_period_service.get_all_periods`` -- ORM rows ordered by the stored
    ``period_index`` and labelled from the stored ``end_date``, both of which
    plan step **C4-c** dropped.  It matters beyond that step: this page renders the
    ordinal and the date range that the four destructive doors beside it then
    decide against, so a row shown here and the row truncate deletes have to be
    described by one derivation or the confirmation the user gives is about a
    different schedule from the one that changes.

    ``display_today`` rather than ``date.today``: the HISTORICAL badge says
    whether a paycheck is behind the OWNER, and the process clock is the
    container's (finding **balance:N-191**, ruled 2026-08-19).

    **THIS SECTION IS THE REPAIR, so it may not REQUIRE what it repairs**
    (plan step ``pay_calendar:C4-d``, ruling **R-PC45**).  It read
    ``calendar_for(user_id)`` unconditionally, which was harmless while that
    door answered an EMPTY calendar for an owner with no
    ``budget.pay_schedule`` row.  That door refuses them now -- and
    ``pay_periods.generate_form`` redirects HERE, and
    ``errors/no_pay_calendar.html`` offers exactly that link -- so the
    recovery page's own repair looped straight back to the recovery page.  An
    adversarial review of this step drove it and measured the loop; the
    template's promise that "it carries the REPAIR" was false for the one
    owner it is written for.

    So this is a SOFT-door caller by definition: it resolves the schedule ROW
    and derives a calendar only when there is one to derive from.  Every door
    that PUBLISHES a per-paycheck figure still refuses that owner, which is the
    ruling; the door that CREATES their schedule cannot.

    **It also collapses a redundant read.**  The row is fetched ONCE and both
    answers come off it -- the calendar through
    :func:`~app.services.pay_calendar.calendar_at_schedule`, and ``pp_schedule``
    itself.  Before, ``calendar_for`` read the schedule and ``get_schedule``
    read it again for the same render, which is the per-render duplicate ledger
    rows **P68** and **P69** record.

    Returns:
        The section's context.  ``pp_periods`` is empty and ``pp_schedule`` is
        ``None`` for an owner with no schedule row -- the state the generate
        form exists to leave, and one the template already renders (it guards
        every ``pp_schedule`` read).  ``pp_rhythm`` is the owner's latest
        era's rhythm in the WIRE spelling the schedule forms post
        (:func:`~app.schemas.validation._pay_rhythm.rhythm_to_wire`), or
        ``None`` for an owner holding no era, which since plan step
        ``pay_calendar:C17-a`` is also the owner who gets no period rows: a
        calendar derives from eras.
    """
    schedule = pay_schedule_service.get_schedule(user_id)
    facts = (
        None if schedule is None
        else pay_schedule_service.ScheduleFacts.of(schedule)
    )
    period_rows = []
    if facts is not None:
        calendar = calendar_at_schedule(user_id, facts)
        locks = pay_period_locks.classify_schedule_locks(
            calendar, as_of=display_today(),
        )
        for period in calendar.saved():
            reason = locks[period.period_id]
            badge_label, badge_class = _PP_LOCK_BADGES.get(
                reason, _PP_MUTABLE_BADGE,
            )
            period_rows.append({
                "period": period,
                "locked": reason is not None,
                "badge_label": badge_label,
                "badge_class": badge_class,
            })
    return {
        "pp_periods": period_rows,
        "pp_schedule": schedule,
        # The LATEST era's rhythm, for the three forms that preselect the
        # owner's payday convention (plan step pay_calendar:C17-a) and,
        # since plan step C17-d-3, their cadence KIND and its inputs: the
        # rhythm is an era's fact, and the schedule row no longer carries
        # one.  In the WIRE spelling rather than as the value or the row,
        # because a form control is keyed on what it posts -- the
        # convention's id, the kind's token, the arm's numbers -- and the
        # schema's own inverse is the one place that spelling is produced.
        # *It was the era ROW (``pp_era``) until C17-d-3, read for its
        # ``shift_id`` alone.*
        "pp_rhythm": (
            rhythm_to_wire(facts.rhythm) if facts is not None else None
        ),
        "pp_can_reset": pay_period_gates.can_reset_pay_periods(user_id),
    }


@settings_bp.route("/settings", methods=["POST"])
@require_owner
def update():
    """Update user settings."""
    schema = UserSettingsSchema()
    errors = schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="general"))

    data = schema.load(request.form)

    settings = current_user.settings
    if settings is None:
        settings = UserSettings(user_id=current_user.id)
        db.session.add(settings)

    # Copy the schema-validated scalar settings (the allowlist + the
    # storage-domain conversion rationale live on _SIMPLE_SETTINGS_FIELDS).
    for field in _SIMPLE_SETTINGS_FIELDS:
        if field in data and data[field] is not None:
            setattr(settings, field, data[field])

    # Account ownership check stays in the route (can't be in schema).
    if "default_grid_account_id" in data:
        acct_id = data["default_grid_account_id"]
        if acct_id is None:
            settings.default_grid_account_id = None
        else:
            acct = db.session.get(Account, acct_id)
            if acct and acct.user_id == current_user.id and acct.is_active:
                settings.default_grid_account_id = acct_id
            else:
                flash("Invalid grid account.", "danger")
                return redirect(url_for("settings.show", section="general"))

    db.session.commit()
    flash("Settings updated.", "success")
    return redirect(url_for("settings.show", section="general"))


def _empty_section_context():
    """Return the dashboard template's full keyword context at defaults.

    ``settings.show`` (and the companion-form re-render) always supply every
    section's template variable so the dashboard partial can reference any of
    them regardless of the active section; the active section then overrides
    only its own slice.  Keeping the defaults in one place stops these keys
    from drifting between the GET handler and the companion-form re-render.
    """
    return {
        "errors": {},
        "accounts": [],
        "grouped": {},
        "tax_years": (),
        "account_types": [],
        "types_in_use": set(),
        "categories": [],
        "archived_categories": [],
        "group_names": [],
        "icon_choices": [],
        "mfa_enabled": False,
        # Not a default but the one value: how long a trusted device stays
        # trusted, which the Security section states in its sentences.
        "trust_days": TRUST_LIFETIME.days,
        "trusted_devices": [],
        "active_companions": [],
        "inactive_companions": [],
        "edit_companion_id": None,
        "form_values": {},
        "pp_periods": [],
        "pp_schedule": None,
        "pp_confirm": None,
        "pp_can_reset": False,
    }


def _load_categories_context():
    """Load the categories section: grouped active categories + the archive.

    Returns ``grouped`` (group_name -> [category]), the sorted distinct
    ``group_names`` for the add/edit dropdown (5A.4-2), and the
    ``archived_categories`` list.
    """
    all_categories = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.sort_order, Category.group_name, Category.item_name)
        .all()
    )
    active_categories = [c for c in all_categories if c.is_active]
    grouped = {}
    for cat in active_categories:
        grouped.setdefault(cat.group_name, []).append(cat)
    return {
        "grouped": grouped,
        "group_names": sorted(set(cat.group_name for cat in active_categories)),
        "archived_categories": [c for c in all_categories if not c.is_active],
    }


def _load_tax_context():
    """Load the tax section: the tax law the app prices against, newest year first.

    READ-ONLY since plan step salary:X-at-1 (ruling **R-SAL74**).  The law has
    one home, :mod:`app.tax_law`, which a release edits; this section shows
    it, with each year's sources, so the owner can see which figures price
    their paychecks and where each came from.  It held a per-user copy of the
    law and two forms that overwrote it until then -- one of which wrote a
    single state standard deduction to all four filing statuses (finding
    **SAL-574**).

    Returns:
        ``{"tax_years": (TaxYearLaw, ...)}``, newest year first.
    """
    return {"tax_years": tuple(reversed(tax_law.LAW.years))}


def _load_account_types_context():
    """Load the account-types section: the user's visible account types, which
    type IDs are in use, the category list, and the icon choices.

    The listing is scoped to seeded built-ins (``user_id IS NULL``) plus the
    current user's own custom types (commit C-28 / F-044); other owners' custom
    types are intentionally invisible.
    """
    account_types = (
        db.session.query(AccountType)
        .filter(db.or_(
            AccountType.user_id.is_(None),
            AccountType.user_id == current_user.id,
        ))
        .order_by(AccountType.name)
        .all()
    )
    return {
        "account_types": account_types,
        "types_in_use": account_service.get_account_type_ids_in_use(current_user.id),
        "categories": (
            db.session.query(AccountTypeCategory)
            .order_by(AccountTypeCategory.id)
            .all()
        ),
        "icon_choices": _ACCOUNT_TYPE_ICON_CHOICES,
    }


def _load_security_context():
    """Load the security section: MFA's state and the live trusted devices.

    Each trusted device (plan#2173, ruling plan#2406) is a row the template
    only displays: the browser's name read from its stored User-Agent, the
    two instants it is dated by (stored UTC; the template's
    ``local_datetime`` filter shows each as a display-timezone day), and
    whether it is the browser this page is rendered on, judged against the
    list already read rather than by a second lookup.

    Returns:
        ``mfa_enabled``, and ``trusted_devices``: one dict per live device,
        most recently trusted first, with ``id``, ``browser``, ``trusted_at``,
        ``expires_at`` and ``is_this_browser``.
    """
    mfa_config = (
        db.session.query(MfaConfig)
        .filter_by(user_id=current_user.id)
        .first()
    )
    devices = trusted_device_service.list_live_devices(current_user.id)
    this_browser = trusted_device_service.device_named_by(
        devices, device_token(current_user.id),
    )
    return {
        "mfa_enabled": mfa_config.is_enabled if mfa_config else False,
        "trusted_devices": [
            {
                "id": device.id,
                "browser": browser_label(device.user_agent),
                "trusted_at": device.trusted_at,
                "expires_at": device.expires_at,
                "is_this_browser": device is this_browser,
            }
            for device in devices
        ],
    }


# --- Companion account management ----------------------------------------
#
# Companions are created, edited, deactivated, and reactivated here.
# Every route below applies two guards on top of the application's login
# gate and the @require_owner decorator:
#   1. Target user must have role_id == COMPANION.  This rejects any
#      attempt to edit another owner (or the current user).
#   2. Target user's linked_owner_id must equal current_user.id.  This
#      rejects any cross-owner tampering even though this app only has
#      one owner today.
# A failing guard returns 404, matching the project rule "404 for both
# 'not found' and 'not yours.'"


def _load_companion_or_404(companion_id):
    """Return the owned companion row or abort with 404.

    Applies both defense-in-depth checks (role and linked owner) and
    uses the same 404 response for missing rows and unowned rows.
    """
    target = db.session.get(User, companion_id)
    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    if target is None:
        abort(404)
    if target.role_id != companion_role:
        abort(404)
    if target.linked_owner_id != current_user.id:
        abort(404)
    return target


def _load_companions_context(raw_edit_id):
    """Return the dict of template variables for the companions section.

    Args:
        raw_edit_id: The ``?edit=<id>`` query-string value, or None.
            Invalid or non-numeric values fall back silently to the
            create form.

    Returns:
        A dict with keys ``active_companions``, ``inactive_companions``,
        ``edit_companion_id``, and ``form_values``.  The form_values
        dict pre-populates the edit form with the matching companion's
        email and display name when a valid ``edit`` id is supplied.
    """
    companions = (
        db.session.query(User)
        .filter_by(
            linked_owner_id=current_user.id,
            role_id=ref_cache.role_id(RoleEnum.COMPANION),
        )
        .order_by(User.display_name, User.email)
        .all()
    )
    active = [c for c in companions if c.is_active]
    inactive = [c for c in companions if not c.is_active]

    edit_id = None
    values = {}
    # Resolved ONCE, before the scan: the id used to be parsed inside the
    # generator's predicate, so it was re-evaluated per companion and never
    # evaluated at all for an owner with none -- which is why this door's
    # share of finding N-136 was only conditionally reachable.
    requested_id = parse_row_id(raw_edit_id)
    if requested_id is not None:
        candidate = next(
            (c for c in active if c.id == requested_id), None,
        )
        if candidate is not None:
            edit_id = candidate.id
            values = {
                "email": candidate.email,
                "display_name": candidate.display_name or "",
            }
    return {
        "active_companions": active,
        "inactive_companions": inactive,
        "edit_companion_id": edit_id,
        "form_values": values,
    }


def _render_companions_section(errors=None, form_values=None,
                               edit_companion_id=None):
    """Re-render the companion section with validation errors preserved.

    Used when create/edit form submission fails validation.  Reloads
    the companion list so the sidebar/list render correctly even
    though the form has errors.  The supplied form_values and
    edit_companion_id override whatever _load_companions_context
    would derive from the query string.
    """
    ctx = _load_companions_context(None)
    # Reuse the GET handler's full default context so the companions
    # form re-render and settings.show() cannot drift on the template's
    # key set; override only the error/companion slice.
    context = _empty_section_context()
    context.update({
        "errors": errors or {},
        "active_companions": ctx["active_companions"],
        "inactive_companions": ctx["inactive_companions"],
        "edit_companion_id": edit_companion_id,
        "form_values": form_values or {},
    })
    return render_template(
        "settings/dashboard.html",
        active_section="companions",
        settings=current_user.settings,
        **context,
    )


@settings_bp.route("/settings/companions", methods=["POST"])
@require_owner
@fresh_login_required()
def companion_create():
    """Create a new companion user linked to the current owner."""
    schema = CompanionCreateSchema()
    errors = schema.validate(request.form)
    if errors:
        # Preserve user-entered email and display name, but never echo
        # the password back into the form.
        form_values = {
            "email": request.form.get("email", "").strip().lower(),
            "display_name": request.form.get("display_name", "").strip(),
        }
        return _render_companions_section(
            errors=errors, form_values=form_values,
        ), 400

    data = schema.load(request.form)

    # Email uniqueness: check against the entire users table, not just
    # companions.  An owner cannot reuse their own email or any other
    # user's email.
    existing = (
        db.session.query(User)
        .filter(db.func.lower(User.email) == data["email"])
        .first()
    )
    if existing is not None:
        return _render_companions_section(
            errors={"email": ["Email address is already in use."]},
            form_values={
                "email": data["email"],
                "display_name": data["display_name"],
            },
        ), 400

    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    companion = User(
        email=data["email"],
        password_hash=hash_password(data["password"]),
        display_name=data["display_name"],
        role_id=companion_role,
        linked_owner_id=current_user.id,
        is_active=True,
    )
    db.session.add(companion)
    db.session.flush()

    # Create UserSettings alongside the User, matching register_user()
    # and seed_companion.py so settings.show() never sees a None on
    # the companion's first access.
    db.session.add(UserSettings(user_id=companion.id))
    db.session.commit()

    flash(f"Companion account '{data['display_name']}' created.", "success")
    return redirect(url_for("settings.show", section="companions"))


@settings_bp.route(
    "/settings/companions/<int:companion_id>/edit", methods=["POST"],
)
@require_owner
@fresh_login_required()
def companion_edit(companion_id):
    """Update an existing companion's email, name, and optional password."""
    companion = _load_companion_or_404(companion_id)

    schema = CompanionEditSchema()
    errors = schema.validate(request.form)
    if errors:
        form_values = {
            "email": request.form.get("email", "").strip().lower(),
            "display_name": request.form.get("display_name", "").strip(),
        }
        return _render_companions_section(
            errors=errors,
            form_values=form_values,
            edit_companion_id=companion.id,
        ), 400

    data = schema.load(request.form)

    # Email uniqueness: allow the companion to keep their own email,
    # but reject any other user's email.
    existing = (
        db.session.query(User)
        .filter(db.func.lower(User.email) == data["email"])
        .filter(User.id != companion.id)
        .first()
    )
    if existing is not None:
        return _render_companions_section(
            errors={"email": ["Email address is already in use."]},
            form_values={
                "email": data["email"],
                "display_name": data["display_name"],
            },
            edit_companion_id=companion.id,
        ), 400

    companion.email = data["email"]
    companion.display_name = data["display_name"]

    new_password = data.get("password") or ""
    if new_password:
        companion.password_hash = hash_password(new_password)
        # Invalidate any live companion session -- matches the behavior
        # of change_password in auth.py for the owner's own sessions.
        companion.session_invalidated_at = db.func.now()

    db.session.commit()

    flash(f"Companion account '{companion.display_name}' updated.", "success")
    return redirect(url_for("settings.show", section="companions"))


@settings_bp.route(
    "/settings/companions/<int:companion_id>/deactivate", methods=["POST"],
)
@require_owner
@fresh_login_required()
def companion_deactivate(companion_id):
    """Soft-delete a companion by flipping is_active to False.

    The companion's entries, MFA config, and settings row are
    preserved.  Flask-Login's user_loader rejects any session whose
    user has is_active=False, so all live companion sessions become
    invalid on their next request.
    """
    companion = _load_companion_or_404(companion_id)

    if not companion.is_active:
        flash("Companion account is already deactivated.", "info")
        return redirect(url_for("settings.show", section="companions"))

    companion.is_active = False
    # Belt-and-suspenders: stamp the invalidation time so session
    # checks that look at session_invalidated_at also reject live
    # sessions, not just the is_active flag.
    companion.session_invalidated_at = db.func.now()
    db.session.commit()

    flash(
        f"Companion account '{companion.display_name}' deactivated. "
        "Their entries are preserved.",
        "success",
    )
    return redirect(url_for("settings.show", section="companions"))


@settings_bp.route(
    "/settings/companions/<int:companion_id>/reactivate", methods=["POST"],
)
@require_owner
@fresh_login_required()
def companion_reactivate(companion_id):
    """Restore access for a previously deactivated companion.

    Only is_active is flipped back to True.  session_invalidated_at
    is intentionally left at the deactivation timestamp so any stale
    session cookies from before deactivation remain invalid -- the
    companion must log in fresh.
    """
    companion = _load_companion_or_404(companion_id)

    if companion.is_active:
        flash("Companion account is already active.", "info")
        return redirect(url_for("settings.show", section="companions"))

    companion.is_active = True
    db.session.commit()

    flash(
        f"Companion account '{companion.display_name}' reactivated.",
        "success",
    )
    return redirect(url_for("settings.show", section="companions"))
