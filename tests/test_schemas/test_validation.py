"""
Shekel Budget App -- Schema Validation Tests

Tests each Marshmallow schema's load() method directly for:
  - Required field enforcement (missing → ValidationError)
  - Type coercion (string amounts → Decimal)
  - Range validation (amount >= 0, month 1-12, etc.)
  - @pre_load empty-string stripping
  - @validates_schema cross-field rules
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app import tax_law as tax_law_module
from app.enums import PaycheckLineKindEnum, RecurrenceUnitEnum
from app.schemas.validation._helpers import _normalize_empty_inputs
from app.schemas.validation.templates import A_CADENCE_IS_REQUIRED
from tests._test_helpers import EMPTY_TAX_LAW, cadence_payload
from app.services.pay_rhythm import FixedDays
from app.schemas.validation import (
    RECURRENCE_NEEDS_A_START,
    AccountCreateSchema,
    CategoryCreateSchema,
    PaycheckLineCreateSchema,
    InlineTransactionCreateSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    PayHistorySchema,
    PayPeriodGenerateSchema,
    RaiseCreateSchema,
    RetirementReadinessQuerySchema,
    SalaryProfileCreateSchema,
    SavingsGoalCreateSchema,
    SavingsGoalUpdateSchema,
    TemplateCreateSchema,
    TemplateUpdateSchema,
    TransactionCreateSchema,
    TransactionUpdateSchema,
    TransferCreateSchema,
    TransferTemplateCreateSchema,
    TransferUpdateSchema,
)
from app.schemas.validation.retirement import errors_by_rail_control


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
        """nominal_day outside 29-31 fails Range validation.

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
                "nominal_day": "28",
            })
        assert "nominal_day" in exc.value.messages

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


# ── SavingsGoalCreateSchema ──────────────────────────────────────────


