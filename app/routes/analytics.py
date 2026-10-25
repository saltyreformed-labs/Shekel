"""
Shekel Budget App -- Analytics Routes

Analytics dashboard whose four nav pills lazy-load one HTMX partial each:
Calendar, Spending, Statements, and Taxes (the Slice-4 shell collapse).  The
Statements pill groups the two confirmed-ledger reports (Income Statement and
Balance Sheet, Build-Order Step 5, via ``ledger_report_service``) behind one
internal toggle.  The Spending pill (Slice 3) replaced the Variance and Trends
pills and the Taxes pill (Slice 2) replaced Year-End; the retired
``/analytics/variance``, ``/analytics/trends``, and ``/analytics/year-end``
URLs now redirect to the main page (``retired_tab``) so old bookmarks do not
404.  Each tab pill pushes its own URL and a direct (non-HTMX) GET renders the
shell with that tab active rather than redirecting to Calendar (D13).  No
analytics export remains -- the last one, the calendar month/year CSV, was
removed with P-AN4.
"""

import calendar as cal_mod
from datetime import date, datetime, timezone

from flask import (
    Blueprint, abort, redirect, render_template, request, url_for,
)
from flask_login import current_user

from app.routes import analytics_view
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today, to_display_date
from app.utils.digit_strings import integer_arg

from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    calendar_service,
    ledger_report_service,
    spending_report_service,
    tax_report_service,
    tax_withholding_service,
)
from app.services.calendar_service import CalendarAccountNotResolvableError
from app.services.pay_calendar import calendar_for

analytics_bp = Blueprint("analytics", __name__)


def _validate_owned_or_abort(model, pk):
    """Validate that ``pk`` references a record owned by ``current_user``.

    Used at the top of analytics route handlers to enforce the
    project security response rule: "404 for both 'not found' and
    'not yours.'"  Without this guard the underlying services
    silently fall back to default data on a cross-user
    ``account_id`` (calendar) or read victim metadata into the
    response window label on a cross-user ``period_id`` (income
    statement), bypassing the access boundary documented in the
    project's auth-helper contract.

    Delegates the existence + ownership check to
    :func:`app.utils.auth_helpers.get_or_404`, which already emits
    the structured ``resource_not_found`` (INFO) and
    ``access_denied_cross_user`` (WARNING) audit events the SOC
    dashboards rely on.  This wrapper adds the abort so the route
    body can stay flat (``_validate_owned_or_abort(...)`` as a
    one-liner instead of an explicit ``if record is None: abort``
    branch in every handler).

    A ``pk`` of ``None`` means "query argument absent" -- bypass
    validation and let the caller's downstream logic supply a
    user-scoped default (e.g. the cash-flow set's primary, or the
    user's current pay period).  The caller MUST
    NOT attempt to use the return value when passing ``None``.

    Audit reference: F-039 + F-098 / commit C-30 of the
    2026-04-15 security remediation plan.

    Args:
        model: The SQLAlchemy model class to look up.  Must expose
            a ``user_id`` column (Pattern A in
            :mod:`app.utils.auth_helpers`).
        pk: The primary key value parsed from a query argument, or
            ``None`` when the argument was not supplied.

    Returns:
        The loaded record on a successful ownership check, or
        ``None`` when ``pk`` was ``None`` (no validation performed).

    Raises:
        werkzeug.exceptions.NotFound: When ``pk`` references a
            non-existent row OR a row owned by a different user.
            Both branches produce the same 404 so the client cannot
            distinguish "no such row" from "not yours" by response
            shape.
    """
    if pk is None:
        return None
    record = get_or_404(model, pk)
    if record is None:
        abort(404)
    return record


