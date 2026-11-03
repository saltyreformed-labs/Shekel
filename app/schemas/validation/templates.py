"""Transaction-template create / update validation schemas."""


from marshmallow import (
    ValidationError,
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    _EFFECTIVE_DATE_RANGE,
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
    _reject_envelope_on_income,
)
from app.schemas.validation._recurrence import RecurrenceFormFieldsMixin


#: The sentence a transaction template stated with no cadence meets (ruling
#: **R-BAL23**, plan step ``balance:X-bi-7b``).  ONE spelling, shared by the
#: create and the update schema, so the two doors refuse in the same words.
A_CADENCE_IS_REQUIRED = (
    "A recurring transaction needs a cadence -- choose how it repeats. "
    "Something that happens once is added on the Budget grid instead."
)


class TemplateCreateSchema(RecurrenceFormFieldsMixin, BaseSchema):
    """Validates POST data for creating a transaction template.

    Includes a cross-field rule (``validate_envelope_only_on_expense``)
    that rejects ``is_envelope=True`` when ``transaction_type_id``
    refers to an income type.  Envelope rollover semantics (period-
    bounded amounts, leftover folds into the next period via
    ``Carry Fwd``) only apply to expense categories like groceries or
    spending money.  Income flows are settled via the
    ``Projected -> Received`` workflow and the discrete
    carry-forward path; they have no rollover.

    **A transaction template REQUIRES a cadence since plan step
    ``balance:X-bi-7b``** (ruling **R-BAL23**, developer 2026-09-13 for the
    edit form too): a transaction definition with no rule is a ONE-OFF, made
    at the Budget grid through ``one_off.place_one_off`` and never through
    this form, so the form's *Does not repeat* option is gone and an empty or
    absent ``recurrence_unit`` is REFUSED here
    (:meth:`validate_a_cadence_is_chosen`) rather than read as "author no
    rule".  The transfer schemas keep the option: that form IS the one-time
    transfer's door until the transfer's own step decides.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None.

        HTML forms always submit every <input> element, even hidden ones,
        as empty strings.  Without this hook, those empty strings fail
        OneOf / Integer validation on optional fields.  A nullable field
        (``end_date``) keeps the key as an explicit ``None`` so clearing it
        on update actually persists.
        """
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    category_id = RowId(required=True)
    transaction_type_id = RowId(required=True)
    account_id = RowId(required=True)

    # Tracking & visibility flags.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    # The recurrence controls this form submits are on
    # :class:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin`,
    # every one of them shared with the transfer form since plan step
    # recurrence:R5-a dropped the one it did not share, the bill's separate
    # due day (ruling R-R96: a rule's own day is the day its rows are due).

    @validates_schema
    def validate_a_cadence_is_chosen(self, data, **kwargs):
        """Refuse a transaction template stated with no cadence (**R-BAL23**).

        On a CREATE the unit is required outright: absent and empty are the
        same statement, that the owner chose nothing, and the form's
        placeholder posts the empty value.  :class:`TemplateUpdateSchema`
        narrows this to the PRESENT-and-empty case, because a partial update
        that omits the field means "leave the stored cadence alone".

        Raises:
            ValidationError: On the ``recurrence_unit`` field, with
                :data:`A_CADENCE_IS_REQUIRED`.
        """
        if data.get("recurrence_unit") is None:
            raise ValidationError(A_CADENCE_IS_REQUIRED, "recurrence_unit")

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on income transaction templates.

        Envelope semantics (the source of truth for the carry-forward
        ``settle-and-roll`` branch -- see
        ``docs/carry-forward-aftermath-design.md``) only make sense for
        expense categories.  An income flow that arrives late is handled
        by the existing status workflow, not by rolling unspent funds
        into the next period.

        The validator runs only when both ``is_envelope`` and
        ``transaction_type_id`` are present in the deserialized payload.
        ``TemplateUpdateSchema`` partial updates that omit
        ``transaction_type_id`` skip the schema-level check; the route
        layer falls back to the existing template's stored
        ``transaction_type_id`` (see ``_is_tracking_on_non_expense`` in
        ``app/routes/templates.py``) so the rule is enforced end-to-end.

        The error is attached to the ``is_envelope`` field for
        consistency with other cross-field validators in this module
        (e.g. ``validate_goal_mode_fields``); the route layer surfaces
        the message to the user via ``flash``.

        Raises:
            ValidationError: If ``is_envelope`` is True and
                ``transaction_type_id`` resolves to the Income type.
        """
        _reject_envelope_on_income(
            data,
            "Purchase tracking is only available for expense templates.",
        )


class TemplateUpdateSchema(TemplateCreateSchema):
    """Validates PUT data for updating a template.

    All fields optional (partial update), plus an effective date for
    recurrence regeneration.  Inherits the
    ``validate_envelope_only_on_expense`` cross-field rule from
    ``TemplateCreateSchema``; on partial updates that omit one of the
    two relevant fields, the validator returns early and the route
    layer applies the rule against the existing template's stored
    values.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    # An UPDATE may omit the first occurrence, and the omission MEANS
    # something: "leave the stored one alone".  See
    # ``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`` for the
    # ruling and for where the one authoring branch of an update is refused
    # instead.
    recurrence_start_is_required = False

    @validates_schema
    def validate_a_cadence_is_chosen(self, data, **kwargs):
        """Refuse CLEARING the cadence; an omitted unit leaves it alone.

        The update half of :meth:`TemplateCreateSchema.validate_a_cadence_is_chosen`.
        A submission that carries the field EMPTY is the placeholder (or the
        retired *Does not repeat*) being saved, which would delete the rule
        and turn a recurring definition into scattered one-offs listed
        nowhere -- refused.  A submission with no ``recurrence_unit`` at all is
        a partial update and states nothing about the cadence.

        Raises:
            ValidationError: On the ``recurrence_unit`` field, with
                :data:`A_CADENCE_IS_REQUIRED`.
        """
        if "recurrence_unit" in data and data["recurrence_unit"] is None:
            raise ValidationError(A_CADENCE_IS_REQUIRED, "recurrence_unit")

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    category_id = RowId()
    transaction_type_id = RowId()
    account_id = RowId()

    # The date this amount takes effect: STORED as a version of the
    # template's amount (plan step X-au-a) and, in the same value, the
    # bound the regeneration sweeps from.
    #
    # Bounded because an HTML date input accepts a four-digit-year typo
    # and the consequence is permanent: an adversarial review submitted
    # ``0202-08-11`` and it became the series' EARLIEST version, which
    # anchors every date before the series and which the withdrawal door
    # refuses to remove.
    # ``ck_template_amount_versions_effective_date_range`` mirrors the
    # window at the storage tier for raw-SQL writers.
    effective_from = fields.Date(validate=_EFFECTIVE_DATE_RANGE)

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))
