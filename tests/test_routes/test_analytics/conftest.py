"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_routes/test_analytics.py``, moved verbatim by ``tools/split``.  A fixture here
reaches exactly the tests that module held, at the scope it declared.
"""

from datetime import date

import pytest

from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    This module relies on calendar-anchored seed_periods (Jan-May 2026)
    and asserts on tax_year=2026, calendar 2026 due_dates, and other
    calendar-anchored values.  Auto-discovery patches every loaded
    module's ``date``/``datetime`` symbols so production code
    (e.g. the statement routes in app.routes.analytics) consuming
    ``date.today()`` agrees with the test's view of "today" regardless
    of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
