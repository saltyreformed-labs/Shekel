"""
Shekel Budget App -- ``CreditCardTermsSchema`` (plan step credit_card:CC-2)

The ONE schema behind the ONE terms door (developer ruling **R-CC24**).  What
is pinned:

* the two rate fields arrive as PERCENTS and load as FRACTIONS in the stored
  domain (E-28), quantized to the column's four places -- the tree's
  convention for every rate field, stated here so the rounding is a documented
  behaviour rather than a surprise;
* the effective date and the five NOT NULL terms are required -- the date
  because a submit is one version of the terms FOR a date (plan#2331, ruling
  **plan#2333**), ``cashback_rate`` so a rate the owner stated is never
  silently rewritten as zero by a submit that carries no value -- each named
  in the errors when missing, and an empty control on one of them reads as
  missing;
* the two nullable dollar figures read an empty control as ``None`` (their
  "not set" state) and refuse zero when given, matching the model's
  ``IS NULL OR > 0`` CHECKs;
* every bound the model's CHECKs state is refused at the schema tier too, so
  an out-of-domain figure, or a date outside the calendar window, is a
  designed 400 rather than an IntegrityError.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.schemas.validation import CreditCardTermsSchema

#: A whole valid form, as the browser posts it (every control, as strings).
_FORM = {
    "effective_date": "2026-03-01",
    "statement_close_day": "20",
    "payment_due_day": "15",
    "min_payment_percent": "2.5",
    "min_payment_floor": "25.00",
    "cashback_rate": "1.5",
    "auto_redeem_threshold": "25.00",
    "credit_limit": "5000.00",
}

_REQUIRED = (
    "effective_date", "statement_close_day", "payment_due_day",
    "min_payment_percent", "min_payment_floor", "cashback_rate",
)


def _load(**overrides):
    """Load :data:`_FORM` with *overrides* applied (a value of ``None`` drops the key)."""
    form = {**_FORM, **overrides}
    form = {k: v for k, v in form.items() if v is not None}
    return CreditCardTermsSchema().load(form)


def _errors(**overrides):
    """The errors dict a refused load carries."""
    with pytest.raises(ValidationError) as excinfo:
        _load(**overrides)
    return excinfo.value.normalized_messages()


class TestTheWholeRowLoads:
    """A full form loads as the row the model stores."""

    def test_the_valid_form_loads_every_column(self):
        """Percents become fractions; days become ints; dollars keep two places."""
        data = _load()
        assert data == {
            "effective_date": date(2026, 3, 1),
            "statement_close_day": 20,
            "payment_due_day": 15,
            "min_payment_percent": Decimal("0.0250"),
            "min_payment_floor": Decimal("25.00"),
            "cashback_rate": Decimal("0.0150"),
            "auto_redeem_threshold": Decimal("25.00"),
            "credit_limit": Decimal("5000.00"),
        }

    def test_a_percent_past_the_columns_places_is_quantized(self):
        """``1.875%`` loads as ``0.0188``: four places, the column's scale.

        The convention every rate field in this package follows (``places``
        mirrors the column), pinned so the rounding is a stated behaviour.
        A card stating a minimum to a thousandth of a percent is not a case
        the design names; if one arrives, this is the test that says the
        schema rounds rather than refuses.
        """
        data = _load(min_payment_percent="1.875")
        assert data["min_payment_percent"] == Decimal("0.0188")

    def test_the_bounds_of_each_domain_are_admitted(self):
        """0% and 100% for both rates, day 1 and 31, a zero floor, a one-cent limit."""
        data = _load(
            statement_close_day="1", payment_due_day="31",
            min_payment_percent="100", cashback_rate="0",
            min_payment_floor="0", auto_redeem_threshold="0.01",
            credit_limit="0.01",
        )
        assert data["statement_close_day"] == 1
        assert data["payment_due_day"] == 31
        assert data["min_payment_percent"] == Decimal("1")
        assert data["cashback_rate"] == Decimal("0")
        assert data["min_payment_floor"] == Decimal("0")
        assert data["auto_redeem_threshold"] == Decimal("0.01")
        assert data["credit_limit"] == Decimal("0.01")

    @pytest.mark.parametrize("stated, loaded", [
        ("2000-01-01", date(2000, 1, 1)),
        ("2100-12-31", date(2100, 12, 31)),
    ])
    def test_both_ends_of_the_date_window_are_admitted(self, stated, loaded):
        """The first and last day of the calendar window load as dates.

        ``ck_credit_card_params_effective_date_range`` admits both ends
        (``CALENDAR_DATE_MIN`` and ``CALENDAR_DATE_MAX``), so the schema does.
        """
        assert _load(effective_date=stated)["effective_date"] == loaded

    def test_the_csrf_token_is_excluded(self):
        """The form's ``csrf_token`` is dropped, not refused (``BaseSchema``)."""
        data = CreditCardTermsSchema().load({**_FORM, "csrf_token": "tok"})
        assert "csrf_token" not in data


