"""
Shekel Budget App -- Salary route package: shared helpers.

Marshmallow schema singletons, form-field allowlists, unique-constraint
name constants, and the private helpers shared across the salary route
sub-modules (transaction regeneration and the HTMX-partial / redirect
responders).
Constructed once at import time so every handler reuses the same
instances, preserving the pre-split monolith's behaviour.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user

from app.utils.auth_helpers import get_or_404, log_refused_lookup
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.account import Account
from app.models.ref import (
    CalcMethod,
    RaiseType,
)
from app.routes._recurrence_conflict_chooser import flash_retained_notice
from app.routes._recurrence_form_render import (
    RecurrenceEnd,
    RecurrenceStart,
    edit_form_cadence,
    edit_form_end,
)
from app.services import (
    account_service,
    paycheck_line_kinds,
    payroll_line_cadence,
    salary_regeneration,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    EndBound,
    picker_model,
    stored_month_reading,
)
from app.schemas.validation import (
    EFFECTIVE_DATE_MAX,
    EFFECTIVE_DATE_MIN,
    PaycheckLineCreateSchema,
    PaycheckLineUpdateSchema,
    RaiseCreateSchema,
    RaiseUpdateSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
    YtdTaxCheckpointSchema,
)

# Field allowlists for the update routes: which submitted form fields may
# be written back to each model via setattr.  Defined at module scope so
# each set is built once per process rather than on every request.
_PROFILE_UPDATE_FIELDS = {
    "name", "filing_status_id", "state_code",
    "qualifying_children", "other_dependents",
    "additional_income", "additional_deductions", "extra_withholding",
    "stub_gross_includes_after_tax",
}
_RAISE_UPDATE_FIELDS = {
    "raise_type_id", "effective_month", "effective_year",
    "percentage", "flat_amount", "is_recurring", "terminal_year", "notes",
}
# ``deductions_per_year`` left this set at plan step salary:R15-b: a
# deduction's cadence is a recurrence rule on the row, authored through the
# recurrence seam (R15-c's form), never a column written by name.
_LINE_UPDATE_FIELDS = {
    "name", "paycheck_line_kind_id", "calc_method_id", "amount",
    "annual_cap", "inflation_enabled",
    "inflation_rate", "inflation_effective_month", "target_account_id",
}

# Names of the composite unique constraints that backstop the
# raise / deduction double-submit fixes (F-051 + F-052 / C-23).
# Each literal mirrors the model declaration in
# ``app/models/salary_raise.py`` and
# ``app/models/paycheck_line.py`` and the migration revision
# ``a3b9c2d40e15``; renaming a constraint requires a coordinated
# edit across all three sites.
_SALARY_RAISES_UNIQUE_CONSTRAINT = "uq_salary_raises_profile_type_year_month"
_PAYCHECK_LINES_UNIQUE_CONSTRAINT = "uq_paycheck_lines_profile_name"

_create_schema = SalaryProfileCreateSchema()
_update_schema = SalaryProfileUpdateSchema()
_raise_schema = RaiseCreateSchema()
_raise_update_schema = RaiseUpdateSchema()
_line_schema = PaycheckLineCreateSchema()
_line_update_schema = PaycheckLineUpdateSchema()
_ytd_checkpoint_schema = YtdTaxCheckpointSchema()


def _get_owned_profile_and_period(profile_id, period_id, calendar):
    """Load an owned salary profile and pay period, or abort 404.

    The shared ownership gate for the two routes keyed on both a profile
    and a period -- the cockpit's anatomy fragment
    (:func:`app.routes.salary.cockpit.anatomy`) and the retired-breakdown
    redirect stub (:func:`app.routes.salary.views.breakdown`).  Verifies
    each id belongs to the current user BEFORE the caller acts, so a
    cross-user id 404s here per the project's "404 for both 'not found'
    and 'not yours'" rule rather than leaking existence.

    **The PERIOD half is now structural** (pay-calendar plan step C2-f2d-3).
    It was ``get_or_404(PayPeriod, period_id)`` -- a read of the global table
    with the owner compared afterwards.  The calendar holds ONE owner's
    paydays, so another owner's id is not found-and-rejected here; it is
    absent, and the 404 falls out of the lookup rather than out of a
    comparison a later edit could drop.
    **What that move COST, and what pays it back**: ``get_or_404`` emits an
    access event on each of its two denial branches, and for one commit the
    calendar's refusal emitted nothing, so a cross-user id probe against these
    two routes was silent in the audit log (adversarial code review, 2026-08-16).
    :func:`~app.utils.auth_helpers.log_refused_lookup` restores the trail at the
    one severity the ambiguity supports -- see that function for why it does not
    claim CROSS_USER.

    Args:
        profile_id: Primary key of the requested salary profile.
        period_id: Primary key of the requested pay period.
        calendar: The current user's
            :class:`~app.services.pay_calendar.PayCalendar`.  Taken as a
            parameter rather than derived here because both callers need the
            same calendar for the paycheck engine beside this call, and one
            derivation per request is the rule this arc is applying
            everywhere.

    Returns:
        A ``(profile, period)`` tuple -- the :class:`SalaryProfile` and the
        :class:`~app.services.pay_calendar.DerivedPeriod`, both the current
        user's.

    Raises:
        werkzeug.exceptions.NotFound: (via ``abort(404)``) when either id
            is missing or owned by another user.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)
    period = calendar.period_by_id(period_id)
    if period is None:
        # See :func:`~app.utils.auth_helpers.log_refused_lookup`: the calendar
        # cannot tell "no such period" from "not yours", and the refusal is
        # logged anyway so a cross-user probe still leaves a trail.
        log_refused_lookup("PayPeriod", period_id)
        abort(404)
    return profile, period


