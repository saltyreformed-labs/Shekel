"""What the code repository's git history says about the plan's cards: the commit trailers.

**Whether a card shipped is git's answer, never the card's open or closed
state** (ruling ``balance:R-BAL170``; the card's state is display, which the
tracker tool's ``quill sync`` writes from this module's answer).  A card is
shipped when a commit on ``dev`` carries the trailer ``Ships: plan#N`` and no
LATER commit carries ``Reopens: plan#N``.

**"Later" is git's ancestry, not a date or the merge order** (ruling
``balance:R-BAL181``).  A ``Reopens:`` commit cancels every ``Ships:`` commit
for the same card in its own history -- the commits it was built on, itself
included -- and no other: a card re-shipped after it was reopened is shipped
again, and a ``Ships:`` commit on a branch the reopening never saw still
stands.  An undo (a revert) is always built on what it undoes, so a
``Reopens:`` that cancels nothing is a STRAY -- most likely a mistyped number
-- and is reported, never acted on.

**Only the trailer block is read, and git parses it** (``%(trailers)``, the
way ``tools/plan_gate/_shipped.py`` reads ``Ships: <arc>:<id>`` until step
X-cx's cutover, L8, deletes it): a sentence that mentions ``plan#7`` claims
nothing.  A value is ``plan#<number>``, or the old ``<arc>:<id>`` form the
plan gate reads, or it is reported as malformed.  An old-form trailer is
returned as its own record (:class:`OldFormClaim`) for the release tool,
which asks what a release carries over a range: a ``Ships:`` in
:attr:`History.old_form`, a ``Reopens:`` in :attr:`History.old_form_reopens`
(none exists, measured 2026-10-14 and 2026-10-23; after the cutover the release
tool's card reader refuses either when a cut holding ``steps.md`` holds it, ruling
``balance:R-BAL287``, and reads any other as prose).  Neither decides anything
here.  The plan gate's own walk (``_shipped._shipping_claims``) is the second
reader of the old-form ``Ships:``, and L8 deletes it with the plan gate's arms.

**A shallow clone is refused**: its history stops short, and every card
shipped before the cut would read as unshipped.

The rules live here, below both of their readers: the tracker tool asks what
shipped, and CI's ``commit_trailers`` check refuses a commit that breaks them.
Moved here from what is now ``tools/quill/_git.py`` by step X-cx's L4.  Nothing
here writes the repository.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tools.ci.arcs import ARCS
from tools.ci.gitcmd import GitError, git, run

SHIPS, REOPENS = "Ships", "Reopens"
#: The branch every shipped card's commit is on, as a message names it; :func:`tracking`
#: is its full name.
DEV = "origin/dev"
#: The branch a release merges into, as a message names it; :func:`tracking` is its full
#: name.
MAIN = "origin/main"
#: A trailer's value naming a card: ``plan#`` and the card's number.
_CARD = re.compile(r"plan#([1-9][0-9]*)")
#: The old form, ``<arc>:<id>``, which the plan gate reads until the cutover.
_OLD_FORM = re.compile(rf"(?:{'|'.join(ARCS)}):\S+")
_FIELD, _VALUE, _RECORD = "\x01", "\x02", "\x03"


@dataclass(frozen=True)
class Trailer:
    """One ``Ships:`` or ``Reopens:`` trailer naming a card."""

    key: str
    card: int
    sha: str
    subject: str


@dataclass(frozen=True)
class OldFormClaim:
    """One ``Ships: <arc>:<id>`` trailer, the form a step was claimed in before the
    cutover (ruling ``balance:R-BAL134``): the commit, the arc and the step's id; or,
    with ``trailer`` :data:`REOPENS`, one ``Reopens: <arc>:<id>``."""

    sha: str
    arc: str
    ident: str
    trailer: str = SHIPS

    @property
    def key(self) -> str:
        """``<arc>:<id>``, the step's key as the trailer wrote it."""
        return f"{self.arc}:{self.ident}"


@dataclass(frozen=True)
class History:
    """Every card trailer on a branch, every value that is neither ``plan#N`` nor the
    old ``<arc>:<id>`` form, every old-form ``Ships:`` claim, and every old-form
    ``Reopens:``."""

    trailers: tuple[Trailer, ...]
    malformed: tuple[str, ...]
    old_form: tuple[OldFormClaim, ...] = ()
    old_form_reopens: tuple[OldFormClaim, ...] = ()


def tracking(branch: str) -> str:
    """``branch`` (:data:`DEV` or :data:`MAIN`) by the full name of its remote-tracking
    reference, which no other reference can stand in for while the branch exists: git
    resolves the short name to the first reference it could name, so a tag or a local branch
    of that name comes before the remote-tracking branch (``git help revisions``), and CI's
    checkout fetches every tag.  A full name that does not exist git still resolves to a tag
    named like it, so whether the branch exists is asked of the exact reference."""
    return f"refs/remotes/{branch}"


