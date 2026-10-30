"""
Shekel Budget App -- Grid route package: the full-page render.

``GET /grid`` -- the primary view, a spreadsheet-like grid whose columns are
pay periods and whose rows are income / expense line items -- plus the
``POST /create-baseline`` repair door the no-baseline card posts to.

The three HTMX fragments this page then re-renders on its own events live in
:mod:`~app.routes.grid.partials`; the producers both halves read are in
:mod:`~app.routes.grid._shared`.
"""

import logging
from decimal import Decimal
from typing import NamedTuple

from flask import redirect, render_template, request, url_for
from flask_login import current_user

from app.db_transaction import write_transaction
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.models.transaction import Transaction
from app.services import (
    baseline_service,
    grid_view_service,
    pay_period_rolling,
)
from app.services.account_resolver import (
    resolve_owner_and_view,
    serves_cash_detail,
)
from app.services.balance_at import BalanceContext
from app.services.cash_flow_set import CashFlowSet
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.services.grid_view_service import RowKey
from app.services.statement_match import awaiting_review_count
from app.services.pay_calendar import DerivedPeriod, PayCadence, PeriodWindow
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today
from app.utils.digit_strings import integer_arg
from app.utils.period_projections import (
    ONE_YEAR_MONTHS,
    SIX_MONTHS,
    TWO_YEARS_MONTHS,
    offered_spans,
)

from app.routes._period_population import populate_new_periods
from app.routes.grid._bp import grid_bp
from app.routes.grid._items import build_amount_maps, load_grid_items
from app.routes.grid._shared import (
    _accrual_row_label,
    _build_grid_view,
    _resolve_low_balance_threshold,
    _resolve_visible_window,
)

logger = logging.getLogger(__name__)


# The span the mobile "Plan" tab looks forward over, in MONTHS.
#
# Plan answers "what does the next half-year look like from today?", decoupled
# from the URL's `periods` / `offset` so the This Period arrow nav cannot
# starve it of forward visibility.  It is `period_projections.SIX_MONTHS`
# rather than a literal 6, and that is the point: it is deliberately the same
# span as the desktop selector's `6M` button, and naming the number once is
# what makes the two agree STRUCTURALLY.  A first draft wrote two literal
# sixes and asserted in a comment that they "cannot come to disagree", which
# is the agreement-by-sentence ledger row **F-17** is about.
#
# **It was `PLAN_WINDOW_PERIODS = 13` -- a pay-period COUNT -- until plan step
# R-F17.** 13 periods is six months only at a biweekly cadence; at a weekly one
# the tab covered three months and at a monthly one it covered thirteen.
PLAN_WINDOW_MONTHS = SIX_MONTHS

# The grid's date-range quick-select buttons that name a SPAN OF TIME, as
# `(span in months, button label, tooltip)`.
#
# **Resolved into a count of the owner's paychecks** by
# `period_projections.offered_spans` (ruling **R-R31**).  The template held
# these as a literal pairing each label with a hardcoded biweekly period count
# until plan step R-F17 -- 6M with thirteen periods, 1Y with twenty-six -- so a
# weekly-paid owner pressing `1Y` got six months of columns and a monthly-paid
# one got two years, each under a button naming a year.  Ledger row **F-17**.
_RANGE_MONTHS: tuple[tuple[int, str, str], ...] = (
    (SIX_MONTHS, "6M", "6 months"),
    (ONE_YEAR_MONTHS, "1Y", "1 year"),
    (TWO_YEARS_MONTHS, "2Y", "2 years"),
)

# The same selector's buttons that name a COUNT OF PAYCHECKS, which is a
# different question and needs no derivation: "3 pay periods" means three
# columns for every owner.  They are listed separately rather than folded in
# with a sentinel, so nothing has to test which kind a row is.
_RANGE_PAYCHECKS: tuple[tuple[str, int, str], ...] = (
    ("3P", 3, "3 pay periods"),
    ("6P", 6, "6 pay periods"),
)


def _range_options(cadence: PayCadence) -> list[tuple[str, int, str]]:
    """Build the date-range quick-select buttons for one owner's cadence.

    The paycheck-count buttons first (they are absolute), then each time-span
    button resolved into however many of THIS owner's paychecks fall inside it.

    **Each distinct window is offered ONCE**, and that is a defect this step
    created and its adversarial review caught.  The deleted literal was
    `3 / 6 / 13 / 26 / 52`, which can never collide; deriving the span buttons
    makes one land on the fixed 3 or 6 of a paycheck button at **64 of the 365
    legal cadences, the shortest being 28 days** -- so a monthly-paid owner got
    `6P` and `6M` both linking to `?periods=6`, and since the template marks a
    button active with `num_periods == count`, BOTH lit up.  The paycheck
    labels are exact and cadence-independent, so the DERIVED label is the one
    that yields.

    **A span no paycheck reaches is not offered at all** (ruling **R-R31**),
    which here is also what keeps the control working rather than merely
    honest: an owner paid every 300 days has no paycheck inside six months, and
    a `6M` button linking to `?periods=0` renders the empty-schedule page for a
    user whose schedule is not empty.

    Args:
        cadence: The owner's :class:`~app.services.pay_calendar.PayCadence`.

    Returns:
        The `(label, column count, tooltip)` rows, in display order, one per
        distinct window.
    """
    options = list(_RANGE_PAYCHECKS)
    offered = {count for _, count, _ in options}
    for count, label, tooltip in offered_spans(cadence, _RANGE_MONTHS):
        if count not in offered:
            options.append((label, count, tooltip))
            offered.add(count)
    return options


