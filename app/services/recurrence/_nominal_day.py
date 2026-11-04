"""The NOMINAL DAY: the 0-or-1 day a short month clamped, and its pair rule.

**Split out of** :mod:`._resolution` **at plan step salary:R15-a** as a PURE
MOVE, when the per-month ceiling's pair refusal took that module past pylint's
1000-line ceiling; every definition below is byte-for-byte what it held,
graded by AST in the same commit.  ``_resolution`` keeps the two value types
and :func:`~._resolution.resolve`; this leaf keeps the one fact those values
carry that needs a calendar arithmetic of its own.

``nominal_day`` records the day a rule MEANS when ``starts_on``'s own month was
too short to hold it -- April has no 31st, so a day-31 rule first occurring
there carries ``starts_on = 2026-04-30`` and ``nominal_day = 31`` (ruling
**R-R3**).  What lives here: the domain the column's CHECK bounds it to, the
join of the two fields into the day a rule fires on
(:func:`cadence_day_of_month`), which days a first occurrence leaves OPEN
(:func:`offerable_nominal_days`, :func:`is_offerable_nominal_day`), and the
refusal both value types hold at construction
(:func:`_require_nominal_day_pair`).

**The dependency runs ONE way**: this module imports :mod:`._frequency` and
nothing of :mod:`._resolution`, which imports it.

Pure: no Flask, no ORM, no clock, no database.
"""
import calendar as calendar_module
from datetime import date

from app.enums import RecurrenceUnitEnum
from app.services.recurrence._frequency import (
    RecurrenceResolutionError,
    has_day_of_month_coordinate,
)

#: The domain ``ck_recurrence_rules_nominal_day`` bounds its column to, and the
#: reason it is 29-31 rather than 1-31: a nominal day at or below the day
#: ``starts_on`` already carries would be a SECOND statement of that day, which
#: is the two-representations defect ruling R-R16 removes.
_NOMINAL_DAY_MIN = 29
_NOMINAL_DAY_MAX = 31


def _last_day_of_month(day: date) -> int:
    """Return the last day of *day*'s own month.

    Args:
        day: Any date.

    Returns:
        28, 29, 30 or 31.
    """
    return calendar_module.monthrange(day.year, day.month)[1]


def cadence_day_of_month(
    unit: RecurrenceUnitEnum, starts_on: date, nominal_day: int | None,
) -> int | None:
    """Return the day of the month a cadence fires on, from the PAIR.

    **The ONE reader of ``(starts_on, nominal_day)``**, which is one fact stored
    in two fields: the date holds the day unless its own month was too short to
    hold it, in which case *nominal_day* holds what the rule meant and the date
    holds the clamp (ruling R-R3).  The occurrence walk, the display describer
    and the generated row's due date all need that day, and writing the join
    three times is how the same rule comes to fire on the 31st and read as the
    30th.

    **It was a property of :class:`ResolvedRecurrence` alone until plan step
    R7c-c**, and it became a function because a second caller appeared that
    holds the pair without holding a resolved value: ``_reading``'s
    ``scheduling_day_of_month``, which answers what the dropped ``day_of_month``
    column held for ``recurrence.compute_due_date``.  Resolving a rule
    there would have required a calendar the pure ``compute_due_date`` does not
    take; open-coding the join is what this function exists to prevent.  The
    property remains, delegating here, so no consumer has to change.

    ``is None``, not truthiness: *nominal_day*'s domain is 29-31, but a
    falsy-day bug here would silently re-clamp every later month.

    Args:
        unit: The cadence unit.
        starts_on: The rule's first occurrence.
        nominal_day: The day the rule means when *starts_on*'s own month was
            too short to hold it, and ``None`` when the date holds it.

    Returns:
        The day 1-31 the rule means, month-end clamped per month by the walk
        itself -- or ``None`` for a unit that does not fire on a day of the
        month (:func:`~app.services.recurrence.has_day_of_month_coordinate`).
        ``None`` is absence rather than a
        missing value: a paycheck-space or weekly rule has no day-of-month to
        name, and answering the date's own day would invent a coordinate the
        cadence never uses.
    """
    if not has_day_of_month_coordinate(unit):
        return None
    if nominal_day is None:
        return starts_on.day
    return nominal_day


