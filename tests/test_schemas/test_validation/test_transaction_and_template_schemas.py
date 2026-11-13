"""Validation schema tests: transactions, transaction templates, transfer templates and transfers,
with their boundary cases.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.enums import RecurrenceUnitEnum
from app.schemas.validation._helpers import _normalize_empty_inputs
from app.schemas.validation.templates import A_CADENCE_IS_REQUIRED
from tests._test_helpers import cadence_payload
from app.services.recurrence import MonthReading
from app.schemas.validation import (
    RECURRENCE_SUBMISSION_KEY,
    InlineTransactionCreateSchema,
    TemplateCreateSchema,
    TemplateUpdateSchema,
    TransactionCreateSchema,
    TransactionUpdateSchema,
    TransferCreateSchema,
    TransferTemplateCreateSchema,
    TransferUpdateSchema,
)


# ── TransactionCreateSchema ──────────────────────────────────────────


class TestTransactionCreateSchema:
    """Tests for TransactionCreateSchema."""

    def test_valid_data(self):
        """Valid data loads successfully with Decimal coercion."""
        data = TransactionCreateSchema().load({
            "name": "Groceries",
            "estimated_amount": "85.50",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert data["name"] == "Groceries"
        assert data["estimated_amount"] == Decimal("85.50")

    def test_missing_required_field(self):
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TransactionCreateSchema().load({
                "estimated_amount": "100.00",
                # Missing name, pay_period_id, scenario_id, etc.
            })
        assert "name" in exc.value.messages

    def test_negative_estimated_amount(self):
        """Negative estimated_amount fails Range validation."""
        with pytest.raises(ValidationError) as exc:
            TransactionCreateSchema().load({
                "name": "Bad",
                "estimated_amount": "-10.00",
                "pay_period_id": "1",
                "scenario_id": "1",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
            })
        assert "estimated_amount" in exc.value.messages


# ── TransactionUpdateSchema ──────────────────────────────────────────


class TestTransactionUpdateSchema:
    """Tests for TransactionUpdateSchema."""

    def test_empty_strings_stripped(self):
        """@pre_load strips empty strings -- empty update is valid."""
        data = TransactionUpdateSchema().load({
            "name": "",
            "estimated_amount": "",
        })
        # Both fields stripped; result is empty dict.
        assert "name" not in data
        assert "estimated_amount" not in data

    def test_valid_partial_update(self):
        """Partial update with valid fields loads correctly.

        Carries the rendered-figure companion because ruling **R-JR** makes a
        figure without one MALFORMED -- see
        ``TestTheSchemasRefuseAFigureWithNoCompanion`` for that half.
        """
        data = TransactionUpdateSchema().load({
            "estimated_amount": "200.00",
            "estimated_amount_as_rendered": "150.00",
        })
        assert data["estimated_amount"] == Decimal("200.00")

    def test_invalid_amount_rejected(self):
        """Non-numeric estimated_amount is rejected."""
        with pytest.raises(ValidationError) as exc:
            TransactionUpdateSchema().load({
                "estimated_amount": "abc",
            })
        assert "estimated_amount" in exc.value.messages


# ── InlineTransactionCreateSchema ────────────────────────────────────


class TestInlineTransactionCreateSchema:
    """Tests for InlineTransactionCreateSchema."""

    def test_valid_data(self):
        """Valid inline data loads without name field."""
        data = InlineTransactionCreateSchema().load({
            "estimated_amount": "50.00",
            "category_id": "1",
            "pay_period_id": "1",
            "transaction_type_id": "1",
            "scenario_id": "1",
            "account_id": "1",
        })
        assert data["estimated_amount"] == Decimal("50.00")
        assert "name" not in data  # Name not required for inline.

    def test_missing_required_field(self):
        """Missing category_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            InlineTransactionCreateSchema().load({
                "estimated_amount": "50.00",
                # Missing category_id, pay_period_id, etc.
            })
        assert "category_id" in exc.value.messages


# ── TemplateCreateSchema ─────────────────────────────────────────────