class TestRequiredAndEmpty:
    """What an absent or empty control means, field by field."""

    @pytest.mark.parametrize("field", _REQUIRED)
    def test_an_absent_required_field_is_named(self, field):
        """Dropping *field* refuses the load naming it and nothing else."""
        errors = _errors(**{field: None})
        assert set(errors) == {field}
        assert errors[field] == ["Missing data for required field."]

    @pytest.mark.parametrize("field", _REQUIRED)
    def test_an_empty_required_control_reads_as_missing(self, field):
        """A browser posts ``""`` for an untouched box; it is the same refusal."""
        errors = _errors(**{field: ""})
        assert set(errors) == {field}
        assert errors[field] == ["Missing data for required field."]

    @pytest.mark.parametrize("field", ("auto_redeem_threshold", "credit_limit"))
    def test_an_empty_nullable_control_loads_as_none(self, field):
        """``""`` on a nullable dollar figure IS its "not set" state."""
        data = _load(**{field: ""})
        assert field in data
        assert data[field] is None

    @pytest.mark.parametrize("field", ("auto_redeem_threshold", "credit_limit"))
    def test_an_absent_nullable_field_loads_as_none(self, field):
        """A form that never carried the control still yields the null state."""
        data = _load(**{field: None})
        assert data[field] is None

    def test_a_typed_zero_cashback_is_a_value(self):
        """``"0"`` on the cashback box is no cash back: stored as zero (E-12)."""
        assert _load(cashback_rate="0")["cashback_rate"] == Decimal("0")


class TestTheBoundsAreRefused:
    """Every CHECK the model states is a schema refusal first."""

    @pytest.mark.parametrize("field, value", [
        ("statement_close_day", "0"),
        ("statement_close_day", "32"),
        ("payment_due_day", "0"),
        ("payment_due_day", "32"),
        ("min_payment_percent", "100.01"),
        ("min_payment_percent", "-0.01"),
        ("cashback_rate", "100.01"),
        ("cashback_rate", "-0.01"),
        ("min_payment_floor", "-0.01"),
        ("min_payment_floor", "10000000.01"),
        ("auto_redeem_threshold", "0"),
        ("auto_redeem_threshold", "-1"),
        ("auto_redeem_threshold", "10000000.01"),
        ("credit_limit", "0"),
        ("credit_limit", "-1"),
        ("credit_limit", "10000000.01"),
        ("effective_date", "1999-12-31"),
        ("effective_date", "2101-01-01"),
    ])
    def test_an_out_of_domain_value_is_refused_on_its_field(self, field, value):
        """*field* = *value* is refused, and only that field is named."""
        errors = _errors(**{field: value})
        assert set(errors) == {field}

    @pytest.mark.parametrize("field", ("min_payment_percent", "cashback_rate"))
    def test_a_non_number_percent_is_refused_as_such(self, field):
        """The percent divide leaves an unparsable value for the field to name.

        ``_normalize_percent_fields`` passes an ``InvalidOperation`` through
        so the field's own "Not a valid number." is what the owner reads,
        rather than a 500 from the divide.
        """
        errors = _errors(**{field: "two"})
        assert errors == {field: ["Not a valid number."]}

    @pytest.mark.parametrize("field", ("statement_close_day", "payment_due_day"))
    def test_a_fractional_day_is_refused(self, field):
        """A day of the month is an integer."""
        errors = _errors(**{field: "15.5"})
        assert set(errors) == {field}