class TestSavingsGoalCreateSchema:
    """Tests for SavingsGoalCreateSchema."""

    def test_valid_data(self):
        """Valid savings goal data loads successfully."""
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Emergency Fund",
            "target_amount": "10000.00",
        })
        assert data["target_amount"] == Decimal("10000.00")

    def test_zero_target_loads_and_a_negative_one_is_rejected(self):
        """target_amount=0 loads: the schema's bound is the table's ``>= 0``.

        Ruling R-CC72 (plan step credit_card:CC-5-5d): a DEBT goal may target
        $0.00, and whether a goal is a debt goal is its account's category,
        which a schema cannot read -- so the "above $0.00 for a SAVINGS goal"
        refusal is the goal door's (``TestTheGoalDoor`` in
        ``tests/test_services/test_debt_goals.py``).  Re-expressed with the
        developer's rule-5 confirmation (2026-09-23); a NEGATIVE target is
        still refused here.
        """
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Zero Goal",
            "target_amount": "0",
        })
        assert data["target_amount"] == Decimal("0")
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Below Zero",
                "target_amount": "-0.01",
            })
        assert "target_amount" in exc.value.messages

    def test_missing_required_field(self):
        """Missing account_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "name": "No Account",
                "target_amount": "1000.00",
            })
        assert "account_id" in exc.value.messages


# ── SavingsGoalUpdateSchema ──────────────────────────────────────────


class TestSavingsGoalUpdateSchema:
    """Tests for SavingsGoalUpdateSchema."""

    def test_empty_strings_dropped_or_mapped_to_none(self):
        """@pre_load drops empty non-nullable fields, Nones nullable ones.

        ``name`` is not nullable, so its empty submit is dropped
        (partial-update semantics: the stored name is untouched).
        ``target_amount`` is ``allow_none``, so its empty submit loads
        as an explicit ``None`` -- the update route's setattr loop then
        clears the stored value, which is what an emptied form field
        means.
        """
        data = SavingsGoalUpdateSchema().load({
            "name": "",
            "target_amount": "",
        })
        assert "name" not in data
        assert data["target_amount"] is None

    def test_valid_partial_update(self):
        """A partial update carrying ``is_active`` loads WITHOUT it.

        Deleting a goal is the only writer of ``is_active`` since plan step
        credit_card:CC-5-5d (review M1): an edit that re-activated a deleted
        goal could bring it back onto an account whose type changed while no
        guard counted it.  Re-expressed with the developer's rule-5
        confirmation (2026-09-23): the unknown field is dropped, as every other
        unknown field is.
        """
        data = SavingsGoalUpdateSchema().load({
            "name": "Renamed",
            "is_active": "false",
        })
        assert data == {"name": "Renamed"}


class TestSavingsGoalCreateSchemaGoalMode:
    """Cross-field validation tests for goal mode in SavingsGoalCreateSchema."""

    def test_create_fixed_goal(self):
        """Fixed-mode goal with target_amount loads successfully.

        goal_mode_id=1 (Fixed), target_amount required, income fields absent.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum  # pylint: disable=import-outside-toplevel

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Emergency Fund",
            "target_amount": "5000.00",
            "goal_mode_id": str(fixed_id),
        })
        assert data["goal_mode_id"] == fixed_id
        assert data["target_amount"] == Decimal("5000.00")
        assert data["income_unit_id"] is None
        assert data["income_multiplier"] is None

    def test_create_income_relative_goal(self):
        """Income-relative goal with unit and multiplier loads successfully.

        goal_mode_id=2 (Income-Relative), income_unit_id and
        income_multiplier required, target_amount optional.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "3-Month Buffer",
            "goal_mode_id": str(ir_id),
            "income_unit_id": str(paychecks_id),
            "income_multiplier": "3.00",
        })
        assert data["goal_mode_id"] == ir_id
        assert data["income_unit_id"] == paychecks_id
        assert data["income_multiplier"] == Decimal("3.00")
        assert data["target_amount"] is None

    def test_income_relative_requires_unit_and_multiplier(self):
        """Income-relative mode without income fields raises ValidationError.

        Both income_unit_id and income_multiplier must be provided when
        goal_mode_id is Income-Relative.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Missing Fields",
                "goal_mode_id": str(ir_id),
            })
        assert "income_unit_id" in exc.value.messages

    def test_fixed_mode_rejects_income_fields(self):
        """Fixed-mode goal with income fields raises ValidationError.

        income_unit_id and income_multiplier must be absent for Fixed goals.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Bad Combo",
                "target_amount": "5000.00",
                "goal_mode_id": str(fixed_id),
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "3.00",
            })
        assert "income_unit_id" in exc.value.messages

    def test_multiplier_must_be_positive(self):
        """income_multiplier=0 or negative raises ValidationError.

        The Range(min=0, min_inclusive=False) validator rejects zero
        and negative values.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

        # Zero multiplier.
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Zero Mult",
                "goal_mode_id": str(ir_id),
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "0.00",
            })
        assert "income_multiplier" in exc.value.messages

        # Negative multiplier.
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Negative Mult",
                "goal_mode_id": str(ir_id),
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "-1.00",
            })
        assert "income_multiplier" in exc.value.messages

    def test_goal_mode_id_defaults_to_fixed_when_omitted(self):
        """Omitting goal_mode_id defaults to Fixed (backward compatibility).

        Existing forms that do not include goal_mode_id should produce
        a Fixed-mode goal automatically via load_default=1.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum  # pylint: disable=import-outside-toplevel

        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Default Mode",
            "target_amount": "5000.00",
        })
        assert data["goal_mode_id"] == ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    def test_target_amount_required_for_fixed_mode(self):
        """Fixed-mode goal without target_amount raises ValidationError.

        target_amount is conditionally required: mandatory for Fixed,
        optional for Income-Relative.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum  # pylint: disable=import-outside-toplevel

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "No Target",
                "goal_mode_id": str(fixed_id),
            })
        assert "target_amount" in exc.value.messages

    def test_target_amount_optional_for_income_relative(self):
        """Income-relative goal without target_amount is valid.

        target_amount is calculated on read by the service layer;
        it does not need to be provided at creation time.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "No Target OK",
            "goal_mode_id": str(ir_id),
            "income_unit_id": str(months_id),
            "income_multiplier": "3.00",
        })
        assert data["target_amount"] is None

    def test_invalid_goal_mode_id_rejected(self):
        """Invalid goal_mode_id (not in ref.goal_modes) raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Bad Mode",
                "target_amount": "5000.00",
                "goal_mode_id": "99",
            })
        assert "goal_mode_id" in exc.value.messages

    def test_invalid_income_unit_id_rejected(self):
        """Invalid income_unit_id (not in ref.income_units) raises ValidationError."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Bad Unit",
                "goal_mode_id": str(ir_id),
                "income_unit_id": "99",
                "income_multiplier": "3.00",
            })
        assert "income_unit_id" in exc.value.messages

    def test_fractional_multiplier_allowed(self):
        """Fractional income_multiplier like 0.50 is valid.

        Half a paycheck is a legitimate savings goal target.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Half Paycheck",
            "goal_mode_id": str(ir_id),
            "income_unit_id": str(paychecks_id),
            "income_multiplier": "0.50",
        })
        assert data["income_multiplier"] == Decimal("0.50")

    def test_income_relative_with_target_amount_accepted(self):
        """Income-relative goal with target_amount is valid.

        target_amount is ignored for income-relative goals (calculated
        on read), but including it is not an error -- it may be a
        cached value from a prior edit.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "With Cached Target",
            "goal_mode_id": str(ir_id),
            "income_unit_id": str(paychecks_id),
            "income_multiplier": "3.00",
            "target_amount": "5000.00",
        })
        assert data["target_amount"] == Decimal("5000.00")


class TestSavingsGoalUpdateSchemaGoalMode:
    """Cross-field validation tests for goal mode in SavingsGoalUpdateSchema."""

    def test_update_schema_rejects_fixed_with_income_fields(self):
        """Update schema enforces same cross-field rules as create schema.

        Fixed-mode update with income fields raises ValidationError.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum, IncomeUnitEnum  # pylint: disable=import-outside-toplevel

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        with pytest.raises(ValidationError) as exc:
            SavingsGoalUpdateSchema().load({
                "goal_mode_id": str(fixed_id),
                "target_amount": "5000.00",
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "3.00",
            })
        assert "income_unit_id" in exc.value.messages

    def test_update_schema_requires_income_fields_for_relative(self):
        """Update to income-relative mode requires income fields."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import GoalModeEnum  # pylint: disable=import-outside-toplevel

        ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        with pytest.raises(ValidationError) as exc:
            SavingsGoalUpdateSchema().load({
                "goal_mode_id": str(ir_id),
            })
        assert "income_unit_id" in exc.value.messages

    def test_update_without_goal_mode_skips_cross_validation(self):
        """Partial update omitting goal_mode_id skips cross-field checks.

        This allows updating other fields (name, target_date) without
        needing to re-specify the entire goal mode configuration.
        """
        data = SavingsGoalUpdateSchema().load({
            "name": "Updated Name",
            "target_amount": "6000.00",
        })
        assert data["name"] == "Updated Name"
        assert data["target_amount"] == Decimal("6000.00")


# ── SalaryProfileCreateSchema ────────────────────────────────────────


class TestSalaryProfileCreateSchema:
    """Tests for SalaryProfileCreateSchema."""

    def test_valid_data(self):
        """Valid salary profile data loads with defaults.

        The salary is the first pay entry, typed per paycheck (plan step
        salary:X-av-3a): its amount and the payday it pays from.
        """
        data = SalaryProfileCreateSchema().load({
            "name": "My Salary",
            "pay_amount": "2884.62",  # $75,000.00 a year / 26
            "pay_payday": "2026-01-02",
            "filing_status_id": "1",
            "state_code": "NC",
        })
        assert data["pay_amount"] == Decimal("2884.62")
        assert data["pay_payday"] == date(2026, 1, 2)

    def test_missing_required_field(self):
        """Missing pay (amount and payday) raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load({
                "name": "Bad Profile",
                "filing_status_id": "1",
                "state_code": "NC",
            })
        assert "pay_amount" in exc.value.messages
        assert "pay_payday" in exc.value.messages

    def test_pay_periods_per_year_cannot_be_submitted(self):
        """A submitted paycheck count reaches no column (R-F16).

        Input: a valid payload carrying ``pay_periods_per_year``, the field
        this schema offered as a 12 / 24 / 26 / 52 dropdown until plan step
        R-F16 dropped the column behind it.
        Expected: the key is absent from the loaded data -- ``BaseSchema``'s
        ``unknown = EXCLUDE`` drops it -- so a stale client, a replayed form or
        a hand-crafted POST cannot reinstate a second answer to "how often am
        I paid" beside ``budget.pay_schedule.cadence_days``.  The count derives
        from the cadence and from nothing a salary form submits.
        """
        data = SalaryProfileCreateSchema().load({
            "name": "Stale client",
            "pay_amount": "2884.62",  # $75,000.00 a year / 26
            "pay_payday": "2026-01-02",
            "filing_status_id": "1",
            "state_code": "NC",
            "pay_periods_per_year": "52",
        })
        assert "pay_periods_per_year" not in data

    def test_state_code_length(self):
        """A three-character state_code is no state the tax law lists, so it is refused."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load({
                "name": "Bad",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",
                "filing_status_id": "1",
                "state_code": "NCC",  # 3 chars, max is 2.
            })
        assert "state_code" in exc.value.messages


# ── RaiseCreateSchema ────────────────────────────────────────────────


class TestRaiseCreateSchema:
    """Tests for RaiseCreateSchema."""

    def test_valid_with_percentage(self):
        """Valid raise with percentage loads successfully."""
        data = RaiseCreateSchema().load({
            "raise_type_id": "1",
            "effective_month": "3",
            "effective_year": "2026",
            "percentage": "3.50",
        })
        assert data["percentage"] == Decimal("3.50")

    def test_valid_with_flat_amount(self):
        """Valid raise with flat_amount loads successfully."""
        data = RaiseCreateSchema().load({
            "raise_type_id": "1",
            "effective_month": "1",
            "effective_year": "2026",
            "flat_amount": "5000.00",
        })
        assert data["flat_amount"] == Decimal("5000.00")

    def test_both_percentage_and_flat_rejected(self):
        """Both percentage and flat_amount raises cross-field error."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "3",
                "effective_year": "2026",
                "percentage": "3",
                "flat_amount": "5000.00",
            })
        assert "_schema" in exc.value.messages

    def test_neither_percentage_nor_flat_rejected(self):
        """Neither percentage nor flat_amount raises cross-field error."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "3",
                "effective_year": "2026",
            })
        assert "_schema" in exc.value.messages

    def test_missing_effective_year_rejected(self):
        """Raise without effective_year is rejected."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "3",
                "percentage": "3",
            })
        assert "effective_year" in exc.value.messages

    def test_month_out_of_range(self):
        """effective_month=13 fails Range(1-12) validation."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_month": "13",
                "effective_year": "2026",
                "percentage": "3",
            })
        assert "effective_month" in exc.value.messages


# ── PaycheckLineCreateSchema ────────────────────────────────────────────


class TestDeductionCreateSchema:
    """Tests for PaycheckLineCreateSchema."""

    def test_valid_data(self):
        """Valid deduction data loads with defaults."""
        data = PaycheckLineCreateSchema().load({
            "name": "401k",
            "paycheck_line_kind_id": "1",
            "calc_method_id": "1",
            "amount": "250.0000",
        })
        assert data["amount"] == Decimal("250.0000")

    def test_the_schema_no_longer_reads_a_frequency_count(self):
        """``deductions_per_year`` left the schema with its column (plan step salary:R15-b).

        A line's cadence is a recurrence rule authored through the recurrence
        seam (R15-c's form), so a posted count is an unknown key: dropped by
        ``BaseSchema``'s EXCLUDE, never loaded, never written by name.
        """
        data = PaycheckLineCreateSchema().load({
            "name": "401k",
            "paycheck_line_kind_id": "1",
            "calc_method_id": "1",
            "amount": "250.0000",
            "deductions_per_year": "24",
        })
        assert "deductions_per_year" not in data
        assert "deductions_per_year" not in PaycheckLineCreateSchema().fields

    def test_missing_required_field(self):
        """Missing name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            PaycheckLineCreateSchema().load({
                "paycheck_line_kind_id": "1",
                "calc_method_id": "1",
                "amount": "100.0000",
            })
        assert "name" in exc.value.messages

    @pytest.mark.parametrize("member", list(PaycheckLineKindEnum))
    def test_a_target_account_is_accepted_on_a_deduction_and_refused_on_an_earning(
        self, member,
    ):
        """The one side-specific rule, swept over every kind (plan step salary:R18-b).

        A deduction may name the account it funds; an earning funds nothing,
        so the pair is refused on ``target_account_id`` with the sentence the
        flash allowlist carries.  IDs for logic: the side is read through the
        kind vocabulary, never the name.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.services import paycheck_line_kinds  # pylint: disable=import-outside-toplevel

        kind_id = ref_cache.paycheck_line_kind_id(member)
        payload = {
            "name": "Line", "paycheck_line_kind_id": str(kind_id),
            "calc_method_id": "1", "amount": "50.0000", "target_account_id": "7",
        }
        if member in paycheck_line_kinds.DEDUCTION_KINDS:
            assert PaycheckLineCreateSchema().load(payload)["target_account_id"] == 7
        else:
            with pytest.raises(ValidationError) as exc:
                PaycheckLineCreateSchema().load(payload)
            assert exc.value.messages["target_account_id"] == [
                "Only a deduction can fund an account; an earning line is "
                "paid to you and has no target account.",
            ]
        # Without a target every kind loads.
        del payload["target_account_id"]
        assert PaycheckLineCreateSchema().load(payload)["paycheck_line_kind_id"] == kind_id


    def test_a_chosen_cadence_with_a_blank_start_loads_and_the_span_is_composed(self):
        """A line's start may be blank (the opening payday); a stated start and bound load as one value.

        Plan step salary:R18-c (ruling **R-SAL38** (2), amending R-SAL30 /
        R-SAL31): the line schema carries the whole recurrence form mixin
        with ``recurrence_start_is_required`` OFF, so a chosen cadence with
        no start is not refused here -- the route derives the opening -- and
        the three end-bound controls compose to one ``EndBound``.  The
        template forms keep the requirement; an inverted window is refused
        on the end date, the mixin's own rule.
        """
        from app.enums import PeriodPlacementEnum  # pylint: disable=import-outside-toplevel
        from app.services.recurrence import EndsOnDate  # pylint: disable=import-outside-toplevel

        base = {
            "name": "Phone Allowance", "paycheck_line_kind_id": "1",
            "calc_method_id": "1", "amount": "45.0000",
            **cadence_payload(
                unit=RecurrenceUnitEnum.MONTH,
                placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                states_a_start=False,
            ),
        }
        blank = PaycheckLineCreateSchema().load(base)
        assert "starts_on" not in blank
        assert "recurrence_end_mode" not in blank

        stated = PaycheckLineCreateSchema().load({
            **base, "starts_on": "2026-09-01",
            "recurrence_end_mode": "on_date", "end_date": "2027-06-30",
        })
        assert stated["starts_on"] == date(2026, 9, 1)
        assert stated["recurrence_end_mode"] == EndsOnDate(on=date(2027, 6, 30))
        assert "end_date" not in stated

        with pytest.raises(ValidationError) as exc:
            PaycheckLineCreateSchema().load({
                **base, "starts_on": "2026-09-01",
                "recurrence_end_mode": "on_date", "end_date": "2026-08-01",
            })
        assert "end_date" in exc.value.messages

        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load({
                "name": "Rent", "account_id": "1", "category_id": "1",
                "transaction_type_id": "1", "amount": "100.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                    states_a_start=False,
                ),
            })
        assert exc.value.messages == RECURRENCE_NEEDS_A_START




# ── AccountCreateSchema ──────────────────────────────────────────────


class TestAccountCreateSchema:
    """Tests for AccountCreateSchema."""

    def test_valid_data(self):
        """Valid account data loads successfully."""
        data = AccountCreateSchema().load({
            "name": "Checking",
            "account_type_id": "1",
            "anchor_balance": "5000.00",
        })
        assert data["name"] == "Checking"
        assert data["anchor_balance"] == Decimal("5000.00")

    def test_missing_required_field(self):
        """Missing name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            AccountCreateSchema().load({
                "account_type_id": "1",
            })
        assert "name" in exc.value.messages

    def test_empty_strings_stripped(self):
        """@pre_load strips empty optional fields."""
        data = AccountCreateSchema().load({
            "name": "Test",
            "account_type_id": "1",
            "anchor_balance": "",
        })
        assert "anchor_balance" not in data


# ── PayPeriodGenerateSchema ──────────────────────────────────────────


class TestPayPeriodGenerateSchema:
    """Tests for PayPeriodGenerateSchema."""

    def test_valid_data_with_defaults(self):
        """Valid data uses defaults for num_periods and the rhythm.

        The rhythm is read off the loaded payload as the VALUE since plan
        step ``pay_calendar:C17-d-3`` (ruling **R-PC84**): the kind control
        and the day count default together to every 14 days, and the wire
        keys are consumed.
        """
        data = PayPeriodGenerateSchema().load({
            "start_date": "2026-03-01",
        })
        from datetime import date
        assert data["start_date"] == date(2026, 3, 1)
        assert data["num_periods"] == 52   # Default.
        assert data["rhythm"].cadence == FixedDays(14)  # Default.
        assert "cadence_days" not in data

    def test_num_periods_out_of_range(self):
        """num_periods=0 fails Range(1-260) validation."""
        with pytest.raises(ValidationError) as exc:
            PayPeriodGenerateSchema().load({
                "start_date": "2026-03-01",
                "num_periods": "0",
            })
        assert "num_periods" in exc.value.messages

    def test_cadence_days_out_of_range(self):
        """cadence_days=0 fails Range(1-365) validation."""
        with pytest.raises(ValidationError) as exc:
            PayPeriodGenerateSchema().load({
                "start_date": "2026-03-01",
                "cadence_days": "0",
            })
        assert "cadence_days" in exc.value.messages

    def test_a_one_day_cadence_is_ACCEPTED(self):
        """The floor is the COLUMN's, and 0 alone could not tell them apart.

        This field's floor was ``max(CADENCE_DAYS_MIN, 2)`` between plan steps
        X-ad-a and ``pay_calendar:C4-c``, because a one-day period could not be
        written into the stored ``end_date`` ``ck_pay_periods_date_order``
        bounded.  The case above rejects 0, which fails at EITHER floor, so it
        was green against both and the docstring's "Range(1-365)" was false of
        the code while it said so.  1 is the value that separates them.
        """
        assert PayPeriodGenerateSchema().load({
            "start_date": "2026-03-01",
            "cadence_days": "1",
        })["rhythm"].cadence == FixedDays(1)

    def test_missing_start_date(self):
        """Missing start_date raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            PayPeriodGenerateSchema().load({
                "num_periods": "10",
            })
        assert "start_date" in exc.value.messages


# ── PayHistorySchema ─────────────────────────────────────────────────


class TestPayHistorySchema:
    """Tests for PayHistorySchema (plan step **balance:X-bh-2**).

    The pay-periods settings door onto ``budget.pay_schedule.history_opens_on``.
    One optional field, and all three of its input shapes matter because the
    route indexes the loaded payload directly.
    """

    def test_an_omitted_key_loads_as_None(self):
        """``load_default=None`` is what keeps an absent key from 500ing.

        An adversarial review of this step deleted that default and the whole
        suite -- 817 cases -- stayed green, because every route test posts the
        key, empty or filled.  Without it the payload loads ``{}`` and the
        route's ``data["history_opens_on"]`` raises ``KeyError``: an unhandled
        500 on a door an ordinary crafted POST reaches.  ``RegisterSchema``
        had this exact case; its sibling did not, and the asymmetry was the
        tell.
        """
        assert PayHistorySchema().load({})["history_opens_on"] is None

    def test_an_empty_string_loads_as_None(self):
        """What a browser submits for the control nobody touched."""
        assert PayHistorySchema().load(
            {"history_opens_on": ""},
        )["history_opens_on"] is None

    def test_a_stated_day_loads_as_a_date(self):
        """THE CONTROL: the field is read, not merely tolerated."""
        from datetime import date
        assert PayHistorySchema().load(
            {"history_opens_on": "2024-06-03"},
        )["history_opens_on"] == date(2024, 6, 3)

    def test_a_day_outside_the_apps_calendar_is_refused(self):
        """The schema refuses what ``ck_pay_schedule_history_opens_range`` would.

        An ``<input type="date">`` accepts a five-digit year, so the CHECK is
        reachable from an ordinary browser and must never be the thing a user
        meets.
        """
        with pytest.raises(ValidationError) as exc:
            PayHistorySchema().load({"history_opens_on": "9999-01-01"})
        assert "history_opens_on" in exc.value.messages


# ── CategoryCreateSchema ─────────────────────────────────────────────


class TestCategoryCreateSchema:
    """Tests for CategoryCreateSchema."""

    def test_valid_data(self):
        """Valid category data loads with sort_order default."""
        data = CategoryCreateSchema().load({
            "group_name": "Auto",
            "item_name": "Car Payment",
        })
        assert data["group_name"] == "Auto"
        assert data["sort_order"] == 0  # Default.

    def test_missing_required_field(self):
        """Missing group_name raises ValidationError."""
        with pytest.raises(ValidationError) as exc:
            CategoryCreateSchema().load({
                "item_name": "Rent",
            })
        assert "group_name" in exc.value.messages


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
        """nominal_day=28 fails Range(min=29, max=31) validation.

        The lower boundary, and it is a real rule rather than an arbitrary
        floor: every month holds its first 28 days, so no first occurrence can
        ever have CLAMPED a 28.  Such a value would restate the day
        ``starts_on`` already carries, which
        ``ck_recurrence_rules_nominal_day`` refuses in the table.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(nominal_day="28")
            )
        assert "nominal_day" in exc.value.messages

    def test_nominal_day_32_rejected(self):
        """nominal_day=32 fails Range(min=29, max=31) validation.

        No month has 32 days. The schema must reject this before it reaches
        the recurrence engine.
        """
        with pytest.raises(ValidationError) as exc:
            TemplateCreateSchema().load(
                self._valid_template_data(nominal_day="32")
            )
        assert "nominal_day" in exc.value.messages

    def test_nominal_day_31_accepted(self):
        """nominal_day=31 is valid -- it is the day a 30-day month clamps.

        The schema grades the DOMAIN alone; whether 31 may sit beside this
        particular ``starts_on`` is a two-field rule the field cannot see, and
        ``RecurrenceSpec.__post_init__`` refuses the contradicting pair at the
        write door (mirroring ``ck_recurrence_rules_nominal_day``).
        """
        data = TemplateCreateSchema().load(
            self._valid_template_data(nominal_day="31")
        )
        assert data["nominal_day"] == 31

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


