"""
Shekel Budget App -- Tests for the tax law package (plan step salary:X-at-1).

The law is data the release carries (ruling salary:R-SAL74), so what these
grade is its SHAPE: a year that is malformed cannot load.  A transcription slip
in a new year -- a bracket ladder with a gap, a filing status left out, a rate
written as a percent, a year with no source -- must stop the application at
import rather than price a paycheck against it.  The figures themselves are
graded against their published sources when a year is added, not restated
here: a test pinning them would be a second copy of the law.

**Every check the per-user tables' constraints make has a refusal here**, one
per constraint, because the law's checks replaced them and must never be
narrower (review 1 of X-at-1 found four that were, finding M1).
"""

import dataclasses
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.enums import FilingStatusEnum, TaxTypeEnum
from app.tax_law import (
    LAW,
    Bracket,
    ChildDeductionTier,
    FederalRules,
    FicaRules,
    StateYearLaw,
    TaxLaw,
    TaxYearLaw,
    ladder,
)
from app.tax_law import _north_carolina

_RUNGS = (
    ("0.00", "10000.00", "0.1000"),
    ("10000.00", "40000.00", "0.1200"),
    ("40000.00", None, "0.2200"),
)


def _federal(**overrides):
    """Return one filing status's made-up federal rules, with *overrides* applied."""
    fields = {
        "standard_deduction": Decimal("15000.00"),
        "child_credit_amount": Decimal("2000.00"),
        "other_dependent_credit_amount": Decimal("500.00"),
        "child_credit_refundable_cap": Decimal("1500.00"),
        "brackets": ladder(*_RUNGS),
    }
    fields.update(overrides)
    return FederalRules(**fields)


def _fica(**overrides):
    """Return made-up FICA rules, with *overrides* applied."""
    fields = {
        "ss_rate": Decimal("0.0620"),
        "ss_wage_base": Decimal("150000.00"),
        "medicare_rate": Decimal("0.0145"),
        "medicare_surtax_rate": Decimal("0.0090"),
        "medicare_surtax_threshold": Decimal("200000.00"),
    }
    fields.update(overrides)
    return FicaRules(**fields)


def _tiers():
    """Return a made-up child deduction tier table: two tiers from zero, open top."""
    return (
        ChildDeductionTier(Decimal("0.00"), Decimal("20000.00"), Decimal("3000.00")),
        ChildDeductionTier(Decimal("20000.00"), None, Decimal("0.00")),
    )


def _state(**overrides):
    """Return a made-up flat-rate state year, with *overrides* applied."""
    fields = {
        "tax_type": TaxTypeEnum.FLAT,
        "flat_rate": Decimal("0.0400"),
        "standard_deduction": {status: Decimal("10000.00") for status in FilingStatusEnum},
        "child_deduction_tiers": {status: _tiers() for status in FilingStatusEnum},
    }
    fields.update(overrides)
    return StateYearLaw(**fields)


def _year(tax_year=2030, **overrides):
    """Return a made-up tax year, every filing status present, with *overrides* applied."""
    fields = {
        "tax_year": tax_year,
        "sources": ("A made-up source",),
        "federal": {status: _federal() for status in FilingStatusEnum},
        "fica": _fica(),
        "states": {"ZZ": _state()},
    }
    fields.update(overrides)
    return TaxYearLaw(**fields)


class TestTheControlsLoad:
    """Each made-up builder loads as written, so every refusal below is its override's."""

    def test_the_made_up_year_loads(self):
        """A complete year: every status, FICA, a state, a source."""
        year = _year()
        assert year.tax_year == 2030
        assert year.states["ZZ"].flat_rate == Decimal("0.0400")

    def test_a_state_without_a_child_deduction_or_a_rate_loads(self):
        """Empty tiers and no rate are how a state states it has neither.

        Its standard deduction is $0.00 since plan step salary:X-at-3 (it was
        the builder's $10,000.00): a state with no income tax deducts nothing,
        or the deduction would be a figure that prices nothing.
        """
        state = _state(
            tax_type=TaxTypeEnum.NONE,
            flat_rate=None,
            standard_deduction={status: Decimal("0.00") for status in FilingStatusEnum},
            child_deduction_tiers={status: () for status in FilingStatusEnum},
        )
        assert state.flat_rate is None
        assert state.child_deduction_tiers[FilingStatusEnum.SINGLE] == ()

    def test_the_bounds_of_every_range_load(self):
        """A rate of 0 or 1, a zero amount and years 2000 and 2100 are all allowed."""
        assert _fica(ss_rate=Decimal("1"), medicare_rate=Decimal("0")).ss_rate == 1
        assert _federal(child_credit_amount=Decimal("0.00")).child_credit_amount == 0
        assert _year(2000).tax_year == 2000
        assert _year(2100).tax_year == 2100


