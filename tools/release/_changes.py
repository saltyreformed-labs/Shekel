"""What each pull request a release carries changes of what production runs: the ``/release``
skill's approval question names each such change no rehearsal kit covered (ruling
``balance:R-BAL281``'s interim, as the developer confirmed it 2026-10-13).

**One reading of each.**  The pull requests are the cuts ``dev`` stood at in
``<base>..<cut>`` (:func:`tools.release._model.first_parent_line`), each read against its
first parent, so a pull request is named for its own change and never for what an earlier
one brought.  What production runs is CI's write rule's reading of it
(:func:`tools.quill.door.changed`, ``tools/quill/_production.py``: the files the image
holds, by the ignore file Docker reads, and the files outside it that build or run
production; rulings ``R-BAL362``, ``R-BAL364``, ``R-BAL400`` and ``R-BAL401``), so the
skill keeps no list of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tools.quill.door import changed
from tools.release._model import first_parent_line


@dataclass(frozen=True)
class Change:
    """One pull request of a release: its merge's sha and subject, and each path where its
    own change changes what production runs (none when it changes nothing of it)."""

    sha: str
    subject: str
    paths: tuple[str, ...]


def changes(root: Path, base: str, cut: str) -> tuple[Change, ...]:
    """Each pull request of ``<base>..<cut>``, newest first, with what its merge changes of
    what production runs against the merge's first parent.

    Raises:
        tools.quill.door.IgnoreFileError: when an ignore file either side of a merge holds
            a pattern Docker refuses.
        tools.ci.gitcmd.GitError: when git cannot read the range or a commit.
    """
    return tuple(Change(sha, subject, changed(root, f"{sha}^1", sha))
                 for sha, subject in first_parent_line(root, base, cut))
