"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_pay_period_reset.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

from __future__ import annotations

import pytest

from tests._test_helpers import freeze_today

from ._helpers import FROZEN_TODAY


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` to FROZEN_TODAY for every test in this package."""
    freeze_today(monkeypatch, FROZEN_TODAY)
