"""
Shekel Budget App -- Salary route package: shared helpers.

Marshmallow schema singletons, form-field allowlists, unique-constraint
name constants, and the private helpers shared across the salary route
sub-modules (transaction regeneration and the HTMX-partial / redirect
responders).
Constructed once at import time so every handler reuses the same
instances, preserving the pre-split monolith's behaviour.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from flask import Response, abort, flash, make_response, render_template, request
from flask.typing import ResponseReturnValue

from app.utils.auth_helpers import get_or_404, log_refused_lookup
from app.utils.error_fragments import designed_error
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.account import Account
from app.models.ref import (
    CalcMethod,
    RaiseType,
)
from app.routes._recurrence_conflict_chooser import flash_retained_notice
from app.routes._redirect_target import RedirectTarget
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
from app.services.payroll_basis import PayrollBasis
from app.services.salary_raises import RAISE_YEAR_MAX, RAISE_YEAR_MIN
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
    "raise_type_id", "effective_date",
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
# ``a3b9c2d40e15`` (the raise's, renamed with its key by ``6740ce13e174``
# when the start became a date); renaming a constraint requires a coordinated
# edit across all three sites.
_SALARY_RAISES_UNIQUE_CONSTRAINT = "uq_salary_raises_profile_type_date"
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


def _regenerate_salary_transactions(
    ctx: BalanceContext, profile: SalaryProfile,
) -> None:
    """Regenerate the profile's paycheck rows in the door's pass, and tell the owner what was kept.

    The salary package's adapter over
    :func:`~app.services.salary_regeneration.regenerate_salary_transactions`,
    the ONE walk every salary write is followed by (plan step salary:S3-f-3,
    ruling **R-SAL24** -- the walk moved below the route layer so the
    ``/retirement`` rail's end-year Save could reach it too).  What is left
    here is the Flask half the service cannot carry: it FLASHES the rows the
    pass declined to touch.

    **It takes the door's read pass, and opens none, since task plan#3106.**
    It built its own from ``current_user`` until then, so the profile update
    door, which asks the owner's calendar whether a stub's Pay Date is a
    payday, and the Fix door, which asks whether a changed payday is one,
    each derived the calendar twice in one POST.  A route builds the one pass
    and what it calls takes it as a required argument, never an optional one
    built when absent (ruling of 2026-08-16, plan step pay_calendar:C2-f2d-1).
    The three raise doors build theirs before staging their write since task
    plan#3169, because the add and edit doors' refusal reads its calendar
    first.  Every other door builds its pass at this call, after its write is
    staged, which is when this adapter built it.  The two line doors and the
    stub doors hold a pre-write pass beside that one, which is finding
    plan#1589's.

    Args:
        ctx: The door's :class:`~app.services.balance_at.BalanceContext`.
            Its calendar, scenario and day are what the regeneration reads,
            with the template's own placements; it prices nothing (plan step
            X-bp), so a pass the door read its calendar from before the write
            serves it unchanged.
        profile: The :class:`SalaryProfile` whose template prices the rows.
    """
    retained = salary_regeneration.regenerate_salary_transactions(ctx, profile)
    # **A RETAINED row is the owner's business, not just the log's** (plan
    # step R10-a, adversarial review): the service returns the ids rather
    # than dropping them, and this is where the salary page says so.
    flash_retained_notice(retained)


def _edit_page(profile_id: int) -> RedirectTarget:
    """Where every refused or failed line-item submission sends the user: the profile's edit page.

    Twelve literal spellings of one target until plan step salary:R15-c
    added four more; one function is the answer to a review that counted them.
    It moved here from ``items.py`` with task plan#3136, so the responders
    below, which spelled the target twice more, reach the one home too.
    """
    return RedirectTarget("salary.edit_profile", {"profile_id": profile_id})


def _raise_form_bounds() -> dict[str, int]:
    """The years the raise form's date and end-year controls offer.

    The window's one home is ``salary_raises.RAISE_YEAR_MIN`` / ``_MAX``,
    which the schema's ``Range`` and the columns' CHECKs are built from; the
    template typed the same two years a second time until task plan#3125.
    Both renders of the Raises card (the edit page and the HTMX section) hand
    these in.

    Returns:
        ``raise_year_min`` and ``raise_year_max``, for the template.
    """
    return {"raise_year_min": RAISE_YEAR_MIN, "raise_year_max": RAISE_YEAR_MAX}


@dataclass(frozen=True)
class RefusedRaise:
    """A refused raise form, as the Raises card redraws it (ruling plan#3134).

    The card's form opens in the mode it was posted in, holding what was
    typed, under the reason.  What was typed rides on the reason's box as
    ``data-raise-*`` attributes, the names the row's edit button carries, and
    ``app.js`` fills the form from the box with the one fill the edit button
    runs (``_fillRaiseForm``).  It is never rendered as the controls'
    ``value``: the Add Raise button resets the form with ``form.reset()``,
    which restores RENDERED values, so a value attribute would make Add Raise
    refill the refused raise instead of clearing the form.

    Attributes:
        message: Why, in the refusal's own words.
        category: Its flash category, which colours the box the way the flash
            is coloured on a plain post: ``danger``, or ``warning`` for a form
            another action made stale.
        posted: The form as posted (``request.form``).
        recurring: Whether the posted Recur box reads as ticked, by the
            schema's own ``is_recurring`` field (its ``truthy`` set), so the
            redraw cannot read the box one way and the refusal another.
        edit_url: The update door the form posted to, or ``None`` for an add.
    """
    message: str
    category: str
    posted: Mapping[str, str]
    recurring: bool
    edit_url: str | None


def _raise_card_context(
    profile: SalaryProfile, basis_source: "Callable[[], PayrollBasis]",
) -> dict:
    """The context both renders of the Raises card draw from (task plan#3169).

    ONE producer for the edit page and the HTMX fragment, as
    :func:`_line_cadence_context` is for the lines card, so a full load and a
    swap cannot word one raise two ways:

    * ``raises_in_pay`` -- ``{raise id: RaiseInPay}``, what recorded pay
      already holds of each raise (ruling **R-SAL95**, worded by ruling
      **plan#3375**), from
      :meth:`~app.services.payroll_basis.PayrollBasis.raise_in_pay`, the one
      producer the add and edit doors refuse by.  The basis is asked for only
      when the profile has a raise, so a card with none derives no calendar;
    * ``raise_year_min`` / ``raise_year_max`` -- the form's years
      (:func:`_raise_form_bounds`).

    Args:
        profile: The salary profile whose raises the card lists.
        basis_source: A zero-argument callable answering the profile's
            :class:`~app.services.payroll_basis.PayrollBasis`: the edit page
            hands the basis its pay list is priced from, the fragment one
            over its door's calendar.

    Returns:
        The three context keys.
    """
    basis = basis_source() if profile.raises else None
    return {
        "raises_in_pay": (
            {} if basis is None
            else {row.id: basis.raise_in_pay(row) for row in profile.raises}
        ),
        **_raise_form_bounds(),
    }


def _render_raises_partial(
    profile: SalaryProfile,
    calendar_source: "Callable[[], PayCalendar]",
    refused: RefusedRaise | None = None,
) -> str:
    """Return the raises section partial for HTMX updates.

    Args:
        profile: The owned profile.
        calendar_source: The door's calendar (its pass's memo), which the
            card's labels are read against (:func:`_raise_card_context`).
        refused: A refused form to redraw open, with its reason
            (:class:`RefusedRaise`), or ``None`` for the card after a change.
    """
    # Refresh relationships
    db.session.refresh(profile)
    raise_types = db.session.query(RaiseType).all()
    return render_template(
        "salary/_raises_section.html",
        profile=profile,
        raise_types=raise_types,
        refused=refused,
        **_raise_card_context(
            profile, lambda: PayrollBasis(profile, calendar_source()),
        ),
    )


def _respond_after_raise_change(
    profile: SalaryProfile, calendar_source: "Callable[[], PayCalendar]",
):
    """Respond after a raise mutation succeeds (or is idempotently absorbed).

    Returns the raises-section partial for an in-page HTMX swap, or a
    full-page redirect to the profile edit view for a normal form post.
    Centralises the response branch shared by the add/update/delete raise
    handlers so each has a single success exit point.  A refused form is
    :class:`RaiseRefusal`'s.

    Args:
        profile: The owned profile.
        calendar_source: The door's calendar, its pass's memo, so the card
            derives none of its own.
    """
    if request.headers.get("HX-Request"):
        return _render_raises_partial(profile, calendar_source)
    return _edit_page(profile.id).to_response()


@dataclass(frozen=True)
class RaiseRefusal:
    """How a raise door answers a refused form (task plan#3136, ruling plan#3134).

    The raise form saves in place (``hx-target="#raises-section"``), so on an
    htmx request the answer is the Raises card itself, at 422 and marked as a
    designed fragment (:func:`~app.utils.error_fragments.designed_error`):
    the reason in a box and the form open, holding what was typed
    (:class:`RefusedRaise`).  A redirect there is followed by the browser and
    the whole edit page it lands on is swapped INTO the card, where its flash
    never shows.  A plain form post keeps the flash and the redirect to the
    edit page.

    Called with the sentence alone it is a
    :data:`~app.routes._form_errors.FormRefusal`, the loader's
    (:func:`~app.routes._form_errors.load_form_or_refuse`); the update
    door's stale-form pre-check calls it with ``"warning"`` too.  It flashes
    nothing on an htmx request, so the reason is not shown a second time on
    the next page loaded.

    Attributes:
        profile: The owned profile whose card is redrawn.
        calendar_source: The door's calendar, its pass's memo, which the
            redrawn card's labels are read against.
        edit_url: The update door the form posted to, so the form reopens in
            edit mode; ``None`` for the add door.
    """
    profile: SalaryProfile
    calendar_source: "Callable[[], PayCalendar]"
    edit_url: str | None = None

    def __call__(self, message: str, category: str = "danger") -> Response:
        """Answer the refusal *message* in the request's own shape.

        Args:
            message: Why, in the refusal's own words.
            category: The flash category: ``danger``, or ``warning`` for a
                stale form.

        Returns:
            The card at 422 on an htmx request, else a redirect to the edit
            page with *message* flashed.
        """
        if request.headers.get("HX-Request"):
            refused = RefusedRaise(
                message, category, request.form,
                request.form.get("is_recurring") in _raise_schema.fields["is_recurring"].truthy,
                self.edit_url,
            )
            return make_response(designed_error(
                _render_raises_partial(self.profile, self.calendar_source, refused), 422,
            ))
        flash(message, category)
        return _edit_page(self.profile.id).to_response()


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
    return _edit_page(profile.id).to_response()


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
