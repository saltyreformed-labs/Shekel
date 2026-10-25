"""``tools/ci`` is the bottom layer of ``tools/``: its modules import the standard library
and each other, and nothing else.

Two claims rest on it.  The tracker tool and the other tools import this package,
so an import back would make the layers depend on each other (the design the
developer approved, ruling balance:R-BAL223); and ``ci.yml`` runs the verdict with
no install, so a third-party import would fail only on the runner.  An AST rule, so a
test rather than a reviewer's memory (review of 1e52a05ae, N3).  Test modules are
exempt: they run after the install.
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.ci import arcs

PACKAGE = arcs.REPO / "tools" / "ci"
#: The layer's modules, and ``tools/__init__.py``, which every no-install job imports first.
MODULES = sorted(path for path in PACKAGE.glob("*.py")
                 if not (path.match("test_*.py") or path.match("conftest.py")))
MODULES.append(PACKAGE.parent / "__init__.py")


def _imports(path: Path) -> Iterator[tuple[int, str]]:
    """``(line, module)`` for every import in ``path``; a relative one keeps its dots, so it is
    never this package's name."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, "." * node.level + (node.module or "")


def _allowed(module: str) -> bool:
    """The standard library, or this package by its full name.  A relative import is not
    allowed: ``..`` climbs out of the package (review of fe5953d84, MEDIUM 3)."""
    return (module.split(".")[0] in sys.stdlib_module_names
            or module == "tools.ci" or module.startswith("tools.ci."))


def test_every_module_of_the_layer_is_graded():
    """The glob finds the modules CI runs, so an empty list cannot pass by grading nothing."""
    assert {"arcs.py", "ci_verdict.py", "gitcmd.py"} <= {
        path.name for path in MODULES}
    assert PACKAGE.parent / "__init__.py" in MODULES


@pytest.mark.parametrize("module", [".", ".gitcmd", "..", "..quill"])
def test_a_relative_import_is_refused(module):
    """``from . import gitcmd`` and ``from .. import quill`` alike: no module of the layer
    imports relatively, so there is no allowance for ``..`` to slip through beside one."""
    assert not _allowed(module)


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(arcs.REPO)))
def test_a_module_imports_the_standard_library_and_this_package_only(path):
    """Nothing from ``tools.quill``, another tool or a third-party package."""
    outside = [f"{path.name}:{line}: {module}" for line, module in _imports(path)
               if not _allowed(module)]
    assert not outside, f"tools/ci is the bottom layer and stdlib-only: {outside}"
