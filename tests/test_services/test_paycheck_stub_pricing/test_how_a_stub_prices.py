"""How the chosen stub prices: its side read by the kinds the stub records, federal by the
formulas' difference too, every tax and every line floored at $0.00, the wage-base cap, the
stub's own year's law and its own payday's rhythm, and a stub missing a tax refused (rulings
R-SAL55, R-SAL58, R-SAL77).
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import BusinessDayShiftEnum, PaycheckLineKindEnum
from app.services import paycheck_calculator
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCalendar
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm
from app.services.payroll_basis import PayrollBasis
from app.services.salary_paydays import paycheck_on
from app.services.tax_config_service import (
    configs_by_year,
    profile_tax_series,
)
from app.tax_law import TaxLaw
from tests._tax_configs import hand_built_law, tax_configs_for
from tests._test_helpers import (
    add_test_pay_stub,
    made_up_federal,
    made_up_fica,
    made_up_state,
    made_up_year,
    make_salary_profile,
)
from tests.test_services.test_paycheck_calculator._helpers import (
    FakeBracket,
    FakeFederalRules,
    FakeFicaRules,
    FakeProfile,
    FakeStateTaxRules,
)
from tests.test_services.test_tax_report_service._helpers import (
    _make_full_year_periods as _report_periods,
    _make_profile as _report_profile,
)

from ._helpers import (
    _FEDERAL,
    _STATE,
    _SS,
    _MEDICARE,
    _taxes,
    _law,
    _priced,
    _same_lines_stub,
    _four,
)


class TestTheStubSideIsPricedByWhatTheStubRecords:
    """R-SAL58: the stub's side of the difference reads the kinds the STUB prints."""

    def test_a_line_the_stub_printed_after_tax_is_priced_after_tax_on_its_side(self, owner, db):
        """The stub printed Health after tax; the app's Health is before tax.

        Stub side: no pre-tax line, so taxable = gross = 3,060.00 and the
        formulas say state 153.00; the stub prints state 153.00.  Priced
        ``153.00 + 143.00 - 153.00 = 143.00``.  Reading the app's kind on the
        stub's side instead would price its state at 143.00 and the
        paycheck's at ``153.00 + 143.00 - 143.00 = 153.00``.
        """
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 16), "3000.00",
            taxes=_taxes("250.00", "153.00", "189.72", "44.37"),
            lines=(
                (owner["health"], "200.00", PaycheckLineKindEnum.POST_TAX_DEDUCTION),
                (owner["phone"], "60.00"),
            ),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert breakdown.taxes.state == Decimal("143.00")


class TestTheFederalLineTakesTheFormulasDifferenceToo:
    """Federal is priced like the other three: the stub's figure plus the formulas' difference.

    The module's own law withholds no federal, so this case installs the
    made-up federal rules too (a ``$14,600.00`` standard deduction, 10% to
    ``$11,600.00``, then 12%).  A ``$1,500.00`` paycheck with no lines::

        paycheck  1,500.00 x 26 - 14,600.00 = 24,400.00
                  1,160.00 + 12% x 12,800.00 = 2,696.00 / 26 = 103.69
        stub      1,400.00 x 26 - 14,600.00 = 21,800.00
                  1,160.00 + 12% x 10,200.00 = 2,384.00 / 26 =  91.69

    The stub prints federal 95.00, so the paycheck withholds
    ``95.00 + 103.69 - 91.69 = 107.00``; state ``72.00 + 75.00 - 70.00 =
    77.00``.
    """

    @pytest.mark.usefixtures("app", "seed_periods")
    def test_a_federal_figure_moves_by_the_formulas_difference(
        self, db, seed_user, tax_law,
    ):
        """107.00 federal and 77.00 state on 02-13, priced from the 01-16 stub."""
        tax_law(TaxLaw(years=(made_up_year(
            2026, federal=made_up_federal(), fica=made_up_fica(),
            states={"NC": made_up_state(Decimal("0.0500"), Decimal("0.00"))},
        ),)))
        profile = make_salary_profile(
            seed_user, db.session, name="Small Job", pay=Decimal("1500.00"),
            pay_from=date(2026, 1, 2),
        )
        db.session.flush()
        add_test_pay_stub(
            profile, date(2026, 1, 16), "1400.00",
            taxes=_taxes("95.00", "72.00", "86.80", "20.30"),
        )
        db.session.commit()
        breakdown = _priced({"profile": profile}, date(2026, 2, 13))
        assert _four(breakdown) == (
            Decimal("107.00"), Decimal("77.00"), Decimal("93.00"), Decimal("21.75"),
        )


