"""Tests for the "repeating on" control's offer of the weekday reading.

Plan step ``recurrence:R8-c`` (rulings plan#2668 and plan#2670).  One
``<select>`` reads a month rule's first date one way: the same day of the
month, a clamped month-end day, the weekday counted from the month's start
("the third Tuesday"), or the month's last such weekday.  What it posts is a
TOKEN (:attr:`~app.services.recurrence.MonthReading.token`), read back by the
one reader :func:`~app.services.recurrence.month_reading_from_token`; what it
SAYS for a weekday option depends on the date being typed, so the server
words every date a reading can read (:func:`weekday_reading_options`) and
which of them a date allows (:func:`weekday_reading_choices`).

Every expected word below was written by hand; the labels are also held to
what :func:`~app.services.recurrence.describe` says of the same rule, so the
form offers a reading in the words the Recurring row then shows.  Nov 1 2026
is a Sunday and Nov 30 2026 a Monday.

Pure: no database, so no ``app`` fixture.
"""
import calendar
import json
from datetime import date, timedelta

import pytest

from app.enums import RecurrenceUnitEnum
from app.services.recurrence import (
    MonthReading,
    WeekdayReading,
    describe,
    month_reading_from_token,
    offerable_weekday_readings,
    stored_month_reading,
    weekday_reading_choices,
    weekday_reading_options,
)
from app.services.recurrence._picker import nominal_day_options
from tests.test_services.test_recurrence_occurrence import resolved_value

_MONTH = RecurrenceUnitEnum.MONTH
_FROM_START = WeekdayReading.FROM_START
_FROM_END = WeekdayReading.FROM_END

#: The week words, written out rather than read off the describer.
_ORDINALS = ("first", "second", "third", "fourth")

#: The weekday names by ``date.weekday()`` index, written out rather than
#: read off ``calendar.day_name``, which follows the process locale.
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday",
)


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


def _option(reading: WeekdayReading):
    """Return the weekday option for *reading*, and that there is exactly one.

    Args:
        reading: The weekday reading.

    Returns:
        Its :class:`~app.services.recurrence._picker.WeekdayReadingOption`.
    """
    (option,) = [
        option for option in weekday_reading_options()
        if option.token == reading.value
    ]
    return option


def _november_2026(week: int, weekday: int, *, from_end: bool) -> date:
    """Return the date of Nov 2026 at *week* on *weekday*, counted from one end.

    Nov 1 2026 is a Sunday and Nov 30 a Monday, so the first of a weekday is
    the 1st plus its distance from Sunday, and the last is the 30th less its
    distance back from Monday.

    Args:
        week: The week counted from that end.
        weekday: A ``date.weekday()`` index, 0 = Monday.
        from_end: Count back from the 30th rather than on from the 1st.

    Returns:
        The date.
    """
    if from_end:
        return date(2026, 11, 30 - (calendar.MONDAY - weekday) % 7 - 7 * (week - 1))
    return date(2026, 11, 1 + (weekday - calendar.SUNDAY) % 7 + 7 * (week - 1))


class TestTheMonthReading:
    """``MonthReading``: one choice, and the one spelling of what it posts."""

    def test_both_halves_set_is_refused(self):
        """A nominal day beside a weekday reading reads one date two ways."""
        with pytest.raises(ValueError, match="one date is read one way"):
            MonthReading(nominal_day=31, weekday_reading=_FROM_START)

    @pytest.mark.parametrize(
        ("reading", "token"),
        [
            (MonthReading(), ""),
            (MonthReading(nominal_day=29), "29"),
            (MonthReading(nominal_day=31), "31"),
            (MonthReading(weekday_reading=_FROM_START), "from_start"),
            (MonthReading(weekday_reading=_FROM_END), "from_end"),
        ],
        ids=["same-day", "29th", "last-day", "from-start", "from-end"],
    )
    def test_each_reading_posts_its_own_token(self, reading, token):
        """Empty for the same day, the day's digits, or the reading's stored value."""
        assert reading.token == token


class TestTheTokensRoundTrip:
    """``month_reading_from_token`` reads exactly what the options post."""

    def test_every_option_the_control_renders_reads_back_as_itself(self):
        """The default, three month-end days and two weekday readings, in order.

        The tokens the template's three loops emit (``_recurrence_fields.html``):
        the default option's empty string, then ``picker.nominal_days`` and
        ``picker.weekday_readings`` -- the producers below.
        """
        tokens = [
            "",
            *(option.token for option in nominal_day_options()),
            *(option.token for option in weekday_reading_options()),
        ]

        assert tokens == ["", "29", "30", "31", "from_start", "from_end"]
        assert [month_reading_from_token(token) for token in tokens] == [
            MonthReading(),
            MonthReading(nominal_day=29),
            MonthReading(nominal_day=30),
            MonthReading(nominal_day=31),
            MonthReading(weekday_reading=_FROM_START),
            MonthReading(weekday_reading=_FROM_END),
        ]
        assert [
            month_reading_from_token(token).token for token in tokens
        ] == tokens

    @pytest.mark.parametrize("token", ["031", " 31", "28", "x"])
    def test_a_token_no_option_posts_is_refused(self, token):
        """A leading zero, a space, a day every month holds, and a stranger.

        A plain integer parse would read the first two as 31; 28 is a day the
        date already states, so no option posts it.
        """
        assert month_reading_from_token(token) is None


