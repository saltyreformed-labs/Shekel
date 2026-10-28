"""CI's write rule for the file naming a card's task in progress, ``plan/doing/N`` (rulings
plan#2009 and plan#2022; plan#1861's spec, item 3), judged by the landing check
(``tools/quill/landing.py``) in its one walk of the plan's records.

**Card N's task in progress is that file on its branch** (:func:`_git.in_progress`): ``quill
start`` writes it and ``quill drop`` deletes it, so a card's merge brings it to ``dev`` and a
release to ``main``, and a claim cut from ``dev``, or a branch merging ``dev`` in, takes it
from there.  So only card N's own claim may write it, as the landing check's rule 5 lets only a
claim write a marker (``R-BAL359``, rule 6's door aside) or a task's done file (``R-BAL386``).
Over each entry under ``plan/doing`` a change writes (:func:`refused`):

1. **An entry that is no card's file** is refused, as M's tree reads it by quill's one reading
   of a tree's records (:func:`_git.lines_in`, as the landing check's rule 1 reads a marker's):
   a name that is no card number, a file in a directory below, ``plan/doing`` itself a file,
   or a submodule's commit (a gitlink).  **So is a file whose line names no task**
   (``plan#T``, :func:`_git.doing_named`), which :func:`_git.in_progress` reads as nothing in
   progress, silently.
2. **A delete passes, from any branch** (ruling plan#2022), as a marker's and a done file's do:
   a revert of the card's merge that added the file lands from a branch of no card and deletes
   it with the rest, and a claim cut from a ``dev`` holding none has nothing in progress, the
   right answer.  A revert of a merge that CHANGED the file -- a card landed again after a
   person's "not done" kept it -- writes the old line back instead, which rule 4 here refuses
   from any branch but ``card/N``.
3. **A line the other integration branch's own line took in passes as carried from it** -- a
   release, a resync, a branch that merged the other in -- as the landing check's rule 4 reads
   a marker's (:meth:`landing.Landing.carries`).
4. **Any other line only from ``card/N``, into its own file**: from it, any line naming a task.

The marker's rules 3, 7 and 8 are not applied.  Rule 3 would refuse a re-claim: a line
``plan#T`` is no claim's own, as a marker's line is, and a re-claim's ``quill start`` of a
finishing task its first claim began writes that very line again.  Rule 8 reads what ``dev``
and ``main`` together say, and no reader asks their merge which task is in progress.  And the
tracker is not read for the task a file names: ``quill start`` names only a task under N.  A
line naming another, written by hand, stamps the next commit with that task, which the hook's
backstop refuses (:mod:`tools.quill._stamps`); written as the claim's last change, it lands,
where no reader asks it anything.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from tools.quill import _git

if TYPE_CHECKING:
    from tools.quill.landing import Landing


def _refusal(landing: Landing, entry: _git.Entry, files: dict[str, int]) -> str | None:
    """Why ``entry``, an entry under ``plan/doing`` the change adds, changes or deletes, whose
    path ``files`` names as a card's file, by that card, or does not (rule 1), is refused (the
    module's rules); None when it passes."""
    if entry.blob is None:
        return None
    if (number := files.get(entry.path)) is None:
        return (f"{entry.path}: no card's file naming its task in progress is this (one is a "
                f"file, {_git.DOING}/<card number>), so no change may add or change it")
    if _git.doing_named(landing.root, entry.blob) is None:
        return (f"{entry.path}: what this change writes there names no task, which reads as "
                f"nothing in progress on {_git.card_branch(number)}, silently: the file is one "
                "line, plan#<task number>, as quill start writes it")
    if number == landing.claim or landing.carries(entry):
        return None
    return (f"{entry.path}: {landing.pull.head or 'a branch of another repository'} writes it, "
            f"but only card {number}'s own claim, {_git.card_branch(number)}, may add or change "
            "the file naming its task in progress (quill start writes it there), and a release "
            "or a resync carries one only through the merge that took it in")


def refused(landing: Landing, entries: Collection[_git.Entry]) -> list[str]:
    """One sentence for each of ``entries``, each an entry under ``plan/doing`` that
    ``landing``'s change adds, changes or deletes, that the module's rules refuse, in their
    order; a delete passes (rule 2).  Which entries are a card's file is M's tree's answer
    (:attr:`landing.Landing.lines`), read only when there are entries to judge.

    Raises:
        tools.ci.gitcmd.GitError: When git cannot read a line the change writes.
    """
    if not entries:
        return []
    files = {_git.doing_file(number): number for number in landing.lines[_git.DOING]}
    return [why for entry in entries if (why := _refusal(landing, entry, files)) is not None]
