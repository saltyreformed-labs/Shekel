"""
Shekel Budget App -- Analytics View-Model Builders

The route-layer presentation helpers for the analytics blueprint,
separated from :mod:`app.routes.analytics` so that module holds the HTTP
handlers (request parsing, auth, redirects, ``render_template``) and this
one holds the pure "turn a service report into template-ready data" logic
(Single Responsibility; it also keeps the handler module under the
1000-line ceiling as the page grows one slice at a time).

Every function here is pure: it takes a service report (or plain scalars)
and returns dicts / JSON strings / SVG point strings, with NO Flask
request context, NO database access, and NO models.  This is the one layer
where ``float`` is allowed -- at the Chart.js / SVG serialization boundary
(coding-standards: floats live only here, never in a money calculation);
the money values themselves are computed as ``Decimal`` in the services.
"""

import calendar as cal_mod
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# ── Taxes tab display ──────────────────────────────────────────────

# Two-decimal display quantum for the Taxes tab's percentage chips
# (rates arrive as 4-dp fractions; render as 0.01%-resolution percents).
_PCT_QUANTUM = Decimal("0.01")
_HUNDRED = Decimal("100")


def build_taxes_display(report):
    """Compute the Taxes tab's route-layer display values.

    The presentation splits every signed figure into a magnitude plus a
    direction flag (a "$1,234 owed" hero must not render "-$1,234 owed"),
    and scales the 4-dp rate fractions to 2-dp percents -- Decimal display
    math that belongs at the route layer, never in the template.

    Args:
        report: The populated
            :class:`~app.services.tax_report_service.TaxReport`.

    Returns:
        dict with the hero/chip magnitudes and direction flags, the
        percent-scaled rates (``None`` propagated for a ``None`` effective
        rate or state rate), and the Schedule A margin split.
    """
    refund = report.refund
    effective = report.chips.effective_rate
    state_rate = report.liability.state.flat_rate
    return {
        "hero_is_refund": refund.total_refund >= 0,
        "hero_amount": abs(refund.total_refund),
        "federal_is_refund": refund.federal_refund >= 0,
        "federal_amount": abs(refund.federal_refund),
        "state_is_refund": refund.state_refund >= 0,
        "state_amount": abs(refund.state_refund),
        "effective_pct": (
            (effective * _HUNDRED).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)
            if effective is not None else None
        ),
        "marginal_pct": (
            report.chips.marginal_rate * _HUNDRED
        ).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP),
        "state_rate_pct": (
            (state_rate * _HUNDRED).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)
            if state_rate is not None else None
        ),
        "margin_positive": report.schedule_a.margin > 0,
        "margin_amount": abs(report.schedule_a.margin),
    }


# ── Spending tab display ───────────────────────────────────────────
# The hero comparison signed-delta split (the ``build_taxes_display``
# precedent), the ledger lens rows' bar geometry, and the month chart
# serialization (the ``serialize_flow_strip`` float boundary).

# The By-change lens's diverging minibar grows from the track's center, so
# a full-magnitude row fills half the track: its width percentage is capped
# at 50 (progress_bar.js applies widths relative to the whole track).
_DIVERGING_HALF_TRACK = 50


def prev_month(year, month):
    """Return the ``(year, month)`` one calendar month before the argument.

    Args:
        year: The reference calendar year.
        month: The reference calendar month (1-12).

    Returns:
        The prior month's ``(year, month)``, rolling the year back at January.
    """
    if month == 1:
        return year - 1, 12
    return year, month - 1


def next_month(year, month):
    """Return the ``(year, month)`` one calendar month after the argument.

    Args:
        year: The reference calendar year.
        month: The reference calendar month (1-12).

    Returns:
        The next month's ``(year, month)``, rolling the year forward at
        December.
    """
    if month == 12:
        return year + 1, 1
    return year, month + 1


def build_spending_nav(today, year, month):
    """Build the Spending tab's month-navigation context.

    Args:
        today: The current date in the display timezone.
        year: The shown calendar year.
        month: The shown calendar month (1-12).

    Returns:
        dict of the month label, the prev/next month targets (with the prior
        month's name for the vs-prior chip caption), an ``is_current_month``
        flag for the in-progress caption, and ``can_go_next`` -- ``False``
        once the shown month is the current month or later, capping forward
        navigation on this measured surface.
    """
    prev_year, prev_mon = prev_month(year, month)
    next_year, next_mon = next_month(year, month)
    return {
        "month_name": cal_mod.month_name[month],
        "prev_year": prev_year,
        "prev_month": prev_mon,
        "prev_month_name": cal_mod.month_name[prev_mon],
        "next_year": next_year,
        "next_month": next_mon,
        "is_current_month": year == today.year and month == today.month,
        "can_go_next": (year, month) < (today.year, today.month),
    }


