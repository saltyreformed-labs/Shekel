"""
Shekel Budget App -- Paycheck engine: the tax FORMULAS, the four withholding lines.

The per-paycheck wage figures the formulas read (:class:`_WageBasis`) and the
formulas themselves, :func:`_bracket_tax_lines`: IRS Pub 15-T federal
withholding (:func:`_bracket_federal`) plus the annualised state tax
(:func:`_bracket_state`) plus FICA, charged on the Social Security and Medicare
wages (the gross less the all-payroll-taxes lines, since plan step
salary:S11-e) with the wage-base cap enforced from the year-to-date
cumulative the wage figures carry (CRIT-03 / F-037).

**Since plan step salary:S11-c-2c the formulas are one path, and a pay stub is
the other** (rulings **R-SAL42**, **R-SAL54**, **R-SAL55**): :mod:`._stubs`
runs these formulas twice -- on the paycheck and on the stub that prices it --
and adds their difference to the stub's own four taxes.  Until then this leaf
also held a calibrated path, one stub's four EFFECTIVE RATES applied to every
paycheck (``_calibrated_tax_lines``, through ``calibration_service``), and a
``_tax_lines`` that picked between the two; both went with the rates.

Split out of the one-module engine at plan step **salary:C12** (ledger row
**P64**).  Imports :mod:`._breakdown` and the tax services below the engine,
and nothing else of the package.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services import tax_calculator
from app.utils.money import ZERO, round_money

from ._breakdown import TaxLines


@dataclass(frozen=True)
class _WageBasis:
    """The per-paycheck wage figures withholding is computed from.

    The four wage figures travel together through the formulas: the period
    gross, the period Social Security and Medicare wages (gross less the
    all-payroll-taxes deductions, since plan step **salary:S11-e**), the
    period taxable amount (gross less every pre-tax deduction, floored at
    zero), and the year-to-date cumulative of the second that drives the
    FICA Social Security wage-base cap.  The fifth field is the count the
    formulas annualise them by, which is a fact of the same paycheck.  Since
    plan step **salary:S11-c-2c** a pay stub's own paycheck is one of these
    too (:mod:`._stubs`): the stub's gross and taxable by the kinds it
    records, the year-to-date and the count in force on ITS payday.

    Attributes:
        gross_biweekly: The period gross.
        fica_wages: The period Social Security and Medicare wages: the gross
            less the "Pre-tax, all payroll taxes" lines, floored at zero
            (:func:`~._breakdown.waterfall_fica_wages`; ruling **plan#2639**,
            ledger row **SAL-566**).  The two payroll taxes are charged on it;
            until S11-e they were charged on the gross.
        taxable_biweekly: The period gross less the pre-tax deductions of
            both kinds, floored at zero.
        cumulative_wages: The Social Security and Medicare wages already paid
            this calendar year, before this paycheck.
        periods_per_year: How many paychecks a year the rhythm in force on
            the payday pays -- the full-year count IRS Pub 15-T annualises a
            period's wages by, and the state tax is annualised and divided
            back by.  **Per paycheck since plan step salary:X-av-2** (ruling
            **R-SAL66**; ledger row **SAL-569**): it was the basis's
            ``periods_per_year``, the LATEST era's count, so a paycheck paid
            under an earlier rhythm was annualised at a count it was never
            paid at.  It arrives from
            :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`, the
            read that prices the same paycheck's salary at that rhythm, so the
            rhythm it is paid at and the annualiser cannot part.
    """
    gross_biweekly: Decimal
    fica_wages: Decimal
    taxable_biweekly: Decimal
    cumulative_wages: Decimal
    periods_per_year: Decimal


def _bracket_tax_lines(basis, wages, total_pre_tax, tax_configs):
    """Compute the four withholding lines from IRS Pub 15-T brackets plus FICA.

    Social Security and Medicare are charged on ``wages.fica_wages`` (plan
    step salary:S11-e), and its year-to-date cumulative feeds the SS
    wage-base cap (CRIT-03 / F-037), so a paycheck past the cap is charged no
    Social Security, and neither is a stub's paycheck priced past it.

    Args:
        basis: The :class:`~app.services.payroll_basis.PayrollBasis` -- read
            for the W-4 federal inputs.
        wages: The per-paycheck :class:`_WageBasis` (gross, Social Security
            and Medicare wages, taxable, the cumulative YTD of the second that
            drives the SS wage-base cap, and the count Pub 15-T annualises the
            period's wages by).
        total_pre_tax: Per-period pre-tax deduction total (annualised for
            the bracket federal calculation).
        tax_configs: dict with bracket_set, state_config, fica_config, and
            ``basis``: the
            :class:`~app.services.tax_config_service.TaxLawBasis` the
            resolver hands back with the other three (ruling **R-SAL75**).
            The rules may be ``None`` (a filing status the law does not
            model); the basis may not, since every config set the resolver
            returns carries one.

    Returns:
        TaxLines with the federal, state, social_security, and medicare
        withholding amounts, and ``rules``, the basis of the law they were
        priced on (plan step salary:X-at-6).
    """
    pay_periods_per_year = wages.periods_per_year
    bracket_set = tax_configs.get("bracket_set")
    federal = (
        _bracket_federal(
            basis.profile, wages.gross_biweekly, pay_periods_per_year,
            bracket_set, total_pre_tax * pay_periods_per_year,
        )
        if bracket_set
        else ZERO
    )
    state = _bracket_state(
        wages.taxable_biweekly, pay_periods_per_year, tax_configs.get("state_config")
    )
    fica = tax_calculator.calculate_fica(
        wages.fica_wages, tax_configs.get("fica_config"), wages.cumulative_wages
    )
    return TaxLines(
        federal=federal,
        state=state,
        social_security=fica["ss"],
        medicare=fica["medicare"],
        rules=tax_configs["basis"],
    )


def _bracket_federal(profile, gross_biweekly, pay_periods_per_year, bracket_set,
                     annual_pre_tax):
    """Return the bracket-based biweekly federal withholding (IRS Pub 15-T).

    Reads the W-4 inputs off ``profile`` and delegates to
    :func:`tax_calculator.calculate_federal_withholding`.

    Args:
        profile: The SalaryProfile (read for the W-4 inputs).
        gross_biweekly: The period gross to withhold against.
        pay_periods_per_year: The full-year denominator IRS Pub 15-T
            annualises against, off :attr:`_WageBasis.periods_per_year`.
        bracket_set: The year's
            :class:`~app.services.tax_config_service.FederalTaxRules` for the
            filer's status; the formula withholds on their
            ``withholding_rules`` (ruling plan#3213).
        annual_pre_tax: Annualised pre-tax deduction total.

    Returns:
        Decimal biweekly federal withholding.
    """
    w4 = tax_calculator.W4Inputs(
        additional_income=getattr(profile, "additional_income", 0) or 0,
        pre_tax_deductions=annual_pre_tax,
        additional_deductions=getattr(profile, "additional_deductions", 0) or 0,
        qualifying_children=getattr(profile, "qualifying_children", 0) or 0,
        other_dependents=getattr(profile, "other_dependents", 0) or 0,
        extra_withholding=getattr(profile, "extra_withholding", 0) or 0,
    )
    return tax_calculator.calculate_federal_withholding(
        gross_biweekly, pay_periods_per_year, bracket_set, w4,
    )


def _bracket_state(taxable_biweekly, pay_periods_per_year, state_config):
    """Return the biweekly state withholding from annualised taxable income.

    Args:
        taxable_biweekly: Gross less pre-tax deductions, floored at zero.
        pay_periods_per_year: The full-year denominator the annual state tax
            is computed over and divided back by, off
            :attr:`_WageBasis.periods_per_year`.
        state_config: The year's
            :class:`~app.services.tax_config_service.StateTaxRules` (or None).

    Returns:
        Decimal biweekly state withholding.
    """
    state_annual = tax_calculator.calculate_state_tax(
        taxable_biweekly * pay_periods_per_year, state_config
    )
    return round_money(state_annual / pay_periods_per_year)
