"""The Taxes tab says which year's tax rules priced it and names the state (plan step X-at-5).

Ruling R-SAL75: "each screen showing tax lines prints one line from it, e.g.
'2027 on 2026's tax rules'", the line read off the report's basis rather than
worked out by the screen; ruling R-SAL76 keeps a missing year on the nearest
year's rules unchanged, and this line is what makes that visible (finding
N-235).  Every case installs a made-up law (ruling R-SAL80), so none moves when
a real year is published.  The cases ask for years with no pay periods, so the
figures are zero and the words are what is under test.

Its own module rather than ``test_analytics.py``, which is already past the
1,000-line ceiling (ruling R-IR; its split is plan#2000's).
"""

import re
from decimal import Decimal
from html import unescape

import pytest

from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.tax_law import TaxLaw
from tests._test_helpers import made_up_state, made_up_year, start_test_pay_list

#: Two made-up years, each listing North Carolina.
_NC_2025_2026 = (
    made_up_year(2025, states={"NC": made_up_state(Decimal("0.0425"))}),
    made_up_year(2026, states={"NC": made_up_state(Decimal("0.0399"))}),
)


def _seed_profile(seed_user, db, state_code="NC"):
    """Seed one active single salary profile in *state_code*, $130,000.00 a year.

    The route builds the report from it; no pay period falls in the years
    asked for, so only the words differ between cases.
    """
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name="Rules Line Profile",
        filing_status_id=(
            db.session.query(FilingStatus).filter_by(name="single").one().id
        ),
        state_code=state_code,
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("5000.00"))  # $130,000.00 a year / 26
    db.session.commit()
    return profile


def _tab_text(auth_client, tax_year):
    """GET the Taxes tab partial for *tax_year*; return its visible text, one space apart."""
    resp = auth_client.get(
        f"/analytics/taxes?year={tax_year}", headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", resp.data.decode())).split())


class TestTheRulesLine:
    """The Assumptions card's Tax year row prints which year's rules priced the tab."""

    def test_a_year_the_law_carries_prints_the_bare_year(
        self, app, auth_client, seed_user, db, tax_law,
    ):
        """2026 is in the law: the row is the year alone, with no note."""
        tax_law(TaxLaw(years=_NC_2025_2026))
        with app.app_context():
            _seed_profile(seed_user, db)

            text = _tab_text(auth_client, 2026)

            assert "Tax year 2026 Filing status" in text
            assert "tax rules" not in text
            assert "Shekel doesn't have" not in text

    def test_a_year_after_the_newest_prints_the_newest_years_rules(
        self, app, auth_client, seed_user, db, tax_law,
    ):
        """2030 is past the newest year, 2026: every part priced on 2026's (R-SAL76)."""
        tax_law(TaxLaw(years=_NC_2025_2026))
        with app.app_context():
            _seed_profile(seed_user, db)

            text = _tab_text(auth_client, 2030)

            assert (
                "Tax year 2030 on 2026's tax rules "
                "Shekel doesn't have 2030's tax rules. Filing status"
            ) in text

    def test_a_year_before_the_first_prints_the_first_years_rules(
        self, app, auth_client, seed_user, db, tax_law,
    ):
        """A typed 2019 is before the law's first year, 2025 (finding N-235's own case)."""
        tax_law(TaxLaw(years=_NC_2025_2026))
        with app.app_context():
            _seed_profile(seed_user, db)

            text = _tab_text(auth_client, 2019)

            assert (
                "Tax year 2019 on 2025's tax rules "
                "Shekel doesn't have 2019's tax rules. Filing status"
            ) in text

    def test_a_year_without_its_state_prints_each_parts_year(
        self, app, auth_client, seed_user, db, tax_law,
    ):
        """2027 shipped without NC (R-SAL86): federal and FICA 2027's, NC 2026's."""
        tax_law(TaxLaw(years=(*_NC_2025_2026, made_up_year(2027))))
        with app.app_context():
            _seed_profile(seed_user, db)

            text = _tab_text(auth_client, 2027)

            assert (
                "Tax year 2027: federal tax on 2027's rules, Social Security and "
                "Medicare on 2027's, NC tax on 2026's "
                "Shekel doesn't have all of 2027's tax rules. Filing status"
            ) in text


class TestTheStateIsNamedFromTheProfile:
    """Every label naming the state reads the profile's, never a literal NC."""

    @pytest.mark.usefixtures("seed_periods")
    def test_a_profile_in_another_listed_state_labels_the_tab_with_it(
        self, app, auth_client, seed_user, db, tax_law,
    ):
        """A made-up law listing South Carolina alone: the tab says SC wherever it named NC.

        Until plan step X-at-5 eight labels on the tab spelled the state as
        the literal "NC"; the law lists the states the app supports (ruling
        R-SAL78), so a second state would have been labelled North Carolina.
        ``seed_periods`` puts paydays in 2026, so Box 1 wages are above zero
        and the effective-rate chip, whose caption names the state, renders.
        The figures are not under test here; every label is.
        """
        tax_law(TaxLaw(years=(
            made_up_year(2026, states={"SC": made_up_state(Decimal("0.0500"))}),
        )))
        with app.app_context():
            _seed_profile(seed_user, db, state_code="SC")

            text = _tab_text(auth_client, 2026)

            assert "2026 federal + SC combined" in text   # hero caption
            assert re.search(r"SC state \$[\d,]+\.\d\d (refund|owed)", text)
            assert "fed + SC combined, of Box 1 wages" in text
            assert "State SC · 5.00% flat after $25,500" in text
            assert "SC taxable base" in text
            assert "SC tax (5.00% after $25,500 deduction)" in text
            assert "SC withheld (" in text
            assert re.search(r"SC (refund|owed) \$[\d,]+\.\d\d", text)
            assert re.search(r"\bNC\b", text) is None
