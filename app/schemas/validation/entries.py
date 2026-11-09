"""Transaction-entry create / update validation schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.services.entry_service import CHARGE, REFUND

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    ShownIds,
    _normalize_empty_inputs,
)


class EntryCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction entry.

    Requires amount (>= 0.01), description (1--200 chars), and
    ``purchased_on``.  ``on_card`` (the CC tick) defaults to False.

    **The ``>= 0.01`` bound is THIS FORM's rule and not the table's** (ruling
    **bank_import:R-II**).  ``transaction_entries`` requires only ``amount
    <> 0``, because a merchant credit files as a NEGATIVE purchase; what makes
    a typed negative wrong HERE is that a form takes a MAGNITUDE.  The
    bank-import door does not pass through this schema and is the caller that
    legitimately writes a signed figure
    (``statement_match._create._born_purchase``).

    **``direction`` is how a refund is stated, and it is why the bound can
    stay** (developer ruling **bank_import:R-IK**, 2026-09-01, plan step
    ``bank_import:X-gj-2b-3``).
    A purchase's sign is composed by
    :func:`~app.services.entry_service.purchase_amount` from this pair, so a
    minus is not refused here -- it is unrepresentable, on both this form and
    :class:`EntryUpdateSchema`.  It defaults to ``charge`` because that is what
    a purchase form is for and an omitted control must not silently mean the
    rarer, money-moving answer.

    **``settled_on`` is deliberately absent from the CREATE door.**  It records
    the day the user SAW the purchase on a bank statement, and at the moment a
    purchase is entered there is nothing to have seen.  It is set later, either
    by ticking the purchase at a balance true-up or by editing the entry -- see
    :class:`EntryUpdateSchema`.  Offering it here would invite a value that is
    a forecast rather than an observation.

    **``account_id`` is the account the purchase's money moved through** (plan
    step ``credit_card:CC-5-2``, ruling **R-CC15**): the card, for a swipe in
    a checking envelope.  OPTIONAL, and absent means the row's own account --
    the form posts it only when the owner has a card to choose (ruling
    **R-CC34**), so a form with no picker submits exactly what it did before
    this step.  The schema says only that it is a row id; WHOSE account it is,
    and whether it may hold a purchase, is ``entry_service.create_entry``'s
    gate against the ROW's owner.  It is absent from :class:`EntryUpdateSchema`
    because the update door does not move a purchase between accounts.

    **``on_card`` is the CC tick and ``card_account_id`` its pick** (plan task
    plan#2930, ruling plan#2888): ticked, the purchase goes on the owner's
    card, and the form draws the pick only for two or more cards.  The add
    door wrote the CC flag from the tick until then; it writes none now
    (``entry_service.EntryDetails``).  Which card, and whose, is the door's.
    **The tick still posts as ``is_credit``** (``data_key``), so an add form
    drawn before the deploy, whose box posts ``is_credit=on``, puts its
    purchase on the card as the ruling says, rather than having the field
    dropped as unknown and the purchase filed on checking unasked (the
    review of plan#2930, M3: grid pages stay open across a deploy).
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    direction = fields.String(
        load_default=CHARGE, validate=validate.OneOf([CHARGE, REFUND]),
    )
    description = fields.String(
        required=True, validate=validate.Length(min=1, max=200),
    )
    purchased_on = fields.Date(required=True)
    on_card = fields.Boolean(load_default=False, data_key="is_credit")
    card_account_id = RowId()
    account_id = RowId()


class EntryUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction entry.

    All fields optional for partial updates.  When present, the same
    validation rules as EntryCreateSchema apply.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    # **No lower bound, and its absence is the ruling** (developer
    # 2026-08-31, ruling **bank_import:R-II**).  ``EntryCreateSchema`` keeps
    # **THE BOUND IS BACK, AND THE SIGN IS A CONTROL** (developer ruling
    # 2026-09-01, plan step ``bank_import:X-gj-2b-3``).
    #
    # Its history is the argument.  ``Range(min=0.01)`` stood here until ruling
    # **bank_import:R-II**, then came off because the inline form renders
    # ``value="{{ entry.amount }}"`` and submits every control it holds -- so a
    # stored refund could not be re-described or re-dated while the box refused
    # a negative.  That was a real defect and the remedy was aimed at the wrong
    # tier: it deleted a domain rule to fix a FORM that validates a field the
    # owner did not touch.
    #
    # What it cost, and this door is reached ONLY by the human PATCH route
    # (``routes/entries.py`` builds the one instance) -- the bank-import door
    # writes through ``entry_service`` and passes no schema at all.  So both
    # doors carrying this rule are doors a person types at, and one of them had
    # no bound: a typed ``-45.00`` where ``45.00`` was meant booked a REFUND in
    # silence, moved the projection by twice the figure, and said nothing on
    # any screen -- while the identical keystroke on the add form 180 lines
    # below was a 422.
    #
    # Both forms take a MAGNITUDE now and state the direction beside it, and
    # ``entry_service.purchase_amount`` composes the stored sign.  A minus is
    # not refused; it cannot be expressed.  ``direction`` is OPTIONAL here
    # because this is a partial update -- an edit that touches only the
    # description sends neither field -- and the route pairs them: an amount
    # without a direction cannot be composed, so it is refused rather than
    # guessed.
    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    direction = fields.String(validate=validate.OneOf([CHARGE, REFUND]))
    description = fields.String(validate=validate.Length(min=1, max=200))
    purchased_on = fields.Date()
    # ``allow_none`` is what makes an emptied date input CLEAR the observation
    # rather than being dropped as "not provided" (see
    # ``_normalize_empty_inputs``).  Clearing it is a real user action: "I
    # ticked this as posted and the statement does not actually show it", which
    # must put the purchase back among the outstanding ones.
    settled_on = fields.Date(allow_none=True)
    is_credit = fields.Boolean()
    # The bank lines the edit form's CC caption NAMED (plan step
    # ``credit_card:CC-5-4a-5``, rulings **R-CC80** / **R-CC127**): what
    # un-ticking the envelope's last card purchase frees when it deletes a
    # payback a match names.  Posted by the owner's edit form, empty when it
    # names none.  ``allow_none`` is presence:
    # :class:`~app.schemas.validation._helpers.ShownIds` says why.
    shown_lines = ShownIds(allow_none=True)

    # Optimistic-locking pin (commit C-18).
    version_id = RowId(validate=validate.Range(min=1))
