"""
Shekel Budget App -- Retirement Dashboard Inputs and Resolvers

The LOADER and the resolvers behind the retirement picture: what a render
reads from the database once (:func:`load_gap_inputs`), and the single
definitions of the questions that picture asks of the user's settings --
which retirement date is planned and who owns it, what safe-withdrawal rate
applies, what estimated retirement tax rate is stored, what the pension
benefit sums to, what the owner's current paycheck is, and what the
final-year take-home comes to.

**It stopped ORCHESTRATING at plan step C2-f2d-2.**  ``compute_gap_data``
lived here and was one of the two implementations of "the retirement picture
at a candidate plan"; :mod:`app.services.retirement_plan` is now the only
one, and it composes the resolvers below.  What is left here is what that
producer -- and the lever solver, and the readiness shaping -- all read
from, which is why it is one module and not three.

All functions accept plain data (the render's read pass, loaded inputs) and
return plain data.  No Flask imports.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.pension_profile import PensionProfile
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.user import UserSettings
from app.services import (
    paycheck_calculator,
    pension_calculator,
)
from app.services.pay_calendar import PayCadence, PayCalendar
from app.services.payroll_basis import PayrollBasis
from app.services.salary_raises import RaiseTerms
from app.utils.dates import add_months
from app.utils.money import round_money


# Default safe-withdrawal-rate percentage when the user has no
# ``UserSettings`` row or has not customised ``safe_withdrawal_rate``.
# 4% is the Trinity Study baseline (Cooley, Hubbard, Walz, 1998) and
# the standard default for FIRE-style retirement planners.  Stored as
# a percentage Decimal (not the fractional decimal that the database
# column carries) because this constant is fed directly into the
# dashboard slider, whose ``min``/``max`` are expressed in percent.
_DEFAULT_SWR_PCT = Decimal("4.00")

# Percentage scaler.  ``safe_withdrawal_rate`` is stored as a fractional
# decimal (4% as ``Decimal("0.0400")``) while the default above is stated in
# percent, and the engine states a paycheck's take-home rate in percent
# (``Earnings.take_home_rate_pct``) while the gap comparison scales by the
# fraction.  Named so the conversion direction is explicit at both sites.
_PCT_SCALE = Decimal("100")


# ── Result and context bundles ───────────────────────────────────


@dataclass(frozen=True)
class PensionSummary:
    """Aggregated pension-benefit outputs for the gap analysis.

    Returned by :func:`compute_pension_summary` so the picture producer
    carries the pension-derived values it forwards downstream as one
    immutable result rather than parallel locals: the summed monthly
    pension income (the gap calculator's pension input) and the
    per-pension derivation entries (the P3c page's footer; the pre-P3b "last
    benefit only" field this superseded is gone -- audit finding D6).  *It
    carried the last qualifying pension's salary-by-year series too, for the
    gap-comparison projection to reuse, until plan step salary:X-av-3a read
    that projection's final-year pay off the pay list's walk directly: the
    series was the LAST pension's profile's while the gap scaled the FIRST
    active profile's take-home rate, two profiles on one figure whenever an
    owner held more than one.*

    Attributes:
        monthly_income: The summed monthly benefit across all qualifying
            pensions (``Decimal("0")`` when none qualify).
        per_pension: One dict per qualifying pension (``name``,
            ``benefit_multiplier``, ``consecutive_high_years``,
            ``benefit``), in iteration order.  Retains the benefits the
            loop already computed so the page renders the derivation line
            PER PENSION (audit finding D6: the old "last benefit only"
            card silently disagreed with the summed gap row the moment a
            second pension existed).
    """

    monthly_income: Decimal
    per_pension: list = field(default_factory=list)


@dataclass(frozen=True)
class RetirementRows:
    """The owner's retirement rows: the settings row, the active pensions and
    the active salary profiles.

    What the ``/retirement`` page's assumptions rail renders from and what
    the gap analysis loads first (:class:`GapInputs` extends this with the pay
    cadence).  ONE loader, :func:`load_retirement_rows`, so the rail a refusal
    re-renders and the picture the page derives read the same three rows the
    same way (plan step salary:S3-f-4).

    Attributes:
        settings: The user's :class:`UserSettings`, or ``None``.
        pensions: The user's active :class:`PensionProfile` rows.
        salary_profiles: The user's active :class:`SalaryProfile` rows.
    """

    settings: UserSettings | None
    pensions: list[PensionProfile]
    salary_profiles: list[SalaryProfile]


@dataclass(frozen=True)
class GapInputs(RetirementRows):
    """The once-per-request loaded inputs the gap analysis reads.

    Returned by :func:`load_gap_inputs` and carried on
    :class:`~app.services.retirement_plan.RetirementInputs`, so every producer
    on the page shares one loader for the date-independent inputs -- the
    retire-later solver's binary-search probes load these ONCE and re-derive
    only the date-dependent parts (salary path, pension benefit, growth
    horizon) per probe.

    **It carried the owner's current paycheck until plan step salary:S3-f-2a,
    and that field went for two reasons at once.**  It was priced at load by a
    direct engine call that passed no calibration, so the verdict's income
    target and the payroll feed beside it priced ONE payday two ways (ruling
    **R-SAL21** as amended; the figures are at
    :func:`compute_current_paycheck`).  And it was a snapshot where the value
    is a function of the PLAN POINT: a what-if over a raise's end year (plan
    step S3-f-2b) can move this year's paycheck, so the picture derives it per
    point from the pass's pricer rather than reading one loaded here.  The
    ``_CurrentPay`` record that held it -- a net figure beside the breakdown
    that figure was copied from -- went with it; the engine's own
    :class:`~app.services.paycheck_calculator.PaycheckBreakdown` is the value.

    Attributes:
        settings: The user's :class:`UserSettings`, or ``None``
            (:class:`RetirementRows`).
        pensions: The user's active :class:`PensionProfile` rows
            (:class:`RetirementRows`).
        salary_profiles: The user's active :class:`SalaryProfile` rows
            (:class:`RetirementRows`).
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), loaded here at
            plan step R7a-2a so the gap analysis and every lever probe measure
            pre-retirement income against the same rhythm.  It is
            date-independent like everything else on this bundle, which is why
            a probe at month offset ``m`` never reloads it.
    """

    pay_cadence: PayCadence


#: What a plan point believes a profile's raises to be: the profile's rows'
#: terms with each recurring raise's end year read off the point.  The
#: :class:`~app.services.retirement_plan.PlanPoint` is the one producer of it
#: (its ``terms_for``); every salary-path reader below TAKES one rather than
#: reading ``profile.raises``, so a probe over one raise's end year reaches the
#: pension, the income target, the current paycheck and the payroll feeds
#: through one input (plan step salary:S3-f-2b, rulings **R-SAL20**, **R-SAL21**).
TermsFor = Callable[[SalaryProfile], tuple[RaiseTerms, ...]]


@dataclass(frozen=True)
class BelievedPayroll:
    """The owner's payroll as ONE plan point believes it.

    The parameter object :func:`compute_gap_net_biweekly` takes for what the
    point resolved (plan step salary:S3-f-2b): the raise set each profile is
    believed under, and the current paycheck priced under that set.  It exists
    because that producer was already at five arguments when the believed set
    became its sixth input, and a sixth positional argument -- or a disable --
    is not the remedy; the two travel together because they are one fact
    stated twice over, the set and a paycheck priced from it, and a caller
    holding one without the other could price the target's rate under one
    belief and its salary path under another.

    Built once per point by
    :func:`~app.services.retirement_plan._derive_picture`.  The pension summary
    takes :attr:`terms_for` alone, because that is all it reads.

    Attributes:
        terms_for: ``profile -> tuple[RaiseTerms, ...]``, the point's believed
            set for any profile (see :data:`TermsFor`).
        current_paycheck: The owner's current paycheck off the pass's pricer
            under that set (:func:`compute_current_paycheck`), or ``None``
            when they have no active profile or no current period.
    """

    terms_for: TermsFor
    current_paycheck: paycheck_calculator.PaycheckBreakdown | None


def load_retirement_rows(balance_ctx) -> RetirementRows:
    """Load the owner's retirement rows -- the settings, pensions and profiles.

    The one spelling of the three queries.  :func:`load_gap_inputs` reads
    them through here and adds the pay cadence; the ``/retirement`` routes
    read them through here for the assumptions rail's own renders -- a Save's
    answer and the readiness what-if's refusal -- which derive no picture and
    so need no bundle (plan step salary:S3-f-4).  The owner comes off the READ
    PASS the route built, as for every producer below a route.

    Args:
        balance_ctx: The request's
            :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`RetirementRows`.
    """
    user_id = balance_ctx.user_id
    settings = (
        db.session.query(UserSettings).filter_by(user_id=user_id).first()
    )
    pensions = (
        db.session.query(PensionProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    salary_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )
    return RetirementRows(
        settings=settings, pensions=pensions, salary_profiles=salary_profiles,
    )


def load_gap_inputs(balance_ctx):
    """Load the gap analysis's per-request inputs in one place.

    **The owner comes off the READ PASS** (plan step C2-f2d-1): the caller
    holds one for the whole render, and taking a bare ``user_id`` beside it is
    what let this producer and the lever solver each open a pass of their own.

    **It runs ONCE per render, since plan step C2-f2d-2**, because its one
    caller is :func:`app.services.retirement_plan.load_retirement_inputs` and a
    route calls that.  It ran twice until then -- the gap producer's copy and
    the lever solver's -- which is 46 of the 179 queries a ``/retirement``
    render issued on a production clone, including a whole paycheck-engine run
    (ledger row **P57**).

    Args:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext` -- the owner, the
            baseline scenario and the day, pinned once by the route.

    Returns:
        A :class:`GapInputs` bundle (settings, active pensions, active
        salary profiles, and the owner's pay cadence).

    Raises:
        PayCalendarError: The owner has no resolvable pay cadence -- no
            ``budget.pay_schedule`` row, which since plan step C4-b-2 IMPLIES no
            pay periods (``fk_pay_periods_schedule``).
            The gap's pre-retirement income is their paycheck converted to a
            month, so there is no honest figure without it.  **Raised where the
            calendar is BUILT since plan step pay_calendar:C4-d** (ruling
            R-PC45) -- ``balance_ctx.calendar()`` reaches
            :func:`app.services.pay_calendar.calendar_for`, which refuses that
            owner -- rather than where the cadence is read;
            :attr:`app.services.pay_calendar.PayCalendar.cadence` is total now.
    """
    rows = load_retirement_rows(balance_ctx)
    return GapInputs(
        settings=rows.settings,
        pensions=rows.pensions,
        salary_profiles=rows.salary_profiles,
        # Resolved once here (plan step R7a-2a): the retire-later solver
        # probes this bundle dozens of times per request and the cadence does
        # not move with a candidate retirement date.
        #
        # **Off the pass's own calendar rather than through ``cadence_for``**
        # (plan step R-F16).  The pass memoizes ONE calendar for the whole
        # render and every producer on this page reads it, so a second door
        # here was one extra ``budget.pay_schedule`` query for a value the
        # pass derives once whoever asks first -- which is the rule
        # :attr:`app.services.pay_calendar.PayCalendar.cadence` states in as
        # many words ("a caller that ALREADY holds a calendar must use this").
        # Both doors refuse the same owner, so the ``Raises`` above is
        # unchanged.
        pay_cadence=balance_ctx.calendar().cadence,
    )


def resolve_swr_fraction(settings):
    """Resolve the active safe-withdrawal rate as a fractional Decimal.

    **The ONE definition**, and since plan step C2-f2d-2 it also has ONE
    caller: :func:`app.services.retirement_plan._derive_picture` resolves the
    rate for the picture, and every consumer reads it back off that picture's
    own analysis rather than resolving a second one.  Two call sites is how
    the CRIT-04 / F-042 / PA-04 / PA-05 phantom-income defect happened -- they
    resolved the same column under two different rules (truthiness
    ``or "0.04"`` vs. ``is None``), so an explicit ``Decimal("0.0000")``
    safe-withdrawal rate rendered as 0.00% on the slider but drove the
    projection at 4%: a phantom $4,000/mo of retirement income on a $1.2M
    balance the slider says is zero.  E-12 / coding-standard "do not rely on
    truthiness for business logic": a stored zero rate is a real zero; only
    ``settings is None`` or ``safe_withdrawal_rate is None`` means "unset, use
    the default."

    Args:
        settings: the user's :class:`~app.models.user.UserSettings`
            row, or ``None`` when the user has not yet created one.

    Returns:
        The fractional-decimal SWR (the form
        :func:`app.services.retirement_gap_calculator.calculate_gap`
        expects: ``0.04`` for the 4% rule, not ``4.0``).  Falls back
        to ``_DEFAULT_SWR_PCT / _PCT_SCALE`` when ``settings`` is
        ``None`` or the stored column is ``None``; an explicit zero
        stored value is preserved as :class:`~decimal.Decimal` zero.
    """
    if settings is None or settings.safe_withdrawal_rate is None:
        return _DEFAULT_SWR_PCT / _PCT_SCALE
    return Decimal(str(settings.safe_withdrawal_rate))


# ── The picture's per-point derivations ──────────────────────────


def compute_pension_summary(
    pensions: list[PensionProfile],
    as_of: date,
    terms_for: TermsFor,
    calendar: PayCalendar,
    month_offset: int = 0,
) -> PensionSummary:
    """Aggregate the pension benefit across the user's active pensions.

    Iterates the active pensions, projecting each one that carries both a
    planned retirement date and a linked salary profile, and sums their
    monthly benefit, retaining each qualifying pension's derivation.

    Args:
        pensions: The user's active :class:`PensionProfile` rows.
        as_of: The read pass's pinned day, whose YEAR opens the salary path.
            It was ``date.today()`` here until pay-calendar plan step C2-f2e
            (ledger row **P55**): one of the last three producers on
            ``/retirement`` to resolve the clock for itself, on a render that
            already held a pass with a pinned day.  The three are asked once
            per plan point and the retire-later lever probes about ten, so one
            render read the clock about thirteen times -- and the reads are
            ``.year``, so they diverge across a NEW YEAR: the verdict card
            projecting its salary path from year N while the lever card beside
            it projects from N+1, which is the two-cards-two-clocks shape plan
            step C2-f2d-1 measured at ``$4.18`` for the read pass itself.
        terms_for: The raise set each pension's profile is believed under at
            this plan point (plan step salary:S3-f-2b) -- the point's own
            :meth:`~app.services.retirement_plan.PlanPoint.terms_for`.  The
            salary path read the profile's ROWS here until then, so a probe
            over one raise's end year could reach the income target and the
            payroll feed but not the pension that the same salary funds -- two
            beliefs on one verdict, the shape ruling **R-SAL20** rejected.  A
            pension linked to a profile the rail does not list (an archived
            one) is projected from that profile's stored rows, which is what
            the point's fallback answers for it.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`
            (the read pass's): the rhythms each profile's pay list is walked
            at, since plan step salary:X-av-3a projects the salary path
            through the engine's walk (ruling **R-SAL59**), its pay rate
            (:meth:`~app.services.payroll_basis.PayrollBasis.pay_rate_on`)
            since task plan#3100.
        month_offset: Whole months added to EACH qualifying pension's
            planned retirement date before projecting (the P2b retire-later
            probes: a later retirement extends the salary path, the years
            of service, and the high-salary window together).  ``0`` (the
            default) evaluates the stored plan unchanged --
            :func:`app.utils.dates.add_months` with 0 months is the
            identity.

    Returns:
        A :class:`PensionSummary` bundling the summed monthly pension
        income (``Decimal("0")`` when no pension qualifies) and the
        per-pension derivation entries.
    """
    monthly_income = Decimal("0")
    per_pension = []
    for pension in pensions:
        if pension.planned_retirement_date and pension.salary_profile:
            profile = pension.salary_profile
            planned = add_months(
                pension.planned_retirement_date, month_offset,
            )
            salary_by_year = pension_calculator.project_profile_salaries(
                PayrollBasis(profile, calendar, terms_for(profile)),
                as_of.year,
                planned.year,
            )
            benefit = pension_calculator.calculate_benefit(
                benefit_multiplier=pension.benefit_multiplier,
                consecutive_high_years=pension.consecutive_high_years,
                hire_date=pension.hire_date,
                planned_retirement_date=planned,
                salary_by_year=salary_by_year,
            )
            monthly_income += benefit.monthly_benefit
            # Retain every pension's derivation inputs (D6): the loop
            # already computed the benefit; keeping only the last one is
            # what made the old details card lie for multi-pension users.
            per_pension.append({
                "name": pension.name,
                "benefit_multiplier": pension.benefit_multiplier,
                "consecutive_high_years": pension.consecutive_high_years,
                "benefit": benefit,
            })
    return PensionSummary(monthly_income, per_pension)


def compute_current_paycheck(
    balance_ctx, salary_profiles: list[SalaryProfile], terms_for: TermsFor,
) -> paycheck_calculator.PaycheckBreakdown | None:
    """The owner's current paycheck, priced by the PASS's pricer.

    **ONE producer of a paycheck on ``/retirement``, since plan step
    salary:S3-f-2a** (ruling **R-SAL21** as amended 2026-09-12).  This was
    ``_compute_current_pay``: a direct
    :func:`~app.services.paycheck_calculator.calculate_paycheck` call that
    loaded the tax configs itself and passed NO ``calibration=``, while the
    payroll feed beside it priced the same payday through
    :meth:`~app.services.balance_at.BalanceContext.paychecks` WITH the
    profile's calibration -- the pricer the payroll feeds, the grid's salary
    rows and the salary pages' projections read.  (``/savings``' own
    current-paycheck door priced the way this one did until plan step
    salary:C12-b took it, ledger row **P62**'s last site.)  Measured on the developer's data
    on 2026-09-12: one 2026-09-10 paycheck at net ``$2,541.49`` by this door
    and ``$2,572.78`` by the pricer (taxes ``$355.14`` against ``$323.85``,
    the gross ``$3,631.74`` identical), and through the income target that is
    required savings ``$1,120,707.00 -> $1,162,269.00``.  Ledger row **P62**
    had recorded the two as agreeing; that was never measured across the
    calibration.  Reading the pass's pricer here is also what prices the
    current payday ONCE per render where it was priced twice, and for an owner
    whose profile funds an account it builds no second
    :class:`~app.services.income_service.ProfilePaychecks` at all -- the
    feed loader already built that profile's, and the pricer's memo answers.

    **Derived per PLAN POINT rather than loaded once**, which is the other
    half of that ruling and why this is a resolver
    :func:`~app.services.retirement_plan._derive_picture` calls rather than a
    field of :class:`GapInputs`.  A what-if over a raise's end year (plan step
    S3-f-2b, which is where *terms_for* arrived) can move THIS YEAR's paycheck
    when the probed year precedes it, so the picture at a point prices the
    current paycheck under that point's raise set or the income target and the
    salary path it scales would disagree.  At the stored set every point
    derives the same paycheck, the pricer is the one the feed loader built,
    and its per-payday memo makes the repeats free; a probed set is a pricer
    of its own, memoized under the set, so ten probes at one belief share one.

    **WHICH period is current comes off the read pass** (plan step C2-f2d-1,
    corrected by its adversarial code review): the pass's ONE memoized
    calendar and its pinned ``as_of``, so the verdict, the levers and every
    probe resolve it from one clock rather than each reading
    ``date.today()``.  A period is not cosmetic -- it selects the displayed
    balance and the contribution basis.

    Args:
        balance_ctx: The render's read pass.  Its calendar decides which
            period is current, its ``as_of`` is the day it is decided for,
            and its pricer prices it.
        salary_profiles: The owner's active salary profiles; the FIRST is the
            page's current profile, the same rule
            :func:`compute_gap_net_biweekly` projects the salary path from.
        terms_for: The raise set that profile is believed under at this plan
            point (see :data:`TermsFor`).

    Returns:
        The current period's
        :class:`~app.services.paycheck_calculator.PaycheckBreakdown`, or
        ``None`` when the owner has no active salary profile or no saved
        period covers the pass's day.
    """
    if not salary_profiles:
        return None
    current_period = balance_ctx.calendar().period_containing(
        balance_ctx.as_of,
    )
    if current_period is None:
        return None
    profile = salary_profiles[0]
    return balance_ctx.paychecks().for_profile(
        profile, terms_for(profile),
    ).at(current_period)


def recurring_raises(salary_profiles: list[SalaryProfile]) -> list[SalaryRaise]:
    """Every RECURRING raise on the owner's active profiles, in rail order.

    **The one membership walk** for the set the ``/retirement`` rail states,
    the plan point believes and a probe may name (plan step salary:S3-f-2b).
    Recurring only, and that is the whole rule: an end year answers how long
    a FORECAST is believed, and a one-time raise is a recorded fact that
    happens once, which is why
    ``ck_salary_raises_terminal_year_only_on_a_recurring_raise`` forbids the
    column a value there at all (developer ruling 2026-09-05, plan step
    salary:S3-b).  It was spelled inline in
    :func:`resolve_recurring_raise_assumptions` alone until the point needed
    the same set; two inline spellings of one membership are rule 14's shape.

    Args:
        salary_profiles: The owner's active
            :class:`~app.models.salary_profile.SalaryProfile` rows.

    Returns:
        The recurring :class:`~app.models.salary_raise.SalaryRaise` rows, in
        profile then row order (the relationship's ``effective_date``
        ordering).
    """
    return [
        raise_obj
        for profile in salary_profiles
        for raise_obj in profile.raises
        if raise_obj.is_recurring
    ]


def resolve_recurring_raise_assumptions(
    salary_profiles: list[SalaryProfile],
) -> list[dict]:
    """The end-year assumption every recurring raise contributes to the page.

    The assumptions rail's counterpart to
    :func:`resolve_retirement_date_provenance`, and it exists for the same
    reason: the rail has TWO render sites -- the dashboard's include and
    ``retirement.update_settings``'s re-render after a save -- so a list
    assembled in the template would be assembled twice, in Jinja, which is
    where this project does not compute.

    **Recurring raises only**, which is :func:`recurring_raises`'s membership
    rule and is stated there; a one-time raise has no assumption to state and
    contributes no row.  **Each row is a PROBE since plan step salary:S3-f-2b**:
    the rail renders the salary form's own end-year pair (a mode and a year,
    ruling **R-SAL13**) pre-filled from the row, and every readiness refresh
    carries them, so the start's year here is the floor the year input
    states and the ``raise_id`` is what names the probe's parameters.

    Args:
        salary_profiles: The owner's active
            :class:`~app.models.salary_profile.SalaryProfile` rows.

    **It lists every ACTIVE profile's recurring raises, which is not the
    same set as the raises the page PROJECTS**, and an adversarial review of
    plan step salary:S3-c is why that is said here rather than implied.  The
    projections read ``salary_profiles[0]``, each pension's own
    ``salary_profile`` and the profiles that FUND an account; an owner with
    two active profiles therefore sees rows for raises that feed no figure,
    and a probe on one of those moves nothing.  Listing what the owner has
    RECORDED is the honest claim and the useful one -- the rail links each
    row to the page that edits it -- but it is a weaker claim than "what this
    page projects from", and only the first is true.

    Returns:
        One dict per recurring raise, in profile then row order, carrying
        ``raise_id``, ``profile_id``, ``profile_name``, ``raise_type`` (the
        type's DISPLAY name, off the row's own property), ``percentage``,
        ``flat_amount``, ``effective_date`` (the day it starts) and
        ``terminal_year`` (the last year it is believed, ``None`` for
        indefinitely).
    """
    return [
        {
            "raise_id": raise_obj.id,
            "profile_id": raise_obj.salary_profile.id,
            "profile_name": raise_obj.salary_profile.name,
            # The row's ONE spelling of its type's name (plan step
            # salary:S3-f-1 made it a property over the FK through the ref
            # cache; this site kept reading the relationship until S3-f-2b).
            "raise_type": raise_obj.raise_type_name,
            "percentage": raise_obj.percentage,
            "flat_amount": raise_obj.flat_amount,
            "effective_date": raise_obj.effective_date,
            "terminal_year": raise_obj.terminal_year,
        }
        for raise_obj in recurring_raises(salary_profiles)
    ]


def resolve_retirement_date_provenance(
    pensions: list[PensionProfile], settings: UserSettings | None,
) -> dict:
    """Resolve the planned retirement date WITH its provenance.

    The single owner of the precedence rule (a pension's planned date
    beats the settings date; the latest pension wins), returned with the
    facts the assumptions rail needs to render the date row honestly
    (acceptance-drive fix 1): when a pension owns the date, a settings
    save cannot change the resolved horizon, so the row must show the
    resolved date read-only with a link to the owning pension instead of
    an input whose Save silently loses.

    Args:
        pensions: The user's active pensions.
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        dict with ``date`` (the resolved date, or ``None``), ``source``
        (``"pension"`` / ``"settings"`` / ``"none"`` -- producer-computed
        state strings, compared literally like the lever states), and
        ``pension_id`` / ``pension_name`` (the MAX-date owning pension
        when ``source == "pension"``, else ``None``).
    """
    dated_pensions = [
        p for p in pensions if p.planned_retirement_date is not None
    ]
    if dated_pensions:
        owner = max(dated_pensions, key=lambda p: p.planned_retirement_date)
        return {
            "date": owner.planned_retirement_date,
            "source": "pension",
            "pension_id": owner.id,
            "pension_name": owner.name,
        }
    if settings is not None and settings.planned_retirement_date is not None:
        return {
            "date": settings.planned_retirement_date,
            "source": "settings",
            "pension_id": None,
            "pension_name": None,
        }
    return {
        "date": None,
        "source": "none",
        "pension_id": None,
        "pension_name": None,
    }


def resolve_planned_retirement_date(
    pensions: list[PensionProfile], settings: UserSettings | None,
) -> date | None:
    """Derive the planned retirement date from pensions, else settings.

    Prefers the latest planned retirement date across the user's
    pensions; falls back to the retirement date stored on the user's
    settings.  Delegates to
    :func:`resolve_retirement_date_provenance` so the precedence rule
    has exactly one definition.

    Args:
        pensions: The user's active pensions.
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        The resolved planned retirement date, or ``None`` when neither a
        pension nor the settings supply one.
    """
    return resolve_retirement_date_provenance(pensions, settings)["date"]


@dataclass(frozen=True)
class GapPaycheck:
    """The net paycheck the retirement gap compares against, and the rhythm it is paid at.

    Plan step **salary:X-av-2** (ruling **R-SAL70**): the gap calculator turns
    this paycheck into a month, and the count it converts with must be the
    one the paycheck is paid at.  :func:`compute_gap_net_biweekly` answers
    two different paychecks -- the projected final-year one, divided by the
    gap inputs' cadence (the LATEST era's, which governs the years ahead),
    or the CURRENT one standing in when that projection cannot be made,
    which the engine priced at the era in force on today's payday.  It
    returned the net alone, and the caller converted both at the latest
    era's count: right for the first, and wrong for the second whenever a
    later rhythm is on record -- a monthly ``$4,017.50`` net read as
    ``$8,704.58`` a month under a later biweekly era (made-up figures).

    Attributes:
        net: The net pay for one paycheck.
        cadence: The rhythm that paycheck is paid at.
    """

    net: Decimal
    cadence: PayCadence


def compute_gap_net_biweekly(
    gap: GapInputs,
    payroll: BelievedPayroll,
    planned_retirement_date: date | None,
    as_of: date,
    calendar: PayCalendar,
) -> GapPaycheck:
    """Project the final-year net biweekly pay for the gap comparison.

    Scales the projected final-year gross biweekly (from the raise-aware
    salary projection) by the current effective take-home rate
    (net / gross), so the gap calculator compares retirement income
    against a raise-adjusted pre-retirement take-home figure rather than
    today's pay.  Returns the current net biweekly unchanged when there
    is no salary profile, no horizon, no positive current pay, or no
    projectable salary series -- and ``Decimal("0")`` when there is no
    current paycheck at all.

    **The take-home rate is the ENGINE's own** (plan step salary:S3-f-2a):
    :attr:`~app.services.paycheck_calculator.Earnings.take_home_rate_pct`,
    scaled from the percent the salary breakdown page states to the fraction
    this scales by.  This function divided net by gross itself until then --
    a second spelling of one ratio, whose zero-gross guard was a second copy
    of the one that property already carries as its ``None``.  Scaling a
    ``Decimal`` by 100 and back is exact, so the figure is unchanged.

    Args:
        gap: The render's :class:`GapInputs`, read for the owner's active
            salary profiles (the first is the profile whose final-year pay is
            projected) and the pay cadence of the zero-paycheck answer.
        payroll: What the plan point believes (:class:`BelievedPayroll`):
            the owner's current paycheck off the pass's pricer
            (:func:`compute_current_paycheck`; ``None`` when they have no
            active profile or no current period), and the raise set the
            salary path is projected under when this has to open one.  **A
            per-point ARGUMENT rather than a field of *gap* since plan step
            salary:S3-f-2a**, because it is derived per plan point where the
            bundle is loaded once per render (see :class:`GapInputs`); the
            believed set joined it at S3-f-2b.
        planned_retirement_date: The projection horizon, or ``None``.
        as_of: The read pass's pinned day, whose YEAR opens the salary path.
            It was ``date.today()`` here until pay-calendar plan step C2-f2e
            (ledger row **P55**): one of the last three producers on
            ``/retirement`` to resolve the clock for itself, on a render that
            already held a pass with a pinned day.  The three are asked once
            per plan point and the retire-later lever probes about ten, so one
            render read the clock about thirteen times -- and the reads are
            ``.year``, so they diverge across a NEW YEAR: the verdict card
            projecting its salary path from year N while the lever card beside
            it projects from N+1, which is the two-cards-two-clocks shape plan
            step C2-f2d-1 measured at ``$4.18`` for the read pass itself.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`
            (the read pass's), whose rhythms the final year's pay is walked
            at (plan step salary:X-av-3a).

    Returns:
        The :class:`GapPaycheck`: the projected final-year net at the rhythm
        its pay is walked at; the current net at the rhythm it was priced at
        (:attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`) when the
        projection cannot be performed; ``Decimal("0")`` at the gap inputs'
        cadence when there is no current paycheck, which is ``$0.00`` a month
        at any count.
    """
    if payroll.current_paycheck is None:
        return GapPaycheck(net=Decimal("0"), cadence=gap.pay_cadence)
    earnings = payroll.current_paycheck.earnings
    net_biweekly = earnings.net_pay
    # The current paycheck, when it has to stand in for the final year's, is
    # converted at the rhythm the engine PRICED it at (ruling R-SAL70).
    current = GapPaycheck(
        net=net_biweekly, cadence=payroll.current_paycheck.period.cadence,
    )
    # ``None`` when the engine's gross is not positive -- the one zero-gross
    # guard, stated where the ratio is defined rather than repeated here.
    take_home_rate_pct = earnings.take_home_rate_pct
    if not (
        gap.salary_profiles
        and planned_retirement_date
        and net_biweekly > 0
        and take_home_rate_pct is not None
    ):
        return current

    if planned_retirement_date.year < as_of.year:
        return current

    profile = gap.salary_profiles[0]
    # F-20 / MED-06 / F-032: the rate's denominator is the same per-period
    # gross the engine reports (the pre-Commit-17 ``annual_salary /
    # pay_periods`` recompute silently dropped any applicable SalaryRaise).
    effective_take_home_rate = take_home_rate_pct / _PCT_SCALE
    # The final year's pay RATE off the ONE walk the paychecks and the
    # pension's salary path read (plan step salary:X-av-3a; a rate rather
    # than a paycheck's base since task plan#3100, ruling plan#3089), on the
    # December 1 the pension evaluates each year at.  It was the pension
    # path's final yearly salary divided back into a paycheck (plan step
    # balance:X-aw's ``gross_per_paycheck``): a division of a product the
    # walk had already made, now read at the rate itself.
    final = PayrollBasis(
        profile, calendar, payroll.terms_for(profile),
    ).pay_rate_on(date(planned_retirement_date.year, 12, 1))
    return GapPaycheck(
        net=round_money(final.per_paycheck * effective_take_home_rate),
        cadence=final.cadence,
    )


def resolve_estimated_tax_rate(
    settings: UserSettings | None,
) -> Decimal | None:
    """Resolve the estimated retirement tax rate from user settings.

    Zero is a real value (E-12): an explicitly saved 0% rate returns
    ``Decimal("0")`` -- the user has SET their estimate -- and only NULL
    (settings absent or the column unset) returns ``None``, which is what
    drives the F1 ``tax_rate_missing`` flag.  This closes the display
    half of the LOW-05 / CRIT-04 carry-open (L1): pre-fix the truthiness
    here made a saved 0% render "Not set -- 0% assumed" forever in the
    three places F1 surfaced the flag.  Whether a bracket-based fallback
    should ever be built for the unset case remains the carried product
    question and is unaffected.

    Args:
        settings: The user's :class:`UserSettings`, or ``None``.

    Returns:
        The stored estimated retirement tax rate as a Decimal (an
        explicit zero preserved), or ``None`` only when settings are
        absent or the column is NULL.
    """
    if (settings is not None
            and settings.estimated_retirement_tax_rate is not None):
        return Decimal(str(settings.estimated_retirement_tax_rate))
    return None
