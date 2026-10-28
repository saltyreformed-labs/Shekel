"""``quill watch``: the board shows a push within a minute (rulings plan#1871, plan#1874).

It runs every minute on this host (``shekel-plan-watch.timer``, its units' sources in
``/opt/docker/scripts``), from ``quill sync``'s own clone at ``dev``'s tip, the two never at
once: each unit runs quill through ``shekel-plan-quill.sh``, which holds the clone's lock for
the whole run.  ONE ``git ls-remote`` of ``origin``'s ``dev``, ``main`` and ``card/*``
(:func:`_git.origin_tips`) is compared with the tips the last finished pass saw, which it keeps
in the clone's git directory as the refs ``refs/quill/watched/<branch>`` (:data:`WATCHED`):
refs, so each tip's commit stays in the clone for the next pass to compare, and one transaction
writes them all (:func:`_save`).  When none moved it ends there, having asked GitHub nothing:
``quill`` connects to the tracker for it only past that point (``quill.main``).

Else it reads git as every command does (:func:`_git.read`, from that one listing) and decides
by sync's own producer (:mod:`_sync`) the open or closed state and the board's Status
(:func:`_status.sync_statuses`) of each card a moved branch names (:func:`_moved`): each whose
``card/N`` appeared, moved or went, and, for ``dev`` or ``main`` moved, each whose ship marker
or task file changed between the tip it saw and the tip fetched; with every card a decision
over them reads (:func:`_sync.decided_over`).  The tips fetched are saved then, and only then,
so a pass a failure cut short, or a ``--dry-run``, leaves its moves to the next pass.  With
``dev``'s or ``main``'s tip not saved (its first pass), it saw nothing, so every card moved: it
decides over sync's whole set (:func:`_sync.every_card`) and the Status of every card the board
holds.

It is a pass of sync's producer over fewer cards, never a second producer.  The 3-hourly
``quill sync`` stays: it is the pass that sees a tracker edit no push makes, puts a task on the
board (:func:`_tasks.put_on_board`), undoes a split a leaf closed while being filed, and names
each entry under ``plan/ships`` or ``plan/tasks`` that is no card's.  It prints a REPORT line
as ``sync`` does, and exits 0 on it: a REPORT is logged here and alerts from the 3-hourly sync
only (ruling plan#1907), so every other exit status is a failure its unit alerts on, a crash's
too (Python exits 1 on an exception no door catches).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from tools.ci.gitcmd import git
from tools.quill import _git, _status, _sync
from tools.quill._tracker import Tracker

#: Where a pass keeps the tip of each branch it saw, as ``<WATCHED><branch>``.
WATCHED = "refs/quill/watched/"


def _saved(root: Path) -> dict[str, str]:
    """Each branch's tip the last finished pass saw (:data:`WATCHED`), by the branch's name."""
    out = git(root, "for-each-ref", "--format=%(objectname) %(refname)", WATCHED)
    return {ref.removeprefix(WATCHED): sha
            for sha, _, ref in (line.partition(" ") for line in out.splitlines())}


def _fetched(root: Path, names: Iterable[str]) -> dict[str, str]:
    """The tip :func:`_git.read`'s fetch brought for each branch ``names`` names: its tracking
    ref ``refs/remotes/origin/<branch>``, by the branch's name."""
    names = sorted(names)
    out = git(root, "rev-parse", *(f"refs/remotes/origin/{name}" for name in names))
    return dict(zip(names, out.split(), strict=True))


def _save(root: Path, tips: Mapping[str, str], was: Mapping[str, str]) -> None:
    """Keep ``tips`` as the tips this pass saw, deleting each branch ``was`` holds that ``tips``
    lacks: one ``git update-ref --stdin``, which makes all of them or none."""
    lines = [f"update {WATCHED}{name} {sha}" for name, sha in sorted(tips.items())]
    lines += [f"delete {WATCHED}{name}" for name in sorted(set(was) - set(tips))]
    git(root, "update-ref", "--stdin", stdin="".join(f"{line}\n" for line in lines))


def _moved(root: Path, was: Mapping[str, str], now: Mapping[str, str]) -> set[int]:
    """The cards a branch that moved from ``was`` (the tips saved, ``dev``'s and ``main``'s
    among them) to ``now`` (the tips fetched) names, each move printed: a ``card/N`` that
    appeared, moved or went names N; ``dev`` or ``main`` moved names each card whose marker,
    and each task whose file, the change from its old tip to its new adds, changes or deletes
    (:func:`_git.changes`)."""
    seeds = set()
    for name in sorted(set(was) | set(now)):
        old, new = was.get(name), now.get(name)
        if old == new:
            continue
        if (card := _git.claimed_card(name)) is not None:
            seeds.add(card)
            print(f"{name} " + (f"appeared at {_git.shown(new)}" if old is None else
                                f"went (it was at {_git.shown(old)})" if new is None else
                                f"moved: {_git.shown(old)} -> {_git.shown(new)}"))
            continue
        paths = [entry.path for entry in _git.changes(root, old, new, _git.MARKERS, _git.TASKS)]
        seeds.update(number for path in paths
                     for number in (_git.card_of(path), _git.task_of(path)) if number is not None)
        print(f"{name} moved: {_git.shown(old)} -> {_git.shown(new)}, changing "
              + (", ".join(paths) or "no marker or task file"))
    return seeds


def cmd_watch(args, connect: Callable[[], Tracker], root: Path) -> int:
    """One pass (the module docstring): nothing when no branch moved since the last finished
    pass; else the state and the board's Status of each card a moved branch names, by sync's
    producer, each REPORT line printed, then the tips fetched saved -- not under ``--dry-run``,
    which prints each write unmade.  ``connect`` reaches the tracker, called only once a branch
    moved.  0 once the pass is done, its REPORT lines among its output (ruling plan#1907)."""
    listed = _git.origin_tips(root)
    was = _saved(root)
    if listed == was:
        return 0
    tracker = connect()
    reads = _git.read(root, listed)
    now = _fetched(root, listed)
    if all(name in was for name in _git.INTEGRATION):
        decided = _sync.decided_over(tracker, reads, _moved(root, was, now))
        scope = set(decided.cards)
    else:
        print("no tips saved, so every card moved: a pass over every card, as sync's")
        decided, scope = _sync.every_card(tracker, reads, _git.ever_marked(root)), None
    _sync.write_states(tracker, decided, reads.shipped.cards, args.dry_run)
    for line in _status.sync_statuses(tracker, decided.cards, reads, args.dry_run, scope):
        print(f"REPORT: {line}")
    if not args.dry_run:
        _save(root, now, was)
    return 0
