"""Investment dashboard -- the growth CHART.

Everything ``investment/_growth_chart.html`` draws: the forward projection over
the owner's own paychecks to the horizon, the modeled-history series behind
it, the Today and
retirement markers between them, and the optional what-if overlay.  Split from
the cards at this package's module-size ceiling on plan step D1c's cohesion
line -- that half answers "what does this account look like right now", this one
answers "what does it look like from here on".

The initial dashboard chart and the HTMX fragment share ONE pay-period basis
at the slider default horizon, so they cannot disagree.  **That basis stopped
being SYNTHETIC at pay-calendar plan step C2-e**: it was a fabricated 14-day
axis whatever the owner's real cadence (ledger row P20), and it is now
:meth:`~app.services.pay_calendar.PayCalendar.projection_axis` over their own
paydays.

Boundary discipline (``CLAUDE.md``): no Flask symbol, all money is
:class:`~decimal.Decimal`; ``float`` appears only at the Chart.js
serialization boundary.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from app.models.account import Account
from app.services import balance_at, growth_engine
from app.services.investment_projection import build_contribution_timeline
from app.services.pay_calendar import PeriodWindow
from app.utils.money import round_money

from ._context import (
    _ProjectionContext,
    _load_investment_params,
    _load_projection_context,
)


def _run_growth_projection(
    ctx: _ProjectionContext, periods: PeriodWindow,
) -> list[growth_engine.ProjectedBalance]:
    """Project balances across *periods* from the shared growth context.

    The single home for the ``growth_engine.project_balance`` splat the
    committed series and the what-if overlay both issue with identical
    arguments.  Callers must guard ``ctx.params is not None`` before calling.

    Seeds from ``ctx.projection_seed`` -- the modelled balance on the day
    BEFORE ``ctx.projection_start``, which is the day after the history line's
    last valued point (rulings R-AB / R-AE / R-AF).  The window and the seed's
    past are therefore disjoint: the engine cannot re-grow a day the seed
    already grew, nor re-apply a contribution it already holds, so nothing has
    to be subtracted back out of either (deep-quality-hunt #9 / #14, findings
    N-80 / N-84).

    The annual-limit accounting seeds from ``ctx.projection_ytd``, which is the
    THROUGH-current total on this surface: ruling R-AF put the current pay
    period OUTSIDE the window, so the engine never applies that period's own
    contribution and the strictly-before seed would leave the limit one period
    too roomy (:func:`._context._projection_ytd` carries the worked figure).

    **The contribution timeline is assembled HERE since plan step
    salary:R14-b**, not carried on the context.  A timeline's domain is the
    axis it will be projected over, and this chart's axis is a function of the
    horizon the REQUEST names -- so assembling it beside the other per-account
    inputs fixed it to the owner's saved window and left every period past
    that to ``periodic_contribution``, one raise-blind figure covering up to
    38 years of a 40-year chart (finding **D45**).  Built over ``periods``, a
    dated record answers every projected period and that fallback never fires.

    **The employer contribution is sized per period too**, through the engine's
    own ``salary_basis`` hook: the gross of the paycheck the account's funding
    profile was paid on each period
    (:meth:`~app.services.investment_projection.AccountPayrollFeed.gross_at`,
    which IS that hook's shape since plan step salary:S3-e-2), where
    ``employer_params`` used to carry ONE gross for the whole run.  That hook
    existed already and only ``/retirement`` supplied it, which is why this
    chart's employer line and that page's disagreed about the same account.
    """
    return growth_engine.project_balance(
        current_balance=ctx.projection_seed,
        assumed_annual_return=ctx.params.assumed_annual_return,
        periods=periods,
        periodic_contribution=ctx.inputs.periodic_contribution,
        employer_params=ctx.inputs.employer_params,
        annual_contribution_limit=ctx.params.annual_contribution_limit,
        ytd_contributions_start=ctx.projection_ytd,
        contributions=build_contribution_timeline(
            feed=ctx.feed,
            contribution_transactions=ctx.transfer_contributions,
            periods=periods,
            as_of=ctx.balance_ctx.as_of,
        ),
        salary_basis=ctx.feed.gross_at,
    )


def _build_chart_series(
    projection: list[growth_engine.ProjectedBalance],
    seed_balance: Decimal,
) -> tuple[list[str], list[str], list[str]]:
    """Build the chart's ``(labels, balances, contributions)`` string lists.

    The single home for the cumulative-contribution chart loop the
    dashboard and the growth-chart fragment both ran inline with
    different variable names (so R0801 never clustered them).  The
    contribution series is the running ``seed_balance + cumulative
    employee + employer`` total per period, where ``seed_balance`` is the
    projection's start-of-first-period seed (deep-quality-hunt #9) so the
    invested-principal line and the with-growth line share one origin.

    **Each row carries the period it priced**, so this no longer takes the
    axis and builds an id-keyed map of it to find each row's caption (plan
    step C2-e).  That map is where ledger row **P21**'s collapse would have
    landed: every period past the owner's saved horizon is a projection with
    no id, so 471 of a 20-year axis's 523 rows would have resolved to one
    entry and every projected month would have carried the same caption.  The
    three lists stay equal length because there is one row per axis period and
    every row now supplies its own label.
    """
    labels: list[str] = []
    balances: list[str] = []
    contributions: list[str] = []
    cumulative_contrib = Decimal("0")
    for pb in projection:
        labels.append(pb.period.start_date.strftime("%b %Y"))
        balances.append(str(round_money(pb.end_balance)))
        cumulative_contrib += pb.contribution + pb.employer_contribution
        contributions.append(
            str(round_money(seed_balance + cumulative_contrib))
        )
    return labels, balances, contributions


def _empty_chart_context() -> dict:
    """Return the empty-chart context (no projection to draw)."""
    return {
        "chart_labels": [],
        "chart_balances": [],
        "chart_contributions": [],
        "projection_end": None,
    }


def _assemble_chart_context(
    account: Account,
    ctx: _ProjectionContext,
    horizon_years: int,
    what_if_raw: str | None,
) -> dict:
    """Build the full chart context: projection + history + markers (C2).

    The single code path the dashboard first paint and the HTMX fragment both
    use (the owner's paychecks across ``horizon_years`` for the committed +
    optional what-if series, plus modeled history and Today/retirement
    markers), so they cannot disagree on basis.  Empty when the horizon yields
    no periods; callers guard ``ctx.params is not None``.

    **The axis opens at ``ctx.projection_start``, not at today** (ruling R-AF):
    the day after the history line's last valued point, which is the day BEFORE
    ``ctx.projection_seed`` is read on.  Taking the window and the seed from ONE
    derivation is what makes the two lines MEET -- deriving the window here and
    the seed in the loader is exactly how they came to be 10-13 days apart, with
    a step at the Today marker that nobody had chosen.

    **The axis's first period OPENS on ``projection_start``, and that identity
    is now structural** (plan step C2-e).  It used to hold by arithmetic --
    the deleted producer built its first period AT the date it was handed -- and
    the pay calendar's does not: it answers the period COVERING a day, which
    opened on a payday.  So :func:`._context._projection_start` derives that day
    from the calendar itself, as the day after the span covering the clock ends,
    and the two cannot come apart.  The assert below states it rather than
    trusting it: an adversarial code review of this step measured **$57.24** of
    growth counted twice at the head of a $102,686.18 balance on the branch
    where they HAD come apart -- an owner whose generated schedule has lapsed,
    where the old fallback opened the window at today while the axis opened it
    on the last payday.
    """
    horizon_years = max(1, min(horizon_years, 40))
    end_date = ctx.projection_start + timedelta(days=horizon_years * 365)
    # The owner's OWN paychecks from the window's opening day, projected past
    # their saved schedule at the cadence they recorded (plan step C2-e).  The
    # axis was fabricated and hardcoded to 14 days until then, so a monthly-paid
    # owner's chart applied 26 paycheck contributions a year (ledger row P20).
    periods = ctx.balance_ctx.calendar().projection_axis(
        ctx.projection_start, end_date,
    )
    if not periods:
        return _empty_chart_context()
    assert periods[0].start_date == ctx.projection_start, (
        f"the projection window opens {ctx.projection_start.isoformat()} and "
        f"its first period opens {periods[0].start_date.isoformat()}: the seed "
        f"is valued the day before the former, so the engine would re-grow "
        f"every day between them"
    )
    projection = _run_growth_projection(ctx, periods)
    chart_context = _growth_chart_context(ctx, periods, projection, what_if_raw)
    history = _build_history_series(account, ctx)
    markers = _build_chart_markers(
        ctx, len(history["history_balances"]), periods,
    )
    return {**chart_context, **history, **markers}


def _build_history_series(account: Account, ctx: _ProjectionContext) -> dict:
    """Return the modeled-history chart series over real past periods (C2).

    Modeled balances up to and including the current period, read through the
    SAME :func:`app.services.balance_at.balance_map` the headline uses (so the
    tail meets the headline at the Today boundary).  Empty when there is no
    current period; values are stringified cent ``Decimal``.  The no-scenario
    arm went at plan step X-v2 (ruling R-BW) -- that state is answered above
    this route now, and the ``balances is None`` arm went at X-f1c3a: the map
    answered ``None`` only for an account with ``current_anchor_period_id IS
    NULL``, which the schema forbade and the column no longer exists to express
    (finding N-73).
    """
    bctx = ctx.balance_ctx
    if ctx.current_period is None:
        return {"history_labels": [], "history_balances": []}
    balances = balance_at.balance_map(account, bctx)
    labels: list[str] = []
    values: list[str] = []
    for period in bctx.reported_periods():
        if period.period_index > ctx.current_period.period_index:
            continue
        balance = balances.get(period.period_id)
        if balance is None:
            continue
        labels.append(period.start_date.strftime("%b %Y"))
        values.append(str(round_money(balance)))
    return {"history_labels": labels, "history_balances": values}


def _build_chart_markers(
    ctx: _ProjectionContext,
    history_len: int,
    projection_periods: PeriodWindow,
) -> dict:
    """Return the Today-boundary and retirement-year chart markers (C2).

    ``today_boundary_index`` (== history length) splits solid history from
    the dashed projection; ``retirement_marker_index`` / ``retirement_year``
    mark the projection period holding the planned retirement date, else
    ``None`` (unset or beyond the horizon).

    **The marker's position is the WINDOW's own containment answer** (plan step
    C2-f2c, ledger row **P48**).  This walked the window period by period
    testing ``start_date <= retirement_date <= end_date``, which is
    :meth:`~app.services.pay_calendar.PeriodWindow.containing`'s predicate
    written a second time -- the last HAND-ROLLED member of ledger row **P6**'s
    census of "which pay period contains this date" implementations.  *It was
    not the last MEMBER*: that census named one other survivor,
    ``pay_period_service.get_current_period``, which was SQL rather than a scan
    and which plan step **C2-f3a** DELETED, closing ledger row **P19** with
    it -- its ``.first()`` carried no ``ORDER BY``, so over two periods
    covering one day it answered whichever row the planner reached first.  Row
    **P6**'s CENSUS is now empty; the ROW is owned by the ``C2`` container and
    open until it ticks.  The two agree
    over a tiling window, so this retired a DUPLICATE rather than a
    divergence; what it buys is that the answer now comes from the same bisect
    the rest of the application places a date with, and cannot drift from it.

    The window's index is what the chart needs rather than the period itself:
    Chart.js marks a POSITION in the plotted series, which runs history first
    and then one point per projected period, so the marker sits at
    ``history_len`` plus the period's offset WITHIN this window.

    **``retirement_date`` comes off the shared feed** rather than from this
    module's own ``user_settings`` query, which was the second of two per
    dashboard render for one value.

    Args:
        ctx: The shared per-request projection feed.
        history_len: How many points the solid history series holds.
        projection_periods: The axis the dashed projection is plotted over.

    Returns:
        The three marker keys.  ``retirement_marker_index`` is ``None`` when no
        retirement date is set, and when the one set falls outside this axis --
        before it opens or past its horizon.
    """
    retirement_date = ctx.planned_retirement_date
    offset = (
        None if retirement_date is None
        else projection_periods.containing_index(retirement_date)
    )
    return {
        "today_boundary_index": history_len,
        "retirement_year": (
            None if retirement_date is None else retirement_date.year
        ),
        # ``offset is None``, never falsiness: ZERO is the answer for a date in
        # the window's own FIRST period, and a truthiness test would drop that
        # marker to the Today boundary instead of one point past it.
        "retirement_marker_index": (
            None if offset is None else history_len + offset
        ),
    }


def _growth_chart_context(
    ctx: _ProjectionContext,
    periods: PeriodWindow,
    projection: list[growth_engine.ProjectedBalance],
    what_if_raw: str | None,
) -> dict:
    """Assemble the growth-chart fragment's full template context.

    Builds the committed-projection chart series plus the optional
    what-if overlay and comparison card.  Split out of
    :func:`compute_growth_chart_data` so that orchestrator stays a thin
    load-project-render sequence.
    """
    chart_labels, chart_balances, chart_contributions = _build_chart_series(
        projection, ctx.projection_seed,
    )

    what_if_amount = _parse_what_if(what_if_raw)
    what_if_balances, comparison = _compute_what_if_overlay(
        what_if_amount, ctx, periods, projection,
    )

    return {
        "chart_labels": chart_labels,
        "chart_balances": chart_balances,
        "chart_contributions": chart_contributions,
        "what_if_balances": what_if_balances,
        "what_if_amount": what_if_amount,
        "comparison": comparison,
        # Committed end balance at the horizon: the verdict strip's current-plan
        # figure when no what-if is entered.
        "projection_end": round_money(projection[-1].end_balance) if projection else None,
    }


def _parse_what_if(what_if_raw: str | None) -> Decimal | None:
    """Parse the what-if string, returning ``None`` for invalid / negative input.

    Zero is a valid input ("growth-only scenario: what if I stop
    contributing?").  Anything that fails :class:`Decimal` parsing
    or is strictly negative degrades to ``None`` -- the caller
    interprets ``None`` as "no what-if overlay" and renders the
    single-line chart.
    """
    if not what_if_raw:
        return None
    try:
        value = Decimal(what_if_raw)
    except (InvalidOperation, ValueError):
        return None
    if value < Decimal("0"):
        return None
    return value


def _compute_what_if_overlay(
    what_if_amount: Decimal | None,
    ctx: _ProjectionContext,
    periods: PeriodWindow,
    projection: list[growth_engine.ProjectedBalance],
) -> tuple[list[str], dict | None]:
    """Run the what-if projection (when an amount is supplied) plus comparison.

    Returns:
        ``(what_if_balances, comparison)`` where ``what_if_balances``
        is a list of string-formatted end balances (one per period)
        and ``comparison`` is ``None`` or a 5-key dict describing
        committed-vs-what-if end balances.
    """
    if what_if_amount is None or not periods:
        return [], None

    # contributions=None forces the engine to use periodic_contribution
    # for every period (a flat-rate what-if).  Employer match is
    # recalculated automatically because the per-period loop passes
    # each period's contribution to ``calculate_employer_contribution``.
    what_if_projection = growth_engine.project_balance(
        current_balance=ctx.projection_seed,
        assumed_annual_return=ctx.params.assumed_annual_return,
        periods=periods,
        periodic_contribution=what_if_amount,
        employer_params=ctx.inputs.employer_params,
        annual_contribution_limit=ctx.params.annual_contribution_limit,
        ytd_contributions_start=ctx.projection_ytd,
        contributions=None,
        # The employer match still sizes off each period's OWN gross (plan
        # step salary:R14-b): the what-if varies what the OWNER puts in, not
        # what the employer pays them, so freezing the basis here would make
        # the overlay and the committed line disagree about the same paycheck.
        salary_basis=ctx.feed.gross_at,
    )

    what_if_balances = [
        str(round_money(pb.end_balance))
        for pb in what_if_projection
    ]

    comparison = None
    if projection and what_if_projection:
        committed_end = round_money(projection[-1].end_balance)
        whatif_end = round_money(what_if_projection[-1].end_balance)
        difference = round_money(whatif_end - committed_end)
        comparison = {
            "committed_end": committed_end,
            "whatif_end": whatif_end,
            "difference": difference,
            "is_positive": difference > Decimal("0"),
            "is_zero": difference == Decimal("0"),
        }
    return what_if_balances, comparison


def compute_growth_chart_data(
    user_id: int,
    account: Account,
    horizon_years: int,
    what_if_raw: str | None,
) -> dict:
    """Build the context for the ``investment/_growth_chart.html`` fragment.

    Routes through the SAME :func:`_assemble_chart_context` the dashboard's
    first paint uses (C2), so both agree on the pay-period basis and
    carry the history series + markers.  Empty-chart shape when no params row
    exists or the horizon yields no periods.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked account instance.
        horizon_years: Slider value; clamped to ``[1, 40]`` defensively.
        what_if_raw: Optional ``what_if_contribution``; invalid / negative
            degrade to the single-line chart, ``Decimal("0")`` is valid.

    Returns:
        The chart fragment's context dict.
    """
    params = _load_investment_params(account.id)
    if not params:
        return _empty_chart_context()
    ctx = _load_projection_context(user_id, account, params)
    return _assemble_chart_context(account, ctx, horizon_years, what_if_raw)
