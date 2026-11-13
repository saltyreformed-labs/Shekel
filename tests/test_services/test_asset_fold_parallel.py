"""X-g1 / X-g4b: the modelled replay against an INDEPENDENT engine.

Plan steps X-g1 and X-g4b (``docs/audits/balance_architecture/README.md``).

**This file was a PARALLEL RUN and is no longer one** (ruling R-AU).  It ran the
modelled replay beside the three producers plan step X-g2b replaced and
CLASSIFIED every divergence into three named classes -- the reverse projection
contradicting the user's own assertions (findings N-43 / N-74), the anchor
period earning nothing (ruling R-Y), and the per-DAY versus per-PERIOD grain
(ruling R-T).  Plan step X-g4b deleted those producers, so three of the five
classes here had no reference left to compare against and went with them; what
they measured is recorded in ``archive/cash_arc_as_built_2026-07-27.md``.

**What SURVIVES is the half that never graded against the incumbent**, and it is
kept as its own file rather than folded into the ``test_asset_fold`` package because it
is a different KIND of evidence (plan Section 7.2's independence rule):

* :class:`TestTheGrainIsARegroupingNotADifferentModel` grades the replay against
  ``growth_engine.project_balance`` -- the pure math engine ruling R-U
  deliberately KEEPS for the what-if surfaces, and which no balance path reads.
  On a shape isolating the grain (assertion on the anchor period's LAST day, no
  contribution feed), the daily replay tracks the per-PERIOD engine to within a
  CENT on every post-anchor period: ``span_return_rate`` over a 14-day span is
  ``(1 + annual) ** (14/365) - 1``, the 14th power of the one-day rate the
  replay applies, so the two compound the SAME curve and differ only where
  cent-rounding lands.  That is what shows the replay is one model read at a
  finer grain rather than a second opinion.
* :class:`TestTheTwoGrainsAreOneRunningTotal` asserts properties of the replay
  ALONE: a scalar at a date, a daily series and the per-period map are three
  readings of ONE resolved step list.

The ``test_asset_fold`` package beside this grades the replay on HAND-COMPUTED figures.
Neither grades it against a shipping producer, which is finding N-7's rule.

No sampling: the every-day class walks all 140 days of the seeded horizon
(a 14-day sample once scored perfect while wrong by $178,103.41 on 22% of days).
"""

from datetime import date, timedelta
from decimal import Decimal

from app.services import growth_engine
from app.services.balance_at import _asset_fold
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.balance_at._context import BalanceContext
from app.services.projection_inputs import load_investment_params_for_accounts
from tests._test_helpers import (
    create_hysa_account,
    last_covered_day,
    make_investment_account,
    period_window,
    reassert_balance_on,
    settle_instant_on,
)

_LATE_AS_OF = date(2026, 12, 31)
_CENT = Decimal("0.01")


def _ctx(seed_user):
    """Return a read-pass context for the seed user, pinned late."""
    return BalanceContext.build(seed_user["user"].id, as_of=_LATE_AS_OF)


def _params_for(account):
    """Return the account's ``InvestmentParams`` through the shared loader."""
    return load_investment_params_for_accounts([account])[account.id]


def _replay(account, ctx, periods, params=None):
    """Return the modelled per-period columns, with no contribution feed."""
    return _asset_fold.asset_period_view(
        account, ctx, ContributionInputs(investment_params=params),
    )


