"""
Shekel Budget App -- Calendar Service

Groups transactions by calendar month and day, computes per-month
income/expense/net totals, detects 3rd-paycheck months, flags
large/infrequent transactions, and projects month-end balances.

Pure-function service -- no Flask imports, no database writes.
"""

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.user import UserSettings
from app.services import balance_at
from app.services.account_resolver import resolve_analytics_cash_flow_set
from app.services.calendar_day_flows import (
    MAX_VISIBLE_DAY_FLOWS,
    DayEntry,
    DayOverflow,
    build_day_entry,
    build_estimated_day_entry,
    day_overflow,
    fold_income_expense,
    order_for_display,
)
from app.services.calendar_infrequency import badge_cadence
from app.services.calendar_items import SpanItems, span_items
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCadence,
    projection_paydays_in_month_through,
)
from app.services.balance_at import BalanceContext
from app.services.transfer_legs import PlanItem, cell_key

logger = logging.getLogger(__name__)

class CalendarAccountNotResolvableError(LookupError):
    """Raised when the calendar cannot resolve a backing ACCOUNT.

    After Commits 3-8 of the main remediation locked the E-19 / CRIT-01
    invariant ("anchor is never NULL; ``resolve_anchor`` raises or
    returns a valid ``AnchorPoint``"), a ``None`` outcome from
    :func:`~app.services.account_resolver.resolve_analytics_cash_flow_set`
    (``resolve_analytics_account`` until plan step CC-4-3) -- an explicit
    ``account_id`` the admission test refuses, or an owner with no
    grid-eligible account -- indicates an *upstream* defect (a deleted
    analytics account) or a question the surface cannot answer, not a normal
    "empty calendar" state.  Pre-F-2 the service silently substituted a zeroed
    :class:`MonthSummary` / :class:`YearOverview`, which masked the upstream bug
    behind a ``$0.00`` calendar shown to the user with no error.  Raising
    instead lets the route layer answer with the project-standard 404
    ("404 for both 'not found' and 'not yours'", see
    :mod:`app.utils.auth_helpers`).

    **It no longer covers a missing baseline SCENARIO** (plan step X-v2, ruling
    R-BW).  Both entries used to raise this for that state too, and the route
    turned it into a 404 -- a seventh answer to a question fifteen other
    surfaces were each answering differently, and the least useful of them: a
    404 tells a user with a repairable setup problem that their calendar does
    not exist.  The seam now raises
    :class:`~app.exceptions.BaselineMissingError` and one application-level
    handler renders the repair.  Two conditions that shared one exception are
    two exceptions again, and this one means exactly what its name says.
    """


@dataclass(frozen=True)
class DailyView:  # pylint: disable=too-many-instance-attributes
    """The month's daily running-balance projection for the calendar surface.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because this
    is one cohesive value record: the month view's daily projection, read as
    a unit by the calendar month template and its flow strip.  Splitting it
    would fragment a single concept (the running balance and the summary-strip
    figures derived from it) for no design gain.

    The balances are projected-only and entry-aware, sourced from the balance
    seam's :func:`~app.services.balance_at.cash_daily_balance_series`, so the
    day-cell end-of-day hero and the flow strip line share one basis and
    reconcile with the grid.  The ``elapsed_*`` / ``remaining_*`` figures are
    the MEASURED / PROJECTED nominal in-out folds (from ``day_totals``, the
    same contribution basis the day cells show), so they tie to the
    day cells beside them; they COINCIDE with the projected balances in
    ordinary data but deliberately diverge where the projection excludes a
    settled row (already in the anchor), applies an envelope's entry-aware
    reservation, or takes a live override -- the measured-vs-modeled
    distinction the presentation labels, not a defect.  ``None`` on
    :class:`MonthSummary` for the year overview (which does not render daily
    balances; slice-1 scope).

    Attributes:
        daily_balances: ``{day_of_month: Decimal}`` projected end-of-day
            running balance, one entry per calendar day in the month.
        trough_day: The day of the month's minimum end-of-day balance (the
            earliest such day on a tie), or ``None`` for an empty month.
        trough_balance: That minimum end-of-day balance, or ``None``.
        balance_today: The end-of-day balance on the current day when it
            falls in this month (display timezone), else ``None``.
        elapsed_income / elapsed_expense: Income / expense that has landed on
            or before today (measured-so-far); the whole month when the month
            is entirely past.
        remaining_income / remaining_expense: Income / expense still projected
            after today; the whole month when the month is entirely future.
    """

    daily_balances: dict[int, Decimal]
    trough_day: int | None
    trough_balance: Decimal | None
    balance_today: Decimal | None
    elapsed_income: Decimal
    elapsed_expense: Decimal
    remaining_income: Decimal
    remaining_expense: Decimal


