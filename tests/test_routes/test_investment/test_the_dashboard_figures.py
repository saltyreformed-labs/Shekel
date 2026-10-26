"""The dashboard's and the growth chart's balance read through the canonical producer, and the
Employer Per Period card fed the limit-capped employee contribution.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import balance_at, growth_engine
from app.services.balance_at import BalanceContext
from app.utils.money import round_money
from tests._test_helpers import current_pay_period, last_covered_day, start_test_pay_list
from tests._test_helpers import open_books_before_the_first_assertion
from app.models.amount_ownership import AmountOwnership

from ._helpers import (
    _create_investment_account,
    _create_investment_params,
    _create_deduction,
    _extract_data_attr,
    _add_envelope_expense_with_cleared_entries_inv,
)


class TestInvestmentEntryAwareRouting:
    """C8-2 / C8-3: /investment dashboard + growth chart use canonical producer.

    Pins the R-1 finding: pre-Commit-8 the two investment handlers
    each had bare ``calculate_balances`` calls with no
    ``selectinload(Transaction.entries)``.  Routing both through
    ``balance_resolver.balances_for`` (which owns the eager-load and
    the anchor resolution) makes the entries-aware reduction
    structural for these routes.
    """

    def test_investment_holdings_entry_aware(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C8-2: dashboard current_balance == canonical producer value.

        Reproduction of the symptom on /investment:

          - Investment account anchor 50,000.00 on the current period.
          - One Projected envelope expense on the same account in the
            same period, ``estimated_amount = 500.00``.
          - Three CLEARED debit entries summing 45.71 (20 + 15.71 + 10).

        Hand arithmetic (CRIT-01 / F-009 / R-1):

          cleared_debit   = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          current_balance = 50,000.00 + 0 - 454.29 = 49,545.71

        The route renders this number formatted with ``{:,.2f}`` so
        the byte string ``$49,545.71`` (or the bare ``49,545.71``
        inside the page) MUST appear in the response.  Pre-Commit-8
        the route reported ``49,500.00`` (= 50,000 - 500) via the
        silent-degrade seam.  We also assert byte-equality with the
        canonical producer's value so the contract is locked beyond
        the rendered string.
        """

        with app.app_context():
            user = seed_user["user"]
            current_period = current_pay_period(user.id)
            assert current_period is not None

            # ``account_service.create_account`` (via the helper) anchors
            # the new account against the user's current pay period and
            # writes the matching ``AccountAnchorHistory`` row, so no
            # explicit override is needed -- the resolver reads the
            # factory's history row directly.
            acct = _create_investment_account(
                seed_user, db.session,
                type_name="401(k)", name="Test 401k",
                balance="50000.00",
            )
            _create_investment_params(db.session, acct.id)
            _add_envelope_expense_with_cleared_entries_inv(
                db.session,
                seed_user=seed_user,
                account=acct,
                period=current_period,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            # The purchases above are dated by their builder, BEFORE this
            # calendar's first period, so the books move again now that the
            # rows exist (plan step X-f3c-2b, ruling **R-HG**).  The helper
            # bounds on the earliest settled row as well as on the assertion,
            # which is why calling it a second time is the whole repair.
            open_books_before_the_first_assertion(db.session, acct)
            db.session.commit()

            # The entries-aware CASH BASIS the modelled balance is computed
            # ON: 50,000 - max(500 - 45.71 - 0, 0) = 49,545.71.  Read through
            # the seam's cash-flow view at plan step X-g4b, which deleted the
            # anchor-forward producer this asserted against; that view is the
            # same fold the modelled replay folds beneath its accrual, so the
            # basis under test is genuinely the one the tile is built on.
            bctx = BalanceContext.build(user.id)
            basis = balance_at.cash_balance_map(
                acct, bctx,
            )
            assert basis[current_period.id] == Decimal("49545.71")

            # What the tile RENDERS is that cash basis plus the modelled
            # accrual, since plan step X-g2b gave the anchor period its own
            # days (ruling R-Y).  The entries-aware arithmetic above is still
            # the load-bearing half: it is what the accrual is computed ON, so
            # the pre-fix seed would still land the rendered figure ~$45 low.
            displayed = balance_at.balance_map(
                acct, bctx,
            )[current_period.id]
            assert displayed > Decimal("49545.71")
            assert displayed - Decimal("49545.71") < Decimal("200.00")

            resp = auth_client.get(f"/accounts/{acct.id}/investment")
            assert resp.status_code == 200
            # The rendered current-balance tile carries the comma-formatted
            # Decimal.  Pre-Commit-8 it would render 49,500.00 (silent
            # degrade); asserting the modelled figure's presence AND the
            # pre-fix value's absence locks the regression in both directions.
            assert f"{displayed:,.2f}".encode() in resp.data
            assert b"49,500.00" not in resp.data

    def test_investment_growth_chart_entry_aware(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C8-3: growth_chart() seeds the projection from the entries-aware balance.

        Same setup as C8-2.  The growth-chart route projects a
        synthetic period series forward from ``current_balance`` --
        if that seed is wrong, the entire chart series is wrong.  The
        first chart point (``data-balances[0]``) is the seed period's
        end balance from the growth engine; with ``periodic_contribution
        = 0`` (no deductions, no recurring transfers) and only the
        post-anchor projection from 49,545.71, the first chart point
        ends very close to the seed (subject to one biweekly's worth
        of compounding at 7% annual = ~0.27%, ~$133 on $49,545.71).

        Pre-Commit-8 the seed was 49,500.00 via the silent-degrade
        seam, so the first chart point would land near $49,633 instead
        of near $49,679.  We assert the chart's first point sits in
        a tight band around the entry-aware seed -- the band is wide
        enough to absorb the growth engine's contribution / employer-
        match math but narrow enough to reject the pre-fix value.
        """
        with app.app_context():
            user = seed_user["user"]
            current_period = current_pay_period(user.id)
            assert current_period is not None

            # ``account_service.create_account`` (via the helper) anchors
            # the new account against the user's current pay period and
            # writes the matching ``AccountAnchorHistory`` row, so no
            # explicit override is needed -- the resolver reads the
            # factory's history row directly.
            acct = _create_investment_account(
                seed_user, db.session,
                type_name="401(k)", name="Test 401k",
                balance="50000.00",
            )
            _create_investment_params(db.session, acct.id)
            _add_envelope_expense_with_cleared_entries_inv(
                db.session,
                seed_user=seed_user,
                account=acct,
                period=current_period,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            # The purchases above are dated by their builder, BEFORE this
            # calendar's first period, so the books move again now that the
            # rows exist (plan step X-f3c-2b, ruling **R-HG**).  The helper
            # bounds on the earliest settled row as well as on the assertion,
            # which is why calling it a second time is the whole repair.
            open_books_before_the_first_assertion(db.session, acct)
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{acct.id}/investment/growth-chart?horizon_years=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            balances = _extract_data_attr(resp.data, "balances")
            assert balances is not None and len(balances) > 0

            # The band this used to assert is now an EXACT identity, because
            # plan step X-g2b made the seed a date the chart also opens its
            # axis from (ruling R-AF): the first projected point is the
            # account's modelled balance at the current period's END,
            # compounded over exactly one period.  With no contributions and no
            # employer match there is nothing else in the row.
            bctx = BalanceContext.build(user.id)
            seed = balance_at.balance_at(acct, bctx, last_covered_day(current_period))
            # The span is the axis's OWN first period -- the owner's next
            # paycheck, read off the same door the chart resolves its axis
            # through (plan step C2-e).  It used to be the CURRENT period's
            # span, which matched only because the fabricated axis it replaced
            # was hardcoded to the same 14 days.
            # 7.0% is ``_create_investment_params``' default assumed return.
            axis_head = bctx.calendar().projection_axis(
                last_covered_day(current_period) + timedelta(days=1),
                last_covered_day(current_period) + timedelta(days=365),
            )[0]
            rate = growth_engine.span_return_rate(
                Decimal("0.07000"), axis_head.start_date, axis_head.end_date,
            )
            first_point = Decimal(balances[0])
            assert first_point == seed + round_money(seed * rate), (
                f"First chart point {first_point} is not the seed {seed} "
                "compounded one period; the chart and its seed have "
                "separated (ruling R-AF)."
            )
            # ...and it is still strictly above where the pre-Commit-8
            # silent-degrade seed (49,500.00) would land, which is what this
            # test was written to catch.
            assert first_point > Decimal("49640.00")


class TestEmployerMatchCapped:
    """C25 / HIGH-07 / F-043 / F-055: dashboard "Employer Per Period" card
    feeds the limit-capped employee contribution into
    ``calculate_employer_contribution`` so it matches the growth chart's
    employer line and the year-end ``year_summary_employer_total``.

    Pre-fix the card passed the UNCAPPED ``periodic_contribution`` to the
    matcher (``investment.py:183 -> 187-189``), so near the annual limit
    a match-type employer overstated the card per-period match relative
    to the chart and year-end (F-043 worked example: $240 vs $100).
    """

    def _make_settled_shadow_income(
        self, seed_user, to_account, period, amount, db_session,
    ):
        """Seed a Settled transfer into the investment account.

        Uses the canonical ``transfer_service.create_transfer`` -- the
        only sanctioned path for budget.transfers rows -- and leaves it in
        a ``Received`` status whose ``excludes_from_balance = False`` so
        the route's YTD aggregation (``calculate_investment_inputs``
        Step 4) counts the resulting shadow income.

        **Created Projected, settled through the settle verb, then laid
        Received on BARE**: the create makes no settled transfer (ruling
        **R-BAL379**), and the verb settles a transfer with ``Done``; no
        transfer door writes ``Received``.  It was created ``Received``
        until that ruling, whose question approved building this state
        another way with the case's expected answer unchanged; the stored
        transfer is the one that create wrote (both sides recorded at the
        figure on the owner's today), but for its version counter, which the
        lay's one more write moves (measured by 4d-4a's review).  Laid over
        the SETTLED transfer, so both sides keep their dated records; the bare
        lay is the technique
        ``test_routes/test_transfers/_helpers._received_transfer_of`` uses,
        which lays Received over a Projected transfer holding none.
        """
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        received_id = ref_cache.status_id(StatusEnum.RECEIVED)
        cat = seed_user["categories"]["Groceries"]
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=to_account.id,
                pay_period_id=period.id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(amount),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=cat.id,
                name="YTD seed",
            ),
        )
        transfer_service.settle_transfer(xfer.id, seed_user["user"].id)
        xfer.status_id = received_id
        db_session.commit()
        return xfer

    def test_card_uses_capped_contribution_at_binding_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C25-1: card per-period employer match equals the chart/year-end
        capped value when the annual limit binds.

        Setup (F-043 worked example, scaled to fit a deduction):

          annual_contribution_limit = $23,500
          YTD shadow income contributed in past period of current year
            = $23,300  -> remaining = $200
          gross_biweekly                                 = $8,000
          (annual_salary = 8000 * 26 = $208,000)
          match: 50% up to 6% of gross
            matchable_salary = 8000 * 0.06           = $480.00
          deduction (employee contribution per period) = $1,500

        Pre-fix card (UNCAPPED $1,500 fed to the matcher):
          matched  = min(1500, 480)                  = 480
          employer = 480 * 0.50                      = $240.00

        Post-fix card (CAPPED at remaining limit before matcher):
          capped   = min(1500, max(23500 - 23300, 0)) = 200
          matched  = min(200, 480)                   = 200
          employer = 200 * 0.50                      = $100.00

        The card now reads $100.00; the pre-fix string $240.00 must
        not appear in the response.
        """
        # 401(k) account; create_account anchors at the current period.
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="HIGH-07 401k", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("23500.00"),
            contribution_limit_year=2026,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.5000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )

        # Salary profile: annual 208000 -> gross_biweekly 8000.
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Day Job",
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("8000.00"))  # $208,000.00 a year / 26
        db.session.flush()

        # Deduction $1500/period -> uncapped periodic_contribution = 1500.
        _create_deduction(db.session, profile.id, acct.id, "1500.00")
        db.session.commit()

        # YTD seed: $23,300 settled shadow income in a past period
        # within the current year.  seed_periods_today[0] is ~56 days
        # before today (period 4 = current), so for any test date this
        # year it lands in the same calendar year as today.
        self._make_settled_shadow_income(
            seed_user, acct, seed_periods_today[0],
            Decimal("23300.00"), db.session,
        )

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        # Post-fix capped employer match.
        assert "$100.00" in html, (
            "Card did not render the capped employer match of $100.00 "
            "(HIGH-07/F-043).  Card site may still be bypassing "
            "cap_contribution_at_limit before calling "
            "calculate_employer_contribution."
        )
        # Pre-fix uncapped value must NOT appear: locks the fix in both
        # directions.  Use a regex-safe assertion -- the value must not
        # appear anywhere in the rendered HTML.
        assert "$240.00" not in html, (
            "Pre-fix uncapped employer match $240.00 detected; the "
            "cap_contribution_at_limit fix has regressed."
        )

    def test_card_unchanged_when_well_below_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C25-3: well below limit, the card value is unchanged (regression
        guard).

        Same fixture as the binding-limit test but with no YTD seed:
        remaining = max(23500 - 0, 0) = 23500;
        capped = min(1500, 23500) = 1500 (cap does not bind);
        matched = min(1500, 480) = 480;
        employer = 480 * 0.50 = $240.00.

        The capped helper returns 1500 (the full periodic) so the card
        produces the same byte-identical $240.00 it always did.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="HIGH-07 below-limit", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("23500.00"),
            contribution_limit_year=2026,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.5000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Day Job",
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("8000.00"))  # $208,000.00 a year / 26
        db.session.flush()
        _create_deduction(db.session, profile.id, acct.id, "1500.00")
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        # No YTD: cap does not bind; full match (matchable=480) applies.
        assert "$240.00" in html, (
            "Below-limit card regressed: expected $240.00 employer per "
            "period.  cap_contribution_at_limit may be incorrectly "
            "clamping below the limit."
        )
