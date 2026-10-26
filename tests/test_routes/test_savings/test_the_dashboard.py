"""The /savings dashboard page: its cards, the transfers it counts, the Setup Required badge, and
the net-worth context and stream it renders.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import CompoundingFrequencyEnum, EmployerContributionTypeEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.ref import AccountType, CalcMethod, PaycheckLineKind, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import account_service

from tests._test_helpers import (
    create_hysa_account,
    record_paydays_across_a_hole,
    rhythm_of,
    start_test_pay_list,
)

from ._helpers import _create_savings_account, _create_goal


def _create_investment_account_with_params(seed_user, seed_periods):
    """Create a 401k account with investment params and anchor period.

    Returns:
        (Account, InvestmentParams)
    """
    acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name="Test 401k",
            anchor_balance=Decimal("50000.00"),
        ),
    )
    db.session.add(acct)
    db.session.flush()

    params = InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        contribution_limit_year=2026,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
    )
    db.session.add(params)
    db.session.commit()
    return acct, params


def _create_investment_account_with_contributions(seed_user, seed_periods):
    """Create a 401k with employer flat 5% and employee deduction.

    Returns:
        (Account, InvestmentParams, SalaryProfile, PaycheckLine)
    """
    acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name="Test 401k Employer",
            anchor_balance=Decimal("50000.00"),
        ),
    )
    db.session.add(acct)
    db.session.flush()

    params = InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        contribution_limit_year=2026,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
        employer_flat_percentage=Decimal("0.0500"),
    )
    db.session.add(params)

    scenario = seed_user["scenario"]
    filing_status = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=scenario.id,
        filing_status_id=filing_status.id,
        name="Test Salary",
        state_code="NC",
    )
    db.session.add(profile)
    start_test_pay_list(
        profile, Decimal("3846.15"),  # $100,000.00 a year / 26
        seed_periods[0].start_date,
    )
    db.session.flush()

    pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").first()
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").first()
    deduction = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=pre_tax.id,
        calc_method_id=flat_method.id,
        name="401k Contribution",
        amount=Decimal("500.0000"),
        target_account_id=acct.id,
    )
    db.session.add(deduction)
    db.session.commit()
    return acct, params, profile, deduction


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestDashboard:
    """Tests for GET /savings -- the savings dashboard."""

    def test_dashboard_renders(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders successfully with accounts and periods."""
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"savings" in resp.data.lower() or b"Savings" in resp.data

    def test_dashboard_no_savings_accounts(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders even when user has no savings-type accounts."""
        with app.app_context():
            # seed_user only has a checking account -- no savings accounts.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Accounts" in resp.data
            assert b"No savings goals yet" in resp.data

    def test_dashboard_with_goals(self, app, auth_client, seed_user, seed_periods):
        """Dashboard displays savings goals when they exist."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            _create_goal(seed_user, acct)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Vacation Fund" in resp.data

    def test_dashboard_no_goals(self, app, auth_client, seed_user, seed_periods):
        """Dashboard renders account projections even with no goals."""
        with app.app_context():
            _create_savings_account(seed_user)

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            # Should show savings account even without goals.
            assert b"Savings" in resp.data

    def test_dashboard_investment_account_shows_growth_projections(
        self, app, auth_client, seed_user,
    ):
        """Investment account cards show projected balances with compound growth."""
        import re

        with app.app_context():
            # Start periods 14 days before today so today falls inside
            # period 0 or 1.  The savings dashboard renders milestone
            # projections at offsets +6, +13, +26 from the current
            # period; with a low current_period.period_index, all three
            # land within the 40 generated periods regardless of when
            # the test is run.  A fixed start_date instead would silently
            # drift current_period forward each calendar week and break
            # the 1-year milestone (offset 26) once today moved past
            # ~August 2026 (only 2 milestones would be displayed and
            # the assertion below would fail).
            start = date.today() - timedelta(days=14)
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=start,
                num_periods=40,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            acct, params = _create_investment_account_with_params(
                seed_user, periods,
            )

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With 7% annual return on $50k, the 1-year projection should
            # be notably higher than $50,000. If growth is NOT applied,
            # the balance stays flat at $50,000 (the bug).
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [int(a.replace(',', '')) for a in amounts]

            # The cockpit card shows ONE consolidated projection line (the
            # furthest horizon, 1 year). With 7% annual return on $50k the
            # 1-year projection (~$53.5k) is the largest dollar figure on the
            # page -- above the ~$51k net-worth band (checking $1k + the $50k
            # account summed at today's balances, NOT the projection) and the
            # $50k anchor -- so max > $52k proves growth was applied (a flat
            # balance would leave the max at the $51k band total).
            assert amounts_int, f"no dollar amounts rendered; page: {html[:300]}"
            assert max(amounts_int) > 52000, (
                "Expected the 1-year projection to exceed $52,000 with 7% "
                f"growth; largest amount was ${max(amounts_int):,}. "
                f"Amounts on page: {amounts}"
            )

    def test_dashboard_investment_account_includes_contributions(
        self, app, auth_client, seed_user,
    ):
        """Investment cards include employee + employer contributions in projections."""
        import re

        with app.app_context():
            # See test_dashboard_investment_account_shows_growth_projections
            # for why ``start`` is computed relative to today.
            start = date.today() - timedelta(days=14)
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=start,
                num_periods=40,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            acct, params, profile, ded = _create_investment_account_with_contributions(
                seed_user, periods,
            )

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With $500/period employee + 5% employer (~$192/period) + 7% growth
            # on $50k, projections should be substantially higher than growth-only.
            # Growth-only 1yr ~$53,500. With contributions (~$18k/yr), ~$71k+.
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [int(a.replace(',', '')) for a in amounts]

            # The cockpit card shows ONE consolidated projection line (1 year).
            # With $500/period employee + ~$192/period employer (5% of $100k/26)
            # + 7% growth on $50k, the 1-year projection is ~$72k -- well above
            # both the growth-only baseline (~$53.5k) and the ~$51k net-worth
            # band -- so max > $60k proves the contributions are included.
            assert amounts_int, f"no dollar amounts rendered; page: {html[:300]}"
            assert max(amounts_int) > 60000, (
                "Expected the 1-year projection to exceed $60,000 with "
                f"contributions; largest amount was ${max(amounts_int):,}. "
                f"Amounts on page: {amounts}"
            )

    def test_dashboard_employer_contribution_without_employee_deduction(
        self, app, auth_client, seed_user,
    ):
        """Employer flat 5% works even when no paycheck deduction targets the account."""
        import re

        with app.app_context():
            # See test_dashboard_investment_account_shows_growth_projections
            # for why ``start`` is computed relative to today.
            start = date.today() - timedelta(days=14)
            # The BINDING went with the ``current_anchor_period_id`` line it
            # fed (ruling R-EH); the CALL is fixture setup and stays -- it is
            # what creates the periods this test projects over.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=start,
                num_periods=40,
                rhythm=rhythm_of(14),
            )
            db.session.flush()

            # Create 401k with employer flat 5% but NO employee deduction.
            acct_type = db.session.query(AccountType).filter_by(name="401(k)").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=acct_type.id,
                    name="Employer Only 401k",
                    anchor_balance=Decimal("50000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()

            params = InvestmentParams(
                account_id=acct.id,
                assumed_annual_return=Decimal("0.07000"),
                annual_contribution_limit=Decimal("23500.00"),
                contribution_limit_year=2026,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.FLAT_PERCENTAGE),
                employer_flat_percentage=Decimal("0.0500"),
            )
            db.session.add(params)

            # Create salary profile (no deduction targeting the 401k), and
            # NAME it as the account's funding job -- ruling R-SAL5, applied
            # at plan step salary:R14-b.  This is precisely the shape that
            # column exists for: an employer contribution with no deduction
            # naming the account had no link to any profile at all, so the
            # basis fell to an owner-level unordered lookup.  Without the
            # link the ruling of 2026-09-04 models no employer money, and the
            # projection this case asserts would collapse to growth-only.
            filing_status = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing_status.id,
                name="Main Job",
                state_code="NC",
            )
            db.session.add(profile)
            # $100,000.00 a year / 26, from the first payday recorded above.
            start_test_pay_list(profile, Decimal("3846.15"), start)
            db.session.flush()
            params.salary_profile_id = profile.id
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()

            # With 5% employer on $3846/period (~$5k/yr) + 7% growth on $50k,
            # 1-year should be ~$58k+. Without employer, growth-only ~$53.5k.
            amounts = re.findall(r'\$([0-9,]+)', html)
            amounts_int = [int(a.replace(',', '')) for a in amounts]

            # The cockpit card shows ONE consolidated projection line (1 year).
            # With 5% employer flat on $100k/26 (~$192/period) + 7% growth on
            # $50k and no employee deduction, the 1-year projection is ~$58.7k --
            # above the growth-only baseline (~$53.5k) and the ~$51k net-worth
            # band -- so max > $55k proves the employer contribution is included.
            assert amounts_int, f"no dollar amounts rendered; page: {html[:300]}"
            assert max(amounts_int) > 55000, (
                "Expected the 1-year projection to exceed $55,000 with the "
                f"employer contribution; largest amount was ${max(amounts_int):,}. "
                f"Amounts on page: {amounts}"
            )

    def test_dashboard_requires_login(self, app, client, seed_user):
        """Unauthenticated request redirects to login."""
        with app.app_context():
            resp = client.get("/savings", follow_redirects=False)
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


