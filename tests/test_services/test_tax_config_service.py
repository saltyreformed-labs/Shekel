"""
Shekel Budget App -- Tax Config Service Tests

Verifies which year of the tax law each kind resolves for a profile -- federal
rules, state rules and FICA, each on its own series -- and that the answer
reads no clock.  The law is :mod:`app.tax_law`; each test here installs the
made-up law it reasons about through the ``tax_law`` fixture (ruling
salary:R-SAL80), so no case moves when a real year is published.
"""

import dataclasses
from datetime import date
from decimal import Decimal

import pytest

from app.enums import FilingStatusEnum
from app.exceptions import UnsupportedStateError
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services.tax_config_service import (
    FederalTaxRules,
    StateTaxRules,
    TaxLawBasis,
    configs_by_year,
    profile_tax_series,
    resolve_tax_year,
)
from app.tax_law import ChildDeductionTier, TaxLaw, ladder
from tests._made_up_federal import made_up_credits, made_up_federal
from tests._tax_configs import tax_configs_for
from tests._test_helpers import (
    EMPTY_TAX_LAW,
    made_up_fica,
    made_up_state,
    made_up_year,
    start_test_pay_list,
)


class _FakePeriod:
    """Minimal stand-in exposing the ``start_date`` the resolver reads."""

    def __init__(self, start_date):
        """Hold the one date a caller derives a period's tax year from.

        Args:
            start_date: The period's payday.
        """
        self.start_date = start_date


def _make_profile(seed_user, *, state_code="NC", filing_status_name="single"):
    """Build and flush an active SalaryProfile for the seeded user."""
    filing_status = (
        db.session.query(FilingStatus).filter_by(name=filing_status_name).one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name="Test Profile",
        filing_status_id=filing_status.id,
        state_code=state_code,
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("3076.92"))  # $80,000.00 a year / 26
    db.session.flush()
    return profile


def _nc_year(tax_year, flat_rate):
    """Return a made-up tax year whose one state is North Carolina at *flat_rate*."""
    return made_up_year(tax_year, states={"NC": made_up_state(flat_rate)})


def _state_slice(year_law, state_code="NC", status=FilingStatusEnum.SINGLE):
    """Return the state rules *year_law* holds for *status*, as the resolver hands them back.

    The expected value a resolved ``state_config`` is compared against: the
    law's rules carry no ``tax_year`` field (the year is stated once, on the
    year), so "2026's rules were picked" is "the result EQUALS 2026's slice".
    """
    state = year_law.states[state_code]
    return StateTaxRules(
        state_code=state_code,
        tax_type=state.tax_type,
        flat_rate=state.flat_rate,
        standard_deduction=state.standard_deduction[status],
        child_deduction_tiers=state.child_deduction_tiers[status],
    )


def _federal_slice(year_law, status=FilingStatusEnum.SINGLE):
    """Return the federal rules *year_law* holds for *status*, as the resolver hands them back.

    The status's deduction and ladder with the year's credits beside them
    (ruling plan#3340): the expected value a resolved ``bracket_set`` is
    compared against, as :func:`_state_slice` is for the state.
    """
    rules = year_law.federal[status]
    per_child = year_law.federal_credits
    return FederalTaxRules(
        standard_deduction=rules.standard_deduction,
        brackets=rules.brackets,
        child_credit_amount=per_child.child_credit_amount,
        other_dependent_credit_amount=per_child.other_dependent_credit_amount,
        child_credit_refundable_cap=per_child.child_credit_refundable_cap,
        withholding_standard_deduction=rules.withholding_standard_deduction,
        withholding_child_credit_amount=per_child.withholding_child_credit_amount,
    )