@dataclass(frozen=True)
class MonthSummary:  # pylint: disable=too-many-instance-attributes
    """Aggregated data for one calendar month.

    Pylint: ``too-many-instance-attributes`` (13/7) -- suppressed
    because this is a cohesive single-return aggregate -- one calendar
    month's summary -- whose fields are flat columns read together by the
    calendar surface: the month and year templates render the money fields
    and is_third_paycheck_month, and the CSV year export emits them as one
    row per month.  The three money fields are the month's headline
    numbers read individually, not a sub-object read as a unit, so there is
    no section to extract; nesting would fragment one contract across the
    templates and the exporter for no design gain.  ``day_totals`` is the
    per-day income/expense map (parallel to ``day_entries``) the analytics
    calendar route renders directly, so the route does no money math of
    its own.  ``day_overflow`` is the parallel per-day "+N more" residual;
    ``daily`` bundles the whole daily running-balance projection as one
    cohesive sub-object (:class:`DailyView`), ``None`` for the year overview.
    ``account_name`` is the BALANCE account's display name (the on-screen
    scope label the month template renders so the scope is stated, not silent
    -- the analytics-audit cross-cutting fix).  Since plan step CC-4-3 the
    day cells hold the paycheck's rows across the owner's cash-flow set
    (ruling ``R-CC16``) while the balance line, the month-end figure and this
    name stay one member's.

    ``projected_end_balance`` is the period-flat seam scalar at the last
    calendar day (the containing period's end); the month view's honest
    "balance on the last day of the month" is ``daily.daily_balances[last]``,
    which the flow strip ends on -- the two differ when the month ends
    mid-period, and the month template uses the running value so the strip
    and the month-end tile agree.
    """

    year: int
    month: int
    total_income: Decimal
    total_expenses: Decimal
    net: Decimal
    projected_end_balance: Decimal
    is_third_paycheck_month: bool
    day_entries: dict[int, list[DayEntry]]
    day_totals: dict[int, tuple[Decimal, Decimal]]
    day_overflow: dict[int, DayOverflow]
    paycheck_days: list[int]
    daily: DailyView | None
    account_name: str


@dataclass(frozen=True)
class YearOverview:
    """12-month year overview data."""

    year: int
    months: list[MonthSummary]
    annual_income: Decimal
    annual_expenses: Decimal
    annual_net: Decimal