class TestALadderIsOneContiguousClimbFromZero:
    """FederalRules refuses brackets that are not one ladder; ``ladder`` numbers the rungs."""

    def test_each_rung_is_ordered_by_its_position(self):
        """sort_order is the rung's position, never a hand-written number."""
        rungs = ladder(*_RUNGS)
        assert [r.sort_order for r in rungs] == [0, 1, 2]
        assert rungs[1].min_income == Decimal("10000.00")
        assert rungs[2].max_income is None
        assert rungs[2].rate == Decimal("0.2200")

    def test_a_ladder_not_starting_at_zero_is_refused(self):
        """The bottom rung taxes income from the first dollar."""
        with pytest.raises(ValueError, match="starts at zero"):
            _federal(brackets=ladder(
                ("100.00", "10000.00", "0.1000"), ("10000.00", None, "0.1200"),
            ))

    def test_a_gap_between_rungs_is_refused(self):
        """Income between two rungs would be taxed by neither."""
        with pytest.raises(ValueError, match="not where rung 0 ends"):
            _federal(brackets=ladder(
                ("0.00", "10000.00", "0.1000"), ("10001.00", None, "0.1200"),
            ))

    def test_an_open_rung_below_the_top_is_refused(self):
        """Only the top rung may be open-ended."""
        with pytest.raises(ValueError, match="not where rung 0 ends"):
            _federal(brackets=ladder(
                ("0.00", None, "0.1000"), ("10000.00", None, "0.1200"),
            ))

    def test_a_closed_top_rung_is_refused(self):
        """Income above a closed top rung would be taxed by nothing."""
        with pytest.raises(ValueError, match="open-ended"):
            _federal(brackets=ladder(
                ("0.00", "10000.00", "0.1000"), ("10000.00", "40000.00", "0.1200"),
            ))

    def test_an_empty_ladder_is_refused(self):
        """A ladder with no rungs taxes nothing."""
        with pytest.raises(ValueError, match="starts at zero"):
            _federal(brackets=ladder())

    def test_rungs_out_of_position_are_refused(self):
        """The calculator orders rungs by sort_order, so it must agree with their order."""
        low, high = ladder(("0.00", "10000.00", "0.1000"), ("10000.00", None, "0.1200"))
        swapped = (
            dataclasses.replace(low, sort_order=1),
            dataclasses.replace(high, sort_order=0),
        )
        with pytest.raises(ValueError, match="ordered by their position"):
            _federal(brackets=swapped)

    def test_a_rung_that_is_not_a_bracket_is_refused(self):
        """A stand-in never ran Bracket's checks, so it cannot be in the law."""
        stand_in = SimpleNamespace(
            min_income=Decimal("0.00"), max_income=None,
            rate=Decimal("0.1000"), sort_order=0,
        )
        with pytest.raises(ValueError, match="tuple of Bracket"):
            _federal(brackets=(stand_in,))

    def test_brackets_as_a_list_are_refused(self):
        """A list could be changed after the checks ran; the law holds tuples."""
        with pytest.raises(ValueError, match="tuple of Bracket"):
            _federal(brackets=list(ladder(*_RUNGS)))