class TestResolveTaxYear:
    """resolve_tax_year: the ONE substitution rule, and it reads no clock.

    A pure function over the configured-year set, so these cases use ABSOLUTE
    years and pass on every calendar day -- which is itself the property under
    test.  The rule it replaced consulted ``date.today().year`` and therefore
    could not be stated without one.
    """

    def test_an_exactly_configured_year_resolves_to_itself(self):
        """A year with its own configuration is never substituted."""
        assert resolve_tax_year(2026, (2025, 2026)) == 2026

    def test_a_future_year_resolves_to_the_latest_configured(self):
        """An unconfigured FUTURE year takes the newest published rules."""
        assert resolve_tax_year(2029, (2025, 2026)) == 2026

    def test_it_reaches_back_not_forward_when_both_are_possible(self):
        """With configuration on both sides, the year AT OR BEFORE wins.

        Tax rules take effect and persist, so 2027 is governed by 2026's
        published rules, not by 2030's -- even though 2030 is the nearer year
        in absolute distance.
        """
        assert resolve_tax_year(2027, (2026, 2030)) == 2026

    def test_a_year_predating_all_configuration_reaches_forward(self):
        """The weaker arm: a historical year with nothing at or before it.

        An approximation for a year the user never configured, and the only
        answer available other than none at all.
        """
        assert resolve_tax_year(2019, (2025, 2026)) == 2025

    def test_no_configuration_at_all_resolves_to_nothing(self):
        """An empty candidate set has nothing to substitute, so it says so."""
        assert resolve_tax_year(2026, ()) is None

    def test_the_current_year_being_unconfigured_does_not_strand_it(self):
        """The New Year cliff, stated as the rule that removes it.

        The retired rule substituted the CURRENT calendar year, so a request
        FOR that year found nothing to redirect to and resolved to no
        configuration -- which the paycheck engine reads as zero Social
        Security.  Measured on production data 2026-08-11: 40 of 51 live-priced
        salary rows moved and projected income rose $8,460.50 on 2027-01-01.
        Here 2027 is both the requested year and "today", and it still resolves.
        """
        assert resolve_tax_year(2027, (2025, 2026)) == 2026


class TestProfileTaxSeries:
    """profile_tax_series: three INDEPENDENT candidate sets, scoped as the loader is."""

    def test_each_kind_keeps_its_own_years(self, app, seed_user, tax_law):
        """The three series are separate; the state's years are not the others'.

        The law states federal rules and FICA for every year it carries, so
        those two share their years; a state is listed only for the years the
        law supports it.  A year with no state entry is a federal and FICA year
        only.
        """
        tax_law(TaxLaw(years=(_nc_year(2024, Decimal("0.0399")), made_up_year(2025))))
        with app.app_context():
            profile = _make_profile(seed_user)

            series = profile_tax_series(profile)

            assert sorted(series.state_configs) == [2024]
            assert sorted(series.fica_configs) == [2024, 2025]
            assert sorted(series.bracket_sets) == [2024, 2025]


    def test_the_state_is_sliced_to_the_profiles_filing_status(
        self, app, seed_user, tax_law,
    ):
        """A profile's state rules are its OWN status's deduction and tiers.

        The law states a state's rate once and its deductions per filing
        status; the resolver hands back one status's slice.  Every status here
        carries a different made-up deduction and tier table, so a slice taken
        from the wrong status shows as a different figure.
        """
        deductions = {
            status: Decimal(f"{10000 + 1000 * position}.00")
            for position, status in enumerate(FilingStatusEnum)
        }
        tiers = {
            status: (
                ChildDeductionTier(
                    Decimal("0.00"), None, Decimal(f"{100 * (position + 1)}.00"),
                ),
            )
            for position, status in enumerate(FilingStatusEnum)
        }
        state = dataclasses.replace(
            made_up_state(Decimal("0.0450")),
            standard_deduction=deductions,
            child_deduction_tiers=tiers,
        )
        tax_law(TaxLaw(years=(made_up_year(2026, states={"NC": state}),)))
        with app.app_context():
            profile = _make_profile(seed_user)
            for status in FilingStatusEnum:
                profile.filing_status_id = (
                    db.session.query(FilingStatus).filter_by(name=status.value).one().id
                )

                rules = profile_tax_series(profile).state_configs[2026]

                assert rules.flat_rate == Decimal("0.0450"), status
                assert rules.standard_deduction == deductions[status], status
                assert rules.child_deduction_tiers == tiers[status], status

    def test_the_federal_rules_are_each_status_with_the_years_credits(
        self, app, db, seed_user, tax_law,
    ):
        """A profile's federal rules are its OWN status's, with the year's ONE set of credits.

        The law states the standard deduction and ladder per filing status
        and the per-child credits once for every filer (ruling plan#3340).
        Every status here carries a different made-up deduction, so rules
        taken from the wrong status show as a different figure; the year's
        credits, stated once, reach every status alike.
        """
        federal = {
            status: dataclasses.replace(
                made_up_federal(),
                standard_deduction=Decimal(f"{14000 + 1000 * position}.00"),
            )
            for position, status in enumerate(FilingStatusEnum)
        }
        year = dataclasses.replace(
            made_up_year(2026, states={"NC": made_up_state(Decimal("0.0450"))}),
            federal=federal,
            federal_credits=made_up_credits(),
        )
        tax_law(TaxLaw(years=(year,)))
        with app.app_context():
            profile = _make_profile(seed_user)
            for status in FilingStatusEnum:
                profile.filing_status_id = (
                    db.session.query(FilingStatus).filter_by(name=status.value).one().id
                )

                rules = profile_tax_series(profile).bracket_sets[2026]

                assert rules.standard_deduction == federal[status].standard_deduction
                assert rules.brackets == federal[status].brackets, status
                assert rules.child_credit_amount == Decimal("2000.00"), status
                assert rules.other_dependent_credit_amount == Decimal("500.00"), status
                assert rules.child_credit_refundable_cap == Decimal("0.00"), status
                assert rules == _federal_slice(year, status), status


