"""Tests for the month coordinate: how a month-measured rule's first date is read.

Plan step ``recurrence:R8-c`` (rulings plan#2668 and plan#2670, finding
plan#1387 / D29).  A ``MONTH`` or ``YEAR`` rule's first date is read by its
day of the month, by its weekday counted from the month's start ("the third
Tuesday"), or as the month's last such weekday ("the last Friday"); the week
number and the weekday come from the date.  Every expected date below was
counted by hand off a calendar, never recomputed with the function under test:
Nov 1 2026 is a Sunday, Jan 1 2027 a Friday, Nov 1 2027 a Monday, Nov 1 2028
a Wednesday and Dec 31 2100 a Friday.

No database read: what a unit's reference row states comes from the reference
cache, which the session-wide ``setup_database`` loads, so no case here asks for
the ``app`` fixture.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.recurrence import (
    Cadence,
    DayOfMonth,
    LastWeekday,
    MonthCoordinate,
    NthWeekday,
    RecurrenceDescriptionError,
    RecurrenceResolutionError,
    RecurrenceSpec,
    WeekdayReading,
    describe,
    month_coordinate,
    occurrences,
    offerable_weekday_readings,
    resolve,
)
from app.services.recurrence import _describe, _months
from app.services.recurrence._month_coordinate import (
    LAST_WEEK_EVERY_MONTH_HOLDS,
    WEEKDAY_READING_WINDOWS,
    WeekdayReadingWindow,
    week_of_month,
    weekday_shape,
)
from app.services.recurrence._row_day import cadence_scheduled_day
from app.utils.dates import month_ordinal
from tests.test_services.test_recurrence_occurrence import resolved_value
from tests.test_services.test_recurrence_resolution import build_calendar

_MONTH = RecurrenceUnitEnum.MONTH
_YEAR = RecurrenceUnitEnum.YEAR
_FROM_START = WeekdayReading.FROM_START
_FROM_END = WeekdayReading.FROM_END

#: The owner a spec below names, and the calendar it resolves against.
_OWNER_ID = 7

#: Tue 2026-11-17: the third Tuesday of November 2026 (the 17th, 10th, 3rd).
_THIRD_TUESDAY = date(2026, 11, 17)

#: Fri 2027-01-29, the last Friday of January 2027 (and its fifth).
_LAST_FRIDAY = date(2027, 1, 29)

#: Fri 2027-01-22, the fourth Friday of January 2027 and not its last.
_FOURTH_FRIDAY = date(2027, 1, 22)

#: The last Friday of every month of 2027, counted off a calendar.
_LAST_FRIDAYS_2027 = [
    date(2027, 1, 29), date(2027, 2, 26), date(2027, 3, 26),
    date(2027, 4, 30), date(2027, 5, 28), date(2027, 6, 25),
    date(2027, 7, 30), date(2027, 8, 27), date(2027, 9, 24),
    date(2027, 10, 29), date(2027, 11, 26), date(2027, 12, 31),
]

#: The fourth Friday of every month of 2027.  It is the last in Feb, Mar,
#: May, Jun, Aug, Sep and Nov, which hold four; Jan, Apr, Jul, Oct and Dec
#: hold five, and there the two readings part.
_FOURTH_FRIDAYS_2027 = [
    date(2027, 1, 22), date(2027, 2, 26), date(2027, 3, 26),
    date(2027, 4, 23), date(2027, 5, 28), date(2027, 6, 25),
    date(2027, 7, 23), date(2027, 8, 27), date(2027, 9, 24),
    date(2027, 10, 22), date(2027, 11, 26), date(2027, 12, 24),
]


def _days(first: date, last: date) -> list[date]:
    """Return every date from *first* through *last*.

    Args:
        first: The first date.
        last: The last date, included.

    Returns:
        The dates, ascending.
    """
    return [
        first + timedelta(days=offset)
        for offset in range((last - first).days + 1)
    ]


def _walked(
    starts_on: date,
    reading: WeekdayReading | None,
    through: date,
    *,
    unit: RecurrenceUnitEnum = _MONTH,
    interval_n: int = 1,
) -> list[date]:
    """Return a rule's occurrences through *through*, from a stated value.

    Args:
        starts_on: The first occurrence.
        reading: How it is read by its weekday, or ``None`` for its day.
        through: The last date to walk to.
        unit: The cadence unit.
        interval_n: Units between occurrences.

    Returns:
        The occurrence dates.
    """
    resolved = resolved_value(
        unit=unit, starts_on=starts_on, interval_n=interval_n,
        weekday_reading=reading,
    )
    return list(occurrences(resolved, build_calendar(), through=through))


class TestWhichReadingsADateAllows:
    """``offerable_weekday_readings``: from the start to the 28th, from the end in the last week."""

    def test_a_mid_month_date_reads_from_the_start_only(self):
        """The 17th is the third of its weekday and a fortnight from the end."""
        assert offerable_weekday_readings(_MONTH, _THIRD_TUESDAY) == (
            _FROM_START,
        )

    def test_a_date_past_the_28th_reads_from_the_end_only(self):
        """The 29th is a fifth weekday, which not every month holds (plan#2670)."""
        assert offerable_weekday_readings(_MONTH, _LAST_FRIDAY) == (_FROM_END,)

    def test_a_fourth_that_is_not_the_last_reads_from_the_start_only(self):
        """Jan 22 2027 is the fourth Friday; the 29th is the last."""
        assert offerable_weekday_readings(_MONTH, _FOURTH_FRIDAY) == (
            _FROM_START,
        )

    @pytest.mark.parametrize(
        "starts_on",
        [
            # Mon 2027-01-25: the fourth Monday, and Jan 31 is a Sunday.
            date(2027, 1, 25),
            # Sun 2027-02-28: the fourth Sunday of a 28-day February.
            date(2027, 2, 28),
        ],
        ids=["jan-25", "feb-28"],
    )
    def test_a_fourth_that_is_also_the_last_reads_both_ways(self, starts_on):
        """Two different cadences: they part wherever a month holds five."""
        assert offerable_weekday_readings(_YEAR, starts_on) == (
            _FROM_START, _FROM_END,
        )

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.WEEK, RecurrenceUnitEnum.PERIOD],
    )
    def test_a_unit_with_no_month_coordinate_offers_nothing(self, unit):
        """A weekly or paycheck cadence reads no weekday of a month."""
        assert offerable_weekday_readings(unit, _THIRD_TUESDAY) == ()

    @pytest.mark.parametrize("unit", [_MONTH, _YEAR])
    def test_every_date_of_three_years_against_the_calendar_itself(self, unit):
        """From the start iff the 28th or earlier; from the end iff a week on is next month.

        The oracle is the calendar, not the function's own arithmetic: a date
        is its month's last such weekday exactly when the same weekday a week
        later falls in another month.
        """
        for day in _days(date(2026, 1, 1), date(2028, 12, 31)):
            expected = []
            if day.day <= 28:
                expected.append(_FROM_START)
            if (day + timedelta(days=7)).month != day.month:
                expected.append(_FROM_END)
            assert offerable_weekday_readings(unit, day) == tuple(expected), day


