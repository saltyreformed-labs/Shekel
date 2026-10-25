"""The fixture the release tool's tests share: a throwaway repository whose commits carry
files (``steps.md``, migrations, ship markers), built with :mod:`tools.ci.scratch`'s
isolated git; and the histories the card reader's tests build on it (a cut after the
cutover, a pull request merged as ``dev`` takes one writing ship markers, a
deploy-together group in quill's own fake)."""
from __future__ import annotations

import shutil
from itertools import count
from pathlib import Path

import pytest

from tools.ci import scratch
from tools.ci.scratch import run
from tools.quill.door import MARKERS, FakeTracker
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.release._carried import STEPS_PATH
from tools.release._plan import Inputs, plan

_HEADER = ("| arc | id | also | what this step does | order | commit | starts |\n"
           "| --- | --- | --- | --- | --- | --- | --- |\n")


def steps(*rows: tuple[str, ...]) -> str:
    """A ``steps.md`` holding one row per ``(key, sentence, order, commit)``, or per
    ``(key, sentence, order, commit, also)`` for a row naming the other names it has."""
    lines = []
    for key, sentence, order, commit, *also in rows:
        arc, ident = key.split(":", 1)
        lines.append(f"| {arc} | {ident} | {also[0] if also else '--'} | {sentence} | {order} "
                     f"| {commit} | -- |\n")
    return "# Steps\n\n" + _HEADER + "".join(lines)


def migration(revision: str, down: str | None) -> str:
    """A migration file's text declaring ``revision`` and its ``down_revision``."""
    parent = "None" if down is None else f'"{down}"'
    return f'"""A migration."""\nrevision = "{revision}"\ndown_revision = {parent}\n'


class Repo:
    """A repository whose commits are built from whole file sets, each commit's files its
    first parent's with the changes given (``None`` deletes a file)."""

    def __init__(self, root: Path) -> None:
        """An empty repository at ``root``."""
        self.root = root
        self._files: dict[str, dict[str, str]] = {}

    def commit(self, subject: str, files: dict[str, str | None] | None = None, *,
               ships: tuple[str, ...] = (), trailers: tuple[str, ...] = (),
               parents: tuple[str, ...] = ()) -> str:
        """A commit whose message carries a ``Ships:`` trailer per key in ``ships`` and
        each line of ``trailers``; its sha.  Deleting a file the first parent lacks is
        refused: it changes nothing, so a test written so could not fail (M3's review, its
        6: an undo of a marker that was never written)."""
        held = dict(self._files[parents[0]]) if parents else {}
        for path, text in (files or {}).items():
            if text is None:
                assert path in held, f"{path} is not in the parent: deleting it changes nothing"
                del held[path]
            else:
                held[path] = text
        for child in self.root.iterdir():
            if child.is_dir() and child != self.root / ".git":
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()
        for path, text in held.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, encoding="utf-8")
        run(self.root, "add", "-A")
        tree = run(self.root, "write-tree")
        message = ["-m", subject]
        block = [f"Ships: {key}" for key in ships] + list(trailers)
        if block:
            message += ["-m", "\n".join(block)]
        args = [arg for parent in parents for arg in ("-p", parent)]
        sha = run(self.root, "commit-tree", tree, *args, *message)
        self._files[sha] = held
        return sha

    def merge(self, onto: str, branch: str, subject: str = "Merge pull request") -> str:
        """A merge of ``branch`` into ``onto`` holding ``branch``'s files, as a pull request
        built on ``onto`` merges."""
        changes: dict[str, str | None] = {path: None for path in self._files[onto]}
        changes.update(self._files[branch])
        return self.commit(subject, changes, parents=(onto, branch))

    def plan(self, rows: tuple[tuple[str, ...], ...], *, parents: tuple[str, ...],
             ships: tuple[str, ...] = (), subject: str = "registry") -> str:
        """A commit writing ``steps.md`` as ``rows``."""
        return self.commit(subject, {STEPS_PATH: steps(*rows)}, ships=ships, parents=parents)


@pytest.fixture(name="repo")
def _repo(tmp_path):
    """A :class:`Repo` under tmp_path (never a real checkout)."""
    return Repo(scratch.repository(tmp_path))


# -- the card reader's histories -----------------------------------------------------------

MONEY = ("balance", MOVES_MONEY_LABEL)
#: Numbers no two writes share: each claim's marker line, as a claim records its instant, and
#: each pull request's change to ``app.py``.
_WRITES = count(1)


def ship(number: int) -> dict[str, str | None]:
    """Card ``number``'s marker written as a claim writes it, a line no other write of the
    run holds: added, or changed when the card shipped before."""
    return {f"{MARKERS}/{number}": f"claimed {next(_WRITES)}\n"}


def undo(number: int) -> dict[str, str | None]:
    """Card ``number``'s marker deleted, as a revert of its ship deletes it."""
    return {f"{MARKERS}/{number}": None}


def after_cutover(repo):
    """A base holding steps.md, then the cutover's commit deleting it: ``(base, cutover)``."""
    base = repo.plan((), parents=())
    return base, repo.commit("cutover", {STEPS_PATH: None}, parents=(base,))


def pull_request(repo, onto, *marks, files=None):
    """A pull request built on ``onto`` writing each of ``marks`` (:func:`ship`,
    :func:`undo`) and ``files`` (a change to ``app.py`` when none are given), merged into it
    as ``dev`` takes one: the merge."""
    changed = dict(files) if files is not None else {"app.py": str(next(_WRITES))}
    for mark in marks:
        changed.update(mark)
    work = repo.commit("work", changed, parents=(onto,))
    return repo.merge(onto, work)


def release_after(repo, shipped, carried):
    """A release after the cutover: ``(base, cut)``, the base a pull request on the
    cutover writing the marks ``shipped`` (the cutover itself when there are none), the
    cut one on the base writing ``carried``."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, *shipped) if shipped else cutover
    return base, pull_request(repo, base, *carried)


def judge(repo, base, cut, tracker, unrehearsed=()):
    """``plan`` over ``<base>..<cut>``, reaching ``tracker``."""
    return plan(repo.root, base, cut, Inputs(unrehearsed, (), lambda: tracker))


def deploy_group(tracker, container=50, leaves=(51, 52)):
    """A container labelled deploy-together and its leaves, linked under it."""
    tracker.add(container, labels=("balance", DEPLOY_TOGETHER_LABEL))
    for leaf in leaves:
        tracker.add(leaf)
        tracker.add_child(container, tracker.cards_by_number[leaf])


def replan(tracker, step, leaves):
    """``step`` split into ``leaves``, each a new step linked under it."""
    for leaf in leaves:
        tracker.add(leaf)
        tracker.add_child(step, tracker.cards_by_number[leaf])


def released_group(repo, leaves=(51, 52)):
    """Group #50 released whole at a base after the cutover: ``(tracker, base)``."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, *(ship(leaf) for leaf in leaves))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=leaves)
    return tracker, base


def replanned_and_dropped(tracker):
    """#51 re-planned as #511 and #512, then dropped as ``quill drop 51`` drops a split
    step: its drop written on each leaf still work, #51 left open (R-BAL190), so #51 is
    dropped because every leaf was (R-BAL187)."""
    replan(tracker, 51, (511, 512))
    for leaf in (511, 512):
        tracker.close(leaf, "not_planned")
