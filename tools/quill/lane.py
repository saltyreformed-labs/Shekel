"""Start a lane: a worktree of its own made from ``origin/dev``, and a session in it whose
first act claims its step (step plan#1824, ruling plan#1835).

The developer runs it in a new terminal, from the root of any checkout of the code
repository, naming the lane and, until the board can hand out what he wants first
(plan#1820), its step::

    .venv/bin/python -m tools.quill.lane NAME [plan#N]

The checkout's own venv runs it, so no environment need be active in that terminal (the
system ``python`` lacks quill's packages).  It makes the worktree ``shekel-NAME`` beside the
main checkout (``~/projects/shekel-NAME``), DETACHED at the ``origin/dev`` its one fetch has
just brought, so it makes no branch and writes no upstream into the configuration every
worktree shares (``git worktree add -b`` from a remote-tracking branch writes one); builds
the worktree's own ``.venv`` from the lock its tree holds (ruling plan#2828: ``uv sync
--locked``, which ``scripts/test.sh`` runs again before each suite, so a lane's packages follow
its merges; the session's hooks stay on the main checkout's venv, ``scripts/hooks/_hooklib.sh``);
and starts ``claude`` there in place of this process, with that venv active (:func:`start`), so
the session's ``python`` and its commits' pre-commit hooks run the packages its tree names, and
with a first prompt that claims the step named, or the one ``quill next`` hands out.  ``quill
claim`` then makes the card's branch and checks it out in the lane's worktree.  A path that
is already there is refused before anything is fetched or made.

Exit status: 0 the session started (only a test's own launch returns); 1 refused, with
nothing made; 2 git failed, or the venv could not be built or ``claude`` could not start
(the output says what was made), or the command line is not the form above (argparse's
usage error).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tools.ci.arcs import REPO
from tools.ci.gitcmd import DEV, GitError, git, tracking
from tools.quill import _git
from tools.quill._command import card_number
from tools.quill._exits import Refused, exit_status

#: What every lane's worktree directory is named with, before the lane's name.
PREFIX = "shekel-"
#: A lane's name: lowercase letters and digits, words joined by single hyphens, so the
#: worktree's name is one path component that no shell or git reads as anything else.
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
#: How a session runs the plan's command, as CLAUDE.md's Common Commands spell it.
_QUILL = "python -m tools.quill.quill"
#: What a lane's worktree holds its venv in, where uv builds a project's by default.
_VENV = ".venv"


def lane_name(text: str) -> str:
    """``text`` when it is a lane's name (:data:`_NAME`)."""
    if not _NAME.fullmatch(text):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a lane's name: lowercase letters and digits, words joined by "
            "single hyphens")
    return text


def main_checkout(root: Path) -> Path:
    """The main checkout of the repository ``root`` is a checkout of: the first worktree
    git lists, whichever of its worktrees ``root`` is."""
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree "))
    raise GitError(f"git worktree list in {root} names no worktree")


def first_prompt(card: int | None) -> str:
    """What the lane's session is asked first: to claim ``card``, or with none, the step
    ``quill next`` hands out; then to run ``/charter`` for it."""
    claim = (f"Claim plan#{card}: `{_QUILL} claim plan#{card}`." if card is not None else
             f"Run `{_QUILL} next` and claim the step it names: `{_QUILL} claim plan#N`.")
    return (f"This is a new lane, in a worktree of its own at origin/dev. {claim} "
            "Then run /charter for that step.")


def _build_venv(worktree: Path) -> None:
    """Build ``worktree``'s venv from the lock its tree holds (:data:`_VENV`).  ``VIRTUAL_ENV``
    names that venv, as :func:`start` does: uv writes only the project's whichever it names
    (measured 2026-11-04, on scratch venvs), and warns when it names another, as it does in a
    terminal with one active.

    Raises:
        OSError: When uv cannot be run, or fails, its own words above; the worktree stands.
    """
    environment = {**os.environ, "VIRTUAL_ENV": str(worktree / _VENV)}
    built = subprocess.run(["uv", "sync", "--locked"], cwd=worktree, env=environment, check=False)
    if built.returncode != 0:
        raise OSError(f"uv sync --locked exited {built.returncode}; the worktree stands: build "
                      f"its venv and start its session with `cd {worktree} && uv sync --locked "
                      "&& source .venv/bin/activate && claude`")


