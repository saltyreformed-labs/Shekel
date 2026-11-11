"""A "Pre-tax, all payroll taxes" line leaves the Social Security and Medicare wages too.

Plan step **salary:S11-e** (ruling **plan#2639**, "A new line kind"; ledger row
**SAL-566**): the pre-tax kind split in two.  A ``pre_fica_deduction`` (a
Section 125 premium, a flexible spending account) leaves the Social Security
and Medicare wages as well as the income-tax base; a ``pre_tax_deduction`` (a
retirement deferral) leaves the income-tax base alone, as every pre-tax line
did before (``test_pre_tax_deductions.py`` grades that kind).  Every figure is
made up and worked by hand in its case.

The shared fixtures: ``simple_bracket_set`` (10% to $50,000, 22% above, a
$15,000 standard deduction), ``nc_state_config`` (4.5% flat, no standard
deduction) and ``FakeFicaRules`` (6.2% Social Security on a $168,600 base,
1.45% Medicare).  The owner is paid ``$2,307.69`` a paycheck ($60,000 a year
over 26); with no line the established baseline is federal 173.08, state
103.85, Social Security 143.08, Medicare 33.46.
"""

from datetime import date
from decimal import Decimal

from app.services.paycheck_calculator import calculate_paycheck, project_salary
from app.services.paycheck_calculator._calendar_questions import _get_cumulative_wages
from tests._tax_configs import hand_built_law
from tests._test_helpers import payroll_basis

from ._helpers import FakeDeduction, FakeFicaRules, FakeProfile, _period, _timing_id


def _owner(*lines, pay_from=date(2026, 1, 16)):
    """The module docstring's owner, with *lines*."""
    return FakeProfile(
        pay=Decimal("2307.69"), pay_from=pay_from,  # $60,000.00 a year / 26
        lines=list(lines), created_at=date(2026, 1, 1),
    )


def _health(amount="200"):
    """A Section 125 Health premium: the all-payroll-taxes kind."""
    return FakeDeduction(name="Health", amount=amount, paycheck_line_kind="pre_fica_deduction")


class TestTheAllPayrollTaxesKindOnOnePaycheck:
    """One paycheck: the kind leaves every tax base, the income-tax-only kind one."""

    def test_a_health_line_leaves_social_security_and_medicare_wages_too(
        self, simple_tax_configs,
    ):
        """$200 of Health lowers all four taxes; a $200 401(k) lowers only the income taxes.

        Hand calculation::

            gross                  2,307.69
            FICA wages             2,307.69 - 200.00 = 2,107.69
            taxable                2,307.69 - 200.00 = 2,107.69

            federal  (59,999.94 - 5,200.00 - 15,000.00) x 10% = 3,979.99 / 26 = 153.08
            state     2,107.69 x 26 x 4.5% = 2,466.00 / 26                    =  94.85
            SS        2,107.69 x 6.2%  = 130.67678 -> 130.68   (baseline 143.08)
            Medicare  2,107.69 x 1.45% =  30.56150 ->  30.56   (baseline  33.46)

            net  2,307.69 - 200.00 - (153.08 + 94.85 + 130.68 + 30.56) = 1,698.52

        Social Security and Medicare drop ``12.40 + 2.90 = 15.30``, which is
        ``200.00 x 7.65%``: what the employer exempts and the formulas charged
        on the gross until S11-e.  The federal and state figures are the
        income-tax-only kind's (``test_pre_tax_deductions.py``), because both
        kinds leave the income-tax base.
        """
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        breakdown = calculate_paycheck(
            payroll_basis(_owner(_health()), [period]), period, simple_tax_configs,
        )
        assert breakdown.earnings.taxable_income == Decimal("2107.69")
        assert breakdown.taxes.federal == Decimal("153.08")
        assert breakdown.taxes.state == Decimal("94.85")
        assert breakdown.taxes.social_security == Decimal("130.68")
        assert breakdown.taxes.medicare == Decimal("30.56")
        assert breakdown.earnings.net_pay == Decimal("1698.52")
        assert breakdown.deductions.total_pre_fica == Decimal("200")

    def test_both_pre_tax_kinds_on_one_paycheck_leave_different_bases(
        self, simple_tax_configs,
    ):
        """$200 Health (all payroll taxes) and $100 401(k) (income tax only).

        Hand calculation::

            FICA wages  2,307.69 - 200.00          = 2,107.69
            taxable     2,307.69 - 200.00 - 100.00 = 2,007.69

            federal  (59,999.94 - 7,800.00 - 15,000.00) x 10% = 3,719.99 / 26 = 143.08
            state     2,007.69 x 26 x 4.5% = 2,349.00 / 26                    =  90.35
            SS        2,107.69 x 6.2%  -> 130.68
            Medicare  2,107.69 x 1.45% ->  30.56

            net  2,307.69 - 300.00 - (143.08 + 90.35 + 130.68 + 30.56) = 1,613.02

        The 401(k) moves the income taxes and not Social Security or Medicare;
        the Health line moves all four.  Both sit in the breakdown's one
        ``pre_tax`` list, Health first (waterfall order).
        """
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        breakdown = calculate_paycheck(
            payroll_basis(
                _owner(
                    FakeDeduction(name="401k", amount="100"),
                    _health(),
                ),
                [period],
            ),
            period, simple_tax_configs,
        )
        assert breakdown.earnings.taxable_income == Decimal("2007.69")
        assert breakdown.taxes.federal == Decimal("143.08")
        assert breakdown.taxes.state == Decimal("90.35")
        assert breakdown.taxes.social_security == Decimal("130.68")
        assert breakdown.taxes.medicare == Decimal("30.56")
        assert breakdown.earnings.net_pay == Decimal("1613.02")
        assert [line.name for line in breakdown.deductions.pre_tax] == ["Health", "401k"]
        assert [line.paycheck_line_kind_id for line in breakdown.deductions.pre_tax] == [
            _timing_id("pre_fica_deduction"), _timing_id("pre_tax_deduction"),
        ]
        assert breakdown.deductions.total_pre_tax == Decimal("300")
        assert breakdown.deductions.total_pre_fica == Decimal("200")

    def test_lines_above_the_gross_floor_the_wages_at_zero(self, simple_tax_configs):
        """$2,500 of Health against a 2,307.69 gross: Social Security and Medicare are $0.00.

        Hand calculation::

            FICA wages  max(2,307.69 - 2,500.00, 0) = 0.00
            taxable     max(2,307.69 - 2,500.00, 0) = 0.00
            SS          0.00 x 6.2%  = 0.00
            Medicare    0.00 x 1.45% = 0.00

        ``waterfall_fica_wages`` floors at $0.00, as ``waterfall_taxable``
        does.  Without the floor the wages would be -192.31: Social Security
        would still read 0.00, held by its own wage-base clamp
        (``tax_calculator.capped_social_security``), but Medicare would be
        ``-192.31 x 1.45% = -2.788495 -> -2.79`` and RAISE the net (measured
        with the floor removed).
        """
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        breakdown = calculate_paycheck(
            payroll_basis(_owner(_health("2500")), [period]), period, simple_tax_configs,
        )
        assert breakdown.deductions.total_pre_fica == Decimal("2500")
        assert breakdown.earnings.taxable_income == Decimal("0.00")
        assert breakdown.taxes.social_security == Decimal("0.00")
        assert breakdown.taxes.medicare == Decimal("0.00")


