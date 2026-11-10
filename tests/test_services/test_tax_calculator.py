"""
Shekel Budget App -- Unit Tests for Federal Withholding Calculation

Tests the IRS Publication 15-T Percentage Method implementation in
tax_calculator.calculate_federal_withholding().

All bracket data used here is labeled as test fixtures and does not
represent actual IRS publications.
"""

import pytest
from decimal import Decimal

from app.enums import TaxTypeEnum
from app.services.tax_calculator import (
    AnnualFederalTax,
    W4Inputs,
    calculate_annual_federal_liability,
    calculate_federal_withholding,
    calculate_state_tax,
    calculate_fica,
    capped_social_security,
    marginal_rate_for,
    resolve_child_deduction_per_child,
    _apply_marginal_brackets,
)
from app.services.exceptions import (
    InvalidDependentCountError,
    InvalidGrossPayError,
    InvalidPayPeriodsError,
    InvalidFilingStatusError,
)
from app.services.tax_config_service import StateTaxRules


# ── Test Fixtures ─────────────────────────────────────────────────


class FakeBracket:
    """Minimal stand-in for a TaxBracket ORM object."""

    def __init__(self, min_income, max_income, rate, sort_order):
        self.min_income = min_income
        self.max_income = max_income
        self.rate = rate
        self.sort_order = sort_order


class _FakeChildTier:
    """Minimal stand-in for a StateChildDeduction tier row.

    ``resolve_child_deduction_per_child`` reads only ``agi_max`` and
    ``deduction_per_child`` (the lookup keys on ``agi_max``); ``agi_min`` is
    carried for parity with the real row.
    """

    def __init__(self, agi_min, agi_max, deduction_per_child):
        self.agi_min = agi_min
        self.agi_max = agi_max
        self.deduction_per_child = deduction_per_child


class FakeBracketSet:
    """Minimal stand-in for a TaxBracketSet ORM object.

    Bracket data below is for testing only and does not represent
    actual IRS bracket values.
    """

    def __init__(
        self,
        standard_deduction=Decimal("15000"),
        child_credit_amount=Decimal("2000"),
        other_dependent_credit_amount=Decimal("500"),
        brackets=None,
        child_credit_refundable_cap=Decimal("1700"),
    ):
        self.standard_deduction = standard_deduction
        self.child_credit_amount = child_credit_amount
        self.other_dependent_credit_amount = other_dependent_credit_amount
        self.child_credit_refundable_cap = child_credit_refundable_cap
        self.brackets = brackets or []


def _single_brackets():
    """Test fixture: simplified progressive bracket structure."""
    return [
        FakeBracket(Decimal("0"),      Decimal("10000"),  Decimal("0.10"), 0),
        FakeBracket(Decimal("10000"),  Decimal("40000"),  Decimal("0.12"), 1),
        FakeBracket(Decimal("40000"),  Decimal("85000"),  Decimal("0.22"), 2),
        FakeBracket(Decimal("85000"),  Decimal("160000"), Decimal("0.24"), 3),
        FakeBracket(Decimal("160000"), Decimal("210000"), Decimal("0.32"), 4),
        FakeBracket(Decimal("210000"), Decimal("540000"), Decimal("0.35"), 5),
        FakeBracket(Decimal("540000"), None,              Decimal("0.37"), 6),
    ]


@pytest.fixture
def single_bracket_set():
    """Single filer bracket set with $15,000 standard deduction."""
    return FakeBracketSet(
        standard_deduction=Decimal("15000"),
        child_credit_amount=Decimal("2000"),
        other_dependent_credit_amount=Decimal("500"),
        brackets=_single_brackets(),
    )


# ── Test 1: Zero Tax (credits exceed tax) ─────────────────────────


