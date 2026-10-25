"""The fixture the ``quill`` command's tests share: a throwaway code repository."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run as _run
from tools.ci.scratch import with_origin


@pytest.fixture(name="code")
def _code(tmp_path):
    """A code repository under tmp_path on the branch ``feat/work``, with a real bare
    ``origin`` holding ``dev`` and ``main`` at its one commit (:func:`scratch.with_origin`):
    every command's read of git -- the claims ``origin`` lists, the fetch, the ship markers
    -- and every claim and release runs as git runs it, nothing replaced."""
    root = tmp_path / "code"
    root.mkdir()
    _run(root, "init", "--quiet", "--initial-branch=feat/work")
    tree = _run(root, "hash-object", "-t", "tree", "/dev/null")
    base = _run(root, "commit-tree", tree, "-m", "base")
    _run(root, "update-ref", "refs/heads/feat/work", base)
    with_origin(root, base)
    return root