class TestTheYearToDateCountsSocialSecurityWages:
    """The wage-base total sums each earlier payday's FICA wages, not its gross."""

    def test_the_year_to_date_leaves_out_the_health_line(self):
        """Two earlier paydays of ``2,307.69 - 200.00 = 2,107.69`` sum to 4,215.38.

        Until S11-e the total summed the gross, ``2 x 2,307.69 = 4,615.38``,
        counting the Health line toward the Social Security wage base.
        """
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        basis = payroll_basis(_owner(_health(), pay_from=date(2026, 1, 2)), periods)
        assert _get_cumulative_wages(basis, date(2026, 1, 30)) == Decimal("4215.38")

    def test_the_wage_base_is_reached_by_social_security_wages(
        self, simple_bracket_set, nc_state_config,
    ):
        """On a made-up $5,000.00 base the third paycheck is charged on what is left of it.

        Hand calculation (FICA wages 2,107.69 a paycheck)::

            01-02  year-to-date      0.00  SS 2,107.69 x 6.2%           = 130.68
            01-16  year-to-date  2,107.69  SS 2,107.69 x 6.2%           = 130.68
            01-30  year-to-date  4,215.38  SS (5,000.00 - 4,215.38) x 6.2%
                                              = 784.62 x 6.2% = 48.64644 ->  48.65

        Medicare has no base: 30.56 on each.  Counting the gross, as the
        total did until S11-e, the third paycheck would have been charged
        ``(5,000.00 - 4,615.38) x 6.2% = 23.85``.
        """
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        configs = hand_built_law(
            2026,
            bracket_set=simple_bracket_set,
            state_config=nc_state_config,
            fica_config=FakeFicaRules(ss_wage_base="5000"),
        )
        breakdowns = project_salary(
            payroll_basis(_owner(_health(), pay_from=date(2026, 1, 2)), periods),
            periods, configs,
        )
        assert [bd.taxes.social_security for bd in breakdowns] == [
            Decimal("130.68"), Decimal("130.68"), Decimal("48.65"),
        ]
        assert [bd.taxes.medicare for bd in breakdowns] == [Decimal("30.56")] * 3
