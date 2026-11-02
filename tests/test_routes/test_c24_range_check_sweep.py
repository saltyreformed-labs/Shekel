"""
Shekel Budget App -- C-24 Range and CHECK Constraint Sweep Tests

Verifies the schema bound additions and database CHECK constraint
sweep introduced by commit C-24 of the 2026-04-15 security
remediation plan:

  - F-011: ``RaiseCreateSchema.percentage`` and ``flat_amount`` are
    bounded to a positive, column-fitting range that rejects pay-cut
    values and absurd typos.
  - F-012: ``PaycheckLineCreateSchema.amount`` carries a wide
    field-level Range plus the
    ``validate_amount_against_calc_method`` cross-field rule that
    caps percent-method deductions at 100%.
  - F-074: ``SalaryProfile`` W-4 fields gain ``Range(min=0)``
    validators on both create and update schemas.
  - F-075: ``TaxBracketSetSchema`` monetary fields gained
    ``Range(min=0)`` validators and ``tax_year`` the same
    ``Range(2000, 2100)`` enforced on raises.  The schema, and the FICA
    and state-tax form schemas beside it, went with their Settings forms
    at plan step salary:X-at-1; the law's own import-time checks carry
    those bounds now (``tests/test_tax_law.py``).
  - F-077: 21 storage-tier CHECK constraints across nine tables
    that reject raw-SQL bypasses of the form-layer validators.

For each finding, the tests cover three dimensions:

  1. **Boundary acceptance** -- a value at each side of the new
     bound is accepted by the schema (no false rejections).
  2. **Bound rejection** -- a value just outside the bound surfaces
     a clean :class:`marshmallow.exceptions.ValidationError` on the
     correct field name (no IntegrityError fall-through).
  3. **Storage-tier rejection** -- a raw INSERT/UPDATE that bypasses
     the schema and would violate the predicate is rejected by the
     database with a named CHECK violation.

Tests for the migration's pre-flight refusal logic live alongside
the existing C-22/C-23 migration round-trip coverage.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.extensions import db
from app.models.interest_params import InterestParams
from app.models.loan_features import RateHistory
from app.models.paycheck_line import PaycheckLine
from app.models.ref import (
    AccountType, CalcMethod, PaycheckLineKind,
    FilingStatus, RaiseType, TaxType,
)
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import account_service
from app.schemas.validation import (
    PaycheckLineCreateSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
    RaiseCreateSchema,
    RetirementReadinessQuerySchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
)
from tests._test_helpers import constraint_name_from, start_test_pay_list


# ── Constraint name constants ─────────────────────────────────────
#
# Each must stay in sync with the matching declaration in
# ``app/models/`` and with the migration body in
# ``migrations/versions/b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py``.

CK_INTEREST_APY = "ck_interest_params_valid_apy"
CK_INVEST_RETURN = "ck_investment_params_valid_return"
CK_INVEST_LIMIT = "ck_investment_params_nonneg_contribution_limit"
CK_INVEST_FLAT = "ck_investment_params_valid_employer_flat_pct"
CK_INVEST_MATCH = "ck_investment_params_valid_employer_match_pct"
CK_INVEST_CAP = "ck_investment_params_valid_employer_match_cap"
CK_RATE_HISTORY = "ck_rate_history_valid_interest_rate"
CK_USER_SWR = "ck_user_settings_valid_safe_withdrawal"
CK_USER_TAX_RATE = "ck_user_settings_valid_estimated_tax_rate"
CK_DEDUCTION_INFL_RATE = "ck_paycheck_lines_valid_inflation_rate"
CK_DEDUCTION_INFL_MONTH = "ck_paycheck_lines_valid_inflation_month"
CK_RAISE_YEAR = "ck_salary_raises_valid_effective_year"
CK_STATE_TAX_DEDUCTION = "ck_state_tax_configs_nonneg_standard_deduction"
CK_STATE_TAX_YEAR = "ck_state_tax_configs_valid_tax_year"
CK_FICA_TAX_YEAR = "ck_fica_configs_valid_tax_year"
CK_BRACKET_TAX_YEAR = "ck_tax_bracket_sets_valid_tax_year"


#: The shared reader, moved to ``tests/_test_helpers`` at plan step
#: **salary:S3-b** when a second suite needed it and copied it verbatim
#: instead.  Aliased rather than renamed at ~40 call sites below, which
#: would be churn in a file this change does not otherwise touch.
_constraint_name_from = constraint_name_from


# ── F-011: RaiseCreateSchema bounds ───────────────────────────────


class TestRaiseSchemaBounds:
    """Schema-layer bound checks on ``RaiseCreateSchema`` (F-011 / C-24)."""

    def _payload(self, **overrides):
        """Build a baseline raise payload with valid required fields."""
        base = {
            "raise_type_id": "1",
            "effective_month": "1",
            "effective_year": "2026",
            "is_recurring": "false",
        }
        base.update(overrides)
        return base

    def test_minimum_percentage_accepted(self):
        """0.01% is the smallest accepted positive raise."""
        schema = RaiseCreateSchema()
        data = schema.load(
            self._payload(percentage="0.01"),
        )
        assert data["percentage"] == Decimal("0.01")

    def test_zero_percentage_rejected(self):
        """A zero raise is not a raise (no pay cuts policy, F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(percentage="0"))
        assert "percentage" in info.value.messages

    def test_negative_percentage_rejected(self):
        """Pay cuts are not modelled (F-011 explicit decision)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(percentage="-5"))
        assert "percentage" in info.value.messages

    def test_percentage_at_upper_bound_accepted(self):
        """200% is the realistic ceiling (3x salary jump)."""
        schema = RaiseCreateSchema()
        data = schema.load(self._payload(percentage="200"))
        assert data["percentage"] == Decimal("200")

    def test_percentage_above_upper_rejected(self):
        """An order-of-magnitude typo (e.g. 2000) is rejected at the form."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(percentage="201"))
        assert "percentage" in info.value.messages

    def test_minimum_flat_amount_accepted(self):
        """1 cent is the smallest accepted positive flat raise."""
        schema = RaiseCreateSchema()
        data = schema.load(self._payload(flat_amount="0.01"))
        assert data["flat_amount"] == Decimal("0.01")

    def test_zero_flat_amount_rejected(self):
        """A zero flat raise is not a raise (F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(flat_amount="0"))
        assert "flat_amount" in info.value.messages

    def test_negative_flat_amount_rejected(self):
        """Pay cuts are not modelled (F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(flat_amount="-100"))
        assert "flat_amount" in info.value.messages

    def test_flat_amount_at_upper_bound_accepted(self):
        """$10,000,000 is the form-layer ceiling for a flat raise."""
        schema = RaiseCreateSchema()
        data = schema.load(self._payload(flat_amount="10000000"))
        assert data["flat_amount"] == Decimal("10000000")

    def test_flat_amount_above_upper_rejected(self):
        """Above $10M is treated as a typo (F-011)."""
        schema = RaiseCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(flat_amount="10000001"))
        assert "flat_amount" in info.value.messages


