"""
Shekel Budget App -- Paycheck engine: the VALUE a priced paycheck is.

The six dataclasses :func:`~._pricing.calculate_paycheck` assembles and every
reader of a paycheck consumes -- the salary cockpit, the projection ledger,
the amount model, the payroll feeds.  They carry no behaviour beyond the
section totals that belong to the section owning the data (``taxes.total``,
``deductions.total_pre_tax``, ``earnings.total_taxable``,
``earnings.take_home_rate_pct``), so this leaf imports nothing of the engine
and every other leaf may import it.  Four rules sit beside them:
:func:`waterfall_gross`, :func:`waterfall_fica_wages`,
:func:`waterfall_taxable` and :func:`waterfall_net`, the gross, the Social
Security and Medicare wages, the taxable wage and the net a waterfall of
those totals makes, which a priced paycheck and a transcribed pay stub share
(plan steps **salary:S11-b**, **salary:S11-c-2c** and **salary:S11-e**).
:func:`totals_by_kind` is the one summation of a list's amounts by kind,
which a transcribed pay stub's totals read, and :func:`pre_fica_total` its
all-payroll-taxes total, which a paycheck's breakdown and the year-to-date
wage total read (plan step **salary:S11-e**), so both sides of a stub's
difference total that kind by one rule.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**), which is what put the package where the module had been; the
package docstring carries the engine's contract and every leaf's place in it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services.pay_banner import PayBanner
from app.services.pay_calendar import PayCadence
from app.services.tax_config_service import TaxLawBasis
from app.utils.money import ZERO, round_money


@dataclass
class PricedLine:
    """One payroll line PRICED for one paycheck: a name and what it is worth.

    ``DeductionLine`` until plan step **salary:R18-b** (ruling **R-SAL38**),
    which prices the earning kinds through the same pass; a line of any of
    the five kinds is this value once priced, and which side it is on is
    which list of the breakdown holds it.  ``target_account_id`` is the
    account a DEDUCTION funds (the contribution feed reads it) and ``None``
    for every earning.

    ``paycheck_line_id`` is WHICH ``salary.paycheck_lines`` row was priced,
    since plan step **salary:S11-b**: the pay stub entry door sets each stub
    amount beside the line the app prices that payday and joins the two on
    this key, never on the display name.  The engine copies the priced line's
    ``id``; it is ``None`` only for a line that carries none -- the engine
    suite's duck-typed line fakes and the display fakes tests build by hand.

    ``paycheck_line_kind_id`` is the kind the line was priced AS, since plan
    step **salary:S11-c-2c**: the engine's stub picker keeps the taxed kinds'
    lines by it, on the paycheck's side and the stub's alike, so the set of
    kinds a tax formula reads is written once (``_stubs.TAXED_KINDS``).
    **REQUIRED and keyword-only since plan step salary:S11-e** (ledger row
    **SAL-597**): it defaulted to ``None``, and ``_stubs._taxed`` keeps a line
    only by its kind, so a constructor that forgot it had the line dropped
    from the taxed set with no error and the stub picker comparing the wrong
    lines.  Required, no constructor can forget it; keyword-only, none can
    slip an id into it by position (the :attr:`PeriodInfo.cadence` shape).
    """
    name: str
    amount: Decimal
    target_account_id: int = None
    paycheck_line_id: int | None = None
    paycheck_line_kind_id: int = field(kw_only=True)


def waterfall_gross(base_pay: Decimal, taxable_earnings: Decimal) -> Decimal:
    """Return a paycheck's GROSS: base pay plus the taxable earnings.

    The first step of the waterfall :func:`waterfall_net` finishes, written
    once for the same two readers: the engine's
    :func:`~._lines.priced_gross` (and the year-to-date wage cumulative that
    replays it) and a transcribed pay stub's totals (plan step
    **salary:S11-b**).

    Args:
        base_pay: What the salary pays for the paycheck.
        taxable_earnings: The taxable earning lines' total.

    Returns:
        Their sum.
    """
    return base_pay + taxable_earnings


def waterfall_fica_wages(gross: Decimal, pre_fica: Decimal) -> Decimal:
    """Return the SOCIAL SECURITY AND MEDICARE wages: the gross less the all-payroll-taxes lines.

    The base the two payroll taxes are charged on and the year-to-date wage
    total sums, since plan step **salary:S11-e** (ruling **plan#2639**;
    ledger row **SAL-566**): a "Pre-tax, all payroll taxes" deduction (a
    Section 125 premium, a flexible spending account) leaves it, and a
    "Pre-tax, income tax only" one does not.  Until then the formulas
    charged both taxes on the gross, taxing the lines an employer exempts.
    Written once for the four readers: the engine's paycheck
    (:func:`~._pricing.calculate_paycheck`), a pay stub's paycheck on the
    side of the difference the stub supplies (:mod:`._stubs`), each
    earlier payday the year-to-date sums
    (:func:`~._calendar_questions._get_cumulative_wages`), and the Taxes
    tab's W-2 preview, whose Social Security and Medicare wage boxes are the
    year's figure (:mod:`app.services.tax_report_service`).

    Args:
        gross: Base pay plus the taxable earnings.
        pre_fica: The all-payroll-taxes deductions' total.

    Returns:
        ``gross - pre_fica``, or ``$0.00`` when the deductions exceed it.
    """
    return max(gross - pre_fica, ZERO)


def waterfall_taxable(gross: Decimal, pre_tax: Decimal) -> Decimal:
    """Return a paycheck's TAXABLE wage: the gross less the pre-tax deductions, floored at zero.

    The income-tax base, written once for the two paychecks the formulas
    price since plan step **salary:S11-c-2c**: the engine's own
    (:func:`~._pricing.calculate_paycheck`) and a pay stub's, on the side of
    the difference the stub supplies (:mod:`._stubs`); and, since plan step
    **salary:S11-e** (finding plan#2660), the Taxes tab's W-2 preview, whose
    box 1 and box 16 are the year's figure
    (:mod:`app.services.tax_report_service`), which spelled it by hand
    without the floor.

    Args:
        gross: Base pay plus the taxable earnings.
        pre_tax: The pre-tax deductions' total, both pre-tax kinds: each
            leaves the income-tax base.

    Returns:
        ``gross - pre_tax``, or ``$0.00`` when the deductions exceed it.
    """
    return max(gross - pre_tax, ZERO)


def waterfall_net(
    gross: Decimal, pre_tax: Decimal, taxes: Decimal, post_tax: Decimal,
    after_tax: Decimal,
) -> Decimal:
    """Return the deposit a paycheck's waterfall makes: its NET pay.

    Gross, less the pre-tax deductions, the withholding and the post-tax
    deductions, plus the after-tax earnings.  **The rule written once** for
    the two things that have a net: the paycheck the engine prices
    (:func:`~._pricing.calculate_paycheck`) and the real pay stub whose
    printed net the entry door checks (:mod:`app.services.pay_stub_service`,
    plan step **salary:S11-b**).  With :func:`waterfall_gross` it is the whole
    arithmetic the two share; which LINES feed each total is each caller's own
    grouping (the engine's pass per kind, a stub's sum per kind in
    :func:`~._stubs.stub_totals`), so a new kind changes these signatures and
    both callers answer the change.

    Args:
        gross: Base pay plus the taxable earnings.
        pre_tax: The pre-tax deductions' total.
        taxes: The withholding's total.
        post_tax: The post-tax deductions' total.
        after_tax: The after-tax earnings' total.

    Returns:
        The net, rounded to the cent.
    """
    return round_money(gross - pre_tax - taxes - post_tax + after_tax)


def every_priced_line(taxable, deductions, after_tax) -> tuple:
    """Return every line a paycheck priced, all five kinds, in waterfall order.

    The taxable earnings, the pre-tax and post-tax deductions, then the
    after-tax earnings: what the pay stub door's report sets each stub line
    beside (:attr:`PaycheckBreakdown.priced_lines`, plan step
    **salary:S11-c-2c**), and, since plan step **salary:S11-e** (ledger row
    **SAL-597**), what :func:`~._pricing.calculate_paycheck` hands the stub
    picker, which spelled the same lines by hand beside this.

    Args:
        taxable: The taxable earning lines.
        deductions: The :class:`DeductionBreakdown`.
        after_tax: The after-tax earning lines.

    Returns:
        The :class:`PricedLine` values.
    """
    return (*taxable, *deductions.pre_tax, *deductions.post_tax, *after_tax)


def totals_by_kind(rows: Iterable) -> dict[PaycheckLineKindEnum, Decimal]:
    """Return what *rows* add up to, kind by kind: every kind keyed, ``ZERO`` where none.

    **The one summation of a kind's amounts** (plan step salary:S11-e), read
    by a pay stub's totals (:func:`~._stubs.stub_totals`, where it lived until
    then) and by :func:`pre_fica_total`, so the paycheck side and the stub
    side of a stub-priced paycheck total each kind by the same rule.  Each
    row counts under the kind it carries: the kind a priced line was priced
    as, and the kind a stub prints a figure under (ruling **R-SAL58**).

    Args:
        rows: Anything with a ``paycheck_line_kind_id`` and an ``amount``:
            priced lines, a saved stub's line amounts and one-offs, or the
            entry form's figures.  Walked once.

    Returns:
        ``{kind: total}`` over every :class:`~app.enums.PaycheckLineKindEnum`
        member.

    Raises:
        KeyError: A row names a kind the ref cache does not hold.  The stub
            door refuses such an id before it totals a form
            (:func:`app.services.pay_stub_service.totals_of`'s caller), a
            saved row's foreign key cannot hold one, and the engine prices
            each line under a kind it read from the cache.
    """
    totals = {member: ZERO for member in PaycheckLineKindEnum}
    for row in rows:
        totals[ref_cache.paycheck_line_kind_member(row.paycheck_line_kind_id)] += row.amount
    return totals


def pre_fica_total(lines: Iterable) -> Decimal:
    """Return the all-payroll-taxes total of *lines*: what leaves the FICA wages.

    The :attr:`~app.enums.PaycheckLineKindEnum.PRE_FICA_DEDUCTION` entry of
    :func:`totals_by_kind`, read by the kind each line carries (required on
    a priced line since plan step salary:S11-e, ledger row **SAL-597**).
    The two readers of a priced paycheck's all-payroll-taxes total call it: a
    paycheck's :attr:`DeductionBreakdown.total_pre_fica`, and each earlier
    payday the year-to-date wage total sums
    (:func:`~._calendar_questions._get_cumulative_wages`).  A pay stub's
    totals read the same entry off their own :func:`totals_by_kind`.

    Args:
        lines: Priced lines of any kind.

    Returns:
        The sum, ``ZERO`` when none is of that kind.
    """
    return totals_by_kind(lines)[PaycheckLineKindEnum.PRE_FICA_DEDUCTION]


@dataclass
class TaxLines:
    """The four withholding lines computed for a single paycheck, and what priced them.

    ``stub_payday`` is the payday of the pay stub the four lines were priced
    from, or ``None`` when no switched-on stub is dated on or before the
    paycheck and the tax formulas alone priced them (plan step
    **salary:S11-c-2c**, ruling **R-SAL100**, "Name the pricing stub";
    :mod:`._stubs`).  A payday names one stub of a profile, which holds at most
    one per payday (``uq_pay_stubs_profile_payday``); the date rather than the
    row keeps the breakdown a plain value.  It rides on the TAX lines because
    it is their basis and only theirs: the deductions and the earnings are the
    paycheck lines' own on either path.

    ``rules`` is which year's tax rules priced the four lines, since plan step
    **salary:X-at-6** (ruling **R-SAL75**: "whatever prices a figure hands
    back which year's rules ... it used; the paycheck ... carr[ies] that";
    finding **N-235**): the
    :class:`~app.services.tax_config_service.TaxLawBasis` of the law they
    were priced on, which the resolver hands back with that law
    (:func:`~app.services.tax_config_service.configs_by_year`).  On a
    paycheck it is the basis of the set the paycheck was priced on, on either
    path: a stub's taxes move by what the formulas price on that set.  That
    is the paycheck's OWN tax year's because every production caller hands
    the engine a by-year mapping (``income_service.ProfilePaychecks``); the
    engine's one-set mode, which only unit tests use, would price and label
    a paycheck on whatever single set it is handed, and plan#3476 deletes
    it so nothing but the caller's discipline holds this.  The stub's
    side is priced on the stub's own year's law (ruling **R-SAL77**), whose
    basis no paycheck carries yet (plan#3423).  **REQUIRED and
    keyword-only** (the :attr:`PricedLine.paycheck_line_kind_id` shape): a
    default would let a constructor forget it, and a paycheck priced on
    another year's rules would render as a plain figure again, the defect
    N-235 names.  So nothing builds figures priced on no law as this value:
    a pay stub's printed taxes are read by kind (``_stubs._printed_taxes``).
    """
    federal: Decimal = ZERO
    state: Decimal = ZERO
    social_security: Decimal = ZERO
    medicare: Decimal = ZERO
    stub_payday: date | None = None
    rules: TaxLawBasis = field(kw_only=True)

    @property
    def total(self) -> Decimal:
        """Return the sum of the four withholding lines."""
        return self.federal + self.state + self.social_security + self.medicare


@dataclass
class DeductionBreakdown:
    """Pre- and post-tax deduction line items for a single paycheck.

    ``pre_tax`` holds BOTH pre-tax kinds, in waterfall order -- the
    all-payroll-taxes lines, then the income-tax-only ones -- each line
    carrying the kind it was priced as (plan step **salary:S11-e**, ruling
    **plan#2639**).  One list rather than two because every reader of it
    wants both: the income-tax base subtracts both (:attr:`total_pre_tax`),
    the contribution feed reads the account either funds, and
    :func:`every_priced_line` lists both; a second list would be a reader
    each of them could forget.  The one figure that tells them apart is
    :attr:`total_pre_fica`.
    """
    pre_tax: list[PricedLine] = field(default_factory=list)
    post_tax: list[PricedLine] = field(default_factory=list)

    @property
    def total_pre_tax(self) -> Decimal:
        """Return the sum of the pre-tax deduction amounts, both pre-tax kinds."""
        return sum((d.amount for d in self.pre_tax), ZERO)

    @property
    def total_pre_fica(self) -> Decimal:
        """Return the all-payroll-taxes lines' sum: what leaves Social Security and Medicare wages.

        :func:`pre_fica_total` of ``pre_tax``, the rule the year-to-date
        wage total reads for each earlier payday.
        """
        return pre_fica_total(self.pre_tax)

    @property
    def total_post_tax(self) -> Decimal:
        """Return the sum of the post-tax deduction amounts."""
        return sum((d.amount for d in self.post_tax), ZERO)


@dataclass
class Earnings:
    """The earnings side of a single paycheck: base pay, its lines, and the figures they make.

    **Base pay plus a list of lines, since plan step salary:R18-b** (ruling
    **R-SAL38**; ledger row **D59**).  Until then this was four scalars and
    ``gross_biweekly`` WAS the salary rate, so nothing could add a dollar to
    a paycheck that was not an annual-salary raise; an employer allowance
    with a cadence had to be a separate income template, and one payroll
    deposit met two or three app rows.

    Attributes:
        annual_salary: The yearly figure on the payday: the paycheck's pay
            RATE, the pay in force on the last day it pays for, times the
            paychecks a year of the rhythm in force on it
            (:attr:`~app.services.pay_rate.BasePay.annual`; ruling
            **R-SAL59**, plan step salary:X-av-3a -- the post-raise annual
            salary the rate was divided from until then).  It was
            :attr:`base_biweekly` times the count until task plan#3100, when
            a paycheck a raise starts inside began paying part of it.
        base_biweekly: What the SALARY pays for one paycheck -- each weekday
            of its pay period at the pay in force that day
            (:meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`,
            ruling **plan#3089**), and the base every PERCENTAGE line is a
            percentage of
            (R-SAL38: never of gross, so a percentage earning is not circular
            and no existing line moves when an earning joins).
        gross_biweekly: ``base_biweekly`` plus the TAXABLE earning lines --
            the stub's gross, the FICA base, what withholding annualises and
            what the year-to-date wage cumulative sums.
        taxable_income: ``gross_biweekly`` less the pre-tax deductions,
            floored at zero; the income-tax base.
        net_pay: The deposit: gross less both deduction passes and the
            withholding, plus the AFTER-TAX earning lines.
        taxable: The taxable earning lines priced for this paycheck, in
            the profile's line order.
        after_tax: The after-tax earning lines priced for this paycheck.
    """
    annual_salary: Decimal
    base_biweekly: Decimal
    gross_biweekly: Decimal
    taxable_income: Decimal = ZERO
    net_pay: Decimal = ZERO
    taxable: list[PricedLine] = field(default_factory=list)
    after_tax: list[PricedLine] = field(default_factory=list)

    @property
    def total_taxable(self) -> Decimal:
        """Return the sum of the taxable earning lines (``gross - base``)."""
        return sum((line.amount for line in self.taxable), ZERO)

    @property
    def total_after_tax(self) -> Decimal:
        """Return the sum of the after-tax earning lines."""
        return sum((line.amount for line in self.after_tax), ZERO)

    @property
    def take_home_rate_pct(self) -> Decimal | None:
        """Return ``net / gross`` expressed as a percent (MED-04 / E-16).

        Pre-computed here so the salary breakdown template renders the
        take-home rate without Jinja-side division.  Returns ``None``
        when ``gross_biweekly`` is non-positive so the template can
        render a placeholder ``--`` without dividing by zero.

        **FULL precision, never quantized here** (plan step salary:S3-f-2a):
        ``retirement_dashboard_service.compute_gap_net_biweekly`` scales the
        retirement income target by this ratio, so a display rounding added
        here would move a money figure; the template rounds at its own edge.
        """
        if self.gross_biweekly <= ZERO:
            return None
        return (self.net_pay / self.gross_biweekly) * Decimal("100")


@dataclass
class PeriodInfo:
    """Pay-period identity and per-paycheck event flags.

    Attributes:
        payday: The day this paycheck arrives, the period's ``start_date``.
            **The TOTAL identity, added at plan step salary:S3-d**: every
            paycheck has a payday where only a MATERIALISED one has an id.  It
            retired ``projection_inputs``' ``payday_by_period_id`` table,
            which existed only to recover this fact from the id.
        period_id: ``budget.pay_periods.id``, or ``None`` for a paycheck on a
            PROJECTED payday -- one the owner's cadence reaches past their
            saved schedule.  **Typed ``int`` and documented "never ``None``
            structurally" until plan step salary:S3-d**, which held only while
            nothing priced a payday past the horizon; the pricer does, so the
            nullable :class:`~app.services.pay_calendar.DerivedPeriod` has
            always carried reaches here, for its reason -- a projected payday
            is not a row a foreign key can name.  REQUIRED with no default, so
            widening the type did not also make it forgettable.
        is_third_paycheck: Whether this is the third paycheck starting in its
            calendar month, which is what a 24-per-year deduction skips.
        banner: The banner of this paycheck, as
            :meth:`~app.services.payroll_basis.PayrollBasis.banner_on`
            composes it: the change a pay entry recorded from its payday
            makes and the forecast raises it is the first to pay, each with
            its direction (ruling **R-SAL84**, task plan#3168).  A display
            string until that task, which every surface read its direction
            back off.
        cadence: The rhythm this paycheck was PRICED at -- the era in force
            on :attr:`payday` (:func:`~app.services.pay_calendar.cadence_on`),
            whose count multiplies :attr:`Earnings.base_biweekly` into
            :attr:`Earnings.annual_salary` and annualised the withholding.
            **The one count any reader turns this paycheck into a monthly or
            yearly figure with** (ruling **R-SAL70**, plan step
            salary:X-av-2): debt-to-income, a goal stated in months of
            income, the retirement gap's current-pay fallback.  Those readers
            multiplied by the LATEST era's count, which was right only while
            the engine divided by the same wrong one; with the division per
            payday they would have read a monthly-era paycheck of
            ``$5,000.00`` as ``$10,833.33`` a month under a later biweekly era
            (made-up figures).  A fact of the PAYDAY, so it sits here beside
            it rather than among the figures it converts.  REQUIRED and
            keyword-only, so no constructor can forget it and none can slip a
            figure into it by position.
    """
    payday: date
    period_id: "int | None"
    is_third_paycheck: bool = False
    banner: PayBanner = field(default_factory=PayBanner)
    cadence: PayCadence = field(kw_only=True)


@dataclass
class PaycheckBreakdown:
    """Complete paycheck breakdown for a single pay period.

    The breakdown is organised into four cohesive sections rather than a
    flat field list: :class:`PeriodInfo` (``period``), :class:`Earnings`
    (``earnings``), :class:`TaxLines` (``taxes``), and
    :class:`DeductionBreakdown` (``deductions``).  Section totals live on
    the section that owns the data (``taxes.total``,
    ``deductions.total_pre_tax``, ``earnings.take_home_rate_pct``).

    ``taxes`` is REQUIRED since plan step salary:X-at-6, when
    :attr:`TaxLines.rules` became so: no default could build tax lines
    without the law they were priced on.
    """
    period: PeriodInfo
    earnings: Earnings
    taxes: TaxLines
    deductions: DeductionBreakdown = field(default_factory=DeductionBreakdown)

    @property
    def priced_lines(self) -> tuple:
        """Every line this paycheck priced, all five kinds (:func:`every_priced_line`)."""
        return every_priced_line(
            self.earnings.taxable, self.deductions, self.earnings.after_tax,
        )
