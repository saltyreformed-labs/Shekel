"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_routes/test_savings.py``, moved verbatim by ``tools/split``.  A fixture here
reaches exactly the tests that module held, at the scope it declared.
"""

from datetime import date

import pytest

from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Savings tests use seed_periods[7] (loan-related), an
    origination_date=date(2026, 1, 1) that aligns specific seed_periods
    indices to specific amortization months, and inline ``date.today()``
    calls (e.g. ``start = date.today() - timedelta(days=14)``).
    Auto-discovery patches every loaded module so test, fixture, and
    production services all see the same frozen "today" regardless of
    wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
