"""Which days a paycheck PAYS FOR: the employer's pay period behind a payday.

Plan step **salary plan#3048** (task plan#3099), ruling **plan#3096**: a job
learns its pay periods from one stub's Pay Date and Period End, and keeps one
fact, how many days each period ends before the payday payroll SCHEDULED.  This
module is the calendar's half of that rule; the fact itself is the job's
(``salary.salary_profiles.period_end_days_before_payday``), handed in.

**A paycheck's pay period is not the app's pay period.**  The app's period
(:class:`~._derive.DerivedPeriod`) runs from a payday to the day before the
next one: it is where money is FILED.  The employer's runs over the days the
paycheck pays wages FOR, which end some days before the payday and begin the
day after the previous paycheck's end.  Ruling **plan#3089** prices each of
those weekdays at the pay in force that day, which is why the second span has
to exist at all.

**Each period end hangs off the SCHEDULED payday, never the paid one**, so a
payday a holiday moves keeps the period it was scheduled for: Thanksgiving
2030 falls on a scheduled Thursday 11-28 that the ``prior`` convention pays on
Wednesday 11-27, and that paycheck still pays Saturday 11-09 through Friday
11-22.  The scheduled payday is the calendar's own match of a paid day to its
planned one (:func:`~._eras.matched_planned`, read on the NOMINAL grid by
:func:`~._grid.nominal_payday`), the same match every era reader places a
recorded payday by, so the two cannot disagree about which paycheck a day is.

**The periods TILE by construction.**  A paycheck's first day is the day after
its PREVIOUS paycheck's last, and that previous paycheck is the calendar's own
(:func:`previous_payday`: :meth:`~._calendar.PayCalendar.span_containing` on
the day before the payday), so no day is paid twice and none is left unpaid
between two paychecks the calendar holds.  The opening paycheck has no previous
one in the calendar; its previous is the payday the plan puts just below the
record (:func:`~._eras.grid_below_record`), the same day the backward rhythm
and "Add earlier paychecks" stand on.

**One kind of pay period it cannot state**, said so a caller does not assume
it: a period anchored to the MONTH and paid before it ends (monthly pay on the
25th for the whole month) has no fixed gap before its payday.  For that
reason the job's form refuses a Period End after the payday its paycheck was
scheduled for (:func:`~app.services.salary_pay_period.days_before_payday`).

Placed after :mod:`._calendar` in the package's one-way chain, beside
:mod:`._walks`, because it takes a whole calendar.  Pure: no session, no
clock, no Flask.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from ._calendar import PayCalendar
from ._eras import grid_below_record, matched_planned
from ._grid import nominal_payday

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class PaidDays:
    """The days one paycheck pays wages for, both ends included.

    Attributes:
        first: The day after the previous paycheck's period end.
        last: The period end: the scheduled payday less the job's gap.
    """

    first: date
    last: date


def scheduled_payday(calendar: PayCalendar, payday: date) -> date:
    """Return the day payroll SCHEDULED the paycheck paid on *payday*.

    The planned payday *payday* stands for (:func:`~._eras.matched_planned`,
    the nearest planned payday over the owner's eras), read on the NOMINAL
    grid: the grid day before any payday convention moved it onto a business
    day.  For a payday nothing moved, it is *payday* itself.

    Args:
        calendar: The owner's calendar.
        payday: A payday of *calendar*, saved or projected.

    Returns:
        The scheduled payday.
    """
    index, steps = matched_planned(calendar.eras, payday)
    era = calendar.eras[index]
    return nominal_payday(era.effective_from, era.rhythm.cadence, steps)


def previous_payday(calendar: PayCalendar, payday: date) -> date:
    """Return the payday of the paycheck just before the one paid on *payday*.

    **The one statement of "the paycheck before"** for the pay period's
    readers (task plan#3122): :func:`days_paid_for` starts a paycheck's days
    the day after this one's end, and the engine's banner reads which days
    this one priced (:meth:`~app.services.payroll_basis.PayrollBasis
    .banner_on`), so the two cannot disagree about which paycheck came
    first.  It is the calendar's own paycheck covering the day before
    *payday*; below the record, where the calendar holds none, it is the grid
    payday the plan puts just below *payday* (:func:`~._eras.grid_below_record`),
    the day the backward rhythm and "Add earlier paychecks" stand on, which
    is nominal: no convention moves a payday the calendar does not hold.

    Args:
        calendar: The owner's calendar.
        payday: A payday of *calendar*: saved, projected past the record, or
            stepped back below it.

    Returns:
        The previous paycheck's payday.
    """
    previous = calendar.span_containing(payday - _ONE_DAY)
    if previous is not None:
        return previous.start_date
    era, top = grid_below_record(calendar.eras, payday)
    return nominal_payday(era.effective_from, era.rhythm.cadence, top)


def days_paid_for(
    calendar: PayCalendar, payday: date, ends_days_before: int,
) -> PaidDays:
    """Return the days the paycheck paid on *payday* pays wages for.

    **The one producer of a paycheck's pay period** (ruling **plan#3096**):
    from the day after the previous paycheck's period end through its own, a
    period end being the scheduled payday (:func:`scheduled_payday`) less
    *ends_days_before*.

    **It takes the PAYDAY, not a period value**, since task plan#3100: the
    paycheck engine prices paydays below the record that the calendar
    counts but never makes periods of (:func:`~._rhythm
    .paydays_in_year_before`, ruling ``pay_calendar:R-PC14``), and a payday
    is the whole of what this reads.

    Args:
        calendar: The owner's calendar.
        payday: The day one of *calendar*'s paychecks is paid: saved,
            projected past the record, or stepped back below it.
        ends_days_before: The job's
            ``period_end_days_before_payday``.

    Returns:
        The :class:`PaidDays`.
    """
    gap = timedelta(days=ends_days_before)
    return PaidDays(
        first=scheduled_payday(calendar, previous_payday(calendar, payday)) - gap + _ONE_DAY,
        last=scheduled_payday(calendar, payday) - gap,
    )


__all__ = ["PaidDays", "days_paid_for", "previous_payday", "scheduled_payday"]