class TestARungChecksItself:
    """A Bracket refuses what ``salary.tax_brackets``' CHECKs refused, and an empty rung."""

    def test_a_rate_above_one_is_refused(self):
        """ck_tax_brackets_valid_rate: 12 means a typo for 0.12."""
        with pytest.raises(ValueError, match="not a fraction"):
            ladder(("0.00", "10000.00", "0.1000"), ("10000.00", None, "12"))

    def test_a_negative_rate_is_refused(self):
        """ck_tax_brackets_valid_rate's lower bound."""
        with pytest.raises(ValueError, match="not a fraction"):
            ladder(("0.00", None, "-0.1000"))

    def test_a_rate_finer_than_four_places_is_refused(self):
        """The column is Numeric(5, 4); the marginal-rate chip renders four places."""
        with pytest.raises(ValueError, match="more than 4 decimal places"):
            ladder(("0.00", None, "0.12345"))

    def test_a_trailing_zero_is_not_an_extra_place(self):
        """The places are the value's, not its spelling: 0.03990 is four places."""
        assert ladder(("0.00", None, "0.03990"))[0].rate == Decimal("0.0399")

    def test_a_rung_ending_below_its_start_is_refused(self):
        """ck_tax_brackets_income_order, on a ladder whose rungs still meet end to start."""
        with pytest.raises(ValueError, match="not above where it starts"):
            ladder(
                ("0.00", "10000.00", "0.1000"),
                ("10000.00", "5000.00", "0.1200"),
                ("5000.00", None, "0.2200"),
            )

    def test_an_empty_rung_is_refused(self):
        """Stricter than the table, which allowed max == min: a rung taxing no income."""
        with pytest.raises(ValueError, match="not above where it starts"):
            ladder(("0.00", "0.00", "0.1000"), ("0.00", None, "0.1200"))

    def test_a_negative_start_is_refused(self):
        """ck_tax_brackets_nonneg_min."""
        with pytest.raises(ValueError, match="must be at least zero"):
            Bracket(Decimal("-1.00"), None, Decimal("0.1000"), 0)

    def test_a_float_is_refused(self):
        """A float cannot carry an exact figure (the column was Numeric)."""
        with pytest.raises(ValueError, match="must be a Decimal figure"):
            Bracket(0.0, None, Decimal("0.1000"), 0)


class TestFederalAmounts:
    """The four amounts refuse what ``salary.tax_bracket_sets``' CHECKs refused."""

    @pytest.mark.parametrize("field", [
        "standard_deduction",
        "child_credit_amount",
        "other_dependent_credit_amount",
        "child_credit_refundable_cap",
    ])
    def test_a_negative_amount_is_refused(self, field):
        """ck_tax_bracket_sets_nonneg_*: each amount is at least zero."""
        with pytest.raises(ValueError, match=f"federal {field} -1.00 must be at least zero"):
            _federal(**{field: Decimal("-1.00")})

    @pytest.mark.parametrize("field", ["standard_deduction", "child_credit_amount"])
    def test_a_missing_amount_is_refused(self, field):
        """The columns were NOT NULL."""
        with pytest.raises(ValueError, match="must be a Decimal figure, not None"):
            _federal(**{field: None})

    def test_an_amount_finer_than_a_cent_is_refused(self):
        """The column is Numeric(12, 2)."""
        with pytest.raises(ValueError, match="more than 2 decimal places"):
            _federal(standard_deduction=Decimal("15000.005"))

    @pytest.mark.parametrize("value", ["10000000000.00", "1E+12"])
    def test_an_amount_too_large_for_its_column_is_refused(self, value):
        """Numeric(12, 2) holds ten digits before the point."""
        with pytest.raises(ValueError, match="more than 10 digits before the point"):
            _federal(standard_deduction=Decimal(value))

    def test_the_largest_amount_the_column_holds_loads(self):
        """The control for the bound above."""
        rules = _federal(standard_deduction=Decimal("9999999999.99"))
        assert rules.standard_deduction == Decimal("9999999999.99")

    def test_a_bracket_boundary_too_large_is_refused(self):
        """A rung's bound is an amount like any other."""
        with pytest.raises(ValueError, match="more than 10 digits before the point"):
            ladder(("0.00", "100000000000.00", "0.1000"), ("100000000000.00", None, "0.1200"))