def _regenerate_salary_transactions(profile):
    """Regenerate the profile's paycheck rows and tell the owner what was kept.

    The salary package's adapter over
    :func:`~app.services.salary_regeneration.regenerate_salary_transactions`,
    the ONE walk every salary write is followed by (plan step salary:S3-f-3,
    ruling **R-SAL24** -- the walk moved below the route layer so the
    ``/retirement`` rail's end-year Save could reach it too).  What is left
    here is the Flask half the service cannot carry: this route package opens
    the read pass for the requester, and it FLASHES the rows the pass
    declined to touch.  The name and the signature are the pre-move ones, so
    every call site across this package -- and the tests that patch this
    name to simulate a stale race -- is untouched.  One thing did move: the
    pass is built before the service's own template guard runs, one scenario
    query for a profile without a template, a state ``create_profile`` never
    produces.

    Args:
        profile: The :class:`SalaryProfile` whose template prices the rows.
    """
    retained = salary_regeneration.regenerate_salary_transactions(
        BalanceContext.build(current_user.id), profile,
    )
    # **A RETAINED row is the owner's business, not just the log's** (plan
    # step R10-a, adversarial review): the service returns the ids rather
    # than dropping them, and this is where the salary page says so.
    flash_retained_notice(retained)


def _render_raises_partial(profile):
    """Return the raises table partial for HTMX updates."""
    # Refresh relationships
    db.session.refresh(profile)
    raise_types = db.session.query(RaiseType).all()
    return render_template(
        "salary/_raises_section.html",
        profile=profile,
        raise_types=raise_types,
        now_year=date.today().year,
    )


def _respond_after_raise_change(profile):
    """Respond after a raise mutation succeeds (or is idempotently absorbed).

    Returns the raises-section partial for an in-page HTMX swap, or a
    full-page redirect to the profile edit view for a normal form post.
    Centralises the response branch shared by the add/update/delete raise
    handlers so each has a single success exit point.
    """
    if request.headers.get("HX-Request"):
        return _render_raises_partial(profile)
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


