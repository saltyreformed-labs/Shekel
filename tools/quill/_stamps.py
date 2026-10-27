"""CI's backstop to the commit hook (ruling plan#1872; plan#1861's spec, item 5): a card's pull
request is refused while a commit it brings names no task under the card.

**Each commit made on ``card/N`` names the task under N it builds**, by the trailer ``Task:
plan#T`` the commit hook stamps (``tools/quill/commit_hook.py``).  A hook can be skipped -- a
commit made with both ``--no-verify`` and the editor, a clone whose hooks were never
installed, work committed on another branch and merged in -- so CI's landing check judges the
claim's commits again where its pull request lands (``tools/quill/landing.py``, on a change
from ``card/N`` recording card N, beside its open-task arm).  Each of the claim's own commits
(:func:`_git.stamps`: those neither ``dev`` nor ``main`` holds) is refused by :func:`unnamed`
when it names no task, and for each card it names that is no task under N, as the tracker says
(:func:`_state.names_no_task`, the one reading of which card a task is under).  Never held to
it (:func:`_work`):

- a merge, which builds nothing of its own;
- the claim's commit, which ``quill claim`` makes from objects alone, so no hook runs: a commit
  of one parent whose whole change writes the card's marker (on ``dev``, or with ``--here`` on
  the commit checked out), and every commit it was made on, ``--here``'s work already started,
  which was made on no ``card/N``.  Where several such commits are, those no other grew from
  bound the claim, so a commit later rewriting the marker alone frees nothing made before it;
- a commit whose tree does not hold the hook's code (:data:`HOOK`), which pre-commit's entry
  runs from the working tree (``python -m tools.quill.commit_hook``), so no hook stamped it: a
  branch claimed before the hook landed is held from the commits made once it takes the hook
  in (the spec's item 12).  A tree holding the code is held whatever its pre-commit
  configuration says, so a hook taken out of it fails closed.

The tracker is read only for a card a commit names that the reads before this one did not
bring: none while each names a task under N, which the open-task arm reads.
"""
from __future__ import annotations

from pathlib import Path

from tools.ci.gitcmd import is_ancestor
from tools.quill import _git, commit_hook
from tools.quill._state import names_no_task
from tools.quill._tracker import BareCard, CardReader

#: The hook's code as a tree holds it: the module pre-commit's entry runs (``python -m
#: tools.quill.commit_hook``), at the path Python reads that name from in the working tree.
HOOK = f"{commit_hook.__name__.replace('.', '/')}.py"


def _claims(root: Path, stamp: _git.Stamp, card: int) -> bool:
    """Whether ``stamp`` is a claim of card ``card`` as ``quill claim`` makes one
    (:func:`_git.claim`): a commit of one parent whose whole change against it writes the
    card's marker.

    Raises:
        tools.ci.gitcmd.GitError: When git cannot read the commit's change.
    """
    if len(stamp.parents) != 1:
        return False
    written = _git.changes(root, stamp.parents[0], stamp.sha)
    return [(entry.path, entry.blob is not None) for entry in written] == [
        (_git.marker(card), True)]


def _work(root: Path, head: str, card: int) -> list[_git.Stamp]:
    """Each of the claim at ``head``'s own commits held to naming its task (the module's
    list), oldest first: neither a merge, nor a claim of card ``card`` nor a commit one no
    other grew from was made on, and its tree holding :data:`HOOK`.

    Raises:
        tools.ci.gitcmd.GitError: When git cannot read the claim's commits.
    """
    own = [stamp for stamp in _git.stamps(root, head) if len(stamp.parents) < 2]
    claims = {stamp.sha for stamp in own if not stamp.tasks and _claims(root, stamp, card)}
    first = [sha for sha in claims
             if not any(other != sha and is_ancestor(root, other, sha) for other in claims)]
    return [stamp for stamp in reversed(own) if stamp.sha not in claims
            and not any(is_ancestor(root, stamp.sha, sha) for sha in first)
            and _git.blob_at(root, stamp.sha, HOOK) is not None]


def unnamed(reader: CardReader, root: Path, head: str, card: BareCard,
            held: dict[int, BareCard]) -> list[str]:
    """One sentence for each commit of the claim at ``head`` held to naming its task
    (:func:`_work`) that names none, and for each card one names that is no task under
    ``card``, the claim's card (:func:`_state.names_no_task`), oldest commit first.  ``held``
    holds the cards read so far: each card a commit names that it lacks is read through
    ``reader`` in one read and added to it, and one the tracker lacks is named as no card.

    Raises:
        tools.ci.gitcmd.GitError: When git cannot read the claim's commits.
        _tracker.TrackerError, _github.GitHubError, requests.RequestException: When the
            tracker cannot be read, as :meth:`_tracker.CardReader.cards` raises them.
    """
    work, branch = _work(root, head, card.number), _git.card_branch(card.number)
    if lacked := {number for stamp in work for number in stamp.tasks} - set(held):
        held.update(reader.cards(lacked))
    reasons = []
    for stamp in work:
        commit = f"commit {stamp.sha[:12]} ({stamp.subject})"
        if not stamp.tasks:
            reasons.append(
                f"{commit}: {branch} brings it, and it names no task, though its tree holds the "
                f"commit hook, which stamps each commit of {branch} with the task under "
                f"plan#{card.number} in progress (`{_git.TASK_TRAILER}: plan#T`): reword it to "
                f"name the task it builds, then push {branch} again")
        reasons += [f"{commit}: {branch} brings it, naming plan#{number}, but {why}, so it "
                    f"builds no piece of plan#{card.number}: reword it to name the task under "
                    f"plan#{card.number} it builds, then push {branch} again"
                    for number in dict.fromkeys(stamp.tasks)
                    if (why := names_no_task(number, held, card.number))]
    return reasons