# ── TestSalaryProfileCreateSchemaBoundary ───────────────────────────


class TestSalaryProfileCreateSchemaBoundary:
    """Boundary tests for SalaryProfileCreateSchema state_code validation."""

    def _valid_salary_data(self, **overrides):
        """Return a valid salary profile payload with optional overrides."""
        data = {
            "name": "Test Profile",
            "pay_amount": "2884.62",  # $75,000.00 a year / 26
            "pay_payday": "2026-01-02",
            "filing_status_id": "1",
            "state_code": "NC",
        }
        data.update(overrides)
        return data

    def test_empty_state_code_rejected(self):
        """Empty state_code is stripped by @pre_load, then fails as missing required field.

        An empty state code would break the tax calculator lookup.
        """
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(
                self._valid_salary_data(state_code="")
            )
        assert "state_code" in exc.value.messages

    def test_single_char_state_code_rejected(self):
        """A one-character state_code is no state the tax law lists, so it is refused."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(
                self._valid_salary_data(state_code="N")
            )
        assert "state_code" in exc.value.messages

    def test_lowercase_state_code_refused(self):
        """Lower-case ``nc`` is refused: the tax law lists ``NC``, and no other spelling.

        Until plan step salary:X-at-3 this case pinned ``nc`` as ACCEPTED,
        stored as typed, leaving normalization to a later layer that never
        did it -- and the paycheck engine priced that profile's state tax at
        $0.00, since the law lists ``NC`` (finding SAL-575, measured at
        X-at-3).  Ruling R-SAL78: a save naming a state the law does not list
        is refused.  The form offers the law's own codes, so a browser never
        submits ``nc``.
        """
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(
                self._valid_salary_data(state_code="nc")
            )
        assert exc.value.messages["state_code"] == [
            "Shekel has no tax rules for 'nc'; it supports "
            f"{', '.join(tax_law_module.LAW.supported_states)}."
        ]

    def test_the_law_installed_when_validating_decides(self, tax_law):
        """The schema reads the law when it validates, not when it was imported.

        A law with no year lists no state, so even ``NC`` is refused under
        it -- the law a release ships (or a test installs) is the one that
        decides (plan step salary:X-at-3).
        """
        tax_law(EMPTY_TAX_LAW)
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(self._valid_salary_data())
        assert exc.value.messages["state_code"] == [
            "Shekel has no tax rules for 'NC'; it supports no state."
        ]




# ── TestCategoryCreateSchemaBoundary ────────────────────────────────


class TestCategoryCreateSchemaBoundary:
    """Boundary tests for CategoryCreateSchema field length limits."""

    def test_group_name_at_max_length(self):
        """group_name of exactly 100 characters passes Length(max=100) validation."""
        long_name = "G" * 100
        data = CategoryCreateSchema().load({
            "group_name": long_name,
            "item_name": "Test",
        })
        assert len(data["group_name"]) == 100

    def test_group_name_over_max_length(self):
        """group_name of 101 characters fails Length(max=100) validation."""
        long_name = "G" * 101
        with pytest.raises(ValidationError) as exc:
            CategoryCreateSchema().load({
                "group_name": long_name,
                "item_name": "Test",
            })
        assert "group_name" in exc.value.messages

    def test_xss_in_group_name(self):
        """Schema accepts raw HTML in group_name -- no sanitization at this layer.

        # Schema accepts raw HTML. XSS prevention relies on Jinja2 auto-escaping
        # in templates ({{ name }} auto-escapes by default). Verify template
        # escaping separately.
        """
        data = CategoryCreateSchema().load({
            "group_name": "<img src=x onerror=alert(1)>",
            "item_name": "Test",
        })
        assert data["group_name"] == "<img src=x onerror=alert(1)>"


# ── Audit Remediation Range Validation Tests (H-06, M-14) ──────────


class TestAnnualSalaryRange:
    """SalaryProfileCreateSchema rejects zero and negative pay (H-06).

    The salary is typed per paycheck since plan step salary:X-av-3a, so the
    bound is ``pay_amount``'s: above zero (``min_inclusive=False``).
    """

    def _base(self, **overrides):
        data = {
            "name": "Test",
            "pay_amount": "2884.62",  # $75,000.00 a year / 26
            "pay_payday": "2026-01-02",
            "filing_status_id": "1", "state_code": "NC",
        }
        data.update(overrides)
        return data

    def test_zero_salary_rejected(self):
        """pay_amount=0 is rejected (min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(self._base(pay_amount="0"))
        assert "pay_amount" in exc.value.messages

    def test_negative_salary_rejected(self):
        """Negative pay_amount is rejected."""
        with pytest.raises(ValidationError) as exc:
            # -$50,000.00 a year / 26
            SalaryProfileCreateSchema().load(self._base(pay_amount="-1923.08"))
        assert "pay_amount" in exc.value.messages

    def test_positive_salary_accepted(self):
        """Valid positive pay_amount passes."""
        data = SalaryProfileCreateSchema().load(self._base(pay_amount="1.00"))
        assert data["pay_amount"] == Decimal("1.00")


class TestRaiseRangeValidation:
    """RaiseCreateSchema rejects out-of-range years and amounts (M-14)."""

    def _base(self, **overrides):
        data = {
            "raise_type_id": "1", "effective_month": "7",
            "effective_year": "2026", "percentage": "3.00",
        }
        data.update(overrides)
        return data

    def test_year_zero_rejected(self):
        """effective_year=0 is rejected (min=2000)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(effective_year="0"))
        assert "effective_year" in exc.value.messages

    def test_year_far_future_rejected(self):
        """effective_year=999999 is rejected (max=2100)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(effective_year="999999"))
        assert "effective_year" in exc.value.messages

    def test_valid_year_accepted(self):
        """effective_year=2026 passes."""
        data = RaiseCreateSchema().load(self._base())
        assert data["effective_year"] == 2026


