"""North Carolina's statutory deduction tables, which the statute does not index by year.

The standard deduction by filing status (N.C.G.S. 105-153.5(a)(1)) and the
per-child deduction tiers (N.C.G.S. 105-153.5(a1), NCDOR D-401 Child Deduction
Table) are enacted amounts, so each tax year's law reads these ONE tables
rather than restating them; the year states only what does change, the flat
rate (N.C.G.S. 105-153.7(a)).  A future year whose statute amends either table
gets its own table beside these, and the years before it keep reading these.

**Graded 2026-11-10** (step plan#3207): no session law through S.L. 2026-62,
the latest ncleg.gov had posted, amends N.C.G.S. 105-153.5(a)(1) or (a1), and
the 2025 Form D-401 instructions print these same tables, band edges included.
Each year that reads them and has no D-401 instructions yet names the step that
re-checks them against its own when NCDOR posts them.

Married filing separately's standard deduction is the table's $12,750.  The
statute's zero for "a person who is not eligible for a standard deduction
under section 63 of the Code" is not modelled; for a separate filer that is
IRC sec. 63(c)(6)(A), "where either spouse itemizes deductions" (the D-401's
"If spouse claims itemized deductions", $0).
"""

from decimal import Decimal

from app.enums import FilingStatusEnum, TaxTypeEnum
from app.tax_law._types import ChildDeductionTier, StateYearLaw

STANDARD_DEDUCTION = {
    FilingStatusEnum.SINGLE: Decimal("12750.00"),
    FilingStatusEnum.MARRIED_JOINTLY: Decimal("25500.00"),
    FilingStatusEnum.MARRIED_SEPARATELY: Decimal("12750.00"),
    FilingStatusEnum.HEAD_OF_HOUSEHOLD: Decimal("19125.00"),
}

# The statute's table gives single and married-filing-separately filers rows
# of their own with the same figures (the D-401 prints them as one row), so
# they share one tuple here.
_SINGLE_OR_SEPARATE_TIERS = (
    ChildDeductionTier(Decimal("0.00"), Decimal("20000.00"), Decimal("3000.00")),
    ChildDeductionTier(Decimal("20000.00"), Decimal("30000.00"), Decimal("2500.00")),
    ChildDeductionTier(Decimal("30000.00"), Decimal("40000.00"), Decimal("2000.00")),
    ChildDeductionTier(Decimal("40000.00"), Decimal("50000.00"), Decimal("1500.00")),
    ChildDeductionTier(Decimal("50000.00"), Decimal("60000.00"), Decimal("1000.00")),
    ChildDeductionTier(Decimal("60000.00"), Decimal("70000.00"), Decimal("500.00")),
    ChildDeductionTier(Decimal("70000.00"), None, Decimal("0.00")),
)

CHILD_DEDUCTION_TIERS = {
    FilingStatusEnum.SINGLE: _SINGLE_OR_SEPARATE_TIERS,
    FilingStatusEnum.MARRIED_JOINTLY: (
        ChildDeductionTier(Decimal("0.00"), Decimal("40000.00"), Decimal("3000.00")),
        ChildDeductionTier(Decimal("40000.00"), Decimal("60000.00"), Decimal("2500.00")),
        ChildDeductionTier(Decimal("60000.00"), Decimal("80000.00"), Decimal("2000.00")),
        ChildDeductionTier(Decimal("80000.00"), Decimal("100000.00"), Decimal("1500.00")),
        ChildDeductionTier(Decimal("100000.00"), Decimal("120000.00"), Decimal("1000.00")),
        ChildDeductionTier(Decimal("120000.00"), Decimal("140000.00"), Decimal("500.00")),
        ChildDeductionTier(Decimal("140000.00"), None, Decimal("0.00")),
    ),
    FilingStatusEnum.MARRIED_SEPARATELY: _SINGLE_OR_SEPARATE_TIERS,
    FilingStatusEnum.HEAD_OF_HOUSEHOLD: (
        ChildDeductionTier(Decimal("0.00"), Decimal("30000.00"), Decimal("3000.00")),
        ChildDeductionTier(Decimal("30000.00"), Decimal("45000.00"), Decimal("2500.00")),
        ChildDeductionTier(Decimal("45000.00"), Decimal("60000.00"), Decimal("2000.00")),
        ChildDeductionTier(Decimal("60000.00"), Decimal("75000.00"), Decimal("1500.00")),
        ChildDeductionTier(Decimal("75000.00"), Decimal("90000.00"), Decimal("1000.00")),
        ChildDeductionTier(Decimal("90000.00"), Decimal("105000.00"), Decimal("500.00")),
        ChildDeductionTier(Decimal("105000.00"), None, Decimal("0.00")),
    ),
}


def state_year(flat_rate: str) -> StateYearLaw:
    """Return North Carolina's law for a year taxed at *flat_rate*.

    The one place a year's North Carolina entry is assembled: the year states
    its rate, and the statutory tables above supply the rest.

    Args:
        flat_rate: The year's flat rate as a decimal string, exactly as
            N.C.G.S. 105-153.7(a) states it for that year (``"0.0399"``).

    Returns:
        The year's :class:`~app.tax_law.StateYearLaw` for North Carolina.
    """
    return StateYearLaw(
        tax_type=TaxTypeEnum.FLAT,
        flat_rate=Decimal(flat_rate),
        standard_deduction=STANDARD_DEDUCTION,
        child_deduction_tiers=CHILD_DEDUCTION_TIERS,
    )
