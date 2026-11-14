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

from app.services.balance_at import _asset_fold, _cash_estimate
from tests._test_helpers import (
    generate_transfer_of,
    make_salary_profile,
    make_transfer_template,
)

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


def _a_recurring_transfer_into(db, seed_user, account, seed_periods, amount):
    """Author a transfer of *amount* from checking into *account* every paycheck.

    Its rows are generated on all ten saved paychecks, as a generate pass
    writes them, so inside the saved schedule the contribution base reads
    each as a recorded row and past it the cash fold estimates the rest.

    Returns:
        The transfer template.
    """
    template = make_transfer_template(
        db.session, seed_user, account, amount=amount,
    )
    for period in seed_periods:
        generate_transfer_of(template, period)
    db.session.commit()
    return template


class TestTheBaseCountsTheEstimatedTransfers:
    """Ruling R-R consequence (a) past the horizon (task plan#3943).

    A recurring transfer into a 401(k) is cash the fold already moves, so it
    is never contributed again; it is read for how much of the year's limit
    is consumed and for the employee total the match sizes off.  Inside the
    saved schedule its rows say so.  Past it no row exists, and the base
    read nothing there until this task, so a projected year's limit and
    match ignored every recurring transfer.  Each case fails on that base.
    """

    def test_the_limit_is_reached_on_the_deduction_and_the_transfer_together(
        self, db, seed_user, seed_periods,
    ):
        """$500 deducted and $300 transferred each payday against a $9,700.00 limit.

        Hand-computed with the cap rule ``min(amount, limit - ytd)``, never
        below zero.  The transfer consumes the limit on every payday,
        01-02 included (it is cash, not modelled, so the assertion's cover
        does not drop it); the deduction is modelled from 01-16.  Through
        the horizon: ``ytd = 300 + 9 * (300 + 500) = $7,500.00``, and the
        nine saved deductions contribute ``9 * $500.00 = $4,500.00``, the
        control that nothing inside the saved schedule moved.  Past it:
        05-22 ``7,800 -> 500``, 06-05 ``8,600 -> 500``, 06-19
        ``9,400 -> min(500, 300) = 300``, and from 07-03 (``ytd`` at
        $10,000.00) every 2026 payday pays $0.00.  Through 2026-12-31 that
        is ``4,500 + 500 + 500 + 300 = $5,800.00``.  2027-01-01 opens a new
        year: ``ytd = 300``, so the deduction pays $500.00 and the total is
        $6,300.00.

        A base blind to the estimated transfers reads ``7,500`` growing by
        $500.00 alone: 05-22 through 07-03 pay $500.00 each and 07-17 pays
        ``min(500, 200) = 200``, so $6,700.00 through 2026-12-31 and
        $7,200.00 through 2027-01-01.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        _a_recurring_transfer_into(
            db, seed_user, account, seed_periods, "300.00",
        )
        params = _params_for(account)
        params.annual_contribution_limit = Decimal("9700.00")
        db.session.commit()
        ctx = _ctx(seed_user)
        feed = _feed_for(seed_user, account, params)

        assert _contributed_through(
            account, ctx, _HORIZON, params, feed,
        ) == Decimal("4500.00")
        assert _contributed_through(
            account, ctx, date(2026, 12, 31), params, feed,
        ) == Decimal("5800.00")
        assert _contributed_through(
            account, ctx, date(2027, 1, 1), params, feed,
        ) == Decimal("6300.00")

    def test_the_match_sizes_off_the_deduction_and_the_transfer_from_one_walk(
        self, db, seed_user, seed_periods, monkeypatch,
    ):
        """A 50% match capped at 6% of $3,019.23 sees $100 deducted and $300 transferred.

        Hand-computed, the existing tier test's arithmetic carried past the
        horizon: the matchable salary is ``round(3019.23 * 0.06) =
        $181.15``, the employee total ``100.00 + 300.00 = $400.00``, the
        match ``round(min(400.00, 181.15) * 0.50) = round(90.575) =
        $90.58``, so each payday's modelled contribution is ``100.00 +
        90.58 = $190.58``, the transfer itself never among it.  Through the
        horizon nine paydays: ``9 * $190.58 = $1,715.22`` (the control).
        Through 2026-06-30 three projected paydays (05-22, 06-05, 06-19)
        more: ``12 * $190.58 = $2,286.96``.  A base blind to the estimate
        matches $100.00 alone there, ``round(100.00 * 0.50) = $50.00``, and
        reads ``1,715.22 + 3 * 150.00 = $2,165.22``.

        Read on a pass dated 2026-03-01, inside the saved schedule, so the
        cash fold estimates the same transfer the base reads: the template
        is walked ONCE for both (``BalanceContext.estimates``).
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="match", match_pct=Decimal("0.5000"),
            match_cap_pct=Decimal("0.0600"),
        )
        _salaried_deduction(seed_user, account, "100.00")
        template = _a_recurring_transfer_into(
            db, seed_user, account, seed_periods, "300.00",
        )
        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)
        walks = []
        real_walk = _cash_estimate.unwritten_occurrences

        def counting_walk(definition, *args, **kwargs):
            """Record which definition is walked, then walk it."""
            walks.append(definition.id)
            return real_walk(definition, *args, **kwargs)

        monkeypatch.setattr(
            _cash_estimate, "unwritten_occurrences", counting_walk,
        )
        as_of = date(2026, 3, 1)

        assert _contributed_through(
            account, _ctx(seed_user, as_of), _HORIZON, params, feed,
        ) == Decimal("1715.22")
        assert walks == []
        assert _contributed_through(
            account, _ctx(seed_user, as_of), date(2026, 6, 30), params, feed,
        ) == Decimal("2286.96")
        assert walks == [template.id]
