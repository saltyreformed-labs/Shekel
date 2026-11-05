"""An all-payroll-taxes line through a stub: both sides leave it out of the Social Security and
Medicare wages (plan step salary:S11-e, ruling plan#2639).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.models.pay_stub import PayStubOneOff
from app.services import paycheck_calculator
from tests._test_helpers import add_test_pay_stub

from ._helpers import _taxes, _priced, _four


class TestAnAllPayrollTaxesLineThroughAStub:
    """Plan step salary:S11-e (ruling plan#2639): both sides leave the line out of FICA wages.

    Each case re-picks the owner's Health line as "Pre-tax, all payroll
    taxes", so the formulas on the owner's paycheck read::

        FICA wages  3,060.00 - 200.00 = 2,860.00
        state       2,860.00 x 5%    = 143.00
        SS          2,860.00 x 6.2%  = 177.32   (189.72 on the gross)
        Med         2,860.00 x 1.45% =  41.47   ( 44.37 on the gross)
    """

    @staticmethod
    def _health_is_all_payroll_taxes(owner, db):
        """Re-pick the owner's Health line as the all-payroll-taxes kind, committed."""
        owner["health"].paycheck_line_kind_id = ref_cache.paycheck_line_kind_id(
            PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
        )
        db.session.commit()

    def _stub(self, owner, taxes, health_kind=PaycheckLineKindEnum.PRE_FICA_DEDUCTION):
        """A 2026-01-16 stub of the owner's two lines, Health recorded under *health_kind*."""
        return add_test_pay_stub(
            owner["profile"], date(2026, 1, 16), "3000.00", taxes,
            lines=((owner["health"], "200.00", health_kind), (owner["phone"], "60.00")),
        )

    def test_with_no_stub_the_formulas_charge_fica_on_the_wages_alone(self, owner, db):
        """No stub: SS 177.32 and Medicare 41.47, the module's 189.72 and 44.37 less 7.65% of 200.00.

        Net ``3,060.00 - 200.00 - (0.00 + 143.00 + 177.32 + 41.47) = 2,498.21``.
        """
        self._health_is_all_payroll_taxes(owner, db)
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday is None
        assert _four(breakdown) == (
            Decimal("0.00"), Decimal("143.00"), Decimal("177.32"), Decimal("41.47"),
        )
        assert breakdown.earnings.net_pay == Decimal("2498.21")

    def test_a_premium_rise_since_the_stub_lowers_social_security_and_medicare(self, owner, db):
        """Health rose from 200.00 on the stub to 220.00: FICA falls by 7.65% of the 20.00.

        Paycheck: FICA wages and taxable ``3,060.00 - 220.00 = 2,840.00``, so
        state 142.00, SS 176.08, Medicare 41.18.  Stub side (Health 200.00 as
        recorded): state 143.00, SS 177.32, Medicare 41.47, which it prints.
        So::

            state    143.00 + 142.00 - 143.00 = 142.00
            SS       177.32 + 176.08 - 177.32 = 176.08   (down 1.24 = 20.00 x 6.2%)
            Medicare  41.47 +  41.18 -  41.47 =  41.18   (down 0.29 = 20.00 x 1.45%)

        Until S11-e the formulas charged FICA on the gross, which the premium
        does not move, so SS and Medicare stayed on the stub's figures.  Net
        ``3,060.00 - 220.00 - (240.00 + 142.00 + 176.08 + 41.18) = 2,240.74``.
        """
        self._health_is_all_payroll_taxes(owner, db)
        self._stub(owner, _taxes("240.00", "143.00", "177.32", "41.47"))
        owner["health"].amount = Decimal("220.00")
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert _four(breakdown) == (
            Decimal("240.00"), Decimal("142.00"), Decimal("176.08"), Decimal("41.18"),
        )
        assert breakdown.earnings.net_pay == Decimal("2240.74")

    def test_a_stub_still_recording_health_income_tax_only_prices_fica_short(self, owner, db):
        """The ruling's stated cost: a stub not yet re-picked prices FICA short by 7.65% of the line.

        The stub records Health under "Pre-tax, income tax only" (R-SAL58: the
        stub's side reads the kind the STUB records), while it prints the
        employer's real figures, SS 177.32 and Medicare 41.47.  Its side's
        formulas therefore charge FICA on the gross, 189.72 and 44.37::

            SS       177.32 + 177.32 - 189.72 = 164.92   (12.40 short)
            Medicare  41.47 +  41.47 -  44.37 =  38.57   ( 2.90 short)

        ``12.40 + 2.90 = 15.30 = 200.00 x 7.65%``, until the owner re-picks
        the stub's Health line, which the entry door lists as a kind that
        differs.  State and federal read both pre-tax kinds alike: 143.00 and
        240.00.
        """
        self._health_is_all_payroll_taxes(owner, db)
        self._stub(
            owner, _taxes("240.00", "143.00", "177.32", "41.47"),
            health_kind=PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert _four(breakdown) == (
            Decimal("240.00"), Decimal("143.00"), Decimal("164.92"), Decimal("38.57"),
        )

    def test_a_stub_without_the_line_is_a_stub_of_other_lines(self, owner, db):
        """The all-payroll-taxes kind is taxed: a stub lacking the line loses to one with it.

        The 01-16 stub records both lines; the later 01-30 stub records Phone
        alone (a paycheck that skipped Health), printing the employer's
        figures on 3,060.00 with nothing excluded: state 153.00, SS 189.72,
        Medicare 44.37.  The 02-13 paycheck carries Health, so the 01-16 stub
        is its one same-lines stub and prices it at its own pay: its four
        figures exactly, federal ``240.00``.  Were the kind left out of the
        taxed set (``_stubs.TAXED_KINDS``), Health would drop from "the same
        lines" on every side, both stubs would match, and the later 01-30
        would price it: federal ``250.00``, and the stub the Taxes tab names
        the wrong one.
        """
        self._health_is_all_payroll_taxes(owner, db)
        self._stub(owner, _taxes("240.00", "143.00", "177.32", "41.47"))
        add_test_pay_stub(
            owner["profile"], date(2026, 1, 30), "3000.00",
            _taxes("250.00", "153.00", "189.72", "44.37"),
            lines=((owner["phone"], "60.00"),),
        )
        db.session.commit()
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert _four(breakdown) == (
            Decimal("240.00"), Decimal("143.00"), Decimal("177.32"), Decimal("41.47"),
        )

    def test_a_one_off_of_the_kind_changes_the_stubs_taxes(self, owner, db):
        """R-SAL123: a one-off of the all-payroll-taxes kind makes a stub one of other lines.

        The 01-30 stub records both lines and a made-up ``$40.00`` "Retro
        FSA" one-off of the kind, printing what it leaves: taxable and FICA
        wages ``3,060.00 - 200.00 - 40.00 = 2,820.00``, so state 141.00, SS
        174.84 (2,820.00 x 6.2%) and Medicare 40.89 (2,820.00 x 1.45% =
        40.89).  The one-off changes its taxes, so the stub page says so
        (``one_offs_change_taxes``) and the 01-16 stub, of the paycheck's own
        lines, prices 02-13: federal ``240.00``.  Read as changing no tax, the
        01-30 stub would price it: federal ``250.00``.
        """
        self._health_is_all_payroll_taxes(owner, db)
        self._stub(owner, _taxes("240.00", "143.00", "177.32", "41.47"))
        later = add_test_pay_stub(
            owner["profile"], date(2026, 1, 30), "3000.00",
            _taxes("250.00", "141.00", "174.84", "40.89"),
            lines=(
                (owner["health"], "200.00", PaycheckLineKindEnum.PRE_FICA_DEDUCTION),
                (owner["phone"], "60.00"),
            ),
        )
        later.one_offs.append(PayStubOneOff(
            name="Retro FSA", amount=Decimal("40.00"),
            paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
            ),
        ))
        db.session.commit()
        assert paycheck_calculator.one_offs_change_taxes(later.one_offs) is True
        breakdown = _priced(owner, date(2026, 2, 13))
        assert breakdown.taxes.stub_payday == date(2026, 1, 16)
        assert _four(breakdown) == (
            Decimal("240.00"), Decimal("143.00"), Decimal("177.32"), Decimal("41.47"),
        )
