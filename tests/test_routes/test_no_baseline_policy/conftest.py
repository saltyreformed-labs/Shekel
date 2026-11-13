"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_routes/test_no_baseline_policy.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

import pytest


@pytest.fixture()
def baseline_less_owner(seeded_world):
    """The ids of the world this package's tests declare.

    A thin alias for :data:`seeded_world` so the tests keep naming the state
    they are in rather than the mechanism that delivers it.

    Returns:
        A dict of the row ids each parametrised URL and assertion needs.
    """
    return seeded_world
