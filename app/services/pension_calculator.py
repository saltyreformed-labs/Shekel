"""
Shekel Budget App -- Pension Calculator Service

Pure function service that calculates defined-benefit pension income
based on years of service, salary projection, and a benefit multiplier.

All functions are pure (no DB access) -- data is passed in as arguments.
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.utils.money import round_money

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


@dataclass
class PensionBenefit:
    """Result of a pension benefit calculation."""
    years_of_service: Decimal
    high_salary_average: Decimal
    annual_benefit: Decimal
    monthly_benefit: Decimal
    high_salary_years: list = field(default_factory=list)  # [(year, salary)]


def calculate_benefit(benefit_multiplier, consecutive_high_years,
                      hire_date, planned_retirement_date,
                      salary_by_year):
    """Calculate the projected pension benefit.

    Args:
        benefit_multiplier:      Decimal per-year multiplier (e.g. 0.0185 for 1.85%).
        consecutive_high_years:  int -- number of consecutive highest salary years to average.
        hire_date:               date -- employment start date.
        planned_retirement_date: date -- planned retirement date.
        salary_by_year:          list of (year, yearly salary) tuples, sorted by year.

    Returns:
        PensionBenefit dataclass.
    """
    benefit_multiplier = Decimal(str(benefit_multiplier))
    years_of_service = _calculate_years_of_service(hire_date, planned_retirement_date)

    if not salary_by_year:
        return PensionBenefit(
            years_of_service=years_of_service,
            high_salary_average=ZERO,
            annual_benefit=ZERO,
            monthly_benefit=ZERO,
        )

    high_avg, high_years = _compute_high_salary_average(
        salary_by_year, consecutive_high_years
    )

    annual_benefit = round_money(
        benefit_multiplier * years_of_service * high_avg
    )

    monthly_benefit = round_money(annual_benefit / 12)

    return PensionBenefit(
        years_of_service=years_of_service,
        high_salary_average=high_avg,
        annual_benefit=annual_benefit,
        monthly_benefit=monthly_benefit,
        high_salary_years=high_years,
    )


def project_profile_salaries(basis, start_year, end_year):
    """Project a salary profile's yearly salary for each year in a range.

    **Each year's salary is the pay in force on December 1, times that
    day's paychecks a year** -- :meth:`~app.services.payroll_basis
    .PayrollBasis.pay_rate_on`'s :attr:`~app.services.pay_rate.PayRate
    .annual`, the one derivation of a pay rate since task plan#3100, and the
    engine's own base pay on December 1 from plan step salary:X-av-3a
    (ruling **R-SAL59**: the yearly figure is pay x paychecks a year, derived
    and never stored).  A rate and not a paycheck: a paycheck whose pay
    period straddles a raise pays part of it (ruling **plan#3089**), and is
    no year's salary.
    Until then this walked ``salary_raises.apply_raises`` over the profile's
    stored annual salary: a second walk of the raises beside the engine's,
    agreeing because both called one function, and one the pay list's
    replacement rule (a recorded entry replaces the forecast raises due by
    its payday) could never have reached.  The raise set is the basis's own
    (:attr:`~app.services.payroll_basis.PayrollBasis.raise_terms`), which is
    how a plan point's believed set reaches the pension (plan step
    salary:S3-f-2b, ruling **R-SAL20**).

    **It shares ONE TERMINATION RULE with the paychecks** (ruling
    **R-SAL11**): the end year is stored per raise and read by the one walk.

    **It evaluates each YEAR at December 1**, so every raise effective during
    that year -- recurring or one-time -- is applied, where the engine prices
    each payday at its own date.  A payday in a year whose raise lands after
    January is priced here from that year's post-raise pay; unifying that is
    plan step **salary:S3**'s, which makes the engine price the whole
    projected horizon.

    Args:
        basis: The :class:`~app.services.payroll_basis.PayrollBasis` of the
            profile, built with the raise set to project under.
        start_year: int first year to project (the current year, by caller
            convention -- every call site passes the read pass's
            ``as_of.year``).
        end_year: int last year to project (inclusive).

    Returns:
        list of (year, Decimal salary) tuples.
    """
    return [
        (year, basis.pay_rate_on(date(year, 12, 1)).annual)
        for year in range(start_year, end_year + 1)
    ]


def _calculate_years_of_service(hire_date, retirement_date):
    """Calculate years of service as a Decimal."""
    if not hire_date or not retirement_date:
        return ZERO
    delta_days = (retirement_date - hire_date).days
    if delta_days < 0:
        return ZERO
    return (Decimal(str(delta_days)) / Decimal("365.25")).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def _compute_high_salary_average(salary_by_year, consecutive_high_years):
    """Find the consecutive window with the highest average salary.

    Args:
        salary_by_year:        list of (year, Decimal salary) sorted by year.
        consecutive_high_years: int window size.

    Returns:
        (best_avg, best_window) where best_window is the list of (year, salary) tuples.
    """
    n = len(salary_by_year)
    window_size = min(consecutive_high_years, n)

    if window_size <= 0:
        return ZERO, []

    best_avg = ZERO
    best_window = []

    for i in range(n - window_size + 1):
        window = salary_by_year[i:i + window_size]
        total = sum(Decimal(str(s)) for _, s in window)
        avg = round_money(total / window_size)
        if avg > best_avg:
            best_avg = avg
            best_window = window

    return best_avg, best_window