def _tab_shell_if_not_htmx(active_tab: str):
    """Return the analytics shell for a direct (non-HTMX) tab GET, else None.

    D13 (analytics tabs as navigation): a tab endpoint is a real URL.  A
    direct browser navigation to it -- a fresh load, a refresh, or a
    bookmark -- carries no ``HX-Request`` header, so instead of redirecting
    to the Calendar default it renders the full shell with ``active_tab``
    pre-selected; the shell then auto-loads that tab's partial.  An HTMX tab
    request returns ``None`` so the caller renders its ``_``-partial as
    before.

    Callers MUST run any ownership validation (e.g.
    :func:`_validate_owned_or_abort`) before calling this, so a cross-user id
    still 404s on a direct navigation rather than being served the shell.

    Args:
        active_tab: The shell's active-tab discriminator -- one of
            ``"calendar"``, ``"spending"``, ``"statements"``, ``"taxes"``.
            A display-only string (no business logic keys off it), matching
            the ``active_statement`` idiom the Statements toggle already uses.

    Returns:
        A rendered-shell response for a non-HTMX request, or ``None`` when
        the request is an HTMX tab load.
    """
    if request.headers.get("HX-Request"):
        return None
    return render_template("analytics/analytics.html", active_tab=active_tab)


@analytics_bp.route("/analytics")
@require_owner
def page():
    """Render the main analytics page with its lazy-loaded tab pills.

    The page contains a nav-pills bar with four pills: Calendar, Spending,
    Statements, and Taxes (the Slice-4 shell collapse).  The Statements pill
    groups the Income Statement and Balance Sheet behind an internal toggle;
    the Spending pill replaced the retired Variance and Trends pills (Slice
    3) and the Taxes pill replaced Year-End (Slice 2).  The Calendar tab
    auto-loads on page visit via HTMX (``active_tab="calendar"``); the others
    load on click.  Each pill pushes its own URL, and a direct GET to a tab
    endpoint renders this shell with that tab active (D13).
    """
    return render_template("analytics/analytics.html", active_tab="calendar")


@analytics_bp.route("/analytics/calendar")
@require_owner
def calendar_tab():
    """HTMX partial: calendar tab with month detail or year overview.

    Query parameters:
        view: 'month' (default) or 'year'.
        year: Calendar year (default: current year).
        month: Calendar month 1-12 (default: current month).
        account_id: The BALANCE account, with the meaning the grid's
            ``?account_id=`` has since plan step ``credit_card:CC-4-3``: the
            day cells hold the paycheck's rows across the owner's cash-flow
            set (checking and its cards, ruling R-CC16) and this names the
            member whose balance line renders; an owned cash-flow account
            outside the set is its own single-account calendar; a refused
            id 404s (below).  Absent, the set's primary.

    A direct (non-HTMX) request renders the analytics shell with the
    Calendar tab active (D13), which then auto-loads this partial.
    """
    # ``today`` is the user's wall-clock day (display timezone), not the
    # server's UTC day: the month default, the today marker, and the
    # elapsed-vs-remaining split all follow the user's calendar per the
    # timezone display policy.
    today = to_display_date(datetime.now(timezone.utc))
    view = request.args.get("view", "month")
    year = request.args.get("year", today.year, type=integer_arg)
    month = request.args.get("month", today.month, type=integer_arg)
    account_id = request.args.get("account_id", None, type=integer_arg)

    # F-039 / commit C-30: a cross-user or non-existent account_id
    # must 404 before any service call.  The underlying
    # ``calendar_service._resolve_account`` falls back to the user's
    # default checking account when ownership fails, which would
    # otherwise mask the IDOR probe behind a normal-looking 200
    # rendered against the requester's own data.
    _validate_owned_or_abort(Account, account_id)

    year = max(2000, min(2100, year))
    month = max(1, min(12, month))

    # D13: a direct (non-HTMX) hit renders the shell with Calendar active --
    # after the ownership guard above, so a cross-user account_id still 404s
    # before any page is served -- and the shell auto-loads this partial.  An
    # HTMX tab request falls through to render the partial below.
    shell = _tab_shell_if_not_htmx("calendar")
    if shell is not None:
        return shell

    settings = db.session.query(UserSettings).filter_by(
        user_id=current_user.id,
    ).first()
    threshold = settings.large_transaction_threshold if settings else 500
    low_balance = settings.low_balance_threshold if settings else 500

    # The settings row this route already holds is the saved-default layer
    # the set's primary reads (plan step CC-4-3): passed down rather than
    # loaded a second time inside the service.
    try:
        if view == "year":
            return _render_year_view(year, account_id, threshold, settings)
        data = calendar_service.get_month_detail(
            user_id=current_user.id, year=year, month=month,
            account_id=account_id, large_threshold=threshold, today=today,
            user_settings=settings,
        )
        return _render_month_view(data, year, month, low_balance, today)
    except CalendarAccountNotResolvableError:
        # F-2: same contract as the CSV branch above -- 404 matches the
        # project security rule ("404 for both 'not found' and 'not
        # yours'", app/utils/auth_helpers.py).
        abort(404)


