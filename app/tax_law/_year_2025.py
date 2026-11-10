"""The 2025 tax law, transcribed from the documents :data:`SOURCES` names.

**2025's return and its paychecks price on different figures** (ruling
plan#3213, plan step salary:X-at-7).  P.L. 119-21 (signed 2025-07-04) raised
the year's standard deduction to $15,750 (single and married filing
separately), $31,500 (married filing jointly) and $23,625 (head of household)
-- Rev. Proc. 2025-32 sec. 3.01 -- and its child credit to $2,200 a child.
The IRS left the 2025 withholding documents as it had printed them before the
law (IR-2025-82).  Pub 15-T (2025) withholds on a standard deduction of
$15,000 single and married filing separately, $30,000 jointly and $22,500 head
of household: Worksheet 1A line 1g's $8,600 ($12,900 jointly) plus the annual
table's zero-rate row, $6,400 ($17,100 jointly, $13,900 head of household).
Form W-4 (2025) Step 3 claims $2,000 a qualifying child.  So each filing
status states its Pub 15-T deduction as its withholding figure, the year
states the W-4's child credit once beside its other per-child credits (rulings
plan#3340, plan#3342), and the difference -- withheld on the old figures, owed
on the new -- comes back in the refund the Taxes tab shows.  (Pub 15-T's
ladder for married filing separately is the single filer's, which the law
does not state: finding plan#3243.)

Until plan step salary:X-at-7 both priced on Rev. Proc. 2024-40's
pre-P.L. 119-21 deduction (finding **salary:SAL-576**): the Taxes tab
overstated 2025's tax by up to the $750 ($1,500 jointly, $1,125 head of
household) times the marginal rate -- the bracket each taxed dollar fell in,
and nothing where the tax was already zero after the credits -- and the
paychecks withheld on the return's $2,200 child credit rather than the W-4's
$2,000.
"""

from decimal import Decimal

from app.enums import FilingStatusEnum
from app.tax_law import _north_carolina
from app.tax_law._types import (
    FederalCredits,
    FederalRules,
    FicaRules,
    TaxYearLaw,
    ladder,
)

SOURCES = (
    "IRS Rev. Proc. 2024-40, sec. 3.01 (tax rate tables)",
    "IRS Rev. Proc. 2025-32, sec. 3.01 (standard deduction as P.L. 119-21 "
    "sec. 70102 amended it)",
    "P.L. 119-21 sec. 70104 (child tax credit $2,200 per child)",
    "IRS Pub 15-T (2025), Worksheet 1A line 1g and the Annual Percentage "
    "Method tables' STANDARD Withholding Rate Schedules (withholding "
    "standard deduction)",
    "Form W-4 (2025), Step 3 (withholding child credit $2,000 per qualifying "
    "child)",
    "2025 Schedule 8812 instructions (refundable child tax credit $1,700 per "
    "child)",
    "IRC sec. 24(h)(4) (credit for other dependents $500)",
    "SSA 2025 COLA fact sheet (Social Security wage base $176,100)",
    "IRC sec. 3101(a) and (b)(1) (Social Security 6.2%, Medicare 1.45%); "
    "sec. 3102(f) (Additional Medicare 0.9% withheld on wages over $200,000)",
    "N.C.G.S. 105-153.7(a) (North Carolina flat rate 4.25% for 2025)",
    "N.C.G.S. 105-153.5(a)(1) and (a1) (North Carolina standard and child "
    "deductions)",
)

_FEDERAL = {
    FilingStatusEnum.SINGLE: FederalRules(
        standard_deduction=Decimal("15750.00"),
        withholding_standard_deduction=Decimal("15000.00"),
        brackets=ladder(
            ("0.00", "11925.00", "0.1000"),
            ("11925.00", "48475.00", "0.1200"),
            ("48475.00", "103350.00", "0.2200"),
            ("103350.00", "197300.00", "0.2400"),
            ("197300.00", "250525.00", "0.3200"),
            ("250525.00", "626350.00", "0.3500"),
            ("626350.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_JOINTLY: FederalRules(
        standard_deduction=Decimal("31500.00"),
        withholding_standard_deduction=Decimal("30000.00"),
        brackets=ladder(
            ("0.00", "23850.00", "0.1000"),
            ("23850.00", "96950.00", "0.1200"),
            ("96950.00", "206700.00", "0.2200"),
            ("206700.00", "394600.00", "0.2400"),
            ("394600.00", "501050.00", "0.3200"),
            ("501050.00", "751600.00", "0.3500"),
            ("751600.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_SEPARATELY: FederalRules(
        standard_deduction=Decimal("15750.00"),
        withholding_standard_deduction=Decimal("15000.00"),
        brackets=ladder(
            ("0.00", "11925.00", "0.1000"),
            ("11925.00", "48475.00", "0.1200"),
            ("48475.00", "103350.00", "0.2200"),
            ("103350.00", "197300.00", "0.2400"),
            ("197300.00", "250525.00", "0.3200"),
            ("250525.00", "375800.00", "0.3500"),
            ("375800.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.HEAD_OF_HOUSEHOLD: FederalRules(
        standard_deduction=Decimal("23625.00"),
        withholding_standard_deduction=Decimal("22500.00"),
        brackets=ladder(
            ("0.00", "17000.00", "0.1000"),
            ("17000.00", "64850.00", "0.1200"),
            ("64850.00", "103350.00", "0.2200"),
            ("103350.00", "197300.00", "0.2400"),
            ("197300.00", "250500.00", "0.3200"),
            ("250500.00", "626350.00", "0.3500"),
            ("626350.00", None, "0.3700"),
        ),
    ),
}

_CREDITS = FederalCredits(
    child_credit_amount=Decimal("2200.00"),
    other_dependent_credit_amount=Decimal("500.00"),
    child_credit_refundable_cap=Decimal("1700.00"),
    withholding_child_credit_amount=Decimal("2000.00"),
)

_FICA = FicaRules(
    ss_rate=Decimal("0.0620"),
    ss_wage_base=Decimal("176100.00"),
    medicare_rate=Decimal("0.0145"),
    medicare_surtax_rate=Decimal("0.0090"),
    medicare_surtax_threshold=Decimal("200000.00"),
)

YEAR_2025 = TaxYearLaw(
    tax_year=2025,
    sources=SOURCES,
    federal=_FEDERAL,
    federal_credits=_CREDITS,
    fica=_FICA,
    states={"NC": _north_carolina.state_year("0.0425")},
)