class TestOneKindsYearsDoNotDecideAnotherKinds:
    """A year one kind lacks must not zero that kind, nor move the others.

    The defect an adversarial review caught in this change's first draft, which
    resolved ONE year for the profile from the UNION of the three tables.  A
    year present in only one table then became the resolved year for itself AND
    every later year, and the two missing kinds silently returned ``None`` --
    the same zero-withholding failure the change exists to remove, widened from
    one year to the whole horizon.  Measured on a clone of production
    2026-08-11 under the union rule, when the law was stored per user and the
    Settings page could write a single kind for any year: saving one 2027
    state-tax row dropped a 2028 paycheck's Social Security to ``$0.00`` and
    raised its net by **$216.63**.

    Since plan step salary:X-at-1 the law states federal rules and FICA for
    every year it carries, so only the STATE can be missing from a year the
    others have -- the case below.
    """

    def test_a_year_without_the_state_strands_nothing(
        self, app, seed_user, tax_law,
    ):
        """2027 lists no state; federal and FICA take 2027, the state stays on 2026."""
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        # 2027's federal rules and credits differ from 2026's zero ones, so a
        # bracket set resolved on 2026 shows as unequal to 2027's slice.
        year_2027 = made_up_year(
            2027,
            federal=made_up_federal(),
            federal_credits=made_up_credits(),
            fica=dataclasses.replace(made_up_fica(), ss_wage_base=Decimal("190000.00")),
        )
        tax_law(TaxLaw(years=(year_2026, year_2027)))
        assert _federal_slice(year_2027) != _federal_slice(year_2026)
        with app.app_context():
            profile = _make_profile(seed_user)

            for requested in (2027, 2028):
                result = tax_configs_for(profile, requested)
                assert result["bracket_set"] == _federal_slice(year_2027), requested
                assert result["fica_config"] is year_2027.fica, requested
                assert result["fica_config"].ss_wage_base == Decimal("190000.00")
                # The state resolves on its OWN series, so it takes the 2026
                # entry rather than going missing with 2027.
                assert result["state_config"] == _state_slice(year_2026), requested