class TestWithholdingFigures:
    """A year states a withholding figure only where it differs (ruling plan#3213).

    The made-up rules' return figures are a 15,000.00 standard deduction and a
    2,000.00 child credit (:func:`_federal`).
    """

    _RETURNS = {
        "withholding_standard_deduction": Decimal("15000.00"),
        "withholding_child_credit_amount": Decimal("2000.00"),
    }

    @pytest.mark.parametrize("field", sorted(_RETURNS))
    def test_a_figure_equal_to_the_returns_is_refused(self, field):
        """Stating the return's figure again would be a second spelling of it."""
        with pytest.raises(ValueError, match=f"federal {field} .* is the return's figure"):
            _federal(**{field: self._RETURNS[field]})

    @pytest.mark.parametrize("field", sorted(_RETURNS))
    def test_a_negative_figure_is_refused(self, field):
        """A withholding figure is an amount like the return's."""
        with pytest.raises(ValueError, match=f"federal {field} -1.00 must be at least zero"):
            _federal(**{field: Decimal("-1.00")})

    @pytest.mark.parametrize("field", sorted(_RETURNS))
    def test_a_figure_finer_than_a_cent_is_refused(self, field):
        """To the cent, as every amount in the law."""
        with pytest.raises(ValueError, match="more than 2 decimal places"):
            _federal(**{field: Decimal("1400.005")})

    @pytest.mark.parametrize("field", sorted(_RETURNS))
    def test_a_float_figure_is_refused(self, field):
        """A float cannot carry an exact figure."""
        with pytest.raises(ValueError, match="must be a Decimal figure"):
            _federal(**{field: 1400.0})

    def test_rules_stating_none_withhold_on_themselves(self):
        """The default: withholding prices on the return's rules, the same object."""
        rules = _federal()
        assert rules.withholding_standard_deduction is None
        assert rules.withholding_child_credit_amount is None
        assert rules.withholding_rules is rules

    def test_each_stated_figure_takes_the_returns_place(self):
        """Both stated: the withholding rules carry them and the rest of the return's."""
        rules = _federal(
            withholding_standard_deduction=Decimal("14000.00"),
            withholding_child_credit_amount=Decimal("1800.00"),
        )
        withheld = rules.withholding_rules
        assert withheld.standard_deduction == Decimal("14000.00")
        assert withheld.child_credit_amount == Decimal("1800.00")
        assert withheld.other_dependent_credit_amount == Decimal("500.00")
        assert withheld.child_credit_refundable_cap == Decimal("1500.00")
        assert withheld.brackets == rules.brackets
        # It states none itself: it already is what withholding prices on.
        assert withheld.withholding_standard_deduction is None
        assert withheld.withholding_child_credit_amount is None
        assert withheld.withholding_rules is withheld
        # The return's rules are unmoved.
        assert rules.standard_deduction == Decimal("15000.00")
        assert rules.child_credit_amount == Decimal("2000.00")

    def test_a_figure_not_stated_stays_the_returns(self):
        """One stated: the other withholding figure is the return's."""
        deduction_only = _federal(withholding_standard_deduction=Decimal("14000.00"))
        assert deduction_only.withholding_rules.standard_deduction == Decimal("14000.00")
        assert deduction_only.withholding_rules.child_credit_amount == Decimal("2000.00")

        credit_only = _federal(withholding_child_credit_amount=Decimal("1800.00"))
        assert credit_only.withholding_rules.standard_deduction == Decimal("15000.00")
        assert credit_only.withholding_rules.child_credit_amount == Decimal("1800.00")


