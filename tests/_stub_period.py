"""The salary create form's two pay-period boxes, as a test fills them.

Plan step salary plan#3048 (task plan#3099), ruling plan#3096: a new job asks
for one stub's Pay Date and Period End.  A test that creates a job through the
form copies them off a stub of the developer's employer, whose periods end six
days before payday, so every job a test creates states the gap the migration
gave the developer's.
"""

from datetime import date, timedelta

#: How many days the developer's employer ends a pay period before its payday
#: (his 2026-07-16 stub: Pay Date 07-16, Period End 07-10).
STUB_PERIOD_ENDS_DAYS_BEFORE = 6


def stub_period_on(payday: "date | str") -> dict[str, str]:
    """Return the create form's two boxes for a stub paid on *payday*.

    Args:
        payday: A payday of the test owner's calendar, as a date or an ISO
            string.

    Returns:
        ``{"stub_pay_date": ..., "stub_period_end": ...}``, ISO strings, the
        period ending :data:`STUB_PERIOD_ENDS_DAYS_BEFORE` days before.
    """
    day = date.fromisoformat(payday) if isinstance(payday, str) else payday
    return {
        "stub_pay_date": day.isoformat(),
        "stub_period_end": (day - timedelta(days=STUB_PERIOD_ENDS_DAYS_BEFORE)).isoformat(),
    }