# ── F-012: PaycheckLineCreateSchema bounds + cross-field ─────────────


class TestDeductionSchemaBounds:
    """Schema-layer bound checks on ``PaycheckLineCreateSchema`` (F-012 / C-24)."""

    def _payload(self, app, **overrides):
        """Build a baseline deduction payload with valid FK ids."""
        with app.app_context():
            timing_id = (
                db.session.query(PaycheckLineKind)
                .filter_by(name="pre_tax_deduction").one().id
            )
            flat_id = (
                db.session.query(CalcMethod)
                .filter_by(name="flat").one().id
            )
        base = {
            "name": "401k",
            "paycheck_line_kind_id": str(timing_id),
            "calc_method_id": str(flat_id),
            "amount": "500.00",
        }
        base.update(overrides)
        return base

    def test_minimum_amount_accepted(self, app):
        """4-decimal precision min (0.0001) is accepted."""
        schema = PaycheckLineCreateSchema()
        data = schema.load(self._payload(app, amount="0.0001"))
        assert data["amount"] == Decimal("0.0001")

    def test_zero_amount_rejected(self, app):
        """A zero deduction has no effect; rejected at the schema."""
        schema = PaycheckLineCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, amount="0"))
        assert "amount" in info.value.messages

    def test_dollar_amount_at_upper_accepted(self, app):
        """$1M is the wide ceiling for the flat-dollar form."""
        schema = PaycheckLineCreateSchema()
        data = schema.load(self._payload(app, amount="1000000"))
        assert data["amount"] == Decimal("1000000")

    def test_dollar_amount_above_upper_rejected(self, app):
        """An obvious extra-digit typo on a flat deduction is rejected."""
        schema = PaycheckLineCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, amount="1000001"))
        assert "amount" in info.value.messages

    def test_percentage_method_caps_at_100(self, app):
        """``validate_amount_against_calc_method`` rejects > 100% percent."""
        with app.app_context():
            pct_id = (
                db.session.query(CalcMethod)
                .filter_by(name="percentage").one().id
            )
        schema = PaycheckLineCreateSchema()
        payload = self._payload(
            app, calc_method_id=str(pct_id), amount="150",
        )
        with pytest.raises(ValidationError) as info:
            schema.load(payload)
        assert "amount" in info.value.messages

    def test_percentage_method_accepts_100(self, app):
        """100% is the inclusive percent ceiling (whole-paycheck deduction)."""
        with app.app_context():
            pct_id = (
                db.session.query(CalcMethod)
                .filter_by(name="percentage").one().id
            )
        schema = PaycheckLineCreateSchema()
        payload = self._payload(
            app, calc_method_id=str(pct_id), amount="100",
        )
        data = schema.load(payload)
        assert data["amount"] == Decimal("100")

    def test_percentage_method_accepts_typical(self, app):
        """A typical 6% 401(k) deduction passes both validators."""
        with app.app_context():
            pct_id = (
                db.session.query(CalcMethod)
                .filter_by(name="percentage").one().id
            )
        schema = PaycheckLineCreateSchema()
        data = schema.load(self._payload(
            app, calc_method_id=str(pct_id), amount="6",
        ))
        assert data["amount"] == Decimal("6")

    def test_inflation_rate_percent_input_accepted(self, app):
        """3% inflation input passes; the route divides by 100 later."""
        schema = PaycheckLineCreateSchema()
        data = schema.load(self._payload(
            app, inflation_enabled="true", inflation_rate="3",
        ))
        assert data["inflation_rate"] == Decimal("3")

    def test_inflation_rate_above_100_rejected(self, app):
        """A 150% per-year escalation is a typo, not a rate."""
        schema = PaycheckLineCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(
                app, inflation_enabled="true", inflation_rate="150",
            ))
        assert "inflation_rate" in info.value.messages

    def test_annual_cap_zero_rejected(self, app):
        """``annual_cap`` must be positive when present (DB CHECK > 0)."""
        schema = PaycheckLineCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, annual_cap="0"))
        assert "annual_cap" in info.value.messages


