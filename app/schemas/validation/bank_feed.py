"""
Shekel Budget App -- Bank feed panel form schemas

The three forms of the feed panel (plan step ``bank_import:X-f6b-2``, leaf
(3c); rulings **R-BI12**, **R-BI27**): paste a setup token, save the
mapping, and the two presses that carry only the page they were pressed on.
Each is its own schema for the reason the statement schemas are separate:
different acts, and one schema carrying all of them would have every field
optional and refuse nothing.
"""

from marshmallow import ValidationError, fields, validate, validates_schema

from app.schemas.validation._helpers import BaseSchema, OptionalRowId, RowId
from app.services.bank_feed import EXTERNAL_ID_LIMIT

#: The longest paste the claim door reads.  A setup token measured
#: 2026-09-18 is a few hundred characters (base64 of a claim URL); the bound
#: is generous and exists so an arbitrary body is refused before it is
#: base64-decoded.
SETUP_TOKEN_LIMIT = 4096


class FeedPageSchema(BaseSchema):
    """Which statements page the panel was pressed on.

    The feed is the OWNER's, so every feed door takes no ``account_id`` in
    its path; the account rides in the form only to say where to answer,
    and the route gates it the way the page itself is gated.  A
    :class:`~app.schemas.validation._helpers.RowId`, because it names a ROW.
    """

    account_id = RowId(required=True)


class FeedClaimSchema(FeedPageSchema):
    """The paste-a-setup-token form."""

    setup_token = fields.String(
        required=True,
        validate=validate.Length(
            min=1, max=SETUP_TOKEN_LIMIT,
            error="Paste the setup token Bridge gave you, whole.",
        ),
        error_messages={"required": "Paste the setup token Bridge gave you."},
    )


class FeedMappingSchema(FeedPageSchema):
    """The mapping form: one row per Bridge account Bridge listed.

    **Two parallel lists, because a browser submits every control it
    renders in document order**: each row carries a hidden ``external_id``
    and a ``target_account_id`` select whose "Not mapped" option submits the
    empty string.  The lists are the same length by construction of the
    template and refused here when they are not, so a hand-built payload
    cannot pair an id with the wrong row.  The same Bridge account twice is
    refused here too: one row per account is what the form renders.
    """

    external_id = fields.List(
        fields.String(validate=validate.Length(min=1, max=EXTERNAL_ID_LIMIT)),
        required=True,
    )
    target_account_id = fields.List(OptionalRowId(), required=True)

    @validates_schema
    def validate_rows_pair_up(self, data, **kwargs):
        """Refuse lists of unequal length or a Bridge account named twice.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's context, unused.

        Raises:
            ValidationError: When the two lists do not pair, or an external
                id repeats.
        """
        del kwargs
        ids = data.get("external_id", [])
        targets = data.get("target_account_id", [])
        if len(ids) != len(targets):
            raise ValidationError(
                "The mapping form did not pair every Bridge account with a "
                "choice.", "target_account_id",
            )
        if len(ids) != len(set(ids)):
            raise ValidationError(
                "The mapping form named the same Bridge account twice.",
                "external_id",
            )
