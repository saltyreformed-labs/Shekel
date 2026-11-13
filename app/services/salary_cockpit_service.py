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
``banner`` (a :class:`~app.services.pay_banner.PayBanner`) and
``is_third_paycheck`` flag the paycheck calculator already computed onto each
breakdown's :class:`~app.services.paycheck_calculator.PeriodInfo`.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services import paycheck_line_kinds
from app.services.pay_calendar import DerivedPeriod
from app.services.paycheck_calculator import PaycheckBreakdown
from app.services.tax_config_service import TaxLawBasis
from app.utils.money import HUNDRED, ZERO, round_money

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


def _pay_rate(breakdown: PaycheckBreakdown) -> Decimal:
    """Return the paycheck's pay RATE per paycheck: the pay in force on its last paid day.

    The rate's yearly figure over the paychecks a year it is paid at
    (:attr:`~app.services.paycheck_calculator.Earnings.annual_salary` and
    :attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`, both the
    engine's :class:`~app.services.pay_rate.BasePay` rate).  The engine's
    yearly figure is the per-paycheck rate times the count, exactly, so the
    cent this rounds to is that rate.

    Args:
        breakdown: One paycheck's breakdown.

    Returns:
        The per-paycheck rate, to the cent.
    """
    return round_money(
        breakdown.period.cadence.annual_to_per_paycheck(breakdown.earnings.annual_salary),
    )


def base_regular_net(pairs: list[PeriodPair], idx: int) -> Decimal:
    """Return the net of the nearest regular paycheck at the same pay rate.

    A third-paycheck period skips the 24x deductions, so its net spikes
    above the regular per-paycheck net.  For the staircase chart line and
    the third-paycheck delta chip we need the "base" regular net at the
    same pay level: the net of the nearest NON third-paycheck period
    whose base is the period-at-``idx``'s pay rate per paycheck.  Prefer the
    closest earlier period; fall back to the closest later one; and, in
    the degenerate case where no regular period at that pay exists, fall
    back to the period's own net.

    **The third paycheck is keyed on its PAY RATE since task plan#3123**,
    the pay in force on the last day it pays for at its own rhythm
    (:func:`_pay_rate`).  It was keyed on its BASE until then, which is the
    rate for every paycheck paid wholly at one pay, so those match as they
    did.  A paycheck
    whose pay period straddles a raise is paid a blend no other paycheck is
    (ruling **plan#3089**), so as a third paycheck it found no regular one
    and fell back to its own net: a delta of $0.00 and a spike on the
    regular line.  Made-up: biweekly Thursdays, a gap of six, $2,000.00 and
    5% from Wednesday 2027-07-14; 07-29, July's third payday, pays
    07-10..07-23 for 2,000.00 x 2/10 + 2,100.00 x 8/10 = 2,080.00.  With a
    $300.00 line it skips and no tax, it now compares with a regular
    paycheck at 2,100.00, netting 1,800.00: +$280.00, what it nets above a
    regular paycheck at the pay in force.  **A regular paycheck matches only
    where its BASE is that rate**, what it pays rather than its own rate, so
    a straddling one, whose rate can be the new pay while its base is still
    the old (a raise landing on its period's last weekend, task plan#3122),
    is passed over.

    **Per paycheck at the paycheck's rhythm, never the yearly figure alone**
    (ruling **R-SAL70**, plan step salary:X-av-2): across a change of rhythm
    one yearly salary is two paychecks, and a third paycheck of a new
    biweekly era matched against a regular WEEKLY paycheck at the same salary
    would show the difference of the two rhythms as a third-paycheck bonus.
    A raise still separates the levels, because it moves the rate.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        idx: Index into ``pairs`` of the third-paycheck period.

    Returns:
        The base regular net pay as a :class:`~decimal.Decimal`.
    """
    target_rate = _pay_rate(pairs[idx][1])
    for j in range(idx - 1, -1, -1):
        breakdown = pairs[j][1]
        if (not breakdown.period.is_third_paycheck
                and breakdown.earnings.base_biweekly == target_rate):
            return breakdown.earnings.net_pay
    for j in range(idx + 1, len(pairs)):
        breakdown = pairs[j][1]
        if (not breakdown.period.is_third_paycheck
                and breakdown.earnings.base_biweekly == target_rate):
            return breakdown.earnings.net_pay
    return pairs[idx][1].earnings.net_pay


