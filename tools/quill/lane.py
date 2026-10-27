"""Start a lane: a worktree of its own made from ``origin/dev``, and a session in it whose
first act claims its step (step plan#1824, ruling plan#1835).

The developer runs it in a new terminal, from any checkout of the code repository, naming
the lane and, until the board can hand out what he wants first (plan#1820), its step::

    python -m tools.quill.lane NAME [plan#N]

It makes the worktree ``shekel-NAME`` beside the main checkout (``~/projects/shekel-NAME``),
DETACHED at the ``origin/dev`` its one fetch has just brought, so it makes no branch and
writes no upstream into the configuration every worktree shares (``git worktree add -b``
from a remote-tracking branch writes one); links the worktree's ``.venv`` to the main
checkout's, as every lane's is (a session's hooks put its own worktree's ``.venv/bin`` first
on ``PATH``, ``scripts/hooks/_hooklib.sh``); and starts ``claude`` there in place of this
process, with a first prompt that claims the step named, or the one ``quill next`` hands
out.  ``quill claim`` then makes the card's branch and checks it out in the lane's
worktree.  A path that is already there is refused before anything is fetched or made.

Exit status: 0 the session started (only a test's own launch returns); 1 refused, with
nothing made; 2 git failed, or a file could not be made or ``claude`` could not start
(the output says what was made), or the command line is not the form above (argparse's
usage error).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from tools.ci.arcs import REPO
from tools.ci.gitcmd import DEV, GitError, git, tracking
from tools.quill import _git
from tools.quill._command import Refused, card_number

#: What every lane's worktree directory is named with, before the lane's name.
PREFIX = "shekel-"
#: A lane's name: lowercase letters and digits, words joined by single hyphens, so the
#: worktree's name is one path component that no shell or git reads as anything else.
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
#: How a session runs the plan's command, as CLAUDE.md's Common Commands spell it.
_QUILL = "python -m tools.quill.quill"


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


def make(root: Path, name: str) -> Path:
    """Make lane ``name``'s worktree beside ``root``'s main checkout, detached at the
    ``origin/dev`` a fetch has just brought, its ``.venv`` linked to the main checkout's;
    its path.

    Raises:
        Refused: When the path is there already (a dangling link too), before the fetch.
        GitError: When the worktree list, the fetch or the worktree's making fails.
        OSError: When the link cannot be made; the worktree stands.
    """
    checkout = main_checkout(root)
    worktree = checkout.parent / f"{PREFIX}{name}"
    if worktree.exists(follow_symlinks=False):
        raise Refused(f"{worktree} is there already: name another lane, or start a session "
                      f"in that one with `cd {worktree} && claude`")
    _git.fetch(root)
    sha = git(root, "rev-parse", "--verify", f"{tracking(DEV)}^{{commit}}").strip()
    git(root, "worktree", "add", "--quiet", "--detach", str(worktree), sha)
    print(f"made {worktree}, detached at {DEV} {sha[:12]}")
    (worktree / ".venv").symlink_to(checkout / ".venv", target_is_directory=True)
    print(f"  its .venv links {checkout / '.venv'}")
    return worktree


def start(worktree: Path, argv: list[str]) -> None:
    """Run ``argv`` in ``worktree`` in place of this process; it returns only by raising.

    Raises:
        OSError: When the program cannot be run.
    """
    os.chdir(worktree)
    os.execvp(argv[0], argv)


def parser() -> argparse.ArgumentParser:
    """The command line: the lane's name, then the step it claims, if named."""
    line = argparse.ArgumentParser(prog="python -m tools.quill.lane", description=(
        "Start a lane: a worktree beside the main checkout at origin/dev, and a session in "
        "it whose first act claims its step."))
    line.add_argument("name", type=lane_name, help="the lane's name: its worktree is shekel-NAME")
    line.add_argument("card", nargs="?", type=card_number,
                      help="the step it claims (plan#N); with none, the one `quill next` names")
    return line


def main(argv: list[str] | None = None, root: Path | None = None,
         launch: Callable[[Path, list[str]], None] = start) -> int:
    """Start one lane; its exit status, should ``launch`` return."""
    args = parser().parse_args(argv)
    try:
        worktree = make(root or REPO, args.name)
    except Refused as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 1
    except (GitError, OSError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 2
    try:
        launch(worktree, ["claude", first_prompt(args.card)])
    except OSError as error:
        print(f"failed: claude did not start ({error}); the worktree stands: start its "
              f"session with `cd {worktree} && claude`", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
