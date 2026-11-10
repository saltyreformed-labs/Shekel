"""
Shekel Budget App -- Annual Tax Liability Service Tests

Hand-confirmed assertions for ``tax_liability_service.compute_annual_liability``:
the filing-time FEDERAL + NC-STATE annual liability the analytics Taxes tab
builds its refund estimate on (T-P1).  The law is the shipped one, the
default every test prices under (ruling salary:R-SAL80) -- the same 2025/2026
figures the signup seed copied to every user until plan step salary:X-at-1;
the profile is built inline (the established test_tax_config_service pattern).

Every expected figure is hand-computed in the test docstring, including how
ROUND_HALF_UP resolves the NC half-cent.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import tax_law as tax_law_module
from app.exceptions import UnsupportedStateError
from app.extensions import db as _db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services.tax_config_service import TaxLawBasis, load_tax_configs_for_year
from app.services.tax_liability_service import AnnualLiability, compute_annual_liability
from app.tax_law import TaxLaw
from tests._test_helpers import (
    EMPTY_TAX_LAW,
    made_up_state,
    made_up_year,
    start_test_pay_list,
)


def _liability(profile, tax_year, annual_wage_income, annual_pretax):
    """Resolve *profile*'s law for *tax_year* and price its liability on it.

    The order the Taxes tab's producer follows: it resolves the primary
    profile's law once and hands the set to the liability (plan step
    salary:X-at-5), so a state the law does not list is refused at the
    resolution, before any arithmetic.
    """
    return compute_annual_liability(
        profile, load_tax_configs_for_year(profile, tax_year),
        annual_wage_income, annual_pretax,
    )


def _make_profile(
    seed_user,
    *,
    name="Liability Test Profile",
    state_code="NC",
    filing_status_name="single",
    additional_income="0.00",
    additional_deductions="0.00",
    qualifying_children=0,
    other_dependents=0,
):
    """Build and flush an active single/NC SalaryProfile for the seeded user.

    Mirrors the ``test_tax_config_service`` profile helper, adding the W-4
    fields the liability service reads (Step 4(a) income and dependent
    counts) plus the Step 4(b) deductions the service must IGNORE.  ``name``
    is a parameter because the ``uq_salary_profiles_user_scenario_name``
    constraint forbids two same-named profiles for one user + scenario.
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
        additional_income=Decimal(additional_income),
        additional_deductions=Decimal(additional_deductions),
        qualifying_children=qualifying_children,
        other_dependents=other_dependents,
    )
    _db.session.add(profile)
    start_test_pay_list(profile, Decimal("4230.77"))  # $110,000.00 a year / 26
    _db.session.flush()
    return profile


def _committed_profile(seed_user, **profile_kwargs):
    """Build and commit a profile; it prices under the shipped law."""
    profile = _make_profile(seed_user, **profile_kwargs)
    _db.session.commit()
    return profile


class TestWorkedAnchor:
    """The developer's locked worked anchor (single, NC, 2026 seeds)."""

    def test_anchor_federal_and_state(self, app, db, seed_user):
        """Anchor: wages 110,000; pre-tax 12,000; 4(a) 1,200; 4(b) 3,000.

        Federal (2026 single, std ded 16,100):
          taxable   = 110000 + 1200 - 12000 - 16100 = 83,100.00
          brackets  = 12400*0.10 + (50400-12400)*0.12 + (83100-50400)*0.22
                    = 1240.00 + 4560.00 + 7194.00 = 12,994.00
          credits   = 0  ->  liability = 12,994.00
          (4(b) 3,000 is set on the profile but MUST NOT change this.)
        State (NC 2026, flat 3.99%, std ded 12,750):
          base      = 110000 + 1200 - 12000 = 99,200.00
          taxable   = 99200 - 12750 = 86,450.00
          raw       = 86450 * 0.0399 = 3,449.3550 exactly
          liability = 3,449.36 (ROUND_HALF_UP on the .0050 half-cent)
        """
        profile = _committed_profile(
            seed_user,
            additional_income="1200.00",
            additional_deductions="3000.00",
        )
        result = _liability(
            profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )

        assert isinstance(result, AnnualLiability)
        # The shipped law carries 2026, so every part is priced on 2026's rules.
        assert result.basis == TaxLawBasis(2026, 2026, 2026, 2026)
        assert result.annual_wage_income == Decimal("110000.00")
        assert result.annual_pretax == Decimal("12000.00")
        assert result.additional_income == Decimal("1200.00")

        # Federal layer (0 children -> no CTC, no ACTC; CTC amount is the
        # OBBBA-corrected 2,200, refundable cap 1,700).
        assert result.federal.taxable == Decimal("83100.00")
        assert result.federal.liability == Decimal("12994.00")
        assert result.federal.refundable_actc == Decimal("0.00")
        assert result.federal.standard_deduction == Decimal("16100.00")
        assert result.federal.child_credit_amount == Decimal("2200.00")
        assert result.federal.other_dependent_credit_amount == Decimal("500.00")
        assert result.federal.child_credit_refundable_cap == Decimal("1700.00")
        assert result.federal.qualifying_children == 0
        assert result.federal.other_dependents == 0

        # State layer (single, 0 children -> no NC child deduction; AGI base
        # 99,200 is above the single $70k top tier anyway).
        assert result.state.taxable_base == Decimal("99200.00")
        assert result.state.liability == Decimal("3449.36")
        assert result.state.flat_rate == Decimal("0.0399")
        assert result.state.standard_deduction == Decimal("12750.00")
        assert result.state.child_deduction_per_child == Decimal("0")
        assert result.state.child_deduction_total == Decimal("0")


