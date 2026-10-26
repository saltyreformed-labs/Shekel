"""FICA and the year: cumulative wages, the year's salary projection, the Social Security wage cap
and the Medicare surtax.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.tax_calculator import calculate_fica
from app.services.paycheck_calculator import project_salary, PaycheckBreakdown
from app.services.paycheck_calculator._calendar_questions import _get_cumulative_wages
from app.utils.money import ZERO
from tests._test_helpers import payroll_basis

from ._helpers import (
    TWO_PLACES,
    FakeRaise,
    _period,
    FakeStateTaxConfig,
    FakeFicaConfig,
    FakeProfile,
)


class TestCumulativeWages:
    """Tests for _get_cumulative_wages()."""

    def test_sums_prior_periods_in_same_year(self):
        """Adds gross for earlier periods."""
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        result = _get_cumulative_wages(payroll_basis(profile, all_periods), p3.start_date)

        gross_per = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                     rounding=ROUND_HALF_UP)
        assert result == gross_per * 2

    def test_first_period_zero_cumulative(self):
        """First period → 0."""
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)

        result = _get_cumulative_wages(payroll_basis(profile, [p1]), p1.start_date)
        assert result == ZERO

    def test_different_year_periods_excluded(self):
        """Prior year periods skipped."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2025, 12, 19),  # $60,000.00 a year / 26
            created_at=date(2025, 1, 1),
        )
        p_prev = _period(start_date=date(2025, 12, 19), period_id=25)
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        all_periods = [p_prev, p1]

        result = _get_cumulative_wages(payroll_basis(profile, all_periods), p1.start_date)
        assert result == ZERO


class TestProjectSalary:
    """Tests for project_salary()."""

    def test_returns_one_breakdown_per_period(self, base_profile,
                                              simple_tax_configs):
        """len(result) == len(periods)."""
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]

        result = project_salary(payroll_basis(base_profile, periods), periods, simple_tax_configs)

        assert len(result) == 3
        assert all(isinstance(r, PaycheckBreakdown) for r in result)

    def test_raise_event_appears_in_correct_period(self, simple_tax_configs):
        """raise_event populated at raise month."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 2, 13),  # $60,000.00 a year / 26
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=False)],
            created_at=date(2026, 1, 1),
        )
        periods = [
            _period(start_date=date(2026, 2, 13), period_id=1),
            _period(start_date=date(2026, 3, 13), period_id=2),
            _period(start_date=date(2026, 4, 10), period_id=3),
        ]

        result = project_salary(payroll_basis(profile, periods), periods, simple_tax_configs)

        assert result[0].period.raise_event == ""
        assert "MERIT" in result[1].period.raise_event
        assert result[2].period.raise_event == ""

    def test_recurring_raise_event_not_shown_before_effective_year(self, simple_tax_configs):
        """A recurring raise badges no event in years before its effective_year (deep-hunt #13).

        A recurring raise effective March 2027 must not show a raise
        event on the March 2026 paycheck (whose gross is unchanged), and
        must show one once it actually recurs in March 2027.  The pre-fix
        recurring branch matched on month alone, badging "MERIT" in 2026
        while apply_raises applied nothing.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 2, 13),  # $60,000.00 a year / 26
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2027, is_recurring=True)],
            created_at=date(2026, 1, 1),
        )
        # Before the effective year: no event anywhere in 2026, salary flat.
        periods_2026 = [
            _period(start_date=date(2026, 2, 13), period_id=1),
            _period(start_date=date(2026, 3, 13), period_id=2),
            _period(start_date=date(2026, 4, 10), period_id=3),
        ]
        result_2026 = project_salary(payroll_basis(profile, periods_2026), periods_2026, simple_tax_configs)
        assert all(r.period.raise_event == "" for r in result_2026)
        # 2307.69 x 26 = 59999.94: the yearly figure is pay x paychecks a
        # year since plan step salary:X-av-3a (ruling R-SAL59).
        assert result_2026[1].earnings.annual_salary == Decimal("59999.94")

        # In the effective year the raise recurs at its month: the event
        # shows at March 2027 and is absent the month before, so the fix
        # gates on the year without over-suppressing the legitimate event.
        periods_2027 = [
            _period(start_date=date(2027, 2, 12), period_id=27),
            _period(start_date=date(2027, 3, 12), period_id=28),
        ]
        result_2027 = project_salary(payroll_basis(profile, periods_2027), periods_2027, simple_tax_configs)
        assert result_2027[0].period.raise_event == ""
        assert "MERIT" in result_2027[1].period.raise_event
        # 2307.69 x 1.03 = 2376.9207 -> 2376.92 (R-SAL60); x 26 = 61799.92.
        assert result_2027[1].earnings.annual_salary == Decimal("61799.92")

    def test_empty_periods_empty_result(self, base_profile, simple_tax_configs):
        """[] → []."""
        result = project_salary(
            payroll_basis(base_profile, []), [], simple_tax_configs,
        )
        assert result == []

    def test_configs_by_year_applies_each_periods_own_year(
        self, base_profile, simple_bracket_set, standard_fica,
    ):
        """DH-#30: in configs_by_year mode each period uses its own year's configs.

        Same $60k profile and identical gross both years, but 2027 carries
        a higher state flat rate (9.0%) than 2026 (4.5%), so the 2027
        period's net pay must be strictly lower -- proving the per-period
        year selection, not a single shared config set, drives the tax.
        Revert-proof: applying a single dict to both periods would make the
        two nets equal.
        """
        configs_by_year = {
            2026: {
                "bracket_set": simple_bracket_set,
                "state_config": FakeStateTaxConfig(flat_rate="0.045"),
                "fica_config": standard_fica,
            },
            2027: {
                "bracket_set": simple_bracket_set,
                "state_config": FakeStateTaxConfig(flat_rate="0.090"),
                "fica_config": standard_fica,
            },
        }
        periods = [
            _period(start_date=date(2026, 6, 5), period_id=1),
            _period(start_date=date(2027, 6, 4), period_id=2),
        ]

        result = project_salary(
            payroll_basis(base_profile, periods), periods, configs_by_year=configs_by_year,
        )

        assert result[0].earnings.gross_biweekly == result[1].earnings.gross_biweekly
        assert result[1].earnings.net_pay < result[0].earnings.net_pay

    def test_requires_exactly_one_config_source(
        self, base_profile, simple_tax_configs,
    ):
        """ValueError when neither or both config sources are supplied."""
        periods = [_period(start_date=date(2026, 1, 2), period_id=1)]
        with pytest.raises(ValueError, match="exactly one"):
            project_salary(payroll_basis(base_profile, periods), periods)
        with pytest.raises(ValueError, match="exactly one"):
            project_salary(
            payroll_basis(base_profile, periods), periods, simple_tax_configs,
                configs_by_year={2026: simple_tax_configs},
            )


