"""
Shekel Budget App -- Calendar-month arithmetic, in ONE place

A monthly cadence walks ABSOLUTE month ordinals rather than adding months to a
date, and clamps the day it means to each month's length.  Two callers need
that walk and they must not disagree:

* :func:`app.services.recurrence._resolution._calendar_anchor` takes the first
  occurrence on or after a rule's opening bound;
* :func:`app.services.recurrence._occurrence.occurrences` takes the whole
  sequence from that anchor forward.

They ARE the same walk seeded differently -- the anchor is its first element --
and until plan step R3 they were two copies of the same arithmetic in two
modules, with ``12`` spelled twice and a test standing in for the thing they
share.  A neutral review named it as the one place this arc's own thesis (one
producer, no second copy to disagree with the first) was not being followed.
So the walk lives here and both call it.

Why ordinals and not ``date`` arithmetic
----------------------------------------

Adding a month to a date has to answer "what is 31 January plus one month",
and every answer that returns a DATE loses the day: 28 February plus one month
is 28 March, and the 31st never comes back.  Walking ordinals keeps the
NOMINAL day and re-clamps it per month, so a day-31 rule is 31 Jan, 28 Feb,
31 Mar -- the last day of every month, which is the only reading that
reproduces the reverse matcher plan step R4a replaced (it clamped with
``min(day, monthrange(...))`` per period) and the only one that does not decay
(ruling R-R3).

**The two ordinal primitives are DECLARED in** :mod:`app.utils.dates`
**and re-exported here** (plan step ``pay_calendar:C17-d-2``).
:func:`month_ordinal` and :func:`clamped_day` were born in this module for
the walk below; the pay calendar's day-of-month kinds
(:mod:`app.services.pay_calendar._grid`) step the same ordinals and clamp
the same meant day, and that package can import neither a private module of
this one nor this package (which imports ``pay_calendar`` itself).  Rule 14
says move the shared leaf rather than spell it twice, so the pair lives in
the date-utility leaf below both, and every reader of this module's names
-- ``_occurrence``, ``_frequency``, ``_resolution`` -- is unchanged.

**Which units are measured in months, and how many, is NOT stated here** since
plan step plan#2302 (ruling plan#2748).  It was: a ``_MONTHS_PER_UNIT`` table
(month 1, year 12) and the ``MONTH_SPANNING_UNITS`` tuple taken from its keys,
the one statement every refusal of a unit pairing derived from.  A CHECK on
``budget.recurrence_rules`` cannot read a code table, so that step moved the
fact onto the unit's reference row (``ref.recurrence_units.months_per_unit``),
where the database GENERATES the day-of-month flag from it, and
:func:`months_per_step` reads the row through :mod:`app.ref_cache`.

No Flask, no ORM, no clock, and no database read: the unit's span comes from
the reference cache, loaded once per process.
"""
from collections.abc import Callable, Iterator
from datetime import date

from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.exceptions import ShekelError
from app.utils.dates import (
    CALENDAR_DATE_MAX,
    clamped_day,
    month_on_or_before,
    month_ordinal,
)


class MonthStepError(ShekelError):
    """A cadence unit has no reading in months.

    A broken invariant rather than user input: :func:`months_per_step` is asked
    only for a cadence its caller has already routed to the calendar family, so
    a unit arriving here without a month span is a caller that routed a
    paycheck or week cadence without asking the unit's day-of-month flag,
    which the database generates from the same span (ruling plan#2748).

    **A :class:`~app.exceptions.ShekelError`, and an adversarial review of plan
    step R7b-1 is why.**  It was a bare ``ValueError``, which put it outside the
    hierarchy every other refusal in this package raises into
    (``RecurrenceResolutionError``, ``RecurrenceGenerationError``,
    ``RecurrenceFrequencyError``) -- so a handler written against that hierarchy
    would not have caught it.  Both callers also CONVERT it to their own
    module's error, because each states a different contract about what could
    not be answered; the base class is what makes a third caller that forgets
    to convert still fail inside the hierarchy rather than outside it.
    """