class TestEveryPricedTaxStopsAtZero:
    """R-SAL55: a priced tax is never below $0.00, whatever the cause."""

    def test_a_difference_larger_than_the_stubs_figure_floors_at_zero(self, owner, db):
        """The stub printed state 0.00 on a 3,100.00 base; the paycheck pays less.

        Stub side: gross 3,160.00, taxable 2,960.00, the formulas say state
        148.00.  Priced ``0.00 + 143.00 - 148.00 = -5.00``, floored to
        ``0.00``.  SS: ``195.92 + 189.72 - 195.92 = 189.72``.
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3100.00",
            _taxes("250.00", "0.00", "195.92", "45.82"),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.state == Decimal("0.00")
        assert breakdown.taxes.social_security == Decimal("189.72")


class TestEveryLineFloorsOnItsOwn:
    """R-SAL55 on each of the four lines: "Every priced tax, all four, stops at $0.00"."""

    @pytest.mark.usefixtures("app", "seed_periods")
    def test_four_negative_differences_each_floor_at_zero(self, db, seed_user, tax_law):
        """A stub printing $0.00 for all four taxes on a larger paycheck than this one.

        The made-up federal rules of the federal case above; the stub's
        paycheck pays 1,600.00, this one 1,500.00::

            federal  0.00 + 103.69 - 115.69 = -12.00 -> 0.00
                     (1,600.00 x 26 - 14,600.00 = 27,000.00;
                      1,160.00 + 12% x 15,400.00 = 3,008.00 / 26 = 115.69)
            state    0.00 +  75.00 -  80.00 =  -5.00 -> 0.00
            SS       0.00 +  93.00 -  99.20 =  -6.20 -> 0.00
            Medicare 0.00 +  21.75 -  23.20 =  -1.45 -> 0.00
        """
        tax_law(TaxLaw(years=(made_up_year(
            2026, federal=made_up_federal(), fica=made_up_fica(),
            states={"NC": made_up_state(Decimal("0.0500"), Decimal("0.00"))},
        ),)))
        profile = make_salary_profile(
            seed_user, db.session, name="Small Job", pay=Decimal("1500.00"),
            pay_from=date(2026, 1, 2),
        )
        db.session.flush()
        add_test_pay_stub(
            profile, date(2026, 1, 16), "1600.00",
            _taxes("0.00", "0.00", "0.00", "0.00"),
        )
        db.session.commit()
        breakdown = _priced({"profile": profile}, date(2026, 2, 13))
        assert _four(breakdown) == (
            Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
        )


class TestTheSocialSecurityCapThroughAStub:
    """The wage-base cap reaches a stub-priced paycheck (CRIT-03 / F-037).

    A ``$10,000.00`` paycheck with no lines; the 01-02 stub prints SS 620.00
    (``10,000.00 x 6.2%``, the year's first paycheck) and Medicare 145.00.
    Before the 2026-08-28 paycheck the year has paid ``17 x 10,000.00 =
    170,000.00``, so the formulas tax only ``6,100.00`` of it:
    ``620.00 + 378.20 - 620.00 = 378.20``.  From 09-11 on the formulas say
    ``0.00`` and so does the stub-priced paycheck.  The Medicare surtax
    (0.9% over ``$200,000.00`` a year) reaches it too: before 10-09 the year
    has paid ``200,000.00``, so Medicare is ``145.00 + 90.00 = 235.00`` and
    the stub-priced figure ``145.00 + 235.00 - 145.00 = 235.00``.
    """

    @pytest.mark.usefixtures("app", "seed_periods")
    def test_the_crossing_paycheck_and_the_next_are_capped(self, db, seed_user, tax_law):
        """378.20 on 08-28, 0.00 on 09-11; Medicare is never capped."""
        tax_law(_law())
        profile = make_salary_profile(
            seed_user, db.session, name="Big Job", pay=Decimal("10000.00"),
            pay_from=date(2026, 1, 2),
        )
        db.session.flush()
        add_test_pay_stub(
            profile, date(2026, 1, 2), "10000.00",
            taxes=_taxes("0.00", "500.00", "620.00", "145.00"),
        )
        db.session.commit()
        owner = {"profile": profile}
        crossing = _priced(owner, date(2026, 8, 28))
        after = _priced(owner, date(2026, 9, 11))
        assert crossing.taxes.social_security == Decimal("378.20")
        assert after.taxes.social_security == Decimal("0.00")
        assert after.taxes.medicare == Decimal("145.00")
        assert after.taxes.stub_payday == date(2026, 1, 2)
        assert _priced(owner, date(2026, 10, 9)).taxes.medicare == Decimal("235.00")


class TestAStubIsPricedOnItsOwnYearsLaw:
    """R-SAL77: the stub on its own payday's year, the paycheck on its own."""

    def test_a_december_stub_prices_a_january_paycheck_through_both_years(self, owner, db):
        """2027's 4% reaches the January paycheck: ``143.00 + 114.40 - 143.00 = 114.40``.

        The 2026-12-18 stub prints state 143.00 under 2026's 5%.  Priced on
        BOTH years' law the paycheck's state is 114.40 (2,860.00 x 4%);
        R-SAL77's rejected option -- the stub on the paycheck's year --
        would read 143.00.
        """
        _same_lines_stub(
            owner, date(2026, 12, 18), "3000.00",
            _taxes("250.00", "143.00", "189.72", "44.37"),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2027, 1, 1))
        assert breakdown.taxes.stub_payday == date(2026, 12, 18)
        assert breakdown.taxes.state == Decimal("114.40")

    def test_a_2025_stub_prices_a_2026_paycheck_on_each_years_withholding_figures(
        self, app, db, seed_user,
    ):
        """The shipped law: 2025's stub side withholds on Pub 15-T's and the W-4's figures.

        Ruling plan#3213.  Married filing jointly, one child, 4,000.00 a
        paycheck, no lines; the 2025-12-19 stub prints federal 250.00.

          paycheck (2026, 32,200 and 2,200 -- 2026 states no withholding figure):
            104000 - 32200 = 71,800.00
            24800*0.10 + (71800-24800)*0.12 = 2480.00 + 5640.00 = 8,120.00
            (8120 - 2200) / 26 = 227.6923... -> 227.69
          stub (2025's withholding figures, 30,000 and 2,000):
            104000 - 30000 = 74,000.00
            23850*0.10 + (74000-23850)*0.12 = 2385.00 + 6018.00 = 8,403.00
            (8403 - 2000) / 26 = 246.2692... -> 246.27
          federal = 250.00 + 227.69 - 246.27 = 231.42

        On the return's 2025 figures for the stub side too (31,500 and 2,200:
        (8223 - 2200) / 26 = 231.65) it would read 250.00 + 227.69 - 231.65 =
        246.04: the 14.62 a paycheck the ruling's option A would over-withhold
        until a 2026 stub is entered (measured: with 2025's withholding
        figures deleted, this test reads 246.04).
        """
        profile = _report_profile(
            seed_user, filing_status_name="married_jointly", pay="4000.00",
            qualifying_children=1, first_payday=date(2025, 12, 19),
        )
        _report_periods(seed_user["user"], count=2, start=date(2025, 12, 19))
        add_test_pay_stub(
            profile, date(2025, 12, 19), "4000.00",
            taxes=_taxes("250.00", "140.00", "248.00", "58.00"),
        )
        db.session.commit()

        breakdown = _priced({"profile": profile}, date(2026, 1, 2))
        assert breakdown.taxes.stub_payday == date(2025, 12, 19)
        assert breakdown.taxes.federal == Decimal("231.42")

    def test_one_years_law_refuses_a_stub_of_another_year(self, owner, db):
        """Handed 2027's law alone, a 2026 stub pricing a 2027 paycheck is refused."""
        _same_lines_stub(
            owner, date(2026, 12, 18), "3000.00",
            _taxes("250.00", "143.00", "189.72", "44.37"),
        )
        db.session.commit()
        profile = owner["profile"]
        ctx = BalanceContext.build(profile.user_id)
        period = paycheck_on(ctx.calendar(), date(2027, 1, 1))
        with pytest.raises(ValueError, match="R-SAL77"):
            paycheck_calculator.project_salary(
                PayrollBasis(profile, ctx.calendar()), [period],
                tax_configs_for(profile, 2027),
            )

    def test_a_per_year_law_missing_the_stubs_year_is_refused(self, owner, db):
        """A per-year mapping built without the stub's year names the year it lacks."""
        _same_lines_stub(
            owner, date(2026, 12, 18), "3000.00",
            _taxes("250.00", "143.00", "189.72", "44.37"),
        )
        db.session.commit()
        profile = owner["profile"]
        ctx = BalanceContext.build(profile.user_id)
        period = paycheck_on(ctx.calendar(), date(2027, 1, 1))
        with pytest.raises(ValueError, match="no tax law was resolved for 2026"):
            paycheck_calculator.project_salary(
                PayrollBasis(profile, ctx.calendar()), [period],
                configs_by_year=configs_by_year(profile_tax_series(profile), [2027]),
            )

    def test_the_year_set_holds_every_stubs_year(self, owner, db):
        """``tax_years_for``: the periods' years and every stub's, switched on or off."""
        _same_lines_stub(
            owner, date(2026, 12, 18), "3000.00",
            _taxes("250.00", "143.00", "189.72", "44.37"),
        ).use_for_pricing = False
        db.session.commit()
        profile = owner["profile"]
        ctx = BalanceContext.build(profile.user_id)
        period = paycheck_on(ctx.calendar(), date(2027, 1, 1))
        assert paycheck_calculator.tax_years_for(
            PayrollBasis(profile, ctx.calendar()), [period],
        ) == {2026, 2027}