class TestFicaRules:
    """FicaRules refuses what the deleted ``ck_fica_configs_*`` CHECKs refused."""

    @pytest.mark.parametrize("field", ["ss_rate", "medicare_rate", "medicare_surtax_rate"])
    def test_a_percent_written_for_a_rate_is_refused(self, field):
        """6.2 for 6.2% would take more than the whole paycheck."""
        with pytest.raises(ValueError, match="not a fraction"):
            _fica(**{field: Decimal("6.2")})

    @pytest.mark.parametrize("field", ["ss_rate", "medicare_rate", "medicare_surtax_rate"])
    def test_a_negative_rate_is_refused(self, field):
        """Each rate's lower bound."""
        with pytest.raises(ValueError, match="not a fraction"):
            _fica(**{field: Decimal("-0.0100")})

    @pytest.mark.parametrize("field", ["ss_wage_base", "medicare_surtax_threshold"])
    @pytest.mark.parametrize("value", ["0.00", "-1.00"])
    def test_a_base_or_threshold_not_above_zero_is_refused(self, field, value):
        """ck_fica_configs_positive_wage_base / _positive_surtax_threshold."""
        with pytest.raises(ValueError, match="must be above zero"):
            _fica(**{field: Decimal(value)})

    def test_a_missing_rate_is_refused(self):
        """The columns were NOT NULL; a None rate would price Social Security at zero."""
        with pytest.raises(ValueError, match="must be a Decimal figure, not None"):
            _fica(ss_rate=None)


class TestStateYearLaw:
    """A state year refuses what ``salary.state_tax_configs`` and its child table refused."""

    def test_a_percent_written_for_the_flat_rate_is_refused(self):
        """The historical slip: North Carolina's 3.99% entered as 3.99 taxes at 399%."""
        with pytest.raises(ValueError, match="state flat_rate 3.99 is not a fraction"):
            _north_carolina.state_year("3.99")

    def test_a_negative_flat_rate_is_refused(self):
        """ck_state_tax_configs_valid_rate's lower bound."""
        with pytest.raises(ValueError, match="not a fraction"):
            _state(flat_rate=Decimal("-0.0100"))

    def test_a_negative_standard_deduction_is_refused(self):
        """ck_state_tax_configs_nonneg_standard_deduction."""
        deduction = {status: Decimal("10000.00") for status in FilingStatusEnum}
        deduction[FilingStatusEnum.SINGLE] = Decimal("-1.00")
        with pytest.raises(ValueError, match="single state standard deduction -1.00"):
            _state(standard_deduction=deduction)

    def test_a_tax_type_that_is_not_the_enum_is_refused(self):
        """The calculator compares the type's ref id, which only a member has."""
        with pytest.raises(ValueError, match="tax_type is a TaxTypeEnum"):
            _state(tax_type="flat")

    def test_a_flat_state_with_no_rate_is_refused(self):
        """A flat-rate state states its rate, or it would price $0.00 (plan step salary:X-at-3)."""
        with pytest.raises(ValueError, match="a flat-rate state states its rate"):
            _state(flat_rate=None)

    def test_a_state_with_no_income_tax_stating_a_rate_is_refused(self):
        """A rate beside "no income tax" contradicts it: which one priced the state?"""
        with pytest.raises(
            ValueError, match="a state with no income tax states no rate, not 0.0400",
        ):
            _state(tax_type=TaxTypeEnum.NONE)

    def test_a_flat_state_at_zero_is_refused(self):
        """0% flat is a second spelling of "no income tax", which has one: tax type NONE."""
        with pytest.raises(
            ValueError,
            match=(
                "a flat-rate state's rate is above zero: a state with no income "
                "tax is written as one, tax type NONE"
            ),
        ):
            _state(flat_rate=Decimal("0.0000"))

    def test_a_state_with_no_income_tax_stating_a_deduction_is_refused(self):
        """An untaxed state deducting $10,000.00 shows a figure that prices nothing."""
        with pytest.raises(
            ValueError,
            match="a state with no income tax deducts nothing, not 10000.00 for single",
        ):
            _state(
                tax_type=TaxTypeEnum.NONE, flat_rate=None,
                child_deduction_tiers={status: () for status in FilingStatusEnum},
            )

    def test_a_state_with_no_income_tax_stating_child_tiers_is_refused(self):
        """An untaxed state's child deduction would deduct from a tax of $0.00."""
        with pytest.raises(
            ValueError,
            match="a state with no income tax has no child deduction, not tiers for single",
        ):
            _state(
                tax_type=TaxTypeEnum.NONE, flat_rate=None,
                standard_deduction={status: Decimal("0.00") for status in FilingStatusEnum},
            )

    @pytest.mark.parametrize("flat_rate", [None, Decimal("0.0400")])
    def test_a_bracket_state_is_refused(self, flat_rate):
        """No formula prices a state's ladder, so the law cannot list one, rate or none."""
        with pytest.raises(
            ValueError,
            match=(
                "a state taxed on a bracket ladder cannot be written: no formula "
                r"in the app prices one, so it would price \$0.00"
            ),
        ):
            _state(tax_type=TaxTypeEnum.BRACKET, flat_rate=flat_rate)

    def test_a_state_missing_a_filing_status_is_refused(self):
        """The state's deductions are per status, so every status is stated."""
        deduction = {status: Decimal("10000.00") for status in FilingStatusEnum}
        del deduction[FilingStatusEnum.MARRIED_SEPARATELY]
        with pytest.raises(ValueError, match="married_separately"):
            _state(standard_deduction=deduction)

    def test_a_tier_ending_at_or_below_its_start_is_refused(self):
        """ck_state_child_deductions_agi_order: agi_max above agi_min."""
        with pytest.raises(ValueError, match="not above where it starts"):
            ChildDeductionTier(Decimal("20000.00"), Decimal("20000.00"), Decimal("0.00"))

    def test_a_negative_tier_start_is_refused(self):
        """ck_state_child_deductions_nonneg_agi_min."""
        with pytest.raises(ValueError, match="agi_min -1.00 must be at least zero"):
            ChildDeductionTier(Decimal("-1.00"), None, Decimal("0.00"))

    def test_a_negative_deduction_per_child_is_refused(self):
        """ck_state_child_deductions_nonneg_deduction."""
        with pytest.raises(ValueError, match="deduction_per_child -1.00 must be at least zero"):
            ChildDeductionTier(Decimal("0.00"), None, Decimal("-1.00"))

    def test_tiers_with_a_gap_are_refused(self):
        """AGI between two tiers would find no deduction."""
        gapped = (
            ChildDeductionTier(Decimal("0.00"), Decimal("20000.00"), Decimal("3000.00")),
            ChildDeductionTier(Decimal("25000.00"), None, Decimal("0.00")),
        )
        tiers = {status: _tiers() for status in FilingStatusEnum}
        tiers[FilingStatusEnum.HEAD_OF_HOUSEHOLD] = gapped
        with pytest.raises(ValueError, match="head_of_household child deduction tiers: rung 1"):
            _state(child_deduction_tiers=tiers)

    def test_tiers_not_starting_at_zero_are_refused(self):
        """The lowest tier covers AGI from the first dollar."""
        tiers = {status: _tiers()[1:] for status in FilingStatusEnum}
        with pytest.raises(ValueError, match="child deduction tiers starts at zero"):
            _state(child_deduction_tiers=tiers)

    def test_tiers_as_a_list_are_refused(self):
        """The law holds tuples, so a checked table cannot change after the check."""
        tiers = {status: list(_tiers()) for status in FilingStatusEnum}
        with pytest.raises(ValueError, match="tuple of ChildDeductionTier"):
            _state(child_deduction_tiers=tiers)


