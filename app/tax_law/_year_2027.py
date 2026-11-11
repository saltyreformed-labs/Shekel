"""The 2027 tax law, transcribed from the documents :data:`SOURCES` names.

**Every figure stands exactly as its source prints it.**  Rev. Proc. 2026-35
sec. 3.01 gives the head of household a different 24% / 32% boundary
($208,300) and 35% / 37% boundary ($661,350) from the single filer's
($208,325, $661,375), and the ladder below carries Table 2's figures, as 2026's
module carries Rev. Proc. 2025-32's own head-of-household boundary ($201,750,
corrected from the single filer's $201,775 at plan step salary:X-at-7, finding
**salary:SAL-577**).

**2027 states no withholding figure** (ruling plan#3213: a year states one only
where its withholding documents print other figures than the return's).  On
2026-11-10 neither prints one: the Pub 15-T draft irs.gov posts is still 2026's,
and the draft Form W-4 (2027), created 2026-06-10, shows placeholders for its
Step 3 child credit and its standard deductions.  Step plan#3353 re-checks
against both when the IRS posts them final.

**North Carolina's 2027 rate is fixed by statute, not by a revenue trigger.**
S.L. 2026-41 sec. 44.1(a) (approved 2026-07-07) rewrote N.C.G.S.
105-153.7(a): 3.99% in 2026, 3.49% in 2027 through 2029.  It also struck the
trigger rows for the 2027 through 2034 tax years from subsection (a1), so no
revenue test can move 2027's rate.  North Carolina's standard and child
deductions are the unindexed tables in :mod:`app.tax_law._north_carolina`,
graded there; step plan#3349 re-checks them against the 2027 Form D-401
instructions when NCDOR posts them.
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
    "IRS Rev. Proc. 2026-35 (the advance copy irs.gov posted 2026-11-05), "
    "sec. 3.01 (tax rate tables), sec. 3.05 (child tax credit $2,300; "
    "refundable $1,800 per child) and sec. 3.15 (standard deduction)",
    "IRC sec. 24(h)(4) (credit for other dependents $500, not indexed)",
    "SSA notice, Cost-of-Living Increase and Other Determinations for 2027, "
    "Docket No. SSA-2026-1024, Federal Register document 2026-21796, "
    "2026-10-26 (Social Security wage base $192,300)",
    "IRC sec. 3101(a) and (b)(1) (Social Security 6.2%, Medicare 1.45%); "
    "sec. 3101(b)(2) (Additional Medicare 0.9%) and sec. 3102(f) (withheld "
    "on wages over $200,000)",
    "N.C.G.S. 105-153.7(a) as rewritten by S.L. 2026-41 sec. 44.1(a) (North "
    "Carolina flat rate 3.49% in 2027)",
    "N.C.G.S. 105-153.5(a)(1) and (a1) (North Carolina standard and child "
    "deductions)",
)

_FEDERAL = {
    FilingStatusEnum.SINGLE: FederalRules(
        standard_deduction=Decimal("16600.00"),
        brackets=ladder(
            ("0.00", "12800.00", "0.1000"),
            ("12800.00", "52025.00", "0.1200"),
            ("52025.00", "109125.00", "0.2200"),
            ("109125.00", "208325.00", "0.2400"),
            ("208325.00", "264550.00", "0.3200"),
            ("264550.00", "661375.00", "0.3500"),
            ("661375.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_JOINTLY: FederalRules(
        standard_deduction=Decimal("33200.00"),
        brackets=ladder(
            ("0.00", "25600.00", "0.1000"),
            ("25600.00", "104050.00", "0.1200"),
            ("104050.00", "218250.00", "0.2200"),
            ("218250.00", "416650.00", "0.2400"),
            ("416650.00", "529100.00", "0.3200"),
            ("529100.00", "793650.00", "0.3500"),
            ("793650.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.MARRIED_SEPARATELY: FederalRules(
        standard_deduction=Decimal("16600.00"),
        brackets=ladder(
            ("0.00", "12800.00", "0.1000"),
            ("12800.00", "52025.00", "0.1200"),
            ("52025.00", "109125.00", "0.2200"),
            ("109125.00", "208325.00", "0.2400"),
            ("208325.00", "264550.00", "0.3200"),
            ("264550.00", "396825.00", "0.3500"),
            ("396825.00", None, "0.3700"),
        ),
    ),
    FilingStatusEnum.HEAD_OF_HOUSEHOLD: FederalRules(
        standard_deduction=Decimal("24925.00"),
        brackets=ladder(
            ("0.00", "18250.00", "0.1000"),
            ("18250.00", "69650.00", "0.1200"),
            ("69650.00", "109100.00", "0.2200"),
            ("109100.00", "208300.00", "0.2400"),
            ("208300.00", "264550.00", "0.3200"),
            ("264550.00", "661350.00", "0.3500"),
            ("661350.00", None, "0.3700"),
        ),
    ),
}

_CREDITS = FederalCredits(
    child_credit_amount=Decimal("2300.00"),
    other_dependent_credit_amount=Decimal("500.00"),
    child_credit_refundable_cap=Decimal("1800.00"),
)

_FICA = FicaRules(
    ss_rate=Decimal("0.0620"),
    ss_wage_base=Decimal("192300.00"),
    medicare_rate=Decimal("0.0145"),
    medicare_surtax_rate=Decimal("0.0090"),
    medicare_surtax_threshold=Decimal("200000.00"),
)

YEAR_2027 = TaxYearLaw(
    tax_year=2027,
    sources=SOURCES,
    federal=_FEDERAL,
    federal_credits=_CREDITS,
    fica=_FICA,
    states={"NC": _north_carolina.state_year("0.0349")},
)