# ── F-074: SalaryProfile W-4 field bounds ─────────────────────────


class TestSalaryProfileSchemaBounds:
    """Schema-layer bound checks on the W-4 fields (F-074 / C-24)."""

    def _payload(self, app, **overrides):
        """Build a baseline salary-profile payload."""
        with app.app_context():
            single_id = (
                db.session.query(FilingStatus)
                .filter_by(name="single").one().id
            )
        base = {
            "name": "Test Profile",
            "pay_amount": "3846.15",  # $100,000.00 a year / 26
            "pay_payday": "2026-01-02",
            "filing_status_id": str(single_id),
            "state_code": "NC",
        }
        base.update(overrides)
        return base

    def test_zero_w4_fields_accepted(self, app):
        """The schema's ``load_default="0"`` round-trips through the bound."""
        schema = SalaryProfileCreateSchema()
        data = schema.load(self._payload(
            app,
            additional_income="0",
            additional_deductions="0",
            extra_withholding="0",
        ))
        assert data["additional_income"] == Decimal("0")
        assert data["additional_deductions"] == Decimal("0")
        assert data["extra_withholding"] == Decimal("0")

    def test_negative_additional_income_rejected(self, app):
        """DB CHECK ``additional_income >= 0`` is now matched at the schema."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, additional_income="-1"))
        assert "additional_income" in info.value.messages

    def test_negative_additional_deductions_rejected(self, app):
        """DB CHECK ``additional_deductions >= 0`` matched at the schema."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, additional_deductions="-1"))
        assert "additional_deductions" in info.value.messages

    def test_negative_extra_withholding_rejected(self, app):
        """DB CHECK ``extra_withholding >= 0`` matched at the schema."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, extra_withholding="-1"))
        assert "extra_withholding" in info.value.messages

    def test_extra_digit_typo_rejected(self, app):
        """$10M+1 trips the form-layer typo guard."""
        schema = SalaryProfileCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load(self._payload(app, additional_income="10000001"))
        assert "additional_income" in info.value.messages

    def test_update_schema_inherits_bounds(self):
        """``SalaryProfileUpdateSchema`` rejects negatives identically."""
        schema = SalaryProfileUpdateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({"additional_income": "-5"})
        assert "additional_income" in info.value.messages


# ── Misc schema bounds ────────────────────────────────────────────


class TestMiscSchemaBounds:
    """Bounds added on F-077-adjacent schemas (investment; FICA and state until X-at-1)."""

    def test_invest_negative_contribution_limit_rejected(self):
        """A -1 annual contribution limit is refused at the form, on its own field."""
        schema = InvestmentParamsCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({
                "assumed_annual_return": "0.07",
                "annual_contribution_limit": "-1",
            })
        assert "annual_contribution_limit" in info.value.messages

    def test_invest_contribution_limit_year_out_of_range_rejected(self):
        """A contribution-limit year of 1999, below the schema's 2000-2100 range, is refused."""
        schema = InvestmentParamsCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({
                "assumed_annual_return": "0.07",
                "contribution_limit_year": "1999",
            })
        assert "contribution_limit_year" in info.value.messages

    def test_invest_return_negative_100_percent_rejected(self):
        """DH-#28 follow-up: a -100% return (fraction -1) is rejected.

        The schema's percent @pre_load turns "-100" into the fraction
        "-1"; the exclusive lower bound (min_inclusive=False) rejects it
        because -1 is the degenerate, non-invertible reverse-projection
        rate (1 + rate == 0).
        """
        schema = InvestmentParamsCreateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({"assumed_annual_return": "-100"})
        assert "assumed_annual_return" in info.value.messages

    def test_invest_update_return_negative_100_percent_rejected(self):
        """The update schema rejects -100% just like the create schema."""
        schema = InvestmentParamsUpdateSchema()
        with pytest.raises(ValidationError) as info:
            schema.load({"assumed_annual_return": "-100"})
        assert "assumed_annual_return" in info.value.messages

    def test_invest_return_just_above_negative_one_accepted(self):
        """A return just above -100% (-99.999% -> -0.99999) is accepted.

        Pins that the bound is exclusive only AT -1, not a wholesale ban
        on deep-loss assumptions: -0.99999 is the smallest Numeric(7,5)
        value above the new bound.
        """
        schema = InvestmentParamsCreateSchema()
        result = schema.load({"assumed_annual_return": "-99.999"})
        # "-99.999" percent / 100 -> Decimal("-0.99999")
        assert result["assumed_annual_return"] == Decimal("-0.99999")

    def test_retirement_return_rate_negative_100_percent_rejected(self):
        """The retirement return-rate what-if mirrors the same > -1 bound.

        Repointed in P3c: the gap fragment's RetirementGapQuerySchema
        retired with the old page; the readiness what-if schema carries
        the identical (-1, 1] bound (folded in, not weakened).
        """
        schema = RetirementReadinessQuerySchema()
        with pytest.raises(ValidationError) as info:
            schema.load({"return_rate": "-100"})
        assert "return_rate" in info.value.messages


