"""
Shekel Budget App -- Paycheck engine: the four TAXES, priced from a pay stub.

**A paycheck's taxes are its pay stub's, corrected by the formulas** (plan
step **salary:S11-c-2c**; rulings **R-SAL42**, **R-SAL54**, **R-SAL55**,
**R-SAL58**, **R-SAL77**, **R-SAL100**; closes ledger row **SAL-565**).  The
latest switched-on stub dated on or before the paycheck with the SAME LINES
prices it; with none, the latest switched-on stub on or before it of ANY
lines; with none at all, the formulas alone (:func:`priced_taxes`).  Each of
the four taxes is then the stub's own figure plus what the app's formulas
(:func:`~._withholding._bracket_tax_lines`) say changed between the stub's
paycheck and this one, floored at ``$0.00`` -- so a paycheck with the stub's
lines and the stub's pay lands on the stub, and a raise, a new tax year or a
line's new amount moves it by what the formulas price that change at.

Until this step one dated stub's four EFFECTIVE RATES priced every paycheck,
past and future, whatever its lines (``calibration_service``).  A third
paycheck in a month skips the Section 125 lines, so it took a normal
paycheck's Social Security and Medicare rates -- which carry those lines'
reduction -- on a base with nothing excluded, and was projected high (ledger
row **SAL-565**, measured on a production clone; the figure stays outside the
repository).  A stub of its own lines now prices it.

**How each side is priced** (the formulas run twice, on two paychecks):

* the PAYCHECK -- its gross, its pre-tax lines, the year-to-date before its
  payday, the rhythm in force on it, and its own tax year's law;
* the STUB's paycheck -- the stub's base pay plus the earnings it records as
  taxable, less the lines it records as pre-tax, each by the kind THE STUB
  prints it under and never the line's current kind (**R-SAL58**), with the
  year-to-date and the rhythm in force on the STUB's payday and the law of
  the stub's own tax year (**R-SAL77**: a December stub prices a January
  paycheck through both years' tables, so a new year's law reaches every
  estimate).  The year-to-date is the app's own replay of the year's earlier
  paychecks, the figure the formulas price the paycheck side with.

The W-4 entries, filing status and state are TODAY's on both sides (ruling
**R-SAL101**): the app keeps no dated history of them yet, so a change made
after the latest stub cancels out until a stub paid under it is entered
(plan step ``S16`` dates them; finding **SAL-591**).  **The formulas charge
Social Security and Medicare on the gross less the "Pre-tax, all payroll
taxes" lines since plan step salary:S11-e** (ruling **plan#2639**; ledger
row **SAL-566**), on both sides: the paycheck's by the kind each LINE is
priced as, the stub's by the kind the STUB records each figure under
(R-SAL58), so a stub that records a line under the other pre-tax kind than
its paycheck line now has prices the paycheck by that difference, which
the entry door lists as a kind that differs.

**Around the wage base it errs further, and that is ledger row SAL-595**
(owner plan#2637).  The stub's side is priced at the APP's year-to-date, so
where the app and the employer disagree about whether the stub's paycheck
had reached the base -- the employer counting wages the app never saw --
the stub's whole Social Security figure, or its absence, is carried into
every paycheck it prices, the next year's included, until a stub of the new
year is entered.  The Medicare surtax threshold shares it.  It moves
``$0.00`` for pay that never reaches the base.  S11-e removed its other
cause, an all-payroll-taxes line counted toward the base.

**A worker Social Security does not cover is ledger row SAL-596** (owner
plan#2636).  The formulas always charge 6.2%, so a stub that prints
``$0.00`` Social Security prices every later paycheck at 6.2% of its pay
above the stub's (``0.00 + formulas(paycheck) - formulas(stub)``, floored),
and every paycheck before the first stub at the full 6.2%, where the deleted
calibration priced ``$0.00``.  It moves ``$0.00`` for a worker the tax covers.

**What "the same lines" means, decided here** (applications of
**balance:R-BAL207**, the developer's standing rule that behaviour he cannot
see is decided by the best from-scratch design): a stub has the paycheck's
lines when the TAXED lines it records at a NON-ZERO amount -- its taxable
earnings and pre-tax deductions, each by the kind the stub records it under
(:data:`TAXED_KINDS`) -- are exactly the ones the engine prices at a non-zero
amount that payday, and it records no one-off of those kinds carrying money
(:func:`_has_the_lines`, through ONE filter, :func:`_taxed`, on all three).
The one-off half is ruling **R-SAL123** ("Only tax-changing one-offs",
amending R-SAL42 fork 8b and R-SAL54's reading, :func:`one_offs_change_taxes`):
a one-off of a taxed kind still makes a stub one of other lines, and one that
changes no tax -- an after-tax reimbursement, a post-tax deduction -- does not,
so it no longer sends the picker to an older stub.  Only those two kinds for
the paycheck lines too (an application of R-BAL207), because the question is whether
the stub's taxes describe this paycheck and no tax formula reads a post-tax
deduction or an after-tax earning: comparing them too would pass over a
newer stub that differs only in such a line for an older one, losing what
the newer stub's taxes reflect (the leaf's review, LOW-3; narrowed by the
coordinator under R-BAL207, 2026-10-05).  Only NON-ZERO amounts, because a
line worth ``$0.00`` moves no tax on either side, and the engine prices a
line its annual cap has used up at ``$0.00`` where no stub prints it, so
comparing the recorded keys alone would pass over a correct same-lines stub
for one of other lines at exactly the paycheck a cap runs out.

**One producer of what a stub adds up to.**  :func:`stub_totals` sums a
stub's figures by the kind each records and runs the waterfall the engine's
paycheck runs (:func:`~._breakdown.waterfall_gross`,
:func:`~._breakdown.waterfall_net`); the entry door's checks
(:func:`app.services.pay_stub_service.totals_of`) read it too, so the gross
the door checked is the gross the formulas price.  It lives here because the
door's module imports the engine and the engine may not import it back.

Imports :mod:`._breakdown`, :mod:`._calendar_questions` and
:mod:`._withholding`.  :mod:`._pricing` composes it, and the package
re-exports the names a caller outside it reads (:class:`StubTotals`,
:func:`stub_totals`, :func:`tax_years_for`, :func:`one_offs_change_taxes`).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum, WithholdingKindEnum
from app.services.paycheck_line_kinds import PRE_TAX_KINDS
from app.utils.money import ZERO, round_money

from ._breakdown import (
    TaxLines,
    totals_by_kind,
    waterfall_fica_wages,
    waterfall_gross,
    waterfall_net,
    waterfall_taxable,
)
from ._calendar_questions import _get_cumulative_wages
from ._withholding import _bracket_tax_lines, _WageBasis


#: The kinds whose lines a tax formula reads -- the gross holds the taxable
#: earnings, the Social Security and Medicare wages are the gross less the
#: all-payroll-taxes deductions, and the taxable wage is the gross less both
#: pre-tax kinds -- and so the kinds "the same lines" compares (the module
#: docstring).  A post-tax deduction and an after-tax earning move no tax.
TAXED_KINDS = (PaycheckLineKindEnum.TAXABLE_EARNING, *PRE_TAX_KINDS)


@dataclass(frozen=True)
class StubTotals:
    """What a stub's figures ADD UP to -- derived, never stored.

    Attributes:
        gross: Base pay plus the taxable earnings.
        pre_tax: The pre-tax deductions, both pre-tax kinds.
        pre_fica: The "Pre-tax, all payroll taxes" deductions alone: what
            leaves the Social Security and Medicare wages (plan step
            salary:S11-e).
        taxes: The four taxes.
        post_tax: The post-tax deductions.
        after_tax: The after-tax earnings.
        net: :func:`~._breakdown.waterfall_net` of the five above, the rule a
            priced paycheck's net uses too.
    """
    gross: Decimal
    pre_tax: Decimal
    pre_fica: Decimal
    taxes: Decimal
    post_tax: Decimal
    after_tax: Decimal
    net: Decimal


def stub_totals(base_pay: Decimal, kinded: Iterable, taxes: Iterable[Decimal]) -> StubTotals:
    """Return what a stub's figures add up to, each by the kind the STUB prints it under.

    Ruling **R-SAL58**: a stub line and a one-off each carry their own kind,
    so nothing here reads a paycheck line.  The ONE producer of a stub's
    totals: the entry door checks a form's figures with it and the engine
    prices a saved stub's paycheck with it.  The figures are summed by
    :func:`~._breakdown.totals_by_kind`, the summation a priced paycheck's
    all-payroll-taxes total reads too (plan step salary:S11-e), so the two
    sides of the stub's difference total that kind by one rule.

    Args:
        base_pay: The base pay the stub prints.
        kinded: Every line figure and one-off the stub prints -- anything
            with a ``paycheck_line_kind_id`` and an ``amount``, which the
            saved rows and the entry form's values both are.
        taxes: The stub's four tax figures.

    Returns:
        The :class:`StubTotals`.
    """
    by_kind = totals_by_kind(kinded)
    gross = waterfall_gross(base_pay, by_kind[PaycheckLineKindEnum.TAXABLE_EARNING])
    total_taxes = sum(taxes, ZERO)
    pre_fica = by_kind[PaycheckLineKindEnum.PRE_FICA_DEDUCTION]
    pre_tax = sum((by_kind[kind] for kind in PRE_TAX_KINDS), ZERO)
    post_tax = by_kind[PaycheckLineKindEnum.POST_TAX_DEDUCTION]
    after_tax = by_kind[PaycheckLineKindEnum.AFTER_TAX_EARNING]
    return StubTotals(
        gross=gross, pre_tax=pre_tax, pre_fica=pre_fica, taxes=total_taxes,
        post_tax=post_tax, after_tax=after_tax,
        net=waterfall_net(gross, pre_tax, total_taxes, post_tax, after_tax),
    )


def tax_years_for(basis, periods) -> set[int]:
    """Return every tax year pricing *periods* may read: theirs and each stub's.

    The ONE rule for which years of the law a caller resolves (ruling
    **R-SAL77**: a stub is priced on its own payday's year).  Every stub of
    the profile counts, switched on or not: a year too many costs nothing --
    the law is in the code -- and naming exactly the stubs that will price
    would be a second spelling of :func:`_pricing_stub`.

    Args:
        basis: The :class:`~app.services.payroll_basis.PayrollBasis` whose
            profile's stubs may price.
        periods: The periods about to be priced.

    Returns:
        The tax years.
    """
    return (
        {period.start_date.year for period in periods}
        | {stub.payday.year for stub in basis.profile.pay_stubs}
    )


@dataclass(frozen=True)
class Law:
    """The tax law one paycheck's pricing may read: its own year's, and its stub's.

    Attributes:
        paycheck: The config set (``bracket_set`` / ``state_config`` /
            ``fica_config``) of the paycheck's own tax year.
        paycheck_year: That year.
        by_year: ``{tax_year: config set}`` when the caller resolved the law
            per year, or ``None`` when it handed one year's set alone.
    """
    paycheck: dict
    paycheck_year: int
    by_year: Mapping[int, dict] | None = None

    def for_stub(self, stub) -> dict:
        """Return the law of the stub's own tax year (ruling **R-SAL77**).

        Raises:
            ValueError: The caller resolved no law for the stub's year -- the
                per-year mapping lacks it (built without :func:`tax_years_for`),
                or one year's set was handed for a stub of another year.
                Pricing the stub on the paycheck's year instead is the option
                R-SAL77 rejected, so it is refused rather than done silently.
        """
        year = stub.payday.year
        if self.by_year is not None:
            if year not in self.by_year:
                raise ValueError(
                    f"the pay stub dated {stub.payday.isoformat()} prices this "
                    f"paycheck, but no tax law was resolved for {year}: build "
                    f"the per-year law with tax_years_for"
                )
            return self.by_year[year]
        if year != self.paycheck_year:
            raise ValueError(
                f"the pay stub dated {stub.payday.isoformat()} prices a "
                f"{self.paycheck_year} paycheck, and one year's tax law was "
                f"handed: a stub is priced on its own year's law (R-SAL77), so "
                f"pass configs_by_year covering tax_years_for"
            )
        return self.paycheck


@dataclass(frozen=True)
class Pay:
    """What one paycheck pays before tax: the figures the formulas read off it.

    Attributes:
        payday: The day it is paid, which fixes its year-to-date and rhythm.
        gross: Base pay plus the taxable earnings.
        pre_tax: The pre-tax deductions' total, both pre-tax kinds.
        pre_fica: The all-payroll-taxes deductions' total (plan step
            salary:S11-e): what leaves the Social Security and Medicare wages.
    """
    payday: date
    gross: Decimal
    pre_tax: Decimal
    pre_fica: Decimal


def _taxed(rows) -> list:
    """Return the *rows* of a :data:`TAXED_KINDS` kind carrying money.

    The ONE filter "the same lines" reads (the module docstring), on every
    row it compares: a priced paycheck's lines by the kind the engine priced
    each as, and a stub's line amounts and one-offs by the kind the STUB
    records each under (**R-SAL58**); a row worth ``$0.00`` moves no tax and
    is dropped too.

    Args:
        rows: Anything with a ``paycheck_line_kind_id`` and an ``amount``.

    Returns:
        The rows kept, in order.
    """
    taxed = {ref_cache.paycheck_line_kind_id(kind) for kind in TAXED_KINDS}
    return [
        row for row in rows
        if row.paycheck_line_kind_id in taxed and row.amount != ZERO
    ]


def one_offs_change_taxes(one_offs) -> bool:
    """Whether a stub's *one_offs* make it a stub of other lines (ruling **R-SAL123**).

    "Only tax-changing one-offs": a one-off of a :data:`TAXED_KINDS` kind
    carrying money changes the stub's taxes, so the stub prices a paycheck
    only when no SWITCHED-ON stub of that paycheck's own lines is on or
    before it (a third paycheck that skips a twice-a-month line, say, has
    lines a normal one does not); an
    after-tax or post-tax one-off, or one worth ``$0.00``, does not.  The
    picker's rule (:func:`_has_the_lines`) and the stub page's sentence
    (:attr:`app.services.pay_stub_service.StubReport.one_off_changes_taxes`)
    both read it, so the page cannot say what the picker does not do.

    Args:
        one_offs: A stub's one-offs (rows or figures), each with a
            ``paycheck_line_kind_id`` and an ``amount``.

    Returns:
        ``True`` when any of them changes the stub's taxes.
    """
    return bool(_taxed(one_offs))


def _carried(lines) -> frozenset:
    """Return the taxed paycheck lines a priced paycheck or a stub carries money on.

    The ``paycheck_line_id`` of each :func:`_taxed` line: what both sides of
    "the same lines" are compared on, the paycheck's in :func:`priced_taxes`
    and a stub's in :func:`_has_the_lines`, which adds the one-off half of
    the rule.

    Args:
        lines: Priced lines or a stub's line amounts, of any kind.

    Returns:
        The line ids.
    """
    return frozenset(line.paycheck_line_id for line in _taxed(lines))


def priced_taxes(basis, pay: Pay, lines, law: Law) -> TaxLines:
    """Price a paycheck's four taxes: from its pay stub, or the formulas alone.

    Args:
        basis: The :class:`~app.services.payroll_basis.PayrollBasis`; its
            profile's ``pay_stubs`` are the candidates.
        pay: The paycheck's :class:`Pay`.
        lines: The paycheck's priced lines, of any kind; :func:`_carried`
            keeps the taxed ones.
        law: The :class:`Law` the paycheck and its stub are priced under.

    Returns:
        The :class:`~._breakdown.TaxLines`, naming the stub that priced
        them, or ``None`` there for the formulas alone.
    """
    formulas = _formulas(basis, pay, law.paycheck)
    stub = _pricing_stub(basis.profile.pay_stubs, pay.payday, _carried(lines))
    if stub is None:
        return formulas
    printed = _printed_taxes(stub)
    totals = stub_totals(
        stub.base_pay, (*stub.line_amounts, *stub.one_offs),
        (row.amount for row in stub.withholdings),
    )
    on_stub = _formulas(
        basis, Pay(stub.payday, totals.gross, totals.pre_tax, totals.pre_fica),
        law.for_stub(stub),
    )
    return TaxLines(
        federal=_floored(printed.federal + formulas.federal - on_stub.federal),
        state=_floored(printed.state + formulas.state - on_stub.state),
        social_security=_floored(
            printed.social_security + formulas.social_security
            - on_stub.social_security
        ),
        medicare=_floored(printed.medicare + formulas.medicare - on_stub.medicare),
        stub_payday=stub.payday,
    )


def _pricing_stub(stubs, payday: date, lines: frozenset):
    """Return the stub that prices the paycheck paid *payday*, or ``None``.

    Rulings **R-SAL42** (fork 1, "Latest same-lines stub") and **R-SAL54**
    ("Nearest stub, then formulas"): among the switched-on stubs (fork 8a,
    "A switch on each stub": off prices every paycheck as if it had never
    been entered) dated on or before the payday, the latest with the same
    lines, else the latest of any lines.  A profile holds one stub per
    payday, so "the latest" is never a tie.

    Args:
        stubs: The profile's stubs.
        payday: The paycheck's payday.
        lines: :func:`_carried` of the paycheck.

    Returns:
        The stub, or ``None`` when no switched-on stub is on or before it.
    """
    candidates = [
        stub for stub in stubs if stub.use_for_pricing and stub.payday <= payday
    ]
    same = [stub for stub in candidates if _has_the_lines(stub, lines)]
    return max(same or candidates, key=lambda stub: stub.payday, default=None)


def _has_the_lines(stub, lines: frozenset) -> bool:
    """Whether *stub* has the paycheck's lines: the module docstring's rule, whole.

    Its paycheck lines carrying money under a :data:`TAXED_KINDS` kind -- the
    kind the STUB records each under (R-SAL58) -- are exactly *lines*, and no
    one-off of it changes its taxes (:func:`one_offs_change_taxes`, ruling
    R-SAL123).

    Args:
        stub: A candidate stub.
        lines: :func:`_carried` of the paycheck.

    Returns:
        ``True`` when the stub is a same-lines stub for the paycheck.
    """
    return _carried(stub.line_amounts) == lines and not one_offs_change_taxes(stub.one_offs)


def _printed_taxes(stub) -> TaxLines:
    """Return the four taxes *stub* prints.

    Raises:
        ValueError: The stub lacks one of the four.  The entry door refuses
            such a stub (a ``$0.00`` is a figure, a blank is not), so this is
            a writer's defect, named rather than priced as ``$0.00``.
    """
    printed = {row.withholding_kind_id: row.amount for row in stub.withholdings}
    figures = {}
    for member in WithholdingKindEnum:
        kind_id = ref_cache.withholding_kind_id(member)
        if kind_id not in printed:
            raise ValueError(
                f"the pay stub dated {stub.payday.isoformat()} records no "
                f"{member.name} figure, so it cannot price a paycheck"
            )
        figures[member] = printed[kind_id]
    return TaxLines(
        federal=figures[WithholdingKindEnum.FEDERAL_INCOME],
        state=figures[WithholdingKindEnum.STATE_INCOME],
        social_security=figures[WithholdingKindEnum.SOCIAL_SECURITY],
        medicare=figures[WithholdingKindEnum.MEDICARE],
    )


def _formulas(basis, pay: Pay, configs: dict) -> TaxLines:
    """Run the tax formulas on one paycheck's pay, at its payday's year-to-date and rhythm."""
    return _bracket_tax_lines(
        basis,
        _WageBasis(
            pay.gross,
            waterfall_fica_wages(pay.gross, pay.pre_fica),
            waterfall_taxable(pay.gross, pay.pre_tax),
            _get_cumulative_wages(basis, pay.payday),
            basis.base_pay_on(pay.payday).periods_per_year,
        ),
        pay.pre_tax, configs,
    )


def _floored(amount: Decimal) -> Decimal:
    """Return a priced tax, never below ``$0.00`` (ruling **R-SAL55**, "Never below $0.00").

    At the cent either way: a floored line is ``0.00``, as every other tax
    line is written.
    """
    return round_money(max(amount, ZERO))
