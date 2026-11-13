"""Account, account-type, anchor, and HYSA interest-params schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app import ref_cache
from app.enums import AcctCategoryEnum, CompoundingFrequencyEnum
from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)


def _valid_compounding_frequency_ids() -> set[int]:
    """Return the set of valid ``ref.compounding_frequencies`` IDs.

    Resolved from the cache at call time (request context) so the
    schema validates the posted FK id against the live ref table
    rather than a hardcoded id set -- the IDs-for-logic invariant (#38).
    """
    return {
        ref_cache.compounding_frequency_id(member)
        for member in CompoundingFrequencyEnum
    }


class AnchorUpdateSchema(BaseSchema):
    """Validates PATCH data for asserting an account's balance.

    **It carried an optional ``version_id`` until plan step X-f1c3c** (ruling
    R-EN), the C-17 optimistic-locking counter the route compared against
    ``Account.version_id`` to answer 409 on a stale form.  A true-up no longer
    writes the ``accounts`` row that counter guards -- it appends an assertion
    -- so there is nothing for a second tab to overwrite and no conflict to
    report.  ``AccountUpdateSchema`` below keeps its ``version_id``: that door
    writes real columns and still has a row to guard.

    **It gained ``observed_on`` at plan step X-f1c4c** (rulings **R-EE** /
    **R-EI**), which is what makes the one-click editor able to say WHEN a
    balance was read rather than always meaning "now".  The ``@pre_load`` is not
    decoration: an untouched HTML date input submits ``""``, and
    ``fields.Date()`` deserializes that to a validation error rather than to
    "absent", so without the normalizer a cleared box would 400 instead of
    meaning today.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    anchor_balance = fields.Decimal(required=True, places=2, as_string=True)
    # The civil day ``anchor_balance`` was TRUE (ruling R-DH), optional exactly
    # as ``AccountCreateSchema.observed_on`` below is: blank means today.  The
    # BOUNDS are not here, for the reason stated there -- they compare against
    # the DISPLAY timezone's today and against the owner's pay schedule, and a
    # schema owns neither clock nor query.  ``anchor_service`` refuses both
    # (ruling R-ER), so the two anchor write doors share one rule.
    observed_on = fields.Date()
    # What the box ASKED when the form was rendered (plan step
    # credit_card:CC-5-5b, ruling R-CC61): a liability's editor submits
    # ``asks_owed=true`` because it asked for the amount owed, and every other
    # editor submits nothing, which reads as the balance it asked for.  It is a
    # fact the FORM states about itself, not a branch on account class (ruling
    # R-J keeps that out of this tier): the route compares it with what the
    # account asks when the route runs and refuses a mismatch, so a save typed
    # under one meaning is not stored under the other.  The route reads the
    # account under the owner's write lock its save took first (plan step
    # ``balance:X-bn``); ``app.routes.accounts._door_meaning`` states what
    # that covers and what the preview does not.  Absent is ``False``, which
    # also fails a pre-CC-5-5b card editor closed -- that page pre-filled the
    # held figure.
    asks_owed = fields.Boolean(load_default=False)


class OpeningRestatementSchema(BaseSchema):
    """Validates POST data for restating what an account's books opened with.

    Plan step **X-f3c-2b-2a**.  Two fields that are ONE fact -- "these books
    opened on day D holding $E" -- and the sibling of
    :class:`AnchorUpdateSchema` above rather than a variant of it: an opening
    is not an assertion, and the whole of plan step X-f3c-2a is that conflating
    the two gave the app two definitions of the same quantity.

    **BOTH fields are required, where the assertion's day is optional**, and
    the difference is what each act means.  A true-up with a blank date box
    means *the balance I am reading right now*, which is a sensible default a
    user takes a hundred times.  A restatement means *the figure on file is
    wrong and here is the right one*; there is no day it defaults to, because
    the day on file is exactly one of the two things being corrected.  The form
    PRE-FILLS the standing values so the owner edits rather than retypes, and a
    submission that changes neither is answered as idempotent success by ruling
    **R-EQ** rather than refused here.

    **The BOUNDS are not here**, for the reason :class:`AnchorUpdateSchema`
    gives for its own: they compare against the DISPLAY timezone's today and
    against the account's recorded movements, and a schema owns neither clock
    nor query.  :mod:`app.services.opening_service` refuses both, so every
    entrance to that door shares one rule.

    ``opening_equity`` carries no sign validator, deliberately and for the same
    reason the column carries no CHECK: the figure is LEDGER-NATIVE, positive
    for an asset's opening capital and negative for a liability's (the Van
    Loan's derived opening is ``-$531.94``), and ruling **R-J** forbids this
    tier branching on account class.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    opened_on = fields.Date(required=True)
    opening_equity = fields.Decimal(required=True, places=2, as_string=True)
    # What the card ASKED when it was rendered -- the amount owed on a
    # liability -- for the stale-form refusal :class:`AnchorUpdateSchema`
    # describes (plan step credit_card:CC-5-5b, ruling R-CC61).
    asks_owed = fields.Boolean(load_default=False)


class AccountCreateSchema(BaseSchema):
    """Validates POST data for creating an account."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    account_type_id = RowId(required=True)
    anchor_balance = fields.Decimal(places=2, as_string=True)
    # The civil day ``anchor_balance`` was TRUE (ruling R-DH, plan step 2).
    # Optional: blank means today, applied by ``account_service.create_account``
    # so every caller shares the default.  The "not in the future" rule is
    # enforced there too rather than here, because it compares against the
    # DISPLAY timezone's today and a schema has no business owning that clock.
    observed_on = fields.Date()


class AccountUpdateSchema(BaseSchema):
    """Validates POST data for updating an account.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the edit form was rendered.  The handler compares
    the submitted value against the current ``Account.version_id``
    and short-circuits with 409 Conflict on mismatch.  This door writes real
    ``accounts`` columns, so unlike :class:`AnchorUpdateSchema` -- which stopped
    writing any at plan step X-f1c3c -- it still has a row to guard.

    **It carried ``anchor_balance`` until plan step X-f1e** (finding **N-195**),
    which made this the app's second balance-assertion door.  Dropping the field
    is what makes the deletion structural rather than cosmetic: a forged POST
    carrying ``anchor_balance`` is now DISCARDED by the schema, so the route
    cannot assert a balance even if a later edit reintroduced a branch that
    tried to.  :class:`AnchorUpdateSchema` is the one schema that accepts an
    asserted balance, and it is reached only through ``accounts.true_up``.
    ``AccountCreateSchema`` keeps its own, which is the account's OPENING and a
    different fact from a later reading.

    **It carried ``is_active`` until task plan#3697**, which no form rendered:
    a forged POST could archive an account past the archive door's refusal
    and leave a card's payment active on an archived card.  Archive and
    unarchive are their own doors, so the field is now dropped as every
    unknown field is.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(validate=validate.Length(min=1, max=100))
    account_type_id = RowId()
    version_id = RowId(validate=validate.Range(min=1))


class AccountTypeCreateSchema(BaseSchema):
    """Validates POST data for creating an account type.

    Includes all metadata fields that drive dispatch logic.  Cross-field
    validation ensures flag combinations are consistent with the chosen
    category (e.g. has_amortization requires Liability).
    """

    name = fields.String(required=True, validate=validate.Length(min=1, max=30))
    category_id = RowId(required=True)
    has_parameters = fields.Boolean(load_default=False)
    has_amortization = fields.Boolean(load_default=False)
    has_interest = fields.Boolean(load_default=False)
    is_pretax = fields.Boolean(load_default=False)
    is_liquid = fields.Boolean(load_default=False)
    icon_class = fields.String(
        load_default="bi-bank",
        validate=validate.Length(max=30),
    )
    max_term_months = fields.Integer(
        load_default=None, allow_none=True,
        validate=validate.Range(min=1, max=600),
    )

    @validates_schema
    def validate_flag_combinations(self, data, **kwargs):
        """Enforce category-flag consistency rules."""

        cat_id = data.get("category_id")
        liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
        asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
        retirement_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)

        if data.get("has_amortization") and cat_id != liability_id:
            raise ValidationError(
                "has_amortization requires Liability category.",
                field_name="has_amortization",
            )
        if data.get("has_interest") and cat_id != asset_id:
            raise ValidationError(
                "has_interest requires Asset category.",
                field_name="has_interest",
            )
        if data.get("is_pretax") and cat_id != retirement_id:
            raise ValidationError(
                "is_pretax requires Retirement category.",
                field_name="is_pretax",
            )
        if data.get("is_liquid") and cat_id != asset_id:
            raise ValidationError(
                "is_liquid requires Asset category.",
                field_name="is_liquid",
            )
        if data.get("has_amortization") and data.get("has_interest"):
            raise ValidationError(
                "has_amortization and has_interest are mutually exclusive.",
                field_name="has_amortization",
            )
        if data.get("max_term_months") and not data.get("has_amortization"):
            raise ValidationError(
                "max_term_months requires has_amortization.",
                field_name="max_term_months",
            )


class AccountTypeUpdateSchema(BaseSchema):
    """Validates POST data for updating an account type.

    All fields are optional for partial updates.  Cross-field
    validation mirrors AccountTypeCreateSchema but only fires when
    the relevant fields are present in the submitted data.
    """

    name = fields.String(validate=validate.Length(min=1, max=30))
    category_id = RowId()
    has_parameters = fields.Boolean()
    has_amortization = fields.Boolean()
    has_interest = fields.Boolean()
    is_pretax = fields.Boolean()
    is_liquid = fields.Boolean()
    icon_class = fields.String(validate=validate.Length(max=30))
    max_term_months = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1, max=600),
    )

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None.

        When *data* is a Werkzeug MultiDict (HTML form submission), take
        the last value for each key so the hidden-input + checkbox pattern
        resolves correctly: checked -> 'true' (last value wins), unchecked
        -> 'false' (sole value from hidden input).  The flattened dict
        then goes through the shared empty-input rule so the nullable
        ``max_term_months`` clears on an empty submit.
        """
        if hasattr(data, "getlist"):
            data = {
                k: vs[-1] for k in data if (vs := data.getlist(k))
            }
        return _normalize_empty_inputs(self, data)

    @validates_schema
    def validate_flag_combinations(self, data, **kwargs):
        """Enforce category-flag consistency rules on partial updates.

        Category-flag checks only fire when both the flag and
        category_id are present, so partial updates that omit
        category_id do not falsely reject.  Mutual-exclusion and
        dependency checks fire whenever both relevant fields are
        present.
        """

        cat_id = data.get("category_id")

        if cat_id is not None:
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            retirement_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)

            if data.get("has_amortization") and cat_id != liability_id:
                raise ValidationError(
                    "has_amortization requires Liability category.",
                    field_name="has_amortization",
                )
            if data.get("has_interest") and cat_id != asset_id:
                raise ValidationError(
                    "has_interest requires Asset category.",
                    field_name="has_interest",
                )
            if data.get("is_pretax") and cat_id != retirement_id:
                raise ValidationError(
                    "is_pretax requires Retirement category.",
                    field_name="is_pretax",
                )
            if data.get("is_liquid") and cat_id != asset_id:
                raise ValidationError(
                    "is_liquid requires Asset category.",
                    field_name="is_liquid",
                )

        if data.get("has_amortization") and data.get("has_interest"):
            raise ValidationError(
                "has_amortization and has_interest are mutually exclusive.",
                field_name="has_amortization",
            )
        if data.get("max_term_months") and not data.get("has_amortization"):
            raise ValidationError(
                "max_term_months requires has_amortization.",
                field_name="max_term_months",
            )