def offerable_nominal_days(
    unit: RecurrenceUnitEnum, starts_on: date,
) -> tuple[int, ...]:
    """Return the nominal days *starts_on* leaves open, largest last.

    **What the form's "Repeats on" control offers**, and the ONE producer of
    it: the set is exactly the values :func:`_require_nominal_day_pair` admits
    beside this date, so a control built from it cannot offer a pair the write
    door, the spec or ``ck_recurrence_rules_nominal_day`` would refuse.  That is
    the property plan step R7b-2 gave the cadence controls by serving them from
    the encoder's own table, applied to the day.

    **Empty for all but a handful of dates**, which is what keeps "one date
    authors the cadence" true in the ordinary case.  A date is ambiguous only
    when it is its own month's LAST day and that month is shorter than 31 days:
    ``2026-04-30`` could mean "the 30th" or "the 31st / the last day of the
    month", and those are different cadences from May onwards.  Every other
    date says its day and nothing else -- including the 31st of a 31-day month,
    which already IS the last-day idiom because the walk clamps it.

    Args:
        unit: The cadence unit.  A cadence not measured in whole months has no
            day-of-month coordinate at all, so it offers nothing.
        starts_on: The rule's first occurrence.

    Returns:
        The offerable days in ascending order -- ``(31,)`` for an April 30th,
        ``(29, 30, 31)`` for a common-year February 28th, and ``()`` for every
        unambiguous date.
    """
    if not has_day_of_month_coordinate(unit):
        return ()
    if starts_on.day != _last_day_of_month(starts_on):
        return ()
    return tuple(
        day
        for day in range(_NOMINAL_DAY_MIN, _NOMINAL_DAY_MAX + 1)
        if day > starts_on.day
    )


def is_offerable_nominal_day(
    unit: RecurrenceUnitEnum, starts_on: date, nominal_day: int | None,
) -> bool:
    """Return whether the pair is consistent, WITHOUT raising.

    :func:`_require_nominal_day_pair`'s question asked by a validator rather
    than by a write door, the same split
    :func:`~app.services.recurrence.is_authorable` records for the cadence: the
    door raises because reaching it with a contradictory pair is a broken
    invariant, while a SUBMISSION carrying one is bad input to refuse with a
    field error naming the control.  Built on the same set, so the schema and
    the door cannot disagree about it.

    Args:
        unit: The cadence unit.
        starts_on: The rule's first occurrence.
        nominal_day: The submitted nominal day, or ``None``.

    Returns:
        ``True`` when the pair is one the table can hold.
    """
    if nominal_day is None:
        return True
    return nominal_day in offerable_nominal_days(unit, starts_on)


def _require_nominal_day_pair(
    unit: RecurrenceUnitEnum,
    starts_on: date,
    nominal_day: int | None,
    *,
    where: str,
) -> None:
    """Refuse a ``(starts_on, nominal_day)`` pair that contradicts itself.

    **The one statement of the invariant, and since plan step R7c-b it is held
    at CONSTRUCTION rather than checked before a walk.**  ``nominal_day``
    records the day a rule MEANS when ``starts_on``'s own month was too short to
    hold it -- April has no 31st, so a day-31 rule first occurring there carries
    ``starts_on = 2026-04-30`` and ``nominal_day = 31`` (ruling R-R3).  Two
    fields, one fact, and a fact stated twice needs something to keep the
    statements in step.

    Until this step that something was a GUARD run at generation time
    (``_occurrence._require_generable``), backed by a CHECK that could not
    express the whole rule: ``ck_recurrence_rules_nominal_day`` bounded the
    domain and required the nominal day to exceed the date's, which admits
    ``(2026-04-15, 30)`` -- a nominal day beside a date that was never clamped.
    R7c-b completes the CHECK with the clamp equality below and moves the
    in-memory half here, so both values that carry the pair
    (:class:`RecurrenceSpec` and :class:`ResolvedRecurrence`) refuse it before
    they exist.  There is no state left for a generation-time fence to catch.
    The unit half of the rule reached the table at plan step plan#2302
    (``ck_recurrence_rules_nominal_day_unit``); this answers in front of it.

    **Membership in :func:`offerable_nominal_days`, and not a second list of
    conditions.**  That function IS the rule -- the cadence must fire on a day
    of the month, the date must be its month's last day, and the value must
    exceed it and stay inside 29-31 -- so stating the conditions again here
    would be the two-hand-written-sets shape this package removes elsewhere.
    The refusal NAMES the admissible set instead, which is more actionable than
    naming whichever branch happened to fail.

    Args:
        unit: The cadence unit.
        starts_on: The rule's first occurrence.
        nominal_day: The day the rule means, or ``None`` when the date holds it.
        where: What to name in the refusal, composed by the caller because only
            the caller knows which value is being built.

    Raises:
        RecurrenceResolutionError: When the pair contradicts itself.
    """
    if is_offerable_nominal_day(unit, starts_on, nominal_day):
        return
    offerable = offerable_nominal_days(unit, starts_on)
    admissible = (
        f"the only days it leaves open are {list(offerable)}" if offerable
        else "that date leaves no day open -- either the cadence has no "
             "day-of-month coordinate, or the date is not its own month's "
             "last day, so it already states the day the rule fires on"
    )
    raise RecurrenceResolutionError(
        f"recurrence nominal_day {nominal_day} cannot sit beside a first "
        f"occurrence of {starts_on} on a {unit!r} cadence for {where}: "
        f"{admissible}.  A nominal day records a day the first occurrence's "
        f"month CLAMPED (ruling R-R3), so any other value would be a second "
        f"statement of the day starts_on already carries, or a day the rule "
        f"does not fire on.  Mirrors ck_recurrence_rules_nominal_day."
    )


__all__ = [
    "cadence_day_of_month",
    "is_offerable_nominal_day",
    "offerable_nominal_days",
]