def build_spending_display(report):
    """Compute the Spending tab's route-layer display values.

    Bundles the hero comparison chips (signed delta + direction + the
    baseline for the caption, the P-AN11 fix form) with the merged ledger's
    two lens row sets (By size / By change), each carrying its bar geometry
    so the template renders without math.

    Args:
        report: The populated
            :class:`~app.services.spending_report_service.SpendingReport`.

    Returns:
        dict with ``vs_prior`` / ``vs_average`` comparison-display dicts (or
        ``None`` each), ``size_groups`` (the By-size group/item row dicts),
        and ``change_rows`` (the By-change row dicts).
    """
    return {
        "vs_prior": _comparison_display(report.hero.vs_prior),
        "vs_average": _comparison_display(report.hero.vs_average),
        "size_groups": _size_lens_rows(report.breakdown),
        "change_rows": _change_lens_rows(report.changes),
    }


def _comparison_display(comparison):
    """Render one hero comparison as a signed-delta chip dict.

    Args:
        comparison: A
            :class:`~app.services.spending_report_service.Comparison`.

    Returns:
        A dict with the signed ``delta``, its ``direction`` (``"up"`` /
        ``"down"`` / ``"flat"``), the signed ``pct`` (or ``None``), and the
        ``baseline`` the chip's caption states (the P-AN11 fix: the caption
        names the prior total so the delta cannot read as the prior month's
        total).  ``None`` when the comparison has no baseline at all.
    """
    if comparison.delta is None:
        return None
    return {
        "delta": comparison.delta,
        "direction": _delta_direction(comparison.delta),
        "pct": comparison.pct,
        "baseline": comparison.baseline,
    }


def _delta_direction(delta):
    """Map a signed spending delta to its direction key.

    Args:
        delta: The signed ``Decimal`` delta.

    Returns:
        ``"up"`` (spent more), ``"down"`` (spent less), or ``"flat"``
        (exactly equal) -- the template maps these to the money-state
        classes (up = danger, down = done, flat = muted).
    """
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def _size_lens_rows(breakdown):
    """Build the By-size lens's template-ready group rows.

    Bars are CAPPED to the largest group (the D7 ruling): every bar's width
    is its amount relative to the largest row, so the biggest group spans
    the full track and lengths stay comparable, while the ``share`` text
    stays share-of-total.  A single-item group collapses to one row (the
    grid D3 singleton rule ported): its item name becomes the ``kin``
    suffix and no drill-down items render.

    Args:
        breakdown: The report's
            :class:`~app.services.spending_report_service.SpendingGroupRow`
            list, amount-descending.

    Returns:
        A list of group dicts (name / kin / is_new / amount / share /
        bar_pct / delta / delta_class / item_rows), where ``item_rows`` is
        a list of the same shape minus ``kin`` and is empty for a singleton
        group.  Named ``item_rows`` (not ``items``) because Jinja's dot
        lookup would resolve ``group.items`` to the dict METHOD, not the
        key.
    """
    if not breakdown:
        return []
    # **The largest RENDERED row, groups and items alike** (plan step
    # ``bank_import:X-gj-2b-3``, found by adversarial financial review).  It
    # was `max(group.amount ...)`, which bounded every bar at 100% only
    # because an item's amount could not exceed its group's: every category
    # total was non-negative, so `item <= group <= max`.  Ruling
    # **bank_import:R-II** ended that -- a group of `Groceries +600.00` and
    # `Restaurants -500.00` sums to `+100.00` while holding a `+600.00` item --
    # and against a `300.00` maximum that item rendered a bar of **200.0**,
    # off its own track and clamped only by `progress_bar.js`, which is the
    # tier :func:`_bar_pct` says in as many words a money-shaped decision may
    # not be left to.
    #
    # Taking the maximum over what is actually DRAWN restores the docstring's
    # stated contract -- the largest row is full width and every other is
    # proportional to it -- by construction rather than by a clamp, and it is
    # the SAME figure on any window holding no refund, since an item cannot
    # then exceed its own group.
    max_amount = max(
        [group.amount for group in breakdown]
        + [item.amount for group in breakdown for item in group.items],
    )
    rows = []
    for group in breakdown:
        singleton = len(group.items) == 1
        kin = None
        if singleton and group.items[0].item_name != group.group_name:
            kin = group.items[0].item_name
        rows.append({
            "name": group.group_name,
            "kin": kin,
            "is_new": group.is_new,
            "amount": group.amount,
            "share": group.share,
            "bar_pct": _bar_pct(group.amount, max_amount),
            "delta": group.delta,
            "delta_class": _delta_class(group.delta),
            "item_rows": [] if singleton else [
                {
                    "name": item.item_name,
                    "is_new": item.is_new,
                    "amount": item.amount,
                    "share": item.share,
                    "bar_pct": _bar_pct(item.amount, max_amount),
                    "delta": item.delta,
                    "delta_class": _delta_class(item.delta),
                }
                for item in group.items
            ],
        })
    return rows