# ── Shadow Transaction Inclusion Tests ────────────────────────────


class TestSavingsDashboardShadowTransactions:
    """Verify that the savings dashboard includes shadow transactions
    (from transfers) in account balance calculations.

    Before this fix, the dashboard filtered transactions by template_id,
    which excluded shadow transactions (template_id=None).  The correct
    filter uses the account_id column added in Task 1 of the transfer
    rework.
    """

    def test_hysa_balance_includes_transfer_deposit(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Verify that the savings dashboard passes shadow income
        transactions to the HYSA balance calculator, so transfer deposits
        increase the projected balance.  Without this, HYSA projections
        underestimate the balance by the total of all missed deposits.
        """
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Create HYSA account with known anchor balance, through the
            # shared factory rather than an inline account + InterestParams
            # pair: since plan step X-c2a modelled interest accrues only
            # forward of the account's latest ASSERTION, and the factory is
            # where that assertion's instant is pinned to the anchor period
            # (``account_service.create_account`` stamps it with the wall
            # clock, which for this suite's 2026-01-02 periods lands months
            # after the whole projection and would accrue nothing anywhere).
            hysa = create_hysa_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
                apy=Decimal("0.04500"), name="High Yield Savings",
            )

            # Add transfer categories required by transfer_service.
            incoming = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Incoming",
            )
            outgoing = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Outgoing",
            )
            db.session.add_all([incoming, outgoing])
            db.session.flush()

            # Create a $500 transfer from checking to HYSA.
            projected = db.session.query(Status).filter_by(name="Projected").one()
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=hysa.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("500.00")),
                    status_id=projected.id,
                    category_id=outgoing.id,
                ),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            # The HYSA should show in the dashboard.  Its balance should
            # include the $500 deposit + interest.  With anchor $10,000
            # + $500 deposit + daily compounding at 4.5% APY, the
            # balance will be ~$10,601.  The key assertion is that the
            # balance exceeds $10,500 (anchor + deposit), proving the
            # deposit was included before interest compounded.
            html = resp.data.decode()
            assert "High Yield Savings" in html
            # Without the fix, the balance would be ~$10,096 (anchor
            # + interest only, no deposit).  With the fix, it exceeds
            # $10,500.  Check for "10,6" which confirms the deposit
            # is reflected ($10,601 with interest).
            assert "10,6" in html

    def test_savings_balance_includes_transfer_deposit(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Verify that a regular savings account (no HYSA params) includes
        shadow income from transfers in its balance calculation.  The
        balance for a savings account receiving a $1000 transfer must
        reflect the deposit, not just the anchor balance.
        """
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        with app.app_context():
            # Origination anchor written at creation time so the
            # dated AccountAnchorHistory SoT (E-19 / Commit 4)
            # matches the test's intended anchor.  Pre-Commit-6
            # ``savings_dashboard_service`` read ``current_anchor_*``
            # columns directly; mutating those columns after
            # creation was enough to override the displayed balance.
            # Post-Commit-6 the resolver reads from history; the
            # cache mutation alone is reconciled-against, not honored
            # (the resolver logs EVT_ANCHOR_CACHE_RECONCILED and
            # returns the history value).  This is the documented
            # E-19 SoT shift, not a behavioral regression.
            savings = _create_savings_account(
                seed_user, name="Emergency Fund",
                anchor_balance=Decimal("3000.00"),
            )
            db.session.flush()

            # Transfer categories.
            incoming = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Incoming",
            )
            outgoing = Category(
                user_id=seed_user["user"].id,
                group_name="Transfers", item_name="Outgoing",
            )
            db.session.add_all([incoming, outgoing])
            db.session.flush()

            projected = db.session.query(Status).filter_by(name="Projected").one()
            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[0].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1000.00")),
                    status_id=projected.id,
                    category_id=outgoing.id,
                ),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            # Anchor $3,000 + $1,000 deposit = $4,000 at period 0.
            assert "4,000" in html

    def test_account_with_no_transfers_still_works(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Verify that the savings dashboard renders correctly for
        accounts that have no transfers.  The account_id filter must
        produce an empty list without errors, not crash or show stale
        data from another account's transactions.
        """
        with app.app_context():
            # Origination anchor written at creation time -- see the
            # explanatory comment in
            # test_savings_balance_includes_transfer_deposit above
            # for the E-19 / Commit 6 SoT-shift rationale.
            _create_savings_account(
                seed_user, name="Plain Savings",
                anchor_balance=Decimal("2000.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200

            html = resp.data.decode()
            assert "Plain Savings" in html
            assert "2,000" in html


# ── Setup Required Badge Tests ───────────────────────────────────


class TestSetupRequiredBadge:
    """Tests for the 'Setup Required' badge on the savings dashboard.

    The badge appears when a parameterized account type is missing its
    params record (e.g. account created before auto-creation was added).
    """

    def test_setup_badge_shown_for_hysa_without_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HYSA without InterestParams shows 'Setup Required' badge on dashboard."""
        with app.app_context():
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Unconfigured HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" in resp.data

    def test_setup_badge_hidden_for_hysa_with_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """HYSA with InterestParams does NOT show 'Setup Required' badge."""
        with app.app_context():
            from app.models.interest_params import InterestParams

            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Configured HYSA",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            # HIGH-06 / Commit 24: ``apy`` is NOT NULL with no
            # server_default; supply an explicit value.
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.04500"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" not in resp.data

    def test_setup_badge_shown_for_investment_without_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """401(k) without InvestmentParams shows 'Setup Required' badge."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Unconfigured 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" in resp.data

    def test_setup_badge_hidden_for_investment_with_params(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """401(k) with InvestmentParams does NOT show 'Setup Required' badge."""
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Configured 401k",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" not in resp.data

    def test_setup_badge_not_shown_for_checking(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Checking account does not show 'Setup Required' badge."""
        with app.app_context():
            # seed_user already has a checking account.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Setup Required" not in resp.data

    def test_needs_setup_with_no_params_record(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """401(k) with missing InvestmentParams renders without error and shows badge.

        Verifies the dashboard handles missing params gracefully (no 500)
        when an account was created before auto-creation was implemented.
        """
        with app.app_context():
            k401_type = db.session.query(AccountType).filter_by(
                name="401(k)"
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=k401_type.id,
                    name="Legacy 401k",
                    anchor_balance=Decimal("50000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Legacy 401k" in resp.data
            assert b"Setup Required" in resp.data


class TestDashboardNetWorthContext:
    """Tests for the net-worth cockpit data exposed by the /savings route.

    The route hands the template the money-precise ``net_worth`` figures
    (today totals + change + the forward trend series) and serializes the
    trend to a Chart.js JSON string (``net_worth_chart_json``); ``float``
    is applied only at that route boundary.  The template rebuild that
    renders this data is a later phase, so these tests inspect the context
    and the serialized contract directly.
    """

    @staticmethod
    def _capture_dashboard_context(app, auth_client):
        """Return the (template, context) captured from GET /savings.

        Uses Flask's ``template_rendered`` signal so the test reads the
        exact context the route handed ``render_template`` without parsing
        HTML.  Asserts the dashboard template rendered (a 200 page), so the
        test fails loud rather than inspecting a redirect.
        """
        # Pylint: import-outside-toplevel -- deferred import is the
        # file-wide test convention.
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get("/savings")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"GET /savings returned {response.status_code}; expected 200"
        )
        records = [
            (t, c) for t, c in recorded
            if t.name == "savings/dashboard.html"
        ]
        assert records, (
            "GET /savings did not render savings/dashboard.html; rendered: "
            f"{[t.name for t, _ in recorded]!r}"
        )
        return records[0]

    def test_context_carries_net_worth_decimal_figures(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The context's net_worth dict carries money-precise Decimals.

        With the seed Checking ($1,000) plus an added Savings ($4,000) and
        no transactions, the today figures are flat:
          total_assets = 1000.00 + 4000.00 = 5000.00, liabilities 0.00,
          net_worth 5000.00, liquid 5000.00.
        """
        with app.app_context():
            _create_savings_account(
                seed_user, name="Savings",
                anchor_balance=Decimal("4000.00"),
            )
            db.session.commit()

            _template, context = self._capture_dashboard_context(
                app, auth_client,
            )
            net_worth = context["net_worth"]

            # 1000.00 + 4000.00 = 5000.00
            assert net_worth.today.total_assets == Decimal("5000.00")
            assert net_worth.today.total_liabilities == Decimal("0.00")
            assert net_worth.today.net_worth == Decimal("5000.00")
            assert net_worth.today.liquid == Decimal("5000.00")

    def test_chart_json_parses_to_expected_shape_with_floats(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """net_worth_chart_json carries both ranges as float series.

        The route serializes ONE payload with the ``2 years`` totals (parallel
        ``net`` / ``assets`` / ``liabilities`` float arrays + the per-category
        ``composition`` split) and the nested ``horizon`` range (P-AC1).  With
        the seed Checking ($1,000) plus an added Savings ($4,000) and flat
        balances, every ``2 years`` ``net`` point is ``5000.0`` (the float
        boundary), and the horizon starts there too.
        """
        # pylint: disable=import-outside-toplevel
        import json
        with app.app_context():
            _create_savings_account(
                seed_user, name="Savings",
                anchor_balance=Decimal("4000.00"),
            )
            db.session.commit()

            _template, context = self._capture_dashboard_context(
                app, auth_client,
            )
            chart = json.loads(context["net_worth_chart_json"])

            # The payload carries only what net_worth_cockpit.js reads (plan
            # step X-s1, finding N-104): the top-level ``assets`` /
            # ``liabilities`` totals it used to ship reach no consumer, since
            # ``selectRange`` never names them and the stacked bands already
            # ARE those totals -- asserted below against the PRODUCER series,
            # which keeps both keys for the cross-page equality oracle.
            assert set(chart.keys()) == {
                "labels", "net", "current_index", "composition", "horizon",
            }
            series = context["net_worth"].series
            n = len(series.periods)
            assert n > 0
            assert len(chart["labels"]) == n
            assert len(chart["net"]) == n
            # float boundary: every value is a float, not a Decimal/str.
            assert all(isinstance(v, float) for v in chart["net"])
            # Flat $5,000 net worth at every trend point -> 5000.0.
            assert chart["net"][0] == 5000.0
            # current_index (the solid/dashed boundary) passes straight
            # through from the producer's series; an int in [0, n].
            assert chart["current_index"] == series.current_index
            assert isinstance(chart["current_index"], int)
            assert 0 <= chart["current_index"] <= n

            # The 2-year composition: each band a float list of length n, and
            # the bands reconciling to ``net`` at every point.  The payload's
            # ``assets`` / ``liabilities`` totals were deleted at X-s1, and so
            # were the producer's -- they were the same per-period sums under a
            # second key -- so the reconciliation is against ``net``, the one
            # total that survives because the client draws it.
            comp = chart["composition"]
            asset_bands = ("asset", "retirement", "investment", "other")
            for band in (*asset_bands, "liability"):
                assert len(comp[band]) == n
                assert all(isinstance(v, float) for v in comp[band])
            for i in range(n):
                asset_side = sum(comp[band][i] for band in asset_bands)
                assert asset_side - comp["liability"][i] == chart["net"][i]
                assert chart["net"][i] == float(series.net[i])

            # The horizon range: a float net series that starts at the hero
            # ($5,000), plus composition bands + milestone list.
            horizon = chart["horizon"]
            assert horizon is not None
            assert horizon["net"][0] == 5000.0
            assert all(isinstance(v, float) for v in horizon["net"])
            assert isinstance(horizon["composition"], dict)
            assert isinstance(horizon["milestones"], list)
            assert horizon["current_index"] == 0

    def test_dashboard_still_renders_with_net_worth_wired(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The existing /savings page still renders 200 with net worth added.

        The template rebuild is a later phase; adding the net-worth context
        keys must not break the current render.
        """
        with app.app_context():
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Accounts" in resp.data


class TestNetWorthStreamRender:
    """The /savings page renders the P-AC1 net-worth stream element.

    The diverging allocation bar and the old trend chart (with its
    6/13/26/All picker) were replaced by ONE element: a range toggle over a
    stream canvas plus a composition legend.
    """

    def test_renders_range_toggle_canvas_and_legend(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The stream's range toggle, canvas, and legend render on the page.

        With the seed Checking ($1,000) plus an added Savings ($4,000) there
        is a current period, so the chart region renders: the page carries
        the `2 years` / `Horizon` range toggle (Horizon default), the stream
        canvas with its serialized data-chart, and the composition legend
        with an Assets band swatch (the two accounts are assets).  The
        retired diverging-bar markup is gone.
        """
        with app.app_context():
            _create_savings_account(
                seed_user, name="Savings",
                anchor_balance=Decimal("4000.00"),
            )
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # The two-mode range toggle replacing the old picker + net/split.
            assert 'data-nw-range="horizon"' in html
            assert 'data-nw-range="2yr"' in html
            # The stream canvas carrying the serialized both-ranges payload.
            assert 'id="net-worth-chart-canvas"' in html
            assert "data-chart=" in html
            # The composition legend with the Assets band swatch.
            assert "nw-legend" in html
            assert "nw-legend__swatch--asset" in html
            # The retired diverging bar is gone.
            assert "nw-alloc__bar" not in html
