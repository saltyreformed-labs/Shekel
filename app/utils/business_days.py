"""
Shekel Budget App -- Business-Day Calendar

The weekend rule, the computed US federal holiday set, and the ONE
displacement that moves a date off a day no money moves on.  Pure date
arithmetic: no Flask, no SQLAlchemy, no clock, so it imports cleanly into
the pay-calendar package (which is pure by design) and into the recurrence
engine alike.

**One module, two consumers, ruled that way** (``pay_calendar:R-PC47``).
The pay calendar's projection asks it where a payday really lands when the
rhythm names a holiday; ``recurrence:R8-d`` asks it the same question for a
bill's CASH date.  Two copies of "which days is a bank open" would be two
places for a holiday rule to drift, and the drift would be invisible: both
copies answer the same thing for most of any year and part company on the
handful of days the whole feature exists for.

**The holiday set is what carries this module, and a weekend rule alone
would be theatre.**  Measured 2026-09-04 against ``shekel-prod-db``, whose
owner holds 63 recorded paydays from 2026-03-26 to 2028-08-10: every one of
them is a Thursday, so no payday of his can ever fall on a Saturday or a
Sunday, and none of the 63 falls on a federal holiday either.  The rhythm
day ledger row **N-398** is about -- the 2026-01-01 paycheck payroll really
paid on 2025-12-31 -- is New Year's Day, and it is a **Thursday**.  A
weekend-only rule sees nothing there at all.

The scale, stated over the WHOLE calendar this application admits rather
than over a sampled window, because a window's edge is a number nobody
re-derives: continuing that owner's cadence forward to
:data:`~app.utils.dates.CALENDAR_DATE_MAX` gives 1,888 projected paydays of
which **64** fall on a federal holiday and none on a weekend; backward to
:data:`~app.utils.dates.CALENDAR_DATE_MIN`, 684 rhythm days of which **22**
do.  How many of the 22 an owner is exposed to depends on how far back
``budget.pay_schedule.history_opens_on`` lets the rhythm run, so no single
number states it: N-398's own measurement is against a stated opening of
2026-01-05, where the 2026-01-01 day falls below the floor and the
year-to-date correctly reads nine; state an earlier opening and it reads
ten.

**Which calendar this is, said precisely, because two defensible ones
differ.**  These are the ELEVEN federal holidays of ``5 U.S.C. 6103(a)``.
The Saturday-to-preceding-Friday rule is ``5 U.S.C. 6103(b)``; the
Sunday-to-following-Monday one is E.O. 11582 -- two sources, not one, and
:func:`_observed` applies both.  Together they are the calendar
``pay_calendar:R-PC47`` names in the words "computed federal holiday set".

The FEDERAL RESERVE's calendar is not identical to it: the Fed does not
observe a Saturday holiday on the preceding Friday, so banks settle ACH on
a day federal offices are closed.  Since a direct deposit rides ACH, that
is arguably the more accurate calendar for a payday, and it is an open
question for a later leaf rather than a settled one.  Measured 2026-09-04
over 2000-2100: the two calendars differ on **71** days, every one of them
a Friday, and **no** Thursday payday lands differently under either
convention.  That second figure is an ENUMERATION and not a deduction -- a
Friday divergence CAN in principle reach a Thursday payday, since ``NEXT``
from a Thursday holiday lands on the Friday; it happens not to, and an
earlier draft of this paragraph argued from the weekday alone and was a non
sequitur.  Changing calendars is a change to :func:`_observed` and to
nothing else, which is the whole argument for one module.

Inauguration Day is deliberately absent.  It is a federal holiday for
federal employees in the District of Columbia area only, banks do not
close, and payroll does not move for it.
"""
from datetime import date, timedelta
from functools import cache

from app.enums import BusinessDayShiftEnum
from app.utils.dates import (
    CALENDAR_DATE_MAX,
    CALENDAR_DATE_MIN,
    last_weekday,
    nth_weekday,
)

# ``datetime.date.weekday()`` indices, named because the arithmetic below
# reads as nonsense against bare integers: a holiday that is "the fourth
# ``3`` of November" is a line no reviewer can grade.
MONDAY = 0
THURSDAY = 3
SATURDAY = 5
SUNDAY = 6

