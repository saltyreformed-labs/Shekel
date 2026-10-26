"""Deductions: how a line is priced in the pipeline, an annual cap throttling it, and inflation
compounding its amount.
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.services.paycheck_calculator import calculate_paycheck
from app.services.paycheck_calculator._lines import _inflation_years, _LineContext, _priced_lines
from tests._test_helpers import payroll_basis

from ._helpers import (
    TWO_PLACES,
    _timing_id,
    _period,
    twenty_four_rule,
    twelve_rule,
    FakeDeduction,
    FakeProfile,
)


class TestDeductionCalculation:
    """Tests for _priced_lines and deduction behavior in pipeline."""

    def test_flat_pre_tax_deduction(self, base_profile, simple_tax_configs):
        """Flat amount subtracted before taxes."""
        base_profile.lines = [
            FakeDeduction(name="401k", amount="200", paycheck_line_kind="pre_tax_deduction"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        assert len(result.deductions.pre_tax) == 1
        assert result.deductions.pre_tax[0].name == "401k"
        assert result.deductions.pre_tax[0].amount == Decimal("200.00")

    def test_flat_post_tax_deduction(self, base_profile, simple_tax_configs):
        """Flat amount subtracted after taxes."""
        base_profile.lines = [
            FakeDeduction(name="Roth", amount="150", paycheck_line_kind="post_tax_deduction"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        assert len(result.deductions.post_tax) == 1
        assert result.deductions.post_tax[0].amount == Decimal("150.00")

    def test_percentage_deduction(self, base_profile, simple_tax_configs):
        """Percentage of gross_biweekly."""
        base_profile.lines = [
            FakeDeduction(name="401k", amount="0.06", calc_method="percentage",
                          paycheck_line_kind="pre_tax_deduction"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        gross = Decimal("60000") / 26
        expected = (gross * Decimal("0.06")).quantize(TWO_PLACES,
                                                     rounding=ROUND_HALF_UP)
        assert result.deductions.pre_tax[0].amount == expected

    def test_inactive_deduction_skipped(self, base_profile, simple_tax_configs):
        """is_active=False excluded."""
        base_profile.lines = [
            FakeDeduction(name="Old Plan", amount="200", is_active=False),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        assert len(result.deductions.pre_tax) == 0

    def test_timing_filter(self, base_profile, simple_tax_configs):
        """Pre-tax deduction not in post-tax list and vice versa."""
        base_profile.lines = [
            FakeDeduction(name="401k", amount="200", paycheck_line_kind="pre_tax_deduction"),
            FakeDeduction(name="Roth", amount="100", paycheck_line_kind="post_tax_deduction"),
        ]
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(base_profile, [period]), period,
            simple_tax_configs)

        pre_names = [d.name for d in result.deductions.pre_tax]
        post_names = [d.name for d in result.deductions.post_tax]
        assert "401k" in pre_names
        assert "401k" not in post_names
        assert "Roth" in post_names
        assert "Roth" not in pre_names

    def test_24_per_year_skipped_on_third_paycheck(self):
        """A 24-per-year line's rule (at most 2 a month) skips the third paycheck.

        The rule plan step salary:R15-b migrates a 24 onto: every paycheck
        from the opening payday, at most 2 a month.  Taken on the first two
        of a three-payday month and not on the third -- the same answer the
        column's ordinal rule gave, now the walk's.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(name="Health", amount="100",
                              recurrence_rule=twenty_four_rule(date(2026, 1, 2))),
            ],
        )
        # Build a month with 3 paychecks
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        basis = payroll_basis(profile, all_periods)
        taken = [
            len(_priced_lines(
                _LineContext(basis, p.start_date, gross), _timing_id("pre_tax_deduction"),
            ))
            for p in all_periods
        ]
        assert taken == [1, 1, 0]

    def test_12_per_year_only_first_of_month(self):
        """A 12-per-year line's rule (monthly, first paycheck on/after the 1st) is taken once.

        The rule plan step salary:R15-b migrates a 12 onto: each month's 1st,
        placed on the first paycheck on or after it -- the month's first
        payday, which is the column's ``ordinal == 1``.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 2, 13),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            lines=[
                FakeDeduction(name="Life", amount="50",
                              recurrence_rule=twelve_rule(date(2026, 2, 1))),
            ],
        )
        p1 = _period(start_date=date(2026, 2, 13), period_id=4)
        p2 = _period(start_date=date(2026, 2, 27), period_id=5)
        all_periods = [p1, p2]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _priced_lines(
            _LineContext(payroll_basis(profile, all_periods), p1.start_date, gross),
            _timing_id("pre_tax_deduction"),
        )
        assert len(result) == 1
        assert result[0].amount == Decimal("50")

    def test_12_per_year_skipped_non_first(self):
        """The monthly rule is not taken on the month's second paycheck."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 2, 13),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            lines=[
                FakeDeduction(name="Life", amount="50",
                              recurrence_rule=twelve_rule(date(2026, 2, 1))),
            ],
        )
        p1 = _period(start_date=date(2026, 2, 13), period_id=4)
        p2 = _period(start_date=date(2026, 2, 27), period_id=5)
        all_periods = [p1, p2]

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _priced_lines(
            _LineContext(payroll_basis(profile, all_periods), p2.start_date, gross),
            _timing_id("pre_tax_deduction"),
        )
        assert len(result) == 0

    # ── Commit 32 / MED-07 / PA-22: pct-of-zero-gross boundary ────

    def test_percentage_pre_tax_of_zero_gross_is_zero(self):
        """Percentage pre-tax deduction with gross_biweekly=0 yields 0.

        Pinning the PA-22 edge that 07_test_gaps Slice-3 / Concept 7 / 8
        flag as UNTESTED: a percentage deduction applied to a zero
        biweekly gross must produce a Decimal("0.00") line, never a
        negative or undefined value.  The amount is
            gross_biweekly * pct -> 0 * 0.06 = 0
        quantized HALF_UP to 0.00.  Asserting the exact edge value rather
        than just `len(result) == 1` proves the edge BEHAVIOR
        (testing-standards.md "Edge Case Tests").
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            lines=[
                FakeDeduction(name="401k", amount="0.06",
                              calc_method="percentage",
                              paycheck_line_kind="pre_tax_deduction"),
            ],
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = _priced_lines(
            _LineContext(
                payroll_basis(profile, [period]), period.start_date, Decimal("0.00"),
            ),
            _timing_id("pre_tax_deduction"),
        )
        assert len(result) == 1
        assert result[0].name == "401k"
        assert result[0].amount == Decimal("0.00"), (
            f"Expected 0.00, got {result[0].amount}"
        )

    def test_percentage_post_tax_of_zero_gross_is_zero(self):
        """Percentage post-tax deduction with gross_biweekly=0 yields 0.

        Mirror of test_percentage_pre_tax_of_zero_gross_is_zero for the
        post-tax timing.  Both timings share the same parameterized
        producer (F-038/F-039 AGREE), so this is the post-side edge
        proof.  amount = 0 * 0.04 = 0, quantized HALF_UP to 0.00.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            lines=[
                FakeDeduction(name="Roth", amount="0.04",
                              calc_method="percentage",
                              paycheck_line_kind="post_tax_deduction"),
            ],
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = _priced_lines(
            _LineContext(
                payroll_basis(profile, [period]), period.start_date, Decimal("0.00"),
            ),
            _timing_id("post_tax_deduction"),
        )
        assert len(result) == 1
        assert result[0].name == "Roth"
        assert result[0].amount == Decimal("0.00"), (
            f"Expected 0.00, got {result[0].amount}"
        )


