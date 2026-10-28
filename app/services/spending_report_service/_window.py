"""Which window, which rows, and the trailing series the hero is derived from.

The Spending report's first act: turn a :class:`._types.SpendingWindow` into
period ids, a date span and a human label; load that window's settled expenses;
and DERIVE the trailing same-type series the chart draws and the hero's
vs-prior / vs-average chips are read off -- so the chart and the chips cannot
disagree.  That series was a per-slot backwards WALK until plan step C2-f3d,
whose pay-period arm ran a stored-ordinal query per bar; it is now one
derivation over the calendar the scope already carries (:func:`_series_windows`).

:func:`_spent_total` lives here rather than with the hero because BOTH read it:
a window's total is what :func:`_load_window` answers for a prior window and
what the hero reports for the chosen one, and two spellings of "what did this
window spend" is the drift the shared series exists to prevent.

Boundary discipline: no Flask import; DB reads only, ``Decimal`` out.
"""

from decimal import Decimal

from app.services import spending_analysis
from app.services.cash_flow_set import PlanItems
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import PlanItem, parent_of
from app.utils.money import ZERO

from ._types import (
    SeriesPoint,
    SpendingWindow,
    _ResolvedWindow,
    _ScopeIds,
)

# Bars on the hero chart: the chosen window plus its predecessors of the
# same type (D7: a trailing-12 month chart for the exposed month picker).
# **At least 2**, because the series' last two slots are named positions --
# ``[-1]`` is the chosen window and ``[-2]`` its prior -- which
# ``_series_windows``, ``_build_series``, ``_hero._build_hero`` and the
# chart's ``compare_index`` all index directly.  And it must EXCEED
# ``_hero._TRAILING_WINDOW_COUNT``, so the vs-average baseline derives from
# the same series the chart draws; neither bound is enforced, and both are
# stated at both ends rather than only at the one read first.
_CHART_WINDOW_COUNT = 12

_MONTHS_PER_YEAR = 12


def _resolve_window(ids: _ScopeIds, window: SpendingWindow) -> _ResolvedWindow:
    """Resolve a window to its period ids, date span, and human label.

    A ``"pay_period"`` window resolves to its single period with no date
    span (the period is the window).  A ``"month"`` / ``"year"`` window
    resolves to its calendar span -- the attribution fetch runs on the span
    itself (:func:`_window_transactions`) -- plus the overlapping periods
    as the tracked-window signal.

    **The pay-period arm reads the scope's CALENDAR, not the table** (plan
    step C2-f3a).  It was ``db.session.get(PayPeriod, window.period_id)``: a
    second read of ``budget.pay_periods`` on a render that already holds the
    owner's whole schedule, and an UNSCOPED one -- any owner's id resolved,
    and its dates went into the window label.  Nothing reachable submits a
    ``period_id`` to ``/analytics/spending`` today (the route exposes only
    month and year), so this hardens a door rather than closing a live leak;
    the calendar carries ONE owner's periods, so a foreign id now resolves
    nothing by construction.

    Args:
        ids: The report's scope, whose ``calendar`` answers both the overlap
            and the pay-period window's own identity lookup.
        window: The window to resolve.

    Returns:
        The :class:`_ResolvedWindow`.  ``period_ids`` is empty when a
        pay-period window's id names none of this owner's periods, or a
        calendar window overlaps none.  ``calendar_window_bounds`` never
        crosses its bounds (C2-f).
    """
    if window.window_type == "pay_period":
        period = ids.calendar.period_by_id(window.period_id)
        if period is None:
            return _ResolvedWindow([], None, None, "")
        return _ResolvedWindow(
            [period.period_id], None, None, spending_analysis.window_label(
                window.window_type, window.month, window.year, period,
            ),
        )

    first_day, last_day = spending_analysis.calendar_window_bounds(
        window.window_type, window.year, window.month,
    )
    return _ResolvedWindow(
        [p.period_id for p in ids.calendar.overlapping(first_day, last_day)],
        first_day, last_day, spending_analysis.window_label(
            window.window_type, window.month, window.year, None,
        ),
    )