class TestTemplateCreateSchema:
    """Tests for TemplateCreateSchema."""

    def test_valid_data(self):
        """Valid template data loads with all required fields -- a cadence among them.

        A transaction template REQUIRES a cadence since plan step
        ``balance:X-bi-7b`` (ruling R-BAL23): a definition with no rule is a
        one-off's, made at the grid, so the form's *Does not repeat* is gone
        and ``validate_a_cadence_is_chosen`` refuses an absent or empty unit.
        """
        data = TemplateCreateSchema().load({
            "name": "Monthly Rent",
            "amount": "1200.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            **cadence_payload(),
        })
        assert data["name"] == "Monthly Rent"
        assert data["amount"] == Decimal("1200.00")

    def test_no_cadence_is_refused(self):
        """Absent and empty units are one statement on a create: refused (R-BAL23)."""
        base = {
            "name": "Monthly Rent",
            "amount": "1200.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        }
        for unit in ({}, {"recurrence_unit": ""}):
            with pytest.raises(ValidationError) as exc:
                TemplateCreateSchema().load({**base, **unit})
            assert exc.value.messages["recurrence_unit"] == [A_CADENCE_IS_REQUIRED]

    def test_missing_required_field(self):
        """Missing name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "amount": "100.00",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
            })
        assert "name" in exc.value.messages

    def test_invalid_recurrence_unit(self):
        """A non-integer recurrence unit fails the row-id field's parse.

        ``recurrence_unit`` replaced ``recurrence_pattern`` at plan step
        R7b-2: the form authors the two axes and the closed pattern set is a
        storage encoding, so what a submission names is a
        ``ref.recurrence_units`` row.  The refusal is
        :class:`~app.schemas.validation._helpers.RowId`'s, which
        ``RecurrenceUnitField`` derives from.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "name": "Test",
                "amount": "100.00",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
                "recurrence_unit": "daily",  # Not a valid integer.
            })
        assert "recurrence_unit" in exc.value.messages

    def test_offset_periods_is_not_a_field_at_all(self):
        """Defect **D8**: no submission can state a rule's PHASE.

        The field was vestigial -- no template ever rendered an input for it,
        so every submission carried the schema default of 0 and the update
        path wrote that over the rule's real phase.  Plan step R7b-2 deleted
        it rather than guarding it, and ``unknown = EXCLUDE`` is what makes
        "an amount-only edit cannot re-phase a cadence" structural: a
        hand-crafted POST carrying the key is dropped here, so it never
        reaches the route helper that used to read it.
        """
        assert "offset_periods" not in TemplateCreateSchema().fields
        data = TemplateCreateSchema().load({
            "name": "Test",
            "amount": "100.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            "offset_periods": "7",
            **cadence_payload(),
        })
        assert "offset_periods" not in data

    def test_nominal_day_range(self):
        """A "repeating on" day outside 29-31 names no option and fails validation.

        ``repeats_on`` since plan step recurrence:R8-c (``nominal_day`` until
        then): the control posts each option's own token, so 28 is a token no
        option posts.

        ``day_of_month`` was this field's predecessor and its domain was 1-31,
        because it stated the cycle's day outright.  Plan step R7c-b made the
        first occurrence AUTHORED, so ``starts_on`` carries that day; what is
        left is the 0-or-1 day the date's own month was too SHORT to hold
        (ruling R-R3).  Every month holds its first 28 days, so a value below
        29 could only be a second statement of the day ``starts_on`` already
        carries -- which is the two-representations defect the ruling removes.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "name": "Test",
                "amount": "100.00",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
                "repeats_on": "28",
            })
        assert "repeats_on" in exc.value.messages

    def test_empty_strings_stripped(self):
        """@pre_load drops empty non-nullable fields and nulls the nullable ones.

        Both arms of the ``_normalize_empty_inputs`` contract, on the two
        recurrence fields that take different sides of it.

        ``interval_n`` is not ``allow_none``, so an untouched HTML input --
        which always submits, as ``""`` -- means "not provided" and the key is
        dropped, leaving partial-update semantics intact.

        ``recurrence_unit`` IS ``allow_none`` (plan step R2e-1, on the field
        R7b-2 renamed), so its empty value is the null STATE rather than a
        missing one: it is what the form's "Does not repeat" option posts, and
        the update route reads a present ``None`` as "clear this template's
        recurrence".  If it were dropped like ``interval_n``, choosing that
        option would be indistinguishable from an amount-only PATCH that
        submits no recurrence keys at all -- which is exactly how the option
        came to be a silent no-op before R2e-1.

        ``recurrence_placement`` takes the same side, because the browser
        posts both controls whatever the unit says: an empty placement beside
        an empty unit must not read as a partial update either.

        **Asked of the hook itself since plan step ``balance:X-bi-7b``.**  The
        transaction schemas REFUSE a present empty unit one layer up (ruling
        R-BAL23), so ``load()`` can no longer hand back the normalised payload
        for this shape -- and it is exactly the normalised ``None`` that
        reaches that refusal, which is what this contract now feeds.
        """
        schema = TemplateCreateSchema()
        data = _normalize_empty_inputs(schema, {
            "name": "Test",
            "amount": "100.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            "recurrence_unit": "",
            "recurrence_placement": "",
            "interval_n": "",
            "starts_on": "",
        })
        # Not nullable -- "" means "not provided", so the key goes.
        assert "interval_n" not in data
        # ``starts_on`` replaced a ``day_of_month`` probe at plan step R7c-b,
        # and the swap is not cosmetic: that field no longer EXISTS on this
        # schema, so ``unknown = EXCLUDE`` dropped the key whatever the
        # empty-input rule did -- the assertion passed for the wrong reason.
        # This one is a declared, non-nullable field, and its drop-on-empty is
        # load-bearing: ``refuse_inverted_window`` reads an ABSENT start as
        # "leave the stored date alone", so a cleared box arriving as a stated
        # ``None`` instead would erase the rule's first occurrence.
        assert "starts_on" not in data
        # Nullable -- "" IS the value, so the key stays and carries None.
        assert "recurrence_unit" in data
        assert data["recurrence_unit"] is None
        assert "recurrence_placement" in data
        assert data["recurrence_placement"] is None


# ── TemplateUpdateSchema ─────────────────────────────────────────────


class TestTemplateUpdateSchema:
    """Tests for TemplateUpdateSchema."""

    def test_all_optional(self):
        """Empty update (after stripping) is valid."""
        data = TemplateUpdateSchema().load({
            "name": "",
            "amount": "",
        })
        assert "name" not in data
        assert "amount" not in data

    def test_effective_from_date_parsing(self):
        """effective_from parses a valid date string."""
        data = TemplateUpdateSchema().load({
            "effective_from": "2026-03-01",
        })
        from datetime import date
        assert data["effective_from"] == date(2026, 3, 1)

    def test_effective_from_invalid_date(self):
        """effective_from with invalid date raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TemplateUpdateSchema().load({
                "effective_from": "not-a-date",
            })
        assert "effective_from" in exc.value.messages


