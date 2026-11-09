"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_arch/test_one_read_pass_per_render.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from tests._test_helpers import create_loan_account, make_investment_account, make_salary_profile


#: The DERIVATION itself, which is what "one calendar per render" is about.
#: A read pass MEMOIZES the calendar, so "one pass" and "one derivation" ought
#: to be the same claim -- and they were not.  Measured across pay-calendar plan
#: step C2-f2d-3: ``/savings`` opened ONE pass and derived the owner's schedule
#: SEVEN times, ``/retirement`` SIX, growing with the owner's investment-account
#: count, because two producers reached ``income_service`` through call chains
#: that held a pass and did not hand it over.  The pass counter beside this one
#: read 1 throughout.  Two counts of one question, and only one of them could
#: see it.
#:
#: **It named the LOADER (``calendar_for``) until plan step C4, and that made
#: it a name list rather than a rule.**  ``pay_calendar`` grew a second loader
#: door -- ``calendar_at_schedule``, for the rolling top-up, which runs BEFORE
#: the render opens its pass and so cannot share one -- and every count here
#: stayed at 1 while ``/grid`` and ``/dashboard`` began deriving twice.  A
#: guard that enumerates doors is blind to the next door by construction;
#: ``derive_periods`` is the one function every ``PayCalendar`` runs through
#: (``PayCalendar.__post_init__``), so counting it cannot be walked around.
_CALENDAR_DOOR = ("app.services.pay_calendar._derive", "derive_periods")


def _seed_projecting_account(db, seed_user, seed_periods_today):
    """Give the owner a 401(k) AND a retirement date, so both engines run.

    Necessary for ``/savings`` and not merely tidy: its Horizon skips the
    /retirement engine entirely -- returning zero bands before it builds a
    projection context at all -- when the owner has no retirement or investment
    account.  A count taken on the bare fixture would therefore read ``1`` on
    the tree this leaf FIXED and on the tree it fixed it FROM, which is the
    arm-that-cannot-fail shape ``docs/plans/lessons.md`` records.

    ``/retirement`` does not need it (both its producers load a projection
    batch before any early return), and it is seeded there anyway so the two
    counts are taken over the same state.

    **The retirement DATE is load-bearing and was missing until 2026-08-16.**
    Without one, ``compute_lever_data`` short-circuits on its ``no_horizon``
    arm before deriving anything, so the retire-later search never runs -- and
    a planted defect that made the lever solver bypass the picture memo
    entirely was measured to pass ``test_the_page_derives_no_plan_twice``
    unchanged.  The gate was grading a render whose expensive half never
    executed.  A horizon far enough out that the plan is NOT already funded
    keeps the binary search bisecting rather than answering at offset 0.

    **It varies on ONE axis, and that bounds what the counts above cover**
    (adversarial design review, 2026-08-16).  A future producer reached only by
    an owner who has a loan, an active goal, a credit card or a current period
    sits behind an early return on this fixture, so its pass would go uncounted.
    Widening the owner is the honest way to widen the gate; the claim these
    tests make is over the state seeded HERE, not over every owner.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.user import UserSettings
    from app.utils.dates import add_months

    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=seed_user["user"].id)
        .one()
    )
    settings.planned_retirement_date = add_months(date.today(), 240)
    # **The SALARY PROFILE was load-bearing for this count until plan step
    # salary:R14-b, and the reason is recorded because the reason is what
    # decayed.**  ``income_service.get_current_gross_biweekly`` returned at
    # its profile lookup for an owner who had none, and every producer that
    # reached it did so behind that early return -- so a calendar count taken
    # without a profile read the same number on the tree that derived the
    # owner's schedule SEVEN times and on the tree that derives it once.  That
    # was the arm-that-cannot-fail shape one line up, found the same way: by
    # an adversarial review re-running the measurement over a richer owner.
    # That producer is DELETED, and the seam hands the pass's own calendar to
    # ``projection_inputs.load_payroll_feeds`` whether or not a profile
    # exists, so no early return can hide a derivation now.  The profile stays
    # because a richer owner is still the better subject -- it is what gives
    # the feed a paycheck to price -- but this case no longer DEPENDS on it,
    # and a future reader should not infer that it does.
    make_salary_profile(seed_user, db.session)
    account = make_investment_account(
        seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
    )
    db.session.commit()
    return account


def _seed_dashboard_owner(db, seed_user, seed_periods_today):
    """Widen :func:`_seed_projecting_account`'s owner until ``/`` runs it all.

    **Necessary for the budget dashboard, and the reason is the recorded
    lesson one function up.**  ``/`` runs three producers, and two of them --
    the savings-goal tracks and the debt track -- return an empty answer for an
    owner with no active goal and no loan account.  A count taken on the
    narrower fixture would therefore be taken over a render whose tracks tier
    never executed, which is the arm-that-cannot-fail shape
    ``docs/plans/lessons.md`` records and which cost pay-calendar plan step
    C2-f2d-3 a false measurement.

    Returns:
        The seeded loan :class:`~app.models.account.Account`, so a caller can
        assert the tier it feeds actually rendered.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.savings_goal import SavingsGoal
    from app.utils.dates import add_months, display_today

    _seed_projecting_account(db, seed_user, seed_periods_today)
    loan = create_loan_account(
        seed_user, db.session, name="Dashboard Mortgage",
        principal=Decimal("200000.00"),
    )
    db.session.add(SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        name="Dashboard Emergency Fund",
        target_amount=Decimal("10000.00"),
        target_date=add_months(display_today(), 24),
        is_active=True,
    ))
    db.session.commit()
    return loan
