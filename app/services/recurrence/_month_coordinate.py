"""The MONTH COORDINATE: which day of each month a month-measured cadence lands on.

A ``MONTH`` or ``YEAR`` cadence fires once in each month it is due, and its
first occurrence ``starts_on`` says on which day.  **How that date is READ is
the one fact the date cannot carry itself** (plan step ``recurrence:R8-c``,
rulings **plan#2668** and **plan#2670**, amending **R-R25**), so a rule states
it and nothing else:

* **by its day of the month** -- today's only reading.  ``nominal_day`` holds
  the meant day when ``starts_on``'s own month clamped it (ruling **R-R3**,
  :mod:`._nominal_day`).
* **by its weekday counted from the month's start** -- "the third Tuesday".
  The week number ``(day - 1) // 7 + 1`` and the weekday both come from
  ``starts_on``, so neither is stored and neither can disagree with the date
  (``CLAUDE.md`` rule 14).
* **as the month's LAST such weekday** -- "the last Friday", which is the 4th
  in some months and the 5th in others, and differs from "the fourth Friday"
  about five months a year (Jan, Apr, Jul, Oct and Dec in 2027).

**There is no fifth weekday** (plan#2670): every month holds a 1st through 4th
of every weekday and only some hold a 5th, so the from-start reading is offered
only for a date on the 28th or earlier and a 29th-31st date reads as its day or
as the last weekday.  Every weekday rule therefore fires once in each month it
is due, and :class:`~app.services.recurrence.Cadence`'s yearly count and
monthly equivalent are unchanged by the reading.

What lives here: the closed set a rule states (:class:`WeekdayReading`) and the
read of its stored value (:func:`weekday_reading_of`), the
three shapes the join answers (:class:`DayOfMonth`, :class:`NthWeekday`,
:class:`LastWeekday`), the join itself (:func:`month_coordinate`, which took
``_nominal_day.cadence_day_of_month``'s place), which dates each reading can
read (:data:`WEEKDAY_READING_WINDOWS`, counted by :func:`week_of_month`) and
which shape it names there (:func:`weekday_shape`), the one producer of which
readings a date allows (:func:`offerable_weekday_readings`) and the refusal
both value types hold (:func:`_require_weekday_reading_pair`).

**The dependency runs ONE way**: this module imports :mod:`._frequency` and
nothing of :mod:`._resolution`, which imports it.

Pure: no Flask, no ORM, no clock, no database.
"""
import calendar as calendar_module
import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from app.enums import RecurrenceUnitEnum
from app.services.recurrence._frequency import (
    RecurrenceResolutionError,
    has_day_of_month_coordinate,
)
from app.utils.dates import (
    DAYS_PER_WEEK,
    SHORTEST_MONTH_DAYS,
    clamped_day,
    last_weekday,
    month_of_ordinal,
    nth_weekday,
)

#: The latest week number EVERY month holds of every weekday: four whole weeks
#: fit in the shortest month, so a 1st-4th always exists and a 5th does not.
#: The ceiling of the from-start reading (ruling plan#2670), derived rather
#: than written as ``4`` so it reads as the reason it is four.
LAST_WEEK_EVERY_MONTH_HOLDS = SHORTEST_MONTH_DAYS // DAYS_PER_WEEK


class WeekdayReading(enum.Enum):
    """How a rule's first date is read when it is read by its WEEKDAY.

    The whole of what a rule states about its weekday (ruling plan#2668):
    the week number and the weekday come from ``starts_on``.  ABSENCE -- a
    rule carrying no reading -- is the day-of-month reading, so this set has no
    member for it, the way ``nominal_day``'s absence means the date holds the
    day.

    Not a ``ref`` table's mirror (:mod:`app.enums` holds those): nothing
    compares an id against it, and ``budget.recurrence_rules.weekday_reading``
    holds the member's VALUE under same-row CHECKs that a ref id would make
    name literals.  The write door stores ``.value`` and the read door reads
    the member back through :func:`weekday_reading_of`.

        from_start -- the weekday counted from the month's start: a date on
                      the 15th-21st is "the third" of its weekday.
        from_end   -- the month's last such weekday.
    """

    FROM_START = "from_start"
    FROM_END = "from_end"


