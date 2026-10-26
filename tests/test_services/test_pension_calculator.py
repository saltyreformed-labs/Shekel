"""
Shekel Budget App -- Unit Tests for Pension Calculator

Tests the pension benefit calculation including years of service,
high-salary average computation, and salary projection integration.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from app.services.pension_calculator import (
    PensionBenefit,
    calculate_benefit,
    project_profile_salaries,
    _calculate_years_of_service,
    _compute_high_salary_average,
    ZERO,
)
from tests._test_helpers import biweekly_window, payroll_basis


# ── Fake Objects ─────────────────────────────────────────────────


class FakeRaise:
    """A raise-shaped value carrying exactly what the walk reads.

    **It has no raise type ID, and the absence is the point** (plan step
    salary:S3-c, ruling R-SAL11).  It carried a ``raise_type_id`` resolved
    through the ref cache while the projection discriminated cola-type
    raises from merit and custom ones to decide which the merit horizon
    stopped.  Nothing discriminates now -- every raise stops at its own
    stored ``terminal_year`` -- so a type on this double would assert a
    distinction the producer can no longer make, which is how a test starts
    describing a rule that is not there.

    *It carries a type's display NAME since plan step salary:X-av-3a*, which
    projects the salary path through the paycheck engine's walk: that walk
    reads a raise set as :class:`~app.services.salary_raises.RaiseTerms`
    values, and ``RaiseTerms.of`` copies ``raise_type_name`` for the pay
    banner's label.  Nothing on the salary path reads it.
    """

    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False, terminal_year=None):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        #: The last year this raise is believed to happen, ``None`` for
        #: indefinitely.  Read directly by ``salary_raises._applications``.
        self.terminal_year = terminal_year
        #: Display only: the banner's label (see the class docstring).
        self.raise_type_name = "merit"


#: The payday every fake pay list below is recorded from: one biweekly
#: paycheck before 2026-01-02, so it precedes the first landing of every raise
#: in this module (a raise lands on the 1st of its effective month, the
#: earliest here 2026-01-01).  An entry REPLACES every forecast raise landing
#: on or before its payday (ruling R-SAL59), so an entry dated after a raise's
#: landing would hold that raise rather than exercise it.
_ENTRY_PAYDAY = date(2025, 12, 19)


@dataclass(frozen=True)
class FakePayEntry:
    """A pay entry carrying exactly what the walk reads: its payday and its pay."""

    payday: date
    amount: Decimal


class FakeProfile:
    """A salary profile carrying exactly what the pension's salary path reads.

    Plan step salary:X-av-3a replaced the yearly salary with a PAY LIST, and
    :func:`project_profile_salaries` walks it through
    :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on`: ONE entry
    paying *pay* (one paycheck's gross) from :data:`_ENTRY_PAYDAY`, raised by
    *raises*.  Its ``user_id`` owns the calendar ``payroll_basis`` derives for
    it; no user row has that id, and nothing here reads one.
    """

    def __init__(self, pay, raises, *, user_id=1):
        self.user_id = user_id
        self.pay_entries = [FakePayEntry(_ENTRY_PAYDAY, Decimal(pay))]
        self.raises = raises


def _salary_path(pay, raises, start_year, end_year):
    """The pension's salary path for a biweekly profile paid *pay* from :data:`_ENTRY_PAYDAY`.

    Each year's figure is the walk's pay on December 1 times 26 paychecks
    (ruling R-SAL59: the yearly figure is pay x paychecks a year, never
    divided back out), with each raise step rounded to the cent as a stub
    prints it (ruling R-SAL60).
    """
    return project_profile_salaries(
        payroll_basis(
            FakeProfile(pay, raises), biweekly_window(_ENTRY_PAYDAY, 1),
        ),
        start_year,
        end_year,
    )


# ── Tests ────────────────────────────────────────────────────────


class TestCalculateBenefit:
    def test_basic_benefit(self):
        """multiplier * years * average = expected benefit."""
        salary_by_year = [
            (2040, Decimal("80000")),
            (2041, Decimal("82000")),
            (2042, Decimal("84000")),
            (2043, Decimal("86000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2018, 7, 1),
            planned_retirement_date=date(2043, 7, 1),
            salary_by_year=salary_by_year,
        )
        assert result.years_of_service == Decimal("25.00")
        assert result.high_salary_average == Decimal("83000.00")
        # 0.0185 * 25 * 83000 = 38387.50
        assert result.annual_benefit == Decimal("38387.50")
        assert result.monthly_benefit == Decimal("3198.96")

    def test_high_salary_average_correct_window(self):
        """Highest consecutive window selected."""
        salary_by_year = [
            (2035, Decimal("60000")),
            (2036, Decimal("70000")),
            (2037, Decimal("80000")),
            (2038, Decimal("90000")),
            (2039, Decimal("85000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=3,
            hire_date=date(2010, 1, 1),
            planned_retirement_date=date(2040, 1, 1),
            salary_by_year=salary_by_year,
        )
        # Best 3-year window: 2037-2039 = (80000+90000+85000)/3 = 85000
        assert result.high_salary_average == Decimal("85000.00")

    def test_fewer_years_than_window(self):
        """Less data than window uses all available."""
        salary_by_year = [
            (2040, Decimal("80000")),
            (2041, Decimal("85000")),
        ]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2030, 1, 1),
            planned_retirement_date=date(2042, 1, 1),
            salary_by_year=salary_by_year,
        )
        assert result.high_salary_average == Decimal("82500.00")

    def test_empty_salary_projections(self):
        """No salary data returns zero benefit."""
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
            planned_retirement_date=date(2045, 1, 1),
            salary_by_year=[],
        )
        assert result.annual_benefit == ZERO
        assert result.monthly_benefit == ZERO

    def test_monthly_is_annual_divided_by_12(self):
        """Monthly benefit = annual / 12."""
        salary_by_year = [(2040, Decimal("100000"))]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.02"),
            consecutive_high_years=1,
            hire_date=date(2020, 1, 1),
            planned_retirement_date=date(2040, 1, 1),
            salary_by_year=salary_by_year,
        )
        expected_annual = Decimal("0.02") * Decimal("20.00") * Decimal("100000")
        expected_monthly = (expected_annual / 12).quantize(Decimal("0.01"))
        assert result.monthly_benefit == expected_monthly

    def test_very_short_service(self):
        """Less than 1 year of service.

        days = (2026-06-01 - 2026-01-01) = 151
        years = (151 / 365.25).quantize(0.01) = 0.41
        high_salary_avg = 80000.00 (window min(4,1)=1)
        annual = 0.0185 * 0.41 * 80000 = 606.80
        monthly = 606.80 / 12 = 50.57
        """
        salary_by_year = [(2026, Decimal("80000"))]
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2026, 1, 1),
            planned_retirement_date=date(2026, 6, 1),
            salary_by_year=salary_by_year,
        )
        assert result.years_of_service == Decimal("0.41"), (
            f"Expected 0.41 years, got {result.years_of_service}"
        )
        assert result.annual_benefit == Decimal("606.80"), (
            f"Expected annual 606.80, got {result.annual_benefit}"
        )
        assert result.monthly_benefit == Decimal("50.57"), (
            f"Expected monthly 50.57, got {result.monthly_benefit}"
        )


class TestYearsOfService:
    def test_exact_years(self):
        result = _calculate_years_of_service(date(2000, 1, 1), date(2025, 1, 1))
        assert result == Decimal("25.00")

    def test_zero_service(self):
        result = _calculate_years_of_service(date(2026, 1, 1), date(2026, 1, 1))
        assert result == ZERO

    def test_negative_service(self):
        result = _calculate_years_of_service(date(2026, 1, 1), date(2025, 1, 1))
        assert result == ZERO

    def test_none_dates(self):
        assert _calculate_years_of_service(None, date(2040, 1, 1)) == ZERO
        assert _calculate_years_of_service(date(2020, 1, 1), None) == ZERO


class TestHighSalaryAverage:
    def test_single_year(self):
        avg, window = _compute_high_salary_average(
            [(2040, Decimal("80000"))], 1
        )
        assert avg == Decimal("80000.00")

    def test_highest_at_end(self):
        data = [
            (2036, Decimal("60000")),
            (2037, Decimal("70000")),
            (2038, Decimal("80000")),
            (2039, Decimal("90000")),
        ]
        avg, window = _compute_high_salary_average(data, 2)
        # Best 2-year: 2038-2039 = (80000+90000)/2 = 85000
        assert avg == Decimal("85000.00")
        assert len(window) == 2




class TestProjectSalariesByYear:
    """The pension's salary path, re-stated on the pay list's walk (plan step salary:X-av-3a).

    ``project_salaries_by_year(annual_salary, raises, ...)`` compounded a
    stored yearly salary and was deleted with it; the path is
    :func:`project_profile_salaries` now, the walk's pay on each December 1
    times the paychecks a year.  Each case keeps its rule and its raises; the
    salary is the one paycheck the migration writes for it,
    round-half-up(yearly / 26), so the yearly figures move by the
    pay-times-count product (ruling R-SAL59) and by each raise step's cent
    rounding (ruling R-SAL60).
    """

    def test_no_raises(self):
        """No raise: every year is the entry's pay times 26.

        $80,000 a year is 3,076.92 a paycheck (80,000 / 26 = 3,076.923...);
        3,076.92 x 26 = 79,999.92.
        """
        result = _salary_path("3076.92", [], 2026, 2028)
        assert len(result) == 3
        for _year, salary in result:
            assert salary == Decimal("79999.92")

    def test_with_recurring_raise(self):
        """Recurring 3% raise with no end year compounds each year.

        Each year is evaluated at December 1, so the March application
        always applies.  From 3,076.92 a paycheck, one cent-rounded step per
        year:
        2026: 3,076.92 x 1.03 = 3,169.2276 -> 3,169.23; x 26 = 82,399.98
        2027: 3,169.23 x 1.03 = 3,264.3069 -> 3,264.31; x 26 = 84,872.06
        2028: 3,264.31 x 1.03 = 3,362.2393 -> 3,362.24; x 26 = 87,418.24
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=3,
                      effective_year=2026, is_recurring=True),
        ]
        result = _salary_path("3076.92", raises, 2026, 2028)
        # 3,169.23 x 26
        assert result[0][1] == Decimal("82399.98"), (
            f"2026 salary: expected 82399.98, got {result[0][1]}"
        )
        # 3,264.31 x 26
        assert result[1][1] == Decimal("84872.06"), (
            f"2027 salary: expected 84872.06, got {result[1][1]}"
        )
        # 3,362.24 x 26
        assert result[2][1] == Decimal("87418.24"), (
            f"2028 salary: expected 87418.24, got {result[2][1]}"
        )

    def test_recurring_raise_highest_years_near_retirement(self):
        """A raise with no end year extrapolates to retirement.

        A 2.5% recurring raise believed indefinitely from 2026 to 2046, on
        3,461.54 a paycheck ($90,000 / 26), makes the last 4 years the
        highest, which is what the high-salary window must then select.
        """
        raises = [
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True),
        ]
        salary_by_year = _salary_path("3461.54", raises, 2026, 2046)
        result = calculate_benefit(
            benefit_multiplier=Decimal("0.0185"),
            consecutive_high_years=4,
            hire_date=date(2006, 6, 1),
            planned_retirement_date=date(2046, 6, 1),
            salary_by_year=salary_by_year,
        )
        high_years = [y for y, _ in result.high_salary_years]
        # The 4 highest consecutive salary years must be the last 4
        assert high_years == [2043, 2044, 2045, 2046], (
            f"Expected highest years near retirement, got {high_years}"
        )


class TestTheEndYearOnEachRaise:
    """How long each raise is believed, read off the raise (**R-SAL11**).

    **Translated from ``TestMeritHorizon`` at plan step salary:S3-c**, which
    reproduced that class's walk to the cent; **re-stated on the pay list's
    walk at plan step salary:X-av-3a**, where the stored fact became one
    paycheck (3,846.15 for $100,000 / 26) and each raise step rounds to the
    cent (ruling R-SAL60), so every figure below is the walk's per-paycheck
    pay on December 1 times 26 and each case lists its steps.  The rules are
    unchanged: an end year stops a raise, no end year compounds, a raise is
    never pulled before its start, applications walk in date order.

    Each raise evaluates at December 1, so the effective month never gates
    the December-of-year application.
    """

    def test_a_raise_with_an_end_year_stops_while_one_without_continues(self):
        """Two 10% raises, one ending 2028 and one believed indefinitely.

        3,846.15 a paycheck; both recurring from 2026, one effective January
        and ending 2028, the other effective July with no end year.  Each
        step x 1.10, rounded to the cent:
          2026-01 4,230.77   2026-07 4,653.85  -> 2026: x 26 = 121,000.10
          2027-01 5,119.24   2027-07 5,631.16
          2028-01 6,194.28   2028-07 6,813.71  -> 2028: x 26 = 177,156.46
        After 2028 the ended raise contributes nothing further and only the
        July one compounds:
          2029-07 7,495.08  -> 2029: x 26 = 194,872.08  (NOT 8,244.59 x 26 =
                               214,359.34, which is both-continue)
          2030-07 8,244.59   2031-07 9,069.05 -> 2031: x 26 = 235,795.30
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      terminal_year=2028),
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True),
        ]
        result = dict(_salary_path("3846.15", raises, 2026, 2031))
        # 4,653.85 x 26 (both raises applied once)
        assert result[2026] == Decimal("121000.10")
        # 6,813.71 x 26 (both raises still applying)
        assert result[2028] == Decimal("177156.46")
        # 7,495.08 x 26 (the ended raise contributes nothing)
        assert result[2029] == Decimal("194872.08")
        # 9,069.05 x 26
        assert result[2031] == Decimal("235795.30")

    def test_a_raise_with_no_end_year_compounds_uninterrupted(self):
        """A raise believed indefinitely drops and repeats no occurrence.

        3,846.15 a paycheck; 10% (July) recurring from 2026 with no end
        year, one step a year:
          2026: 4,230.77 x 26 = 110,000.02
          2027: 4,653.85
          2028: 5,119.24 x 26 = 133,100.24
          2029: 5,631.16 x 26 = 146,410.16
          2030: 6,194.28 x 26 = 161,051.28
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2026, is_recurring=True),
        ]
        result = dict(_salary_path("3846.15", raises, 2026, 2030))
        assert result[2026] == Decimal("110000.02")   # 4,230.77 x 26
        assert result[2028] == Decimal("133100.24")   # 5,119.24 x 26
        assert result[2029] == Decimal("146410.16")   # 5,631.16 x 26
        assert result[2030] == Decimal("161051.28")   # 6,194.28 x 26

    def test_the_salary_plateaus_after_the_only_raises_end_year(self):
        """One 10% raise ending 2028, and nothing else moves the salary.

        3,846.15 a paycheck; recurring from 2026, ending 2028:
          2026: 4,230.77 x 26 = 110,000.02
          2027: 4,653.85
          2028: 5,119.24 x 26 = 133,100.24
          2029: 133,100.24 (nothing applies)
          2030: 133,100.24 (nothing applies)
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      terminal_year=2028),
        ]
        result = dict(_salary_path("3846.15", raises, 2026, 2030))
        assert result[2026] == Decimal("110000.02")   # 4,230.77 x 26
        assert result[2028] == Decimal("133100.24")   # 5,119.24 x 26
        assert result[2029] == Decimal("133100.24")   # plateau
        assert result[2030] == Decimal("133100.24")   # plateau

    def test_a_future_scheduled_raise_is_not_pulled_before_its_start(self):
        """A raise effective 2031 first applies in ITS year, never earlier (H1).

        3,846.15 a paycheck; a 10% recurring raise effective 2031, no end
        year:
          2026-2030: 3,846.15 x 26 = 99,999.90  (it does not exist yet)
          2031: 4,230.77 x 26 = 110,000.02  (first application)
          2032: 4,653.85 x 26 = 121,000.10

        H1 was the defect this guards: the merit horizon plan step S3-c
        deleted expressed itself by RE-ANCHORING a raise's effective year past
        a cutoff so a second compounding pass counted only the occurrences
        beyond it, and a plain reset pulled this 2031 raise back to 2029.  It
        needed a ``max(own, anchor)`` floor to stop that.  Nothing has moved
        an effective year since plan step salary:S3-a, so the floor has no
        subject; the case is kept because a future implementation could
        reintroduce one.
        """
        raises = [
            FakeRaise(percentage="0.10", effective_month=7,
                      effective_year=2031, is_recurring=True),
        ]
        result = dict(_salary_path("3846.15", raises, 2026, 2032))
        assert result[2028] == Decimal("99999.90")   # not live yet
        assert result[2029] == Decimal("99999.90")   # NOT pulled to 2029 (H1)
        assert result[2030] == Decimal("99999.90")
        assert result[2031] == Decimal("110000.02")  # 4,230.77 x 26
        assert result[2032] == Decimal("121000.10")  # 4,653.85 x 26

    def test_mixed_flat_and_percentage_raises_walk_chronologically(self):
        """Flat and percentage raises compound in the order the money arrives.

        3,846.15 a paycheck; a flat $1,000-a-year recurring raise (1,000 / 26
        = 38.4615... a paycheck, ruling R-SAL65) + a 10% recurring one, both
        effective January 2026 and neither ending.  The walk applies each
        APPLICATION on the date it lands, flat before percentage within a
        date (M-01), each step rounded to the cent:

          2026: 3,884.61, then x 1.10 = 4,273.07 -> x 26 = 111,099.82
          2027: 4,311.53, then x 1.10 = 4,742.68 -> x 26 = 123,309.68
          2028: 4,781.14, then x 1.10 = 5,259.25 -> x 26 = 136,740.50
          2029: 5,297.71, then x 1.10 = 5,827.48 -> x 26 = 151,514.48
          2030: 5,865.94, then x 1.10 = 6,452.53 -> x 26 = 167,765.78

        The two VALUE pins are what this grades: a walk grouping each raise's
        run (every flat step before every percentage one) answers 5,272.80
        (137,092.80) at 2028 and 6,503.97 (169,103.22) at 2030, computed by
        hand -- off by 352.30 and 1,337.44.

        **It used to assert an INVARIANCE as well**, running the same
        raises at ``merit_horizon_years`` 10 and 2 and asserting the two
        answered identically -- which graded that a recurring cola was not
        given a terminal year.  Plan step salary:S3-c deleted the parameter
        and the type test with it, so both calls became the same call and
        the assertion became a tautology.  It is dropped rather than
        rewritten: the direction it half-covered -- that a raise WITH an end
        year really stops -- is graded by
        ``test_a_raise_with_an_end_year_stops_while_one_without_continues``
        and ``test_the_salary_plateaus_after_the_only_raises_end_year``,
        each of which a no-op end year fails.
        """
        raises = [
            FakeRaise(flat_amount="1000", effective_month=1,
                      effective_year=2026, is_recurring=True),
            FakeRaise(percentage="0.10", effective_month=1,
                      effective_year=2026, is_recurring=True),
        ]
        result = dict(_salary_path("3846.15", raises, 2026, 2030))
        # Third year of the walk above: 5,259.25 x 26.
        assert result[2028] == Decimal("136740.50")
        # Fifth year of the walk above: 6,452.53 x 26.
        assert result[2030] == Decimal("167765.78")

    def test_a_one_time_raise_dated_late_now_actually_happens(self):
        """A late one-time raise is not dropped.

        **Plan step salary:S3-c's behaviour change**, and measurement 3 of
        ruling **R-SAL11** is the defect it names.  Under the deleted merit
        horizon every one-time raise was handed the global cutoff as its
        terminal year, so a promotion the owner had recorded for a year past
        it applied ZERO times and the projection silently never showed it.
        A one-time raise carries no end year at all
        (``ck_salary_raises_terminal_year_only_on_a_recurring_raise``), so a
        recorded raise happens.

        Three runs against the same 3% January recurring raise on 3,846.15 a
        paycheck, asked at 2035; the one-time raises are $2,000 a year, which
        is 2,000 / 26 = 76.923... a paycheck:

          the recurring raise alone      -> 5,168.91 x 26 = 134,391.66
          + one-time $2,000 in May 2028  -> 5,263.51 x 26 = 136,851.26
                                            (4,202.79 + 76.92... = 4,279.71
                                            in May 2028, then seven more 3%)
          + one-time $2,000 in May 2035  -> 5,245.83 x 26 = 136,391.58
                                            (5,168.91 + 76.92... = 5,245.83,
                                            after that year's January step)

        The 2028 one fell inside the old cutoff of 2031, and it is kept here
        as the control: a build that dropped every one-time raise, or one that
        applied the late one twice, fails one of the three.
        """
        recurring = FakeRaise(percentage="0.03", effective_month=1,
                              effective_year=2026, is_recurring=True)
        within = FakeRaise(flat_amount="2000", effective_month=5,
                           effective_year=2028, is_recurring=False)
        late = FakeRaise(flat_amount="2000", effective_month=5,
                         effective_year=2035, is_recurring=False)
        pay = "3846.15"
        alone = dict(_salary_path(pay, [recurring], 2026, 2035))
        with_within = dict(_salary_path(pay, [recurring, within], 2026, 2035))
        with_late = dict(_salary_path(pay, [recurring, late], 2026, 2035))

        # Ten cent-rounded 3% steps from 3,846.15: 5,168.91 x 26.
        assert alone[2035] == Decimal("134391.66")
        # It lands in May 2035, after that year's January application, and
        # nothing follows it: 5,245.83 x 26.
        assert with_late[2035] == Decimal("136391.58")
        assert with_late[2035] > alone[2035]
        # Seven later 3% applications compound it, which is why it answers
        # more than the late one: 5,263.51 x 26.
        assert with_within[2035] == Decimal("136851.26")
        assert with_within[2035] > with_late[2035]

    def test_an_ended_raise_does_not_compound_later_flat_dollars(self):
        """A raise that stopped in 2031 must not grow 2040's flat money.

        The regression that parked the first attempt at plan step S4.  A
        recurring flat $1,500-a-year raise (1,500 / 26 = 57.6923... a
        paycheck, which rounds each step to +57.69) with no end year and a
        recurring 4% one ending 2031, on 3,846.15 a paycheck.  Each January
        through 2031 adds the flat step then the 4% one (flat first on one
        date, M-01), reaching 5,264.56 in 2031.  Once the 4% raise ends,
        each later year may only add the flat step -- its multiplier has no
        claim on money that arrives after it stopped:

          2031: 5,264.56 x 26 = 136,878.56
          2032: 5,322.25 x 26 = 138,378.50   (+57.69 a paycheck, 1,499.94)
          2033: 5,379.94 x 26 = 139,878.44   (+57.69 a paycheck, 1,499.94)
          2040: 5,783.77 x 26 = 150,378.02   (+57.69 seven more times)

        The attempt applied the ended raise to those additions, because it
        removed the two-phase split while the walk still grouped applications
        by raise.  On the yearly engine it answered ``155,001.58`` at 2040
        against the ``150,379.34`` then asserted -- **+4,622.24** -- and
        1,040.43 of the gap was already present at 2031, so the divergence
        was never purely post-cutoff.  It reads correctly here only because
        the walk orders by date, so this case pins BOTH rules at once.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        ending = FakeRaise(percentage="0.04", effective_month=1,
                           effective_year=2026, is_recurring=True,
                           terminal_year=2031)
        result = dict(_salary_path("3846.15", [flat, ending], 2026, 2040))
        assert result[2031] == Decimal("136878.56")
        assert result[2032] == Decimal("138378.50")
        assert result[2033] == Decimal("139878.44")
        assert result[2040] == Decimal("150378.02")
        # Stated as the increment, because that is the defect's shape:
        # the flat step alone, 57.69 x 26.
        assert result[2032] - result[2031] == Decimal("1499.94")
        assert result[2033] - result[2032] == Decimal("1499.94")

    def test_real_shaped_pair_one_ending_and_one_not(self):
        """3% July raise with no end year + 2.5% January one ending 2031.

        3,846.15 a paycheck; both recurring from 2026; projected
        2026..2035.  The 2.5% raise applies six times (each January
        2026..2031); the 3% one is never believed to stop and applies each
        July.  Each step rounded to the cent, independently of the producer:

          2026: 3,942.30 (Jan), 4,060.57 (Jul) -> x 26 = 105,574.82
          2031: 5,170.76 (Jan), 5,325.88 (Jul) -> x 26 = 138,472.88
                (its last year)
          2032: 5,485.66 (Jul only)           -> x 26 = 142,627.16
          2033: 5,650.23                      -> x 26 = 146,905.98
          2035: 5,994.33                      -> x 26 = 155,852.58

        **The oracle is absolute, not relative.**  This test used to assert
        ``result[2032] == round_money(result[2031] * 1.03)`` -- it read a
        year back out of the producer and re-compounded it, and an oracle
        built from the value under test cannot catch an error that moves
        both years together.
        """
        raises = [
            FakeRaise(percentage="0.03", effective_month=7,
                      effective_year=2026, is_recurring=True),
            FakeRaise(percentage="0.025", effective_month=1,
                      effective_year=2026, is_recurring=True,
                      terminal_year=2031),
        ]
        result = dict(_salary_path("3846.15", raises, 2026, 2035))
        # 4,060.57 x 26 (both apply)
        assert result[2026] == Decimal("105574.82")
        # Its last believed year: six of each.  5,325.88 x 26.
        assert result[2031] == Decimal("138472.88")
        # Past it the 2.5% raise STOPS while the 3% one continues.
        assert result[2032] == Decimal("142627.16")
        assert result[2033] == Decimal("146905.98")
        assert result[2035] == Decimal("155852.58")
        # And it is genuinely stopped: had it kept applying, 2032 would walk
        # 5,325.88 x 1.025 = 5,459.03 (Jan), then x 1.03 = 5,622.80 (Jul),
        # and 5,622.80 x 26 = 146,192.80, which is strictly more.
        assert result[2032] < Decimal("146192.80")