class TestDeductionAnnualCap:
    """``PaycheckLine.annual_cap`` throttles a deduction once its
    calendar-year total reaches the cap, then resumes the next January
    (deep-hunt #2 -- the cap was stored/validated/rendered but never
    enforced).  Each test would fail with the cap unenforced (the old
    behavior subtracted the full amount every period).
    """

    def _amounts_over(self, profile, periods, *, timing="pre_tax_deduction",
                      history_opens_on=None):
        """Per-period deduction amount for the single deduction on ``profile``.

        Every case here uses a line with NO rule, which is taken on every
        payday (R-SAL3), so the cadence read is a no-op and any zero in the
        result is the cap's doing.  (Until plan step salary:R15-b the cases
        stated a month ordinal of 1 for the same reason.)

        ``history_opens_on`` says how far back the owner's paychecks reach and
        defaults to the column's own ``None``, which since ruling
        **balance:R-IA**'s 2026-08-31 amendment means NOT STATED: the engine
        counts only the recorded paydays.  So every case here measures what it
        measured before plan step **balance:X-bh-2**, and the one case about
        the backward rhythm states its floor.

        The gross each line is priced against is the basis's own base pay
        on the payday since plan step salary:X-av-3a, which deleted the
        yearly salary this helper divided by 26: every profile here pays
        $2,307.69 a paycheck, the figure 60000 / 26 rounded to.
        """
        basis = payroll_basis(profile, periods, history_opens_on=history_opens_on)
        amounts = []
        for p in periods:
            lines = _priced_lines(
                _LineContext(
                    basis, p.start_date,
                    basis.base_pay_on(p.start_date).per_paycheck,
                ),
                _timing_id(timing),
            )
            amounts.append(lines[0].amount)
        return amounts

    def test_flat_deduction_clamps_to_cap_then_stops(self):
        """$600/period under a $1000 cap applies 600, 400, 0, 0."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[FakeDeduction(name="HSA", amount="600",
                                      annual_cap="1000")],
        )
        # Four 2026 periods; the line has no rule (every paycheck, no cadence
        # skip), so only the cap zeroes the later periods.
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
            _period(start_date=date(2026, 2, 13), period_id=4),
        ]
        # P1: YTD 0 -> full 600.  P2: YTD 600, room 400 -> 400 (lands on cap).
        # P3, P4: YTD 1200 >= cap -> 0.  Sum of applied == 1000 exactly.
        assert self._amounts_over(profile, periods) == [
            Decimal("600"), Decimal("400"), Decimal("0"), Decimal("0"),
        ]

    def test_cap_with_room_does_not_throttle(self):
        """A cap above the annual total leaves every period at full amount."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[FakeDeduction(name="401k", amount="200",
                                      annual_cap="100000")],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
        ]
        # 200/period never approaches a $100k cap -> unchanged.
        assert self._amounts_over(profile, periods) == [
            Decimal("200"), Decimal("200"),
        ]

    def _december_opening_profile(self):
        """A $600 deduction under a $1,000 cap, on a December-opening set."""
        return FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 12, 4),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            lines=[FakeDeduction(name="HSA", amount="600",
                                      annual_cap="1000")],
        ), [
            _period(start_date=date(2026, 12, 4), period_id=1),
            _period(start_date=date(2026, 12, 18), period_id=2),
            _period(start_date=date(2027, 1, 1), period_id=3),
        ]

    def test_cap_is_calendar_year_scoped_and_resets(self):
        """The cap resets each January: a new-year period starts fresh.

        Stated with NO pay history, which since ruling **balance:R-IA**'s
        2026-08-31 amendment is an owner nobody has asked -- so the engine
        counts only the three recorded paydays and the hand-computed figures
        below are unchanged by plan step balance:X-bh-2.  That the default is
        the SAFE one is the amendment's whole point, and this case is where it
        shows: a fixture that says nothing measures what it always measured.
        """
        profile, periods = self._december_opening_profile()

        # 2026 exhausts the cap (600 then 400); the 2027 period counts only
        # same-year prior periods (none), so the cap is fresh -> full 600.
        assert self._amounts_over(profile, periods) == [
            Decimal("600"), Decimal("400"), Decimal("600"),
        ]

    def test_a_STATED_history_exhausts_the_cap_BEFORE_the_record_opens(self):
        """What the backward rhythm changes for a cap, stated as money.

        Plan step **balance:X-bh-2**.  The same three periods for an owner who
        HAS said when their paychecks began: the rhythm runs back to that day,
        so by the time the record opens on 2026-12-04 they have already been
        paid 24 times that year and a $1,000 cap on a $600 deduction is long
        gone.  Both December periods clamp to zero and January resets.

        This is finding **N-390** priced on the deduction side: before this
        step the same owner was charged $1,000 of a cap they had already
        spent, because the year opened where the RECORD did rather than where
        they did.
        """
        profile, periods = self._december_opening_profile()

        # 2026-12-04 less 24 fortnights is 2026-01-02, so the year holds 24
        # rhythm paydays before the record opens -- 24 x $600 against a
        # $1,000 cap.
        assert (periods[0].start_date - date(2026, 1, 2)).days == 24 * 14
        assert self._amounts_over(
            profile, periods, history_opens_on=date(2025, 1, 1),
        ) == [Decimal("0"), Decimal("0"), Decimal("600")]

    def test_percentage_deduction_capped_on_dollar_total(self):
        """A percentage deduction is clamped on its cumulative dollar amount."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[FakeDeduction(name="401k", amount="0.10",
                                      calc_method="percentage",
                                      annual_cap="400")],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        # gross 60000/26 = 2307.69; 10% = 230.77/period.  P1: 230.77.
        # P2: room 400-230.77 = 169.23 -> 169.23.  P3: cap exhausted -> 0.
        assert self._amounts_over(profile, periods) == [
            Decimal("230.77"), Decimal("169.23"), Decimal("0"),
        ]

    def test_capped_deduction_raises_net_pay_once_exhausted(self):
        """End-to-end: a capped-out period nets more than a pre-cap period.

        Proves the clamp flows through ``calculate_paycheck`` to net pay --
        the headline harm in deep-hunt #2 was understated net pay.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[FakeDeduction(name="HSA", amount="600",
                                      paycheck_line_kind="post_tax_deduction",
                                      annual_cap="1000")],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        configs = {"bracket_set": None, "state_config": None, "fica_config": None}
        nets = [
            calculate_paycheck(
            payroll_basis(profile, periods), p,
            configs).earnings.net_pay
            for p in periods
        ]
        # Post-tax deduction reduces net directly.  P1 takes 600, P2 takes the
        # capped 400, P3 takes 0 -> net rises as the deduction shrinks.
        assert nets[1] - nets[0] == Decimal("200.00")  # 600 - 400 deducted
        assert nets[2] - nets[1] == Decimal("400.00")  # 400 - 0 deducted


