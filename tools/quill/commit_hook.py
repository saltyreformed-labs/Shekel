"""The commit hook: each commit on a card's branch names the piece it builds, and none is made
while no piece is begun (ruling plan#1872; plan#1861's spec, item 4).

**A commit on ``card/N`` names its task** by the trailer ``Task: plan#T``
(:func:`_git.task_trailer`), T the task in progress on the branch by quill's one reading of it
(:func:`_git.in_progress` at ``HEAD``, from local git and no network): the one the branch's
file ``plan/doing/N`` names, which ``quill start`` wrote, while ``HEAD`` holds no done file
for it (ruling plan#2009).  So the commit that adds T's file is T's, and the next is refused
until ``quill start`` begins the next piece.  As each commit's message is written, the hook
(:func:`judge`):

- stamps the trailer on a message naming no task while a task is in progress;
- passes a message naming the task in progress as it is;
- refuses a commit while no task is in progress, and a message naming a task not in
  progress, two tasks, or a value that is no task's name (``plan#T``), each saying why;
- leaves alone a merge (git's ``MERGE_HEAD``, there however the merge is concluded: one
  concluded with ``-m`` reaches the hook as a message, measured 2026-10-27, git 2.56), a
  detached ``HEAD`` (a rebase's commits, which keep the trailers they had), and any branch
  but ``card/N``.

**One rule at two stages** of ``.pre-commit-config.yaml``: ``prepare-commit-msg``, which
``git commit --no-verify`` does not skip, and ``commit-msg``, once the editor has closed.  A
message with no text yet -- only comments, and the diff ``-v`` shows below git's cut line --
is one whose editor is still to open: it is not stamped, since a stamp is text, which would
turn the empty message that aborts a commit into a commit (measured), and it is refused only
while no task is in progress; ``commit-msg`` judges what the editor leaves.  So a commit made
with both ``--no-verify`` and the editor is not stamped: that is left to CI's backstop at the
pull request (``tools/quill/_stamps.py``, plan#1880).  An amend of the commit that finished a
piece is refused, that piece's file being at the tip: ``git reset --soft HEAD~1``, then commit
again.

The repository is the one git runs the hook in, from the top of its working tree
(githooks(5)): the working directory.  Every git call goes through :mod:`tools.ci.gitcmd`, so
the variables git exports to a hook (``GIT_DIR``, ``GIT_INDEX_FILE``) bind none of them; only
refs and their commits are read (``HEAD``, ``MERGE_HEAD``, and ``dev``'s and ``main``'s
remote-tracking branches, as fetched last), and the message, never the index being committed.
Nothing it imports reads the tracker or needs an installed package: its refusal and exit
status are :mod:`_exits`'s, not ``_command``'s, whose reads of the tracker load ``requests``
and ``cryptography`` (plan#1990), and its test runs it under ``python -S``.

Usage, as pre-commit runs it (its hook ``quill-task``, with the checkout's own interpreter)::

    .venv/bin/python -m tools.quill.commit_hook <message file>

Exit status: 0 passed, stamped or left alone; 1 refused, the message left as it was; 2 git or
the message file failed, or the command line names not one message file.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

from tools.ci.gitcmd import GitError, git, run
from tools.quill import _git
from tools.quill._exits import Refused, exit_status

#: The line below which git drops a commit's message, the diff ``git commit -v`` shows there
#: (``Do not modify or remove the line above``), after the comment string, of any length.
_CUT = re.compile(r"^\S+ -{24} >8 -{24}$", re.MULTILINE)


def _listed(tasks: Iterable[int]) -> str:
    """``tasks`` named in a sentence, in order: ``plan#2``, ``plan#2 and plan#3``, ``plan#2,
    plan#3 and plan#4``."""
    names = [f"plan#{number}" for number in sorted(tasks)]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def judge(branch: str, begun: int | None, values: list[str], has_text: bool) -> int | None:
    """The task to stamp on a commit on ``branch`` (``card/N``), or None when its message is
    to be left as it is: ``begun``, the task in progress there (:func:`_git.in_progress`),
    None when none is; ``values``, each value of its message's ``Task`` trailers;
    ``has_text``, whether it holds text yet (:func:`_has_text`).  The module's rule, from
    these alone.

    Raises:
        Refused: When no task is in progress; and when a value names no task, two tasks are
            named, or the one named is not in progress.
    """
    card = _git.claimed_card(branch)
    if begun is None:
        raise Refused(f"no task of plan#{card} is in progress on {branch}, so this commit is no "
                      f"piece's: begin the next with `python -m tools.quill.quill start plan#T` "
                      f"(`quill show plan#{card}` lists its tasks in order), then commit.  To "
                      "amend the commit that finished a piece, whose file is at the tip: `git "
                      "reset --soft HEAD~1`, then commit again")
    if bad := [value for value in values if _git.task_named(value) is None]:
        raise Refused(f"its trailer `{_git.TASK_TRAILER}: {bad[0]}` names no task: a task is "
                      f"named `{_git.TASK_TRAILER}: plan#<number>`")
    named = {_git.task_named(value) for value in values}
    if len(named) > 1:
        raise Refused(f"its message names {_listed(named)}: a commit is one piece's")
    if named and (task := named.pop()) != begun:
        raise Refused(f"its message names plan#{task}, which is not in progress on {branch}, "
                      f"and plan#{begun} is: a commit is the piece's it builds, begun by `quill "
                      "start` and not finished")
    if values or not has_text:
        return None
    return begun


