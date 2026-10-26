"""The growth chart fragment: its HTMX redirect, empty state, data and ownership, and the what-if
contribution overlay with its annual limit, employer match, comparison and horizon.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum

from ._helpers import (
    _create_investment_account,
    _create_investment_params,
    _create_other_investment,
    _create_salary_profile,
    _create_deduction,
    _extract_data_attr,
)


class TestGrowthChartFragment:
    """Tests for the investment growth chart HTMX fragment (U2)."""

    def test_growth_chart_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET without HX-Request header redirects to investment dashboard."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/growth-chart")
        assert resp.status_code == 302
        assert "/investment" in resp.headers.get("Location", "")

    def test_growth_chart_empty_without_params(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Returns empty state when no investment params exist."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"No projection data" in resp.data

    def test_growth_chart_with_data(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Returns canvas element when projection data exists."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data

    def test_growth_chart_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's growth chart returns 404
        and does not leak victim data."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data, (
            "IDOR response leaked victim's account name"
        )


# ── Tests: What-If Contribution Calculator (5.3-1) ─────────────


class TestWhatIfContributionCalculator:
    """Tests for the what-if contribution calculator on the investment
    growth chart.

    The what-if feature overlays a hypothetical contribution scenario
    on the committed projection, producing a dual-dataset chart and
    a comparison card showing the balance difference at the horizon.
    """

    def test_chart_no_what_if_single_line(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET growth-chart without what_if param: single dataset only.

        Backward compatibility: existing chart behavior unchanged when
        no what-if parameter is provided.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "growthChart" in html
        assert "data-whatif-balances" not in html, (
            "What-if data should not be present without what_if param"
        )
        # No comparison card.
        assert "Current Plan" not in html
        assert "Difference" not in html

    def test_chart_with_what_if_dual_lines(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=500: what-if data present.

        Verifies the response contains both committed and what-if
        datasets, and a comparison card.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "growthChart" in html
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None, (
            "What-if balances should be present"
        )
        assert len(whatif_balances) > 0
        # P2 rebuild: the comparison card became the OOB verdict strip
        # ("Current plan" sentence case).
        assert "Current plan" in html
        assert "Difference" in html
        # What-if label includes the amount.
        assert "500.00" in html

    def test_chart_what_if_zero_valid(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=0: valid growth-only scenario.

        Zero means "what if I stop contributing?" -- the what-if line
        shows balance growth without any contributions.  This is NOT
        treated as "clear the what-if."
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None, (
            "Zero is a valid what-if (growth-only), not 'clear'"
        )

    def test_chart_what_if_empty_string_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution= (empty): no what-if, single dataset.

        Empty input means "no what-if" -- chart reverts to standard mode.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_chart_what_if_invalid_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=abc: invalid input ignored, no error.

        Non-numeric input degrades gracefully to single-line chart.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=abc",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_chart_what_if_negative_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=-100: negative contribution ignored.

        Negative contributions are nonsensical; chart renders without
        what-if overlay.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=-100",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_what_if_respects_annual_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Annual contribution limit caps what-if contributions.

        The annual limit is per CALENDAR year and resets each January
        (the year-boundary reset in ``growth_engine``).  A 1-year rolling
        horizon cannot demonstrate the cap: it straddles two calendar
        years and each side stays at or under the limit, so nothing is
        trimmed and the result varies with today's date.  A 2-year
        horizon always contains at least one FULL calendar year, where
        26 periods x $500 = $13,000 exceeds the $7,000 cap and is provably
        trimmed -- making the assertion date-independent.

        Setup: $0 balance, $7,000/year limit, 0% return, $500/period.
        With 0% return and a $0 start, the final balance is the sum of
        capped contributions; the uncapped total is $500 per period, so a
        bound cap makes the final balance strictly smaller.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="Limited IRA", balance="0.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("7000.00"),
        )
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        # One end-balance per period; with 0% return and a $0 start, the
        # final balance is the sum of capped contributions.  $500 per
        # period is exactly the uncapped total -- equal to the final
        # balance only if the cap never bound.  A full interior calendar
        # year is trimmed from $13,000 to $7,000, so the cap binds and the
        # capped total is strictly less, on every date.
        last_balance = Decimal(whatif_balances[-1])
        uncapped_total = Decimal("500") * len(whatif_balances)
        assert last_balance < uncapped_total, (
            f"Cap must trim a full year: got ${last_balance}, "
            f"uncapped ${uncapped_total}"
        )
        assert last_balance >= Decimal("7000"), (
            f"Expected at least one year's limit ($7000), got ${last_balance}"
        )

    def test_what_if_employer_match_recalculated(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer match is recalculated for the what-if amount.

        Setup: 401(k) with 100% match up to 6% of gross.
        $100K salary -> biweekly gross ~$3846.15.
        6% of gross ~$230.77 (matchable).
        What-if: $300/period -> employer matches min($300, $230.77) = $230.77.
        Total per period: $300 + $230.77 = $530.77.

        With 0% return, end balance must exceed employee-only total
        ($300 * N periods), proving employer match was applied.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Matched 401k", balance="0.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=None,
            contribution_limit_year=None,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("1.0000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )
        # The salary profile provides the gross the employer match is a
        # percentage OF, and names itself as the account's funding job
        # (R-SAL5) -- there is no deduction here to imply the link.
        _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id, funds=acct.id,
        )
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=300",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        last_balance = Decimal(whatif_balances[-1])
        # Employee-only: $300 * ~27 periods = ~$8100.
        # With employer match: ($300 + $230.77) * ~27 = ~$14330.
        assert last_balance > Decimal("8100"), (
            f"Expected balance > $8100 (employee alone), got ${last_balance}. "
            "Employer match may not be applied to what-if amount."
        )

    def test_what_if_no_limit_brokerage(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Brokerage account (no annual limit): contributions uncapped.

        With 0% return and no limit, end balance = $what_if * N periods.
        Must exceed the amount that would be capped at a typical limit,
        confirming no artificial cap is applied.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Brokerage",
            name="My Brokerage", balance="0.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        last_balance = Decimal(whatif_balances[-1])
        # No limit: $500 * ~27 periods = ~$13500.
        # Should exceed a typical IRA limit of $7000.
        assert last_balance > Decimal("7000"), (
            f"Expected uncapped balance > $7000, got ${last_balance}"
        )

    def test_what_if_comparison_positive(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if > current contribution: comparison shows positive difference.

        No current contributions -> committed is growth-only.
        What-if at $500/period adds contributions.
        The what-if end balance exceeds committed -> positive difference.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Positive difference: what-if exceeds committed.
        assert "+$" in html, (
            "Expected positive difference indicator in comparison card"
        )

    def test_what_if_comparison_negative(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if < current contribution: comparison shows negative difference.

        Current contributions at $500/period via deduction.
        What-if at $100/period is less.
        The what-if end balance is lower -> negative difference.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=100",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Negative difference: what-if is less than committed.
        assert "-$" in html, (
            "Expected negative difference indicator in comparison card"
        )

    def test_what_if_comparison_zero(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if == current (both zero): comparison shows zero difference.

        No current contributions and what-if=0 means both projections
        are growth-only from the same starting balance.  Difference is
        exactly $0.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "same as current plan" in html, (
            "Expected zero-difference message in comparison card"
        )

    def test_what_if_no_current_contributions(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No existing contributions: committed is growth-only.

        When the account has no deductions or transfers, the committed
        projection is purely growth-based.  The what-if adds contributions,
        so it should produce a higher end balance.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        committed = _extract_data_attr(resp.data, "balances")
        whatif = _extract_data_attr(resp.data, "whatif-balances")
        assert committed is not None
        assert whatif is not None
        # What-if with contributions should exceed growth-only committed.
        assert Decimal(whatif[-1]) > Decimal(committed[-1]), (
            "What-if with contributions should exceed growth-only committed"
        )

    def test_what_if_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """Other user's account with what-if param: 404.

        IDOR protection is unaffected by the what-if parameter.
        """
        other_acct = _create_other_investment(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data

    def test_what_if_preserves_horizon(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if with custom horizon: both projections use same period count.

        The committed and what-if datasets must have the same length
        (same x-axis) regardless of the horizon setting.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=10&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        committed = _extract_data_attr(resp.data, "balances")
        whatif = _extract_data_attr(resp.data, "whatif-balances")
        assert committed is not None
        assert whatif is not None
        assert len(committed) == len(whatif), (
            f"Committed ({len(committed)}) and what-if ({len(whatif)}) "
            "must have the same number of data points"
        )
        # 10-year horizon should produce many more periods than 2-year.
        assert len(committed) > 100, (
            f"10-year horizon should have 100+ periods, got {len(committed)}"
        )
