"""Tasks in quill (ruling ``balance:R-BAL386``): a piece of one step or finding, a commit
inside its card's one pull request, done when its file ``plan/tasks/N`` is in git
(``_state.done_tasks``).

``quill file task`` files one (``_filing``).  Here are ``quill task-file``, which writes a
task's file into the working tree for the session to commit with the piece it finishes
(:func:`cmd_task_file`), and the read a card's drop makes of the tasks under it
(:func:`taken_by_drop`).  ``quill sync`` writes each task's state from git, and ``quill
show`` names each task under a card with it.

Split out of the command module, which these would take past the 1,000 lines
``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from tools.ci.trailers import DEV, MAIN
from tools.quill import _git
from tools.quill._command import Refused, _label, _one
from tools.quill._state import drop_takes, dropped
from tools.quill._tracker import Card, Tracker
from tools.quill.setup_tracker import TASK

#: What a task's file says: the instant it was written (:func:`_git.instant`), so each write
#: is a new line, as each claim's marker is.
DONE = "done "


def cmd_task_file(args, tracker: Tracker, root: Path,
                  clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Write task ``plan#T``'s file ``plan/tasks/T`` (:func:`_git.task_file`), one line,
    ``done <instant>``, into the working tree, for the session to commit with the piece it
    finishes: written only while the branch checked out is ``card/<the task's card>``, the
    one branch on which the file marks it done.  It writes no tracker state: ``sync`` closes
    the task once ``origin``'s branch holds the file.  Refused, with nothing written, for a
    card that is no task, a task under no card of the tracker, a task dropped (its file would
    mark a dropped piece done), and any other branch checked out, or none."""
    task = _one(tracker, args.task)
    if task.kind != TASK:
        raise Refused(f"{_label(task)} is no task: a task's file is written for a task, which "
                      "`quill file task` files")
    if task.parent is None:
        raise Refused(f"{_label(task)} is under no card of the tracker: link it under the step "
                      "or finding it is a piece of first")
    if dropped(task.number, {task.number: task}, frozenset()):
        raise Refused(f"{_label(task)} was dropped (closed as {task.state_reason}): its file "
                      "would mark a dropped piece done; reopen it on the web first if it is "
                      "work after all")
    branch = _git.card_branch(task.parent)
    here = _git.checked_out(root)
    if here != branch:
        raise Refused(f"{_label(task)} is a piece of plan#{task.parent}, so its file is written "
                      f"on {branch}, and {here or 'no branch'} is checked out: switch to "
                      f"{branch} (`quill claim plan#{task.parent}` makes it)")
    path = root / _git.task_file(task.number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{DONE}{_git.instant(clock())}\n", encoding="utf-8")
    print(f"wrote {_git.task_file(task.number)}: commit it with the piece it finishes and push "
          f"{branch}; `quill sync` then closes {_label(task)}")
    return 0


def taken_by_drop(tracker: Tracker, card: Card, tasks: Iterable[int]) -> list[Card]:
    """The tasks under ``card`` that its drop drops (:func:`_state.drop_takes`), in card order,
    each read by its number: a parent's read lists a sub-issue's state, never who closed it
    or why.  Each task whose file is in the merge of ``dev`` and ``main`` (``tasks``) is
    named as left; no read is made for a card with no task under it."""
    numbers = card.task_children
    if not numbers:
        return []
    landed, taken = set(tasks), []
    for task in sorted(tracker.cards(numbers).values(), key=lambda task: task.number):
        if task.number in landed:
            print(f"  task plan#{task.number} is done in git ({_git.task_file(task.number)} is "
                  f"on {DEV} or {MAIN}), so it is not dropped")
        elif drop_takes(task, landed):
            taken.append(task)
    return taken