class TestAYearIsCompleteOrDoesNotLoad:
    """A tax year covers every filing status, carries FICA, cites its sources and fits the range."""

    def test_a_year_missing_a_filing_status_is_refused(self):
        """A missing status would price that filer's federal tax at zero."""
        federal = {status: _federal() for status in FilingStatusEnum}
        del federal[FilingStatusEnum.HEAD_OF_HOUSEHOLD]
        with pytest.raises(ValueError, match="head_of_household"):
            _year(federal=federal)

    def test_a_year_without_fica_is_refused(self):
        """A None FICA would price Social Security and Medicare at $0.00 from that year on."""
        with pytest.raises(ValueError, match="FICA rules are FicaRules, not None"):
            _year(fica=None)

    def test_a_filing_status_without_rules_is_refused(self):
        """Every status is present AND holds FederalRules."""
        federal = {status: _federal() for status in FilingStatusEnum}
        federal[FilingStatusEnum.SINGLE] = None
        with pytest.raises(ValueError, match="single federal rules are FederalRules, not None"):
            _year(federal=federal)

    @pytest.mark.parametrize("state_code", ["NCX", "nc", "N1", None, 12])
    def test_a_state_code_that_is_not_two_capital_letters_is_refused(self, state_code):
        """A mis-keyed state would silently price its filers at $0.00 state tax."""
        with pytest.raises(ValueError, match="not a two-letter capital state code"):
            _year(states={state_code: _state()})

    def test_sources_as_a_list_are_refused(self):
        """A list could be changed after the checks ran; the law holds tuples."""
        with pytest.raises(ValueError, match="sources are a tuple"):
            _year(sources=["A made-up source"])

    def test_a_state_that_is_not_a_state_year_is_refused(self):
        """A state entry is a checked StateYearLaw or nothing."""
        with pytest.raises(ValueError, match="ZZ law is a StateYearLaw"):
            _year(states={"ZZ": None})

    @pytest.mark.parametrize("tax_year", [1999, 2101, "2030", True])
    def test_a_year_outside_the_range_is_refused(self, tax_year):
        """ck_*_valid_tax_year: an integer from 2000 to 2100."""
        with pytest.raises(ValueError, match="a tax year is a year from 2000 to 2100"):
            _year(tax_year)

    def test_a_year_citing_no_source_is_refused(self):
        """Nobody could check a figure with no source (ruling R-SAL74)."""
        with pytest.raises(ValueError, match="cites no source"):
            _year(sources=())

    def test_a_blank_source_is_refused(self):
        """A blank line names no document."""
        with pytest.raises(ValueError, match="cites no source"):
            _year(sources=("  ",))

    def test_years_out_of_order_are_refused(self):
        """The law lists each year once, oldest first."""
        with pytest.raises(ValueError, match="distinct and ascending"):
            TaxLaw(years=(_year(2031), _year(2030)))

    def test_a_repeated_year_is_refused(self):
        """Two copies of one year are two answers to one question."""
        with pytest.raises(ValueError, match="distinct and ascending"):
            TaxLaw(years=(_year(2030), _year(2030)))

    def test_a_skipped_year_is_refused(self):
        """No year between two the law carries may be missing (ruling salary:R-SAL86).

        A gap would be priced on the year before it with no alarm, because the
        alarms ask only whether the law reaches the due year.
        """
        with pytest.raises(ValueError, match=r"skips a year: \[2030, 2032\]"):
            TaxLaw(years=(_year(2030), _year(2032)))

    def test_consecutive_years_and_no_year_at_all_load(self):
        """The refusal is of a GAP: back-to-back years load, and so does an empty law."""
        law = TaxLaw(years=(_year(2030), _year(2031), _year(2032)))
        assert [year.tax_year for year in law.years] == [2030, 2031, 2032]
        assert not TaxLaw(years=()).years

    def test_years_as_a_list_are_refused(self):
        """The law's years are a tuple, like every other sequence in it."""
        with pytest.raises(ValueError, match="years are a tuple"):
            TaxLaw(years=[_year(2030)])

    def test_a_law_year_that_is_not_a_tax_year_is_refused(self):
        """Every year of the law ran TaxYearLaw's checks."""
        with pytest.raises(ValueError, match="a tax law year is a TaxYearLaw"):
            TaxLaw(years=(None,))


