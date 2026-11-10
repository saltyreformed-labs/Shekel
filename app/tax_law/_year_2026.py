"""The 2026 tax law, transcribed from the documents :data:`SOURCES` names.

**The head-of-household 24% / 32% boundary is $201,750** (Rev. Proc. 2025-32
sec. 4.01 Table 2), and Pub 15-T (2026)'s head-of-household annual table agrees
($217,300 less its $15,550 zero-rate row).  From the app's first seeding until
plan step salary:X-at-7 the ladder carried $201,775, the single filer's figure,
which taxed up to $25 of a head of household's income at 24% that the law
taxes at 32%: $2.00 a year once taxable income passes $201,775 (finding
**salary:SAL-577**).

**2026 states no withholding figure** (ruling plan#3213): Pub 15-T (2026)
withholds on the return's standard deduction -- Worksheet 1A line 1g's $8,600
($12,900 jointly) plus the annual table's zero-rate row, $7,500 ($19,300
jointly, $15,550 head of household) -- and Form W-4 (2026) Step 3 claims the
return's $2,200 a qualifying child, so a 2026 paycheck withholds on the
figures below.  (Pub 15-T's ladder for married filing separately is the
single filer's, which the law does not state: finding plan#3243.)

North Carolina's standard deduction is the enacted, unindexed statutory amount;
re-verify it against the 2026 Form D-401 instructions when NCDOR posts them.
"""

from decimal import Decimal

from app.enums import FilingStatusEnum
from app.tax_law import _north_carolina
from app.tax_law._types import (
    FederalRules,
    FicaRules,
    TaxYearLaw,
    ladder,
)

SOURCES = (
    "IRS Rev. Proc. 2025-32, sec. 4.01 (tax rate tables), sec. 4.05 (child "
    "tax credit $2,200; refundable $1,700 per child) and sec. 4.14 (standard "
    "deduction)",
    "IRC sec. 24(h)(4) (credit for other dependents $500)",
    "IRS Pub 15-T (2026), Worksheet 1A line 1g and the Annual Percentage "
    "Method tables' STANDARD Withholding Rate Schedules (the withholding "
    "standard deduction, the return's)",
    "Form W-4 (2026), Step 3 (the withholding child credit, the return's "
    "$2,200)",
    "SSA 2026 COLA fact sheet (Social Security wage base $184,500)",
    "IRC sec. 3101(a) and (b)(1) (Social Security 6.2%, Medicare 1.45%); "
    "sec. 3102(f) (Additional Medicare 0.9% withheld on wages over $200,000)",
    "N.C.G.S. 105-153.7(a) (North Carolina flat rate 3.99% after 2025)",
    "N.C.G.S. 105-153.5(a)(1) and (a1) (North Carolina standard and child "
    "deductions)",
)

_FEDERAL = {
    FilingStatusEnum.SINGLE: FederalRules(
        standard_deduction=Decimal("16100.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "12400.00", "0.1000"),
            ("12400.00", "50400.00", "0.1200"),
            ("50400.00", "105700.00", "0.2200"),
            ("105700.00", "201775.00", "0.2400"),
            ("201775.00", "256225.00", "0.3200"),
            ("256225.00", "640600.00", "0.3500"),
            ("640600.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_JOINTLY: FederalRules(
        standard_deduction=Decimal("32200.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "24800.00", "0.1000"),
            ("24800.00", "100800.00", "0.1200"),
            ("100800.00", "211400.00", "0.2200"),
            ("211400.00", "403550.00", "0.2400"),
            ("403550.00", "512450.00", "0.3200"),
            ("512450.00", "768700.00", "0.3500"),
            ("768700.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_SEPARATELY: FederalRules(
        standard_deduction=Decimal("16100.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "12400.00", "0.1000"),
            ("12400.00", "50400.00", "0.1200"),
            ("50400.00", "105700.00", "0.2200"),
            ("105700.00", "201775.00", "0.2400"),
            ("201775.00", "256225.00", "0.3200"),
            ("256225.00", "384350.00", "0.3500"),
            ("384350.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.HEAD_OF_HOUSEHOLD: FederalRules(
        standard_deduction=Decimal("24150.00"),
        child_credit_amount=Decimal("2200.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("1700.00"),
        brackets=ladder(
            ("0.00", "17700.00", "0.1000"),
            ("17700.00", "67450.00", "0.1200"),
            ("67450.00", "105700.00", "0.2200"),
            ("105700.00", "201750.00", "0.2400"),
            ("201750.00", "256200.00", "0.3200"),
            ("256200.00", "640600.00", "0.3500"),
            ("640600.00", None, "0.3700"),
        ),
    ),
}

_FICA = FicaRules(
    ss_rate=Decimal("0.0620"),
    ss_wage_base=Decimal("184500.00"),
    medicare_rate=Decimal("0.0145"),
    medicare_surtax_rate=Decimal("0.0090"),
    medicare_surtax_threshold=Decimal("200000.00"),
)

YEAR_2026 = TaxYearLaw(
    tax_year=2026,
    sources=SOURCES,
    federal=_FEDERAL,
    fica=_FICA,
    states={"NC": _north_carolina.state_year("0.0399")},
)
