"""Registers the ``tests`` package for pytest's assertion rewriting, and nothing else: the
suite's fixtures and hooks live in ``tests/conftest.py``.

pytest rewrites the asserts of a test module (``python_files``), of a ``conftest.py``, of a
module named on the command line and of a module registered for rewriting (pytest registers
its plugins itself; any other module is registered by
:func:`pytest.register_assert_rewrite`).  A helper module under ``tests``
(``tests/_test_helpers.py``, a split package's ``_helpers.py``) is none of the first three,
so unregistered its failing assert raises an ``AssertionError`` carrying only the assert's
own message, if it has one, and none of the values it compared; and a passing one never
reaches pytest's ``pytest_assertion_pass`` hook, which ``tools/testrecord`` records.

The registration lives HERE, at the repository root, because pytest imports this file before
it imports the ``tests`` package, and so before any module under it.  ``tests/`` is a
package, so pytest imports ``tests/__init__.py`` to reach ``tests/conftest.py``, and
registering ``tests`` from there finds it already imported: pytest then warns ``Module
already imported so cannot be rewritten; tests`` (``AssertionRewritingHook.mark_rewrite``,
``_pytest/assertion/rewrite.py:263-279``; measured 2026-10-16 on this tree, pytest 9.1.1,
CPython 3.14.8), and the helpers ``tests/conftest.py`` imports before the registration are
not rewritten either.

pytest loads this file in the suite's runs, and also in the tools' own suites
(``pytest tools/<name> -c /dev/null``): their confcutdir is then ``/dev``, so pytest reads a
``conftest.py`` in every directory above the tool but ``/``, this one's among them, and puts
this directory on ``sys.path`` to import it.  It changed no outcome there: measured
2026-10-16, tools/split, tools/testrecord, tools/ci and tools/pylint/tests gave the same
results with it and without it, and tools/plan_gate (deleted since, at step X-cx's S3b),
tools/quill and tools/release the counts they gave before it.
"""
import pytest

pytest.register_assert_rewrite("tests")
