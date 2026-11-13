"""Salary route tests: the tax configuration page and its layout.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

import dataclasses
from decimal import Decimal

from app.tax_law import TaxLaw
from tests._made_up_federal import made_up_credits, made_up_federal
from tests._test_helpers import (
    bracket_set_law,
    made_up_year,
    state_and_fica_law,
)


# ── Tax Config ─────────────────────────────────────────────────────


class TestTaxConfig:
    """The old tax-config address, which only redirects now.

    Its state-tax and FICA POST doors were deleted at plan step salary:X-at-1
    (ruling R-SAL74): the tax law lives in :mod:`app.tax_law`, and nothing in
    the app writes it.
    """

    def test_tax_config_redirects_to_settings(self, app, auth_client, seed_user):
        """GET /salary/tax-config returns 302 redirect to settings dashboard."""
        with app.app_context():
            response = auth_client.get("/salary/tax-config")

            assert response.status_code == 302
            assert "/settings" in response.headers["Location"]
            assert "section=tax" in response.headers["Location"]


class TestTaxConfigLayout:
    """Tests for tax config page section ordering, collapse behavior, and form integrity.

    Verifies the reorganized layout: State Tax first, FICA second, Federal
    Tax Brackets third (collapsed by default with per-year grouping).
    """

    def test_tax_config_section_order(self, app, auth_client, seed_user):
        """Sections appear in order: State Tax, FICA, Federal Tax Brackets."""
        with app.app_context():
            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

            state_pos = html.index("State Tax")
            fica_pos = html.index("FICA")
            federal_pos = html.index("Federal Tax Brackets")

            assert state_pos < fica_pos < federal_pos, (
                f"Expected State Tax ({state_pos}) < FICA ({fica_pos}) "
                f"< Federal ({federal_pos})"
            )

    def test_federal_brackets_collapsed_by_default(self, app, auth_client, seed_user, tax_law):
        """Federal brackets card body starts collapsed (no 'show' class)."""
        with app.app_context():
            tax_law(bracket_set_law())

            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

            assert 'class="collapse" id="federal-brackets-collapse"' in html
            assert 'class="collapse show" id="federal-brackets-collapse"' not in html

    def test_federal_brackets_content_present_in_dom(self, app, auth_client, seed_user, tax_law):
        """Bracket data is in the DOM even though the section is collapsed."""
        with app.app_context():
            tax_law(bracket_set_law())

            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

            # Bracket amounts from seeded data should be present in the HTML.
            assert "$11,600" in html
            assert "10.0%" in html

    def test_federal_brackets_toggle_button_exists(self, app, auth_client, seed_user):
        """Toggle button for Federal Brackets has proper Bootstrap collapse attributes."""
        with app.app_context():
            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

            assert 'data-bs-toggle="collapse"' in html
            assert 'data-bs-target="#federal-brackets-collapse"' in html
            assert 'aria-controls="federal-brackets-collapse"' in html
            # Outer toggle starts collapsed.
            assert 'aria-expanded="false" aria-controls="federal-brackets-collapse"' in html

    def test_tax_config_no_nested_forms(self, app, auth_client, seed_user, tax_law):
        """No form tags are nested inside other form tags after the reorder."""
        with app.app_context():
            tax_law(state_and_fica_law())

            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

            # Count form open and close tags.
            open_count = html.count("<form")
            close_count = html.count("</form>")
            assert open_count == close_count, (
                f"Unbalanced form tags: {open_count} opens, {close_count} closes"
            )

            # Track depth to verify no nesting.
            depth = 0
            i = 0
            while i < len(html):
                open_idx = html.find("<form", i)
                close_idx = html.find("</form>", i)
                if open_idx == -1 and close_idx == -1:
                    break
                if open_idx != -1 and (close_idx == -1 or open_idx < close_idx):
                    depth += 1
                    assert depth <= 1, "Nested <form> tags detected"
                    i = open_idx + 1
                else:
                    depth -= 1
                    i = close_idx + 1

    def test_multiple_tax_years_most_recent_expanded(self, app, auth_client, seed_user, tax_law):
        """With multiple tax years, the most recent is expanded, older collapsed."""
        with app.app_context():
            tax_law(bracket_set_law(2025, 2026))

            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

            # Most recent year (2026) should be expanded.
            assert 'class="collapse show" id="federal-brackets-2026"' in html
            # Older year (2025) should be collapsed.
            assert 'class="collapse" id="federal-brackets-2025"' in html
            assert 'class="collapse show" id="federal-brackets-2025"' not in html

    def test_a_year_stating_withholding_figures_shows_them(
        self, app, auth_client, seed_user, tax_law,
    ):
        """A year whose withholding documents print other figures shows them (ruling plan#3213).

        The made-up 2025 states a 13,850.00 withholding deduction for every
        filing status beside the return's 14,600.00, and a 1,800.00
        withholding child credit beside the return's 2,000.00; the made-up
        2026 states none, so its card shows no withholding line.  The child
        credit is the year's, stated once for every filer (ruling plan#3340),
        so the card shows it once where it showed it under each status.
        """
        tax_law(TaxLaw(years=(
            made_up_year(
                2025,
                federal=dataclasses.replace(
                    made_up_federal(),
                    withholding_standard_deduction=Decimal("13850.00"),
                ),
                federal_credits=dataclasses.replace(
                    made_up_credits(),
                    withholding_child_credit_amount=Decimal("1800.00"),
                ),
            ),
            made_up_year(
                2026, federal=made_up_federal(), federal_credits=made_up_credits(),
            ),
        )))
        with app.app_context():
            response = auth_client.get("/settings?section=tax")
            html = response.data.decode()

        # Newest year first: the 2026 card, then the 2025 card.
        start_2026 = html.index('id="federal-brackets-2026"')
        start_2025 = html.index('id="federal-brackets-2025"')
        card_2026 = html[start_2026:start_2025]
        card_2025 = html[start_2025:]
        assert "Paychecks withhold on" not in card_2026
        assert card_2025.count("Paychecks withhold on (Pub 15-T):") == 4
        assert card_2025.count("$13,850.00") == 4
        assert card_2025.count("Paychecks withhold on (Form W-4):") == 1
        assert card_2025.count("$1,800.00") == 1
        # The return's figures: each status's deduction heads it, and the
        # year's credits show once.
        assert card_2025.count("(Std. Deduction: $14,600.00)") == 4
        assert card_2025.count("Child Credit:</span> $2,000.00") == 1
        assert card_2025.count("Other Dependent Credit:</span> $500.00") == 1

    def test_tax_config_page_renders_without_errors(self, app, auth_client, seed_user):
        """Tax config page renders 200 with no template errors."""
        with app.app_context():
            response = auth_client.get("/settings?section=tax")
            assert response.status_code == 200
            assert b"Tax Rates" in response.data