# ── F-077: Storage-tier CHECK enforcement (raw SQL bypass) ────────
#
# Each test below INSERTs or UPDATEs via raw SQL so the route layer
# (and the now-tightened schemas) cannot intercept the value, and
# asserts the database rejects with the specific named constraint.


def _insert_account(seed_user, name, type_name):
    """Insert a budget.accounts row of the given account type."""
    acct_type = (
        db.session.query(AccountType).filter_by(name=type_name).one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=Decimal("0.00"),
        ),
    )
    db.session.add(account)
    db.session.commit()
    return account


class TestInterestParamsCheck:
    """``budget.interest_params.apy`` CHECK (F-077)."""

    def test_apy_above_one_rejected(self, app, seed_user):
        """A raw INSERT of a 150% APY (1.5) is refused by ``ck_interest_params_valid_apy``."""
        with app.app_context():
            account = _insert_account(seed_user, "HYSA 1", "HYSA")
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.interest_params "
                        "(account_id, apy, compounding_frequency_id, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 1.5, "
                        "(SELECT id FROM ref.compounding_frequencies "
                        " WHERE name = 'daily'), now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INTEREST_APY

    def test_apy_at_one_accepted(self, app, seed_user):
        """Storage-tier upper bound is inclusive at 1.0 (== 100% APY)."""
        with app.app_context():
            account = _insert_account(seed_user, "HYSA 2", "HYSA")
            params = InterestParams(
                account_id=account.id,
                apy=Decimal("1.00000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            )
            db.session.add(params)
            db.session.commit()
            assert params.apy == Decimal("1.00000")

    def test_apy_negative_rejected(self, app, seed_user):
        """A raw INSERT of a -0.01 APY is refused by the same CHECK's lower bound."""
        with app.app_context():
            account = _insert_account(seed_user, "HYSA 3", "HYSA")
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.interest_params "
                        "(account_id, apy, compounding_frequency_id, "
                        " created_at, updated_at) "
                        "VALUES (:aid, -0.01, "
                        "(SELECT id FROM ref.compounding_frequencies "
                        " WHERE name = 'daily'), now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INTEREST_APY


class TestInvestmentParamsCheck:
    """``budget.investment_params`` CHECK constraints (F-077)."""

    def _account(self, seed_user):
        """Insert the 401(k) account each case's investment params belong to."""
        return _insert_account(seed_user, "401k", "401(k)")

    def test_negative_contribution_limit_rejected(self, app, seed_user):
        """A raw INSERT of a -$1.00 contribution limit is refused by its non-negative CHECK."""
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " annual_contribution_limit, "
                        " employer_contribution_type_id, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, -1.00, "
                        "(SELECT id FROM ref.employer_contribution_types "
                        " WHERE name = 'none'), now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_LIMIT

    def test_return_at_negative_one_rejected(self, app, seed_user):
        """DH-#28 follow-up: storage rejects assumed_annual_return = -1.

        Raw INSERT bypasses the tightened schema; the DB CHECK
        ``ck_investment_params_valid_return`` (now ``> -1``) is the last
        line of defence against the non-invertible -100% return.
        """
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type_id, "
                        " created_at, updated_at) "
                        "VALUES (:aid, -1.00, "
                        "(SELECT id FROM ref.employer_contribution_types "
                        " WHERE name = 'none'), now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_RETURN

    def test_employer_flat_above_one_rejected(self, app, seed_user):
        """A raw INSERT of a 150% employer flat contribution (1.5) is refused by its CHECK."""
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type_id, "
                        " employer_flat_percentage, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, "
                        "(SELECT id FROM ref.employer_contribution_types "
                        " WHERE name = 'flat_percentage'), 1.5, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_FLAT

    def test_employer_match_above_ten_rejected(self, app, seed_user):
        """A raw UPDATE of the employer match to -0.01 is refused by its CHECK (``0 <= x <= 10``).

        The column's ``Numeric(5,4)`` caps the match at 9.9999, below the CHECK's upper
        bound of 10, so the case stores 9.9999 and grades the CHECK's lower bound.
        """
        with app.app_context():
            account = self._account(seed_user)
            # The Numeric(5,4) column physically caps below 10 so we
            # exercise the column type rather than the CHECK by
            # picking a value > 9.9999.  The DataError surfaces from
            # the column type and is enough to confirm the CHECK is
            # not the binding constraint here; storage tier remains
            # protected on every face.
            try:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type_id, "
                        " employer_match_percentage, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, "
                        "(SELECT id FROM ref.employer_contribution_types "
                        " WHERE name = 'match'), 9.9999, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            # Now violate the CHECK directly via UPDATE that reduces
            # to an in-range column value but still trips the
            # constraint when set to a negative value.
            inserted_id = db.session.execute(
                text(
                    "SELECT id FROM budget.investment_params "
                    "WHERE account_id = :aid"
                ),
                {"aid": account.id},
            ).scalar()
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE budget.investment_params "
                        "SET employer_match_percentage = -0.01 "
                        "WHERE id = :iid"
                    ),
                    {"iid": inserted_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_MATCH

    def test_employer_match_cap_above_one_rejected(self, app, seed_user):
        """A raw INSERT of a 150% employer match cap (1.5) is refused by its CHECK."""
        with app.app_context():
            account = self._account(seed_user)
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.investment_params "
                        "(account_id, assumed_annual_return, "
                        " employer_contribution_type_id, "
                        " employer_match_cap_percentage, "
                        " created_at, updated_at) "
                        "VALUES (:aid, 0.07, "
                        "(SELECT id FROM ref.employer_contribution_types "
                        " WHERE name = 'match'), 1.5, "
                        "        now(), now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_INVEST_CAP


class TestRateHistoryCheck:
    """``budget.rate_history.interest_rate`` CHECK (F-077)."""

    def test_rate_above_one_rejected(self, app, seed_user):
        """A raw INSERT of a 150% loan rate (1.5) is refused by the rate CHECK's upper bound."""
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage R", "Mortgage",
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.rate_history "
                        "(account_id, effective_date, interest_rate, "
                        " created_at) "
                        "VALUES (:aid, '2026-01-01', 1.5, now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_RATE_HISTORY

    def test_rate_at_one_accepted(self, app, seed_user):
        """A 100% loan rate (1.00000) is stored: the rate CHECK's upper bound is inclusive."""
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage R2", "Mortgage",
            )
            row = RateHistory(
                account_id=account.id,
                effective_date=date(2026, 1, 1),
                interest_rate=Decimal("1.00000"),
            )
            db.session.add(row)
            db.session.commit()
            assert row.id is not None

    def test_rate_below_zero_rejected(self, app, seed_user):
        """The lower bound of ``ck_rate_history_valid_interest_rate``.

        The CHECK is ``interest_rate >= 0 AND interest_rate <= 1``; the
        sibling tests pin the upper bound, this pins the lower.  Both
        bounds matter because DH-#56 retired ``loan_params.interest_rate``
        (and its CHECKs), making ``rate_history.interest_rate`` the loan
        rate's sole storage-tier domain guard.
        """
        with app.app_context():
            account = _insert_account(
                seed_user, "Mortgage R3", "Mortgage",
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO budget.rate_history "
                        "(account_id, effective_date, interest_rate, "
                        " created_at) "
                        "VALUES (:aid, '2026-01-01', -0.01, now())"
                    ),
                    {"aid": account.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_RATE_HISTORY


class TestUserSettingsCheck:
    """``auth.user_settings`` rate CHECK constraints (F-077)."""

    def test_swr_above_one_rejected(self, app, seed_user):
        """A raw UPDATE of the safe withdrawal rate to 150% (1.5) is refused by its CHECK."""
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id).one()
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE auth.user_settings "
                        "SET safe_withdrawal_rate = 1.5 "
                        "WHERE id = :sid"
                    ),
                    {"sid": settings.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_USER_SWR

    def test_estimated_tax_rate_above_one_rejected(self, app, seed_user):
        """A raw UPDATE of the estimated retirement tax rate to 200% (2.0) is refused."""
        with app.app_context():
            settings = (
                db.session.query(UserSettings)
                .filter_by(user_id=seed_user["user"].id).one()
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE auth.user_settings "
                        "SET estimated_retirement_tax_rate = 2.0 "
                        "WHERE id = :sid"
                    ),
                    {"sid": settings.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_USER_TAX_RATE


class TestPaycheckLineCheck:
    """``salary.paycheck_lines`` CHECK constraints (F-077)."""

    def _make_profile(self, seed_user):
        """Insert a single filer's salary profile, its pay list started at $3,846.15."""
        single_id = (
            db.session.query(FilingStatus)
            .filter_by(name="single").one().id
        )
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=single_id,
            name="Profile",
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("3846.15"))  # $100,000.00 a year / 26
        db.session.commit()
        return profile

    def _make_deduction_kwargs(self):
        """Return the kind and calc-method ids of a flat pre-tax deduction."""
        timing_id = (
            db.session.query(PaycheckLineKind)
            .filter_by(name="pre_tax_deduction").one().id
        )
        flat_id = (
            db.session.query(CalcMethod)
            .filter_by(name="flat").one().id
        )
        return {
            "paycheck_line_kind_id": timing_id,
            "calc_method_id": flat_id,
        }

    def test_inflation_rate_above_one_rejected(self, app, seed_user):
        """A raw UPDATE of a line's inflation rate to 150% (1.5) is refused by its CHECK."""
        with app.app_context():
            profile = self._make_profile(seed_user)
            kwargs = self._make_deduction_kwargs()
            ded = PaycheckLine(
                salary_profile_id=profile.id,
                name="401k",
                amount=Decimal("500.00"),
                **kwargs,
            )
            db.session.add(ded)
            db.session.commit()
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE salary.paycheck_lines "
                        "SET inflation_rate = 1.5 WHERE id = :did"
                    ),
                    {"did": ded.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_DEDUCTION_INFL_RATE

    def test_inflation_month_thirteen_rejected(self, app, seed_user):
        """A raw UPDATE of a line's inflation month to 13 is refused by its 1-12 CHECK."""
        with app.app_context():
            profile = self._make_profile(seed_user)
            kwargs = self._make_deduction_kwargs()
            ded = PaycheckLine(
                salary_profile_id=profile.id,
                name="HSA",
                amount=Decimal("100.00"),
                **kwargs,
            )
            db.session.add(ded)
            db.session.commit()
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "UPDATE salary.paycheck_lines "
                        "SET inflation_effective_month = 13 "
                        "WHERE id = :did"
                    ),
                    {"did": ded.id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_DEDUCTION_INFL_MONTH


class TestSalaryRaiseCheck:
    """``salary.salary_raises.effective_year`` CHECK (F-077)."""

    def _make_profile(self, seed_user):
        """Insert a single filer's salary profile, its pay list started at $3,846.15."""
        single_id = (
            db.session.query(FilingStatus)
            .filter_by(name="single").one().id
        )
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=single_id,
            name="P",
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("3846.15"))  # $100,000.00 a year / 26
        db.session.commit()
        return profile

    def test_effective_year_below_2000_rejected(self, app, seed_user):
        """A raw INSERT of a raise effective in 1999 is refused by its 2000-2100 year CHECK."""
        with app.app_context():
            profile = self._make_profile(seed_user)
            type_id = (
                db.session.query(RaiseType)
                .filter_by(name="merit").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.salary_raises "
                        "(salary_profile_id, raise_type_id, "
                        " effective_year, effective_month, "
                        " percentage, is_recurring, version_id, "
                        " created_at) "
                        "VALUES (:pid, :tid, 1999, 1, 0.0300, "
                        "        false, 1, now())"
                    ),
                    {"pid": profile.id, "tid": type_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_RAISE_YEAR

    def test_effective_year_null_rejected(self, app, seed_user):
        """Storage rejects a NULL effective_year (DH-#57 NOT NULL).

        The never-UI-reachable NULL-year recurring raise was retired:
        ``effective_year`` is NOT NULL at the storage tier, in lockstep
        with the create/update schema's ``required=True``.  A raw INSERT
        of NULL must raise the not-null IntegrityError.
        """
        with app.app_context():
            profile = self._make_profile(seed_user)
            type_id = (
                db.session.query(RaiseType)
                .filter_by(name="cola").one().id
            )
            with pytest.raises(IntegrityError):
                db.session.execute(
                    text(
                        "INSERT INTO salary.salary_raises "
                        "(salary_profile_id, raise_type_id, "
                        " effective_year, effective_month, "
                        " percentage, is_recurring, version_id, "
                        " created_at) "
                        "VALUES (:pid, :tid, NULL, 1, 0.0300, "
                        "        true, 1, now())"
                    ),
                    {"pid": profile.id, "tid": type_id},
                )
                db.session.flush()
            db.session.rollback()


class TestStateTaxConfigCheck:
    """``salary.state_tax_configs`` CHECK constraints (F-077)."""

    def test_negative_standard_deduction_rejected(self, app, seed_user):
        """Storage rejects -1.00 via ck_state_tax_configs_nonneg_standard_deduction."""
        with app.app_context():
            tax_type_id = (
                db.session.query(TaxType).filter_by(name="flat").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.state_tax_configs "
                        "(user_id, tax_type_id, filing_status_id, state_code, "
                        " tax_year, flat_rate, standard_deduction, created_at) "
                        "VALUES (:uid, :ttid, "
                        " (SELECT id FROM ref.filing_statuses WHERE name='single'), "
                        " 'CA', 2026, 0.05, -1.00, now())"
                    ),
                    {"uid": seed_user["user"].id, "ttid": tax_type_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_STATE_TAX_DEDUCTION

    def test_tax_year_above_2100_rejected(self, app, seed_user):
        """Storage rejects 2200 via ck_state_tax_configs_valid_tax_year."""
        with app.app_context():
            tax_type_id = (
                db.session.query(TaxType).filter_by(name="flat").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.state_tax_configs "
                        "(user_id, tax_type_id, filing_status_id, state_code, "
                        " tax_year, flat_rate, created_at) "
                        "VALUES (:uid, :ttid, "
                        " (SELECT id FROM ref.filing_statuses WHERE name='single'), "
                        " 'CA', 2200, 0.05, now())"
                    ),
                    {"uid": seed_user["user"].id, "ttid": tax_type_id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_STATE_TAX_YEAR


class TestFicaConfigCheck:
    """``salary.fica_configs.tax_year`` CHECK (F-077)."""

    def test_tax_year_below_2000_rejected(self, app, seed_user):
        """Storage rejects 1999 via ck_fica_configs_valid_tax_year."""
        with app.app_context():
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.fica_configs "
                        "(user_id, tax_year, ss_rate, ss_wage_base, "
                        " medicare_rate, medicare_surtax_rate, "
                        " medicare_surtax_threshold, created_at) "
                        "VALUES (:uid, 1999, 0.062, 176100, "
                        "        0.0145, 0.009, 200000, now())"
                    ),
                    {"uid": seed_user["user"].id},
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_FICA_TAX_YEAR


class TestTaxBracketSetCheck:
    """``salary.tax_bracket_sets.tax_year`` CHECK (F-077)."""

    def test_tax_year_above_2100_rejected(self, app, seed_user):
        """Storage rejects 2200 via ck_tax_bracket_sets_valid_tax_year."""
        with app.app_context():
            single_id = (
                db.session.query(FilingStatus)
                .filter_by(name="single").one().id
            )
            with pytest.raises(IntegrityError) as info:
                db.session.execute(
                    text(
                        "INSERT INTO salary.tax_bracket_sets "
                        "(user_id, filing_status_id, tax_year, "
                        " standard_deduction, child_credit_amount, "
                        " other_dependent_credit_amount, created_at) "
                        "VALUES (:uid, :fid, 2200, 15000.00, "
                        "        2000.00, 500.00, now())"
                    ),
                    {
                        "uid": seed_user["user"].id,
                        "fid": single_id,
                    },
                )
                db.session.flush()
            db.session.rollback()
            assert _constraint_name_from(info.value) == CK_BRACKET_TAX_YEAR