def next_raise_after(pairs: list[PeriodPair], today: date) -> dict[str, object] | None:
    """Return the first paycheck after ``today`` that carries a banner.

    Reads the calculator's banner as it is: the engine badges a raise on the
    one paycheck it first reaches (ruling **plan#3092**,
    :meth:`~app.services.payroll_basis.PayrollBasis.banner_on`), so the next
    banner is the next change to the pay.  **The chip names it in its own
    direction** (ruling **R-SAL85**, task plan#3168): a banner holding a cut
    is "Next pay cut", amber with a down arrow, and any raise beside it keeps
    its green; the chip read none of it before that task, so a recorded cut
    showed as a green "Next raise".  Until task
    plan#3111 this kept only the first of a run of equal labels, because the
    engine badged every paycheck of a raise's month; that collapse was a
    second spelling of "the first paycheck", and it hid a paycheck whose
    label matched the one before it.  With a July 1 raise and biweekly
    Thursday paydays, the answer on 07-03 is now 07-16, the paycheck the
    raise first reaches; while the engine badged 07-02 too, that run had
    started by 07-03, so the answer skipped to the raise after it.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.
        today: The reference date.

    Returns:
        ``{"banner": PayBanner, "period_start": date}`` for that paycheck, or
        ``None`` when no paycheck after ``today`` carries one.
    """
    for period, breakdown in pairs:
        if period.start_date > today and breakdown.period.banner.events:
            return {
                "banner": breakdown.period.banner,
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
        the same pay rate (:func:`base_regular_net`), or ``None`` when no
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


def substituted_tax_rules(pairs: list[PeriodPair]) -> list[TaxLawBasis]:
    """Return each basis on which some paycheck was priced on another year's rules.

    The projection ledger prints one line per tax year whose paychecks were
    priced on rules other than their own (plan step salary:X-at-6, ruling
    **R-SAL75**; finding **N-235**).  Each basis is read off a paycheck's own
    tax lines (``taxes.rules``), the engine's answer, so the ledger never
    works out a year itself.  One pricer resolves each tax year once, so every
    paycheck of a year carries the same basis and a year is one line.

    Args:
        pairs: The full ordered ``(period, breakdown)`` list.

    Returns:
        The distinct substituted bases, ascending by ``tax_year``, so the
        template groups nothing.  Empty when every paycheck was priced on its
        own year's rules.
    """
    bases = dict.fromkeys(
        breakdown.taxes.rules
        for _period, breakdown in pairs
        if breakdown.taxes.rules.substituted
    )
    return sorted(bases, key=lambda basis: basis.tax_year)


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
        None: ``banner`` + ``period_start``), and ``third_paycheck`` (dict |
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

    **It names which year's tax rules priced them** (plan step salary:X-at-6,
    ruling **R-SAL75**; finding **N-235**): ``tax_rules`` is the tax lines'
    own ``rules``, the basis of the law the engine priced them on, so the
    cockpit and the card print the year off the paycheck rather than working
    it out, and the card steps with the period like the stub's payday does.

    Args:
        breakdown: The focused period's breakdown.

    Returns:
        A dict with the gross/base/taxable/net/pre_tax_total/taxes_total/
        post_tax_total/taxable_earnings_total/after_tax_total/kept_from_gross
        Decimals, the ``pct_net`` (of ``kept_from_gross``) / ``pct_pre_tax``
        / ``pct_taxes`` / ``pct_post_tax`` one-decimal percentages of gross,
        ``tax_stub_payday`` (a :class:`~datetime.date` or ``None``) and
        ``tax_rules`` (a :class:`~app.services.tax_config_service.TaxLawBasis`).
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
        "tax_rules": breakdown.taxes.rules,
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
    events are emitted in ``raises`` with each banner's words
    (:attr:`~app.services.pay_banner.PayBanner.text`), one for each
    windowed paycheck the calculator badged: it badges a raise on the
    paycheck the raise first reaches and nowhere else (ruling
    **plan#3092**), so one label per raise is the engine's answer and not
    this function's.  A mark is the words alone, a cut's carrying its minus,
    so the chart reads no direction of its own.

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
        if breakdown.period.banner.events:
            raises.append({
                "start": period.start_date,
                "label": breakdown.period.banner.text,
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
