"""The helpers and constants two or more of this package's parts read: the module-level helpers of
``tests/test_services/test_paycheck_calculator.py``, moved verbatim by ``tools/split``.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services.pay_calendar import DerivedPeriod
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


#: The owner of every stand-in here by default: :class:`FakeRule`'s and
#: :class:`FakeProfile`'s.  The two must agree, because the calendar the
#: :func:`payroll_basis` helper derives belongs to the PROFILE's owner and the
#: engine refuses a rule resolved against another owner's schedule.  No user
#: row has this id, which is fine: nothing here reads the owner from the
#: database, only compares the rule's owner with the calendar's.
_STAND_IN_OWNER = 1


class FakeRule:
    """Minimal stand-in for a deduction's ``RecurrenceRule`` row.

    Carries exactly the attributes :func:`app.services.recurrence
    .recurrence_spec` reads off a row (plan step salary:R15-b), so the engine
    resolves it against the basis's calendar as it would a stored rule.
    ``user_id`` is what the row's owning arc would answer; the calendar the
    :func:`payroll_basis` helper derives belongs to the profile's owner, and
    both default to :data:`_STAND_IN_OWNER`.
    """

    def __init__(self, *, unit, placement, starts_on, interval_n=1,
                 max_per_month=None, user_id=_STAND_IN_OWNER):
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

    **It names an owner** (``user_id``, :data:`_STAND_IN_OWNER` by default),
    because ``payroll_basis`` derives the calendar for the profile's owner.
    """

    def __init__(self, pay, raises=None, lines=None,
                 created_at=None, pay_from=date(2026, 1, 2),
                 additional_income=0, additional_deductions=0,
                 extra_withholding=0, qualifying_children=0,
                 other_dependents=0, *, user_id=_STAND_IN_OWNER):
        self.user_id = user_id
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