def _line_cadence_phrases(
    profile, calendar_source: "Callable[[], PayCalendar]",
) -> dict[int, str]:
    """How often each of *profile*'s deductions is taken, worded for the page.

    The Frequency cell's one source since plan step salary:R15-b: each
    line's recurrence rule described through the recurrence package, or
    *every paycheck* for a line with none (R-SAL3).  A rule is resolved
    against the owner's calendar, asked of *calendar_source* exactly when a
    line carries a rule (which is exactly when the owner has a schedule, a
    rule being authored against one) and never otherwise, so a render with
    no rule derives nothing here.

    **It takes a SOURCE rather than deriving its own since plan step
    salary:X-av-3a**, the shape :func:`~app.services.income_service
    .paycheck_pricing` takes for the same reason: the edit page now derives
    the owner's calendar on every render (the pay list), and this helper's
    own ``calendar_for`` was then a SECOND derivation of one schedule in one
    render -- measured at 2 by the page's one-derivation test.  The edit page
    hands its pass's memo; the HTMX fragment, which holds no pass, hands
    ``calendar_for`` bound to the owner, still asked only when a line recurs.

    Args:
        profile: The salary profile whose deductions the section lists.
        calendar_source: A zero-argument callable answering the owner's
            :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        ``{deduction id: phrase}``.
    """
    calendar = (
        calendar_source() if any(d.recurs for d in profile.lines) else None
    )
    return payroll_line_cadence.cadence_phrases(profile.lines, calendar)


def _line_cadence_context(
    profile, calendar_source: "Callable[[], PayCalendar]",
) -> dict:
    """The context the lines section renders each line's cadence and kind from.

    ONE producer for the two renders of ``salary/_lines_section.html``
    -- the edit page and the HTMX fragment -- so the section cannot read a
    line's cadence one way on a full load and another after a swap (plan
    step salary:R15-c, ruling **R-SAL31**), and since plan step salary:R18-b
    (ruling **R-SAL38**) the kind vocabulary rides with it:

    * ``kind_options`` -- the five kinds as ``(id, label)`` in waterfall
      order, the form's select; ``kind_labels`` -- ``{id: label}``, the row
      chip's words; ``pre_tax_kind_ids`` -- the ids of the two pre-tax
      kinds, which the row chip marks (plan step salary:S11-e).  All three
      from :mod:`app.services.paycheck_line_kinds`, the one producer of a
      kind's label and of which kinds are pre-tax, so the ref row's NAME is
      never worded by a template and the pre-tax kinds are never listed in
      one;

    * ``cadence_phrases`` -- the Frequency cell's words, R15-b's;
    * ``recurrence_picker`` -- the offer set the shared cadence controls
      render from, :func:`~app.services.recurrence.picker_model` over
      :func:`~app.services.payroll_line_cadence.line_cadences`: the set every
      recurrence form takes, narrowed to the units a payroll line may repeat
      by (ruling plan#2591, amending **R-SAL37**'s "the whole set": a line's
      rule picks which paychecks take it, so a weekly amount would be taken
      once a paycheck however many weeks the paycheck holds);
    * ``selected_cadences`` -- ``{deduction id: SelectedCadence | None}``,
      what an EDIT of each line starts its controls on, through the one
      render-side reader the template forms use
      (:func:`~app.routes._recurrence_form_render.edit_form_cadence`:
      ``None`` for a line with no rule, and ``None`` with a flashed
      explanation for a stored cadence the application no longer models).
      The section emits each as ``data-line-*`` attributes on the row's edit
      button, and ``app.js`` fills the one inline form from them the way it
      fills every other field of that form.
    * ``selected_spans`` -- ``{line id: LineSpan}``, the row's start, its
      "repeating on" choice and closing bound for the same edit prefill (plan step
      salary:R18-c, ruling **R-SAL38** (2)); ``add_form_start`` /
      ``add_form_end`` -- what the ADD form's span rows open on: a BLANK
      start (blank is the opening payday) and *never*; ``starts_on_min`` /
      ``starts_on_max`` -- the schema's own date window, so the browser hint
      and the refusal state one range.

    Args:
        profile: The salary profile whose lines the section lists.
        calendar_source: The owner's calendar, asked only when a line
            carries a rule (:func:`_line_cadence_phrases`).

    Returns:
        The eleven context keys.
    """
    options = paycheck_line_kinds.kind_options()
    picker = picker_model(payroll_line_cadence.line_cadences())
    return {
        "kind_options": options,
        "kind_labels": dict(options),
        "pre_tax_kind_ids": paycheck_line_kinds.pre_tax_kind_ids(),
        "cadence_phrases": _line_cadence_phrases(profile, calendar_source),
        "recurrence_picker": picker,
        "selected_cadences": {
            line.id: edit_form_cadence(line)
            for line in profile.lines
        },
        "selected_spans": {line.id: _line_span(line, picker) for line in profile.lines},
        "add_form_start": RecurrenceStart(starts_on=None),
        "add_form_end": RecurrenceEnd(selected=NEVER_ENDS),
        "starts_on_min": EFFECTIVE_DATE_MIN,
        "starts_on_max": EFFECTIVE_DATE_MAX,
    }


