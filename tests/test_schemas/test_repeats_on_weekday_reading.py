"""The "repeating on" field at the schema door (plan step recurrence:R8-c, ruling plan#2909).

``repeats_on`` replaced ``nominal_day`` when the control began offering the
weekday readings beside the clamped month-end days (rulings plan#2668 and
plan#2670): ONE ``<select>`` posts ONE token, and
:class:`~app.schemas.validation._recurrence.RepeatsOnField` turns it into the
:class:`~app.services.recurrence.MonthReading` it names.  Whether the date
ALLOWS the reading is a rule over the pair, refused by
``validate_repeats_on_fits_the_start`` as a field error naming the control --
so a pair the write door's ``RecurrenceSpec`` would refuse as a broken
invariant (a 500) never reaches it -- in a sentence naming what the date DOES
allow, in the control's own words.

Swept over every schema that declares the field: the two template kinds'
create and update forms and the payroll line's (salary:R18-c), because the
rule is inherited and an override that lost it would let the same POST
through one door and not another.  Tue 2026-11-17 is the third Tuesday of
November 2026 (3, 10, 17); Mon 2026-11-30 its fifth and last Monday.  Apr 1
2026 is a Wednesday, so Apr 15 is the third and Apr 30 the last Thursday (2,
9, 16, 23, 30); Jan 31 2026 is a Saturday, in a month that clamps nothing; Feb
29 2028 is a Tuesday, the fifth of its month, which holds no 30th or 31st.
"""
from datetime import date

import pytest
from marshmallow import ValidationError

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.schemas.validation import (
    TemplateCreateSchema,
    TemplateUpdateSchema,
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
)
from app.schemas.validation.salary import (
    PaycheckLineCreateSchema,
    PaycheckLineUpdateSchema,
)
from app.services.recurrence import MonthReading, WeekdayReading

#: Every schema that declares ``starts_on`` and ``repeats_on``.
_SCHEMAS = pytest.mark.parametrize(
    "schema_cls",
    [
        TemplateCreateSchema,
        TemplateUpdateSchema,
        TransferTemplateCreateSchema,
        TransferTemplateUpdateSchema,
        PaycheckLineCreateSchema,
        PaycheckLineUpdateSchema,
    ],
    ids=lambda schema_cls: schema_cls.__name__,
)

#: Tue 2026-11-17, the third Tuesday of November 2026.
_THIRD_TUESDAY = date(2026, 11, 17)

#: Mon 2026-11-30, the fifth and last Monday of November 2026.
_LAST_MONDAY = date(2026, 11, 30)


def _submission(starts_on, repeats_on, *, unit=RecurrenceUnitEnum.MONTH, **extra):
    """Return the cadence and its first date as the form posts them.

    Stated whole, interval included, so the ONLY thing a refusal can be about
    is the pair under test: a chosen cadence with no interval is refused in
    its own right (plan step R7c-c).

    Args:
        starts_on: The first occurrence.
        repeats_on: The "repeating on" token.
        unit: The cadence unit.
        **extra: Further keys, posted verbatim.

    Returns:
        The submission, every value a string.
    """
    return {
        "recurrence_unit": str(ref_cache.recurrence_unit_id(unit)),
        "recurrence_placement": str(
            ref_cache.period_placement_id(PeriodPlacementEnum.CONTAINING_DATE),
        ),
        "interval_n": "1",
        "starts_on": starts_on.isoformat(),
        "repeats_on": repeats_on,
        **extra,
    }


def _refusal(schema_cls, submission):
    """Return the field errors *schema_cls* refuses *submission* with.

    Args:
        schema_cls: The schema class.
        submission: The posted values.

    Returns:
        ``{field: [message, ...]}``.
    """
    with pytest.raises(ValidationError) as refused:
        schema_cls().load(submission, partial=True)
    return refused.value.messages


class TestAWeekdayReadingTheDateAllows:
    """Each reading loads as the ``MonthReading`` it names."""

    @_SCHEMAS
    def test_the_third_tuesday_loads_as_read_from_the_start(self, app, schema_cls):
        """``from_start`` beside the 17th, a Tuesday."""
        with app.app_context():
            loaded = schema_cls().load(
                _submission(_THIRD_TUESDAY, "from_start"), partial=True,
            )

        assert loaded["repeats_on"] == MonthReading(
            weekday_reading=WeekdayReading.FROM_START,
        )
        assert loaded["starts_on"] == _THIRD_TUESDAY

    @_SCHEMAS
    def test_the_last_monday_loads_as_read_from_the_end(self, app, schema_cls):
        """``from_end`` beside the 30th, the month's last Monday."""
        with app.app_context():
            loaded = schema_cls().load(
                _submission(_LAST_MONDAY, "from_end"), partial=True,
            )

        assert loaded["repeats_on"] == MonthReading(
            weekday_reading=WeekdayReading.FROM_END,
        )


