"""One paycheck's pay and payday take ONE bound, whichever door types them.

Finding **SAL-586** (plan step salary:X-av-3b): the salary schemas and the
pay-stub schemas each spelled the bound on what one paycheck's base pays, and
the window a payday may be typed in, so the pay list and the stub door could
come to accept different pay, or different paydays, for one fact (CLAUDE.md
rule 14).  Each has one home now, in ``app.schemas.validation._helpers``.

Two spellings that agree today are still two spellings, so agreement at the
edges cannot grade this: the identity tests below can, because a field that
spells its own ``Range`` again fails them even while its numbers match.  The
edge tests grade what the one bound says, through every field that reads it.
"""

from datetime import date, timedelta

import pytest
from marshmallow import validate

from app.schemas.validation.pay_stubs import PayStubPaydaySchema, PayStubSchema
from app.schemas.validation.salary import (
    SalaryPayEntryFixSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
)
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN

#: Every field that types what ONE paycheck's base pays, and the printed gross,
#: whose bound is the base's (the stub schema's module comment says why).
_PAY_FIELDS = [
    (SalaryProfileCreateSchema, "pay_amount"),
    (SalaryPayEntryFixSchema, "amount"),
    (PayStubSchema, "base_pay"),
    (PayStubSchema, "printed_gross"),
]

#: Every field that types a payday, or the stub dates a job learns its pay
#: period from (ruling plan#3096).
_PAYDAY_FIELDS = [
    (SalaryProfileCreateSchema, "pay_payday"),
    (SalaryProfileCreateSchema, "stub_pay_date"),
    (SalaryProfileCreateSchema, "stub_period_end"),
    (SalaryProfileUpdateSchema, "stub_pay_date"),
    (SalaryProfileUpdateSchema, "stub_period_end"),
    (SalaryPayEntryFixSchema, "payday"),
    (PayStubPaydaySchema, "payday"),
    (PayStubSchema, "payday"),
]


def _ids(cases):
    """Name each (schema, field) case for the test report."""
    return [f"{schema.__name__}.{field}" for schema, field in cases]


def _range_of(schema_cls, field) -> validate.Range:
    """Return the one ``Range`` validator *field* carries on *schema_cls*."""
    ranges = [
        v for v in schema_cls().fields[field].validators
        if isinstance(v, validate.Range)
    ]
    assert len(ranges) == 1, f"{schema_cls.__name__}.{field}: {ranges}"
    return ranges[0]


def _refuses(schema_cls, field, value) -> bool:
    """Return whether *schema_cls* refuses *value* in *field*.

    Only *field* is posted; the other required fields' errors are ignored,
    because the question is this field's bound alone.
    """
    return field in schema_cls().validate({field: value})


class TestThePayBoundHasOneHome:
    """Every pay field reads the one bound, ``_helpers._PAY_AMOUNT_RANGE``."""

    @pytest.mark.parametrize(("schema_cls", "field"), _PAY_FIELDS, ids=_ids(_PAY_FIELDS))
    def test_the_field_reads_the_one_bound(self, schema_cls, field):
        """The field's validators hold the shared instance itself.

        Input: the field as its schema declares it, beside the first pay
        field's (the profile form's ``pay_amount``).
        Expected: the very same ``Range`` object, by identity.
        Why: a ``Range`` spelled again with the same numbers passes every edge
        test below and is still a second home (finding SAL-586).
        """
        assert _range_of(schema_cls, field) is _range_of(*_PAY_FIELDS[0])

    @pytest.mark.parametrize(("schema_cls", "field"), _PAY_FIELDS, ids=_ids(_PAY_FIELDS))
    @pytest.mark.parametrize(
        ("value", "refused"),
        [
            ("0.00", True),           # not above zero, like both CHECKs
            ("0.01", False),          # the least a paycheck can pay
            ("10000000.00", False),   # the form-layer ceiling, inclusive
            ("10000000.01", True),    # one cent past it
        ],
    )
    def test_the_bound_s_edges_through_every_field(
        self, schema_cls, field, value, refused,
    ):
        """Above zero and at most $10,000,000.00, the same through each door.

        Input: each edge of the bound, posted to each pay field.
        Expected: zero and one cent past the ceiling refused; one cent and the
        ceiling itself taken.
        Why: the bound is what keeps a typo out of a paycheck's pay, and a
        door that took a figure another refused would store pay the other
        cannot (``ck_pay_entries_positive_amount``,
        ``ck_pay_stubs_positive_base_pay``).
        """
        assert _refuses(schema_cls, field, value) is refused


class TestThePaydayWindowHasOneHome:
    """Every payday field reads the one window, ``_helpers._PAYDAY_RANGE``."""

    @pytest.mark.parametrize(
        ("schema_cls", "field"), _PAYDAY_FIELDS, ids=_ids(_PAYDAY_FIELDS),
    )
    def test_the_field_reads_the_one_window(self, schema_cls, field):
        """The field's validators hold the shared instance itself.

        Input: the field as its schema declares it, beside the first payday
        field's (the profile form's ``pay_payday``).
        Expected: the very same ``Range`` object, by identity.
        Why: as for the pay bound, a re-spelled window agrees until it is
        changed in one place.
        """
        assert _range_of(schema_cls, field) is _range_of(*_PAYDAY_FIELDS[0])

    @pytest.mark.parametrize(
        ("schema_cls", "field"), _PAYDAY_FIELDS, ids=_ids(_PAYDAY_FIELDS),
    )
    @pytest.mark.parametrize(
        ("day", "refused"),
        [
            (CALENDAR_DATE_MIN - timedelta(days=1), True),
            (CALENDAR_DATE_MIN, False),
            (CALENDAR_DATE_MAX, False),
            (CALENDAR_DATE_MAX + timedelta(days=1), True),
        ],
        ids=["day-before-min", "min", "max", "day-after-max"],
    )
    def test_the_window_s_edges_through_every_field(
        self, schema_cls, field, day: date, refused,
    ):
        """The calendar's own reach, inclusive at both ends, through each door.

        Input: the days either side of each end of the calendar's window
        (``app.utils.dates``: 2000-01-01 through 2100-12-31).
        Expected: each end taken, the day past it refused.
        Why: a payday outside the window is a typo the calendar cannot walk
        to, and one door taking a payday another refuses is the disagreement
        the one home exists to prevent.
        """
        assert _refuses(schema_cls, field, day.isoformat()) is refused
