"""Paychecks priced from pay stubs (plan step salary:S11-c-2c).

Rulings **R-SAL42** (fork 1, "Latest same-lines stub"), **R-SAL54** ("Nearest
stub, then formulas"), **R-SAL55** ("Never below $0.00"), **R-SAL58** ("Stub
records its kind"), **R-SAL77** ("Each on its own year") and **R-SAL100**
("Name the pricing stub"), through the read pass's pricer on real rows.  Each
tax is the stub's figure plus what the formulas say changed between the
stub's paycheck and this one, so every expected figure below is worked by
hand in its case's docstring.

**The made-up law** (every figure here is made up): federal rules that
withhold ``$0.00`` (so federal is the stub's figure, copied), a flat North
Carolina rate with no standard deduction -- 5% in 2026 and 4% in 2027 -- and
FICA at 6.2% on a ``$176,100.00`` base and 1.45%.  With no state deduction
the formulas' state line is ``rate x taxable`` exactly, and FICA is
``rate x gross``.

**The owner**: a ``$3,000.00`` paycheck every 14 days from 2026-01-02, a
pre-tax Health line of ``$200.00`` and a taxable Phone allowance of
``$60.00``, so every paycheck's gross is ``$3,060.00`` and its taxable wage
``$2,860.00``::

    formulas on that paycheck (2026)   state  2,860.00 x 5%    = 143.00
                                       SS     3,060.00 x 6.2%  = 189.72
                                       Med    3,060.00 x 1.45% =  44.37
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import ref_cache
from app.enums import BusinessDayShiftEnum, PaycheckLineKindEnum, WithholdingKindEnum
from app.models.pay_stub import PayStubOneOff
from app.services import paycheck_calculator
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCalendar
from app.services.pay_rhythm import Era, FixedDays, Monthly, Rhythm
from app.services.payroll_basis import PayrollBasis
from app.services.salary_paydays import paycheck_on
from app.services.tax_config_service import (
    configs_by_year,
    load_tax_configs_for_year,
    profile_tax_series,
)
from app.tax_law import TaxLaw
from tests._test_helpers import (
    add_test_pay_stub,
    made_up_federal,
    made_up_fica,
    made_up_state,
    made_up_year,
    make_flat_paycheck_line,
    make_salary_profile,
)
from tests.test_services.test_paycheck_calculator._helpers import (
    FakeBracket,
    FakeBracketSet,
    FakeFicaConfig,
    FakeProfile,
    FakeStateTaxConfig,
)

_FEDERAL = WithholdingKindEnum.FEDERAL_INCOME
_STATE = WithholdingKindEnum.STATE_INCOME
_SS = WithholdingKindEnum.SOCIAL_SECURITY
_MEDICARE = WithholdingKindEnum.MEDICARE


def _taxes(federal, state, social_security, medicare):
    """A stub's four printed taxes, as :func:`add_test_pay_stub` takes them."""
    return {
        _FEDERAL: federal, _STATE: state, _SS: social_security, _MEDICARE: medicare,
    }


def _law():
    """2026 at 5% and 2027 at 4% North Carolina, made-up FICA, no federal tax."""
    return TaxLaw(years=tuple(
        made_up_year(
            year, fica=made_up_fica(),
            states={"NC": made_up_state(Decimal(rate), Decimal("0.00"))},
        )
        for year, rate in ((2026, "0.0500"), (2027, "0.0400"))
    ))


