"""
Shekel Budget App -- Unit Tests for Paycheck Calculator

Tests the recurring raise compounding logic -- the pay list's walk,
PayrollBasis.base_pay_on(), since plan step salary:X-av-3a deleted
salary_raises.apply_raises() and its yearly salary -- and the full
calculate_paycheck()
pipeline including deductions, taxes, 3rd-paycheck detection, inflation,
cumulative wages, and project_salary().
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.exceptions import InvalidGrossPayError
from app.services.pay_rhythm import FixedDays
from app.services.tax_calculator import calculate_fica
from app.models.salary_raise import SalaryRaise
from app.services.paycheck_calculator import (
    calculate_paycheck,
    project_salary,
    PricedLine,
    DeductionBreakdown,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)
# The private helpers come from their LEAF since the engine became a package
# (plan step salary:C12, ledger row P64), as pay_calendar's do.
from app.services.paycheck_calculator._calendar_questions import (
    _get_cumulative_wages,
    _is_third_paycheck,
    _month_ordinal,
)
from app.services.paycheck_calculator._lines import (
    _inflation_years,
    _LineContext,
    _priced_lines,
)
from app.utils.money import ZERO
from app import ref_cache
from app.enums import PaycheckLineKindEnum, RaiseTypeEnum
from app.services.salary_raises import RaiseTerms, terms_of
from app.services.pay_calendar import (
    DerivedPeriod,
    PayCadence,
    PayCalendarError,
)
from tests._test_helpers import payroll_basis

#: The cent quantum these cases round their hand-computed expectations to.
#:
#: Defined HERE rather than imported from the engine since plan step
#: **balance:X-aw**, which deleted the module constant with the residue
#: distribution that was its only production reader.  A constant kept alive in
#: ``app/`` for tests alone is the speculative shape ``CLAUDE.md`` rule 13
#: forbids, and an expectation spelled independently of the code it grades is
#: the point of a test.
TWO_PLACES = Decimal("0.01")


def _timing_id(name):
    """Resolve a payroll line kind name (e.g. 'pre_tax_deduction') to its integer ID."""
    _map = {e.value: e for e in PaycheckLineKindEnum}
    return ref_cache.paycheck_line_kind_id(_map[name])


# ── Fake Objects ─────────────────────────────────────────────────


class FakeRaise:
    """Minimal stand-in for a SalaryRaise ORM object."""

    def __init__(self, percentage=None, flat_amount=None,
                 effective_month=3, effective_year=2026,
                 is_recurring=False, terminal_year=None):
        self.percentage = Decimal(str(percentage)) if percentage else None
        self.flat_amount = Decimal(str(flat_amount)) if flat_amount else None
        self.effective_month = effective_month
        self.effective_year = effective_year
        self.is_recurring = is_recurring
        # The last year the raise is believed to happen, None for
        # indefinitely.  REQUIRED on the stand-in since plan step
        # salary:S3-c: the walk reads it as a plain attribute now, so a
        # double without it no longer silently reads as "no end year".
        self.terminal_year = terminal_year
        # The one attribute the engine reads of the type -- what the model
        # exposes as a property and ``RaiseTerms`` carries as a field (plan
        # step salary:S3-f-1); it carried a type OBJECT until then.
        self.raise_type_name = "merit"


def _period(start_date, period_id=1):
    """A REAL :class:`DerivedPeriod` for the engine under test.

    It was a ``FakePeriod`` stand-in carrying ``start_date`` and ``id`` until
    pay-calendar plan step C2-f2d-3 moved this engine onto the derived
    calendar.  Building the production value rather than a duck type is what
    makes these cases grade the attributes the engine actually reads: a
    stand-in with the old ``id`` spelling would have kept passing while every
    production caller broke.

    ``end_date`` and ``period_index`` are filled to be internally consistent
    with a biweekly rhythm; the engine reads neither (AST-censused before the
    move -- ``start_date`` and the id are its whole period surface), so they
    are here because the frozen value requires them, not because anything
    under test consults them.
    """
    return DerivedPeriod(
        period_id=period_id,
        period_index=period_id - 1,
        start_date=start_date,
        end_date=start_date + timedelta(days=13),
        end_is_projected=False,
    )


class FakeLineKind:
    def __init__(self, name="pre_tax_deduction"):
        self.name = name


class FakeCalcMethod:
    def __init__(self, name="flat"):
        self.name = name


class FakeRule:
    """Minimal stand-in for a deduction's ``RecurrenceRule`` row.

    Carries exactly the attributes :func:`app.services.recurrence
    .recurrence_spec` reads off a row (plan step salary:R15-b), so the engine
    resolves it against the basis's calendar as it would a stored rule.
    ``user_id`` is what the row's owning arc would answer; the calendar the
    :func:`payroll_basis` helper derives belongs to user 1.
    """

    def __init__(self, *, unit, placement, starts_on, interval_n=1,
                 max_per_month=None, user_id=1):
        # pylint: disable=import-outside-toplevel
        from app import ref_cache
        self.user_id = user_id
        self.unit_id = ref_cache.recurrence_unit_id(unit)
        self.placement_id = ref_cache.period_placement_id(placement)
        self.interval_n = interval_n
        self.starts_on = starts_on
        self.max_per_month = max_per_month
        self.nominal_day = None
        self.end_date = None
        self.max_occurrences = None


def twenty_four_rule(starts_on, **kwargs):
    """The rule migration 542c61e48ee8 writes for a 24-per-year line."""
    # pylint: disable=import-outside-toplevel
    from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
    return FakeRule(
        unit=RecurrenceUnitEnum.PERIOD,
        placement=PeriodPlacementEnum.CONTAINING_DATE,
        starts_on=starts_on, max_per_month=2, **kwargs,
    )


def twelve_rule(starts_on, **kwargs):
    """The rule migration 542c61e48ee8 writes for a 12-per-year line."""
    # pylint: disable=import-outside-toplevel
    from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
    return FakeRule(
        unit=RecurrenceUnitEnum.MONTH,
        placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        starts_on=starts_on, **kwargs,
    )


class FakeDeduction:
    """Minimal stand-in for a PaycheckLine ORM object."""

    def __init__(self, name="401k", amount="200", recurrence_rule=None,
                 calc_method="flat", paycheck_line_kind="pre_tax_deduction",
                 inflation_enabled=False, inflation_rate=None,
                 inflation_effective_month=None, is_active=True,
                 annual_cap=None):
        self.name = name
        self.amount = Decimal(str(amount))
        # The line's cadence (plan step salary:R15-b): a :class:`FakeRule`,
        # or ``None`` for every paycheck, as the real column pair reads.
        self.recurrence_rule = recurrence_rule
        self.calc_method = FakeCalcMethod(calc_method)
        self.paycheck_line_kind = FakeLineKind(paycheck_line_kind)
        self.inflation_enabled = inflation_enabled
        self.inflation_rate = Decimal(str(inflation_rate)) if inflation_rate else None
        self.inflation_effective_month = inflation_effective_month
        self.is_active = is_active
        # Calendar-year dollar ceiling (PaycheckLine.annual_cap); None =
        # uncapped.  Mirrors the real model column the calculator clamps on.
        self.annual_cap = Decimal(str(annual_cap)) if annual_cap is not None else None
        # Resolve integer IDs from the ref_cache for ID-based comparisons.
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import CalcMethodEnum, PaycheckLineKindEnum  # pylint: disable=import-outside-toplevel
        _timing_map = {e.value: e for e in PaycheckLineKindEnum}
        _method_map = {e.value: e for e in CalcMethodEnum}
        self.paycheck_line_kind_id = ref_cache.paycheck_line_kind_id(_timing_map[paycheck_line_kind])
        self.calc_method_id = ref_cache.calc_method_id(_method_map[calc_method])


class FakeBracket:
    def __init__(self, min_income, max_income, rate, sort_order):
        self.min_income = min_income
        self.max_income = max_income
        self.rate = rate
        self.sort_order = sort_order


class FakeBracketSet:
    def __init__(self, standard_deduction=Decimal("15000"),
                 child_credit_amount=Decimal("2000"),
                 other_dependent_credit_amount=Decimal("500"),
                 brackets=None):
        self.standard_deduction = standard_deduction
        self.child_credit_amount = child_credit_amount
        self.other_dependent_credit_amount = other_dependent_credit_amount
        self.brackets = brackets or []


class FakeTaxType:
    def __init__(self, name="flat"):
        self.name = name


class FakeStateTaxConfig:
    def __init__(self, flat_rate="0.045", tax_type_name="flat"):
        self.flat_rate = Decimal(str(flat_rate))
        self.tax_type = FakeTaxType(tax_type_name)
        # Resolve the integer ID from the ref_cache for ID-based lookups.
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TaxTypeEnum  # pylint: disable=import-outside-toplevel
        _name_to_enum = {e.value: e for e in TaxTypeEnum}
        self.tax_type_id = ref_cache.tax_type_id(_name_to_enum[tax_type_name])


class FakeFicaConfig:
    def __init__(self, ss_rate="0.062", ss_wage_base="168600",
                 medicare_rate="0.0145", medicare_surtax_rate="0.009",
                 medicare_surtax_threshold="200000"):
        self.ss_rate = Decimal(str(ss_rate))
        self.ss_wage_base = Decimal(str(ss_wage_base))
        self.medicare_rate = Decimal(str(medicare_rate))
        self.medicare_surtax_rate = Decimal(str(medicare_surtax_rate))
        self.medicare_surtax_threshold = Decimal(str(medicare_surtax_threshold))


@dataclass(frozen=True)
class FakePayEntry:
    """Minimal stand-in for a ``SalaryPayEntry`` row: the two fields the engine reads."""

    payday: date
    amount: Decimal


class FakeProfile:
    """Minimal stand-in for a SalaryProfile ORM object.

    **It carries no paycheck count**, and has not since plan step R-F16 dropped
    ``salary_profiles.pay_periods_per_year``.  How often the owner is paid
    reaches the engine on the :class:`PayrollBasis` beside the profile, which
    every call here builds through the shared ``payroll_basis`` helper.

    **It carries a pay LIST, not a yearly figure**, since plan step
    salary:X-av-3a replaced ``annual_salary`` with ``salary.pay_entries``: one
    entry paying *pay* (one paycheck's gross) from *pay_from*, by default
    2026-01-02, the first payday of the ``biweekly_periods`` fixture.

    **It holds no pay stub**, so every paycheck here is priced by the tax
    formulas alone; pricing from a stub (plan step salary:S11-c-2c) is
    ``test_paycheck_stub_pricing.py``'s, on real rows.
    """

    def __init__(self, pay, raises=None, lines=None,
                 created_at=None, pay_from=date(2026, 1, 2),
                 additional_income=0, additional_deductions=0,
                 extra_withholding=0, qualifying_children=0,
                 other_dependents=0):
        self.pay_entries = [FakePayEntry(pay_from, Decimal(str(pay)))]
        self.raises = raises or []
        self.lines = lines or []
        self.pay_stubs = []
        self.created_at = created_at
        self.additional_income = Decimal(str(additional_income))
        self.additional_deductions = Decimal(str(additional_deductions))
        self.extra_withholding = Decimal(str(extra_withholding))
        self.qualifying_children = qualifying_children
        self.other_dependents = other_dependents


# ── Pytest Fixtures ──────────────────────────────────────────────


@pytest.fixture
def simple_bracket_set():
    """2-bracket progressive set: 10% up to $50k, 22% above."""
    return FakeBracketSet(
        standard_deduction=Decimal("15000"),
        brackets=[
            FakeBracket(Decimal("0"), Decimal("50000"), Decimal("0.10"), 0),
            FakeBracket(Decimal("50000"), None, Decimal("0.22"), 1),
        ],
    )


@pytest.fixture
def nc_state_config():
    """NC flat rate 4.5%."""
    return FakeStateTaxConfig(flat_rate="0.045")


@pytest.fixture
def standard_fica():
    """Standard 2026 FICA rates."""
    return FakeFicaConfig()


@pytest.fixture
def simple_tax_configs(simple_bracket_set, nc_state_config, standard_fica):
    """Combined tax config dict."""
    return {
        "bracket_set": simple_bracket_set,
        "state_config": nc_state_config,
        "fica_config": standard_fica,
    }


@pytest.fixture
def base_profile():
    """$60k salary, 26 periods, no raises/deductions."""
    return FakeProfile(
        pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        created_at=date(2026, 1, 1),
    )


@pytest.fixture
def biweekly_periods():
    """26 derived pay periods for 2026, starting Jan 2."""
    start = date(2026, 1, 2)
    periods = []
    for i in range(26):
        d = date.fromordinal(start.toordinal() + i * 14)
        periods.append(_period(start_date=d, period_id=i + 1))
    return periods


#: The schedule the raise walks below are priced on (plan step
#: salary:X-av-3a): biweekly from 2025-12-19, the payday before the
#: ``biweekly_periods`` fixture's first.  A pay entry REPLACES every raise
#: landing on or before its payday (ruling **R-SAL59**), so the entries here
#: are dated on it -- before every raise these cases state, the earliest of
#: which lands on 2026-01-01 -- which is the developer's rule-5 ruling "Keep
#: each raise working".
_WALK_OPENS = date(2025, 12, 19)


def _walked_pay(profile, payday):
    """What *profile*'s pay list pays on *payday*, on the schedule from :data:`_WALK_OPENS`.

    The raise walk these cases grade was ``apply_raises`` over a YEARLY salary
    until plan step salary:X-av-3a deleted it; it is
    :meth:`~app.services.payroll_basis.PayrollBasis.base_pay_on` now, over
    what ONE paycheck pays, each raise step rounded to the cent (ruling
    **R-SAL60**) and a flat raise's yearly dollars spread over the paychecks a
    year where it lands (ruling **R-SAL65**).
    """
    basis = payroll_basis(profile, [_period(start_date=_WALK_OPENS)])
    return basis.base_pay_on(payday).per_paycheck


# ── Existing Tests: Recurring Raise Compounding ──────────────────


class TestRecurringRaiseCompounding:
    """Verify that recurring raises compound correctly across years.

    Re-stated on the pay list's walk at plan step salary:X-av-3a (the
    developer's rule-5 ruling "Re-state each on the walk"): the same cases,
    asserted per paycheck.  $100,000.00 a year was $3,846.15 a paycheck
    (100000 / 26 = 3846.1538... -> 3846.15), priced from 2025-12-19
    (:data:`_WALK_OPENS`), and each 3% step rounds to the cent (R-SAL60):
    3846.15 x 1.03 = 3961.5345 -> 3961.53; x 1.03 = 4080.3759 -> 4080.38;
    x 1.03 = 4202.7914 -> 4202.79.
    """

    @staticmethod
    def _march_merit():
        """A 3% raise recurring every March from 2026, on $3,846.15 a paycheck."""
        return FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[FakeRaise(percentage="0.03", effective_month=3,
                              effective_year=2026, is_recurring=True)],
        )

    def test_recurring_raise_not_yet_effective(self):
        """Before effective month in effective year, raise should not apply."""
        # 2026-02-13, February's first payday: the March raise has not landed.
        assert _walked_pay(self._march_merit(), date(2026, 2, 13)) == Decimal("3846.15")

    def test_recurring_raise_first_year_at_effective_month(self):
        """In effective year at effective month, raise should apply once."""
        # 2026-03-13, March's first payday: 3846.15 x 1.03 = 3961.5345 -> 3961.53
        assert _walked_pay(self._march_merit(), date(2026, 3, 13)) == Decimal("3961.53")

    def test_recurring_raise_first_year_after_effective_month(self):
        """Later in effective year, raise should still apply once."""
        # 2026-06-05: still the one application, 3961.53
        assert _walked_pay(self._march_merit(), date(2026, 6, 5)) == Decimal("3961.53")

    def test_recurring_raise_second_year_before_month(self):
        """Next year before effective month: still only 1 application."""
        # 2027-01-01: 2027's March not reached, so still 3961.53
        assert _walked_pay(self._march_merit(), date(2027, 1, 1)) == Decimal("3961.53")

    def test_recurring_raise_second_year_after_month(self):
        """Next year after effective month: 2 total applications (compounded)."""
        # 2027-04-09: 3961.53 x 1.03 = 4080.3759 -> 4080.38
        assert _walked_pay(self._march_merit(), date(2027, 4, 9)) == Decimal("4080.38")

    def test_recurring_raise_third_year(self):
        """Two years later after effective month: 3 total applications.

        2028-06-02: 4080.38 x 1.03 = 4202.7914 -> 4202.79 -- one cent under
        the yearly walk's 109,272.70 / 26 = 4202.80, which is the per-raise
        rounding ruling R-SAL60 accepts.
        """
        assert _walked_pay(self._march_merit(), date(2028, 6, 2)) == Decimal("4202.79")

    def test_one_time_raise_applies_once(self):
        """A non-recurring raise should only apply once."""
        profile = FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[FakeRaise(percentage="0.05", effective_month=1,
                              effective_year=2026, is_recurring=False)],
        )
        # Check in 2027 (2027-06-04) -- still just one application:
        # 3846.15 x 1.05 = 4038.4575 -> 4038.46.
        assert _walked_pay(profile, date(2027, 6, 4)) == Decimal("4038.46")

    def test_recurring_flat_raise(self):
        """Recurring flat raise should add the flat amount each year.

        $5,000 a year is 5000 / 26 = 192.3077 a paycheck (R-SAL65), each
        application rounded (R-SAL60): 3846.15 + 192.3077 = 4038.4577 ->
        4038.46; + 192.3077 = 4230.7677 -> 4230.77; + 192.3077 = 4423.0777 ->
        4423.08 -- the same 115,000 / 26 = 4423.08 the yearly walk priced.
        """
        profile = FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[FakeRaise(flat_amount="5000", effective_month=1,
                              effective_year=2026, is_recurring=True)],
        )
        # 2028-06-02: 3 applications (2026, 2027, 2028).
        assert _walked_pay(profile, date(2028, 6, 2)) == Decimal("4423.08")

    def test_recurring_raise_second_year_at_effective_month(self):
        """March 2026 raise checked in March 2027 = 2 applications.

        Validates the `+1` in the application count formula:
          years_passed = 2027 - 2026 = 1
          period_month (3) >= eff_month (3) → applications = 1 + 1 = 2
        This is correct because the raise applied in March 2026 AND
        recurs again in March 2027.  On 2027-03-12, March's first payday:
        3846.15 x 1.03 = 3961.53; x 1.03 = 4080.3759 -> 4080.38.
        """
        assert _walked_pay(self._march_merit(), date(2027, 3, 12)) == Decimal("4080.38")


class TestRaiseOrdering:
    """Same-date raises apply flat-before-percentage, deterministically (M-01 / deep-hunt #12)."""

    def test_same_date_flat_applies_before_percentage(self):
        """A flat + percentage raise on one date apply flat-first, whatever the input order.

        Raise application is non-commutative, so the documented
        flat-before-percentage contract must hold regardless of the
        order the DB returns the rows in.  Per paycheck on the pay list's
        walk (plan step salary:X-av-3a), $100,000.00 a year being $3,846.15
        and $5,000 a year 5000 / 26 = 192.3077 a paycheck:
          flat-first:        (3846.15 + 192.3077) -> 4038.46; x 1.03 = 4159.6138 -> 4159.61
          percentage-first:   3846.15 x 1.03 -> 3961.53; + 192.3077 = 4153.8377 -> 4153.84
        The engine must always produce the flat-first value.
        """
        flat = FakeRaise(flat_amount="5000", effective_month=1, effective_year=2026)
        pct = FakeRaise(percentage="0.03", effective_month=1, effective_year=2026)
        as_of = date(2026, 6, 5)
        expected = Decimal("4159.61")  # (3846.15 + 5000 / 26) -> 4038.46; x 1.03

        # Both input orders must yield the flat-first result.  The
        # [pct, flat] order is the revert-proof case: without the method
        # tie-break the stable sort keeps percentage first -> 4153.84.
        for order in ([flat, pct], [pct, flat]):
            profile = FakeProfile(
                pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
                raises=order,
            )
            assert _walked_pay(profile, as_of) == expected


class TestRaiseTermination:
    """A raise stops accruing where its own belief stops.

    ``terminal_year`` is the last year a raise is believed to happen.  The
    pay list's walk (:func:`_walked_pay`; ``apply_raises`` until plan step
    salary:X-av-3a) honours it by not accruing applications past that year,
    and by never applying a one-time raise dated beyond it.  These cases
    grade the clamp itself; the mapping that DECIDES each raise's terminal
    year is graded in ``test_pension_calculator``.  Re-stated per paycheck at
    X-av-3a: $100,000.00 a year is $3,846.15 a paycheck, priced from
    2025-12-19, before the first raise lands on 2026-01-01.
    """

    @staticmethod
    def _pay_on(raise_row, payday):
        """What $3,846.15 a paycheck from 2025-12-19 pays on *payday* under *raise_row*."""
        profile = FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=[raise_row],
        )
        return _walked_pay(profile, payday)

    @staticmethod
    def _raise(percentage, *, month=1, year=2026, recurring=True,
               terminal_year=None):
        """A percentage :class:`SalaryRaise`, defaulting to recurring.

        An UNATTACHED row rather than a hand-rolled double: plan step
        salary:S3-c deleted ``TerminatedRaise``, the fabricated value the
        pension projector used to build, so a real row is the only shape
        the walk is ever handed now and the only one worth grading it
        against.  Every attribute the walk reads is set explicitly, because
        a client-side ``default`` fires at INSERT and this row never reaches
        a session -- ``raise_type_id`` included since plan step
        salary:X-av-3a, because the walk reads each row through
        ``RaiseTerms.of``, which reads ``raise_type_name`` off it.
        """
        return SalaryRaise(
            effective_year=year, effective_month=month,
            is_recurring=recurring, percentage=Decimal(percentage),
            flat_amount=None, terminal_year=terminal_year,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        )

    def test_the_orm_rows_end_year_defaults_to_nothing_so_paychecks_are_unmoved(self):
        """A new ``SalaryRaise`` starts at "believed indefinitely", so nothing clamps.

        **This replaces the absence tripwire plan step salary:S3-a left
        here, which fired as designed when plan step salary:S3-b added the
        column** (ruling **R-SAL11**, developer 2026-09-05: the horizon a
        recurring raise decays over is a fact on the raise).  That test
        asserted ``not hasattr(SalaryRaise, "terminal_year")`` and said in
        its own failure message that the premise would need re-deriving if
        the column arrived.  It has, so this is the re-derivation rather
        than a test edited to pass.

        **The premise moved from ABSENCE to a DEFAULT of nothing.**  The
        paycheck engine passes ``profile.raises`` -- real ORM rows -- and
        ``_applications`` reads ``raise_obj.terminal_year`` on every one of
        them.  What keeps a paycheck unmoved is that the field answers
        ``None`` for a raise nobody gave an end year.  A ``default`` on the
        column would end that silently and in the money-moving direction,
        which is what these assertions guard.

        *The read was ``getattr(raise_obj, "terminal_year", None)`` until
        plan step salary:S3-c, whose deletion of the pension projector's
        fabricated values left the row as the only shape passed.  A missing
        attribute now raises instead of defaulting, which is a tightening
        this class does not grade -- ``test_s3b_raise_terminal_year.py``
        does.*

        **It is NOT strictly stronger than the check it replaces, and an
        adversarial review of this step corrected a draft that said it
        was** -- along with the reason that draft gave, which was simply
        false: a defaulted column HAS the attribute, so the old
        ``not hasattr`` would have caught one.  What the old check bought
        was foreclosing the engine reading anything at all, and the ruling
        spent that deliberately.  What is left is narrower and is stated
        rather than claimed: given the column must now exist, this fails on
        a default the old check could not have distinguished from the
        column's mere existence.

        **The instance probe is the surface that governs**, and it is here
        because the deleted test argued for it: it used ``hasattr`` rather
        than ``__table__.columns`` precisely because a ``property`` or
        ``hybrid_property`` would satisfy the engine's attribute read while
        the column set would not.  ``SalaryRaise().terminal_year is None``
        is that argument kept.

        **A ``server_default`` is asserted against the DATABASE, not this
        model**, in ``tests/test_models/test_s3b_raise_terminal_year.py``
        -- a migration could add one without touching this class, and
        SQLAlchemy omits a valueless column from the INSERT, so the DB
        default would fire with a model-level assertion still green.  That
        is the F-068 / F-134 model-migration drift class
        ``test_c25_column_invariants`` exists for.  The arithmetic IDENTITY
        is measured there too, over a committed row.
        """
        column = SalaryRaise.__table__.columns["terminal_year"]
        assert column.nullable is True
        assert column.default is None, (
            "salary_raises.terminal_year gained a client-side default; "
            "every new raise now terminates and the paycheck engine "
            "clamps rows nobody set an end year on"
        )
        assert SalaryRaise().terminal_year is None, (
            "a fresh SalaryRaise answers something other than None for "
            "terminal_year, so the paycheck engine now clamps a "
            "raise nobody gave an end year -- check for a property, a "
            "hybrid_property or an __init__ default shadowing the column"
        )

    def test_a_raise_with_no_terminal_year_compounds_indefinitely(self):
        """3% recurring from 2026, asked at 2036, is 11 applications.

        Each rounded to the cent (R-SAL60): 3846.15 -> 3961.53 -> 4080.38 ->
        4202.79 -> 4328.87 -> 4458.74 -> 4592.50 -> 4730.28 (4730.275, half
        up) -> 4872.19 -> 5018.36 -> 5168.91 -> 5323.98 on 2036-12-05 -- the
        yearly walk's 138,423.39 / 26 = 5323.98 too.  This is the shape
        every in-window paycheck uses.
        """
        result = self._pay_on(self._raise("0.03"), date(2036, 12, 5))
        assert result == Decimal("5323.98")

    def test_a_terminated_recurring_raise_stops_accruing(self):
        """Terminating at 2030 caps the same raise at five applications.

        2026..2030 is 5, so 3846.15 -> 3961.53 -> 4080.38 -> 4202.79 ->
        4328.87 -> 4458.74 (the yearly walk's 115,927.41 / 26 = 4458.75 less
        R-SAL60's cent) -- and it stays there however far past 2030 the
        caller asks, because the raise stopped rather than slowed.
        """
        terminated = self._raise("0.03", terminal_year=2030)
        assert self._pay_on(terminated, date(2036, 12, 5)) == Decimal("4458.74")
        assert self._pay_on(terminated, date(2099, 12, 11)) == Decimal("4458.74")

    def test_a_one_time_raise_past_its_terminal_year_never_applies(self):
        """A 2035 one-time raise believed only through 2030 is dropped."""
        beyond = self._raise("0.10", year=2035, recurring=False,
                             terminal_year=2030)
        result = self._pay_on(beyond, date(2036, 12, 5))
        assert result == Decimal("3846.15")

    def test_a_one_time_raise_within_its_terminal_year_still_applies(self):
        """Its 2028 sibling is kept: 3846.15 * 1.10 = 4230.765 -> 4230.77.

        Without this, the case above would pass on a rule that dropped
        every one-time raise.
        """
        within = self._raise("0.10", year=2028, recurring=False,
                             terminal_year=2030)
        result = self._pay_on(within, date(2036, 12, 5))
        assert result == Decimal("4230.77")

    def test_a_raise_applies_through_the_END_of_its_terminal_year(self):
        """A July raise terminating in 2026 still gets its 2026 application.

        Termination is a whole-year fact, so a raise effective later in
        that year is still counted in it: ``3846.15 * 1.03 = 3961.5345 ->
        3961.53``.
        """
        july = self._raise("0.03", month=7, terminal_year=2026)
        result = self._pay_on(july, date(2036, 12, 5))
        assert result == Decimal("3961.53")

    def test_inside_the_terminal_year_the_callers_own_month_still_governs(self):
        """March 2026 has not reached a July raise, terminal year or not.

        The clamp is a ceiling on the asked date, never a floor: it must
        not back-date a raise into months before it exists.  Same raise as
        the case above, asked earlier, answers the untouched base.
        """
        july = self._raise("0.03", month=7, terminal_year=2026)
        result = self._pay_on(july, date(2026, 3, 13))
        assert result == Decimal("3846.15")


class TestChronologicalRaiseOrder:
    """Applications land in DATE order, not grouped by raise.

    Until this rule, ``apply_raises`` sorted the RAISES and then applied
    each one's whole run of yearly applications before starting the next.
    A percentage raise therefore multiplied flat dollars that had not
    arrived yet.  These cases grade the interleaving; the counts are
    unchanged, so an owner holding only percentage raises is unaffected,
    which the last case pins.

    Re-stated on the pay list's walk at plan step salary:X-av-3a
    (:func:`_walked_pay`, which reads
    :func:`~app.services.salary_raises.applications_between` for the same
    order): $100,000.00 a year is $3,846.15 a paycheck from 2025-12-19, a
    flat raise's yearly dollars spread over 26 paychecks (R-SAL65), each
    step rounded to the cent (R-SAL60).
    """

    @staticmethod
    def _profile(raises):
        """$3,846.15 a paycheck from 2025-12-19 under *raises*, in the order given."""
        return FakeProfile(
            pay=Decimal("3846.15"), pay_from=_WALK_OPENS,  # $100,000.00 a year / 26
            raises=raises,
        )

    def test_a_percentage_does_not_compound_flat_dollars_that_arrive_later(self):
        """A recurring flat COLA and a recurring percentage merit interleave yearly.

        $3,846.15 a paycheck; flat $1,500 a year (1500 / 26 = 57.6923 a
        paycheck) and 4%, both recurring from 2026-01.  Each year the flat
        lands, then the percentage multiplies what is there -- so the
        percentage never grows a dollar that arrives after it:
          2026: (3846.15 + 57.6923) -> 3903.84; x 1.04 = 4059.9936 -> 4059.99
          2027: (4059.99 + 57.6923) -> 4117.68; x 1.04 = 4282.3872 -> 4282.39
          2028: (4282.39 + 57.6923) -> 4340.08; x 1.04 = 4513.6832 -> 4513.68

        The old grouped order added all three flats to the base first and
        compounded once: 3846.15 -> 3903.84 -> 3961.53 -> 4019.22, then
        x 1.04 three times = 4521.08 at 2028, which credited three years of
        growth to dollars that had not arrived.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        pct = FakeRaise(percentage="0.04", effective_month=1,
                        effective_year=2026, is_recurring=True)
        profile = self._profile([flat, pct])
        assert _walked_pay(profile, date(2026, 12, 4)) == Decimal("4059.99")
        assert _walked_pay(profile, date(2027, 12, 3)) == Decimal("4282.39")
        assert _walked_pay(profile, date(2028, 12, 1)) == Decimal("4513.68")

    def test_input_order_still_does_not_change_the_answer(self):
        """Reversing the input list changes nothing (M-01 determinism preserved).

        The ordering key is the application's date and method, never the
        row order, so the DB may return these two in either order.  Same
        2028 value as the case above.
        """
        flat = FakeRaise(flat_amount="1500", effective_month=1,
                         effective_year=2026, is_recurring=True)
        pct = FakeRaise(percentage="0.04", effective_month=1,
                        effective_year=2026, is_recurring=True)
        profile = self._profile([pct, flat])
        assert _walked_pay(profile, date(2028, 12, 1)) == Decimal("4513.68")

    def test_a_one_time_raise_lands_on_its_own_date_not_last(self):
        """A mid-series one-time raise compounds only what preceded it.

        $3,846.15 a paycheck; a recurring flat $1,000 a year from 2026-01
        (1000 / 26 = 38.4615 a paycheck) and a ONE-TIME 10% on 2027-06.  In
        date order:
          2026-01 + 38.4615 -> 3884.61
          2027-01 + 38.4615 -> 3923.07
          2027-06 x 1.10    -> 4315.377 -> 4315.38
          2028-01 + 38.4615 -> 4353.84

        The old order applied the flat's three additions first and then
        the 10%, reaching 3961.53 x 1.10 = 4357.68 -- the extra $3.84 being
        10% of the 2028 addition, credited in 2027.
        """
        flat = FakeRaise(flat_amount="1000", effective_month=1,
                         effective_year=2026, is_recurring=True)
        once = FakeRaise(percentage="0.10", effective_month=6,
                         effective_year=2027, is_recurring=False)
        result = _walked_pay(self._profile([flat, once]), date(2028, 12, 1))
        assert result == Decimal("4353.84")

    def test_percentage_only_raises_are_completely_unaffected(self):
        """Every raise a percentage: the order leaves the COUNTS alone, and can move a cent.

        Multiplication commutes, so regrouping the applications changed
        nothing on the yearly walk -- which is why this rule moved no money
        for an owner whose raises are all percentages.  3% from 2026-01 and
        2% from 2026-07, at 2027-12: both have two applications.  **Since
        plan step salary:X-av-3a each step rounds to the cent (ruling
        R-SAL60), so the order can move a cent**: in date order 3846.15 x
        1.03 -> 3961.53; x 1.02 -> 4040.76; x 1.03 -> 4161.98; x 1.02 =
        4245.2196 -> 4245.22, where the grouped order (3%, 3%, 2%, 2%)
        rounds to 4245.23.  The COUNTS are what this rule leaves unaffected;
        the pin is the date order's figure.
        """
        merit = FakeRaise(percentage="0.03", effective_month=1,
                          effective_year=2026, is_recurring=True)
        cola = FakeRaise(percentage="0.02", effective_month=7,
                         effective_year=2026, is_recurring=True)
        result = _walked_pay(self._profile([merit, cola]), date(2027, 12, 3))
        assert result == Decimal("4245.22")


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


class TestThirdPaycheckDetection:
    """Tests for ``_month_ordinal`` and the ``_is_third_paycheck`` rule on it.

    They were two functions each scanning a caller-supplied period list until
    plan step **balance:X-bh-1** -- ``_is_third_paycheck(period, all_periods)``
    and ``_is_first_paycheck_of_month(period, all_periods)``.  Both asked one
    question of one payday, so both are now the ORDINAL, counted off the
    owner's calendar, and the cases below grade the number and the two
    predicates that read it.
    """

    def test_month_with_two_paychecks_places_the_second(self):
        """Standard month with 2 paychecks: the second is ordinal 2."""
        p1 = _period(start_date=date(2026, 2, 13), period_id=1)
        p2 = _period(start_date=date(2026, 2, 27), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 2, 13),  # $60,000.00 a year / 26
        ), [p1, p2])

        assert _month_ordinal(basis.calendar, p2.start_date) == 2
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p2.start_date)) is False

    def test_third_paycheck_of_the_month_is_ordinal_three(self):
        """Month with 3 paydays: the third is ordinal 3 and reads as third."""
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        ), [p1, p2, p3])

        assert _month_ordinal(basis.calendar, p3.start_date) == 3
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p3.start_date)) is True

    def test_first_period_of_a_three_paycheck_month_is_ordinal_one(self):
        """The first payday of a 3-paycheck month is not a 3rd paycheck."""
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        ), [p1, p2, p3])

        assert _month_ordinal(basis.calendar, p1.start_date) == 1
        assert _is_third_paycheck(_month_ordinal(basis.calendar, p1.start_date)) is False

    def test_a_neighbouring_month_does_not_count(self):
        """Only the payday's OWN calendar month is counted.

        The two January paydays sit within a cadence of the February one, so a
        rule that counted a window rather than a month would place the
        February payday at 3 and skip a 24-per-year deduction on it.
        """
        jan1 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan2 = _period(start_date=date(2026, 1, 16), period_id=2)
        jan3 = _period(start_date=date(2026, 1, 30), period_id=3)
        feb = _period(start_date=date(2026, 2, 13), period_id=4)
        basis = payroll_basis(
            FakeProfile(pay=Decimal("2307.69")), [jan1, jan2, jan3, feb],  # $60,000.00 a year / 26
        )

        assert _month_ordinal(basis.calendar, feb.start_date) == 1

    def test_a_payday_past_the_horizon_is_placed_by_the_cadence(self):
        """A projected payday is counted, not answered as if it stood alone.

        The calendar holds 2026-01-02 and 2026-01-16 only, so its horizon is
        2026-01-29 and every later payday is projected at the 14-day cadence:
        01-30, 02-13, 02-27, 03-13, 03-27.  Counting only SAVED paydays would
        place BOTH March paydays at 0 -- so a 24-per-year deduction could
        never be skipped past the horizon and a 12-per-year one could never be
        taken.  The count projects forward instead (``pay_calendar:R-PC9``).
        """
        jan1 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan2 = _period(start_date=date(2026, 1, 16), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
        ), [jan1, jan2])
        # Built directly rather than through ``_period``: a period past the
        # horizon carries ``period_id = None``, which is exactly what marks it
        # unmaterialised, and that helper numbers its index off the id.
        first_of_march = DerivedPeriod(
            period_id=None, period_index=6, start_date=date(2026, 3, 13),
            end_date=date(2026, 3, 26), end_is_projected=True,
        )
        second_of_march = DerivedPeriod(
            period_id=None, period_index=7, start_date=date(2026, 3, 27),
            end_date=date(2026, 4, 9), end_is_projected=True,
        )

        assert _month_ordinal(basis.calendar, first_of_march.start_date) == 1
        assert _month_ordinal(basis.calendar, second_of_march.start_date) == 2

    def test_a_payday_below_the_opening_is_counted_for_a_STATED_owner(self):
        """The rhythm runs backwards too, which is plan step balance:X-bh-2.

        The calendar opens 2026-01-16, so the January payday two weeks before
        it -- 2026-01-02 -- is one the owner was really paid on and the app
        holds no row for.  It is counted once they SAY their paychecks began
        earlier: the opening payday then reads 2 and the one after it reads 3,
        so a 24-per-year deduction is SKIPPED on 2026-01-30 where before this
        step it was taken, and a 12-per-year one stops being taken on
        2026-01-16.  That is ledger row **N-390** on the month side.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(
            FakeProfile(
                pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            ), [opening, later],
            history_opens_on=date(2025, 1, 1),
        )

        assert _month_ordinal(basis.calendar, opening.start_date) == 2
        assert _month_ordinal(basis.calendar, later.start_date) == 3

    def test_an_UNSTATED_owner_counts_only_the_record(self):
        """THE CONTROL, and the reading every owner starts at.

        The same two paydays for somebody nobody has asked: nothing runs below
        the record, so the ordinals are 1 and 2 -- exactly what this calendar
        answered for every owner before plan step balance:X-bh-2.  ``NULL`` is
        an absence, not a claim (ruling **balance:R-IA** as amended
        2026-08-31), and the safe direction for an absence is to count less.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
        ),
                              [opening, later])

        assert _month_ordinal(basis.calendar, opening.start_date) == 1
        assert _month_ordinal(basis.calendar, later.start_date) == 2

    def test_a_floor_ON_the_opening_payday_also_counts_only_the_record(self):
        """The owner whose first paycheck IS the first one recorded.

        Distinct from the case above in what it MEANS -- a statement rather
        than an absence -- and identical in what it answers, which is why both
        stand: ``pay_calendar:R-PC14`` calls this owner ordinary, and the
        engine must not tell them apart.
        """
        opening = _period(start_date=date(2026, 1, 16), period_id=1)
        later = _period(start_date=date(2026, 1, 30), period_id=2)
        basis = payroll_basis(
            FakeProfile(
                pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            ), [opening, later],
            history_opens_on=opening.start_date,
        )

        assert _month_ordinal(basis.calendar, opening.start_date) == 1
        assert _month_ordinal(basis.calendar, later.start_date) == 2


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


class TestFirstPaycheckOfMonth:
    """The 12-per-year deduction cadence, which is ordinal 1 and only that."""

    def test_first_period_in_month_is_ordinal_one(self):
        p1 = _period(start_date=date(2026, 3, 6), period_id=1)
        p2 = _period(start_date=date(2026, 3, 20), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 3, 6),  # $60,000.00 a year / 26
        ), [p1, p2])

        assert _month_ordinal(basis.calendar, p1.start_date) == 1

    def test_second_period_in_month_is_not_ordinal_one(self):
        p1 = _period(start_date=date(2026, 3, 6), period_id=1)
        p2 = _period(start_date=date(2026, 3, 20), period_id=2)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 3, 6),  # $60,000.00 a year / 26
        ), [p1, p2])

        assert _month_ordinal(basis.calendar, p2.start_date) == 2


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


# ── Pre-Tax Deduction Tax Impact Tests ─────────────────────────


class TestPreTaxDeductionTaxImpact:
    """Verify that pre-tax deductions reduce income taxes but NOT FICA.

    This is the core tax calculation invariant for U.S. payroll:
      - Federal income tax: computed on (gross - pre_tax - std_deduction)
      - State income tax: computed on (gross - pre_tax - state_std_deduction)
      - Social Security: computed on gross (NOT reduced by pre-tax deductions)
      - Medicare: computed on gross (NOT reduced by pre-tax deductions)

    Each test computes expected values by hand from first principles and
    compares against the calculator output. The hand calculations follow
    the IRS Pub 15-T Percentage Method pipeline exactly:
      1. Annualize gross: gross_biweekly * 26
      2. Subtract annualized pre-tax deductions
      3. Subtract standard deduction
      4. Apply marginal brackets
      5. De-annualize: annual_tax / 26

    All tests use the simple_bracket_set fixture (0-50k@10%, 50k+@22%,
    std_deduction=$15,000) and nc_state_config (NC 4.5% flat, no state
    standard deduction) unless otherwise noted.

    Baseline reference (no deductions, $60k salary, established by
    TestCalculatePaycheckPipeline.test_basic_paycheck_no_deductions):
      gross=2307.69, federal=173.08, state=103.85, SS=143.08,
      medicare=33.46, net=1854.22
    """

    def test_flat_pretax_deduction_reduces_federal_and_state(
        self, simple_tax_configs
    ):
        """$200/paycheck pre-tax 401(k) lowers federal and state taxes.

        This test catches the section 3.1 bug if it were to exist: taxes
        computed on gross instead of taxable income.

        Hand calculation:
          gross = 60000/26 = $2,307.69
          pre_tax = $200.00
          taxable_biweekly = 2307.69 - 200 = $2,107.69

          Federal (Pub 15-T):
            annual_income = 2307.69 * 26 = $59,999.94
            annual_pre_tax = 200 * 26 = $5,200.00
            adjusted = 59,999.94 - 5,200 = $54,799.94
            taxable = 54,799.94 - 15,000 = $39,799.94
            tax = 39,799.94 * 0.10 = $3,979.99  (all in 10% bracket)
            per_period = 3,979.99 / 26 = $153.08

          State:
            annual = 2,107.69 * 26 = $54,799.94
            tax = 54,799.94 * 0.045 = $2,466.00
            per_period = 2,466.00 / 26 = $94.85

        Without deduction: federal=173.08, state=103.85 (baseline).
        Reduction: federal drops by $20.00 (= $200 * 10% marginal rate),
                   state drops by $9.00 (= $200 * 4.5% flat rate).
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        with_ded = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Baseline comparison (from established test):
        baseline_federal = Decimal("173.08")
        baseline_state = Decimal("103.85")

        # Federal tax must be LOWER with pre-tax deduction.
        assert with_ded.taxes.federal == Decimal("153.08"), (
            f"federal_tax: expected 153.08, got {with_ded.taxes.federal}"
        )
        assert with_ded.taxes.federal < baseline_federal, (
            "Pre-tax deduction must reduce federal tax"
        )

        # State tax must be LOWER with pre-tax deduction.
        assert with_ded.taxes.state == Decimal("94.85"), (
            f"state_tax: expected 94.85, got {with_ded.taxes.state}"
        )
        assert with_ded.taxes.state < baseline_state, (
            "Pre-tax deduction must reduce state tax"
        )

        # Taxable income field must equal gross minus pre-tax.
        assert with_ded.earnings.taxable_income == Decimal("2107.69"), (
            f"taxable_income: expected 2107.69, "
            f"got {with_ded.earnings.taxable_income}"
        )

        # Verify the magnitude of tax reduction matches marginal rates.
        # $200 * 10% marginal bracket = $20.00/period federal reduction.
        assert baseline_federal - with_ded.taxes.federal == Decimal("20.00"), (
            f"Federal reduction should be $20.00 "
            f"(= $200 * 10% bracket rate), "
            f"got {baseline_federal - with_ded.taxes.federal}"
        )
        # $200 * 4.5% NC flat rate = $9.00/period state reduction.
        assert baseline_state - with_ded.taxes.state == Decimal("9.00"), (
            f"State reduction should be $9.00 "
            f"(= $200 * 4.5% flat rate), "
            f"got {baseline_state - with_ded.taxes.state}"
        )

    def test_pretax_deduction_does_not_reduce_fica(
        self, simple_tax_configs
    ):
        """FICA (SS + Medicare) must be computed on gross, NOT taxable income.

        Pre-tax 401(k) deductions reduce federal/state income tax bases but
        do NOT reduce FICA wages (IRC Section 3121 -- 401(k) contributions
        are subject to FICA). If this test fails, FICA is being incorrectly
        computed on taxable income instead of gross.

        Baseline FICA (no deductions):
          SS = 2307.69 * 0.062 = $143.08
          Medicare = 2307.69 * 0.0145 = $33.46

        With $200 pre-tax deduction: FICA must be IDENTICAL.
        """
        no_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            simple_tax_configs
        )

        # SS must be identical -- computed on gross, not taxable.
        assert with_ded.taxes.social_security == no_ded.taxes.social_security, (
            f"SS changed with pre-tax deduction: "
            f"{no_ded.taxes.social_security} -> {with_ded.taxes.social_security}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {with_ded.taxes.social_security}"
        )

        # Medicare must be identical -- computed on gross, not taxable.
        assert with_ded.taxes.medicare == no_ded.taxes.medicare, (
            f"Medicare changed with pre-tax deduction: "
            f"{no_ded.taxes.medicare} -> {with_ded.taxes.medicare}. "
            f"FICA must be computed on gross, not taxable income."
        )
        assert with_ded.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {with_ded.taxes.medicare}"
        )

        # Gross must also be identical (deductions don't change gross).
        assert with_ded.earnings.gross_biweekly == no_ded.earnings.gross_biweekly

    def test_percentage_pretax_deduction_reduces_taxes(
        self, simple_tax_configs
    ):
        """6% percentage-based pre-tax 401(k) reduces income taxes correctly.

        Percentage deductions use calc_method='percentage' and are computed
        as a percentage of gross_biweekly. The resulting amount must reduce
        the tax base for income taxes but not FICA.

        Hand calculation:
          gross = 60000/26 = $2,307.69
          deduction = 2307.69 * 0.06 = $138.46
          taxable_biweekly = 2307.69 - 138.46 = $2,169.23

          Federal:
            annual_pre_tax = 138.46 * 26 = $3,599.96
            adjusted = 59,999.94 - 3,599.96 = $56,399.98
            taxable = 56,399.98 - 15,000 = $41,399.98
            tax = 41,399.98 * 0.10 = $4,140.00
            per_period = 4,140.00 / 26 = $159.23

          State:
            annual = 2,169.23 * 26 = $56,399.98
            tax = 56,399.98 * 0.045 = $2,538.00
            per_period = 2,538.00 / 26 = $97.62

          FICA: unchanged at SS=$143.08, Medicare=$33.46
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="0.06",
                    calc_method="percentage",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Deduction amount computed from gross.
        assert result.deductions.pre_tax[0].amount == Decimal("138.46"), (
            f"6% of 2307.69: expected 138.46, "
            f"got {result.deductions.pre_tax[0].amount}"
        )

        assert result.earnings.taxable_income == Decimal("2169.23"), (
            f"taxable_income: expected 2169.23, "
            f"got {result.earnings.taxable_income}"
        )

        assert result.taxes.federal == Decimal("159.23"), (
            f"federal_tax: expected 159.23, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("97.62"), (
            f"state_tax: expected 97.62, got {result.taxes.state}"
        )

        # FICA on gross -- unaffected by percentage deduction.
        assert result.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.taxes.medicare}"
        )

        # Net pay end-to-end.
        # 2307.69 - 138.46 - 159.23 - 97.62 - 143.08 - 33.46 = 1735.84
        assert result.earnings.net_pay == Decimal("1735.84"), (
            f"net_pay: expected 1735.84, got {result.earnings.net_pay}"
        )

    def test_third_paycheck_skipped_deduction_increases_taxes(
        self, simple_tax_configs
    ):
        """On a 3rd paycheck, 24/yr deductions are skipped, raising taxes.

        When a 24-per-year deduction (e.g., health insurance) is skipped on
        a 3rd paycheck, the pre-tax deduction total is lower, which means
        taxable income is higher, which means income taxes are higher. This
        is correct real-world payroll behavior.

        Setup: 3 periods in January (p1=Jan 2, p2=Jan 16, p3=Jan 30).
        p3 is the 3rd paycheck. $100/paycheck health insurance at 24/yr.

        On p2 (normal paycheck, deduction applies):
          pre_tax = $100
          annual_pre_tax = 100 * 26 = $2,600
          federal taxable = (59,999.94 - 2,600 - 15,000) = $42,399.94
          federal = 42,399.94 * 0.10 = $4,239.99 / 26 = $163.08
          state = (2207.69 * 26) * 0.045 = $2,583.00 / 26 = $99.35

        On p3 (3rd paycheck, deduction skipped):
          pre_tax = $0
          federal = $173.08 (same as no-deduction baseline)
          state = $103.85

        Tax increase on 3rd paycheck:
          federal: 173.08 - 163.08 = $10.00 (= $100 * 10% bracket)
          state: 103.85 - 99.35 = $4.50 (= $100 * 4.5% flat)
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="Health Insurance", amount="100",
                    recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        # 3 periods in January to trigger 3rd paycheck detection.
        p1 = _period(start_date=date(2026, 1, 2), period_id=1)
        p2 = _period(start_date=date(2026, 1, 16), period_id=2)
        p3 = _period(start_date=date(2026, 1, 30), period_id=3)
        all_periods = [p1, p2, p3]

        normal = calculate_paycheck(
            payroll_basis(profile, all_periods), p2,
            simple_tax_configs
        )
        third = calculate_paycheck(
            payroll_basis(profile, all_periods), p3,
            simple_tax_configs
        )

        # On normal paycheck, deduction applies.
        assert len(normal.deductions.pre_tax) == 1, (
            "Normal paycheck should have 1 pre-tax deduction"
        )
        assert normal.deductions.pre_tax[0].amount == Decimal("100.00")
        assert normal.taxes.federal == Decimal("163.08"), (
            f"Normal federal: expected 163.08, got {normal.taxes.federal}"
        )
        assert normal.taxes.state == Decimal("99.35"), (
            f"Normal state: expected 99.35, got {normal.taxes.state}"
        )

        # On 3rd paycheck, deduction is skipped.
        assert len(third.deductions.pre_tax) == 0, (
            "3rd paycheck should have 0 pre-tax deductions "
            "(24/yr deduction skipped)"
        )
        assert third.period.is_third_paycheck is True

        # 3rd paycheck taxes are HIGHER because deduction was skipped.
        assert third.taxes.federal == Decimal("173.08"), (
            f"3rd paycheck federal: expected 173.08, "
            f"got {third.taxes.federal}"
        )
        assert third.taxes.state == Decimal("103.85"), (
            f"3rd paycheck state: expected 103.85, "
            f"got {third.taxes.state}"
        )
        assert third.taxes.federal > normal.taxes.federal, (
            "3rd paycheck federal should be higher (deduction skipped)"
        )
        assert third.taxes.state > normal.taxes.state, (
            "3rd paycheck state should be higher (deduction skipped)"
        )

        # Tax increase exactly matches deduction * marginal rate.
        assert third.taxes.federal - normal.taxes.federal == Decimal("10.00"), (
            f"Federal increase: expected $10 "
            f"(= $100 * 10% bracket), "
            f"got {third.taxes.federal - normal.taxes.federal}"
        )
        assert third.taxes.state - normal.taxes.state == Decimal("4.50"), (
            f"State increase: expected $4.50 "
            f"(= $100 * 4.5% flat), "
            f"got {third.taxes.state - normal.taxes.state}"
        )

        # FICA identical on both (gross is the same).
        assert third.taxes.social_security == normal.taxes.social_security
        assert third.taxes.medicare == normal.taxes.medicare

    def test_multiple_pretax_deductions_stack(
        self, simple_tax_configs
    ):
        """Two pre-tax deductions ($200 + $100) stack to reduce taxes by $300.

        Hand calculation:
          gross = $2,307.69
          total_pre_tax = $300.00
          taxable_biweekly = 2307.69 - 300 = $2,007.69
          annual_pre_tax = 300 * 26 = $7,800

          Federal:
            adjusted = 59,999.94 - 7,800 = $52,199.94
            taxable = 52,199.94 - 15,000 = $37,199.94
            tax = 37,199.94 * 0.10 = $3,719.99 / 26 = $143.08

          State:
            annual = 2,007.69 * 26 = $52,199.94
            tax = 52,199.94 * 0.045 = $2,349.00 / 26 = $90.35

          Federal reduction from baseline: 173.08 - 143.08 = $30.00
            = $300 * 10% bracket rate
          State reduction from baseline: 103.85 - 90.35 = $13.50
            = $300 * 4.5% flat rate

          Net = 2307.69 - 300 - 143.08 - 90.35 - 143.08 - 33.46 = $1,597.72
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
                FakeDeduction(
                    name="Health", amount="100",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        assert result.deductions.total_pre_tax == Decimal("300.00"), (
            f"total_pre_tax: expected 300, got {result.deductions.total_pre_tax}"
        )
        assert result.earnings.taxable_income == Decimal("2007.69"), (
            f"taxable_income: expected 2007.69, "
            f"got {result.earnings.taxable_income}"
        )
        assert result.taxes.federal == Decimal("143.08"), (
            f"federal_tax: expected 143.08, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("90.35"), (
            f"state_tax: expected 90.35, got {result.taxes.state}"
        )

        # Reduction from baseline matches total deduction * marginal rate.
        assert Decimal("173.08") - result.taxes.federal == Decimal("30.00"), (
            "Federal reduction should be $30 = $300 * 10%"
        )
        assert Decimal("103.85") - result.taxes.state == Decimal("13.50"), (
            "State reduction should be $13.50 = $300 * 4.5%"
        )

        # FICA still on gross.
        assert result.taxes.social_security == Decimal("143.08")
        assert result.taxes.medicare == Decimal("33.46")

        # End-to-end net pay.
        assert result.earnings.net_pay == Decimal("1597.72"), (
            f"net_pay: expected 1597.72, got {result.earnings.net_pay}"
        )

    def test_post_tax_deduction_does_not_affect_any_tax(
        self, simple_tax_configs
    ):
        """Post-tax deductions (e.g., Roth IRA) must NOT change any tax amount.

        A post-tax deduction reduces net pay but has zero impact on federal,
        state, SS, or Medicare. If this test fails, post-tax deductions are
        leaking into the tax base calculation.

        All tax values must match the no-deduction baseline exactly:
          federal=173.08, state=103.85, SS=143.08, Medicare=33.46

        Net = 2307.69 - 173.08 - 103.85 - 143.08 - 33.46 - 200.00
            = $1,654.22
        """
        no_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        post_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="Roth IRA", amount="200",
                    paycheck_line_kind="post_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        post_ded = calculate_paycheck(
            payroll_basis(post_ded_profile, [period]), period,
            simple_tax_configs
        )

        # Every tax field must be identical.
        assert post_ded.taxes.federal == no_ded.taxes.federal == Decimal("173.08"), (
            f"Post-tax deduction changed federal: "
            f"{no_ded.taxes.federal} -> {post_ded.taxes.federal}"
        )
        assert post_ded.taxes.state == no_ded.taxes.state == Decimal("103.85"), (
            f"Post-tax deduction changed state: "
            f"{no_ded.taxes.state} -> {post_ded.taxes.state}"
        )
        assert post_ded.taxes.social_security == no_ded.taxes.social_security == Decimal("143.08"), (
            f"Post-tax deduction changed SS: "
            f"{no_ded.taxes.social_security} -> {post_ded.taxes.social_security}"
        )
        assert post_ded.taxes.medicare == no_ded.taxes.medicare == Decimal("33.46"), (
            f"Post-tax deduction changed Medicare: "
            f"{no_ded.taxes.medicare} -> {post_ded.taxes.medicare}"
        )

        # Taxable income must also be unchanged.
        assert post_ded.earnings.taxable_income == no_ded.earnings.taxable_income, (
            "Post-tax deduction should not affect taxable_income"
        )

        # Only net pay changes (reduced by $200 post-tax).
        assert post_ded.earnings.net_pay == Decimal("1654.22"), (
            f"net_pay: expected 1654.22, got {post_ded.earnings.net_pay}"
        )
        assert post_ded.earnings.net_pay == no_ded.earnings.net_pay - Decimal("200.00"), (
            "Net pay should decrease by exactly the post-tax amount"
        )

    def test_mixed_pre_and_post_tax_deductions(
        self, simple_tax_configs
    ):
        """Pre-tax and post-tax deductions interact correctly.

        $200 pre-tax 401(k) + $150 post-tax Roth IRA. Only the pre-tax
        deduction reduces the tax base. Post-tax is subtracted after taxes.

        Tax values should match the "$200 pre-tax only" scenario:
          federal=$153.08, state=$94.85, SS=$143.08, Medicare=$33.46

        Net = 2307.69 - 200.00 - 153.08 - 94.85 - 143.08 - 33.46 - 150.00
            = $1,533.22
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
                FakeDeduction(
                    name="Roth IRA", amount="150",
                    paycheck_line_kind="post_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        result = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Taxes match the $200 pre-tax scenario (post-tax has no effect).
        assert result.taxes.federal == Decimal("153.08"), (
            f"federal: expected 153.08, got {result.taxes.federal}"
        )
        assert result.taxes.state == Decimal("94.85"), (
            f"state: expected 94.85, got {result.taxes.state}"
        )
        assert result.taxes.social_security == Decimal("143.08"), (
            f"SS: expected 143.08, got {result.taxes.social_security}"
        )
        assert result.taxes.medicare == Decimal("33.46"), (
            f"Medicare: expected 33.46, got {result.taxes.medicare}"
        )

        # Both deduction types present in their respective lists.
        assert result.deductions.total_pre_tax == Decimal("200.00")
        assert result.deductions.total_post_tax == Decimal("150.00")

        # Net pay accounts for both deduction types.
        assert result.earnings.net_pay == Decimal("1533.22"), (
            f"net_pay: expected 1533.22, got {result.earnings.net_pay}"
        )

    def test_state_tax_with_standard_deduction_and_pretax(
        self, simple_bracket_set, standard_fica
    ):
        """State standard deduction and pre-tax deductions both reduce state tax.

        The state tax pipeline is: (gross - pre_tax) * 26 - state_std_ded,
        then multiply by flat rate. Both reductions must apply.

        Setup: NC 4.5% flat rate WITH $12,750 standard deduction.

        Without pre-tax deductions:
          annual = 2307.69 * 26 = $59,999.94
          state_taxable = 59,999.94 - 12,750 = $47,249.94
          tax = 47,249.94 * 0.045 = $2,126.25 / 26 = $81.78

        With $200 pre-tax deduction:
          annual = 2107.69 * 26 = $54,799.94
          state_taxable = 54,799.94 - 12,750 = $42,049.94
          tax = 42,049.94 * 0.045 = $1,892.25 / 26 = $72.78

        Reduction: 81.78 - 72.78 = $9.00 (= $200 * 4.5% flat rate)
        """
        # State config with standard deduction (not in default fixture).
        state_with_std_ded = FakeStateTaxConfig(flat_rate="0.045")
        state_with_std_ded.standard_deduction = Decimal("12750")

        configs = {
            "bracket_set": simple_bracket_set,
            "state_config": state_with_std_ded,
            "fica_config": standard_fica,
        }

        no_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            configs
        )

        # Without deduction: state std ded reduces the base.
        assert no_ded.taxes.state == Decimal("81.78"), (
            f"No-deduction state: expected 81.78, "
            f"got {no_ded.taxes.state}"
        )

        # With deduction: both reductions apply.
        assert with_ded.taxes.state == Decimal("72.78"), (
            f"With-deduction state: expected 72.78, "
            f"got {with_ded.taxes.state}"
        )

        # Reduction matches deduction * flat rate.
        assert no_ded.taxes.state - with_ded.taxes.state == Decimal("9.00"), (
            f"State reduction: expected $9.00 "
            f"(= $200 * 4.5%), "
            f"got {no_ded.taxes.state - with_ded.taxes.state}"
        )

    def test_net_pay_end_to_end_with_pretax(
        self, simple_tax_configs
    ):
        """Full pipeline net pay with pre-tax deduction matches hand calc.

        This is the integration check: every component (gross, deductions,
        each tax type, net) is verified in one pass. If any upstream value
        is wrong, the net pay will not match.

        Net = gross - pre_tax - federal - state - SS - medicare - post_tax
            = 2307.69 - 200.00 - 153.08 - 94.85 - 143.08 - 33.46 - 0
            = $1,683.22
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="200",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        r = calculate_paycheck(
            payroll_basis(profile, [period]), period,
            simple_tax_configs
        )

        # Verify every component individually.
        assert r.earnings.gross_biweekly == Decimal("2307.69")
        assert r.deductions.total_pre_tax == Decimal("200.00")
        assert r.earnings.taxable_income == Decimal("2107.69")
        assert r.taxes.federal == Decimal("153.08")
        assert r.taxes.state == Decimal("94.85")
        assert r.taxes.social_security == Decimal("143.08")
        assert r.taxes.medicare == Decimal("33.46")
        assert r.deductions.total_post_tax == Decimal("0")

        # Verify net pay matches the formula.
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
            f"net_pay {r.earnings.net_pay} != formula result {expected_net}"
        )
        assert r.earnings.net_pay == Decimal("1683.22"), (
            f"net_pay: expected 1683.22, got {r.earnings.net_pay}"
        )

    def test_pretax_deduction_in_higher_bracket_larger_reduction(
        self, simple_tax_configs
    ):
        """Pre-tax deduction in a higher bracket produces a larger tax savings.

        At $120k salary, the marginal bracket is 22% (income above $50k in
        the simple_bracket_set). A $500 pre-tax deduction at this income
        saves $500 * 22% = $110/period in federal tax, compared to
        $500 * 10% = $50/period at the $60k income level.

        This test verifies the calculator correctly applies marginal rates,
        not average rates, to the deduction amount.

        Without deduction ($120k):
          gross = 120000/26 = $4,615.38 (120000/26 = 4615.384615... -> 4615.38)
          annual = 4615.38 * 26 = $119,999.88
          taxable = 119,999.88 - 15,000 = $104,999.88
          tax: 50000*0.10 + 54999.88*0.22 = 5000 + 12099.97 = $17,099.97
          per_period = 17,099.97 / 26 = $657.69

        With $500 pre-tax deduction:
          annual_pre_tax = 500 * 26 = $13,000
          adjusted = 119,999.88 - 13,000 = $106,999.88
          taxable = 106,999.88 - 15,000 = $91,999.88
          tax: 50000*0.10 + 41999.88*0.22 = 5000 + 9239.97 = $14,239.97
          per_period = 14,239.97 / 26 = $547.69

        Reduction = 657.69 - 547.69 = $110.00 = $500 * 22%
        """
        no_ded_profile = FakeProfile(
            pay=Decimal("4615.38"), pay_from=date(2026, 1, 16),  # $120,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_ded_profile = FakeProfile(
            pay=Decimal("4615.38"), pay_from=date(2026, 1, 16),  # $120,000.00 a year / 26
            lines=[
                FakeDeduction(
                    name="401k", amount="500",
                    paycheck_line_kind="pre_tax_deduction",
                ),
            ],
            created_at=date(2026, 1, 1),
        )
        period = _period(start_date=date(2026, 1, 16), period_id=1)

        no_ded = calculate_paycheck(
            payroll_basis(no_ded_profile, [period]), period,
            simple_tax_configs
        )
        with_ded = calculate_paycheck(
            payroll_basis(with_ded_profile, [period]), period,
            simple_tax_configs
        )

        assert no_ded.taxes.federal == Decimal("657.69"), (
            f"No-deduction federal: expected 657.69, "
            f"got {no_ded.taxes.federal}"
        )
        assert with_ded.taxes.federal == Decimal("547.69"), (
            f"With-deduction federal: expected 547.69, "
            f"got {with_ded.taxes.federal}"
        )

        # Reduction = $500 * 22% marginal bracket = $110.00
        reduction = no_ded.taxes.federal - with_ded.taxes.federal
        assert reduction == Decimal("110.00"), (
            f"Federal reduction: expected $110.00 "
            f"(= $500 * 22% marginal bracket), got {reduction}"
        )

        # FICA unchanged at higher income.
        assert with_ded.taxes.social_security == no_ded.taxes.social_security
        assert with_ded.taxes.medicare == no_ded.taxes.medicare


class TestTheGrossIsARateAndNotAShareOfAYear:
    """Plan step **balance:X-aw** / ruling **balance:R-HW**: the per-paycheck
    gross is the salary over the owner's paycheck count, and nothing else.

    These replace ``TestBiweeklyResidueReconciliation``, which graded audit
    MED-05 / PA-07's contract -- the annual quantisation residue distributed
    across a calendar year so the year summed to the annual salary exactly.
    That contract is superseded, and the case that mattered most is the one it
    could not have: :meth:`test_the_gross_does_not_move_with_the_period_list`
    is finding **N-239**, and it FAILS on the superseded rule by construction,
    because deciding which paychecks got the residue cent required counting the
    period rows the caller happened to pass.
    """

    @pytest.mark.parametrize(
        "annual_salary,expected_gross",
        [
            # Hand-computed: annual / 26, ROUND_HALF_UP at the cent.
            # $50,000 / 26 = $1923.0769... -> $1923.08
            (Decimal("50000"), Decimal("1923.08")),
            # $75,000 / 26 = $2884.6153... -> $2884.62
            (Decimal("75000"), Decimal("2884.62")),
            # $100,000 / 26 = $3846.1538... -> $3846.15
            (Decimal("100000"), Decimal("3846.15")),
            # $60,000 / 26 = $2307.6923... -> $2307.69
            (Decimal("60000"), Decimal("2307.69")),
            # $78,000 / 26 = $3000.00 exact -- no rounding at all.
            (Decimal("78000"), Decimal("3000.00")),
            # The owner's own salary, whose stub pays a flat $3,526.00:
            # $91,675 / 26 = $3525.9615... -> $3525.96.
            (Decimal("91675"), Decimal("3525.96")),
        ],
    )
    def test_every_paycheck_of_a_salary_segment_pays_the_same_figure(
        self, annual_salary, expected_gross, biweekly_periods,
        simple_tax_configs,
    ):
        """All 26 paychecks carry ONE figure, which is what a real stub does.

        The superseded rule gave the earliest few paychecks of the year an
        extra cent, so a year held two distinct grosses differing by $0.01.
        A real employer pays one number: the owner's own nine measured payroll
        deposits all carry a gross of $3,526.00.
        """
        profile = FakeProfile(
            # The parametrized yearly figure / 26, ROUND_HALF_UP at the cent:
            # what migration 70680a4a7405 converts a stored salary to.
            pay=(annual_salary / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            created_at=date(2026, 1, 1),
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26
        grosses = {r.earnings.gross_biweekly for r in results}
        assert grosses == {expected_gross}, (
            f"expected one gross {expected_gross} across the year, "
            f"got {sorted(grosses)}"
        )

    def test_the_gross_does_not_move_with_the_period_list(
        self, simple_tax_configs,
    ):
        """Finding **N-239**: extending the schedule must not re-price a paycheck.

        Prices ONE fixed paycheck -- 2026-01-02, the owner's first payday of
        the year -- against period lists of five very different sizes, and
        asserts every caller gets the same answer.  A route preview passes a
        single period; a year-scoped caller passes that year; the recurrence
        engine's schedule extend passes only the rows it just created.

        **The SUBJECT has to sit where the superseded rule put its residue, or
        this case grades nothing.**  An earlier draft used the 14th payday and
        PASSED on the pre-X-aw code, because MED-05 / PA-07 gave the extra cent
        to the earliest 6 periods of the year and the 14th was never one of
        them -- the assertion held under both rules and the test measured
        nothing.  Period 1 is inside that window, so the old rule answers
        $2307.70 from a 26- or 27-period list and $2307.69 from a shorter one
        (which fell to its half-up fallback): two figures for one paycheck.

        $60,000 / 26 = $2307.6923... -> $2307.69, whoever asks and however
        much of the schedule they hold.

        The real-data form of the same defect: on the owner's own schedule,
        filling 2028 from its 16 stored rows to its 26 paydays moved six
        already-settled paychecks by a cent each ($3,930.07 -> $3,930.06).
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        # 27 is not a typo: a biweekly calendar year holds 27 paydays about
        # one year in eleven.  A Jan 2 phase is NOT one of them -- Jan 2 plus
        # 26 x 14 days lands 2027-01-01, so 2026 holds 26 -- and the 27th
        # element here therefore falls in the NEXT year.  It still discriminates
        # (the superseded rule counted 26 same-year periods either way, so both
        # the 26- and 27-element lists answered $2307.70), and it is the case
        # that exercises a list running past its own year's end.  The owner's
        # real phase, Jan 1, is a genuine 27-payday 2026.
        list_sizes = (1, 3, 16, 26, 27)
        subject = _period(start_date=date(2026, 1, 2), period_id=1)

        answers = {}
        for size in list_sizes:
            all_periods = [subject] + [
                _period(start_date=date(2026, 1, 2) + timedelta(days=14 * i),
                        period_id=i + 1)
                for i in range(1, size)
            ]
            assert len(all_periods) == size
            answers[size] = calculate_paycheck(
            payroll_basis(profile, all_periods), subject,
            simple_tax_configs,
            ).earnings.gross_biweekly

        assert set(answers.values()) == {Decimal("2307.69")}, (
            "one paycheck must have ONE gross however much of the schedule "
            f"the caller holds; got {answers}"
        )

    def test_a_mid_year_raise_gives_two_constant_figures(
        self, biweekly_periods, simple_tax_configs,
    ):
        """A raise changes the rate ONCE; each side of it is flat.

        A non-recurring 10% raise effective July 2026 splits the year at the
        14th period (Jan 2 + 13*14 days = Jul 3).  Before it every paycheck is
        $60,000 / 26 = $2307.69; after it every paycheck is
        $2307.69 x 1.10 = $2538.459 -> $2538.46 (the raise steps the
        per-paycheck pay since plan step salary:X-av-3a, ruling R-SAL60; the
        yearly walk's $66,000 / 26 rounded to the same figure).  The
        superseded rule gave the first three of the pre-raise run and the
        first two of the post-raise run an extra cent, so each run held two
        figures.  The yearly figure each paycheck reports is pay x 26 (ruling
        R-SAL59): $59,999.94 and $65,999.96.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            raises=[
                FakeRaise(
                    percentage="0.10",
                    effective_month=7, effective_year=2026,
                    is_recurring=False,
                ),
            ],
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26
        pre = {r.earnings.gross_biweekly for r in results[:13]}
        post = {r.earnings.gross_biweekly for r in results[13:]}
        assert [r.earnings.annual_salary for r in results[:13]] == (
            [Decimal("59999.94")] * 13
        )
        assert [r.earnings.annual_salary for r in results[13:]] == (
            [Decimal("65999.96")] * 13
        )
        assert pre == {Decimal("2307.69")}, f"pre-raise run: {sorted(pre)}"
        assert post == {Decimal("2538.46")}, f"post-raise run: {sorted(post)}"

        # What ruling R-HW costs in a RAISE year, pinned: the superseded rule
        # made each segment total its exact pro-rata share ($30,000.00 and
        # $33,000.00, summing to $63,000.00). Each is now the flat rate times
        # the segment, three cents and two cents under respectively.
        pre_total = sum(r.earnings.gross_biweekly for r in results[:13])
        post_total = sum(r.earnings.gross_biweekly for r in results[13:])
        assert pre_total == Decimal("29999.97"), f"pre-raise total {pre_total}"
        assert post_total == Decimal("32999.98"), f"post-raise total {post_total}"
        assert pre_total + post_total == Decimal("62999.95")

    @pytest.mark.parametrize(
        "annual_salary,expected_year_total,expected_gap",
        [
            # 26 * round(annual/26) against the annual salary.  The gap is
            # what ruling R-HW accepts and MED-05 / PA-07 existed to close;
            # it is stated per row so the cost is reviewable rather than
            # asserted as "close enough".
            # 26 * 2307.69 = 59999.94, $0.06 under $60,000.
            (Decimal("60000"), Decimal("59999.94"), Decimal("-0.06")),
            # 26 * 1923.08 = 50000.08, $0.08 OVER $50,000 -- the gap has no
            # fixed sign, because the per-paycheck figure rounds either way.
            (Decimal("50000"), Decimal("50000.08"), Decimal("0.08")),
            # 26 * 3000.00 = 78000.00 exactly: a salary that divides evenly
            # has no gap at all.
            (Decimal("78000"), Decimal("78000.00"), Decimal("0.00")),
            # The owner's: 26 * 3525.96 = 91674.96, $0.04 under $91,675 --
            # and the employer's own flat $3,526.00 sums to $91,676.00, which
            # is $1.00 over.  Neither the app nor payroll hits the salary
            # exactly, which is why the identity was given up (finding N-391).
            (Decimal("91675"), Decimal("91674.96"), Decimal("-0.04")),
        ],
    )
    def test_the_year_no_longer_sums_to_the_annual_salary_exactly(
        self, annual_salary, expected_year_total, expected_gap,
        biweekly_periods, simple_tax_configs,
    ):
        """The COST of ruling R-HW, pinned so it cannot drift unnoticed.

        MED-05 / PA-07 added the residue distribution to make this sum exact.
        Ruling R-HW gives that up deliberately: the identity is not one payroll
        honours, and buying it cost a per-paycheck figure that no stub shows
        and a dependence on which pay-period rows exist (finding N-239).

        **Since plan step salary:X-av-3a the pay is the stored fact and the
        year is derived** (ruling R-SAL59: the yearly figure is pay x
        paychecks a year), so the total below is 26 x the pay by
        construction and the gap is the migration's one-time rounding of the
        old yearly salary (pinned in ``test_pay_entries_migration``).  What
        this case grades of the ENGINE is that the yearly figure every
        paycheck reports is that same total -- the one derivation R-SAL59
        names -- and never the contract salary it was converted from.
        """
        profile = FakeProfile(
            # The parametrized yearly figure / 26, ROUND_HALF_UP at the cent:
            # what migration 70680a4a7405 converts a stored salary to.
            pay=(annual_salary / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            created_at=date(2026, 1, 1),
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        total = sum(r.earnings.gross_biweekly for r in results)
        assert total == expected_year_total
        assert total - annual_salary.quantize(TWO_PLACES) == expected_gap
        assert {r.earnings.annual_salary for r in results} == {expected_year_total}

    # **``test_the_investment_projection_prices_the_same_gross`` was DELETED
    # at plan step salary:R14-b, and what it graded is worth stating.**  It
    # pinned that the paycheck engine and ``investment_projection`` rounded a
    # percentage deduction's gross identically -- driven at the owner's own
    # 2027 salary of $96,785.88, where until plan step balance:X-aw the two
    # answered $3,722.54 and $3,722.53 on 5 of his 63 saved periods, so a
    # percentage was taken against a gross a cent below the one the paycheck
    # subtracted it from.  X-aw made both ask
    # ``payroll_basis.gross_per_paycheck``, and this case held them there.
    #
    # It has no successor because it has no SUBJECT: the second producer is
    # gone.  The contribution feed reads this engine's own breakdown now
    # (ruling **R-SAL2**), so the agreement the case enforced is structural
    # rather than asserted -- and the caveat it carried is settled with it.
    # Its own docstring recorded that it graded the rounding rule and NOT the
    # wiring, because the two sides' INPUTS still differed by any applicable
    # raise (finding **D45**), and that "a case asserting these two equal on a
    # raise-BEARING profile would fail, and should".  There is one input now,
    # and ``test_income_service.TestThePerPeriodGrossIsTheENGINES`` grades a
    # raise-bearing profile end to end.


class TestGrossPerPaycheck:
    """The per-paycheck gross's rounding contract, graded directly rather than through the engine.

    Plan step **balance:X-aw** added ``gross_per_paycheck`` as a public
    function with two callers, and an adversarial review noted it had no case
    of its own.  **Plan step salary:X-av-3a deleted it**: the per-paycheck
    pay is stored now (ruling R-SAL59), so the yearly division it performed
    has no successor -- its float refusal and its divisor-at-every-cadence
    cases went with it -- and the migration's one conversion is graded in
    ``test_pay_entries_migration``.  What survives is its rounding MODE, which
    the walk's every raise step now owns (ruling R-SAL60).
    """

    def test_it_rounds_half_up_at_an_exact_half_cent(self):
        """The rounding MODE, at the one input where the modes disagree.

        None of the salaries the engine tests land on a half cent, so the mode
        is inherited from ``round_money`` and graded nowhere in this file.
        Re-stated on the pay list's walk at plan step salary:X-av-3a, where
        the rounding is a raise step's: $2,000.10 a paycheck under a 5% raise
        is 2000.10 x 1.05 = $2,100.105 exactly -- ROUND_HALF_UP gives
        $2,100.11, Python's default ROUND_HALF_EVEN (banker's) gives
        $2,100.10.  A money figure must never reach the even-rounding default
        implicitly.
        """
        profile = FakeProfile(
            pay=Decimal("2000.10"), pay_from=_WALK_OPENS,
            raises=[FakeRaise(percentage="0.05", effective_month=1,
                              effective_year=2026)],
        )
        assert _walked_pay(profile, date(2026, 1, 2)) == Decimal("2100.11")


class TestTheGrossContractIsDocumented:
    """The per-paycheck gross contract is stated where a reader will find it.

    Replaces ``TestBiweeklyResidueDocstring``, which pinned MED-05 / PA-07's
    wording so a revert to F-127's could not pass silently.  Ruling
    **balance:R-HW** superseded MED-05 / PA-07, so these pin the NEW contract
    for the same reason: the residue-distribution wording must not creep back
    in and read as though it still applied.

    The fourth arm that lived here -- the ids these docstrings cite have a
    row in the registry they name -- left this suite on 2026-09-11 and went
    with the planning registries at the tracker switch: each id now titles a
    card in the tracker, which no test reads.
    """

    def test_module_docstring_names_the_rate_contract(self):
        """Module docstring states the rule, what it replaced, and its cost."""
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel

        doc = paycheck_calculator.__doc__ or ""
        assert "RATE" in doc
        assert "X-aw" in doc
        assert "R-HW" in doc
        # The supersession trail stays legible in both directions.
        assert "MED-05" in doc and "PA-07" in doc
        # Both spellings appear and both are load-bearing: this contract
        # SUPERSEDED MED-05 / PA-07, which had itself superseded F-127.
        assert "superseding" in doc and "superseded" in doc
        assert "N-239" in doc
        # The cost is stated rather than quietly dropped.
        assert "no longer sum to the annual salary exactly" in doc

    def test_calculate_paycheck_docstring_points_at_the_one_producer(self):
        """The function docstring names the producer and denies the list.

        A caller reading only the signature in an IDE tooltip has to learn
        that the owner's payday SET does NOT reach the gross -- that is the
        whole content of finding N-239.  The set is no longer an argument at
        all: plan step **balance:X-bh-1** moved the four judgements that DO
        read it onto the calendar the basis carries.  The producer it names
        is the pay list's walk since plan step salary:X-av-3a deleted
        ``gross_per_paycheck``.
        """
        doc = calculate_paycheck.__doc__ or ""
        assert "base_pay_on" in doc
        assert "RATE" in doc
        assert "the payday SET does not reach it" in doc

    def test_the_producer_states_what_it_gave_up(self):
        """The producer carries the rulings that cost it cents.

        ``gross_per_paycheck`` carried R-HW, N-239 and MED-05 until plan step
        salary:X-av-3a deleted it; the package docstring keeps that trail
        (the case above).  The producer now is the pay list's walk,
        ``PayrollBasis.base_pay_on``, and what it gives up is ruled twice:
        the yearly figure is pay x paychecks a year rather than the salary
        typed (R-SAL59), and each raise step rounds to the cent (R-SAL60).
        """
        from app.services.payroll_basis import (  # pylint: disable=import-outside-toplevel
            BasePay,
            PayrollBasis,
        )

        doc = PayrollBasis.base_pay_on.__doc__ or ""
        assert "R-SAL59" in doc
        assert "R-SAL60" in doc
        assert "R-SAL59" in (BasePay.__doc__ or "")



# ── salary:S3-f-1: the basis NAMES its raise set ────────────────────


class TestTheBasisNamesItsRaiseSet:
    """Every engine read of a raise goes through ``basis.raises``.

    Plan step **salary:S3-f-1** (ruling **R-SAL20**).  The engine read
    ``basis.profile.raises`` at three sites -- the paycheck's own gross, the
    FICA wage-base cumulative and a capped deduction's year-to-date -- and
    badged the raise event off the profile too, so the only raise set a
    paycheck could be priced under was the stored one.  A what-if over one
    raise's end year (plan step salary:S3-f) needs all four to read the SAME
    supplied set, or the verdict beside it is priced under two beliefs.

    **Each case is one read, and each states the figure a read that still
    went to the rows would produce**, so a site left on ``profile.raises``
    fails the case that covers it rather than hiding behind the three that
    happen to agree.  The fixture: ``$60,000`` a year, ``$2,307.69`` a
    paycheck since plan step salary:X-av-3a, one recurring 5% merit raise
    every March from 2027 believed FOREVER on the rows, and TERMS carrying the
    same raise believed through 2027 alone -- so a 2029 paycheck is priced
    off ``$2,671.43`` under the rows (2307.69 x 1.05 = 2423.0745 -> 2423.07;
    x 1.05 = 2544.2235 -> 2544.22; x 1.05 = 2671.431 -> 2671.43, each step
    rounded by ruling R-SAL60) and ``$2,423.07`` (one application) under the
    terms.

    **The pay entry is dated 2026-01-09**, a payday on this calendar's rhythm
    (2029-01-05 less 78 fortnights) before the raise's first landing on
    2027-03-01: an entry replaces every raise landing on or before its payday
    (ruling R-SAL59), so an entry on the calendar's opening 2029-01-05 would
    already hold the 2027 and 2028 applications and the rows and the terms
    would differ by one application where this class needs three against one
    (the developer's rule-5 ruling "Keep each raise working").
    """

    @staticmethod
    def _rows_and_terms():
        """The profile (raise believed forever) and the terms (through 2027)."""
        forever = FakeRaise(
            percentage="0.05", effective_month=3, effective_year=2027,
            is_recurring=True, terminal_year=None,
        )
        through_2027 = FakeRaise(
            percentage="0.05", effective_month=3, effective_year=2027,
            is_recurring=True, terminal_year=2027,
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 9),  # $60,000.00 a year / 26
            raises=[forever], created_at=date(2026, 1, 1),
        )
        return profile, (through_2027,)

    @staticmethod
    def _periods_2029():
        """26 biweekly 2029 periods from Friday 2029-01-05."""
        start = date(2029, 1, 5)
        return [
            _period(start_date=start + timedelta(days=14 * i), period_id=i + 1)
            for i in range(26)
        ]

    def test_no_terms_prices_the_profiles_own_rows(self):
        """The default is the rows' own terms -- the stored plan, unchanged.

        ``raises`` is the rows converted to ``RaiseTerms`` values, never the
        rows themselves: the value's fields are the engine's whole contract
        with a raise, so a walk reading anything else fails here rather than
        pricing rows and values differently.
        """
        profile, _ = self._rows_and_terms()
        basis = payroll_basis(profile, self._periods_2029())

        assert basis.raise_terms is None
        assert basis.raises == terms_of(profile.raises)
        assert all(isinstance(term, RaiseTerms) for term in basis.raises)
        # 2029-06: three applications (2027, 2028, 2029) of 5% on $2,307.69:
        # 2671.43 a paycheck, 2671.43 x 26 = 69457.18 a year (R-SAL59).
        assert basis.base_pay_on(date(2029, 6, 1)).per_paycheck == Decimal("2671.43")
        assert basis.base_pay_on(date(2029, 6, 1)).annual == Decimal("69457.18")

    def test_a_supplied_set_is_canonicalised_too(self):
        """``raises`` is ``RaiseTerms`` values whichever arm supplied them.

        A supplied set spelled with raise-shaped objects is converted like
        the default arm's rows, so "the engine prices only the value" is a
        property of the basis and not of its callers' care -- a second
        adversarial review of this step found the first draft converting
        only the default arm.
        """
        profile, terms = self._rows_and_terms()
        believed = replace(
            payroll_basis(profile, self._periods_2029()), raise_terms=terms,
        )

        assert believed.raises == terms_of(terms)
        assert all(isinstance(term, RaiseTerms) for term in believed.raises)
        assert believed.raises[0].terminal_year == 2027

    def test_the_gross_is_priced_off_the_supplied_terms(
        self, simple_tax_configs,
    ):
        """Site 1: the paycheck's own gross reads the terms, not the rows.

        ``$2,423.07`` under the terms; a read that still went to the rows
        would price ``$2,671.43`` (the class docstring's walk).
        """
        profile, terms = self._rows_and_terms()
        periods = self._periods_2029()
        june = periods[11]  # 2029-06-08
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        assert calculate_paycheck(
            stored, june, simple_tax_configs,
        ).earnings.gross_biweekly == Decimal("2671.43")
        assert calculate_paycheck(
            believed, june, simple_tax_configs,
        ).earnings.gross_biweekly == Decimal("2423.07"), (
            "the paycheck's gross was priced off the profile's rows, not the "
            "raise set the basis names"
        )

    def test_the_raise_event_is_badged_off_the_supplied_terms(
        self, simple_tax_configs,
    ):
        """The banner announces the set the paycheck was PRICED under.

        March 2029 badges the forever raise on the rows; under terms believed
        through 2027 it moved nothing that year and must badge nothing --
        otherwise the surface announces a raise the gross beside it was not
        priced with, the two-walks defect ``salary_raises`` records.
        """
        profile, terms = self._rows_and_terms()
        periods = self._periods_2029()
        march = periods[4]  # 2029-03-02
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        assert calculate_paycheck(
            stored, march, simple_tax_configs,
        ).period.raise_event == "MERIT +5.00%"
        assert calculate_paycheck(
            believed, march, simple_tax_configs,
        ).period.raise_event == "", (
            "the raise event was badged off the profile's rows while the "
            "paycheck was priced off the basis's terms"
        )

    def test_the_wage_base_cumulative_reads_the_supplied_terms(self):
        """Site 3: the year-to-date gross sums the terms' salary.

        Ten prior 2029 paydays at ``$2,423.07`` sum to ``$24,230.70`` under
        the terms.  Under the rows the January and February paydays carry two
        applications (``$2,544.22``) and the March-May ones three
        (``$2,671.43``): ``4 x 2,544.22 + 6 x 2,671.43 = $26,205.46``.
        """
        profile, terms = self._rows_and_terms()
        periods = self._periods_2029()
        eleventh = periods[10]  # 2029-05-25; ten paydays before it in 2029
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        assert _get_cumulative_wages(stored, eleventh.start_date) == Decimal("26205.46")
        assert _get_cumulative_wages(believed, eleventh.start_date) == Decimal("24230.70"), (
            "the FICA wage-base cumulative summed the profile's rows' salary, "
            "not the raise set the basis names"
        )

    def test_a_capped_deductions_year_to_date_reads_the_supplied_terms(self):
        """Site 2: the annual-cap replay prices prior paydays off the terms.

        A 10% deduction capped at ``$2,500.00`` a year.  Under the terms the
        ten prior paydays took ``10 x round($2,423.07 x 0.10) = $2,423.10``,
        leaving ``$76.90`` of room, so the eleventh paycheck's line is
        clamped to exactly that.  Under the rows they took
        ``4 x 254.42 + 6 x 267.14 = $2,620.52``, past the cap, so the line is
        ``$0.00`` -- which is also what a replay that read the rows while the
        live gross read the terms would produce, so ``$76.90`` pins the
        replay's raise set and not merely the paycheck's.
        """
        profile, terms = self._rows_and_terms()
        profile.lines = [FakeDeduction(
            name="HSA", amount="0.10", calc_method="percentage",
            annual_cap="2500",
        )]
        periods = self._periods_2029()
        eleventh = periods[10]
        stored = payroll_basis(profile, periods)
        believed = replace(stored, raise_terms=terms)

        def line_under(basis):
            return _priced_lines(
                _LineContext(
                    basis, eleventh.start_date,
                    basis.base_pay_on(eleventh.start_date).per_paycheck,
                ),
                _timing_id("pre_tax_deduction"),
            )[0].amount

        assert line_under(stored) == ZERO
        assert line_under(believed) == Decimal("76.90"), (
            "the capped deduction's year-to-date replay priced prior paydays "
            "off the profile's rows, not the raise set the basis names"
        )


# ── salary:R15-b: the basis READS each line's cadence ────────────────


class TestTheBasisReadsEachLinesCadence:
    """A line's frequency is its recurrence rule's answer, read once per basis.

    Plan step **salary:R15-b** (rulings **R-SAL3**, **R-SAL29**).  Both
    deduction passes ask :meth:`PayrollBasis.line_applies_on`, which
    resolves each line's rule ONCE and walks it ONCE through the occurrence
    walk every recurring definition is generated by; a line with no rule is
    every paycheck.  The retired ``deductions_per_year`` compared a stored
    count against the payday's month ordinal per paycheck and, for a capped
    line, per prior payday of the year (ledger row SAL-556).
    """

    @staticmethod
    def _january():
        return [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
            _period(start_date=date(2026, 2, 13), period_id=4),
        ]

    def test_a_line_with_no_rule_is_every_paycheck(self):
        """R-SAL3's NULL: no rule, taken on every payday the calendar names."""
        line = FakeDeduction(name="401k", amount="200")
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        assert all(basis.line_applies_on(line, p.start_date) for p in self._january())

    def test_a_fake_with_no_rule_attribute_at_all_is_every_paycheck(self):
        """A duck-typed line that never heard of rules reads as the column's old 26."""
        line = FakeDeduction(name="401k", amount="200")
        del line.recurrence_rule
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        assert basis.line_applies_on(line, date(2026, 1, 30))

    def test_the_rule_is_resolved_and_walked_once_per_basis(self, monkeypatch):
        """Twenty asks, one resolution, one walk: the memo is per basis, not per ask."""
        # pylint: disable=import-outside-toplevel
        from app.services import payroll_basis as module

        line = FakeDeduction(
            name="Health", amount="100", recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        resolves, walks = [], []
        real_resolve, real_walk = module.resolve, module.projected_occurrence_placements

        def counted_resolve(*args, **kwargs):
            resolves.append(args)
            return real_resolve(*args, **kwargs)

        def counted_walk(*args, **kwargs):
            walks.append(args)
            return real_walk(*args, **kwargs)

        monkeypatch.setattr(module, "resolve", counted_resolve)
        monkeypatch.setattr(module, "projected_occurrence_placements", counted_walk)
        answers = [
            basis.line_applies_on(line, p.start_date)
            for p in self._january() * 5
        ]
        assert answers[:4] == [True, True, False, True]
        assert len(resolves) == 1 and len(walks) == 1

    def test_the_walk_reaches_paydays_past_the_saved_schedule(self):
        """A projected payday years past the record is admitted or skipped by the same rule.

        The engine prices projected paychecks (a 20-year retirement
        projection), so the memo walks past the saved horizon as far as it
        is asked: June 2028 holds three paydays on this rhythm, two and a
        half years past the seeded schedule, and the 24 line is taken on its
        first two and skipped on its third.
        """
        line = FakeDeduction(
            name="Health", amount="100", recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        rhythm = [date(2026, 1, 2) + timedelta(days=14 * k) for k in range(80)]
        june_2028 = [d for d in rhythm if (d.year, d.month) == (2028, 6)]
        assert len(june_2028) == 3, june_2028
        assert [basis.line_applies_on(line, d) for d in june_2028] == [True, True, False]
        may_2028 = [d for d in rhythm if (d.year, d.month) == (2028, 5)]
        assert len(may_2028) == 2, may_2028
        assert all(basis.line_applies_on(line, d) for d in may_2028)

    def test_lines_with_one_cadence_share_one_walk(self, monkeypatch):
        """Eleven 24-per-year lines resolve to one cadence and are walked ONCE.

        The developer's eleven lines are exactly this shape (measured
        2026-09-13 on a clone: one walk instead of eleven took the 63-paycheck
        pricing from 800 ms to 9 ms beside the base tree's 5 ms).
        """
        # pylint: disable=import-outside-toplevel
        from app.services import payroll_basis as module

        lines = [
            FakeDeduction(
                name=f"line {n}", amount="10",
                recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
            )
            for n in range(11)
        ]
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=lines,
        ), self._january())
        walks = []
        real_walk = module.projected_occurrence_placements
        monkeypatch.setattr(
            module, "projected_occurrence_placements",
            lambda *a, **k: walks.append(a) or real_walk(*a, **k),
        )
        for line in lines:
            for p in self._january():
                basis.line_applies_on(line, p.start_date)
        assert len(walks) == 1
        assert len({id(c) for c in basis._line_cadences.values()}) == 1  # pylint: disable=protected-access

    def test_the_walk_extends_when_asked_past_its_reach(self, monkeypatch):
        """Every walk reaches a year past the ask, and a re-walk at least doubles the span.

        Asked 2026-01-02 the walk reaches 2027-01-03; asked 2028-06-16 it
        must extend, to max(2028-06-16 + 366 days = 2029-06-17, the doubled
        span 2028-01-04) = 2029-06-17; asked 2028-06-02 and 2028-06-30 (June
        2028's first and third paydays), both within reach, no walk.  The
        year past the ask is what an adversarial review of this step showed
        to be load-bearing (the containing-date case below).
        """
        # pylint: disable=import-outside-toplevel
        from app.services import payroll_basis as module

        line = FakeDeduction(
            name="Health", amount="100", recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        reaches = []
        real_walk = module.projected_occurrence_placements
        monkeypatch.setattr(
            module, "projected_occurrence_placements",
            lambda *a, **k: reaches.append(k["through"]) or real_walk(*a, **k),
        )
        basis.line_applies_on(line, date(2026, 1, 2))
        basis.line_applies_on(line, date(2026, 12, 18))
        assert reaches == [date(2027, 1, 3)]
        basis.line_applies_on(line, date(2028, 6, 16))
        assert reaches == [date(2027, 1, 3), date(2029, 6, 17)]
        assert basis.line_applies_on(line, date(2028, 6, 2)) is True
        assert basis.line_applies_on(line, date(2028, 6, 30)) is False  # June's third
        assert reaches == [date(2027, 1, 3), date(2029, 6, 17)]

    def test_a_containing_date_occurrence_after_the_payday_is_admitted_at_the_reach(self):
        """An occurrence up to a period AFTER a payday places on it; the reach must cover it.

        Reproduced by the review of this step on the first memo shape, which
        set the reach exactly at the payday asked: a monthly-on-the-15th line
        under CONTAINING_DATE places 2028-01-15 on the paycheck of 2028-01-14,
        and an ask for 2028-01-14 that jumped past the reach walked through
        2028-01-14, missed the 15th, and CACHED the wrong ``False``.  Neither
        migrated shape can meet it (a paycheck occurrence IS its payday, and
        a monthly-first occurrence precedes its paycheck), and neither does a
        form-authored line: R15-c derives a monthly line's first occurrence
        as the 1st (ruling R-SAL36), so the 15th here is the engine's own
        contract, graded on a shape a hand-authored rule can still state --
        a CONTAINING_DATE monthly line from the 1st meets the same reach
        concern whenever the 1st falls the day after a payday.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum

        fifteenth = FakeRule(
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 1, 15),
        )
        line = FakeDeduction(name="Union dues", amount="25", recurrence_rule=fifteenth)
        basis = payroll_basis(FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            lines=[line],
        ), self._january())
        assert basis.line_applies_on(line, date(2026, 1, 2)) is True   # Jan 2-15 holds the 15th
        # A far ask, then the same payday asked again: both must be True.
        assert basis.line_applies_on(line, date(2028, 1, 14)) is True  # Jan 14-27 holds the 15th
        assert basis.line_applies_on(line, date(2028, 1, 14)) is True
        assert basis.line_applies_on(line, date(2028, 1, 28)) is False

    def test_the_cumulative_asks_no_month_ordinal(self):
        """The capped line's year-to-date replays membership, not the month (SAL-556).

        Structural: the lines module (``_deductions`` until plan step
        salary:R18-b) no longer imports the ordinal reader at all, so the
        per-prior-payday month derivation the ledger row measured cannot be
        reintroduced without this line noticing.
        """
        # pylint: disable=import-outside-toplevel
        import inspect
        from app.services.paycheck_calculator import _lines as module

        source = inspect.getsource(module)
        assert "_month_ordinal" not in source
        assert "line_applies_on" in source

    def test_a_capped_twenty_four_line_caps_over_the_paydays_it_is_taken_on(self):
        """The year-to-date sums only admitted paydays: the cap lands one paycheck later than it would at 26."""
        line = FakeDeduction(
            name="HSA", amount="600", annual_cap="1500",
            recurrence_rule=twenty_four_rule(date(2026, 1, 2)),
        )
        periods = self._january()
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        basis = payroll_basis(profile, periods)
        gross = (Decimal("60000") / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        amounts = [
            [l.amount for l in _priced_lines(
                _LineContext(basis, p.start_date, gross), _timing_id("pre_tax_deduction"),
            )]
            for p in periods
        ]
        # Jan 2 and Jan 16 taken (600, 600 -> 1200 of the 1500 cap); Jan 30 is
        # the third paycheck, skipped; Feb 13 takes the remaining 300.
        assert amounts == [[Decimal("600.00")], [Decimal("600.00")], [], [Decimal("300.00")]]


# ── Earning lines (plan step salary:R18-b, ruling R-SAL38) ──────────────


class TestEarningLines:
    """A paycheck is base pay plus a list of lines: the two EARNING kinds, hand-computed.

    Every figure here is derived by hand from the same rules the deduction
    cases above state ($60k over 26; 10% to $50k, 22% above, $15k standard
    deduction; NC 4.5%; FICA 6.2% / 1.45%) and asserted as a constant, so the
    engine is graded against arithmetic done outside it.  Base pay is
    ``60000 / 26 = 2307.69``; the base case with no line nets ``1854.22``
    (``test_basic_paycheck_no_deductions``).
    """

    @staticmethod
    def _one(period_date=date(2026, 1, 16)):
        return _period(start_date=period_date, period_id=1)

    def test_a_taxable_earning_joins_gross_fica_and_withholding(self, simple_tax_configs):
        """+$100 taxable every paycheck: gross 2407.69, every tax line moves, net 1932.07.

          gross    = 2307.69 + 100.00 = 2407.69     (base stays 2307.69)
          federal  : annual 62599.94 - 15000 = 47599.94; 10% -> 4759.99; /26 = 183.08
          state    : 62599.94 * 0.045 = 2817.00; /26 = 108.35
          SS       : 2407.69 * 0.062 = 149.28
          Medicare : 2407.69 * 0.0145 = 34.91
          net      = 2407.69 - 183.08 - 108.35 - 149.28 - 34.91 = 1932.07
        """
        line = FakeDeduction(name="Phone Allowance", amount="100", paycheck_line_kind="taxable_earning")
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        period = self._one()

        result = calculate_paycheck(payroll_basis(profile, [period]), period, simple_tax_configs)

        assert result.earnings.base_biweekly == Decimal("2307.69")
        assert result.earnings.gross_biweekly == Decimal("2407.69")
        assert result.earnings.total_taxable == Decimal("100.00")
        assert [(l.name, l.amount, l.target_account_id) for l in result.earnings.taxable] == [
            ("Phone Allowance", Decimal("100.00"), None),
        ]
        assert result.earnings.after_tax == []
        assert result.earnings.taxable_income == Decimal("2407.69")
        assert result.taxes.federal == Decimal("183.08")
        assert result.taxes.state == Decimal("108.35")
        assert result.taxes.social_security == Decimal("149.28")
        assert result.taxes.medicare == Decimal("34.91")
        assert result.earnings.net_pay == Decimal("1932.07")
        # The pass shares the deduction lists' shape and leaves them empty.
        assert result.deductions.pre_tax == [] and result.deductions.post_tax == []

    def test_an_after_tax_earning_joins_net_and_nothing_else(self, simple_tax_configs):
        """+$100 after tax: gross, taxable income and every tax line as the base case; net + 100."""
        line = FakeDeduction(name="Reimbursement", amount="100", paycheck_line_kind="after_tax_earning")
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        period = self._one()

        result = calculate_paycheck(payroll_basis(profile, [period]), period, simple_tax_configs)

        assert result.earnings.base_biweekly == Decimal("2307.69")
        assert result.earnings.gross_biweekly == Decimal("2307.69")
        assert result.earnings.taxable_income == Decimal("2307.69")
        assert result.earnings.taxable == []
        assert [(l.name, l.amount) for l in result.earnings.after_tax] == [
            ("Reimbursement", Decimal("100.00")),
        ]
        assert result.earnings.total_after_tax == Decimal("100.00")
        assert (result.taxes.federal, result.taxes.state,
                result.taxes.social_security, result.taxes.medicare) == (
            Decimal("173.08"), Decimal("103.85"), Decimal("143.08"), Decimal("33.46"),
        )
        assert result.earnings.net_pay == Decimal("1954.22")

    def test_a_percentage_deduction_is_a_percentage_of_base_not_of_gross(self, simple_tax_configs):
        """6% pre-tax beside a $100 taxable earning: 6% x 2307.69 = 138.46, not 6% x 2407.69 = 144.46.

        Ruling R-SAL38 (3).  With the line taken from base:
          taxable income = 2407.69 - 138.46 = 2269.23
          federal : annual 62599.94 - 3599.96 (138.46 x 26) - 15000 = 43999.98; 10% -> 4400.00; /26 = 169.23
          state   : 2269.23 x 26 = 58999.98 x 0.045 = 2655.00; /26 = 102.12
          SS / Medicare on gross: 149.28 / 34.91
          net     = 2407.69 - 138.46 - 169.23 - 102.12 - 149.28 - 34.91 = 1813.69
        """
        lines = [
            FakeDeduction(name="Phone Allowance", amount="100", paycheck_line_kind="taxable_earning"),
            FakeDeduction(name="State Retirement", amount="0.06", calc_method="percentage",
                          paycheck_line_kind="pre_tax_deduction"),
        ]
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=lines,
        )
        period = self._one()

        result = calculate_paycheck(payroll_basis(profile, [period]), period, simple_tax_configs)

        assert result.deductions.pre_tax[0].amount == Decimal("138.46")
        assert result.earnings.gross_biweekly == Decimal("2407.69")
        assert result.earnings.taxable_income == Decimal("2269.23")
        assert result.taxes.federal == Decimal("169.23")
        assert result.taxes.state == Decimal("102.12")
        assert result.earnings.net_pay == Decimal("1813.69")

    def test_a_taxable_earnings_cadence_places_it_on_the_first_paycheck_of_the_month(
        self, simple_tax_configs,
    ):
        """The developer's phone allowance: $45 taxable on the month's first paycheck only.

        Jan 2 is January's first paycheck (gross 2352.69); Jan 16 its second
        (gross 2307.69, no line); the year-to-date wages before Jan 16 carry
        the $45 -- the one gross producer, replayed.
        """
        line = FakeDeduction(
            name="Phone Allowance", amount="45", paycheck_line_kind="taxable_earning",
            recurrence_rule=twelve_rule(date(2026, 1, 1)),
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        jan2 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan16 = _period(start_date=date(2026, 1, 16), period_id=2)
        basis = payroll_basis(profile, [jan2, jan16])

        first = calculate_paycheck(basis, jan2, simple_tax_configs)
        second = calculate_paycheck(basis, jan16, simple_tax_configs)

        assert first.earnings.gross_biweekly == Decimal("2352.69")
        assert [l.amount for l in first.earnings.taxable] == [Decimal("45.00")]
        assert second.earnings.gross_biweekly == Decimal("2307.69")
        assert second.earnings.taxable == []
        assert _get_cumulative_wages(basis, jan16.start_date) == Decimal("2352.69")

    def test_the_year_to_date_wages_carry_the_taxable_earning_into_the_ss_cap(self):
        """A $100 taxable line reaches a $4,700 wage base one paycheck earlier.

        statutory max = 0.062 x 4700 = 291.40.  Without the line the third
        paycheck (cumulative 4615.38) owes the remainder, 5.25; with it the
        second paycheck (cumulative 2407.69) owes 142.12 and the third
        (cumulative 4815.38) owes nothing -- which is what a cumulative that
        left the earning out would get wrong.
        """
        configs = {
            "bracket_set": None,
            "state_config": None,
            "fica_config": FakeFicaConfig(ss_wage_base="4700"),
        }
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        plain = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_line = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[FakeDeduction(name="Bonus", amount="100", paycheck_line_kind="taxable_earning")],
        )

        plain_ss = [
            calculate_paycheck(payroll_basis(plain, periods), p, configs).taxes.social_security
            for p in periods
        ]
        line_ss = [
            calculate_paycheck(payroll_basis(with_line, periods), p, configs).taxes.social_security
            for p in periods
        ]

        assert plain_ss == [Decimal("143.08"), Decimal("143.08"), Decimal("5.25")]
        assert line_ss == [Decimal("149.28"), Decimal("142.12"), Decimal("0.00")]
        assert _get_cumulative_wages(payroll_basis(with_line, periods), periods[2].start_date) == Decimal("4815.38")

    def test_a_lines_span_bounds_the_paychecks_it_is_taken_on(self, simple_tax_configs):
        """A rule from the second paycheck for two occurrences is taken on the 2nd and 3rd only.

        Plan step salary:R18-c (ruling **R-SAL38** (2)): a line's span is its
        rule's own ``starts_on`` and closing bound, which the occurrence walk
        the engine already reads has honoured for every definition; this
        pins that a payroll line's earning rides only the paydays inside it.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
        rule = FakeRule(
            unit=RecurrenceUnitEnum.PERIOD,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 1, 16),
        )
        rule.max_occurrences = 2
        line = FakeDeduction(
            name="Stipend", amount="100", paycheck_line_kind="after_tax_earning",
            recurrence_rule=rule,
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
            _period(start_date=date(2026, 2, 13), period_id=4),
        ]
        basis = payroll_basis(profile, periods)

        results = [calculate_paycheck(basis, p, simple_tax_configs) for p in periods]

        assert [r.earnings.total_after_tax for r in results] == [
            Decimal("0.00"), Decimal("100.00"), Decimal("100.00"), Decimal("0.00"),
        ]

    def test_an_earning_is_capped_and_deactivated_like_any_line(self, simple_tax_configs):
        """The shared pass: a $150 annual cap on a $100 after-tax line pays 100, 50, 0; an inactive line pays nothing."""
        capped = FakeDeduction(
            name="Stipend", amount="100", annual_cap="150", paycheck_line_kind="after_tax_earning",
        )
        inactive = FakeDeduction(
            name="Old Allowance", amount="500", paycheck_line_kind="taxable_earning", is_active=False,
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[capped, inactive],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        basis = payroll_basis(profile, periods)

        results = [calculate_paycheck(basis, p, simple_tax_configs) for p in periods]

        assert [r.earnings.total_after_tax for r in results] == [
            Decimal("100.00"), Decimal("50.00"), Decimal("0.00"),
        ]
        assert [r.earnings.net_pay for r in results] == [
            Decimal("1954.22"), Decimal("1904.22"), Decimal("1854.22"),
        ]
        assert all(r.earnings.gross_biweekly == Decimal("2307.69") for r in results)
