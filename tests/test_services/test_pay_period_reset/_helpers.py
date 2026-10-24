"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_pay_period_reset.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date

from app.routes._period_population import populate_new_periods
from app.services import pay_period_admin
from tests._test_helpers import record_paydays_across_a_hole, rhythm_of


# Pinned "today".  The rebuilt schedules in this package's parts start
# 2026-06-05 at a 14-day cadence, so index 0 (06-05..06-18) contains today,
# the day an account's opening assertion is filed on when its setup names
# none (``anchor_service.resolve_observation_day``).
FROZEN_TODAY = date(2026, 6, 15)


_NEW_START = date(2026, 6, 5)


def _seed_old_schedule(db_session, seed_user, count=5):
    """Append a stale schedule (indices 1..count) after the bootstrap.

    seed_user already has the 2024 bootstrap period (index 0) that its
    Checking account anchors to; these are the extra periods the reset
    will wipe alongside it.
    """
    record_paydays_across_a_hole(
        user_id=seed_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=count,
        rhythm=rhythm_of(14),
    )
    db_session.commit()


def _reset_and_populate(user_id, **kwargs):
    """Run BOTH halves of a reset, exactly as the route does.

    ``reset_pay_periods`` wipes, rebuilds and re-syncs both posting families;
    ``populate_new_periods`` opens the generate pass afterwards and fills the
    rebuilt schedule.  Ruling **R-R38**: the pass may only be opened above the
    service layer, and only after the write.

    Args:
        user_id: The owning user's id.
        **kwargs: Forwarded to
            :func:`~app.services.pay_period_admin.reset_pay_periods`.

    Returns:
        The rebuilt periods, now populated.
    """
    new_periods = pay_period_admin.reset_pay_periods(user_id, **kwargs)
    populate_new_periods(user_id, new_periods)
    return new_periods