def months_per_step(unit: RecurrenceUnitEnum, interval_n: int) -> int:
    """Return the month stride a ``(interval_n, unit)`` cadence walks in.

    **The ONE producer of that stride, and it had two.**  The anchor derivation
    (``_resolution._calendar_anchor``) took a ``month_step`` off the pattern
    table while the occurrence walk (``_occurrence._unbounded``) computed
    ``interval_n * MONTHS_PER_YEAR`` for the YEAR unit itself -- two spellings
    of one fact, in the two places whose own docstrings claim to be "the SAME
    walk seeded differently".  They agreed; nothing made them.

    Both of those callers route to it on a calendar unit and CONVERT the
    refusal below into their own module's error; see :class:`MonthStepError`.

    The span is the unit's reference row's (``ref_cache.recurrence_unit_months``,
    ruling plan#2748), and so is the day-of-month flag that routes a caller
    here: the database generates the flag from this span, so a unit that names
    a day of the month has a stride by construction.

    Args:
        unit: The cadence unit.  Must be a calendar unit.
        interval_n: How many *unit*\\ s pass between occurrences.

    Returns:
        Months between occurrences -- 3 for a quarterly cadence, 24 for every
        two years.

    Raises:
        MonthStepError: When *unit* is not measured in months.
    """
    months = ref_cache.recurrence_unit_months(unit)
    if months is None:
        raise MonthStepError(
            f"recurrence unit {unit!r} has no reading in months, so it has no "
            f"month stride.  Only the calendar units walk months; a "
            f"pay-period or weekly cadence reaching this call means the anchor "
            f"family and the occurrence walk disagree about which units are "
            f"calendar units."
        )
    return months * interval_n


def walk_months(
    start_ordinal: int, day_in: Callable[[int], date], month_step: int,
) -> Iterator[date]:
    """Yield *day_in*'s day in *start_ordinal*'s month, then every *month_step*.

    **It walks a GRID rather than a day since plan step ``recurrence:R8-c``**:
    the caller hands a month coordinate's own day
    (:meth:`~._month_coordinate.MonthCoordinate.date_in`) -- a day of the
    month clamped into each month, the nth weekday, or the last weekday.  The
    stride and the bound below are the same for all three, so one walk serves
    every shape and a "third Tuesday" rule cannot be walked on its first
    date's day of the month (plan ledger row **D29**).  It takes the function
    rather than the shape because this module sits BELOW ``_frequency``, which
    the coordinate module imports: naming the shape type here would be an
    import cycle, and a walk needs nothing of a shape but its day.

    **Bounded by the calendar this application reaches, and NOT by anything
    about the rule.**  The anchor derivation takes the first element that clears
    its bound and the occurrence engine applies the rule's own closing bounds,
    so a stopping condition drawn from the RECURRENCE would be a third opinion
    about when one ends.  The bound here is a different kind of fact: past
    :data:`~app.utils.dates.CALENDAR_DATE_MAX` there is no date this application
    can express, so there is no occurrence to name -- which is the same
    disposition ``_occurrence._bounded`` already takes for its caller's window.

    **It was unbounded until plan step R7c-c, on a claim that step falsified.**
    The docstring read "walked past year 9999 it raises ``ValueError`` from
    ``date`` rather than looping, which no caller's window can reach" -- true
    while a calendar cadence's stride was 1, 3, 6 or 12 months, because the
    closed pattern set had no name for any other.  R7c-c frees the interval, so
    ``every 10000 years`` became authorable and its SECOND occurrence overflows
    ``date``: a ``ValueError`` from outside this package's error hierarchy,
    which the recurrence preview's handler does not catch, on an endpoint that
    reads its values from ``request.args`` where no schema stands.  That is the
    same shape ``ck_recurrence_rules_starts_on_range`` was added for one leaf
    earlier, arriving through the other authored value.

    The bound is the month of the last grid day on or before
    :data:`~app.utils.dates.CALENDAR_DATE_MAX`
    (:func:`app.utils.dates.month_on_or_before`, the comparison behind the
    monthly-grid set the loan calendar, the card statement and the pay grid
    read too since plan step ``recurrence:R25``, ruling **R-R122**, stated over
    any one-day-a-month grid since ``R8-c``), read from the application's own
    calendar vocabulary rather than restated, so the walk,
    ``ck_recurrence_rules_starts_on_range`` and
    ``_resolution._require_authored_start_window`` all bound the same
    calendar.  It is applied to the ORDINAL rather than to the yielded date,
    so the overflowing ``date`` is never constructed.

    Args:
        start_ordinal: The absolute month ordinal to start from.
        day_in: The day the rule lands on in a month ordinal, inside that
            month.
        month_step: Months between occurrences.  Must be positive; both
            callers refuse a non-positive interval before walking.

    Yields:
        Occurrence dates, ascending, until the consumer stops pulling or the
        walk leaves the calendar.  A cadence whose stride carries its second
        occurrence past that point therefore fires ONCE, which is the honest
        answer rather than an error: it does fire once, and it names no second
        date the application can hold.
    """
    last_ordinal = month_on_or_before(CALENDAR_DATE_MAX, day_in)
    for ordinal in range(start_ordinal, last_ordinal + 1, month_step):
        yield day_in(ordinal)


__all__ = [
    "MonthStepError",
    "clamped_day",
    "month_ordinal",
    "months_per_step",
    "walk_months",
]