class TestFourBExclusion:
    """W-4 Step 4(b) additional deductions never change filing liability."""

    def test_4b_does_not_change_liability(self, app, db, seed_user):
        """Two profiles differing ONLY in 4(b) yield identical liability.

        The service never reads ``additional_deductions``, so both resolve to
        the anchor federal liability 12,994.00.
        """
        no_4b = _make_profile(
            seed_user, name="No 4b", additional_income="1200.00",
        )
        with_4b = _make_profile(
            seed_user,
            name="With 4b",
            additional_income="1200.00",
            additional_deductions="3000.00",
        )
        db.session.commit()

        base = _liability(
            no_4b, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        alt = _liability(
            with_4b, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        assert base.federal.liability == alt.federal.liability == Decimal("12994.00")
        assert base.federal.taxable == alt.federal.taxable == Decimal("83100.00")


class TestConfigYearSelection:
    """load_tax_configs_for_year selects the requested year's brackets."""

    def test_2025_vs_2026_differ(self, app, db, seed_user):
        """Same inputs against 2025 vs 2026 seeds produce different results.

        2025 single (std ded 15,750, P.L. 119-21 by Rev. Proc. 2025-32 sec.
        3.01 -- the return's, not Pub 15-T (2025)'s 15,000 a paycheck
        withholds on; plan step salary:X-at-7, ruling plan#3213):
          taxable   = 110000 + 1200 - 12000 - 15750 = 83,450.00
          brackets  = 11925*0.10 + (48475-11925)*0.12 + (83450-48475)*0.22
                    = 1192.50 + 4386.00 + 7694.50 = 13,273.00
        (On the pre-P.L. 119-21 15,000 the figures were 84,200.00 and
        13,438.00: the 750 more deduction at 22% is the 165.00 between.)
        State NC 2025 (flat 4.25%, std ded 12,750):
          raw       = (99200 - 12750) * 0.0425 = 86450 * 0.0425 = 3,674.125
          liability = 3,674.13 (ROUND_HALF_UP on the .005 half-cent)
        2026 anchor liability is 12,994.00 / 3,449.36 (see TestWorkedAnchor).
        """
        profile = _committed_profile(seed_user, additional_income="1200.00")

        r2026 = _liability(
            profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        r2025 = _liability(
            profile, 2025,
            Decimal("110000.00"), Decimal("12000.00"),
        )

        assert r2025.basis == TaxLawBasis(2025, 2025, 2025, 2025)
        assert r2026.basis == TaxLawBasis(2026, 2026, 2026, 2026)
        assert r2025.federal.standard_deduction == Decimal("15750.00")
        assert r2025.federal.taxable == Decimal("83450.00")
        assert r2025.federal.liability == Decimal("13273.00")
        assert r2025.state.flat_rate == Decimal("0.0425")
        assert r2025.state.liability == Decimal("3674.13")

        # 2026 differs on both layers (std ded 16,100; rate 3.99%).
        assert r2026.federal.standard_deduction == Decimal("16100.00")
        assert r2026.federal.liability == Decimal("12994.00")
        assert r2026.state.liability == Decimal("3449.36")
        assert r2025.federal.liability != r2026.federal.liability


class TestTheBasisItWasPricedOn:
    """The liability carries the resolver's basis for the law it priced (plan step X-at-5)."""

    def test_a_substituted_year_is_priced_and_labelled_on_the_newest_years_rules(
        self, seed_user, tax_law,
    ):
        """2028 is past the made-up law's newest year, 2027: priced on 2027's NC rate.

        Made-up law, every federal and FICA figure zero: 2026 NC 4.00%, 2027
        NC 5.00%, each with ``made_up_state``'s $25,500.00 standard deduction.
        Single, wages 50,000.00, nothing pre-tax, no 4(a), no children:
          NC base      = 50,000.00
          taxable      = 50000 - 25500 = 24,500.00
          2027's tax   = 24500 * 0.0500 = 1,225.00  (2026's would be 980.00)
        The basis names 2027 for every part, the year whose figure was used.
        """
        tax_law(TaxLaw(years=(
            made_up_year(2026, states={"NC": made_up_state(Decimal("0.0400"))}),
            made_up_year(2027, states={"NC": made_up_state(Decimal("0.0500"))}),
        )))
        profile = _committed_profile(seed_user)
        configs = load_tax_configs_for_year(profile, 2028)

        result = compute_annual_liability(
            profile, configs, Decimal("50000.00"), Decimal("0.00"),
        )

        assert result.basis is configs["basis"]
        assert result.basis == TaxLawBasis(2028, 2027, 2027, 2027)
        assert result.state.flat_rate == Decimal("0.0500")
        assert result.state.liability == Decimal("1225.00")


class TestHeadOfHouseholdBoundary2026:
    """2026's head-of-household 24% / 32% boundary is $201,750 (finding SAL-577)."""

    def test_income_past_the_boundary_is_taxed_at_32_from_201750(
        self, app, db, seed_user,
    ):
        """Head of household, 2026, wages 260,000, nothing pre-tax, no 4(a).

        Rev. Proc. 2025-32 sec. 4.01 Table 2 (std ded 24,150, sec. 4.14):
          taxable  = 260000 - 24150 = 235,850.00
          brackets = 17700*0.10                 =  1,770.00
                   + (67450-17700)*0.12         =  5,970.00
                   + (105700-67450)*0.22        =  8,415.00
                   + (201750-105700)*0.24       = 23,052.00
                   + (235850-201750)*0.32       = 10,912.00
                   = 50,119.00  (the table: 39,207 + 32% of the 34,100 over 201,750)
        On the $201,775 the ladder carried until plan step salary:X-at-7 the
        24% rung ran 25 dollars longer: 23,058.00 + 10,904.00, so 50,117.00 --
        $2.00 short.
        """
        profile = _committed_profile(
            seed_user, filing_status_name="head_of_household",
        )
        result = _liability(
            profile, 2026, Decimal("260000.00"), Decimal("0.00"),
        )

        assert result.federal.taxable == Decimal("235850.00")
        assert result.federal.liability == Decimal("50119.00")


class TestDependentCredits:
    """Dependent counts flow off the profile into the nonrefundable credit."""

    def test_two_children_one_other_dependent(self, app, db, seed_user):
        """2 children + 1 other dependent = 4,900 credit off the anchor tax.

          credit    = 2*2200 + 1*500 = 4,900.00  (CTC $2,200 per OBBBA)
          liability = 12994.00 - 4900.00 = 8,094.00
        Credit fully absorbed (12,994 > 4,900) so no ACTC.  State: single
        AGI base 99,200 is above the single $70k top child-deduction tier,
        so the NC child deduction is 0 -> state liability unchanged (3,449.36).
        """
        profile = _committed_profile(
            seed_user,
            additional_income="1200.00",
            qualifying_children=2,
            other_dependents=1,
        )
        result = _liability(
            profile, 2026,
            Decimal("110000.00"), Decimal("12000.00"),
        )
        assert result.federal.qualifying_children == 2
        assert result.federal.other_dependents == 1
        assert result.federal.liability == Decimal("8094.00")
        assert result.federal.refundable_actc == Decimal("0.00")
        assert result.state.child_deduction_total == Decimal("0")
        assert result.state.liability == Decimal("3449.36")


class TestDeveloperLiveAnchorMFJ:
    """The developer's live-shape anchor: 2026 MFJ, 4 children (T-P5).

    Ties the whole extension together on his real numbers -- refundable ACTC,
    NC filing-status standard deduction, and NC per-child deduction.
    """

    def test_live_shape_full_integration(self, app, db, seed_user):
        """MFJ, 4 children, wages 94,619.62, pre-tax 13,943.93, no 4(a).

        Federal (2026 MFJ, std ded 32,200; CTC 2,200; refundable cap 1,700):
          taxable  = 94619.62 - 13943.93 - 32200 = 48,475.69
          brackets = 24800*0.10 + (48475.69-24800)*0.12
                   = 2480.00 + 2841.0828 = 5,321.08
          credits  = 4 * 2200 = 8,800.00  ->  liability max(0, 5321.08-8800)=0
          ACTC     = min(unused 3478.92, cap 6800, earned 13817.94) = 3,478.92
        NC state (flat 3.99%, MFJ std ded 25,500; child deduction tier):
          AGI base = 94619.62 - 13943.93 = 80,675.69
          per child = 1,500 (AGI in the MFJ 80k-100k tier)
          child ded = 4 * 1500 = 6,000.00
          taxable   = 80675.69 - 25500 - 6000 = 49,175.69
          tax       = 49175.69 * 0.0399 = 1,962.110031 -> 1,962.11
        """
        profile = _committed_profile(
            seed_user,
            filing_status_name="married_jointly",
            qualifying_children=4,
        )
        result = _liability(
            profile, 2026,
            Decimal("94619.62"), Decimal("13943.93"),
        )

        assert result.federal.taxable == Decimal("48475.69")
        assert result.federal.liability == Decimal("0.00")
        assert result.federal.refundable_actc == Decimal("3478.92")
        assert result.federal.child_credit_amount == Decimal("2200.00")

        assert result.state.standard_deduction == Decimal("25500.00")
        assert result.state.taxable_base == Decimal("80675.69")
        assert result.state.child_deduction_per_child == Decimal("1500.00")
        assert result.state.child_deduction_total == Decimal("6000.00")
        assert result.state.liability == Decimal("1962.11")


class TestNCFilingStatusStandardDeduction:
    """finding 2b: the NC standard deduction is now filing-status-specific."""

    def test_mfj_uses_25500_not_single_12750(self, app, db, seed_user):
        """An MFJ profile resolves the $25,500 NC standard deduction.

        MFJ, 0 children, wages 100,000, no pre-tax/4(a):
          AGI base = 100,000; MFJ std ded 25,500 (NOT the single 12,750)
          taxable  = 100000 - 25500 = 74,500.00
          tax      = 74500 * 0.0399 = 2,972.55
        """
        profile = _committed_profile(
            seed_user, filing_status_name="married_jointly",
        )
        result = _liability(
            profile, 2026,
            Decimal("100000.00"), Decimal("0.00"),
        )
        assert result.state.standard_deduction == Decimal("25500.00")
        assert result.state.liability == Decimal("2972.55")

    def test_single_still_uses_12750(self, app, db, seed_user):
        """A single profile keeps the $12,750 NC standard deduction (regression).

        Single, 0 children, wages 100,000:
          taxable = 100000 - 12750 = 87,250.00
          tax     = 87250 * 0.0399 = 3,481.275 -> 3,481.28 (ROUND_HALF_UP)
        """
        profile = _committed_profile(seed_user, filing_status_name="single")
        result = _liability(
            profile, 2026,
            Decimal("100000.00"), Decimal("0.00"),
        )
        assert result.state.standard_deduction == Decimal("12750.00")
        assert result.state.liability == Decimal("3481.28")


class TestNCChildDeductionTierBoundary:
    """The NC child-deduction tier edge belongs to the lower (generous) tier."""

    def test_single_agi_exactly_at_tier_edge(self, app, db, seed_user):
        """Single AGI exactly 40,000 -> 2,000/child ("Up to 40,000" inclusive).

        The single tiers put "Over 30,000 - Up to 40,000" at 2,000/child; an
        AGI of exactly 40,000 is the inclusive upper of that tier (NOT the
        1,500 "Over 40,000" tier).  Single, 1 child, wages 40,000, no pre-tax:
          AGI base  = 40,000; per child = 2,000 -> child ded = 2,000.00
          taxable   = 40000 - 12750 - 2000 = 25,250.00
          tax       = 25250 * 0.0399 = 1,007.475 -> 1,007.48 (ROUND_HALF_UP)
        """
        profile = _committed_profile(
            seed_user, filing_status_name="single", qualifying_children=1,
        )
        result = _liability(
            profile, 2026,
            Decimal("40000.00"), Decimal("0.00"),
        )
        assert result.state.child_deduction_per_child == Decimal("2000.00")
        assert result.state.child_deduction_total == Decimal("2000.00")
        assert result.state.liability == Decimal("1007.48")

    def test_single_agi_one_cent_over_edge_drops_tier(self, app, db, seed_user):
        """Single AGI 40,000.01 -> 1,500/child ("Over 40,000" tier).

        wages 40,000.01, 1 child:
          per child = 1,500 -> child ded = 1,500.00
          taxable   = 40000.01 - 12750 - 1500 = 25,750.01
          tax       = 25750.01 * 0.0399 = 1,027.4254... -> 1,027.43
        """
        profile = _committed_profile(
            seed_user, filing_status_name="single", qualifying_children=1,
        )
        result = _liability(
            profile, 2026,
            Decimal("40000.01"), Decimal("0.00"),
        )
        assert result.state.child_deduction_per_child == Decimal("1500.00")
        assert result.state.child_deduction_total == Decimal("1500.00")
        assert result.state.liability == Decimal("1027.43")

    def test_zero_children_no_child_deduction(self, app, db, seed_user):
        """A filer with 0 children gets no child deduction regardless of AGI."""
        profile = _committed_profile(
            seed_user, filing_status_name="single", qualifying_children=0,
        )
        result = _liability(
            profile, 2026,
            Decimal("40000.00"), Decimal("0.00"),
        )
        assert result.state.child_deduction_total == Decimal("0")
        # taxable = 40000 - 12750 = 27,250; tax = 27250 * 0.0399 = 1,087.275
        # -> 1,087.28 (ROUND_HALF_UP).
        assert result.state.liability == Decimal("1087.28")


class TestClampAndMissingConfigs:
    """The zero clamp, and the refusal of a state the law does not list (salary:X-at-3)."""

    def test_low_income_clamps_both_layers_to_zero(self, app, db, seed_user):
        """Income below both standard deductions -> zero federal and state.

        wages 10,000; pre-tax 0; 4(a) 0:
          federal taxable = 10000 - 16100 < 0 -> 0.00 -> liability 0.00
          state base      = 10000 -> (10000 - 12750) < 0 -> liability 0.00
        """
        profile = _committed_profile(seed_user)
        result = _liability(
            profile, 2026,
            Decimal("10000.00"), Decimal("0.00"),
        )
        assert result.federal.taxable == Decimal("0")
        assert result.federal.liability == Decimal("0")
        assert result.state.taxable_base == Decimal("10000.00")
        assert result.state.liability == Decimal("0.00")

    def test_a_state_the_law_does_not_list_is_refused(self, app, db, seed_user):
        """A PA profile is refused, not priced at $0.00 state liability.

        The shipped law does not list Pennsylvania.  Until plan step
        salary:X-at-3 this case pinned the defect finding SAL-575 names: the
        state resolved no rules and the liability priced $0.00 state tax
        beside a full federal figure.  Plan step salary:X-at-3: "the engine
        refuses a state the law lacks" (ruling R-SAL78), with an error naming
        the profile (ruling R-SAL130).
        """
        profile = _committed_profile(
            seed_user, state_code="PA", additional_income="1200.00",
        )
        with pytest.raises(UnsupportedStateError) as refused:
            _liability(
                profile, 2026,
                Decimal("110000.00"), Decimal("12000.00"),
            )
        assert refused.value.state_code == "PA"
        assert refused.value.profile_id == profile.id
        assert refused.value.supported == tax_law_module.LAW.supported_states

    def test_an_empty_law_refuses_the_profile_before_its_federal_rules(
        self, app, db, seed_user, tax_law,
    ):
        """A law with no year lists no state, so the profile is refused first.

        Until plan step salary:X-at-3 this raised InvalidFilingStatusError: no
        year held a bracket set.  The law's state check now runs before any
        year is resolved, and an empty law supports no state.
        """
        tax_law(EMPTY_TAX_LAW)
        profile = _make_profile(seed_user, additional_income="1200.00")
        db.session.commit()
        with pytest.raises(UnsupportedStateError, match=r"\(it lists no state\)"):
            _liability(
                profile, date.today().year,
                Decimal("110000.00"), Decimal("12000.00"),
            )