@pytest.fixture(name="owner")
def _owner(app, db, seed_user, seed_periods, tax_law):  # pylint: disable=unused-argument
    """The module docstring's owner, committed: ``{"profile", "health", "phone"}``.

    Pylint: ``unused-argument`` -- ``app`` and ``seed_periods`` are asked for
    their side effects, the app context and the owner's 2026 paydays.
    """
    tax_law(_law())
    profile = make_salary_profile(
        seed_user, db.session, name="Day Job", pay=Decimal("3000.00"),
        pay_from=date(2026, 1, 2),
    )
    db.session.flush()
    health = make_flat_paycheck_line(
        profile, "Health", "200.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    )
    phone = make_flat_paycheck_line(
        profile, "Phone", "60.00", PaycheckLineKindEnum.TAXABLE_EARNING,
    )
    db.session.commit()
    return {"profile": profile, "health": health, "phone": phone}


def _priced(owner, day):
    """The paycheck the read pass's pricer prices on *day*."""
    ctx = BalanceContext.build(owner["profile"].user_id)
    period = paycheck_on(ctx.calendar(), day)
    assert period is not None, f"{day} is not one of the owner's paydays"
    return ctx.paychecks().for_profile(owner["profile"]).at(period)


def _same_lines_stub(owner, payday, base_pay, taxes):
    """A stub of the owner's two lines at their app figures."""
    return add_test_pay_stub(
        owner["profile"], payday, base_pay, taxes,
        lines=((owner["health"], "200.00"), (owner["phone"], "60.00")),
    )


def _four(breakdown):
    """The four priced taxes, in withholding order."""
    taxes = breakdown.taxes
    return (taxes.federal, taxes.state, taxes.social_security, taxes.medicare)


class TestNoStubPricesWithTheFormulasAlone:
    """No switched-on stub on or before a paycheck: the formulas alone (R-SAL42 (1))."""

    def test_with_no_stub_the_paycheck_is_the_formulas_and_names_none(self, owner):
        """0.00 / 143.00 / 189.72 / 44.37, and no pricing stub named."""
        breakdown = _priced(owner, date(2026, 2, 13))
        assert _four(breakdown) == (
            Decimal("0.00"), Decimal("143.00"), Decimal("189.72"), Decimal("44.37"),
        )
        assert breakdown.taxes.stub_payday is None

    def test_a_switched_off_stub_prices_nothing(self, owner, db):
        """A stub switched off prices every paycheck as if it had never been entered (fork 8a)."""
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        ).use_for_pricing = False
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday is None
        assert breakdown.taxes.state == Decimal("143.00")

    def test_a_stub_dated_after_the_paycheck_does_not_price_it(self, owner, db):
        """Only a stub ON OR BEFORE the payday prices: the 03-13 stub prices 03-13, never 02-13."""
        _same_lines_stub(
            owner, date(2026, 3, 13), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        db.session.commit()
        assert _priced(owner, date(2026, 2, 13)).taxes.stub_payday is None
        assert _priced(owner, date(2026, 3, 13)).taxes.stub_payday == date(2026, 3, 13)
        assert _priced(owner, date(2026, 3, 27)).taxes.stub_payday == date(2026, 3, 13)


class TestASameLinesStubPricesThePaycheck:
    """The latest same-lines stub's taxes plus the formulas' difference (R-SAL42)."""

    def test_at_the_stubs_own_pay_the_paycheck_takes_the_stubs_four_taxes(self, owner, db):
        """Same lines and pay: the formulas price both paychecks alike, so each tax is the stub's.

        The 01-16 stub prints federal 250.00, state 150.00 (not the formulas'
        143.00: the employer's own figure is what a stub is for), SS 189.72
        and Medicare 44.37.  Both YTDs sit far below the wage base.  Net is
        ``3,060.00 - 200.00 - 634.09 = 2,225.91``.
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert _four(breakdown) == (
            Decimal("250.00"), Decimal("150.00"), Decimal("189.72"), Decimal("44.37"),
        )
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert breakdown.earnings.net_pay == Decimal("2225.91")

    def test_a_pay_change_since_the_stub_is_priced_by_the_formulas(self, owner, db):
        """The stub's paycheck paid 2,900.00 base; the formulas price the 100.00 more.

        Stub side: gross ``2,900.00 + 60.00 = 2,960.00``, taxable
        ``2,760.00``; the formulas say state 138.00, SS 183.52, Medicare
        42.92.  The stub prints federal 240.00, state 140.00, SS 183.52,
        Medicare 42.92.  So::

            federal  240.00 + 0.00   - 0.00   = 240.00
            state    140.00 + 143.00 - 138.00 = 145.00
            SS       183.52 + 189.72 - 183.52 = 189.72
            Medicare  42.92 +  44.37 -  42.92 =  44.37

        Net ``3,060.00 - 200.00 - 619.09 = 2,240.91``.
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "2900.00",
            _taxes("240.00", "140.00", "183.52", "42.92"),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert _four(breakdown) == (
            Decimal("240.00"), Decimal("145.00"), Decimal("189.72"), Decimal("44.37"),
        )
        assert breakdown.earnings.net_pay == Decimal("2240.91")

    def test_the_latest_same_lines_stub_beats_a_later_one_of_other_lines(self, owner, db):
        """A 01-30 stub with a one-off bonus has other lines; the 01-16 stub prices 02-13.

        A one-off that changes the stub's taxes -- the Bonus is a taxable
        earning -- makes a stub's lines differ from every paycheck's (R-SAL54:
        "A one-off stub is just one of those", as R-SAL123 amends it: only a
        tax-changing one-off does).
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        _same_lines_stub(
            owner, date(2026, 1, 30), "3000.00",
            _taxes("400.00", "175.00", "220.72", "51.62"),
        ).one_offs.append(PayStubOneOff(
            name="Bonus", amount=Decimal("500.00"),
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.TAXABLE_EARNING,
            ),
        ))
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert breakdown.taxes.state == Decimal("150.00")


class TestTheLatestStubOfItsKindPrices:
    """"The latest": of two same-lines stubs, and of two any-lines stubs, the later prices."""

    def test_of_two_same_lines_stubs_the_later_prices(self, owner, db):
        """01-16 prints state 150.00, 01-30 prints 155.00: 02-13 takes the 01-30 stub's 155.00."""
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        _same_lines_stub(
            owner, date(2026, 1, 30), "3000.00",
            _taxes("250.00", "155.00", "189.72", "44.37"),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 30)
        assert breakdown.taxes.state == Decimal("155.00")

    def test_of_two_other_lines_stubs_the_later_prices(self, owner, db):
        """Two Health-only stubs: 02-13 priced from 01-30, ``141.00 + 143.00 - 140.00 = 144.00``.

        Each prints no Phone line, so neither has the paycheck's lines; both
        stubs' paychecks are ``3,000.00`` gross, ``2,800.00`` taxable, so the
        formulas say state 140.00 on either side.
        """
        for payday, state in ((date(2026, 1, 16), "140.00"), (date(2026, 1, 30), "141.00")):
            add_test_pay_stub(
                owner["profile"], payday, "3000.00",
                _taxes("250.00", state, "186.00", "43.50"),
                lines=((owner["health"], "200.00"),),
            )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 30)
        assert breakdown.taxes.state == Decimal("144.00")