def weekday_reading_of(stored: str | None) -> WeekdayReading | None:
    """Return the reading a ``weekday_reading`` column value names.

    The inverse of the write door's ``.value``, held beside the set it reads
    so the read door names no literal.  ``ck_recurrence_rules_weekday_reading``
    admits only the members' values, so the lookup cannot meet a third one.

    Args:
        stored: The column's value, or ``None`` for a rule read by its day of
            the month.

    Returns:
        The member, or ``None``.
    """
    if stored is None:
        return None
    return WeekdayReading(stored)


class MonthCoordinate(ABC):
    """The day of each month a cadence lands on: one value, three shapes.

    **Every shape answers both questions and the base answers neither**, for
    the reason :class:`~app.services.recurrence.EndBound` states: a default
    here would be a partial function over a closed set, and a shape added
    without its date would walk on a day nobody chose.

    Built by :func:`month_coordinate` and nowhere else in the application, so
    a shape always agrees with the first date it was read from.
    """

    @abstractmethod
    def date_in(self, ordinal: int) -> date:
        """Return the day this coordinate names in a month.

        Args:
            ordinal: An absolute month ordinal
                (:func:`~app.utils.dates.month_ordinal`).

        Returns:
            The day, always inside that month.
        """

    @property
    @abstractmethod
    def day_of_month(self) -> int | None:
        """Return the one day of the month every occurrence is read from, or ``None``.

        The day shape's day, before the per-month clamp; ``None`` for a
        weekday shape, whose day moves from month to month (the third Tuesday
        is the 15th-21st).  Asked where a caller needs a FIXED day: the
        describer's "first paycheck" collapse and the rule's scheduling day.
        """


@dataclass(frozen=True)
class DayOfMonth(MonthCoordinate):
    """The same day every month, clamped into a short month (ruling R-R3).

    Attributes:
        day: The day the rule MEANS, 1-31: ``nominal_day`` when the first
            occurrence's month clamped it, the first occurrence's own day
            otherwise.
    """

    day: int

    def date_in(self, ordinal: int) -> date:
        """Return :attr:`day` in the month, clamped to its length.

        Args:
            ordinal: An absolute month ordinal.

        Returns:
            The day -- the 31st in January, the 30th in April.
        """
        return clamped_day(ordinal, self.day)

    @property
    def day_of_month(self) -> int:
        """Return :attr:`day`.

        Returns:
            The meant day, 1-31.
        """
        return self.day


@dataclass(frozen=True)
class NthWeekday(MonthCoordinate):
    """The *nth* *weekday* of every month: "the third Tuesday".

    Attributes:
        nth: The week number, 1 through :data:`LAST_WEEK_EVERY_MONTH_HOLDS`.
        weekday: A :meth:`datetime.date.weekday` index, 0 = Monday.
    """

    nth: int
    weekday: int

    def __post_init__(self) -> None:
        """Refuse a week number some month does not hold.

        **No fifth** (ruling plan#2670): a 5th weekday exists in some months
        only, and :func:`~app.utils.dates.nth_weekday` asked for one a month
        lacks answers a day in the FOLLOWING month, so the walk would fire
        twice in one month and skip another.  Unconstructible rather than
        refused at the walk.

        Raises:
            RecurrenceResolutionError: When *nth* is outside 1 through
                :data:`LAST_WEEK_EVERY_MONTH_HOLDS`.
        """
        if 1 <= self.nth <= LAST_WEEK_EVERY_MONTH_HOLDS:
            return
        raise RecurrenceResolutionError(
            f"a weekday counted from the month's start must be the 1st "
            f"through the {LAST_WEEK_EVERY_MONTH_HOLDS}th, got {self.nth}: "
            f"only those exist in every month (ruling plan#2670), so a later "
            f"week number would fire in the following month wherever a month "
            f"lacks it.  A date on the 29th-31st reads as its day of the "
            f"month or as the month's last such weekday."
        )

    def date_in(self, ordinal: int) -> date:
        """Return the month's *nth* *weekday*.

        Args:
            ordinal: An absolute month ordinal.

        Returns:
            The day.
        """
        year, month = month_of_ordinal(ordinal)
        return nth_weekday(year, month, self.weekday, self.nth)

    @property
    def day_of_month(self) -> None:
        """Return ``None``: the day moves from month to month.

        Returns:
            ``None``.
        """
        return None


