"""
Shekel Budget App -- Unified Spending Report Service (S-P1, rebuilt for D7)

The producer behind the analytics Spending surface (the S14 "months lead"
cockpit renders it).  It answers "where did the money actually go" for one
chosen window (pay-period / month / year) on a window-over-window change
basis (D7 ruling 2026-07-10: month-over-month for the exposed month picker):

* **Trailing series** -- the chosen window plus its eleven predecessors of
  the same type, each with its settled spend total (``None`` for a window
  before the user's pay-period history).  The route serializes it for the
  hero band's emphasis month chart, and the hero's vs-prior / vs-average
  comparisons are DERIVED from this same series, so the chart and the chips
  cannot disagree.
* **Where It Went** -- settled expenses in the chosen window, grouped by
  category group with drill-down items, each carrying its dollar amount,
  its share of the window total, and its signed window-over-window dollar
  delta (producer-computed; templates do no math).
* **Changes** -- the flat By-change lens rows: every category with settled
  spend in the chosen window OR its prior window, with both totals and the
  signed delta, sorted by delta magnitude.  Categories with prior spend but
  none in the chosen window appear as zero-current rows (the D7
  zero-month-rows rider), so a bill that STOPPED is as visible as one that
  grew.
* **Estimate surprises** -- the settled rows whose entered actual differed
  from the estimate (the retired Variance tab's one real signal, reused via
  the shared :func:`spending_analysis.resolved_actual_amount` kernel), a
  capped ranked list plus the net.
* **Hero band** -- window spent total, versus the prior window, versus the
  trailing-window average, and payment timing (the year-end timeliness rule
  scoped to the window).

The former per-period trend enrichment (sparkline series, half-window delta
chips, top movers) retired with D7: the per-period trend basis misled on a
month-anchored page, so the surface's only change basis is now
window-over-window.

**The producer answers THREE window types and the surface exposes ONE**, which
is the S-P1 gate ruling deferring the pay-period and year pickers rather than
cancelling them -- so `/analytics/spending` builds only month windows and
:func:`app.routes.analytics_view.serialize_spending_chart` has only a month
branch.  A pay-period report is therefore computable and not renderable today.
The arm is maintained rather than deleted because plan step **C4-c** dropped the
``period_index`` column every "which paycheck precedes this one" answer used to
read, and an arm that reads it owes that conversion whichever surface reaches it
(developer ruling 2026-08-19, at plan step C2-f3d).

The Spending surface is MEASURED: settled-only, scoped to the owner's
CASH-FLOW SET -- the primary grid account and its active cards, read through
the one clause every plan-item reader appends (ruling ``credit_card:R-CC16``,
plan step CC-4-3; the audit's target-IA row scoped it to the first active
checking account before that).  It carries the balance account's name / id
and the settled-only flag so the template can label the scope on screen.
Settled-only is why figures price through ``settled_contribution`` (X-au-c2).

Boundary discipline: no Flask import.  The route resolves the window from
query params and passes a :class:`SpendingWindow`; every figure is a
``Decimal`` (``float`` conversion is the route layer's Chart.js boundary).
DB reads live in the service layer, mirroring the sibling analytics
producers.

## Package layout

It was ONE module until plan step X-au-c2b, and it sat at exactly its
1000-line ``too-many-lines`` ceiling -- finding **N-270**, recorded three days
earlier as "the NEXT edit to this module, any edit, trips C0302".  Routing its
budget reader through the amount model was that edit.  The split follows the
section banners the module already carried, so each surface's rules live beside
the shapes they fill (the :mod:`app.services.ledger_report_service` precedent;
N-270 named ``year_end_summary_service``, which commit ``3aecceb0`` had already
DELETED):

* :mod:`._types` -- every frozen result shape, plus the three private records
  the helpers pass between them.  No behaviour.
* :mod:`._window` -- window resolution, the settled-expense load, and the
  trailing series both the chart and the hero's baselines derive from.
* :mod:`._breakdown` -- Where It Went and the By-change lens, both reducing
  ONE pair of per-category totals.
* :mod:`._surprises` -- the estimate-surprises list and its net.
* :mod:`._hero` -- the hero band, reading its baselines off the series.

This module holds the ONE public entry point and re-exports the shapes a route
or a template names, so no consumer reaches into a submodule.
"""

from app.models.user import UserSettings
from app.services import spending_analysis
from app.services.account_resolver import resolve_cash_flow_set
from app.services.cash_ledger import derived_amount_basis
from app.services.pay_calendar import calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.services.transfer_legs import PlanItem

from ._breakdown import _build_breakdown, _build_changes, _totals_by_category
from ._hero import _build_hero
from ._surprises import _build_surprises
from ._types import (
    ChangeRow,
    Comparison,
    HeroFigures,
    SeriesPoint,
    SpendingGroupRow,
    SpendingItemRow,
    SpendingReport,
    SpendingScope,
    SpendingWindow,
    Surprise,
    Surprises,
    _ScopeIds,
)
from ._window import (
    _build_series,
    _load_window,
    _resolve_window,
    _series_windows,
    _window_total,
    _window_transactions,
)

