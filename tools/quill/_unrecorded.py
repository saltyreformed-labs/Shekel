"""CI's landing check, its rule 9 (rulings ``balance:R-BAL362``, ``R-BAL364``, ``R-BAL400`` and
``R-BAL401``, narrowed by plan#2351), as ``tools/quill/landing.py``'s module docstring states
it: a change to what production runs must add, change or delete a ship marker, unless it
moves pinned versions alone.

What production runs is :mod:`tools.quill._production`'s reading of a commit, and what a
change moves of its pinned versions :mod:`tools.quill._versions`'; this module judges the
change with both and says why it is refused (:func:`refused`).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from tools.quill import _git, _production, _versions

if TYPE_CHECKING:
    from tools.quill.landing import Landing

#: How many of production's changed paths, and of the reasons a change moves more than
#: versions, the sentence names before it counts the rest.
_NAMED = 5
#: The files outside the image that the sentence names, as :mod:`_production` reads them.
_OUTSIDE = (f"{', '.join(_production.RECIPE)} or {_production.DEPLOY}/ (a "
            f"{_production.README} there excepted)")


def _named(items: tuple[str, ...], joined: str) -> str:
    """At most :data:`_NAMED` of ``items``, each after the first ``joined`` to the one before,
    and how many more there are."""
    more = f" and {len(items) - _NAMED} more" if len(items) > _NAMED else ""
    return f"{joined.join(items[:_NAMED])}{more}"


def _unversioned(landing: Landing,
                 changed: Mapping[str, tuple[str | None, str | None]]) -> tuple[str, ...]:
    """Why ``landing``, which changes production at ``changed`` (:func:`_production.changed`),
    is no change that moves versions alone (ruling plan#2351), each as a clause; none when it
    is one: it changes no plan record (:attr:`landing.Landing.records`), and each path it
    changes there moves pinned versions alone, none across a major
    (:func:`_versions.moves`).

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read a file it changes there.
    """
    reasons = [f"it changes {entry.path} too, a plan record"
               for entries in landing.records.values() for entry in entries]
    for path, (was, now) in changed.items():
        try:
            reasons += [f"{path} moves {move.what} from {move.old} to {move.new}, a new major "
                        "version" for move in _versions.moves(landing.root, path, was, now)
                        if move.major]
        except _versions.NotVersions as error:
            reasons.append(str(error))
    return tuple(reasons)


def refused(landing: Landing) -> list[str]:
    """One sentence when ``landing`` writes or deletes no entry under :data:`_git.MARKERS`,
    production built from M differs from production built from M's first parent
    (:func:`_production.changed`), and the change does more than move pinned versions
    (:func:`_unversioned`), naming the paths where production differs and, where one of them
    is a file that pins versions (:data:`_versions.PINNED`), what more it does; none
    otherwise.

    Raises:
        _production.IgnoreFileError: when the ignore file either commit holds is one Docker
            refuses.
        tools.ci.gitcmd.GitError: when git cannot read either commit, or a file it changes.
    """
    if landing.records[_git.MARKERS]:
        return []
    if not (changed := _production.changed(landing.root, landing.onto, landing.merge)):
        return []
    if not (reasons := _unversioned(landing, changed)):
        return []
    pinned = (f", and only a change that moves pinned versions alone, writing no plan record, "
              f"lands with none (ruling plan#2351): here {_named(reasons, '; ')}"
              if _versions.PINNED & changed.keys() else "")
    return [f"{landing.pull.head or 'a branch of another repository'} changes what production "
            f"runs ({_named(tuple(changed), ', ')}), but adds, changes or deletes no ship "
            "marker, so no release would count it: a change to a file the production image "
            f"holds, {_OUTSIDE} lands from a card's claim (quill claim <card>), whose marker "
            f"records it{pinned}"]