class TestContributionPerPeriodRange:
    """SavingsGoalCreateSchema enforces a strictly positive contribution.

    Pre-C-25 the schema accepted 0 (``Range(min=0)``) but the DB CHECK
    rejected it (``contribution_per_period IS NULL OR
    contribution_per_period > 0``), so a 0 input surfaced as a 500
    IntegrityError on commit.  F-106 / C-25 of the 2026-04-15 security
    remediation plan tightens the schema to
    ``Range(min=Decimal("0"), min_inclusive=False)`` so the boundary
    is enforced uniformly at the schema and storage tiers.  The
    detailed coverage lives in
    :mod:`tests.test_schemas.test_c25_boundary_inclusivity`; the
    cases here remain as a regression gate alongside the rest of the
    legacy schema-validation suite.
    """

    def _base(self, **overrides):
        data = {
            "account_id": "1", "name": "Emergency Fund",
            "target_amount": "10000.00",
        }
        data.update(overrides)
        return data

    def test_negative_contribution_rejected(self):
        """contribution_per_period=-100 is rejected by the strictly-positive bound."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load(
                self._base(contribution_per_period="-100.00")
            )
        assert "contribution_per_period" in exc.value.messages

    def test_zero_contribution_rejected(self):
        """contribution_per_period=0 is rejected (F-106 / C-25).

        DB CHECK rejects 0; the schema must reject it too so the
        gap surfaces as a clean 400 instead of a 500 on commit.
        """
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load(
                self._base(contribution_per_period="0.00")
            )
        assert "contribution_per_period" in exc.value.messages


class TestRaiseCreateSchemaEndYearAttribution:
    """The ONE end-year rule's refusals land on THIS form's controls.

    Plan step **salary:S3-f-2b** moved the rule into
    :func:`app.services.salary_raises.end_year_of`, which blames a HALF of the
    answer (``"mode"`` / ``"year"``); the schema maps that onto its own field
    names, and nothing graded the mapping (an adversarial review of the step).
    The rule itself is graded in ``test_salary_raises_end_year.py``; these
    cases grade only the attribution and the resolved payload.
    """

    @staticmethod
    def _recurring(**overrides):
        data = {
            "raise_type_id": "1", "effective_month": "1",
            "effective_year": "2027", "percentage": "5",
            "is_recurring": "on",
        }
        data.update(overrides)
        return data

    def test_an_unanswered_mode_lands_on_raise_end_mode(self):
        """The mode half is the ``raise_end_mode`` control."""
        errors = RaiseCreateSchema().validate(self._recurring())
        assert errors == {"raise_end_mode": [
            "Say how long this recurring raise is believed: pick an end "
            "year, or say it has none.",
        ]}

    def test_a_missing_year_lands_on_terminal_year(self):
        """The year half is the ``terminal_year`` control."""
        errors = RaiseCreateSchema().validate(
            self._recurring(raise_end_mode="year", terminal_year=""),
        )
        assert errors == {"terminal_year": [
            "Enter the last year this raise is believed to happen.",
        ]}

    def test_a_year_before_the_effective_year_lands_on_terminal_year(self):
        """...and names the payload's own effective year."""
        errors = RaiseCreateSchema().validate(
            self._recurring(raise_end_mode="year", terminal_year="2026"),
        )
        assert errors == {"terminal_year": [
            "A raise cannot end before it starts: it takes effect in 2027.",
        ]}

    def test_no_end_year_resolves_the_column_to_none_whatever_the_box_holds(self):
        """``drop_end_year_mode`` resolves through the same rule: mode wins."""
        data = RaiseCreateSchema().load(
            self._recurring(raise_end_mode="none", terminal_year="2031"),
        )
        assert data["terminal_year"] is None
        assert "raise_end_mode" not in data
        believed = RaiseCreateSchema().load(
            self._recurring(raise_end_mode="year", terminal_year="2031"),
        )
        assert believed["terminal_year"] == 2031


