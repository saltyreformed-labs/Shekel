"""A pre-tax deduction reduces income taxes but not FICA."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.paycheck_calculator import calculate_paycheck
from tests._test_helpers import payroll_basis

from ._helpers import (
    TWO_PLACES,
    _period,
    twenty_four_rule,
    FakeDeduction,
    FakeStateTaxConfig,
    FakeProfile,
)


# ── Pre-Tax Deduction Tax Impact Tests ─────────────────────────


class TestPreTaxDeductionTaxImpact:
    """Verify that pre-tax deductions reduce income taxes but NOT FICA.

    This is the core tax calculation invariant for U.S. payroll:
      - Federal income tax: computed on (gross - pre_tax - std_deduction)
      - State income tax: computed on (gross - pre_tax - state_std_deduction)
      - Social Security: computed on gross (NOT reduced by pre-tax deductions)
      - Medicare: computed on gross (NOT reduced by pre-tax deductions)

    Each test computes expected values by hand from first principles and
    compares against the calculator output. The hand calculations follow
    the IRS Pub 15-T Percentage Method pipeline exactly:
      1. Annualize gross: gross_biweekly * 26
      2. Subtract annualized pre-tax deductions
      3. Subtract standard deduction
      4. Apply marginal brackets
      5. De-annualize: annual_tax / 26

    All tests use the simple_bracket_set fixture (0-50k@10%, 50k+@22%,
    std_deduction=$15,000) and nc_state_config (NC 4.5% flat, no state
    standard deduction) unless otherwise noted.

    Baseline reference (no deductions, $60k salary, established by
    TestCalculatePaycheckPipeline.test_basic_paycheck_no_deductions):
      gross=2307.69, federal=173.08, state=103.85, SS=143.08,
      medicare=33.46, net=1854.22
    """

    def test_flat_pretax_deduction_reduces_federal_and_state(
        self, simple_tax_configs
    ):
        """$200/paycheck pre-tax 401(k) lowers federal and state taxes.

        This test catches the section 3.1 bug if it were to exist: taxes
        computed on gross instead of taxable income.

        Hand calculation:
          gross = 60000/26 = $2,307.69
          pre_tax = $200.00
          taxable_biweekly = 2307.69 - 200 = $2,107.69

          Federal (Pub 15-T):
            annual_income = 2307.69 * 26 = $59,999.94
            annual_pre_tax = 200 * 26 = $5,200.00
            adjusted = 59,999.94 - 5,200 = $54,799.94
            taxable = 54,799.94 - 15,000 = $39,799.94
            tax = 39,799.94 * 0.10 = $3,979.99  (all in 10% bracket)
            per_period = 3,979.99 / 26 = $153.08

          State:
            annual = 2,107.69 * 26 = $54,799.94
            tax = 54,799.94 * 0.045 = $2,466.00
            per_period = 2,466.00 / 26 = $94.85

        Without deduction: federal=173.08, state=103.85 (baseline).
        Reduction: federal drops by $20.00 (= $200 * 10% marginal rate),
                   state drops by $9.00 (= $200 * 4.5% flat rate).
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        with_ded = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Baseline comparison (from established test):
        baseline_federal = Decimal("173.08")
        baseline_state = Decimal("103.85")

        # Federal tax must be LOWER with pre-tax deduction.
        assert with_ded.taxes.federal == Decimal("153.08"), (
            f"federal_tax: expected 153.08, got {with_ded.taxes.federal}"
        )
        assert with_ded.taxes.federal < baseline_federal, (
            "Pre-tax deduction must reduce federal tax"
        )

        # State tax must be LOWER with pre-tax deduction.
        assert with_ded.taxes.state == Decimal("94.85"), (
            f"state_tax: expected 94.85, got {with_ded.taxes.state}"
        )
        assert with_ded.taxes.state < baseline_state, (
            "Pre-tax deduction must reduce state tax"
        )

        # Taxable income field must equal gross minus pre-tax.
        assert with_ded.earnings.taxable_income == Decimal("2107.69"), (
            f"taxable_income: expected 2107.69, "
            f"got {with_ded.earnings.taxable_income}"
        )

        # Verify the magnitude of tax reduction matches marginal rates.
        # $200 * 10% marginal bracket = $20.00/period federal reduction.
        assert baseline_federal - with_ded.taxes.federal == Decimal("20.00"), (
            f"Federal reduction should be $20.00 "
            f"(= $200 * 10% bracket rate), "
            f"got {baseline_federal - with_ded.taxes.federal}"
        )
        # $200 * 4.5% NC flat rate = $9.00/period state reduction.
        assert baseline_state - with_ded.taxes.state == Decimal("9.00"), (
            f"State reduction should be $9.00 "
            f"(= $200 * 4.5% flat rate), "
            f"got {baseline_state - with_ded.taxes.state}"
        )

    def test_pretax_deduction_does_not_reduce_fica(
        self, simple_tax_configs
    ):
        """FICA (SS + Medicare) must be computed on gross, NOT taxable income.

        Pre-tax 401(k) deductions reduce federal/state income tax bases but
        do NOT reduce FICA wages (IRC Section 3121 -- 401(k) contributions
        are subject to FICA). If this test fails, FICA is being incorrectly
        computed on taxable income instead of gross.

        Baseline FICA (no deductions):
          SS = 2307.69 * 0.062 = $143.08
          Medicare = 2307.69 * 0.0145 = $33.46

        With $200 pre-tax deduction: FICA must be IDENTICAL.
        """
        no_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            simple_tax_configs
        )

        # SS must be identical -- computed on gross, not taxable.
        assert with_ded.taxes.social_security == no_ded.taxes.social_security, (
            f"SS changed with pre-tax deduction: "
            f"{no_ded.taxes.social_security} -> {with_ded.taxes.social_security}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {with_ded.taxes.social_security}"
        )

        # Medicare must be identical -- computed on gross, not taxable.
        assert with_ded.taxes.medicare == no_ded.taxes.medicare, (
            f"Medicare changed with pre-tax deduction: "
            f"{no_ded.taxes.medicare} -> {with_ded.taxes.medicare}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {with_ded.taxes.medicare}"
        )

        # Gross must also be identical (deductions don't change gross).
        assert with_ded.earnings.gross_biweekly == no_ded.earnings.gross_biweekly

    def test_percentage_pretax_deduction_reduces_taxes(
        self, simple_tax_configs
    ):
        """6% percentage-based pre-tax 401(k) reduces income taxes correctly.

        Percentage deductions use calc_method='percentage' and are computed
        as a percentage of gross_biweekly. The resulting amount must reduce
        the tax base for income taxes but not FICA.

        Hand calculation:
          gross = 60000/26 = $2,307.69
          deduction = 2307.69 * 0.06 = $138.46
          taxable_biweekly = 2307.69 - 138.46 = $2,169.23

          Federal:
            annual_pre_tax = 138.46 * 26 = $3,599.96
            adjusted = 59,999.94 - 3,599.96 = $56,399.98
            taxable = 56,399.98 - 15,000 = $41,399.98
            tax = 41,399.98 * 0.10 = $4,140.00
            per_period = 4,140.00 / 26 = $159.23

          State:
            annual = 2,169.23 * 26 = $56,399.98
            tax = 56,399.98 * 0.045 = $2,538.00
            per_period = 2,538.00 / 26 = $97.62

          FICA: unchanged at SS=$143.08, Medicare=$33.46
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="0.06",
                    calc_method="percentage",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Deduction amount computed from gross.
        assert result.deductions.pre_tax[0].amount == Decimal("138.46"), (
            f"6% of 2307.69: expected 138.46, "
            f"got {result.deductions.pre_tax[0].amount}"
        )

        assert result.earnings.taxable_income == Decimal("2169.23"), (
            f"taxable_income: expected 2169.23, "
            f"got {result.earnings.taxable_income}"
        )

        assert result.taxes.federal == Decimal("159.23"), (
            f"federal_tax: expected 159.23, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("97.62"), (
            f"state_tax: expected 97.62, got {result.taxes.state}"
        )

        # FICA on gross -- unaffected by percentage deduction.
        assert result.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.taxes.medicare}"
        )

        # Net pay end-to-end.
        # 2307.69 - 138.46 - 159.23 - 97.62 - 143.08 - 33.46 = 1735.84
        assert result.earnings.net_pay == Decimal("1735.84"), (
            f"net_pay: expected 1735.84, got {result.earnings.net_pay}"
        )

    def test_third_paycheck_skipped_deduction_increases_taxes(
        self, simple_tax_configs
    ):
        """On a 3rd paycheck, 24/yr deductions are skipped, raising taxes.

        When a 24-per-year deduction (e.g., health insurance) is skipped on
        a 3rd paycheck, the pre-tax deduction total is lower, which means
        taxable income is higher, which means income taxes are higher. This
        is correct real-world payroll behavior.

        Setup: 3 periods in January (p1=Jan 2, p2=Jan 16, p3=Jan 30).
        p3 is the 3rd paycheck. $100/paycheck health insurance at 24/yr.

        On p2 (normal paycheck, deduction applies):
          pre_tax = $100
          annual_pre_tax = 100 * 26 = $2,600
          federal taxable = (59,999.94 - 2,600 - 15,000) = $42,399.94
          federal = 42,399.94 * 0.10 = $4,239.99 / 26 = $163.08
          state = (2207.69 * 26) * 0.045 = $2,583.00 / 26 = $99.35

        On p3 (3rd paycheck, deduction skipped):
          pre_tax = $0
          federal = $173.08 (same as no-deduction baseline)
          state = $103.85

        Tax increase on 3rd paycheck:
          federal: 173.08 - 163.08 = $10.00 (= $100 * 10% bracket)
          state: 103.85 - 99.35 = $4.50 (= $100 * 4.5% flat)
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="Health Insurance", amount="100",
                    recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        # 3 periods in January to trigger 3rd paycheck detection.
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        normal = calculate_paycheck(
            payroll_basis(profile, all_periods), p2,
            simple_tax_configs
        )
        third = calculate_paycheck(
            payroll_basis(profile, all_periods), p3,
            simple_tax_configs
        )

        # On normal paycheck, deduction applies.
        assert len(normal.deductions.pre_tax) == 1, (
            "Normal paycheck should have 1 pre-tax deduction"
        )
        assert normal.deductions.pre_tax[0].amount == Decimal("100.00")
        assert normal.taxes.federal == Decimal("163.08"), (
            f"Normal federal: expected 163.08, got {normal.taxes.federal}"
        )
        assert normal.taxes.state == Decimal("99.35"), (
            f"Normal state: expected 99.35, got {normal.taxes.state}"
        )

        # On 3rd paycheck, deduction is skipped.
        assert len(third.deductions.pre_tax) == 0, (
            "3rd paycheck should have 0 pre-tax deductions "
            "(24/yr deduction skipped)"
        )
        assert third.period.is_third_paycheck is True

        # 3rd paycheck taxes are HIGHER because deduction was skipped.
        assert third.taxes.federal == Decimal("173.08"), (
            f"3rd paycheck federal: expected 173.08, "
            f"got {third.taxes.federal}"
        )
        assert third.taxes.state == Decimal("103.85"), (
            f"3rd paycheck state: expected 103.85, "
            f"got {third.taxes.state}"
        )
        assert third.taxes.federal > normal.taxes.federal, (
            "3rd paycheck federal should be higher (deduction skipped)"
        )
        assert third.taxes.state > normal.taxes.state, (
            "3rd paycheck state should be higher (deduction skipped)"
        )

        # Tax increase exactly matches deduction * marginal rate.
        assert third.taxes.federal - normal.taxes.federal == Decimal("10.00"), (
            f"Federal increase: expected $10 "
            f"(= $100 * 10% bracket), "
            f"got {third.taxes.federal - normal.taxes.federal}"
        )
        assert third.taxes.state - normal.taxes.state == Decimal("4.50"), (
            f"State increase: expected $4.50 "
            f"(= $100 * 4.5% flat), "
            f"got {third.taxes.state - normal.taxes.state}"
        )

        # FICA identical on both (gross is the same).
        assert third.taxes.social_security == normal.taxes.social_security
        assert third.taxes.medicare == normal.taxes.medicare

    def test_multiple_pretax_deductions_stack(
        self, simple_tax_configs
    ):
        """Two pre-tax deductions ($200 + $100) stack to reduce taxes by $300.

        Hand calculation:
          gross = $2,307.69
          total_pre_tax = $300.00
          taxable_biweekly = 2307.69 - 300 = $2,007.69
          annual_pre_tax = 300 * 26 = $7,800

          Federal:
            adjusted = 59,999.94 - 7,800 = $52,199.94
            taxable = 52,199.94 - 15,000 = $37,199.94
            tax = 37,199.94 * 0.10 = $3,719.99 / 26 = $143.08

          State:
            annual = 2,007.69 * 26 = $52,199.94
            tax = 52,199.94 * 0.045 = $2,349.00 / 26 = $90.35

          Federal reduction from baseline: 173.08 - 143.08 = $30.00
            = $300 * 10% bracket rate
          State reduction from baseline: 103.85 - 90.35 = $13.50
            = $300 * 4.5% flat rate

          Net = 2307.69 - 300 - 143.08 - 90.35 - 143.08 - 33.46 = $1,597.72
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
                FakeDeduction(
                    name="Health", amount="100",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.deductions.total_pre_tax == Decimal("300.00"), (
            f"total_pre_tax: expected 300, got {result.deductions.total_pre_tax}"
        )
        assert result.earnings.taxable_income == Decimal("2007.69"), (
            f"taxable_income: expected 2007.69, "
            f"got {result.earnings.taxable_income}"
        )
        assert result.taxes.federal == Decimal("143.08"), (
            f"federal_tax: expected 143.08, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("90.35"), (
            f"state_tax: expected 90.35, got {result.taxes.state}"
        )

        # Reduction from baseline matches total deduction * marginal rate.
        assert Decimal("173.08") - result.taxes.federal == Decimal("30.00"), (
            "Federal reduction should be $30 = $300 * 10%"
        )
        assert Decimal("103.85") - result.taxes.state == Decimal("13.50"), (
            "State reduction should be $13.50 = $300 * 4.5%"
        )

        # FICA still on gross.
        assert result.taxes.social_security == Decimal("143.08")
        assert result.taxes.medicare == Decimal("33.46")

        # End-to-end net pay.
        assert result.earnings.net_pay == Decimal("1597.72"), (
            f"net_pay: expected 1597.72, got {result.earnings.net_pay}"
        )

    def test_post_tax_deduction_does_not_affect_any_tax(
        self, simple_tax_configs
    ):
        """Post-tax deductions (e.g., Roth IRA) must NOT change any tax amount.

        A post-tax deduction reduces net pay but has zero impact on federal,
        state, SS, or Medicare. If this test fails, post-tax deductions are
        leaking into the tax base calculation.

        All tax values must match the no-deduction baseline exactly:
          federal=173.08, state=103.85, SS=143.08, Medicare=33.46

        Net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46 - 200.00
            = $1,654.22
        """
        no_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        post_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="Roth IRA", amount="200",
                    paycheck_line_kind="post_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        post_ded = calculate_paycheck(
            payroll_basis(post_ded_profile, [period]), period,
            simple_tax_configs
        )

        # Every tax field must be identical.
        assert post_ded.taxes.federal == no_ded.taxes.federal == Decimal("173.08"), (
            f"Post-tax deduction changed federal: "
            f"{no_ded.taxes.federal} -> {post_ded.taxes.federal}"
        )
        assert post_ded.taxes.state == no_ded.taxes.state == Decimal("103.85"), (
            f"Post-tax deduction changed state: "
            f"{no_ded.taxes.state} -> {post_ded.taxes.state}"
        )
        assert post_ded.taxes.social_security == no_ded.taxes.social_security == Decimal("143.08"), (
            f"Post-tax deduction changed SS: "
            f"{no_ded.taxes.social_security} -> {post_ded.taxes.social_security}"
        )
        assert post_ded.taxes.medicare == no_ded.taxes.medicare == Decimal("33.46"), (
            f"Post-tax deduction changed Medicare: "
            f"{no_ded.taxes.medicare} -> {post_ded.taxes.medicare}"
        )

        # Taxable income must also be unchanged.
        assert post_ded.earnings.taxable_income == no_ded.earnings.taxable_income, (
            "Post-tax deduction should not affect taxable_income"
        )

        # Only net pay changes (reduced by $200 post-tax).
        assert post_ded.earnings.net_pay == Decimal("1654.22"), (
            f"net_pay: expected 1654.22, got {post_ded.earnings.net_pay}"
        )
        assert post_ded.earnings.net_pay == no_ded.earnings.net_pay - Decimal("200.00"), (
            "Net pay should decrease by exactly the post-tax amount"
        )

    def test_mixed_pre_and_post_tax_deductions(
        self, simple_tax_configs
    ):
        """Pre-tax and post-tax deductions interact correctly.

        $200 pre-tax 401(k) + $150 post-tax Roth IRA. Only the pre-tax
        deduction reduces the tax base. Post-tax is subtracted after taxes.

        Tax values should match the "$200 pre-tax only" scenario:
          federal=$153.08, state=$94.85, SS=$143.08, Medicare=$33.46

        Net = 2307.69 - 200.00 - 153.08 - 94.85 - 143.08 - 33.46 - 150.00
            = $1,533.22
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
                FakeDeduction(
                    name="Roth IRA", amount="150",
                    paycheck_line_kind="post_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Taxes match the $200 pre-tax scenario (post-tax has no effect).
        assert result.taxes.federal == Decimal("153.08"), (
            f"federal: expected 153.08, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("94.85"), (
            f"state: expected 94.85, got {result.taxes.state}"
        )
        assert result.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.taxes.medicare}"
        )

        # Both deduction types present in their respective lists.
        assert result.deductions.total_pre_tax == Decimal("200.00")
        assert result.deductions.total_post_tax == Decimal("150.00")

        # Net pay accounts for both deduction types.
        assert result.earnings.net_pay == Decimal("1533.22"), (
            f"net_pay: expected 1533.22, got {result.earnings.net_pay}"
        )

    def test_state_tax_with_standard_deduction_and_pretax(
        self, simple_bracket_set, standard_fica
    ):
        """State standard deduction and pre-tax deductions both reduce state tax.

        The state tax pipeline is: (gross - pre_tax) * 26 - state_std_ded,
        then multiply by flat rate. Both reductions must apply.

        Setup: NC 4.5% flat rate WITH $12,750 standard deduction.

        Without pre-tax deductions:
          annual = 2307.69 * 26 = $59,999.94
          state_taxable = 59,999.94 - 12,750 = $47,249.94
          tax = 47,249.94 * 0.045 = $2,126.25 / 26 = $81.78

        With $200 pre-tax deduction:
          annual = 2107.69 * 26 = $54,799.94
          state_taxable = 54,799.94 - 12,750 = $42,049.94
          tax = 42,049.94 * 0.045 = $1,892.25 / 26 = $72.78

        Reduction: 81.78 - 72.78 = $9.00 (= $200 * 4.5% flat rate)
        """
        # State config with standard deduction (not in default fixture).
        state_with_std_ded = FakeStateTaxConfig(flat_rate="0.045")
        state_with_std_ded.standard_deduction = Decimal("12750")

        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": state_with_std_ded,
            "fica_config": standard_fica,
        }

        no_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            configs
        )

        # Without deduction: state std ded reduces the base.
        assert no_ded.taxes.state == Decimal("81.78"), (
            f"No-deduction state: expected 81.78, "
            f"got {no_ded.taxes.state}"
        )

        # With deduction: both reductions apply.
        assert with_ded.taxes.state == Decimal("72.78"), (
            f"With-deduction state: expected 72.78, "
            f"got {with_ded.taxes.state}"
        )

        # Reduction matches deduction * flat rate.
        assert no_ded.taxes.state - with_ded.taxes.state == Decimal("9.00"), (
            f"State reduction: expected $9.00 "
            f"(= $200 * 4.5%), "
            f"got {no_ded.taxes.state - with_ded.taxes.state}"
        )

    def test_net_pay_end_to_end_with_pretax(
        self, simple_tax_configs
    ):
        """Full pipeline net pay with pre-tax deduction matches hand calc.

        This is the integration check: every component (gross, deductions,
        each tax type, net) is verified in one pass. If any upstream value
        is wrong, the net pay will not match.

        Net = gross - pre_tax - federal - state - SS - medicare - post_tax
            = 2307.69 - 200.00 - 153.08 - 94.85 - 143.08 - 33.46 - 0
            = $1,683.22
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        r = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Verify every component individually.
        assert r.earnings.gross_biweekly == Decimal("2307.69")
        assert r.deductions.total_pre_tax == Decimal("200.00")
        assert r.earnings.taxable_income == Decimal("2107.69")
        assert r.taxes.federal == Decimal("153.08")
        assert r.taxes.state == Decimal("94.85")
        assert r.taxes.social_security == Decimal("143.08")
        assert r.taxes.medicare == Decimal("33.46")
        assert r.deductions.total_post_tax == Decimal("0")

        # Verify net pay matches the formula.
        expected_net = (
            r.earnings.gross_biweekly
            - r.deductions.total_pre_tax
            - r.taxes.federal
            - r.taxes.state
            - r.taxes.social_security
            - r.taxes.medicare
            - r.deductions.total_post_tax
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        assert r.earnings.net_pay == expected_net, (
            f"net_pay {r.earnings.net_pay} != formula result {expected_net}"
        )
        assert r.earnings.net_pay == Decimal("1683.22"), (
            f"net_pay: expected 1683.22, got {r.earnings.net_pay}"
        )

    def test_pretax_deduction_in_higher_bracket_larger_reduction(
        self, simple_tax_configs
    ):
        """Pre-tax deduction in a higher bracket produces a larger tax savings.

        At $120k salary, the marginal bracket is 22% (income above $50k in
        the simple_bracket_set). A $500 pre-tax deduction at this income
        saves $500 * 22% = $110/period in federal tax, compared to
        $500 * 10% = $50/period at the $60k income level.

        This test verifies the calculator correctly applies marginal rates,
        not average rates, to the deduction amount.

        Without deduction ($120k):
          gross = 120000/26 = $4,615.38 (120000/26 = 4615.384615... -> 4615.38)
          annual = 4615.38 * 26 = $119,999.88
          taxable = 119,999.88 - 15,000 = $104,999.88
          tax: 50000*0.10 + 54999.88*0.22 = 5000 + 12099.97 = $17,099.97
          per_period = 17,099.97 / 26 = $657.69

        With $500 pre-tax deduction:
          annual_pre_tax = 500 * 26 = $13,000
          adjusted = 119,999.88 - 13,000 = $106,999.88
          taxable = 106,999.88 - 15,000 = $91,999.88
          tax: 50000*0.10 + 41999.88*0.22 = 5000 + 9239.97 = $14,239.97
          per_period = 14,239.97 / 26 = $547.69

        Reduction = 657.69 - 547.69 = $110.00 = $500 * 22%
        """
        no_ded_profile = FakeProfile(
            pay=Decimal("4615.38"), pay_from=date(2026, 1, 16),  # $120,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            pay=Decimal("4615.38"), pay_from=date(2026, 1, 16),  # $120,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="500",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            simple_tax_configs
        )

        assert no_ded.taxes.federal == Decimal("657.69"), (
            f"No-deduction federal: expected 657.69, "
            f"got {no_ded.taxes.federal}"
        )
        assert with_ded.taxes.federal == Decimal("547.69"), (
            f"With-deduction federal: expected 547.69, "
            f"got {with_ded.taxes.federal}"
        )

        # Reduction = $500 * 22% marginal bracket = $110.00
        reduction = no_ded.taxes.federal - with_ded.taxes.federal
        assert reduction == Decimal("110.00"), (
            f"Federal reduction: expected $110.00 "
            f"(= $500 * 22% marginal bracket), got {reduction}"
        )

        # FICA unchanged at higher income.
        assert with_ded.taxes.social_security == no_ded.taxes.social_security
        assert with_ded.taxes.medicare == no_ded.taxes.medicare