class TestOneYearsLaw:
    """One year's law for a profile: slice the law, then pick the year that applies (DH-#30)."""

    def test_returns_target_year_configs_when_present(self, app, seed_user, tax_law):
        """A year the law HAS returns its own rules, with no substitution."""
        year_2026 = _nc_year(2026, Decimal("0.0500"))
        tax_law(TaxLaw(years=(_nc_year(2025, Decimal("0.0399")), year_2026)))
        with app.app_context():
            profile = _make_profile(seed_user)

            result = tax_configs_for(profile, 2026)

            assert result["state_config"].flat_rate == Decimal("0.0500")
            assert result["state_config"] == _state_slice(year_2026)

    def test_an_unconfigured_year_loads_the_latest_configured_year(
        self, app, seed_user, tax_law,
    ):
        """A year the law lacks loads the newest year's rules."""
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        tax_law(TaxLaw(years=(year_2026,)))
        with app.app_context():
            profile = _make_profile(seed_user)

            result = tax_configs_for(profile, 2031)

            assert result["state_config"].flat_rate == Decimal("0.0399")
            assert result["state_config"] == _state_slice(year_2026)

    def test_an_empty_law_refuses_every_profile(self, app, seed_user, tax_law):
        """A law with no year lists no state, so it refuses the profile.

        Until plan step salary:X-at-3 this was the ONE way all three kinds
        came back None -- never merely because the REQUESTED year is missing,
        which is what the retired current-year fallback produced every New
        Year.  The engine now refuses a state the law does not list (ruling
        R-SAL78), and an empty law lists none, so it answers no profile at
        all rather than three Nones.
        """
        tax_law(EMPTY_TAX_LAW)
        with app.app_context():
            profile = _make_profile(seed_user)

            with pytest.raises(UnsupportedStateError) as refused:
                tax_configs_for(profile, date.today().year)

            assert refused.value.supported == ()
            assert refused.value.state_code == "NC"

    def test_the_resolution_does_not_move_with_the_calendar(
        self, app, seed_user, tax_law,
    ):
        """The same request resolves the same way whatever year it is asked in.

        The property the whole change exists for: 2027, 2028 and 2029 all
        resolve to 2026's rules, and none of them consults today.  Under the
        retired rule, whichever of those years happened to BE the current year
        resolved to nothing at all.
        """
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        tax_law(TaxLaw(years=(year_2026,)))
        with app.app_context():
            profile = _make_profile(seed_user)

            for requested in (2027, 2028, 2029):
                result = tax_configs_for(profile, requested)
                assert result["state_config"] == _state_slice(year_2026), requested


class TestConfigsByYear:
    """configs_by_year: one resolved config set per distinct year (DH-#30).

    **The door was ``load_tax_configs_for_periods(user_id, profile, periods)``
    until plan step salary:S3-d**, which split the SERIES load from the year
    resolution so a caller that prices a payday on demand pays the three
    queries once rather than per payday.  These cases grade the same rule
    through the surviving half: each builds the series explicitly, where the
    deleted function built it for them.  (Since plan step salary:X-at-1 the
    series reads the law in the code and issues no query at all.)
    """

    def test_maps_each_distinct_period_year(self, app, seed_user, tax_law):
        """Returns {year: configs} for every distinct year present in periods."""
        current_year = date.today().year
        future_year = current_year + 1
        tax_law(TaxLaw(years=(
            _nc_year(current_year, Decimal("0.0399")),
            _nc_year(future_year, Decimal("0.0500")),
        )))
        with app.app_context():
            profile = _make_profile(seed_user)

            periods = [
                _FakePeriod(date(current_year, 6, 1)),
                _FakePeriod(date(current_year, 7, 1)),  # same year, deduped
                _FakePeriod(date(future_year, 1, 1)),
            ]
            result = configs_by_year(
                profile_tax_series(profile),
                {period.start_date.year for period in periods},
            )

            assert set(result.keys()) == {current_year, future_year}
            assert result[current_year]["state_config"].flat_rate == Decimal("0.0399")
            assert result[future_year]["state_config"].flat_rate == Decimal("0.0500")

    def test_an_unconfigured_period_year_resolves_in_its_own_slot(
        self, app, seed_user, tax_law,
    ):
        """A period year the law lacks keys its own slot to the resolved year.

        The slot stays keyed by the PERIOD's year -- the caller looks it up by
        ``period.start_date.year`` -- while the rules inside it are the
        latest year's.
        """
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        tax_law(TaxLaw(years=(year_2026,)))
        with app.app_context():
            profile = _make_profile(seed_user)

            periods = [
                _FakePeriod(date(2026, 6, 1)),
                _FakePeriod(date(2030, 1, 1)),  # the law has no 2030
            ]
            result = configs_by_year(
                profile_tax_series(profile),
                {period.start_date.year for period in periods},
            )

            assert set(result.keys()) == {2026, 2030}
            assert result[2030]["state_config"].flat_rate == Decimal("0.0399")
            assert result[2030]["state_config"] == _state_slice(year_2026)

    def test_empty_years_returns_empty_mapping(self, app, seed_user):
        """No years -> empty mapping, and no resolution attempted."""
        with app.app_context():
            profile = _make_profile(seed_user)
            assert configs_by_year(profile_tax_series(profile), set()) == {}

    def test_each_slot_carries_its_own_years_basis(self, app, seed_user, tax_law):
        """A slot's basis is the slot's own year's resolution, not a sibling's.

        2026 is in the law and 2030 is not, so the same call hands back one
        exact basis and one substituted one.
        """
        tax_law(TaxLaw(years=(_nc_year(2026, Decimal("0.0399")),)))
        with app.app_context():
            profile = _make_profile(seed_user)

            result = configs_by_year(profile_tax_series(profile), {2026, 2030})

            assert result[2026]["basis"] == TaxLawBasis(2026, 2026, 2026, 2026)
            assert result[2030]["basis"] == TaxLawBasis(2030, 2026, 2026, 2026)


