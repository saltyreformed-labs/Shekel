"""What the job pays on a day and on a payday, as values: a pay rate and a paycheck's base pay.

Moved out of :mod:`app.services.payroll_basis` unchanged (task plan#3169), so
the basis stays under the 1,000-line ceiling (ruling **R-IR**) as the raise
doors' producer joins it.  :meth:`~app.services.payroll_basis.PayrollBasis
.pay_rate_on` returns a :class:`PayRate` and :meth:`~app.services
.payroll_basis.PayrollBasis.base_pay_on` a :class:`BasePay`; the basis is the
only producer of either.

Pure: values, no Flask, no database.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services.pay_calendar import PayCadence


@dataclass(frozen=True)
class PayRate:
    """What the job pays a paycheck on one DAY, at the rhythm in force that day.

    Task plan#3100: the stub's Rate/Salary.  :meth:`PayrollBasis.pay_rate_on`
    returns it for any day, and a paycheck's own rate is the one in force on
    the last day it pays for (:attr:`BasePay.rate`).  Every yearly figure
    reads a rate, never a paycheck's base times the count: a paycheck whose
    pay period straddles a raise pays part of it (ruling **plan#3089**), and
    that paycheck times 26 is no one's salary.

    **The yearly figure is a property of the other two, never a third field**
    (R-SAL59: "The yearly figure is pay x paychecks a year, shown, never
    stored").

    **It carries the CADENCE, not only its count** (ruling **R-SAL70**): a
    reader turning a figure into a monthly or yearly one converts at the
    rhythm it was priced at, through
    :class:`~app.services.pay_calendar.PayCadence`'s own conversions, and
    never at a count read off the calendar a second time.

    Attributes:
        per_paycheck: What one paycheck pays at this rate, cent-exact.
        cadence: The rhythm the rate is paid at.
    """

    per_paycheck: Decimal
    cadence: PayCadence

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks a year :attr:`cadence` pays, an integral ``Decimal``."""
        return self.cadence.periods_per_year

    @property
    def annual(self) -> Decimal:
        """Return the yearly figure: :attr:`per_paycheck` times the paychecks a year, exact."""
        return self.per_paycheck * self.periods_per_year


@dataclass(frozen=True)
class BasePay:
    """What the salary pays on one payday: the paycheck's base, and its pay rate.

    Plan step **salary:X-av-2** (ruling **R-SAL66**; ledger row **SAL-569**),
    the stored fact's since plan step **salary:X-av-3a** (ruling
    **R-SAL59**), and two figures since task plan#3100 (ruling
    **plan#3089**).  The value :meth:`PayrollBasis.base_pay_on` returns, so
    the paycheck engine reads a payday's salary figures from ONE read: the
    base every paycheck prices from, the rate its yearly figure is, and the
    count Pub 15-T annualises the paycheck's wages by.

    **The base and the rate part only where a change lands inside the pay
    period.**  Made-up: $2,000.00 a paycheck, 5% from Wednesday 2027-03-03,
    a paycheck paying Saturday 02-20 through Friday 03-05.  Its ten weekdays
    are seven at $2,000.00 and three at $2,100.00, so its base is $1,400.00 +
    $630.00 = $2,030.00, and its rate is $2,100.00, the pay in force on 03-05:
    $54,600.00 a year at 26, where the base would have read $52,780.00.

    Attributes:
        per_paycheck: What this paycheck pays: each weekday it pays for at
            the pay in force that day (:meth:`PayrollBasis.base_pay_on`).
        rate: The paycheck's :class:`PayRate`: the pay in force on the last
            day it pays for, at the paycheck's rhythm.
    """

    per_paycheck: Decimal
    rate: PayRate

    @property
    def cadence(self) -> PayCadence:
        """Return the rhythm the paycheck is paid at (``cadence_on`` the payday)."""
        return self.rate.cadence

    @property
    def periods_per_year(self) -> Decimal:
        """Return how many paychecks a year the paycheck's rhythm pays."""
        return self.rate.periods_per_year

    @property
    def annual(self) -> Decimal:
        """Return the paycheck's yearly figure: its RATE's, never its base times the count."""
        return self.rate.annual


__all__ = ["BasePay", "PayRate"]