# ── RetirementReadinessQuerySchema: the rail's per-raise probe (S3-f-2b) ──


class TestReadinessQueryGathersTheRaiseProbes:
    """``raise_end_mode_<id>`` / ``raise_end_year_<id>`` become ``raise_probes``.

    Plan step **salary:S3-f-2b**.  The rail submits every recurring raise's
    end-year PAIR on every refresh, with the raise id in the parameter NAME;
    ``BaseSchema`` drops unknown keys, so the pairs are gathered in
    ``@pre_load`` into ``{raise_id: (mode, year)}`` and each is graded on the
    salary form's own vocabulary and year window.  What is NOT graded here is
    the cross-field rule against the raise's effective year -- that needs the
    row, and ``RetirementInputs.plan_with`` applies it.
    """

    def test_two_pairs_load_as_the_probe_dict(self):
        """One entry per raise id, each the ``(mode, year_or_None)`` pair."""
        data = RetirementReadinessQuerySchema().load({
            "swr": "4",
            "raise_end_mode_1": "none", "raise_end_year_1": "",
            "raise_end_mode_2": "year", "raise_end_year_2": "2031",
        })
        assert data["raise_probes"] == {1: ("none", None), 2: ("year", 2031)}
        assert data["swr"] == Decimal("0.04000")

    def test_no_pairs_means_no_probe_key(self):
        """A request from a rail with no recurring raises carries nothing."""
        data = RetirementReadinessQuerySchema().load({"swr": "4"})
        assert "raise_probes" not in data

    def test_an_empty_year_box_under_the_year_mode_reaches_the_service(self):
        """``("year", None)`` is a valid PAIR here; the rule refuses it there.

        The schema's job is the fields; whether "ends after" with no year is
        an answer is the ONE end-year rule's question, asked against the row.
        """
        data = RetirementReadinessQuerySchema().load({
            "raise_end_mode_5": "year", "raise_end_year_5": "",
        })
        assert data["raise_probes"] == {5: ("year", None)}

    def test_a_mode_outside_the_vocabulary_is_refused(self):
        """Only ``year`` and ``none`` are answers."""
        with pytest.raises(ValidationError) as exc:
            RetirementReadinessQuerySchema().load({
                "raise_end_mode_5": "forever", "raise_end_year_5": "",
            })
        assert "raise_probes" in exc.value.messages

    def test_a_year_without_its_mode_is_refused(self):
        """A crafted query naming a year and no mode answers nothing."""
        with pytest.raises(ValidationError) as exc:
            RetirementReadinessQuerySchema().load({"raise_end_year_5": "2031"})
        assert "raise_probes" in exc.value.messages

    def test_a_year_outside_the_column_window_is_refused(self):
        """The same 2000-2100 window the salary form's year fields state."""
        for year in ("1999", "2101"):
            with pytest.raises(ValidationError) as exc:
                RetirementReadinessQuerySchema().load({
                    "raise_end_mode_5": "year", "raise_end_year_5": year,
                })
            assert "raise_probes" in exc.value.messages

    def test_a_raise_id_is_read_as_every_row_id_is(self):
        """``RowId``'s spelling: ``007`` and an empty id are refusals."""
        for raw in ("007", "", "-5", "1.9"):
            with pytest.raises(ValidationError) as exc:
                RetirementReadinessQuerySchema().load({
                    f"raise_end_mode_{raw}": "none",
                })
            assert "raise_probes" in exc.value.messages