class TestTheBasis:
    """The resolver hands back each part's own year (ruling R-SAL75, plan step X-at-5).

    The years were thrown away inside the resolver until X-at-5, so a figure
    priced on another year's rules showed as a plain dollar amount (finding
    N-235).  Each case asserts all four years, because each part resolves on
    its own series and a basis built from one part's year would pass a test
    that read only that part.
    """

    def test_a_year_the_law_carries_is_priced_on_itself(
        self, app, seed_user, tax_law,
    ):
        """Every part of 2026 is 2026's: nothing substituted, one rules year."""
        tax_law(TaxLaw(years=(
            _nc_year(2025, Decimal("0.0425")), _nc_year(2026, Decimal("0.0399")),
        )))
        with app.app_context():
            profile = _make_profile(seed_user)

            basis = tax_configs_for(profile, 2026)["basis"]

            assert basis == TaxLawBasis(
                tax_year=2026, federal_year=2026, state_year=2026, fica_year=2026,
            )
            assert basis.substituted is False
            assert basis.rules_year == 2026
            assert basis.has_some_own_rules is True

    def test_a_year_after_the_newest_is_priced_on_the_newest(
        self, app, seed_user, tax_law,
    ):
        """2028 is past the law's newest year, 2027: every part is 2027's (R-SAL76)."""
        tax_law(TaxLaw(years=(
            _nc_year(2026, Decimal("0.0399")), _nc_year(2027, Decimal("0.0349")),
        )))
        with app.app_context():
            profile = _make_profile(seed_user)

            basis = tax_configs_for(profile, 2028)["basis"]

            assert basis == TaxLawBasis(
                tax_year=2028, federal_year=2027, state_year=2027, fica_year=2027,
            )
            assert basis.substituted is True
            assert basis.rules_year == 2027
            assert basis.has_some_own_rules is False

    def test_a_year_before_the_first_is_priced_on_the_first(
        self, app, seed_user, tax_law,
    ):
        """A typed 2019 reaches forward to the law's first year, 2025, for every part."""
        tax_law(TaxLaw(years=(
            _nc_year(2025, Decimal("0.0425")), _nc_year(2026, Decimal("0.0399")),
        )))
        with app.app_context():
            profile = _make_profile(seed_user)

            basis = tax_configs_for(profile, 2019)["basis"]

            assert basis == TaxLawBasis(
                tax_year=2019, federal_year=2025, state_year=2025, fica_year=2025,
            )
            assert basis.substituted is True
            assert basis.rules_year == 2025
            assert basis.has_some_own_rules is False

    def test_a_year_lacking_the_state_names_the_states_own_year(
        self, app, seed_user, tax_law,
    ):
        """2027 ships without NC (R-SAL86): federal and FICA are 2027's, NC 2026's.

        The parts differ, so there is no one rules year, and the year is
        substituted although 2027's federal law is in.
        """
        tax_law(TaxLaw(years=(_nc_year(2026, Decimal("0.0399")), made_up_year(2027))))
        with app.app_context():
            profile = _make_profile(seed_user)

            basis = tax_configs_for(profile, 2027)["basis"]

            assert basis == TaxLawBasis(
                tax_year=2027, federal_year=2027, state_year=2026, fica_year=2027,
            )
            assert basis.substituted is True
            assert basis.rules_year is None
            assert basis.has_some_own_rules is True

    def test_a_year_past_a_newest_lacking_the_state_has_none_of_its_own(
        self, app, seed_user, tax_law,
    ):
        """2028 with 2027 shipped without NC: the parts differ, and none is 2028's.

        Federal and FICA resolve on 2027, NC on 2026, so there is no one
        rules year -- yet unlike 2027 itself no part is the year asked for.
        The Taxes tab's note says "all of" only when some part is
        (plan#3363), so this case is the one that tells the two apart.
        """
        tax_law(TaxLaw(years=(_nc_year(2026, Decimal("0.0399")), made_up_year(2027))))
        with app.app_context():
            profile = _make_profile(seed_user)

            basis = tax_configs_for(profile, 2028)["basis"]

            assert basis == TaxLawBasis(
                tax_year=2028, federal_year=2027, state_year=2026, fica_year=2027,
            )
            assert basis.substituted is True
            assert basis.rules_year is None
            assert basis.has_some_own_rules is False

    def test_the_rules_handed_back_are_the_basis_years_rules(
        self, app, seed_user, tax_law,
    ):
        """The basis names the years whose rules the same set carries.

        2028 lacks a state in its newest year, 2027, so the state is 2026's;
        the rules in the set are those years' own -- FICA the year's own
        object, the federal and state rules the years' slices for the
        profile's status -- so the label can never name a year other than
        the one that priced.  2027's federal rules and credits differ from
        2026's zero ones, so the federal slice tells the two years apart.
        """
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        year_2027 = made_up_year(
            2027, federal=made_up_federal(), federal_credits=made_up_credits(),
        )
        tax_law(TaxLaw(years=(year_2026, year_2027)))
        assert _federal_slice(year_2027) != _federal_slice(year_2026)
        with app.app_context():
            profile = _make_profile(seed_user)

            configs = tax_configs_for(profile, 2028)

            assert configs["basis"] == TaxLawBasis(2028, 2027, 2026, 2027)
            assert configs["bracket_set"] == _federal_slice(year_2027)
            assert configs["fica_config"] is year_2027.fica
            assert configs["state_config"] == _state_slice(year_2026)

