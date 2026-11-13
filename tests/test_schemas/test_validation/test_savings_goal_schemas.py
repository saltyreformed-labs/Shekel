"""Validation schema tests: savings goals, their goal-mode cross-field rules and the contribution's
range.
"""

from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.schemas.validation import SavingsGoalCreateSchema, SavingsGoalUpdateSchema


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