__all__ = [
    "ChangeRow",
    "Comparison",
    "HeroFigures",
    "SeriesPoint",
    "SpendingGroupRow",
    "SpendingItemRow",
    "SpendingReport",
    "SpendingScope",
    "SpendingWindow",
    "Surprise",
    "Surprises",
    "compute_spending_report",
]


def compute_spending_report(
    user_id: int,
    window: SpendingWindow,
    *,
    user_settings: UserSettings | None,
) -> SpendingReport | None:
    """Compute the Spending surface dataset for *user_id* over *window*.

    Resolves the owner's cash-flow set and baseline scenario, then builds
    the trailing window series, the category breakdown with
    window-over-window deltas, the By-change rows, the estimate surprises,
    and the hero band over the chosen window's settled expenses.  The
    hero's vs-prior and vs-average baselines are derived from the series,
    so the chart and the chips agree by construction.

    **The scope is the owner's cash-flow set since plan step CC-4-3** --
    the primary grid account plus its active cards, resolved by
    :func:`~app.services.account_resolver.resolve_cash_flow_set`, the ONE
    call the grid and the dashboard make, with no override (the surface
    takes none).  It was ``resolve_analytics_account(user_id, None)``: the
    first active checking account with no settings layer, a second spelling
    of the primary that agreed with the grid's only while no
    ``default_grid_account_id`` was saved.  The report's default therefore
    follows the owner's saved default grid account now, and an owner with
    no checking account but another grid-eligible one gets a report on it
    where they got the empty state.

    Args:
        user_id: The owning user (scopes every query).
        window: The chosen :class:`SpendingWindow`.
        user_settings: The owner's ``UserSettings`` row, or ``None`` when
            they have none -- the saved-default layer the primary reads.
            Keyword-only and REQUIRED, ``None`` being a value a caller states
            rather than a default it can fall into: an omitted row would
            resolve a different primary than the grid's for an owner with a
            saved default, silently, and a missing argument fails at the
            call instead.  The route passes the row it holds.

    Returns:
        The populated :class:`SpendingReport`, or ``None`` when the user has
        no grid-eligible account or no baseline scenario (the template
        renders an empty state).  A resolvable user whose window simply has
        no settled spend gets a populated report with an empty breakdown and
        a zero spent total (the documented empty shape), never ``None``.

    Raises:
        ValueError: An invalid window (``validate_window``), or -- as its
            ``PayCalendarError`` subclass, newly reachable at plan step C2-f1
            -- an owner whose paydays cannot define a calendar.  *Finding
            **P8** named the route that made that reachable from a page, and
            plan step C4-b-2 closed it: the inferred cadence is gone and the
            stored one is bounded to the range the derivation enforces.*
    """
    spending_analysis.validate_window(
        window.window_type, window.period_id, window.month, window.year,
    )

    cash_flow = resolve_cash_flow_set(user_id, user_settings)
    if cash_flow is None:
        return None
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return None

    ids = _ScopeIds(
        cash_flow=cash_flow, scenario_id=scenario.id,
        calendar=calendar_for(user_id),
    )
    resolved = _resolve_window(ids, window)
    txns = _window_transactions(ids, resolved)

    # The chart's twelve windows are DERIVED once, off the calendar the
    # scope already carries (plan step C2-f3d): the prior window is the
    # second-to-last slot rather than a separate backwards step, so the
    # figure the hero compares against and the bar beside it are one answer.
    windows = _series_windows(ids, window)
    prior_window = windows[-2]

    # The prior window loads once: its transactions feed the per-category
    # deltas AND its total feeds both the series' step-1 point and the
    # hero's vs-prior baseline (one load, three consumers that must agree).
    if prior_window is None:
        prior_txns: list[PlanItem] = []
        prior_total = None
    else:
        prior_txns, prior_total = _load_window(ids, prior_window)

    series = _build_series(
        ids, windows,
        viewed_total=_window_total(resolved, txns),
        prior_total=prior_total,
    )

    current_by_cat = _totals_by_category(txns)
    prior_by_cat = _totals_by_category(prior_txns)

    return SpendingReport(
        scope=SpendingScope(
            account_id=cash_flow.balance.id,
            account_name=cash_flow.balance.name,
            settled_only=True,
            window_label=resolved.label,
        ),
        hero=_build_hero(txns, series, current_by_cat),
        series=series,
        breakdown=_build_breakdown(current_by_cat, prior_by_cat),
        changes=_build_changes(current_by_cat, prior_by_cat),
        # ONE basis for the pass.  The surprises list needs it to price a
        # row whose plan the amount-source cutovers declared derived;
        # every other consumer here values through
        # ``row_valuation.settled_contribution``, which answers a settled
        # row from its SETTLEMENT record and never reaches the amount
        # model, so this is the package's only basis and costs no query
        # (both of its derivations are lazy).
        #
        # It is built at the call rather than carried on ``_ScopeIds``
        # beside the calendar, which is where a SECOND consumer would want
        # it -- the calendar rides there for exactly that reason (C2-f1).
        # With one consumer there is nothing yet to keep in step; the day a
        # second appears in this package it belongs on the scope object.
        surprises=_build_surprises(txns, derived_amount_basis(user_id, scenario.id)),
    )