def _merging(root: Path) -> bool:
    """Whether a merge is being concluded in ``root``'s working tree: git's ``MERGE_HEAD``
    is there.

    Raises:
        GitError: When git cannot say.
    """
    done = run(root, "rev-parse", "--quiet", "--verify", "MERGE_HEAD")
    if done.returncode not in (0, 1):
        raise GitError(f"git rev-parse MERGE_HEAD: {done.stderr.strip()}")
    return done.returncode == 0


def _values(root: Path, message: Path) -> list[str]:
    """Each value of the ``Task`` trailers of the message in the file ``message``, as git
    parses a message's trailers (``interpret-trailers --parse``: the key matched in any case,
    as :func:`_git.stamps` reads a commit's), each with the space around it removed."""
    out = git(root, "interpret-trailers", "--parse", str(message))
    pairs = [line.partition(":") for line in out.splitlines()]
    return [value.strip() for key, _, value in pairs
            if key.strip().casefold() == _git.TASK_TRAILER.casefold()]


def _has_text(root: Path, text: str) -> bool:
    """Whether the message ``text`` holds text a commit keeps: anything but comments above
    git's cut line (:data:`_CUT`), read by git's own stripping of comments
    (``git stripspace --strip-comments``)."""
    above = _CUT.split(text, maxsplit=1)[0]
    return bool(git(root, "stripspace", "--strip-comments", stdin=above).strip())


def stamp(root: Path, message: Path) -> None:
    """Judge the commit being made in ``root`` whose message is the file ``message``
    (:func:`judge`), and stamp it when it is due: its task's trailer written into the file
    by git's trailer writer, at the end of the message's trailers.  A merge, a detached
    ``HEAD`` and any branch but ``card/N`` are left alone, unread.

    Raises:
        Refused: When :func:`judge` refuses the commit.
        GitError: When git fails.
        OSError: When the file cannot be read or written.
    """
    branch = _git.checked_out(root)
    card = None if branch is None else _git.claimed_card(branch)
    if branch is None or card is None or _merging(root):
        return
    task = judge(branch, _git.in_progress(root, "HEAD", card), _values(root, message),
                 _has_text(root, message.read_text(encoding="utf-8")))
    if task is not None:
        git(root, "interpret-trailers", "--in-place", "--where", "end", "--if-missing", "add",
            "--trailer", _git.task_trailer(task), str(message))


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    """Judge the commit whose message file the command line names, in ``root`` (the working
    directory unless named); exit as the module says (:func:`_exits.exit_status`)."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m tools.quill.commit_hook <message file> (pre-commit names the "
              f"one file at prepare-commit-msg and commit-msg); given {args!r}", file=sys.stderr)
        return 2
    here = Path.cwd() if root is None else root

    def judged() -> int:
        stamp(here, here / args[0])
        return 0
    return exit_status(judged, (GitError, OSError))


if __name__ == "__main__":
    sys.exit(main())