class TestWithholdingRules:
    """``FederalTaxRules.withholding_rules``: what a paycheck withholds on (ruling plan#3213).

    The ONE producer of the withholding figures: the return's rules
    themselves where the year states none, else a copy with each stated
    figure in the return's place.  The made-up return figures are a
    15,000.00 standard deduction, a 2,000.00 child credit, 500.00 per other
    dependent and a 1,500.00 refundable cap.
    """

    @staticmethod
    def _rules(**overrides):
        """Return made-up rules for one filing status, with *overrides* applied."""
        fields = {
            "standard_deduction": Decimal("15000.00"),
            "brackets": ladder(("0.00", "10000.00", "0.1000"), ("10000.00", None, "0.1200")),
            "child_credit_amount": Decimal("2000.00"),
            "other_dependent_credit_amount": Decimal("500.00"),
            "child_credit_refundable_cap": Decimal("1500.00"),
        }
        fields.update(overrides)
        return FederalTaxRules(**fields)

    def test_rules_stating_none_withhold_on_themselves(self):
        """The default: withholding prices on the return's rules, the same object."""
        rules = self._rules()
        assert rules.withholding_standard_deduction is None
        assert rules.withholding_child_credit_amount is None
        assert rules.withholding_rules is rules

    def test_each_stated_figure_takes_the_returns_place(self):
        """Both stated: the withholding rules carry them and the rest of the return's."""
        rules = self._rules(
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
        deduction_only = self._rules(withholding_standard_deduction=Decimal("14000.00"))
        assert deduction_only.withholding_rules.standard_deduction == Decimal("14000.00")
        assert deduction_only.withholding_rules.child_credit_amount == Decimal("2000.00")

        credit_only = self._rules(withholding_child_credit_amount=Decimal("1800.00"))
        assert credit_only.withholding_rules.standard_deduction == Decimal("15000.00")
        assert credit_only.withholding_rules.child_credit_amount == Decimal("1800.00")