class TestTheCoordinateIsReadFromTheDate:
    """``month_coordinate`` answers one value of three shapes from what the rule states."""

    def test_each_reading_answers_its_own_shape(self):
        """The day, the clamped day, the nth weekday, the last weekday, and none."""
        assert month_coordinate(
            _MONTH, _THIRD_TUESDAY, None, None,
        ) == DayOfMonth(day=17)
        assert month_coordinate(
            _MONTH, date(2026, 4, 30), 31, None,
        ) == DayOfMonth(day=31)
        assert month_coordinate(
            _MONTH, _THIRD_TUESDAY, None, _FROM_START,
        ) == NthWeekday(nth=3, weekday=calendar.TUESDAY)
        assert month_coordinate(
            _YEAR, _LAST_FRIDAY, None, _FROM_END,
        ) == LastWeekday(weekday=calendar.FRIDAY)
        assert month_coordinate(
            RecurrenceUnitEnum.WEEK, _THIRD_TUESDAY, None, None,
        ) is None

    def test_every_shape_lands_on_the_date_it_was_read_from(self):
        """The walk's first yield is the first occurrence, by construction.

        Over every date of three years and every reading the date allows
        (with the day-of-month reading, always allowed), the coordinate's day
        in the date's own month is the date itself.
        """
        for day in _days(date(2026, 1, 1), date(2028, 12, 31)):
            for reading in (None, *offerable_weekday_readings(_MONTH, day)):
                coordinate = month_coordinate(_MONTH, day, None, reading)
                assert coordinate.date_in(month_ordinal(day)) == day, (
                    day, reading,
                )

    def test_only_the_day_shape_has_a_fixed_day(self):
        """A weekday's day moves from month to month, so it names none."""
        assert DayOfMonth(day=17).day_of_month == 17
        assert NthWeekday(nth=3, weekday=calendar.TUESDAY).day_of_month is None
        assert LastWeekday(weekday=calendar.FRIDAY).day_of_month is None

    @pytest.mark.parametrize("nth", [0, 5])
    def test_a_week_number_some_month_lacks_is_unconstructible(self, nth):
        """No fifth (plan#2670), and no zeroth: the walk could not land them."""
        with pytest.raises(RecurrenceResolutionError, match=f"got {nth}"):
            NthWeekday(nth=nth, weekday=calendar.FRIDAY)

    def test_a_reading_with_no_shape_is_refused(self):
        """A member added without a shape must not read as the day of the month."""
        with pytest.raises(
            RecurrenceResolutionError, match="has no month coordinate",
        ):
            month_coordinate(_MONTH, _THIRD_TUESDAY, None, "every blue moon")