def _change_lens_rows(changes):
    """Build the By-change lens's template-ready row dicts.

    Each row gets a center-zeroed diverging minibar: the fill's width is the
    delta's magnitude relative to the largest delta, capped to half the
    track (:data:`_DIVERGING_HALF_TRACK`), growing right (``side: "up"``,
    danger tint) for more spending and left (``"down"``, done tint) for
    less.  A zero-delta row has no fill (``side: None``).

    Args:
        changes: The report's
            :class:`~app.services.spending_report_service.ChangeRow` list,
            delta-magnitude-descending.

    Returns:
        A list of row dicts (name / kin / is_new / delta / delta_class /
        current / bar_pct / side).
    """
    if not changes:
        return []
    max_delta = max(abs(row.delta) for row in changes)
    rows = []
    for row in changes:
        if row.delta > 0:
            side = "up"
        elif row.delta < 0:
            side = "down"
        else:
            side = None
        rows.append({
            "name": row.item_name,
            "kin": (
                row.group_name if row.group_name != row.item_name else None
            ),
            "is_new": row.is_new,
            "delta": row.delta,
            "delta_class": _delta_class(row.delta),
            "current": row.current,
            "bar_pct": (
                _bar_pct(abs(row.delta), max_delta) * _DIVERGING_HALF_TRACK
                / 100
            ),
            "side": side,
        })
    return rows


def _delta_class(delta):
    """Map a signed spending delta to its money-state CSS class.

    Args:
        delta: The signed ``Decimal`` delta.

    Returns:
        ``"trend-up"`` (danger; spent more), ``"trend-down"`` (done; spent
        less), or ``"trend-flat"`` (muted; exactly equal).
    """
    return f"trend-{_delta_direction(delta)}"


def _bar_pct(amount, max_amount):
    """Scale an amount to its bar width as a percent of the largest row.

    The ledger's ``float`` boundary (presentation geometry, never a money
    value): the largest row renders a full-width bar and every other bar is
    proportional to it.

    **A NEGATIVE amount draws no bar, and it is this function that says so**
    (ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``).  A
    category whose refunds exceeded its purchases has a negative total since
    that step relaxed ``ck_transaction_entries_positive_amount`` to
    ``amount <> 0``; before it, ``_totals_by_category`` took an ``abs()`` and
    this argument was non-negative by construction, which is what this
    docstring used to assert.  A bar answers *how much of the budget did this
    consume*, and the answer for such a row is NONE -- it gave money back.

    **Floored HERE rather than left to ``progress_bar.js``.**  That clamp is a
    defence against a malformed server value and says so in as many words; a
    figure this function can produce for an ordinary refunded category is not
    malformed, and leaving it to the browser would put a money-shaped decision
    in the one tier no test in this project drives.

    Args:
        amount: The row's ``Decimal`` amount, SIGNED.
        max_amount: The largest row's ``Decimal`` amount.

    Returns:
        The width percentage as a float rounded to 2 decimals.  ``0.0`` when
        the maximum is not positive -- an all-zero ledger, or a window whose
        every category was refund-dominated, has no bars to size -- and
        ``0.0`` for any row that is not itself positive.
    """
    if max_amount <= 0 or amount <= 0:
        return 0.0
    return round(float(amount) / float(max_amount) * 100, 2)


