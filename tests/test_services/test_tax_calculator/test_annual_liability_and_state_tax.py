"""The filing-time federal liability and its refundable Additional Child Tax Credit, and the state
tax: North Carolina's per-child deduction tiers, the additional deduction, the annual base and
the tax types priced.
"""

import pytest
from decimal import Decimal

from app.enums import TaxTypeEnum
from app.services.tax_calculator import (
    AnnualFederalTax,
    W4Inputs,
    calculate_annual_federal_liability,
    calculate_state_tax,
    resolve_child_deduction_per_child,
)
from app.services.exceptions import InvalidDependentCountError, InvalidFilingStatusError
from app.services.tax_config_service import StateTaxRules

from ._helpers import _FakeChildTier, _federal_2026_single, _FakeNCStateTaxRules, _federal_2026_mfj


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
            Decimal("99200.00"), _FakeNCStateTaxRules(),
            additional_deduction=Decimal("6000.00"),
        )
        without = calculate_state_tax(Decimal("99200.00"), _FakeNCStateTaxRules())
        assert with_ded == Decimal("3209.96"), (
            f"With 6,000 child deduction: expected 3209.96, got {with_ded}"
        )
        assert without - with_ded == Decimal("239.40")

    def test_additional_deduction_floors_taxable_at_zero(self):
        """A deduction larger than the base floors taxable (and tax) at zero."""
        result = calculate_state_tax(
            Decimal("20000.00"), _FakeNCStateTaxRules(),
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
        result = calculate_state_tax(Decimal("99200.00"), _FakeNCStateTaxRules())
        assert result == Decimal("3449.36"), (
            f"NC anchor state tax: expected 3449.36, got {result}"
        )

    def test_nc_base_below_standard_deduction_is_zero(self):
        """A base below the NC standard deduction yields zero state tax.

        base 10,000 - std ded 12,750 = -2,750 -> clamped -> 0.00.
        """
        result = calculate_state_tax(Decimal("10000.00"), _FakeNCStateTaxRules())
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