def get_month_detail(  # pylint: disable=too-many-arguments
    user_id: int,
    year: int,
    month: int,
    account_id: int | None = None,
    large_threshold: int = 500,
    *,
    today: date | None = None,
    user_settings: UserSettings | None,
) -> MonthSummary:
    """Compute calendar data for a single month.

    Queries transactions for pay periods that overlap the given month,
    assigns each transaction to a calendar day via due_date (clamped into
    its pay period), and computes income/expense totals, projected
    month-end balance, large/infrequent flags, and -- when ``today`` is
    supplied -- the daily running-balance projection (:class:`DailyView`).

    **Past the owner's last saved paycheck the entries are ESTIMATED**
    (plan step ``pay_calendar:C9``, task plan#3945, closing ledger row
    **N-394**): no row exists there, so each recurring definition's
    occurrence sits where its row would, read off the cash folds the balance
    line folds (:func:`~app.services.calendar_items.span_items`), and the
    payday markers count those paychecks too.  The card listed nothing there
    before, beside a balance that moved.

    **The rows are the paycheck's across the owner's CASH-FLOW SET since plan
    step ``credit_card:CC-4-3``** (ruling ``R-CC16``): the primary grid
    account plus its active cards, read through the one clause every
    plan-item reader appends, with the balance line still ONE member's.
    ``account_id`` keeps the meaning the grid gives it -- a member puts its
    balance on the line behind the set's rows; an owned cash-flow account
    outside the set (a savings account) is that account's single-account
    calendar as before -- and keeps this surface's own policy for a refused
    id (another owner's, archived, a loan, unknown): a 404, never a fall
    through (:func:`~app.services.account_resolver.resolve_analytics_cash_flow_set`).
    The DEFAULT is the set's primary -- the owner's saved default grid
    account, else their first active checking account, else their first
    grid-eligible account -- where it was the first active checking account
    alone; the one definition of the primary every cash-flow surface reads.

    Pylint: ``too-many-arguments`` (7/5) -- these seven are independent
    calendar-render inputs, not a cohesive entity: the owner id, the target
    year and month, the optional account scope, the large-flag threshold, the
    display-tz ``today`` that gates the daily view, and the settings row the
    primary reads.  They are passed straight through from the route's own
    request args and the row it already holds, so a param object would be
    stamp coupling; ``get_year_overview`` shares the same non-cohesive shape
    minus ``today``.

    Args:
        user_id: The user's ID.
        year: Calendar year.
        month: Calendar month (1-12).
        account_id: The BALANCE account, as the grid's ``?account_id=``
            override: a member of the set or an owned cash-flow account
            outside it.  Defaults to the set's primary.
        large_threshold: Amount at or above which a transaction is
            flagged as large.
        today: The current date in the display timezone (the route resolves
            it).  When supplied, the month's daily running-balance view is
            computed and attached as :attr:`MonthSummary.daily` (the flow
            strip, day-cell balances, and elapsed/remaining split need it);
            when ``None`` (aggregate callers that do not render daily
            balances) ``daily`` is ``None`` and no balance-series read runs.
        user_settings: The owner's ``UserSettings`` row, or ``None`` when
            they have none -- the saved-default layer the primary reads,
            which this surface did not consult before plan step CC-4-3.
            Keyword-only and REQUIRED, with ``None`` a value a caller states
            rather than a default it can fall into: an omitted row would
            resolve a different primary than the grid's for an owner with a
            saved default, silently -- the drift this step deletes -- and a
            missing argument fails at the call instead.  The route passes
            the row it already loaded.

    Returns:
        A MonthSummary with day-level and aggregate data.

    Raises:
        CalendarAccountNotResolvableError: An explicit ``account_id`` the
            admission test refuses, or an owner with no grid-eligible account
            -- the route turns it into a 404.
        PayCalendarError: See
            :func:`~app.services.calendar_infrequency.badge_cadence`.
        RecurrenceResolutionError: See
            :func:`~app.services.calendar_infrequency.is_infrequent`.  NEW at
            plan step R7a-2b together with the one above, and the unhandled
            500 is the intended answer -- ``routes/analytics.py`` catches only
            the first of these three.
    """
    cash_flow = resolve_analytics_cash_flow_set(
        user_id, user_settings, account_id,
    )
    if cash_flow is None:
        raise CalendarAccountNotResolvableError(
            f"Analytics account not resolvable for user_id={user_id} "
            f"account_id={account_id} year={year} month={month}",
        )

    balance_ctx = BalanceContext.build(user_id)
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    # ONE resolution of the month's paychecks and their items, threaded into
    # every consumer (plan step C2-f1; :class:`~app.services.calendar_items
    # .SpanItems` carries what it collapsed).
    items = span_items(cash_flow, balance_ctx, first_day, last_day)

    ctx = _MonthBuildContext(
        year=year, account=cash_flow.balance, items=items,
        large_threshold=large_threshold,
        balance_ctx=balance_ctx, today=today,
        pay_cadence=badge_cadence(user_id, items.transactions, items.estimated),
    )
    return _build_month_summary(month, ctx)