def _window_transactions(
    ids: _ScopeIds, resolved: _ResolvedWindow,
) -> PlanItems:
    """Load the settled expense items attributed to a resolved window.

    A calendar window selects by the attribution rule itself --
    COALESCE(due_date, pay period start) inside the window span, across
    ALL the user's periods
    (:func:`spending_analysis.query_settled_expenses_in_span`) -- so a
    bill due in month M belongs to M even when its funding period does not
    overlap M (the former period-overlap pre-filter attributed such a row
    to NO month window).  A pay-period window applies no date filter: the
    period IS the window.

    Args:
        ids: The report's scope ids.
        resolved: The resolved window (period ids + optional span).

    Returns:
        The window's :class:`~app.services.cash_flow_set.PlanItems`: its
        settled expense rows and its settled transfers' expense legs (leaf
        ``balance:X-bi-6-1b``).
    """
    if resolved.first_day is None:
        return spending_analysis.query_settled_expenses(
            ids.scenario_id, resolved.period_ids, ids.cash_flow,
        )
    return spending_analysis.query_settled_expenses_in_span(
        ids.scenario_id, ids.cash_flow, ids.user_id,
        resolved.first_day, resolved.last_day,
    )


def _window_total(
    resolved: _ResolvedWindow, txns: list[PlanItem],
) -> Decimal | None:
    """Return a loaded window's spend total, or ``None`` when untracked.

    ``None`` means the window overlaps no pay period AND holds no
    attributed rows (before the user's history) so callers exclude it from
    averages and the chart draws a tick.  A tracked window with no spend is
    ``Decimal("0")`` -- a real lean window that DOES count -- and a window
    that is untracked but still holds attributed rows (a due date outside
    every period's span) sums them rather than hiding real settled money.

    Args:
        resolved: The resolved window (the tracked signal).
        txns: The window's loaded settled expense items.

    Returns:
        The settled spend total, or ``None``.
    """
    if not resolved.period_ids and not txns:
        return None
    return _spent_total(txns)


def _load_window(
    ids: _ScopeIds, window: SpendingWindow,
) -> tuple[list[PlanItem], Decimal | None]:
    """Resolve and load a window's settled expense items plus its spend total.

    Args:
        ids: The report's scope ids.
        window: The window to load.

    Returns:
        ``(items, total)``; ``total`` per :func:`_window_total`.
    """
    resolved = _resolve_window(ids, window)
    items = _window_transactions(ids, resolved).items
    return items, _window_total(resolved, items)


# ── Trailing series ─────────────────────────────────────────────────


