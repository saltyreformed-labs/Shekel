"""The "repeating on" field's weekday half at the schema door (plan step recurrence:R8-c).

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
November 2026 (3, 10, 17); Mon 2026-11-30 its fifth and last Monday.
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
        """No fifth (plan#2670): the 30th offers its day or the last Monday."""
        with app.app_context():
            messages = _refusal(
                schema_cls, _submission(_LAST_MONDAY, "from_start"),
            )

        assert messages == {
            "repeats_on": [
                "Starting on Nov 30, 2026, this can repeat on the same day of "
                "the month or on the last Monday.",
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
