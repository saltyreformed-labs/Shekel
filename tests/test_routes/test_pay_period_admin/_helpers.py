"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_pay_period_admin.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date

from app.models.pay_period import PayPeriod
from tests._test_helpers import rhythm_of, all_periods, record_paydays_across_a_hole


FROZEN_TODAY = date(2026, 6, 15)


def _future_periods(db_session, seed_user, count=6):
    """Generate `count` future biweekly periods (after the bootstrap).

    Two years past the 2024 opening payday, so the writer would refuse the
    batch for skipping every planned paycheck between (plan step
    ``pay_calendar:C17-c-2a``, ruling R-PC67); the hole is written through
    the test tree's own helper for exactly that state.
    """
    periods = record_paydays_across_a_hole(
        seed_user["user"].id, date(2026, 7, 3), count, rhythm_of(14),
    )
    db_session.commit()
    return periods


def _spanning_periods(db_session, seed_user, count=6):
    """Generate `count` biweekly periods from 06-05, the paycheck holding FROZEN_TODAY.

    For a REGENERATE: the 06-05 paycheck has started and is kept, so the
    plan's next payday is 06-19 and a rebuild may open in ``[06-19, 07-03)``
    -- where a rebuild over :func:`_future_periods`' block would keep only
    the 2024 opening payday and be refused for skipping two years.
    """
    periods = record_paydays_across_a_hole(
        seed_user["user"].id, date(2026, 6, 5), count, rhythm_of(14),
    )
    db_session.commit()
    return periods


def _period_count(db_session, user_id):
    """Count the user's pay periods."""
    return db_session.query(PayPeriod).filter_by(user_id=user_id).count()


def _starts(user_id):
    """The user's current period paydays.

    What the truncate assertions read since plan step C3-a: the operation is
    now defined on the payday rather than on the ordinal, and the payday is the
    column that survives plan step C4.
    """
    return {p.start_date for p in all_periods(user_id)}
