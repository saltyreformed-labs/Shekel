"""The fixture the ``quill`` command's tests share: a throwaway code repository."""
from __future__ import annotations

import pytest

from tools.ci.scratch import run as _run
from tools.ci.scratch import with_origin
from tools.quill import _migrate_fake, _migrate_source


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


@pytest.fixture(name="corpus")
def _corpus(monkeypatch):
    """The migration's small plan (:mod:`_migrate_fake`), R-BAL240's deploy-together key
    pointed at its container and no card filed already, since the live keys name steps
    the corpus does not hold.  ``MAPPED`` is changed in place: every module reads the one
    dict."""
    monkeypatch.setattr(_migrate_source, "DEPLOY_TOGETHER", frozenset({"balance:A-2"}))
    for key in list(_migrate_source.MAPPED):
        monkeypatch.delitem(_migrate_source.MAPPED, key)
    return _migrate_fake.registries()
