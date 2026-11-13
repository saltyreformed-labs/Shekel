"""The entry create and update schemas' validation, and R-II's tier split: neither hand door takes
a typed sign.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError as MarshmallowValidationError

from app.models.transaction_entry import TransactionEntry
from app.services import entry_service
from app.schemas.validation import EntryCreateSchema, EntryUpdateSchema
from app.exceptions import ValidationError
from tests._test_helpers import typed


# ── Schema Tests ──────────────────────────────────────────────────


class TestEntryCreateSchema:
    """Tests for EntryCreateSchema validation."""

    def test_valid_create_data(self, app):
        """All required fields present and valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Kroger",
                "purchased_on": "2026-01-05",
            })
            assert data["amount"] == Decimal("50.00")
            assert data["description"] == "Kroger"
            assert data["purchased_on"] == date(2026, 1, 5)
            assert data["is_credit"] is False

    def test_schema_rejects_zero_amount(self, app):
        """Amount of 0 is rejected (min is 0.01)."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "0",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_rejects_negative_amount(self, app):
        """Negative amount is rejected."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "-10.00",
                    "description": "Test",
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_schema_accepts_minimum_amount(self, app):
        """Amount of exactly 0.01 is accepted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "0.01",
                "description": "Penny",
                "purchased_on": "2026-01-05",
            })
            assert data["amount"] == Decimal("0.01")

    def test_schema_rejects_empty_description(self, app):
        """Empty description is rejected (min length 1)."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "50.00",
                    "description": "",
                    "purchased_on": "2026-01-05",
                })
            # strip_empty_strings removes "" -> missing required field
            assert "description" in exc_info.value.messages

    def test_schema_rejects_description_too_long(self, app):
        """Description over 200 characters is rejected."""
        with app.app_context():
            schema = EntryCreateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({
                    "amount": "50.00",
                    "description": "A" * 201,
                    "purchased_on": "2026-01-05",
                })
            assert "description" in exc_info.value.messages

    def test_schema_accepts_single_char_description(self, app):
        """Description of exactly 1 character is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "10.00",
                "description": "X",
                "purchased_on": "2026-01-05",
            })
            assert data["description"] == "X"

    def test_schema_accepts_max_length_description(self, app):
        """Description of exactly 200 characters is valid."""
        with app.app_context():
            schema = EntryCreateSchema()
            desc = "B" * 200
            data = schema.load({
                "amount": "10.00",
                "description": desc,
                "purchased_on": "2026-01-05",
            })
            assert data["description"] == desc

    def test_schema_is_credit_default_false(self, app):
        """is_credit defaults to False when omitted."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "purchased_on": "2026-01-05",
            })
            assert data["is_credit"] is False

    def test_schema_is_credit_true(self, app):
        """is_credit can be set to True."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Test",
                "purchased_on": "2026-01-05",
                "is_credit": True,
            })
            assert data["is_credit"] is True

    def test_schema_strips_empty_strings(self, app):
        """Empty string fields are stripped (HTML form pattern)."""
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.00",
                "description": "Kroger",
                "purchased_on": "2026-01-05",
                "is_credit": "",  # Empty string from unchecked checkbox
            })
            # is_credit reverts to default (False) after strip
            assert data["is_credit"] is False

    def test_schema_amount_quantized(self, app):
        """Amount with extra decimal places is quantized to 2 places.

        Marshmallow's Decimal(places=2) handles this by rounding.
        """
        with app.app_context():
            schema = EntryCreateSchema()
            data = schema.load({
                "amount": "50.999",
                "description": "Test",
                "purchased_on": "2026-01-05",
            })
            # Marshmallow with places=2 quantizes to 2 decimal places.
            assert data["amount"] == Decimal("51.00")


class TestNeitherHandDoorTakesATypedSign:
    """R-II's tier split, as the developer RE-RULED it on 2026-09-01.

    Ruling **bank_import:R-II** put the row invariant (``amount <> 0``) at the
    service tier, where the bank-import door meets it too
    (``entry_service._refusals._reject_zero_amount``), and moved POSITIVITY off
    the table onto the hand-entry door.

    **This class asserted the OPPOSITE of what it asserts now, and the reason
    is a measurement rather than a preference.**  It read *the update schema
    ACCEPTS a negative because a refund is one*, and defended the asymmetry as
    R-II's point.  ``EntryUpdateSchema`` is reached ONLY by the human PATCH
    route -- the bank-import door writes through ``entry_service`` and passes
    no schema at all -- so both doors carrying this rule are doors a person
    types at, and one of them had no bound.  A typed ``-45.00`` where ``45.00``
    was meant booked a REFUND in silence and moved the projection by twice the
    figure, while the identical keystroke on the ADD form was a 422.

    **Both doors take a MAGNITUDE now**, and the direction is a control
    (``entry_service.purchase_amount``).  The editable-refund problem the old
    asymmetry solved is solved better: the edit form renders the magnitude and
    preselects the direction, so re-describing a stored refund changes no
    figure and needs no negative in the box.
    """

    def test_create_schema_still_refuses_a_typed_negative(self, app):
        """The ADD form's rule survives R-II: a typed negative is a typo."""
        with app.app_context():
            schema = EntryCreateSchema()
            with pytest.raises(MarshmallowValidationError) as exc_info:
                schema.load({
                    "amount": "-28.29",
                    "description": "Amazon refund",
                    "purchased_on": "2026-01-05",
                })
            assert "amount" in exc_info.value.messages

    def test_update_schema_ALSO_refuses_a_typed_negative(self, app):
        """The EDIT door's bound, restored on the developer's 2026-09-01 ruling.

        It is the same rule as the create door's now, and stating BOTH in one
        class is what keeps a future edit from removing one and leaving the
        other -- which is the shape that produced the silent refund.
        """
        with app.app_context():
            schema = EntryUpdateSchema()
            with pytest.raises(MarshmallowValidationError) as exc_info:
                schema.load({"amount": "-28.29"})
            assert "amount" in exc_info.value.messages

    def test_a_refund_is_stated_by_the_DIRECTION_on_both_doors(self, app):
        """The control the two refusals above must not have deleted.

        A refund is still recordable -- that is ruling **R-II** -- and this is
        how: a magnitude plus ``direction``, composed by
        :func:`~app.services.entry_service.purchase_amount`.  Without this case
        the pair above is satisfied by a door that cannot record a refund at
        all.
        """
        with app.app_context():
            for schema in (EntryCreateSchema(), EntryUpdateSchema()):
                data = schema.load({
                    "amount": "28.29", "direction": "refund",
                    "description": "Amazon refund",
                    "purchased_on": "2026-01-05",
                })
                assert data["amount"] == Decimal("28.29")
                assert data["direction"] == "refund"
            assert entry_service.purchase_amount(
                Decimal("28.29"), records_a_refund=True,
            ) == Decimal("-28.29")
            assert entry_service.purchase_amount(
                Decimal("28.29"), records_a_refund=False,
            ) == Decimal("28.29")

    def test_neither_door_lets_a_zero_through_to_the_database(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Zero is refused as a ValidationError, never as an IntegrityError.

        The CHECK is the backstop; a zero reaching it surfaces to the user as
        *Something went wrong* over a traceback.  Both write doors ask
        ``_reject_zero_amount`` instead, so the refusal names the field.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            with pytest.raises(ValidationError, match="cannot be zero"):
                entry_service.create_entry(
                    transaction_id=txn.id,
                    user_id=user.id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("0.00")),
                        description="Nothing",
                        purchased_on=date(2026, 1, 5),
                    ),
                )

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("10.00")),
                    description="Real purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.commit()

            with pytest.raises(ValidationError, match="cannot be zero"):
                entry_service.update_entry(
                    entry.id, user.id, figure=typed(Decimal("0.00")),
                )

    def test_a_refund_round_trips_through_the_service_door(
        self, app, db, seed_user, seed_entry_template,
    ):
        """A negative purchase is created and STORED as a negative.

        The act plan step ``bank_import:X-gj-2b`` exists to make possible,
        asserted at the tier the bank-import door actually uses -- it calls
        ``create_entry`` directly and never passes through a Marshmallow schema.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entry = entry_service.create_entry(
                transaction_id=txn.id,
                user_id=user.id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("-28.29")),
                    description="Amazon refund",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.commit()

            assert entry.amount == Decimal("-28.29")
            assert db.session.get(
                TransactionEntry, entry.id,
            ).amount == Decimal("-28.29")


class TestEntryUpdateSchema:
    """Tests for EntryUpdateSchema validation."""

    def test_update_partial_amount(self, app):
        """Only amount provided -- valid partial update."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({"amount": "75.00"})
            assert data["amount"] == Decimal("75.00")
            assert "description" not in data

    def test_update_partial_is_credit_only(self, app):
        """Only is_credit provided -- valid partial update."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({"is_credit": True})
            assert data["is_credit"] is True
            assert "amount" not in data

    def test_update_no_fields(self, app):
        """No fields provided -- empty dict (service handles this error)."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({})
            assert data == {}

    def test_update_rejects_zero_amount(self, app):
        """Amount of 0 is rejected in update schema too.

        The half of the old bound that SURVIVED ruling **bank_import:R-II**: a
        purchase worth nothing is not a purchase, and that is the row's own
        invariant (``amount <> 0``) rather than a statement about the sign.
        Kept on the schema so it answers 422 -- the service gate under it
        (``_reject_zero_amount``) answers 400, which is the right code for a
        caller that reached the service without a form.
        """
        with app.app_context():
            schema = EntryUpdateSchema()
            from marshmallow import ValidationError as MarshmallowError
            with pytest.raises(MarshmallowError) as exc_info:
                schema.load({"amount": "0"})
            assert "amount" in exc_info.value.messages

    # **The negative-amount case that stood here is GONE, not moved** (plan
    # step ``bank_import:X-gj-2b-3``).  It was a second copy of
    # ``TestNeitherHandDoorTakesATypedSign``'s subject -- adversarial test-quality
    # review found the pair -- and both asserted the contract the developer
    # re-ruled on 2026-09-01: the edit door takes a MAGNITUDE, and a refund is
    # stated by ``direction``.  One class states that now, with the control
    # that a refund is still recordable, rather than two that agreed.

    def test_update_unknown_fields_excluded(self, app):
        """Unknown fields are excluded by BaseSchema (Meta.unknown = EXCLUDE)."""
        with app.app_context():
            schema = EntryUpdateSchema()
            data = schema.load({
                "amount": "50.00",
                "bogus_field": "ignored",
            })
            assert "bogus_field" not in data
            assert data["amount"] == Decimal("50.00")
