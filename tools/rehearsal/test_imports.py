"""The rehearsal kit's two import rules, held in one test (the coordinator's K1 leaf-plan
ruling, item 1).

The orchestrator (every module but ``side/``) imports the standard library, ``psycopg``,
``tools.ci`` and this package, never ``side/``; the side probe (``side/``) imports the standard
library, ``flask``, ``time_machine`` and ``app``'s two public names.  Why each is so is the
package docstring.  A test module or ``conftest.py`` in either half may import ``pytest`` too.
Which of ``tools.ci``'s modules are public is ``shekel-private-module-import``'s to say (pylint,
in the same CI step), not this test's.

An AST rule over every import statement, so a test rather than a reviewer's memory.  Each
rule is also shown refusing planted modules, so the side probe's rule is graded before
``side/`` holds a module of its own.
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent
SIDE = PACKAGE / "side"

#: The whole of ``app`` the side probe may name: the factory and the database extension.
SIDE_APP_NAMES = frozenset({"app.create_app", "app.extensions.db"})


def _imported(source: str) -> Iterator[tuple[int, str]]:
    """``(line, name)`` for every name an import statement in ``source`` binds: the module of
    ``import x``, and ``module.name`` for each name of ``from module import name`` (``module.*``
    for a star, which its module's rule then judges).  A relative import keeps its leading
    dots, so it never reads as a full name."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            joiner = "" if module.endswith(".") else "."
            yield from ((node.lineno, module + joiner + alias.name) for alias in node.names)


def _within(name: str, *packages: str) -> bool:
    """Whether ``name`` is one of ``packages`` or inside one."""
    return any(name == package or name.startswith(package + ".") for package in packages)


def _allowed(path: Path, name: str) -> bool:
    """Whether the module at ``path`` may import ``name``, by the rule of its half."""
    if not name.startswith(".") and name.split(".")[0] in sys.stdlib_module_names:
        return True
    if (path.match("test_*.py") or path.match("conftest.py")) and _within(name, "pytest"):
        return True
    if SIDE in path.parents:
        return _within(name, "flask", "time_machine") or name in SIDE_APP_NAMES
    return (_within(name, "psycopg", "tools.ci", "tools.rehearsal")
            and not _within(name, "tools.rehearsal.side"))


def _refused(path: Path, source: str) -> list[str]:
    """Every import in ``source`` the module at ``path`` may not make, as ``line: name``."""
    return [f"{line}: {name}" for line, name in _imported(source) if not _allowed(path, name)]


MODULES = sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)


def test_every_module_of_the_package_is_graded():
    """The walk finds the package's modules, so an empty list cannot pass by grading nothing."""
    assert {"__init__.py", "conftest.py", "test_imports.py"} <= {path.name for path in MODULES}


@pytest.mark.parametrize("path", MODULES, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_a_module_imports_only_what_its_half_may(path):
    """Every real module of the kit holds its half's rule."""
    refused = _refused(path, path.read_text(encoding="utf-8"))
    assert not refused, f"{path.relative_to(PACKAGE)} imports what its half may not: {refused}"


ORCHESTRATOR = PACKAGE / "planted.py"
ORCHESTRATOR_TEST = PACKAGE / "test_planted.py"
SIDE_MODULE = SIDE / "planted.py"
SIDE_TEST = SIDE / "test_planted.py"


@pytest.mark.parametrize("path, source, name", [
    (ORCHESTRATOR, "import app", "app"),
    (ORCHESTRATOR, "from app.models import Account", "app.models.Account"),
    (ORCHESTRATOR, "from app import create_app", "app.create_app"),
    (ORCHESTRATOR, "import scripts.private_cluster", "scripts.private_cluster"),
    (ORCHESTRATOR, "from scripts import private_cluster", "scripts.private_cluster"),
    (ORCHESTRATOR, "from tools.quill import door", "tools.quill.door"),
    (ORCHESTRATOR, "from tools import quill", "tools.quill"),
    (ORCHESTRATOR, "import tools", "tools"),
    (ORCHESTRATOR, "import flask", "flask"),
    (ORCHESTRATOR, "import pytest", "pytest"),
    (ORCHESTRATOR, "from . import bench", ".bench"),
    (ORCHESTRATOR, "from ..ci import gitcmd", "..ci.gitcmd"),
    (ORCHESTRATOR, "from tools.rehearsal.side import probe", "tools.rehearsal.side.probe"),
    (ORCHESTRATOR, "from tools.rehearsal import side", "tools.rehearsal.side"),
    (ORCHESTRATOR_TEST, "from app import create_app", "app.create_app"),
    (SIDE_MODULE, "from tools.ci import gitcmd", "tools.ci.gitcmd"),
    (SIDE_MODULE, "import tools.rehearsal", "tools.rehearsal"),
    (SIDE_MODULE, "import app", "app"),
    (SIDE_MODULE, "import app.extensions", "app.extensions"),
    (SIDE_MODULE, "from app import models", "app.models"),
    (SIDE_MODULE, "from app.models import Account", "app.models.Account"),
    (SIDE_MODULE, "import psycopg", "psycopg"),
    (SIDE_MODULE, "import pytest", "pytest"),
    (SIDE_MODULE, "from . import crawl", ".crawl"),
    (SIDE_TEST, "from tools.rehearsal import bench", "tools.rehearsal.bench"),
])
def test_a_planted_import_outside_its_half_is_refused(path, source, name):
    """Each rule fires: the orchestrator never reaches ``app``, ``scripts``, another tools
    package or ``side/``, and the side probe never reaches ``tools`` or more of ``app``."""
    assert _refused(path, f'"""A planted module."""\n{source}\n') == [f"2: {name}"]


@pytest.mark.parametrize("path, source", [
    (ORCHESTRATOR, "from __future__ import annotations"),
    (ORCHESTRATOR, "import json"),
    (ORCHESTRATOR, "from os import path"),
    (ORCHESTRATOR, "import psycopg"),
    (ORCHESTRATOR, "from psycopg import sql"),
    (ORCHESTRATOR, "from tools.ci import gitcmd"),
    (ORCHESTRATOR, "from tools.ci.gitcmd import is_ancestor"),
    (ORCHESTRATOR, "from tools.rehearsal import bench"),
    (ORCHESTRATOR_TEST, "import pytest"),
    (SIDE_MODULE, "import os"),
    (SIDE_MODULE, "import flask"),
    (SIDE_MODULE, "from flask import Flask"),
    (SIDE_MODULE, "import time_machine"),
    (SIDE_MODULE, "from app import create_app"),
    (SIDE_MODULE, "from app.extensions import db"),
    (SIDE_TEST, "import pytest"),
])
def test_a_planted_import_inside_its_half_is_allowed(path, source):
    """Each rule lets through exactly what its half needs, so neither refuses by refusing all."""
    assert not _refused(path, f'"""A planted module."""\n{source}\n')
