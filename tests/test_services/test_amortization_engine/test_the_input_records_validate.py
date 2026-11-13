"""RateChangeRecord, PeriodTerms and ProjectionInputs refuse invalid data at construction, with an
actionable message, rather than producing a silently wrong schedule.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import InterestAccrualConventionEnum
from app.services.amortization_engine import PeriodTerms, ProjectionInputs, RateChangeRecord


class TestRateChangeRecordValidation:
    """Tests for RateChangeRecord dataclass validation.

    Validates that invalid inputs are caught at construction time with
    clear error messages rather than producing silent wrong results in the
    schedule loop.
    """

    def test_negative_rate_raises_value_error(self):
        """Negative interest rate must be rejected."""
        with pytest.raises(ValueError, match="interest_rate must be >= 0"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=Decimal("-0.01"),
            )

    def test_float_rate_raises_type_error(self):
        """Float rate is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="interest_rate must be a Decimal"):
            RateChangeRecord(
                effective_date=date(2025, 6, 1),
                interest_rate=0.07,
            )

    def test_string_date_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="effective_date must be a date"):
            RateChangeRecord(
                effective_date="2025-06-01",
                interest_rate=Decimal("0.07"),
            )

    def test_zero_rate_valid(self):
        """Zero rate is valid -- represents an interest-free period."""
        record = RateChangeRecord(
            effective_date=date(2025, 6, 1),
            interest_rate=Decimal("0.00"),
        )
        assert record.interest_rate == Decimal("0.00")

    def test_valid_construction(self):
        """Valid RateChangeRecord construction succeeds."""
        record = RateChangeRecord(
            effective_date=date(2025, 6, 1),
            interest_rate=Decimal("0.07"),
        )
        assert record.effective_date == date(2025, 6, 1)
        assert record.interest_rate == Decimal("0.07")


class TestPeriodTermsValidation:
    """Construction-time validation for PeriodTerms and the
    ProjectionInputs empty-terms guard.

    Mirrors TestRateChangeRecordValidation: invalid data must fail loudly
    at construction, not produce a silently wrong schedule.
    """

    def test_negative_rate_raises_value_error(self):
        """Negative annual rate must be rejected."""
        with pytest.raises(ValueError, match="annual_rate must be >= 0"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("-0.01"),
                monthly_pi=Decimal("1500.00"),
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_negative_pi_raises_value_error(self):
        """Negative level payment must be rejected."""
        with pytest.raises(ValueError, match="monthly_pi must be >= 0"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("0.06"),
                monthly_pi=Decimal("-1.00"),
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_float_pi_raises_type_error(self):
        """Float P&I is a precision bug and must be rejected."""
        with pytest.raises(TypeError, match="monthly_pi must be a Decimal"):
            PeriodTerms(
                start_date=date(2026, 1, 1),
                annual_rate=Decimal("0.06"),
                monthly_pi=1500.00,
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_string_date_raises_type_error(self):
        """String date must be rejected -- only date instances accepted."""
        with pytest.raises(TypeError, match="start_date must be a date"):
            PeriodTerms(
                start_date="2026-01-01",
                annual_rate=Decimal("0.06"),
                monthly_pi=Decimal("1500.00"),
                convention=InterestAccrualConventionEnum.MONTHLY_1_12,
            )

    def test_empty_terms_schedule_rejected(self):
        """ProjectionInputs refuses an empty terms feed at construction.

        A projection with no terms has no payment or rate to apply;
        the constructor fails loudly instead of producing an empty or
        wrong schedule downstream.
        """
        with pytest.raises(ValueError, match="terms_schedule"):
            ProjectionInputs(
                starting_balance=Decimal("1000.00"),
                starting_date=date(2026, 2, 1),
                remaining_months=12,
                payment_day=1,
                terms_schedule=[],
            )