class _GridContext(NamedTuple):
    """Request-derived context for the grid view.

    Produced by :func:`_resolve_grid_context`.  Carrying this as a
    :class:`typing.NamedTuple` lets :func:`index` access fields via
    attribute (``ctx.balance_ctx``) without binding a separate local per
    field from a tuple unpack -- the same pattern keeps the
    orchestrator's pylint ``R0914`` count below the project threshold
    after the mobile-follow-up Commit 8 / F-6 decomposition.

    **Every period here is DERIVED since plan step C2-f2b**, and ``user_id``
    went with the change: the three ``pay_period_service`` queries that needed
    it are now three views of the ONE calendar the read pass memoizes, and
    :attr:`balance_ctx` already carries the owner.  Nothing here reads
    ``end_date`` or ``period_index`` out of the table, which is what plan step
    **C4-c** needed before it could drop both columns.

    Attributes:
        balance_ctx: The read pass's ``BalanceContext`` (scenario + as-of +
            the owner's pay calendar).
        cash_flow: The VIEW of the owner's cash-flow set this page renders --
            checking and its cards, with the balance line's account named
            (developer ruling ``credit_card:R-CC16``, plan step CC-4-1;
            :func:`~app.services.account_resolver.resolve_owner_and_view`) --
            or ``None`` when the user has no account rows at all (the
            post-Commit-3 user-with-zero-accounts edge case).  The rows this
            page loads are every member's; the balance, the anchor and the
            bank control are :attr:`account`'s.
        owner_cash_flow: The OWNER's set with no override, from the same walk
            -- what a purchase under any row on this page may name (plan step
            ``credit_card:CC-5-2``, ruling **R-CC37**;
            :func:`~app.services.cash_flow_set.purchase_accounts`).  A second
            field rather than :attr:`cash_flow` because an override naming an
            account OUTSIDE the set collapses the view to that one account --
            the rows this page shows -- while what a purchase may name is the
            owner's set whatever page it is recorded from, and the purchase
            door tests against exactly that.  The same object as
            :attr:`cash_flow` when the request names no override.
        num_periods: Count of visible pay-period columns.
        start_offset: Offset added to the current period's
            ``period_index`` for the leftmost visible column.
        current_period: The paycheck covering the pass's ``as_of`` (the natural
            leftmost column when ``start_offset == 0``).
        periods: The visible period slice, as a
            :class:`~app.services.pay_calendar.PeriodWindow` of length at most
            ``num_periods`` -- shorter where the schedule ends first.
        all_periods: ALL of the user's SAVED pay periods -- the seam's own
            reporting domain (``BalanceContext.reported_periods``) rather than
            a second reading of it, so the columns and the transactions this
            route loads cannot describe different period sets.  Not
            anchor-forward: the fold answers a pre-anchor period with the
            balance in force then (plan step X-c2b2), and each period is valued
            off its own span.
    """

    balance_ctx: BalanceContext
    cash_flow: CashFlowSet | None
    owner_cash_flow: CashFlowSet | None
    num_periods: int
    start_offset: int
    current_period: DerivedPeriod
    periods: PeriodWindow
    all_periods: PeriodWindow

    @property
    def account(self) -> Account | None:
        """The BALANCE line's account (the set's primary, or the override).

        What every single-account surface on this page reads -- the header,
        the anchor, the bank control, the accrual label, the create form's
        default -- so it is named once rather than spelled
        ``ctx.cash_flow.balance if ctx.cash_flow else None`` at each.
        """
        return self.cash_flow.balance if self.cash_flow is not None else None

    @property
    def members(self) -> tuple[Account, ...]:
        """The set's members, in picker order; empty for the no-account owner.

        What the Add Transaction modal's account picker offers (plan step
        ``credit_card:CC-4-2``): every account a paycheck's plan item may live
        on, the balance line's among them.  ONE member is the pre-card grid,
        and the template renders the hidden input it always did for that case.
        """
        return self.cash_flow.members if self.cash_flow is not None else ()