class TestErrorsByRailControl:
    """A schema's refusals, re-keyed onto the assumptions rail's controls.

    The ONE error shape the rail renders, ``{control name: [messages]}``
    (plan step salary:S3-f-3 for the Save; salary:S3-f-4, ruling **R-SAL33**,
    for the readiness GET's refusals).  ``raise_probes`` is marshmallow's
    ``Dict`` report and is the only field whose name is not its control's.
    """

    def test_a_refused_half_lands_on_its_own_control(self):
        """A mode refusal keys ``raise_end_mode_<id>``; a year refusal the year's."""
        assert errors_by_rail_control({
            "raise_probes": {
                "5": {"value": {"mode": ["Must be one of: year, none."]}},
                "7": {"value": {"year": ["Must be greater than or equal to 2000."]}},
            },
        }) == {
            "raise_end_mode_5": ["Must be one of: year, none."],
            "raise_end_year_7": ["Must be greater than or equal to 2000."],
        }

    def test_a_refused_id_lands_on_the_mode_control_of_the_id_as_submitted(self):
        """``RowId``'s refusal names no rendered row; it keys the first control."""
        assert errors_by_rail_control({
            "raise_probes": {"007": {"key": ["Not a valid row id."]}},
        }) == {"raise_end_mode_007": ["Not a valid row id."]}

    def test_every_other_field_passes_through_under_its_own_name(self):
        """``swr``, ``months`` and the rest are already keyed by their controls."""
        errors = {
            "swr": ["Must be greater than or equal to 0 and less than or equal to 1."],
            "months": ["Must be greater than or equal to 0 and less than or equal to 180."],
            "raise_probes": {"5": {"value": {"year": ["Not a valid integer."]}}},
        }
        by_control = errors_by_rail_control(errors)
        assert by_control == {
            "swr": ["Must be greater than or equal to 0 and less than or equal to 1."],
            "months": ["Must be greater than or equal to 0 and less than or equal to 180."],
            "raise_end_year_5": ["Not a valid integer."],
        }
        # A copy, not the caller's lists: the route echoes the schema's dict
        # nowhere else, but a helper that aliased it would let a later
        # ``extend`` on one key leak into the other.
        by_control["swr"].append("x")
        assert errors["swr"] == [
            "Must be greater than or equal to 0 and less than or equal to 1.",
        ]

    def test_no_probe_field_is_the_identity(self):
        """A settings-only refusal has nothing to re-key."""
        assert errors_by_rail_control({"planned_retirement_date": ["Must be in the future."]}) == {
            "planned_retirement_date": ["Must be in the future."],
        }

    def test_raise_probes_is_the_only_field_whose_report_is_not_a_list(self):
        """The pass-through copies a LIST; a second nested field would need its own re-keying.

        marshmallow reports a ``Dict`` or ``Nested`` field as a dict, which
        ``list(messages)`` would silently reduce to its keys (an adversarial
        review of plan step salary:S3-f-4).  So the census: of both schemas
        that read the rail's pair, ``raise_probes`` is the one such field.
        """
        # pylint: disable=import-outside-toplevel
        from marshmallow import fields
        from app.schemas.validation import RetirementSettingsSchema

        for schema in (RetirementReadinessQuerySchema(), RetirementSettingsSchema()):
            nested = {
                name for name, field in schema.fields.items()
                if isinstance(field, (fields.Dict, fields.Nested, fields.List, fields.Mapping))
            }
            assert nested == {"raise_probes"}, (type(schema).__name__, nested)


