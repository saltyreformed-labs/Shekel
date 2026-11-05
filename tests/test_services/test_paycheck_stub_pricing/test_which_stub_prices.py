"""Which stub prices a paycheck: with none switched on, the formulas alone; the latest same-lines
stub plus the formulas' difference; the later of two stubs of a kind; and with no same-lines
stub, the latest of any lines (rulings R-SAL42, R-SAL54).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.models.pay_stub import PayStubOneOff
from tests._test_helpers import add_test_pay_stub, make_flat_paycheck_line

from ._helpers import _taxes, _priced, _same_lines_stub, _four


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