@dataclass(frozen=True)
class LastWeekday(MonthCoordinate):
    """The LAST *weekday* of every month: "the last Friday".

    Attributes:
        weekday: A :meth:`datetime.date.weekday` index, 0 = Monday.
    """

    weekday: int

    def date_in(self, ordinal: int) -> date:
        """Return the month's last *weekday*.

        Args:
            ordinal: An absolute month ordinal.

        Returns:
            The day.
        """
        year, month = month_of_ordinal(ordinal)
        return last_weekday(year, month, self.weekday)

    @property
    def day_of_month(self) -> None:
        """Return ``None``: the day moves from month to month.

        Returns:
            ``None``.
        """
        return None


@dataclass(frozen=True)
class WeekdayReadingWindow:
    """Which dates a weekday reading can read: the weeks of a month it counts.

    A weekday reading names its date's place among its weekday's days in the
    month, counted from ONE end, and it reads only a date that every month
    holds at that place.  Both readings are that one shape -- "the last
    Friday" is the first Friday counted back from the end -- so the offer set
    asks one question of each (:func:`offerable_weekday_readings`) and the
    form's script interprets the same two numbers rather than holding a rule
    of its own (``recurrence_form.js``, ``syncRepeatsOn``).

    Attributes:
        counts_from_end: Whether the week is counted back from the month's
            last day rather than forward from its first.
        last_week: The latest week, counted from that end, the reading reads.
    """

    counts_from_end: bool
    last_week: int


#: The ONE statement of which dates each weekday reading can read, keyed by
#: the reading so a member added to :class:`WeekdayReading` without a window
#: raises ``KeyError`` rather than being offered for every date.
#:
#: * From the start, the 1st-4th of a weekday: no fifth (ruling plan#2670).
#: * From the end, the month's last such weekday: the 1st counted back.
WEEKDAY_READING_WINDOWS: dict[WeekdayReading, WeekdayReadingWindow] = {
    WeekdayReading.FROM_START: WeekdayReadingWindow(
        counts_from_end=False, last_week=LAST_WEEK_EVERY_MONTH_HOLDS,
    ),
    WeekdayReading.FROM_END: WeekdayReadingWindow(
        counts_from_end=True, last_week=1,
    ),
}


def week_of_month(day: date, *, counts_from_end: bool) -> int:
    """Return which of its month's *day*.weekday()s *day* is, counted from one end.

    Args:
        day: Any date.
        counts_from_end: Count back from the month's last day rather than
            forward from its first.

    Returns:
        1 for the 1st-7th (or the last seven days, counted from the end), 2
        for the next seven, and so on to 5.
    """
    if counts_from_end:
        last_day = calendar_module.monthrange(day.year, day.month)[1]
        return (last_day - day.day) // DAYS_PER_WEEK + 1
    return (day.day - 1) // DAYS_PER_WEEK + 1


def weekday_shape(
    reading: WeekdayReading, week: int, weekday: int,
) -> MonthCoordinate:
    """Return the coordinate *reading* names for a date at *week* on *weekday*.

    The one mapping from a reading to its shape, asked by
    :func:`month_coordinate` for a rule and by the form's offer
    (:func:`~app.services.recurrence.weekday_reading_options`) for every week
    and weekday a date can sit at, so the words the form shows are the
    shape's the walk fires on.

    Args:
        reading: The weekday reading.
        week: The date's week counted from the end *reading* counts from
            (:func:`week_of_month`).  The last weekday has no week number:
            it is the first counted back, whatever *week* says.
        weekday: A :meth:`datetime.date.weekday` index, 0 = Monday.

    Returns:
        The coordinate.

    Raises:
        RecurrenceResolutionError: When *reading* is a member this function
            has no shape for, or a from-start *week* past the 4th
            (:class:`NthWeekday`).
    """
    if reading is WeekdayReading.FROM_START:
        return NthWeekday(nth=week, weekday=weekday)
    if reading is WeekdayReading.FROM_END:
        return LastWeekday(weekday=weekday)
    raise _unshaped(reading)


