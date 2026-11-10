"""
Shekel Budget App -- Analytics Taxes Report Service (T-P3)

The producer behind the analytics Taxes tab (T-P4 renders it).  It fuses
the two T-P1/T-P2 building blocks into the four surfaces the locked Taxes
anatomy shows -- the refund estimate, the hero chips, the hybrid W-2
preview, and the Schedule A check -- for one tax year:

* **Refund** = withholding-to-date TOTAL (measured checkpoint + modeled
  remainder, from :mod:`tax_withholding_service`) minus the filing-time
  liability (from :mod:`tax_liability_service`), computed SEPARATELY for
  federal and NC state.  The federal refund ADDS the refundable Additional
  Child Tax Credit (T-P5), so a household whose CTC exceeds its tax still
  shows the ACTC as a refund.  Positive = refund, negative = owed (see
  :class:`RefundEstimate`).
* **Hybrid W-2 preview** = per-filer W-2 boxes built from the hybrid gross
  and the four hybrid withholding lines, labelled "measured through <stub
  date>, modeled after" via the carried measured / modeled split.
* **Schedule A check** = itemized estimate (mortgage interest from the
  balance seam's one loan-interest producer
  :func:`app.services.balance_at.loan_interest_in_year` -- the loan's own
  fold for the settled past, its schedule for the future -- plus the hybrid
  state income tax withheld) versus the standard deduction.  Informational
  only: the v1 LIABILITY stays standard-deduction based; the itemize
  election is out of scope and disclosed.

Single-filer identity (audit ruling): every ACTIVE salary profile in the
baseline scenario belongs to ONE filer (multiple jobs, one 1040).  Wages,
pre-tax, and withholding are SUMMED across the active profiles; the filing
inputs the liability needs -- filing status, dependent counts, W-4 Step
4(a) -- are taken from the PRIMARY profile (the salary cockpit's
first-by-``(sort_order, name)`` rule).  With the developer's single active
profile this degrades to the trivially-correct one-profile case.

Liability basis (T-P1 ruling + T-P5 extension): 4(a) in, 4(b) out,
nonrefundable CTC/ODC clamp on the liability with the unused child credit
paid out as the refundable ACTC, and the NC filing-status standard deduction
plus AGI-tiered per-child deduction -- all owned by
:func:`tax_liability_service.compute_annual_liability`; this module only
feeds it the hybrid wage and the modeled pre-tax.

Pre-tax modelling (assumptions disclosure): the YTD checkpoint captures no
pre-tax figure, so the annual pre-tax that reduces the liability's taxable
base is MODELED over the FULL year (``project_salary`` pre-tax totals across
every one of the year's periods), even for the elapsed periods whose
WITHHOLDING is measured.  Disclosed via ``pretax_modeled_for_elapsed``.  The
all-payroll-taxes share of it, which leaves the W-2's Social Security and
Medicare wages as well, is read off the same breakdowns in the same pass
(plan step salary:S11-e).

No duplicated data: the liability's own outputs (standard deduction,
dependent counts + credit amounts, state rate + state standard deduction,
and the basis -- the tax year and which year's rules priced each part, plan
step salary:X-at-5) live on the carried
:class:`~app.services.tax_liability_service.AnnualLiability`;
the T-P4 assumptions card reads them from ``report.liability`` rather than
having them copied onto :class:`TaxAssumptions` (which carries only the
non-liability disclosures).

Boundary discipline: no Flask import (``today`` and the query results are
plain data).  DB reads (baseline scenario, active profiles, the year's pay
periods, and the mortgage accounts the Schedule A figure needs) live in the
service layer, mirroring the year-end summary orchestrator's precedent --
this module loads the year's periods exactly as ``_load_common_data`` does.
No tax arithmetic is re-implemented: the liability, the withholding hybrid,
the bracket ladder, the primary-profile rule, and the mortgage-interest
figure are all reused from their owning modules (the last from the balance
seam, :func:`app.services.balance_at.loan_interest_in_year`).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app import ref_cache
from app.enums import AcctTypeEnum
from app.services import (
    balance_at,
    tax_calculator,
)
from app.services.paycheck_calculator import (
    waterfall_fica_wages,
    waterfall_taxable,
)
from app.services.projection_inputs import (
    load_active_accounts_with_types,
    load_active_salary_profiles,
)
from app.services.balance_at import BalanceContext
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.tax_liability_service import (
    AnnualLiability,
    compute_annual_liability,
)
from app.services.tax_withholding_service import (
    WithholdingComponents,
    compute_withholding_to_date,
    year_paydays,
)

ZERO = Decimal("0")

# Effective-rate resolution: four decimal places (0.01%), matching the four
# places the tax law's bracket rates (and therefore the marginal-rate chip) are
# held to (``app.tax_law._types``, which refuses a finer rate), so the two rate
# chips render at one precision.
_RATE_QUANTUM = Decimal("0.0001")

# The all-zero components: the summed measured/modeled sides degrade to this
# when there are no periods and no checkpoint.
_ZERO_COMPONENTS = WithholdingComponents(ZERO, ZERO, ZERO, ZERO, ZERO)


@dataclass(frozen=True)
class WithholdingBasis:
    """What priced the taxes of ONE job's modeled paychecks (plan step salary:S11-c-2c).

    Ruling **R-SAL100**: the Taxes tab names the stubs that priced the
    year's estimated paychecks.  It is kept PER JOB because each job's
    paychecks are priced from that job's own stubs: a union across jobs
    would say "the tax formulas, then <stub>" of a filer whose second job
    the formulas priced all year (the leaf's review, MED-1).

    Attributes:
        job: The salary profile's name, which the tab shows only when more
            than one profile is active.
        stub_paydays: The paydays of the stubs that priced any of the job's
            modeled paychecks, ascending.
        formulas_priced: ``True`` when the tax formulas priced at least one
            of them alone -- no stub used for pricing dated on or before it.
            For one job those paychecks are always its earliest: every
            payday after a switched-on stub has that stub on or before it,
            so the formulas price none of them, and the tab can say "the
            tax formulas, then <stubs>".
    """

    job: str
    stub_paydays: tuple[date, ...]
    formulas_priced: bool


def _basis_of(job: str, priced_from: tuple[date | None, ...]) -> WithholdingBasis:
    """Read one job's :class:`WithholdingBasis` off what priced its modeled paychecks.

    Args:
        job: The salary profile's name.
        priced_from: :attr:`~app.services.tax_withholding_service
            .WithholdingToDate.priced_from` -- one entry per modeled
            paycheck: its pricing stub's payday, or ``None`` for the formulas.

    Returns:
        The job's basis: its distinct stub paydays ascending, and whether
        the formulas priced any paycheck alone.
    """
    return WithholdingBasis(
        job=job,
        stub_paydays=tuple(sorted({day for day in priced_from if day is not None})),
        formulas_priced=None in priced_from,
    )


@dataclass(frozen=True)
class WithholdingSummary:
    """Per-filer withholding-to-date summed across the active profiles.

    ``total`` (= ``measured + modeled`` component-wise) is what the refund
    subtracts liability from and what the W-2 boxes are built on;
    ``measured`` and ``modeled`` are kept apart so the W-2 preview labels
    its per-box mix ("measured through <stub date>, modeled after").
    ``measured_through`` is the LATEST stub date any profile is measured
    through (exact for the single-profile case; the representative date for
    the multi-profile combined preview), or ``None`` when every profile is
    fully modeled.  ``has_checkpoint`` records whether ANY profile carried
    a checkpoint (the pre-tax-modelling disclosure keys off it).
    ``bases`` is what priced each job's modeled paychecks, one
    :class:`WithholdingBasis` per active profile with any paycheck modeled,
    in the profiles' order (empty when nothing is modeled).  It is read off
    the engine's breakdowns, never off the profiles' stubs: whether a stub is
    used for pricing is the engine picker's question, so a screen naming the
    stubs that priced a paycheck cannot name one the engine passed over.
    """

    total: WithholdingComponents
    measured: WithholdingComponents
    modeled: WithholdingComponents
    measured_through: date | None
    has_checkpoint: bool
    bases: tuple[WithholdingBasis, ...]


@dataclass(frozen=True)
class ModeledPretax:
    """The year's modeled pre-tax deductions summed across the active profiles.

    Both totals are read off the same breakdowns in one pass
    (:func:`_aggregate_modeled_pretax`), so the figure the W-2's Social
    Security and Medicare wages subtract can never be priced apart from the
    one box 1 subtracts (plan step salary:S11-e).

    Attributes:
        total_pre_tax: Both pre-tax kinds -- what leaves the income-tax base:
            the liability's taxable base and W-2 box 1.
        total_pre_fica: The all-payroll-taxes kind alone, a part of
            ``total_pre_tax`` -- what leaves W-2 boxes 3 and 5 as well.
    """

    total_pre_tax: Decimal
    total_pre_fica: Decimal


@dataclass(frozen=True)
class RefundEstimate:
    """The refund producer's output: federal + state + total refund.

    State refund is ``withheld - liability``; federal refund is
    ``withheld - liability + refundable_actc`` (the refundable Additional
    Child Tax Credit, T-P5).  Positive = money back, negative = owed at
    filing.  The withheld, liability, and ACTC operands themselves live on
    the report's ``withholding`` / ``liability`` bundles, so only the three
    precomputed deltas the templates cannot compute are carried here.
    ``total_refund`` (federal + state) is the hero headline.
    """

    federal_refund: Decimal
    state_refund: Decimal
    total_refund: Decimal


@dataclass(frozen=True)
class W2Wages:
    """The four per-filer W-2 wage boxes.

    ``box1_wages`` (and ``box16_state_wages``, equal to it) is hybrid gross
    less the modeled annual pre-tax, both pre-tax kinds, floored at $0.00 by
    the engine's one rule for the income-tax wage
    (:func:`~app.services.paycheck_calculator.waterfall_taxable`; finding
    plan#2660: it was spelled here by hand without the floor);
    ``box5_medicare_wages`` is hybrid gross less the modeled annual
    all-payroll-taxes lines alone (a Section 125 premium, a flexible spending
    account), the one rule the engine charges Social Security and Medicare on
    (:func:`~app.services.paycheck_calculator.waterfall_fica_wages`); and
    ``box3_ss_wages`` caps that same figure at the year's SS wage base.  An
    income-tax-only line (a retirement deferral) leaves box 1 and stays in
    boxes 3 and 5, as on the employer's W-2.  Until plan step salary:S11-e
    boxes 3 and 5 were built on the raw gross, which counted the
    all-payroll-taxes lines the employer's W-2 leaves out.  Box 3 caps the
    COMBINED figure at one wage base -- an estimate for a multi-job filer
    whose real per-W-2 caps apply per employer.
    """

    box1_wages: Decimal
    box3_ss_wages: Decimal
    box5_medicare_wages: Decimal
    box16_state_wages: Decimal


@dataclass(frozen=True)
class W2Withheld:
    """The four per-filer W-2 withholding boxes (the hybrid lines).

    Each is the summed hybrid withholding line (measured checkpoint +
    modeled remainder) for its tax: federal, Social Security, Medicare, and
    state.
    """

    box2_federal: Decimal
    box4_ss_withheld: Decimal
    box6_medicare_withheld: Decimal
    box17_state_withheld: Decimal


@dataclass(frozen=True)
class W2Preview:
    """A per-filer, filing-time W-2 estimate (measured + modeled hybrid).

    ``wages`` / ``withheld`` carry the eight W-2 boxes; ``measured`` /
    ``modeled`` are the summed withholding-to-date sides (the five
    ``ytd``-shaped figures) so T-P4 labels the mix, and ``measured_through``
    is the stub date the "measured through <date>, modeled after" label
    reads (``None`` when fully modeled).
    """

    wages: W2Wages
    withheld: W2Withheld
    measured: WithholdingComponents
    modeled: WithholdingComponents
    measured_through: date | None


@dataclass(frozen=True)
class ItemizedComponents:
    """The itemizable Schedule A components the app can source.

    ``mortgage_interest`` is the fold-actual + schedule-projected figure from
    :func:`app.services.balance_at.loan_interest_in_year`; ``state_income_tax``
    is the hybrid state income tax withheld.  ``property_tax`` is ``None`` -- there
    is no
    unambiguous property-tax source to query (escrow line items are
    free-text named, with no ref-table kind separating a tax line from
    insurance), so it is OMITTED.
    """

    mortgage_interest: Decimal
    state_income_tax: Decimal
    property_tax: Decimal | None


@dataclass(frozen=True)
class ScheduleACheck:
    """Informational itemize-vs-standard check (the liability stays standard).

    ``itemized_estimate`` sums the sourced ``components``;
    ``margin = itemized_estimate - standard_deduction`` (a POSITIVE margin
    means itemizing could lower taxable income -- the card copy is T-P4's
    job).  ``property_tax_included`` is ``False`` (no property-tax source);
    ``salt_cap_not_applied`` is ``True`` (no SALT cap constant is seeded, so
    none is applied).  The itemized election does NOT feed the v1 liability.
    """

    components: ItemizedComponents
    itemized_estimate: Decimal
    standard_deduction: Decimal
    margin: Decimal
    property_tax_included: bool
    salt_cap_not_applied: bool


@dataclass(frozen=True)
class TaxChips:
    """The hero band's rate + timing chips.

    ``effective_rate`` is ``(federal_liability + state_liability) /
    box1_wages`` quantised to four places, or ``None`` when box 1 wages are
    non-positive (zero-safe).  ``marginal_rate`` is the bracket rate
    containing federal taxable.  ``next_stub`` is the next payday strictly
    after ``today`` within the year, or ``None``.  The federal / NC refund
    chips of the same hero band read from the report's ``refund`` bundle
    (not duplicated here).
    """

    effective_rate: Decimal | None
    marginal_rate: Decimal
    next_stub: date | None


@dataclass(frozen=True)
class FilingInputs:
    """The primary profile's filing identity (IDs for logic, names to show).

    ``filing_status_id`` drives logic; ``filing_status_name`` is the raw ref
    name for display (the T-P4 card formats it the way the settings screen
    does, ``name|replace('_', ' ')|title``).  ``state_code`` is the primary
    profile's state.
    """

    filing_status_id: int
    filing_status_name: str
    state_code: str


@dataclass(frozen=True)
class ModellingDisclosures:
    """The modelling caveats the assumptions card surfaces.

    The withholding basis -- what priced the modeled paychecks -- is not
    here: it is :attr:`WithholdingSummary.bases`, per job, read off the
    engine's breakdowns (plan step salary:S11-c-2c, ruling **R-SAL100**,
    which deleted the primary profile's calibration state this carried).
    ``checkpoint_as_of_date`` is the measured stub date (``None`` = fully
    modeled);
    ``pretax_modeled_for_elapsed`` is ``True`` when a checkpoint exists (the
    elapsed withholding is measured but its pre-tax is still modeled).
    ``actc_modeled`` is always ``True``
    (T-P5: the refundable Additional Child Tax Credit IS now modeled, so the
    liability's zero clamp no longer swallows a CTC-heavy household's refund);
    ``phase_out_not_modeled`` is always ``True`` (the CTC/ACTC MAGI phase-outs
    -- $400k MFJ / $200k other -- are not applied; the developer's AGI is far
    below them).
    """

    checkpoint_as_of_date: date | None
    pretax_modeled_for_elapsed: bool
    actc_modeled: bool
    phase_out_not_modeled: bool


@dataclass(frozen=True)
class TaxAssumptions:
    """The non-liability disclosures the T-P4 assumptions card renders.

    Carries only what is NOT already on ``report.liability`` (which owns the
    standard deduction, dependent counts + credit amounts, state rate +
    state standard deduction, and tax year the card also shows): the primary
    profile's ``filing`` identity, the ``disclosures`` caveats, and the
    multi-profile note.  ``filing_inputs_from`` names the primary profile
    ONLY when more than one profile is active (``None`` for the
    single-profile case).
    """

    filing: FilingInputs
    disclosures: ModellingDisclosures
    active_profile_count: int
    filing_inputs_from: str | None


@dataclass(frozen=True)
class TaxReport:  # pylint: disable=too-many-instance-attributes
    """The complete analytics Taxes tab dataset for one filer and year.

    One bundle per T-P4 card: ``refund`` + ``chips`` (hero band),
    ``liability`` (derivation ledger; also the SSOT for the basis -- the tax
    year and which year's rules priced it -- standard deduction, credits,
    and state rate the assumptions card shows),
    ``w2_preview`` (hybrid W-2), ``schedule_a`` (Schedule A check),
    ``assumptions`` (assumptions card).  ``withholding`` carries the shared
    measured/modeled split the hero and W-2 both read.

    ``primary_profile_id`` is the resolved primary profile's id, carried so
    the route can wire the YTD checkpoint card's form action (and its
    ``latest_checkpoint`` read) WITHOUT re-deriving the primary-profile
    rule -- this module's resolution stays the single implementation.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because
    this is the one top-level bundle the Taxes tab renders: one cohesive
    field per locked-anatomy card plus the primary-profile pointer; the
    per-card sub-bundles already group everything groupable, so the only
    "fix" would be an artificial extra nesting level no consumer reads as
    a unit.
    """

    refund: RefundEstimate
    withholding: WithholdingSummary
    liability: AnnualLiability
    w2_preview: W2Preview
    schedule_a: ScheduleACheck
    chips: TaxChips
    assumptions: TaxAssumptions
    primary_profile_id: int


def compute_tax_report(user_id: int, year: int, today: date) -> TaxReport | None:
    """Compute the analytics Taxes tab dataset for *user_id* and *year*.

    Resolves the baseline scenario and the user's ACTIVE salary profiles
    (ordered ``(sort_order, name)`` -- the first is the PRIMARY filer whose
    filing inputs drive the liability), loads the year's pay periods the way
    the year-end orchestrator does, and fuses the T-P1 liability and T-P2
    withholding-to-date into the refund, chips, W-2 preview, and Schedule A
    surfaces.

    Args:
        user_id: The owning user (scopes every query below).
        year: The tax year to report on.
        today: The display-timezone "today" the caller resolves (the
            producer stays Flask-free); drives the next-payday chip only.

    Returns:
        The populated :class:`TaxReport`, or ``None`` when the user has no
        baseline scenario or no active salary profile (T-P4 renders an
        empty state).  A user with profiles but no pay periods for the year
        degrades to an all-modeled zero report (no crash).
    """
    # The report's civil window is the DISPLAY-timezone year, so the loan
    # schedules behind Schedule A must be resolved against that same ``today``.
    # The context pins it; ``generate_debt_schedules`` used to take no date at
    # all and re-derive its own ``date.today()``, silently discarding the one
    # this producer was handed (the New-Year UTC/display seam).
    balance_ctx = BalanceContext.build(user_id, as_of=today)
    # ``scenario_id`` RAISES for a user with no baseline (plan step X-v2,
    # ruling R-BX) and one application-level handler answers that state, so the
    # ``return None`` this used to open with -- which rendered the same empty
    # state as "no salary profile", two different conditions wearing one answer
    # -- is gone.  The ``None`` below now means exactly one thing.
    profiles = load_active_salary_profiles(user_id, balance_ctx.scenario_id)
    if not profiles:
        return None

    primary = profiles[0]
    # ONE calendar derivation for the whole report: the pass memoizes it, the
    # year's periods are a filter of it, and its cadence is the paycheck count
    # every projection below divides by (plan step R-F16).
    calendar = balance_ctx.calendar()
    # **Asked only when it can be ANSWERED, and the guard that used to say so
    # is gone** (plan step balance:X-bh-1).  A conditional ``calendar.cadence
    # if calendar.cadence_days is not None`` stood here to keep
    # ``PayCalendar.cadence``'s refusal off the page; the basis carries the
    # CALENDAR and resolves the cadence on read, so there was nothing left to
    # guard.  **Since plan step pay_calendar:C4-d there is nothing left to
    # refuse either** (ruling R-PC45): the owner that property refused holds no
    # ``budget.pay_schedule`` row, and ``calendar_for`` now refuses THEM, so a
    # calendar reaching this line always carries a cadence and the read above
    # is total.
    periods = year_paydays(calendar, year)
    # The primary profile's law for the year, resolved ONCE: the liability,
    # the W-2's Social Security cap and the marginal-rate chip all price from
    # this set, so the basis the liability carries -- which year's rules
    # priced each part, the line the tab prints (ruling R-SAL75, plan step
    # salary:X-at-5) -- is the resolution behind every one of them.  Until
    # X-at-5 the liability resolved the same profile and year a second time.
    configs = load_tax_configs_for_year(primary, year)

    withholding = _aggregate_withholding(year, profiles, balance_ctx)
    pretax = _aggregate_modeled_pretax(profiles, periods, balance_ctx)

    liability = compute_annual_liability(
        primary, configs, withholding.total.gross, pretax.total_pre_tax,
    )
    box1_wages = waterfall_taxable(withholding.total.gross, pretax.total_pre_tax)
    next_stub = _next_stub(periods, today)

    return TaxReport(
        refund=_build_refund(withholding, liability),
        withholding=withholding,
        liability=liability,
        w2_preview=_build_w2_preview(
            withholding, box1_wages, pretax.total_pre_fica,
            configs.get("fica_config"),
        ),
        schedule_a=_build_schedule_a(
            user_id, year, balance_ctx, withholding, liability,
        ),
        chips=_build_chips(
            liability, box1_wages, configs.get("bracket_set"), next_stub,
        ),
        assumptions=_build_assumptions(primary, withholding, profiles),
        primary_profile_id=primary.id,
    )


# ── Data loading (year-end orchestrator precedent) ────────────────


# ── Withholding + pre-tax aggregation (single-filer sum) ──────────


def _aggregate_withholding(
    year: int, profiles: list, ctx: BalanceContext,
) -> WithholdingSummary:
    """Sum withholding-to-date across the active profiles (one filer).

    Calls :func:`tax_withholding_service.compute_withholding_to_date` per
    profile -- so the measured-checkpoint + modeled-remainder hybrid (and
    its full-year cumulative context) is owned by T-P2, not re-derived --
    and sums the ``total`` / ``measured`` / ``modeled`` sides component-wise.
    ``measured_through`` is the LATEST stub date any profile is measured
    through (``None`` when all are fully modeled), and ``bases`` what priced
    each profile's modeled paychecks (:func:`_basis_of`).

    Args:
        year: The tax year.
        profiles: The active salary profiles.
        ctx: The report's read pass, whose calendar is the owner's pay
            calendar -- the paycheck count each profile's projection divides
            its annual salary by, and the payday set its year-cumulative
            state is counted over (plan steps R-F16 and balance:X-bh-1) --
            and whose pricer prices the modeled remainder (plan step
            salary:S11-c-2c).

    Returns:
        The summed :class:`WithholdingSummary`.
    """
    totals: list[WithholdingComponents] = []
    measures: list[WithholdingComponents] = []
    models: list[WithholdingComponents] = []
    stub_dates: list[date] = []
    bases: list[WithholdingBasis] = []
    has_checkpoint = False

    for profile in profiles:
        wtd = compute_withholding_to_date(profile, year, ctx)
        totals.append(wtd.total)
        measures.append(wtd.measured)
        models.append(wtd.projected)
        if wtd.checkpoint is not None:
            has_checkpoint = True
        if wtd.measured_through is not None:
            stub_dates.append(wtd.measured_through)
        if wtd.priced_from:
            bases.append(_basis_of(profile.name, wtd.priced_from))

    return WithholdingSummary(
        total=_sum_components(totals),
        measured=_sum_components(measures),
        modeled=_sum_components(models),
        measured_through=max(stub_dates) if stub_dates else None,
        has_checkpoint=has_checkpoint,
        bases=tuple(bases),
    )


def _aggregate_modeled_pretax(
    profiles: list, periods: list, ctx: BalanceContext,
) -> ModeledPretax:
    """Sum the FULL-year modeled pre-tax across the active profiles.

    The checkpoint captures no pre-tax figure, so the annual pre-tax that
    reduces the liability's taxable base is modelled over EVERY one of the
    year's periods (not just the remainder): the read pass's pricer prices
    the full period list and each breakdown's ``deductions.total_pre_tax``
    is summed, and beside it, from the same breakdowns, its
    ``deductions.total_pre_fica``: the all-payroll-taxes lines the W-2's
    Social Security and Medicare wages leave out (plan step salary:S11-e).
    A pay stub prices only the tax lines, never the pre-tax
    deductions (plan step salary:S11-c-2c), so the stubs move nothing here.
    **The pass's pricer since that step**, where it called ``project_salary``
    with ONE year's tax configs: a stub is priced on its own payday's year
    (ruling **R-SAL77**), and the per-year resolution that answers it has one
    spelling, :class:`~app.services.income_service.ProfilePaychecks`; the
    withholding remainder asks the same pricer, so each payday is priced once
    per report.

    Args:
        profiles: The active salary profiles.
        periods: The year's pay periods.
        ctx: The report's read pass, whose pricer prices each profile's
            paychecks against the owner's pay calendar -- the paycheck count
            the projection divides each annual salary by, and the payday set
            the engine's month and year context is counted over (plan steps
            R-F16 and balance:X-bh-1).  Its cadence is resolvable whenever
            this loop runs at all: the guard above returns before it when
            there are no periods, and an owner with a period always resolves
            a cadence.

    Returns:
        The summed modeled annual :class:`ModeledPretax` (both totals
        ``ZERO`` when there are no periods).
    """
    total_pre_tax = ZERO
    total_pre_fica = ZERO
    if periods:
        for profile in profiles:
            for bd in ctx.paychecks().for_profile(profile).over(periods):
                total_pre_tax += bd.deductions.total_pre_tax
                total_pre_fica += bd.deductions.total_pre_fica
    return ModeledPretax(
        total_pre_tax=total_pre_tax, total_pre_fica=total_pre_fica,
    )


def _sum_components(
    parts: list[WithholdingComponents],
) -> WithholdingComponents:
    """Sum a list of :class:`WithholdingComponents` field-by-field.

    Args:
        parts: The per-profile components to add.

    Returns:
        The component-wise sum (:data:`_ZERO_COMPONENTS` for an empty list).
    """
    if not parts:
        return _ZERO_COMPONENTS
    return WithholdingComponents(
        gross=sum((p.gross for p in parts), ZERO),
        federal=sum((p.federal for p in parts), ZERO),
        state=sum((p.state for p in parts), ZERO),
        social_security=sum((p.social_security for p in parts), ZERO),
        medicare=sum((p.medicare for p in parts), ZERO),
    )


# ── Surface builders ──────────────────────────────────────────────


def _build_refund(
    withholding: WithholdingSummary, liability: AnnualLiability,
) -> RefundEstimate:
    """Build the federal + state refund from withheld minus liability.

    Args:
        withholding: The summed withholding-to-date.
        liability: The filing-time liability (federal + state layers).

    Returns:
        The :class:`RefundEstimate` (positive = refund, negative = owed).
    """
    # Federal refund adds the refundable ACTC: withheld minus the
    # nonrefundable liability PLUS the refundable Additional Child Tax Credit
    # (T-P5).  For a household whose CTC exceeds its tax, the ACTC IS the
    # refund (the liability floors at zero).
    federal_refund = (
        withholding.total.federal
        - liability.federal.liability
        + liability.federal.refundable_actc
    )
    state_refund = withholding.total.state - liability.state.liability
    return RefundEstimate(
        federal_refund=federal_refund,
        state_refund=state_refund,
        total_refund=federal_refund + state_refund,
    )


def _build_w2_preview(
    withholding: WithholdingSummary,
    box1_wages: Decimal,
    pre_fica: Decimal,
    fica_config,
) -> W2Preview:
    """Build the per-filer hybrid W-2 preview from the summed withholding.

    Box 5 (Medicare wages) is the hybrid gross less the modeled annual
    all-payroll-taxes lines, by the engine's one rule
    (:func:`~app.services.paycheck_calculator.waterfall_fica_wages`); box 3
    (SS wages) caps that figure at the year's ``ss_wage_base``, the lines
    leaving it before the cap is applied, and with no FICA config the cap is
    unknown and box 3 equals box 5.  Box 1 / 16 are the wage base
    (``box1_wages``); the withholding boxes are the hybrid lines.

    Args:
        withholding: The summed withholding-to-date.
        box1_wages: Hybrid gross less the modeled annual pre-tax, by
            :func:`~app.services.paycheck_calculator.waterfall_taxable`.
        pre_fica: The modeled annual all-payroll-taxes lines
            (:attr:`ModeledPretax.total_pre_fica`).
        fica_config: The year's :class:`app.tax_law.FicaRules` (or ``None``)
            for the SS cap.

    Returns:
        The populated :class:`W2Preview`.
    """
    fica_wages = waterfall_fica_wages(withholding.total.gross, pre_fica)
    if fica_config is not None:
        ss_wages = min(fica_wages, Decimal(str(fica_config.ss_wage_base)))
    else:
        ss_wages = fica_wages
    return W2Preview(
        wages=W2Wages(
            box1_wages=box1_wages,
            box3_ss_wages=ss_wages,
            box5_medicare_wages=fica_wages,
            box16_state_wages=box1_wages,
        ),
        withheld=W2Withheld(
            box2_federal=withholding.total.federal,
            box4_ss_withheld=withholding.total.social_security,
            box6_medicare_withheld=withholding.total.medicare,
            box17_state_withheld=withholding.total.state,
        ),
        measured=withholding.measured,
        modeled=withholding.modeled,
        measured_through=withholding.measured_through,
    )


def _build_schedule_a(
    user_id: int,
    year: int,
    balance_ctx: BalanceContext,
    withholding: WithholdingSummary,
    liability: AnnualLiability,
) -> ScheduleACheck:
    """Build the informational Schedule A itemize-vs-standard check.

    Mortgage interest comes from the balance seam's ONE loan-interest producer
    (:func:`app.services.balance_at.loan_interest_in_year`): it loads the user's
    MORTGAGE accounts (:func:`_load_mortgage_accounts` -- NOT every amortizing
    account; a car loan's interest is not deductible) and sums each loan's interest
    PAID in the year, folded from the loan's own events for the settled past and
    projected from its schedule for the future -- the same total producer the
    balance derives from, so the deduction and the balance can no longer disagree
    (plan step C3c, closing B-6).  The state income-tax component is the hybrid
    state withholding.  Property tax is omitted (no unambiguous source).

    Args:
        user_id: The owning user (scopes the debt-account load).
        year: The tax year (interest is summed in the year PAID).
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext` (scopes the
            fold + schedule read, and pins the display-tz ``as_of``).
        withholding: The summed withholding-to-date (state component).
        liability: The liability (its federal standard deduction).

    Returns:
        The populated :class:`ScheduleACheck`.
    """
    mortgage_accounts = _load_mortgage_accounts(user_id)
    mortgage_interest = sum(
        (
            balance_at.loan_interest_in_year(account, balance_ctx, year)
            for account in mortgage_accounts
        ),
        ZERO,
    )
    state_income_tax = withholding.total.state
    itemized_estimate = mortgage_interest + state_income_tax
    standard_deduction = liability.federal.standard_deduction
    return ScheduleACheck(
        components=ItemizedComponents(
            mortgage_interest=mortgage_interest,
            state_income_tax=state_income_tax,
            property_tax=None,
        ),
        itemized_estimate=itemized_estimate,
        standard_deduction=standard_deduction,
        margin=itemized_estimate - standard_deduction,
        property_tax_included=False,
        salt_cap_not_applied=True,
    )


def _load_mortgage_accounts(user_id: int) -> list:
    """Return the user's active MORTGAGE accounts -- Schedule A line 8's domain.

    Deliberately NOT ``_load_common_data``'s ``debt_accounts`` selection, which
    this used to mirror, and the divergence IS the fix.  The two answer different
    questions: that one asks "what do I owe on?" and rightly takes every
    amortizing account; this one asks "whose interest is deductible as home
    mortgage interest?", and only a mortgage's is.

    Mirroring the debt selection put a CAR LOAN's interest into the home-mortgage
    deduction.  ``has_amortization`` is set on ``AUTO_LOAN``, ``STUDENT_LOAN``,
    ``PERSONAL_LOAN`` and ``HELOC`` as well as ``MORTGAGE``, so every one of them
    was summed into ``schedule_a.components.mortgage_interest``.  Personal
    interest (car, personal loan) is not deductible at all, and student-loan
    interest is an above-the-line adjustment, never Schedule A -- so a user with
    a car loan had their itemize-vs-standard comparison inflated by its full
    annual interest, and could be told to itemize when the standard deduction
    wins.

    Selected by account_type ID (the project's ref-table rule: IDs for logic),
    not by a collateral link: the TYPE is the user's own declaration that the
    account is a mortgage, whereas ``collateral_account_id`` is optional and a
    real mortgage may simply not have it filled in.

    **Known omission, deliberate.**  A HELOC's interest IS deductible when the
    proceeds bought, built, or substantially improved the residence, and is not
    otherwise -- a use-of-proceeds fact the app does not record.  This Schedule A
    is an informational itemize-vs-standard check that already omits what it
    cannot source unambiguously (property tax, ``property_tax_included=False``);
    excluding the HELOC follows that stance rather than guessing at the
    taxpayer's intent.  Revisit if the app ever records use of proceeds.

    Args:
        user_id: The owning user.

    Returns:
        The active mortgage account list (possibly empty).
    """
    mortgage_type_id = ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE)
    return [
        a for a in load_active_accounts_with_types(user_id)
        if a.account_type_id == mortgage_type_id
    ]


def _build_chips(
    liability: AnnualLiability,
    box1_wages: Decimal,
    bracket_set,
    next_stub: date | None,
) -> TaxChips:
    """Build the hero-band rate + timing chips.

    ``effective_rate`` = ``(federal + state liability) / box1_wages``
    quantised to four places, ``None`` when box 1 is non-positive (zero-safe
    -- the zero-wage / zero-period degrade case).  ``marginal_rate`` is the
    bracket rate containing federal taxable (``ZERO`` when no bracket set
    resolves).

    Args:
        liability: The liability (federal taxable + both liabilities).
        box1_wages: The effective-rate denominator.
        bracket_set: The year's :class:`app.tax_law.FederalRules` (or
            ``None``).
        next_stub: The precomputed next-payday date (or ``None``).

    Returns:
        The populated :class:`TaxChips`.
    """
    combined_liability = liability.federal.liability + liability.state.liability
    if box1_wages > ZERO:
        effective_rate = (combined_liability / box1_wages).quantize(
            _RATE_QUANTUM, rounding=ROUND_HALF_UP,
        )
    else:
        effective_rate = None

    brackets = bracket_set.brackets if bracket_set is not None else []
    marginal_rate = tax_calculator.marginal_rate_for(
        liability.federal.taxable, brackets,
    )
    return TaxChips(
        effective_rate=effective_rate,
        marginal_rate=marginal_rate,
        next_stub=next_stub,
    )


def _next_stub(periods: list, today: date) -> date | None:
    """Return the earliest payday strictly after *today* among *periods*.

    Args:
        periods: The year's pay periods (each ``start_date`` is its payday).
        today: The reference date.

    Returns:
        The next payday ``date`` after ``today``, or ``None`` when none
        remains in the year.
    """
    future = [p.start_date for p in periods if p.start_date > today]
    return min(future) if future else None


def _build_assumptions(
    primary, withholding: WithholdingSummary, profiles: list,
) -> TaxAssumptions:
    """Build the structured non-liability disclosures the T-P4 card renders.

    Filing inputs come from the PRIMARY profile; when more than one profile
    is active the ``filing_inputs_from`` field names it (else ``None``).
    ``pretax_modeled_for_elapsed`` fires when any profile carries a
    checkpoint (the elapsed WITHHOLDING is measured but its pre-tax is not).

    Args:
        primary: The primary :class:`SalaryProfile` (filing inputs + state).
        withholding: The summed withholding (checkpoint presence + date).
        profiles: The active profiles (for the multi-profile disclosure).

    Returns:
        The populated :class:`TaxAssumptions`.
    """
    return TaxAssumptions(
        filing=FilingInputs(
            filing_status_id=int(primary.filing_status_id),
            filing_status_name=primary.filing_status.name,
            state_code=primary.state_code,
        ),
        disclosures=ModellingDisclosures(
            checkpoint_as_of_date=withholding.measured_through,
            pretax_modeled_for_elapsed=withholding.has_checkpoint,
            actc_modeled=True,
            phase_out_not_modeled=True,
        ),
        active_profile_count=len(profiles),
        filing_inputs_from=primary.name if len(profiles) > 1 else None,
    )
