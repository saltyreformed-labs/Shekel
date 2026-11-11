"""A profile's pricer is the one home of its tax law in a read pass (plan step X-at-5).

``ProfilePaychecks.law`` resolves each tax year once and remembers it beside
the per-payday memo, and ``over`` prices every paycheck on that same set, so
the Taxes tab's liability -- which takes the pricer -- carries the very law
the profile's paychecks were priced on (plan#3364, following ruling R-IZ).

Its own module rather than ``test_income_service.py``, which is past the
1,000-line ceiling (ruling R-IR).
"""

from decimal import Decimal

import pytest

from app.services import paycheck_calculator
from app.services.income_service import ProfilePaychecks
from app.services.pay_calendar import calendar_for
from app.services.tax_config_service import TaxLawBasis
from app.services.tax_withholding_service import year_paydays
from tests._test_helpers import counting_calls, make_salary_profile

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