def _same_weekdays_counted(day: date, step: int) -> int:
    """Return how many of *day*'s weekday its month holds on one side of *day*.

    The calendar as the oracle rather than the module's arithmetic: step a
    week at a time from *day* and count the dates still in *day*'s month,
    *day* included.

    Args:
        day: Any date.
        step: ``-7`` to count back to the month's start (its week from the
            start), ``7`` to count on to its end (its week from the end).

    Returns:
        The count, 1-5.
    """
    count = 0
    probe = day
    while probe.month == day.month:
        count += 1
        probe += timedelta(days=step)
    return count


class TestTheWindowsAndTheWeek:
    """The one table of which dates each reading reads, and the week it counts."""

    def test_the_window_table_is_total_over_the_readings(self):
        """Every reading has a window, from-start first, and only those two.

        A member added to ``WeekdayReading`` without a window would be refused
        at the walk (``_unshaped``) rather than offered; this is what says the
        table and the set are the same size today, and what each window is:
        the 1st-4th counted from the start (no fifth, plan#2670) and the 1st
        counted back from the end.
        """
        assert list(WEEKDAY_READING_WINDOWS) == [_FROM_START, _FROM_END]
        assert set(WEEKDAY_READING_WINDOWS) == set(WeekdayReading)
        assert WEEKDAY_READING_WINDOWS == {
            _FROM_START: WeekdayReadingWindow(
                counts_from_end=False, last_week=4,
            ),
            _FROM_END: WeekdayReadingWindow(counts_from_end=True, last_week=1),
        }

    def test_the_week_against_the_calendar_from_both_ends(self):
        """Every date of three years: its week counted forward and back.

        Spot-checked by hand first: Tue 2026-11-17 is the third Tuesday (3,
        10, 17) and the second from the end (24); Mon 2026-11-30 the fifth
        Monday and the last; Sun 2027-02-28 the fourth Sunday of a 28-day
        February and its last.
        """
        assert week_of_month(_THIRD_TUESDAY, counts_from_end=False) == 3
        assert week_of_month(_THIRD_TUESDAY, counts_from_end=True) == 2
        assert week_of_month(date(2026, 11, 30), counts_from_end=False) == 5
        assert week_of_month(date(2026, 11, 30), counts_from_end=True) == 1
        assert week_of_month(date(2027, 2, 28), counts_from_end=False) == 4
        assert week_of_month(date(2027, 2, 28), counts_from_end=True) == 1
        for day in _days(date(2026, 1, 1), date(2028, 12, 31)):
            assert week_of_month(day, counts_from_end=False) == (
                _same_weekdays_counted(day, -7)
            ), day
            assert week_of_month(day, counts_from_end=True) == (
                _same_weekdays_counted(day, 7)
            ), day

    @pytest.mark.parametrize("weekday", range(7))
    @pytest.mark.parametrize("week", range(1, LAST_WEEK_EVERY_MONTH_HOLDS + 1))
    def test_from_the_start_names_the_nth_weekday(self, week, weekday):
        """The week and the weekday, verbatim, for every one the window reads."""
        assert weekday_shape(_FROM_START, week, weekday) == NthWeekday(
            nth=week, weekday=weekday,
        )

    @pytest.mark.parametrize("weekday", range(7))
    @pytest.mark.parametrize("week", range(1, 6))
    def test_from_the_end_names_the_last_weekday_whatever_the_week(
        self, week, weekday,
    ):
        """The last Friday has no week number: it is the first counted back."""
        assert weekday_shape(_FROM_END, week, weekday) == LastWeekday(
            weekday=weekday,
        )

    def test_a_fifth_from_the_start_is_refused_by_the_shape(self):
        """The window ends at the 4th, and the shape refuses past it (plan#2670)."""
        with pytest.raises(RecurrenceResolutionError, match="got 5"):
            weekday_shape(_FROM_START, 5, calendar.FRIDAY)

    def test_a_reading_with_no_shape_is_refused_by_the_shape(self):
        """``weekday_shape``'s own refusal, the sentence ``_unshaped`` writes."""
        with pytest.raises(
            RecurrenceResolutionError,
            match="'every blue moon' has no month coordinate",
        ):
            weekday_shape("every blue moon", 1, calendar.MONDAY)

    def test_a_reading_with_no_window_is_refused_not_read_by_its_day(
        self, monkeypatch,
    ):
        """A member whose window went missing is refused, never read as the 29th.

        ``month_coordinate`` asks the window before the shape, so with the
        from-end window removed the last Friday is refused with the sentence
        ``_unshaped`` writes rather than falling through to the date's day.
        """
        monkeypatch.delitem(WEEKDAY_READING_WINDOWS, _FROM_END)

        with pytest.raises(
            RecurrenceResolutionError,
            match=r"weekday reading <WeekdayReading\.FROM_END: 'from_end'> "
                  r"has no month coordinate",
        ):
            month_coordinate(_MONTH, _LAST_FRIDAY, None, _FROM_END)


