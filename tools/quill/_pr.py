"""The ``quill pr`` command (:func:`cmd_pr`): a card's pull request into ``dev``, opened from its
branch ``card/N`` with the line ``Card: saltyreformed-labs/shekel-plan#N`` appended to its text
(:func:`card_line`), so the card shows it (plan#1861's item 10).  GitHub writes a mention of an
issue in a pull request's text into that issue's timeline, from the code repository into the
private tracker too: read 2026-10-27, PR #560's text names ``saltyreformed-labs/shekel-plan#9``
and plan#9's timeline holds that pull request, its state shown, from one second after it was
opened (whether the code repository was public on 2026-10-14, as it is now, no read here
says).  A mention inside backticks is code, which GitHub links nowhere (PR #557's names plan#9
so, and plan#9's timeline holds no PR #557), so the line is plain text.

The pull request is opened as each session opened one before, by ``gh pr create`` under the
developer's own login (a command's App token is narrowed to the tracker,
``_tracker.Tracker.connect``), in the code repository ``gh`` reads from this checkout's
``origin``; the session queues it as before (``gh pr merge <url> --merge``).  The command reads
nothing of the tracker, so it never connects to it (``quill.CONNECTS_LATE``).  A failed ``gh`` is
never run again: GitHub may have opened the pull request before its answer was lost, as with any
write (``_github``'s module docstring), so the failure says to read it back.

Its own module: the command module stands within fifty lines of the 1,000 ``too-many-lines``
allows.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tools.ci.gitcmd import DEV
from tools.quill import _git
from tools.quill._command import _read
from tools.quill._exits import Refused
from tools.quill._tracker import Tracker
from tools.quill.setup_tracker import PLAN

#: The branch a card's pull request merges into, by the name ``origin`` gives it.
BASE = DEV.removeprefix("origin/")


class PullRequestError(RuntimeError):
    """``gh pr create`` exited non-zero; the message holds what it printed."""


def card_line(number: int) -> str:
    """The line card ``number``'s pull request ends with: the card, named in the tracker as
    GitHub links a mention of an issue in another repository."""
    return f"Card: {PLAN.owner}/{PLAN.name}#{number}"


def cmd_pr(args, _connect: Callable[[], Tracker], root: Path) -> int:
    """Open card ``args.card``'s pull request from ``card/N`` into ``dev``, titled
    ``args.title``, its text ``args.body_file``'s with :func:`card_line` appended, by ``gh pr
    create`` run in ``root``; print the pull request's address, and what ``gh`` warned of.

    Raises:
        Refused: for a text that is only space, before ``gh`` runs.
        PullRequestError: when ``gh`` exits non-zero: GitHub refused it (no branch
            ``card/N``, nothing on it that ``dev`` lacks, a pull request from it already
            open), or its answer was lost after the pull request may have been opened.
    """
    body = _read(args.body_file)
    if not body.strip():
        raise Refused(f"{args.body_file} holds no text: a pull request says what it changes")
    branch = _git.card_branch(args.card)
    done = subprocess.run(
        ["gh", "pr", "create", "--base", BASE, "--head", branch, "--title", args.title,
         "--body-file", "-"],
        cwd=root, input=f"{body.rstrip()}\n\n{card_line(args.card)}\n", capture_output=True,
        text=True, check=False,
    )
    if done.returncode:
        raise PullRequestError(
            f"gh pr create --head {branch}: {done.stderr.strip() or done.stdout.strip()}; it is "
            f"never run again unread, as GitHub may have opened it: `gh pr list --head {branch}` "
            "reads it back")
    if done.stderr.strip():
        print(done.stderr.strip(), file=sys.stderr)
    url = done.stdout.strip()
    print(f"opened {url}: plan#{args.card}'s pull request from {branch} into {BASE}, its card "
          f"linked by the line {card_line(args.card)!r}; queue it with `gh pr merge {url} "
          "--merge`")
    return 0
