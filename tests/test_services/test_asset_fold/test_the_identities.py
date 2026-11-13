"""An account modelling nothing is its cash fold; each period reconciles; growth splits."""

from datetime import date, timedelta
from decimal import Decimal

from app.services.balance_at import _asset_fold, _cash_fold, _cash_periods
from tests._test_helpers import (
    create_savings_account,
    create_settled_transfer,
    derived_span,
    last_covered_day,
    make_salary_profile,
    period_window,
)

from ._helpers import (
    _ctx,
    _inputs,
    _fold,
    _view,
    _feed_for,
    _params_for,
    _401k,
    _salaried_deduction,
)


def _growth(account, ctx, as_of, *, params=None, feed=None):
    """Return *account*'s ``(accrual, contribution)`` through *as_of*."""
    return _asset_fold.asset_growth_at(
        account, ctx, as_of, _inputs(params, feed),
    )


class TestAnAccountThatModelsNothingIsItsCashFold:
    """No parameters, no modelled tier -- and the fold stays TOTAL.

    An INTEREST-kinded account with no ``InterestParams`` is an HYSA the user
    has not configured; an INVESTMENT with no ``InvestmentParams`` is the state
    the kernel's dispatcher already falls through on; a Property with no
    appreciation row is one whose rate is unset.  Inventing a rate for any of
    them would put growth on a screen the account has never earned.
    """

    def test_a_plain_savings_account_reads_its_cash_fold(
        self, db, seed_user, seed_periods,
    ):
        """A PLAIN account has no modelled tier at all, and folds as cash."""
        account = create_savings_account(
            seed_user, db.session, "Plain", Decimal("4000.00"),
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        assert [
            column.balance
            for column in _view(account, ctx, seed_periods).values()
        ] == list(
            _cash_fold.period_balances(
                _cash_fold.assembled_fold(account, ctx),
                period_window(seed_periods),
            ).values()
        )

    def test_an_investment_without_params_reads_its_cash_fold(
        self, seed_user, seed_periods,
    ):
        """The dispatcher's own no-params state: $20,000 flat, no growth."""
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods[:2], params=None)
        assert columns[seed_periods[0].id].balance == Decimal("20000.00")
        assert columns[seed_periods[1].id].accrual == Decimal("0.00")


