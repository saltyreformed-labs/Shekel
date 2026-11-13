"""
Shekel Budget App -- Salary Cockpit Producer Tests

Unit tests for :mod:`app.services.salary_cockpit_service`, the pure
producers behind the salary cockpit and projection summary.  The
functions take ``(period, breakdown)`` pairs and return plain Decimal
context, so these tests hand-build breakdowns and hand-compute every
expected figure (no DB, no Flask).

Scenario shared by most tests (7 periods, one merit raise, two third
paychecks) -- salary A = $52,000 for periods 0-3, salary B = $54,000
for periods 4-6:

  idx start        salary  net     kind
  0   2026-05-01   A       1400    regular
  1   2026-05-15   A       1600    third paycheck
  2   2026-06-01   A       1400    regular  (contains today 2026-06-05)
  3   2026-06-15   A       1400    regular
  4   2026-07-01   B       1450    regular  (raise event)
  5   2026-07-15   B       1450    regular
  6   2026-11-01   B       1650    third paycheck
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services import salary_cockpit_service as svc
from app.services.pay_banner import ForecastRaise, PayBanner, RecordedChange
from app.services.pay_calendar import DerivedPeriod, PayCadence
from app.services.pay_rhythm import FixedDays
from app.services.paycheck_calculator import (
    DeductionBreakdown,
    PricedLine,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)
from app.services.salary_raises import RaiseTerms
from app.services.tax_config_service import TaxLawBasis
from tests._tax_configs import own_year_rules

TODAY = date(2026, 6, 5)


def _fake_period(period_id, start_date, end_date):
    """A REAL :class:`DerivedPeriod` (the producers read id/start/end only).

    It was a ``_FakePeriod`` dataclass with an ``id`` field until pay-calendar
    plan step C2-f2d-3 moved these producers onto the derived calendar.  A
    duck type with the wrong field name would have kept passing here while
    every production caller broke, so the production value is built instead.
    """
    return DerivedPeriod(
        period_id=period_id,
        period_index=period_id - 1,
        start_date=start_date,
        end_date=end_date,
        end_is_projected=False,
    )


def _raise_banner(type_name, percentage):
    """A banner naming one percentage raise of *type_name*, as the engine builds one.

    The producers here read a banner's words and directions, never a raise's
    date, so every fake raise is dated 2026-07-01.
    """
    return PayBanner((ForecastRaise(RaiseTerms(
        effective_date=date(2026, 7, 1), is_recurring=False,
        percentage=Decimal(percentage), flat_amount=None, terminal_year=None,
        raise_type_name=type_name,
    )),))


#: A 2.5% merit raise's banner: "Merit +2.5%".
_MERIT = _raise_banner("merit", "0.0250")
#: A 3% cost-of-living raise's banner: "Cola +3%".
_COLA = _raise_banner("cola", "0.0300")


#: The rhythm a paycheck here is priced at unless a case says otherwise.
_BIWEEKLY = PayCadence(FixedDays(14))
_WEEKLY = PayCadence(FixedDays(7))


def _priced(lines, kind):
    """Return ``(name, amount_str)`` pairs as the priced lines of *kind*.

    A priced line names the kind it was priced as, required since plan step
    salary:S11-e (ledger row SAL-597), so every fake line here names the kind
    of the list it is built into.
    """
    kind_id = ref_cache.paycheck_line_kind_id(kind)
    return [
        PricedLine(name=name, amount=Decimal(amount), paycheck_line_kind_id=kind_id)
        for name, amount in lines
    ]


def _pair(
    pid, start, end, annual, gross, net, *,
    taxable="0", is_third=False, banner=PayBanner(),
    federal="0", state="0", ss="0", medicare="0",
    pre=(), post=(), taxable_lines=(), after_tax=(), cadence=_BIWEEKLY,
    stub_payday=None, rules=None,
):
    """Build a ``(period, breakdown)`` pair from plain values.

    ``pre`` / ``post`` are iterables of ``(name, amount_str)`` deduction
    lines; ``taxable_lines`` / ``after_tax`` the two earning kinds' (plan
    step salary:R18-b).  ``gross`` is the figure the engine would report --
    base plus the taxable lines -- and ``base_biweekly`` is derived from it
    here so the fake carries the engine's own identity.  Every monetary
    value is constructed from a string.  ``cadence`` is the rhythm the
    paycheck was priced at (ruling R-SAL70); biweekly unless a case crosses
    an era change.  ``stub_payday`` is the payday of the pay stub the
    engine priced the four taxes from, ``None`` for the tax formulas (plan
    step salary:S11-c-2c).  ``rules`` is which year's tax rules priced them
    (plan step salary:X-at-6): *start*'s own year's, every part, unless a
    case says otherwise.
    """
    period = _fake_period(pid, start, end)
    breakdown = PaycheckBreakdown(
        period=PeriodInfo(
            period.start_date, pid,
            is_third_paycheck=is_third, banner=banner,
            cadence=cadence,
        ),
        earnings=Earnings(
            annual_salary=Decimal(annual),
            base_biweekly=Decimal(gross) - sum(
                (Decimal(a) for _n, a in taxable_lines), Decimal("0"),
            ),
            gross_biweekly=Decimal(gross),
            taxable_income=Decimal(taxable),
            net_pay=Decimal(net),
            taxable=_priced(taxable_lines, PaycheckLineKindEnum.TAXABLE_EARNING),
            after_tax=_priced(after_tax, PaycheckLineKindEnum.AFTER_TAX_EARNING),
        ),
        taxes=TaxLines(
            federal=Decimal(federal), state=Decimal(state),
            social_security=Decimal(ss), medicare=Decimal(medicare),
            stub_payday=stub_payday,
            rules=own_year_rules(start.year) if rules is None else rules,
        ),
        deductions=DeductionBreakdown(
            pre_tax=_priced(pre, PaycheckLineKindEnum.PRE_TAX_DEDUCTION),
            post_tax=_priced(post, PaycheckLineKindEnum.POST_TAX_DEDUCTION),
        ),
    )
    return period, breakdown


def _scenario():
    """Return the shared 7-period scenario as an ordered pairs list."""
    return [
        _pair(10, date(2026, 5, 1), date(2026, 5, 14), "52000", "2000", "1400"),
        _pair(11, date(2026, 5, 15), date(2026, 5, 28), "52000", "2000", "1600",
              is_third=True),
        _pair(12, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
              taxable="1800", state="150", ss="124", medicare="26",
              pre=[("401k", "200")], post=[("Roth", "100")]),
        _pair(13, date(2026, 6, 15), date(2026, 6, 28), "52000", "2000", "1400"),
        _pair(14, date(2026, 7, 1), date(2026, 7, 14), "54000", "2076.92", "1450",
              banner=_MERIT),
        _pair(15, date(2026, 7, 15), date(2026, 7, 28), "54000", "2076.92", "1450"),
        _pair(16, date(2026, 11, 1), date(2026, 11, 14), "54000", "2076.92", "1650",
              is_third=True),
    ]


class TestBaseRegularNet:
    """base_regular_net: nearest regular paycheck at the same pay rate."""

    def test_prefers_previous_regular_same_salary(self):
        """The idx-1 third paycheck resolves to idx-0's regular net (prev)."""
        pairs = _scenario()
        # idx 1 is a third paycheck at salary A; nearest regular same-salary
        # preferring previous is idx 0 => net 1400.00.
        assert svc.base_regular_net(pairs, 1) == Decimal("1400.00")

    def test_falls_back_to_next_when_no_previous(self):
        """A leading third paycheck falls forward to the next regular net."""
        pairs = [
            _pair(1, date(2026, 5, 1), date(2026, 5, 14), "52000", "2000", "1600",
                  is_third=True),
            _pair(2, date(2026, 5, 15), date(2026, 5, 28), "52000", "2000", "1400"),
        ]
        # No earlier period; next regular same salary is idx 1 => 1400.00.
        assert svc.base_regular_net(pairs, 0) == Decimal("1400.00")

    def test_a_third_paycheck_straddling_a_raise_matches_a_regular_one_at_its_rate(self):
        """The straddle third paycheck compares with a regular paycheck at its pay rate (plan#3123).

        Made-up: $2,000.00 a paycheck and 5% from a Wednesday, 2/10 of the
        third paycheck's weekdays at the old pay: its base is 2,000.00 x 2/10
        + 2,100.00 x 8/10 = 2,080.00, its rate 2,100.00 (2,100.00 x 26 =
        54,600.00 a year), and with its $300.00 line skipped and no tax it
        nets 2,080.00.  No paycheck shares that base, so the base key fell
        back to its own net, a delta of $0.00; the regular paycheck at
        2,100.00 nets 2,100.00 - 300.00 = 1,800.00: 2,080.00 - 1,800.00 =
        +280.00.  The earlier regular paycheck, at 2,000.00, is a different
        rate and is passed over.
        """
        pairs = [
            _pair(1, date(2027, 7, 1), date(2027, 7, 14), "52000", "2000", "1700"),
            _pair(2, date(2027, 7, 15), date(2027, 7, 28), "52000", "2000", "1700"),
            _pair(3, date(2027, 7, 29), date(2027, 8, 11), "54600", "2080", "2080",
                  is_third=True),
            _pair(4, date(2027, 8, 12), date(2027, 8, 25), "54600", "2100", "1800"),
        ]
        assert svc.base_regular_net(pairs, 2) == Decimal("1800.00")
        assert svc.next_third_after(pairs, date(2027, 7, 20)) == {
            "period_start": date(2027, 7, 29),
            "net": Decimal("2080.00"),
            "delta": Decimal("280.00"),
        }

    def test_one_yearly_salary_at_two_rhythms_is_two_pay_rates(self):
        """52,000.00 a year biweekly (2,000.00) and weekly (1,000.00) do not match (R-SAL70)."""
        pairs = [
            _pair(1, date(2027, 3, 1), date(2027, 3, 7), "52000", "1000", "900",
                  cadence=_WEEKLY),
            _pair(2, date(2027, 3, 8), date(2027, 3, 21), "52000", "2000", "2000",
                  is_third=True),
            _pair(3, date(2027, 3, 22), date(2027, 4, 4), "52000", "2000", "1700"),
        ]
        # The weekly paycheck shares the yearly figure but not the rate, so the
        # nearest match is the later biweekly one: 1,700.00.
        assert svc.base_regular_net(pairs, 1) == Decimal("1700.00")

    def test_a_regular_paycheck_whose_rate_moved_before_its_pay_is_passed_over(self):
        """A regular paycheck at the new rate but the old base is not a match.

        Made-up, as task plan#3122's weekend case: 3% from the Saturday that
        ends the second paycheck's period, so its rate is 2,060.00 (53,560.00
        a year) while it pays 2,000.00 and nets 1,700.00.  The third paycheck
        after it pays 2,060.00 and skips its $300.00 line: it nets 2,060.00.
        The match is the later regular paycheck paying 2,060.00, netting
        1,760.00, not the earlier one at the old pay: 2,060.00 - 1,760.00.
        """
        pairs = [
            _pair(1, date(2028, 6, 23), date(2028, 7, 6), "52000", "2000", "1700"),
            _pair(2, date(2028, 7, 7), date(2028, 7, 20), "53560", "2000", "1700"),
            _pair(3, date(2028, 7, 21), date(2028, 8, 3), "53560", "2060", "2060",
                  is_third=True),
            _pair(4, date(2028, 8, 4), date(2028, 8, 17), "53560", "2060", "1760"),
        ]
        assert svc.base_regular_net(pairs, 2) == Decimal("1760.00")


