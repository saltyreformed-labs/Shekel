"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_pay_period_write.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

import pytest

from tests._test_helpers import freeze_today

from ._helpers import FROZEN_TODAY


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    """Pin ``date.today()`` for every test in this module."""
    freeze_today(monkeypatch, FROZEN_TODAY)
