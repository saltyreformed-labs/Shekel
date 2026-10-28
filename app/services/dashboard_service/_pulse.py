"""
Shekel Budget App -- Dashboard: the pulse region (canvas + street + due-soon).

:func:`compute_pulse_section` is the single ``balanceChanged`` refresh
region of the Terminal Road rebuild (Loop B B-1): the period-end hero,
the 13-period projected end-balance chart + threshold, the full-horizon
trough and peak, the still-due totals (current + next period), and the
current period's due-soon rows.  Everything it returns derives from the
same transaction state, so one producer + endpoint serves all of it.

Pure aggregation -- no Flask imports, no database writes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import groupby
from typing import TYPE_CHECKING

from app.models.user import UserSettings
from app.services import balance_at, cash_ledger
from app.services.entry_service import compute_remaining
from app.services.transfer_legs import PlanItem, cell_key
from app.utils.money import round_money
from app.utils.period_projections import SIX_MONTHS

from ._bills import _query_unpaid_expense_rows, txn_to_bill_dict
from ._section import _DEFAULT_STALENESS_DAYS, DashboardSection

if TYPE_CHECKING:
    # The pay calendar's own view of a period.  EVERY period on this page is
    # one since pay-calendar plan step C2-f2e -- the current one, the next
    # one, and every point the chart, the trough and the peak read -- where
    # the current period and the forward set were ``budget.pay_periods`` ORM
    # rows carrying the two derived columns plan step **C4-c** dropped.  Type-only:
    # the values arrive from ``balance_ctx.calendar()`` and
    # ``balance_ctx.reported_periods()``, which this module reaches through the
    # section's ``BalanceContext``.
    from app.services.pay_calendar import DerivedPeriod, PayCadence

_ZERO = Decimal("0")


# The span the projected end-balance chart covers, in MONTHS.
#
# The developer's normal grid timeframe (data-value pass, Gate B amendments),
# and `period_projections.SIX_MONTHS` rather than a literal so it is the same
# span the grid's `6M` button and the mobile Plan tab name.  Fewer points
# render when fewer periods exist.
#
# **It was `_CHART_HORIZON_PERIODS = 13` -- a pay-period COUNT -- until plan
# step R-F17**, with `dashboard/_pulse.html`'s canvas announcing "the next six
# months" to every screen reader beside it.  13 periods is six months only at a
# biweekly cadence: a weekly-paid owner's chart plotted 91 days under that
# label and a monthly-paid one plotted thirteen months.  Ledger row **F-17**
# names two surfaces and the developer widened the step to the grid; this one
# was found by BOTH adversarial reviews re-grepping the row's own predicate
# rather than the diff, which is the check
# `feedback_a_closed_row_needs_its_predicate_regrepped` exists for -- the row
# could not honestly close while it stood.
_CHART_HORIZON_MONTHS = SIX_MONTHS

# Most rows a single street station shows before collapsing the remainder
# into a "+N more" line.  Several bills regularly share one due date
# (groceries, gas, spending money budgeted every paycheck), so a day is a
# station -- one dot whose label stacks that day's bills.  This caps the
# stack height so a busy day cannot overflow the fixed-height band; the
# rest of the day's detail lives on the grid (the "Open in grid" link).
_STREET_STATION_MAX_VISIBLE = 3


# ── Pulse producer (canvas + street + due-soon) ────────────────────


def compute_pulse_section(
    section: "DashboardSection | None",
) -> dict | None:
    """Compute the dashboard pulse region (canvas + street + due-soon).

    The single ``balanceChanged`` refresh region of the Terminal Road
    rebuild (Loop B B-1).  Everything it returns derives from the same
    transaction state, so one producer + endpoint serves the hero figure,
    the projected end-balance chart, the full-horizon trough, the
    still-due totals, and the current period's due-soon rows.

    Returns ``None`` when the user has no resolvable account, no baseline
    scenario, or no period contains today -- the page renders its
    no-data / no-scenario fallback in that case (matching the other
    narrow producers' ``None`` contract).  Otherwise returns a dict with
    keys:

      * ``hero`` -- see :func:`_hero`.
      * ``chart`` -- see :func:`_chart`.
      * ``trough`` -- see :func:`_trough` (``None`` when no period
        is projected): the lowest projected end balance ahead.
      * ``peak`` -- see :func:`_peak` (``None`` in the same no-period
        case as ``trough``): the highest projected end balance ahead, the
        exact mirror of ``trough`` over the same full forward horizon.
      * ``still_due`` -- see :func:`_still_due`.
      * ``street`` -- see :func:`_street` (the current period's
        day-span and today's offset within it).
      * ``due_soon`` -- see :func:`_due_soon` (the flat bill list;
        the template's "anytime this period" shelf reads its undated
        rows).
      * ``due_soon_stations`` -- see :func:`_due_soon_stations` (the
        dated rows grouped per day for the street axis, so bills sharing a
        due date render as one station instead of overlapping).

    Args:
        section: The render's :class:`~._section.DashboardSection`, or ``None``
            when the owner has no resolvable grid account.  **The read pass
            arrives on it rather than being built here** (pay-calendar plan
            step C2-f2e, ledger rows **P56** and **P61**): this producer and
            :func:`~._tracks.compute_tracks_section` each opened one, so ``/``
            held two passes and derived the owner's pay calendar twice per
            render.  The route opens the one pass and both take it.

    Returns:
        The pulse-region dict, or ``None`` when the region cannot be
        computed (no account / scenario / current period).
    """
    # The no-baseline arm this guard used to carry is GONE (plan step X-v2,
    # ruling R-BW): the seam raises and one application-level handler answers,
    # so this region no longer decides what a user with no computable balance
    # sees.  What remains are the two states this producer genuinely models --
    # no grid account, and no period containing today.
    #
    # **The account is checked FIRST and that ORDER is load-bearing.**  Reading
    # the owner's reported window derives their calendar, and a no-account
    # owner reached no calendar before this step; a producer that starts
    # deriving one for them is how a cheap structural refusal turns into
    # whatever the derivation decides.  The failure that made the order
    # URGENT is closed -- ledger row **P35**, a legacy period whose stored span
    # ``resolve_cadence`` read back as a cadence outside 1..365, which plan
    # step C4-b-2 made unstorable -- but the ORDER stays, because it is about
    # not asking a question this owner has no answer for, and that is true
    # whatever the derivation happens to refuse today.  It is the opposite of
    # ``/grid``, which derives before it looks at an account and which P35
    # recorded as WIDENED for exactly that.
    if section is None:
        return None

    balance_ctx = section.balance_ctx
    account = section.account
    settings = section.settings
    # The owner's saved schedule, from the SAME window the balance seam reports
    # every per-period figure on this page over (pay-calendar plan step C2-f2e,
    # ledger row **P36**).  It was ``pay_period_service.get_all_periods``: a
    # second read of one schedule, ordered by the stored ``period_index`` where
    # the seam's own domain is ordered by payday -- so a stored ordinal that
    # disagreed with payday order (plan finding **P1**) plotted this chart in
    # one order and the balances it reads in another, and a destructive
    # pay-period write landing between the two reads left this producer
    # indexing the map by an id the seam no longer held.  One derivation, no
    # second spelling -- and THIS is the line that fills the pass's calendar
    # memo, since ``reported_periods()`` is ``calendar().saved()``; every other
    # calendar read on this render is a memo hit.
    all_periods = balance_ctx.reported_periods()
    # WHERE in that window the pass's day falls, and the current period is the
    # entry AT it.  Asked of the window rather than derived from
    # ``period_index`` (C2-f2e's adversarial code review, 2026-08-18): the two
    # agree only because ``reported_periods()`` is the window over the WHOLE
    # saved calendar, so its ordinals happen to equal the calendar's -- and
    # ``PeriodWindow.containing_index``'s own docstring names that as the
    # coincidence a caller must not rest on, being "a second rule about how a
    # view's ordinals relate to the calendar's, stated at the caller and
    # enforced nowhere".  It is also ONE bisect where reading
    # ``section.current_period`` beside a ``period_index`` filter was two.
    current_index = all_periods.containing_index(balance_ctx.as_of)
    if current_index is None:
        return None
    current_period = all_periods[current_index]
    # The pass's own calendar answers "which paycheck comes next" (plan step
    # C2-f1), off the memo the window read above filled -- ``BalanceContext.build``
    # resolves only the scenario, so the calendar's two queries are paid once.
    next_period = balance_ctx.calendar().period_starting_after(
        current_period.start_date,
    )

    # ONE projection walk over ALL periods through the ``balance_at`` seam's
    # CASH-FLOW view (``cash_balance_map``).  The dashboard account is
    # ``resolve_grid_account``'s pick and may be ANY kind (a user can point
    # the dashboard at an HYSA, or the fallback can land on a non-checking
    # account), and this region is the spending-account runway -- so it reads
    # the pure transaction running balance, NOT the kind-correct
    # ``balance_map`` (which would accrue interest into an HYSA's chart,
    # amortize a loan, or compound an investment, and would inflate the
    # "lowest point ahead" so a real future dip below zero could be hidden).
    #
    # **The RUNWAY argument above is the whole reason; the agreement argument
    # this comment used to give beside it was FALSE and is deleted** (finding
    # N-87, ruling R-AK).  It claimed the modelled map would diverge "from the
    # grid that deliberately keeps the SAME account on the cash-flow view".
    # The grid has layered an accrual for an INTEREST account since PR #47, so
    # this region and the grid ALREADY disagree for that kind: measured at the
    # last projected period on the prod-shape clone 2026-07-27, Fidelity
    # Savings $5,363.56 here against the grid's $5,779.68 ($416.12) and the
    # Money Market $16,644.27 against $17,348.99 ($704.72).  Plan step X-g3b
    # extends the same gap to the INVESTMENT and APPRECIATING kinds.  The
    # divergence is RECORDED, not fixed: whether ``/dashboard``'s runway
    # question should read a modelled balance is its own ruling with its own
    # measurement, and it is not made inside a render cutover.
    #
    # The chart slices the current-period-forward
    # tail to 13 points and the trough scans the whole forward tail.  The
    # trough horizon is the entire forward run -- the retired
    # negative-projection alert's full multi-year reach, but DELIBERATELY
    # INCLUDING the current period the alert skipped (``start_date <=
    # today``): the chart's first plotted point is the current period's end
    # balance, so the labeled "lowest point ahead" must be able to coincide
    # with it rather than understating the worst visible dip.
    # **Every read of this map below is INDEXED, and the two skips that used to
    # sit under the chart and the extremum scan are DELETED** (C2-f2e's
    # adversarial code review, 2026-08-18).  ``cash_balance_map`` is a TOTAL
    # fold (plan step X-c2b2) over ``ctx.reported_periods()``, and
    # ``forward_periods`` is a SLICE of that same window, so every key is
    # present by construction.  The skips were kept on the ground that "the
    # forward slice is the caller's, not the producer's", which does not follow
    # -- a caller-chosen subset of the domain is still in the domain -- and
    # what they would have done had they ever fired is exactly what the hero's
    # own comment below refuses: a chart quietly missing a point, or a "lowest
    # point ahead" scanned over fewer periods than its label promises.
    end_balances = balance_at.cash_balance_map(account, balance_ctx)
    forward_periods = list(all_periods)[current_index:]

    # ONE unpaid-item load for the still-due totals AND the due-soon list:
    # both read the current period's items (due-soon is exactly that subset)
    # and still-due additionally reads the next period's, so loading the
    # current+next set once -- with its single ``selectinload(entries)``
    # round trip -- and splitting it in memory avoids a second identical
    # load on the ``balanceChanged`` refresh path.  The items are the
    # paycheck's across the owner's cash-flow set (ruling R-CC16, plan step
    # CC-4-3) where the hero, the chart and the trough above are the balance
    # account's alone: a bill on the card is still a bill this paycheck owes.
    # Since leaf X-bi-6-1b they are the set's own ROWS and the expense LEGS
    # of its transfers, read off the parents (the loader's docstring).
    period_ids = [current_period.period_id]
    if next_period is not None:
        period_ids.append(next_period.period_id)
    unpaid = _query_unpaid_expense_rows(
        section.cash_flow, balance_ctx.scenario_id, period_ids,
    )
    # ONE valuation of that one item set, for the same reason the load is
    # shared (plan step X-au-c2): the due-soon list and the still-due totals
    # both price the SAME items, and two valuations of one item are how two
    # figures on one screen come to disagree.  Built here rather than per item
    # because the salary producer runs the paycheck engine over the owner's
    # whole pay-period set (finding **N-228**).  The line that used to sit here
    # claimed this set "makes no query at all, every row being an expense": the
    # salary half indeed answers nothing, but the legs are priced off their
    # parents (a loan payment's through rule 4), so the LOAN half is asked
    # and resolves its config map.  Each map is ONE call over every item,
    # keyed by ``cell_key`` and valued by the item's parent (ruling
    # plan#1983) -- an ``int`` and a tuple cannot collide, so one dict serves
    # both shapes (``routes/grid/_items.build_amount_maps``).
    contributions = cash_ledger.contributions_by_key(
        unpaid.items, balance_ctx.amounts(),
    )
    # The second question the same items must answer, off the SAME basis (plan
    # step X-au-c2b): what an envelope's BUDGET is, which ruling E-21 fixes on
    # the row's own amount unconditionally -- never the entered actual, never
    # status-dependent -- so a contribution cannot stand in for it.
    budgets = cash_ledger.amounts_by_key(unpaid.items, balance_ctx.amounts())

    due_soon = _due_soon(
        unpaid.items, contributions, budgets, current_period, balance_ctx.as_of,
    )

    return {
        # The key is present BY CONSTRUCTION since pay-calendar plan step
        # C2-f2e: ``current_period`` is now a member of the very window the map
        # was built over (``reported_periods()``, which is
        # ``calendar().saved()``), where it used to be a ``get_current_period``
        # row that the map's domain merely happened to contain.  Indexed rather
        # than ``.get``-with-a-default on purpose: a default here would render
        # SOME number for a hero whose own period the projection did not cover,
        # which is the silent-wrong shape this arc exists to end.
        "hero": _hero(
            section, end_balances[current_period.period_id], current_period,
            next_period,
        ),
        # The cadence is read behind the ``current_index is None`` return
        # above, which is the proof the calendar holds paydays and therefore a
        # readable cadence (plan step R-F17; see ``horizon_offsets``).
        "chart": _chart(
            forward_periods, end_balances, settings,
            balance_ctx.calendar().cadence,
        ),
        "trough": _trough(
            forward_periods, end_balances, current_period,
        ),
        "peak": _peak(
            forward_periods, end_balances, current_period,
        ),
        "still_due": _still_due(
            unpaid.items, contributions, budgets, current_period, next_period,
        ),
        "street": _street(current_period, balance_ctx.as_of),
        "due_soon": due_soon,
        "due_soon_stations": _due_soon_stations(due_soon),
    }


def _hero(
    section: DashboardSection,
    balance: Decimal,
    current_period: "DerivedPeriod",
    next_period: "DerivedPeriod | None",
) -> dict:
    """Build the pulse hero block: the period-END balance and its captions.

    The headline ``balance`` is the CURRENT PERIOD'S projected end balance,
    read straight off the ``cash_balance_map`` the chart is built from -- so
    the hero IS the chart's first point rather than a second producer call
    that has to agree with it.

    **It reads the period end, and the template is why** (plan step X-c2b2).
    ``_pulse.html`` labels this figure "End of this period" and captions it
    "projected through <period end>".  It used to be the as-of-TODAY scalar,
    which was legitimate only because that scalar was period-FLAT: it summed
    the whole period's still-projected rows whatever their dates, so "today"
    and "period end" were the same number.  The fold makes the scalar
    date-precise (finding cash D2), so a bill due later this period is no
    longer in today's balance -- and reading it here would have put the
    balance TODAY under a label promising the balance at the period's END,
    differing by the whole unpaid remainder of the period.  The figure follows
    the label.

    The cash-flow view (NOT the kind-correct map, which would accrue interest
    / amortize / compound) is deliberate: the account is
    ``resolve_grid_account``'s any-kind pick, and the chart the hero must
    agree with reads ``cash_balance_map`` for the same reason.  Net pay is
    retired (data-value pass); only the next-paycheck DATE survives.

    ``is_stale`` is ``True`` when the anchor has never been set OR its
    last update is strictly older than ``settings.anchor_staleness_days``
    (the same settings-driven staleness the retired Alerts card used; the
    rebuild moves the signal onto the "last updated" caption).

    Args:
        section: The render's :class:`~._section.DashboardSection` -- the
            account this hero is about (``resolve_grid_account``'s pick; may be
            any non-amortizing kind), the settings its staleness threshold
            reads, and the read pass whose pinned day the staleness count
            measures from.  Taken as one value rather than as three arguments
            because they are one value: they arrive together, from one
            resolution, and a caller could not honestly supply a settings row
            belonging to a different owner than the account.
        balance: The current period's projected end balance, off the same
            ``cash_balance_map`` the chart plots.
        current_period: The period containing the pass's day.  Passed rather
            than re-read off *section* so the hero's dates and the balance
            beside them come from ONE bisect of the calendar.
        next_period: The paycheck after it, or ``None`` past the schedule's
            end.  **TAKEN rather than looked up** (plan step C2-f1): this
            module held a ``_next_paycheck_date`` query of its own -- "the
            first period whose ``start_date`` is after ``date.today()``" --
            for a value the caller had already resolved as "the period after
            the current one", and the same card renders both (the hero's
            next-paycheck caption beside the still-due panel's
            ``next_period_start``).
            **The two questions were not the same question**, which is what
            made keeping both a defect rather than a duplication: this one is
            anchored on *current_period*, which ``get_current_period`` selected
            by the STORED span, while the deleted query anchored on the clock
            -- so wherever a stored ``end_date`` disagreed with the derived
            one (plan finding **P1**, the disagreement nothing reconciles) the
            period the card called current need not be the period today fell
            in, and the two captions named different paydays.  It also read its
            own ``date.today()``, a second clock in one render.  Since
            pay-calendar plan step C2-f2e *current_period* is itself derived
            from the paydays at the pass's own day, so the stored span is out
            of the question entirely and one clock answers both.

    Returns:
        A dict with keys ``balance``, ``period_start_date``,
        ``period_end_date``, ``account_name``, ``account_id``,
        ``last_updated_date``, ``is_stale``, ``next_paycheck_date``.
    """
    # The day the balance was TRUE, read as a stored fact -- ONE value feeding
    # both the caption and the staleness count (ruling R-EP, plan step
    # X-f1c3a).  It was ``MAX(created_at)`` truncated to a day: a THIRD
    # statement of "which assertion is latest", keyed on the RECORDING instant
    # rather than the business day, so a back-dated assertion recorded later
    # named the wrong row -- and before ruling R-DH (b) the truncation ran in
    # two zones at once, so for four hours each evening the caption and the
    # arithmetic disagreed about which day it was (finding N-133).
    # ``reconciled_through`` is deliberately the accessor rather than
    # ``resolve_anchor``: this surface wants only the day, it must answer
    # ``None`` for an account with no assertion (the never-set staleness arm)
    # rather than raise, and the two are provably equal -- ``MAX(observed_on)``
    # against the last element of the same ordering, pinned by a test.
    account = section.account
    last_observed_on = cash_ledger.reconciled_through(account.id).observed_day

    return {
        "balance": balance,
        "period_start_date": current_period.start_date,
        "period_end_date": current_period.end_date,
        "account_name": account.name,
        "account_id": account.id,
        "last_updated_date": last_observed_on,
        "is_stale": _anchor_is_stale(
            last_observed_on, section.settings, section.balance_ctx.as_of,
        ),
        "next_paycheck_date": (
            next_period.start_date if next_period is not None else None
        ),
    }


def _anchor_is_stale(
    observed_on: date | None,
    settings: UserSettings | None,
    as_of: date,
) -> bool:
    """Return whether the checking anchor is stale (the warning condition).

    Stale means the anchor has NEVER been set (``observed_on`` is ``None``) OR
    the day it was asserted for is strictly older than
    ``settings.anchor_staleness_days`` (the same settings-driven threshold
    the retired Alerts card used; the rebuild surfaces it on the "last
    updated" caption rather than as a separate alert).  A no-settings
    fallback uses ``_DEFAULT_STALENESS_DAYS``.  Extracted from
    :func:`_hero` so the never-set branch is testable without a
    resolvable anchor (the resolver hard-raises on a truly empty history,
    so the never-set state cannot reach the hero's balance call in
    production -- but the branch is defensive and worth pinning).

    **It measures from the day the balance was TRUE, not the day it was
    recorded** (ruling R-EP, plan step X-f1c3a).  Those were the same value
    until ``observed_on`` became user-supplied; a back-dated assertion recorded
    today is stale on the day it names, which is what a user asking "how old is
    this number" means.

    **The comparison's own clock is the READ PASS's since pay-calendar plan
    step C2-f2e, and it is still the PROCESS day.**  What that step made true is
    narrower than "this render reads one clock": the three reads THIS producer
    owned now share the pass's day.  ``BalanceContext.amounts()`` still reads
    ``date.today()`` for its loan half, which is finding **N-40** and plan step
    **X-i2**, and this region reaches it through
    :func:`~app.services.cash_ledger.contributions_by_key`.  That read is
    disclosed on the memo rather than fixed here, because X-i2 MOVES MONEY.  It was a bare
    ``date.today()`` here -- one of three this region resolved for itself,
    beside the street's "Today" marker and the due-soon list's days-until-due
    -- so a render crossing midnight could count staleness against one day and
    place the marker on the next.  ``as_of`` is the same value every one of
    them now reads, pinned once when the route built the pass and unable to
    move under its consumers.  What this does NOT change is WHICH clock: the
    pass defaults to ``date.today()``, the process day, while ``observed_on``
    is a display-zone civil day, so the two can still differ by one across the
    evening window -- the finding N-138 class, whose app-side conversion was
    BUILT AND REVERTED because doing it site by site turns agreeing-but-wrong
    into actively disagreeing and no gate can currently certify completeness.
    The split is neither created nor widened here; ledger row **P55** carries
    it.

    Args:
        observed_on: The civil day the latest assertion was true for, or
            ``None`` when the anchor has never been set.
        settings: The user's settings, or ``None``.
        as_of: The read pass's pinned day -- the render's ONE clock.

    Returns:
        ``True`` when the anchor is never-set or older than the threshold.
    """
    if observed_on is None:
        return True
    staleness_days = (
        settings.anchor_staleness_days if settings
        else _DEFAULT_STALENESS_DAYS
    )
    return (as_of - observed_on).days > staleness_days


def _chart(
    forward_periods: "list[DerivedPeriod]",
    end_balances: dict[int, Decimal],
    settings: UserSettings | None,
    cadence: PayCadence,
) -> dict:
    """Build the projected end-balance chart series and threshold line.

    The current period plus however many of the owner's paychecks fall inside
    :data:`_CHART_HORIZON_MONTHS` (fewer when
    fewer periods exist) -- each ``{end_date, balance}`` from the
    anchor-forward end-balance map.  The first point coincides with the
    hero by construction: both are the current period's entry in the SAME
    folded period map (plan step X-c2b2), so the chart's first point IS the
    hero rather than a second producer that has to agree with it.  ``low_balance_threshold`` is
    the user's setting as a ``Decimal`` (the column is a NOT NULL
    whole-dollar integer, so it always carries a value when a settings
    row exists) or ``None`` only when the user has no settings row at
    all -- the UI draws it as a faint dashed line.

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            the seam's folded ``cash_balance_map``.
        settings: The user's settings, or ``None`` when the user has no
            settings row.
        cadence: The owner's :class:`~app.services.pay_calendar.PayCadence`,
            which decides how many of their paychecks the span holds.  Read by
            the caller only behind its own no-current-period return, which is
            the proof the calendar carries a cadence at all.

    Returns:
        A dict with keys ``points`` (a list of ``{end_date, balance}``
        dicts) and ``low_balance_threshold`` (``Decimal``, or ``None``
        when ``settings`` is ``None``).
    """
    # As many POINTS as the owner has paychecks inside the span -- a COUNT
    # starting at the current period, the same reading the grid's range buttons
    # and the Plan tab take, NOT the chips' offset (plan step R-F17).  At
    # biweekly that is 13, exactly the literal it replaces: 13 points span
    # 13 x 14 = 182 days, so the canvas's "next six months" label is now true
    # at every cadence rather than only at the developer's.
    #
    # The floor of one is the same POLICY `_build_plan_view` states: a chart on
    # the dashboard must draw something, where a range BUTTON may simply not be
    # offered.
    chart_periods = forward_periods[
        :max(cadence.paychecks_within(_CHART_HORIZON_MONTHS), 1)
    ]
    points = [
        {"end_date": period.end_date, "balance": end_balances[period.period_id]}
        for period in chart_periods
    ]

    threshold = None
    if settings is not None:
        threshold = Decimal(str(settings.low_balance_threshold))

    return {"points": points, "low_balance_threshold": threshold}


def _trough(
    forward_periods: "list[DerivedPeriod]",
    end_balances: dict[int, Decimal],
    current_period: "DerivedPeriod",
) -> dict | None:
    """Find the lowest projected end balance over the FULL forward horizon.

    The "lowest point ahead" stat -- the minimum extremum from
    :func:`_extremum` (``find_max=False``).  See that helper for the
    full-horizon scan, the deliberate current-period inclusion, and the
    ``offset`` deep-link contract.

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            the seam's folded ``cash_balance_map``.
        current_period: The period containing today (the offset origin).

    Returns:
        A dict with keys ``balance`` (the minimum end balance),
        ``end_date``, and ``offset`` (>= 0).  ``None`` only when
        *forward_periods* is EMPTY -- an owner whose schedule ends before the
        pass's day, which the caller's own current-period guard already
        refuses, so in practice never.
    """
    return _extremum(
        forward_periods, end_balances, current_period, find_max=False,
    )


def _peak(
    forward_periods: "list[DerivedPeriod]",
    end_balances: dict[int, Decimal],
    current_period: "DerivedPeriod",
) -> dict | None:
    """Find the highest projected end balance over the FULL forward horizon.

    The "highest point ahead" stat -- the exact mirror of
    :func:`_trough`, the maximum extremum from
    :func:`_extremum` (``find_max=True``).  Scans the same full
    forward horizon (current period onward, all periods, not just the 13
    charted points) and degrades to ``None`` in the same no-projection
    case as the trough.

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            the seam's folded ``cash_balance_map``.
        current_period: The period containing today (the offset origin).

    Returns:
        A dict with keys ``balance`` (the maximum end balance),
        ``end_date``, and ``offset`` (>= 0).  ``None`` only when
        *forward_periods* is EMPTY -- an owner whose schedule ends before the
        pass's day, which the caller's own current-period guard already
        refuses, so in practice never.
    """
    return _extremum(
        forward_periods, end_balances, current_period, find_max=True,
    )


def _extremum(
    forward_periods: "list[DerivedPeriod]",
    end_balances: dict[int, Decimal],
    current_period: "DerivedPeriod",
    find_max: bool,
) -> dict | None:
    """Find the extreme projected end balance over the FULL forward horizon.

    The shared core of :func:`_trough` (``find_max=False``, the
    minimum) and :func:`_peak` (``find_max=True``, the maximum):
    scans every period from the current one forward (not just the 13
    charted points), so a danger dip OR a peak beyond the chart window is
    still caught.  The horizon is the retired negative-projection alert's
    full multi-year forward reach, with one deliberate divergence: that
    alert skipped the current period (``start_date <= today``), whereas
    this scan INCLUDES it, because the chart's first plotted point is the
    current period's end balance and the "lowest/highest point ahead" stat
    must be able to coincide with it rather than understating the worst
    visible dip (or the visible peak).  The winner's ``offset`` is its
    ``period_index`` minus the current period's, so the UI can deep-link
    the grid at that period (``grid.index?offset=N``).

    Args:
        forward_periods: Periods from the current one forward, ordered by
            ``period_index``.
        end_balances: The ``period_id -> Decimal`` end-balance map from
            the seam's folded ``cash_balance_map``.
        current_period: The period containing today (the offset origin).
        find_max: ``True`` to return the maximum end balance (the peak),
            ``False`` to return the minimum (the trough).

    Returns:
        A dict with keys ``balance`` (the extreme end balance),
        ``end_date``, and ``offset`` (>= 0).  ``None`` only when
        *forward_periods* is EMPTY -- an owner whose schedule ends before the
        pass's day, which the caller's own current-period guard already
        refuses, so in practice never.
    """
    extremum_period = None
    extremum_balance = None
    for period in forward_periods:
        balance = end_balances[period.period_id]
        if extremum_balance is None:
            extremum_balance = balance
            extremum_period = period
            continue
        if (balance > extremum_balance) if find_max else (balance < extremum_balance):
            extremum_balance = balance
            extremum_period = period

    if extremum_period is None:
        return None

    return {
        "balance": extremum_balance,
        "end_date": extremum_period.end_date,
        "offset": extremum_period.period_index - current_period.period_index,
    }


def _still_due(
    rows: list[PlanItem],
    contributions: dict,
    budgets: dict,
    current_period: "DerivedPeriod",
    next_period: "DerivedPeriod | None",
) -> dict:
    """Compute the still-due totals for the current and next periods.

    Locked basis (Gate B4, data-value pass):

      * Untracked projected expense rows contribute what the row is worth
        (the row's displayed obligation: actual when populated, else its
        resolved amount; never negative for an expense).
      * Entry-tracked rows contribute their entries-aware remaining
        (the row's resolved BUDGET minus the sum of recorded entries) FLOORED
        AT ZERO -- an over-budget envelope contributes ``0``, never a
        negative that would understate the total (its overspend already
        left the as-of-today balance).
      * A transfer's EXPENSE LEG IS included (B4b): a still-due total is
        an obligation figure -- what the paycheck still owes across the
        owner's cash-flow set (plan step CC-4-3), a card's bill included --
        and the loader carries each transfer in as the leg on the side its
        money leaves (leaf X-bi-6-1b; it was the transfer-out shadow row).

    Each period's total is summed in full ``Decimal`` precision and
    rounded once at the boundary with :func:`round_money`.

    Args:
        rows: The current+next periods' unpaid expense items -- rows and
            expense legs -- from the shared :func:`_query_unpaid_expense_rows`
            load (made once by :func:`compute_pulse_section` and shared with
            :func:`_due_soon`); each item is bucketed by its
            ``pay_period_id``.
        contributions: ``{cell_key: Decimal}`` over exactly those items,
            from the caller's one :func:`~app.services.cash_ledger
            .contributions_by_key` call, and
            shared with :func:`_due_soon`.  Indexed with ``[]``: an item
            missing from it is a caller that priced a different set, and a
            default here would be a fabricated figure in a total.
        budgets: ``{cell_key: Decimal}`` over the same items, from the
            caller's one :func:`~app.services.cash_ledger.amounts_by_key`
            call -- the E-21 base an entry-tracked row's
            remaining is computed against.  Indexed with ``[]`` for the
            reason the contributions are.
        current_period: The period containing today.
        next_period: The period after the current one, or ``None``.

    Returns:
        A dict with keys ``current_period`` and ``next_period``, each a
        ``Decimal`` total (``round_money(_ZERO)`` -> ``Decimal("0.00")``
        for a period with no still-due rows; ``next_period`` is
        ``Decimal("0.00")`` when there is no next period), plus
        ``next_period_start`` and ``next_period_end`` (the next period's
        date range, both ``None`` when there is no next period -- the
        template renders a generate-periods fallback line then).
    """
    current_total = _ZERO
    next_total = _ZERO
    for txn in rows:
        key = cell_key(txn)
        contribution = _row_still_due(txn, contributions[key], budgets[key])
        if txn.pay_period_id == current_period.period_id:
            current_total += contribution
        elif next_period is not None and txn.pay_period_id == next_period.period_id:
            next_total += contribution

    return {
        "current_period": round_money(current_total),
        "next_period": round_money(next_total),
        "next_period_start": (
            next_period.start_date if next_period is not None else None
        ),
        "next_period_end": (
            next_period.end_date if next_period is not None else None
        ),
    }


def _row_still_due(
    txn: PlanItem, contribution: Decimal, budget: Decimal,
) -> Decimal:
    """Return one row's still-due contribution on the locked basis (B4a).

    An entry-tracked (envelope) row contributes its entries-aware
    remaining (its resolved BUDGET minus the sum of all recorded
    entries, via :func:`compute_remaining`) floored at zero -- so an
    over-budget envelope contributes ``0`` rather than a negative.

    **It is floored BELOW and deliberately not above** (developer ruling
    2026-09-01, plan step ``bank_import:X-gj-2b-3``).  Since ruling
    **bank_import:R-II** a refund files as a negative purchase, so an
    envelope's remaining can EXCEED its budget -- `$150.00` still due against a
    `$100.00` plan, for one holding a `-$50.00` refund and nothing else -- and
    that figure is what the ruled net basis means: the envelope's plan is
    `$100.00` of NET spending and `-$50.00` of it has happened.  Capping it at
    the budget was refused with those numbers, because the dashboard would then
    disagree with the balance projection beside it.  A
    non-tracked row contributes what the row is WORTH (the obligation the
    bill row already displays; positive for an expense).  Returned
    unrounded; the caller rounds the period sum once at the boundary.

    Args:
        txn: A projected expense row with ``entries`` eager-loaded (the
            canonical load loads them), or a transfer's expense leg, which
            tracks no purchases and so contributes what it is worth.
        contribution: What this row contributes, from the caller's one
            :func:`~app.services.cash_ledger.contributions_by_key` call --
            the replacement for ``txn.effective_amount``, which could not
            answer for a row whose amount is DERIVED (plan step X-au-c2).
            Read only for a non-tracked row; an envelope answers on its
            E-21 budget base instead.
        budget: The row's resolved amount -- the E-21 base, from the caller's
            one :func:`~app.services.cash_ledger.amounts_by_key` call.  Read
            only for an entry-tracked row.  An ARGUMENT rather than a read of
            ``txn.estimated_amount`` since plan step X-au-c2b: a derived row
            stores no figure in that column.

    Returns:
        The row's still-due ``Decimal`` contribution (>= 0).
    """
    if txn.tracks_purchases:
        # The row's PURCHASES, never the family (ruling R-BAL68): the seam's
        # covering movement is not spend against the budget.
        remaining = compute_remaining(budget, txn.purchases)
        return remaining if remaining > _ZERO else _ZERO
    return contribution


def _street(current_period: "DerivedPeriod", as_of: date) -> dict:
    """Build the street band's day-span and today's offset within it.

    The street band lays the current period out day by day, and the
    due-soon rows already position each dated event at
    ``(due_date - current_period.start_date).days`` (see
    :func:`_due_soon`).  These two numbers SHARE that same basis so
    the band's percentage math lines up: the period start is day 0, the
    period end is ``days_total``, and an event due on the start sits at 0
    while one due on the end sits at ``days_total``.

      * ``days_total`` -- ``(end_date - start_date).days``: the day span
        of the current period.  The period-end station on the street sits
        at this offset (the far right of the band).
      * ``today_offset`` -- ``(as_of - start_date).days``: where the
        "Today" marker falls on the band.  It CANNOT fall outside ``[0,
        days_total]`` since pay-calendar plan step C2-f2e: the period is the
        one the pass's own day falls in and *as_of* is that same day, so
        ``start_date <= as_of <= end_date`` holds by construction.  It could
        before -- the period came from the STORED span and this offset from a
        second ``date.today()``, so a render crossing midnight placed the
        marker one day past a period the other clock had already left.  The JS
        still clamps defensively.

    Args:
        current_period: The period containing the pass's day.
        as_of: The read pass's pinned day -- the render's ONE clock, shared
            with the due-soon list's days-until-due and the hero's staleness
            count.

    Returns:
        A dict with integer keys ``days_total`` and ``today_offset``.
    """
    return {
        "days_total": (current_period.end_date - current_period.start_date).days,
        "today_offset": (as_of - current_period.start_date).days,
    }


def _due_soon(
    rows: list[PlanItem],
    contributions: dict,
    budgets: dict,
    current_period: "DerivedPeriod",
    as_of: date,
) -> list[dict]:
    """Build the current period's due-soon rows (the street / mobile list).

    Reuses the shared row query and the shared :func:`txn_to_bill_dict`
    bill builder (the same dicts the live bills card renders), then adds
    two fields each row needs for the street band and its mobile
    fallback:

      * ``day_offset`` -- ``(due_date - current_period.start_date).days``
        for a dated row, so the row sits at the right day on the
        full-width street axis spanning the current period; ``None`` for
        an undated row.
      * ``undated`` -- ``True`` when the row has no ``due_date`` (it lands
        on the "anytime this period" shelf, not a day position).

    Only the current period's unpaid rows are returned (the audit's
    Tier-2 scope: overdue + rest of current period; prior-period unpaid
    rows stay out of scope, as they are for the live bills card).  Rows
    are sorted by ``due_date`` ascending (undated rows last, by name) so
    the list reads chronologically.

    Args:
        rows: The current+next periods' unpaid expense items -- rows and
            expense legs -- from the shared :func:`_query_unpaid_expense_rows`
            load (made once by :func:`compute_pulse_section` and shared with
            :func:`_still_due`).  This helper filters to the current
            period's items -- the next-period items in the shared set are
            the still-due totals' concern, not the due-soon list's.
        contributions: ``{cell_key: Decimal}`` over exactly those items,
            from the caller's one :func:`~app.services.cash_ledger
            .contributions_by_key` call, and
            shared with :func:`_still_due`, so a bill's amount cell and the
            still-due total it feeds price the item ONCE.
        budgets: ``{cell_key: Decimal}`` over the same items, shared the
            same way -- the E-21 base an entry-tracked bill's amount cell and
            its progress fields both answer on.
        current_period: The period containing the pass's day.
        as_of: The read pass's pinned day, which each bill's
            ``days_until_due`` counts from.  It was a ``date.today()`` of this
            function's own until pay-calendar plan step C2-f2e -- a third clock
            on one render, beside the street marker's and the hero staleness
            count's -- so a bill could read "due today" under a street band
            that had already moved to tomorrow.

    Returns:
        A list of bill dicts (see :func:`txn_to_bill_dict`) each carrying
        the extra ``day_offset`` and ``undated`` keys.
    """
    due_soon: list[dict] = []
    for txn in rows:
        if txn.pay_period_id != current_period.period_id:
            continue
        key = cell_key(txn)
        bill = txn_to_bill_dict(txn, as_of, contributions[key], budgets[key])
        if txn.due_date is not None:
            bill["day_offset"] = (txn.due_date - current_period.start_date).days
            bill["undated"] = False
        else:
            bill["day_offset"] = None
            bill["undated"] = True
        due_soon.append(bill)

    # Dated rows first, sorted by due_date; undated rows last, by name.
    due_soon.sort(
        key=lambda b: (
            b["undated"],
            b["due_date"] if b["due_date"] is not None else current_period.start_date,
            b["name"],
        )
    )
    return due_soon


def _due_soon_stations(due_soon: list[dict]) -> list[dict]:
    """Group the dated due-soon rows into per-day street stations.

    The street band positions each dated row at ``(day_offset / days) *
    100%``; rows sharing a ``due_date`` share a ``day_offset`` and so land
    on the same point.  Rendering one label per row there stacks them on a
    single pixel (the original overlap bug).  Instead a *day* is one
    station: a single dot whose label lists that day's bills.

    The input is the :func:`_due_soon` output, already sorted
    ``(undated, due_date, name)``.  Dated rows therefore lead the list in
    chronological order and ``day_offset`` is monotonic across them, so
    consecutive grouping by ``day_offset`` yields one entry per calendar
    day in axis order.  Undated rows (``day_offset`` ``None``) are dropped
    here -- they belong on the "anytime this period" shelf, not the axis.

    Every row in a station shares one ``due_date``, hence one
    ``days_until_due`` (carried on the station for the dot's overdue /
    soon / normal state) -- it is read from the first member.  The visible
    rows are capped at :data:`_STREET_STATION_MAX_VISIBLE`; any remainder
    is reported as ``extra_count`` for a "+N more" line.

    Args:
        due_soon: The sorted bill-dict list from :func:`_due_soon`.

    Returns:
        One station dict per dated day, in axis order, each with keys
        ``day_offset`` (int), ``days_until_due`` (int | None), ``count``
        (int -- total bills that day), ``visible_items`` (the capped
        bill-dict list), and ``extra_count`` (int -- bills beyond the cap).
    """
    dated = [bill for bill in due_soon if not bill["undated"]]

    stations: list[dict] = []
    for day_offset, members in groupby(dated, key=lambda b: b["day_offset"]):
        group = list(members)
        count = len(group)
        stations.append(
            {
                "day_offset": day_offset,
                "days_until_due": group[0]["days_until_due"],
                "count": count,
                "visible_items": group[:_STREET_STATION_MAX_VISIBLE],
                "extra_count": max(0, count - _STREET_STATION_MAX_VISIBLE),
            }
        )
    return stations
