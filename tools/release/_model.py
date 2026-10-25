"""What a release carries, in the shape the release tool's card reader answers it
(:mod:`tools.release._cards`): each piece of work in ``<base>..<cut>``, whether it moves
money and which deploy-together groups it is under, and what the reader cannot read
(step X-cx's L9a, T-a a1).

**One reader.**  Before the cutover a second one read the plan's registries for a cut
whose tree held ``steps.md``; step X-cx's S3b deleted it with them (ruling
``balance:R-BAL389``).  The cutover's freeze ends with a release, so production holds
every step an old-form ``Ships: <arc>:<id>`` claims (ruling ``R-BAL287``), and no
release after it carries a commit made before it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.ci.gitcmd import git


class ReleaseError(RuntimeError):
    """What a release carries cannot be read, so nothing about it is judged."""


def first_parent_line(root: Path, base: str, cut: str) -> list[tuple[str, str]]:
    """``(sha, subject)`` for each cut ``dev`` stood at in ``<base>..<cut>``, newest first,
    ``cut`` itself included: its first-parent line, one state per pull request merged."""
    out = git(root, "log", "--first-parent", "--format=%H%x01%s", f"{base}..{cut}")
    return [(sha, subject) for sha, _, subject in
            (entry.partition("\x01") for entry in out.splitlines() if entry)]


@dataclass(frozen=True)
class Step:
    """One piece of work a release carries: its key, the commits in the range that changed
    its ship marker (newest first), whether it moves money, the deploy-together groups it
    touches, nearest first (each it is under, its own card's group included, unless it will
    never ship and production holds it on neither side; a card re-planned since its marker
    landed, or a finding, is under a group without being a leaf of it), and whether the
    release takes it out of production: held there at the base and not at the cut (its
    ship marker deleted, ruling ``balance:R-BAL288``)."""

    key: str
    claims: tuple[str, ...]
    money: bool
    groups: tuple[str, ...]
    undone: bool = False


@dataclass(frozen=True)
class Group:
    """A deploy-together container a release touches, and each of its members (its leaves
    that can still ship, each step below it production holds at the cut, and each step
    below it that will never ship production holds at the base): carried into production
    by this release, released before it, held by production on neither side (missing), or
    taken out of production by it (undone); and each card the release names under it that
    is no member of it, with the steps between (``through``), and each member that will
    never ship (``dead``)."""

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
