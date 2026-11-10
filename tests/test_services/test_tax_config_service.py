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
    StateTaxRules,
    configs_by_year,
    load_tax_configs_for_year,
    profile_tax_series,
    resolve_tax_year,
)
from app.tax_law import ChildDeductionTier, TaxLaw
from tests._test_helpers import (
    EMPTY_TAX_LAW,
    made_up_fica,
    made_up_state,
    made_up_year,
    start_test_pay_list,
)

# pylint: disable=redefined-outer-name


class _FakePeriod:
    """Minimal stand-in exposing the ``start_date`` the resolver reads."""

    def __init__(self, start_date):
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

    def test_each_kind_keeps_its_own_years(self, app, db, seed_user, tax_law):
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
        self, app, db, seed_user, tax_law,
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
        self, app, db, seed_user, tax_law,
    ):
        """2027 lists no state; federal and FICA take 2027, the state stays on 2026."""
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        year_2027 = made_up_year(
            2027,
            fica=dataclasses.replace(made_up_fica(), ss_wage_base=Decimal("190000.00")),
        )
        tax_law(TaxLaw(years=(year_2026, year_2027)))
        with app.app_context():
            profile = _make_profile(seed_user)

            for requested in (2027, 2028):
                result = load_tax_configs_for_year(profile, requested)
                assert result["bracket_set"] is year_2027.federal[
                    FilingStatusEnum.SINGLE
                ], requested
                assert result["fica_config"] is year_2027.fica, requested
                assert result["fica_config"].ss_wage_base == Decimal("190000.00")
                # The state resolves on its OWN series, so it takes the 2026
                # entry rather than going missing with 2027.
                assert result["state_config"] == _state_slice(year_2026), requested


class TestLoadTaxConfigsForYear:
    """load_tax_configs_for_year: resolve which year applies, then load it (DH-#30)."""

    def test_returns_target_year_configs_when_present(self, app, db, seed_user, tax_law):
        """A year the law HAS returns its own rules, with no substitution."""
        year_2026 = _nc_year(2026, Decimal("0.0500"))
        tax_law(TaxLaw(years=(_nc_year(2025, Decimal("0.0399")), year_2026)))
        with app.app_context():
            profile = _make_profile(seed_user)

            result = load_tax_configs_for_year(profile, 2026)

            assert result["state_config"].flat_rate == Decimal("0.0500")
            assert result["state_config"] == _state_slice(year_2026)

    def test_an_unconfigured_year_loads_the_latest_configured_year(
        self, app, db, seed_user, tax_law,
    ):
        """A year the law lacks loads the newest year's rules."""
        year_2026 = _nc_year(2026, Decimal("0.0399"))
        tax_law(TaxLaw(years=(year_2026,)))
        with app.app_context():
            profile = _make_profile(seed_user)

            result = load_tax_configs_for_year(profile, 2031)

            assert result["state_config"].flat_rate == Decimal("0.0399")
            assert result["state_config"] == _state_slice(year_2026)

    def test_an_empty_law_refuses_every_profile(self, app, db, seed_user, tax_law):
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
                load_tax_configs_for_year(profile, date.today().year)

            assert refused.value.supported == ()
            assert refused.value.state_code == "NC"

    def test_the_resolution_does_not_move_with_the_calendar(
        self, app, db, seed_user, tax_law,
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
                result = load_tax_configs_for_year(profile, requested)
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

    def test_maps_each_distinct_period_year(self, app, db, seed_user, tax_law):
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
        self, app, db, seed_user, tax_law,
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

    def test_empty_years_returns_empty_mapping(self, app, db, seed_user):
        """No years -> empty mapping, and no resolution attempted."""
        with app.app_context():
            profile = _make_profile(seed_user)
            assert configs_by_year(profile_tax_series(profile), set()) == {}
