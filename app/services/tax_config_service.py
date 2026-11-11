"""
Shekel Budget App -- Tax Config Service

Resolves which tax law prices a salary profile's paycheck in a given year,
for the paycheck calculator and the Taxes tab.  The law itself is
:mod:`app.tax_law` -- ONE copy, in the code (ruling **salary:R-SAL74**, plan
step **salary:X-at-1**); this module reads it and decides which of its years
applies.  Until X-at-1 it read a copy of the law stored per user in five
``salary`` tables, which signup and every deploy copied in and the Settings
page could overwrite; plan step salary:X-at-2 dropped them.

**Which year's law applies to a given year is ONE rule and it lives here**
(:func:`resolve_tax_year`).  The app carries the years that have been
published -- 2025 and 2026 at X-at-1 -- while pay periods run ~2 years ahead,
so most projected periods ask for a year the law does not have yet.  Answering
that with "no law" is not an option: the paycheck engine reads a missing
``fica_config`` as zero Social Security
(:func:`~app.services.tax_calculator.capped_social_security`, which documents
that arm for bootstrap), so an unresolved year silently inflates net pay by the
whole SS line.

The rule this module used to apply was "fall back to the CURRENT CALENDAR YEAR",
and it had a cliff the day the current year is itself unconfigured -- which was
every New Year, for every user, because configuration was seeded per year and
nothing seeded the next one.  Measured on a clone of production 2026-08-11: on
2027-01-01, with no write and no user action, 40 of 51 live-priced salary rows
change and the projected income over the horizon rises by **$8,460.50** (period
22 goes from ``NET 2,639.30`` with ``ss 205.19`` to ``NET 2,844.49`` with
``ss 0.00``).  Counting the 11 periods the grid's own rolling top-up creates
that same day it is **$10,914.93** over 51 of 62 rows.  The replacement rule
reads no clock at all, so no date can move a resolved figure.

**The read was not the whole exposure, which is why the direction of the fix
matters.**  Two writers would have made those figures permanent: a settle ran
a cache reconciler in ``transaction_service`` BEFORE the status flip and
wrote the live figure into ``estimated_amount``, after which the row left the
read-time repair's Projected-only candidate set and nothing could repair it;
and any salary, pay stub or tax-config save runs
``salary_regeneration.regenerate_salary_transactions`` (in the salary routes
package until plan step salary:S3-f-3), which rebuilds every
row from today forward.  A read-time defect with two write-back doors is a
storage defect on a delay.

*Both write-back doors are gone as of plan step **balance:X-au-d**: a salary
row DECLARES the definition that prices it and stores no figure, so the settle
has no cache to reconcile and a regeneration writes no amount.  The paragraph
stays because it is the ARGUMENT for the direction of this module's fix, not a
description of live code.*
"""

import logging
from dataclasses import dataclass, replace
from decimal import Decimal

