"""
Shekel Budget App -- Annual Tax Liability Service

Computes a salary profile's filing-time FEDERAL and NC-STATE income tax
liability for a full tax year -- the missing piece the analytics Taxes tab
needs to estimate a refund (refund = withheld - liability, produced later
in T-P3).  This module is orchestration only: it resolves the year's tax
law (via the ``load_tax_configs_for_year`` SSOT), reads the W-4 inputs
off the profile, and delegates every arithmetic step to the pure engine in
``tax_calculator``.  It never touches projections -- the year's wage and
pre-tax figures are PASSED IN (the T-P3 producer computes them from elapsed
actuals plus projected remainder).

Liability basis (developer ruling 2026-07-04, worked-example fork):

* W-4 Step 4(a) additional income counts as REAL income in both the
  federal and NC bases; Step 4(b) additional deductions are EXCLUDED (a
  withholding-only hint -- the Schedule A check owns the itemize-vs-standard
  election at filing).
* NC base = wages + Step 4(a) - pre-tax deductions, floored at zero; the
  filing-status NC standard deduction AND the AGI-tiered NC per-child
  deduction (T-P5) are subtracted inside / alongside ``calculate_state_tax``.
* Dependent credits (CTC/ODC) are nonrefundable for the federal liability
  (it clamps at zero), but the UNUSED child credit spills into the separate
  refundable Additional Child Tax Credit (``federal.refundable_actc``, T-P5).

The dataclasses below carry both the computed liabilities and the
assumption inputs (standard deductions, credit amounts and counts, flat
state rate) the T-P4 assumptions card renders -- no template ever recomputes
a monetary figure.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.tax_calculator import (
    W4Inputs,
    calculate_annual_federal_liability,
    calculate_state_tax,
    resolve_child_deduction_per_child,
)
from app.services.tax_config_service import load_tax_configs_for_year

ZERO = Decimal("0")


@dataclass(frozen=True)
class FederalLiability:  # pylint: disable=too-many-instance-attributes
    """The federal annual tax layer plus the assumptions T-P4 renders.

    ``taxable`` and ``liability`` are the engine's nonrefundable figures;
    ``refundable_actc`` is the separately-computed refundable Additional
    Child Tax Credit (T-P5), and ``child_credit_refundable_cap`` is the
    per-child refundable ceiling the derivation card shows.  The remaining
    fields are the inputs the derivation and assumptions card display (the
    standard deduction, the two dependent counts, and their per-unit credit
    amounts) so the presentation layer does no arithmetic.

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed because this
    is the one flat federal-figures bundle the derivation ledger renders
    field-by-field (``report.liability.federal.<figure>``); each attribute is
    a distinct line the card shows, and nesting a sub-bundle would only add an
    access level no consumer reads as a unit (and would ripple into the
    template the orchestrator owns).
    """

    standard_deduction: Decimal
    taxable: Decimal
    liability: Decimal
    refundable_actc: Decimal
    qualifying_children: int
    other_dependents: int
    child_credit_amount: Decimal
    other_dependent_credit_amount: Decimal
    child_credit_refundable_cap: Decimal


@dataclass(frozen=True)
class StateLiability:
    """The NC (flat) annual tax layer plus its assumption inputs.

    ``flat_rate`` is ``None`` for a state the law lists with no income tax,
    whose ``standard_deduction`` is ``$0.00`` and ``liability`` zero
    (``taxable_base`` is still reported for context).  Both would be ``None``
    only with no state rules at all, which since plan step salary:X-at-3 is a
    filing status the law does not model -- refused by the federal layer
    before this one is built -- because a state the law does not list is
    refused outright (:func:`compute_annual_liability`).  ``child_deduction_per_child`` is the
    resolved AGI-tier per-child deduction (T-P5) and ``child_deduction_total``
    the amount actually subtracted (per-child x qualifying children); both are
    zero for a state with no child deduction or a filer with no children.
    """

    flat_rate: Decimal | None
    standard_deduction: Decimal | None
    taxable_base: Decimal
    liability: Decimal
    child_deduction_per_child: Decimal
    child_deduction_total: Decimal


@dataclass(frozen=True)
class AnnualLiability:
    """Filing-time federal + state tax liability for one profile and year.

    Carries the input components (``annual_wage_income``, ``annual_pretax``,
    ``additional_income``) alongside the two computed layers so the T-P3
    refund producer and the T-P4 assumptions card have every figure they
    render without recomputation.
    """

    tax_year: int
    annual_wage_income: Decimal
    annual_pretax: Decimal
    additional_income: Decimal
    federal: FederalLiability
    state: StateLiability


def compute_annual_liability(
    profile, year, annual_wage_income, annual_pretax,
) -> AnnualLiability:
    """Compute a profile's filing-time federal + NC-state liability for *year*.

    Resolves the year's tax law through the shared
    :func:`load_tax_configs_for_year` SSOT (the same per-year resolution --
    the latest year the law carries at or before the requested one -- that
    the salary projection uses), reads
    the additional-income and dependent counts off *profile*, and delegates
    the arithmetic to the pure ``tax_calculator`` engine.

    Args:
        profile: The SalaryProfile.  Supplies ``filing_status_id`` and
            ``state_code`` (which slice of the law applies) plus ``additional_income``
            (W-4 4(a)), ``qualifying_children``, and ``other_dependents``.
            Its ``additional_deductions`` (4(b)) is deliberately NOT read.
        year: The tax year to compute liability for.
        annual_wage_income: The year's total wage income (gross wages).
            Passed in -- T-P1 does not project.
        annual_pretax: The year's total pre-tax deductions (retirement,
            Section 125, health premiums).  Passed in.

    Returns:
        :class:`AnnualLiability` -- both computed liabilities plus the
        assumption inputs.

    Raises:
        UnsupportedStateError: The law does not list the profile's state
            (:func:`load_tax_configs_for_year`; plan step salary:X-at-3).
        InvalidFilingStatusError: If no bracket set resolves for *year*
            (raised by :func:`calculate_annual_federal_liability` on a None
            bracket set -- consistent with the withholding engine).
    """
    configs = load_tax_configs_for_year(profile, year)
    wage = Decimal(str(annual_wage_income))
    pretax = Decimal(str(annual_pretax))
    additional_income = Decimal(str(profile.additional_income))

    federal = _federal_layer(
        configs["bracket_set"], profile, wage, pretax, additional_income,
    )
    # NC base (AGI proxy for the child-deduction tier lookup, documented
    # approximation of federal AGI): wages + 4(a) - pre-tax, floored at zero.
    taxable_base = max(ZERO, wage + additional_income - pretax)
    state_config = configs["state_config"]
    child_tiers = (
        state_config.child_deduction_tiers if state_config is not None else ()
    )
    state = _state_layer(
        state_config, taxable_base, child_tiers,
        int(profile.qualifying_children),
    )

    return AnnualLiability(
        tax_year=year,
        annual_wage_income=wage,
        annual_pretax=pretax,
        additional_income=additional_income,
        federal=federal,
        state=state,
    )


def _federal_layer(
    bracket_set, profile, wage, pretax, additional_income,
) -> FederalLiability:
    """Build the federal liability layer from configs + profile W-4 inputs.

    Consults only the four liability-relevant W-4 fields (``additional_income``,
    ``pre_tax_deductions``, ``qualifying_children``, ``other_dependents``);
    the profile's ``additional_deductions`` (Step 4(b)) is not passed, so it
    cannot move the liability.

    Args:
        bracket_set: The year's
            :class:`~app.services.tax_config_service.FederalTaxRules`, or None
            (raises via the engine).
        profile: The SalaryProfile supplying the dependent counts.
        wage: The year's wage income (Decimal).
        pretax: The year's pre-tax deductions (Decimal).
        additional_income: The profile's W-4 Step 4(a) income (Decimal).

    Returns:
        The populated :class:`FederalLiability`.
    """
    w4 = W4Inputs(
        additional_income=additional_income,
        pre_tax_deductions=pretax,
        qualifying_children=profile.qualifying_children,
        other_dependents=profile.other_dependents,
    )
    # Raises InvalidFilingStatusError on a None bracket_set before any
    # attribute access below, matching the withholding engine's contract.
    fed = calculate_annual_federal_liability(wage, bracket_set, w4)

    return FederalLiability(
        standard_deduction=Decimal(str(bracket_set.standard_deduction)),
        taxable=fed.taxable,
        liability=fed.liability,
        refundable_actc=fed.refundable_actc,
        qualifying_children=int(profile.qualifying_children),
        other_dependents=int(profile.other_dependents),
        child_credit_amount=Decimal(str(bracket_set.child_credit_amount)),
        other_dependent_credit_amount=Decimal(
            str(bracket_set.other_dependent_credit_amount)
        ),
        child_credit_refundable_cap=Decimal(
            str(bracket_set.child_credit_refundable_cap)
        ),
    )


def _state_layer(
    state_config, taxable_base, child_tiers, qualifying_children,
) -> StateLiability:
    """Build the NC state liability layer.

    ``taxable_base`` is the NC base (wages + Step 4(a) - pre-tax, floored at
    zero), computed by the caller and reused BOTH as the state-tax base and
    as the AGI proxy for the child-deduction tier lookup (a documented
    approximation of federal AGI).  The NC standard deduction is applied
    inside :func:`calculate_state_tax`; the resolved per-child deduction times
    the qualifying-child count is passed as the additional deduction (T-P5).
    :func:`calculate_state_tax` returns zero for a state with no income tax,
    and an empty ``child_tiers`` (a state with no child deduction) resolves to
    a zero child deduction.

    Args:
        state_config: The year's
            :class:`~app.services.tax_config_service.StateTaxRules`, or None
            -- only for a filing status the law does not model, which the
            federal layer refuses first (plan step salary:X-at-3).
        taxable_base: The NC base / AGI proxy (Decimal, already floored).
        child_tiers: The state's child-deduction tiers (possibly empty).
        qualifying_children: The primary filer's qualifying-child count.

    Returns:
        The populated :class:`StateLiability`.
    """
    per_child = resolve_child_deduction_per_child(taxable_base, child_tiers)
    child_deduction_total = per_child * qualifying_children
    liability = calculate_state_tax(
        taxable_base, state_config, additional_deduction=child_deduction_total,
    )

    flat_rate = None
    standard_deduction = None
    if state_config is not None:
        if state_config.flat_rate is not None:
            flat_rate = Decimal(str(state_config.flat_rate))
        if state_config.standard_deduction is not None:
            standard_deduction = Decimal(str(state_config.standard_deduction))

    return StateLiability(
        flat_rate=flat_rate,
        standard_deduction=standard_deduction,
        taxable_base=taxable_base,
        liability=liability,
        child_deduction_per_child=per_child,
        child_deduction_total=child_deduction_total,
    )
