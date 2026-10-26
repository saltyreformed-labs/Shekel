"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_recurrence_engine.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

import pytest
from datetime import date, timedelta

from ._helpers import FakePeriod


# --- Fixture: 26 Biweekly Periods for 2026 ----------------------------------


@pytest.fixture()
def biweekly_periods():
    """26 biweekly FakePeriod objects for 2026, starting Jan 2."""
    periods = []
    start = date(2026, 1, 2)
    for i in range(26):
        s = start + timedelta(days=14 * i)
        e = s + timedelta(days=13)
        periods.append(FakePeriod(id=i + 1, start_date=s, end_date=e,
                                  period_index=i))
    return periods