from app import ref_cache, tax_law
from app.enums import FilingStatusEnum, TaxTypeEnum
from app.exceptions import UnsupportedStateError
from app.tax_law import Bracket, ChildDeductionTier, FicaRules

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FederalTaxRules:
    """One tax year's federal income-tax law as it applies to ONE filing status.

    The law states the standard deduction and the bracket ladder per filing
    status (:class:`app.tax_law.FederalRules`) and the per-child credits once
    for every filer (:class:`app.tax_law.FederalCredits`, ruling plan#3340); a
    profile files under one status, so this is that status's rules with the
    year's credits beside them -- the tax RETURN's figures
    :func:`app.services.tax_calculator.calculate_annual_federal_liability`
    reads, and through :attr:`withholding_rules` what
    :func:`~app.services.tax_calculator.calculate_federal_withholding` reads.
    :func:`_federal_rules` builds it, as :func:`_state_rules` builds
    :class:`StateTaxRules`.

    Attributes:
        standard_deduction: The status's standard deduction.
        brackets: The status's bracket ladder.
        child_credit_amount: The year's child tax credit per qualifying child.
        other_dependent_credit_amount: The year's credit per other dependent.
        child_credit_refundable_cap: The year's refundable Additional Child
            Tax Credit cap per child.
        withholding_standard_deduction: Pub 15-T's standard deduction for the
            status where it differs from the return's, else ``None``.
        withholding_child_credit_amount: Form W-4's per-child credit for the
            year where it differs from the return's, else ``None``.
    """

    standard_deduction: Decimal
    brackets: tuple[Bracket, ...]
    child_credit_amount: Decimal
    other_dependent_credit_amount: Decimal
    child_credit_refundable_cap: Decimal
    withholding_standard_deduction: Decimal | None = None
    withholding_child_credit_amount: Decimal | None = None

    @property
    def withholding_rules(self) -> "FederalTaxRules":
        """Return the rules federal WITHHOLDING prices on (ruling plan#3213).

        The ONE producer of them:
        :func:`app.services.tax_calculator.calculate_federal_withholding`
        reads it off the rules it is handed, so no caller can withhold on the
        return's figures where the year states others.  These rules
        themselves when the year states no withholding figure; otherwise a
        copy with each stated figure in place of the return's, stating none
        itself (it is already what withholding prices on).

        Returns:
            The :class:`FederalTaxRules` the withholding formula prices on.
        """
        if (
            self.withholding_standard_deduction is None
            and self.withholding_child_credit_amount is None
        ):
            return self
        return replace(
            self,
            standard_deduction=(
                self.standard_deduction
                if self.withholding_standard_deduction is None
                else self.withholding_standard_deduction
            ),
            child_credit_amount=(
                self.child_credit_amount
                if self.withholding_child_credit_amount is None
                else self.withholding_child_credit_amount
            ),
            withholding_standard_deduction=None,
            withholding_child_credit_amount=None,
        )


@dataclass(frozen=True)
class StateTaxRules:
    """One state's law for one tax year as it applies to ONE filing status.

    The law states a state's rate once per year and its deductions per filing
    status (:class:`app.tax_law.StateYearLaw`); a profile files under one
    status, so this is that status's slice with the rate beside it -- the
    attributes :func:`app.services.tax_calculator.calculate_state_tax` reads,
    plus the child-deduction tiers the annual liability resolves.

    ``tax_type`` is the law's own :class:`~app.enums.TaxTypeEnum` member, which
    the calculator compares (ruling plan#3211).  Until plan step salary:X-at-2
    it was the member's ``ref.tax_types`` id, because a stored row named its
    type by a key onto that table; the law is code, so the table was a second
    spelling of the enum, and X-at-2 deleted it.

    Attributes:
        state_code: The two-letter state.
        tax_type: The state's tax type, as the law states it.
        flat_rate: The state's flat rate for the year, or ``None``.
        standard_deduction: This filing status's state standard deduction.
        child_deduction_tiers: This filing status's per-child deduction tiers,
            lowest AGI first; empty for a state with none.
    """

    state_code: str
    tax_type: TaxTypeEnum
    flat_rate: Decimal | None
    standard_deduction: Decimal
    child_deduction_tiers: tuple[ChildDeductionTier, ...]


@dataclass(frozen=True)
class ProfileTaxSeries:
    """Every year of the law a profile can resolve against, by kind and year.

    **Three INDEPENDENT year series, and their independence is the whole point.**
    An earlier draft of this module resolved ONE year for the profile from the
    UNION of the three kinds and then loaded all three under it.  That is wrong
    in a way that moves money, because the loader needs each kind to have its
    own entry for that year: a year present in only one kind became the
    resolved year for itself AND for every later year, and the other two lines
    silently became zero across the whole horizon.  Measured on a clone of
    production 2026-08-11, when the law was stored per user and the Settings
    page could write a single kind for any year: saving one 2027 state-tax row
    made 2028 resolve to 2027, dropping the bracket set and FICA to ``None``
    and Social Security to ``$0.00`` -- **+$216.63 a period**.

    The law now carries federal rules and FICA for every year it carries
    (:class:`app.tax_law.TaxYearLaw` refuses a year without them), so that
    state can no longer arise between those two kinds.  A STATE still can: a
    new year may ship before one of its states does (ruling **R-SAL86**), so
    the state's series lacks that year.  Resolving each kind against its own
    series confines that gap to the state line -- the state resolves on the
    latest earlier year that lists it -- instead of letting it move the
    federal and FICA lines too.  (A state first listed AFTER the law's first
    year, whose earlier years would reach FORWARD to it, cannot be written
    since plan step salary:X-at-3: :class:`app.tax_law.TaxLaw` refuses it,
    ruling **R-SAL129**.)

    Attributes:
        bracket_sets: ``{tax_year: FederalTaxRules}`` for the profile's filing
            status.
        state_configs: ``{tax_year: StateTaxRules}`` for the profile's state
            and filing status.
        fica_configs: ``{tax_year: FicaRules}``.  FICA carries no filing status
            and no state.
    """

    bracket_sets: "dict[int, FederalTaxRules]"
    state_configs: "dict[int, StateTaxRules]"
    fica_configs: "dict[int, FicaRules]"