class TestAWeekdayReadingTheDateRefuses:
    """A field error on ``repeats_on``, naming what the date allows."""

    @_SCHEMAS
    def test_a_paycheck_cadence_reads_no_weekday_of_a_month(self, app, schema_cls):
        """A paycheck has no day of the month, so the sentence names the units that do."""
        with app.app_context():
            messages = _refusal(
                schema_cls,
                _submission(
                    _THIRD_TUESDAY, "from_start",
                    unit=RecurrenceUnitEnum.PERIOD,
                ),
            )

        assert messages == {
            "repeats_on": [
                "A paycheck or weekly schedule has no day of the month, so "
                "it cannot repeat on a weekday of the month. Choose a "
                "monthly or yearly schedule.",
            ],
        }

    @_SCHEMAS
    def test_a_fifth_monday_cannot_be_read_from_the_start(self, app, schema_cls):
        """No fifth (plan#2670): the 30th offers its day, the month's last or the last Monday."""
        with app.app_context():
            messages = _refusal(
                schema_cls, _submission(_LAST_MONDAY, "from_start"),
            )

        assert messages == {
            "repeats_on": [
                "Starting on Nov 30, 2026, this can repeat on the same day of "
                "the month, on the last day of the month or on the last "
                "Monday.",
            ],
        }

    @_SCHEMAS
    def test_a_third_tuesday_is_not_the_last(self, app, schema_cls):
        """The 17th is a fortnight from the end, so it offers its day or the third Tuesday."""
        with app.app_context():
            messages = _refusal(
                schema_cls, _submission(_THIRD_TUESDAY, "from_end"),
            )

        assert messages == {
            "repeats_on": [
                "Starting on Nov 17, 2026, this can repeat on the same day of "
                "the month or on the third Tuesday.",
            ],
        }


class TestANominalDayTheDateRefuses:
    """The same list behind the day it could not mean (ruling plan#2909)."""

    @_SCHEMAS
    @pytest.mark.parametrize("starts_on,repeats_on,message", [
        (
            date(2026, 4, 15), "30",
            "Apr 15, 2026 cannot mean day 30. Starting on that date, this can "
            "repeat on the same day of the month or on the third Wednesday.",
        ),
        (
            date(2026, 4, 30), "30",
            "Apr 30, 2026 cannot mean day 30. Starting on that date, this can "
            "repeat on the same day of the month, on the last day of the "
            "month or on the last Thursday.",
        ),
        (
            date(2026, 1, 31), "31",
            "Jan 31, 2026 cannot mean day 31. Starting on that date, this can "
            "repeat on the same day of the month or on the last Saturday.",
        ),
        (
            date(2028, 2, 29), "29",
            "Feb 29, 2028 cannot mean day 29. Starting on that date, this can "
            "repeat on the same day of the month, on the 30th, on the last "
            "day of the month or on the last Tuesday.",
        ),
    ], ids=["mid_month", "month_end", "a_month_that_clamps_nothing", "leap_day"])
    def test_the_sentence_lists_every_choice_the_date_allows(
        self, app, schema_cls, starts_on, repeats_on, message,
    ):
        """Word for word: the ruling's two, and the short and long of the list."""
        with app.app_context():
            messages = _refusal(schema_cls, _submission(starts_on, repeats_on))

        assert messages == {"repeats_on": [message]}

    @_SCHEMAS
    @pytest.mark.parametrize(
        "unit", [RecurrenceUnitEnum.PERIOD, RecurrenceUnitEnum.WEEK],
    )
    def test_a_schedule_with_no_day_of_the_month_lists_nothing(
        self, app, schema_cls, unit,
    ):
        """The control is not offered there, so the sentence names the units that do.

        One key is read because a payroll line refuses a weekly unit in its
        own right, beside this.
        """
        with app.app_context():
            messages = _refusal(
                schema_cls, _submission(_LAST_MONDAY, "31", unit=unit),
            )

        assert messages["repeats_on"] == [
            "A paycheck or weekly schedule has no day of the month, so it "
            "cannot repeat on day 31. Choose a monthly or yearly schedule.",
        ]

    @_SCHEMAS
    def test_the_controls_default_beside_a_paycheck_is_no_statement(self, app, schema_cls):
        """A blank control states no reading, so a paycheck rule still loads.

        The same day of the month is no choice beside a paycheck
        (``month_reading_choices`` is empty there), and what keeps it from
        being refused is that the empty token never reaches the rule: it is
        dropped as an untouched input.
        """
        with app.app_context():
            loaded = schema_cls().load(
                _submission(
                    _THIRD_TUESDAY, "", unit=RecurrenceUnitEnum.PERIOD,
                ),
                partial=True,
            )

        assert "repeats_on" not in loaded


class TestTheOldKeyIsGone:
    """``nominal_day`` is no field any more, so a nominal day beside a reading is unpostable."""

    @_SCHEMAS
    def test_a_posted_nominal_day_is_dropped_beside_a_reading(self, app, schema_cls):
        """``EXCLUDE`` drops the key: the one value read is ``repeats_on``'s.

        Were ``nominal_day`` still a field, this submission would state the
        exclusive arc's two halves at once
        (``ck_recurrence_rules_one_month_reading``); with one key there is
        one reading.
        """
        with app.app_context():
            loaded = schema_cls().load(
                _submission(_THIRD_TUESDAY, "from_start", nominal_day="31"),
                partial=True,
            )

        assert "nominal_day" not in loaded
        assert loaded["repeats_on"] == MonthReading(
            weekday_reading=WeekdayReading.FROM_START,
        )
