"""
Shekel Budget App -- Tax Calculator Service

Pure functions for computing federal, state, and FICA taxes.
No database access -- all data is passed in as arguments.

Federal withholding follows the IRS Publication 15-T Percentage Method:
  Step 1 -- Annualize income
  Step 2 -- Apply pre-tax adjustments
  Step 3 -- Subtract standard deduction
  Step 4 -- Apply marginal tax brackets (data-driven)
  Step 5 -- Apply credits (W-4 Step 3)
  Step 6 -- De-annualize to per-period withholding
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from app.enums import TaxTypeEnum
from app.services.exceptions import (
    InvalidDependentCountError,
    InvalidFilingStatusError,
    InvalidGrossPayError,
    InvalidPayPeriodsError,
)
from app.utils.money import round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# Refundable Additional Child Tax Credit (ACTC) formula parameters (IRC
# 24(d)).  The refundable ACTC is ``15% x max(0, earned_income - 2,500)``,
# further capped at the per-child refundable ceiling.  These two parameters
# are STATUTORY and, verified 2026-07 against the 2025 Schedule 8812
# instructions, were NOT changed by OBBBA and are not inflation-indexed --
# so they are module constants, not per-year figures in the tax law (unlike
# the per-child ceiling, which each year of :mod:`app.tax_law` states once
# among its per-child credits, :class:`app.tax_law.FederalCredits`).  If a
# future law makes either parameter year-varying, move it into the tax law
# then.
ACTC_EARNED_INCOME_RATE = Decimal("0.15")
ACTC_EARNED_INCOME_FLOOR = Decimal("2500")


# ── Federal Withholding (IRS Pub 15-T Percentage Method) ──────────


@dataclass(frozen=True)
class W4Inputs:
    """The employee's W-4 / pre-tax withholding adjustments.

    Bundles the per-employee inputs to :func:`calculate_federal_withholding`
    beyond gross pay, the pay-period count, and the bracket set: the W-4
    Step 3 dependent counts, the Step 4(a)/(b)/(c) amounts, and the
    annualized pre-tax deductions.  All fields default to "none" so an
    employee with a blank W-4 is ``W4Inputs()``.

    ``__post_init__`` coerces each field to its target type (``Decimal``
    constructed from strings for money, ``int`` for the counts), so callers
    may pass raw model values and the calculator consumes them directly --
    the same construct-from-strings discipline the function previously
    applied to each argument, now in one place.

    Fields:
        additional_income:     W-4 Step 4(a) -- other annual income.
        pre_tax_deductions:    Total annual pre-tax deductions (retirement,
                               Section 125, health premiums).
        additional_deductions: W-4 Step 4(b) -- additional annual deductions.
        qualifying_children:   W-4 Step 3 -- qualifying children under 17.
        other_dependents:      W-4 Step 3 -- other dependents.
        extra_withholding:     W-4 Step 4(c) -- extra withholding per period.
    """

    additional_income: Decimal = ZERO
    pre_tax_deductions: Decimal = ZERO
    additional_deductions: Decimal = ZERO
    qualifying_children: int = 0
    other_dependents: int = 0
    extra_withholding: Decimal = ZERO

    def __post_init__(self):
        """Coerce every field to its target type on construction.

        The four money fields are rebuilt as ``Decimal(str(value))`` and the two
        dependent counts as ``int``.  Callers pass raw model values -- an ORM
        column, or a bare ``0`` from an ``or 0`` default
        (:func:`app.services.paycheck_calculator` does exactly this) -- so the
        string hop is what keeps a ``float`` from ever reaching a
        ``Decimal`` constructor, the project's money rule, in ONE place instead
        of at every argument.

        It does NOT quantize: these are ANNUALIZED W-4 amounts, and
        :func:`calculate_federal_withholding` rounds HALF_UP to cents once at the
        end.  Rounding the inputs would inject error rather than remove it.

        ``object.__setattr__`` because the dataclass is frozen.
        """
        for money_field in (
            "additional_income",
            "pre_tax_deductions",
            "additional_deductions",
            "extra_withholding",
        ):
            object.__setattr__(
                self, money_field, Decimal(str(getattr(self, money_field))),
            )
        object.__setattr__(
            self, "qualifying_children", int(self.qualifying_children),
        )
        object.__setattr__(
            self, "other_dependents", int(self.other_dependents),
        )


def calculate_federal_withholding(gross_pay, pay_periods, bracket_set, w4=W4Inputs()):
    """Calculate per-period federal income tax withholding.

    Implements the IRS Publication 15-T Percentage Method (2026+).
    All monetary values must be Decimal.  Returns a Decimal rounded
    HALF_UP to two decimal places.

    **It prices on the year's WITHHOLDING figures** (ruling plan#3213, plan
    step salary:X-at-7): ``bracket_set.withholding_rules``, which carries
    Pub 15-T's standard deduction and the W-4's credits where the year states
    figures other than the return's (:class:`app.tax_law.FederalRules` says
    why a year can), and is the return's rules themselves where it states
    none.  Reading it HERE,
    off the rules every caller hands in, is what keeps any caller from
    withholding on the return's figures;
    :func:`calculate_annual_federal_liability` reads the return's.

    Args:
        gross_pay:              Gross pay for one pay period.
        pay_periods:            Number of pay periods per year (e.g. 26).
        bracket_set:            The year's federal rules for the filer's
                                status (:class:`app.services.tax_config_service.FederalTaxRules`),
                                whose ``withholding_rules`` carry
                                .standard_deduction, .child_credit_amount,
                                .other_dependent_credit_amount, and .brackets
                                (:class:`app.tax_law.Bracket` rungs).
        w4:                     :class:`W4Inputs` -- the employee's W-4 /
                                pre-tax withholding adjustments (Step 3
                                dependent counts, Step 4(a)/(b)/(c) amounts,
                                annualized pre-tax deductions).  Defaults to an
                                empty ``W4Inputs()`` (a blank W-4).

    Returns:
        Decimal -- per-period federal withholding amount.

    Raises:
        InvalidGrossPayError:       If gross_pay < 0.
        InvalidPayPeriodsError:     If pay_periods <= 0.
        InvalidFilingStatusError:   If bracket_set is None.
        InvalidDependentCountError: If dependent counts are negative.
    """
    # ── Input validation ──────────────────────────────────────────
    gross_pay = Decimal(str(gross_pay))
    pay_periods = int(pay_periods)

    if gross_pay < ZERO:
        raise InvalidGrossPayError(gross_pay)
    if pay_periods <= 0:
        raise InvalidPayPeriodsError(pay_periods)
    if bracket_set is None:
        raise InvalidFilingStatusError(None)
    if w4.qualifying_children < 0:
        raise InvalidDependentCountError(
            "qualifying_children", w4.qualifying_children,
        )
    if w4.other_dependents < 0:
        raise InvalidDependentCountError(
            "other_dependents", w4.other_dependents,
        )
    rules = bracket_set.withholding_rules

    # ── Step 1 -- Annualize income ─────────────────────────────────
    # IRS Pub 15-T: multiply periodic gross pay by the number of
    # pay periods, then add any additional annual income from W-4 4(a).
    annual_income = (gross_pay * pay_periods) + w4.additional_income

    logger.debug("Step 1 -- annual_income: %s", annual_income)

    # ── Step 2 -- Pre-tax adjustments ──────────────────────────────
    # Subtract annualized pre-tax deductions (retirement, Sec 125, etc.)
    # and W-4 Step 4(b) additional deductions.
    adjusted_income = (
        annual_income - w4.pre_tax_deductions - w4.additional_deductions
    )
    adjusted_income = max(adjusted_income, ZERO)

    # ── Step 3 -- Subtract standard deduction ──────────────────────
    standard_deduction = Decimal(str(rules.standard_deduction))
    taxable_income = adjusted_income - standard_deduction
    taxable_income = max(taxable_income, ZERO)

    logger.debug("Step 3 -- taxable_income: %s", taxable_income)

    # ── Step 4 -- Apply marginal tax brackets ──────────────────────
    # Brackets are data-driven: iterate sorted bracket tiers and apply
    # the marginal rate to the portion of income within each tier.
    annual_tax_before_credits = _apply_marginal_brackets(
        taxable_income, rules.brackets
    )

    logger.debug(
        "Step 4 -- annual_tax_before_credits: %s", annual_tax_before_credits
    )

    # ── Step 5 -- Apply credits (W-4 Step 3) ───────────────────────
    total_credits = _dependent_credits(
        rules, w4.qualifying_children, w4.other_dependents,
    )

    logger.debug("Step 5 -- total_credits: %s", total_credits)

    annual_tax_after_credits = annual_tax_before_credits - total_credits
    annual_tax_after_credits = max(annual_tax_after_credits, ZERO)

    logger.debug(
        "Step 5 -- annual_tax_after_credits: %s", annual_tax_after_credits
    )

    # ── Step 6 -- De-annualize ─────────────────────────────────────
    per_period_withholding = (
        annual_tax_after_credits / pay_periods
    ) + w4.extra_withholding

    per_period_withholding = round_money(per_period_withholding)

    logger.debug(
        "Step 6 -- per_period_withholding: %s", per_period_withholding
    )

    return per_period_withholding


def _dependent_credits(bracket_set, qualifying_children, other_dependents):
    """Return the total annual W-4 Step 3 dependent credits.

    The child credit (per qualifying child under 17) plus the
    other-dependent credit, read from the rules'
    ``child_credit_amount`` / ``other_dependent_credit_amount``.

    **Read with no default, so rules that state no credits raise
    ``AttributeError``** rather than price as a filer owed none.  The law's
    per-status :class:`app.tax_law.FederalRules` states none since plan step
    plan#3206 (the year states them once, ruling plan#3340), and a default of
    zero priced it with no credit at all: a 2027 head of household earning
    $80,000 with two children owed $6,244.00 on it where the law says
    $1,644.00 (measured 2026-11-11, plan#3206's review).  The resolver's
    slice, :class:`app.services.tax_config_service.FederalTaxRules`, carries
    them.  The withholding formula refuses the same rules the same way, on
    ``withholding_rules``.
    """
    child_credit_amount = Decimal(str(bracket_set.child_credit_amount))
    other_credit_amount = Decimal(str(bracket_set.other_dependent_credit_amount))
    return (
        qualifying_children * child_credit_amount
        + other_dependents * other_credit_amount
    )


def _apply_marginal_brackets(taxable_income, brackets):
    """Apply progressive marginal tax rates from a bracket list.

    Brackets are iterated in sort_order.  Each bracket defines a
    (min_income, max_income, rate) range.  The top bracket has
    max_income = None (open-ended).

    Args:
        taxable_income: Decimal -- income after standard deduction.
        brackets:       Iterable of :class:`app.tax_law.Bracket` rungs.

    Returns:
        Decimal -- annual tax before credits, rounded to 2 places.
    """
    if taxable_income <= ZERO:
        return ZERO

    total_tax = ZERO
    for bracket in sorted(brackets, key=lambda b: b.sort_order):
        bracket_min = Decimal(str(bracket.min_income))
        bracket_max = (
            Decimal(str(bracket.max_income)) if bracket.max_income else None
        )
        rate = Decimal(str(bracket.rate))

        if taxable_income <= bracket_min:
            break

        if bracket_max is None:
            amount_in_bracket = taxable_income - bracket_min
        else:
            amount_in_bracket = min(taxable_income, bracket_max) - bracket_min

        if amount_in_bracket > ZERO:
            total_tax += amount_in_bracket * rate

    return round_money(total_tax)


def marginal_rate_for(taxable, brackets):
    """Return the marginal bracket rate that contains ``taxable`` income.

    The rate on the taxpayer's LAST dollar of taxable income -- the chip
    the analytics Taxes hero band shows next to the effective rate.  It is
    the rate of the highest bracket whose ``min_income`` is STRICTLY below
    ``taxable``, which is exactly the last bracket
    :func:`_apply_marginal_brackets` adds tax from (that loop stops at the
    first bracket with ``taxable <= min_income``).  Sourcing the chip from
    the same bracket ladder the liability uses means the marginal chip and
    the tax math cannot disagree.

    Boundary rule (documented so the chip and the liability agree at an
    exact edge): a ``taxable`` sitting EXACTLY on a bracket edge -- i.e.
    equal to the upper bracket's ``min_income`` (and the lower bracket's
    ``max_income``) -- belongs to the LOWER bracket.  The upper bracket
    starts STRICTLY above its ``min_income`` (``taxable > min_income`` is
    required for it to apply), so at the edge the last taxed dollar is
    still in the lower tier.  Example (2026 single): ``taxable == 50,400``
    -> 12% (the 22% tier opens above 50,400), while ``taxable == 50,401``
    -> 22%.

    ``taxable`` at or below the lowest bracket's ``min_income`` (the usual
    case being income fully absorbed by the standard deduction, ``taxable
    == 0``) returns the LOWEST bracket's rate: the rate the first positive
    dollar of taxable income would meet.  An empty ``brackets`` iterable
    returns ``ZERO`` (no tax structure to place ``taxable`` in) -- a
    degenerate seed the analytics producer never hits, since it computes
    the liability (which raises on a missing bracket set) first.

    Args:
        taxable: The federal taxable income to place (Decimal, or any
            value ``Decimal(str(...))`` accepts; coerced from a string so
            a ``float`` never enters the money math).
        brackets: The federal rules' bracket rungs, each exposing
            ``sort_order``, ``min_income``, and ``rate`` (the same objects
            :func:`_apply_marginal_brackets` iterates).

    Returns:
        Decimal -- the marginal bracket rate as a fraction (e.g.
        ``Decimal("0.2400")`` for the 24% tier).
    """
    ordered = sorted(brackets, key=lambda b: b.sort_order)
    if not ordered:
        return ZERO

    taxable = Decimal(str(taxable))
    # Default to the lowest tier's rate: for ``taxable`` at or below the
    # lowest ``min_income`` no bracket is strictly below it, so the first
    # positive taxable dollar's rate is the honest answer.
    marginal = Decimal(str(ordered[0].rate))
    for bracket in ordered:
        # Mirror _apply_marginal_brackets: a bracket taxes income only when
        # ``taxable > min_income``, so the marginal tier is the last one
        # that gate admits.  An exact edge (``taxable == min_income``) fails
        # the gate and leaves the lower tier as marginal.
        if taxable > Decimal(str(bracket.min_income)):
            marginal = Decimal(str(bracket.rate))
        else:
            break
    return marginal


# ── Annual Filing-Time Liability (federal) ────────────────────────


@dataclass(frozen=True)
class AnnualFederalTax:
    """The computed figures of the filing-time federal calculation.

    ``taxable`` is federal taxable income (wages plus W-4 Step 4(a) income,
    less pre-tax deductions and the standard deduction, floored at zero);
    ``liability`` is the tax owed on it after nonrefundable dependent
    credits, floored at zero; ``refundable_actc`` is the refundable
    Additional Child Tax Credit computed separately from ``liability`` (the
    liability clamps at zero, but the unused child credit spills into this
    refundable component -- T-P5).  The three travel together so a caller
    that renders the derivation -- the Taxes tab's assumptions card (T-P4) --
    does not have to recompute ``taxable`` from the raw income components:
    the taxable-income clamp expression lives in exactly one place,
    :func:`calculate_annual_federal_liability`.
    """

    taxable: Decimal
    liability: Decimal
    refundable_actc: Decimal


def calculate_annual_federal_liability(annual_wage_income, bracket_set, w4=W4Inputs()):
    """Compute the filing-time FEDERAL income tax liability for a full year.

    This is the annual-liability sibling of
    :func:`calculate_federal_withholding`: it applies the same bracket
    ladder and dependent credits, but ONCE to the whole year's income rather
    than per pay period, to answer "what will this taxpayer owe the IRS at
    filing time" (the tax-refund estimate the analytics Taxes tab is built
    around).  Both functions share the :func:`_apply_marginal_brackets` and
    :func:`_dependent_credits` primitives, so there is a single bracket
    implementation.  It reads the RETURN's standard deduction and credits off
    ``bracket_set`` itself, where the withholding formula reads the year's
    withholding figures (``bracket_set.withholding_rules``, ruling plan#3213):
    in a year whose withholding documents print other figures than the
    return's, the two parting is the refund or balance due those documents
    leave.

    The identity (developer ruling 2026-07-04, worked-example fork):

        taxable   = max(0, wages + Step 4(a) income
                            - pre-tax deductions - standard deduction)
        liability = max(0, marginal_brackets(taxable) - dependent_credits)

    W-4 Step 4(a) additional income (``w4.additional_income``) counts as
    REAL income and is included in the base.  W-4 Step 4(b) additional
    deductions (``w4.additional_deductions``) and Step 4(c) extra
    withholding (``w4.extra_withholding``) are withholding-only hints and
    are DELIBERATELY NOT read here: at filing time the Schedule A check
    owns the itemize-vs-standard election, so 4(b) must not move the
    liability.  Dependent credits are nonrefundable for the ``liability``
    itself (it clamps at zero), but the UNUSED child-credit portion spills
    into a SEPARATE refundable component -- the Additional Child Tax Credit
    (ACTC), computed by :func:`_refundable_actc` (T-P5).

    Args:
        annual_wage_income: The year's Box-1 wage income (gross wages less
            pre-tax deductions is passed as ``annual_wage_income`` minus
            ``w4.pre_tax_deductions``).  Also used as the ACTC earned-income
            base (a documented approximation -- Box-1 wages are the
            taxpayer's earned income here).  Constructed to ``Decimal`` from
            a string; a ``float`` argument is coerced via ``str`` first.
        bracket_set: The year's federal rules for the filer's status
            (:class:`app.services.tax_config_service.FederalTaxRules`, or a stand-in) exposing
            ``standard_deduction``, ``child_credit_amount``,
            ``other_dependent_credit_amount``, ``child_credit_refundable_cap``,
            and ``brackets``.
        w4: :class:`W4Inputs` -- the employee's W-4 inputs.  Only
            ``additional_income``, ``pre_tax_deductions``,
            ``qualifying_children``, and ``other_dependents`` are consulted;
            ``additional_deductions`` and ``extra_withholding`` are ignored
            (see above).  Defaults to an empty ``W4Inputs()`` (a blank W-4).

    Returns:
        :class:`AnnualFederalTax` -- the year's federal ``taxable`` income,
        the nonrefundable ``liability`` owed on it, and the ``refundable_actc``
        refundable credit, all Decimal at two places.

    Raises:
        InvalidFilingStatusError:   If ``bracket_set`` is None (mirrors
            :func:`calculate_federal_withholding`, which cannot resolve a
            bracket ladder without a filing status).
        InvalidDependentCountError: If a dependent count is negative.
    """
    if bracket_set is None:
        raise InvalidFilingStatusError(None)
    if w4.qualifying_children < 0:
        raise InvalidDependentCountError(
            "qualifying_children", w4.qualifying_children,
        )
    if w4.other_dependents < 0:
        raise InvalidDependentCountError(
            "other_dependents", w4.other_dependents,
        )

    annual_wage_income = Decimal(str(annual_wage_income))
    standard_deduction = Decimal(str(bracket_set.standard_deduction))

    # Filing-time taxable income.  Step 4(a) is real income; Step 4(b)
    # (w4.additional_deductions) and Step 4(c) (w4.extra_withholding) are
    # withholding-only and deliberately excluded per the 2026-07-04 ruling.
    taxable = (
        annual_wage_income
        + w4.additional_income
        - w4.pre_tax_deductions
        - standard_deduction
    )
    taxable = max(taxable, ZERO)

    tax_before_credits = _apply_marginal_brackets(taxable, bracket_set.brackets)
    total_credits = _dependent_credits(
        bracket_set, w4.qualifying_children, w4.other_dependents,
    )
    liability = max(tax_before_credits - total_credits, ZERO)
    refundable_actc = _refundable_actc(
        bracket_set, w4.qualifying_children, tax_before_credits,
        total_credits, annual_wage_income,
    )

    return AnnualFederalTax(
        taxable=taxable, liability=liability, refundable_actc=refundable_actc,
    )


def _refundable_actc(
    bracket_set, qualifying_children, tax_before_credits, total_credits,
    earned_income,
):
    """Return the refundable Additional Child Tax Credit (ACTC).

    The refundable ACTC is the smallest of three legs (per IRC 24(d) /
    Schedule 8812; developer T-P5 ruling):

        unused_credit   = max(0, total_dependent_credits - tax_before_credits)
        cap_leg         = child_credit_refundable_cap x qualifying_children
        earned_income_leg = 15% x max(0, earned_income - 2,500)
        ACTC = max(0, min(unused_credit, cap_leg, earned_income_leg))

    ``unused_credit`` uses the TOTAL dependent credits (CTC + ODC) per the
    ruling's formula; the ``cap_leg`` (which multiplies only qualifying
    CHILDREN by the per-child refundable ceiling) is what bounds the result
    to the child-only refundable maximum, so an other-dependent credit can
    only widen ``unused_credit``, never the refundable payout, unless it is
    the binding leg -- see the report's out-of-scope note.  ``earned_income``
    is the Box-1 wage income (a documented approximation of IRC earned
    income).  Phase-outs (MAGI over 400k MFJ / 200k other) are NOT modeled.

    Args:
        bracket_set: The rules exposing ``child_credit_refundable_cap``,
            read with no default as :func:`_dependent_credits` reads the
            credits (it says why).
        qualifying_children: W-4 Step 3 qualifying-children count.
        tax_before_credits: Marginal-bracket tax before dependent credits.
        total_credits: The total nonrefundable dependent credits (CTC + ODC).
        earned_income: The ACTC earned-income base (Box-1 wages).

    Returns:
        Decimal -- the refundable ACTC, quantised HALF_UP to two places.
    """
    refundable_cap = Decimal(str(bracket_set.child_credit_refundable_cap))
    unused_credit = max(ZERO, total_credits - tax_before_credits)
    cap_leg = refundable_cap * qualifying_children
    earned_income_leg = ACTC_EARNED_INCOME_RATE * max(
        ZERO, Decimal(str(earned_income)) - ACTC_EARNED_INCOME_FLOOR,
    )
    actc = min(unused_credit, cap_leg, earned_income_leg)
    return round_money(max(actc, ZERO))


# ── State Tax ─────────────────────────────────────────────────────


def calculate_state_tax(annual_gross, state_config, *, additional_deduction=ZERO):
    """Calculate annual state income tax.

    **Two tax types are priced, and anything else is refused** (plan step
    salary:X-at-3: the engine refuses "a flat state with no rate"; ruling
    **salary:R-SAL78**).  A state with no income tax prices ``$0.00``; a flat-rate
    state prices its rate on the base after its deductions.  Until X-at-3 a
    flat-rate config with no rate, or a type no formula prices, fell through
    to ``$0.00`` -- the silent figure finding **SAL-575** names.  The law
    cannot hold either (:class:`app.tax_law.StateYearLaw` refuses both when it
    loads); this refusal is what keeps the formula from answering for one
    handed in from anywhere else.

    Args:
        annual_gross:  Total annual gross income (Decimal).
        state_config:  The year's state rules for the filer's status
                       (:class:`app.services.tax_config_service.StateTaxRules`),
                       or ``None`` when the law resolved none -- a filing
                       status it does not model, since the resolver refuses a
                       state it does not list -- which returns 0.
        additional_deduction:  A further deduction subtracted from the base
            alongside the state standard deduction (the resolved NC per-child
            deduction total -- T-P5).  Defaults to ``ZERO`` so the withholding
            path (which does NOT apply the child deduction) is unchanged; the
            liability service passes the resolved child-deduction total.

    Returns:
        Decimal -- annual state tax owed.

    Raises:
        ValueError: *state_config* is a flat-rate state's with no rate, or of
            a tax type no formula here prices.
    """
    if state_config is None:
        return ZERO
    if state_config.tax_type is TaxTypeEnum.NONE:
        return ZERO
    if state_config.tax_type is not TaxTypeEnum.FLAT:
        raise ValueError(
            f"no formula prices state tax type {state_config.tax_type.value}: "
            f"only a flat rate or no income tax is priced"
        )
    if state_config.flat_rate is None:
        raise ValueError(
            "a flat-rate state's rules state no rate, so its tax cannot be priced"
        )

    rate = Decimal(str(state_config.flat_rate))
    std_ded = Decimal(str(getattr(state_config, "standard_deduction", None) or 0))
    taxable = annual_gross - std_ded - Decimal(str(additional_deduction))
    taxable = max(taxable, ZERO)
    return round_money(taxable * rate)


def resolve_child_deduction_per_child(agi, tiers):
    """Return the per-child state child-deduction for the AGI tier.

    The NC child deduction (N.C.G.S. 105-153.5(a1)) is AGI-tiered: each tier
    (an :class:`app.tax_law.ChildDeductionTier`, or any object
    exposing ``agi_max`` and ``deduction_per_child``) applies to AGI in
    ``(agi_min, agi_max]``.  The statute reads "Up to $X" (inclusive) then
    "Over $X", so a threshold value belongs to the LOWER / more-generous
    tier; the matching tier is therefore the one with the SMALLEST ``agi_max``
    that is >= ``agi``.  The open-ended top tier (``agi_max is None``) catches
    everything above the last finite bound and returns its (typically $0)
    amount.  An empty ``tiers`` (a state with no child deduction, e.g. any
    non-NC state) returns ``ZERO``.

    Args:
        agi: The AGI to place (the NC base -- wages + 4(a) - pre-tax -- as a
            documented approximation of federal AGI).  Coerced from a string.
        tiers: Iterable of tier objects (``agi_max``, ``deduction_per_child``).

    Returns:
        Decimal -- the per-child deduction for the tier containing ``agi``
        (``ZERO`` when no tier matches / the list is empty).
    """
    agi = Decimal(str(agi))

    def _sort_key(tier):
        # Finite bounds ascending by value, the open-ended (None) tier last.
        if tier.agi_max is None:
            return (True, ZERO)
        return (False, Decimal(str(tier.agi_max)))

    for tier in sorted(tiers, key=_sort_key):
        if tier.agi_max is None or agi <= Decimal(str(tier.agi_max)):
            return Decimal(str(tier.deduction_per_child))
    return ZERO


# ── FICA ──────────────────────────────────────────────────────────


def capped_social_security(gross, cumulative_wages, fica_config):
    """Compute one period's Social Security tax with the wage-base cap enforced.

    Sole source of truth for SS arithmetic, which :func:`calculate_fica`
    delegates to so the IRS invariant -- a worker's yearly SS never exceeds
    `ss_wage_base * ss_rate` -- has one spelling.

    Per-period SS is `ss_rate * gross`, accrued until the cumulative SS
    collected reaches the statutory annual maximum, after which it is zero.
    Expressed as one clamp:

        statutory_max = ss_rate * ss_wage_base
        period_ss     = ss_rate * gross
        remaining     = statutory_max - ss_rate * cumulative_wages
        ss            = max(0, min(period_ss, remaining))

    which is the classic three-branch cap (cumulative >= base -> 0; crossing
    -> partial; under -> full `gross * ss_rate`), verified against the $312k
    worked example: period 16 -> $279.00, period 17 -> $0.00.

    **One rate since plan step salary:S11-c-2c.**  It took an ``ss_rate``
    override for the pay-stub calibration, which applied one stub's
    effective Social Security rate to every paycheck under the same cap.
    That path is deleted (ruling **R-SAL100**): a paycheck's Social Security
    is now its pay stub's figure corrected by the formulas, and the formulas
    always price at the statutory rate.

    Args:
        gross:            The Social Security wages for this pay period
                          (NOT annualized): the gross less the lines that
                          leave them (plan step salary:S11-e).
        cumulative_wages: Year-to-date Social Security wages BEFORE this
                          period.
        fica_config:      The year's :class:`app.tax_law.FicaRules`, with
                          `ss_rate` and `ss_wage_base`.  When None -- the
                          law resolved no FICA year at all -- returns ZERO,
                          mirroring `calculate_fica`'s None-fica handling.

    Returns:
        Decimal: SS tax for the period, quantised HALF_UP to two places.
    """
    if fica_config is None:
        return round_money(ZERO)

    gross = Decimal(str(gross))
    cumulative = Decimal(str(cumulative_wages))
    rate = Decimal(str(fica_config.ss_rate))
    ss_wage_base = Decimal(str(fica_config.ss_wage_base))

    statutory_max = rate * ss_wage_base
    period_ss = rate * gross
    remaining = statutory_max - rate * cumulative
    capped = max(min(period_ss, remaining), ZERO)
    return round_money(capped)


def calculate_fica(annual_gross, fica_config, cumulative_wages=ZERO):
    """Calculate FICA taxes (Social Security + Medicare) for a pay period.

    Handles the SS wage base cap and Medicare surtax threshold using
    cumulative wages to track year-to-date totals.  The SS portion is
    delegated to `capped_social_security`, the cap invariant's one spelling
    (F-037 / CRIT-03).

    Args:
        annual_gross:     The Social Security and Medicare wages for this pay
                          period (NOT annualized): the gross less the
                          lines that leave them (plan step salary:S11-e).
        fica_config:      The year's :class:`app.tax_law.FicaRules` (rates and thresholds).
        cumulative_wages: Year-to-date Social Security and Medicare wages
                          BEFORE this period.

    Returns:
        dict with keys: ss, medicare, total (all Decimal).
    """
    if fica_config is None:
        return {"ss": ZERO, "medicare": ZERO, "total": ZERO}

    gross = Decimal(str(annual_gross))
    cumulative = Decimal(str(cumulative_wages))
    medicare_rate = Decimal(str(fica_config.medicare_rate))
    surtax_rate = Decimal(str(fica_config.medicare_surtax_rate))
    surtax_threshold = Decimal(str(fica_config.medicare_surtax_threshold))

    ss_tax = capped_social_security(gross, cumulative, fica_config)

    # Medicare -- base rate on all income + surtax above threshold
    medicare_tax = round_money(gross * medicare_rate)

    if cumulative + gross > surtax_threshold:
        if cumulative >= surtax_threshold:
            surtax_income = gross
        else:
            surtax_income = (cumulative + gross) - surtax_threshold
        medicare_tax += round_money(surtax_income * surtax_rate)

    total = ss_tax + medicare_tax
    return {"ss": ss_tax, "medicare": medicare_tax, "total": total}
