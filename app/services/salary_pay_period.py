"""Shekel Budget App -- a job's pay periods, as its form asks and shows them.

Plan step **salary plan#3048** (task plan#3099), ruling **plan#3096**: a job
learns its pay periods from one stub's Pay Date and Period End.  The owner
copies the two dates off any one stub; the job keeps one fact,
``salary.salary_profiles.period_end_days_before_payday``, how many days each
period ends before the payday payroll SCHEDULED.  This module is the form's
two directions over that fact:

* :func:`days_before_payday` turns the two dates INTO it, through the
  calendar's match of a paid day to its planned one
  (:func:`~app.services.pay_calendar.scheduled_payday`), so dates copied from
  a holiday payday's stub still give the gap every other paycheck has;
* :func:`pay_period_view` shows it BACK as dates: the latest payday and its
  period end, which re-save to the same gap, and the next paychecks with the
  days each pays for (:func:`~app.services.pay_calendar.days_paid_for`, the
  one producer of a paycheck's pay period).

Flask-free, and no query of its own: everything is asked of the owner's
:class:`~app.services.pay_calendar.PayCalendar`, which the route's
:class:`~app.services.balance_at.BalanceContext` derives.
"""

from dataclasses import dataclass
from datetime import date
from itertools import islice

from app.exceptions import ValidationError
from app.models.salary_profile import PERIOD_END_DAYS_BEFORE_PAYDAY_MAX
from app.services.pay_calendar import (
    PaidDays,
    PayCalendar,
    days_paid_for,
    paychecks_from,
    scheduled_payday,
)
from app.services.salary_paydays import not_a_payday

#: How many of the owner's next paychecks the job's page lists with the days
#: each pays for: enough to see the rhythm, and past a holiday the next one.
UPCOMING_PAYCHECKS_SHOWN = 3


def days_before_payday(
    calendar: PayCalendar, pay_date: "date | None", period_end: "date | None",
) -> int:
    """Return how many days the period ends before its SCHEDULED payday.

    Both dates are needed: the edit form leaves the stored gap alone when
    neither is posted, and one without the other states no gap.  The Pay Date
    must be a payday of the owner's calendar (the salary doors'
    one rule, :func:`~app.services.salary_paydays.not_a_payday`).  The Period
    End may not be after the Pay Date: one gap cannot state a period paid
    before it ends (a month-anchored period paid on the 25th).  Nor after the
    payday the paycheck was SCHEDULED for, which is later than the Pay Date
    only when a payday convention moved the payday later; and it may not be
    more than :data:`~app.models.salary_profile.PERIOD_END_DAYS_BEFORE_PAYDAY_MAX`
    days before it, which is a mistyped year.

    Worked example (the developer's 07-16 stub): Pay Date 2026-07-16, a
    scheduled Thursday nothing moved, and Period End 2026-07-10 give
    ``16 - 10 = 6``.  The Thanksgiving 2030 stub, Pay Date Wednesday 11-27
    (paid early for the holiday, scheduled Thursday 11-28) and Period End
    Friday 11-22, gives ``28 - 22 = 6`` too, where counting from the paid day
    would give 5 and shift every other paycheck's period by a day.

    Args:
        calendar: The owner's calendar.
        pay_date: The stub's Pay Date, or ``None`` when not posted.
        period_end: The stub's Period End, or ``None`` when not posted.

    Returns:
        The days, within the column's range.

    Raises:
        ValidationError: In words, for each refusal above.
    """
    if pay_date is None or period_end is None:
        raise ValidationError(
            "Copy both the Pay Date and the Period End from one pay stub."
        )
    refusal = not_a_payday(calendar, pay_date)
    if refusal is not None:
        raise ValidationError(refusal)
    if period_end > pay_date:
        raise ValidationError(
            f"Period End {period_end.isoformat()} is after the Pay Date "
            f"{pay_date.isoformat()}.  Copy both dates from one pay stub: a "
            f"paycheck pays for days that have ended by its payday."
        )
    scheduled = scheduled_payday(calendar, pay_date)
    if period_end > scheduled:
        raise ValidationError(
            f"Period End {period_end.isoformat()} is after "
            f"{scheduled.isoformat()}, the day your {pay_date.isoformat()} "
            f"paycheck was scheduled for before it moved to a business day.  "
            f"Copy both dates from one pay stub."
        )
    days = (scheduled - period_end).days
    if days > PERIOD_END_DAYS_BEFORE_PAYDAY_MAX:
        raise ValidationError(
            f"Period End {period_end.isoformat()} is {days} days before the "
            f"{pay_date.isoformat()} payday, and a pay period ends at most "
            f"{PERIOD_END_DAYS_BEFORE_PAYDAY_MAX} days before it.  Check the "
            f"year on both dates."
        )
    return days


@dataclass(frozen=True)
class UpcomingPaycheck:
    """One of the next paychecks, with the days it pays for.

    Attributes:
        payday: The day it is paid.
        paid_days: The days it pays wages for.
    """

    payday: date
    paid_days: PaidDays


@dataclass(frozen=True)
class PayPeriodView:
    """A job's pay-period setting as its page shows it.

    Attributes:
        pay_date: The latest payday on or before today (the first payday
            when today is before it), or ``None`` for a calendar with no
            payday.
        period_end: That paycheck's period end, so the two dates re-save to
            the stored gap; ``None`` beside a ``None`` pay date.
        upcoming: The next :data:`UPCOMING_PAYCHECKS_SHOWN` paychecks, paid
            on or after today, each with the days it pays for.
    """

    pay_date: "date | None"
    period_end: "date | None"
    upcoming: "tuple[UpcomingPaycheck, ...]"


def pay_period_view(
    calendar: PayCalendar, ends_days_before: int, today: date,
) -> PayPeriodView:
    """Return the job's setting shown back as dates, and its next paychecks.

    Args:
        calendar: The owner's calendar.
        ends_days_before: The job's ``period_end_days_before_payday``.
        today: The render's pinned day.

    Returns:
        The :class:`PayPeriodView`.
    """
    latest = calendar.span_containing(today)
    if latest is None and calendar.periods:
        latest = calendar.periods[0]
    upcoming = tuple(
        UpcomingPaycheck(
            payday=paycheck.start_date,
            paid_days=days_paid_for(calendar, paycheck.start_date, ends_days_before),
        )
        for paycheck in islice(
            (p for p in paychecks_from(calendar, today) if p.start_date >= today),
            UPCOMING_PAYCHECKS_SHOWN,
        )
    )
    if latest is None:
        return PayPeriodView(pay_date=None, period_end=None, upcoming=upcoming)
    return PayPeriodView(
        pay_date=latest.start_date,
        period_end=days_paid_for(
            calendar, latest.start_date, ends_days_before,
        ).last,
        upcoming=upcoming,
    )


__all__ = [
    "PayPeriodView",
    "UPCOMING_PAYCHECKS_SHOWN",
    "UpcomingPaycheck",
    "days_before_payday",
    "pay_period_view",
]
