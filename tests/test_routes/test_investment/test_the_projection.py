"""The growth projection's seed and window: the current period's contribution applied once, the
annual-limit YTD seed covering exactly the periods outside the window, the window opening at the
seed on a lapsed schedule, and the projection continuing the history line.
"""

import pathlib
from datetime import timedelta
from decimal import Decimal

from app.services import balance_at, growth_engine, investment_dashboard_service
from app.services.balance_at import BalanceContext
from app.services.investment_dashboard_service import _context as investment_context
from app.services.investment_dashboard_service._context import (
    _load_investment_params,
    _load_projection_context,
    _projection_ytd,
)
from app.services.investment_projection import InvestmentInputs
from app.utils.money import round_money
from tests._test_helpers import (
    current_pay_period,
    last_covered_day,
    reassert_balance_on,
    settle_instant_on,
)

from ._helpers import (
    _create_investment_account,
    _create_investment_params,
    _make_projected_shadow_income,
)


class TestTheProjectionAppliesEachContributionOnce:
    """deep-quality-hunt #9: the investment dashboard projection applies the
    current period's transfer contribution exactly once.

    Pre-fix the projection seeded the END-of-current-period balance (which
    already contains the current period's contribution) while also
    including the current period in the projection window, so the growth
    engine re-applied the contribution -- double-counting it on the first
    row and the whole forward curve.  The fix seeds the end-of-current
    balance with only the current period's own transfer contribution
    removed.
    """

    def test_current_period_transfer_applied_once_in_projection(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A $1,000 current-period transfer is applied once, not twice.

        Setup (0% return isolates the contribution from growth):
          401(k) anchored at the current period at $10,000.
          $1,000 PROJECTED shadow contribution in the current period.

        The modelled end-of-current balance = 10,000 + 1,000 = $11,000 (the
        displayed tile, and the history line's last point).  The projection
        seeds from exactly that and opens the day after, so the $1,000 the user
        recorded is in the seed and the $1,000 the engine adds is the NEXT
        period's modelled contribution.

        C2 (Loop B P1, developer-approved behavior change): the dashboard
        chart renders on the SAME synthetic-period basis as the HTMX fragment
        and the ``projection`` context key was dropped, so this is verified
        through the chart's own points rather than a per-row
        ``ProjectedBalance`` list.  With 0% return the first synthetic period
        accrues no growth, and the $1,000 transfer averaged over its single
        period gives a $1,000 periodic contribution, so:
          chart_balances[0] = 10,000 seed + 0 growth + 1,000 = 11,000.
        The pre-fix double-count (seed 11,000, then re-add 1,000) would land
        the first projected point at 12,000 -- which this test forbids.  The
        headline tile (11,000) is unchanged by the basis change.
        """
        from app.services.investment_dashboard_service import (  # pylint: disable=import-outside-toplevel
            compute_dashboard_data,
        )

        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Double-count 401k", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("100000.00"),
        )
        current_period = current_pay_period(
            seed_user["user"].id,
        )
        assert current_period is not None
        _make_projected_shadow_income(
            seed_user, acct, current_period, Decimal("1000.00"), db.session,
        )

        data = compute_dashboard_data(seed_user["user"].id, acct)

        # Displayed tile = the modelled end-of-current balance.
        assert data["current_balance"] == Decimal("11000.00")

        # The two lines MEET: the history line's last point is the balance the
        # projection seeds from, so there is no step at the Today marker for a
        # caption to explain (ruling R-AF).
        history = data["history_balances"]
        chart_balances = data["chart_balances"]
        assert history, "the chart should render modelled history"
        assert chart_balances, "chart should project the horizon forward"
        assert Decimal(history[-1]) == Decimal("11000.00")

        # ...and then steps by EXACTLY one modelled period's contribution.
        # A double count lands here at 13,000; the retired subtraction ported
        # onto this axis lands the history/seed junction at 10,000 instead.
        assert Decimal(chart_balances[0]) == Decimal("12000.00")
        assert (
            Decimal(chart_balances[0]) - Decimal(history[-1])
        ) == Decimal("1000.00")


class TestTheAnnualLimitSeedFollowsTheWindow:
    """The engine's YTD seed holds exactly the periods OUTSIDE the window.

    The annual contribution limit is consumed by the contributions the growth
    engine does NOT project plus the ones it does, so its
    ``ytd_contributions_start`` must cover exactly the periods the window
    excludes -- no more, no less.

    **Ruling R-AF moved that boundary and the seed did not follow it.**  The
    axis used to open at ``date.today()``, so its first synthetic period stood
    in for the rest of the current pay period and the engine applied that
    period's contribution itself; the seed therefore excluded it
    (``ytd_contributions_seed``, deep-quality-hunt #10).  The axis now opens the
    day AFTER the current period ends, so the engine never applies it and the
    seed must INCLUDE it.  Keeping the old field reports room that is already
    spent: on a $23,500 limit with $1,000 a period and today in the year's 15th
    period, the engine prices $9,500 of remaining room where $8,500 is left,
    projects one extra contribution inside the calendar year, and compounds it
    for the whole horizon.  It also disagrees with the limit CARD on the same
    page, which has always counted the current period.

    The rule is one pure function, so it is pinned as one: an integration
    fixture would have to drive a real contribution feed all the way to the
    annual cap to see the difference, and would then be pinning the growth
    engine's capping rather than this boundary.  The wiring is pinned
    separately below.
    """

    @staticmethod
    def _inputs(seed, through):
        """Return an ``InvestmentInputs`` carrying the two YTD totals."""
        return InvestmentInputs(
            periodic_contribution=Decimal("1000.00"),
            employer_params=None,
            annual_contribution_limit=Decimal("23500.00"),
            ytd_contributions=Decimal(through),
            ytd_contributions_seed=Decimal(seed),
        )

    def test_the_chart_always_seeds_the_through_current_total(self):
        """The current period is outside this surface's window, so it counts.

        $14,000 strictly before the current period and $15,000 through it.  The
        window opens the day AFTER the period covering the clock ends, so the
        engine will never apply that period's $1,000 -- the seed must, or the
        year has $1,000 of room that is already spent.

        **This took a window date and a current period until plan step C2-e**,
        and branched on whether the window contained that period.  Both
        arguments are gone: :func:`._context._projection_start` derives the
        window's opening day from the CALENDAR as the day after the span
        covering the clock ends, so the answer is structurally the same one on
        every input.  The two tests that graded the other arm are below,
        re-pointed at the surfaces that still have one.
        """
        assert _projection_ytd(
            self._inputs("14000.00", "15000.00"),
        ) == Decimal("15000.00")

    def test_the_two_surfaces_take_DIFFERENT_ytd_fields_by_construction(self):
        """What replaced the branch: two call sites, one field each.

        ``retirement_projection``'s axis opens at or INSIDE the period covering
        the clock, so its engine walks that period and charges its contribution
        against the limit as it applies it -- seeding the through-current total
        there would charge it twice (deep-quality-hunt #10).  The chart's window
        opens after it, so it must.  The distinction is now WHICH FIELD each
        call site reads, which is a property of the source rather than of a
        date comparison that can be got wrong.
        """
        source = pathlib.Path(
            "app/services/retirement_projection.py",
        ).read_text(encoding="utf-8")
        assert "ytd_contributions_start=inputs.ytd_contributions_seed" in source
        assert "ytd_contributions_start=inputs.ytd_contributions," not in source

    def test_the_two_ytd_totals_COINCIDE_with_no_current_period(self):
        """Why the branch's other arm was a no-op, and could be deleted.

        The deleted branch answered ``ytd_contributions_seed`` when there was no
        current period.  ``investment_projection`` returns ZERO for BOTH totals
        in that state, so both arms returned the same figure -- a branch that
        cannot change the answer, which CLAUDE.md rule 1 forbids shipping.  This
        pins the fact the deletion rests on, in the module that owns it.

        The two totals became ONE function over an ``inclusive`` flag at plan
        step C2-f2c, which is what they always were; the fact this test rests
        on is unchanged, and the no-current-period arm is the branch that
        answers ZERO before either bound is consulted.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.investment_projection._inputs import (
            _ytd_contributions,
        )
        assert _ytd_contributions([], None, inclusive=True) == Decimal("0")
        assert _ytd_contributions([], None, inclusive=False) == Decimal("0")

    def test_the_chart_reads_the_resolved_ytd(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The wiring: a real context resolves to the through-current total.

        A $1,000.00 contribution recorded IN the current period makes the two
        YTD fields differ, so this cannot pass by them coinciding.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Limit 401k", balance="10000.00",
        )
        _create_investment_params(db.session, acct.id)
        current = current_pay_period(seed_user["user"].id)
        _make_projected_shadow_income(
            seed_user, acct, current, Decimal("1000.00"), db.session,
        )

        ctx = investment_context._load_projection_context(
            seed_user["user"].id, acct,
            investment_context._load_investment_params(acct.id),
        )
        # The two fields differ, and the resolved one is the through-current.
        assert ctx.inputs.ytd_contributions == Decimal("1000.00")
        assert ctx.inputs.ytd_contributions_seed == Decimal("0.00")
        assert ctx.projection_ytd == Decimal("1000.00")
        # ...because the window opens past the current period (ruling R-AF).
        assert ctx.projection_start > last_covered_day(current)


class TestTheProjectionMeetsItsSeedOnALapsedSchedule:
    """Plan step **C2-e**: the window opens where the seed stops, ALWAYS.

    Ruling R-AF put the /investment chart's axis on the day after the history
    line's last valued point, and the seed on that same last day, so the two
    lines meet.  That held by arithmetic while the axis was FABRICATED -- the
    deleted producer built its first period AT the date it was handed.  A pay
    calendar does not: it answers the period COVERING a day, which opened on a
    payday.

    **So the state this grades is an owner whose generated schedule has run
    out**, which nothing forces them to fix and which the balance arc's X-ad-b
    and X-x steps exist because of.  No period CONTAINS today there, the old
    window opened at today, and the axis would have opened on the
    last payday up to a cadence earlier -- the engine re-growing days
    ``balance_at`` had already grown.  An adversarial code review of this step
    measured it at **$57.24** on a $102,686.18 balance, compounded over the
    whole slider horizon.
    """

    def test_the_window_opens_on_the_axis_head_when_no_period_covers_today(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The schedule ended months ago; the chart still meets its seed.

        ``seed_periods`` opens 2026-01-02 and runs ten biweekly periods, so it
        closes 2026-05-21 -- behind the suite's clock.  There is no current
        period, and every axis period is a PROJECTION at the owner's cadence.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            assert current_pay_period(user_id) is None, (
                "this test needs a LAPSED schedule; the fixture changed"
            )
            acct = _create_investment_account(
                seed_user, db.session, name="Lapsed 401k", balance="100000.00",
            )
            _create_investment_params(db.session, acct.id)
            db.session.commit()

            ctx = _load_projection_context(
                user_id, acct, _load_investment_params(acct.id),
            )
            # The feed PRESERVES the "no current period" answer (plan step
            # C2-f2c, ledger row P19).  It resolves the period from the pass's
            # own calendar now, and the TOTAL search beside the one it uses
            # would answer here with a PROJECTED period carrying
            # ``period_id = None`` -- which ``_resolve_current_balance`` would
            # then use to index a map keyed by ``budget.pay_periods.id``.
            assert ctx.current_period is None
            axis = ctx.balance_ctx.calendar().projection_axis(
                ctx.projection_start, ctx.projection_start + timedelta(days=365),
            )
            # The property the whole class exists for: the seed is valued the
            # day BEFORE the window opens, so no day is grown twice and none is
            # skipped.
            assert axis[0].start_date == ctx.projection_start
            assert ctx.projection_seed == balance_at.balance_at(
                acct, ctx.balance_ctx,
                axis[0].start_date - timedelta(days=1),
            )
            # ... and the axis head is a PROJECTION, which is what makes this
            # the branch the fabricated producer used to cover by accident.
            assert axis[0].period_id is None

    def test_the_first_plotted_point_is_the_seed_grown_one_axis_period(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The money form of the same property, hand-checked against the span.

        With no contributions configured the first chart point is exactly the
        seed compounded over the head period's own inclusive span -- nothing
        else is in the row.  Under the defect it was the seed compounded over a
        span that started before the seed's own date.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            acct = _create_investment_account(
                seed_user, db.session, name="Lapsed Roth", balance="100000.00",
            )
            _create_investment_params(db.session, acct.id)
            db.session.commit()

            chart = investment_dashboard_service.compute_growth_chart_data(
                user_id, acct, 1, None,
            )
            ctx = _load_projection_context(
                user_id, acct, _load_investment_params(acct.id),
            )
            head = ctx.balance_ctx.calendar().projection_axis(
                ctx.projection_start, ctx.projection_start + timedelta(days=365),
            )[0]
            # 7.0% is ``_create_investment_params``' default assumed return.
            rate = growth_engine.span_return_rate(
                Decimal("0.07000"), head.start_date, head.end_date,
            )
            assert Decimal(chart["chart_balances"][0]) == (
                ctx.projection_seed + round_money(ctx.projection_seed * rate)
            )


class TestTheProjectionContinuesTheHistory:
    """Rulings R-AE / R-AF: the chart's two lines MEET, and nothing is filtered.

    The chart draws modelled HISTORY through the current pay period and then a
    PROJECTION.  Two rulings decide whether they join, and they were written
    apart: R-U said the projection's seed has modelled growth filtered out (so
    the engine could not re-grow a period the seed already grew) and R-AB said
    the seed is read the day BEFORE the window opens (so the engine cannot
    re-apply a contribution the seed already holds).  The date makes the filter
    unnecessary -- the window and the seed's past are disjoint -- and applying
    both starts the projection line BELOW the history line by every cent the
    account earned since its last balance assertion: measured $161.31 /
    $109.10 / $292.11 on the three real accounts (finding N-80).

    So the seed is the account's ordinary modelled balance on that day, which
    is exactly the history line's last point.
    """

    def test_the_projection_opens_where_the_history_closes(
        self, seed_user, db, seed_periods_today,
    ):
        """The first projected point is the history's last, compounded once.

        The identity is exact and it is what fires on BOTH regressions: restore
        the ACCRUAL filter and the seed drops to the un-grown basis, and open
        the axis at today instead of the day after the history ends and the
        first point carries a different span.  A bare "the projection rises"
        assertion catches neither -- it is true of every configuration.

        The opening assertion is restamped into the PAST so the account has
        modelled growth to lose (``account_service.create_account`` anchors at
        the current period and stamps the row with the database clock, which the
        suite's frozen today does not reach -- findings N-65 / N-77).
        """
        acct = _create_investment_account(
            seed_user, db.session, name="Grown 401k", balance="50000.00",
        )
        _create_investment_params(db.session, acct.id)
        # Anchor a full period back, so the history line has grown by the time
        # it reaches the current period's end.
        reassert_balance_on(
            db.session, acct,
            settle_instant_on(seed_periods_today[0].start_date),
        )
        db.session.commit()

        data = investment_dashboard_service.compute_dashboard_data(
            seed_user["user"].id, acct,
        )
        history = data["history_balances"]
        assert history, "the chart should render modelled history"
        # It HAS grown -- otherwise the ACCRUAL filter would be undetectable.
        assert Decimal(history[-1]) > Decimal("50000.00")

        bctx = BalanceContext.build(seed_user["user"].id)
        current = current_pay_period(seed_user["user"].id)
        seed = balance_at.balance_at(acct, bctx, last_covered_day(current))
        # The seed IS the history line's last point (ruling R-AE) ...
        assert Decimal(history[-1]) == seed
        # ... and the first projected point is that seed compounded over ONE
        # period of the axis that opens the next day (ruling R-AF), read off
        # the same door the chart resolves its axis through (plan step C2-e).
        # 7.0% is ``_create_investment_params``' default assumed return.
        axis_head = bctx.calendar().projection_axis(
            last_covered_day(current) + timedelta(days=1),
            last_covered_day(current) + timedelta(days=365),
        )[0]
        rate = growth_engine.span_return_rate(
            Decimal("0.07000"), axis_head.start_date, axis_head.end_date,
        )
        assert Decimal(data["chart_balances"][0]) == seed + round_money(
            seed * rate,
        )
