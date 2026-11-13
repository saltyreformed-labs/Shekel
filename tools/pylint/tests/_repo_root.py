"""The checkout's root directory, for a checker test that reads a source file by its place.

Spelled in a test itself, ``Path(__file__).resolve().parents[N]`` carries the test's own
depth in ``N``: move the test one directory deeper (``tools/split`` turns a module into a
package, every part one level down) and the same line computes another directory without
complaint.  That is why ``tools/split`` refuses to move code that reads ``__file__``.

This module is that fact's home for ``tools/pylint/tests/``, as ``tests/_repo_root.py``
is for ``tests/``.  That one cannot serve here: the checker tests run apart from the
app's suite (``pytest tools/pylint/tests -c /dev/null`` in CI and in the pre-commit
hook), where the checkout's root is not on ``sys.path``.  This directory has no
``__init__.py``, so pytest's default import mode puts it on ``sys.path`` for every test
module beneath it, a split package's parts included, and ``from _repo_root import
REPO_ROOT`` resolves under either runner.  It sits directly in ``tools/pylint/tests/``, so
``parents[3]`` is the root by construction, and it does not move when its importers do.
Moved or renamed itself, it breaks every importer loudly, at import, never with a wrong
path.
"""
from pathlib import Path

#: The root of the checkout this module sits in: the parent of ``tools/``.
REPO_ROOT = Path(__file__).resolve().parents[3]
