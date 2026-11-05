"""The fixtures the ``quill`` command's tests share: a throwaway code repository, and the
releases each action's repository tags, read in place of GitHub."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.ci.scratch import run as _run
from tools.ci.scratch import with_origin
from tools.quill import _versions


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


@pytest.fixture(name="tags")
def _tags(monkeypatch):
    """What each action's repository tags, read in place of GitHub by the version walk
    (:func:`_versions._tagged`, whose own reading of a repository ``test__versions.py``
    grades against real ones): ``tagged``, which the test fills, ``(owner/repo, release)``
    to the commit its tag names, a release missing from it one the repository does not tag;
    and ``reads``, each ``(owner/repo, release)`` the walk asked, in order."""
    held = SimpleNamespace(tagged={}, reads=[])

    def tagged(_root, repository, release):
        """The commit ``held.tagged`` names for the release, the read recorded."""
        held.reads.append((repository, release))
        return held.tagged.get((repository, release))

    monkeypatch.setattr(_versions, "_tagged", tagged)
    return held
