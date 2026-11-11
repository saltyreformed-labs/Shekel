"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_routes/test_transfer_update_loan_destination.py``, moved verbatim by
``tools/split``.  A fixture here reaches exactly the tests that module held, at the scope it
declared.
"""

import pytest

from tests._test_helpers import freeze_today

from ._helpers import _TODAY


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today ahead of the loan's origination."""
    freeze_today(monkeypatch, _TODAY)