def profile_tax_series(profile) -> ProfileTaxSeries:
    """Return the law's three series for *profile*: every year, sliced to its status and state.

    The candidate sets :func:`resolve_tax_year` picks from, and the reason that
    rule needs no clock: they are the years the law carries.  Reads
    :data:`app.tax_law.LAW` and issues no query.

    **A state the law does not list is REFUSED** (plan step salary:X-at-3:
    "the engine refuses a state the law lacks"; ruling **R-SAL78**).  Until
    then it resolved no state rules, which priced zero state tax with no word
    -- or, where a pay stub priced the paycheck, carried the stub's printed
    state tax unmoved by any pay change, since the formulas priced both sides
    at zero (finding **SAL-575**, re-measured at X-at-3).  Both profile doors
    refuse such a state, so only a row older than a release that dropped one
    reaches this; the refusal (ruling **R-SAL130**) reaches the owner as one
    page naming the profile and linking its edit page
    (:mod:`app.error_handlers`).  Every pricing surface reads
    the law through here -- the paycheck pricer
    (:class:`~app.services.income_service.ProfilePaychecks`) and the Taxes
    tab's :func:`load_tax_configs_for_year` -- so the one check covers them all.

    **A filing status the law does not model resolves no federal or state
    rules**, which is what the per-user copy answered for a status it held no
    row for.  A paycheck prices missing federal rules as zero federal
    withholding (``paycheck_calculator/_withholding.py``) where the annual
    liability refuses them
    (:func:`~app.services.tax_calculator.calculate_annual_federal_liability`
    raises ``InvalidFilingStatusError``).  Every member of
    :class:`~app.enums.FilingStatusEnum` is modelled in every year the law
    carries (:class:`app.tax_law.TaxYearLaw` refuses a year without one), so
    only a ``ref.filing_statuses`` row with no member reaches that arm.

    Args:
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code``, and its ``id`` and ``name`` for a refusal.

    Returns:
        The profile's :class:`ProfileTaxSeries`.

    Raises:
        UnsupportedStateError: The law does not list the profile's state.
    """
    status = ref_cache.filing_status_member(profile.filing_status_id)
    law = tax_law.LAW
    if not law.supports(profile.state_code):
        raise UnsupportedStateError(
            profile.id, profile.name, profile.state_code, law.supported_states,
        )
    return ProfileTaxSeries(
        bracket_sets={
            year.tax_year: _federal_rules(year, status)
            for year in law.years
            if status is not None
        },
        # The state's series is the law's ONE spelling of it, which the
        # tax-law alarms read too (plan step salary:X-at-4).
        state_configs={
            year.tax_year: _state_rules(
                profile.state_code, year.states[profile.state_code], status,
            )
            for year in law.years_listing(profile.state_code)
            if status is not None
        },
        fica_configs={year.tax_year: year.fica for year in law.years},
    )