# Juneteenth National Independence Day became a federal holiday on
# 2021-06-17, so it is NOT one for any earlier year.  The bound matters
# because this application's calendar opens at
# ``app.utils.dates.CALENDAR_DATE_MIN`` (2000-01-01): 21 of the years a
# user may put on record predate the holiday, and emitting it for them
# would move a payday that payroll never moved.
JUNETEENTH_FIRST_YEAR = 2021


def _observed(day: date) -> date:
    """Return the day a FIXED-DATE federal holiday is observed on.

    TWO rules from two sources, not one: a holiday falling on a Saturday is
    observed the preceding Friday (``5 U.S.C. 6103(b)``), one falling on a
    Sunday the following Monday (E.O. 11582).  Applied to the five
    fixed-date holidays only -- the six weekday-anchored ones (Martin
    Luther King, Jr. Day and the rest) can never land on a weekend, so
    asking this of them would be a branch no input reaches.

    **This function is the whole of the Federal Reserve divergence** the
    module docstring names: the Fed applies the Sunday rule and not the
    Saturday one, which is why the two calendars differ on 71 days over
    2000-2100 and every one of them is a Friday.

    Args:
        day: The holiday's statutory date.

    Returns:
        The date it is observed on, which is *day* itself from Monday to
        Friday.
    """
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == SUNDAY:
        return day + timedelta(days=1)
    return day


@cache
def federal_holidays(year: int) -> "frozenset[date]":
    """Return every federal holiday OBSERVED within a calendar year.

    Observed rather than statutory, and every returned date falls inside
    *year*, which is what makes :func:`is_business_day` able to answer from
    a single year's set.  The two statements are the same rule read from
    both ends: 2028-01-01 is a Saturday, so New Year's Day 2028 is observed
    on **2027**-12-31 and belongs to that year's answer and not to 2028's.

    Cached because it is a pure function of one small integer and the
    projection asks it once per candidate payday, over a horizon far wider
    than any one screen: the production owner's forward walk to
    ``CALENDAR_DATE_MAX`` asks 1,888 times and wants 73 distinct years, and
    the backward one asks 684 more for 27.  The key is bounded by ``date``'s own
    year domain, so the memo cannot grow without bound, and the value is a
    frozenset, so no caller can corrupt another's answer.

    Args:
        year: Calendar year.

    Returns:
        The observed holidays, every one of them dated within *year*.
    """
    statutory = [
        date(year, 1, 1),                          # New Year's Day
        date(year, 7, 4),                          # Independence Day
        date(year, 11, 11),                        # Veterans Day
        date(year, 12, 25),                        # Christmas Day
    ]
    if year >= JUNETEENTH_FIRST_YEAR:
        statutory.append(date(year, 6, 19))        # Juneteenth
    observed = {
        day for day in map(_observed, statutory) if day.year == year
    }
    # New Year's Day of the FOLLOWING year, when the weekend rule pulls its
    # observance back across the boundary into this one.  Without this the
    # last working Friday of such a year reads as an ordinary business day.
    spillover = _observed(date(year + 1, 1, 1))
    if spillover.year == year:
        observed.add(spillover)
    observed.update({
        nth_weekday(year, 1, MONDAY, 3),          # Martin Luther King, Jr.
        nth_weekday(year, 2, MONDAY, 3),          # Washington's Birthday
        last_weekday(year, 5, MONDAY),            # Memorial Day
        nth_weekday(year, 9, MONDAY, 1),          # Labor Day
        nth_weekday(year, 10, MONDAY, 2),         # Columbus Day
        nth_weekday(year, 11, THURSDAY, 4),       # Thanksgiving Day
    })
    return frozenset(observed)


def is_business_day(day: date) -> bool:
    """Return whether money moves on *day*.

    Args:
        day: A calendar day within
            :data:`~app.utils.dates.CALENDAR_DATE_MIN`..:data:`~app.utils.dates.CALENDAR_DATE_MAX`.
            Not "any" day: :func:`federal_holidays` reads the FOLLOWING
            year for the New Year spillover, so ``date.max`` raises out of
            ``datetime`` rather than answering.  No in-app caller can reach
            that, and the bound is stated rather than guarded because a
            guard here would be a fence around a state no door admits.

    Returns:
        ``True`` for a weekday that is not an observed federal holiday.
    """
    return day.weekday() < SATURDAY and day not in federal_holidays(day.year)


