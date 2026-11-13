"""The pay walk's values, and the helpers that price with them.

A leaf of :mod:`app.services.payroll_basis` (task plan#3576):
:class:`RhythmChangeWithoutPay`, the one public value, and the walk's private
records and helpers, which :class:`~app.services.payroll_basis.PayrollBasis`
reads.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.services.pay_calendar import PaidDays, PayCadence
from app.services.pay_rate import PayRate
from app.services.salary_raises import RaiseTerms
from app.utils.business_days import SATURDAY
from app.utils.money import round_money


@dataclass(frozen=True)
class RhythmChangeWithoutPay:
    """A change of rhythm whose pay on one side is carried, as the salary page names it.

    Plan step **salary:X-av-3a**, ruling **R-SAL82** ("Yearly pay carries,
    said"): where the owner's rhythm changes and no entry is recorded from the
    new rhythm's first paycheck, the pay from then is the yearly pay carried
    across the change -- the pay before it times the old count, over the new
    count -- and the salary page says so rather than believing the figure
    silently.

    **And a rhythm before the first entry, when no pay is recorded at it**
    (tasks plan#3171 and plan#3575, finding plan#1586): every paycheck before
    the first entry is priced from it (ruling **R-SAL59**), so when the first
    entry is dated after a change of count, the paychecks of each rhythm
    before it paid at another count are that entry's yearly pay carried BACK
    to their count, and the page names the change ending each such rhythm:
    :attr:`before_the_change`.

    Attributes:
        first_payday: The payday of the first paycheck at the new rhythm
            (:func:`~app.services.pay_calendar.first_paycheck_at`): the
            change.
        per_paycheck: The carried pay: at the new rhythm, in force from the
            first day its first paycheck pays for; or, before the change, at
            the rhythm before it, in force on the last day before it.
        carried_annual: The yearly pay carried across: the pay just before
            the change times the paychecks a year it was paid at; before the
            change, the first entry's yearly pay.
        before_the_change: ``True`` when the carried pay is the rhythm's
            before the change, carried back from the first entry; ``False``
            when it is the new rhythm's, from the change on.
    """

    first_payday: date
    per_paycheck: Decimal
    carried_annual: Decimal
    before_the_change: bool = False


@dataclass(frozen=True)
class _Seam:
    """One change of rhythm as the walk reads it: an era after the first.

    Private to :class:`PayrollBasis`.

    Attributes:
        payday: The payday of the era's first paycheck
            (:func:`~app.services.pay_calendar.first_paycheck_at`).
        first: The first day that paycheck pays for: the change takes effect
            from it, so every day one paycheck pays for is paid at that
            paycheck's rhythm and a pay period never straddles one.
        index: The era's index in the calendar's eras.
    """

    payday: date
    first: date
    index: int


@dataclass(frozen=True)
class _Entry:
    """One pay entry as the walk reads it.

    Private to :class:`PayrollBasis`.

    Attributes:
        opens: The first day the entry's paycheck pays for: the entry is the
            pay in force from then (ruling **plan#3091**).
        payday: The entry's payday.
        amount: What one paycheck pays, as recorded.
    """

    opens: date
    payday: date
    amount: Decimal


@dataclass(frozen=True)
class _InForce:
    """The pay in force on one day, and what the walk to it read.

    Private to :class:`PayrollBasis`.

    Attributes:
        rate: The day's :class:`PayRate`.
        entry: The entry the pay was walked from.
        carried: ``(day, yearly pay)`` of the LAST change of count the walk
            carried the pay across, or ``None`` when it crossed none.
        raised: ``(landing day, raise)`` of every forecast application the
            walk raised the pay by, in the order it applied them: what a
            paycheck's banner names (:meth:`PayrollBasis.banner_on`).
    """

    rate: PayRate
    entry: _Entry
    carried: "tuple[date, Decimal] | None"
    raised: "tuple[tuple[date, RaiseTerms], ...]"


def _carry(pay: Decimal, paid_at: PayCadence, to: PayCadence) -> Decimal:
    """Return *pay* at rhythm *paid_at* carried to rhythm *to*, keeping its yearly pay.

    Ruling **R-SAL82**: at a change of rhythm with no pay recorded from it,
    the paycheck becomes the yearly pay (pay x paychecks a year) over the new
    count, rounded to the cent.

    Args:
        pay: The per-paycheck pay before the change, cent-exact.
        paid_at: The rhythm it was paid at.
        to: The rhythm it is carried to.

    Returns:
        The carried pay, quantized to the cent.
    """
    return round_money(PayRate(pay, paid_at).annual / to.periods_per_year)


def _at_rhythm(rate: PayRate, rhythm: PayCadence) -> PayRate:
    """Return *rate* paid at *rhythm*: carried across a change of count, else as it is.

    "A change of rhythm" is a change of COUNT, the test the walk's own carry
    turns on: a new phase paying as often keeps the pay.

    Args:
        rate: The rate to restate.
        rhythm: The rhythm to state it at.

    Returns:
        The :class:`PayRate` at *rhythm*.
    """
    if rate.periods_per_year == rhythm.periods_per_year:
        return PayRate(rate.per_paycheck, rhythm)
    return PayRate(_carry(rate.per_paycheck, rate.cadence, rhythm), rhythm)


def _priced_days(paid: PaidDays) -> "list[date]":
    """Return the days a paycheck's base is priced over: its pay period's weekdays.

    Ruling **plan#3089**: a paycheck pays its pay period's Monday-to-Friday
    days, a holiday or a vacation day included (the 07-16 stub paid 07-03, a
    holiday, at the new pay), each at the pay in force that day.  **A period
    holding no weekday is priced on its last day**, at the pay in force then:
    only a rhythm of one or two days can hold such a period, a weekend, and
    with no weekday to share the pay over it pays its rate, as a paycheck no
    change reaches does.  The pricing (:meth:`PayrollBasis.base_pay_on`) and
    the banner (:meth:`PayrollBasis.banner_on`) both read these days, so a
    raise is announced on the paycheck that first PAYS a day of it (task
    plan#3122).

    Args:
        paid: The paycheck's pay period.

    Returns:
        The days, ascending; never empty.
    """
    span = (paid.last - paid.first).days + 1
    weekdays = [
        day for day in (paid.first + timedelta(days=offset) for offset in range(span))
        if day.weekday() < SATURDAY
    ]
    return weekdays or [paid.last]