def _series_windows(
    ids: _ScopeIds, window: SpendingWindow,
) -> "list[SpendingWindow | None]":
    """Return the chart's :data:`_CHART_WINDOW_COUNT` windows, chosen last.

    **The whole series in ONE derivation, which is what plan step C2-f3d
    bought.**  Each slot used to be resolved by its own ``_shift_window(window,
    steps)`` call, and for a ``"pay_period"`` window each of those eleven calls
    ran a ``WHERE period_index = <chosen> - <steps>`` query against
    ``budget.pay_periods`` -- a NINTH hand-rolled period search (ledger row
    **P45**), reading the stored ordinal plan step **C4-c** dropped, on a render
    that already holds the owner's whole derived calendar.  A run of
    consecutive paychecks ending at a known one is exactly what
    :meth:`~app.services.pay_calendar.PayCalendar.window` answers, so the
    eleven searches collapse into one slice and the ordinal walk stops existing
    rather than moving from SQL into Python.

    **On a schedule whose stored ordinals agree with payday order the answer is
    unchanged, and THAT equality is structural rather than measured**: a derived
    ``period_index`` is the period's position in the owner's payday order and
    runs ``0..n-1`` dense (:func:`~app.services.pay_calendar.derive_periods`),
    so the ordinal range ``[chosen - 11, chosen)`` names the same eleven
    predecessors the eleven queries found one at a time, and a range reaching
    below zero simply comes back short -- which is the blank leading slot the
    queries produced by matching nothing.

    **TWO classes of answer DO change, and both change toward the right one.**

    *The stored ordinal can be wrong and the derived one cannot.*  This arc's
    own taxonomy names three expressible faults in that column
    (:mod:`app.services.pay_calendar`): an index out of payday order, a gap,
    and an overlap.  The first TRANSPOSED two bars, taking the vs-prior
    baseline with them.  The second cost the walk a SLOT: an unmatched
    ordinal left a blank bar mid-chart, so eleven steps reached only ten
    paychecks and the series began one paycheck LATER than it should --
    which moves the vs-average wherever the twelfth slot is occupied
    (measured on a twenty-period owner: `$1,300.00` against a true
    `$1,250.00`; on a ten-period one only the bar POSITIONS move, since both
    sides then hold the same six windows).  Neither fault is constructible
    here: the periods are derived from the paydays, so their ordinals are
    dense and in payday order by construction.

    *An unknown ``period_id`` resolves nothing rather than something.*  The
    walk read it with an UNSCOPED ``db.session.get``, so another owner's id
    supplied an ordinal and then THIS owner's paychecks beneath it --
    :func:`_resolve_window` states why the calendar closes that door and why it
    hardens rather than fixes.  **What moves is more than the blank slots**:
    ``[-2]`` is the prior window, so the hero's vs-prior and vs-average chips,
    every per-category delta and every By-change row moved with it.

    Every slot is filled, including the ones no window exists for, so index
    arithmetic on the result is stable: the chosen window is always ``[-1]``
    and its prior is always ``[-2]``, which is what :mod:`._hero` reads its two
    baselines off and what the chart's ``viewed_index`` / ``compare_index``
    name.  **The pay-period arm seats each paycheck at its own ordinal OFFSET,
    and that is a READING choice rather than a safety one** -- mutation
    testing of this step showed appending and left-padding to be an equivalent
    mutant that no test can distinguish, because the calendar's ordinals are
    dense (``test_pay_calendar_derivation`` asserts that directly) and the
    slice therefore always arrives ascending and gap-free.  What the offset
    buys is that the assignment names the same ordinal range that selected the
    period, so it is checkable at the line rather than by carrying the density
    argument to it.

    Args:
        ids: The report's scope, whose ``calendar`` answers the pay-period
            arm.  A calendar built by
            :func:`~app.services.pay_calendar.calendar_for` holds only
            MATERIALISED periods, which is what makes every ``period_id``
            below a real ``budget.pay_periods.id``;
            :meth:`~app.services.pay_calendar.PayCalendar.window` does not
            enforce that itself, which is ledger row **P72**.
        window: The chosen window, which is the last slot.

    Returns:
        :data:`_CHART_WINDOW_COUNT` entries, oldest first, the chosen window
        last.  An entry is ``None`` where no window exists, which happens two
        ways and only in a ``"pay_period"`` series: the slot falls before the
        owner's first payday, or the chosen id names none of their periods.  A
        ``"month"`` / ``"year"`` series is calendar arithmetic and always names
        a window -- whether such a window has any DATA is
        :func:`_window_total`'s answer, not this one's.
    """
    history = _CHART_WINDOW_COUNT - 1
    if window.window_type == "pay_period":
        earlier: "list[SpendingWindow | None]" = [None] * history
        chosen = ids.calendar.period_by_id(window.period_id)
        if chosen is not None:
            first = chosen.period_index - history
            for period in ids.calendar.window(first, history):
                earlier[period.period_index - first] = SpendingWindow(
                    window_type="pay_period", period_id=period.period_id,
                )
    elif window.window_type == "month":
        earlier = [
            SpendingWindow(window_type="month", month=month, year=year)
            for year, month in (
                _shift_month(window.year, window.month, step)
                for step in range(history, 0, -1)
            )
        ]
    else:
        earlier = [
            SpendingWindow(window_type="year", year=window.year - step)
            for step in range(history, 0, -1)
        ]
    return [*earlier, window]


