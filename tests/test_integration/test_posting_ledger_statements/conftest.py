"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_integration/test_posting_ledger_statements.py``, moved verbatim by ``tools/split``.
A fixture here reaches exactly the tests that module held, at the scope it declared.
"""

from __future__ import annotations

import pytest

from tests._test_helpers import freeze_today

from ._helpers import _FIXTURE_TODAY


@pytest.fixture(autouse=True)
def _today_after_the_fixture_year(monkeypatch):
    """Freeze today to :data:`_FIXTURE_TODAY` for every test in this module.

    A fixture's calendar must contain its own today, and this one has three
    instants that must stay in order: the opening, the settles, and now.  These
    suites pin settles into :data:`_Y` and open accounts explicitly at
    :data:`_FIXTURE_OPENING`, so freezing now at the end of the same year puts
    all three in the production order -- an account existed, then money moved,
    and today is after both.

    Without it the clock sits in the real present while the calendar sits in
    2099, which is the fixture-clock defect class findings N-131, N-132 and R8
    were all instances of, and which ruling R-EJ's write-door guard turns from
    silent into loud.
    """
    freeze_today(monkeypatch, _FIXTURE_TODAY)