class TestTheSupportedStates:
    """The states the law lists are the states the app supports (plan step salary:X-at-3).

    Ruling salary:R-SAL78: the profile form offers them, both doors refuse
    any other, and the engine refuses a profile in any other.  Ruling
    salary:R-SAL129: a state is listed from the law's first year.
    """

    def test_they_are_every_state_the_law_lists_alphabetically(self):
        """Listed in dict order YY, AA; supported as AA, YY."""
        law = TaxLaw(years=(
            _year(2030, states={"YY": _state(), "AA": _state()}),
            _year(2031, states={"YY": _state(), "AA": _state()}),
        ))
        assert law.supported_states == ("AA", "YY")

    def test_a_new_year_shipping_before_its_state_still_supports_it(self):
        """Ruling R-SAL86: 2031 ships without AA, and AA stays supported (its alarm names it)."""
        law = TaxLaw(years=(
            _year(2030, states={"AA": _state(), "YY": _state()}),
            _year(2031, states={"YY": _state()}),
        ))
        assert law.supported_states == ("AA", "YY")

    def test_a_law_with_no_year_supports_no_state(self):
        """No year lists anything, so the engine refuses every profile under it."""
        assert TaxLaw(years=()).supported_states == ()

    def test_late_states_are_refused_and_named_alphabetically(self):
        """Two states first listed in 2031 of a law opening in 2030: both named, in order."""
        with pytest.raises(
            ValueError,
            match=(
                "the tax law lists AA, BB from 2031 but not in its first year, "
                "2030: a state is listed from the law's first year"
            ),
        ):
            TaxLaw(years=(
                _year(2030),
                _year(2031, states={"ZZ": _state(), "BB": _state(), "AA": _state()}),
            ))