class TestZeroTaxScenario:
    """When credits exceed computed tax, withholding should be zero."""

    def test_credits_exceed_tax(self, single_bracket_set):
        """Test fixture: low income + 3 children = credits wipe out tax."""
        # Gross ~$30k/year => taxable ~$15k => tax ~$1,600
        # 3 children * $2,000 = $6,000 credit => tax zeroed out
        result = calculate_federal_withholding(
            gross_pay=Decimal("1153.85"),  # ~$30k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(qualifying_children=3),
        )
        assert result == Decimal("0.00")

    def test_income_below_standard_deduction(self, single_bracket_set):
        """Income below standard deduction yields zero tax."""
        # ~$14,300/year < $15,000 standard deduction
        result = calculate_federal_withholding(
            gross_pay=Decimal("550.00"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("0.00")

    def test_zero_gross_pay(self, single_bracket_set):
        """Zero gross pay yields zero withholding."""
        result = calculate_federal_withholding(
            gross_pay=Decimal("0"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("0.00")


# ── Test 2: Positive Tax Scenario ──────────────────────────────────


class TestPositiveTaxScenario:
    """Standard withholding scenarios that produce positive tax."""

    def test_basic_biweekly_withholding(self, single_bracket_set):
        """Test fixture: $60k salary, biweekly, no dependents."""
        # Annual: $60,000 - $15,000 std ded = $45,000 taxable
        # Bracket calc: $10,000 * 0.10 + $30,000 * 0.12 + $5,000 * 0.22
        #             = $1,000 + $3,600 + $1,100 = $5,700
        # Per period: $5,700 / 26 = $219.23 (rounded)
        result = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),  # $60k / 26
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("219.23")

    def test_weekly_pay_frequency(self, single_bracket_set):
        """$60k target salary at weekly pay frequency.

        Annualized: 1153.85 * 52 = $60,000.20 (not exactly $60k).
        Taxable: 60,000.20 - 15,000 = $45,000.20.
        Brackets: 10,000*0.10 + 30,000*0.12 + 5,000.20*0.22
                = 1,000 + 3,600 + 1,100.044 = $5,700.044.
        Quantized annual tax: $5,700.04.
        Per period: 5,700.04 / 52 = $109.62 (ROUND_HALF_UP).
        """
        result = calculate_federal_withholding(
            gross_pay=Decimal("1153.85"),
            pay_periods=52,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("109.62"), (
            f"Weekly withholding: expected 109.62, got {result}"
        )

    def test_monthly_pay_frequency(self, single_bracket_set):
        """Monthly pay frequency."""
        result = calculate_federal_withholding(
            gross_pay=Decimal("5000.00"),  # $60k / 12
            pay_periods=12,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("475.00")

    def test_extra_withholding_added(self, single_bracket_set):
        """W-4 Step 4(c) extra withholding adds to each period."""
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_extra = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(extra_withholding=Decimal("50.00")),
        )
        assert with_extra == base + Decimal("50.00")

    def test_additional_income_increases_tax(self, single_bracket_set):
        """W-4 Step 4(a) additional income raises withholding.

        Hand calculation (Commit 32 / MED-07 pinning of directional check):
          annual_income  = 2307.69 * 26 + 10000 = 69999.94
          taxable        = 69999.94 - 15000     = 54999.94
          brackets tax   = 10000*0.10 + 30000*0.12 + 14999.94*0.22
                         = 1000 + 3600 + 3299.9868 = 7899.9868
          per period     = 7899.9868 / 26 = 303.84564... -> 303.85 (HALF_UP)
        """
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_additional = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(additional_income=Decimal("10000")),
        )
        assert base == Decimal("219.23")
        assert with_additional == Decimal("303.85"), (
            f"Expected 303.85, got {with_additional}"
        )

    def test_additional_deductions_reduce_tax(self, single_bracket_set):
        """W-4 Step 4(b) additional deductions lower withholding.

        Hand calculation (Commit 32 / MED-07 pinning of directional check):
          annual_income  = 2307.69 * 26 = 59999.94
          adjusted       = 59999.94 - 5000 = 54999.94
          taxable        = 54999.94 - 15000 = 39999.94
          brackets tax   = 10000*0.10 + 29999.94*0.12
                         = 1000 + 3599.9928 = 4599.9928
          per period     = 4599.9928 / 26 = 176.92281... -> 176.92 (HALF_UP)
        """
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_deductions = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(additional_deductions=Decimal("5000")),
        )
        assert base == Decimal("219.23")
        assert with_deductions == Decimal("176.92"), (
            f"Expected 176.92, got {with_deductions}"
        )

    def test_pre_tax_deductions_reduce_tax(self, single_bracket_set):
        """Annualized pre-tax deductions (retirement, etc.) lower withholding.

        Hand calculation (Commit 32 / MED-07 pinning of directional check):
          annual_income  = 2307.69 * 26 = 59999.94
          adjusted       = 59999.94 - 6000 = 53999.94
          taxable        = 53999.94 - 15000 = 38999.94
          brackets tax   = 10000*0.10 + 28999.94*0.12
                         = 1000 + 3479.9928 = 4479.9928
          per period     = 4479.9928 / 26 = 172.30742... -> 172.31 (HALF_UP)
        """
        base = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        with_pretax = calculate_federal_withholding(
            gross_pay=Decimal("2307.69"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(pre_tax_deductions=Decimal("6000")),
        )
        assert base == Decimal("219.23")
        assert with_pretax == Decimal("172.31"), (
            f"Expected 172.31, got {with_pretax}"
        )


# ── Test 3: High Income (Multiple Brackets) ───────────────────────


class TestHighIncomeScenario:
    """High earners spanning many or all bracket tiers."""

    def test_income_spans_all_brackets(self, single_bracket_set):
        """$600k target salary spanning all seven brackets.

        Annualized: 23,076.92 * 26 = $599,999.92 (not exactly $600k).
        Taxable: 599,999.92 - 15,000 = $584,999.92.
        Brackets:
          $10,000.00 * 0.10 =  $1,000.00
          $30,000.00 * 0.12 =  $3,600.00
          $45,000.00 * 0.22 =  $9,900.00
          $75,000.00 * 0.24 = $18,000.00
          $50,000.00 * 0.32 = $16,000.00
         $330,000.00 * 0.35 = $115,500.00
          $44,999.92 * 0.37 = $16,649.9704
        Total: $180,649.9704 -> quantized $180,649.97.
        Per period: 180,649.97 / 26 = $6,948.08 (ROUND_HALF_UP).
        """
        result = calculate_federal_withholding(
            gross_pay=Decimal("23076.92"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("6948.08"), (
            f"All-bracket withholding: expected 6948.08, got {result}"
        )

    def test_very_high_income_top_bracket_only(self):
        """$1M target salary with simplified two-bracket system.

        Custom brackets: 0-100k @ 10%, 100k+ @ 37%.
        Annualized: 38,461.54 * 26 = $1,000,000.04.
        Taxable: 1,000,000.04 - 15,000 = $985,000.04.
        Brackets:
          $100,000.00 * 0.10 = $10,000.00
          $885,000.04 * 0.37 = $327,450.0148
        Total: $337,450.0148 -> quantized $337,450.01.
        Per period: 337,450.01 / 26 = $12,978.85 (ROUND_HALF_UP).
        """
        brackets = [
            FakeBracket(Decimal("0"), Decimal("100000"),
                        Decimal("0.10"), 0),
            FakeBracket(Decimal("100000"), None,
                        Decimal("0.37"), 1),
        ]
        bracket_set = FakeBracketSet(
            standard_deduction=Decimal("15000"),
            brackets=brackets,
        )
        result = calculate_federal_withholding(
            gross_pay=Decimal("38461.54"),
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert result == Decimal("12978.85"), (
            f"Two-bracket withholding: expected 12978.85, got {result}"
        )


# ── Test 4: Bracket Boundary Conditions ────────────────────────────


class TestBracketBoundary:
    """Income exactly at bracket thresholds."""

    def test_income_exactly_at_first_bracket_top(self, single_bracket_set):
        """Taxable income near the first bracket boundary.

        Annualized: 961.54 * 26 = $25,000.04 (not exactly $25k).
        Taxable: 25,000.04 - 15,000 = $10,000.04.
        Spills $0.04 into the 12% bracket:
          $10,000.00 * 0.10 = $1,000.00
          $0.04 * 0.12      = $0.0048
        Total: $1,000.0048 -> quantized $1,000.00.
        Per period: 1,000.00 / 26 = $38.46 (ROUND_HALF_UP).
        """
        result = calculate_federal_withholding(
            gross_pay=Decimal("961.54"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("38.46"), (
            f"First bracket top: expected 38.46, got {result}"
        )

    def test_income_one_dollar_into_next_bracket(self, single_bracket_set):
        """Taxable income slightly above first bracket boundary.

        Annualized: 961.58 * 26 = $25,001.08 (not exactly $25,001).
        Taxable: 25,001.08 - 15,000 = $10,001.08.
        Spills $1.08 into the 12% bracket:
          $10,000.00 * 0.10 = $1,000.00
          $1.08 * 0.12      = $0.1296
        Total: $1,000.1296 -> quantized $1,000.13.
        Per period: 1,000.13 / 26 = $38.47 (ROUND_HALF_UP).
        """
        result = calculate_federal_withholding(
            gross_pay=Decimal("961.58"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        assert result == Decimal("38.47"), (
            f"One dollar into next bracket: expected 38.47, got {result}"
        )

    def test_income_exactly_at_standard_deduction(self, single_bracket_set):
        """Income exactly equal to standard deduction = zero tax."""
        # $15,000 / 26 = $576.92
        result = calculate_federal_withholding(
            gross_pay=Decimal("576.92"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        # 576.92 * 26 = 14,999.92 < 15,000 std ded => zero
        assert result == Decimal("0.00")

    def test_single_bracket_no_remainder(self):
        """Flat tax: single bracket covering all income."""
        brackets = [
            FakeBracket(Decimal("0"), None, Decimal("0.15"), 0),
        ]
        bracket_set = FakeBracketSet(
            standard_deduction=Decimal("0"),
            brackets=brackets,
        )
        # $52,000/year, 26 periods => $2,000/period
        # Tax: $52,000 * 0.15 = $7,800 / 26 = $300.00
        result = calculate_federal_withholding(
            gross_pay=Decimal("2000"),
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert result == Decimal("300.00")


# ── Test 5: Invalid Input Rejection ────────────────────────────────


class TestInputValidation:
    """Defensive validation raises domain-specific exceptions."""

    def test_negative_gross_pay(self, single_bracket_set):
        with pytest.raises(InvalidGrossPayError):
            calculate_federal_withholding(
                gross_pay=Decimal("-100"),
                pay_periods=26,
                bracket_set=single_bracket_set,
            )

    def test_zero_pay_periods(self, single_bracket_set):
        with pytest.raises(InvalidPayPeriodsError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=0,
                bracket_set=single_bracket_set,
            )

    def test_negative_pay_periods(self, single_bracket_set):
        with pytest.raises(InvalidPayPeriodsError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=-1,
                bracket_set=single_bracket_set,
            )

    def test_none_bracket_set(self):
        with pytest.raises(InvalidFilingStatusError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=26,
                bracket_set=None,
            )

    def test_negative_qualifying_children(self, single_bracket_set):
        with pytest.raises(InvalidDependentCountError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=26,
                bracket_set=single_bracket_set,
                w4=W4Inputs(qualifying_children=-1),
            )

    def test_negative_other_dependents(self, single_bracket_set):
        with pytest.raises(InvalidDependentCountError):
            calculate_federal_withholding(
                gross_pay=Decimal("2000"),
                pay_periods=26,
                bracket_set=single_bracket_set,
                w4=W4Inputs(other_dependents=-2),
            )


# ── Test: Dependent Credits ────────────────────────────────────────


class TestDependentCredits:
    """W-4 Step 3 credit calculations."""

    def test_child_credits_reduce_tax(self, single_bracket_set):
        """Two qualifying children reduce per-period withholding.

        Annualized: 3,846.15 * 26 = $99,999.90.
        Taxable: 99,999.90 - 15,000 = $84,999.90.
        Brackets:
          $10,000.00 * 0.10 = $1,000.00
          $30,000.00 * 0.12 = $3,600.00
          $44,999.90 * 0.22 = $9,899.978
        Annual tax before credits: $14,499.978 -> quantized
        $14,499.98.
        no_kids: 14,499.98 / 26 = $557.69.
        two_kids: credits = 2 * $2,000 = $4,000.
          Annual after credits: 14,499.98 - 4,000 = $10,499.98.
          Per period: 10,499.98 / 26 = $403.85.
        Diff: 557.69 - 403.85 = $153.84.
        """
        no_kids = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        two_kids = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(qualifying_children=2),
        )
        assert no_kids == Decimal("557.69"), (
            f"No-kids withholding: expected 557.69, got {no_kids}"
        )
        assert two_kids == Decimal("403.85"), (
            f"Two-kids withholding: expected 403.85, got {two_kids}"
        )
        diff = no_kids - two_kids
        assert diff == Decimal("153.84"), (
            f"Child credit diff: expected 153.84, got {diff}"
        )

    def test_other_dependent_credits(self, single_bracket_set):
        """Two other dependents reduce per-period withholding.

        Same base income as test_child_credits_reduce_tax:
        Annual tax before credits: $14,499.98.
        no_deps: 14,499.98 / 26 = $557.69.
        two_other: credits = 2 * $500 = $1,000.
          Annual after credits: 14,499.98 - 1,000 = $13,499.98.
          Per period: 13,499.98 / 26 = $519.23.
        Diff: 557.69 - 519.23 = $38.46.
        """
        no_deps = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        two_other = calculate_federal_withholding(
            gross_pay=Decimal("3846.15"),
            pay_periods=26,
            bracket_set=single_bracket_set,
            w4=W4Inputs(other_dependents=2),
        )
        assert no_deps == Decimal("557.69"), (
            f"No-deps withholding: expected 557.69, got {no_deps}"
        )
        assert two_other == Decimal("519.23"), (
            f"Two-other withholding: expected 519.23, got {two_other}"
        )
        diff = no_deps - two_other
        assert diff == Decimal("38.46"), (
            f"Other dependent diff: expected 38.46, got {diff}"
        )


# ── Test: _apply_marginal_brackets (internal helper) ───────────────


class TestApplyMarginalBrackets:
    """Direct tests for the bracket iteration logic."""

    def test_empty_brackets(self):
        result = _apply_marginal_brackets(Decimal("50000"), [])
        assert result == Decimal("0")

    def test_zero_taxable_income(self):
        result = _apply_marginal_brackets(Decimal("0"), _single_brackets())
        assert result == Decimal("0")

    def test_negative_taxable_income(self):
        result = _apply_marginal_brackets(Decimal("-1000"), _single_brackets())
        assert result == Decimal("0")


class TestMarginalRateFor:
    """The marginal-rate chip helper, boundary-pinned against the ladder.

    Uses the file's ``_single_brackets()`` test ladder:
      10% (0, 10000] | 12% (10000, 40000] | 22% (40000, 85000] |
      24% (85000, 160000] | 32% (160000, 210000] | 35% (210000, 540000] |
      37% (540000, inf).
    The rule is "the bracket that CONTAINS taxable, an exact edge belonging
    to the LOWER bracket" -- the same gate ``_apply_marginal_brackets`` taxes
    from (``taxable > min_income``), so the chip and the tax math agree.
    """

    def test_interior_value(self):
        """50,000 sits inside (40,000, 85,000] -> 22%."""
        assert marginal_rate_for(Decimal("50000"), _single_brackets()) == (
            Decimal("0.22")
        )

    def test_exact_edge_belongs_to_lower_bracket(self):
        """40,000 is the 12%/22% edge -> LOWER 12% (22% opens above 40,000)."""
        assert marginal_rate_for(Decimal("40000"), _single_brackets()) == (
            Decimal("0.12")
        )

    def test_one_dollar_above_edge_is_upper_bracket(self):
        """40,001 crosses into (40,000, 85,000] -> 22%."""
        assert marginal_rate_for(Decimal("40001"), _single_brackets()) == (
            Decimal("0.22")
        )

    def test_first_bracket_edge_belongs_to_lowest(self):
        """10,000 is the 10%/12% edge -> LOWER 10%."""
        assert marginal_rate_for(Decimal("10000"), _single_brackets()) == (
            Decimal("0.10")
        )

    def test_top_open_bracket(self):
        """600,000 is above the last edge (540,000) -> top 37%."""
        assert marginal_rate_for(Decimal("600000"), _single_brackets()) == (
            Decimal("0.37")
        )

    def test_top_bracket_edge_belongs_to_lower(self):
        """540,000 is the 35%/37% edge -> LOWER 35% (37% opens above)."""
        assert marginal_rate_for(Decimal("540000"), _single_brackets()) == (
            Decimal("0.35")
        )

    def test_zero_taxable_returns_lowest_rate(self):
        """Taxable 0 (income absorbed by the std deduction) -> lowest 10%.

        No bracket sits strictly below 0, so the first positive taxable
        dollar's rate is the honest chip.
        """
        assert marginal_rate_for(Decimal("0"), _single_brackets()) == (
            Decimal("0.10")
        )

    def test_empty_brackets_returns_zero(self):
        """No bracket structure -> ZERO (the degenerate seed)."""
        assert marginal_rate_for(Decimal("50000"), []) == Decimal("0")

    def test_unsorted_brackets_are_ordered_by_sort_order(self):
        """The helper sorts by sort_order, so input order does not matter."""
        shuffled = list(reversed(_single_brackets()))
        assert marginal_rate_for(Decimal("120000"), shuffled) == Decimal("0.24")


# ── Test: Annual Consistency ─────────────────────────────────────


class TestAnnualConsistency:
    """Verify withholding sums to annual tax over a full year."""

    def test_26_period_annual_withholding_matches_annual_tax(
        self, single_bracket_set
    ):
        """Biweekly withholding * 26 approximates annual tax.

        Salary: $78,000.00, gross_biweekly = $3,000.00.
        Verify: 3,000.00 * 26 = $78,000.00 exactly.
        Taxable: 78,000 - 15,000 = $63,000.
        Brackets:
          $10,000 * 0.10 = $1,000.00
          $30,000 * 0.12 = $3,600.00
          $23,000 * 0.22 = $5,060.00
        Annual tax: $9,660.00.
        Per period: 9,660.00 / 26 = 371.538... -> $371.54.
        Annual via withholding: 371.54 * 26 = $9,660.04.
        Rounding discrepancy: $0.04 (within 26 * $0.01).
        """
        annual_salary = Decimal("78000.00")
        gross_biweekly = Decimal("3000.00")
        per_period = calculate_federal_withholding(
            gross_pay=gross_biweekly,
            pay_periods=26,
            bracket_set=single_bracket_set,
        )
        # Annual tax via the live engine: subtract std deduction then
        # apply marginal brackets directly (LOW-01: legacy wrapper deleted).
        taxable_annual = annual_salary - single_bracket_set.standard_deduction
        annual_tax = _apply_marginal_brackets(
            taxable_annual, single_bracket_set.brackets
        )
        assert per_period == Decimal("371.54"), (
            f"Per-period withholding: expected 371.54, "
            f"got {per_period}"
        )
        assert annual_tax == Decimal("9660.00"), (
            f"Annual tax: expected 9660.00, got {annual_tax}"
        )
        annual_via_withholding = per_period * 26
        # 371.54 * 26 = 9660.04
        assert annual_via_withholding == Decimal("9660.04"), (
            f"Annual via withholding: expected 9660.04, "
            f"got {annual_via_withholding}"
        )
        # Rounding discrepancy: 9660.04 - 9660.00 = 0.04
        # Expected: 26 periods x up to $0.01 rounding each.
        assert annual_via_withholding - annual_tax == Decimal("0.04"), (
            f"Rounding discrepancy: expected 0.04, "
            f"got {annual_via_withholding - annual_tax}"
        )

    def test_annual_pay_period_no_rounding_loss(
        self, single_bracket_set
    ):
        """Annual pay (1 period/year): withholding equals annual tax.

        With pay_periods=1, there is no de-annualize/re-annualize
        rounding because the per-period amount IS the annual amount.
        Salary: $78,000.00. Taxable: $63,000.
        Brackets: 10,000*0.10 + 30,000*0.12 + 23,000*0.22
                = 1,000 + 3,600 + 5,060 = $9,660.00.
        """
        annual_salary = Decimal("78000.00")
        per_period = calculate_federal_withholding(
            gross_pay=annual_salary,
            pay_periods=1,
            bracket_set=single_bracket_set,
        )
        # Annual tax via the live engine: subtract std deduction then
        # apply marginal brackets directly (LOW-01: legacy wrapper deleted).
        taxable_annual = annual_salary - single_bracket_set.standard_deduction
        annual_tax = _apply_marginal_brackets(
            taxable_annual, single_bracket_set.brackets
        )
        assert per_period == Decimal("9660.00"), (
            f"Annual withholding: expected 9660.00, "
            f"got {per_period}"
        )
        assert per_period == annual_tax, (
            f"Annual withholding {per_period} "
            f"!= annual tax {annual_tax}"
        )


# ── Negative-Path and Boundary-Condition Tests ─────────────────────


class _FakeStateTaxConfig:
    """Minimal state tax config for negative path tests."""

    def __init__(self, flat_rate="0.045"):
        self.flat_rate = Decimal(str(flat_rate))
        self.tax_type = TaxTypeEnum.FLAT


class _FakeFicaConfig:
    """Minimal FICA config for negative path tests."""

    def __init__(self):
        self.ss_rate = Decimal("0.062")
        self.ss_wage_base = Decimal("168600")
        self.medicare_rate = Decimal("0.0145")
        self.medicare_surtax_rate = Decimal("0.009")
        self.medicare_surtax_threshold = Decimal("200000")


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for the tax calculator.

    Verifies behavior at bracket boundaries, with zero/negative inputs,
    and with edge-case pay frequencies.
    """

    def test_annual_pay_single_period_no_rounding_loss(self):
        """With pay_periods=1, per-period withholding equals annual tax exactly.

        Input: pay_periods=1, gross_pay=78000, standard_deduction=15000.
        Taxable: 78000 - 15000 = 63000.
        Tax: 50000*0.10 + 13000*0.22 = 5000 + 2860 = 7860.00.
        Expected: withholding = 7860.00 (no division rounding since periods=1).
        Why: This is the cleanest validation that the tax engine's per-period
        conversion is correct.
        """
        # Simple 2-bracket set: 0-50000@10%, 50000+@22%
        brackets = [
            FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
            FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
        ]
        bracket_set = FakeBracketSet(
            standard_deduction=Decimal("15000"),
            brackets=brackets,
        )

        result = calculate_federal_withholding(
            gross_pay=Decimal("78000"),
            pay_periods=1,
            bracket_set=bracket_set,
        )

        # taxable = 78000 - 15000 = 63000
        # 50000*0.10 + 13000*0.22 = 5000 + 2860 = 7860.00
        # per_period = 7860 / 1 = 7860.00 (exact, no rounding loss)
        assert result == Decimal("7860.00"), (
            f"Annual withholding: expected 7860.00, got {result}"
        )

    def test_zero_taxable_income(self):
        """Zero taxable income must produce zero tax in all components.

        Input: taxable_income=0 for federal brackets, gross=0 for state/FICA.
        Expected: All tax components are Decimal("0.00").
        Why: Zero income must not produce negative taxes, NaN, or crash
        in bracket iteration.
        """
        # Federal brackets: zero taxable income
        fed_result = _apply_marginal_brackets(Decimal("0"), _single_brackets())
        assert fed_result == Decimal("0.00")

        # Federal withholding pipeline: zero gross
        bracket_set = FakeBracketSet(brackets=_single_brackets())
        fed_wh = calculate_federal_withholding(
            gross_pay=Decimal("0"),
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert fed_wh == Decimal("0.00")

        # State: zero annual income
        state = calculate_state_tax(Decimal("0"), _FakeStateTaxConfig())
        assert state == Decimal("0.00")

        # FICA: zero wages
        fica = calculate_fica(Decimal("0"), _FakeFicaConfig(), Decimal("0"))
        assert fica["ss"] == Decimal("0.00")
        assert fica["medicare"] == Decimal("0.00")

    def test_negative_taxable_income_after_deductions(self):
        """Negative taxable income (standard deduction > gross) produces zero tax.

        Input: taxable_income=-5000 passed directly to _apply_marginal_brackets.
        Expected: returns Decimal("0.00"). The function guards with
        'if taxable_income <= ZERO: return ZERO'.
        Why: If standard_deduction > gross_income, taxable income is negative.
        The bracket loop must not produce negative tax or iterate incorrectly.
        """
        # Direct negative input to bracket logic
        # Source clamps: if taxable_income <= ZERO: return ZERO
        result = _apply_marginal_brackets(Decimal("-5000"), _single_brackets())
        assert result == Decimal("0.00"), (
            f"Negative taxable income should produce zero tax, got {result}"
        )

        # Via the full withholding pipeline: income below standard deduction
        bracket_set = FakeBracketSet(
            standard_deduction=Decimal("15000"),
            brackets=_single_brackets(),
        )
        # 500*26 = 13000 < 15000 std ded → taxable=0 → tax=0
        result_pipeline = calculate_federal_withholding(
            gross_pay=Decimal("500"),
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert result_pipeline == Decimal("0.00")

    def test_income_exactly_at_bracket_boundary(self):
        """Income at the first bracket boundary stays entirely in that bracket.

        Input: taxable_income=50000 with boundary at 50000
        (brackets: 0-50000@10%, 50000+@22%).
        Expected: tax = 50000*0.10 = 5000.00. No amount spills into 22% bracket.
        Why: Off-by-one at bracket boundaries is a classic tax calculation bug.
        """
        brackets = [
            FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
            FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
        ]

        result = _apply_marginal_brackets(Decimal("50000"), brackets)

        # 50000 * 0.10 = 5000.00 (all in first bracket)
        # Second bracket: taxable(50000) <= bracket_min(50000) → break
        assert result == Decimal("5000.00"), (
            f"At boundary: expected 5000.00, got {result}. "
            f"No income should spill into the 22% bracket."
        )

    def test_fica_zero_wages(self):
        """Zero wages must produce zero FICA taxes.

        Input: gross=0, cumulative_wages=0.
        Expected: SS=Decimal("0.00"), Medicare=Decimal("0.00").
        Why: Zero wages must not produce negative taxes or crash.
        """
        result = calculate_fica(
            Decimal("0"), _FakeFicaConfig(), Decimal("0")
        )

        assert result["ss"] == Decimal("0.00"), (
            f"SS: expected 0.00, got {result['ss']}"
        )
        assert result["medicare"] == Decimal("0.00"), (
            f"Medicare: expected 0.00, got {result['medicare']}"
        )


# ── capped_social_security helper (CRIT-03 / F-037) ───────────────


class _FicaConfig184500:
    """Fixed FicaConfig stub at the 2026-seed wage base.

    capped_social_security only reads `ss_rate` and `ss_wage_base`.
    Medicare attributes are populated because test_calculate_fica_delegates_to_helper
    exercises the full calculate_fica pipeline (which reads them) to prove
    the bracket path also routes through the helper.
    """

    def __init__(self):
        self.ss_rate = Decimal("0.062")
        self.ss_wage_base = Decimal("184500")
        self.medicare_rate = Decimal("0.0145")
        self.medicare_surtax_rate = Decimal("0.009")
        self.medicare_surtax_threshold = Decimal("200000")


class TestCappedSocialSecurityHelper:
    """Direct tests for capped_social_security.

    Verifies the helper's three branches against hand-computed cap
    arithmetic.  The helper is the single source of truth for SS
    (CRIT-03 / F-037; its calibration caller was deleted at plan step
    salary:S11-c-2c); these tests pin each branch independently of
    ``calculate_fica`` so a regression here surfaces before the broader
    pipeline tests.
    """

    def test_branch_under_cap_full_rate(self):
        """cumul + gross < ss_wage_base -> full statutory rate.

        $12,000 gross, $48,000 cumul; $60,000 << $184,500.
        12000 * 0.062 = 744.00.
        """
        result = capped_social_security(
            Decimal("12000.00"),
            Decimal("48000.00"),
            _FicaConfig184500(),
        )
        assert result == Decimal("744.00"), (
            f"Under-cap SS must be 744.00, got {result}"
        )

    def test_branch_partial_at_crossing(self):
        """cumul + gross > ss_wage_base, cumul < ss_wage_base -> partial.

        cumul = 180000, gross = 12000; cumul + gross = 192000 > 184500.
        ss_taxable = 184500 - 180000 = 4500.00.
        ss = 4500.00 * 0.062 = 279.00.
        """
        result = capped_social_security(
            Decimal("12000.00"),
            Decimal("180000.00"),
            _FicaConfig184500(),
        )
        assert result == Decimal("279.00"), (
            f"Partial-crossing SS must be 279.00, got {result}"
        )

    def test_branch_at_cap_zero(self):
        """cumul == ss_wage_base -> ZERO (boundary inclusive).

        Equality at the cap zeros the period; the IRS cap is inclusive.
        """
        result = capped_social_security(
            Decimal("12000.00"),
            Decimal("184500.00"),
            _FicaConfig184500(),
        )
        assert result == Decimal("0.00"), (
            f"At-cap SS must be 0.00, got {result}"
        )

    def test_branch_above_cap_zero(self):
        """cumul > ss_wage_base -> ZERO.

        cumul = 192000 > 184500 -> SS = 0.00.
        """
        result = capped_social_security(
            Decimal("12000.00"),
            Decimal("192000.00"),
            _FicaConfig184500(),
        )
        assert result == Decimal("0.00"), (
            f"Above-cap SS must be 0.00, got {result}"
        )

    def test_calculate_fica_delegates_to_helper(self):
        """C18-4: calculate_fica's SS line equals capped_social_security exactly.

        Both produce identical Decimal pennies for the same inputs.  This
        is the "single source of truth" lock: if the bracket path ever
        reintroduces its own copy of the cap arithmetic, this assertion
        fails because the rounding boundaries diverge.
        """
        cfg = _FicaConfig184500()
        # Sample the partial-crossing branch where rounding is most fragile.
        gross = Decimal("12000.00")
        cumul = Decimal("180000.00")

        helper_ss = capped_social_security(gross, cumul, cfg)
        fica_ss = calculate_fica(gross, cfg, cumul)["ss"]

        assert helper_ss == fica_ss == Decimal("279.00"), (
            f"calculate_fica SS ({fica_ss}) must equal helper ({helper_ss})"
        )

    def test_string_inputs_accepted(self):
        """String gross/cumulative are coerced to Decimal -- mirrors callers."""
        result = capped_social_security(
            "12000.00", "48000.00", _FicaConfig184500()
        )
        assert result == Decimal("744.00")

    def test_none_fica_config_returns_zero(self):
        """`fica_config=None` returns ZERO, mirroring `calculate_fica`.

        `calculate_fica` already returns all-ZERO when fica_config is None
        (paycheck-projection contract for profiles without a seeded FICA
        config -- e.g. early bootstrap, unit tests that omit the FICA
        seed).  The helper must preserve this contract so a direct caller
        agrees with ``calculate_fica`` on missing config.
        """
        result = capped_social_security(
            Decimal("12000.00"), Decimal("0"), None
        )
        assert result == Decimal("0.00"), (
            f"None fica_config must yield ZERO, got {result}"
        )


# ── Annual Filing-Time Federal Liability (T-P1) ────────────────────


def _federal_2026_single():
    """The 2026 single filer's federal rules, as ``app.tax_law`` states them.

    Mirrors the 2026 law's single-filer rules (``DEFAULT_FEDERAL_BRACKETS
    [2026]["single"]`` in the ``tax_seed_data`` seed until plan step
    salary:X-at-1) so the annual liability arithmetic is hand-confirmed
    against the app's own figures (std deduction 16,100; CTC 2,200 per
    OBBBA; ODC 500; refundable ACTC cap 1,700).
    """
    return FakeBracketSet(
        standard_deduction=Decimal("16100"),
        child_credit_amount=Decimal("2200"),
        other_dependent_credit_amount=Decimal("500"),
        child_credit_refundable_cap=Decimal("1700"),
        brackets=[
            FakeBracket(Decimal("0"),      Decimal("12400"),  Decimal("0.10"), 0),
            FakeBracket(Decimal("12400"),  Decimal("50400"),  Decimal("0.12"), 1),
            FakeBracket(Decimal("50400"),  Decimal("105700"), Decimal("0.22"), 2),
            FakeBracket(Decimal("105700"), Decimal("201775"), Decimal("0.24"), 3),
            FakeBracket(Decimal("201775"), Decimal("256225"), Decimal("0.32"), 4),
            FakeBracket(Decimal("256225"), Decimal("640600"), Decimal("0.35"), 5),
            FakeBracket(Decimal("640600"), None,              Decimal("0.37"), 6),
        ],
    )


class _FakeNCStateConfig:
    """The seeded 2026 NC flat state config (rate 3.99%, std ded 12,750)."""

    def __init__(self, flat_rate="0.0399", standard_deduction="12750"):
        self.flat_rate = Decimal(flat_rate)
        self.standard_deduction = Decimal(standard_deduction)
        self.tax_type = TaxTypeEnum.FLAT


class TestAnnualFederalLiability:
    """calculate_annual_federal_liability: filing-time federal tax (T-P1).

    Applies the seeded bracket ladder ANNUALLY to
    (wages + 4(a) - pre-tax - standard deduction), minus nonrefundable
    CTC/ODC.  Every expected number is hand-computed in the docstring.
    """

    def test_worked_anchor(self):
        """Developer's locked anchor: single, NC, 2026 configs.

        wages 110,000; pre-tax 12,000; 4(a) 1,200; no dependents.
          taxable   = 110000 + 1200 - 12000 - 16100 = 83,100.00
          brackets  = 12400*0.10 + (50400-12400)*0.12 + (83100-50400)*0.22
                    = 1240.00 + 4560.00 + 7194.00 = 12,994.00
          credits   = 0  ->  liability = 12,994.00
        """
        result = calculate_annual_federal_liability(
            Decimal("110000.00"),
            _federal_2026_single(),
            W4Inputs(
                additional_income=Decimal("1200.00"),
                pre_tax_deductions=Decimal("12000.00"),
            ),
        )
        assert isinstance(result, AnnualFederalTax)
        assert result.taxable == Decimal("83100.00"), (
            f"Anchor taxable: expected 83100.00, got {result.taxable}"
        )
        assert result.liability == Decimal("12994.00"), (
            f"Anchor liability: expected 12994.00, got {result.liability}"
        )

    def test_step_4a_additional_income_is_real_income(self):
        """W-4 Step 4(a) income raises taxable and liability (real income).

        Same anchor without the 1,200 of 4(a):
          taxable   = 110000 - 12000 - 16100 = 81,900.00
          brackets  = 1240 + 4560 + (81900-50400)*0.22
                    = 1240 + 4560 + 6930.00 = 12,730.00
        The 1,200 of 4(a) sits entirely in the 22% band:
          1200 * 0.22 = 264.00  ->  12730.00 + 264.00 = 12,994.00 (anchor).
        """
        without_4a = calculate_annual_federal_liability(
            Decimal("110000.00"),
            _federal_2026_single(),
            W4Inputs(pre_tax_deductions=Decimal("12000.00")),
        )
        with_4a = calculate_annual_federal_liability(
            Decimal("110000.00"),
            _federal_2026_single(),
            W4Inputs(
                additional_income=Decimal("1200.00"),
                pre_tax_deductions=Decimal("12000.00"),
            ),
        )
        assert without_4a.taxable == Decimal("81900.00")
        assert without_4a.liability == Decimal("12730.00")
        assert with_4a.liability == Decimal("12994.00")
        assert with_4a.liability - without_4a.liability == Decimal("264.00"), (
            "4(a) income of 1,200 in the 22% band must add 264.00 of tax"
        )

    def test_step_4b_additional_deductions_excluded(self):
        """W-4 Step 4(b) deductions do NOT change liability (the ruling lock).

        Two W-4s differing ONLY in additional_deductions (0 vs 3,000, the
        ruling's exact anchor 4(b) value) must yield identical taxable and
        liability -- 4(b) is a withholding-only hint, excluded at filing.
        """
        base_w4 = W4Inputs(
            additional_income=Decimal("1200.00"),
            pre_tax_deductions=Decimal("12000.00"),
        )
        with_4b = W4Inputs(
            additional_income=Decimal("1200.00"),
            pre_tax_deductions=Decimal("12000.00"),
            additional_deductions=Decimal("3000.00"),
        )
        no_4b = calculate_annual_federal_liability(
            Decimal("110000.00"), _federal_2026_single(), base_w4,
        )
        yes_4b = calculate_annual_federal_liability(
            Decimal("110000.00"), _federal_2026_single(), with_4b,
        )
        assert no_4b == yes_4b, (
            "4(b) additional deductions must not affect filing liability"
        )
        assert yes_4b.liability == Decimal("12994.00")

    def test_bracket_boundary_exactly_at_50400(self):
        """Taxable exactly at the 12%/22% edge stays out of the 22% band.

        wages 66,500; no 4(a); no pre-tax:
          taxable  = 66500 - 16100 = 50,400.00 (the bracket edge)
          brackets = 12400*0.10 + (50400-12400)*0.12
                   = 1240.00 + 4560.00 = 5,800.00  (nothing spills into 22%)
        """
        result = calculate_annual_federal_liability(
            Decimal("66500.00"), _federal_2026_single(),
        )
        assert result.taxable == Decimal("50400.00")
        assert result.liability == Decimal("5800.00"), (
            f"At bracket edge: expected 5800.00, got {result.liability}"
        )

    def test_ctc_and_odc_credits_applied(self):
        """2 children + 1 other dependent = 4,900 nonrefundable credit (CTC 2,200).

        Anchor before credits is 12,994.00 (see test_worked_anchor).
          credit    = 2*2200 + 1*500 = 4,900.00  (CTC $2,200 per OBBBA)
          liability = 12994.00 - 4900.00 = 8,094.00
        The credit is fully absorbed (12,994 > 4,900), so no ACTC:
          unused = max(0, 4900 - 12994) = 0 -> refundable_actc = 0.00.
        """
        result = calculate_annual_federal_liability(
            Decimal("110000.00"),
            _federal_2026_single(),
            W4Inputs(
                additional_income=Decimal("1200.00"),
                pre_tax_deductions=Decimal("12000.00"),
                qualifying_children=2,
                other_dependents=1,
            ),
        )
        assert result.taxable == Decimal("83100.00")
        assert result.liability == Decimal("8094.00"), (
            f"After 4,900 credit: expected 8094.00, got {result.liability}"
        )
        assert result.refundable_actc == Decimal("0.00"), (
            f"Fully-absorbed credit yields no ACTC, got {result.refundable_actc}"
        )

    def test_credits_clamp_liability_at_zero(self):
        """Credits exceeding the tax clamp liability at zero (nonrefundable).

        wages 25,000; no 4(a); no pre-tax; 1 child:
          taxable   = 25000 - 16100 = 8,900.00
          brackets  = 8900 * 0.10 = 890.00
          credit    = 1 * 2200 = 2,200.00  (exceeds 890.00, CTC per OBBBA)
          liability = max(0, 890.00 - 2200.00) = 0.00 (not -1,310.00)
        The unused credit spills into the refundable ACTC (unused-limited):
          unused = max(0, 2200 - 890)          = 1,310.00
          cap    = 1700 * 1                     = 1,700.00
          earned = 0.15 * (25000 - 2500)        = 3,375.00
          ACTC   = min(1310, 1700, 3375)        = 1,310.00
        """
        result = calculate_annual_federal_liability(
            Decimal("25000.00"),
            _federal_2026_single(),
            W4Inputs(qualifying_children=1),
        )
        assert result.taxable == Decimal("8900.00")
        assert result.liability == Decimal("0.00"), (
            f"Nonrefundable clamp: expected 0.00, got {result.liability}"
        )
        assert result.refundable_actc == Decimal("1310.00"), (
            f"Unused-credit-limited ACTC: expected 1310.00, "
            f"got {result.refundable_actc}"
        )

    def test_taxable_clamps_at_zero_when_deductions_exceed_income(self):
        """Pre-tax + standard deduction exceeding income -> zero taxable/tax.

        wages 10,000; pre-tax 5,000; std ded 16,100:
          10000 - 5000 - 16100 = -11,100 -> clamped to 0 -> liability 0.00
        """
        result = calculate_annual_federal_liability(
            Decimal("10000.00"),
            _federal_2026_single(),
            W4Inputs(pre_tax_deductions=Decimal("5000.00")),
        )
        assert result.taxable == Decimal("0"), (
            f"Taxable must clamp to 0, got {result.taxable}"
        )
        assert result.liability == Decimal("0"), (
            f"Liability must clamp to 0, got {result.liability}"
        )

    def test_none_bracket_set_raises(self):
        """A None bracket set raises InvalidFilingStatusError (engine contract)."""
        with pytest.raises(InvalidFilingStatusError):
            calculate_annual_federal_liability(Decimal("110000.00"), None)

    def test_negative_qualifying_children_raises(self):
        """Negative dependent counts raise, matching the withholding engine."""
        with pytest.raises(InvalidDependentCountError):
            calculate_annual_federal_liability(
                Decimal("110000.00"),
                _federal_2026_single(),
                W4Inputs(qualifying_children=-1),
            )

    def test_negative_other_dependents_raises(self):
        """Negative other-dependent count raises InvalidDependentCountError."""
        with pytest.raises(InvalidDependentCountError):
            calculate_annual_federal_liability(
                Decimal("110000.00"),
                _federal_2026_single(),
                W4Inputs(other_dependents=-3),
            )


def _federal_2026_mfj():
    """The 2026 married-filing-jointly federal rules, as ``app.tax_law`` states them.

    Mirrors the 2026 law's married-filing-jointly rules
    (``DEFAULT_FEDERAL_BRACKETS[2026]["married_jointly"]`` in the
    ``tax_seed_data`` seed until plan step salary:X-at-1): std
    deduction 32,200; CTC 2,200; ODC 500; refundable ACTC cap 1,700.
    """
    return FakeBracketSet(
        standard_deduction=Decimal("32200"),
        child_credit_amount=Decimal("2200"),
        other_dependent_credit_amount=Decimal("500"),
        child_credit_refundable_cap=Decimal("1700"),
        brackets=[
            FakeBracket(Decimal("0"),      Decimal("24800"),  Decimal("0.10"), 0),
            FakeBracket(Decimal("24800"),  Decimal("100800"), Decimal("0.12"), 1),
            FakeBracket(Decimal("100800"), Decimal("211400"), Decimal("0.22"), 2),
            FakeBracket(Decimal("211400"), Decimal("403550"), Decimal("0.24"), 3),
            FakeBracket(Decimal("403550"), Decimal("512450"), Decimal("0.32"), 4),
            FakeBracket(Decimal("512450"), Decimal("768700"), Decimal("0.35"), 5),
            FakeBracket(Decimal("768700"), None,              Decimal("0.37"), 6),
        ],
    )


class TestRefundableACTC:
    """Refundable Additional Child Tax Credit (T-P5), each leg pinned.

        ACTC = min(unused_credit, cap x children, 15% x (earned - 2,500))
        unused_credit = max(0, total_dependent_credits - tax_before_credits)

    Every leg is exercised as the binding constraint, plus the developer's
    live-shape anchor and the zero-children degenerate case.
    """

    def test_developer_live_shape_anchor_unused_limited(self):
        """Developer's live data: 2026 MFJ, 4 children, wages 94,619.62.

        wages 94,619.62; pre-tax 13,943.93; no 4(a); std ded 32,200:
          taxable  = 94619.62 - 13943.93 - 32200 = 48,475.69
          brackets = 24800*0.10 + (48475.69-24800)*0.12
                   = 2480.00 + 2841.0828 = 5321.0828 -> 5,321.08
          credits  = 4 * 2200 = 8,800.00  (fully absorbs the 5,321.08 tax)
          liability = max(0, 5321.08 - 8800) = 0.00
        Refundable ACTC (unused-credit-limited):
          unused = max(0, 8800 - 5321.08)      = 3,478.92
          cap    = 1700 * 4                     = 6,800.00
          earned = 0.15 * (94619.62 - 2500)     = 0.15 * 92119.62 = 13,817.943
          ACTC   = min(3478.92, 6800, 13817.94) = 3,478.92
        """
        result = calculate_annual_federal_liability(
            Decimal("94619.62"),
            _federal_2026_mfj(),
            W4Inputs(
                pre_tax_deductions=Decimal("13943.93"),
                qualifying_children=4,
            ),
        )
        assert result.taxable == Decimal("48475.69")
        assert result.liability == Decimal("0.00")
        assert result.refundable_actc == Decimal("3478.92"), (
            f"Unused-limited ACTC: expected 3478.92, got {result.refundable_actc}"
        )

    def test_cap_limited(self):
        """Cap leg binds: unused and earned both exceed 4 x 1,700 = 6,800.

        2026 MFJ, 4 children, wages 50,000, no pre-tax:
          taxable  = 50000 - 32200 = 17,800.00  (all in the 10% band)
          tax      = 17800 * 0.10 = 1,780.00  ->  liability 0 (credit 8,800)
          unused   = max(0, 8800 - 1780) = 7,020.00
          cap      = 1700 * 4            = 6,800.00
          earned   = 0.15 * (50000-2500) = 7,125.00
          ACTC     = min(7020, 6800, 7125) = 6,800.00  (cap-limited)
        """
        result = calculate_annual_federal_liability(
            Decimal("50000.00"),
            _federal_2026_mfj(),
            W4Inputs(qualifying_children=4),
        )
        assert result.liability == Decimal("0.00")
        assert result.refundable_actc == Decimal("6800.00"), (
            f"Cap-limited ACTC: expected 6800.00, got {result.refundable_actc}"
        )

    def test_earned_income_limited(self):
        """Earned-income leg binds: 15% of (earned - 2,500) is the smallest.

        2026 MFJ, 4 children, wages 10,000 (below std ded), no pre-tax:
          taxable  = max(0, 10000 - 32200) = 0.00  ->  tax 0, liability 0
          unused   = max(0, 8800 - 0)      = 8,800.00
          cap      = 1700 * 4              = 6,800.00
          earned   = 0.15 * (10000 - 2500) = 0.15 * 7500 = 1,125.00
          ACTC     = min(8800, 6800, 1125) = 1,125.00  (earned-limited)
        """
        result = calculate_annual_federal_liability(
            Decimal("10000.00"),
            _federal_2026_mfj(),
            W4Inputs(qualifying_children=4),
        )
        assert result.taxable == Decimal("0")
        assert result.liability == Decimal("0.00")
        assert result.refundable_actc == Decimal("1125.00"), (
            f"Earned-limited ACTC: expected 1125.00, got {result.refundable_actc}"
        )

    def test_zero_children_no_actc(self):
        """No qualifying children -> zero cap leg -> zero ACTC.

        2026 MFJ, 0 children, wages 10,000 (below std ded):
          total credits = 0 -> unused = max(0, 0 - 0) = 0
          cap           = 1700 * 0 = 0
          ACTC          = min(0, 0, ...) = 0.00
        """
        result = calculate_annual_federal_liability(
            Decimal("10000.00"),
            _federal_2026_mfj(),
            W4Inputs(qualifying_children=0),
        )
        assert result.refundable_actc == Decimal("0.00"), (
            f"Zero-children ACTC must be 0.00, got {result.refundable_actc}"
        )

    def test_actc_zero_when_credit_fully_used_by_high_tax(self):
        """High tax fully uses the credit -> unused 0 -> no ACTC.

        2026 MFJ, 2 children, wages 200,000, no pre-tax:
          taxable  = 200000 - 32200 = 167,800.00
          tax      = 24800*0.10 + (100800-24800)*0.12 + (167800-100800)*0.22
                   = 2480 + 9120 + 14740 = 26,340.00
          credits  = 2 * 2200 = 4,400.00 (fully used; 26,340 > 4,400)
          liability = 26340 - 4400 = 21,940.00
          unused   = max(0, 4400 - 26340) = 0 -> ACTC 0.00
        """
        result = calculate_annual_federal_liability(
            Decimal("200000.00"),
            _federal_2026_mfj(),
            W4Inputs(qualifying_children=2),
        )
        assert result.liability == Decimal("21940.00")
        assert result.refundable_actc == Decimal("0.00")


class TestResolveChildDeductionPerChild:
    """resolve_child_deduction_per_child: NC AGI-tiered per-child lookup.

    Uses the seeded NC MFJ tiers (Up to 40k -> 3,000; 40k-60k -> 2,500;
    ...; over 140k -> 0), pinning the inclusive-upper boundary rule where a
    threshold value belongs to the LOWER / more-generous tier.
    """

    @staticmethod
    def _nc_mfj_tiers():
        """The seeded NC MFJ child-deduction tiers as fake tier objects."""
        raw = [
            (0, 40000, 3000), (40000, 60000, 2500), (60000, 80000, 2000),
            (80000, 100000, 1500), (100000, 120000, 1000),
            (120000, 140000, 500), (140000, None, 0),
        ]
        return [
            _FakeChildTier(
                Decimal(str(lo)),
                Decimal(str(hi)) if hi is not None else None,
                Decimal(str(amt)),
            )
            for lo, hi, amt in raw
        ]

    def test_interior_value(self):
        """AGI 80,675.69 sits in (80,000, 100,000] -> 1,500/child."""
        assert resolve_child_deduction_per_child(
            Decimal("80675.69"), self._nc_mfj_tiers(),
        ) == Decimal("1500")

    def test_threshold_belongs_to_lower_tier(self):
        """AGI exactly 40,000 is "Up to 40,000" (inclusive) -> 3,000, not 2,500."""
        assert resolve_child_deduction_per_child(
            Decimal("40000"), self._nc_mfj_tiers(),
        ) == Decimal("3000")

    def test_one_cent_over_threshold_is_next_tier(self):
        """AGI 40,000.01 is "Over 40,000" -> 2,500/child."""
        assert resolve_child_deduction_per_child(
            Decimal("40000.01"), self._nc_mfj_tiers(),
        ) == Decimal("2500")

    def test_above_top_tier_is_zero(self):
        """AGI over 140,000 falls in the open-ended $0 tier."""
        assert resolve_child_deduction_per_child(
            Decimal("250000"), self._nc_mfj_tiers(),
        ) == Decimal("0")

    def test_zero_agi_first_tier(self):
        """AGI 0 is in the first tier (Up to 40,000) -> 3,000/child."""
        assert resolve_child_deduction_per_child(
            Decimal("0"), self._nc_mfj_tiers(),
        ) == Decimal("3000")

    def test_empty_tiers_returns_zero(self):
        """A state with no child-deduction tiers resolves to 0/child."""
        assert resolve_child_deduction_per_child(Decimal("50000"), []) == (
            Decimal("0")
        )


class TestStateTaxAdditionalDeduction:
    """calculate_state_tax honours the additional (child) deduction (T-P5)."""

    def test_additional_deduction_reduces_taxable(self):
        """A child-deduction total is subtracted before the flat rate.

        base 99,200; NC std ded 12,750; child deduction 6,000:
          taxable = 99200 - 12750 - 6000 = 80,450.00
          tax     = 80450 * 0.0399 = 3,209.955 -> 3,209.96 (ROUND_HALF_UP)
        Without the child deduction the tax is 3,449.36 (the anchor), so the
        6,000 deduction lowers NC tax by 239.40.
        """
        with_ded = calculate_state_tax(
            Decimal("99200.00"), _FakeNCStateConfig(),
            additional_deduction=Decimal("6000.00"),
        )
        without = calculate_state_tax(Decimal("99200.00"), _FakeNCStateConfig())
        assert with_ded == Decimal("3209.96"), (
            f"With 6,000 child deduction: expected 3209.96, got {with_ded}"
        )
        assert without - with_ded == Decimal("239.40")

    def test_additional_deduction_floors_taxable_at_zero(self):
        """A deduction larger than the base floors taxable (and tax) at zero."""
        result = calculate_state_tax(
            Decimal("20000.00"), _FakeNCStateConfig(),
            additional_deduction=Decimal("30000.00"),
        )
        assert result == Decimal("0.00")


class TestAnnualStateBase:
    """calculate_state_tax on the NC annual base (the T-P1 state layer).

    The liability service computes the NC base = wages + 4(a) - pre-tax
    (clamped) and delegates to calculate_state_tax, which subtracts the NC
    standard deduction internally.  These pin the flat-rate std-deduction
    branch the anchor depends on.
    """

    def test_nc_anchor_base_rounds_half_up(self):
        """NC 2026 anchor: base 99,200 -> (99200-12750)*3.99% = 3,449.36.

        (99200 - 12750) * 0.0399 = 86450 * 0.0399 = 3,449.3550 exactly;
        ROUND_HALF_UP at the half-cent (the discarded .0050) rounds the
        second decimal up: 3,449.35 -> 3,449.36.
        """
        result = calculate_state_tax(Decimal("99200.00"), _FakeNCStateConfig())
        assert result == Decimal("3449.36"), (
            f"NC anchor state tax: expected 3449.36, got {result}"
        )

    def test_nc_base_below_standard_deduction_is_zero(self):
        """A base below the NC standard deduction yields zero state tax.

        base 10,000 - std ded 12,750 = -2,750 -> clamped -> 0.00.
        """
        result = calculate_state_tax(Decimal("10000.00"), _FakeNCStateConfig())
        assert result == Decimal("0.00")


class TestStateTaxTypes:
    """calculate_state_tax prices two tax types and refuses the rest (plan step salary:X-at-3).

    Plan step salary:X-at-3: the engine refuses "a flat state with no rate".
    Until X-at-3 a flat-rate config with no rate, or a type no formula
    prices, fell through to $0.00 -- the silent figure finding SAL-575 names.
    Each case hands the formula the resolver's own value,
    :class:`~app.services.tax_config_service.StateTaxRules`.
    """

    @staticmethod
    def _rules(tax_type, flat_rate):
        """A made-up state's rules for one filing status: no deduction, no child tiers."""
        return StateTaxRules(
            state_code="ZZ",
            tax_type=TaxTypeEnum[tax_type],
            flat_rate=flat_rate,
            standard_deduction=Decimal("0.00"),
            child_deduction_tiers=(),
        )

    def test_a_state_with_no_income_tax_prices_zero(self):
        """The explicit $0.00 entry: no tax on $78,000.00."""
        assert calculate_state_tax(
            Decimal("78000.00"), self._rules("NONE", None),
        ) == Decimal("0")

    def test_a_flat_state_prices_its_rate(self):
        """5% flat, no deduction: 78,000.00 x 0.05 = 3,900.00."""
        assert calculate_state_tax(
            Decimal("78000.00"), self._rules("FLAT", Decimal("0.0500")),
        ) == Decimal("3900.00")

    def test_a_flat_state_with_no_rate_is_refused(self):
        """Not $0.00: the rate is what prices a flat state, so its absence is refused."""
        with pytest.raises(
            ValueError,
            match="a flat-rate state's rules state no rate, so its tax cannot be priced",
        ):
            calculate_state_tax(Decimal("78000.00"), self._rules("FLAT", None))

    def test_a_bracket_state_is_refused_rather_than_priced_as_flat(self):
        """No formula prices a state's ladder; a rate beside one is not priced as flat.

        Until X-at-3 a bracket-type config carrying a rate took the flat
        branch (``if state_config.flat_rate``) and priced 3,900.00 here.
        """
        rules = self._rules("BRACKET", Decimal("0.0500"))
        with pytest.raises(
            ValueError,
            match=(
                r"no formula prices state tax type bracket: "
                r"only a flat rate or no income tax is priced"
            ),
        ):
            calculate_state_tax(Decimal("78000.00"), rules)