def history(root: Path, *refs: str) -> History:
    """Every ``Ships:`` and ``Reopens:`` trailer naming a card, and every old-form
    ``Ships:`` and ``Reopens:``, on ``refs`` (:data:`DEV` when none is named).

    One ``git log`` over everything ``refs`` name together -- a branch, a range
    such as ``base..head``, or several tips, whose histories it reads as one,
    each commit once -- reading each message's trailer block as git parses it.

    Raises:
        GitError: when ``root`` is a shallow clone, whose history stops short.
    """
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise GitError(
            f"{root} is a shallow clone, so it cannot say what shipped: run "
            "`git fetch --unshallow origin` (in CI, check out with fetch-depth: 0)"
        )
    out = git(
        root, "log", *(refs or (DEV,)),
        f"--format=%H{_FIELD}%s{_FIELD}%(trailers:key={SHIPS},valueonly,separator=%x02)"
        f"{_FIELD}%(trailers:key={REOPENS},valueonly,separator=%x02){_RECORD}",
    )
    found: list[Trailer | OldFormClaim | str] = []
    for record in filter(str.strip, out.split(_RECORD)):
        sha, subject, ships, reopens = record.strip("\n").split(_FIELD)
        for key, values in ((SHIPS, ships), (REOPENS, reopens)):
            found += [_read(key, value, sha, subject)
                      for value in filter(None, (v.strip() for v in values.split(_VALUE)))]
    old = [read for read in found if isinstance(read, OldFormClaim)]
    return History(tuple(read for read in found if isinstance(read, Trailer)),
                   tuple(read for read in found if isinstance(read, str)),
                   tuple(claim for claim in old if claim.trailer == SHIPS),
                   tuple(claim for claim in old if claim.trailer == REOPENS))


def _read(key: str, value: str, sha: str, subject: str) -> Trailer | OldFormClaim | str:
    """What one trailer's value is: a card trailer; an old-form ``Ships:`` or ``Reopens:``;
    or the line reporting a value that is neither form."""
    card = _CARD.fullmatch(value)
    if card:
        return Trailer(key, int(card.group(1)), sha, subject)
    if not _OLD_FORM.fullmatch(value):
        return f"{sha[:12]} {key}: {value!r} is not plan#<number>"
    arc, _, ident = value.partition(":")
    return OldFormClaim(sha, arc, ident, key)


def is_ancestor(root: Path, older: str, newer: str) -> bool:
    """Whether ``older`` is in ``newer``'s history (a commit is in its own)."""
    done = run(root, "merge-base", "--is-ancestor", older, newer)
    if done.returncode not in (0, 1):
        raise GitError(f"git merge-base --is-ancestor {older} {newer}: {done.stderr.strip()}")
    return done.returncode == 0


def cancels(root: Path, undo: Trailer, claim: Trailer) -> bool:
    """Whether the ``Reopens:`` trailer ``undo`` cancels the ``Ships:`` trailer ``claim``: the
    same card, and ``claim``'s commit in ``undo``'s history (R-BAL181)."""
    return undo.card == claim.card and is_ancestor(root, claim.sha, undo.sha)


def shipped(root: Path, found: History) -> tuple[dict[int, tuple[Trailer, ...]],
                                                 tuple[Trailer, ...]]:
    """``{card: the Ships trailers still standing}`` for every card git says shipped,
    and the STRAY ``Reopens:`` trailers, which cancel no ``Ships:``.

    A ``Ships:`` trailer stands unless a ``Reopens:`` trailer for the same card
    sits on a commit whose history holds it (R-BAL181).  Whether a ``Reopens:``
    is a stray is asked of that trailer, never of its commit: one commit may
    reopen two cards, and cancel a ship for only one of them (BAL-608).
    """
    ships: dict[int, list[Trailer]] = {}
    reopens: dict[int, list[Trailer]] = {}
    for trailer in found.trailers:
        (ships if trailer.key == SHIPS else reopens).setdefault(trailer.card, []).append(trailer)
    standing, cancelling = {}, set()
    for card, claims in ships.items():
        live = []
        for claim in claims:
            undone = [undo for undo in reopens.get(card, ()) if cancels(root, undo, claim)]
            cancelling.update(undone)
            if not undone:
                live.append(claim)
        if live:
            standing[card] = tuple(live)
    strays = tuple(undo for undos in reopens.values() for undo in undos
                   if undo not in cancelling)
    return standing, strays