class TestAStubMissingATaxIsRefused:
    """A stub lacking one of the four taxes is a writer's defect, named rather than priced."""

    def test_a_stub_without_medicare_names_the_missing_tax(self, owner, db):
        """The entry door refuses such a stub; one written around it is refused at pricing."""
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 16), "3000.00",
            taxes={_FEDERAL: "250.00", _STATE: "150.00", _SS: "189.72"},
            lines=((owner["health"], "200.00"), (owner["phone"], "60.00")),
        )
        db.session.commit()
        with pytest.raises(ValueError, match="MEDICARE"):
            _priced(owner, date(2026, 2, 13))


class TestTheStubSideTakesTheRhythmOnItsOwnPayday:
    """A stub paid under another rhythm is priced at ITS rhythm (review L3, plan step S11-c-2c).

    ``_stubs._formulas`` reads the paychecks-a-year in force on the payday it
    is handed, so the stub's side runs at the rhythm of the STUB's payday and
    the paycheck's side at the paycheck's.  Every case above has one rhythm,
    where the two agree; this one crosses an era, in memory: monthly paydays
    from 2026-01-01, then every 14 days from Thursday 2026-07-02, a made-up
    ``$4,800.00`` paid monthly (``$57,600.00`` a year), so the 07-02 paycheck
    is ``57,600.00 / 26 = 2,215.3846 -> $2,215.38``.  Made-up law: federal at
    a 0% rate (so federal is the stub's figure), state a flat 4.5% after a
    ``$12,000.00`` standard deduction -- annualised over the rhythm's count,
    so the count is visible in the figure -- and FICA at 6.2% / 1.45%.

    The stub, dated 2026-06-01 (monthly, 12 a year), prints base pay
    ``$4,800.00`` and made-up taxes 0.00 / 180.00 / 301.11 / 70.42::

        paycheck 07-02 at 26   state  0.045 x (2,215.38 x 26 - 12,000) / 26
                                      = 0.045 x 45,599.88 / 26 = 78.92
                               SS     2,215.38 x 0.062  = 137.35
                               Med    2,215.38 x 0.0145 =  32.12
        stub 06-01 at 12       state  0.045 x (4,800 x 12 - 12,000) / 12 = 171.00
                               SS     4,800.00 x 0.062  = 297.60
                               Med    4,800.00 x 0.0145 =  69.60
        priced                 state  180.00 + 78.92 - 171.00 =  87.92
                               SS     301.11 + 137.35 - 297.60 = 140.86
                               Med     70.42 +  32.12 -  69.60 =  32.94

    The stub's side priced at the PAYCHECK's 26 instead reads state
    ``0.045 x (4,800 x 26 - 12,000) / 26 = 195.23`` and prices ``63.69``.
    """

    def test_a_monthly_stub_prices_a_biweekly_paycheck_at_twelve_a_year(self, app):
        """State $87.92: the stub's side annualised over 12, the paycheck's over 26."""
        none = BusinessDayShiftEnum.NONE
        calendar = PayCalendar.from_paydays(
            [
                (index + 1, payday) for index, payday in enumerate(
                    [date(2026, month, 1) for month in range(1, 7)]
                    + [date(2026, 7, 2) + timedelta(days=14 * step) for step in range(4)]
                )
            ],
            (
                Era(effective_from=date(2026, 1, 1), rhythm=Rhythm(Monthly(1), none)),
                Era(effective_from=date(2026, 7, 2), rhythm=Rhythm(FixedDays(14), none)),
            ),
            user_id=1,
            history_opens_on=None,
        )
        profile = FakeProfile(
            pay=Decimal("4800.00"), pay_from=date(2026, 1, 1), created_at=date(2026, 1, 1),
        )
        profile.pay_stubs = [SimpleNamespace(
            payday=date(2026, 6, 1), base_pay=Decimal("4800.00"), use_for_pricing=True,
            line_amounts=[], one_offs=[],
            withholdings=[
                SimpleNamespace(
                    withholding_kind_id=ref_cache.withholding_kind_id(kind),
                    amount=Decimal(amount),
                )
                for kind, amount in (
                    (_FEDERAL, "0.00"), (_STATE, "180.00"),
                    (_SS, "301.11"), (_MEDICARE, "70.42"),
                )
            ],
        )]
        state = FakeStateTaxRules(flat_rate="0.045")
        state.standard_deduction = Decimal("12000.00")
        configs = hand_built_law(
            2026,
            bracket_set=FakeFederalRules(
                standard_deduction=Decimal("0"),
                brackets=[FakeBracket(Decimal("0"), None, Decimal("0"), 0)],
            ),
            state_config=state,
            fica_config=FakeFicaRules(),
        )
        period = next(p for p in calendar.periods if p.start_date == date(2026, 7, 2))

        paycheck = paycheck_calculator.calculate_paycheck(
            PayrollBasis(profile, calendar), period, configs,
        )

        assert paycheck.earnings.gross_biweekly == Decimal("2215.38")
        assert paycheck.taxes.stub_payday == date(2026, 6, 1)
        assert _four(paycheck) == (
            Decimal("0.00"), Decimal("87.92"), Decimal("140.86"), Decimal("32.94"),
        )