def _resolve_grid_context(user_id, request_args, settings):
    """Resolve scenario, account, and period range from the request.

    Args:
        user_id: ID of the requesting user.
        request_args: Flask ``request.args`` (or any compatible
            multidict).  Parsed for ``account_id``, ``periods``, and
            ``offset``.
        settings: ``current_user.settings`` (a ``UserSettings`` row)
            or ``None``.  Source of the default ``grid_default_periods``
            when the request omits ``periods``.

    A user with no baseline scenario is NOT handled here (plan step X-v2,
    ruling R-BW).  This route used to render ``errors/no_baseline.html`` itself
    and was the only surface in the app that did, while `/savings` fabricated a
    ``$0.00`` hero and three other pages returned a 500.  The raise comes from
    the caller's :func:`~app.routes.grid._items.load_grid_items`, which asks the context for
    the scenario id it scopes its query with; ONE application-level handler
    renders that same card, so every surface answers the state identically and
    this route states no policy about it.

    **Its ORDER against the no-periods answer changed with it**: a user missing
    both now sees ``no_periods.html``, where the deleted guard put the baseline
    card first.  Neither is a dead end (the card is one click from any balance
    page), and a user with no periods and no baseline needs both repairs.

    **All three period reads come off the pass's own calendar since plan step
    C2-f2b**, through the one resolver
    :func:`~app.routes.grid._shared._resolve_visible_window` the self-refresh
    fragments also call.  What that consolidated is the number of CLOCK READS,
    not the number of clocks: ``get_current_period`` defaulted to
    ``date.today()`` and ``BalanceContext.build`` defaults to the same, so the
    two agreed except across midnight, where one request could seat the current
    column in one paycheck and value the pass in another.  *The render still
    carries a second clock and this is not the step that unifies it*:
    ``today=display_today()`` below drives the past-column styling and the
    carry-forward button, and it is the DISPLAY day where ``as_of`` is the
    process day (ledger row **P49**, owned by ``C2-f3``).

    It is DETERMINISTIC now too: the retired reader's SQL carried no
    ``ORDER BY`` and took ``.first()`` (ledger row **P19**), so two rows
    covering one day -- expressible in the stored columns, unconstructible in
    the derivation -- returned whichever the planner reached first.

    Returns:
        A :class:`_GridContext` on success, OR a rendered HTML string (the
        ``no_periods.html`` early-return page) when the user has no current pay
        period.  The caller distinguishes via ``isinstance(result, str)``.
    """
    balance_ctx = BalanceContext.build(user_id)

    # The owner's cash-flow set: checking and its cards (ruling R-CC16), the
    # balance line being the primary grid account or the ``account_id``
    # override -- an override within the set keeps the set's rows behind
    # that member's balance; one outside it (a savings account) is that
    # account's single-account grid as it always was.
    #
    # Both halves from ONE walk (plan step credit_card:CC-5-2): the view this
    # page shows, and the owner's set with no override for the add-purchase
    # picker under every envelope -- the purchase door admits the row's own
    # account plus the OWNER's members whatever page the row is recorded
    # from, and an override outside the set has just collapsed the view to
    # one account.
    cash_flow_sets = resolve_owner_and_view(
        user_id, settings, request_args.get("account_id", type=integer_arg),
    )

    # Determine the visible period range.
    num_periods = request_args.get(
        "periods",
        default=(settings.grid_default_periods if settings else 6),
        type=integer_arg,
    )
    start_offset = request_args.get("offset", default=0, type=integer_arg)

    resolved = _resolve_visible_window(balance_ctx, num_periods, start_offset)
    if resolved is None:
        return render_template("grid/no_periods.html")
    current_period, periods = resolved
    if not periods:
        return render_template("grid/no_periods.html")

    return _GridContext(
        balance_ctx=balance_ctx,
        cash_flow=cash_flow_sets.view,
        owner_cash_flow=cash_flow_sets.owner,
        num_periods=num_periods,
        start_offset=start_offset,
        current_period=current_period,
        periods=periods,
        # ALL of the user's periods -- the fold answers every one, and each
        # render window is a slice of this domain.  Asked of the PASS rather
        # than of the calendar so this and the seam's column set are the same
        # window by construction, not two reads that happen to agree.
        all_periods=balance_ctx.reported_periods(),
    )


class _GridRowData(NamedTuple):
    """Row-render values produced by :func:`_build_grid_row_data`.

    The four fields are the grid's per-render "row contract": they are
    produced together and spliced together into the ``grid/grid.html``
    render context, so carrying them as a :class:`typing.NamedTuple`
    (rather than a four-tuple unpacked into four parallel locals) keeps
    both :func:`index` and :func:`_build_plan_view` under pylint's
    ``R0914`` local-count threshold and names each value at the call
    site.

    **The two ENTRY maps left at pay-calendar plan step C4-a-3** for
    :func:`_build_entry_maps`, which :func:`index` alone calls; the whole
    argument for the move is stated there rather than four times over.

    Attributes:
        income_row_keys: Ordered income-section row keys for the row
            window (the visible window, or the full projection when
            ``show_all``).
        expense_row_keys: Ordered expense-section row keys.
        matched_by_row_period: ``(category_id, template_id, txn_name,
            period_id) -> matched transactions`` index read by the cell
            template.
        due_captions: ``{txn_id -> the due date to caption, or None}``
            for the cell template's "Due:" line
            (:func:`~app.services.grid_view_service.due_captions_by_key`,
            pay-calendar plan step C4-a-1).  The template decided it
            itself off ``t.pay_period.start_date`` until then, which is
            a lazy relationship load issued from inside the render.
    """

    income_row_keys: list[RowKey]
    expense_row_keys: list[RowKey]
    matched_by_row_period: dict[tuple[int, int | None, str, int], list[Transaction]]
    due_captions: dict[int, "date | None"]