class TestTheWalk:
    """The occurrence walk lands each shape's own day, every month it is due."""

    def test_the_third_tuesday_is_not_the_seventeenth(self):
        """D29's date half: the same first date, read two ways.

        Third Tuesdays: Dec 1 2026 and Mar 1 2027 are Tuesday/Monday, so Dec
        15, Jan 19, Feb 16, Mar 16, Apr 20.  Read by its day, the rule fires
        on the 17th, which is a Thursday in December and a Sunday in January.
        """
        through = date(2027, 4, 30)
        assert _walked(_THIRD_TUESDAY, _FROM_START, through) == [
            date(2026, 11, 17), date(2026, 12, 15), date(2027, 1, 19),
            date(2027, 2, 16), date(2027, 3, 16), date(2027, 4, 20),
        ]
        assert _walked(_THIRD_TUESDAY, None, through) == [
            date(2026, 11, 17), date(2026, 12, 17), date(2027, 1, 17),
            date(2027, 2, 17), date(2027, 3, 17), date(2027, 4, 17),
        ]

    def test_the_last_friday_of_2027(self):
        """Twelve last Fridays, on the 24th through the 31st."""
        assert _walked(
            _LAST_FRIDAY, _FROM_END, date(2027, 12, 31),
        ) == _LAST_FRIDAYS_2027

    def test_the_fourth_friday_parts_from_the_last_in_five_months(self):
        """Jan, Apr, Jul, Oct and Dec 2027 hold five Fridays: 22, 23, 23, 22, 24."""
        fourth = _walked(_FOURTH_FRIDAY, _FROM_START, date(2027, 12, 31))

        assert fourth == _FOURTH_FRIDAYS_2027
        parted = [
            (last.month, mine.day)
            for last, mine in zip(_LAST_FRIDAYS_2027, fourth)
            if last != mine
        ]
        assert parted == [(1, 22), (4, 23), (7, 23), (10, 22), (12, 24)]

    def test_thanksgiving_yearly(self):
        """The fourth Thursday of November: 2026-11-26, 2027-11-25, 2028-11-23."""
        assert _walked(
            date(2026, 11, 26), _FROM_START, date(2028, 12, 31), unit=_YEAR,
        ) == [date(2026, 11, 26), date(2027, 11, 25), date(2028, 11, 23)]

    def test_the_second_tuesday_quarterly(self):
        """Jan 12, Apr 13, Jul 13, Oct 12 2027 (Apr and Jul open on Thursday)."""
        assert _walked(
            date(2027, 1, 12), _FROM_START, date(2027, 12, 31), interval_n=3,
        ) == [
            date(2027, 1, 12), date(2027, 4, 13),
            date(2027, 7, 13), date(2027, 10, 12),
        ]

    @pytest.mark.parametrize(
        ("starts_on", "reading"),
        [(_FOURTH_FRIDAY, _FROM_START), (_LAST_FRIDAY, _FROM_END)],
        ids=["fourth", "last"],
    )
    def test_a_weekday_rule_fires_once_in_every_month(self, starts_on, reading):
        """Fourteen years, one Friday in each month: no month skipped or doubled."""
        walked = _walked(starts_on, reading, date(2040, 12, 31))

        assert [(day.year, day.month) for day in walked] == [
            (year, month)
            for year in range(2027, 2041) for month in range(1, 13)
        ]
        assert {day.weekday() for day in walked} == {calendar.FRIDAY}

    def test_the_walk_stops_at_the_calendars_last_month(self):
        """The last Monday from January 2100: twelve dates, the last Dec 27.

        Dec 31 2100 is a Friday, so its last Monday is the 27th; the walk's
        bound is the calendar's last month whichever shape it walks.
        """
        walked = list(_months.walk_months(
            month_ordinal(date(2100, 1, 1)),
            LastWeekday(weekday=calendar.MONDAY).date_in,
            1,
        ))

        assert len(walked) == 12
        assert walked[-1] == date(2100, 12, 27)


