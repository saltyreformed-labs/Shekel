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
from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache, tax_law
from app.enums import TaxTypeEnum
from app.exceptions import UnsupportedStateError
from app.tax_law import ChildDeductionTier, FederalRules, FicaRules

logger = logging.getLogger(__name__)


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
        bracket_sets: ``{tax_year: FederalRules}`` for the profile's filing
            status.
        state_configs: ``{tax_year: StateTaxRules}`` for the profile's state
            and filing status.
        fica_configs: ``{tax_year: FicaRules}``.  FICA carries no filing status
            and no state.
    """

    bracket_sets: "dict[int, FederalRules]"
    state_configs: "dict[int, StateTaxRules]"
    fica_configs: "dict[int, FicaRules]"


@dataclass(frozen=True)
class TaxLawBasis:
    """Which year of the law priced each part of one tax year (ruling **R-SAL75**).

    The resolver's own answer, kept rather than thrown away: each part is
    resolved on its own series (:class:`ProfileTaxSeries`), so each carries
    its own year.  It is DERIVED on every resolution and stored nowhere, so it
    cannot go stale when a release adds a year.  Ruling **R-SAL76** keeps a
    missing year on the nearest year's rules unchanged; this is the label that
    makes that substitution visible (plan steps salary:X-at-5, the Taxes tab,
    and X-at-6, the salary pages).

    A part's year is ``None`` only when the law resolves no rules for it at
    all, which is the same condition its rules are ``None`` under
    (:func:`_configs_from_series`).

    Attributes:
        tax_year: The year whose rules were asked for.
        federal_year: The year whose federal income-tax rules applied.
        state_year: The year whose state rules applied.
        fica_year: The year whose Social Security and Medicare rules applied.
    """

    tax_year: int
    federal_year: int | None
    state_year: int | None
    fica_year: int | None

    @property
    def substituted(self) -> bool:
        """Whether any part was priced on rules other than its own year's.

        Returns:
            ``True`` when a part's year is not :attr:`tax_year`, a part with
            no rules at all included.
        """
        return any(
            year != self.tax_year
            for year in (self.federal_year, self.state_year, self.fica_year)
        )

    @property
    def rules_year(self) -> int | None:
        """The one year whose rules priced every part, or ``None`` when they differ.

        A screen says "2028 on 2027's tax rules" when every part shares one
        year, and names each part's year when they do not -- a year may ship
        before one of its states (ruling **R-SAL86**).

        Returns:
            That year: :attr:`tax_year` itself when nothing was substituted.
        """
        years = {self.federal_year, self.state_year, self.fica_year}
        return years.pop() if len(years) == 1 else None

    @property
    def has_some_own_rules(self) -> bool:
        """Whether some part was priced on :attr:`tax_year`'s own rules.

        A screen whose parts differ says "Shekel doesn't have all of 2027's
        tax rules" only when the app holds some of them: 2027 shipped without
        its state (ruling **R-SAL86**), so federal tax and FICA are 2027's.
        For 2028 with the same law every part is an earlier year's -- federal
        and FICA 2027's, the state 2026's -- and the app holds none of 2028's
        rules, so "all of" would claim rules it does not have.

        Returns:
            ``True`` when a part's year is :attr:`tax_year`, which includes
            every basis that substituted nothing.
        """
        return self.tax_year in (self.federal_year, self.state_year, self.fica_year)


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
    the law through here by one door, the paycheck pricer
    (:class:`~app.services.income_service.ProfilePaychecks`), whose law the
    Taxes tab's liability reads too since plan step salary:X-at-5 -- so the
    one check covers them all.

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
            year.tax_year: year.federal[status]
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


def _state_rules(state_code, state_year, status) -> StateTaxRules:
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

    The body of :func:`configs_by_year`, one year at a time, so a caller
    slices the law ONCE and every year after the first is pure computation.

    **Each kind's resolved year is handed back as the set's ``basis``**
    (:class:`TaxLawBasis`, ruling **R-SAL75**): the one record of which year's
    rules priced a figure, which a screen prints rather than working it out
    itself.  Until plan step salary:X-at-5 the years were thrown away here and
    only a DEBUG line kept them, so every surface showed a substituted year's
    figures as plain dollar amounts (finding **N-235**).

    A substitution is still logged at DEBUG rather than INFO because it is the
    STEADY STATE, not an event: every projected period beyond the newest year
    the law carries resolves this way, on every read, until that year is
    published.  The line reads the basis, so "was a part substituted" has one
    spelling.

    Args:
        series: The profile's :class:`ProfileTaxSeries`.
        tax_year: The tax year whose rules are wanted.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config`` -- each
            value the applicable rules, or ``None`` only when the law carries
            NO year of that kind for the profile: never merely because
            *tax_year* itself is not in the law, and never because a SIBLING
            kind lacks that year.  Since plan step salary:X-at-3 that is only
            the federal and state rules of a filing status the law does not
            model, as a law with no year lists no state and so refuses every
            profile (:func:`profile_tax_series`).  And ``basis``, the
            :class:`TaxLawBasis` naming the year each of the three was
            resolved on.
    """
    federal_year, bracket_set = _pick(series.bracket_sets, tax_year)
    state_year, state_config = _pick(series.state_configs, tax_year)
    fica_year, fica_config = _pick(series.fica_configs, tax_year)
    basis = TaxLawBasis(
        tax_year=tax_year,
        federal_year=federal_year,
        state_year=state_year,
        fica_year=fica_year,
    )
    if basis.substituted:
        logger.debug("Tax year %d is not wholly in the law; applying %s", tax_year, basis)
    return {
        "bracket_set": bracket_set,
        "state_config": state_config,
        "fica_config": fica_config,
        "basis": basis,
    }


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

    **It is the app's one resolving door since plan step salary:X-at-5**,
    reached through :meth:`~app.services.income_service.ProfilePaychecks.law`,
    so no two surfaces can disagree on which year's brackets and FICA wage
    base/cap apply (deep-hunt DH-#30).  A single-year door,
    ``load_tax_configs_for_year(profile, tax_year)``, sliced the law afresh
    on every call; the Taxes tab's report was its last caller in the app,
    and when the report began reading its law off the primary profile's
    pricer it left for the tests (``tests/_tax_configs.py``), a production
    function with only test callers being the shape ruling P54 names.

    Args:
        series: The profile's :class:`ProfileTaxSeries`, from
            :func:`profile_tax_series`.
        tax_years: The tax years wanted -- any iterable; duplicates collapse.

    Returns:
        dict: ``{tax_year: {bracket_set, state_config, fica_config, basis}}``,
            one entry per distinct year asked for, empty for an empty ask.
    """
    return {year: _configs_from_series(series, year) for year in set(tax_years)}
