"""What a release of ``dev`` would move (:func:`tools.quill._versions.versions_waiting`, rulings
plan#2829, plan#2876): the one question the daily job's mention and ``quill next`` both ask, of
the remote-tracking ``main`` against ``dev``, over every file that pins versions.  Throwaway
repositories; ``tools/release/test__changes.py`` grades the walk's every shape through the
release tool's listing, which reads it too."""
from __future__ import annotations

import pytest

from tools.ci.gitcmd import GitError
from tools.ci.scratch import commit, commit_files, point_dev, point_main, repository
from tools.ci.scratch import run as _scratch
from tools.quill._versions import LOCK, Moved, NotVersions, versions_waiting

#: A digest for each side of an image's move.
_OLD, _NEW = "a" * 64, "b" * 64
#: A compose file pinning postgres.
_COMPOSE = f"services:\n  db:\n    image: postgres:18-alpine@sha256:{_OLD}\n"


def _lock(*packages):
    """A lock holding each of ``packages``, a name and a version, from PyPI."""
    return "version = 1\nrevision = 5\n" + "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n' for name, version in packages)


@pytest.fixture(name="code")
def _code(tmp_path):
    """A throwaway repository."""
    return repository(tmp_path)


def _branches(code, main, dev):
    """Point ``code``'s remote-tracking ``main`` at a commit of the files ``main``, and its
    ``dev`` at that commit with the changes ``dev``."""
    released = commit_files(code, commit(code, "start"), "main", main)
    point_main(code, released)
    point_dev(code, commit_files(code, released, "dev", dev))


def test_a_version_dev_moved_waits(code):
    """A package and an image ``dev`` moved are each named, from ``main``'s to ``dev``'s, by
    path, and what ``dev`` left alone is not."""
    _branches(code, {LOCK: _lock(("flask", "3.1.0"), ("zeta", "0.6.3")),
                     "docker-compose.yml": _COMPOSE},
              {LOCK: _lock(("flask", "3.1.1"), ("zeta", "0.6.3")),
               "docker-compose.yml": _COMPOSE.replace(f"18-alpine@sha256:{_OLD}",
                                                      f"18.7-alpine@sha256:{_NEW}")})
    assert versions_waiting(code) == (
        Moved("docker-compose.yml", "postgres", (f"18-alpine@sha256:{_OLD}",),
              (f"18.7-alpine@sha256:{_NEW}",)),
        Moved(LOCK, "flask", ("3.1.0",), ("3.1.1",)))


def test_a_moved_version_is_named_as_every_listing_names_it():
    """One spelling: the release tool's listing and the issue's both print a :class:`Moved`
    so, ``none`` for a side that pins it at none."""
    assert str(Moved(LOCK, "flask", ("3.1.0",), ("3.1.1",))) == "uv.lock: flask 3.1.0 -> 3.1.1"
    assert str(Moved(LOCK, "zeta", ("0.6.3", "0.7.0"), ())) == "uv.lock: zeta 0.6.3, 0.7.0 -> none"
    assert str(Moved(LOCK, "olefile", (), ("0.47",))) == "uv.lock: olefile none -> 0.47"


def test_no_version_moved_leaves_nothing_waiting(code):
    """``dev`` holds other work, and a change to a pinning file moving no pin: nothing waits."""
    _branches(code, {LOCK: _lock(("flask", "3.1.0")), "app.py": "0"},
              {LOCK: _lock(("flask", "3.1.0")).replace("revision = 5", "revision = 6"),
               "app.py": "1"})
    assert not versions_waiting(code)


def test_a_version_production_does_not_run_waits_too(code):
    """Ruling plan#2876: a pin moved in a file production's image leaves out and no deploy reads
    still waits, as ``main`` runs its own copy of a workflow: an action only ``updates.yml`` pins,
    and the dev compose file's image, each named by its file."""
    workflow = ".github/workflows/updates.yml"
    uses = f"      - uses: actions/upload-artifact@{'1' * 40} # v7.0.1\n"
    _branches(code, {".dockerignore": "docker-compose*\n.github\n",
                     "docker-compose.dev.yml": _COMPOSE, workflow: uses},
              {"docker-compose.dev.yml": _COMPOSE.replace(_OLD, _NEW),
               workflow: uses.replace("1" * 40, "2" * 40).replace("v7.0.1", "v7.1.0")})
    assert versions_waiting(code) == (
        Moved(workflow, "actions/upload-artifact", (f"{'1' * 40} # v7.0.1",),
              (f"{'2' * 40} # v7.1.0",)),
        Moved("docker-compose.dev.yml", "postgres", (f"18-alpine@sha256:{_OLD}",),
              (f"18-alpine@sha256:{_NEW}",)))


def test_while_main_holds_no_lock_every_locked_package_waits(code):
    """The window between plan#1183 landing and its release: ``main`` holds no lock, so each
    package reads against none, which a release would bring."""
    _branches(code, {"app.py": "0"}, {LOCK: _lock(("flask", "3.1.1"), ("zeta", "0.6.4"))})
    assert versions_waiting(code) == (Moved(LOCK, "flask", (), ("3.1.1",)),
                                      Moved(LOCK, "zeta", (), ("0.6.4",)))


def test_only_the_remote_tracking_branches_are_read(code):
    """A local branch or a tag named ``origin/dev`` or ``origin/main`` stands in for neither:
    git resolves those short names to a tag or a branch first, and CI's checkout fetches every
    tag."""
    _branches(code, {LOCK: _lock(("flask", "3.1.0"))}, {LOCK: _lock(("flask", "3.1.1"))})
    other = commit_files(code, commit(code, "other"), "other", {LOCK: _lock(("flask", "9.9.9"))})
    for branch in ("origin/dev", "origin/main"):
        for kind in ("refs/heads", "refs/tags"):
            _scratch(code, "update-ref", f"{kind}/{branch}", other)
    assert versions_waiting(code) == (Moved(LOCK, "flask", ("3.1.0",), ("3.1.1",)),)


def test_a_clone_holding_neither_branch_raises_rather_than_reading_nothing(code):
    """No remote-tracking branch is no empty list: a caller must not read it as nothing
    waiting."""
    with pytest.raises(GitError):
        versions_waiting(code)


def test_a_lock_no_pin_of_which_can_be_named_raises(code):
    """A lock on ``dev`` that is no TOML names no version, never one moved as none."""
    _branches(code, {LOCK: _lock(("flask", "3.1.0"))}, {LOCK: "[[package\n"})
    with pytest.raises(NotVersions):
        versions_waiting(code)


def test_the_ignore_file_is_not_read(code):
    """Every pinned file counts, so what the image holds is not asked: one Docker refuses stops
    nothing, and a release of it is judged by the release tool."""
    _branches(code, {LOCK: _lock(("flask", "3.1.0"))},
              {LOCK: _lock(("flask", "3.1.1")), ".dockerignore": "!\n"})
    assert versions_waiting(code) == (Moved(LOCK, "flask", ("3.1.0",), ("3.1.1",)),)
