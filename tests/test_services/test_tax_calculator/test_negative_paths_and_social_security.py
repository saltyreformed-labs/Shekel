"""The calculator's negative and boundary paths across federal, state and FICA, and the Social
Security wage-base cap's one helper.
"""

from decimal import Decimal

from app.services.tax_calculator import (
    calculate_federal_withholding,
    calculate_state_tax,
    calculate_fica,
    capped_social_security,
    _apply_marginal_brackets,
)

from ._helpers import (
    FakeBracket,
    FakeFederalRules,
    _single_brackets,
    _FakeStateTaxRules,
    _FakeFicaRules,
    _FicaRules184500,
)


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
        bracket_set = FakeFederalRules(
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
        bracket_set = FakeFederalRules(brackets=_single_brackets())
        fed_wh = calculate_federal_withholding(
            gross_pay=Decimal("0"),
            pay_periods=26,
            bracket_set=bracket_set,
        )
        assert fed_wh == Decimal("0.00")

        # State: zero annual income
        state = calculate_state_tax(Decimal("0"), _FakeStateTaxRules())
        assert state == Decimal("0.00")

        # FICA: zero wages
        fica = calculate_fica(Decimal("0"), _FakeFicaRules(), Decimal("0"))
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
        bracket_set = FakeFederalRules(
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
            Decimal("0"), _FakeFicaRules(), Decimal("0")
        )

        assert result["ss"] == Decimal("0.00"), (
            f"SS: expected 0.00, got {result['ss']}"
        )
        assert result["medicare"] == Decimal("0.00"), (
            f"Medicare: expected 0.00, got {result['medicare']}"
        )


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
            _FicaRules184500(),
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
            _FicaRules184500(),
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
            _FicaRules184500(),
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
            _FicaRules184500(),
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
        cfg = _FicaRules184500()
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
            "12000.00", "48000.00", _FicaRules184500()
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