def get_year_overview(
    user_id: int,
    year: int,
    account_id: int | None = None,
    large_threshold: int = 500,
    *,
    user_settings: UserSettings | None,
) -> YearOverview:
    """Compute 12-month overview for a calendar year.

    Fetches all transactions for the year in a single query, then
    partitions by month in Python to avoid 12 database round trips.  The
    scope is :func:`get_month_detail`'s: the owner's cash-flow set behind
    one balance line (plan step CC-4-3).

    Args:
        user_id: The user's ID.
        year: Calendar year.
        account_id: The BALANCE account, as :func:`get_month_detail` takes
            it.  Defaults to the set's primary.
        large_threshold: Large transaction threshold.
        user_settings: The owner's ``UserSettings`` row, or ``None`` -- as
            :func:`get_month_detail` takes it: keyword-only and required.

    Returns:
        A YearOverview with 12 MonthSummary entries (Jan-Dec).

    Raises:
        The three :func:`get_month_detail` raises, for the same reasons.
    """
    cash_flow = resolve_analytics_cash_flow_set(
        user_id, user_settings, account_id,
    )
    if cash_flow is None:
        raise CalendarAccountNotResolvableError(
            f"Analytics account not resolvable for user_id={user_id} "
            f"account_id={account_id} year={year}",
        )

    balance_ctx = BalanceContext.build(user_id)
    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    # ONE resolution, threaded -- see ``get_month_detail`` for what C2-f1 collapsed.
    items = span_items(cash_flow, balance_ctx, first_day, last_day)

    ctx = _MonthBuildContext(
        year=year, account=cash_flow.balance, items=items,
        large_threshold=large_threshold,
        balance_ctx=balance_ctx, today=None,
        pay_cadence=badge_cadence(user_id, items.transactions, items.estimated),
    )
    months = [_build_month_summary(m, ctx) for m in range(1, 13)]

    annual_income = sum(ms.total_income for ms in months)
    annual_expenses = sum(ms.total_expenses for ms in months)

    return YearOverview(
        year=year,
        months=months,
        annual_income=annual_income,
        annual_expenses=annual_expenses,
        annual_net=annual_income - annual_expenses,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _assign_transactions_to_days(
    ctx: "_MonthBuildContext",
    month: int,
) -> tuple[
    dict[int, list[DayEntry]],
    dict[int, tuple[Decimal, Decimal]],
    dict[int, DayOverflow],
]:
    """Assign transactions to calendar days and fold per-day totals.

    Returns the day_map, the per-day ``{day: (income, expense)}`` totals
    map, and the per-day ``{day: DayOverflow}`` collapse map for the target
    month.  Deduplicates by item key (:func:`~app.services.transfer_legs
    .cell_key`: a row's id, a leg's pair) to prevent double-counting when
    periods overlap month boundaries.

    Each day's entries are ordered income first, then expenses by descending
    magnitude (the locked calendar anatomy); the first
    :data:`MAX_VISIBLE_DAY_FLOWS` are the visible named lines and any beyond
    are summarized in :class:`DayOverflow` (count plus signed residual net).

    **Which items count is the SQL's alone** (F-3 / W-065's locked Choice-2
    predicate, in :func:`~app.services.calendar_items
    .query_transactions_for_range`, the one query whose rows and legs this
    folds).  Every item was re-checked here against
    :func:`~app.utils.balance_predicates.is_balance_contributing` until ruling
    **plan#2262** deleted that -- the remediation's belt-and-suspenders, a
    second statement of the predicate over items the SQL had already
    filtered.  It could drop only what the SQL dropped, so no figure moved;
    what it did was hide the SQL filter from every behaviour test (switching
    the filter off left them green), so the filter was graded only by two
    tests that stubbed both statements of it.  The cancelled and credit
    tests grade the one statement now.

    **It takes the CONTEXT rather than the five settings it reads off it**
    (plan step X-au-c2).  Adding ``contributions`` -- what each row is WORTH,
    resolved once for the whole build -- took the plain-data signature to six
    arguments, and the project's rule for a PRIVATE helper is to decompose
    rather than to wrap arguments in an object (``docs/plans/lessons.md``).
    There is nothing to decompose here: the bundle already exists, this helper
    has exactly one caller, and every one of those settings is a field of the
    context that caller is holding.  Taking it directly is what stops the rows
    and their valuation from arriving as two arguments a caller could mismatch,
    which is the hazard :class:`~app.services.cash_ledger.AmountBasis`
    states one tier down.

    Args:
        ctx: The build's :class:`_MonthBuildContext` -- its ``items`` (the
            ``transactions``, their ``contributions`` and the ``estimated``
            occurrences), the ``year``, the ``large_threshold`` and the
            ``pay_cadence``.  ``contributions`` is indexed with ``[]``: a row
            missing from it is a build that priced a different set, and a
            default would be a fabricated figure in a day cell.
        month: Target calendar month (1-12).
    """
    threshold = Decimal(str(ctx.large_threshold))

    seen: set = set()
    day_map: dict[int, list[DayEntry]] = defaultdict(list)

    # The spans the rows were SELECTED by, keyed for PLACEMENT (plan step
    # C2-f1): the fetch was by membership of this very window, so the indexing
    # below is total by construction rather than by luck.
    spans = {period.period_id: period for period in ctx.items.periods}

    for txn in ctx.items.transactions:
        key = cell_key(txn)
        if key in seen:
            continue
        display_day = _get_display_day(
            txn, spans[txn.pay_period_id], month, ctx.year,
        )
        if display_day is None:
            continue

        seen.add(key)
        entry = build_day_entry(
            txn, ctx.items.contributions[key], threshold, ctx.pay_cadence,
        )
        day_map[display_day].append(entry)

    # Past the last saved paycheck, each estimated occurrence sits where its
    # row would: its attribution day, the rule ``_get_display_day`` reads for
    # a row.  No row answers one, so nothing here can be counted twice.
    for flow in ctx.items.estimated:
        display_day = _day_in_month(flow.attributed_on, month, ctx.year)
        if display_day is not None:
            day_map[display_day].append(
                build_estimated_day_entry(flow, threshold, ctx.pay_cadence),
            )

    # The three day-level readings of one amount live together in
    # :mod:`~app.services.calendar_day_flows`, which is where the rule that
    # two are FIGURES and one is a RANKING is written down.
    for day in day_map:
        order_for_display(day_map[day])

    day_totals = {
        day: fold_income_expense(entries)
        for day, entries in day_map.items()
    }
    overflow = {
        day: day_overflow(entries)
        for day, entries in day_map.items()
        if len(entries) > MAX_VISIBLE_DAY_FLOWS
    }

    return dict(day_map), day_totals, overflow


@dataclass(frozen=True)
class _MonthBuildContext:
    """The pre-queried data and config shared across a year's month summaries.

    ``get_year_overview`` resolves the cash-flow set and the span's items
    (:class:`~app.services.calendar_items.SpanItems`: its periods, their rows
    and legs, what each is worth, and the occurrences estimated past the last
    saved paycheck) once and builds twelve summaries from them, varying only
    the month (``get_month_detail`` builds one).
    ``account`` is the set's BALANCE line -- the member whose running
    balance, month-end figure and name the summaries carry; the rows were
    selected across the whole set before this context was built, so the set
    itself does not ride here.
    Bundling these into the context the build shares keeps
    :func:`_build_month_summary` a two-argument call and makes that
    resolved-once-reused relationship explicit.

    ``today`` is the display-timezone current date supplied by the month
    view (``None`` for the year overview): when set, the build computes the
    month's daily running-balance :class:`DailyView`; when ``None`` no
    balance-series read runs and ``MonthSummary.daily`` stays ``None``.

    ``pay_cadence`` is the owner's, for the infrequent badge, and is ``None``
    exactly when nothing in this build repeats -- see
    :func:`~app.services.calendar_infrequency.badge_cadence`.

    Seven fields, where it was ten until task plan#3945 moved the four the
    span's items are into one record.
    """

    year: int
    account: Account
    items: SpanItems
    large_threshold: int
    balance_ctx: BalanceContext
    today: date | None
    pay_cadence: PayCadence | None


def _build_month_summary(month: int, ctx: _MonthBuildContext) -> MonthSummary:
    """Assemble a MonthSummary for one month from pre-queried context.

    Assigns each transaction to a calendar day via _get_display_day,
    deduplicating by transaction ID to prevent double-counting when
    periods overlap month boundaries.

    Args:
        month: Target calendar month (1-12).
        ctx: The year/account/items/threshold/pass shared across the build
            (see :class:`_MonthBuildContext`).

    Returns:
        A MonthSummary for the target month.
    """
    day_entries, day_totals, overflow = _assign_transactions_to_days(
        ctx, month,
    )
    # Month headline totals are the sum of the per-day folds, so the
    # month and per-day numbers derive from the one fold_income_expense
    # rule and cannot disagree.
    total_income = sum(
        (income for income, _expense in day_totals.values()), Decimal("0"),
    )
    total_expenses = sum(
        (expense for _income, expense in day_totals.values()), Decimal("0"),
    )

    end_balance = _compute_month_end_balance(
        ctx.account, ctx.year, month, ctx.balance_ctx,
    )
    # ONE read of "which paydays does this month hold", answering both the
    # markers and the three-paycheck flag (plan step **balance:X-bh-1**): two
    # scans of ``ctx.periods`` before it, the second of which
    # (``_detect_third_paycheck_months``) rebuilt a whole-year set per card AND
    # was a second implementation of what ``paycheck_calculator`` asks to
    # decide a 24-per-year deduction's cadence.  The paydays this card holds
    # money for (ruling **balance:R-IB**): saved, and projected past the last
    # saved one since task plan#3945 gave those paychecks their estimated
    # entries, but never the rhythm before the first payday, where nothing is
    # estimated.  ``projection_paydays_in_month_through`` states it beside its
    # twin.
    month_paydays = projection_paydays_in_month_through(
        ctx.balance_ctx.calendar(),
        date(ctx.year, month, calendar.monthrange(ctx.year, month)[1]),
    )
    paycheck_days = sorted({payday.day for payday in month_paydays})

    daily = (
        _compute_daily_view(ctx, month, day_totals)
        if ctx.today is not None else None
    )

    return MonthSummary(
        year=ctx.year,
        month=month,
        total_income=total_income,
        total_expenses=total_expenses,
        net=total_income - total_expenses,
        projected_end_balance=end_balance,
        is_third_paycheck_month=len(month_paydays) >= 3,
        day_entries=day_entries,
        day_totals=day_totals,
        day_overflow=overflow,
        paycheck_days=paycheck_days,
        daily=daily,
        account_name=ctx.account.name,
    )


def _compute_daily_view(
    ctx: _MonthBuildContext,
    month: int,
    day_totals: dict[int, tuple[Decimal, Decimal]],
) -> DailyView:
    """Build the month's daily running-balance projection.

    Reads the day-by-day end-of-day balance from the balance seam
    (:func:`~app.services.balance_at.cash_daily_balance_series`), derives the
    month trough (earliest day of the minimum balance) and today's balance,
    and splits the per-day income/expense folds into elapsed (on or before
    today) and remaining (after today).  ``ctx.today`` is the display-timezone
    current date; a month entirely in the past is all elapsed, one entirely
    in the future all remaining.

    Args:
        ctx: The build context (account, scenario, and display-tz ``today``,
            which must not be ``None`` here -- the caller gates on it).
        month: Target calendar month (1-12).
        day_totals: The per-day ``{day: (income, expense)}`` folds, split at
            ``today`` into the elapsed / remaining summary-strip figures.

    Returns:
        The assembled :class:`DailyView`.
    """
    first_day = date(ctx.year, month, 1)
    last_day = date(ctx.year, month, calendar.monthrange(ctx.year, month)[1])
    series = balance_at.cash_daily_balance_series(
        ctx.account, ctx.balance_ctx, first_day, last_day,
    )
    daily_balances = {day.day: balance for day, balance in series.items()}

    trough_day, trough_balance = _find_trough(daily_balances)

    today = ctx.today
    if today > last_day:
        split_day, balance_today = last_day.day, None
    elif today < first_day:
        split_day, balance_today = 0, None
    else:
        split_day, balance_today = today.day, daily_balances.get(today.day)

    elapsed = _fold_split(day_totals, split_day, True)
    remaining = _fold_split(day_totals, split_day, False)

    return DailyView(
        daily_balances=daily_balances,
        trough_day=trough_day,
        trough_balance=trough_balance,
        balance_today=balance_today,
        elapsed_income=elapsed[0],
        elapsed_expense=elapsed[1],
        remaining_income=remaining[0],
        remaining_expense=remaining[1],
    )


def _find_trough(
    daily_balances: dict[int, Decimal],
) -> tuple[int | None, Decimal | None]:
    """Return the (day, balance) of the month's minimum end-of-day balance.

    The series is day-ascending, so a strict ``<`` comparison keeps the
    EARLIEST day on a tie.  An empty month (no days) returns ``(None, None)``.

    Args:
        daily_balances: The ``{day: Decimal}`` end-of-day balances.

    Returns:
        ``(trough_day, trough_balance)``, or ``(None, None)`` when empty.
    """
    trough_day: int | None = None
    trough_balance: Decimal | None = None
    for day, balance in daily_balances.items():
        if trough_balance is None or balance < trough_balance:
            trough_day, trough_balance = day, balance
    return trough_day, trough_balance


def _fold_split(
    day_totals: dict[int, tuple[Decimal, Decimal]],
    split_day: int,
    elapsed: bool,
) -> tuple[Decimal, Decimal]:
    """Sum the per-day income / expense folds on one side of ``split_day``.

    ``elapsed`` selects days on or before ``split_day`` (measured so far);
    otherwise days strictly after it (still projected).  Both legs seed at
    ``Decimal("0")`` so money stays ``Decimal``.

    Args:
        day_totals: The per-day ``{day: (income, expense)}`` folds.
        split_day: The day the month splits at (today's day, or a whole-month
            boundary for a fully past / future month).
        elapsed: ``True`` for the elapsed side (day <= split_day), ``False``
            for the remaining side (day > split_day).

    Returns:
        ``(income, expense)`` summed over the selected side.
    """
    income = sum(
        (inc for day, (inc, _exp) in day_totals.items()
         if (day <= split_day) == elapsed),
        Decimal("0"),
    )
    expense = sum(
        (exp for day, (_inc, exp) in day_totals.items()
         if (day <= split_day) == elapsed),
        Decimal("0"),
    )
    return income, expense


def _get_display_day(
    txn: PlanItem,
    period: DerivedPeriod,
    target_month: int,
    target_year: int,
) -> int | None:
    """Determine the calendar day to display a transaction on.

    Returns the day-of-month when the transaction's attribution date falls in
    the target month, or None otherwise (preventing double-counting across
    month boundaries).

    The attribution date is the shared
    :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day` rule --
    ``due_date`` (fallback: the pay period ``start_date``) clamped into the
    transaction's own pay period span, which prevents a due_date that strays
    just outside its period from leaking a flow onto a neighboring period's
    day.  **It is asked OF the period since plan step C4-a-2**, which deleted
    the three-argument ``utils.dates.attribution_date`` this called: the span
    and the rule now travel as one value.

    **The span is the one the row was SELECTED by, passed in for exactly that
    reason** (plan step C2-f1, caught by adversarial review before it shipped).
    It read ``txn.pay_period`` -- the STORED ``end_date`` -- while
    :func:`get_month_detail` moved its SELECTION onto the DERIVED calendar,
    splitting one function across two clocks.  Where the two disagree (plan
    finding **P12**: a no-op ``/pay-periods/generate`` post rewrites
    ``cadence_days`` without writing a row, so the last period's derived end
    moves and its stored end does not) a row could be CLAMPED into April by the
    stored span while its period was excluded from April's selection by the
    derived one -- absent from the day cells, from ``total_expenses`` and from
    ``net``, silently.  One value answers both now, and the pairing cannot be
    got wrong: the fetch was BY membership of that window.

    **It no longer places a flow on the same day as the balance step for it,
    and that is an open fork rather than a settled rule** (plan step X-c2b2,
    finding N-58).  The balance line under these cells is the cash fold now:
    a SETTLED row steps it on the day its money moved (``settled_on``, the
    display-timezone civil day since ruling R-DH (b)) and a still-projected one
    on ``max(attribution, as_of + 1)`` (ruling R-G).  Neither is the budget
    attribution date this function returns, so a chip and its own balance step
    can sit days apart -- median 2, p75 7, max 25 over 126 settled Checking
    rows, re-measured 2026-08-28 (N-42).  The two agreed by construction
    before the cutover because the retired ramp distributed the same
    still-projected rows over these same attribution days.  The grid met the
    identical split and answered it with ruling R-K's remainder rows row; the
    calendar has no such row yet, and which way it should go -- move
    the chip to the cash clock, add a reconciling figure, or label the
    divergence -- is the developer's to rule.

    Args:
        txn: The row or transfer leg to place (a leg's ``due_date`` is its
            parent's).
        period: Its pay period, as the calendar derived it -- the SAME value
            the row was selected by.
        target_month: The month being rendered.
        target_year: Its year.

    Returns:
        The day of *target_month* to render the row on, or ``None`` when its
        attribution date falls in another month.
    """
    return _day_in_month(
        period.attribution_day(txn.due_date), target_month, target_year,
    )


def _day_in_month(landing: date, month: int, year: int) -> int | None:
    """Return *landing*'s day of the month when it falls in *month* of *year*, else ``None``.

    The one month filter a row and an estimated occurrence are both placed
    through, so neither is drawn in a month its attribution day is not in.

    Args:
        landing: The item's attribution day.
        month: The month being rendered.
        year: Its year.

    Returns:
        The day of the month, or ``None`` for a day in another month.
    """
    if landing.month == month and landing.year == year:
        return landing.day
    return None


def _compute_month_end_balance(
    account: Account,
    year: int,
    month: int,
    balance_ctx: BalanceContext,
) -> Decimal:
    """Project the account's cash-flow balance at the calendar month-end (E-27).

    Routes through the balance-at seam's cash-flow scalar
    :func:`~app.services.balance_at.cash_balance_at` (Level-1 Commit 8),
    which folds the account's cash events (plan step X-c2b2), at the
    actual last day of the month.  The cash-flow entry (not the
    kind-correct :func:`~app.services.balance_at.balance_at`) keeps this a
    pure transaction running-balance of the BALANCE account, the member
    whose own rows among the day cells it reconciles with -- the cells hold
    the whole set's rows since plan step CC-4-3, so the month's totals and
    this line can differ by what moves on the other members, a divergence
    with no reconciling term yet (the grid's "On other accounts" is the
    shape); the balance account can be any cash-flow kind via an explicit
    ``account_id``.  This is the HIGH-02 / W-277
    fix: pre-remediation the calendar walked a separate code path
    that (a) selected the last pay period whose ``end_date`` was on or
    before the calendar month-end (up to ~13 days stale when the
    period straddled the month boundary) and (b) issued a transaction
    query without ``selectinload(Transaction.entries)``, silently
    degrading to the unreduced contribution (the F-009 seam on a second
    surface).  Both defects collapse into the single canonical
    "balance as of date D" producer.

    Args:
        account: The :class:`~app.models.account.Account` to summarize.
        year: Target calendar year.
        month: Target calendar month (1-12).
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        ``Decimal`` -- the projected balance on the last day of the
        target month, quantized to cents via
        :func:`~app.utils.money.round_money` inside the resolver.
    """
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return balance_at.cash_balance_at(account, balance_ctx, last_day)
