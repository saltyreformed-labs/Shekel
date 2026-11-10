"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_bank_feed.py``, moved verbatim by ``tools/split``.  A fixture here
reaches exactly the tests that module held, at the scope it declared.
"""

import pytest

from ._helpers import _Bridge, block_real_requests


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch):
    """Every test in this module runs behind :func:`block_real_requests`."""
    block_real_requests(monkeypatch)


@pytest.fixture
def bridge(monkeypatch):
    """Bridge, faked and spied."""
    return _Bridge(monkeypatch)