class TestTheLawCannotChangeOnceItLoads:
    """Every mapping in the law is a read-only view of a private copy.

    A frozen dataclass stops a field being reassigned, not a dict in it being
    changed -- which would get past every check above after the year loaded.
    """

    def test_a_years_federal_rules_cannot_be_replaced(self):
        """Assigning into the loaded year's federal mapping raises."""
        year = _year()
        with pytest.raises(TypeError):
            year.federal[FilingStatusEnum.SINGLE] = None

    def test_a_states_deduction_cannot_be_replaced(self):
        """Assigning into a loaded state's deduction mapping raises."""
        state = _state()
        with pytest.raises(TypeError):
            state.standard_deduction[FilingStatusEnum.SINGLE] = Decimal("-1.00")

    def test_the_callers_dict_does_not_reach_the_loaded_law(self):
        """The law holds a copy: changing the dict it was built from changes nothing."""
        federal = {status: _federal() for status in FilingStatusEnum}
        year = _year(federal=federal)
        federal[FilingStatusEnum.SINGLE] = None
        assert isinstance(year.federal[FilingStatusEnum.SINGLE], FederalRules)

    def test_the_shipped_law_is_read_only(self):
        """The law the app ships is frozen the same way."""
        with pytest.raises(TypeError):
            LAW.years[-1].states["NC"] = None


class TestTheShippedLaw:
    """What the app carries holds the shape the resolver relies on."""

    def test_every_year_carries_every_filing_status_fica_and_a_source(self):
        """The resolver slices federal rules by status and FICA by year alone."""
        assert LAW.years
        for year in LAW.years:
            assert set(year.federal) == set(FilingStatusEnum)
            assert isinstance(year.fica, FicaRules)
            assert year.sources

    def test_a_second_state_waits_on_the_two_state_banner_wording(self):
        """The shipped law lists ONE state until the developer rules the two-state wording.

        Ruling salary:R-SAL91 worded the tax-law banner for ONE state priced on
        an older year; its two-state form ("... and NC tax uses 2026's and SC
        tax uses 2026's, ...", and the check script's "..., except NC on
        2026's and SC on 2026's.") is extrapolated, pinned only by made-up
        laws, and unruled.  Plan step salary:X-at-3 owed the developer that
        question before a second state lands, and its ruling R-SAL128 lists
        North Carolina alone, so the question is held HERE (ledger row
        salary:SAL-599; the developer, 2026-10-08, "Failing test + ledger
        row"): the release adding a second state fails this until it is asked.
        It counts states and names none, so it is no second copy of the law.
        """
        assert len(LAW.supported_states) == 1, (
            f"the shipped tax law now lists {', '.join(LAW.supported_states)}: "
            "before a second state ships, put R-SAL91's two-state banner "
            "wording to the developer (ledger row salary:SAL-599), then "
            "retire this test"
        )

    def test_every_state_year_states_every_filing_status(self):
        """A state's standard deduction and child tiers are read per status."""
        for year in LAW.years:
            for state in year.states.values():
                assert set(state.standard_deduction) == set(FilingStatusEnum)
                assert set(state.child_deduction_tiers) == set(FilingStatusEnum)
