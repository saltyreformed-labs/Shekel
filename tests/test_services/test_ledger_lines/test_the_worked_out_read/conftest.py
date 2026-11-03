"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_ledger_lines/test_the_worked_out_read.py``, moved verbatim by
``tools/split``.  A fixture here reaches exactly the tests that module held, at the scope it
declared.
"""

import pytest

from tests._test_helpers import freeze_today

from ._helpers import _TODAY


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """Freeze the app's today at :data:`_TODAY` for every case."""
    freeze_today(monkeypatch, _TODAY)