def serialize_spending_chart(report):
    """Serialize the trailing-12 month series for the chart canvas.

    The Spending tab's Chart.js serialization boundary (floats live only
    here).  Handles the tab's exposed window type -- calendar months -- and
    emits one bar per series point:

    - ``labels``: short month names (``"Jul"``); the tooltip carries the
      year from ``nav``.
    - ``values``: the month's settled total as a float, or ``null`` for a
      month with no pay periods (drawn as a baseline tick, like a zero
      month).
    - ``nav``: the ``{year, month}`` click-to-navigate target per bar.
    - ``viewed_index`` / ``compare_index``: the emphasis bar (the viewed
      month, always last) and its comparison bar (the prior month) -- the
      only two bars that get value labels (D7).
    - ``avg``: the hero's vs-average baseline (the SAME figure as the chip,
      so the reference line and the chip cannot disagree), or ``null``.
    - ``history_note``: ``"settled history begins Mar 2026"`` when the
      window's leading months are all empty, else ``null``.

    Args:
        report: The populated
            :class:`~app.services.spending_report_service.SpendingReport`
            for a month window.

    Returns:
        A JSON string for the canvas ``data-chart`` attribute.
    """
    labels = []
    values = []
    nav = []
    for point in report.series:
        window = point.window
        if window is None or window.month is None or window.year is None:
            labels.append("")
            values.append(None)
            nav.append(None)
            continue
        labels.append(f"{date(window.year, window.month, 1):%b}")
        values.append(float(point.total) if point.total is not None else None)
        nav.append({"year": window.year, "month": window.month})

    avg = report.hero.vs_average.baseline
    return json.dumps({
        "labels": labels,
        "values": values,
        "nav": nav,
        "viewed_index": len(report.series) - 1,
        "compare_index": len(report.series) - 2,
        "avg": float(avg) if avg is not None else None,
        "history_note": _history_note(report.series),
    })


def _history_note(series):
    """Return the chart's settled-history note, or ``None``.

    The note explains a leading run of EMPTY bars: when the first point that
    moved any money is not the first bar, every earlier bar is empty, so the
    chart states where history begins rather than reading as missing data.

    **The test is "moved money", not "spent a positive amount"** (plan step
    ``bank_import:X-gj-2b-3``).  It read ``total > 0`` on the stated premise
    that totals are non-negative, which ruling **bank_import:R-II** ended: a
    month whose refunds exceeded its purchases has a negative total and DRAWS
    A BAR, so a leading negative month got the caption *settled history begins*
    a month later than the chart's own first bar -- the note contradicting the
    picture beside it.  A window with periods but no settled spend is
    ``Decimal("0")`` and is genuinely an empty bar, which is why zero is still
    not history and ``None`` (a window before the user's periods) still is not.

    Args:
        series: The report's
            :class:`~app.services.spending_report_service.SeriesPoint` list.

    Returns:
        ``"settled history begins Mar 2026"`` styled text, or ``None`` when
        the first bar already moved money or no bar moved any.
    """
    for index, point in enumerate(series):
        if point.total is not None and point.total != 0:
            if index == 0 or point.window is None:
                return None
            window = point.window
            if window.month is None or window.year is None:
                return None
            first = date(window.year, window.month, 1)
            return f"settled history begins {first:%b %Y}"
    return None


# ── Calendar tab serialization ─────────────────────────────────────