@dataclass(frozen=True)
class LineSpan:
    """What a stored line's SPAN controls prefill with on an edit.

    Attributes:
        starts_on: The rule's own first occurrence, or ``None`` for a line
            with no rule -- rendered as a BLANK box, because blank is what the
            form means by "from the opening payday" and a re-save of a blank
            box derives the same default again (plan step salary:R18-c).
        repeats_on: What the rule's "repeating on" choice posts
            (:attr:`~app.services.recurrence.MonthReading.token`): empty for
            the same day of the month and for a line with no rule.
        end: The closing bound as the ONE value the form's three controls
            compose to (:class:`~app.services.recurrence.EndBound`): *never*
            for a line with no rule.
    """
    starts_on: date | None
    repeats_on: str
    end: EndBound


def _line_span(line, picker) -> LineSpan:
    """Return *line*'s span for the edit prefill, through the render-side readers.

    The bound comes from :func:`~app.routes._recurrence_form_render.edit_form_end`
    -- the same reader the template forms use, so the three stored shapes are
    discriminated in one place; a payroll line's bounds are never the loan's,
    so the row is never locked.  The start is read off the rule rather than
    through ``edit_form_starts_on``, which opens a rule-less definition on
    TODAY -- the template forms' default, and the wrong one here, where a
    blank box is the opening payday and today would be read as a stated
    start.

    Args:
        line: One of the profile's lines.
        picker: The form's offer sets.

    Returns:
        The :class:`LineSpan`.
    """
    rule = line.recurrence_rule
    return LineSpan(
        starts_on=None if rule is None else rule.starts_on,
        repeats_on=(
            "" if rule is None
            else stored_month_reading(rule.nominal_day, rule.weekday_reading).token
        ),
        end=edit_form_end(line, None, picker, locked=False).selected,
    )


def _render_lines_partial(profile: SalaryProfile, notice: str | None = None) -> str:
    """Return the payroll-lines section partial for HTMX updates.

    Args:
        profile: The owned profile.
        notice: A refusal to show inside the section, or ``None`` -- the one
            way a partial response can SAY something, since a flash is a
            property of a full page render (``base.html``).
    """
    db.session.refresh(profile)
    calc_methods = db.session.query(CalcMethod).all()
    investment_accounts = _get_investment_accounts(profile.user_id)
    return render_template(
        "salary/_lines_section.html",
        profile=profile,
        calc_methods=calc_methods,
        investment_accounts=investment_accounts,
        notice=notice,
        **_line_cadence_context(
            profile, lambda: calendar_for(profile.user_id),
        ),
    )


def _respond_after_line_change(
    profile: SalaryProfile, notice: str | None = None,
) -> ResponseReturnValue:
    """Respond after a deduction mutation succeeds (or is idempotently absorbed).

    Returns the lines-section partial for an in-page HTMX swap, or a
    full-page redirect to the profile edit view for a normal form post.
    Counterpart to :func:`_respond_after_raise_change` for the add/update/
    delete deduction handlers.

    A *notice* -- a refusal the section's own button provoked (plan step
    salary:S11-b: a line a pay stub names cannot be deleted) -- is shown
    INSIDE the swapped section on the HTMX path, where the owner clicked, and
    flashed on the full-page one.
    """
    if request.headers.get("HX-Request"):
        return _render_lines_partial(profile, notice)
    if notice is not None:
        flash(notice, "warning")
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


def _get_investment_accounts(user_id):
    """Load retirement/investment accounts for the target account dropdown."""
    retirement_types = account_service.list_retirement_investment_account_types()
    type_ids = {rt.id for rt in retirement_types}
    if not type_ids:
        return []
    return (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.account_type_id.in_(type_ids),
            Account.is_active.is_(True),
        )
        .order_by(Account.name)
        .all()
    )