class TestNextEvents:
    """next_raise_after / next_third_after strictly after today."""

    def test_next_raise_after_today(self):
        """First raise event after 2026-06-05 is the 2026-07-01 merit step, its banner whole.

        The chip reads the banner's words and direction off the value: "Merit
        +2.5%", a raise.
        """
        result = svc.next_raise_after(_scenario(), TODAY)
        assert result == {
            "banner": _MERIT,
            "period_start": date(2026, 7, 1),
        }
        assert result["banner"].text == "Merit +2.5%"
        assert result["banner"].cuts == PayBanner()

    def test_a_recorded_cut_after_today_is_the_next_and_says_so(self):
        """A recorded cut on 07-01 is the next banner, and the value says it is a cut (R-SAL85).

        The chip read its words before task plan#3168 and named every banner
        a raise in green.  Made-up: $2,000.00 to $1,930.00.
        """
        cut = PayBanner((RecordedChange(Decimal("-70.00"), yearly=False),))
        pairs = [
            _pair(1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400"),
            _pair(2, date(2026, 7, 1), date(2026, 7, 14), "50180", "1930", "1350",
                  banner=cut),
        ]
        result = svc.next_raise_after(pairs, TODAY)
        assert result == {"banner": cut, "period_start": date(2026, 7, 1)}
        assert result["banner"].cuts == cut

    def test_next_third_after_today_delta(self):
        """Next third paycheck (2026-11-01) delta = 1650 - base 1450 = 200."""
        result = svc.next_third_after(_scenario(), TODAY)
        # base regular net at salary B preferring previous is idx 5 (1450);
        # delta = 1650.00 - 1450.00 = 200.00.
        assert result == {
            "period_start": date(2026, 11, 1),
            "net": Decimal("1650.00"),
            "delta": Decimal("200.00"),
        }

    def test_next_raise_none_when_no_future_raise(self):
        """No raise after today returns None."""
        pairs = [_pair(1, date(2026, 1, 1), date(2026, 1, 14), "52000", "2000", "1400")]
        assert svc.next_raise_after(pairs, TODAY) is None

    def test_next_raise_is_the_next_badged_paycheck(self):
        """Today 07-03 and a July 1 raise: 07-16, the paycheck the engine badges.

        The engine badges a raise on the one paycheck it first reaches (ruling
        plan#3092): 07-02 pays for days before July 1 and carries nothing,
        07-16 carries the raise, and 07-30 carries nothing.  Made-up figures.
        """
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "52000", "2000", "1400"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "53560", "2048", "1430",
                  banner=_COLA),
            _pair(3, date(2026, 7, 30), date(2026, 8, 12), "53560", "2060", "1440"),
            _pair(4, date(2027, 1, 7), date(2027, 1, 20), "54899", "2111.50", "1475",
                  banner=_MERIT),
        ]
        assert svc.next_raise_after(pairs, date(2026, 7, 3)) == {
            "banner": _COLA,
            "period_start": date(2026, 7, 16),
        }

    def test_an_equal_label_after_today_is_the_next_raise(self):
        """Two equal raises reaching 08-01 and 08-15; today 08-05: 08-15.

        Each banner is its own raise.  The run-start filter this replaced
        (task plan#3111) read 08-15 as the tail of a run that started on or
        before today and skipped it, answering None.
        """
        pairs = [
            _pair(1, date(2026, 8, 1), date(2026, 8, 14), "53560", "2060", "1440",
                  banner=_COLA),
            _pair(2, date(2026, 8, 15), date(2026, 8, 28), "55167", "2121.80", "1480",
                  banner=_COLA),
        ]
        assert svc.next_raise_after(pairs, date(2026, 8, 5)) == {
            "banner": _COLA,
            "period_start": date(2026, 8, 15),
        }