class TestInflationAdjustment:
    """Tests for _inflation_years() and inflation in deductions."""

    def test_one_year_inflation(self):
        """amount * (1 + rate)^1."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 6, 1),  # $60,000.00 a year / 26
            created_at=date(2025, 1, 1),
            lines=[
                FakeDeduction(name="Health", amount="100",
                              inflation_enabled=True, inflation_rate="0.03",
                              inflation_effective_month=1),
            ],
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 1

        # Verify in deduction calculation
        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _priced_lines(
            _LineContext(
                payroll_basis(profile, [period]), period.start_date, gross,
            ),
            _timing_id("pre_tax_deduction"),
        )
        expected = (Decimal("100") * Decimal("1.03")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result[0].amount == expected

    def test_two_years_compound_inflation(self):
        """amount * (1 + rate)^2."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 6, 1),  # $60,000.00 a year / 26
            created_at=date(2024, 1, 1),
            lines=[
                FakeDeduction(name="Health", amount="100",
                              inflation_enabled=True, inflation_rate="0.03",
                              inflation_effective_month=1),
            ],
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 2

        gross = (Decimal("60000") / 26).quantize(TWO_PLACES,
                                                 rounding=ROUND_HALF_UP)
        result = _priced_lines(
            _LineContext(
                payroll_basis(profile, [period]), period.start_date, gross,
            ),
            _timing_id("pre_tax_deduction"),
        )
        expected = (Decimal("100") * Decimal("1.03") ** 2).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result[0].amount == expected

    def test_inflated_percentage_rounds_once_at_return(self):
        """E-26(a): full precision through pct x inflation, ONE rounding.

        gross 1000.49, 5% deduction, 3% inflation, 1 year:
          exact: 1000.49 * 0.05 = 50.0245; * 1.03 = 51.525235
          -> round once HALF_UP -> 51.53.
        The pre-ratification double-quantize gave 51.52 (50.0245 ->
        50.02 first, then 50.02 * 1.03 = 51.5206 -> 51.52) -- the
        intermediate quantize ate the half-cent the full-precision
        product carries.  Ratified 2026-06-11 ("quantize once at
        return"); this pin fails under a restored double-quantize.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 6, 1),  # $60,000.00 a year / 26
            created_at=date(2025, 1, 1),
            lines=[
                FakeDeduction(name="401k", amount="0.05",
                              calc_method="percentage",
                              inflation_enabled=True, inflation_rate="0.03",
                              inflation_effective_month=1),
            ],
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)
        result = _priced_lines(
            _LineContext(
                payroll_basis(profile, [period]), period.start_date, Decimal("1000.49"),
            ),
            _timing_id("pre_tax_deduction"),
        )
        assert result[0].amount == Decimal("51.53")

    def test_flat_four_decimal_amount_rounds_to_cents(self):
        """E-26(a): a 4dp flat amount rounds once at the line boundary.

        The column is Numeric(12, 4), so a flat deduction can carry
        sub-cent precision (schema places=4).  Pre-ratification the raw
        4dp value flowed UNQUANTIZED into the taxable/net math while
        the line displayed at 2dp -- a sub-cent display-vs-math
        divergence.  500.1234 -> 500.12 (HALF_UP): the displayed line
        now equals the amount actually subtracted.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 6, 1),  # $60,000.00 a year / 26
            created_at=date(2025, 1, 1),
            lines=[FakeDeduction(name="HSA", amount="500.1234")],
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)
        result = _priced_lines(
            _LineContext(
                payroll_basis(profile, [period]), period.start_date, Decimal("2307.69"),
            ),
            _timing_id("pre_tax_deduction"),
        )
        assert result[0].amount == Decimal("500.12")

    def test_before_effective_month_reduces_years(self):
        """period_month < eff_month → years - 1."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 3, 1),  # $60,000.00 a year / 26
            created_at=date(2024, 1, 1),
        )
        # Period is in March, effective month is June
        period = _period(start_date=date(2026, 3, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 6)
        # 2026 - 2024 = 2, but month 3 < 6 → 2 - 1 = 1
        assert years == 1

    def test_created_at_none_zero_years(self):
        """profile.created_at=None → no inflation."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 6, 1),  # $60,000.00 a year / 26
            created_at=None,
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 0

    def test_same_year_as_creation_zero_years(self):
        """Year 0 = no inflation."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 6, 1),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 6, 1), period_id=1)

        years = _inflation_years(period.start_date, profile, 1)
        assert years == 0
