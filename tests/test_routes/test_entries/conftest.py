"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_routes/test_entries.py``, moved verbatim by ``tools/split``.  A fixture here
reaches exactly the tests that module held, at the scope it declared.
"""

from datetime import date

import pytest

from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Entry tests use hardcoded purchase dates such as
    date(2026, 1, 5) and date(2026, 4, 12) that must fall inside the
    calendar-anchored seed_periods range.  Freezing today inside the
    seeded range keeps "which paycheck contains today" deterministic without
    disturbing those calendar values.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