@analytics_bp.route("/analytics/taxes")
@require_owner
def taxes_tab():
    """HTMX partial: the Taxes tab (refund hero, W-2 preview, Schedule A).

    Renders :func:`app.services.tax_report_service.compute_tax_report` for
    one tax year: the refund hero band with rate/timing chips, the YTD
    checkpoint card (the T-P2 partial, wired to the primary profile), the
    assumptions card, the derivation ledger, the hybrid W-2 preview, and
    the Schedule A check.  A user with no baseline scenario or no active
    salary profile gets the empty state.

    Query parameters:
        year: Tax year (default: the display-timezone current year),
            clamped to [2000, 2100] like the sibling tabs.

    A direct (non-HTMX) request renders the shell with the Taxes tab active
    (D13), which then auto-loads this partial.
    """
    today = to_display_date(datetime.now(timezone.utc))
    year = request.args.get("year", today.year, type=integer_arg)
    year = max(2000, min(2100, year))

    shell = _tab_shell_if_not_htmx("taxes")
    if shell is not None:
        return shell

    report = tax_report_service.compute_tax_report(current_user.id, year, today)
    # ONE producer of "which years may this owner ask about", shared with the
    # Income Statement tab, and it takes a calendar since plan step C2-f3a --
    # it was its own ``ORDER BY start_date ... .first()`` query, a second
    # implementation of a question the derivation answers.
    #
    # **This route DERIVES one where the statement tab threads the one it
    # already holds, and that is a known +1 on THIS render until plan step
    # C11** (ledger rows **P56** and **P69**).  ``tax_report_service`` opens
    # its own read pass and separately calls ``calendar_for`` for its year
    # slice; both are doors that step closes by taking the pass from the route,
    # at which point this derivation becomes the render's only one.  **It named
    # C2-f3e until 2026-08-20**, correctly when written and not afterwards:
    # C11 was a LEAF of C2-f3 and was promoted out of it on 2026-08-19, so
    # C2-f3e shipped without closing either row.  A step id written into a
    # comment has no reconciler: nothing grades this line, so it was
    # corrected by hand at the tick.  Deriving here is the
    # direction that fix goes -- the route is the door -- so the duplicate is a
    # transient of the sequence rather than a second producer being added.
    available_years = _get_available_years(
        calendar_for(current_user.id), today.year,
    )

    if report is None:
        return render_template(
            "analytics/_taxes.html",
            report=None,
            year=year,
            available_years=available_years,
        )

    # The checkpoint card partial's contract (T-P2): the owned profile for
    # its form action plus the year's latest checkpoint.  The report's
    # primary_profile_id carries the service-resolved primary so the
    # primary-profile rule is not re-derived here.
    profile = db.session.get(SalaryProfile, report.primary_profile_id)
    checkpoint = tax_withholding_service.latest_checkpoint(
        report.primary_profile_id, year,
    )
    return render_template(
        "analytics/_taxes.html",
        report=report,
        year=year,
        available_years=available_years,
        display=analytics_view.build_taxes_display(report),
        profile=profile,
        checkpoint=checkpoint,
        errors={},
        save_error=None,
        form_values={},
    )


