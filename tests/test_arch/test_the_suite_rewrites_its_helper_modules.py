"""Architecture test: pytest rewrites the asserts of the suite's helper modules.

pytest rewrites the asserts of a test module (``python_files = test_*.py``), of a
``conftest.py``, of a module named on the command line and of a module registered with
``pytest.register_assert_rewrite``.  The repository-root ``conftest.py`` registers the
``tests`` package, so a helper module under it (``tests/_test_helpers/_rows.py``, a split
package's ``_helpers.py``) is rewritten too: a failing assert there reports the values it
compared, and a passing one reaches pytest's ``pytest_assertion_pass`` hook, which
``tools/testrecord`` records to prove a split.  Without the registration a helper's failing
assert carries only its own message, if any, and none of the values it compared, and a split
that moves asserts into ``_helpers.py`` loses both: the R-BAL303 pilot (2026-10-16) measured
38 tests of the four merged splits whose moved asserts went unrecorded after the move.

The registration must also run BEFORE the ``tests`` package is imported, which only a
conftest above ``tests/`` can do.  Registered from ``tests/conftest.py`` instead, at its top
the package is already imported (pytest warns, and nothing fails on a warning), and at its
end so are the helpers that conftest imports while it loads, ``tests._test_helpers`` among
them, unrewritten with no warning naming them.  The second test grades the placement:
pytest's rewriting hook loads a module only when it is registered by the time the module is
imported, and every module under ``tests`` is imported after the package itself, so a
package the hook loaded means every module under it was loaded by the hook too.

:mod:`tests.test_arch._rewrite_probe` is a helper module made for the first test: neither a
test module nor a conftest, so only the registration rewrites it.
"""
import pytest

import tests
from tests.test_arch._rewrite_probe import assert_is_one


def test_a_helper_modules_failing_assert_reports_its_values():
    """A failing assert in a helper module explains itself with the values it compared;
    unrewritten, it raises ``AssertionError`` with an empty message."""
    with pytest.raises(AssertionError) as caught:
        assert_is_one(2)
    assert str(caught.value) == "assert 2 == 1"


def test_the_package_itself_was_loaded_by_the_rewriting_hook():
    """The ``tests`` package was loaded by pytest's rewriting hook, the loader of this test
    module, so it was registered before anything imported it.  Registered at the top of
    ``tests/conftest.py`` or at its end, the package's loader is Python's own (measured
    2026-10-16)."""
    assert tests.__spec__.loader is __spec__.loader