def shift_to_business_day(day: date, shift: BusinessDayShiftEnum) -> date:
    """Return where *day* really lands under a shift convention.

    **The ONE displacement**, and the reason this module exists as a shared
    one.  A rhythm names a nominal day; an employer's convention says what
    happens when that day is not a business day, and a bill's cash date
    asks the identical question (``pay_calendar:R-PC47``).

    **It is a DISPLACEMENT of one day and knows nothing about a cadence.**
    The caller keeps its own progression running on NOMINAL days and asks
    this of each one independently.  Feeding the answer back into the
    progression is what would move every later payday by a day,
    permanently, and nothing here can prevent that -- the discriminating
    code is the caller's, so the guarantee belongs to the leaf that adds
    one.  What this function offers toward it is only that the landing is
    always a business day, hence a fixed point: applying it twice is
    applying it once.  *An earlier draft called that fixed point "the
    non-compounding guarantee", which it is not: a caller that re-anchors
    its cadence on the output compounds while every displacement here stays
    perfectly idempotent.*

    Args:
        day: The nominal day, which need not be a business day.
        shift: The owner's convention.  :attr:`BusinessDayShiftEnum.NONE`
            returns *day* untouched.

    Returns:
        *day* when it is already a business day or the convention is
        ``NONE``; otherwise the nearest business day in the convention's
        direction.

        **The answer may fall OUTSIDE
        :data:`~app.utils.dates.CALENDAR_DATE_MIN`..:data:`~app.utils.dates.CALENDAR_DATE_MAX`,
        and bounding it is the caller's.**  Both ends of the application's
        calendar are non-business days -- 2000-01-01 is a Saturday and
        2100-12-31 is the observed New Year's Day 2101 -- so
        ``PRIOR`` from the floor answers 1999-12-30 and ``NEXT`` from the
        ceiling answers 2101-01-03.  This module answers the arithmetic
        question it was asked; the columns that persist a date
        (``ck_pay_schedule_history_opens_range`` and its two siblings)
        state the bound, and the leaf that writes one decides whether an
        escaping date is clamped or refused.

    Raises:
        ValueError: *shift* is not a :class:`BusinessDayShiftEnum` member.
            Dispatching on PRIOR and treating everything else as NEXT
            would give an unrecognised value a money-moving default, which
            is the shape of a set defined by subtraction: the residue
            claims members nobody censused.
    """
    if shift is BusinessDayShiftEnum.NONE or is_business_day(day):
        return day
    if shift is BusinessDayShiftEnum.PRIOR:
        step = timedelta(days=-1)
    elif shift is BusinessDayShiftEnum.NEXT:
        step = timedelta(days=1)
    else:
        raise ValueError(
            f"unhandled business-day shift {shift!r}: this function moves "
            f"money dates, so an unrecognised convention is refused rather "
            f"than given the behaviour of whichever arm happens to be last."
        )
    landing = day + step
    while not is_business_day(landing):
        landing += step
    return landing


def earliest_nominal_paid_after(day: date, shift: BusinessDayShiftEnum) -> date:
    """Return the first NOMINAL day whose payday falls strictly after *day*.

    :func:`shift_to_business_day`'s INVERSE in the one direction a caller needs
    it, and it is here rather than at that caller because it reads the same
    holiday set: computed anywhere else it would be a second reader of the
    calendar this module exists to own.

    **Its caller is a refusal that has to name a payday**
    (``registration_service._reject_impossible_first_payday``, plan step
    ``pay_calendar:C14-e-3``).  Registration accepts a stated payday whose
    paycheck still covers today, which is
    ``projected_payday(first_payday, rhythm, 1) > today`` -- and a form must
    say what WOULD be accepted, not only that this was not.  The day it
    names is the last grid day on the owner's stated rhythm strictly before
    this answer (ruling **R-PC83**, plan step ``pay_calendar:C17-d-2``);
    until that step it named this answer less ``cadence_days``, the earliest
    such day on a fixed-days grid, which under
    :attr:`~app.enums.BusinessDayShiftEnum.NONE` collapsed to the
    ``today - cadence_days + 1`` the rule once stated outright.

    **A SCAN rather than a formula, because the closed form is three formulas.**
    Under ``NONE`` the answer is ``day + 1``; under ``NEXT`` it is one past the
    last business day at or before *day*, which can be EARLIER than ``day +
    1``; under ``PRIOR`` it is the first business day after *day*, which can be
    later.  Branching on the member here would put the convention's meaning in
    a second place, which is exactly what
    :func:`shift_to_business_day`'s refusal of a non-member exists to prevent.
    So this asks that function instead, and the scan is what makes it one
    reader.

    **It terminates in at most one closed run either side**, which is a
    theorem about the same calendar :func:`shortest_collision_free_cadence`
    measures: a displacement moves a day by at most the longest closed run, so
    the first qualifying day cannot lie further than that from ``day + 1`` in
    either direction.  The scan opens a run BELOW ``day`` for the ``NEXT``
    case and is guaranteed to stop by ``day + 1 + run``.

    Args:
        day: The day the answer's payday must fall strictly after.
        shift: The owner's convention.

    Returns:
        The nominal day.  Under ``NONE`` that is always ``day + 1``.

    Raises:
        ValueError: *shift* is not a :class:`BusinessDayShiftEnum` member,
            raised by :func:`shift_to_business_day` on the first call rather
            than defaulted here.
    """
    candidate = day - timedelta(days=shortest_collision_free_cadence() - 1)
    while shift_to_business_day(candidate, shift) <= day:
        candidate += timedelta(days=1)
    return candidate