def serialize_flow_strip(data, low_balance, today, year, month):
    """Serialize the month flow strip series to a JSON string.

    The calendar's single Chart.js serialization boundary (coding-standards:
    floats live only here, never in a calculation), mirroring the dashboard's
    ``_serialize_chart``.  Emits one point per calendar day of the month from
    the daily running-balance view the service computed
    (:class:`~app.services.calendar_service.DailyView`), plus the display
    indices the strip needs:

    - ``current_index``: the count of measured days (points ``[0,
      current_index)`` render solid, the rest dashed) -- ``today.day``
      inside the month, ``0`` for a wholly future month (all dashed), the
      day count for a wholly past month (all solid).  Matches the
      net-worth cockpit's ``current_index`` semantics so the shared
      ``splitSegment`` / ``todayMarkerPlugin`` helpers apply unchanged.
    - ``threshold``: the user's low-balance-threshold setting (the same
      source the grid and dashboard read -- Calendar rebuild decision 4).
    - ``payday_indices`` / ``trough_index``: 0-based day indices for the
      payday dots and the labeled trough dot.
    - ``trough_state``: the trough's money state under the grid's
      thresholds (D11, P-AN6) -- ``"negative"`` below zero, ``"low"``
      below the threshold but positive, else ``"ok"`` -- so the strip
      script picks the dot/label ink without comparing money client-side.
      ``None`` when the month has no trough.
    - ``week_tick_indices``: the 1st of the month plus every Sunday
      (0-based), the strip's weekly gridline/tick positions -- Sundays
      match the calendar grid's week start.

    Args:
        data: The month's :class:`MonthSummary` (``daily`` must be present;
            returns ``None`` when it is not, and the template skips the
            strip).
        low_balance: The user's low-balance threshold (whole dollars).
        today: The current date in the display timezone.
        year: Target calendar year.
        month: Target calendar month (1-12).

    Returns:
        A JSON string for the canvas ``data-chart`` attribute, or ``None``
        when no daily view was computed.
    """
    if data.daily is None:
        return None
    days_in_month = cal_mod.monthrange(year, month)[1]
    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)

    if today < first_day:
        current_index = 0
    elif today > last_day:
        current_index = days_in_month
    else:
        current_index = today.day

    week_ticks = sorted({0} | {
        day - 1 for day in range(1, days_in_month + 1)
        if date(year, month, day).weekday() == cal_mod.SUNDAY
    })

    # The trough's money state under the grid's thresholds (D11, P-AN6);
    # the comparison lives here so the strip script never compares money.
    trough_balance = data.daily.trough_balance
    if trough_balance is None:
        trough_state = None
    elif trough_balance < 0:
        trough_state = "negative"
    elif trough_balance < low_balance:
        trough_state = "low"
    else:
        trough_state = "ok"

    balances = data.daily.daily_balances
    return json.dumps({
        "labels": [
            date(year, month, day).strftime("%b %-d")
            for day in range(1, days_in_month + 1)
        ],
        "values": [
            float(balances[day]) for day in range(1, days_in_month + 1)
        ],
        "current_index": current_index,
        "threshold": float(low_balance),
        "payday_indices": [day - 1 for day in data.paycheck_days],
        "trough_index": (
            data.daily.trough_day - 1
            if data.daily.trough_day is not None else None
        ),
        "trough_state": trough_state,
        "week_tick_indices": week_ticks,
    })


def build_calendar_weeks(year, month, data, today):
    """Build a list of week rows for the calendar grid.

    Each week is a list of 7 day dicts with keys: number, entries,
    is_paycheck, is_today, is_modeled, daily_balance, overflow.  The day's
    (income, expense) fold is not one of them: no template draws it, and its
    ``income_total`` / ``expense_total`` copies went at plan task plan#3529,
    which made that fold the balance account's alone (ruling plan#3526).
    ``daily_balance`` is the day's projected
    end-of-day running balance (``None`` before the pay-period horizon or
    when no daily view was computed); ``overflow`` is the day's "+N more"
    residual or ``None``.  ``is_modeled`` marks days AFTER today in the
    display timezone -- the measured/modeled treatment is a date split, not
    a status split (Calendar rebuild decision 7): the cell's balance hero
    renders in secondary ink with a leading tilde on modeled days.
    Empty cells have number=0.  Uses Sunday as the first day of the week.
    """
    # Sunday-start calendar (firstweekday=6 in Python's calendar).
    cal = cal_mod.Calendar(firstweekday=6)
    month_weeks = cal.monthdayscalendar(year, month)

    paycheck_set = set(data.paycheck_days)
    daily_balances = data.daily.daily_balances if data.daily else {}

    weeks = []
    for week in month_weeks:
        row = []
        for day_num in week:
            if day_num == 0:
                row.append({
                    "number": 0,
                    "entries": [],
                    "is_paycheck": False,
                    "is_today": False,
                    "is_modeled": False,
                    "daily_balance": None,
                    "overflow": None,
                })
            else:
                entries = data.day_entries.get(day_num, [])
                is_today = (
                    year == today.year
                    and month == today.month
                    and day_num == today.day
                )
                row.append({
                    "number": day_num,
                    "entries": entries,
                    "is_paycheck": day_num in paycheck_set,
                    "is_today": is_today,
                    "is_modeled": (
                        (year, month, day_num)
                        > (today.year, today.month, today.day)
                    ),
                    "daily_balance": daily_balances.get(day_num),
                    "overflow": data.day_overflow.get(day_num),
                })
        weeks.append(row)
    return weeks
