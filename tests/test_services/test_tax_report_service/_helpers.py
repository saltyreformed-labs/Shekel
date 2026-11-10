"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_tax_report_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import paycheck_calculator
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import load_tax_configs_for_year
from tests._test_helpers import payroll_basis, start_test_pay_list


ZERO = Decimal("0")


# ── Helpers ───────────────────────────────────────────────────────


def _make_profile(
    seed_user,
    *,
    name="Tax Report Profile",
    filing_status_name="single",
    state_code="NC",
    pay="5000.00",  # $130,000.00 a year / 26
    additional_income="0.00",
    qualifying_children=0,
    other_dependents=0,
    sort_order=0,
    first_payday=date(2026, 1, 2),
):
    """Build and flush an active salary profile for the seeded user.

    Mirrors the liability/withholding test profile helpers.  Salary
    defaults to 130,000 (5,000.00/period at 26, no residue).  ``name`` is a
    parameter because ``uq_salary_profiles_user_scenario_name`` forbids two
    same-named profiles for one user + scenario.  ``first_payday`` is the day
    the pay list starts: the 2026 calendar's first payday unless a case
    prices another year (2025's is 2025-01-03, 26 x 14 days earlier).
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
        additional_income=Decimal(additional_income),
        additional_deductions=Decimal("0.00"),
        qualifying_children=qualifying_children,
        other_dependents=other_dependents,
        sort_order=sort_order,
    )
    _db.session.add(profile)
    # From the 2026 calendar's first payday by default: every caller builds
    # its profile BEFORE ``_make_full_year_periods``, when that day is still a
    # PROJECTED payday off the seed owner's 2024 bootstrap (52 x 14 days on).
    start_test_pay_list(profile, Decimal(pay), first_payday)
    _db.session.flush()
    return profile


def _committed_profile(seed_user, **kwargs):
    """Build and flush a profile; it prices under the shipped law."""
    profile = _make_profile(seed_user, **kwargs)
    _db.session.flush()
    return profile


def _make_full_year_periods(user, count=26, start=date(2026, 1, 2)):
    """Create *count* biweekly 2026 pay periods directly (period_index 1..N).

    Built directly rather than via ``seed_periods`` (10 only) so the full
    26-pay 2026 year exists; the producer's 2026 year-periods query returns
    exactly these (the seed_user bootstrap is a 2024 row).
    """
    periods = []
    for i in range(count):
        start_date = start + timedelta(days=14 * i)
        period = PayPeriod(user_id=user.id, start_date=start_date)
        _db.session.add(period)
        periods.append(period)
    _db.session.flush()
    return periods


def _add_pretax_deduction(
    profile, amount, name="401k", kind=PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
):
    """Add a flat pre-tax deduction taken every period (26/year).

    ``kind`` is which of the two pre-tax kinds the line is (plan step
    salary:S11-e): the default, "Pre-tax, income tax only" (a 401k), leaves
    the income-tax base alone; ``PRE_FICA_DEDUCTION``, "Pre-tax, all payroll
    taxes" (a Health premium), leaves the Social Security and Medicare wages
    as well.
    """
    flat = _db.session.query(CalcMethod).filter_by(name="flat").one()
    ded = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(kind),
        calc_method_id=flat.id,
        name=name,
        amount=Decimal(amount),
    )
    _db.session.add(ded)
    _db.session.flush()


def _derived(user_id, year=2026):
    """The year's pay periods AS THE PRODUCER SEES THEM.

    ``_load_year_periods`` reads the owner's DERIVED calendar and filters it to
    the tax year since pay-calendar plan step C2-f2d-3, so the oracle below is
    handed the same shape rather than the ORM rows the fixtures create.
    """
    return tuple(
        period for period in calendar_for(user_id).saved()
        if period.start_date.year == year
    )


def _project_sum(profile, year, periods):
    """Independent oracle: sum ``project_salary`` over *periods*.

    Same configs SSOT and engine as the producer (one year's law: no case
    here seeds a pay stub, the only thing that reads another year's since
    plan step salary:S11-c-2c, ruling **R-SAL77**).  For the
    flat, sub-cap, no-deduction scenarios here the full-context and
    subset-restart projections coincide (all figures below the 184,500 SS
    wage base and 200,000 Medicare surtax threshold), so this stays a
    genuinely independent check of the producer's dollar values.

    **Since plan step balance:X-bh-1 there is no "subset restart" to
    coincide with**: the engine reads the year's paydays off the owner's
    CALENDAR, which this oracle is now handed too, so both sides carry the
    same year-to-date context and what stays independent is the period
    SUBSET each is asked to sum.
    """
    configs = load_tax_configs_for_year(profile, year)
    breakdowns = paycheck_calculator.project_salary(
        payroll_basis(profile, periods), periods, configs,
    )
    return {
        "gross": sum((b.earnings.gross_biweekly for b in breakdowns), ZERO),
        "federal": sum((b.taxes.federal for b in breakdowns), ZERO),
        "state": sum((b.taxes.state for b in breakdowns), ZERO),
        "ss": sum((b.taxes.social_security for b in breakdowns), ZERO),
        "medicare": sum((b.taxes.medicare for b in breakdowns), ZERO),
    }
