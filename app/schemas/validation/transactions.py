"""Ad-hoc transaction and mark-done validation schemas."""


from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    ShownIds,
    _NON_NEGATIVE_MONETARY,
    _normalize_empty_inputs,
    _reject_envelope_on_income,
    reject_figure_without_its_rendered_companion,
)


class TransactionUpdateSchema(BaseSchema):
    """Validates PATCH data for updating a transaction: the ROW's own fields.

    **Loaded for a row whose item is NOT editable at the popover** -- a
    recurring definition's generated row -- and inherited by
    :class:`TransactionItemUpdateSchema` for every other row.  The two
    tracking / visibility flags left this schema at plan step
    ``balance:X-bi-7b`` (ruling **R-BAL20**): a generated row reads them off
    its definition, so a flag posted for such a row reached only the sealed,
    dead cell on the row -- finding **BAL-484**'s writer.  Declared nowhere on
    this schema, that write is unrepresentable through the PATCH door rather
    than refused by a gate: ``Meta.unknown`` is ``EXCLUDE``.

    ``name`` and ``category_id`` stay here.  A one-off's are its
    DEFINITION's and the door lands them there
    (``routes/transactions/_field_updates``); a transfer shadow's and a CC
    payback's are their own; a generated row's are its
    definition's too, and a crafted PATCH still writes the row's copy, which
    the next regeneration rewrites -- the same shape the due-date gate
    closed for that field and no step has yet closed for these two.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the cell or popover was rendered.  The route handler
    compares the submitted value against ``Transaction.version_id``
    and short-circuits with 409 Conflict if they differ -- a stale-
    form check that catches the Tab-1/Tab-2 race even when the two
    requests are sequential rather than truly concurrent.  Optional
    so callers without a way to plumb the version through still
    pass validation; in that case only the SQLAlchemy
    ``version_id_col`` race detection applies, which catches the
    truly-concurrent case at flush time.  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(places=2, as_string=True, validate=validate.Range(min=0))
    # WHAT THE FORM SHOWED in the Estimated box, posted back beside whatever
    # came out of it (ruling **R-JR**, plan step X-au-h).  It is not written to
    # any column and never reaches the row: the door compares the two to decide
    # whether a HUMAN authored this figure, which is the question
    # ``routes._authored_figure`` states in full.
    #
    # **Declared here so a submission carrying one is not silently discarded.**
    # ``Meta.unknown`` is ``EXCLUDE``, so an undeclared companion would vanish
    # in ``load()`` -- and the rule below, seeing it absent, would then refuse
    # every save this form makes.  Declaring it is what makes the refusal a
    # statement about the FORM rather than about this schema.
    #
    # Not ``allow_none``: an empty companion means the form stated nothing
    # about what it rendered, and ``_normalize_empty_inputs`` drops it so the
    # payload reads as ABSENT -- which the rule below refuses, rather than
    # letting an explicit null read as a figure nobody rendered.
    estimated_amount_as_rendered = fields.Decimal(
        places=2, as_string=True, validate=validate.Range(min=0),
    )

    @validates_schema
    def reject_a_figure_that_does_not_say_what_was_rendered(self, data, **kwargs):
        """Refuse an ``estimated_amount`` with no rendered companion (**R-JR**).

        See
        :func:`~app.schemas.validation._helpers.reject_figure_without_its_rendered_companion`
        for why a figure alone cannot be judged, and why refusing beats guessing
        in either direction.
        """
        reject_figure_without_its_rendered_companion(data, "estimated_amount")
    # WHAT MOVED, and the ONLY thing this door may do with one is hand it to a
    # SETTLE arriving in the same request (plan step X-au-c3).  A figure without
    # a settling status is refused by
    # ``transaction_service.apply_requested_status`` with a designed 400.
    #
    # **The full-edit popover's Actual box submits it** (developer ruling,
    # 2026-08-17): a settled row's figure is an observed fact, so it is
    # correctable in place rather than only by reverting.  It stayed declared
    # through the period when no form sent one, and that is worth keeping if a
    # form ever stops: ``Meta.unknown`` is ``EXCLUDE``, so deleting the field
    # would make a stale or crafted submission's figure vanish in silence
    # instead of being refused, and a silently dropped money field is how a
    # user's typed number disappears.
    # ``allow_none`` because an HTML form submits every input including the
    # empty ones, and an empty box means nobody typed a figure.
    #
    # **Bounded ABOVE as well**, by the shared monetary ceiling: the column is
    # ``Numeric(12, 2)``, so a bare lower bound let a figure at or above
    # `10 ** 10` reach the database and die there as a ``DataError`` -- a
    # sibling of ``IntegrityError``, not a subclass, so this route's except-list
    # missed it and the user's Save 500'd in silence.  ``MarkDoneSchema`` below
    # already carries the ceiling for the measurement plan step X-f2-c3 made;
    # this door did not until an adversarial review found the gap (2026-08-18).
    settled_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    status_id = RowId()
    pay_period_id = RowId()
    category_id = RowId()
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # The civil day the money moved (ruling R-ED, plan step X-f1c).  Editable on
    # a FINALISED row, unlike every other money field: the locked set protects
    # BUDGET DECISIONS the user made (amount, period, category, due date) from
    # being rewritten after the fact, and this is not one -- it is an OBSERVED
    # FACT about their bank, which gets corrected when the statement disagrees.
    # The same line ``TransactionEntry`` draws one table over, where
    # ``purchased_on`` is guarded and ``settled_on`` is freely editable.
    #
    # Deliberately NOT ``allow_none``: an empty input loads as ABSENT (see
    # ``_normalize_empty_inputs``), i.e. "leave the day alone", never as a
    # request to clear it.  A settled row always carries the day its money
    # moved -- the way to remove one is to move the row out of the settled
    # band, which the status seam does as part of the same write.
    settled_on = fields.Date()
    # WHICH ACCOUNT the money moved through -- the TENDER (plan step
    # ``credit_card:CC-5-3``, ruling **R-CC15**): the popover's "Paid from"
    # picker, posted on every Save beside ``settled_amount`` and ``settled_on``
    # and read the same way -- an echo of what the row records is dropped, a
    # different account beside a settling or settled status re-points the
    # payment's covering movement, and one beside a status that settles
    # nothing is refused (``status_seam.tender_for_status``).  It reaches the
    # record only through ``transaction_service.apply_requested_status``,
    # never the ``setattr`` loop (``_SEAM_OWNED_FIELDS``).  The row's OWNER
    # gates it, never the caller (ruling **R-CC11**); a foreign id is the
    # door's 404.  Rendered only when the owner's cash-flow set offers a
    # choice (ruling **R-CC34**), so a one-account form posts nothing here
    # and ``_normalize_empty_inputs`` drops an empty select.
    tender_account_id = RowId()
    # The bank lines the popover's withdrawal captions NAMED (plan step
    # ``credit_card:CC-5-4a-5``, rulings **R-CC81** / **R-CC127**): posted on
    # every popover Save, empty when they name none, so the act a Save reaches
    # can refuse a press whose page was out of date.  Absent from the grid's
    # quick edit, whose door then names nothing.  ``allow_none`` is presence:
    # :class:`~app.schemas.validation._helpers.ShownIds` says why.
    shown_lines = ShownIds(allow_none=True)
    version_id = RowId(validate=validate.Range(min=1))


class TransactionItemUpdateSchema(TransactionUpdateSchema):
    """The row's fields plus the flags of the ITEM the row is.

    Loaded by the PATCH door for a PLACED row alone -- a rule-less
    definition's, whose tracking / visibility flags are the definition's and
    land there (plan step ``balance:X-bi-7b``, rulings **R-BAL23**,
    **R-BAL36**).  Until the family's cutover (``balance:X-bi-7d-2``, ruling
    **R-BAL73**) it was loaded for every non-recurring row: a legacy
    link-less one-off stated its flags in cells of its own, and a transfer
    shadow or a CC payback rode along.  Those cells are gone, a shadow and a
    payback have no definition, and both now load the row schema, which
    declares no flag.

    The flags carry deliberately NO load_default: the quick-edit and inline
    PATCH forms render no flag control, and without a default a PATCH that
    omits them leaves the flags untouched, so a quick-edit cannot silently
    clear them.  The popover uses a checkbox + hidden "false" field so an
    explicit true/false is always submitted when the controls are present.
    """

    is_envelope = fields.Boolean()
    companion_visible = fields.Boolean()
    # The DEFINITION's optimistic-locking counter, rendered by the card for a
    # placed row (whose price, name, category and flags live there) and
    # compared by the route beside the row's own ``version_id``: a save that
    # touches only the definition bumps only its counter, so without this a
    # second stale card would overwrite the first's price in silence.  The
    # route ignores it for any row that is not placed.
    template_version_id = RowId(validate=validate.Range(min=1))


class TransactionCreateSchema(BaseSchema):
    """Validates POST data for creating a ONE-OFF from the Add Transaction modal.

    A one-off is a rule-less DEFINITION plus its placed row since plan step
    ``balance:X-bi-7b`` (ruling **R-BAL20**), so this schema validates the
    inputs to BOTH: what the definition says (``name``, the figure, the
    category, the type, the two flags) and where its row goes (the account,
    the paycheck, the scenario, an optional due date and note).  The route
    hands the first set to ``one_off.OneOffToPlace`` and the second to the
    producer's placing arguments.
    """

    name = fields.String(required=True, validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    account_id = RowId(required=True)
    pay_period_id = RowId(required=True)
    scenario_id = RowId(required=True)
    category_id = RowId(required=True)
    transaction_type_id = RowId(required=True)
    # No ``status_id``: a transaction is born Projected (the route assigns it
    # unconditionally).  The only path to a settled status is the status seam
    # (``status_seam.apply_status_change``), so a submitted status is dropped by
    # ``unknown=EXCLUDE`` rather than minting a born-settled row that
    # would have no settle day, bypass ``verify_transition``, and post
    # nothing to the ledger.  Record an already-paid item by creating it
    # Projected, then marking it done.
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    due_date = fields.Date(allow_none=True)
    # The DEFINITION's tracking / visibility flags (plan step
    # ``balance:X-bi-7b``): the one-off's definition carries them and every
    # row it places reads them from there; the row's own cells are never
    # written by a create.  load_default=False so a create that omits them
    # (the modal's unticked boxes) defaults to off, which is the correct
    # baseline for a brand-new plan item.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on an income one-off's definition."""
        _reject_envelope_on_income(
            data, "Purchase tracking is only available for expenses."
        )