class TestTheYearlyCountIsUnchanged:
    """Every weekday rule fires once a month, so the cadence's count holds (plan#2670)."""

    @pytest.mark.parametrize(
        ("starts_on", "reading"),
        [(_FOURTH_FRIDAY, _FROM_START), (_LAST_FRIDAY, _FROM_END)],
        ids=["fourth", "last"],
    )
    def test_a_weekday_rule_fires_as_often_as_its_cadence_says(
        self, starts_on, reading,
    ):
        """Counted off the walk for 2027-2099, against the cadence's own count.

        :class:`~app.services.recurrence.Cadence` takes no month coordinate,
        so a weekday rule's yearly count and monthly equivalent are the day
        rule's: 12 a year, and ``$100.00 x 12 / 12 = $100.00`` a month.  The
        walk is what makes that honest -- twelve weekday dates in every year.
        """
        walked = _walked(starts_on, reading, date(2099, 12, 31))
        monthly = Cadence(interval_n=1, unit=_MONTH)
        pay_cadence = build_calendar().cadence

        assert monthly.occurrences_per_year(pay_cadence) == Decimal("12")
        assert monthly.monthly_equivalent(
            Decimal("100.00"), pay_cadence,
        ) == Decimal("100.00")
        for year in range(2027, 2100):
            assert sum(day.year == year for day in walked) == 12, year


#: Every contradiction the weekday-reading refusal names, with the message
#: that names it: both value types refuse the same four, from one statement
#: (``_month_coordinate._require_weekday_reading_pair``).
_CONTRADICTIONS = pytest.mark.parametrize(
    ("unit", "starts_on", "nominal_day", "reading", "message"),
    [
        (
            RecurrenceUnitEnum.WEEK, _THIRD_TUESDAY, None, _FROM_START,
            "no day-of-month coordinate",
        ),
        (
            # Thu 2026-04-30 is the last Thursday, but it carries the
            # clamp's nominal 31st: one date is read one way.
            _MONTH, date(2026, 4, 30), 31, _FROM_END, "exclusive arc",
        ),
        (
            _MONTH, _LAST_FRIDAY, None, _FROM_START,
            r"only readings it allows are \['from_end'\]",
        ),
        (
            _MONTH, _THIRD_TUESDAY, None, _FROM_END,
            r"only readings it allows are \['from_start'\]",
        ),
    ],
    ids=["week-unit", "beside-nominal-day", "fifth", "not-the-last"],
)