def _build_grid_row_data(transactions, periods, show_all, all_categories):
    """Build row keys, the (row_key, period) match index, and the due captions.

    Row keys + the (row_key, period) -> matched-transactions dict are
    produced by the pure :mod:`app.services.grid_view_service`.  The
    service is also called from :func:`app.routes.companion.index` so
    the owner mobile grid and the companion view share one definition
    of the row-key dedup, sort order, and cell-matching predicate
    (mobile-first v3 plan Commit 13 / D-B).

    Row generation is scoped to the visible window by default so the
    grid stays uncluttered when planning far in advance.  ``show_all``
    opts back in to the full forward projection for full-picture
    review.  Balance math, cell matching, and subtotals work off the
    un-filtered ``transactions`` list -- any txn hidden from row-key
    generation has ``pay_period_id`` outside the visible window, so
    it contributes $0 to every visible-period subtotal and its cells
    were never going to render.

    **It builds no ENTRY maps** since pay-calendar plan step C4-a-3, and
    :class:`_GridRowData` carries why: this runs twice per render and only one
    of the two runs draws a purchase list.

    Returns a :class:`_GridRowData` carrying ``income_row_keys``,
    ``expense_row_keys``, ``matched_by_row_period`` and ``due_captions``.
    """
    if show_all:
        row_source_txns = transactions
    else:
        visible_period_ids = {p.period_id for p in periods}
        row_source_txns = [
            t for t in transactions
            if t.pay_period_id in visible_period_ids
        ]

    income_row_keys = grid_view_service.build_row_keys(
        row_source_txns, all_categories, is_income_section=True,
    )
    expense_row_keys = grid_view_service.build_row_keys(
        row_source_txns, all_categories, is_income_section=False,
    )
    matched_by_row_period = grid_view_service.build_matched_by_row_period(
        income_row_keys, expense_row_keys, periods, transactions,
    )

    # The caption map is built over exactly the rows that get a CELL -- the
    # values of the match index -- rather than over ``transactions``, which
    # carries rows outside the visible window.  That is what makes the payday
    # lookup total: every key it needs is a period being drawn, and a row it
    # cannot cover would raise rather than caption silently.
    due_captions = grid_view_service.due_captions_by_key(
        [txn for cell in matched_by_row_period.values() for txn in cell],
        {period.period_id: period.start_date for period in periods},
    )

    return _GridRowData(
        income_row_keys=income_row_keys,
        expense_row_keys=expense_row_keys,
        matched_by_row_period=matched_by_row_period,
        due_captions=due_captions,
    )


class _GridEntryMaps(NamedTuple):
    """What the grid's ENVELOPE cards render their purchase lists from.

    Attributes:
        entry_sums: ``{txn_id -> sums}``, the cell template's
            "spent / budget" progress aggregate.
        entry_lists: ``{txn_id -> entry_list_view}``, the inline mobile
            entries list per envelope card, rendered server-side to avoid
            per-card HTMX fan-out.
    """

    entry_sums: dict[int, dict]
    entry_lists: dict[int, dict]


def _build_entry_maps(
    transactions, budgets, all_periods, owner_cash_flow,
) -> _GridEntryMaps:
    """Build the two ENVELOPE maps, and the paycheck spans one of them needs.

    **They were built inside :func:`_build_grid_row_data` until pay-calendar
    plan step C4-a-3**, which is the whole of that step's five-argument fork.
    That helper runs TWICE per render and :func:`_build_plan_view` DISCARDS
    both maps it returns -- it publishes six ``plan_*`` keys and neither is
    one of them, while its own docstring had claimed since the tab was built
    that no entry data was computed for it.  So the fork's answer was a
    DELETION rather than a move (ruling **R-PC35**): nothing gained an
    argument, ``_GridRowData`` lost two fields, and that helper and
    ``_build_plan_view`` each lost ``budgets``.

    **It is a FUNCTION rather than two calls written into**
    :func:`index`'s ``render_template`` **arguments**, and an adversarial
    review of this step measured why (2026-08-31): naming those two results
    as locals there takes :func:`index` to ``R0914`` at 16/15, so leaving
    them inline would have been load-bearing rather than stylistic -- a
    quieter way past a ceiling than the scoped disable this step refused,
    because a disable is greppable and an inlined producer call is not.  One
    named value costs one local, which is what ``period_spans`` cost before
    it moved in here beside its only consumer.

    Args:
        transactions: Every item the page loaded, rows and transfer sides
            alike (finding plan#2050) -- NOT the visible-window slice.  A
            side holds no purchase and tracks none, so neither map holds one.
            The mobile card macro indexes these maps by ``txn.id``
            for any row it draws, and narrowing them to the rendered window
            is a behaviour question this leaf does not open (R-PC35's own
            rejected list).
        budgets: The page's ONE ``{transaction_id: amount}`` map.
        all_periods: The window *transactions* was LOADED by --
            ``ctx.all_periods``, the same value :func:`~app.routes.grid._items.load_grid_items`
            scopes its ``pay_period_id IN (...)`` with, read one field by both
            so the span map below cannot cover fewer paychecks than the rows
            it is handed.  Passing the visible slice instead would be a
            ``KeyError`` on a live render, which is why the two reads sit in
            one function rather than being resolved apart.
        owner_cash_flow: ``ctx.owner_cash_flow`` -- the owner's set with no
            override, from which each envelope's add-purchase picker is
            derived (plan step ``credit_card:CC-5-2``).

    Returns:
        The :class:`_GridEntryMaps` for this render.
    """
    return _GridEntryMaps(
        entry_sums=build_entry_sums_dict(transactions, budgets),
        # Pre-render context for the inline mobile entries list on envelope
        # cards.  Computed server-side rather than via per-card HTMX
        # ``hx-trigger="load"`` fan-out to keep one grid page load from
        # blowing past the ``RATELIMIT_DEFAULT`` ceiling of "30 per minute"
        # on the entries endpoint -- with 6 visible periods and ~10 envelope
        # templates each, the lazy-load shape generated ~60 parallel GETs
        # and the over-limit cards stuck on the loading spinner forever.
        #
        # The SPANS the out-of-period purchase warning is judged against
        # (plan step C4-a-3), keyed the way a row names its paycheck.  They
        # are DERIVED, where the warning read the stored ``end_date`` plan
        # step C4-c dropped.
        entry_lists=build_entry_lists_dict(
            transactions, budgets,
            {period.period_id: period for period in all_periods},
            owner_cash_flow,
            # The OWNER is looking: the grid is owner-only, so its purchase
            # lists name the owner's bank lines (plan step
            # ``credit_card:CC-5-4a-5``).
            current_user.id,
        ),
    )