class InlineTransactionCreateSchema(BaseSchema):
    """Validates POST data for creating a ONE-OFF from a grid cell.

    The same definition-plus-row split :class:`TransactionCreateSchema`
    describes, minus the due date the cell's forms do not offer (the row
    takes the day ``one_off.due_date_for`` gives one with none stated).
    Unlike TransactionCreateSchema, the name field is OPTIONAL: the
    quick-create form offers it so an ad-hoc row can be named at the
    Tier-1 entry point (grid audit A5), and the route falls back to the
    category display name when it is omitted or left blank (the
    ``strip_empty_strings`` hook drops an empty submit, so the loaded
    payload simply lacks the key).
    """

    name = fields.String(validate=validate.Length(min=1, max=200))
    estimated_amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=0),
    )
    account_id = RowId(required=True)
    category_id = RowId(required=True)
    pay_period_id = RowId(required=True)
    transaction_type_id = RowId(required=True)
    scenario_id = RowId(required=True)
    # No ``status_id``: born Projected (see TransactionCreateSchema).  A
    # submitted status is dropped by ``unknown=EXCLUDE``; the route assigns
    # Projected and the status seam owns every later transition.
    notes = fields.String(allow_none=True, validate=validate.Length(max=500))
    # The DEFINITION's tracking / visibility flags, as on
    # :class:`TransactionCreateSchema`.  load_default=False so the
    # quick-create form (which omits these controls) defaults to off.
    is_envelope = fields.Boolean(load_default=False)
    companion_visible = fields.Boolean(load_default=False)

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    @validates_schema
    def validate_envelope_only_on_expense(self, data, **kwargs):
        """Reject ``is_envelope=True`` on an income one-off's definition."""
        _reject_envelope_on_income(
            data, "Purchase tracking is only available for expenses."
        )