class TestYearlyNetTotals:
    """yearly_net_totals sums net per calendar year, ordered."""

    def test_single_year_total(self):
        """All 7 nets fall in 2026: 1400+1600+1400+1400+1450+1450+1650 = 10350."""
        result = svc.yearly_net_totals(_scenario())
        assert result == [(2026, Decimal("10350.00"))]

    def test_multi_year_ordered(self):
        """Two years sum independently and sort ascending by year."""
        pairs = [
            _pair(1, date(2026, 12, 20), date(2027, 1, 2), "52000", "2000", "1400"),
            _pair(2, date(2027, 1, 3), date(2027, 1, 16), "52000", "2000", "1450"),
        ]
        # 2026: 1400.00; 2027: 1450.00 (period 1 is keyed by its 2026 start).
        assert svc.yearly_net_totals(pairs) == [
            (2026, Decimal("1400.00")),
            (2027, Decimal("1450.00")),
        ]


class TestSubstitutedTaxRules:
    """substituted_tax_rules: one basis per tax year priced on another year's rules."""

    def test_a_carried_year_and_two_substituted_ones(self):
        """2026 on its own rules; 2027 and 2028 on 2026's: one basis each for the two.

        Two 2027 paychecks carry equal bases built apart, so a year is one
        entry by value, not by object; 2026 is on its own rules and is no
        entry at all.  Ascending by tax year.
        """
        def on_2026s(tax_year):
            return TaxLawBasis(
                tax_year=tax_year, federal_year=2026, state_year=2026, fica_year=2026,
            )

        pairs = [
            _pair(1, date(2026, 12, 4), date(2026, 12, 17), "52000", "2000", "1400"),
            _pair(2, date(2027, 1, 1), date(2027, 1, 14), "52000", "2000", "1400",
                  rules=on_2026s(2027)),
            _pair(3, date(2027, 1, 15), date(2027, 1, 28), "52000", "2000", "1400",
                  rules=on_2026s(2027)),
            _pair(4, date(2028, 1, 14), date(2028, 1, 27), "52000", "2000", "1400",
                  rules=on_2026s(2028)),
        ]

        assert svc.substituted_tax_rules(pairs) == [on_2026s(2027), on_2026s(2028)]

    def test_the_years_come_back_ascending_whatever_order_the_pairs_are_in(self):
        """A 2028 paycheck handed before a 2027 one: still 2027 first, then 2028.

        The ledger's saved periods arrive payday ascending, so its pairs are
        already in year order; the answer's order is the producer's own
        contract, so a caller handing any order gets the same lines.
        """
        def on_2026s(tax_year):
            return TaxLawBasis(
                tax_year=tax_year, federal_year=2026, state_year=2026, fica_year=2026,
            )

        pairs = [
            _pair(1, date(2028, 1, 14), date(2028, 1, 27), "52000", "2000", "1400",
                  rules=on_2026s(2028)),
            _pair(2, date(2027, 1, 1), date(2027, 1, 14), "52000", "2000", "1400",
                  rules=on_2026s(2027)),
        ]

        assert svc.substituted_tax_rules(pairs) == [on_2026s(2027), on_2026s(2028)]

    def test_every_paycheck_on_its_own_years_rules_is_no_entry(self):
        """The shared scenario, all 2026 on 2026's own rules: nothing to say."""
        assert svc.substituted_tax_rules(_scenario()) == []


