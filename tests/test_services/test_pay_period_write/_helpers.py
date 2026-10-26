"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_pay_period_write.py``, moved verbatim by ``tools/split``.
"""

from datetime import date

from app.services.pay_calendar import calendar_for


#: Pinned "today", before every date this module writes.  The lock classifier
#: calls a period whose last covered day is in the past HISTORICAL and refuses
#: to delete it, so the truncate tests below would refuse on the wall clock
#: rather than on what they are grading.
FROZEN_TODAY = date(2025, 12, 1)


def _paydays(user_id):
    """Return the owner's ``(payday, last covered day, ordinal)`` rows in order.

    **The last two are DERIVED and were columns until plan step
    ``pay_calendar:C4-c``.**  Every assertion in this module that names a
    three-tuple therefore says what the owner's CALENDAR looks like after the
    write under test, which is what the writer's callers actually read: the
    writer stores a payday and ``pay_calendar.derive_periods`` answers the
    rest.  The tuple shape is unchanged, deliberately -- the figures these
    cases were hand-computed against did not move, and dropping the columns is
    only correct if they do not.
    """
    return [
        (period.start_date, period.end_date, period.period_index)
        for period in calendar_for(user_id).saved()
    ]
