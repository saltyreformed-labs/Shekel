"""Validation schema tests: salary profiles, raises, payroll lines, and the assumptions rail's
raise probes and control errors.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.enums import PaycheckLineKindEnum, RecurrenceUnitEnum
from tests._stub_period import stub_period_on
from tests._test_helpers import EMPTY_TAX_LAW, cadence_payload, made_up_law
from app.services.recurrence import UNSET
from app.schemas.validation import (
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_SUBMISSION_KEY,
    PaycheckLineCreateSchema,
    RaiseCreateSchema,
    RetirementReadinessQuerySchema,
    SalaryProfileCreateSchema,
    TemplateCreateSchema,
)
from app.schemas.validation.retirement import errors_by_rail_control


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
            **stub_period_on("2026-01-02"),
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
            **stub_period_on("2026-01-02"),
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
                **stub_period_on("2026-01-02"),
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
            "effective_date": "2026-03-01",
            "percentage": "3.50",
        })
        assert data["percentage"] == Decimal("3.50")

    def test_valid_with_flat_amount(self):
        """Valid raise with flat_amount loads successfully."""
        data = RaiseCreateSchema().load({
            "raise_type_id": "1",
            "effective_date": "2026-01-01",
            "flat_amount": "5000.00",
        })
        assert data["flat_amount"] == Decimal("5000.00")

    def test_both_percentage_and_flat_rejected(self):
        """Both percentage and flat_amount raises cross-field error."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_date": "2026-03-01",
                "percentage": "3",
                "flat_amount": "5000.00",
            })
        assert "_schema" in exc.value.messages

    def test_neither_percentage_nor_flat_rejected(self):
        """Neither percentage nor flat_amount raises cross-field error."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_date": "2026-03-01",
            })
        assert "_schema" in exc.value.messages

    def test_missing_start_date_rejected(self):
        """A raise without its start date is rejected (ruling plan#3090)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "percentage": "3",
            })
        assert "effective_date" in exc.value.messages

    def test_an_impossible_date_is_rejected(self):
        """February 30 is no day, so it is no start date."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load({
                "raise_type_id": "1",
                "effective_date": "2026-02-30",
                "percentage": "3",
            })
        assert "effective_date" in exc.value.messages


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
        assert blank[RECURRENCE_SUBMISSION_KEY].starts_on is UNSET
        assert blank[RECURRENCE_SUBMISSION_KEY].end_bound is UNSET

        stated = PaycheckLineCreateSchema().load({
            **base, "starts_on": "2026-09-01",
            "recurrence_end_mode": "on_date", "end_date": "2027-06-30",
        })
        assert stated[RECURRENCE_SUBMISSION_KEY].starts_on == date(2026, 9, 1)
        assert stated[RECURRENCE_SUBMISSION_KEY].end_bound == EndsOnDate(on=date(2027, 6, 30))
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


# ── TestSalaryProfileCreateSchemaBoundary ───────────────────────────


class TestSalaryProfileCreateSchemaBoundary:
    """Boundary tests for SalaryProfileCreateSchema state_code validation."""

    def _valid_salary_data(self, **overrides):
        """Return a valid salary profile payload with optional overrides."""
        data = {
            "name": "Test Profile",
            "pay_amount": "2884.62",  # $75,000.00 a year / 26
            "pay_payday": "2026-01-02",
            **stub_period_on("2026-01-02"),
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

    def test_lowercase_state_code_refused(self, tax_law):
        """Lower-case ``nc`` is refused: the tax law lists ``NC``, and no other spelling.

        Until plan step salary:X-at-3 this case pinned ``nc`` as ACCEPTED,
        stored as typed, leaving normalization to a later layer that never
        did it -- and the paycheck engine priced that profile's state tax at
        $0.00, since the law lists ``NC`` (finding SAL-575, measured at
        X-at-3).  Ruling R-SAL78: a save naming a state the law does not list
        is refused.  The form offers the law's own codes, so a browser never
        submits ``nc``.

        Under a made-up law listing NC and ZZ, so the refusal's list of states
        is pinned as words: "NC and ZZ", the one wording of a list of states
        (ruling plan#3202).  It read the shipped law's states joined by
        commas until then, which no second state had yet put to the test.
        """
        tax_law(made_up_law((2026, {"NC": "0.0399", "ZZ": "0.0500"})))
        with pytest.raises(ValidationError) as exc:
            SalaryProfileCreateSchema().load(
                self._valid_salary_data(state_code="nc")
            )
        assert exc.value.messages["state_code"] == [
            "Shekel has no tax rules for 'nc'; it supports NC and ZZ."
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
            **stub_period_on("2026-01-02"),
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
            "raise_type_id": "1", "effective_date": "2026-07-01", "percentage": "3.00",
        }
        data.update(overrides)
        return data

    def test_a_start_before_2000_is_rejected(self):
        """The last day of 1999 is outside the window (min 2000-01-01)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(effective_date="1999-12-31"))
        assert "effective_date" in exc.value.messages

    def test_a_start_after_2100_is_rejected(self):
        """The first day of 2101 is outside the window (max 2100-12-31)."""
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(effective_date="2101-01-01"))
        assert "effective_date" in exc.value.messages

    def test_both_ends_of_the_window_are_accepted(self):
        """2000-01-01 and 2100-12-31 are the window's own ends."""
        for day in ("2000-01-01", "2100-12-31"):
            data = RaiseCreateSchema().load(self._base(effective_date=day))
            assert data["effective_date"] == date.fromisoformat(day)

    def test_valid_start_accepted(self):
        """A mid-month start keeps its day (ruling plan#3090)."""
        data = RaiseCreateSchema().load(self._base(effective_date="2026-07-15"))
        assert data["effective_date"] == date(2026, 7, 15)

    def test_a_recurring_raise_may_not_start_on_february_29(self):
        """It repeats on its month and day, which three years in four lack.

        A rule of the Recur box and the date together, so it lands under the
        whole-form key the shared loader flashes (ruling plan#3137, beside
        ruling plan#3135; task plan#3125).
        """
        with pytest.raises(ValidationError) as exc:
            RaiseCreateSchema().load(self._base(
                effective_date="2028-02-29", is_recurring="on",
                raise_end_mode="none",
            ))
        assert exc.value.messages["_schema"] == [
            "A raise that repeats every year can't start on February 29, "
            "because most years don't have one. Start it on February 28 "
            "or March 1."
        ]

    def test_a_one_time_raise_may_start_on_february_29(self):
        """A raise that happens once needs its day only once."""
        data = RaiseCreateSchema().load(self._base(effective_date="2028-02-29"))
        assert data["effective_date"] == date(2028, 2, 29)


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
            "raise_type_id": "1", "effective_date": "2027-01-01", "percentage": "5",
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