def _build_series(
    ids: _ScopeIds,
    windows: "list[SpendingWindow | None]",
    *,
    viewed_total: Decimal | None,
    prior_total: Decimal | None,
) -> list[SeriesPoint]:
    """Load each of *windows* into the :class:`SeriesPoint` the chart draws.

    One point per slot :func:`_series_windows` produced, oldest first, so the
    chosen window is always ``series[-1]`` and its prior always ``series[-2]``
    -- a blank slot becomes an all-``None`` point rather than shortening the
    series, which is what keeps that arithmetic true for an owner whose
    schedule does not reach back twelve paychecks.  The LENGTH is the
    producer's: this returns one point per slot it was given, and
    :func:`_series_windows` is where :data:`_CHART_WINDOW_COUNT` of them is
    guaranteed.

    The chosen and prior windows' totals are passed in rather than re-loaded:
    the caller already loaded both windows' transactions for the breakdown and
    change rows, and reusing the totals keeps the chart bar, the hero figure,
    and the ledger summing one dataset.

    Args:
        ids: The report's scope ids.
        windows: The chart's windows from :func:`_series_windows`, oldest
            first and the chosen window last.
        viewed_total: The chosen window's settled spend (``None`` when the
            window overlaps no pay period).
        prior_total: The ``windows[-2]`` window's settled spend, or ``None``.

    Returns:
        The :class:`SeriesPoint` list, oldest first, one per slot.
    """
    points: list[SeriesPoint] = []
    for earlier in windows[:-2]:
        if earlier is None:
            points.append(SeriesPoint(window=None, total=None))
            continue
        _, total = _load_window(ids, earlier)
        points.append(SeriesPoint(window=earlier, total=total))
    points.append(SeriesPoint(window=windows[-2], total=prior_total))
    points.append(SeriesPoint(window=windows[-1], total=viewed_total))
    return points


def _shift_month(year: int, month: int, steps: int) -> tuple[int, int]:
    """Return ``(year, month)`` shifted ``steps`` calendar months back.

    Args:
        year: The reference year.
        month: The reference month (1-12).
        steps: Months to step back (>= 1).

    Returns:
        The ``(year, month)`` pair, rolling the year over correctly (e.g.
        one month before January 2026 is December 2025).
    """
    absolute = year * _MONTHS_PER_YEAR + (month - 1) - steps
    return absolute // _MONTHS_PER_YEAR, absolute % _MONTHS_PER_YEAR + 1


def _spent_total(txns: list[PlanItem]) -> Decimal:
    """Return total settled spend, SIGNED.

    **It was ``abs()`` per row until plan step ``bank_import:X-gj-2b``, and
    that was a money defect once the CHECK moved.**  ``abs()`` could not change
    an answer while ``ck_transaction_entries_positive_amount`` said
    ``amount > 0`` -- a settled envelope is worth the sum of its purchases, so
    its contribution was non-negative by construction.  Ruling **R-II** relaxed
    the CHECK so a merchant refund files as a NEGATIVE purchase, and a
    per-row ``abs()`` then reported a refunded envelope as SPENDING of the same
    magnitude: measured ``-86.67 -> +86.67``, a `$173.34` error against the
    truth on a window where the account received the money.

    The total can therefore be negative, for a window whose refunds exceeded
    its purchases.  That is the honest figure, and it is deliberately NOT the
    breakdown's share denominator: dividing by a net that refunds have pulled
    toward zero is what printed **600%** on a `$600.00` category, so
    :func:`~._breakdown._share_base` divides by what the window MOVED
    (developer ruling **bank_import:R-IL**, 2026-09-01).  The two are the same figure wherever no
    category went negative.

    Args:
        txns: Settled expense items -- rows, and transfer legs since leaf
            ``balance:X-bi-6-1b``.

    Returns:
        The sum of what each item's parent RECORDED as having moved
        (:func:`~app.services.row_valuation.settled_contribution`, ruling
        **plan#1983**: a leg's is its transfer's) over ``txns``.

    Raises:
        AmountUnresolvable: An item has not settled, which no loader here can
            hand it (both filter to the settled statuses in SQL).
    """
    return sum(
        (settled_contribution(parent_of(txn)) for txn in txns), ZERO,
    )
