"""The SessionStart hook reports a RELEASE PIN on the shared main checkout.

**Why the hook grew this at all** (developer ruling 2026-09-05).  A constraint
that lives only in coordinator messages is invisible to any session that clears
its context, and clearing context is ordinary.  On 2026-09-05 the shared main
checkout was pinned to a release branch: one session cleared, looked at the
tree, and concluded IT was driving the release -- because every fact a shared
tree offers is about the CHECKOUT and none is about the session reading them.  A
second session could not learn why its browser pass was impossible, the same
pin being the cause, and is worktree-isolated so it could not have looked.

The arm reads the pin off ``git worktree list``, so it cannot go stale, and it
is mutated in both directions below: the silence is what stops it firing on
every session forever -- a warning that always prints is not a warning.  The
hook's STATED arm, which printed the coordinator's notice board of standing
constraints, went with that board and its tests at the tracker switch (ruling
``balance:R-BAL347``, the developer's confirmation).  The next step is the
tracker's answer, which the hook only points at.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[2] / "scripts/hooks/session-start.sh"


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(cwd), *args), check=True,
                   capture_output=True, text=True)


@pytest.fixture(name="checkouts")
def _checkouts(tmp_path):
    """Return (main, linked): a real repo on a branch, plus a worktree of it.

    A real git worktree rather than a fixture double, because the arm reads
    ``git worktree list`` and the whole defect lives in the difference between
    the session's checkout and the shared main one.  A single-checkout test
    could not reproduce it.
    """
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "dev")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    (main / "seed").write_text("seed\n")
    _git(main, "add", "seed")
    _git(main, "commit", "-qm", "seed")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "feat/lane", str(linked))
    return main, linked


def _run(cwd: pathlib.Path) -> str:
    done = subprocess.run(("bash", str(HOOK)), cwd=str(cwd),
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


class TestTheDerivedArmReadsThePinOffTheTree:
    """The release pin, which nobody has to remember to write down."""

    def test_a_release_branch_on_the_main_checkout_is_reported(self, checkouts):
        """The arm fires, and it names the path a session must not touch."""
        main, linked = checkouts
        _git(main, "checkout", "-q", "-b", "release/2026-09-05")

        out = _run(linked)
        assert "STANDING: the shared main checkout" in out
        assert str(main) in out
        assert "release/2026-09-05" in out
        assert "Do NOT check out, branch from, or" in out

    def test_an_ordinary_branch_is_SILENT(self, checkouts):
        """The other direction, and the reason the arm is worth having.

        A line printed on every session is furniture.  This is what keeps the
        warning meaning something on the day it appears.
        """
        main, linked = checkouts
        assert _git(main, "checkout", "-q", "dev") is None
        assert "STANDING: the shared main checkout" not in _run(linked)

    def test_it_reads_the_MAIN_checkout_and_not_the_session_s_own(self, checkouts):
        """The bug this exists for: the session's own branch is not the subject.

        A session on its own feature branch, in a worktree, must still learn
        that the SHARED checkout is pinned -- and a hook that reported ``$PWD``'s
        branch would say nothing at all.
        """
        main, linked = checkouts
        _git(main, "checkout", "-q", "-b", "release/2026-09-05")

        out = _run(linked)
        assert "branch: feat/lane" in out, "the session's own branch is still reported"
        assert "release/2026-09-05" in out, "and the shared pin is reported beside it"


class TestTheNextStepIsThePointer:
    """The plan of record is the tracker, and the hook never reads it."""

    def test_it_points_at_quill_next(self, checkouts):
        """A start reads no network, so the hook names the command and stops."""
        _, linked = checkouts
        assert "- next step: the tracker's, by python -m tools.quill.quill next" in _run(linked)
