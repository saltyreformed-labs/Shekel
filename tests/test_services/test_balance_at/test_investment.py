"""Balance-at seam tests: investment and property maps, investment growth and contributions, and
the income basis.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.services import balance_at
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.pay_calendar import calendar_for
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.projection_inputs import load_investment_params_for_accounts, load_payroll_feeds
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from tests._test_helpers import NO_TAX_LAW
from tests._test_helpers import (
    all_periods,
    current_pay_period,
    derived_span,
    last_covered_day,
    make_appreciating_account,
    make_investment_account,
    make_salary_profile,
    pricing_over,
)

from ._helpers import _make_hysa, _add_flat_deduction


class TestBalanceMapInvestment:
    """``balance_map`` reproduces the kernel growth path (INVESTMENT)."""

    def test_anchor_at_current_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An investment anchored at the current period equals the kernel.

        The seam assembles the InvestmentParams and the payroll feed the
        paycheck engine prices for it, then delegates to
        the growth path; it must equal the kernel called with those same
        manually-assembled inputs.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            current = current_pay_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, current, Decimal("10000.00"),
            )

            params = load_investment_params_for_accounts([inv]).get(inv.id)

            seam = balance_at.balance_map(inv, bctx)
            expected = net_worth_kernel.build_account_balance_map(
                inv, bctx,
                ContributionInputs(
                    investment_params=params,
                    # PRICED, as the seam's own loader does since plan step
                    # salary:R14-b: the kernel below the seam takes the
                    # paycheck engine's per-payday answer, not the ORM rows
                    # and not a scalar gross.
                    feed=load_payroll_feeds(
                        pricing_over(calendar_for(user_id)), [inv.id],
                        {inv.id: params},
                    )[inv.id],
                ),
            )

            assert seam is not None
            assert seam == expected

    def test_anchor_in_past_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An anchor-in-past investment equals the kernel (forward + reverse).

        Anchored at period index 2, the kernel reverse-projects the two
        pre-anchor periods below the anchor and forward-projects the
        post-anchor periods above it.  The seam must reproduce the whole
        map, exercising both projection directions.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            params = load_investment_params_for_accounts([inv]).get(inv.id)

            seam = balance_at.balance_map(inv, bctx)
            expected = net_worth_kernel.build_account_balance_map(
                inv, bctx,
                ContributionInputs(
                    investment_params=params,
                    # PRICED, as the seam's own loader does since plan step
                    # salary:R14-b: the kernel below the seam takes the
                    # paycheck engine's per-payday answer, not the ORM rows
                    # and not a scalar gross.
                    feed=load_payroll_feeds(
                        pricing_over(calendar_for(user_id)), [inv.id],
                        {inv.id: params},
                    )[inv.id],
                ),
            )

            assert seam is not None
            assert seam == expected
            # Reverse projection below the anchor, forward growth above it.
            assert seam[periods[0].id] < seam[periods[2].id]
            assert seam[periods[-1].id] > seam[periods[2].id]


class TestInvestmentGrowthSinceAnchor:
    """``investment_growth_since_anchor`` decomposes growth vs contributions.

    The chip's contract, RESTATED at plan step X-g2b (ruling R-AC): ``growth +
    contributed`` reconciles to the cent with the displayed balance minus the
    balance the user ASSERTED, because both are readings of one replay whose
    two modelled tiers exist only forward of that assertion (rulings R-L / R-Y
    for the accrual, R-Z for the contribution).  It used to reconcile against
    ``balance_map[anchor_period]`` instead -- the anchor PERIOD's end balance --
    which quietly excluded the anchor period's own growth, because the shipped
    producer could not model any.

    These tests are the load-bearing guard against the map and the
    decomposition drifting apart.  The parity check against the raw producer
    that used to live here is GONE: that producer (``_investment``) is the
    incumbent the replay replaced, kept unwired until plan step X-g4, so
    asserting they agree would assert the defect back into place.
    """

    def test_reconciles_with_displayed_balance_change(
        self, app, db, seed_user, seed_periods_today,
    ):
        """growth + contributed == balance_map[current] - the ASSERTED balance.

        Anchored well before the current period so the window spans real
        growth.  The decomposition sums to the modelled balance change EXACTLY,
        so the chip can never disagree with the hero it explains.

        **The right-hand side is the user's own asserted $10,000.00**, not
        ``balance_map[anchor_period]`` (ruling R-AC).  Both modelled tiers start
        at the assertion's own day (rulings R-Y / R-Z), so the cumulative total
        at any date IS the total since the anchor -- while the anchor PERIOD's
        end balance already contains the days between the assertion and that
        period's end, which the old right-hand side silently dropped.  Reading
        the map at ``periods[0]`` here would understate the left side by exactly
        that period's accrual, so this form is the stronger one AND the only
        one the replay makes true.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            current = current_pay_period(user_id)
            # Anchor at the first period, strictly before the current one.
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            result = balance_at.investment_growth_since_anchor(
                inv, bctx, derived_span(current),
            )
            assert result is not None
            growth, contributed = result

            balances = balance_at.balance_map(inv, bctx)
            # The reconciliation identity, to the cent, against the balance the
            # user ASSERTED rather than against another producer's output.
            assert growth + contributed == (
                balances[current.id] - Decimal("10000.00")
            )
            # A growing account with no negative movements grows: growth > 0.
            assert growth > Decimal("0.00")
            # And the anchor period is NOT excluded: its own end balance is
            # already above the assertion, which is what ruling R-Y bought and
            # what makes the identity above differ from the retired one.
            assert balances[periods[0].id] > Decimal("10000.00")

    def test_the_chip_is_read_where_the_headline_is(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The chip reports through the current period's END, not through today.

        The headline it explains is ``balance_map[current_period]`` -- a
        period-END figure, the convention every net-worth surface has used since
        plan step X-c2b2 -- so a chip read at TODAY would explain a balance the
        page is not showing, by the accrual of the days between (measured $9.65
        to $26.05 on the three real accounts, finding N-81).

        The two dates are made to DISAGREE here (the fixture's current period
        runs past today), so this cannot pass by them coinciding: the chip
        equals the period-end reconciliation and is strictly greater than the
        same decomposition read at today.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            current = current_pay_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            assert last_covered_day(current) > date.today()  # the dates differ

            growth, contributed = balance_at.investment_growth_since_anchor(
                inv, bctx, derived_span(current),
            )
            balances = balance_at.balance_map(inv, bctx)
            assert growth + contributed == (
                balances[current.id] - Decimal("10000.00")
            )
            # Read at TODAY instead and it is strictly smaller -- the days
            # between today and the period's end have not accrued yet.
            at_today = balance_at.balance_at(inv, bctx, date.today())
            assert at_today - Decimal("10000.00") < growth + contributed

    def test_none_when_anchored_at_current_period(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Anchored THIS period -> the chip SHOWS, with that period's own accrual.

        **This arm inverted at plan step X-g2b (ruling R-AC / R-Y).**  The
        shipped producer split its periods on ``period_index > anchor_idx``, so
        an account anchored in the current period had no post-anchor window and
        the page hid the chip.  Ruling R-Y removes that premise: the assertion's
        own day accrues, so such an account HAS earned something -- measured
        $105.26 on the real Roth IRA, $44.95 on the Traditional IRA and $76.59
        on the Empower 401(k) at their anchor periods -- and hiding the chip
        would deny a figure the balance beside it already contains, which is the
        visible contradiction ruling R-K refused to ship.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            current = current_pay_period(user_id)
            inv = make_investment_account(
                seed_user, db.session, current, Decimal("10000.00"),
            )

            result = balance_at.investment_growth_since_anchor(
                inv, bctx, derived_span(current),
            )
            assert result is not None
            growth, contributed = result
            # It earned its own days, and the balance beside it says the same.
            assert growth > Decimal("0.00")
            assert contributed == Decimal("0.00")
            balances = balance_at.balance_map(inv, bctx)
            assert growth + contributed == (
                balances[current.id] - Decimal("10000.00")
            )

    def test_none_when_current_period_is_none(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No current period -> None (the caller hides the chip)."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            result = balance_at.investment_growth_since_anchor(
                inv, bctx, None,
            )
            assert result is None


class TestBalanceMapProperty:
    """``balance_map`` reproduces the kernel appreciation path (APPRECIATING)."""

    def test_property_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property map equals the kernel's appreciation path.

        A Property classifies APPRECIATING, so the seam supplies
        ``investment_params=None`` (the loader excludes it) and the kernel
        reads the appreciation rate off the account's params backref.  The
        market value compounds forward above the anchor and flat-carries
        backward at the anchor value.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            seam = balance_at.balance_map(prop, bctx)
            expected = net_worth_kernel.build_account_balance_map(
                prop, bctx,
                ContributionInputs(),
            )

            assert seam is not None
            assert seam == expected
            # Forward appreciation above the anchor period.
            assert seam[periods[-1].id] > seam[periods[2].id]
            # Pre-anchor periods flat-carry the asserted market value (ruling
            # R-S: a manually-asserted point-in-time value has no historical
            # basis to compound backward from) ...
            assert seam[periods[0].id] == Decimal("400000.00")
            # ... while the ANCHOR period itself now accrues its own days
            # (ruling R-Y).  These two used to be EQUAL, because the shipped
            # producer split on ``period_index > anchor_idx`` and served the
            # anchor period from the flat cash base -- so a Property earned
            # nothing at all in the period it was valued in, and earned it
            # again from scratch every time the user re-asserted.
            assert seam[periods[2].id] > Decimal("400000.00")


class TestTheSeamOwnsTheIncomeBasis:
    """Ruling R-Q: the live override map is the seam's, not the caller's.

    ``balance_map`` and ``cash_balance_map`` used to take an
    ``amount_overrides`` argument, and its ``None``-handling differed by kind:
    the plain path auto-built a LIVE map while the interest path fell back to
    the STORED ``estimated_amount``.  One account could therefore be valued on
    two income bases in one render, and the difference surfaced on the grid as
    interest.  The fold builds its own map over its own plan, so the argument
    is gone and these tests pin what replaced it -- live income, without being
    asked for.
    """

    def test_a_stale_stored_amount_is_priced_live_without_being_asked(
        self, app, db, seed_user, seed_periods_today, tax_law,
    ):
        """A salary row is priced by its profile on both maps, unasked.

        A $104,000 profile nets $4,000.00 a period (104000 / 26, hand-computed).
        Both the kind-correct map and the cash-flow map must carry that figure
        with no argument passed: $1,000 anchor + $4,000 = $5,000.00 at period 5.

        **The row stores NOTHING since plan step X-au-d**, where it carried a
        deliberately stale ``$1.00``.  The property is stronger for it: a fold
        reading the column would contribute ``None`` rather than a wrong
        figure, so the substitution this arc removes is unrepresentable here
        instead of merely absent.
        """
        # pylint: disable=import-outside-toplevel
        tax_law(NO_TAX_LAW)
        from tests.test_services.test_income_service._helpers import (
            _create_profile,
            _make_salary_template,
            _make_txn,
        )

        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()
            txn = _make_txn(seed_user, periods[5], template=template)
            db.session.commit()

            assert txn.estimated_amount is None
            assert balance_at.balance_map(
                account, bctx,
            )[periods[5].id] == Decimal("5000.00")
            assert balance_at.cash_balance_map(
                account, bctx,
            )[periods[5].id] == Decimal("5000.00")

    def test_an_interest_account_is_on_the_same_live_basis_as_a_plain_one(
        self, app, db, seed_user, seed_periods_today, tax_law,
    ):
        """The kind that used to read STORED income reads LIVE income too.

        This is the asymmetry ruling R-Q deleted.  An HYSA's salary row is
        DECLARED exactly as a plain account's is; its balance must be built on
        the same $4,000.00 net, so the grid's premium over the cash basis is
        pure interest rather than an income mismatch.
        """
        # pylint: disable=import-outside-toplevel
        tax_law(NO_TAX_LAW)
        from tests.test_services.test_income_service._helpers import (
            _create_profile,
            _make_salary_template,
            _make_txn,
        )

        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            profile = _create_profile(user_id, scenario.id)
            # The paycheck lands on the HYSA because its DEFINITION does: the
            # engine puts a row on its template's account.
            template = _make_salary_template(
                seed_user, profile, name="HYSA paycheck", account=hysa,
            )
            db.session.commit()
            _make_txn(seed_user, periods[5], template=template)
            db.session.commit()

            kind_correct = balance_at.balance_map(hysa, bctx)
            cash = balance_at.cash_balance_map(hysa, bctx)
            # The live $4,000.00 lands in the CASH basis...
            assert cash[periods[5].id] - cash[periods[4].id] == Decimal(
                "4000.00",
            )
            # ...and the kind-correct balance is that basis plus interest.
            # The FIRST period's accrual is hand-derivable and pins the rate
            # and the 14-day count exactly:
            #   Q(5000 * ((1 + 0.05/365) ** 14 - 1)) = 9.60
            # (a 13-day window -- the day-count regression this catches --
            # gives 8.91).
            assert (
                kind_correct[periods[0].id] - cash[periods[0].id]
            ) == Decimal("9.60")
            # By period 5 the compounded premium is $65.54, nowhere near the
            # ~$3,999 an income-basis mismatch would have produced.  The
            # compounding itself is graded hand-computed in
            # ``test_interest_accrual.py``; this is its regression pin.
            assert (
                kind_correct[periods[5].id] - cash[periods[5].id]
            ) == Decimal("65.54")


class TestInvestmentContributions:
    """Deductions and employer match flow through the seam's growth path."""

    def test_deduction_increases_balance_and_is_scoped(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An active deduction raises the 401(k) balance; checking is untouched.

        Baseline (no deduction) vs a $200/period flat contribution: the
        post-anchor balance must be strictly higher (the contribution is
        consumed).  The seam == the kernel called with the same loaded
        deduction.  In a mixed build_maps, the checking account -- which has
        no deduction -- is unaffected (the deduction is scoped to the 401(k)
        by target_account_id, not leaked).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            baseline = balance_at.balance_map(inv, bctx)

            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            _add_flat_deduction(db, profile, inv, Decimal("200.0000"))
            db.session.commit()

            # A FRESH pass for the read AFTER the write.  A ``BalanceContext``
            # is a memo whose lifetime is the one read it was built for, and
            # since plan step X-i4 the cash fold every modelled kind rides on
            # is one of the derivations it holds.  This case is green on one
            # pass only by accident -- ``make_salary_profile`` writes no
            # transaction, so the CONTRIBUTION tier (re-derived per call) moves
            # while the memoized fold does not -- and adding one row to the
            # fixture would turn it into two stale halves compared against each
            # other.  Found by X-i4's adversarial review, which caught this with
            # a session-level write probe after its three siblings were fixed.
            after_ctx = BalanceContext.build(user_id)
            with_ded = balance_at.balance_map(inv, after_ctx)
            # Post-anchor period reflects the consumed contribution.
            assert with_ded[periods[-1].id] > baseline[periods[-1].id]

            # seam == kernel with the SAME manually-loaded feed.
            params = load_investment_params_for_accounts([inv]).get(inv.id)
            feeds_for_inv = load_payroll_feeds(
                pricing_over(calendar_for(user_id)), [inv.id],
                {inv.id: params},
            )[inv.id]
            expected = net_worth_kernel.build_account_balance_map(
                inv, after_ctx,
                ContributionInputs(
                    investment_params=params,
                    # PRICED, as the seam's own loader does since plan step
                    # salary:R14-b: the kernel below the seam takes the
                    # paycheck engine's per-payday answer, not the ORM rows
                    # and not a scalar gross.
                    feed=feeds_for_inv,
                ),
            )
            assert with_ded == expected
            # Non-vacuity: the feed really does price the deduction.  Asked
            # over the pass's saved periods since plan step salary:S3-e-2 (it
            # was ``any(amount > 0)`` over a priced MAP, put here at S3-e-1
            # when ``models_employee`` went; the property was this predicate).
            assert any(
                feeds_for_inv.employee_at(period) > 0
                for period in after_ctx.reported_periods()
            )

            # Scope: a non-investment account in the same batch is untouched.
            checking = seed_user["account"]
            maps = balance_at.build_maps([inv, checking], after_ctx)
            assert maps[checking.id] == balance_at.balance_map(
                checking, after_ctx,
            )

    def test_employer_match_driven_by_gross_exceeds_no_match(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A gross-capped employer match raises the balance above a no-match peer.

        Two identical 401(k)s ($10k, period-2 anchor, $200/period employee
        contribution) differ only in employer type: one matches 50% up to 6%
        of gross, the other has none.  With a real salary (gross > 0) the
        match cap is positive, so the matched account's post-anchor balance
        exceeds the no-match account's.  A zero / wrong gross would zero the
        cap and collapse the difference.  The seam == the kernel for the
        matched account.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv_match = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
                name="401k Match", employer_type="match",
                match_pct=Decimal("0.5000"), match_cap_pct=Decimal("0.0600"),
            )
            inv_none = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
                name="401k None",
            )
            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            _add_flat_deduction(db, profile, inv_match, Decimal("200.0000"))
            _add_flat_deduction(db, profile, inv_none, Decimal("200.0000"))
            db.session.commit()


            match_map = balance_at.balance_map(inv_match, bctx)
            none_map = balance_at.balance_map(inv_none, bctx)
            assert match_map[periods[-1].id] > none_map[periods[-1].id]

            # seam == kernel for the matched account.
            params = load_investment_params_for_accounts(
                [inv_match],
            ).get(inv_match.id)
            expected = net_worth_kernel.build_account_balance_map(
                inv_match, bctx,
                ContributionInputs(
                    investment_params=params,
                    # PRICED, as the seam's own loader does since plan step
                    # salary:R14-b: the kernel below the seam takes the
                    # paycheck engine's per-payday answer, not the ORM rows
                    # and not a scalar gross.
                    feed=load_payroll_feeds(
                        pricing_over(calendar_for(user_id)),
                        [inv_match.id],
                        {inv_match.id: params},
                    )[inv_match.id],
                ),
            )
            assert match_map == expected
