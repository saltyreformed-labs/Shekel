"""What a release carries, in the shape both of the release tool's readers answer it: each
piece of work in ``<base>..<cut>``, whether it moves money and which deploy-together
groups it is a leaf of, and what neither reader can read (step X-cx's L9a, T-a a1).

**One reader per release, chosen by its cut** (T-a a1): a cut whose tree holds
``steps.md`` is before the cutover (step X-cx's L8), and its steps are the registries'
(:mod:`tools.release._carried`); a cut whose tree holds none is after it, and its work
is the tracker's cards (:mod:`tools.release._cards`).  The cutover's freeze ends with a
release, so production holds every step an old-form ``Ships: <arc>:<id>`` claims before
the cutover's deleting pull request merges (ruling ``balance:R-BAL287``), and no
release needs both.  L8 deletes ``_carried`` and the choice; this module and ``_cards``
remain.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.ci.arcs import REPO
from tools.ci.gitcmd import git, run
from tools.plan_gate.registries import STEPS

#: ``steps.md``'s path inside a commit's tree, which tells a cut before the cutover from
#: one after it.
STEPS_PATH = STEPS.relative_to(REPO).as_posix()


class ReleaseError(RuntimeError):
    """What a release carries cannot be read, so nothing about it is judged."""


def steps_blob(root: Path, commit: str) -> str | None:
    """The blob ``steps.md`` is in ``commit``'s tree; None when the tree holds none (the
    commit is after the cutover)."""
    found = run(root, "rev-parse", "--verify", "--quiet", f"{commit}:{STEPS_PATH}")
    return None if found.returncode else found.stdout.strip()


def first_parent_line(root: Path, base: str, cut: str) -> list[tuple[str, str]]:
    """``(sha, subject)`` for each cut ``dev`` stood at in ``<base>..<cut>``, newest first,
    ``cut`` itself included: its first-parent line, one state per pull request merged."""
    out = git(root, "log", "--first-parent", "--format=%H%x01%s", f"{base}..{cut}")
    return [(sha, subject) for sha, _, subject in
            (entry.partition("\x01") for entry in out.splitlines() if entry)]


@dataclass(frozen=True)
class Step:
    """One piece of work a release carries: its key, the commits in the range that name it
    (newest first; after the cutover, those that changed its ship marker), whether it moves
    money (None when no version holds it open and it is not declared), the deploy-together
    groups it is a leaf of (after the cutover, each it touches, nearest first: each it is
    under, its own card's group included, unless it will never ship and production holds
    it on neither side; a card re-planned since its marker landed, or a finding, is under a
    group without being a leaf of it), the
    source of its declaration when its flag was declared (None when it was read), and
    whether the release takes it out of production: held there at the base and not at
    the cut (its ship marker deleted, ruling ``balance:R-BAL288``)."""

    key: str
    claims: tuple[str, ...]
    money: bool | None
    groups: tuple[str, ...]
    declared: str | None = None
    undone: bool = False


@dataclass(frozen=True)
class Group:
    """A deploy-together container a release touches, and each of its members (its leaves;
    after the cutover, its leaves that can still ship, each step below it production holds
    at the cut, and each step below it that will never ship production holds at the base):
    carried into production by this release, released before it, held by production on
    neither side (missing), or taken out of production by it (undone); and, after the
    cutover, each card the release names under it that is no member of it, with the steps
    between (``through``), and each member that will never ship (``dead``)."""

    key: str
    carried: tuple[str, ...]
    released: tuple[str, ...]
    missing: tuple[str, ...]
    undone: tuple[str, ...] = ()
    through: tuple[str, ...] = ()
    dead: tuple[str, ...] = ()


@dataclass(frozen=True)
class Carried:
    """What ``<base>..<cut>`` carries: its work, the groups it touches, and each thing its
    reader cannot read."""

    steps: tuple[Step, ...]
    groups: tuple[Group, ...]
    unreadable: tuple[str, ...]