def make(root: Path, name: str) -> Path:
    """Make lane ``name``'s worktree beside ``root``'s main checkout, detached at the
    ``origin/dev`` a fetch has just brought, with a venv built from its own lock; its path.

    Raises:
        Refused: When the path is there already (a dangling link too), before the fetch.
        GitError: When the worktree list, the fetch, the read of ``dev`` or the worktree's
            making fails.
        OSError: When the venv cannot be built (:func:`_build_venv`); the worktree stands.
    """
    checkout = main_checkout(root)
    worktree = checkout.parent / f"{PREFIX}{name}"
    if worktree.exists(follow_symlinks=False):
        raise Refused(f"{worktree} is there already: name another lane")
    _git.fetch(root)
    sha = git(root, "rev-parse", "--verify", f"{tracking(DEV)}^{{commit}}").strip()
    git(root, "worktree", "add", "--quiet", "--detach", str(worktree), sha)
    print(f"made {worktree}, detached at {DEV} {sha[:12]}", flush=True)
    _build_venv(worktree)
    print(f"  its {_VENV} is built from its own uv.lock")
    return worktree


def start(worktree: Path, argv: list[str]) -> None:
    """Run ``argv`` in ``worktree`` in place of this process, with the worktree's venv's
    ``bin`` first on ``PATH`` and ``VIRTUAL_ENV`` naming it, so the ``python`` it runs is that
    venv's, and ``PWD`` the worktree; it returns only by raising.  What this process printed is
    flushed first: the program replaces its buffers.

    Raises:
        OSError: When the program cannot be run.
    """
    venv = worktree / _VENV
    environment = {**os.environ, "VIRTUAL_ENV": str(venv), "PWD": str(worktree),
                   "PATH": os.pathsep.join([str(venv / "bin"), *os.get_exec_path()])}
    sys.stdout.flush()
    os.chdir(worktree)
    os.execvpe(argv[0], argv, environment)


def parser() -> argparse.ArgumentParser:
    """The command line: the lane's name, then the step it claims, if named."""
    line = argparse.ArgumentParser(prog=".venv/bin/python -m tools.quill.lane", description=(
        "Start a lane: a worktree beside the main checkout at origin/dev, and a session in "
        "it whose first act claims its step."))
    line.add_argument("name", type=lane_name, help="the lane's name: its worktree is shekel-NAME")
    line.add_argument("card", nargs="?", type=card_number,
                      help="the step it claims (plan#N); with none, the one `quill next` names")
    return line


def _open(root: Path, name: str, card: int | None,
          launch: Callable[[Path, list[str]], None]) -> int:
    """Make lane ``name``'s worktree (:func:`make`) and ``launch`` its session, its first
    prompt claiming ``card``; 0 should ``launch`` return, 2 when it fails, said.

    Raises:
        Refused: As :func:`make` does.
        GitError: As :func:`make` does.
        OSError: When :func:`make` cannot build the venv.
    """
    worktree = make(root, name)
    try:
        launch(worktree, ["claude", first_prompt(card)])
    except OSError as error:
        print(f"failed: claude did not start ({error}); the worktree stands: start its "
              f"session with `cd {worktree} && claude`", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None, root: Path | None = None,
         launch: Callable[[Path, list[str]], None] = start) -> int:
    """Start one lane; its exit status, should ``launch`` return (:func:`_exits.exit_status`)."""
    args = parser().parse_args(argv)
    return exit_status(lambda: _open(root or REPO, args.name, args.card, launch),
                       (GitError, OSError))


if __name__ == "__main__":
    sys.exit(main())