@analytics_bp.route("/analytics/spending")
@require_owner
def spending_tab():
    """HTMX partial: the Spending tab (S14 "months lead" cockpit, D7).

    Renders :func:`app.services.spending_report_service.compute_spending_report`
    for one calendar month: one pulse canvas (spent hero, vs-prior /
    vs-average / payment-timing chips, and the trailing-12 emphasis month
    chart with click-to-navigate bars), the merged "Where It Went" ledger
    with its By size / By change lens on a month-over-month basis, and the
    Estimate Surprises rail.  The surface is MEASURED (settled expenses
    across the owner's cash-flow set -- the primary grid account and its
    cards, plan step ``credit_card:CC-4-3``); the balance account's name and
    the settled basis are labeled on screen.  A user with no grid-eligible
    account or no baseline scenario gets the empty state.

    The producer accepts pay-period / month / year windows, but S-P2 exposes
    only the calendar-month picker (the S-P1 gate ruling).  The default is the
    most recent COMPLETED month (the prior calendar month); a partial current
    month would mislabel an incomplete total.  Forward navigation is capped at
    the current month, since a measured surface has no settled spend in a
    future month.

    Query parameters:
        month: Calendar month 1-12 (default: the prior month), clamped to
            [1, 12] like the sibling tabs.
        year: Calendar year (default: the prior month's year), clamped to
            [2000, 2100].

    A direct (non-HTMX) request renders the shell with the Spending tab
    active (D13), which then auto-loads this partial.
    """
    today = to_display_date(datetime.now(timezone.utc))
    default_year, default_month = analytics_view.prev_month(
        today.year, today.month,
    )
    month = request.args.get("month", default_month, type=integer_arg)
    year = request.args.get("year", default_year, type=integer_arg)
    month = max(1, min(12, month))
    year = max(2000, min(2100, year))

    shell = _tab_shell_if_not_htmx("spending")
    if shell is not None:
        return shell

    window = spending_report_service.SpendingWindow(
        window_type="month", month=month, year=year,
    )
    report = spending_report_service.compute_spending_report(
        current_user.id, window, user_settings=current_user.settings,
    )

    return render_template(
        "analytics/_spending.html",
        report=report,
        year=year,
        month=month,
        display=(
            analytics_view.build_spending_display(report)
            if report is not None else None
        ),
        chart_json=(
            analytics_view.serialize_spending_chart(report)
            if report is not None else None
        ),
        **analytics_view.build_spending_nav(today, year, month),
    )


@analytics_bp.route("/analytics/variance")
@analytics_bp.route("/analytics/trends")
@analytics_bp.route("/analytics/year-end")
@require_owner
def retired_tab():
    """Redirect a retired Analytics tab URL to the main page (Slice 4).

    The Variance and Trends pills were folded into Spending (Slice 3) and
    the Year-End pill into Taxes (Slice 2); the Slice-4 shell collapse
    removes their nav pills entirely.  These three URLs (and their former
    ``?format=csv`` exports) now 302 to ``/analytics`` so an old bookmark or
    external link lands on the current page instead of 404ing.  All the
    surviving analytics data lives on the four current pills (Calendar,
    Spending, Statements, Taxes).
    """
    return redirect(url_for("analytics.page"))