class MarkDoneSchema(BaseSchema):
    """Validates POST data for the mark-done status route.

    Used by ``transactions.mark_done`` (both transfer-shadow and
    regular branches) and, since plan step X-f2-c2, by the reconcile
    panel's amount boxes
    (``routes.accounts.reconcile._submitted_corrections``) -- the same
    question feeding the same parameter of the same verb, so one field
    declaration answers it.  Marshmallow's Decimal field rejects
    malformed numeric input with a clean field-level 400 instead of
    the route's catch-and-translate 400, and
    ``_NON_NEGATIVE_MONETARY`` is the schema-tier counterpart to
    ``status_seam.Settlement``'s refusal of a negative figure (the DB
    CHECK ``ck_transactions_settled_amount`` on the row's own
    ``settled_amount`` said the same through plan step
    ``balance:X-bi-4b-1``; that column, ``actual_amount`` until X-au-c3,
    went at X-bi-4b-2 and the record is the covering movement).

    **Its UPPER bound is what plan step X-f2-c3 added, and the lower
    half alone was a 500.**  The record's figure
    (``budget.transaction_entries.amount``) is ``numeric(12, 2)``, so a
    figure at or above ``10 ** 10`` cannot be stored: it passed the
    ``>= 0`` validator, reached the settle verb and raised
    ``NumericValueOutOfRange`` (SQLSTATE 22003) at flush -- unhandled,
    so a 500 on a door an ordinary crafted POST reaches.  The reconcile
    panel commits a whole statement walk in ONE transaction, so a
    single unstorable box discarded every other tick submitted beside
    it.  Sharing the app's own monetary range rather than declaring a
    second one is the point: a bound this field states for itself is a
    second answer to "what is a valid money input", on a money path.
    50 of this package's 104 ``fields.Decimal`` declarations still carry
    no upper bound, which is ledger finding **N-256** rather than this
    step's to sweep.

    ``allow_none=True`` because an HTML form submits every input
    including the empty ones, so an empty box loads as ``None`` and
    means "nobody typed a figure".

    **What ``None`` MEANS changed at plan step X-au-c3**, and this
    paragraph said the opposite until an adversarial review caught it
    (2026-08-18).  It used to mean "leave the column untouched", because
    a settled row recording nothing was a legal state and every reader
    fell back to the row's plan.  A settle now always RECORDS what
    moved, so ``None`` means "nobody typed one, so record what the
    settle resolved" -- the rule
    ``transaction_service.settle_transaction`` states in those words.
    Audit references: F-042 / F-162 / commit C-27 of the 2026-04-15
    security remediation plan.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None.

        HTML forms always submit every <input> element, including
        empty ones, as empty strings.  Without this hook, an
        unfilled ``actual_amount`` field would arrive as ``""`` and
        fail Decimal coercion -- defeating the point of replacing
        the inline try/except.  Since ``actual_amount`` is
        ``allow_none``, an empty input now loads as an explicit
        ``None``, which the routes treat the same as an absent key:
        leave the column untouched (their reads are
        ``data.get``/``is not None``-guarded, so a ``None`` never
        nullifies a previously recorded actual amount).
        """
        return _normalize_empty_inputs(self, data)

    settled_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=_NON_NEGATIVE_MONETARY,
    )
    # The TENDER -- which account the money moved through (plan step
    # ``credit_card:CC-5-3``, ruling **R-CC15**), posted by the popover's
    # Paid / Received buttons, which include its "Paid from" picker; absent
    # when the surface carries no picker (the cell's checkmark, the mobile
    # card, a one-account owner: ruling **R-CC34**), and then the settle
    # verb's seam books on its default -- the kept record's account, else the
    # row's (ruling **R-CC42**).  Gated by the verb against the ROW's owner.
    tender_account_id = RowId()
    # The bank lines the popover's captions NAMED, posted by its Paid /
    # Received buttons (plan step ``credit_card:CC-5-4a-5``, ruling
    # **R-CC127**); ABSENT from the grid's one-click Mark Paid and the phone
    # card -- the owner's withdraw silently by ruling **R-CC56**, and a
    # companion's may free no line (ruling **R-CC130**) -- and from the
    # reconcile panel, which loads only ``settled_amount`` here.
    # ``allow_none`` is presence: :class:`ShownIds` says why.
    shown_lines = ShownIds(allow_none=True)


