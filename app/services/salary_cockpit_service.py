"""
Shekel Budget App -- Salary Cockpit Producers

Pure context builders for the ``/salary`` cockpit (the rebuilt salary
landing page) and the projection summary framing.  Every function is
pure: it takes plain data (pay periods already paired with the
:class:`~app.services.paycheck_calculator.PaycheckBreakdown` the paycheck
calculator produced for them, plus the reference ``today``) and returns
plain data.  No Flask, no DB access, no ``float`` -- all money math stays
in :class:`~decimal.Decimal`; the caller casts to ``float`` only at the
JSON serialization boundary.

The projection machinery is reused rather than re-derived (DRY): the
"next raise", "next third paycheck", and per-period series all read the
``raise_event`` string and ``is_third_paycheck`` flag the paycheck
calculator already computed onto each breakdown's
:class:`~app.services.paycheck_calculator.PeriodInfo`.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services import paycheck_line_kinds
from app.services.pay_calendar import DerivedPeriod
from app.services.paycheck_calculator import PaycheckBreakdown
from app.utils.money import HUNDRED, ZERO

# The net-pay staircase chart looks back this many pay periods before the
# period containing ``today`` and forward this many months after it, so
# the user sees recent history plus roughly the next year and a half of
# projected paychecks (matching the locked D2 "wide sky" layout).
PERIODS_BEFORE_TODAY = 6
HORIZON_MONTHS = 18

# One-decimal-place quantum for the composition and deduction-bar
# percentages.  These are percentages, not money: they are intentionally
# NOT rounded through ``round_money`` (which is the cents boundary) and
# are quantized with an explicit ROUND_HALF_UP so the display never
# reaches the decimal context's banker's-rounding default implicitly.
_PCT_QUANTUM = Decimal("0.1")

# The pair the calculator surfaces per period: the period itself plus the
# breakdown computed for it.  Every producer below consumes this shape.
PeriodPair = tuple[DerivedPeriod, PaycheckBreakdown]


def _add_months(base: date, months: int) -> date:
    """Return ``base`` advanced by ``months`` calendar months.

    The day is clamped to the 28th so month-length differences never
    overflow (e.g. Jan 31 + 1 month).  Used only to bound the chart
    horizon, where day-of-month precision is irrelevant.

    Args:
        base: The reference date.
        months: Whole months to add (non-negative in practice).

    Returns:
        A :class:`datetime.date` ``months`` after ``base``.
    """
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(base.day, 28))


def _pct_of_gross(value: Decimal, gross: Decimal) -> Decimal:
    """Return ``value`` as a one-decimal percentage of ``gross``.

    Returns ``Decimal("0")`` when ``gross`` is non-positive so a
    zero-gross period cannot divide by zero.

    Args:
        value: The segment amount (a slice of gross).
        gross: The period gross the segment is measured against.

    Returns:
        The percentage, quantized to one decimal place (ROUND_HALF_UP).
    """
    if gross <= ZERO:
        return ZERO
    return (value / gross * HUNDRED).quantize(_PCT_QUANTUM, rounding=ROUND_HALF_UP)


def clean_raise_label(raw_label: str) -> str:
    """Return a display-clean version of a calculator ``raise_event`` string.

    :func:`app.services.salary_raises.get_raise_event` emits raw
    labels in exactly two shapes, joined with ``", "`` when several raises
    land in one period: ``"{TYPE} +{pct}%"`` (percentage, e.g.
    ``"MERIT +2.5000%"`` -- the trailing places follow the stored
    ``Numeric(5, 4)`` precision) and ``"{TYPE} +${amount:,.2f}"`` (flat,
    e.g. ``"COLA +$2,000.00"``).  Since plan step salary:X-av-3a
    :meth:`~app.services.payroll_basis.PayrollBasis.pay_event_on` emits a
    third, alone, for a recorded pay change (ruling **R-SAL84**):
    ``"PAY +${amount:,.2f}"`` or ``"PAY -${amount:,.2f}"``, the one shape
    carrying a minus, with a trailing ``" a year"`` when the change is
    measured in yearly pay across a change of rhythm (kept verbatim here).
    This cleaner reformats each event for display: the type word is
    title-cased (``COLA`` -> ``Cola``, matching the app-wide
    ``raise_type.name|title`` convention) and a percentage's
    trailing zeros are trimmed (``+2.5000%`` -> ``+2.5%``,
    ``+3.0000%`` -> ``+3%``).  Flat amounts keep their to-the-cent money
    formatting verbatim.  Pure string manipulation on the emitter's own
    Decimal-derived text -- no ``float``, no re-derivation.

    Splitting on ``", "`` is safe against flat-amount thousands
    separators: the emitter's join is comma-space while ``$2,000.00``'s
    comma is always followed by a digit.

    Args:
        raw_label: The verbatim ``PeriodInfo.raise_event`` string; may be
            empty (no raise event) or carry multiple comma-joined events.

    Returns:
        The cleaned display label, with the empty string passed through.
    """
    if not raw_label:
        return ""
    cleaned_events: list[str] = []
    for event in raw_label.split(", "):
        type_part, sep, amount_part = event.partition(" +")
        if not sep:
            type_part, sep, amount_part = event.partition(" -")
        if not sep:
            # Not an emitter shape; pass through untouched rather than
            # mangling an unrecognised string.
            cleaned_events.append(event)
            continue
        if amount_part.endswith("%"):
            number = amount_part[:-1]
            if "." in number:
                number = number.rstrip("0").rstrip(".")
            amount_part = f"{number}%"
        cleaned_events.append(f"{type_part.title()}{sep}{amount_part}")
    return ", ".join(cleaned_events)


def is_pay_cut(raw_label: str | None) -> bool:
    """Return whether a calculator ``raise_event`` string announces a pay CUT.

    Ruling **R-SAL85** ("Green up, amber down"): a recorded pay change that
    lowers base pay renders amber with a down arrow under "Pay cut:", while a
    raise or a pay increase keeps the green "Raise:" banner.  The cut is the
    one emitter shape carrying a minus (:func:`clean_raise_label` lists them),
    and :meth:`~app.services.payroll_basis.PayrollBasis.pay_event_on` emits it
    alone, never joined with a raise.

    Args:
        raw_label: The verbatim ``PeriodInfo.raise_event`` string, or
            ``None``.

    Returns:
        ``True`` for ``"PAY -$..."``; ``False`` otherwise, empty included.
    """
    return bool(raw_label) and raw_label.startswith("PAY -")


def raise_run_starts(current_raise_event: str, prev_raise_event: str | None) -> bool:
    """Return True when a period STARTS a raise run.

    The run-start seam shared by every consumer that must collapse the
    calculator's badge-every-period behavior down to one event per raise:
    the "next raise" chip and the chart's per-run labels (via the
    pairs-indexed :func:`_is_raise_run_start`) and the paycheck-anatomy
    raise banner (the cockpit route passes the focused and preceding
    periods' raw ``raise_event`` strings directly, so the banner shows on
    the paycheck where a raise takes effect rather than repeating on every
    paycheck of the raise month -- P-SA1).

    A period starts a run when its display-cleaned label
    (:func:`clean_raise_label`) is non-empty AND differs from the
    immediately-preceding period's: a label change starts a new run, a
    no-event period ends any run, and the same label recurring after a gap
    (next year's COLA) starts a fresh run.

    Args:
        current_raise_event: The period's raw ``raise_event`` string (empty
            when it carries no raise event).
        prev_raise_event: The immediately-preceding period's raw
            ``raise_event`` string, or ``None`` when there is no
            predecessor (the first period).

    Returns:
        True iff the period carries a raise event whose cleaned label
        differs from the immediately-preceding period's (or it has no
        predecessor).
    """
    label = clean_raise_label(current_raise_event)
    if not label:
        return False
    if prev_raise_event is None:
        return True
    return clean_raise_label(prev_raise_event) != label


def _is_raise_run_start(pairs: list[PeriodPair], idx: int) -> bool:
    """Return True when ``pairs[idx]`` is the FIRST period of a raise run.

    Pairs-indexed adapter over :func:`raise_run_starts`: it lifts the
    focused and preceding periods' ``raise_event`` strings out of the
    ordered ``(period, breakdown)`` pairs (the predecessor is ``None`` at
    index 0).  Used by :func:`next_raise_after` and
    :func:`build_chart_series`, which scan the full pairs list.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        idx: Index into ``pairs`` of the period under test.

    Returns:
        True iff the period at ``idx`` starts a raise run.
    """
    prev = pairs[idx - 1][1].period.raise_event if idx > 0 else None
    return raise_run_starts(pairs[idx][1].period.raise_event, prev)


def raise_run_start_period_ids(pairs: list[PeriodPair]) -> set[int]:
    """Return the ids of the periods that START a raise run.

    For a surface that renders EVERY period (the projection ledger), this
    marks only the paycheck where each raise takes effect -- its run start
    (:func:`_is_raise_run_start`) -- so a raise the calculator badges on
    every period of its month is flagged once, on the step, rather than
    repeating a badge on every paycheck of the month (P-SA1, projection
    surface).  The template checks ``period.period_id in`` this set.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.

    Returns:
        The set of ``period.period_id`` values that start a raise run (empty
        when no period carries a raise event).
    """
    return {
        pair[0].period_id
        for idx, pair in enumerate(pairs)
        if _is_raise_run_start(pairs, idx)
    }


def base_regular_net(pairs: list[PeriodPair], idx: int) -> Decimal:
    """Return the net of the nearest regular paycheck at the same base pay.

    A third-paycheck period skips the 24x deductions, so its net spikes
    above the regular per-paycheck net.  For the staircase chart line and
    the third-paycheck delta chip we need the "base" regular net at the
    same pay level: the net of the nearest NON third-paycheck period
    sharing the period-at-``idx``'s base pay per paycheck.  Prefer the
    closest earlier period; fall back to the closest later one; and, in
    the degenerate case where no regular period at that pay exists, fall
    back to the period's own net.

    **Keyed on BASE PAY since plan step salary:X-av-2** (ruling
    **R-SAL70**), where it keyed on the annual salary.  The two were one
    test while every payday divided the annual by the same count; once each
    payday divides by its own era's, one annual salary is two paychecks
    across a change of rhythm, and a third paycheck of a new biweekly era
    matched against a regular WEEKLY paycheck at the same salary would show
    the difference of the two rhythms as a third-paycheck bonus.  A raise
    still separates the levels, because it moves the base.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        idx: Index into ``pairs`` of the third-paycheck period.

    Returns:
        The base regular net pay as a :class:`~decimal.Decimal`.
    """
    target_base = pairs[idx][1].earnings.base_biweekly
    for j in range(idx - 1, -1, -1):
        breakdown = pairs[j][1]
        if (not breakdown.period.is_third_paycheck
                and breakdown.earnings.base_biweekly == target_base):
            return breakdown.earnings.net_pay
    for j in range(idx + 1, len(pairs)):
        breakdown = pairs[j][1]
        if (not breakdown.period.is_third_paycheck
                and breakdown.earnings.base_biweekly == target_base):
            return breakdown.earnings.net_pay
    return pairs[idx][1].earnings.net_pay


def next_raise_after(pairs: list[PeriodPair], today: date) -> dict[str, object] | None:
    """Return the first projected raise RUN starting strictly after ``today``.

    Scans the breakdowns (reusing the calculator's ``raise_event`` string)
    for run STARTS only (:func:`_is_raise_run_start`): the calculator
    badges every period of a raise month, so a run whose start is on or
    before ``today`` already landed and is skipped ENTIRELY even when its
    tail periods start after ``today``.  Live-data shape: with today
    07/03 and a July COLA run 07/02-07/30, the 07/16 and 07/30 tails are
    not "the next raise" -- the honest answer is the following run (the
    January Merit).

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        ``{"label": str, "period_start": date}`` for the next raise run's
        first period (the label display-cleaned via
        :func:`clean_raise_label`, e.g. ``"Merit +2.5%"``), or ``None``
        when no raise run starts after ``today``.
    """
    for idx, (period, breakdown) in enumerate(pairs):
        if not _is_raise_run_start(pairs, idx):
            continue
        if period.start_date > today:
            return {
                "label": clean_raise_label(breakdown.period.raise_event),
                "period_start": period.start_date,
            }
    return None


def next_third_after(pairs: list[PeriodPair], today: date) -> dict[str, object] | None:
    """Return the first projected third paycheck strictly after ``today``.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        ``{"period_start": date, "net": Decimal, "delta": Decimal}`` where
        ``delta`` is the third-paycheck net minus the base regular net at
        the same base pay (:func:`base_regular_net`), or ``None`` when no
        future third paycheck exists in the projection.
    """
    for idx, (period, breakdown) in enumerate(pairs):
        if period.start_date > today and breakdown.period.is_third_paycheck:
            net = breakdown.earnings.net_pay
            return {
                "period_start": period.start_date,
                "net": net,
                "delta": net - base_regular_net(pairs, idx),
            }
    return None


def yearly_net_totals(pairs: list[PeriodPair]) -> list[tuple[int, Decimal]]:
    """Return per-calendar-year net-pay totals, ordered by year.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.

    Returns:
        A list of ``(year, total_net)`` tuples sorted ascending by year.
    """
    totals: dict[int, Decimal] = {}
    for period, breakdown in pairs:
        year = period.start_date.year
        totals[year] = totals.get(year, ZERO) + breakdown.earnings.net_pay
    return [(year, totals[year]) for year in sorted(totals)]


def build_chips(
    pairs: list[PeriodPair], focused_breakdown: PaycheckBreakdown, today: date,
) -> dict[str, object]:
    """Build the hero chip-row data for the focused period.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        focused_breakdown: The breakdown for the focused period.
        today: The reference date (drives the "next" lookups).

    Returns:
        A dict with keys ``gross`` (Decimal), ``annual_salary`` (Decimal),
        ``take_home_rate_pct`` (Decimal | None), ``next_raise`` (dict |
        None: ``label`` + ``period_start``), and ``third_paycheck`` (dict |
        None: ``period_start`` + ``delta``).  Each "next" value is ``None``
        when the projection carries no such future event.
    """
    third = next_third_after(pairs, today)
    third_chip = None
    if third is not None:
        third_chip = {
            "period_start": third["period_start"],
            "delta": third["delta"],
        }
    return {
        "gross": focused_breakdown.earnings.gross_biweekly,
        "annual_salary": focused_breakdown.earnings.annual_salary,
        "take_home_rate_pct": focused_breakdown.earnings.take_home_rate_pct,
        "next_raise": next_raise_after(pairs, today),
        "third_paycheck": third_chip,
    }


def build_composition(breakdown: PaycheckBreakdown) -> dict[str, object]:
    """Build the "where this paycheck goes" composition-card data.

    All figures are Decimals; the four segment percentages (of gross) are
    precomputed to one decimal place so the template performs no math.

    **The spine is a disposition of GROSS, and the earning lines sit on
    either side of it** (plan step salary:R18-b, ruling **R-SAL38**).  A
    TAXABLE earning is inside the gross already (``gross = base + taxable
    earnings``), so the spine needs nothing new for it and the footer states
    the split.  An AFTER-TAX earning is outside the gross: the deposit is
    ``kept_from_gross + after_tax_total``, so the net SEGMENT is the part of
    gross kept (``kept_from_gross``) and the after-tax total is stated
    beside it -- otherwise the four segments would not sum to the bar, and
    the legend's percentage would disagree with its figure.  With no earning
    line every added key is ``$0.00`` / equals ``net``, and the card renders
    exactly as before.

    **The card names what priced the paycheck's taxes** (plan step
    salary:S11-c-2c, ruling **R-SAL100**, its words **R-SAL121**):
    ``tax_stub_payday`` is the payday
    of the pay stub the engine priced the four taxes from, or ``None`` when no
    stub is dated on or before the payday and the tax formulas priced them
    alone.  It is read off the breakdown's tax lines, the engine's own answer,
    so this card cannot name a stub the engine did not use.  It replaces the
    ``federal_zero_calibrated`` flag, which captioned a ``$0.00`` federal line
    under an active calibration: the basis is now stated on every paycheck,
    so a ``$0.00`` federal line carries its reason either way.

    Args:
        breakdown: The focused period's breakdown.

    Returns:
        A dict with the gross/base/taxable/net/pre_tax_total/taxes_total/
        post_tax_total/taxable_earnings_total/after_tax_total/kept_from_gross
        Decimals, the ``pct_net`` (of ``kept_from_gross``) / ``pct_pre_tax``
        / ``pct_taxes`` / ``pct_post_tax`` one-decimal percentages of gross,
        and ``tax_stub_payday`` (a :class:`~datetime.date` or ``None``).
    """
    gross = breakdown.earnings.gross_biweekly
    pre_tax_total = breakdown.deductions.total_pre_tax
    taxes_total = breakdown.taxes.total
    post_tax_total = breakdown.deductions.total_post_tax
    net = breakdown.earnings.net_pay
    after_tax_total = breakdown.earnings.total_after_tax
    kept_from_gross = net - after_tax_total
    return {
        "gross": gross,
        "base": breakdown.earnings.base_biweekly,
        "taxable_earnings_total": breakdown.earnings.total_taxable,
        "taxable": breakdown.earnings.taxable_income,
        "net": net,
        "kept_from_gross": kept_from_gross,
        "after_tax_total": after_tax_total,
        "pre_tax_total": pre_tax_total,
        "taxes_total": taxes_total,
        "post_tax_total": post_tax_total,
        "pct_net": _pct_of_gross(kept_from_gross, gross),
        "pct_pre_tax": _pct_of_gross(pre_tax_total, gross),
        "pct_taxes": _pct_of_gross(taxes_total, gross),
        "pct_post_tax": _pct_of_gross(post_tax_total, gross),
        "tax_stub_payday": breakdown.taxes.stub_payday,
    }


def build_line_rows(breakdown: PaycheckBreakdown) -> list[dict[str, object]]:
    """Build the proportional payroll-line bar-list for the focused period.

    Every line of every kind, grouped in WATERFALL order -- taxable
    earnings, the two pre-tax kinds, post-tax deductions, after-tax earnings
    (plan step salary:R18-b, ruling **R-SAL38**; ``build_deduction_rows``
    over the two deduction groups until then) -- each group sorted by amount
    DESCENDING (largest-first per group; equal amounts keep the calculator's
    stable order).  ``bar_pct`` is scaled so the largest single line across
    every group renders at 100.0 (route-computed so the template does no
    math).  **Grouped by the kind each line carries, in the enum's order,
    since plan step salary:S11-e**, which split the pre-tax kind in two
    inside the breakdown's one ``pre_tax`` list: the groups were the
    breakdown's four lists, so a kind that shares a list would have been
    shown under its neighbour's heading.  Every line of
    :attr:`~app.services.paycheck_calculator.PaycheckBreakdown.priced_lines`
    lands in exactly one group, so no kind can go unshown.

    Args:
        breakdown: The focused period's breakdown.

    Returns:
        A list of dicts, one per line item, each with ``name`` (str),
        ``amount`` (Decimal), ``kind`` (the PaycheckLineKind enum value, the
        template's CSS suffix and group key), ``group_label`` (the group's
        heading, :data:`~app.services.paycheck_line_kinds.GROUP_LABELS`) and
        ``bar_pct`` (Decimal, one decimal).  Empty when the period has no
        lines.
    """
    groups = {kind: [] for kind in PaycheckLineKindEnum}
    for line in breakdown.priced_lines:
        groups[ref_cache.paycheck_line_kind_member(line.paycheck_line_kind_id)].append(line)
    # ``sorted`` is stable, so equal amounts retain the calculator's
    # original line order within each group.
    lines = [
        (line, kind)
        for kind, group in groups.items()
        for line in sorted(group, key=lambda line: line.amount, reverse=True)
    ]
    if not lines:
        return []
    max_amount = max(line.amount for line, _kind in lines)
    rows: list[dict[str, object]] = []
    for line, kind in lines:
        if max_amount > ZERO:
            bar_pct = (line.amount / max_amount * HUNDRED).quantize(
                _PCT_QUANTUM, rounding=ROUND_HALF_UP,
            )
        else:
            bar_pct = ZERO
        rows.append({
            "name": line.name,
            "amount": line.amount,
            "kind": kind.value,
            "group_label": paycheck_line_kinds.GROUP_LABELS[kind],
            "bar_pct": bar_pct,
        })
    return rows


def _window_with_index(
    pairs: list[PeriodPair], today: date, lookback: int,
) -> list[tuple[int, DerivedPeriod, PaycheckBreakdown]]:
    """Return the display window as ``(global_index, period, breakdown)``.

    The window anchors on the first period whose ``end_date`` is on or
    after ``today`` (the current or next period), reaches back ``lookback``
    periods before it, and extends forward to :data:`HORIZON_MONTHS` months
    after ``today``.  Both ends clamp to the available periods.  The global
    index is preserved so :func:`base_regular_net` can search the full list
    (a window-edge third paycheck still resolves its base against periods
    outside the window).

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.
        lookback: Periods to include before the anchor (6 for the net-pay
            staircase; 0 for the forward-only salary path).

    Returns:
        The windowed ``(index, period, breakdown)`` triples, in order.
    """
    if not pairs:
        return []
    anchor = next(
        (i for i, (period, _bd) in enumerate(pairs) if period.end_date >= today),
        len(pairs) - 1,
    )
    start = max(0, anchor - lookback)
    horizon_end = _add_months(today, HORIZON_MONTHS)
    result: list[tuple[int, DerivedPeriod, PaycheckBreakdown]] = []
    for i in range(start, len(pairs)):
        period, breakdown = pairs[i]
        if period.start_date > horizon_end:
            break
        result.append((i, period, breakdown))
    return result


def build_chart_series(pairs: list[PeriodPair], today: date) -> dict[str, object]:
    """Build the net-pay staircase chart series (Decimals throughout).

    The main ``periods`` line is a clean staircase of REGULAR-paycheck net
    so raise steps read instantly: a third-paycheck period carries the BASE
    value (:func:`base_regular_net`) on the line, while its actual spiked
    net is emitted separately in ``thirds`` as a point event.  Raise
    events are emitted in ``raises`` with display-cleaned labels
    (:func:`clean_raise_label`), collapsed to one entry per RUN: the
    calculator badges every period of a raise month, so only run starts
    (:func:`_is_raise_run_start`, judged against the FULL pairs list) are
    emitted -- one label per raise instead of a stacked label per period.
    A run that started before the window paints no label inside it.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        A dict with ``periods`` (list of ``{start: date, net: Decimal}``),
        ``thirds`` (list of ``{start: date, net: Decimal}``), ``raises``
        (list of ``{start: date, label: str}``), and ``today`` (date).
    """
    windowed = _window_with_index(pairs, today, PERIODS_BEFORE_TODAY)
    periods_series: list[dict[str, object]] = []
    thirds: list[dict[str, object]] = []
    raises: list[dict[str, object]] = []
    for idx, period, breakdown in windowed:
        if breakdown.period.is_third_paycheck:
            line_net = base_regular_net(pairs, idx)
            thirds.append({"start": period.start_date, "net": breakdown.earnings.net_pay})
        else:
            line_net = breakdown.earnings.net_pay
        periods_series.append({"start": period.start_date, "net": line_net})
        if _is_raise_run_start(pairs, idx):
            raises.append({
                "start": period.start_date,
                "label": clean_raise_label(breakdown.period.raise_event),
            })
    return {
        "periods": periods_series,
        "thirds": thirds,
        "raises": raises,
        "today": today,
    }


def build_salary_path(pairs: list[PeriodPair], today: date) -> dict[str, object]:
    """Build the annual-salary staircase (forward-only) for the salary card.

    Uses the same horizon as the net-pay chart but forward-only (no
    look-back), so the sparkline reads as future salary growth.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        A dict with ``points`` (list of ``{start: date, annual: Decimal}``)
        and ``end_label`` (a formatted "$<value> <Mon Year>" string for the
        final point, empty when the window is empty).
    """
    windowed = _window_with_index(pairs, today, 0)
    points = [
        {"start": period.start_date, "annual": breakdown.earnings.annual_salary}
        for _idx, period, breakdown in windowed
    ]
    end_label = ""
    if points:
        last = points[-1]
        end_label = f"${last['annual']:,.0f} {last['start'].strftime('%b %Y')}"
    return {"points": points, "end_label": end_label}
