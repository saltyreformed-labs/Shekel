"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_integration/test_cross_page_balance_equality.py``, moved verbatim by
``tools/split``.  A fixture here reaches exactly the tests that module held, at the scope it
declared.
"""

from datetime import date

import pytest

from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_mid_month(monkeypatch):
    """Run this module MID-MONTH, because its periods are calendar months.

    **Found 2026-07-31 with six of this module's tests red on that day, at
    ``HEAD`` and at every commit for weeks, while CI had last been green on
    2026-07-28.**  ``_build_cross_page_calendar_periods`` builds one period per
    calendar month and the anchor is the month containing today, so on the LAST
    day of any month ``date.today() == anchor.end_date`` -- and every
    hand-computed ``expected_balance`` in ``_CASES`` assumes today is inside its
    period, not on its final day.  The suite was a time bomb that fired roughly
    12 days a year and blocked the merge gate on each of them.

    **The production behaviour it trips over was MEASURED CORRECT and is NOT
    changed**, which is the only reason freezing the clock here is a fix and not
    a cover-up.  Ruling R-G lands a still-projected row on
    ``max(attribution_day, as_of + 1)`` -- a plan cannot have already happened
    -- so on a period's last day ``as_of + 1`` is outside the period and the
    whole remaining plan legitimately rolls into the next one.  The column then
    equals the settled balance, and the money is conserved: measured on the
    prod-shape clone, the current column went ``$406.92 -> $2,824.26`` on the
    period's last day while every later column and the horizon's last stayed
    put.  Relaxing the floor to ``as_of`` was tried and REJECTED by measurement:
    it moves the real Checking account's balance TODAY from ``$2,824.26`` to
    ``$2,978.28``, counting income merely expected today as money in hand, and
    ``$2,824.26`` is the figure verified to the cent against the persisted
    double-entry ledger.

    The 15th rather than a pinned calendar date (the
    ``tests/test_services/conftest.py`` precedent freezes to ``2026-03-20``):
    the property this module needs is "strictly inside the month", and deriving
    it from the real month keeps the fixture's year range current instead of
    ageing into a second kind of rot.
    """
    freeze_today(monkeypatch, date.today().replace(day=15))
