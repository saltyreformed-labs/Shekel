"""The year's withholding figures (ruling plan#3213, plan step salary:X-at-7).

One set of federal rules, two formulas: a paycheck withholds on the figures the
year's withholding documents print (Pub 15-T's standard deduction, the W-4's
per-child credit), read through ``FederalRules.withholding_rules``, and the
filing-time liability prices on the return's.  The rules here are made up --
each stated withholding figure differs from the return's so that a formula
reading the wrong one cannot land on the right answer -- and are real
:class:`app.tax_law.FederalRules`, so the producer under test is the law's own.
"""

from decimal import Decimal

from app.services.tax_calculator import (
    W4Inputs,
    calculate_annual_federal_liability,
    calculate_federal_withholding,
)
from app.tax_law import FederalRules, ladder


def _rules_stating_withholding_figures():
    """Return made-up rules whose withholding figures differ from the return's.

    The return: standard deduction 15,000, child credit 2,200.  Withholding:
    14,000 and 2,000.  Both price on one ladder: 10% to 10,000, 12% to 40,000,
    22% above.
    """
    return FederalRules(
        standard_deduction=Decimal("15000.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "10000.00", "0.1000"),
            ("10000.00", "40000.00", "0.1200"),
            ("40000.00", None, "0.2200"),
        ),
        withholding_standard_deduction=Decimal("14000.00"),
        withholding_child_credit_amount=Decimal("2000.00"),
    )


class TestTheTwoFormulasReadTheirOwnFigures:
    """Withholding prices on the withholding figures; the liability on the return's."""

    def test_a_paycheck_withholds_on_the_withholding_figures(self):
        """2,000 a paycheck, 26 paychecks, one child.

          annual    = 2000 * 26 = 52,000.00
          taxable   = 52000 - 14000 (withholding's) = 38,000.00
          tax       = 10000*0.10 + (38000-10000)*0.12 = 1000.00 + 3360.00 = 4,360.00
          credits   = 1 * 2000 (withholding's) = 2,000.00
          per check = (4360 - 2000) / 26 = 2360 / 26 = 90.7692... -> 90.77
        On the return's figures it would be (4240 - 2200) / 26 = 78.46.
        """
        withheld = calculate_federal_withholding(
            gross_pay=Decimal("2000.00"),
            pay_periods=26,
            bracket_set=_rules_stating_withholding_figures(),
            w4=W4Inputs(qualifying_children=1),
        )
        assert withheld == Decimal("90.77")

    def test_the_liability_prices_on_the_returns_figures(self):
        """The same year's wages and child, at filing time.

          taxable   = 52000 - 15000 (the return's) = 37,000.00
          tax       = 10000*0.10 + (37000-10000)*0.12 = 1000.00 + 3240.00 = 4,240.00
          liability = 4240 - 1 * 2200 (the return's) = 2,040.00
          ACTC      = 0.00 (the 2,200 credit is used in full: 4,240 > 2,200)
        On the withholding figures it would be 4360 - 2000 = 2,360.00.
        """
        tax = calculate_annual_federal_liability(
            Decimal("52000.00"),
            _rules_stating_withholding_figures(),
            W4Inputs(qualifying_children=1),
        )
        assert tax.taxable == Decimal("37000.00")
        assert tax.liability == Decimal("2040.00")
        assert tax.refundable_actc == Decimal("0.00")

    def test_rules_stating_none_withhold_on_the_returns_figures(self):
        """No withholding figure stated: the paycheck withholds on the return's.

          taxable   = 52000 - 15000 = 37,000.00
          tax       = 4,240.00 (as above)
          per check = (4240 - 2200) / 26 = 2040 / 26 = 78.4615... -> 78.46
        """
        rules = _rules_stating_withholding_figures()
        plain = FederalRules(
            standard_deduction=rules.standard_deduction,
            child_credit_amount=rules.child_credit_amount,
            other_dependent_credit_amount=rules.other_dependent_credit_amount,
            child_credit_refundable_cap=rules.child_credit_refundable_cap,
            brackets=rules.brackets,
        )
        withheld = calculate_federal_withholding(
            gross_pay=Decimal("2000.00"),
            pay_periods=26,
            bracket_set=plain,
            w4=W4Inputs(qualifying_children=1),
        )
        assert withheld == Decimal("78.46")
