"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_confirmed_view.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

import pytest

from tests._test_helpers import freeze_today

from ._helpers import _FROZEN_TODAY


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze today after the seed window so ``_AS_OF`` is always in the past."""
    freeze_today(monkeypatch, _FROZEN_TODAY)
