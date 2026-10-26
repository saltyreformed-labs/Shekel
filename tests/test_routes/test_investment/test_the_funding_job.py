"""The employer contribution's funding job (an unset job models no employer money and the page says
so; the write door refuses another owner's profile), and the dashboard and growth chart with
deduction-based contributions.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.models.investment_params import InvestmentParams

from ._helpers import (
    _create_investment_account,
    _create_investment_params,
    _create_salary_profile,
    _create_deduction,
)


class TestTheFundingJobIsNamedOrTheMoneyIsNotMODELLED:
    """Ruling **R-SAL5** and the developer's 2026-09-04 ruling, at the surface.

    An employer contribution is a percentage OF a gross, so it has to know
    which job's paycheck to take it from.  Where no deduction names the
    account there was no link to any profile at all, and the basis fell to
    ``income_service.get_current_gross_biweekly``'s unordered ``.first()``
    across the owner's active profiles -- a measured 39% swing on a two-job
    owner, flipping between renders with no data change.  Plan step
    **salary:R14-a** added ``budget.investment_params.salary_profile_id``;
    this step is its reader, and the developer ruled what a NULL means:
    **no employer money is modelled, and the page says so.**

    Both halves are graded here, because "models nothing" alone is what a
    silent regression looks like: an owner whose Employer chip simply vanished
    would have no way to tell a ruling from a bug.
    """

    @staticmethod
    def _account_with_an_employer_contribution(seed_user, db_session):
        """A 401(k) paying a 5% flat employer contribution, funding job UNSET.

        The real Empower shape (ledger row **D45**): no deduction names it, so
        nothing else can imply which job funds it.
        """
        acct = _create_investment_account(
            seed_user, db_session, name="Employer Only 401k",
        )
        _create_investment_params(
            db_session, acct.id,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.05"),
        )
        return acct

    def test_an_unset_funding_job_models_nothing_AND_says_why(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No funding job: the notice renders and the figure is withheld.

        The owner has an active salary profile, so a gross EXISTS -- what is
        missing is the statement that THIS account's employer contribution is
        taken from it.  That distinction is the whole ruling: the app could
        guess, and guessing is what it measured at a 39% swing.
        """
        acct = self._account_with_an_employer_contribution(seed_user, db.session)
        _create_salary_profile(
            db.session, seed_user["user"].id, seed_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No employer money is modelled" in html
        assert "funding job not set" in html
        # The CONFIGURATION still renders: the owner set a 5% contribution and
        # the app saying so is how they recognise the notice as being about
        # their own setup rather than a missing one.
        assert "Employer contributes" in html
        assert "5.00%" in html
        # And the selector offers the job they could name.
        assert 'name="salary_profile_id"' in html
        assert "Day Job" in html

    def test_naming_the_funding_job_models_the_money(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The same account with the job named: a real figure, no notice.

        The pair is what makes the case above non-vacuous -- without it,
        "the notice renders" is equally true of a page that always renders it.
        """
        acct = self._account_with_an_employer_contribution(seed_user, db.session)
        _create_salary_profile(
            db.session, seed_user["user"].id, seed_user["scenario"].id,
            funds=acct.id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No employer money is modelled" not in html
        assert "funding job not set" not in html
        # $100,000 / 26 = $3,846.15 a paycheck; 5% of it is $192.31.
        assert "$192.31" in html

    def test_the_write_door_REFUSES_another_owners_profile(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """A forged ``salary_profile_id`` is a 404, not a priced stranger's salary.

        The form's dropdown lists only the requester's own profiles, so a
        foreign id in the submission is an IDOR -- and the read it would feed
        prices this owner's employer contribution off another owner's salary
        and raise history.  The mirror-image door
        (``paycheck_lines.target_account_id``) was closed at
        ``salary:R14-a`` as ledger row **N-534**; this is the same guard on
        the same rule, and both call ``auth_helpers.require_owned_fk``.
        """
        acct = self._account_with_an_employer_contribution(seed_user, db.session)
        stranger = _create_salary_profile(
            db.session, second_user["user"].id, second_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": str(
                    ref_cache.employer_contribution_type_id(
                        EmployerContributionTypeEnum.FLAT_PERCENTAGE),
                ),
                "employer_flat_percentage": "5",
                "salary_profile_id": str(stranger.id),
            },
        )
        assert resp.status_code == 404
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id).one()
        )
        assert params.salary_profile_id is None

    def test_the_CREATE_branch_refuses_it_too(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """The other arm of the same door, which the case above cannot reach.

        ``update_params`` has two branches and the guard is on both, but the
        case above seeds an ``InvestmentParams`` row first and so only
        exercises the UPDATE arm -- an adversarial review measured the CREATE
        arm ungraded.  Here the account has no params row, so the POST takes
        the create path, and the forged profile must be refused before any
        row is written.
        """
        acct = _create_investment_account(seed_user, db.session)
        stranger = _create_salary_profile(
            db.session, second_user["user"].id, second_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": str(
                    ref_cache.employer_contribution_type_id(
                        EmployerContributionTypeEnum.FLAT_PERCENTAGE),
                ),
                "employer_flat_percentage": "5",
                "salary_profile_id": str(stranger.id),
            },
        )
        assert resp.status_code == 404
        # And nothing was created: the guard runs before the INSERT.
        assert (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id).one_or_none()
        ) is None


class TestContributionAwareDashboard:
    """Tests for the contribution timeline integration (5.2-2).

    Backward compatibility (no deductions/transfers) is already covered
    by TestInvestmentDashboard.test_dashboard_with_params.
    IDOR is already covered by TestInvestmentDashboard.test_dashboard_idor.
    """

    def test_dashboard_with_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Dashboard renders with deduction-based contributions.

        Creates a salary profile and a flat $500 deduction targeting the
        investment account.  Verifies the dashboard renders without error
        and the periodic contribution value appears in the response.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)

        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # The deduction contributes $500/period.
        assert b"500.00" in resp.data

    def test_growth_chart_with_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Growth chart HTMX fragment renders with deduction contributions.

        Verifies the growth chart route processes contribution data without
        error.  The chart uses synthetic periods, so deduction dates
        mostly fall back to periodic_contribution -- but the route must
        still call build_contribution_timeline without crashing.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)

        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data
