"""Transfer-template and ad-hoc transfer validation schemas."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.schemas.validation._helpers import (
    _EFFECTIVE_DATE_RANGE,
    BaseSchema,
    RowId,
    ShownIds,
    _NON_NEGATIVE_MONETARY,
    _normalize_empty_inputs,
    reject_figure_without_its_rendered_companion,
)
from app.schemas.validation._recurrence import RecurrenceFormFieldsMixin


SAME_ACCOUNT_TRANSFER_MESSAGE: str = "From and To accounts must be different."
"""Refusal for a transfer whose source and destination are one account.

Spelled once for the two doors that ask it: this module's
:func:`_reject_same_account_transfer`, which grades the pair a submission
CARRIES, and the transfer-template update route, which grades the pair the
write would LEAVE when the submission carries only one of the keys
(``routes/transfers/_helpers._first_template_fk_refusal``, plan step R7d-f-5).
"""


def _reject_same_account_transfer(data):
    """Reject a transfer whose source and destination are the same account.

    Shared cross-field rule for the transfer-template and ad-hoc transfer
    create schemas (DRY -- one implementation of the check).  A
    self-transfer moves no money: its two legs would sit on one account and
    net to zero; the route surfaces the message to the user.

    Runs only when both ``from_account_id`` and ``to_account_id`` are
    present in the deserialized payload -- a schema grades the WIRE, and a
    partial update carrying one key leaves the other stored.  The pair the
    write would LEAVE is graded at the update route, where the stored row is
    known (``_first_template_fk_refusal``), with the same sentence.

    Raises:
        ValidationError: If ``from_account_id`` equals ``to_account_id``.
    """
    if data.get("from_account_id") and data.get("to_account_id"):
        if data["from_account_id"] == data["to_account_id"]:
            raise ValidationError(SAME_ACCOUNT_TRANSFER_MESSAGE)


class TransferTemplateCreateSchema(RecurrenceFormFieldsMixin, BaseSchema):
    """Validates POST data for creating a transfer template."""

    # **The ONE create schema that may not require a first occurrence**, and
    # the exception is the DESTINATION rather than a relaxation (plan step
    # R7c-b, developer ruling 2026-08-15).  This form offers every active
    # account as a destination, so a recurring LOAN PAYMENT can be created
    # here -- and a loan payment's first occurrence is the loan's first
    # contractual installment, which the app derives and the form therefore
    # renders locked.  A locked control posts nothing.
    #
    # The rule is not dropped, it MOVED to where the destination is known:
    # ``_loan_destination.settle_first_occurrence`` derives it for a
    # loan and refuses a cadence without one for anything else, with this
    # module's own ``RECURRENCE_NEEDS_A_START``.  A schema never learns which
    # accounts are loans, which is the same reason the update path's one
    # authoring branch is refused in its route.
    #
    # ``TemplateCreateSchema`` keeps the requirement: a transaction template
    # has no destination account and can never be a loan payment.
    recurrence_start_is_required = False

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    from_account_id = RowId(required=True)
    to_account_id = RowId(required=True)
    category_id = RowId(required=True)

    # Every recurrence control this form submits is on
    # :class:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin`.

    # The pay period a NON-REPEATING transfer lands in
    # (``_instances._materialize_one_time_transfer``), and the transfer form's
    # alone.  It is NOT a recurrence control and has not been one since plan
    # step R7b-4: it sat on the shared mixin while ONE ``<select>`` meant two
    # things -- "First paycheck" for a repeating definition and "Pay period"
    # for a one-time transfer, relabelled by script on every unit change --
    # and that step gave the first meaning its own DATE control.  What is left
    # is a question only this kind can ask, so it is declared where that kind
    # is.  A transaction template that does not repeat generates nothing and
    # waits for the user; a transfer that does not repeat still moves money
    # exactly once, and this says when.
    start_period_id = RowId()

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        """Reject a transfer whose source and destination are the same account."""
        _reject_same_account_transfer(data)


class TransferTemplateUpdateSchema(TransferTemplateCreateSchema):
    """Validates PUT data for updating a transfer template.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    # An UPDATE may omit the first occurrence, and the omission MEANS
    # something: "leave the stored one alone".  See
    # ``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`` for the
    # ruling and for where the one authoring branch of an update is refused
    # instead.  It is what lets a loan payment -- whose bound the app derives,
    # and whose control therefore renders disabled -- be renamed at all.
    recurrence_start_is_required = False

    # Override -- all fields optional for update.
    name = fields.String(validate=validate.Length(min=1, max=200))
    default_amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    from_account_id = RowId()
    to_account_id = RowId()
    category_id = RowId(allow_none=True)

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