@analytics_bp.route("/analytics/income-statement")
@require_owner
def income_statement_tab():
    """HTMX partial: confirmed-ledger income statement (Statements pill).

    Reads the append-only posting ledger's Income and Expense accounts
    into a revenue / cost statement over one window
    (:func:`app.services.ledger_report_service.compute_income_statement`),
    for the baseline scenario only (the deferred multi-scenario policy is
    R8's).  Grouped with the Balance Sheet behind the Statements pill's
    internal toggle; ``active_statement="income"`` marks this half active.

    Query parameters:
        window: 'pay_period' (default), 'month', or 'year'.
        period_id: Pay period ID (for the pay_period window).
        month: Month number 1-12 (for the month window).
        year: Calendar year (for the month/year windows).

    A direct (non-HTMX) request renders the shell with the Statements tab
    active (D13), which then auto-loads this partial (the Statements pill's
    income-statement default).

    **THIS RENDER DERIVES THE OWNER'S PAY CALENDAR ONCE, AND ASKS IT EVERY
    "which paycheck" QUESTION** (plan step C2-f3a, ledger rows **P19** and
    **P49**).  It asked FOUR separate reads of ``budget.pay_periods`` before,
    each answering a question the one derivation answers: which period is
    current (``pay_period_service.get_current_period`` -- SQL whose ``.first()``
    carried NO ``ORDER BY``), the whole list for the ``<select>``, the earliest
    period for the year list, and the chosen period's dates for the heading
    label, that last one issued a second time inside
    ``ledger_report_service``.  Measured on the arch fixture: four statements
    naming ``budget.pay_periods`` and zero calendar derivations, against two
    statements and one derivation now.

    **The day is the OWNER'S civil day**, not the container's.  Every one of
    those reads defaulted to ``date.today()``, which is the process clock;
    ``display_today()`` is the clock the user's own screen is in and the one
    ``routes/_period_options.period_move_options`` already uses for the same
    question.  The two agree only because both compose files pin
    ``TZ: America/New_York`` (the 2026-06-12 parity audit's finding M01), so
    this removes a dependence on a deployment setting rather than a live
    defect -- and it fixes CI, a script and a bare ``flask run`` on a
    non-Eastern host, where the pin does not reach.
    """
    # IDOR (the same route-boundary guard the calendar uses for
    # ``account_id``): validate a user-supplied ``period_id`` before
    # ``_resolve_window_params`` reads it.  The statement's money queries
    # are user-scoped (a foreign period yields an empty report), but the
    # service reads the period for its window LABEL un-scoped, so a foreign
    # ``period_id`` would otherwise leak the victim's period dates.
    #
    # **It stays a ``get_or_404`` and is deliberately NOT answered from the
    # calendar below**, though the calendar knows the owner's period ids and
    # could.  ``_validate_owned_or_abort`` delegates to
    # ``auth_helpers.get_or_404``, which EMITS the structured
    # ``resource_not_found`` (INFO) and ``access_denied_cross_user`` (WARNING)
    # events the SOC dashboards read; a membership test against the calendar
    # would refuse identically and record nothing.  The audit trail is the
    # reason this read is not one of the four collapsed below.
    _validate_owned_or_abort(
        PayPeriod, request.args.get("period_id", type=integer_arg),
    )

    # D13: a direct navigation renders the shell (Statements active) after the
    # ownership guard, so a foreign period_id still 404s; the report below is
    # then only computed for the actual HTMX partial render.
    shell = _tab_shell_if_not_htmx("statements")
    if shell is not None:
        return shell

    # **The derivation is BELOW the early return, and an adversarial review of
    # this step is why**: it sat above, so every direct navigation -- the
    # bookmark this D13 branch exists for -- paid two queries and a derivation
    # over the owner's whole payday set and threw the value away, then the
    # auto-loaded partial derived it again.  That is the shape
    # ``pay_calendar._loader.cadence_for``'s docstring already records as a
    # defect: resolving BEFORE a producer's early return.  It also moved a
    # ``PayCalendarError`` (ledger row **P35**, closed at plan step C4-b-2)
    # off the shell render, where a legacy owner would have met a 500 on a page
    # that reads no pay-period data at all.  The ORDER is kept on its first
    # reason -- the wasted derivation -- which never depended on that row.
    today = display_today()
    calendar = calendar_for(current_user.id)

    window_type, period_id, month, year = _resolve_window_params(
        calendar, today,
    )
    window = ledger_report_service.StatementWindow(
        window_type=window_type, period_id=period_id, month=month, year=year,
    )
    report = ledger_report_service.compute_income_statement(
        current_user.id, calendar, window,
    )

    periods = calendar.saved()
    available_years = _get_available_years(calendar, today.year)
    return render_template(
        "analytics/_income_statement.html",
        report=report,
        window_type=window_type,
        period_id=period_id,
        month=month,
        year=year,
        periods=periods,
        available_years=available_years,
        active_statement="income",
    )