class TestBuildChips:
    """build_chips: focused-period hero figures + next-event chips."""

    def test_chips_from_focused_period(self):
        """Chips read gross/annual/take-home off the focused breakdown."""
        pairs = _scenario()
        focused = pairs[2][1]  # idx 2, salary A, gross 2000, net 1400
        chips = svc.build_chips(pairs, focused, TODAY)
        assert chips["gross"] == Decimal("2000")
        assert chips["annual_salary"] == Decimal("52000")
        # take-home = net/gross*100 = 1400/2000*100 = 70.
        assert chips["take_home_rate_pct"] == Decimal("70")
        # The next banner, the merit raise's: "Merit +2.5%".
        assert chips["next_raise"] == {
            "banner": _MERIT,
            "period_start": date(2026, 7, 1),
        }
        # third-paycheck chip carries only period_start + delta (200.00).
        assert chips["third_paycheck"] == {
            "period_start": date(2026, 11, 1),
            "delta": Decimal("200.00"),
        }


class TestBuildComposition:
    """build_composition: segment totals + percentages of gross."""

    def test_percentages(self):
        """gross 2000 -> net 70%, pre-tax 10%, taxes 15%, post-tax 5%."""
        breakdown = _scenario()[2][1]  # gross 2000, net 1400, pre 200, tax 300, post 100
        comp = svc.build_composition(breakdown)
        assert comp["gross"] == Decimal("2000")
        assert comp["taxable"] == Decimal("1800")
        assert comp["net"] == Decimal("1400")
        assert comp["pre_tax_total"] == Decimal("200")
        assert comp["taxes_total"] == Decimal("300")  # 0 + 150 + 124 + 26
        assert comp["post_tax_total"] == Decimal("100")
        # of gross 2000: 1400->70.0, 200->10.0, 300->15.0, 100->5.0 (sum 100.0).
        assert comp["pct_net"] == Decimal("70.0")
        assert comp["pct_pre_tax"] == Decimal("10.0")
        assert comp["pct_taxes"] == Decimal("15.0")
        assert comp["pct_post_tax"] == Decimal("5.0")

    def test_names_the_stub_that_priced_the_taxes(self):
        """The card carries the engine's pricing stub, read off the tax lines (R-SAL100)."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            taxable="1800", state="150", ss="124", medicare="26",
            stub_payday=date(2026, 5, 15),
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["tax_stub_payday"] == date(2026, 5, 15)

    def test_names_the_formulas_when_no_stub_priced_the_taxes(self):
        """No stub on or before the payday: the key is None, the formulas' answer."""
        comp = svc.build_composition(_scenario()[2][1])
        assert comp["tax_stub_payday"] is None

    def test_names_the_rules_that_priced_the_taxes(self):
        """The card carries the tax lines' own basis, the engine's answer (R-SAL75).

        A 2028 paycheck priced on 2027's rules: the key is that very basis,
        so the cockpit says "2028 on 2027's tax rules" off the paycheck.
        """
        rules = TaxLawBasis(
            tax_year=2028, federal_year=2027, state_year=2027, fica_year=2027,
        )
        breakdown = _pair(
            1, date(2028, 6, 1), date(2028, 6, 14), "52000", "2000", "1400",
            rules=rules,
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["tax_rules"] is rules

    def test_zero_gross_percentages_are_zero(self):
        """A zero-gross period yields 0% segments (no division by zero)."""
        breakdown = _pair(1, date(2026, 1, 1), date(2026, 1, 14), "0", "0", "0")[1]
        comp = svc.build_composition(breakdown)
        assert comp["pct_net"] == Decimal("0")

    def test_no_earning_line_leaves_the_card_as_it_was(self):
        """Without an earning line the added keys are zero or equal net, and pct_net is of net."""
        comp = svc.build_composition(_scenario()[2][1])
        assert comp["base"] == Decimal("2000")
        assert comp["taxable_earnings_total"] == Decimal("0")
        assert comp["after_tax_total"] == Decimal("0")
        assert comp["kept_from_gross"] == comp["net"] == Decimal("1400")
        assert comp["pct_net"] == Decimal("70.0")

    def test_a_taxable_earning_is_inside_gross_and_the_split_is_stated(self):
        """gross 2000 = base 1900 + a $100 taxable line; the spine is unchanged, the footer knows."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            taxable="1800", state="150", ss="124", medicare="26",
            pre=[("401k", "200")], post=[("Roth", "100")],
            taxable_lines=[("Phone Allowance", "100")],
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["gross"] == Decimal("2000")
        assert comp["base"] == Decimal("1900")
        assert comp["taxable_earnings_total"] == Decimal("100")
        assert comp["kept_from_gross"] == comp["net"] == Decimal("1400")
        assert (comp["pct_net"], comp["pct_pre_tax"], comp["pct_taxes"], comp["pct_post_tax"]) == (
            Decimal("70.0"), Decimal("10.0"), Decimal("15.0"), Decimal("5.0"),
        )

    def test_an_after_tax_earning_sits_outside_gross_and_the_segments_still_sum(self):
        """net 1500 = 1400 kept of a 2000 gross + a $100 after-tax line: the net segment is 70.0, not 75.0."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1500",
            taxable="1800", state="150", ss="124", medicare="26",
            pre=[("401k", "200")], post=[("Roth", "100")],
            after_tax=[("Reimbursement", "100")],
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["net"] == Decimal("1500")
        assert comp["after_tax_total"] == Decimal("100")
        assert comp["kept_from_gross"] == Decimal("1400")
        assert comp["pct_net"] == Decimal("70.0")
        # The four segments of gross still sum to the bar.
        assert (comp["pct_net"] + comp["pct_pre_tax"] + comp["pct_taxes"]
                + comp["pct_post_tax"]) == Decimal("100.0")


class TestBuildLineRows:
    """build_line_rows: proportional bars scaled to the largest line, kind groups in waterfall order.

    Each row's ``group_label`` is the heading over its kind's group (plan step
    salary:S11-e, which split the pre-tax kind and gave the headings a map of
    their own; ruling plan#2639 names the two pre-tax labels).
    """

    def test_rows_scaled_to_largest(self):
        """401k 200 (largest) -> 100.0; Roth 100 -> 50.0."""
        breakdown = _scenario()[2][1]
        rows = svc.build_line_rows(breakdown)
        assert rows == [
            {"name": "401k", "amount": Decimal("200"), "kind": "pre_tax_deduction",
             "group_label": "Pre-tax, income tax only", "bar_pct": Decimal("100.0")},
            {"name": "Roth", "amount": Decimal("100"), "kind": "post_tax_deduction",
             "group_label": "Post-tax deductions", "bar_pct": Decimal("50.0")},
        ]

    def test_empty_when_no_lines(self):
        """A period with no lines returns an empty list."""
        assert svc.build_line_rows(_scenario()[0][1]) == []

    def test_earning_groups_bracket_the_deductions_in_waterfall_order(self):
        """Taxable earnings first, after-tax earnings last; bars scale across every group."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2045", "1500",
            pre=[("401k", "200")], post=[("Roth", "100")],
            taxable_lines=[("Phone Allowance", "45")],
            after_tax=[("Reimbursement", "400")],
        )[1]
        rows = svc.build_line_rows(breakdown)
        assert [(r["name"], r["kind"], r["group_label"], r["bar_pct"]) for r in rows] == [
            ("Phone Allowance", "taxable_earning", "Taxable earnings", Decimal("11.3")),
            ("401k", "pre_tax_deduction", "Pre-tax, income tax only", Decimal("50.0")),
            ("Roth", "post_tax_deduction", "Post-tax deductions", Decimal("25.0")),
            ("Reimbursement", "after_tax_earning", "After-tax earnings", Decimal("100.0")),
        ]

    def test_rows_sorted_desc_within_kind_groups(self):
        """Each kind group sorts amount-descending; pre-tax group first.

        Live-shape input order (calculator order): FSA 50, Vision 10,
        Dental 30, Health 200 (pre-tax); Roth 100, Life 150 (post-tax).
        Expected: Health 200, FSA 50, Dental 30, Vision 10, then Life 150,
        Roth 100.  bar_pct scales to the overall max 200:
          Health 200/200*100 = 100.0   FSA    50/200*100 = 25.0
          Dental  30/200*100 =  15.0   Vision 10/200*100 =  5.0
          Life   150/200*100 =  75.0   Roth  100/200*100 = 50.0
        """
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            pre=[("FSA", "50"), ("Vision", "10"), ("Dental", "30"), ("Health", "200")],
            post=[("Roth", "100"), ("Life", "150")],
        )[1]
        rows = svc.build_line_rows(breakdown)
        assert [(r["name"], r["amount"], r["kind"], r["bar_pct"]) for r in rows] == [
            ("Health", Decimal("200"), "pre_tax_deduction", Decimal("100.0")),
            ("FSA", Decimal("50"), "pre_tax_deduction", Decimal("25.0")),
            ("Dental", Decimal("30"), "pre_tax_deduction", Decimal("15.0")),
            ("Vision", Decimal("10"), "pre_tax_deduction", Decimal("5.0")),
            ("Life", Decimal("150"), "post_tax_deduction", Decimal("75.0")),
            ("Roth", Decimal("100"), "post_tax_deduction", Decimal("50.0")),
        ]

    def test_equal_amounts_keep_stable_calculator_order(self):
        """Ties within a group keep the calculator's original order."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            pre=[("Alpha", "25"), ("Beta", "25"), ("Gamma", "50")],
        )[1]
        rows = svc.build_line_rows(breakdown)
        # Gamma (50) leads; Alpha and Beta tie at 25 and keep their
        # calculator order (sorted() is stable).
        assert [r["name"] for r in rows] == ["Gamma", "Alpha", "Beta"]

    def test_the_two_pre_tax_kinds_are_two_groups_from_one_list(self):
        """Both pre-tax kinds share the breakdown's ``pre_tax`` list and head two groups.

        The engine's list (all-payroll-taxes lines first, plan step
        salary:S11-e): Health 200 and FSA 50 of "Pre-tax, all payroll taxes",
        then 401k 300 of "Pre-tax, income tax only".  Grouped by the kind each
        line carries, in waterfall order, each group largest-first: Health,
        FSA, then 401k, each under its own kind's heading.  Grouped by the
        breakdown's lists instead, 401k would lead the one pre-tax group and
        Health would sit under the other kind's heading.  bar_pct scales to
        the largest line, 300:
          Health 200/300*100 = 66.7   FSA 50/300*100 = 16.7   401k 100.0
        """
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
        )[1]
        breakdown.deductions = DeductionBreakdown(
            pre_tax=[
                *_priced(
                    [("Health", "200"), ("FSA", "50")],
                    PaycheckLineKindEnum.PRE_FICA_DEDUCTION,
                ),
                *_priced([("401k", "300")], PaycheckLineKindEnum.PRE_TAX_DEDUCTION),
            ],
            post_tax=[],
        )
        rows = svc.build_line_rows(breakdown)
        assert [(r["name"], r["kind"], r["group_label"], r["bar_pct"]) for r in rows] == [
            ("Health", "pre_fica_deduction", "Pre-tax, all payroll taxes", Decimal("66.7")),
            ("FSA", "pre_fica_deduction", "Pre-tax, all payroll taxes", Decimal("16.7")),
            ("401k", "pre_tax_deduction", "Pre-tax, income tax only", Decimal("100.0")),
        ]