class TransferCreateSchema(BaseSchema):
    """Validates POST data for creating an ad-hoc transfer."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    from_account_id = RowId(required=True)
    to_account_id = RowId(required=True)
    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    pay_period_id = RowId(required=True)
    scenario_id = RowId(required=True)
    name = fields.String(validate=validate.Length(max=200))
    category_id = RowId(required=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)

    @validates_schema
    def validate_different_accounts(self, data, **kwargs):
        """Reject a transfer whose source and destination are the same account."""
        _reject_same_account_transfer(data)


class TransferUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transfer (inline edit).

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    amount = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False)
    )
    # WHAT THE FORM SHOWED in the Amount box, posted back beside whatever came
    # out of it (ruling **R-JR**, plan step X-au-h).  The transfer twin of
    # ``TransactionUpdateSchema.estimated_amount_as_rendered`` -- see it for why
    # the field is declared rather than left to ``unknown=EXCLUDE``, and why an
    # empty one reads as ABSENT.  ``routes._authored_figure`` states the rule
    # both doors apply.
    #
    # **On this table the companion is what keeps the question answerable at
    # all.**  Plan step X-au-f empties ``budget.transfers.amount`` for a
    # generated transfer, and the comparison this replaces read that column --
    # so ``submitted != stored`` would have been true for every call and every
    # save would have claimed a human re-priced the row (findings **N-436** and
    # **N-448**).  Comparing against what was RENDERED removes that dependency.
    #
    # **The debt this comment used to record is PAID, at plan step X-au-f-1.**
    # It read: both transfer templates render the box and its companion from
    # ``xfer.amount`` -- the raw column -- and Jinja renders ``None`` as the
    # literal string ``"None"``, which this field rejects; so X-au-f owed those
    # templates the resolved figure.  It does now.  Both forms read
    # ``budgets[xfer.id]`` off ``_render_helpers.transfer_budgets``, and the box
    # and its companion read the SAME expression, which is what makes the
    # comparison above answer about a figure rather than about a column.
    # Graded by
    # ``test_a_DERIVED_transfer_renders_its_definitions_price_not_its_column``,
    # which fails on the code that edit replaced.  What survives is the refusal
    # itself: this field still rejects ``"None"``, so a template that regressed
    # to the column would fail loudly at the door rather than silently.
    amount_as_rendered = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0, min_inclusive=False),
    )

    @validates_schema
    def reject_a_figure_that_does_not_say_what_was_rendered(self, data, **kwargs):
        """Refuse an ``amount`` with no rendered companion (**R-JR**).

        See
        :func:`~app.schemas.validation._helpers.reject_figure_without_its_rendered_companion`
        for why a figure alone cannot be judged, and why refusing beats guessing
        in either direction.
        """
        reject_figure_without_its_rendered_companion(data, "amount")
    status_id = RowId()
    pay_period_id = RowId()
    name = fields.String(validate=validate.Length(max=200))
    category_id = RowId(allow_none=True)
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # The civil day the money moved, ONE BOX PER SIDE (ruling R-BAL108; plan
    # step balance:X-bi-6-4c-3, where it was one ``settled_on`` for both
    # shadows, ruling R-ED / plan step X-f1c).  ``settled_on_from`` is the
    # side the money leaves, ``settled_on_to`` the side it arrives at; the
    # route grades each against what THAT box was prefilled from, and a box
    # for a side borrowing the other's day renders EMPTY (ruling R-BAL164), so
    # any day typed there is that side's own.  Editable on a finalised
    # transfer for the reason ``routes/transactions/_helpers._LOCKED_EDIT_FIELDS``
    # carries: the locked fields are budget decisions, and this is an observed
    # fact about the bank.
    #
    # This door is the ONLY door onto the days finding **N-181** names: all 8
    # settled rows whose day the X-f1b backfill had to invent from a pay
    # period's start were transfer SHADOWS (four pairs), and a transfer's
    # days are its two side records' since plan step balance:X-bi-6-4d-2.
    #
    # Deliberately NOT ``allow_none``: an empty input loads as ABSENT ("leave
    # the day alone"), never as a request to clear it -- the service's stated
    # day cannot wrap ``None`` -- and the way to remove a day is to revert the
    # transfer to Projected.
    settled_on_from = fields.Date()
    settled_on_to = fields.Date()

    # WHAT MOVED, the transfer's one figure (developer ruling, 2026-08-17).
    # The Actual box: what the bank really took, which is a DIFFERENT fact
    # from the ``amount`` above and gets its own input rather than
    # overwriting the plan.
    #
    # Editable on a finalised transfer for the reason the day boxes above are,
    # and the two are one assertion's two halves: the #26 lock protects BUDGET
    # DECISIONS from being rewritten, and what the bank took is an OBSERVED
    # FACT, corrected when the statement disagrees.  Until this field existed a
    # transfer's DAY was correctable in place and its FIGURE was not, so
    # restating what moved meant reverting -- and a revert RETAINS the recorded
    # figure, so the re-settle re-booked the old number over the re-planned one.
    #
    # A figure riding a settling ``status_id`` is the settle's; one on a pair
    # that stays settled is a CORRECTION; one on any other status is refused by
    # ``transfer_service._update._grade_submitted_figure`` with a designed
    # 400.  ``allow_none`` because an HTML form submits every input including
    # the empty ones, and an empty box means nobody typed a figure.
    #
    # **``_NON_NEGATIVE_MONETARY`` rather than a bare lower bound**, and the
    # ceiling is the load-bearing half: the figure is stored as the transfer's
    # ``settled_figure``, ``Numeric(12, 2)``, so a figure at or above
    # `10 ** 10` validates, reaches the seam, and dies at
    # the DATABASE with a ``DataError`` -- which is NOT a subclass of the
    # ``IntegrityError`` this route catches, so it surfaces as a 500 and the
    # user's Save appears to do nothing.  Plan step X-f2-c3 measured exactly
    # that on ``MarkDoneSchema`` and fixed it there; this field and its
    # transaction twin were declared with a bare ``Range(min=0)`` and inherited
    # the hole (found by adversarial review, 2026-08-18).
    settled_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=_NON_NEGATIVE_MONETARY,
    )

    # The bank lines the popover's withdrawal captions NAMED (plan step
    # ``credit_card:CC-5-4a-5``, ruling **R-CC127**): posted on every popover
    # Save, empty when they name none.  ``allow_none`` is presence:
    # :class:`~app.schemas.validation._helpers.ShownIds` says why.
    shown_lines = ShownIds(allow_none=True)

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))
