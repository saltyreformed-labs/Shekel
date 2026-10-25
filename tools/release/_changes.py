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
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.quill.door import changed
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
class Changes:
    """What a release changes of what production runs: ``net``, each path where production
    built from the cut differs from production built from the base, in order; and ``pulls``,
    each pull request, newest first, with its own part of it."""

    net: tuple[str, ...]
    pulls: tuple[Change, ...]

    @property
    def unowned(self) -> tuple[str, ...]:
        """Each path of :attr:`net` no pull request's own change changes, in order."""
        owned = {path for pull in self.pulls for path in pull.paths}
        return tuple(path for path in self.net if path not in owned)


def changes(root: Path, base: str, cut: str) -> Changes:
    """What the release ``<base>..<cut>`` changes of what production runs, and each pull
    request's own part of it.

    Raises:
        tools.quill.door.IgnoreFileError: when an ignore file at the base, the cut or either
            side of a merge holds a pattern Docker refuses.
        tools.ci.gitcmd.GitError: when git cannot read the range or a commit.
    """
    net = changed(root, base, cut)
    pulls = tuple(Change(sha, subject,
                         tuple(path for path in changed(root, f"{sha}^1", sha) if path in net))
                  for sha, subject in first_parent_line(root, base, cut))
    return Changes(net, pulls)