class CreditPressSchema(BaseSchema):
    """What the popover's Credit press posts: the day, the card, the lines it named.

    Plan task plan#3056.  The day it puts the row on the card, typed or left
    as the popover showed it (``transaction_service.credit_day``); the card,
    when the owner has two or more to pick from; and the bank lines its
    caption named (ruling **R-CC127**).  Each is absent from a press that
    shows none -- the C key and the palette's Credit, which post nothing --
    and the door then reads the row's own day and its one card.  An empty
    box is absent too, never a cleared day.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop an empty box: a press that named no day or no card."""
        return _normalize_empty_inputs(self, data)

    # The day the money went on the card, posted under the name the popover's
    # "Money moved on" box posts, because it is that fact (ruling R-ED).  Not
    # ``allow_none``: an empty box loads as absent, as the box does there.
    settled_on = fields.Date()
    # The card named; gated by the verb as "Paid from" gates one.
    card_account_id = RowId()
    # ``allow_none`` is presence: :class:`ShownIds` says why.
    shown_lines = ShownIds(allow_none=True)


class TransactionDeleteSchema(BaseSchema):
    """What a row's delete dialog NAMED, sent back as the DELETE's query string.

    Plan step ``credit_card:CC-5-4a-5``.  The bank lines the dialog's clause
    named (ruling **R-CC127**), and the purchases its question counted (ruling
    **R-CC131**, developer 2026-10-04, "Refuse and redraw": *"The dialog also
    sends back the purchases it named. At 10:02 they differ, so nothing is
    deleted"*).  ``allow_none`` on both is presence: :class:`ShownIds` says
    why, and a dialog naming no purchase posts the field empty.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Map an empty field to ``None``: a dialog that named nothing."""
        return _normalize_empty_inputs(self, data)

    shown_lines = ShownIds(allow_none=True)
    shown_purchases = ShownIds(allow_none=True)