class TestTheGrainIsARegroupingNotADifferentModel:
    """The clean shape: assertion on the anchor period's LAST day, no feed.

    On it the two other divergence classes cannot fire -- there is no period
    before the latest assertion to reverse-project, and the anchor period has
    exactly ONE day inside ruling R-Y's window.  What is left is the grain, and
    the arithmetic says it is a re-grouping: ``span_return_rate`` over a
    14-day span is ``(1 + annual) ** (14/365) - 1``, which is the 14th power of
    the one-day rate the replay applies, so the two compound the SAME curve and
    differ only where cent-rounding lands.
    """

    def test_the_daily_grain_compounds_the_same_curve_as_the_period_rate(
        self, db, seed_user, seed_periods,
    ):
        """Nine post-anchor periods, never more than a cent from the engine.

        $20,000 at 7%, asserted on period 0's LAST day (2026-01-15).  The
        reference is :func:`~app.services.growth_engine.project_balance`
        itself -- the pure math engine ruling R-U KEEPS -- run per PERIOD from
        the replay's own anchor-period balance, so the two share a seed, a rate
        and a period list and the only variable left is the grain.  That is the
        honest isolation: comparing against ``build_investment_balance_map``
        instead would fold in ruling R-Y's dropped anchor-period day
        (measured $3.71 here, pinned by the test below), which is a different
        class.

        Measured on this shape: eight of the nine periods agree EXACTLY and one
        is a cent apart, which is the arithmetic saying what it should --
        ``span_return_rate`` over a 14-day span is ``(1 + annual) **
        (14/365) - 1``, the 14th power of the one-day rate the replay applies,
        so the two compound the same curve and differ only where cent-rounding
        lands.
        """
        account = make_investment_account(
            seed_user, db.session, seed_periods[0], Decimal("20000.00"),
        )
        reassert_balance_on(
            db.session, account,
            settle_instant_on(last_covered_day(seed_periods[0])),
        )
        db.session.commit()
        ctx = _ctx(seed_user)
        params = _params_for(account)

        replayed = _replay(account, ctx, seed_periods, params)
        engine = growth_engine.project_balance(
            current_balance=replayed[seed_periods[0].id].balance,
            assumed_annual_return=params.assumed_annual_return,
            periods=period_window(seed_periods[1:]),
        )
        assert len(engine) == 9
        for row in engine:
            gap = replayed[row.period.period_id].balance - row.end_balance
            assert abs(gap) <= _CENT, (
                f"period {row.period.period_id} diverged by {gap}, which is more "
                "than cent-rounding: the grain is supposed to be a "
                "re-grouping of the same curve"
            )

    def test_the_anchor_periods_one_day_is_the_only_structural_gap(
        self, db, seed_user, seed_periods,
    ):
        """The anchor period earns exactly one day's accrual, and no more.

        Ruling R-Y in its smallest form, hand-computed: an assertion on the
        anchor period's LAST day leaves exactly ONE day inside the accrual
        window, worth ``round(20000 * ((1.07 ** (1/365)) - 1)) = $3.71``.  Every
        wider window is the same rule with more days in it (up to a full period
        on the real Roth IRA, $105.26).

        **It was a comparison against the retired map and is now a statement
        about the replay** (plan step X-g4b).  That map served the anchor period
        from the flat cash base and read $20,000.00, earning nothing at all;
        the equivalent claim without it is that the period's balance is the
        asserted value PLUS one day, which is what the two assertions below say
        jointly -- and it is strictly stronger, because the retired form could
        have passed on a replay that accrued the right amount from the wrong
        base.
        """
        account = make_investment_account(
            seed_user, db.session, seed_periods[0], Decimal("20000.00"),
        )
        reassert_balance_on(
            db.session, account,
            settle_instant_on(last_covered_day(seed_periods[0])),
        )
        db.session.commit()
        ctx = _ctx(seed_user)
        params = _params_for(account)

        column = _replay(account, ctx, seed_periods, params)[
            seed_periods[0].id
        ]
        assert column.accrual == Decimal("3.71")
        assert column.balance - column.accrual == Decimal("20000.00")


class TestTheTwoGrainsAreOneRunningTotal:
    """Every day of the horizon, and the period map read off the same steps.

    The internal half of the parallel run: a scalar at a date, a daily series
    and the per-period map must be three readings of ONE resolved step list, not
    three producers a test keeps in step.  Walked over all 140 days of the
    seeded calendar -- never sampled.
    """

    def test_every_day_of_the_horizon_agrees_with_the_period_map(
        self, db, seed_user, seed_periods,
    ):
        """The scalar at each period's end_date IS that period's column.

        Asserted for every period, and the day-by-day series is walked whole in
        between so a divergence anywhere inside a period would have to survive
        both checks.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)
        first, last = seed_periods[0].start_date, last_covered_day(seed_periods[-1])
        horizon = [
            first + timedelta(days=offset)
            for offset in range((last - first).days + 1)
        ]

        daily = _asset_fold.fold_asset_balances(
            account, ctx, horizon, ContributionInputs(),
        )
        columns = _replay(account, ctx, seed_periods)
        assert len(daily) == 140
        for period in seed_periods:
            assert daily[last_covered_day(period)] == columns[period.id].balance

    def test_the_balance_never_goes_backwards_on_a_pure_accrual_shape(
        self, db, seed_user, seed_periods,
    ):
        """With nothing but accrual, every one of the 140 days is >= the last.

        The cheapest whole-horizon sanity the resolving pass can fail: a carry
        that credited a negative step, or an ordering slip that applied a day's
        accrual before its own base, shows up here on the day it happens rather
        than at a period boundary.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)
        first, last = seed_periods[0].start_date, last_covered_day(seed_periods[-1])
        horizon = [
            first + timedelta(days=offset)
            for offset in range((last - first).days + 1)
        ]

        daily = _asset_fold.fold_asset_balances(
            account, ctx, horizon, ContributionInputs(),
        )
        previous = Decimal("10000.00")
        for day in horizon:
            assert daily[day] >= previous, f"balance fell on {day}"
            previous = daily[day]
