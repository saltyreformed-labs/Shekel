"""What a release changes of what production runs, and which pull request's own change
changed each file: the ``/release`` skill's approval question names each such change no
rehearsal kit covered (ruling ``balance:R-BAL281``'s interim, as the developer confirmed it
2026-10-13).

**The release's change is ``<base>`` against ``<cut>``.**  The skill pins ``base`` at the
commit production's image was built from, and releases the cut's own tree: it pushes the cut
itself, or refuses a release branch whose tree is not the cut's (its step 6).  So what
production gets is where production built from the cut differs from production built from
the base, read by CI's write rule's one reading of it (:func:`tools.quill.door.changed`,
``tools/quill/_production.py``: the files the image holds, by the ignore file Docker reads,
and the files outside it that build or run production; rulings ``R-BAL362``, ``R-BAL364``,
``R-BAL400`` and ``R-BAL401``), and the skill keeps no list of its own.

**Each pull request is named for its own part of that change**: its merge against the
merge's first parent (the cuts ``dev`` stood at, :func:`tools.release._model.first_parent_line`),
kept only where the release changes the file.  A merge of ``main`` that brings a hotfix
production already runs names nothing of it.  A file the release changes that no pull
request's own change touches is named apart (:attr:`Changes.unowned`): a merge's resolution
that kept or dropped a side, or an ignore-file edit meeting a file the other branch brought;
the sum of the pull requests' own changes would miss it.

**And each version it moves** (:attr:`Changes.versions`, ruling plan#2351): a change moving
pinned versions alone lands with no card, so no card the approval question names carries it.
Each file that pins versions the release changes is read on each side for what it pins
(:func:`tools.quill.door.pinned`), by the lock reader and the line patterns the landing check
reads a move with (``tools/quill/_versions.py``), and never asked whether the change is
version-only: a release carries cards' own edits to the Dockerfile and the lock beside what an
update moved.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tools.quill.door import Pin, changed, pinned, pins_versions
from tools.release._model import first_parent_line


@dataclass(frozen=True)
class Change:
    """One pull request of a release: its merge's sha and subject, and each path the release
    changes of what production runs that its own change changes (none when it changes none
    of them)."""

    sha: str
    subject: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Moved:
    """One thing whose versions a release moves in a file production runs that pins
    versions: ``what`` (a package, an image, an action) in ``path``, at each version
    production built from the base pins it at (``old``) and each production built from the
    cut does (``new``), each sorted, empty where that side pins it at none."""

    path: str
    what: str
    old: tuple[str, ...]
    new: tuple[str, ...]


@dataclass(frozen=True)
class Changes:
    """What a release changes of what production runs: ``net``, each path where production
    built from the cut differs from production built from the base, in order; ``pulls``,
    each pull request, newest first, with its own part of it; and ``versions``, each thing
    whose versions it moves, by path and then by what it is."""

    net: tuple[str, ...]
    pulls: tuple[Change, ...]
    versions: tuple[Moved, ...] = ()

    @property
    def unowned(self) -> tuple[str, ...]:
        """Each path of :attr:`net` no pull request's own change changes, in order."""
        owned = {path for pull in self.pulls for path in pull.paths}
        return tuple(path for path in self.net if path not in owned)


def _by_what(pins: Iterable[Pin]) -> dict[str, tuple[str, ...]]:
    """The versions ``pins`` holds, by what each pins, each sorted."""
    found: dict[str, list[str]] = {}
    for pin in pins:
        found.setdefault(pin.what, []).append(pin.version)
    return {what: tuple(sorted(versions)) for what, versions in found.items()}


def _moved(root: Path, path: str, was: str | None, now: str | None) -> tuple[Moved, ...]:
    """Each thing whose versions differ in ``path`` between ``was`` and ``now``, its mode and
    object in production built from the base and from the cut (as :func:`changed` gives
    them), by what it is.

    Raises:
        tools.quill.door.NotVersions: when either side is no file, or a lock that is no TOML
            or holds a package with no name.
        tools.ci.gitcmd.GitError: when git cannot read either.
    """
    old, new = (_by_what(pinned(root, path, held)) for held in (was, now))
    return tuple(Moved(path, what, old.get(what, ()), new.get(what, ()))
                 for what in sorted(old.keys() | new.keys()) if old.get(what) != new.get(what))


def changes(root: Path, base: str, cut: str) -> Changes:
    """What the release ``<base>..<cut>`` changes of what production runs, each pull
    request's own part of it, and each version it moves.

    Raises:
        tools.quill.door.IgnoreFileError: when an ignore file at the base, the cut or either
            side of a merge holds a pattern Docker refuses.
        tools.quill.door.NotVersions: as :func:`_moved` raises it.
        tools.ci.gitcmd.GitError: when git cannot read the range or a commit.
    """
    production = changed(root, base, cut)
    net = tuple(production)
    pulls = tuple(Change(sha, subject,
                         tuple(path for path in changed(root, f"{sha}^1", sha) if path in net))
                  for sha, subject in first_parent_line(root, base, cut))
    versions = tuple(moved for path, (was, now) in production.items() if pins_versions(path)
                     for moved in _moved(root, path, was, now))
    return Changes(net, pulls, versions)
