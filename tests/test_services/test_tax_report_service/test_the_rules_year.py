"""Which year's tax rules priced the report: the basis its liability carries (plan step X-at-5).

Ruling R-SAL75: whatever prices a figure hands back which year's rules it
used, and the tax report carries it to the Taxes tab's one line.  Each case
installs a made-up law (ruling R-SAL80) so no case moves when a real year is
published, and asks for a year with no pay periods, so every figure but the
liability's rules is zero and the rules are what is under test.
"""

from datetime import date
from decimal import Decimal

from app.services.tax_config_service import TaxLawBasis
from app.services.tax_report_service import compute_tax_report
from app.tax_law import TaxLaw
from tests._test_helpers import made_up_state, made_up_year

from ._helpers import _committed_profile

#: A made-up law of two years, each with its own NC rate, so the rate the
#: liability carries says which year's rules priced it.
_TWO_YEARS = TaxLaw(years=(
    made_up_year(2025, states={"NC": made_up_state(Decimal("0.0425"))}),
    made_up_year(2026, states={"NC": made_up_state(Decimal("0.0399"))}),
))


def _report(db, seed_user, tax_year):
    """Return the report for *tax_year* of one NC single filer under the made-up law."""
    _committed_profile(seed_user)
    db.session.commit()
    return compute_tax_report(seed_user["user"].id, tax_year, date(2026, 3, 1))


class TestTheRulesYear:
    """The report's liability carries the resolver's basis for the year asked."""

    def test_a_year_the_law_carries_names_itself(self, db, seed_user, tax_law):
        """2026 is in the law: every part is 2026's, and the NC rate is 2026's 3.99%."""
        tax_law(_TWO_YEARS)

        report = _report(db, seed_user, 2026)

        assert report.liability.basis == TaxLawBasis(2026, 2026, 2026, 2026)
        assert report.liability.basis.substituted is False
        assert report.liability.state.flat_rate == Decimal("0.0399")

    def test_a_year_after_the_newest_names_the_newest(self, db, seed_user, tax_law):
        """2030 is past the newest year, 2026: every part is 2026's (R-SAL76)."""
        tax_law(_TWO_YEARS)

        report = _report(db, seed_user, 2030)

        assert report.liability.basis == TaxLawBasis(2030, 2026, 2026, 2026)
        assert report.liability.basis.rules_year == 2026
        assert report.liability.state.flat_rate == Decimal("0.0399")

    def test_a_year_before_the_first_names_the_first(self, db, seed_user, tax_law):
        """A typed 2019 is before the law's first year, 2025: every part is 2025's 4.25%."""
        tax_law(_TWO_YEARS)

        report = _report(db, seed_user, 2019)

        assert report.liability.basis == TaxLawBasis(2019, 2025, 2025, 2025)
        assert report.liability.basis.rules_year == 2025
        assert report.liability.state.flat_rate == Decimal("0.0425")

    def test_a_year_without_its_state_names_each_parts_year(
        self, db, seed_user, tax_law,
    ):
        """2027 shipped without NC (R-SAL86): federal and FICA 2027's, NC 2026's 3.99%."""
        tax_law(TaxLaw(years=(*_TWO_YEARS.years, made_up_year(2027))))

        report = _report(db, seed_user, 2027)

        assert report.liability.basis == TaxLawBasis(2027, 2027, 2026, 2027)
        assert report.liability.basis.rules_year is None
        assert report.liability.state.flat_rate == Decimal("0.0399")