# ── FICA Wage Cap Tests ─────────────────────────────────────────


class TestFICAWageCapBoundary:
    """Tests for FICA Social Security wage cap boundary."""

    def test_fica_ss_wage_cap_boundary(
        self, biweekly_periods, simple_tax_configs
    ):
        """SS tax transitions across 26 periods for $200k salary.

        gross = 200000/26 = $7,692.31
        SS cap = $168,600, rate = 6.2%
        Full SS = 7692.31*0.062 = $476.92
        Transition at period 22:
          cumulative = 21*7692.31 = $161,538.51
          ss_taxable = 168600 - 161538.51 = $7,061.49
          ss = 7061.49*0.062 = $437.81
        Periods 23-26: cumulative >= $168,600, SS = $0.00
        Total SS = 21*476.92 + 437.81 = $10,453.13

        Medicare surtax note: at period 26, cumul+gross =
        $200,000.06 triggers surtax condition but
        (0.06*0.009) rounds to $0.00, so Medicare stays
        $111.54 for all 26 periods.
        """
        profile = FakeProfile(
            pay=Decimal("7692.31"),  # $200,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        # gross = 200000/26 = 7692.307692->7692.31
        # full SS = 7692.31*0.062 = 476.92322->476.92
        full_ss = Decimal("476.92")
        # partial at period 22:
        # cumul=161538.51, taxable=7061.49
        # 7061.49*0.062 = 437.81238->437.81
        partial_ss = Decimal("437.81")
        # Medicare = 7692.31*0.0145 = 111.538495->111.54
        expected_medicare = Decimal("111.54")

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # Periods 1-21: full SS (under cap)
        for i in range(21):
            assert results[i].taxes.social_security == full_ss, (
                f"period {i+1}: SS expected {full_ss}, "
                f"got {results[i].taxes.social_security}"
            )

        # Period 22: partial SS (crosses cap this period)
        # cumulative = 21*7692.31 = 161538.51
        # cumul + gross = 169230.82 > 168600
        assert results[21].taxes.social_security == partial_ss, (
            f"period 22 (transition): SS expected "
            f"{partial_ss}, got {results[21].taxes.social_security}"
        )

        # Periods 23-26: zero SS (already over cap)
        for i in range(22, 26):
            assert results[i].taxes.social_security == Decimal("0.00"), (
                f"period {i+1}: SS expected 0.00, "
                f"got {results[i].taxes.social_security}"
            )

        # Medicare: constant across all 26 periods (no cap)
        for i in range(26):
            assert results[i].taxes.medicare == expected_medicare, (
                f"period {i+1}: medicare expected "
                f"{expected_medicare}, "
                f"got {results[i].taxes.medicare}"
            )

        # Cumulative SS verification
        # 21*476.92 + 437.81 + 4*0.00 = 10453.13
        total_ss = sum(
            r.taxes.social_security for r in results
        )
        assert total_ss == Decimal("10453.13"), (
            f"total SS: expected 10453.13, got {total_ss}"
        )


# ── FICA Direct Boundary Tests ─────────────────────────────────


class TestFICADirectBoundary:
    """Direct unit tests of calculate_fica at exact SS wage cap.

    Tests all three SS branches in tax_calculator.calculate_fica():
      1. cumulative >= ss_wage_base  -> SS = 0
      2. cumulative + gross > ss_wage_base  -> partial SS
      3. cumulative + gross <= ss_wage_base  -> full SS
    """

    @pytest.fixture
    def fica_config(self):
        """Standard FICA config with ss_wage_base=168600."""
        return FakeFicaConfig()

    def test_ss_at_cap_zero(self, fica_config):
        """cumulative == ss_wage_base exactly: SS = 0.00.

        Branch 1: cumulative(168600) >= ss_wage_base(168600).
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("168600"),
        )
        assert result["ss"] == Decimal("0.00"), (
            f"SS at cap: expected 0.00, "
            f"got {result['ss']}"
        )

    def test_ss_above_cap_zero(self, fica_config):
        """cumulative > ss_wage_base: SS = 0.00.

        Branch 1: cumulative(170000) >= ss_wage_base(168600).
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("170000"),
        )
        assert result["ss"] == Decimal("0.00"), (
            f"SS above cap: expected 0.00, "
            f"got {result['ss']}"
        )

    def test_ss_partial_one_dollar_under(self, fica_config):
        """cumulative = 168599, gross = 100: partial SS.

        Branch 2: cumulative(168599) + gross(100) = 168699
        > ss_wage_base(168600).
        ss_taxable = 168600 - 168599 = 1.00
        SS = 1.00 * 0.062 = 0.062 -> 0.06
        """
        result = calculate_fica(
            Decimal("100.00"), fica_config,
            cumulative_wages=Decimal("168599"),
        )
        assert result["ss"] == Decimal("0.06"), (
            f"SS partial ($1 under cap): expected 0.06, "
            f"got {result['ss']}"
        )

    def test_ss_full_well_under_cap(self, fica_config):
        """cumulative = 0, gross = 1000: full SS.

        Branch 3: cumulative(0) + gross(1000) = 1000
        <= ss_wage_base(168600).
        SS = 1000 * 0.062 = 62.00
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("0"),
        )
        assert result["ss"] == Decimal("62.00"), (
            f"SS full: expected 62.00, "
            f"got {result['ss']}"
        )

    def test_ss_partial_straddle(self, fica_config):
        """cumulative = 168000, gross = 1000: partial SS.

        Branch 2: cumulative(168000) + gross(1000) = 169000
        > ss_wage_base(168600).
        ss_taxable = 168600 - 168000 = 600
        SS = 600 * 0.062 = 37.20
        """
        result = calculate_fica(
            Decimal("1000.00"), fica_config,
            cumulative_wages=Decimal("168000"),
        )
        assert result["ss"] == Decimal("37.20"), (
            f"SS partial (straddle): expected 37.20, "
            f"got {result['ss']}"
        )


# ── Medicare Surtax Tests ───────────────────────────────────────


class TestMedicareSurtax:
    """Tests for Medicare surtax at high income levels."""

    def test_medicare_surtax_high_income(
        self, biweekly_periods, simple_tax_configs
    ):
        """Medicare surtax across 26 periods for $300k salary.

        gross = 300000/26 = $11,538.46
        base Medicare = 11538.46*0.0145 = $167.31
        surtax threshold = $200,000, rate = 0.9%

        Transition at period 18:
          cumulative = 17*11538.46 = $196,153.82
          cumul+gross = $207,692.28 > $200,000
          surtax_income = 207692.28 - 200000 = $7,692.28
          surtax = 7692.28*0.009 = $69.23
          medicare = 167.31 + 69.23 = $236.54

        Periods 19-26: cumulative >= $200,000
          surtax = 11538.46*0.009 = $103.85
          medicare = 167.31 + 103.85 = $271.16
        """
        profile = FakeProfile(
            pay=Decimal("11538.46"),  # $300,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        # base Medicare = 11538.46*0.0145 = 167.30767->167.31
        base_med = Decimal("167.31")
        # transition: 167.31 + 69.23 = 236.54
        trans_med = Decimal("236.54")
        # full surtax: 167.31 + 103.85 = 271.16
        full_surtax_med = Decimal("271.16")

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # Periods 1-17: base Medicare only (under threshold)
        for i in range(17):
            assert results[i].taxes.medicare == base_med, (
                f"period {i+1}: medicare expected "
                f"{base_med}, got {results[i].taxes.medicare}"
            )

        # Period 18: partial surtax (crosses threshold)
        # cumul = 17*11538.46 = 196153.82
        # surtax_income = 207692.28 - 200000 = 7692.28
        # surtax = 7692.28*0.009 = 69.23052->69.23
        assert results[17].taxes.medicare == trans_med, (
            f"period 18 (transition): medicare expected "
            f"{trans_med}, got {results[17].taxes.medicare}"
        )

        # Periods 19-26: full surtax (cumul >= threshold)
        # surtax = 11538.46*0.009 = 103.84614->103.85
        for i in range(18, 26):
            assert results[i].taxes.medicare == full_surtax_med, (
                f"period {i+1}: medicare expected "
                f"{full_surtax_med}, "
                f"got {results[i].taxes.medicare}"
            )


# ── Annual Projection Tests ─────────────────────────────────────


class TestAnnualProjection:
    """Tests for full-year salary projection correctness."""

    def test_26_period_annual_net_pay_sum(
        self, base_profile, biweekly_periods,
        simple_tax_configs
    ):
        """C27-3: Annual totals across 26 periods for a $60k salary.

        Every period is identical at this salary, which is the contract ruling
        **balance:R-HW** states: gross = $60,000 / 26 = $2307.6923... ->
        $2307.69, and net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46 =
        $1854.22 on all 26.

        Federal/state/SS/medicare are byte-identical across the year because
        the gross is: one figure in, one figure out.  No cap moves a later
        period either -- the largest cumulative-before is 25 x $2,307.69 =
        $57,692.25, under the SS wage base ($168,600) and the surtax threshold
        ($200,000).

        **Re-pinned at plan step balance:X-aw**, which superseded MED-05 /
        PA-07.  Under that rule the first 6 periods carried $2307.70 and the
        year summed to $60,000.00 exactly; the year now sums to
        26 * $2307.69 = $59,999.94, six cents under the contract salary.
        ``TestTheGrossIsARateAndNotAShareOfAYear`` owns that gap as its own
        subject; this case owns the ANNUAL TOTALS built on top of it.
        """
        results = project_salary(
            payroll_basis(base_profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # 26 * $2307.69 = $59,999.94 -- six cents under the contract salary,
        # which is the cost ruling R-HW accepts (plan step balance:X-aw).
        total_gross = sum(r.earnings.gross_biweekly for r in results)
        assert total_gross == Decimal("59999.94"), (
            f"total gross: expected 59999.94 (26 * 2307.69), "
            f"got {total_gross}"
        )

        # Every period carries the same rate.
        for i in range(26):
            assert results[i].earnings.gross_biweekly == Decimal("2307.69"), (
                f"period {i+1}: expected 2307.69, "
                f"got {results[i].earnings.gross_biweekly}"
            )

        # 173.08 * 26 = 4500.08 (per-period federal byte-identical;
        # both 2307.69 and 2307.70 annualise to the same 10%-bracket
        # withholding after the standard deduction).
        total_federal = sum(r.taxes.federal for r in results)
        assert total_federal == Decimal("173.08") * 26, (
            f"total federal: expected 4500.08, got {total_federal}"
        )

        # 103.85 * 26 = 2700.10
        total_state = sum(r.taxes.state for r in results)
        assert total_state == Decimal("103.85") * 26, (
            f"total state: expected 2700.10, got {total_state}"
        )

        # 143.08 * 26 = 3720.08 (FICA per-period unchanged: both
        # 2307.69*0.062 and 2307.70*0.062 round to 143.08).
        total_ss = sum(r.taxes.social_security for r in results)
        assert total_ss == Decimal("143.08") * 26, (
            f"total SS: expected 3720.08, got {total_ss}"
        )

        # 33.46 * 26 = 869.96 (both grosses round to the same medicare).
        total_medicare = sum(r.taxes.medicare for r in results)
        assert total_medicare == Decimal("33.46") * 26, (
            f"total medicare: expected 869.96, got {total_medicare}"
        )

        # 26 * $1854.22 = $48,209.72, one net figure for the whole year.
        total_net = sum(r.earnings.net_pay for r in results)
        assert total_net == Decimal("48209.72"), (
            f"total net: expected 48209.72 (26 * 1854.22), got {total_net}"
        )

        # Cross-check: net = gross - fed - state - ss - med
        assert total_net == (
            total_gross - total_federal - total_state
            - total_ss - total_medicare
        ), "net cross-check: components don't sum to net"

    def test_project_salary_all_periods_consistent(
        self, base_profile, biweekly_periods,
        simple_tax_configs
    ):
        """C27-3 corollary: the $60k breakdown is IDENTICAL across 26 periods.

        Every field of every period matches -- gross, net and all four
        withholding lines -- because the gross is a rate (ruling
        **balance:R-HW**) and nothing else in this profile varies by period.
        The year's cumulative max is $59,999.94, under the SS cap ($168,600)
        and the surtax threshold ($200,000), so no cap moves a later period.

        **Re-pinned at plan step balance:X-aw.** Under MED-05 / PA-07 this
        case asserted TWO cent-equivalence groups -- the first 6 periods at
        $2307.70 / $1854.23 and the last 20 at $2307.69 / $1854.22 -- which
        was the residue distribution showing through into net pay.  The
        "all 26 identical" invariant it replaced is restored, and it is now
        structural rather than a property of where the residue happened to
        land: the producer cannot see a period at all.
        """
        results = project_salary(
            payroll_basis(base_profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26, (
            f"expected 26 results, got {len(results)}"
        )

        # $60,000 / 26 = $2307.6923... -> $2307.69; net = $2307.69 - $173.08
        # - $103.85 - $143.08 - $33.46 = $1854.22.
        assert results[0].earnings.gross_biweekly == Decimal("2307.69")
        assert results[0].earnings.net_pay == Decimal("1854.22")

        first = results[0]
        for i in range(1, 26):
            r = results[i]
            assert r.earnings.gross_biweekly == first.earnings.gross_biweekly, (
                f"period {i+1}: gross {r.earnings.gross_biweekly} != "
                f"period 1 gross {first.earnings.gross_biweekly}"
            )
            assert r.earnings.net_pay == first.earnings.net_pay, (
                f"period {i+1}: net {r.earnings.net_pay} != "
                f"period 1 net {first.earnings.net_pay}"
            )
            assert r.taxes.federal == first.taxes.federal, (
                f"period {i+1}: federal {r.taxes.federal} != "
                f"period 1 federal {first.taxes.federal}"
            )
            assert r.taxes.state == first.taxes.state, (
                f"period {i+1}: state {r.taxes.state} != "
                f"period 1 state {first.taxes.state}"
            )
            assert r.taxes.social_security == first.taxes.social_security, (
                f"period {i+1}: SS {r.taxes.social_security} != "
                f"period 1 SS {first.taxes.social_security}"
            )
            assert r.taxes.medicare == first.taxes.medicare, (
                f"period {i+1}: medicare {r.taxes.medicare} != "
                f"period 1 medicare {first.taxes.medicare}"
            )
