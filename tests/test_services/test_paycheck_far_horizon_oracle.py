"""A paycheck past the two-year window, its four taxes worked by hand from the published law.

Closes finding **SAL-598** (plan#1598): since plan step salary:S11-c-2c the paychecks past the
saved schedule, which only the retirement and savings projections read, are priced by the tax
formulas with no independent oracle.  This one is that oracle.  It prices a made-up owner's
2041 paychecks through the read pass's pricer (``BalanceContext.paychecks()``), with no pay
stub, so the formulas alone price them, and grades each tax against a figure worked here from
the published documents rather than from the app's code.  It pins the FORMULAS, the substitution
of the newest published year for a year nobody has published, and the year-to-date replay that
the Social Security wage base and the Additional Medicare line are measured against.

**The law: as published through 2026** (ruling **R-SAL80**, "Real law, tests may swap").  The
test installs the shipped law's years up to 2026, so the resolver
(``tax_config_service.resolve_tax_year``: the latest year at or before the one asked for)
prices 2041 by 2026's law, and a year published later cannot move a figure below.  Each figure
is the 2026 document's, checked against it on 2026-11-02:

* IRS Pub 15-T (2026), Worksheet 1A, and its 2026 Annual Percentage Method table, STANDARD
  Withholding Rate Schedules, Single: the row at least ``$113,200`` and less than ``$209,275``
  withholds ``$17,966.00`` plus 24% of the excess.  Line 1g is ``$8,600`` ("otherwise").  The
  W-4 Step 2 box is taken as not checked: the app does not ask it (step plan#2683).
* The 2026 Form W-4, Step 3: ``$2,200`` for each qualifying child (Rev. Proc. 2025-32 sec. 4.05).
* Social Security 6.2% (IRC 3101(a)) to the 2026 wage base, ``$184,500`` (SSA); Medicare 1.45%
  (IRC 3101(b)(1)) plus 0.9% withheld on wages past ``$200,000`` in the year (IRC 3102(f)).
* North Carolina: 3.99% (N.C.G.S. 105-153.7(a)) of the annualised taxable pay less the single
  filer's ``$12,750`` standard deduction (N.C.G.S. 105-153.5(a)(1)).

**The state line is the app's model of the law, and NC-30 is not that model.**  What a North
Carolina employer withholds is NCDOR's NC-30 (2026): 4.09% ("3.99% plus 0.1%"), ``$12,750``
for a married filer too, ``$2,500`` for each NC-4 allowance, rounded to the whole dollar.  The
app prices 3.99% on the return's deduction at the cent.  This test grades the formula the engine
states; replacing it with NC-30's is the salary step plan#2682, which moves the figure below.

A figure is rounded half up at the cent where its derivation shows an arrow, and nowhere else:
the federal line once, at the end; the state line's yearly tax and then its share; Medicare's two
parts apart, as the two taxes they are.  Pub 15-T allows rounding to the whole dollar, and the
app does not take it.

**The owner** (every figure made up): single, North Carolina, one qualifying child, W-4 Step
4(a) ``$1,200.00``, 4(b) ``$3,000.00`` and 4(c) ``$25.00``; an ``$8,763.47`` paycheck every 14
days from 2026-01-02, with no raise; a Section 125 Health premium of ``$251.13`` ("Pre-tax, all
payroll taxes") and a ``$600.00`` 401(k) deferral ("Pre-tax, income tax only") on every paycheck.
Every 2041 paycheck is the same::

    gross                       8,763.47
    Social Security and
    Medicare wages              8,763.47 - 251.13          = 8,512.34
    taxable wages (Pub 15-T 1a) 8,763.47 - 251.13 - 600.00 = 7,912.34

    federal, Pub 15-T Worksheet 1A
      1c  7,912.34 x 26                                    = 205,720.84
      1e  205,720.84 + 1,200.00 (4(a))                     = 206,920.84
      1h  3,000.00 (4(b)) + 8,600.00 (1g)                  =  11,600.00
      1i  206,920.84 - 11,600.00                           = 195,320.84
      2g  17,966.00 + (195,320.84 - 113,200.00) x 24%
          = 17,966.00 + 19,709.0016                        =  37,675.0016
      2h  37,675.0016 / 26                                 =   1,449.03852
      3c  1,449.03852 - 2,200.00 / 26 (84.61538)           =   1,364.42314
      4b  1,364.42314 + 25.00 (4(c))                       =   1,389.42314  -> 1,389.42

    state     (205,720.84 - 12,750.00) x 3.99% = 7,699.536516 -> 7,699.54
              7,699.54 / 26 = 296.13615                                -> 296.14

The 2041 calendar holds 26 paydays, 2041-01-11 to 2041-12-27, so the year's Social Security and
Medicare wages before its k-th paycheck are ``(k - 1) x 8,512.34``; each case below works the
two payroll taxes from that.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app import tax_law as app_tax_law
from app.enums import FilingStatusEnum, PaycheckLineKindEnum
from app.services.balance_at import BalanceContext
from app.services.salary_paydays import paycheck_on
from app.tax_law import TaxLaw
from tests._test_helpers import make_flat_paycheck_line, make_salary_profile

#: The newest year of the law these figures were worked against.
_WORKED_ON = 2026

_FEDERAL = Decimal("1389.42")
_STATE = Decimal("296.14")


@pytest.fixture(name="owner")
def _owner(app, db, seed_user, seed_periods, tax_law):  # pylint: disable=unused-argument
    """The module docstring's owner, committed, priced under the law as published through 2026.

    Pylint: ``unused-argument`` -- ``app`` and ``seed_periods`` are asked for their side
    effects, the app context and the owner's paydays from 2026-01-02.
    """
    tax_law(TaxLaw(years=tuple(
        year for year in app_tax_law.LAW.years if year.tax_year <= _WORKED_ON
    )))
    profile = make_salary_profile(
        seed_user, db.session, name="Far Job", pay=Decimal("8763.47"),
        pay_from=date(2026, 1, 2),
    )
    assert ref_cache.filing_status_member(profile.filing_status_id) is FilingStatusEnum.SINGLE
    profile.qualifying_children = 1
    profile.additional_income = Decimal("1200.00")
    profile.additional_deductions = Decimal("3000.00")
    profile.extra_withholding = Decimal("25.00")
    make_flat_paycheck_line(
        profile, "Health", "251.13", PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
    )
    make_flat_paycheck_line(profile, "401(k)", "600.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION)
    db.session.commit()
    return profile


def _priced(profile, day):
    """The paycheck the read pass's pricer prices on *day*, past the saved schedule."""
    ctx = BalanceContext.build(profile.user_id)
    period = paycheck_on(ctx.calendar(), day)
    assert period is not None, f"{day} is not one of the owner's paydays"
    breakdown = ctx.paychecks().for_profile(profile).at(period)
    assert breakdown.period.period_id is None, f"{day} is a saved paycheck, not a far one"
    assert breakdown.taxes.stub_payday is None, "a pay stub priced it, not the formulas alone"
    return breakdown