def _unshaped(reading: object) -> RecurrenceResolutionError:
    """Return the refusal for a reading with no window or no shape.

    One sentence for both halves a reading needs, because the harm is the
    same either way: :func:`month_coordinate` asks the window first and
    :func:`weekday_shape` the shape, and neither may fall back to the day of
    the month.

    Args:
        reading: The reading neither table answers.

    Returns:
        The error, for the caller to raise.
    """
    return RecurrenceResolutionError(
        f"weekday reading {reading!r} has no month coordinate.  Every "
        f"member of WeekdayReading must have a window and a shape: reading the "
        f"date by its day of the month instead would fire the rule on a day "
        f"its owner did not choose."
    )


def month_coordinate(
    unit: RecurrenceUnitEnum,
    starts_on: date,
    nominal_day: int | None,
    weekday_reading: WeekdayReading | None,
) -> MonthCoordinate | None:
    """Return the day of each month a cadence lands on, from what the rule states.

    **The ONE reader of ``(starts_on, nominal_day, weekday_reading)``**, which
    took ``_nominal_day.cadence_day_of_month``'s place at plan step
    ``recurrence:R8-c``: that function answered a bare day, so a reader handed
    a "third Tuesday" rule could only fire it on its first date's day of the
    month -- plan ledger row **D29**, in the walk and in the describer alike.
    Answering the coordinate as a value of three shapes is what lets each
    reader dispatch on the SHAPE rather than on the unit.

    ``is None``, not truthiness, for both optional fields: a falsy-value bug
    here would silently re-read every later month.

    Args:
        unit: The cadence unit.
        starts_on: The rule's first occurrence.
        nominal_day: The day the rule means when *starts_on*'s own month was
            too short to hold it, or ``None``.
        weekday_reading: How *starts_on* is read by its weekday, or ``None``
            to read it by its day of the month.

    Returns:
        The coordinate -- or ``None`` for a unit with no month coordinate
        (:func:`~app.services.recurrence.has_day_of_month_coordinate`).
        ``None`` is absence rather than a missing value: a paycheck-space or
        weekly rule names no day of a month.

    Raises:
        RecurrenceResolutionError: When *weekday_reading* has no window in
            :data:`WEEKDAY_READING_WINDOWS` or no shape in
            :func:`weekday_shape`, or a from-start reading meets a date past
            the 28th (:class:`NthWeekday`).  Both value types refuse the
            last before it is asked (:func:`_require_weekday_reading_pair`).
    """
    if not has_day_of_month_coordinate(unit):
        return None
    if weekday_reading is None:
        if nominal_day is None:
            return DayOfMonth(day=starts_on.day)
        return DayOfMonth(day=nominal_day)
    window = WEEKDAY_READING_WINDOWS.get(weekday_reading)
    if window is None:
        raise _unshaped(weekday_reading)
    return weekday_shape(
        weekday_reading,
        week_of_month(starts_on, counts_from_end=window.counts_from_end),
        starts_on.weekday(),
    )