class TestTheRefusals:
    """Both value types refuse a reading the rule's other fields contradict.

    The spec took the field with its column (the storage task of plan step
    R8-c), so a caller cannot author what the resolved value refuses and the
    write door is never where the contradiction surfaces.
    """

    @_CONTRADICTIONS
    def test_the_resolved_value_refuses(
        self, unit, starts_on, nominal_day, reading, message,
    ):
        """Each contradiction is unconstructible, with its own message."""
        with pytest.raises(RecurrenceResolutionError, match=message):
            resolved_value(
                unit=unit, starts_on=starts_on, nominal_day=nominal_day,
                weekday_reading=reading,
            )

    @_CONTRADICTIONS
    def test_the_spec_refuses(
        self, unit, starts_on, nominal_day, reading, message,
    ):
        """The authored value refuses the same four, naming the owner."""
        with pytest.raises(RecurrenceResolutionError, match=message) as refused:
            RecurrenceSpec(
                user_id=_OWNER_ID, unit=unit, starts_on=starts_on,
                nominal_day=nominal_day, weekday_reading=reading,
            )
        assert f"user {_OWNER_ID}" in str(refused.value)


class TestResolveCarriesTheReading:
    """``resolve`` hands the spec's reading to the value the walk reads."""

    @pytest.mark.parametrize(
        ("starts_on", "reading"),
        [
            (_THIRD_TUESDAY, None),
            (_THIRD_TUESDAY, _FROM_START),
            (_LAST_FRIDAY, _FROM_END),
        ],
        ids=["day", "from-start", "from-end"],
    )
    def test_every_reading_reaches_the_resolved_value(self, starts_on, reading):
        """Verbatim, ``None`` included."""
        resolved = resolve(
            RecurrenceSpec(
                user_id=_OWNER_ID, unit=_MONTH, starts_on=starts_on,
                weekday_reading=reading,
            ),
            build_calendar(user_id=_OWNER_ID),
        )

        assert resolved.weekday_reading is reading

    def test_twelve_months_is_a_year_and_keeps_its_reading(self):
        """(12, MONTH) canonicalises to (1, YEAR) and walks Thanksgiving.

        Thu 2026-11-26 is the fourth Thursday of November 2026 (5, 12, 19,
        26); Nov 2027 opens on a Monday, so its fourth is the 25th, and Nov
        2028 on a Wednesday, so the 23rd.  The 26th, the day reading, would
        land on a Friday in 2027 and a Sunday in 2028.
        """
        resolved = resolve(
            RecurrenceSpec(
                user_id=_OWNER_ID, unit=_MONTH, interval_n=12,
                starts_on=date(2026, 11, 26), weekday_reading=_FROM_START,
            ),
            build_calendar(user_id=_OWNER_ID),
        )

        assert (resolved.interval_n, resolved.unit) == (1, _YEAR)
        assert resolved.weekday_reading is _FROM_START
        assert list(
            occurrences(
                resolved, build_calendar(user_id=_OWNER_ID),
                through=date(2028, 12, 31),
            ),
        ) == [date(2026, 11, 26), date(2027, 11, 25), date(2028, 11, 23)]


class TestNoFixedDay:
    """A weekday rule names no one day of the month to schedule on."""

    def test_the_resolved_value_answers_its_shape_and_no_day(self):
        """``month_coordinate`` is the shape; ``day_of_month`` the day shape's day only."""
        resolved = resolved_value(
            unit=_MONTH, starts_on=_THIRD_TUESDAY, weekday_reading=_FROM_START,
        )

        assert resolved.month_coordinate == NthWeekday(
            nth=3, weekday=calendar.TUESDAY,
        )
        assert resolved.day_of_month is None

    def test_the_scheduled_day_is_the_day_shapes_alone(self):
        """The 17th read by its day schedules on the 17th; as a Tuesday, on no day."""
        contain = PeriodPlacementEnum.CONTAINING_DATE
        assert cadence_scheduled_day(
            _MONTH, contain, _THIRD_TUESDAY, None, None,
        ) == 17
        assert cadence_scheduled_day(
            _MONTH, contain, _THIRD_TUESDAY, None, _FROM_START,
        ) is None