def _federal_rules(
    year_law: tax_law.TaxYearLaw, status: FilingStatusEnum,
) -> FederalTaxRules:
    """Slice one year of the law's federal rules to one filing status.

    The status's standard deduction and ladder, with the year's per-child
    credits beside them (ruling plan#3340): the ONE place the two meet, so
    every surface pricing a profile reads the same credits for every status.

    Args:
        year_law: The :class:`app.tax_law.TaxYearLaw`.
        status: The profile's :class:`~app.enums.FilingStatusEnum` member.

    Returns:
        The status's :class:`FederalTaxRules`.
    """
    rules = year_law.federal[status]
    per_child = year_law.federal_credits
    return FederalTaxRules(
        standard_deduction=rules.standard_deduction,
        brackets=rules.brackets,
        child_credit_amount=per_child.child_credit_amount,
        other_dependent_credit_amount=per_child.other_dependent_credit_amount,
        child_credit_refundable_cap=per_child.child_credit_refundable_cap,
        withholding_standard_deduction=rules.withholding_standard_deduction,
        withholding_child_credit_amount=per_child.withholding_child_credit_amount,
    )


def _state_rules(
    state_code: str, state_year: tax_law.StateYearLaw, status: FilingStatusEnum,
) -> StateTaxRules:
    """Slice one state-year of the law to one filing status.

    Args:
        state_code: The two-letter state the law keys *state_year* under.
        state_year: The :class:`app.tax_law.StateYearLaw`.
        status: The profile's :class:`~app.enums.FilingStatusEnum` member.

    Returns:
        The status's :class:`StateTaxRules`.
    """
    return StateTaxRules(
        state_code=state_code,
        tax_type=state_year.tax_type,
        flat_rate=state_year.flat_rate,
        standard_deduction=state_year.standard_deduction[status],
        child_deduction_tiers=state_year.child_deduction_tiers[status],
    )


def resolve_tax_year(tax_year: int, configured: tuple[int, ...]) -> int | None:
    """Return which configured year's rules apply to ``tax_year``, or None.

    **The ONE substitution rule, and it reads no clock.**  The latest configured
    year at or before ``tax_year``; failing that -- ``tax_year`` predates every
    configured year -- the earliest configured year; failing that, ``None``,
    because nothing is configured and there is nothing to substitute.

    Latest-at-or-before is the rule because tax rules take effect and persist:
    the newest published brackets are the best available answer for a year not
    yet published, which is precisely the projected-period case.  Reaching
    FORWARD for a year that predates everything is the weaker arm and is stated
    as such -- it is an approximation for a historical year the user never
    configured, not a claim about that year's law.

    It is a pure function over ONE kind's candidate set, applied three times by
    :func:`_configs_from_series` rather than once over a shared set.  Purity is
    what lets the rule be tested without a database; per-kind application is
    what stops one table's row deciding another table's line (see
    :class:`ProfileTaxSeries`).

    **What it replaced, and why the replacement is a rule rather than a wider
    fallback.**  The previous rule substituted the CURRENT CALENDAR YEAR, which
    cannot answer for the year it is itself: on 2027-01-01 a 2027 period stopped
    falling back to 2026 and resolved to no configuration at all.  Widening that
    fallback -- "try the current year, then the one before" -- would move the
    cliff rather than remove it.  Deriving the answer from the configured set
    removes the class: every year resolves to a configured year whenever one
    exists, on every date.

    Args:
        tax_year: The tax year whose rules are wanted.
        configured: One kind's configured years, in any order.

    Returns:
        The configured year whose rules apply, or ``None`` when *configured*
        is empty.
    """
    at_or_before = [year for year in configured if year <= tax_year]
    if at_or_before:
        return max(at_or_before)
    return min(configured) if configured else None


def _pick(series: dict, tax_year: int):
    """Return the entry from ONE kind's *series* whose rules apply to ``tax_year``.

    Args:
        series: That kind's ``{tax_year: rules}`` mapping.
        tax_year: The tax year whose rules are wanted.

    Returns:
        ``(resolved_year, rules)``, or ``(None, None)`` when the kind has no
        entries at all.
    """
    resolved = resolve_tax_year(tax_year, tuple(series))
    return (None, None) if resolved is None else (resolved, series[resolved])


