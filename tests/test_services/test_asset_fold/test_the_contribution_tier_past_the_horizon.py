"""The contribution tier runs past the last saved paycheck (plan step C9, plan#1097).

Ruling **plan#3930** supersedes balance ruling **R-AG**: past the owner's last
saved payday the modelled fold kept ACCRUING an investment's return while its
payroll CONTRIBUTION tier stopped, because :func:`._asset_fold.resolve` walked
the calendar's ``saved()`` periods (ledger row **P7**).  It walks the
calendar's projection axis from the first payday through the read's own end
now, so a deduction and an employer contribution land on every projected
payday the accrual beside them already runs over.

**The schedule every case here reads**, the ``seed_periods`` fixture: ten saved
biweekly paychecks from 2026-01-02, so the last saved period is 05-08 .. 05-21
and the saved horizon is **2026-05-21**.  The projected paydays continue at the
same cadence: 05-22, 06-05, 06-19, and on through 2026-12-18 and 2027-01-01
(``2026-01-02 + 26 * 14 days``).  Every account is asserted on 2026-01-02,
period 0's own payday, so that payday is inside the assertion and contributes
nothing (ruling **R-Z**); the nine saved paydays 01-16 .. 05-08 do.

Every figure is the CONTRIBUTION component of
:func:`._asset_fold.asset_growth_at`, hand-computed as a count of paydays times
a flat amount, so no growth arithmetic sits between a case and its oracle.  Each
case fails on the saved-only walk, which reads the horizon's figure at every
later date; the one that cannot is said to be a guard, not a control.
"""

from datetime import date
from decimal import Decimal

from app.services.balance_at import _asset_fold
from tests._test_helpers import make_salary_profile

from ._helpers import (
    _ctx,
    _inputs,
    _feed_for,
    _params_for,
    _401k,
    _salaried_deduction,
)

# The last day the saved schedule covers: period 9 is 2026-05-08 .. 05-21.
_HORIZON = date(2026, 5, 21)


def _contributed_through(account, ctx, as_of, params, feed):
    """Return the modelled contribution credited from the assertion through *as_of*."""
    _accrual, contribution = _asset_fold.asset_growth_at(
        account, ctx, as_of, _inputs(params, feed),
    )
    return contribution


class TestTheContributionTierPastTheHorizon:
    """Ruling plan#3930: a projected payday contributes as a saved one does."""

    def test_a_deduction_keeps_landing_on_every_projected_payday(
        self, seed_user, seed_periods,
    ):
        """$500 a paycheck, saved or projected, is $500 a paycheck.

        Hand-computed.  Through the horizon (2026-05-21) the nine saved paydays
        01-16 .. 05-08 contribute ``9 * $500.00 = $4,500.00``, which the
        saved-only walk also read, so that figure is the control that nothing
        inside the saved schedule moved.  Through 2026-06-30 the three
        projected paydays 05-22, 06-05 and 06-19 add ``3 * $500.00``, so
        ``12 * $500.00 = $6,000.00``.  The saved-only walk read ``$4,500.00``
        there too: the half model ruling R-AG let stand.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        ctx = _ctx(seed_user)
        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)

        assert _contributed_through(
            account, ctx, _HORIZON, params, feed,
        ) == Decimal("4500.00")
        assert _contributed_through(
            account, ctx, date(2026, 6, 30), params, feed,
        ) == Decimal("6000.00")

    def test_the_annual_limit_runs_across_the_horizon_and_resets_past_it(
        self, db, seed_user, seed_periods,
    ):
        """One calendar-year limit over saved AND projected paydays.

        A ``$5,500.00`` annual limit against the $500 deduction, hand-computed
        with :func:`~app.services.growth_engine.cap_contribution_at_limit`'s
        rule (pay ``min(amount, limit - ytd)``, never below zero).  The nine
        saved paydays consume ``$4,500.00``; the first two projected ones
        (05-22, 06-05) take it to ``$5,500.00``; every later 2026 payday,
        06-19 through 12-18, is capped at ``$0.00``.  So through 2026-12-31 the
        year holds exactly ``$5,500.00``.  2027-01-01 is a projected payday in
        a NEW calendar year, the year-to-date total resets, and it pays its
        full ``$500.00``: ``$6,000.00`` through that day.

        The saved-only walk read ``$4,500.00`` at both dates.  A walk that
        projected but restarted its year-to-date total at the horizon, which
        is what a slice opening there would do, would pay every projected 2026
        payday up to the limit again and read ``$10,000.00`` at 2026-12-31.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        params = _params_for(account)
        params.annual_contribution_limit = Decimal("5500.00")
        db.session.commit()
        ctx = _ctx(seed_user)
        feed = _feed_for(seed_user, account, params)

        assert _contributed_through(
            account, ctx, date(2026, 12, 31), params, feed,
        ) == Decimal("5500.00")
        assert _contributed_through(
            account, ctx, date(2027, 1, 1), params, feed,
        ) == Decimal("6000.00")

    def test_an_employer_contribution_is_sized_on_each_projected_paycheck(
        self, db, seed_user, seed_periods,
    ):
        """5% of a projected paycheck's gross, with no deduction at all.

        The real Empower shape the existing tier test grades inside the saved
        schedule, carried past it.  Hand-computed on a made-up ``$3,019.23``
        paycheck: ``round(3019.23 * 0.05) = round(150.9615) = $150.96`` a
        payday.  Through the horizon ``9 * $150.96 = $1,358.64``; through
        2026-06-30 the three projected paydays add three more, so
        ``12 * $150.96 = $1,811.52``.  The gross of a projected paycheck comes
        off the same funding profile through the same paycheck engine (plan
        step salary:S3-e-2), which is why the figure does not change at the
        horizon.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="flat_percentage",
        )
        params = _params_for(account)
        params.employer_flat_percentage = Decimal("0.0500")
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3019.23"),  # $78,500.00 a year / 26, made up
        )
        db.session.flush()
        params.salary_profile_id = profile.id
        db.session.commit()
        ctx = _ctx(seed_user)
        feed = _feed_for(seed_user, account, params)

        assert _contributed_through(
            account, ctx, _HORIZON, params, feed,
        ) == Decimal("1358.64")
        assert _contributed_through(
            account, ctx, date(2026, 6, 30), params, feed,
        ) == Decimal("1811.52")

    def test_a_read_before_the_first_payday_contributes_nothing_and_answers(
        self, seed_user, seed_periods,
    ):
        """A read valued before the schedule opens is answered, with ``$0.00``.

        A GUARD, not a control: the saved-only walk answers ``$0.00`` here
        too.  What it grades is the axis the tier now asks for.
        ``PayCalendar.projection_axis`` REFUSES a crossed range, and a read
        ending before the first payday would cross one opened AT the first
        payday, so the tier opens its axis at ``date.min``, which the
        producer raises to the first payday and then answers empty rather
        than refusing.  The account is asserted on the first payday,
        2026-01-02, and read on 2025-12-31.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        ctx = _ctx(seed_user)
        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)

        assert _contributed_through(
            account, ctx, date(2025, 12, 31), params, feed,
        ) == Decimal("0.00")