class TestTheWords:
    """``describe`` words each shape (D29's display half)."""

    @pytest.mark.parametrize(
        ("unit", "interval_n", "starts_on", "reading", "cadence"),
        [
            (_MONTH, 1, _THIRD_TUESDAY, _FROM_START, "Monthly (third Tuesday)"),
            (_MONTH, 1, _LAST_FRIDAY, _FROM_END, "Monthly (last Friday)"),
            # Mon 2027-03-01: the first Monday, on the 1st.
            (_MONTH, 1, date(2027, 3, 1), _FROM_START, "Monthly (first Monday)"),
            (
                _YEAR, 1, date(2026, 11, 26), _FROM_START,
                "Yearly (fourth Thursday of Nov)",
            ),
            (
                _MONTH, 3, date(2027, 1, 12), _FROM_START,
                "Quarterly (second Tuesday of Jan)",
            ),
            (
                _MONTH, 2, _LAST_FRIDAY, _FROM_END,
                "Every 2 months (last Friday of Jan)",
            ),
            (_MONTH, 1, _THIRD_TUESDAY, None, "Monthly (day 17)"),
        ],
        ids=[
            "third-tuesday", "last-friday", "first-monday", "thanksgiving",
            "quarterly", "every-2-months", "day-reading",
        ],
    )
    def test_each_shape_reads_as_itself(
        self, unit, interval_n, starts_on, reading, cadence,
    ):
        """Every-month rules name the weekday alone; others name the month too."""
        resolved = resolved_value(
            unit=unit, interval_n=interval_n, starts_on=starts_on,
            weekday_reading=reading,
        )

        assert describe(resolved).cadence == cadence

    def test_the_first_monday_is_never_collapsed_into_first_paycheck(self):
        """The 1st read by its day collapses; read as the first Monday it does not.

        "The first paycheck on or after the first Monday" is a later paycheck
        than the month's first wherever the first Monday is not the 1st.
        """
        deferred = PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER
        words = [
            describe(resolved_value(
                unit=_MONTH, starts_on=date(2027, 3, 1), placement=deferred,
                weekday_reading=reading,
            )).cadence
            for reading in (None, _FROM_START)
        ]

        assert words == [
            "Monthly (first paycheck)",
            "Monthly (first Monday, first paycheck)",
        ]

    @pytest.mark.parametrize(
        ("coordinate", "words"),
        [
            (NthWeekday(nth=1, weekday=calendar.MONDAY), "first Monday"),
            (NthWeekday(nth=2, weekday=calendar.WEDNESDAY), "second Wednesday"),
            (NthWeekday(nth=3, weekday=calendar.TUESDAY), "third Tuesday"),
            (NthWeekday(nth=4, weekday=calendar.SUNDAY), "fourth Sunday"),
            (LastWeekday(weekday=calendar.FRIDAY), "last Friday"),
            (LastWeekday(weekday=calendar.SATURDAY), "last Saturday"),
        ],
        ids=["1st", "2nd", "3rd", "4th", "last-friday", "last-saturday"],
    )
    def test_each_weekday_shape_has_its_own_words(self, coordinate, words):
        """The one producer the describer and the form's options both ask."""
        assert _describe.weekday_words(coordinate) == words

    def test_every_shape_has_wording(self):
        """The phrase table is total over the shapes, and the week words over the weeks."""
        assert set(_describe._COORDINATE_PHRASES) == set(
            MonthCoordinate.__subclasses__(),
        )
        assert len(_describe._WEEK_NUMBER_WORDS) == LAST_WEEK_EVERY_MONTH_HOLDS

    def test_a_shape_with_no_wording_raises(self, monkeypatch):
        """Removing a shape's words fails the render rather than wording another shape."""
        monkeypatch.delitem(_describe._COORDINATE_PHRASES, LastWeekday)
        resolved = resolved_value(
            unit=_MONTH, starts_on=_LAST_FRIDAY, weekday_reading=_FROM_END,
        )

        with pytest.raises(
            RecurrenceDescriptionError, match="'LastWeekday' has no wording",
        ):
            describe(resolved)
