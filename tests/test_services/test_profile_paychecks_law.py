"""A profile's pricer is the one home of its tax law in a read pass (plan step X-at-5).

``ProfilePaychecks.law`` resolves each tax year once and remembers it beside
the per-payday memo, and ``over`` prices every paycheck on that same set, so
the Taxes tab's liability -- which takes the pricer -- carries the very law
the profile's paychecks were priced on (plan#3364, following ruling R-IZ).

Since plan step X-at-6 each paycheck's tax lines carry the basis of the law
they were priced on as their ``rules`` (ruling R-SAL75), so a salary page
names the year's rules off the paycheck itself (finding N-235).

Its own module rather than ``test_income_service.py``, which was past the
1,000-line ceiling then (ruling R-IR).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import WithholdingKindEnum
from app.services import paycheck_calculator
from app.services.income_service import ProfilePaychecks
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import TaxLawBasis
from app.services.tax_withholding_service import year_paydays
from app.tax_law import TaxLaw
from tests._test_helpers import (
    add_test_pay_stub,
    counting_calls,
    made_up_state,
    made_up_year,
    make_salary_profile,
)

#: A made-up law of two years, each listing North Carolina: 2027 is past its
#: newest year, so a 2027 paycheck is priced on 2026's rules (ruling R-SAL76).
_LAW_TO_2026 = TaxLaw(years=(
    made_up_year(2025, states={"NC": made_up_state(Decimal("0.0425"))}),
    made_up_year(2026, states={"NC": made_up_state(Decimal("0.0399"))}),
))

#: The one body every resolution of a year runs, whichever door reached it.
_RESOLVE = ("app.services.tax_config_service", "_configs_from_series")


def _pricer_and_paydays(db, seed_user):
    """Return an NC filer's pricer, $5,000.00 a paycheck, and its saved 2026 paydays.

    ``seed_periods`` saves ten paydays from 2026-01-02, the first of which
    the pay list starts on.
    """
    profile = make_salary_profile(seed_user, db.session, pay=Decimal("5000.00"))
    db.session.commit()
    calendar = calendar_for(seed_user["user"].id)
    return ProfilePaychecks(profile, calendar), year_paydays(calendar, 2026)


@pytest.mark.usefixtures("seed_periods")
class TestThePricersLaw:
    """``law`` answers one set per year, and ``over`` prices on it."""

    def test_a_year_is_resolved_once_and_answers_the_same_set(self, db, seed_user):
        """Two asks for 2026: one resolution, and the second ask is the first's object."""
        paychecks, _ = _pricer_and_paydays(db, seed_user)

        with counting_calls(_RESOLVE) as counts:
            first = paychecks.law(2026)
            second = paychecks.law(2026)

        assert second is first
        assert counts["_configs_from_series"] == 1
        assert first["basis"] == TaxLawBasis(2026, 2026, 2026, 2026)

    def test_a_paycheck_is_priced_on_the_years_set(self, db, seed_user, monkeypatch):
        """``over`` hands the engine ``law(2026)``'s own set and resolves nothing more.

        The engine is wrapped only to see the law it is handed; every
        paycheck is still priced by the real one.  No pay stub, so 2026 is
        the only year ``over`` asks for.
        """
        paychecks, paydays = _pricer_and_paydays(db, seed_user)
        law = paychecks.law(2026)
        handed = []
        real = paycheck_calculator.project_salary

        def seeing(basis, periods, tax_configs=None, *, configs_by_year=None):
            handed.append(configs_by_year)
            return real(basis, periods, tax_configs, configs_by_year=configs_by_year)

        monkeypatch.setattr(paycheck_calculator, "project_salary", seeing)

        with counting_calls(_RESOLVE) as counts:
            breakdowns = paychecks.over(paydays)

        assert len(breakdowns) == 10
        assert breakdowns[0].earnings.gross_biweekly == Decimal("5000.00")
        assert len(handed) == 1
        assert handed[0] == {2026: law}
        assert handed[0][2026] is law
        assert counts["_configs_from_series"] == 0

    def test_the_profile_it_prices_is_the_one_it_was_built_for(self, db, seed_user):
        """``profile`` is the profile handed in, so its W-4 inputs and its law are one object's."""
        profile = make_salary_profile(seed_user, db.session)
        db.session.commit()

        paychecks = ProfilePaychecks(profile, calendar_for(seed_user["user"].id))

        assert paychecks.profile is profile


def _first_2027_payday(paychecks_calendar):
    """The owner's first payday of 2027: past the ten saved, so a projected one."""
    return paychecks_calendar.projection_axis(date(2027, 1, 1), date(2027, 1, 31))[0]


@pytest.mark.usefixtures("seed_periods")
class TestAPaycheckCarriesItsRules:
    """Each paycheck's tax lines name which year's tax rules priced them (plan step X-at-6)."""

    def test_a_year_the_law_carries_is_priced_on_its_own_rules(
        self, db, seed_user, tax_law,
    ):
        """A 2026 paycheck under a law carrying 2026: every part on 2026's rules.

        The lines carry the pricer's own basis for the year, the one object
        ``law(2026)`` resolved, so nothing resolved the year twice.
        """
        tax_law(_LAW_TO_2026)
        paychecks, paydays = _pricer_and_paydays(db, seed_user)

        rules = paychecks.at(paydays[0]).taxes.rules

        assert rules == TaxLawBasis(2026, 2026, 2026, 2026)
        assert rules is paychecks.law(2026)["basis"]
        assert not rules.substituted

    def test_a_year_past_the_newest_names_the_rules_that_priced_it(
        self, db, seed_user, tax_law,
    ):
        """A 2027 paycheck under a law ending 2026: 2027 on 2026's rules, every part."""
        tax_law(_LAW_TO_2026)
        paychecks, _ = _pricer_and_paydays(db, seed_user)
        payday = _first_2027_payday(calendar_for(seed_user["user"].id))

        rules = paychecks.at(payday).taxes.rules

        assert payday.period_id is None
        assert rules == TaxLawBasis(2027, 2026, 2026, 2026)
        assert rules.substituted
        assert rules.rules_year == 2026

    def test_a_stub_priced_paycheck_names_its_own_years_rules_not_the_stubs(
        self, db, seed_user, tax_law,
    ):
        """A 2027 paycheck priced from a 2026 stub names 2027's basis, never 2026's own.

        The stub's side is priced on the stub's own year's law (ruling
        R-SAL77), which is 2026's, whole; the paycheck's taxes move by what
        the formulas price on the PAYCHECK's year's law, so the lines name
        that: 2027 on 2026's rules.  Naming the stub's would read
        ``TaxLawBasis(2026, 2026, 2026, 2026)`` and say nothing was
        substituted.
        """
        tax_law(_LAW_TO_2026)
        profile = make_salary_profile(seed_user, db.session, pay=Decimal("5000.00"))
        add_test_pay_stub(
            profile, date(2026, 1, 2), "5000.00",
            {member: "0.00" for member in WithholdingKindEnum},
        )
        db.session.commit()
        calendar = calendar_for(seed_user["user"].id)
        paychecks = ProfilePaychecks(profile, calendar)

        taxes = paychecks.at(_first_2027_payday(calendar)).taxes

        assert taxes.stub_payday == date(2026, 1, 2)
        assert taxes.rules == TaxLawBasis(2027, 2026, 2026, 2026)
        assert paychecks.law(2026)["basis"] == TaxLawBasis(2026, 2026, 2026, 2026)