@analytics_bp.route("/analytics/balance-sheet")
@require_owner
def balance_sheet_tab():
    """HTMX partial: confirmed-ledger balance sheet as of a date.

    Reads the posted ledger's position as of ``as_of``
    (:func:`app.services.ledger_report_service.compute_balance_sheet`):
    Assets, Liabilities, and Equity sections with a derived
    retained-earnings line and a two-part trial-balance tie-out, for the
    baseline scenario only.  Grouped with the Income Statement behind the
    Statements pill's internal toggle; ``active_statement="balance"`` marks
    this half active.

    Query parameters:
        as_of: ISO date (YYYY-MM-DD); defaults to today and is clamped to
            [2000-01-01, today].

    A direct (non-HTMX) request renders the shell with the Statements tab
    active (D13), which then auto-loads the Statements pill's income-statement
    default; the internal toggle reaches this balance sheet.
    """
    # D13: a direct navigation renders the shell (Statements active); the
    # balance sheet below is only computed for the actual HTMX partial render.
    shell = _tab_shell_if_not_htmx("statements")
    if shell is not None:
        return shell

    today = date.today()
    as_of = _resolve_as_of_param(request.args.get("as_of"), today)
    report = ledger_report_service.compute_balance_sheet(
        current_user.id, as_of,
    )

    return render_template(
        "analytics/_balance_sheet.html",
        report=report,
        as_of=as_of,
        today=today,
        active_statement="balance",
    )


# ── Calendar helpers ────────────────────────────────────────────────


def _render_month_view(data, year, month, low_balance, today):
    """Render the month detail calendar view from resolved MonthSummary data.

    Builds a 7-column Sun-Sat calendar grid (each day carrying its projected
    end-of-day balance) and passes the low-balance threshold through for the
    flow strip line and the below-threshold cell coloring.  The caller
    resolves ``data`` via :func:`calendar_service.get_month_detail` (with
    ``today``) so the fetch and its 404 handling stay in the route.

    ``month_end_balance`` is the RUNNING end-of-day balance on the month's
    last calendar day (``daily.daily_balances``), not the period-flat
    ``MonthSummary.projected_end_balance`` scalar -- the P1 as-built ruling:
    the month-end tile must agree with the flow strip's right end (the two
    differ when the month ends mid-period).
    """
    # Build calendar grid (Sunday-start weeks).
    weeks = analytics_view.build_calendar_weeks(year, month, data, today)

    # Compute prev/next month navigation.
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    month_name = cal_mod.month_name[month]

    # Direct indexing, not .get(): the daily-series producer guarantees a
    # key for every day of the range, so a missing last day is a producer
    # defect that must fail loud (KeyError), not silently hide the chip --
    # the same contract the flow-strip serializer relies on.
    days_in_month = cal_mod.monthrange(year, month)[1]
    month_end_balance = (
        data.daily.daily_balances[days_in_month]
        if data.daily is not None else None
    )

    return render_template(
        "analytics/_calendar_month.html",
        data=data,
        weeks=weeks,
        year=year,
        month=month,
        month_name=month_name,
        prev_month=prev_month,
        prev_year=prev_year,
        next_month=next_month,
        next_year=next_year,
        low_balance_threshold=low_balance,
        today=today,
        month_end_balance=month_end_balance,
        account_name=data.account_name,
        flow_strip_json=analytics_view.serialize_flow_strip(
            data, low_balance, today, year, month,
        ),
    )


def _render_year_view(year, account_id, threshold, settings):
    """Render the year overview with 12 month cards."""
    data = calendar_service.get_year_overview(
        user_id=current_user.id,
        year=year,
        account_id=account_id,
        large_threshold=threshold,
        user_settings=settings,
    )

    # Attach month names to each MonthSummary for template display.
    month_cards = []
    for ms in data.months:
        month_cards.append({
            "summary": ms,
            "name": cal_mod.month_name[ms.month],
        })

    return render_template(
        "analytics/_calendar_year.html",
        data=data,
        month_cards=month_cards,
        year=year,
    )


# ── Window helpers ─────────────────────────────────────────────────
# ``_resolve_window_params`` parses the Income Statement tab's window
# selector (the Variance tab that once shared it is retired).