def _build_plan_view(ctx, all_transactions, grid_view, all_categories):
    """Build the read-only "Plan" tab context window.

    The Plan tab on the mobile grid answers "what does the next half-
    year look like from today?" regardless of how the user is
    navigating in This Period (which can leave the URL at
    ``?periods=1&offset=N``).  This helper computes a parallel data
    slice anchored at ``current_period`` and walking forward far enough to
    cover :data:`PLAN_WINDOW_MONTHS` months of the OWNER's paychecks.

    **No entry sums or entry lists are computed, and that sentence became
    TRUE at pay-calendar plan step C4-a-3** -- Plan renders future periods
    read-only and envelope entries are by design a current / past concept.
    It stood here while :func:`_build_grid_row_data` built both maps over
    every loaded row on this call as well, and returned them into a
    ``row_data`` this function drops on the floor: the claim described the
    OUTPUT and the work was done anyway.  Both builds now live at
    :func:`index`, the one caller that renders them, which is also why this
    function no longer takes the ``budgets`` map it only ever forwarded.

    Args:
        ctx: The :class:`_GridContext` for this request.  Supplies
            ``current_period`` (the plan window's anchor) and, through
            ``balance_ctx``, the pass's own pay calendar -- the same memoized
            derivation the visible window was cut from, so Plan and This Period
            are two views of one schedule rather than two reads of the table.
        all_transactions: The items (rows and legs) already loaded by
            :func:`~app.routes.grid._items.load_grid_items`.  Re-used here instead of
            re-querying; ``_build_grid_row_data`` filters by visible
            window internally so the same list works for the wider
            Plan window.
        grid_view: The :class:`~app.services.balance_at.GridBalanceView`
            produced by :func:`_build_grid_view`.  Its columns are SLICED to
            the plan periods -- never recomputed, which is what keeps the Plan
            recap and the This Period card two windows onto one projection
            rather than two projections.
        all_categories: User's full category set (active + archived).
            Forwarded to the row-key builder so archived-category
            transactions still render.

    Returns:
        Dict with six ``plan_*`` keys ready to splice into the
        ``render_template`` kwargs of :func:`index`:

          - ``plan_periods``: a
            :class:`~app.services.pay_calendar.PeriodWindow` starting at
            ``current_period`` and holding as many of the owner's paychecks as
            fall inside :data:`PLAN_WINDOW_MONTHS` months.  May be shorter when
            the user has fewer remaining generated periods.
          - ``plan_income_row_keys`` / ``plan_expense_row_keys``:
            row-key lists scoped to the plan window.
          - ``plan_matched_by_row_period``: same shape as the
            interactive ``matched_by_row_period`` -- keys are
            ``(category_id, template_id, txn_name, period_id)``.
          - ``plan_columns``: dict[period_id ->
            :class:`~app.services.balance_at.GridColumn`], the view's own
            columns for the plan window.
          - ``plan_row_flags``: the window's
            :class:`~app.services.balance_at.GridRowFlags` (ruling R-O's
            conditional-row rule, evaluated for THIS window rather than the
            visible one -- the Plan tab reaches periods the grid may not
            show).
    """
    calendar = ctx.balance_ctx.calendar()
    # How many of THIS owner's paychecks land inside the plan span (plan step
    # R-F17), where a hardcoded 13 stood for "six months, biweekly".
    #
    # **The floor of one is a POLICY, and saying so is the correction an
    # adversarial review of this step forced.** The comment here argued it was
    # an invariant -- "the paycheck the owner is currently IN already spans the
    # whole of it" -- and that is false for every phase but the period's first
    # day: at a 244-day cadence with today on day 240, the current period ends
    # in four days and this renders four forward days under a tab meaning the
    # next half-year.  The honest statement is the policy: a TAB must render
    # something, so it renders the paycheck the owner is in.  The desktop
    # selector OMITS such a button instead (ruling R-R31), because a button is
    # optional where a tab is not.
    plan_window = max(
        calendar.cadence.paychecks_within(PLAN_WINDOW_MONTHS), 1,
    )
    plan_periods = calendar.window(
        ctx.current_period.period_index, plan_window,
    )

    row_data = _build_grid_row_data(
        all_transactions, plan_periods, False, all_categories,
    )

    return {
        "plan_periods": plan_periods,
        "plan_income_row_keys": row_data.income_row_keys,
        "plan_expense_row_keys": row_data.expense_row_keys,
        "plan_matched_by_row_period": row_data.matched_by_row_period,
        "plan_columns": {
            p.period_id: grid_view.columns[p.period_id]
            for p in plan_periods if p.period_id in grid_view.columns
        },
        "plan_row_flags": grid_view.row_flags(plan_periods),
    }