@cache
def shortest_collision_free_cadence() -> int:
    """Return the shortest cadence a displacing convention can be carried at.

    **The floor is a THEOREM about this calendar, not a measurement of it**,
    which is why it is computed here rather than written down anywhere.  Under
    :attr:`~app.enums.BusinessDayShiftEnum.NEXT`,
    :func:`shift_to_business_day` answers the first open day at or after its
    argument, so two nominal days ``d`` and ``d + cadence`` land on the SAME
    day exactly when every day from ``d`` to ``d + cadence - 1`` is closed --
    that is, exactly when the calendar holds a closed run of length
    ``cadence``.  ``PRIOR`` is the mirror image, reading backward.  So a
    collision is possible if and only if the cadence is no longer than the
    longest run of consecutive closed days, and the shortest safe cadence is
    that run plus one.  Both directions are also monotone, so an ORDER
    inversion is not a separate hazard: a pair either collides or keeps its
    order.

    It matters because ``pay_calendar._derive.derive_periods`` refuses a
    repeated payday outright -- it would place two periods on one opening day
    and give the earlier of them an end before its own start -- so a schedule
    whose cadence is under this floor would make its owner's whole calendar
    raise the moment a convention were chosen.
    :func:`app.services.pay_schedule_service.reject_shift_on_short_cadence`
    is the door that refuses that pairing, and it asks HERE for the number
    rather than holding one.

    **Nothing may hard-code the answer, and that is this function's whole
    reason.**  The value depends on the holiday set, which is not fixed: this
    module's own :data:`JUNETEENTH_FIRST_YEAR` records the set changing once
    inside the window the application admits.  A floor frozen into a CHECK
    constraint or a module constant would be a second home for a derived
    number, and the stale one would fail SILENTLY -- in the direction that
    admits a colliding schedule rather than the one that refuses a legal
    one.

    **The window is the application's own calendar, and that is exact rather
    than conservative.**  A collision needs two NOMINAL paydays, and both are
    bounded: the rhythm runs forward no further than
    :data:`~app.utils.dates.CALENDAR_DATE_MAX` and backward no further than
    ``budget.pay_schedule.history_opens_on``, which
    ``ck_pay_schedule_history_opens_range`` holds at or above
    :data:`~app.utils.dates.CALENDAR_DATE_MIN`.  So the closed run a colliding
    pair straddles lies wholly inside that window, and a run reaching past
    either end cannot be straddled by two paydays this application can hold.

    Returns:
        int -- the shortest cadence in days at which no two nominal days can
        be displaced onto one.  ``4`` for the calendar as it stands, the
        longest closed run being the three days ending 2000-01-17.

        Cached: it is a pure function of the holiday set with no arguments,
        and computing it walks every day of the application's calendar.
    """
    longest_closed_run = 0
    run = 0
    day = CALENDAR_DATE_MIN
    while day <= CALENDAR_DATE_MAX:
        run = 0 if is_business_day(day) else run + 1
        longest_closed_run = max(longest_closed_run, run)
        day += timedelta(days=1)
    return longest_closed_run + 1