class TestLoanParamsCreateSchemaStatedBalance:
    """The setup schema's stated-balance pair (plan step recurrence:R20, ruling R-R72 part 3).

    ``anchor_balance`` / ``anchor_date`` replaced the required
    ``current_principal`` nothing read; they are the true-up schema's pair,
    bounded the same way, and the not-in-the-future rule is ONE function both
    schemas call.
    """

    @staticmethod
    def _payload(**overrides):
        """Return a complete, valid setup payload with *overrides* applied."""
        payload = {
            "original_principal": "250000.00",
            "anchor_balance": "200000.00",
            "anchor_date": "2020-06-01",
            "interest_rate": "6.500",
            "term_months": "360",
            "origination_date": "2020-01-01",
            "payment_day": "1",
        }
        payload.update(overrides)
        return payload

    def test_the_pair_loads_as_a_decimal_and_a_date(self):
        """A valid pair loads typed, beside the params fields."""
        data = LoanParamsCreateSchema().load(self._payload())
        assert data["anchor_balance"] == Decimal("200000.00")
        assert data["anchor_date"] == date(2020, 6, 1)
        assert "current_principal" not in data

    def test_both_are_required(self):
        """Omitting either half is a validation error naming it."""
        for missing in ("anchor_balance", "anchor_date"):
            payload = self._payload()
            del payload[missing]
            with pytest.raises(ValidationError) as exc:
                LoanParamsCreateSchema().load(payload)
            assert missing in exc.value.messages

    def test_a_stray_current_principal_is_dropped_not_read(self):
        """A stale client's retired field is excluded; it does not stand in for the pair."""
        payload = self._payload(current_principal="150000.00")
        data = LoanParamsCreateSchema().load(payload)
        assert "current_principal" not in data
        assert data["anchor_balance"] == Decimal("200000.00")
        del payload["anchor_balance"]
        with pytest.raises(ValidationError) as exc:
            LoanParamsCreateSchema().load(payload)
        assert "anchor_balance" in exc.value.messages

    def test_a_negative_balance_is_rejected_and_zero_accepted(self):
        """``anchor_balance >= 0``: the true-up's bound, and the table's CHECK."""
        with pytest.raises(ValidationError) as exc:
            LoanParamsCreateSchema().load(self._payload(anchor_balance="-0.01"))
        assert "anchor_balance" in exc.value.messages
        data = LoanParamsCreateSchema().load(self._payload(anchor_balance="0"))
        assert data["anchor_balance"] == Decimal("0")

    def test_a_future_date_is_rejected_by_both_schemas_alike(self):
        """Tomorrow is refused with the true-up schema's own sentence; today is accepted."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        with pytest.raises(ValidationError) as setup_exc:
            LoanParamsCreateSchema().load(self._payload(anchor_date=tomorrow))
        with pytest.raises(ValidationError) as trueup_exc:
            LoanAnchorTrueupSchema().load(
                {"anchor_date": tomorrow, "anchor_balance": "1.00"},
            )
        assert setup_exc.value.messages["anchor_date"] == [
            "Anchor date cannot be in the future.",
        ]
        assert trueup_exc.value.messages["anchor_date"] == [
            "Anchor date cannot be in the future.",
        ]
        assert (
            LoanParamsCreateSchema().load(self._payload(anchor_date=today))["anchor_date"]
            == date.today()
        )

    def test_the_origination_half_of_the_bound_is_not_the_schemas(self):
        """A date before origination loads here: the route refuses it, the way the doors do."""
        data = LoanParamsCreateSchema().load(self._payload(anchor_date="2019-12-31"))
        assert data["anchor_date"] < data["origination_date"]