class _BankControl(NamedTuple):
    """The grid's door into what the BANK said, and whether it is waiting.

    Attributes:
        account_id: The grid account, for the statements page URL.
        awaiting: How many bank lines the review is still asking about
            (:func:`~app.services.statement_match.awaiting_review_count`).
            ``0`` is rendered as a plain door rather than hiding it: the
            control is how the feature is FOUND, and a badge that appears
            only once lines exist cannot be found by someone who has never
            imported one.
    """

    account_id: int
    awaiting: int


def _bank_control(account, calendar) -> "_BankControl | None":
    """Return the grid's bank statement control, or ``None`` for no control.

    **Gated on the target page's own kind rule**
    (:func:`~app.services.account_resolver.serves_cash_detail`), not on the
    grid's.  The two differ: :func:`~app.services.account_resolver
    .is_cash_flow_account` refuses only amortizing loans, so an owner with no
    checking account can have a Property or a Roth IRA resolved as their grid
    account -- and the statements page 404s exactly those.  Rendering the
    control off the grid's own gate would put a door onto an error page.

    Args:
        account: The resolved grid account, or ``None`` when the owner has no
            account rows at all.
        calendar: The read pass's memoized
            :class:`~app.services.pay_calendar.PayCalendar`.  Threaded from
            the route rather than re-derived, so this count and the screen it
            links to split at the same first payday.

    Returns:
        The :class:`_BankControl`, or ``None`` when no control belongs here.

    Note:
        **There is deliberately NO guard here for an account carrying no
        ``budget.account_openings`` row**, and it was MEASURED rather than
        reasoned about.  A draft of plan step balance:X-f3c-2b-2b added one --
        ``awaiting_review_count`` reaches the RAISING loader
        (``cash_ledger.account_opening_fact``), so the concern was that a
        broken invariant would take the app's hottest page down rather than one
        control on it.  Deleting every opening row for the seeded account and
        rendering ``/grid`` showed why that guard could never help: the request
        raises inside ``_build_grid_view`` (through
        ``balance_at.grid_balance_view`` -> ``cash_ledger.walk_cash_ledger`` ->
        ``cash_ledger.account_opening_fact``, which is the RAISING loader), and
        ``index`` does not reach this function until many statements later.
        Line numbers are deliberately not quoted: the first draft of this note
        cited two, measured with the deleted guard still in place, and both
        were three lines stale before it was committed.  The guard was
        UNREACHABLE for the state it named, while costing a second
        ``budget.account_openings`` read per cash account on every grid render
        -- the DRY violation ``awaiting_review_count``'s own docstring names
        four lines from where the read was added.  ``CLAUDE.md`` rule 13: no
        error handling for a state that cannot arrive here.
    """
    if account is None or not serves_cash_detail(account):
        return None
    return _BankControl(
        account_id=account.id,
        awaiting=awaiting_review_count(
            account.user_id, account.id, calendar.opening_bound(),
        ),
    )


