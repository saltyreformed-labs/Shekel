"""The Spending report's result shapes: frozen, Decimal-only, no behaviour.

Every value :func:`~app.services.spending_report_service.compute_spending_report`
returns, plus the three private records its own helpers pass between them.  They
live in one module for the reason :mod:`app.services.ledger_report_service._types`
does: the four producing modules beside this one all construct them, and a
result shape defined next to ONE producer reads as that producer's private
vocabulary rather than the package's contract.

Boundary discipline: no query, no Flask import, no arithmetic -- these are the
shapes, and the modules that fill them are where the rules live.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import spending_analysis
from app.services.cash_flow_set import CashFlowSet
from app.services.pay_calendar import PayCalendar


@dataclass(frozen=True)
class SpendingWindow:
    """The time window a spending report is computed over.

    A discriminated selector mirroring
    :class:`app.services.ledger_report_service.StatementWindow`:
    ``window_type`` decides which of the other fields are meaningful --
    ``period_id`` for ``"pay_period"``, ``month`` + ``year`` for
    ``"month"``, ``year`` for ``"year"``.  A deliberately separate value
    object per its own bounded context (the retrospective spending surface);
    the route layer shares only the parameter parsing.

    Attributes:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (``"pay_period"`` windows only).
        month: The calendar month 1-12 (``"month"`` windows only).
        year: The calendar year (``"month"`` and ``"year"`` windows).
    """

    # Pylint: ``duplicate-code`` -- incidental structural similarity with the
    # ``StatementWindow`` four-field selector.  They are deliberately separate
    # value objects in separate bounded contexts (a spending window scopes
    # settled-expense attribution; a statement window scopes a confirmed-ledger
    # paid-date basis), so a shared base would couple two unrelated attribution
    # rules (coding-standards rule 13).  One-sided disable so ``StatementWindow``
    # stays the un-disabled anchor.
    # pylint: disable=duplicate-code
    window_type: str
    period_id: int | None = None
    month: int | None = None
    year: int | None = None
    # pylint: enable=duplicate-code


@dataclass(frozen=True)
class Comparison:
    """A hero comparison: a baseline plus the signed delta and percent.

    Build one with :meth:`of` so the delta and percent are derived
    identically for both the vs-prior and vs-average chips, and every field
    is ``None`` together when the comparison has no baseline (no prior
    window, or no trailing windows to average).

    Attributes:
        baseline: The comparison baseline (prior spend, or trailing
            average), or ``None`` when no baseline exists.
        delta: ``current - baseline`` (signed), or ``None``.
        pct: ``delta`` as a percent of ``baseline``, or ``None`` -- also
            ``None`` when ``baseline`` is not POSITIVE (an empty prior window,
            or one whose refunds exceeded its purchases).
    """

    baseline: Decimal | None
    delta: Decimal | None
    pct: Decimal | None

    @classmethod
    def of(cls, current: Decimal, baseline: Decimal | None) -> "Comparison":
        """Build a comparison of ``current`` against ``baseline``.

        Args:
            current: The chosen window's spend.
            baseline: The comparison baseline, or ``None`` when none exists
                (all three fields then come back ``None``).

        Returns:
            The :class:`Comparison`.  The ``delta`` is always real; ``pct`` is
            ``None`` for any baseline that is not POSITIVE.

            **Zero was the only such baseline until ruling
            **bank_import:R-II**** (plan step ``bank_import:X-gj-2b``).  A
            window's spend was ``sum(abs(...))`` and could not be negative;
            with a merchant refund filing as a NEGATIVE purchase it can be, for
            a window whose refunds exceeded its purchases -- and a percent
            CHANGE against a negative base reports the wrong DIRECTION, which
            is money the owner reads off a chip.  Worked: spend rises from
            ``-50.00`` to ``100.00``, so ``delta`` is ``+150.00`` and the
            ratio is ``150 / -50 = -300%`` -- a fall of three hundred percent
            printed over a rise.  There is no percentage of a negative base
            that means what this chip means, so the honest answer is the one an
            empty prior window already gets: none.  The figures themselves are
            still shown, so nothing is hidden.
        """
        if baseline is None:
            return cls(baseline=None, delta=None, pct=None)
        delta = current - baseline
        return cls(
            baseline=baseline,
            delta=delta,
            # **The non-positive-base rule is `signed_pct`'s, not this
            # value's** (plan step ``bank_import:X-gj-2b-3``).  A
            # ``baseline > ZERO`` test stood here and strictly subsumed that
            # function's own ``base == ZERO``, so one fact had two guards and
            # the inner one was dead for its only caller.
            pct=spending_analysis.signed_pct(delta, baseline),
        )


@dataclass(frozen=True)
class HeroFigures:
    """The Spending hero band.

    Attributes:
        spent_total: Total settled spend in the chosen window, NET -- refunds
            reduce it (ruling **bank_import:R-II**).
        moved_total: What the window MOVED through its categories, ignoring
            direction (:func:`~._breakdown._share_base`).  **It is here because
            the card would otherwise carry two undisclosed bases** (developer
            ruling 2026-09-01, plan step ``bank_import:X-gj-2b-3``): every
            breakdown row's ``share`` is a fraction of THIS figure while the
            hero printed only the NET, so on ``Groceries 600.00`` beside
            ``Electronics -500.00`` the card read a hero of `$100.00` over rows
            of `$600.00 / 55%` and `-$500.00 / -45%` -- percentages summing to
            about 9%, and a row six times its own hero.  Stating both is what
            lets a reader multiply a row by a figure on the card again.  It is
            EQUAL to :attr:`spent_total` for any window holding no refund, so
            an ordinary month shows one number twice and the screen prints it
            once.
        vs_prior: Comparison against the immediately preceding window of the
            same type.
        vs_average: Comparison against the trailing same-type window average.
        payment_timing: The window's timeliness dict
            (``total_bills_paid`` / ``paid_on_time`` / ``paid_late`` /
            ``avg_days_before_due``), or ``None`` when no bill in the window
            has both a paid date and a due date.
    """

    spent_total: Decimal
    moved_total: Decimal
    vs_prior: Comparison
    vs_average: Comparison
    payment_timing: dict | None


@dataclass(frozen=True)
class SeriesPoint:
    """One bar of the hero chart's trailing same-type window series.

    Attributes:
        window: The window this point covers, or ``None`` when the series
            names none for that slot -- either it lies before the user's
            pay-period history, or the chosen ``period_id`` matched none of
            their periods (:func:`._window._series_windows`).  Only a
            ``"pay_period"`` series can hold one, since a ``"month"`` /
            ``"year"`` series is calendar arithmetic and always names a
            window.
        total: The window's settled spend, or ``None`` when the window
            overlaps no pay period (before the user's history) -- the chart
            renders such a point as a baseline tick, and the vs-average
            derivation excludes it.  A window with periods but no settled
            spend is ``Decimal("0")`` (a real lean window that DOES count
            toward the average).
    """

    window: SpendingWindow | None
    total: Decimal | None


@dataclass(frozen=True)
class SpendingItemRow:
    """One 'Where It Went' drill-down item (a single category).

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        item_name: The category item label.
        amount: Settled spend for this category in the window.  **SIGNED, and
            negative for a category its refunds carried below zero** (ruling
            **bank_import:R-II**).
        share: ``amount`` as a fraction of what the window MOVED -- the sum
            of the categories' MAGNITUDES (:func:`~._breakdown._share_base`),
            a full-precision ``Decimal``; templates render, never compute.
            **In ``[-1, 1]``, and the denominator is what makes that a bound
            rather than a hope** (developer ruling **bank_import:R-IL**, 2026-09-01, plan step
            ``bank_import:X-gj-2b-3``).  A category whose refunds exceeded its
            purchases has a NEGATIVE amount and so a negative share, which is
            the honest reading -- *this category took the window DOWN by this
            much of what moved*.  Dividing by the NET window total instead
            rendered **600%** beside **-500%** on ``Groceries 600.00`` and
            ``Electronics -500.00``; the two denominators are the SAME figure
            for a window holding no refunds, so nothing moved on a window
            without one.  Zero only where nothing moved at all.
        delta: ``amount`` minus the category's prior-window spend (signed;
            the D7 window-over-window change basis).
        is_new: ``True`` when the prior window held NO row for this category.
            **Absence, never a zero total** (plan step
            ``bank_import:X-gj-2b-3``): an envelope settled with no purchases
            contributes ``0`` and a category whose refunds cancelled its
            purchases now does too, so a zero prior total is not an absent one.
            Rendered as a "new" badge instead of a percent of zero.
    """

    category_id: int
    item_name: str
    amount: Decimal
    share: Decimal
    delta: Decimal
    is_new: bool


@dataclass(frozen=True)
class SpendingGroupRow:
    """One 'Where It Went' group row with its drill-down items.

    Attributes:
        group_name: The category group label.
        amount: Settled spend for the whole group in the window.  SIGNED --
            negative for a group its refunds carried below zero, exactly as
            :attr:`SpendingItemRow.amount` is.
        share: ``amount`` as a fraction of what the window MOVED, on the same
            terms as :attr:`SpendingItemRow.share`.
        delta: ``amount`` minus the group's prior-window spend (signed).
            The prior side sums EVERY prior-window category in the group,
            including categories with no spend in the chosen window, so a
            group whose big bill stopped shows the drop.
        is_new: ``True`` when the prior window held NO category in this group
            -- absence and not a zero total, for the reason
            :attr:`SpendingItemRow.is_new` states.
        items: The group's :class:`SpendingItemRow` items, amount-descending.
    """

    group_name: str
    amount: Decimal
    share: Decimal
    delta: Decimal
    is_new: bool
    items: list[SpendingItemRow]


@dataclass(frozen=True)
class ChangeRow:
    """One By-change lens row: a category's window-over-window movement.

    Every category with settled spend in the chosen window OR its prior
    window gets a row -- including zero-current rows (prior spend, none
    now; the D7 rider), so a stopped bill is as visible as a grown one.

    Attributes:
        category_id: The category's id (``0`` for the Uncategorized bucket).
        group_name: The category group label.
        item_name: The category item label.
        current: The chosen window's settled spend (``0`` when none).
        prior: The prior window's settled spend (``0`` when none).
        delta: ``current - prior`` (signed).
        is_new: ``True`` when the prior window held NO row for this category
            -- absence and not a zero total, for the reason
            :attr:`SpendingItemRow.is_new` states.  A row exists here only
            because one of the two windows held the category, so the prior
            window's absence is the whole test.
    """

    category_id: int
    group_name: str
    item_name: str
    current: Decimal
    prior: Decimal
    delta: Decimal
    is_new: bool


@dataclass(frozen=True)
class Surprise:
    """A settled item whose recorded actual differed from its estimate.

    Attributes:
        item_key: The settled item's identity --
            :func:`~app.services.transfer_legs.cell_key`: a row's id, or a
            transfer leg's ``(transfer id, account id)`` since leaf
            ``balance:X-bi-6-1b`` (it carried a shadow row's id, as
            ``transaction_id``).
        name: The item's name.
        group_name: Its category group label.
        item_name: Its category item label.
        estimated: The estimate at entry.
        actual: The entered actual at settle.
        delta: ``actual - estimated`` (signed; positive = over estimate).
    """

    item_key: int | tuple[int, int]
    name: str
    group_name: str
    item_name: str
    estimated: Decimal
    actual: Decimal
    delta: Decimal


@dataclass(frozen=True)
class Surprises:
    """The capped surprises list plus the net across ALL surprises.

    Attributes:
        rows: The surprises sorted by ``abs(delta)`` descending and capped at
            :data:`._surprises._MAX_SURPRISES`.
        net: The signed sum of EVERY surprise's delta (not just the capped
            rows) -- the window's net over/under estimate.
    """

    rows: list[Surprise]
    net: Decimal


@dataclass(frozen=True)
class SpendingScope:
    """The page-context facts the template renders as scope labels.

    Attributes:
        account_id: The BALANCE account of the cash-flow set the report reads
            -- the owner's primary grid account (plan step CC-4-3; the first
            active checking account before it).  The rows are the whole
            set's, checking and its cards (ruling ``R-CC16``); this names the
            member whose balance every other cash-flow surface renders.
        account_name: That account's display name (the on-screen scope
            label the audit's cross-cutting fix requires).
        settled_only: Always ``True`` -- the surface is measured
            (settled-only); carried so the measured chip reads it rather
            than hard-coding the basis.
        window_label: The human window label (e.g. ``"January 2026"``).
    """

    account_id: int
    account_name: str
    settled_only: bool
    window_label: str


@dataclass(frozen=True)
class SpendingReport:
    """The complete Spending surface dataset for one window.

    Attributes:
        scope: The account / basis / window page context.
        hero: The hero band (spent, vs-prior, vs-average, payment timing).
        series: The trailing same-type window series, oldest first, with
            the chosen window last (:data:`._window._CHART_WINDOW_COUNT` points).
        breakdown: The 'Where It Went' group rows, amount-descending.
        changes: The By-change lens rows, delta-magnitude-descending.
        surprises: The capped estimate-surprises list and its net.
    """

    scope: SpendingScope
    hero: HeroFigures
    series: list[SeriesPoint]
    breakdown: list[SpendingGroupRow]
    changes: list[ChangeRow]
    surprises: Surprises


@dataclass(frozen=True)
class _ResolvedWindow:
    """A window resolved to the period set and date span it covers.

    ``first_day`` / ``last_day`` are ``None`` for a ``"pay_period"`` window
    (the period IS the span; ``period_ids`` drives the fetch); for a
    ``"month"`` / ``"year"`` window they bound the COALESCE(due_date, pay
    period start) attribution fetch, and ``period_ids`` (the overlapping
    periods) serves only as the tracked-window signal for the None-vs-zero
    total rule (:func:`_window_total`).
    """

    period_ids: list[int]
    first_day: date | None
    last_day: date | None
    label: str


@dataclass(frozen=True)
class _CategoryTotal:
    """A category's settled spend in one window, with its display labels."""

    group_name: str
    item_name: str
    amount: Decimal


@dataclass(frozen=True)
class _ScopeIds:
    """The scope every settled-spend window load reads through.

    One cohesive concept -- WHOSE data a window reads (the owner's cash-flow
    set, the baseline scenario) and the pay CALENDAR they resolve against,
    which rides here because one report resolves TWELVE windows against it
    and it varies with none of them -- twelve derivations of one value
    otherwise (C2-f1).  A ``"pay_period"`` report asks it twice more, for the
    chart's own run of preceding paychecks
    (:func:`._window._series_windows`, C2-f3d); those two reads replaced
    eleven ``budget.pay_periods`` QUERIES.

    ``cash_flow`` was ``account_id`` until plan step CC-4-3: the rows a
    window loads are the paycheck's across checking and its cards (ruling
    ``R-CC16``), read through the one clause every plan-item reader appends.
    The owner's ``user_id`` rode here too until plan step **plan#2130**, read
    only by the calendar-span loader to scope its pay-period join; the one
    loader states that join against each item's own owner
    (:class:`~app.services.cash_flow_set.AttributionSpan`), so the field had
    no reader and went.
    """

    cash_flow: CashFlowSet
    scenario_id: int
    calendar: PayCalendar