class TestBuildChartSeries:
    """build_chart_series: staircase line with the third-paycheck base rule."""

    def test_line_carries_base_thirds_and_raises(self):
        """Third periods carry the base net on the line; spikes go to thirds."""
        series = svc.build_chart_series(_scenario(), TODAY)
        # Window: anchor idx 2 (contains today), lookback 6 -> start 0,
        # horizon +18mo covers all 7 periods.
        starts = [pt["start"] for pt in series["periods"]]
        assert starts == [
            date(2026, 5, 1), date(2026, 5, 15), date(2026, 6, 1),
            date(2026, 6, 15), date(2026, 7, 1), date(2026, 7, 15),
            date(2026, 11, 1),
        ]
        # idx 1 (third) carries base regular net 1400.00, NOT its 1600 spike;
        # idx 6 (third) carries base 1450.00, NOT 1650.
        assert series["periods"][1]["net"] == Decimal("1400.00")
        assert series["periods"][6]["net"] == Decimal("1450.00")
        # The actual third-paycheck nets are the point events.
        assert series["thirds"] == [
            {"start": date(2026, 5, 15), "net": Decimal("1600")},
            {"start": date(2026, 11, 1), "net": Decimal("1650")},
        ]
        # The one raise event, its banner's words.
        assert series["raises"] == [
            {"start": date(2026, 7, 1), "label": "Merit +2.5%"},
        ]
        assert series["today"] == TODAY

    def test_lookback_clamps_and_horizon_bounds(self):
        """Periods past the 18-month horizon are excluded."""
        pairs = _scenario() + [
            _pair(99, date(2028, 6, 1), date(2028, 6, 14), "54000", "2076.92", "1450"),
        ]
        series = svc.build_chart_series(pairs, TODAY)
        # 2028-06-01 is beyond 2026-06-05 + 18 months (2027-12-05) -> excluded.
        assert all(pt["start"].year < 2028 for pt in series["periods"])

    def test_each_badged_paycheck_emits_its_label(self):
        """Two equal raises back to back, then a different one: three labels.

        The engine badges each raise on the paycheck it first reaches (ruling
        plan#3092), so every badge is a raise and the chart paints each.  The
        run-start filter this replaced (task plan#3111) painted one label for
        the two equal ones.  Made-up figures.
        """
        pairs = [
            _pair(1, date(2026, 8, 1), date(2026, 8, 14), "53560", "2060", "1440",
                  banner=_COLA),
            _pair(2, date(2026, 8, 15), date(2026, 8, 28), "55167", "2121.80", "1480",
                  banner=_COLA),
            _pair(3, date(2026, 8, 29), date(2026, 9, 11), "56546", "2174.85", "1515",
                  banner=_MERIT),
            _pair(4, date(2026, 9, 12), date(2026, 9, 25), "56546", "2174.85", "1515"),
        ]
        series = svc.build_chart_series(pairs, date(2026, 8, 5))
        assert series["raises"] == [
            {"start": date(2026, 8, 1), "label": "Cola +3%"},
            {"start": date(2026, 8, 15), "label": "Cola +3%"},
            {"start": date(2026, 8, 29), "label": "Merit +2.5%"},
        ]


class TestBuildSalaryPath:
    """build_salary_path: forward-only annual-salary staircase + end label."""

    def test_forward_only_points_and_end_label(self):
        """Forward window from the anchor; end label formats the last point."""
        path = svc.build_salary_path(_scenario(), TODAY)
        # lookback 0 -> starts at anchor idx 2 (contains today).
        starts = [pt["start"] for pt in path["points"]]
        assert starts == [
            date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1),
            date(2026, 7, 15), date(2026, 11, 1),
        ]
        assert path["points"][0]["annual"] == Decimal("52000")
        assert path["points"][-1]["annual"] == Decimal("54000")
        # end label = last point: $54,000 at Nov 2026.
        assert path["end_label"] == "$54,000 Nov 2026"

    def test_empty_window_empty_label(self):
        """No pairs -> no points and an empty end label."""
        path = svc.build_salary_path([], TODAY)
        assert path == {"points": [], "end_label": ""}