def _resolve_window_params(calendar, today):
    """Parse and apply defaults for the window query parameters.

    The ``window`` / ``period_id`` / ``month`` / ``year`` parsing for the
    Income Statement tab, which discriminates a pay-period / month / year
    window (building a ``StatementWindow`` over the result).  Applies the
    defaults: a bare ``pay_period`` window resolves the user's current
    period (falling back to their most recent, then to a month window when
    the user has no periods at all); a bare ``month`` / ``year`` window fills
    *today*'s fields.  A calendar ``month`` / ``year`` is range-clamped here
    (month to 1-12, year to 2000-2100, the ``calendar_tab`` convention) so a
    hand-crafted out-of-range value cannot reach ``date()`` /
    ``calendar.monthrange()`` in the statement service and raise; the clamp
    is a no-op for the in-range defaults and for the ``pay_period`` window
    (whose ``month`` / ``year`` stay ``None``).

    **Both period reads come off the CALENDAR the caller derived** (plan step
    C2-f3a).  ``period_containing`` is the SAVED-only search, which is exactly
    what ``pay_period_service.get_current_period`` answered -- not
    ``span_containing``, whose projection past the horizon carries
    ``period_id = None`` and would put a ``None`` into ``StatementWindow`` that
    reads as "no window chosen".  The fall-through to a MONTH window for an
    owner with no periods at all is preserved and is why ``None`` here must
    stay a real answer.

    Args:
        calendar: The render's :class:`~app.services.pay_calendar.PayCalendar`,
            derived once by the caller.
        today: The owner's civil day (``display_today()``), supplied by the
            caller so this page holds ONE clock.

    Returns:
        Tuple of (window_type, period_id, month, year); ``month`` and
        ``year`` are either ``None`` or in the clamped ranges above.
    """
    window_type = request.args.get("window", "pay_period")
    if window_type not in ("pay_period", "month", "year"):
        window_type = "pay_period"

    period_id = request.args.get("period_id", type=integer_arg)
    month = request.args.get("month", type=integer_arg)
    year = request.args.get("year", type=integer_arg)

    if window_type == "pay_period" and period_id is None:
        current = calendar.period_containing(today)
        if current is None:
            saved = calendar.saved()
            current = saved[-1] if saved else None
        if current is not None:
            period_id = current.period_id
        else:
            window_type = "month"
            month = today.month
            year = today.year
    if window_type == "month":
        if month is None:
            month = today.month
        if year is None:
            year = today.year
    if window_type == "year" and year is None:
        year = today.year

    # Range-clamp the calendar fields (a no-op for defaults and for a
    # pay_period window's None fields) so neither tab's service constructs
    # an out-of-range date from a hand-crafted query string.
    if month is not None:
        month = max(1, min(12, month))
    if year is not None:
        year = max(2000, min(2100, year))

    return window_type, period_id, month, year


def _get_available_years(calendar, current_year):
    """Build the list of years for the year selector dropdown.

    Spans from the user's earliest pay period year through the
    current year, or just the current year if no periods exist.

    **It was its own ``ORDER BY start_date ... .first()`` query** until plan
    step C2-f3a, which is a fourth read of ``budget.pay_periods`` on a render
    that now derives the schedule once;
    :meth:`~app.services.pay_calendar.PayCalendar.opening_bound` is the same
    question and the same answer, and it is ``None`` for the same owner the
    query returned no row for.

    Args:
        calendar: The render's :class:`~app.services.pay_calendar.PayCalendar`.
        current_year: Today's year as an upper bound.

    Returns:
        List of year integers in descending order.
    """
    opening = calendar.opening_bound()
    start_year = opening.year if opening is not None else current_year
    return list(range(current_year, start_year - 1, -1))


# ── Balance sheet helpers ──────────────────────────────────────────


def _resolve_as_of_param(raw, today):
    """Parse the balance sheet ``as_of`` query arg to a clamped date.

    Args:
        raw: The raw ``as_of`` query value (an ISO ``YYYY-MM-DD`` string),
            or ``None`` when the argument was absent.
        today: The current date -- both the default and the upper clamp
            bound.

    Returns:
        The parsed date clamped to ``[2000-01-01, today]``; ``today`` when
        *raw* is absent or is not a valid ISO date (a future or pre-2000
        as-of is meaningless for the posted ledger, and a garbage value
        must degrade to today rather than raise).
    """
    floor = date(2000, 1, 1)
    if not raw:
        return today
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return today
    return max(floor, min(today, parsed))
