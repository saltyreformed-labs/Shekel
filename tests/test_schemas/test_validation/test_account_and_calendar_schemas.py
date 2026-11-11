"""Validation schema tests: accounts, pay-period generation, pay history, categories, and the loan
setup's stated balance.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.services.pay_rhythm import FixedDays
from app.schemas.validation import (
    AccountCreateSchema,
    CategoryCreateSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    PayHistorySchema,
    PayPeriodGenerateSchema,
)


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