# ── TransferTemplateCreateSchema ─────────────────────────────────────


class TestTransferTemplateCreateSchema:
    """Tests for TransferTemplateCreateSchema."""

    def test_valid_data(self):
        """Valid transfer template data loads successfully."""
        data = TransferTemplateCreateSchema().load({
            "name": "Savings Transfer",
            "amount": "500.00",
            "from_account_id": "1",
            "to_account_id": "2",
            "category_id": "1",
        })
        assert data["amount"] == Decimal("500.00")

    def test_same_accounts_rejected(self):
        """from_account_id == to_account_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TransferTemplateCreateSchema().load({
                "name": "Bad Transfer",
                "amount": "100.00",
                "from_account_id": "1",
                "to_account_id": "1",
                "category_id": "1",
            })
        assert "_schema" in exc.value.messages

    def test_zero_amount_rejected(self):
        """amount=0 fails Range(min=0, min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            TransferTemplateCreateSchema().load({
                "name": "Zero Transfer",
                "amount": "0",
                "from_account_id": "1",
                "to_account_id": "2",
            })
        assert "amount" in exc.value.messages


# ── TransferCreateSchema ─────────────────────────────────────────────


class TestTransferCreateSchema:
    """Tests for TransferCreateSchema."""

    def test_valid_data(self):
        """Valid ad-hoc transfer data loads successfully."""
        data = TransferCreateSchema().load({
            "from_account_id": "1",
            "to_account_id": "2",
            "amount": "300.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
        })
        assert data["amount"] == Decimal("300.00")

    def test_same_accounts_rejected(self):
        """from_account_id == to_account_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load({
                "from_account_id": "1",
                "to_account_id": "1",
                "amount": "100.00",
                "pay_period_id": "1",
                "scenario_id": "1",
                "category_id": "1",
            })
        assert "_schema" in exc.value.messages


# ── TransferUpdateSchema ─────────────────────────────────────────────


class TestTransferUpdateSchema:
    """Tests for TransferUpdateSchema."""

    def test_valid_partial_update(self):
        """Partial update with amount loads correctly.

        Carries the rendered-figure companion because ruling **R-JR** makes a
        figure without one MALFORMED.
        """
        data = TransferUpdateSchema().load({
            "amount": "250.00",
            "amount_as_rendered": "200.00",
        })
        assert data["amount"] == Decimal("250.00")

    def test_zero_amount_rejected(self):
        """amount=0 fails Range(min=0, min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            TransferUpdateSchema().load({
                "amount": "0",
            })
        assert "amount" in exc.value.messages


# ── TransactionCreateSchemaBoundary ─────────────────────────────────


class TestTransactionCreateSchemaBoundary:
    """Boundary and edge-case tests for TransactionCreateSchema."""

    def test_excessive_decimal_places_coerced(self):
        """Decimal field with places=2 quantizes excessive decimal places.

        Marshmallow's Decimal field with places=2 rounds the input to 2
        decimal places on deserialization. A user typing '100.12345' must not
        store 3+ decimal places in a Numeric(12,2) column.
        """
        data = TransactionCreateSchema().load({
            "name": "Test",
            "estimated_amount": "100.12345",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        # 100.12345 rounded to 2 places → 100.12 (3rd decimal is 3 < 5, rounds down).
        assert data["estimated_amount"] == Decimal("100.12")

    def test_xss_in_name_field(self):
        """Schema accepts raw HTML in name field -- no sanitization at this layer.

        # Schema accepts raw HTML. XSS prevention relies on Jinja2 auto-escaping
        # in templates ({{ name }} auto-escapes by default). Verify template
        # escaping separately.
        """
        data = TransactionCreateSchema().load({
            "name": "<script>alert(1)</script>",
            "estimated_amount": "100.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert data["name"] == "<script>alert(1)</script>"

    def test_name_at_max_length(self):
        """A name of exactly 200 characters passes Length(max=200) validation."""
        long_name = "A" * 200
        data = TransactionCreateSchema().load({
            "name": long_name,
            "estimated_amount": "100.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
        })
        assert len(data["name"]) == 200

    def test_name_over_max_length(self):
        """A name of 201 characters fails Length(max=200) validation."""
        long_name = "A" * 201
        with pytest.raises(ValidationError) as exc:
            TransactionCreateSchema().load({
                "name": long_name,
                "estimated_amount": "100.00",
                "pay_period_id": "1",
                "scenario_id": "1",
                "category_id": "1",
                "transaction_type_id": "1",
                "account_id": "1",
            })
        assert "name" in exc.value.messages


# ── TestTemplateCreateSchemaBoundary ────────────────────────────────


class TestTemplateCreateSchemaBoundary:
    """Boundary tests for TemplateCreateSchema recurrence fields."""

    def _valid_template_data(self, **overrides):
        """Return a valid template payload with optional overrides.

        Carries a MONTH cadence, because a transaction template requires one
        since plan step ``balance:X-bi-7b`` (R-BAL23) and ``nominal_day`` is a
        month-unit fact.
        """
        data = {
            "name": "Test Template",
            "amount": "100.00",
            "category_id": "1",
            "transaction_type_id": "1",
            "account_id": "1",
            **cadence_payload(unit=RecurrenceUnitEnum.MONTH, starts_on=date(2026, 4, 30)),
        }
        data.update(overrides)
        return data

    def test_nominal_day_28_rejected(self):
        """repeats_on=28 names no option and fails validation (nominal_day until R8-c).

        The lower boundary, and it is a real rule rather than an arbitrary
        floor: every month holds its first 28 days, so no first occurrence can
        ever have CLAMPED a 28.  Such a value would restate the day
        ``starts_on`` already carries, which
        ``ck_recurrence_rules_nominal_day`` refuses in the table.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(repeats_on="28")
            )
        assert "repeats_on" in exc.value.messages

    def test_nominal_day_32_rejected(self):
        """repeats_on=32 names no option and fails validation (nominal_day until R8-c).

        No month has 32 days. The schema must reject this before it reaches
        the recurrence engine.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(repeats_on="32")
            )
        assert "repeats_on" in exc.value.messages

    def test_nominal_day_31_accepted(self):
        """repeats_on=31 is valid -- it is the day a 30-day month clamps.

        The schema grades the DOMAIN alone; whether 31 may sit beside this
        particular ``starts_on`` is a two-field rule the field cannot see, and
        ``RecurrenceSpec.__post_init__`` refuses the contradicting pair at the
        write door (mirroring ``ck_recurrence_rules_nominal_day``).
        """
        data = TemplateCreateSchema().load(
            self._valid_template_data(repeats_on="31")
        )
        assert data[RECURRENCE_SUBMISSION_KEY].reading == MonthReading(nominal_day=31)

    def test_starts_on_outside_the_calendar_window_rejected(self):
        """A five-digit-year typo in the date box is a FIELD error.

        ``<input type="date">`` accepts ``9999-12-31``, and past the saved
        horizon the pay calendar projects the covering paycheck by ADDING
        ``cadence_days`` to a start -- which raised ``OverflowError`` on the
        recurrence-preview endpoint for any signed-in user (found at plan step
        R7c-b).  The window is ``app.utils.dates.CALENDAR_DATE_MIN``..``_MAX``,
        mirrored by ``ck_recurrence_rules_starts_on_range``.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(starts_on="9999-12-31")
            )
        assert "starts_on" in exc.value.messages

    def test_interval_n_zero_rejected(self):
        """interval_n=0 fails Range(min=1) validation.

        interval_n=0 would cause an infinite loop in the recurrence engine.
        The schema should catch it before the DB check constraint.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(interval_n="0")
            )
        assert "interval_n" in exc.value.messages


# ── TestTransferCreateSchemaBoundary ────────────────────────────────


class TestTransferCreateSchemaBoundary:
    """Boundary tests for TransferCreateSchema amount and account validation."""

    def _valid_transfer_data(self, **overrides):
        """Return a valid transfer payload with optional overrides."""
        data = {
            "from_account_id": "1",
            "to_account_id": "2",
            "amount": "100.00",
            "pay_period_id": "1",
            "scenario_id": "1",
            "category_id": "1",
        }
        data.update(overrides)
        return data

    def test_negative_amount_rejected(self):
        """Negative amount fails Range(min=0, min_inclusive=False) validation."""
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load(
                self._valid_transfer_data(amount="-100")
            )
        assert "amount" in exc.value.messages

    def test_zero_amount_rejected(self):
        """Zero amount fails Range(min=0, min_inclusive=False) -- must be > 0.

        Zero-amount transfers are meaningless and should be blocked at the
        schema level.
        """
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load(
                self._valid_transfer_data(amount="0")
            )
        assert "amount" in exc.value.messages

    def test_same_account_ids_rejected(self):
        """from_account_id == to_account_id raises @validates_schema error.

        The error message contains 'From and To accounts must be different.'
        """
        with pytest.raises(ValidationError) as exc:
            TransferCreateSchema().load(
                self._valid_transfer_data(from_account_id="5", to_account_id="5")
            )
        # Cross-field validation error goes to _schema key.
        assert "_schema" in exc.value.messages

    def test_very_small_positive_amount_accepted(self):
        """amount=0.01 (smallest valid transfer) passes validation.

        Boundary of the Range(min=0, min_inclusive=False) validator.
        """
        data = TransferCreateSchema().load(
            self._valid_transfer_data(amount="0.01")
        )
        assert data["amount"] == Decimal("0.01")
