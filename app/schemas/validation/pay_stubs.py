"""Pay stub entry schemas (plan step salary:S11-b).

The entry door's value rules for a transcribed pay stub, moved out of
:mod:`app.schemas.validation.salary` when plan step ``salary:S11-c-1`` added
:class:`PayStubLineSchema` and the salary module passed pylint's 1000-line
bound: the stub entry door is its own subject beside the salary profile's.
"""

from decimal import Decimal

from marshmallow import fields, pre_load, validate

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _NON_NEGATIVE_MONETARY,
    _normalize_empty_inputs,
)
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN


# ── Pay stub entry (plan step salary:S11-b) ──────────────────────────
#
# A transcribed stub is posted as scalars plus three FAMILIES of figures whose
# members the owner's data decides (one input per paycheck line, one per tax,
# a few one-off rows), so the route reads each family's inputs off the form
# and loads every member through the one schema below that states its value
# rule.  What the figures must add up to, and which names clash, is the
# service's (``app.services.pay_stub_service``): those are rules about the
# owner's lines, not about a field.

#: A stub's payday is a date the pay calendar can walk to: an HTML date input
#: accepts a five-digit-year typo, and ``9999-12-31`` overflows the calendar's
#: forward projection (``app.utils.dates``, whose constants these are).
_STUB_PAYDAY_RANGE = validate.Range(min=CALENDAR_DATE_MIN, max=CALENDAR_DATE_MAX)

#: A stub's base pay and the gross it prints are above zero: the base like its
#: column (``ck_pay_stubs_positive_base_pay``), and the gross because it is the
#: base plus earnings that are never negative.
_STUB_PAY_RANGE = validate.Range(min=Decimal("0"), min_inclusive=False, max=Decimal("10000000"))


class PayStubPaydaySchema(BaseSchema):
    """Validates the entry flow's FIRST step: the stub's payday alone.

    Ruling **R-SAL50**, "Pick the payday first": the payday is chosen before
    the form is built, because which lines the form lists first is the app's
    answer for THAT payday.  Whether the day is one of the owner's paydays is
    the service's refusal (:func:`~app.services.pay_stub_service.payday_refusal`).
    """

    payday = fields.Date(required=True, validate=_STUB_PAYDAY_RANGE)


class PayStubSchema(BaseSchema):
    """Validates a stub's own fields on the entry form's second step.

    ``printed_gross`` and ``printed_net`` are the gross and net the stub
    prints, each typed once as a CHECK and never stored (rulings **R-SAL99**
    and **R-SAL42**); ``base_pay`` is above zero like the column
    (``ck_pay_stubs_positive_base_pay``).  The edit form's ``version_id`` is
    not here: the edit route reads it FIRST, before any field, so a stale form
    is turned away even when its fields are invalid.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    payday = fields.Date(required=True, validate=_STUB_PAYDAY_RANGE)
    base_pay = fields.Decimal(
        required=True, places=2, as_string=True, validate=_STUB_PAY_RANGE,
    )
    printed_gross = fields.Decimal(
        required=True, places=2, as_string=True, validate=_STUB_PAY_RANGE,
    )
    printed_net = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))


class PayStubFigureSchema(BaseSchema):
    """Validates ONE stub figure: a paycheck line's amount, or a tax's.

    ``$0.00`` is a real figure (a federal line fully offset by credits), so
    the bound is ``>= 0`` like the three child tables' CHECKs.
    """

    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )


class PayStubLineSchema(PayStubFigureSchema):
    """Validates what a stub prints for one paycheck line: its amount and its kind.

    Ruling **R-SAL58**, "Stub records its kind": the kind the stub prints the
    line under, which the entry form pre-sets to the paycheck line's own and
    the owner changes only where the stub prints it under another heading.
    Whether the id names one of the five kinds is the service's NOT-FOUND, as
    a one-off's is.
    """

    paycheck_line_kind_id = RowId(required=True)


class PayStubOneOffSchema(PayStubFigureSchema):
    """Validates one one-off row: a name, one of the five kinds, an amount.

    Fork 8b, "Keep it as a one-off".  The name is stored trimmed; a blank one
    is refused here as the table refuses it (``ck_pay_stub_one_offs_name_not_blank``).
    """

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    paycheck_line_kind_id = RowId(required=True)

    @pre_load
    def trim_name(self, data, **kwargs):
        """Trim the name's outer spaces, so an all-space name is a missing one."""
        data = dict(data)
        if isinstance(data.get("name"), str):
            data["name"] = data["name"].strip()
            if not data["name"]:
                del data["name"]
        return data
