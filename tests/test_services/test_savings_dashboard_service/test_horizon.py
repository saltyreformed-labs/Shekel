"""Savings dashboard service tests: the net-worth horizon, milestone labels, and the tile horizons
following the owner's cadence.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext

from tests._test_helpers import current_pay_period, derived_span, make_projected_envelope_expense

from ._helpers import _create_small_loan, _add_savings_account, _add_mortgage_account


class TestNetWorthHorizon:
    """Tests for the long-horizon annual net-worth producer (P-AC1 Loop B P1).

    ``build_horizon`` builds an annual net-worth composition +
    net-trajectory series to the horizon domain (last loan payoff + 1 year, or
    a fixed decade for a loan-free user), reusing the /retirement engine for
    the retirement / investment bands, per-account growth params for the asset
    band, and the loan resolver schedules for the liability band.

    **Read through ``compute_dashboard_data``, the only path production has**
    (plan step X-q2, finding N-100).  These tests called
    ``compute_net_worth_horizon``, a narrow producer with ZERO ``app/`` callers
    -- an AST census found its 10 call sites were all in this file -- so the
    suite was grading a second producer no screen reached while the page read
    the horizon out of the full build.  The narrow producer is deleted; the
    horizon is read where the route reads it.  The one exception is the
    no-pay-periods test below, which calls ``build_horizon`` directly because
    the state it needs -- a user with no pay periods -- is upstream of
    the build rather than inside it.
    """

    def test_none_without_periods(self, app, db, bare_user_with_cadence):
        """No pay periods -> the horizon producer returns None (no axis).

        **``bare_user_with_cadence`` rather than ``bare_user`` since plan step
        ``pay_calendar:C4-d``** (ruling R-PC45): ``calendar_for`` refuses an
        owner with no ``budget.pay_schedule`` row, so a bare owner would raise
        inside ``build_horizon`` and this case would stop being about the
        horizon producer's own empty answer.  The owner it builds is the one
        this case has always been about -- no PAY PERIODS -- with the half of
        them that was never the subject left in place.

        **The owner really has none**, and that is the change pay-calendar plan
        step C2-f2d-3 forced.  This case built a ``_DashboardCoreData`` with
        ``all_periods=[]`` beside a ``seed_user`` pass -- and ``seed_user``
        seeds a bootstrap pay period, so the bundle stated an empty schedule
        while the pass it carried answered a non-empty one.  Production could
        not build that pair (one loader wrote both fields), but a test could,
        and it was grading a state no render can be in.  The period set is off
        the pass now, so the only way to say "no pay periods" is to have none:
        ``bare_user``.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.services.savings_dashboard_service._horizon import (
                build_horizon,
            )
            from app.services.savings_dashboard_service._types import (
                _DashboardCoreData,
            )
            core = _DashboardCoreData(
                accounts=[],
                balance_ctx=BalanceContext.build(
                    bare_user_with_cadence["user"].id,
                ),
            )
            assert build_horizon(core, []) is None

    def test_the_narrow_producers_do_not_need_a_cadence_they_never_use(
        self, app, db, bare_user,
    ):
        """An owner with NO pay cadence still gets the budget dashboard.

        The regression control for a defect plan step R7a-2a introduced and
        then fixed: resolving the owner's pay cadence inside
        ``_load_dashboard_core_data`` made ``/`` raise ``PayCalendarError`` for
        an owner with no ``budget.pay_schedule`` row, because
        ``dashboard_service.compute_tracks_section`` reaches that loader
        through BOTH narrow producers -- and both of them return early for an
        owner with no goals and no loans, so neither ever converts anything.
        A page must not fail for a fact it does not use.

        ``bare_user`` is the state: no periods, no schedule row, no goals, no
        loans.  Both producers must answer their empty result rather than
        raise.  What must still raise is the FULL ``/savings`` build, whose
        coverage footer states a span in paychecks on every render -- that
        assertion is in ``test_pay_cadence.py``, so this one is about the
        surfaces that need nothing.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            assert savings_dashboard_service.compute_goal_progress(
                BalanceContext.build(user_id),
            ) == []
            assert savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(user_id),
            ) is None

    # **``test_a_stored_span_no_longer_decides_whether_a_calendar_derives`` was
    # deleted at plan step ``pay_calendar:C4-c``.**  It stretched one period's
    # stored span to 546 days and asserted the owner's cadence still came from
    # ``budget.pay_schedule`` rather than from that span -- the inference plan
    # step C4-b-2 deleted, whose out-of-range answer used to make every balance
    # page a 500 (ledger row **P35**).
    #
    # **It planted a stored ``budget.pay_periods.end_date`` that disagreed with
    # the owner's paydays, and plan step ``pay_calendar:C4-c`` dropped that
    # column.**  The plant is not merely unreachable, it is SILENT: assigning to
    # an attribute the model no longer maps sets a plain Python attribute, writes
    # no UPDATE, and survives ``expire_all`` -- so the case and its own premise
    # assertion both went on passing while measuring nothing.  Deleted rather
    # than left green.
    #
    # There is no span to stretch now: a period's end is the day before the next
    # payday, computed on every read.  ``resolve_cadence`` reading the schedule
    # row is graded by ``tests/test_services/test_pay_schedule_service.py`` and
    # by ``test_c4b2_pay_period_schedule_key.py``, neither of which needs a
    # doctored column to say it.

    def test_publishes_only_the_keys_the_page_reads(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Every published key is one the route's serializer consumes (N-100).

        The producer's side of the contract plan step X-q2 established; the
        route's side -- each key removed in turn must break
        ``_serialize_horizon`` -- is
        ``test_savings.test_the_cockpit.TestHorizonSerialization``'s
        ``test_every_published_key_is_read``.
        Pinned as a literal here so a key added without a consumer fails a test
        rather than living on as a producer output no screen reaches, which is
        what ``horizon_end`` and ``is_loan_free`` did until X-q2.
        """
        with app.app_context():
            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].horizon

            assert set(horizon) == {
                "dates", "current_index", "composition", "net", "milestones",
            }

    def test_loan_free_uses_fixed_decade_window(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A user with no loans gets the fixed 10-year forward window.

        The domain end is December 31 of ``today.year + 10``; index 0 is today
        (the "Today" marker), and it is the LAST sample -- the domain end is
        ``dates[-1]`` by construction, which is why plan step X-q2 deleted the
        second ``horizon_end`` key that restated it.
        """
        with app.app_context():
            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].horizon
            assert horizon is not None
            assert horizon["dates"][0] == date.today()
            assert horizon["dates"][-1] == date(date.today().year + 10, 12, 31)
            assert horizon["current_index"] == 0

    def test_today_point_equals_net_worth_hero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The horizon's index-0 figures equal the page's net-worth hero.

        Checking ($1,000) + Savings ($4,000) + a 401(k) as assets and a
        $240,000 mortgage: the horizon starts at the same net worth, total
        assets, and total liabilities the cockpit hero shows.  The 401(k)
        pins that the /retirement-engine today balance the horizon reads for
        the retirement band agrees with the account_data balance the hero
        sums (both the model-from-anchor balance_at figure).
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            _add_savings_account(seed_user, Decimal("4000.00"))
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            _add_mortgage_account(seed_user, Decimal("240000.00"))
            uid = seed_user["user"].id

            hero = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )["net_worth"]
            horizon = hero.horizon

            asset_bands = ("asset", "retirement", "investment", "other")
            asset0 = sum(
                (horizon["composition"][band][0] for band in asset_bands),
                Decimal("0"),
            )
            assert horizon["net"][0] == hero.today.net_worth
            assert asset0 == hero.today.total_assets
            assert horizon["composition"]["liability"][0] == (
                hero.today.total_liabilities
            )

    def test_the_asset_band_compounds_to_the_EXACT_sample_date(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Plan step **C2-e**: the asset band left the paycheck axis.

        This band carries NO contributions -- it calls the growth engine with
        ``periodic_contribution`` at its zero default -- so an axis was only
        ever chopping the horizon into pieces the compound formula is
        indifferent to.  Once the pieces are the owner's REAL paychecks that
        indifference ends: the period covering today OPENED on their last
        payday, up to a cadence in the past, while the band's seed is valued
        TODAY, so every sample would have carried growth for days that had
        already happened.

        So each sample is now compounded straight from today, through the one
        growth formula, and this asserts that identity to the cent at every
        point.  A $400,000 Property at 4% is the shape that makes the
        difference visible: a per-period walk from the last payday would run
        the clock up to 13 days early AND round to the cent once per period.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_appreciating_account
            from app.services import balance_at, growth_engine
            from app.utils.money import round_money
            periods = seed_periods_today
            prop = make_appreciating_account(
                seed_user, db.session, periods[0],
                Decimal("400000.00"), Decimal("0.04000"),
            )
            uid = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )
            horizon = data["net_worth"].horizon
            band = horizon["composition"]["asset"]
            dates = horizon["dates"]
            today = dates[0]

            # The band SUMS every asset-band account, and the seeded owner also
            # holds flat cash there.  A flat account contributes the identical
            # figure at every sample (its rate is zero), so the Property's own
            # today value plus that constant reproduces the whole band.
            by_account = {
                ad.account.id: ad.current_balance
                for ad in data["account_data"]
            }
            property_today = by_account[prop.id]
            flat = band[0] - property_today

            # **The BALANCE SEAM is the oracle for the day count**, not this
            # test's own arithmetic.  A first cut recomputed the band's formula
            # and asserted equality, which pinned the implementation rather than
            # grading it -- and the implementation was one day out (an
            # adversarial code review, 2026-08-14).  The seam accrues a modelled
            # asset one day at a time, so the growth it gives from today to
            # ``today + n`` is n days, and the band must agree.
            bctx = BalanceContext.build(uid)
            seam_today = balance_at.balance_at(prop, bctx, today)

            assert len(dates) > 2, "a decade of samples, not a single point"
            for index in range(1, len(dates)):
                elapsed = (dates[index] - today).days
                factor = 1 + growth_engine.growth_rate_for_days(
                    Decimal("0.04000"), elapsed,
                )
                # The seam's own answer for the same span, on the same balance:
                # this is what makes the day count independent of the band.
                assert balance_at.balance_at(
                    prop, bctx, dates[index],
                ) == round_money(seam_today * factor), (
                    f"the seam does not grow {elapsed} days to {dates[index]}"
                )
                assert band[index] == round_money(
                    property_today * factor,
                ) + flat, (
                    f"sample {dates[index]} is not today's value compounded "
                    f"over the {elapsed} days the seam counts"
                )

            # The firing control: the identity above is not vacuous.  The
            # Property really does grow, by far more than a rounding error.
            assert band[-1] - flat > property_today * Decimal("1.4")

    def test_the_engine_band_keys_on_the_ORDINAL_not_the_period_id(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ledger row **P21**: the projected tail shares one ``period_id``.

        The horizon's retirement band samples a per-account balance map at each
        annual sample date.  That map was keyed on ``ProjectedBalance.period_id``
        -- and every period past the owner's saved horizon is a PROJECTION
        whose id is ``None``, so over a ten-year axis the whole projected tail
        collapsed onto one entry and every sample after the schedule ran out
        read the same figure.

        A seeded calendar covers months, so essentially the entire axis here is
        projected: if the map still collapsed, every post-horizon sample would
        be identical.  They are strictly increasing instead.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("50000.00"),
            )
            uid = seed_user["user"].id

            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )["net_worth"].horizon
            band = horizon["composition"]["retirement"]

            assert len(band) > 3
            # Past the seeded schedule every sample is a distinct projected
            # period, so the series must be strictly increasing rather than
            # flat at one collapsed key.
            for earlier, later in zip(band, band[1:]):
                assert later > earlier, (
                    "two horizon samples read the same projected period, "
                    "which is the P21 collapse"
                )
            assert len(set(band)) == len(band)

    def test_group_subtotal_equals_horizon_band_at_today(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The legend matches its chart band per category on the default view.

        The stream's legend renders ``group_subtotals[band]`` (summed from
        each account's ``shown_balance`` -- its ``current_balance``, and for a
        liability what that HELD balance owes; ruling R-CC48, plan step
        credit_card:CC-5-5c) directly beneath the chart.  On the default
        ``Horizon`` range, each band's index-0 value is the horizon's today
        point -- built from the SAME ``current_balance``, the liability band
        through the same ``owed()`` -- so the legend and the chart agree per
        band even for a loan holder
        (Checking $1,000 + Savings $4,000 asset $5,000, and a $240,000
        mortgage liability), and the legend can never disagree with the chart
        it labels.  (The ``2 years`` range's liability band is the loan's
        contractual schedule per decision 11, which equals ``current_balance``
        on reconciled data but can drift when a loan's anchor is a stale
        true-up -- a pre-existing cockpit-wide seam, not this element's.)
        """
        with app.app_context():
            _add_savings_account(seed_user, Decimal("4000.00"))
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )
            uid = seed_user["user"].id

            ctx = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )
            subtotals = ctx["group_subtotals"]
            horizon = ctx["net_worth"].horizon

            # Checking $1,000 + Savings $4,000 = $5,000 asset; $240,000
            # mortgage liability.  Both groups are present.
            assert subtotals["asset"] == Decimal("5000.00")
            assert subtotals["liability"] == Decimal("240000.00")
            for band, subtotal in subtotals.items():
                assert horizon["composition"][band][0] == subtotal, band

    def test_non_amortizing_liability_stays_in_the_band(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A revolving Credit Card debt appears in the liability band at its fold.

        A liability with no amortization schedule (Credit Card, no
        ``loan_params``) reads its cash fold forward (plan step credit_card:CC-1),
        which with no planned rows holds its asserted balance at every date --
        and it must NOT vanish from the horizon: the today point
        still reconciles to the net-worth hero, and the $3,000 debt does not
        disappear when the range toggles from ``2 years`` to ``Horizon``.

        The card is anchored at ``-3,000.00`` HELD, which is what the create
        door stores for "$3,000.00 owed" (the route crosses the typed figure,
        ruling R-CC52, and ``create_account`` stores the held balance it is
        handed), so it holds -3,000.00 and owes 3,000.00 -- and the hero and
        the liability band both report what it owes.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.models.ref import AccountType
            from app.services import account_service
            _add_savings_account(seed_user, Decimal("4000.00"))
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card").one()
            )
            card = account_service.create_account(account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cc_type.id,
                name="Visa",
                anchor_balance=Decimal("-3000.00"),
            ))
            db.session.add(card)
            db.session.commit()
            uid = seed_user["user"].id

            hero = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )["net_worth"]
            horizon = hero.horizon
            liability = horizon["composition"]["liability"]

            # Checking $1,000 + Savings $4,000 assets, $3,000 card liability:
            #   total_liabilities = owed(-3000.00) = 3000.00 ;
            #   net = 5000 - 3000 = 2000.
            assert hero.today.total_liabilities == Decimal("3000.00")
            assert horizon["net"][0] == hero.today.net_worth
            # The card is in the band at index 0, and with no planned rows its
            # fold holds the asserted -3,000.00 at the far end too, which the
            # band reports as the 3,000.00 it owes.
            assert liability[0] == Decimal("3000.00")
            assert liability[-1] == Decimal("3000.00")
            # A card carries no payoff model, so the domain is the fixed
            # loan-free decade -- the card neither sets nor poisons it.
            assert horizon["dates"][-1] == date(date.today().year + 10, 12, 31)

    def test_composition_reconciles_to_net_each_point(
        self, app, db, seed_user, seed_periods_today,
    ):
        """net[k] == sum(asset bands) - liability band at every horizon point."""
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            _add_savings_account(seed_user, Decimal("4000.00"))
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            _add_mortgage_account(seed_user, Decimal("240000.00"))

            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].horizon
            comp = horizon["composition"]
            asset_bands = ("asset", "retirement", "investment", "other")
            for k in range(len(horizon["net"])):
                asset_side = sum(
                    (comp[band][k] for band in asset_bands), Decimal("0"),
                )
                assert horizon["net"][k] == asset_side - comp["liability"][k]

    def test_retirement_band_equals_retirement_engine_at_horizon(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The final retirement band equals the /retirement projection (oracle).

        The P-AC1 ruling's "the cockpit band equals /retirement by
        construction": re-running the SAME engine
        (``build_projection_context`` + ``load_projection_batch`` +
        ``project_accounts_with_batch`` over the context's own axis) at the
        horizon end and summing the projected balances must equal the horizon
        retirement band's final sample.  The three are spelled out because the
        convenience entry that wrapped them had no ``app/`` caller left once
        the retirement picture became the one producer (plan step C2-f2d-2),
        and this oracle wants a DIFFERENT horizon from the picture's own.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            from app.services import pay_period_service, retirement_projection
            periods = seed_periods_today
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("50000.00"),
            )
            uid = seed_user["user"].id

            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )["net_worth"].horizon

            # The domain end is the last annual sample (plan step X-q2 deleted
            # the second key that restated it).  The periods come off the pass
            # (pay-calendar plan step C2-f2d-3) rather than being threaded in.
            ctx = retirement_projection.build_projection_context(
                BalanceContext.build(uid), horizon["dates"][-1], None,
            )
            projections = retirement_projection.project_accounts_with_batch(
                ctx,
                retirement_projection.load_projection_batch(ctx),
                retirement_projection.resolve_projection_axis(ctx),
            )
            expected = sum(
                (p["projected_balance"] for p in projections),
                Decimal("0"),
            )
            # 50k at 7% over a decade grows well past 50k.
            assert expected > Decimal("50000.00")
            assert horizon["composition"]["retirement"][-1] == expected

    def test_liability_amortizes_toward_zero_and_sets_domain(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A mortgage sets the domain to payoff + 1 year and amortizes to zero.

        The liability band starts at the loan's current balance ($240,000) and
        the final sample -- past the payoff -- is zero owed; the domain end
        year is the payoff year plus one.

        Originated TODAY (no overdue gap), so the forward fold synthesizes its
        WHOLE contractual schedule from the full balance and amortizes cleanly to
        zero -- the on-schedule case.  A past origination with no payment records
        would be delinquent under the C6b plan fold and never reach zero (B-9).
        """
        with app.app_context():
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
                origination_date=date.today(),
            )
            uid = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )
            payoff = data["debt_summary"].payoff_outlook.all_clear_on
            horizon = data["net_worth"].horizon
            liability = horizon["composition"]["liability"]

            # A payoff-sized domain is the loan-bearing state: the fixed
            # loan-free decade would not land on the payoff year plus one.
            assert horizon["dates"][-1].year == payoff.year + 1
            assert liability[0] == Decimal("240000.00")
            assert liability[-1] == Decimal("0.00")
            assert liability[-1] < liability[0]

    def test_debt_free_milestone_at_payoff(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan yields exactly one payoff-complete flag at that date.

        The label reads "All loans paid off" as of plan step X-q3 (developer
        ruling on finding N-99): the date behind it covers amortizing loans,
        the only debts with a payoff model, and a revolving balance on the
        same chart's liability band never reaches zero.

        **The label is now the flag's IDENTITY** (plan step X-s1, ruling
        R-BC): the machine ``kind`` beside it reached no consumer -- the
        serializer copied it into the payload and the client's flag plugin
        never read it -- so it is gone from both ends, and this asserts the
        milestone's whole key set here rather than only at the top level.  The
        expected label is imported from the producer, not re-typed: X-q3
        renamed this very string, and a hand-typed copy would have gone stale
        without failing.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _DEBT_FREE_MILESTONE_LABEL,
        )
        with app.app_context():
            # Originated today, so no month behind the loan's assertion is
            # unrecorded and it amortizes to zero (see ``_add_mortgage_account``:
            # since plan step R16-b-2 a skipped month is charged, and the
            # helper's default 2025 origination never clears).
            _add_mortgage_account(
                seed_user, Decimal("240000.00"), origination_date=date.today(),
            )
            uid = seed_user["user"].id

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(uid),
            )
            payoff = data["debt_summary"].payoff_outlook.all_clear_on
            horizon = data["net_worth"].horizon

            # Identified by the (label, date) PAIR, never the label alone
            # (plan step X-t4, finding N-110): a per-loan flag reads
            # "<account name> paid off", which equals this label exactly for an
            # account a user names "All loans".  The pair is unique by
            # construction -- a per-loan flag fires only STRICTLY BEFORE the
            # debt-free date -- and the collision itself is covered by
            # ``TestAMilestoneLabelCanCollide`` below.
            debt_free = [
                m for m in horizon["milestones"]
                if m["label"] == _DEBT_FREE_MILESTONE_LABEL
                and m["date"] == payoff
            ]
            assert len(debt_free) == 1
            # The wording itself, pinned ONCE (this is the only place it is
            # spelled out); every other consumer reads the constant.
            assert _DEBT_FREE_MILESTONE_LABEL == "All loans paid off"
            # The milestone's complete key set -- the producer's half of the
            # nested contract whose route half is
            # ``test_savings.test_the_cockpit.TestHorizonSerialization
            # .test_every_published_key_is_read``.
            for milestone in horizon["milestones"]:
                assert set(milestone) == {"date", "label"}

    def test_net_crossing_milestone(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A net trajectory crossing $500k yields a 'Net $500k' flag.

        A $300,000 401(k) at 7% over the decade window grows the net worth
        (~$301k today) past $500k, so exactly one $500k crossing flag fires at
        the first sample that reaches it.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("300000.00"),
            )

            horizon = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].horizon
            assert horizon["net"][0] < Decimal("500000")
            assert horizon["net"][-1] >= Decimal("500000")
            crossings = [
                m for m in horizon["milestones"] if m["label"] == "Net $500k"
            ]
            assert len(crossings) == 1

    def test_net_milestone_label_formatting(self):
        """The $500k-step crossing labels format without scientific notation.

        A whole-million multiple reads ``$NM`` (int-formatted, so a round
        ten-million is ``$10M`` and never ``Decimal.normalize``'s ``1E+1``);
        a half-million residue keeps its ``.5``.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _format_net_milestone,
        )
        assert _format_net_milestone(Decimal("500000")) == "Net $500k"
        assert _format_net_milestone(Decimal("1000000")) == "Net $1M"
        assert _format_net_milestone(Decimal("1500000")) == "Net $1.5M"
        assert _format_net_milestone(Decimal("10000000")) == "Net $10M"
        assert _format_net_milestone(Decimal("2500000")) == "Net $2.5M"


class TestAMilestoneLabelCanCollide:
    """Two flags may share a label, and the chart draws both (plan step X-t4).

    Finding N-110.  Plan step X-s1 deleted the machine ``kind`` from the
    milestone dicts because nothing in ``app/`` read it -- the serializer copied
    it into the payload and the client's flag plugin never looked at it -- which
    left the LABEL as a flag's only handle.  The debt-free flag reads "All loans
    paid off" and a per-loan flag reads ``f"{account.name} paid off"``, so a
    user who names an account "All loans" makes the two strings equal.

    **Ruled (developer, 2026-07-28): the label IS the identity, and a duplicate
    is a display outcome rather than a defect.**  Two flags at two dates are two
    true statements; the producer must never DROP one to keep labels unique,
    because a silently missing flag is the worse failure.  What the finding
    actually broke was a TEST that counted flags by matching the string, and the
    fix is to identify a flag by the ``(label, date)`` pair -- unique by
    construction, since a per-loan flag fires only strictly before the debt-free
    date.

    This pins the ruling so it is a predicate rather than prose: a future step
    that de-duplicates by label, or drops the colliding per-loan flag, fails
    here.
    """

    def test_an_account_named_all_loans_yields_two_identical_labels(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The colliding account's flag and the debt-free flag BOTH survive.

        A 24-month loan literally named "All loans" pays off years before a
        30-year mortgage, so the producer emits its per-loan flag ("All loans
        paid off") AND the debt-free flag at the mortgage's payoff -- the same
        string, twice, at two dates.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._horizon import (
            _DEBT_FREE_MILESTONE_LABEL,
        )
        with app.app_context():
            colliding = _create_small_loan(
                seed_user, db.session, name="All loans",
            )
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
                origination_date=date.today(),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            milestones = data["net_worth"].horizon["milestones"]
            payoff = data["debt_summary"].payoff_outlook.all_clear_on

            # Precondition: the fixture really does collide -- the per-loan
            # label is built from the account name and equals the constant.
            assert f"{colliding.name} paid off" == _DEBT_FREE_MILESTONE_LABEL

            same_label = [
                m for m in milestones
                if m["label"] == _DEBT_FREE_MILESTONE_LABEL
            ]
            assert len(same_label) == 2, (
                "the producer dropped or merged a flag to keep labels unique; "
                "a user's own payoff flag is not a duplicate to be pruned"
            )
            # Two DISTINCT dates, and the later one is the debt-free flag.
            dates = sorted(m["date"] for m in same_label)
            assert dates[0] < dates[1]
            assert dates[1] == payoff
            # The (label, date) pair is what identifies a flag, and it stays
            # unique across every milestone the chart draws.
            pairs = [(m["label"], m["date"]) for m in milestones]
            assert len(set(pairs)) == len(pairs)


class TestTheTileHorizonsFollowTheOwnersCadence:
    """``/savings`` tiles resolve their horizons per owner, not at 6 / 13 / 26.

    Recurrence plan step **R-F17**, ledger row **F-17**.  This surface reads
    the SAME producer the account detail pages do
    (:func:`app.utils.period_projections.project_balance_horizons`), and until
    the offsets were derived it read them at a fixed biweekly table -- so a
    weekly owner's tile put its six-month balance under a "1 year" chip.

    **Written weekly on purpose.**  Every other case in this file is biweekly,
    where the derivation and the constant it replaced answer the same three
    numbers -- so a hardcoded table planted in ``_build_projection_context``
    survives the whole rest of this suite.  Measured: it did, until this class.

    **The no-CURRENT-PERIOD case that stood here was DELETED as duplicate
    coverage** (adversarial test review of this step): it exercised the same
    ``project_balance_horizons`` guard the pure, database-free
    ``TestProjectBalanceHorizons::test_no_current_period_returns_empty``
    already pins, and it survived every mutation it was written for.  The
    no-PAYDAY case below is different and stays -- that one is about the
    cadence READ, not the guard.
    """

    def test_the_one_year_tile_reads_the_owners_own_year(
        self, app, db, seed_user, seed_schedule_at_cadence,
    ):
        """A weekly owner's chips read 13 / 26 / 52 periods out, not 6 / 13 / 26.

        The account is anchored at $1,000.00 and given ONE expense in each of
        the three windows the two rules disagree over -- at offsets +10, +20
        and +40 -- so every horizon holds a DIFFERENT figure under the derived
        offsets than under the biweekly table they replaced:

          offset  balance      derived reads it as   the old table read it as
          +6      $1,000.00    --                    3 months
          +13       $900.00    3 months              6 months
          +26       $700.00    6 months              1 year
          +52       $400.00    1 year                --

        A first draft of this case asserted the equality alone on a flat
        $1,000.00 account, and a planted biweekly table SURVIVED it: three
        chips agreeing because every column held the same dollar.  The three
        expenses are what make the assertion bite, which is why the balances
        are spelled out above rather than left to the map.
        """
        with app.app_context():
            periods = seed_schedule_at_cadence(cadence_days=7, num_periods=120)
            current = current_pay_period(seed_user["user"].id)
            by_index = {derived_span(p).period_index: p for p in periods}
            for offset, amount in ((10, "100.00"), (20, "200.00"), (40, "300.00")):
                make_projected_envelope_expense(
                    db.session, seed_user=seed_user,
                    pay_period=by_index[derived_span(current).period_index + offset],
                    estimated=Decimal(amount), name=f"Bill +{offset}",
                )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            checking = next(
                projection for projection in result["account_data"]
                if projection.account.id == seed_user["account"].id
            )

            assert checking.projected == {
                "3 months": Decimal("900.00"),
                "6 months": Decimal("700.00"),
                "1 year": Decimal("400.00"),
            }

    def test_an_owner_with_no_paydays_is_answered_rather_than_refused(
        self, app, db, seed_user,
    ):
        """No PAYDAYS: the narrow per-account producer still answers.

        **The state that makes ``_build_projection_context``'s guard
        load-bearing**, measured rather than assumed.  An account carries no
        anchor PERIOD any more (rulings R-EH / R-EO deleted both columns), so
        an owner can hold accounts and no pay periods.
        ``compute_account_balance_cell`` publishes no horizon for such an
        owner, and the guard is what keeps the offsets unresolved for a figure
        the fragment never shows.

        **The owner keeps their ``budget.pay_schedule`` row since plan step
        ``pay_calendar:C4-d``** (ruling R-PC45), and the split is the step.
        This case deleted the row as well, so it measured the owner who has
        stated NOTHING -- for whom ``PayCalendar.cadence`` then refused.  That
        owner has no calendar at all now and is refused at ``calendar_for``,
        which is :meth:`test_an_owner_with_NO_SCHEDULE_ROW_is_refused` below.
        What is left here is the state the guard is actually for: paydays gone,
        rhythm stated, nothing to publish a horizon over.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
        from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
            PayCalendarError,
            calendar_for,
        )
        with app.app_context():
            user_id = seed_user["user"].id
            # **The owner must hold no stored cadence, and since plan step
            # ``pay_calendar:C4-b-1`` the fixture writes one** -- the seeded
            # opening payday comes from ``pay_period_write.record_paydays``,
            # which upserts the ``budget.pay_schedule`` row in the same call.
            # So this state is CONSTRUCTED here rather than inherited from a
            # fixture that happened to supply it, which is the stronger form:
            # the assertion that used to stand here could only report that the
            # fixture had changed.  It is still a real owner -- somebody who
            # has never generated a schedule holds neither row -- and the
            # periods go FIRST, which is the order the foreign key plan step
            # ``pay_calendar:C4-b-2`` adds makes the only non-cascading one.
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()

            # The premise, asserted rather than assumed: the owner really has
            # no payday and really still has a rhythm, so the empty answer
            # below is the guard's and not a refusal wearing its clothes.
            calendar = calendar_for(user_id)
            assert calendar.periods == ()
            assert calendar.cadence.cadence.days == 14

            cell = savings_dashboard_service.compute_account_balance_cell(
                BalanceContext.build(user_id), seed_user["account"].id,
            )

            assert cell.projected == {}

    def test_an_owner_with_NO_SCHEDULE_ROW_is_refused(
        self, app, db, seed_user,
    ):
        """The other half of "no paydays", and it is a refusal (plan step C4-d).

        The owner above states a rhythm and has recorded no payday under it.
        This one has stated nothing -- no ``budget.pay_schedule`` row, which
        since ``fk_pay_periods_schedule`` means no paydays either -- and that
        is the companion's shape, production's user 2.

        Before the ruling this owner reached the narrow producer and got an
        empty ``projected`` map, because ``_build_projection_context`` guarded
        the cadence read.  They are refused at ``calendar_for`` now, once, for
        every surface, so the two halves of "this owner has no rhythm" stop
        being answered differently by each page that happens to guard.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
            PayCalendarError,
            calendar_for,
        )
        from tests._test_helpers import strip_owner_schedule  # pylint: disable=import-outside-toplevel
        with app.app_context():
            user_id = seed_user["user"].id
            # Children FIRST: both schedule keys are ON DELETE RESTRICT.
            strip_owner_schedule(db.session, user_id)
            db.session.commit()

            with pytest.raises(PayCalendarError, match="no pay calendar"):
                calendar_for(user_id)
            with pytest.raises(PayCalendarError, match="no pay calendar"):
                savings_dashboard_service.compute_account_balance_cell(
                    BalanceContext.build(user_id), seed_user["account"].id,
                )
