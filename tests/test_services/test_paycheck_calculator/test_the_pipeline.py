"""calculate_paycheck end to end: the breakdown's computed totals, the pipeline's arithmetic, the
payday it refuses to place, and its edge and boundary paths.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.exceptions import InvalidGrossPayError
from app.services.pay_rhythm import FixedDays
from app.services.paycheck_calculator import (
    calculate_paycheck,
    PricedLine,
    DeductionBreakdown,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)
from app.services.paycheck_calculator._calendar_questions import _month_ordinal
from app.utils.money import ZERO
from app.services.pay_calendar import PayCadence, PayCalendarError
from tests._test_helpers import payroll_basis

from ._helpers import TWO_PLACES, _period, twenty_four_rule, FakeDeduction, FakeProfile


# ── New Tests ────────────────────────────────────────────────────


class TestPaycheckBreakdownProperties:
    """Verify computed properties on the PaycheckBreakdown dataclass."""

    def test_total_pre_tax_sums_deductions(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                base_biweekly=Decimal("2307.69"),
                gross_biweekly=Decimal("2307.69"),
            ),
            deductions=DeductionBreakdown(
                pre_tax=[
                    PricedLine("401k", Decimal("200.00")),
                    PricedLine("HSA", Decimal("50.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("250.00")

    def test_total_post_tax_sums_deductions(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                base_biweekly=Decimal("2307.69"),
                gross_biweekly=Decimal("2307.69"),
            ),
            deductions=DeductionBreakdown(
                post_tax=[
                    PricedLine("Roth IRA", Decimal("100.00")),
                    PricedLine("Life Ins", Decimal("25.00")),
                ],
            ),
        )
        assert breakdown.deductions.total_post_tax == Decimal("125.00")

    def test_total_taxes_sums_all_tax_fields(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                base_biweekly=Decimal("2307.69"),
                gross_biweekly=Decimal("2307.69"),
            ),
            taxes=TaxLines(
                federal=Decimal("200.00"),
                state=Decimal("80.00"),
                social_security=Decimal("143.08"),
                medicare=Decimal("33.46"),
            ),
        )
        assert breakdown.taxes.total == Decimal("456.54")

    def test_empty_deductions_return_zero(self):
        breakdown = PaycheckBreakdown(
            period=PeriodInfo(
                date(2026, 1, 2), period_id=1, cadence=PayCadence(FixedDays(14)),
            ),
            earnings=Earnings(
                annual_salary=Decimal("60000"),
                base_biweekly=Decimal("2307.69"),
                gross_biweekly=Decimal("2307.69"),
            ),
        )
        assert breakdown.deductions.total_pre_tax == Decimal("0")
        assert breakdown.deductions.total_post_tax == Decimal("0")
        assert breakdown.taxes.total == Decimal("0")


class TestCalculatePaycheckPipeline:
    """End-to-end tests for calculate_paycheck()."""

    def test_basic_paycheck_no_deductions(
        self, base_profile, simple_tax_configs
    ):
        """Full pipeline: $60k salary, no deductions, all exact.

        Pipeline trace:
          gross = 60000/26 = $2,307.69
          federal: annual 59999.94 - 15k std ded = 44999.94
            10% bracket: 44999.94*0.10 = 4499.994->4499.99
            per-period: 4499.99/26 = 173.08
          state: 59999.94*0.045 = 2700.00, /26 = 103.85
          SS: 2307.69*0.062 = 143.08
          Medicare: 2307.69*0.0145 = 33.46
          net: 2307.69 - 173.08 - 103.85 - 143.08 - 33.46
             = 1854.22
        """
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )
        all_periods = [period]

        result = calculate_paycheck(
            payroll_basis(base_profile, all_periods), period,
            simple_tax_configs
        )

        # The yearly figure is pay x paychecks a year since plan step
        # salary:X-av-3a (ruling R-SAL59): 2307.69 x 26 = 59999.94.
        assert result.earnings.annual_salary == Decimal("59999.94"), (
            f"annual_salary: expected 59999.94, "
            f"got {result.earnings.annual_salary}"
        )
        # 60000 / 26 = 2307.692307... -> 2307.69
        assert result.earnings.gross_biweekly == Decimal("2307.69"), (
            f"gross_biweekly: expected 2307.69, "
            f"got {result.earnings.gross_biweekly}"
        )
        # Pub 15-T: annual=59999.94, taxable=44999.94
        # 44999.94*0.10=4499.994->4499.99, /26=173.08
        assert result.taxes.federal == Decimal("173.08"), (
            f"federal_tax: expected 173.08, "
            f"got {result.taxes.federal}"
        )
        # state: 59999.94*0.045=2699.9973->2700.00
        # 2700.00/26=103.846...->103.85
        assert result.taxes.state == Decimal("103.85"), (
            f"state_tax: expected 103.85, "
            f"got {result.taxes.state}"
        )
        # SS: 2307.69*0.062=143.07678->143.08
        assert result.taxes.social_security == Decimal("143.08"), (
            f"social_security: expected 143.08, "
            f"got {result.taxes.social_security}"
        )
        # Medicare: 2307.69*0.0145=33.461505->33.46
        assert result.taxes.medicare == Decimal("33.46"), (
            f"medicare: expected 33.46, "
            f"got {result.taxes.medicare}"
        )
        # net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46
        assert result.earnings.net_pay == Decimal("1854.22"), (
            f"net_pay: expected 1854.22, "
            f"got {result.earnings.net_pay}"
        )

    def test_net_pay_formula(self, base_profile, simple_tax_configs):
        """net = gross - pre_tax - fed - state - ss - medicare - post_tax."""
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        all_periods = [period]

        r = calculate_paycheck(
            payroll_basis(base_profile, all_periods), period,
            simple_tax_configs)

        # Hardcoded correctness anchor: base_profile=$60k salary,
        # same setup as test_basic_paycheck_no_deductions.
        # gross=2307.69 - fed=173.08 - state=103.85
        #   - SS=143.08 - med=33.46 = 1854.22
        assert r.earnings.net_pay == Decimal("1854.22"), (
            f"net_pay: expected 1854.22, got {r.earnings.net_pay}"
        )

        # Secondary: internal consistency check (formula holds).
        expected_net = (
            r.earnings.gross_biweekly
            - r.deductions.total_pre_tax
            - r.taxes.federal
            - r.taxes.state
            - r.taxes.social_security
            - r.taxes.medicare
            - r.deductions.total_post_tax
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        assert r.earnings.net_pay == expected_net, (
            f"Consistency check: net_pay={r.earnings.net_pay}, "
            f"formula result={expected_net}"
        )

    def test_gross_biweekly_calculation(self, simple_tax_configs):
        """annual / 26, quantized to 2 places."""
        profile = FakeProfile(
            pay=Decimal("2884.62"), pay_from=date(2026, 1, 16),  # $75,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        expected_gross = (Decimal("75000") / 26).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        assert result.earnings.gross_biweekly == expected_gross

    def test_taxable_income_floors_at_zero(self, simple_tax_configs):
        """When pre_tax deductions > gross, taxable income should be 0."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(name="Mega401k", amount="3000",
                              paycheck_line_kind="pre_tax_deduction"),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        assert result.earnings.taxable_income == ZERO

    def test_no_bracket_set_zero_federal(self, nc_state_config, standard_fica):
        """bracket_set=None → federal=0."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": None,
            "state_config": nc_state_config,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.federal == ZERO

    def test_no_state_config_zero_state(self, simple_bracket_set, standard_fica):
        """state_config=None → state=0."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": None,
            "fica_config": standard_fica,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.state == ZERO

    def test_no_fica_config_zero_fica(self, simple_bracket_set, nc_state_config):
        """fica_config=None → ss=0, medicare=0."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": None,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.social_security == ZERO
        assert result.taxes.medicare == ZERO

    def test_all_tax_configs_none(self):
        """All None → only gross minus deductions."""
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        configs = {
            "bracket_set": None,
            "state_config": None,
            "fica_config": None,
        }

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            configs)

        assert result.taxes.federal == ZERO
        assert result.taxes.state == ZERO
        assert result.taxes.social_security == ZERO
        assert result.taxes.medicare == ZERO
        assert result.earnings.net_pay == result.earnings.gross_biweekly

    def test_w4_fields_passed_to_federal(self, simple_tax_configs):
        """W-4 fields increase federal withholding exactly.

        Base: $60k, no W-4.
          annual=59999.94, taxable=44999.94
          tax=4499.99, /26=173.08
        W-4: additional_income=10000, extra_withholding=50.
          annual=69999.94, taxable=54999.94
          50000*0.10+4999.94*0.22=6099.99
          (6099.99/26)+50=284.615->284.62
        """
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )

        base = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        base_result = calculate_paycheck(
            payroll_basis(base, [period]), period,
            simple_tax_configs
        )

        with_w4 = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            additional_income=10000,
            extra_withholding=50,
        )
        w4_result = calculate_paycheck(
            payroll_basis(with_w4, [period]), period,
            simple_tax_configs
        )

        # Base: 4499.99/26=173.076923->173.08
        assert base_result.taxes.federal == Decimal("173.08"), (
            f"base federal_tax: expected 173.08, "
            f"got {base_result.taxes.federal}"
        )
        # W-4: (6099.99/26)+50=234.615+50=284.615->284.62
        assert w4_result.taxes.federal == Decimal("284.62"), (
            f"w4 federal_tax: expected 284.62, "
            f"got {w4_result.taxes.federal}"
        )


class TestTheEngineRefusesAPaycheckItCannotPlace:
    """The PUBLIC door refuses a payday off the owner's rhythm, from a caller's shape.

    ``_month_ordinal``'s refusal is graded directly in
    ``test_pay_calendar_rhythm``; this grades it where a caller meets it --
    through :func:`calculate_paycheck` -- and it exists because the argument
    that the refusal is unreachable is an ONLY-WAY argument.

    Every one of the 13 call sites in ``app/`` draws its period from the same
    calendar it prices against, so none can reach this today.  That claim is
    one unenumerated writer from false, and plan step **balance:X-bh-1** made
    it expensive rather than cheap to be wrong about: the engine used to return
    a confident number for a payday it could not place and now RAISES, so a
    14th caller -- a period built from a form value, a saved template, a
    backfill script -- meets a ``PayCalendarError`` on a money path instead of
    a wrong figure.  This case is that caller, written from their shape.  If
    one ever appears for real, it fails HERE rather than 500ing in production.

    **Plan step balance:X-bh-2 narrowed WHAT is unplaceable and did not remove
    the refusal**, which is why these cases changed shape rather than going
    away.  A day BELOW the opening payday used to be unplaceable by
    construction; the rhythm now runs backward, so such a day is placed when
    it falls on the rhythm and inside the owner's stated history.  Two kinds
    remain: a day OFF the rhythm's phase -- another owner's payday, or one
    assembled by hand -- and a day below a stated ``history_opens_on``.

    Measured on the developer's own schedule (opening 2026-03-26): the payday
    2026-03-12, which he really was paid on and the app holds no row for,
    answered ``net $2,454.10`` at plan step X-bh-1's ancestor, then RAISED at
    X-bh-1, and is priced correctly as March's first paycheck now.  The last
    case below is that day.
    """

    @staticmethod
    def _biweekly_from_march():
        """The developer's shape: six biweekly paydays opening 2026-03-26."""
        return [
            _period(start_date=date(2026, 3, 26) + timedelta(days=14 * i),
                    period_id=i + 1)
            for i in range(6)
        ]

    def test_a_payday_OFF_the_rhythm_is_refused_not_priced(
        self, simple_tax_configs,
    ):
        """A period built by hand, one day out of phase, raises.

        2026-03-13 is a day nobody on this schedule is paid on: the rhythm
        runs 2026-03-12, 03-26, 04-09 in both directions from the record, and
        a day between two paydays has no position in its month to answer with.
        This is the shape a form value or another owner's period has.
        """
        profile = FakeProfile(
            pay=Decimal("3525.96"), pay_from=date(2026, 3, 26),  # $91,675.00 a year / 26
            created_at=date(2026, 1, 1),
            lines=[FakeDeduction(
                name="Health", amount="500",
                recurrence_rule=twenty_four_rule(date(2026, 3, 12)),
            )],
        )
        basis = payroll_basis(profile, self._biweekly_from_march())
        unheld = _period(start_date=date(2026, 3, 13), period_id=99)

        with pytest.raises(PayCalendarError, match="is not paid on"):
            calculate_paycheck(basis, unheld, simple_tax_configs)

    def test_a_payday_below_a_STATED_opening_is_refused_not_priced(
        self, simple_tax_configs,
    ):
        """On the rhythm, below the floor: the owner says they were not paid then.

        The other half of what stays unplaceable after plan step
        balance:X-bh-2.  2026-03-12 IS on this schedule's rhythm, so it is
        priced for an owner who has stated nothing -- but an owner whose
        paychecks began on 2026-03-26 was not paid on it, and answering a
        position anyway would put a 12-per-year deduction on a paycheck that
        never happened.
        """
        profile = FakeProfile(
            pay=Decimal("3525.96"), pay_from=date(2026, 3, 26),  # $91,675.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        schedule = self._biweekly_from_march()
        basis = payroll_basis(
            profile, schedule, history_opens_on=date(2026, 3, 26),
        )
        unheld = _period(start_date=date(2026, 3, 12), period_id=99)

        with pytest.raises(PayCalendarError, match="is not paid on"):
            calculate_paycheck(basis, unheld, simple_tax_configs)

    def test_the_refusal_names_the_day_so_a_traceback_is_actionable(
        self, simple_tax_configs,
    ):
        """The message carries the payday and the owner, not just a type.

        A refusal a reader cannot act on sends them to the wrong cause, which
        is what the message this asserts exists to prevent.
        """
        profile = FakeProfile(
            pay=Decimal("3525.96"), pay_from=date(2026, 3, 26),  # $91,675.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        basis = payroll_basis(profile, self._biweekly_from_march())
        unheld = _period(start_date=date(2026, 3, 13), period_id=99)

        with pytest.raises(PayCalendarError) as caught:
            calculate_paycheck(basis, unheld, simple_tax_configs)

        message = str(caught.value)
        assert "2026-03-13" in message
        assert "user 1" in message

    def test_a_payday_the_calendar_DOES_hold_is_priced(
        self, simple_tax_configs,
    ):
        """THE CONTROL: the refusal fires on the mismatch, not on every call.

        Without this, both cases above would pass against an engine that
        refused everything.
        """
        profile = FakeProfile(
            pay=Decimal("3525.96"), pay_from=date(2026, 3, 26),  # $91,675.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        schedule = self._biweekly_from_march()
        basis = payroll_basis(profile, schedule)

        result = calculate_paycheck(basis, schedule[0], simple_tax_configs)

        # $91,675 / 26 = $3,525.96, the rate ruling balance:R-HW fixed.
        assert result.earnings.gross_biweekly == Decimal("3525.96")

    def test_the_owners_real_unrecorded_payday_is_PRICED_once_he_STATES_it(
        self, simple_tax_configs,
    ):
        """THE SECOND CONTROL, and it is what plan step balance:X-bh-2 bought.

        2026-03-12 is a day the developer really was paid on and the app holds
        no row for -- the exact day X-bh-1's refusal named.  Once he says his
        paychecks began earlier it is on the rhythm, so it prices at the same
        rate as a recorded paycheck AND takes March's first position (the
        cockpit's badge; a 12-per-year line was charged on it until plan step
        salary:R15-b, whose migration docstring states what a line's rule
        does below the record instead).

        Paired with the two refusals above, this is what separates "the rhythm
        reaches here" from "the engine answers anything": one day apart, 03-12
        prices and 03-13 raises.
        """
        profile = FakeProfile(
            pay=Decimal("3525.96"), pay_from=date(2026, 3, 26),  # $91,675.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        schedule = self._biweekly_from_march()
        basis = payroll_basis(
            profile, schedule, history_opens_on=date(2025, 1, 1),
        )
        unrecorded = _period(start_date=date(2026, 3, 12), period_id=99)

        result = calculate_paycheck(basis, unrecorded, simple_tax_configs)

        assert result.earnings.gross_biweekly == Decimal("3525.96")
        assert _month_ordinal(basis.calendar, unrecorded.start_date) == 1

    def test_the_same_day_still_RAISES_for_an_owner_who_stated_nothing(
        self, simple_tax_configs,
    ):
        """The amendment, graded at the door a caller meets.

        ``NULL`` is not a claim, so an owner nobody has asked has no rhythm
        below their record and 2026-03-12 is unplaceable for them -- exactly
        as it was at plan step balance:X-bh-1, and for the same reason: the
        engine would otherwise answer a confident number for a paycheck it has
        no basis to place.  This is the pair that makes the case above about
        the STATED fact rather than about the day.
        """
        profile = FakeProfile(
            pay=Decimal("3525.96"), pay_from=date(2026, 3, 26),  # $91,675.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        basis = payroll_basis(profile, self._biweekly_from_march())
        unrecorded = _period(start_date=date(2026, 3, 12), period_id=99)

        with pytest.raises(PayCalendarError, match="is not paid on"):
            calculate_paycheck(basis, unrecorded, simple_tax_configs)


# ── Edge Case Tests ─────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases: zero and negative salary."""

    def test_zero_salary(self, simple_tax_configs):
        """All fields zero when annual salary is $0.

        gross = 0/26 = 0.00
        federal: annual=0, taxable=max(0-15000,0)=0, 0.00
        state: 0*0.045 = 0.00, /26 = 0.00
        SS: 0*0.062 = 0.00
        Medicare: 0*0.0145 = 0.00
        net = 0.00
        """
        profile = FakeProfile(
            pay=Decimal("0.00"), pay_from=date(2026, 1, 16),  # $0.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.earnings.gross_biweekly == Decimal("0.00"), (
            f"gross: expected 0.00, "
            f"got {result.earnings.gross_biweekly}"
        )
        assert result.taxes.federal == Decimal("0.00"), (
            f"federal: expected 0.00, "
            f"got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("0.00"), (
            f"state: expected 0.00, "
            f"got {result.taxes.state}"
        )
        assert result.taxes.social_security == Decimal("0.00"), (
            f"SS: expected 0.00, "
            f"got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("0.00"), (
            f"medicare: expected 0.00, "
            f"got {result.taxes.medicare}"
        )
        assert result.earnings.net_pay == Decimal("0.00"), (
            f"net: expected 0.00, "
            f"got {result.earnings.net_pay}"
        )

    def test_negative_salary_behavior(self, simple_tax_configs):
        """Negative salary cascades to InvalidGrossPayError.

        -10000/26 = -384.62 (negative gross_biweekly).
        calculate_federal_withholding validates gross_pay >= 0
        and raises InvalidGrossPayError for negative input.
        calculate_paycheck does not validate salary itself.
        """
        profile = FakeProfile(
            pay=Decimal("-384.62"), pay_from=date(2026, 1, 16),  # -$10,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(
            start_date=date(2026, 1, 16), period_id=1
        )

        with pytest.raises(InvalidGrossPayError):
            calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
            )


# ── Negative-Path and Boundary-Condition Tests ─────────────────────


class TestNegativeAndBoundaryPaths:
    """Negative-path and boundary-condition tests for the paycheck calculator.

    Verifies behavior with zero/edge-case salary profiles, excessive
    deductions, and unusual pay frequencies.
    """

    def test_a_zero_paycheck_count_cannot_be_expressed(self):
        """A zero paycheck count is REFUSED, not silently defaulted (R-F16).

        Input: a cadence of 0 days, which is what a stored
        ``pay_periods_per_year`` of 0 amounted to before plan step R-F16.
        Expected: ``PayCadence`` refuses at construction, so no
        :class:`PayrollBasis` and no paycheck can be built from it.

        **This replaces an assertion that the engine SILENTLY defaulted a zero
        count to 26**, via ``profile.pay_periods_per_year or 26``, whose own
        note asked for "a ValidationError guard if 0 is invalid user input".
        Dropping the column is that guard: the count is now derived from
        ``budget.pay_schedule.cadence_days``, ``ck_pay_schedule_cadence_range``
        bounds that column to 1..365 in the database, and
        :func:`~app.services.pay_calendar.validate_cadence` refuses the same
        range in front of it -- so the misconfigured profile the old test
        described is no longer a state the application can hold.  Division by
        zero in the pipeline is prevented by the value not existing rather
        than by a falsy-coalesce nobody could see.
        """
        from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
            PayCadence,
            PayCalendarError,
        )

        with pytest.raises(PayCalendarError):
            PayCadence(FixedDays(0))

    def test_a_biweekly_cadence_prices_the_known_paycheck(
        self, simple_tax_configs
    ):
        """26 paychecks a year is what a 14-day cadence derives.

        Input: a $60,000 profile on the default 14-day cadence.
        Expected: the known $60k / 26 figures -- gross $2,307.69, net
        $1,854.22 -- proving the DERIVED count reproduces exactly what the
        dropped column's 26 produced, which is the no-op claim plan step
        R-F16 makes for every owner whose two stored answers agreed.
        """
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.earnings.gross_biweekly == Decimal("2307.69")
        assert result.earnings.net_pay == Decimal("1854.22")

    def test_a_once_a_year_cadence_has_no_rounding_artifacts(
        self, simple_bracket_set, nc_state_config, standard_fica
    ):
        """Annual pay frequency (1 period/year) produces no rounding artifacts.

        Input: annual_salary=78000, a 365-day cadence (1 paycheck a year), no
        raises/deductions.
        Pipeline trace:
          gross = 78000 / 1 = 78000.00
          federal: taxable = 78000 - 15000 = 63000
            50000*0.10 + 13000*0.22 = 5000 + 2860 = 7860.00 / 1 = 7860.00
          state: 78000*0.045 = 3510.00 / 1 = 3510.00
          SS: 78000*0.062 = 4836.00
          Medicare: 78000*0.0145 = 1131.00
          net: 78000 - 7860 - 3510 - 4836 - 1131 = 60663.00
        Why: Annual pay frequency is a real edge case (contractors). The
        per-period conversion must not introduce rounding artifacts when periods=1.
        """
        profile = FakeProfile(
            pay=Decimal("78000.00"), pay_from=date(2026, 1, 16),  # $78,000.00 a year / 1
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)
        tax_configs = {
            "bracket_set": simple_bracket_set,
            "state_config": nc_state_config,
            "fica_config": standard_fica,
        }

        # ``round(365.2425 / 365) = 1``: the once-a-year cadence, DERIVED
        # rather than stated as a count (plan step R-F16).
        result = calculate_paycheck(
            payroll_basis(profile, [period], cadence_days=365), period,
            tax_configs,
        )

        # gross = 78000 / 1 = 78000.00 (exact, no rounding)
        assert result.earnings.gross_biweekly == Decimal("78000.00"), (
            f"gross: expected 78000.00, got {result.earnings.gross_biweekly}"
        )
        # Federal: 50000*0.10 + 13000*0.22 = 7860.00 / 1 = 7860.00
        assert result.taxes.federal == Decimal("7860.00"), (
            f"federal: expected 7860.00, got {result.taxes.federal}"
        )
        # State: 78000*0.045 = 3510.00 / 1 = 3510.00
        assert result.taxes.state == Decimal("3510.00"), (
            f"state: expected 3510.00, got {result.taxes.state}"
        )
        # SS: 78000*0.062 = 4836.00
        assert result.taxes.social_security == Decimal("4836.00"), (
            f"SS: expected 4836.00, got {result.taxes.social_security}"
        )
        # Medicare: 78000*0.0145 = 1131.00
        assert result.taxes.medicare == Decimal("1131.00"), (
            f"medicare: expected 1131.00, got {result.taxes.medicare}"
        )
        # net = 78000 - 7860 - 3510 - 4836 - 1131 = 60663.00
        assert result.earnings.net_pay == Decimal("60663.00"), (
            f"net: expected 60663.00, got {result.earnings.net_pay}"
        )

    def test_net_pay_negative_from_excessive_post_tax(self, simple_tax_configs):
        """Excessive post-tax deductions produce negative net pay.

        Input: annual_salary=30000 (gross=1153.85/period), post-tax deduction=2000.
        Pipeline trace:
          gross = 30000/26 = 1153.85
          federal: annual=30000.10, taxable=15000.10
            15000.10*0.10 = 1500.01 / 26 = 57.69
          state: 30000.10*0.045 = 1350.00 / 26 = 51.92
          SS: 1153.85*0.062 = 71.54
          Medicare: 1153.85*0.0145 = 16.73
          post_tax: 2000.00
          net = 1153.85 - 57.69 - 51.92 - 71.54 - 16.73 - 2000.00 = -1044.03
        Why: A user misconfiguring deductions could get a negative net pay shown
        on the budget grid. The app must handle this deterministically, not crash.
        """
        profile = FakeProfile(
            pay=Decimal("1153.85"), pay_from=date(2026, 1, 16),  # $30,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="Excessive Post Tax",
                    amount="2000",
                    paycheck_line_kind="post_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        assert result.earnings.gross_biweekly == Decimal("1153.85")
        assert result.taxes.federal == Decimal("57.69")
        assert result.taxes.state == Decimal("51.92")
        assert result.taxes.social_security == Decimal("71.54")
        assert result.taxes.medicare == Decimal("16.73")

        # The calculator returns negative net pay when post-tax deductions exceed
        # take-home. The route layer should warn the user.
        assert result.earnings.net_pay == Decimal("-1044.03"), (
            f"net_pay: expected -1044.03, got {result.earnings.net_pay}"
        )

    def test_zero_annual_salary(self, simple_tax_configs):
        """Zero salary produces zero in every field without error.

        Input: annual_salary=0 on the default 14-day cadence, no deductions.
        Expected: All fields (including annual_salary, taxable_income) are zero.
        Why: A zero-salary profile (e.g., a template or placeholder) must not
        produce NaN, crash, or negative values in any tax calculation.
        """
        profile = FakeProfile(
            pay=Decimal("0.00"), pay_from=date(2026, 1, 16),  # $0.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        assert result.earnings.annual_salary == Decimal("0.00")
        assert result.earnings.gross_biweekly == Decimal("0.00")
        assert result.earnings.taxable_income == Decimal("0.00")
        assert result.taxes.federal == Decimal("0.00")
        assert result.taxes.state == Decimal("0.00")
        assert result.taxes.social_security == Decimal("0.00")
        assert result.taxes.medicare == Decimal("0.00")
        assert result.earnings.net_pay == Decimal("0.00")

    def test_massive_deductions_exceed_gross(self, simple_tax_configs):
        """Pre-tax deductions exceeding gross clamp taxable to zero.

        Input: annual_salary=52000 (gross=2000/period), pre-tax deduction=2500.
        Pipeline trace:
          gross = 52000/26 = 2000.00
          taxable_biweekly = max(2000 - 2500, 0) = 0.00 (clamped)
          federal: adjusted = max(52000 - 65000, 0) = 0, taxable = 0, tax = 0
          state: 0*26*0.045 = 0
          SS: 2000*0.062 = 124.00 (FICA uses gross, not taxable)
          Medicare: 2000*0.0145 = 29.00
          net = 2000 - 2500 - 0 - 0 - 124 - 29 = -653.00
        Why: Pre-tax deductions reducing gross below zero would produce negative
        taxable income, which could break bracket calculations.
        """
        profile = FakeProfile(
            pay=Decimal("2000.00"), pay_from=date(2026, 1, 16),  # $52,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="Mega Pre Tax",
                    amount="2500",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs)

        # gross = 52000/26 = 2000.00
        assert result.earnings.gross_biweekly == Decimal("2000.00")
        # taxable_biweekly = max(2000 - 2500, 0) = 0.00 (clamped by source code)
        assert result.earnings.taxable_income == Decimal("0.00"), (
            f"taxable_income should be clamped to 0, got {result.earnings.taxable_income}"
        )
        # Federal/state: 0 taxable → 0 tax
        assert result.taxes.federal == Decimal("0.00")
        assert result.taxes.state == Decimal("0.00")
        # FICA is computed on gross, not taxable income.
        # SS: 2000*0.062 = 124.00
        assert result.taxes.social_security == Decimal("124.00")
        # Medicare: 2000*0.0145 = 29.00
        assert result.taxes.medicare == Decimal("29.00")
        # net = 2000 - 2500 - 0 - 0 - 124 - 29 = -653.00
        # The calculator allows negative net when deductions exceed gross.
        assert result.earnings.net_pay == Decimal("-653.00"), (
            f"net_pay: expected -653.00, got {result.earnings.net_pay}"
        )
