"""The credit card's forms: its terms (plan step credit_card:CC-2) and its APR (CC-3).

**The terms.**  ONE schema for ONE door: the "Card terms" form on the cash detail page
renders every control every time, so a submit IS a whole version of the terms
(developer ruling **R-CC24**, 2026-09-18: one door, one schema), stated FOR the
date its ``effective_date`` names -- the door creates that version or corrects
the one already on that date (plan#2331, ruling **plan#2333**: the terms are a
dated series).  There is no
create / update pair here on purpose -- the loan has two because its CREATE
also seeds a rate row and posts a genesis ledger, and the interest door's
every-field-optional shape needs a "required when configuring for the first
time" branch that restates the NOT NULL rule the database already holds.  The
effective date and the five NOT NULL terms are REQUIRED, ``cashback_rate``
among them: the form renders it pre-filled with ``0.00`` for a card with no
terms, so a browser always posts a value, and a submit that carries none is a
crafted or truncated POST rather than a choice -- refused, never silently
written as zero over a rate the owner stated.  The two nullable columns read
an empty control as NULL, the "not set" state the model documents.

E-28 / HIGH-06 / PA-02: both rates are validated as decimal fractions pinned
to ``[0, 1]``, matching the model's CHECKs, and the ``@pre_load`` converts the
form percent (``"2.5"``) to its fraction (``"0.025"``) so the schema's
``Range`` and the DB CHECK accept exactly the same set of values.

**The APR.**  :class:`CardAprSchema` is the second form, two controls, for the
set-by-date door (developer ruling **R-CC27**).

**The payment.**  :class:`CardPaymentSchema` is the third, one control, for the
door that switches the card's payment between payoff rows and a payment
scheduled with the card company (plan step credit_card:CC-6, task plan#2679).
"""

from decimal import Decimal

from marshmallow import fields, pre_load, validate

from app.schemas.validation._helpers import (
    _DAY_OF_MONTH_RANGE,
    _EFFECTIVE_DATE_RANGE,
    _NON_NEGATIVE_MONETARY,
    _RATE_FRACTION_RANGE,
    BaseSchema,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)
from app.services.card_payoff import PAYMENT_CHOICES

#: A stored dollar figure that must be a real amount: above zero, under the
#: ceiling ``_NON_NEGATIVE_MONETARY`` states (read from it, so the two cannot
#: part).  The two nullable terms use it -- NULL is their "not set" state, so a
#: stored value is never zero (the model's ``IS NULL OR > 0`` CHECKs).
_POSITIVE_MONETARY = validate.Range(
    min=Decimal("0"), min_inclusive=False, max=_NON_NEGATIVE_MONETARY.max,
)


class CreditCardTermsSchema(BaseSchema):
    """Validates the card terms form: one whole version of the card's terms.

    Every field maps to a column of
    :class:`~app.models.credit_card_params.CreditCardParams` by name, so the
    door hands ``effective_date`` and the seven terms to
    :func:`~app.services.card_terms.set_terms` as they load.  The effective
    date is bounded to the calendar window every effective-dated field holds,
    so a four-digit-year typo cannot become the series' earliest version
    (which governs every day before it).  ``places`` mirrors each
    column's scale (``Numeric(5, 4)`` for the rates, ``Numeric(12, 2)`` for the
    dollar figures) so a stored value round-trips to the form without loss,
    the same contract the APY and escrow-inflation fields state.
    """

    _PERCENT_FIELDS = ("min_payment_percent", "cashback_rate")

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions.

        An empty ``auto_redeem_threshold`` or ``credit_limit`` becomes ``None``
        (their "not set" state); an empty required control -- the effective
        date or one of the five NOT NULL terms -- is dropped so the field's
        own "Missing data for required field." names it.
        """
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    effective_date = fields.Date(required=True, validate=_EFFECTIVE_DATE_RANGE)
    statement_close_day = fields.Integer(
        required=True, validate=_DAY_OF_MONTH_RANGE,
    )
    payment_due_day = fields.Integer(
        required=True, validate=_DAY_OF_MONTH_RANGE,
    )
    min_payment_percent = fields.Decimal(
        required=True, places=4, as_string=True, validate=_RATE_FRACTION_RANGE,
    )
    min_payment_floor = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # E-12: zero is a value, not missing -- so it is TYPED, never assumed.
    # The column's own default (``0`` on both tiers) serves the ORM
    # constructor and a raw INSERT; the door serves a form that always
    # carries the control, and a submit without it is refused above.
    cashback_rate = fields.Decimal(
        required=True, places=4, as_string=True, validate=_RATE_FRACTION_RANGE,
    )
    auto_redeem_threshold = fields.Decimal(
        allow_none=True, load_default=None, places=2, as_string=True,
        validate=_POSITIVE_MONETARY,
    )
    credit_limit = fields.Decimal(
        allow_none=True, load_default=None, places=2, as_string=True,
        validate=_POSITIVE_MONETARY,
    )


class CardAprSchema(BaseSchema):
    """Validates the card's APR form: one ``budget.rate_history`` row, by date.

    Two controls and nothing else (plan step credit_card:CC-3, developer
    ruling **R-CC27**): the door sets the row for ``effective_date``,
    creating it or rewriting its rate, so a submit IS the row and a same-date
    resubmit is the correction.  The loan's :class:`RateChangeSchema` is not
    reused because it carries ``monthly_pi``, a recast P&I a card never has
    -- a payload naming one would store a figure on a card's row that nothing
    reads.

    E-28 / HIGH-06 / PA-02: ``interest_rate`` is validated as a decimal
    fraction pinned to ``[0, 1]``, matching ``ck_rate_history_valid_interest_rate``,
    and the ``@pre_load`` converts the form percent (``"24.99"``) to its
    fraction (``"0.2499"``); ``places`` mirrors the column's ``Numeric(7, 5)``.
    The effective date is bounded to the calendar window every effective-dated
    field holds, so a four-digit-year typo cannot become the series' earliest
    row.
    """

    _PERCENT_FIELDS = ("interest_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert the percent field to a fraction."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    effective_date = fields.Date(required=True, validate=_EFFECTIVE_DATE_RANGE)
    interest_rate = fields.Decimal(
        required=True, places=5, as_string=True, validate=_RATE_FRACTION_RANGE,
    )


class CardPaymentSchema(BaseSchema):
    """Validates the card's payment form: which of the four payments it has.

    One radio group (task plan#2679): payoff rows, or the statement balance,
    the minimum or everything on the card on each statement's due date.  Its
    values are :data:`app.services.card_payoff.PAYMENT_CHOICES`' tokens, read
    from there so the form and the door cannot part; the door looks the
    token up there for the payment it names.
    """

    payment = fields.String(
        required=True, validate=validate.OneOf(tuple(PAYMENT_CHOICES)),
    )