class TestThePerPeriodIdentity:
    """``balance delta == net + period_timing + book_vs_bank + accrual + contribution``.

    Ruling R-K's identity, extended by ruling R-W to the modelled kinds -- what
    plan step X-g3 renders as the grid's "Growth" row.  It holds BY
    CONSTRUCTION: all five terms are readings of ONE resolved step list, and
    every step is a whole cent.  Every component is computed INDEPENDENTLY here,
    never as a residual proving itself (plan Section 7.2).

    **The cash remainder is TWO terms since plan step S1-c** (ruling R-DH (f)):
    ``period_timing`` (money budgeted to one column that moved in another) and
    ``book_vs_bank`` (what the user's own balance readings booked).  They are
    summed here rather than through a combined accessor, because no such
    accessor survives -- leaving one would invite a surface to render the sum
    again, which is the figure the ruling exists to delete.
    """

    def test_it_holds_over_every_period_of_a_mixed_shape(
        self, db, seed_user, seed_periods,
    ):
        """Settled money, a still-projected row, an accrual and a contribution.

        The shape that can break it: a settled transfer whose cash clock differs
        from its budget column, a projected expense the reader clamps forward, a
        modelled contribution on a payday, and a daily accrual over all of it.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="flat_percentage",
        )
        params = _params_for(account)
        params.employer_flat_percentage = Decimal("0.0500")
        # The job that funds the employer contribution (R-SAL5): without it
        # the 2026-09-04 ruling models no employer money and this shape would
        # be missing the contribution it is here to mix in.
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3019.23"),  # $78,500.00 a year / 26, made up
        )
        db.session.flush()
        params.salary_profile_id = profile.id
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[2], amount=Decimal("750.00"),
            settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        ctx = _ctx(seed_user, as_of=date(2026, 3, 1))

        feed = _feed_for(seed_user, account, params)
        columns = _view(account, ctx, seed_periods, params=params, feed=feed)
        cash = _cash_periods.period_view_of(
            _cash_fold.assembled_fold(account, ctx),
            period_window(seed_periods),
        )
        openings = _fold(
            account, ctx,
            [period.start_date - timedelta(days=1) for period in seed_periods],
            params=params, feed=feed,
        )
        for period in seed_periods:
            column, cash_column = columns[period.id], cash.columns[period.id]
            assert (
                column.balance - openings[period.start_date - timedelta(days=1)]
            ) == (
                cash_column.net
                + cash_column.period_timing + cash_column.book_vs_bank
                + column.accrual + column.contribution
            ), f"identity broke on period {derived_span(period).period_index}"


# ``TestTheSeedFiltersTheModelledReturn`` stood here until plan step X-g2b.
# It graded ``asset_seed_at`` -- ruling R-U's ACCRUAL-filtered read -- and both
# went with ruling **R-AE**, which found that filter to be a SECOND correction
# for an overlap ruling R-AB's date had already removed: applied together they
# start a chart's projection line BELOW its own history line by every cent
# earned since the account's last assertion (up to $292.11 on the real Empower
# 401(k), finding N-80).  What replaced the rule is pinned where the seed is
# now read -- ``tests/test_routes/test_investment/test_the_projection.py``'s
# ``TestTheProjectionContinuesTheHistory`` (the seed IS the history line's last
# point), ``TestTheProjectionAppliesEachContributionOnce`` (applied once),
# and ``test_retirement_dashboard_service.py``'s three-way seed discrimination.
class TestTheGrowthDecomposition:
    """``asset_growth_at``: what the market did and what the user put in.

    The investment detail page's growth chip, read off the replay's own two
    modelled tiers instead of being re-projected by the growth engine.  "Since
    the latest assertion" needs no window arithmetic and that is the point: an
    ACCRUAL exists only from the assertion's own day (rulings R-L / R-Y) and a
    CONTRIBUTION only strictly after it (ruling R-Z), so the cumulative total at
    a date IS the total since the anchor.
    """

    def test_the_two_tiers_and_the_assertion_explain_the_whole_balance(
        self, seed_user, seed_periods,
    ):
        """$20,000 at 7% with a $500.00 deduction: (55.78, 500.00) on 2026-01-16.

        Hand-computed, and it closes on itself: the 14 days of period 0 accrue
        **$51.97** (this file's own figure), the 01-16 payday lands **$500.00**,
        and that day's growth on the raised $20,551.97 base is **$3.81** -- so
        the cumulative accrual is $55.78 and ``20000.00 + 55.78 + 500.00 ==
        20555.78``, the balance the fold independently reports for that day.
        The chip's two numbers and the balance beside it therefore reconcile by
        construction rather than by a test keeping two producers in step.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        ctx = _ctx(seed_user)
        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)

        accrual, contribution = _growth(
            account, ctx, date(2026, 1, 16),
            params=params, feed=feed,
        )
        assert accrual == Decimal("55.78")
        assert contribution == Decimal("500.00")
        assert Decimal("20000.00") + accrual + contribution == _fold(
            account, ctx, [date(2026, 1, 16)],
            params=params, feed=feed,
        )[date(2026, 1, 16)]

    def test_the_anchor_periods_own_days_are_reported_not_hidden(
        self, seed_user, seed_periods,
    ):
        """Ruling R-Y: an account anchored THIS period has already grown.

        The shipping decomposition returns ``None`` -- and the page hides the
        chip -- when no period follows the anchor, because its forward
        projection starts the period AFTER.  The replay accrues from the
        assertion's own day, so there is a real figure to report on day one:
        hand-computed, $20,000 at 7% credits **$3.71** on 2026-01-02 alone.
        Hiding it would deny a number the balance beside it already contains.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        ctx = _ctx(seed_user)

        assert _growth(
            account, ctx, date(2026, 1, 2), params=_params_for(account),
        ) == (Decimal("3.71"), Decimal("0.00"))

    def test_an_account_that_models_nothing_reports_zeros_not_none(
        self, seed_user, seed_periods,
    ):
        """A plain savings account decomposes to ``(0.00, 0.00)``.

        A real answer rather than a missing one, which is the totality rule the
        whole arc turns on: the caller decides whether a zero chip is worth
        rendering, and no consumer has to compose this producer with a fallback.
        """
        ctx = _ctx(seed_user)

        assert _growth(
            seed_user["account"], ctx, last_covered_day(seed_periods[-1]),
        ) == (Decimal("0.00"), Decimal("0.00"))