class TestTheWeekdayOptions:
    """``weekday_reading_options``: every phrase each option can show, and its window."""

    def test_one_option_per_reading_from_start_first_with_its_window(self):
        """The window is the table the offer set reads (``WEEKDAY_READING_WINDOWS``)."""
        options = weekday_reading_options()

        assert [
            (option.token, option.counts_from_end, option.last_week)
            for option in options
        ] == [("from_start", False, 4), ("from_end", True, 1)]

    def test_from_the_start_words_twenty_eight_dates(self):
        """Four weeks of seven weekdays, keyed ``"<week>-<weekday>"``."""
        labels = json.loads(_option(_FROM_START).labels_json)

        assert labels == {
            f"{week}-{weekday}": f"the {_ORDINALS[week - 1]} {name}"
            for week in range(1, 5)
            for weekday, name in enumerate(_WEEKDAYS)
        }
        assert len(labels) == 28
        assert labels["3-1"] == "the third Tuesday"

    def test_from_the_end_words_seven_dates(self):
        """One week counted back, seven weekdays."""
        labels = json.loads(_option(_FROM_END).labels_json)

        assert labels == {
            f"1-{weekday}": f"the last {name}"
            for weekday, name in enumerate(_WEEKDAYS)
        }
        assert labels["1-4"] == "the last Friday"

    @pytest.mark.parametrize("reading", [_FROM_START, _FROM_END])
    def test_every_label_is_the_describers_words_behind_the(self, reading):
        """The form offers a reading in the words the Recurring row shows for it.

        For each key, a monthly rule on the November 2026 date at that week
        and weekday is described, and the label is ``"the "`` and what sits
        inside its parentheses: "the third Tuesday" beside
        "Monthly (third Tuesday)".
        """
        option = _option(reading)
        for key, label in json.loads(option.labels_json).items():
            week, weekday = (int(part) for part in key.split("-"))
            starts_on = _november_2026(
                week, weekday, from_end=option.counts_from_end,
            )
            assert starts_on.weekday() == weekday, key
            cadence = describe(resolved_value(
                unit=_MONTH, starts_on=starts_on, weekday_reading=reading,
            )).cadence

            assert label.startswith("the "), key
            assert cadence == f"Monthly ({label.removeprefix('the ')})", key


class TestTheChoicesADateAllows:
    """``weekday_reading_choices``: the offer set for one date, worded for it."""

    @pytest.mark.parametrize("unit", [_MONTH, RecurrenceUnitEnum.YEAR])
    def test_every_date_of_three_years(self, unit):
        """The offer set's members, in its order, each in that date's words.

        The week is counted by hand -- the 1st-7th is the first, the
        15th-21st the third -- and each label is also the script's phrase at
        that date's key in :attr:`labels_json`, so the rendered form and the
        typed date say one thing.
        """
        start_labels = json.loads(_option(_FROM_START).labels_json)
        end_labels = json.loads(_option(_FROM_END).labels_json)
        for day in _days(date(2026, 1, 1), date(2028, 12, 31)):
            choices = weekday_reading_choices(unit, day)
            name = _WEEKDAYS[day.weekday()]

            assert tuple(choices) == offerable_weekday_readings(unit, day), day
            if _FROM_START in choices:
                week = (day.day - 1) // 7 + 1
                assert choices[_FROM_START] == (
                    f"the {_ORDINALS[week - 1]} {name}"
                ), day
                assert choices[_FROM_START] == (
                    start_labels[f"{week}-{day.weekday()}"]
                ), day
            if _FROM_END in choices:
                assert choices[_FROM_END] == f"the last {name}", day
                assert choices[_FROM_END] == (
                    end_labels[f"1-{day.weekday()}"]
                ), day

    def test_the_hand_picked_dates(self):
        """The 17th, the 30th, and a fourth Sunday that is also the last."""
        assert weekday_reading_choices(_MONTH, date(2026, 11, 17)) == {
            _FROM_START: "the third Tuesday",
        }
        assert weekday_reading_choices(_MONTH, date(2026, 11, 30)) == {
            _FROM_END: "the last Monday",
        }
        assert weekday_reading_choices(_MONTH, date(2027, 2, 28)) == {
            _FROM_START: "the fourth Sunday",
            _FROM_END: "the last Sunday",
        }

    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.WEEK, RecurrenceUnitEnum.PERIOD],
    )
    def test_a_unit_with_no_month_coordinate_allows_none(self, unit):
        """A weekly or paycheck cadence reads no weekday of a month."""
        assert weekday_reading_choices(unit, date(2026, 11, 17)) == {}


class TestTheStoredReading:
    """``stored_month_reading``: what an edit form preselects, off the two columns."""

    @pytest.mark.parametrize(
        ("nominal_day", "weekday_reading", "expected"),
        [
            (None, None, MonthReading()),
            (31, None, MonthReading(nominal_day=31)),
            (None, "from_start", MonthReading(weekday_reading=_FROM_START)),
            (None, "from_end", MonthReading(weekday_reading=_FROM_END)),
        ],
        ids=["same-day", "last-day", "from-start", "from-end"],
    )
    def test_each_stored_pair_reads_as_its_choice(
        self, nominal_day, weekday_reading, expected,
    ):
        """The column's VALUE read back as the member, the nominal day verbatim."""
        assert stored_month_reading(nominal_day, weekday_reading) == expected

    def test_both_columns_set_is_refused(self):
        """``ck_recurrence_rules_one_month_reading`` refuses the row; so does the read."""
        with pytest.raises(ValueError, match="one date is read one way"):
            stored_month_reading(31, "from_end")
