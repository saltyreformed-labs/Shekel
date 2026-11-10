"""``quill next`` says a release is due while ``dev`` and ``main`` pin different versions (ruling
plan#2829, option D), by the one walk the daily job's mention reads
(:func:`tools.quill._versions.versions_waiting`): how many, and what starts a release, never a
list.  Over :class:`_fake.FakeTracker` and a throwaway git repository with a real bare ``origin``
(the ``code`` fixture, ``conftest.py``), each branch moved from another clone, so ``next`` sees
it only through its own fetch.  Nothing here calls GitHub.
"""
from __future__ import annotations

import pytest

from tools.ci.gitcmd import DEV, MAIN, tracking
from tools.ci.scratch import commit_files, elsewhere
from tools.ci.scratch import run as _scratch
from tools.quill._fake import FakeTracker, claim, run
from tools.quill._tracker import Comment
from tools.quill._versions import LOCK

_REFERENCE = {"dev": DEV, "main": MAIN}


def _due(count):
    """The line ``next`` says a release is due with, for ``count`` versions."""
    return (f"RELEASE DUE: dev and main pin different versions ({count}) -- tell the developer; "
            "only his word starts a release (/release)")


def _lock(*packages):
    """A lock holding each of ``packages``, a name and a version, from PyPI."""
    return "version = 1\nrevision = 5\n" + "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n' for name, version in packages)


def _land(code, branch, files):
    """A commit of ``files`` on ``origin``'s ``branch``, pushed from another clone."""
    other = elsewhere(code)
    tip = _scratch(other, "rev-parse", tracking(_REFERENCE[branch]))
    sha = commit_files(other, tip, branch, files)
    _scratch(other, "push", "--quiet", "origin", f"{sha}:refs/heads/{branch}")


def _next(tracker, code, capsys):
    """What ``quill next`` prints, a line each; it exits 0."""
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    return capsys.readouterr().out.splitlines()


def _waiting(tracker):
    """``tracker`` holding one open step, the next to build."""
    tracker.add(1)
    return tracker


def test_a_version_dev_moved_says_a_release_is_due_beside_the_step(code, capsys):
    """``main`` and ``dev`` hold one lock and ``dev`` moves one package: ``next`` still offers
    the step, and says how many, after a fetch of its own, the move having come from another
    clone."""
    for branch in ("main", "dev"):
        _land(code, branch, {LOCK: _lock(("flask", "3.1.0"), ("zeta", "0.6.3"))})
    _land(code, "dev", {LOCK: _lock(("flask", "3.1.1"), ("zeta", "0.6.3"))})
    lines = _next(_waiting(FakeTracker()), code, capsys)
    assert lines[0].startswith("next: plan#1 ")
    assert _due(1) in lines


def test_no_version_moved_says_nothing(code, capsys):
    """``dev`` holds other work and a lock moved in no version: nothing is said of a release."""
    for branch in ("main", "dev"):
        _land(code, branch, {LOCK: _lock(("flask", "3.1.0"))})
    _land(code, "dev", {"app.py": "1", LOCK: _lock(("flask", "3.1.0")).replace("revision = 5",
                                                                               "revision = 6")})
    assert not any("RELEASE" in line for line in _next(_waiting(FakeTracker()), code, capsys))


def test_a_release_carrying_them_ends_the_line(code, capsys):
    """Once ``main`` holds what ``dev`` holds, nothing waits."""
    _land(code, "dev", {LOCK: _lock(("flask", "3.1.1"))})
    assert _due(1) in _next(_waiting(FakeTracker()), code, capsys)
    _land(code, "main", {LOCK: _lock(("flask", "3.1.1"))})
    assert not any("RELEASE" in line for line in _next(_waiting(FakeTracker()), code, capsys))


def test_with_neither_claim_nor_step_it_is_still_said(code, capsys):
    """Nothing to build is no reason to hold it back: a session reading ``next: nothing`` is one
    that can pass it on."""
    _land(code, "dev", {LOCK: _lock(("flask", "3.1.1"))})
    assert _next(FakeTracker(), code, capsys) == ["next: nothing", _due(1)]


def test_while_main_holds_no_lock_it_counts_every_package_and_names_none(code, capsys):
    """The window between plan#1183 landing and its release: a release is due, truly, and the
    line says how many without listing the lock."""
    _land(code, "dev", {LOCK: _lock(("flask", "3.1.1"), ("olefile", "0.47"), ("zeta", "0.6.4"))})
    lines = _next(FakeTracker(), code, capsys)
    assert lines == ["next: nothing", _due(3)]


@pytest.mark.parametrize("lock, said", [
    (_lock(("flask", "3.1.1")), "RELEASE DUE: "),
    ("[[package\n",
     "RELEASE DUE UNKNOWN: a pin on dev or main cannot be read: uv.lock is no TOML"),
], ids=["due", "a_lock_that_is_no_toml"])
def test_what_it_says_stops_no_other_line(code, capsys, lock, said):
    """A moved version, or a lock on ``dev`` that is no TOML, which is said, not raised and never
    read as nothing due: ``next`` still offers the step and exits 0, and the claimed card's
    unanswered comment, read after it, is still named."""
    _land(code, "dev", {LOCK: lock})
    tracker = _waiting(FakeTracker())
    tracker.add(2)
    claim(code, 2)
    tracker.notes[2] = [Comment("SaltyReformed", "2026-10-05T14:00:00Z", "Split this.")]
    lines = _next(tracker, code, capsys)
    assert lines[0].startswith("next: plan#1 ")
    at = next(number for number, line in enumerate(lines) if line.startswith(said))
    assert lines[at + 1].startswith("UNANSWERED COMMENT: plan#2 ")