# ── HYSA Schemas ──────────────────────────────────────────────────


class InterestParamsCreateSchema(BaseSchema):
    """Validates POST data for creating interest parameters.

    E-28 / HIGH-06 / PA-02: ``apy`` is validated as a decimal fraction
    in ``[0, 1]`` matching the DB ``CHECK(apy >= 0 AND apy <= 1)``.
    The ``@pre_load`` divides the form's user-facing percent (e.g.
    ``"4.5"``) by 100 so the ``Range`` validator and the DB CHECK
    accept exactly the same set of values.
    """

    _PERCENT_FIELDS = ("apy",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    # apy storage is a fraction; see class docstring.  ``places=5``
    # mirrors the column's ``Numeric(7, 5)`` precision so a 4.5%
    # input (``"4.5"`` -> fraction ``"0.04500"``) round-trips
    # without loss.
    apy = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    # #38: compounding frequency is now a ref-table FK id, not a free
    # string.  Required on create, mirroring the prior String field.
    compounding_frequency_id = RowId(required=True)

    @validates_schema
    def validate_compounding_frequency(self, data, **kwargs):
        """Reject a compounding-frequency id outside the ref table."""
        freq_id = data.get("compounding_frequency_id")
        if freq_id is not None and (
            freq_id not in _valid_compounding_frequency_ids()
        ):
            raise ValidationError(
                "Invalid compounding frequency.",
                field_name="compounding_frequency_id",
            )


class InterestParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating interest parameters.

    Same fraction-domain convention as :class:`InterestParamsCreateSchema`.
    ``apy`` is optional here so a partial update (e.g. compounding
    frequency only) does not have to resubmit the rate.
    """

    _PERCENT_FIELDS = ("apy",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    apy = fields.Decimal(
        places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    # #38: ref-table FK id (see :class:`InterestParamsCreateSchema`).
    # Optional on update so a partial payload (e.g. apy only) need not
    # resubmit the frequency.
    compounding_frequency_id = RowId()

    @validates_schema
    def validate_compounding_frequency(self, data, **kwargs):
        """Reject a compounding-frequency id outside the ref table.

        Only fires when present -- a partial update that omits it
        leaves the stored frequency unchanged.
        """
        freq_id = data.get("compounding_frequency_id")
        if freq_id is not None and (
            freq_id not in _valid_compounding_frequency_ids()
        ):
            raise ValidationError(
                "Invalid compounding frequency.",
                field_name="compounding_frequency_id",
            )


# ── Appreciation (physical-asset) Schemas ─────────────────────────


class AppreciationParamsUpdateSchema(BaseSchema):
    """Validates POST data for a Property's annual appreciation rate.

    ``appreciation_rate`` is stored as a decimal fraction (e.g. ``0.03500``
    for 3.5%/yr) matching the DB CHECK ``annual_appreciation_rate > -1 AND
    <= 1``.  The ``@pre_load`` divides the form's user-facing percent by
    100 so the ``Range`` validator and the CHECK accept the same set of
    values.  A negative rate (depreciation) is permitted -- the lower bound
    is exclusive of -1 so the per-period compounding stays invertible --
    so a future Vehicle/valuables type reuses this schema unchanged.
    """

    _PERCENT_FIELDS = ("appreciation_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    appreciation_rate = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(
            min=Decimal("-1"), min_inclusive=False, max=Decimal("1"),
        ),
    )
