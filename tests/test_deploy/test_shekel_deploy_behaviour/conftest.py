"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_deploy/test_shekel_deploy_behaviour.py``, moved verbatim by ``tools/split``.  A
fixture here reaches exactly the tests that module held, at the scope it declared.
"""

from __future__ import annotations

import pytest

from ._helpers import _Stack


@pytest.fixture(name="stack")
def _stack(tmp_path) -> _Stack:
    """A throwaway stack with a scripted ``docker`` on PATH.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The prepared :class:`_Stack`.
    """
    return _Stack(tmp_path)