@grid_bp.route("/grid")
@require_owner
def index():
    """Render the full budget grid page.

    Loads the current period as the leftmost column with future
    periods extending to the right.  The number of visible periods is
    controlled by query params or user settings.  Orchestrates
    :func:`_resolve_grid_context` (period range + early returns),
    :func:`~app.routes.grid._items.load_grid_items`, :func:`_build_grid_balances`,
    :func:`_build_grid_subtotals`, :func:`_build_grid_row_data` and
    :func:`_build_entry_maps`, then dispatches to ``grid/grid.html``.

    *Two of those names resolve to nothing and have since before this
    function was decomposed*: ``_build_grid_balances`` and
    ``_build_grid_subtotals`` exist nowhere in ``app/``.  Recorded rather
    than repaired here (``CLAUDE.md`` rule 6) by the adversarial review of
    plan step ``pay_calendar:C4-a-3``, 2026-08-31, which is the step that
    added the last name in the list.
    """
    user_id = current_user.id

    # Continuous rolling window: top up before resolving the grid so any
    # newly generated periods are visible this request.  It writes nothing
    # when rolling is disabled (one schedule read) -- but the block below is a
    # COMMAND transaction, so it takes the owner's write lock at that first
    # read whether or not rolling is enabled (plan step ``balance:X-bn``,
    # ruling R-CC114: a page load's write takes the lock like any save).
    # *Until that step this said "one count, no lock": the top-up took the
    # lock only on a deficit.*
    #
    # **In its own COMMAND transaction** (plan step X-i3): this render is a
    # query, so its transaction is one read-only snapshot and cannot hold the
    # append.  The block writes, commits, and the read pass below then takes
    # its snapshot of a database that already holds the new periods -- which is
    # the ordering this route always needed and stated in prose.
    #
    # The top-up APPENDS and this fills what it appended (ruling **R-R38**):
    # the door leaves the new paydays empty, and the pass their recurring rows
    # are resolved in is opened here, after they exist.  It opens NOTHING when
    # nothing was appended, which is every render but the rare short-window
    # one -- so this page still holds ONE read pass on the render path.
    with write_transaction():
        appended = pay_period_rolling.top_up_rolling_window(user_id)
        populate_new_periods(user_id, appended)

    ctx = _resolve_grid_context(
        user_id, request.args, current_user.settings,
    )
    if isinstance(ctx, str):
        return ctx

    items = load_grid_items(
        ctx.cash_flow, ctx.balance_ctx, ctx.all_periods,
    )
    grid_view, anchor = _build_grid_view(ctx.cash_flow, ctx.balance_ctx)
    # The ONE map every cell on this page reads its amount from, built by the
    # ONE rule every OTHER surface reads it by (``amounts_by_key``): what the
    # row's amount RESOLVES to (ruling R-Q).  It reads the pass's own basis, so
    # the cell and the balance row beside it cannot price one row two ways.
    #
    # **It was ``display_amounts_by_id`` -- that resolve with a live recompute
    # laid over it -- until plan step X-au-d.**  The composition had been
    # written twice and differently before X-au-c2b extracted it (the fragments
    # and the companion published the resolved map ALONE under the same context
    # key, so the grid showed a drifted salary row its live net while the
    # quick-edit box the same click opened showed the stale column).  What the
    # cutovers then did was delete the second term rather than the second
    # spelling: a derived row stores no figure, so there is nothing for a
    # recompute to supersede and the composition collapsed onto the resolve.
    #
    # **That fall-through was ``txn.estimated_amount`` until plan step
    # X-au-c2b**, annotated onto each row as a transient
    # ``live_estimated_amount`` the cell templates read behind an
    # ``is defined`` guard.  Both halves of that had to go: a derived row
    # stores nothing in the column, so the fallback would render an empty
    # string where a figure belongs, and a Jinja ``Undefined`` answers
    # silently, so a render path that forgot to set the attribute showed the
    # stale column with nothing to say it had.  A published MAP is what a
    # template cannot read half of.
    #
    # What each item's money DID sits beside what its amount IS (plan step
    # X-au-c3).  A settled row shows the figure it RECORDED and an unsettled
    # one shows its plan, and the two questions have two maps because they are
    # two questions: merging them would put "has this settled" back inside a
    # figure, which is the overload ``actual_amount`` carried.  The third map
    # is what each item WILL book if it is marked paid, where that differs
    # from both (plan step X-au-c3, developer 2026-08-17): a row reverted out
    # of the settled band KEEPS what it recorded and a re-settle honours it,
    # so its plan is what the balance counts and its retained figure is what
    # a tick books -- two numbers, and the second was visible on no surface
    # but the reconcile panel.  Non-``None`` for exactly the items where that
    # gap is real, so a template draws a marker rather than deciding anything.
    # Since leaf X-bi-6-1 each map holds the transfer LEGS too, by the same
    # three rules over the leg's shape (``_items.build_amount_maps``).
    amounts = build_amount_maps(items, ctx.balance_ctx.amounts())

    # Load ALL categories (including archived) for row-key building so
    # transactions with archived categories still render correctly;
    # the Add Transaction modal dropdown filters to active only.
    all_categories = (
        db.session.query(Category)
        .filter_by(user_id=user_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    show_all = request.args.get("show_all", type=integer_arg) == 1

    row_data = _build_grid_row_data(
        items, ctx.periods, show_all, all_categories,
    )

    # Build the parallel context for the mobile "Plan" tab.  Decoupled
    # from ctx.periods so a `?periods=1&offset=N` URL (driven by the
    # This Period arrow nav) does not starve Plan of forward visibility.
    plan_view = _build_plan_view(
        ctx, items, grid_view, all_categories,
    )

    # The envelope cards' two maps (pay-calendar plan step C4-a-3).  It takes
    # ``ctx.all_periods`` -- the SAME field ``_items.load_grid_items`` scoped
    # its ``pay_period_id IN (...)`` with, eleven lines up -- so the paycheck
    # spans it derives cannot cover fewer rows than it is handed.
    #
    # **That is also what keeps balance finding N-358 out of THIS lookup**, and
    # it says nothing about the rest of the page: the spans and the rows are
    # cut from one window, so there is no second read for a concurrent payday
    # write to land between, which is the argument ``require_period``'s own
    # docstring makes for a precondition carried by the QUERY.  The rolling
    # top-up above commits BEFORE ``_resolve_grid_context`` opens the pass that
    # window comes from, which is what makes it hold the paydays just appended.
    # Over EVERY item, rows and transfer sides alike: a side holds no purchase
    # and tracks none, so neither builder draws it (finding plan#2050; the
    # entry-list builder filters before it asks anything else), and the card
    # macro reads both maps with ``.get``.
    entry_maps = _build_entry_maps(
        items, amounts.budgets, ctx.all_periods, ctx.owner_cash_flow,
    )

    return render_template(
        "grid/grid.html",
        # The THREE amount maps every cell on this page reads (plan steps
        # X-au-c2b and X-au-c3): what the row's amount IS, what its money DID,
        # and what a tick WOULD book where that differs from both.  Published as
        # context rather than annotated onto each row, so a template cannot read
        # a stale column when a render path forgets.
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        # The ID, not the Scenario ROW (plan step X-v2): the template needs
        # exactly the id for its hidden create-form field, and the two sibling
        # create fragments already take ``scenario_id``.  Passing the nullable
        # model handed a template an object it dereferenced as
        # ``{{ scenario.id }}`` -- a Jinja ``UndefinedError`` waiting on a state
        # the seam now answers before the render, and one more reader of the
        # nullable this step exists to stop handing out.
        scenario_id=ctx.balance_ctx.scenario_id,
        # The BALANCE line's account; the rows above are the whole set's.  The
        # row macros read it too: a cell or card whose row is on another
        # member carries an account chip (plan step CC-4-2).
        account=ctx.account,
        # The set's members, for the Add Transaction modal's account picker
        # (plan step CC-4-2): a picker over more than one, the hidden input
        # as before over one.
        accounts=ctx.members,
        # The door into what the BANK said, beside the anchor it agrees
        # with: both answer "what did this account really do", and the
        # import is how the anchor stops being typed from memory.
        bank_control=_bank_control(ctx.account, ctx.balance_ctx.calendar()),
        periods=ctx.periods,
        current_period=ctx.current_period,
        columns=grid_view.columns,
        row_flags=grid_view.row_flags(ctx.periods),
        # The mobile "This Period" card is a ONE-period surface -- it renders
        # ``periods[0]`` -- so ruling R-O's "at least one visible column" is
        # that column alone.  Scoping it here rather than reusing the desktop
        # window's flags is what keeps the initial include and the
        # ``mobileCardSettled`` refresh (which sees one period and no window)
        # from disagreeing about whether a row is on screen.
        period_row_flags=grid_view.row_flags([ctx.periods[0]]),
        # ONE label for the three surfaces this render feeds -- the desktop
        # <tfoot>, the mobile This Period card and the Plan recap all read it
        # from this context (ruling R-AI / R-P), so the form factors cannot name
        # the same row differently.
        accrual_label=_accrual_row_label(ctx.account),
        categories=[c for c in all_categories if c.is_active],
        income_row_keys=row_data.income_row_keys,
        expense_row_keys=row_data.expense_row_keys,
        statuses=db.session.query(Status).all(),
        transaction_types=db.session.query(TransactionType).all(),
        num_periods=ctx.num_periods,
        # The date-range quick-select buttons, resolved from the OWNER's
        # cadence (plan step R-F17, ruling R-R31).  They were a Jinja literal
        # pairing each month label with a hardcoded biweekly period count, so
        # the labels told the truth for one cadence; a template cannot ask a
        # value object a question, so the resolution belongs here.
        range_options=_range_options(ctx.balance_ctx.calendar().cadence),
        start_offset=ctx.start_offset,
        show_all=show_all,
        col_size=(
            "wide" if ctx.num_periods <= 6
            else "medium" if ctx.num_periods <= 13
            else "compact"
        ),
        anchor_balance=anchor.balance if anchor is not None else Decimal("0.00"),
        anchor_as_of=anchor.observed_on if anchor is not None else None,
        # The USER's today, not the server's UTC one: it drives the
        # past-period column styling AND the add-entry form's hidden
        # ``entry_date`` default, which the entry service refuses on that same
        # display clock (ruling R-M).  Two clocks here would let a UTC-running
        # process stamp tomorrow's date for the evening hours the frames
        # disagree, and the app's own form would post a value its own guard
        # rejects.
        today=display_today(),
        all_periods=ctx.all_periods,
        low_balance_threshold=_resolve_low_balance_threshold(),
        entry_sums=entry_maps.entry_sums,
        entry_lists=entry_maps.entry_lists,
        matched_by_row_period=row_data.matched_by_row_period,
        due_captions=row_data.due_captions,
        **plan_view,
    )


@grid_bp.route("/create-baseline", methods=["POST"])
@require_owner
def create_baseline():
    """Create a missing baseline scenario, idempotently.

    Thin wrapper over
    :func:`app.services.baseline_service.create_baseline_scenario`, which owns
    the create-or-noop AND the recovery of both posting ledgers that had no
    scenario to post into while the baseline was missing.  ``None`` means the
    user already had one, so nothing was created and nothing is logged.
    """
    scenario = baseline_service.create_baseline_scenario(current_user.id)
    if scenario is not None:
        logger.info(
            "action=create_baseline user_id=%s scenario_id=%s",
            current_user.id, scenario.id,
        )
    return redirect(url_for("grid.index"))