def offerable_weekday_readings(
    unit: RecurrenceUnitEnum, starts_on: date,
) -> tuple[WeekdayReading, ...]:
    """Return the weekday readings *starts_on* allows, from-start first.

    **What the form offers beside the day of the month, and the ONE producer
    of it**, so a control built from it cannot offer a reading the spec, the
    resolved value or the column's CHECK would refuse -- the property
    :func:`~app.services.recurrence.offerable_nominal_days` gives the clamp.

    * **From the start** while the date is the 1st-4th of its weekday, the
      28th or earlier (no fifth, ruling plan#2670).
    * **From the end** when the date is its month's last such weekday, within
      a week of the month's end.

    Both are read off :data:`WEEKDAY_READING_WINDOWS`, the table the form's
    script is handed too, so the browser and this function apply one rule.

    A 22nd-28th that is also within a week of the end offers both, and they
    are different cadences: Fri 2027-01-22 is the fourth Friday and Jan 29
    the last, so from February on the two part wherever a month holds five.

    Args:
        unit: The cadence unit.  A cadence not measured in whole months has no
            month coordinate, so it offers nothing.
        starts_on: The rule's first occurrence.

    Returns:
        The readings in the table's order, which is the declaration order --
        ``(FROM_START,)`` for the 17th,
        ``(FROM_END,)`` for the 30th of a 31-day month, both for Feb 28 of a
        common year, and ``()`` for a weekly or paycheck cadence.
    """
    if not has_day_of_month_coordinate(unit):
        return ()
    return tuple(
        reading for reading, window in WEEKDAY_READING_WINDOWS.items()
        if week_of_month(starts_on, counts_from_end=window.counts_from_end)
        <= window.last_week
    )


def _require_weekday_reading_pair(
    unit: RecurrenceUnitEnum,
    starts_on: date,
    nominal_day: int | None,
    weekday_reading: WeekdayReading | None,
    *,
    where: str,
) -> None:
    """Refuse a weekday reading the rule's other fields contradict.

    **The one statement of the invariant, held at CONSTRUCTION** by both value
    types, beside :func:`~._nominal_day._require_nominal_day_pair` and
    ``_resolution._require_month_ceiling_pair`` (plan step
    ``recurrence:R8-c``).  Two refusals:

    * **beside a nominal day** -- the exclusive arc ruling **R-R25** put on
      the table: a nominal day reads the date by its clamped day of the month,
      a weekday reading by its weekday, and one date cannot be read both ways.
    * **outside :func:`offerable_weekday_readings`** -- membership in the
      offer set rather than a second list of conditions, which covers a unit
      with no month coordinate and a date the reading does not fit (a 30th
      read from the start, a 17th read from the end).  The refusal NAMES the
      set, as the nominal day's does.

    Args:
        unit: The cadence unit.
        starts_on: The rule's first occurrence.
        nominal_day: The clamped month-end day, or ``None``.
        weekday_reading: The reading, or ``None`` for the day of the month.
        where: What to name in the refusal, composed by the caller because only
            the caller knows which value is being built.

    Raises:
        RecurrenceResolutionError: When the reading contradicts the rule.
    """
    if weekday_reading is None:
        return
    if nominal_day is not None:
        raise RecurrenceResolutionError(
            f"recurrence weekday reading {weekday_reading.value!r} cannot sit "
            f"beside nominal_day {nominal_day} for {where}: a nominal day "
            f"reads the first occurrence {starts_on} by its clamped day of the "
            f"month and a weekday reading by its weekday, and one date is read "
            f"one way (the exclusive arc, ruling R-R25).  State one or the "
            f"other."
        )
    offerable = offerable_weekday_readings(unit, starts_on)
    if weekday_reading in offerable:
        return
    admissible = (
        f"the only readings it allows are "
        f"{[reading.value for reading in offerable]}" if offerable
        else "that cadence has no day-of-month coordinate, so it reads no "
             "weekday of a month"
    )
    raise RecurrenceResolutionError(
        f"recurrence weekday reading {weekday_reading.value!r} cannot read a "
        f"first occurrence of {starts_on} on a {unit!r} cadence for {where}: "
        f"{admissible}.  From the start is the 1st-4th of a weekday (the 28th "
        f"or earlier, no fifth: ruling plan#2670); from the end is the "
        f"month's last such weekday (within a week of its end)."
    )


__all__ = [
    "LAST_WEEK_EVERY_MONTH_HOLDS",
    "WEEKDAY_READING_WINDOWS",
    "DayOfMonth",
    "LastWeekday",
    "MonthCoordinate",
    "NthWeekday",
    "WeekdayReading",
    "WeekdayReadingWindow",
    "month_coordinate",
    "offerable_weekday_readings",
    "week_of_month",
    "weekday_reading_of",
    "weekday_shape",
]