class TestWithNoSameLinesStubTheLatestOfAnyLinesPrices:
    """R-SAL54: no same-lines stub, the latest stub of ANY lines, the formulas the difference."""

    def test_a_stub_without_the_phone_line_prices_through_the_difference(self, owner, db):
        """The 01-16 stub printed no Phone line; it is the only stub, so it prices 02-13.

        Stub side: gross 3,000.00, taxable 2,800.00; the formulas say state
        140.00, SS 186.00, Medicare 43.50, and the stub prints federal
        250.00, state 140.00, SS 186.00, Medicare 43.50.  So::

            state    140.00 + 143.00 - 140.00 = 143.00
            SS       186.00 + 189.72 - 186.00 = 189.72
            Medicare  43.50 +  44.37 -  43.50 =  44.37
        """
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 16), "3000.00",
            taxes=_taxes("250.00", "140.00", "186.00", "43.50"),
            lines=((owner["health"], "200.00"),),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert _four(breakdown) == (
            Decimal("250.00"), Decimal("143.00"), Decimal("189.72"), Decimal("44.37"),
        )
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)

    def test_a_zero_one_off_does_not_make_the_lines_differ(self, owner, db):
        """A one-off printed at $0.00 moves no tax, so the stub keeps the paycheck's lines.

        The 01-16 stub (two lines and a $0.00 "Adjustment" one-off) prices
        02-13 ahead of the later 01-30 stub of Health alone.
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        ).one_offs.append(PayStubOneOff(
            name="Adjustment", amount=Decimal("0.00"),
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.TAXABLE_EARNING,
            ),
        ))
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 30), "3000.00",
            _taxes("250.00", "140.00", "186.00", "43.50"),
            lines=((owner["health"], "200.00"),),
        )
        db.session.commit()
        assert _priced(owner, date(2026, 2, 13)).taxes.stub_payday == date(2026, 1, 16)

    def test_a_post_tax_line_does_not_make_the_lines_differ(self, owner, db):
        """A newer stub that differs only by a post-tax line is a same-lines stub.

        RE-DERIVED at plan step salary:S11-c-2c, when "the same lines" was
        narrowed to the taxed kinds (the leaf's review, LOW-3; the coordinator
        under balance:R-BAL207): until then this case was
        ``test_a_line_capped_out_at_zero_is_not_a_different_line`` and
        expected the 01-16 stub (state ``$150.00``), because the 01-30 stub's
        post-tax Dues charge made its lines differ.  No tax formula reads a
        post-tax line, so the 01-30 stub now has the 02-13 paycheck's lines --
        its taxed lines are Health and Phone, as the paycheck's are -- and,
        being the later, it prices it.  Worked by hand:

            02-13 paycheck   gross 3,000.00 + Phone 60.00 = 3,060.00
                             taxable 3,060.00 - Health 200.00 = 2,860.00
                             (Dues, post-tax, capped at $100.00 and taken
                             on 01-02, is $0.00 and moves no tax)
            01-30 stub       base 3,000.00, Phone 60.00, Health 200.00:
                             the same gross and taxable
            formulas, both   state 2,860.00 x 5% = 143.00, SS 3,060.00 x
                             6.2% = 189.72, Medicare 3,060.00 x 1.45% = 44.37,
                             federal $0.00 (the module's law)
            priced           the stub's figure + 0.00 on every line:
                             federal 260.00, state 160.00, SS 189.72,
                             Medicare 44.37

        The capped-out half this case used to carry -- a line the engine prices
        at ``$0.00`` is not a different line -- is the next case's, on a
        pre-tax line, where the comparison still reads it.
        """
        dues = make_flat_paycheck_line(
            owner["profile"], "Dues", "100.00", PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        )
        dues.annual_cap = Decimal("100.00")
        db.session.flush()
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 30), "3000.00",
            taxes=_taxes("260.00", "160.00", "189.72", "44.37"),
            lines=(
                (owner["health"], "200.00"), (owner["phone"], "60.00"), (dues, "25.00"),
            ),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert [line.amount for line in breakdown.deductions.post_tax] == [Decimal("0.00")]
        assert breakdown.taxes.stub_payday == date(2026, 1, 30)
        assert _four(breakdown) == (
            Decimal("260.00"), Decimal("160.00"), Decimal("189.72"), Decimal("44.37"),
        )

    def test_an_after_tax_one_off_does_not_make_the_lines_differ(self, owner, db):
        """A newer stub whose only difference is an after-tax one-off is a same-lines stub.

        Ruling R-SAL123 ("Only tax-changing one-offs", amending R-SAL42 fork 8b
        and R-SAL54's reading; plan step salary:S11-c-2c): a made-up
        ``$37.19`` after-tax "Mileage" one-off on the 01-30 stub changes no
        tax, so the 01-30 stub keeps the 02-13 paycheck's lines and, being the
        later, prices it -- where the rule before it took the 01-16 stub.  By
        hand: the 01-30 stub's gross is 3,000.00 + Phone 60.00 = 3,060.00 (an
        after-tax earning is outside the gross) and its taxable 2,860.00, the
        paycheck's own, so every formulas difference is $0.00 and the four
        taxes are the stub's: 260.00 / 160.00 / 189.72 / 44.37.
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        _same_lines_stub(
            owner, date(2026, 1, 30), "3000.00",
            _taxes("260.00", "160.00", "189.72", "44.37"),
        ).one_offs.append(PayStubOneOff(
            name="Mileage", amount=Decimal("37.19"),
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.AFTER_TAX_EARNING,
            ),
        ))
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 30)
        assert _four(breakdown) == (
            Decimal("260.00"), Decimal("160.00"), Decimal("189.72"), Decimal("44.37"),
        )

    def test_a_post_tax_one_off_does_not_make_the_lines_differ(self, owner, db):
        """R-SAL123: a post-tax one-off changes no tax either, so the later stub prices.

        The 01-30 stub carries a made-up ``$23.61`` post-tax "Union assessment"
        one-off beside the paycheck's two lines; its gross (3,060.00) and
        taxable (2,860.00) are the paycheck's, so every difference is $0.00 and
        02-13 takes its four taxes, 260.00 / 160.00 / 189.72 / 44.37.
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        _same_lines_stub(
            owner, date(2026, 1, 30), "3000.00",
            _taxes("260.00", "160.00", "189.72", "44.37"),
        ).one_offs.append(PayStubOneOff(
            name="Union assessment", amount=Decimal("23.61"),
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.POST_TAX_DEDUCTION,
            ),
        ))
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 30)
        assert _four(breakdown) == (
            Decimal("260.00"), Decimal("160.00"), Decimal("189.72"), Decimal("44.37"),
        )

    def test_a_pre_tax_one_off_still_makes_the_lines_differ(self, owner, db):
        """R-SAL123: a pre-tax one-off changes the stub's taxes, so the earlier stub prices.

        The 01-30 stub carries a made-up ``$23.61`` pre-tax "Retro HSA"
        one-off: its taxable wage is 2,860.00 - 23.61 = 2,836.39, so it is a
        stub of other lines and the 01-16 stub, of the paycheck's lines,
        prices 02-13 at its own pay: state ``150.00``.  Were the one-off read
        as changing no tax, the 01-30 stub would price it at ``160.00 + 143.00
        - 141.82 = 161.18`` (its side's state 2,836.39 x 5% = 141.8195 ->
        141.82).
        """
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        _same_lines_stub(
            owner, date(2026, 1, 30), "3000.00",
            _taxes("260.00", "160.00", "189.72", "44.37"),
        ).one_offs.append(PayStubOneOff(
            name="Retro HSA", amount=Decimal("23.61"),
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
            ),
        ))
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert breakdown.taxes.state == Decimal("150.00")

    def test_the_paycheck_side_compares_its_taxed_lines_alone(self, owner, db):
        """A post-tax line carrying money on the PAYCHECK is not compared either.

        The narrowed rule's other side (plan step salary:S11-c-2c): a Roth
        line (post-tax, ``$50.00`` every paycheck) carries money on the 02-13
        paycheck, and the 01-16 stub records it beside Health and Phone, so
        the stub's taxed lines are the paycheck's and it prices 02-13 at its
        own pay: every difference $0.00, state ``150.00``.  The later 01-30
        stub prints Health alone, other lines.  Were the paycheck's Roth
        compared while the stub's is not, neither stub would have the lines and
        the later 01-30 would price it: ``140.00 + 143.00 - 140.00 = 143.00``
        (its side: gross 3,000.00, taxable 2,800.00, state 140.00).
        """
        roth = make_flat_paycheck_line(
            owner["profile"], "Roth", "50.00", PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        )
        db.session.flush()
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 16), "3000.00",
            taxes=_taxes("250.00", "150.00", "189.72", "44.37"),
            lines=(
                (owner["health"], "200.00"), (owner["phone"], "60.00"), (roth, "50.00"),
            ),
        )
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 30), "3000.00",
            taxes=_taxes("250.00", "140.00", "186.00", "43.50"),
            lines=((owner["health"], "200.00"),),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert breakdown.taxes.state == Decimal("150.00")

    def test_a_pre_tax_line_capped_out_at_zero_is_not_a_different_line(self, owner, db):
        """A taxed line its cap used up prices at $0.00; a stub without it has the paycheck's.

        An HSA line (pre-tax, ``$100.00`` capped at ``$100.00`` a year) is
        taken on 01-02 and priced at ``$0.00`` from 01-16 on.  The 01-16 stub
        prints Health and Phone; the LATER 01-30 stub also prints an HSA
        charge of ``$25.00``, so its taxed lines carry money the paycheck's do
        not.  The 02-13 paycheck carries money on Health and Phone alone, so
        the 01-16 stub is its same-lines stub (an application of
        balance:R-BAL207: comparing recorded keys alone would read the $0.00
        HSA line as a difference and take the 01-30 stub, pricing state at
        ``160.00 + 143.00 - 141.75 = 161.25``).  The 01-16 stub's pay is the
        paycheck's, so every difference is $0.00 and state is its ``150.00``.
        """
        hsa = make_flat_paycheck_line(
            owner["profile"], "HSA", "100.00", PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        hsa.annual_cap = Decimal("100.00")
        db.session.flush()
        _same_lines_stub(
            owner, date(2026, 1, 16), "3000.00",
            _taxes("250.00", "150.00", "189.72", "44.37"),
        )
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 30), "3000.00",
            taxes=_taxes("260.00", "160.00", "189.72", "44.37"),
            lines=(
                (owner["health"], "200.00"), (owner["phone"], "60.00"), (hsa, "25.00"),
            ),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert {line.name: line.amount for line in breakdown.deductions.pre_tax}["HSA"] == (
            Decimal("0.00")
        )
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert breakdown.taxes.state == Decimal("150.00")


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
                load_tax_configs_for_year(profile, 2027),
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
        state = FakeStateTaxConfig(flat_rate="0.045")
        state.standard_deduction = Decimal("12000.00")
        configs = {
            "bracket_set": FakeBracketSet(
                standard_deduction=Decimal("0"),
                brackets=[FakeBracket(Decimal("0"), None, Decimal("0"), 0)],
            ),
            "state_config": state,
            "fica_config": FakeFicaConfig(),
        }
        period = next(p for p in calendar.periods if p.start_date == date(2026, 7, 2))

        paycheck = paycheck_calculator.calculate_paycheck(
            PayrollBasis(profile, calendar), period, configs,
        )

        assert paycheck.earnings.gross_biweekly == Decimal("2215.38")
        assert paycheck.taxes.stub_payday == date(2026, 6, 1)
        assert _four(paycheck) == (
            Decimal("0.00"), Decimal("87.92"), Decimal("140.86"), Decimal("32.94"),
        )