def _four(breakdown):
    """The four priced taxes, in withholding order."""
    taxes = breakdown.taxes
    return (taxes.federal, taxes.state, taxes.social_security, taxes.medicare)


class TestA2041PaycheckIsPricedByThePublishedLaw:
    """Three 2041 paychecks: the year's first, and the two that cross its payroll-tax lines."""

    def test_the_years_first_paycheck_pays_both_payroll_taxes_in_full(self, owner):
        """1,389.42 / 296.14 / 527.77 / 123.43: a new year's wages start again from zero.

        Hand calculation (wages before it ``0.00``; 2040's paychecks passed the wage base, so
        a figure carried over the year end would charge no Social Security here)::

            Social Security  8,512.34 x 6.2%  = 527.76508 -> 527.77
            Medicare         8,512.34 x 1.45% = 123.42893 -> 123.43
        """
        assert _four(_priced(owner, date(2041, 1, 11))) == (
            _FEDERAL, _STATE, Decimal("527.77"), Decimal("123.43"),
        )

    def test_the_paycheck_crossing_the_wage_base_pays_social_security_up_to_it(self, owner):
        """1,389.42 / 296.14 / 355.93 / 123.43: the 22nd paycheck reaches ``$184,500``.

        Hand calculation (wages before it ``21 x 8,512.34 = 178,759.14``)::

            Social Security  (184,500.00 - 178,759.14) x 6.2%
                             = 5,740.86 x 6.2% = 355.93332 -> 355.93
                             (the full 527.77 would carry the year to 187,271.48)
            Medicare         8,512.34 x 1.45% = 123.42893 -> 123.43
                             (178,759.14 + 8,512.34 = 187,271.48, under 200,000)
        """
        assert _four(_priced(owner, date(2041, 11, 1))) == (
            _FEDERAL, _STATE, Decimal("355.93"), Decimal("123.43"),
        )

    def test_the_paycheck_crossing_the_additional_medicare_line_pays_it_on_the_excess(
        self, owner,
    ):
        """1,389.42 / 296.14 / 0.00 / 162.10: the 24th paycheck passes ``$200,000``.

        Hand calculation (wages before it ``23 x 8,512.34 = 195,783.82``)::

            Social Security  0.00 (the year passed 184,500.00 two paychecks ago)
            Medicare         8,512.34 x 1.45%                     = 123.42893 -> 123.43
                           + (195,783.82 + 8,512.34 - 200,000.00) x 0.9%
                             = 4,296.16 x 0.9%                    =  38.66544 ->  38.67
                                                                               = 162.10
        """
        assert _four(_priced(owner, date(2041, 11, 29))) == (
            _FEDERAL, _STATE, Decimal("0.00"), Decimal("162.10"),
        )