def _configs_from_series(series: ProfileTaxSeries, tax_year: int) -> dict:
    """Resolve each kind in *series* independently for ``tax_year``.

    The shared body of :func:`load_tax_configs_for_year` and
    :func:`configs_by_year`, so the multi-year caller slices the law ONCE and
    every year after the first is pure computation.

    A substitution is logged at DEBUG rather than INFO because it is the
    STEADY STATE, not an event: every projected period beyond the newest year
    the law carries resolves this way, on every read, until that year is
    published.  What is not yet recorded anywhere a user can see is that a
    figure was computed against another year's rules -- an approximation the
    surfaces present as a plain dollar amount (ruling **R-SAL75**, plan steps
    salary:X-at-5 and X-at-6).

    Args:
        series: The profile's :class:`ProfileTaxSeries`.
        tax_year: The tax year whose rules are wanted.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``; each
            value is the applicable rules, or ``None`` when that kind has no
            entries.
    """
    picked = {
        "bracket_set": _pick(series.bracket_sets, tax_year),
        "state_config": _pick(series.state_configs, tax_year),
        "fica_config": _pick(series.fica_configs, tax_year),
    }
    substituted = {
        kind: resolved
        for kind, (resolved, _rules) in picked.items()
        if resolved is not None and resolved != tax_year
    }
    if substituted:
        logger.debug(
            "Tax year %d is not in the law for %s; applying %s",
            tax_year, sorted(substituted), substituted,
        )
    return {kind: rules for kind, (_resolved, rules) in picked.items()}


def load_tax_configs_for_year(profile, tax_year):
    """Return the tax law that APPLIES to ``tax_year`` for *profile*.

    The resolving loader every single-year consumer wants: each kind's own
    series decides which of ITS years applies (:func:`resolve_tax_year`).
    Every surface that resolves per-year rules -- the tax report / withholding /
    liability services and, through :func:`configs_by_year`, the salary
    projection, breakdown and dashboard paths -- goes through here, so two
    surfaces cannot disagree on which year's brackets and FICA wage base/cap
    apply (deep-hunt DH-#30).

    Args:
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code``.
        tax_year (int): The tax year whose rules are wanted.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``.  A value
            is ``None`` only when the law carries NO year for that kind and
            profile -- never merely because *tax_year* itself is not in the law,
            and never because a SIBLING kind lacks that year.  Since plan step
            salary:X-at-3 that is only the federal and state rules of a filing
            status the law does not model: a law with no year lists no state,
            so it refuses every profile below.

    Raises:
        UnsupportedStateError: The law does not list the profile's state
            (:func:`profile_tax_series`).
    """
    return _configs_from_series(profile_tax_series(profile), tax_year)


def configs_by_year(series: ProfileTaxSeries, tax_years) -> dict:
    """Resolve a sliced series for each of *tax_years*.

    The MULTI-YEAR resolving door, so a projection spanning more than one tax
    year applies each period's OWN year's rules.  Its answer is a function of
    the law alone: asking for 2027 beside 2026 gives the same answer as asking
    for it alone, on every date, because :func:`resolve_tax_year` consults no
    clock.

    **It takes the SERIES rather than a profile and a period list, and plan
    step salary:S3-d is why.**
    :class:`~app.services.income_service.ProfilePaychecks` prices a payday
    when it is asked for one, so it slices the law once
    (:func:`profile_tax_series`) and resolves each payday's year against that
    -- when the series was a stored copy, a self-loading door would have cost
    three queries per payday.

    **A caller holding periods rather than years** writes
    ``configs_by_year(series, {p.start_date.year for p in periods})``.  Only
    ``start_date.year`` was ever read, so this never depended on the two
    derived columns pay-calendar plan step C4-c dropped.

    Args:
        series: The profile's :class:`ProfileTaxSeries`, from
            :func:`profile_tax_series`.
        tax_years: The tax years wanted -- any iterable; duplicates collapse.

    Returns:
        dict: ``{tax_year: {bracket_set, state_config, fica_config}}``, one
            entry per distinct year asked for, empty for an empty ask.
    """
    return {year: _configs_from_series(series, year) for year in set(tax_years)}
