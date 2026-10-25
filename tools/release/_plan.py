"""A release's verdict: what refuses it, what the developer's approval question must name,
and, when it is refused or carries two migrations, the newest earlier cut to offer.

The rules are card ``plan#9``'s and the developer's (rulings ``balance:R-BAL279`` -
``R-BAL281``, ``R-BAL287`` and ``R-BAL288``):

- **Refused** (exit 1): two steps that move money, an undone one counting as one
  (R-BAL288); a deploy-together group the release leaves production holding in part
  (each member it lacks named; R-BAL288: wholly in production or wholly out); each thing
  the card reader cannot read (nothing is declared, R-BAL287); and each money mover or
  added migration the skill names UNREHEARSED, because it found no rehearsal kit for it
  (R-BAL281's interim, as the developer confirmed it 2026-10-13: such a release is
  refused in R-BAL280's shape).  Whether a kit exists is the skill's judgment, so it is
  an input.
- **Needs the approval question to name it** (exit 3): two or more added migrations; and
  every migration already on ``main`` that the release edits or deletes.
- Otherwise clean (exit 0).

**The cut to offer** (R-BAL280): the candidates are the cuts ``dev`` actually stood at,
the first-parent line of ``<base>..<cut>``, newest first.  On a refusal, the newest that
is not refused; when the cut adds two or more migrations, separately the newest that is
not refused and adds at most one ("cut before the second").  Each candidate is judged
by the same rules, with the same unrehearsed names.

That line is the states ``dev`` stood at only when the cut is ``dev``'s tip, as the
skill pins it, or a cut this command offered: ``dev`` takes each pull request as a merge
commit (measured 2026-10-13: the 7 first-parent commits of ``dev`` from the 10-09-b
release to pull request #555 are all merges, one per pull request), so each candidate is
``dev`` after a whole pull request.  Cut at a feature branch's tip, the line is that
branch's own commits, and an offered cut can hold a leaf's code without its claim.

**Every candidate is judged with what the release's cut knows**
(:class:`~tools.release._cards.Cards`): the cards the window of any cut on the cut's
first-parent line names (every candidate's), read from the tracker once, so a card the
cut reads as a money mover is one at every candidate.  A name the skill gave as
unrehearsed refuses every candidate carrying it, and a candidate that adds a migration
the cut does not add is refused, since the skill judged no rehearsal of it.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from tools.ci.gitcmd import git
from tools.quill.door import Tracker
from tools.release import _cards
from tools.release._migrations import ADDED, Migration, migrations
from tools.release._model import Carried, Group, ReleaseError, first_parent_line

CLEAN, REFUSED, NEEDS_APPROVAL = 0, 1, 3


@dataclass(frozen=True)
class Verdict:
    """One cut judged: what it carries, its migrations, why it is refused and what the
    approval question must name."""

    cut: str
    subject: str
    carried: Carried
    migrations: tuple[Migration, ...]
    refusals: tuple[str, ...]
    approvals: tuple[str, ...]

    @property
    def status(self) -> int:
        """:data:`REFUSED`, :data:`NEEDS_APPROVAL` or :data:`CLEAN`."""
        if self.refusals:
            return REFUSED
        return NEEDS_APPROVAL if self.approvals else CLEAN

    @property
    def added(self) -> tuple[Migration, ...]:
        """The migrations this cut adds."""
        return _added(self.migrations)


def _added(changed: tuple[Migration, ...]) -> tuple[Migration, ...]:
    """The migrations of ``changed`` that are added."""
    return tuple(m for m in changed if m.change == ADDED)


@dataclass(frozen=True)
class Plan:
    """A release judged: its base, its cut's verdict, the newest earlier cut that passes
    (None unless the cut is refused, or when none does) and the newest earlier passing
    cut adding at most one migration (None unless the cut adds two or more, or when
    none does)."""

    base: str
    verdict: Verdict
    passing: Verdict | None
    one_migration: Verdict | None


@dataclass(frozen=True)
class Inputs:
    """What the skill gives a judgment: each money mover or added migration it found no
    rehearsal kit for; and how to reach the tracker, which every judgment reads.  No
    connection is made unless one is given: only the command line, run as ``python -m``,
    gives quill's App (:data:`tools.quill.quill.CONNECT`)."""

    unrehearsed: Collection[str] = ()
    connect: Callable[[], Tracker] | None = None


def _split(group: Group) -> str | None:
    """Why a cut leaves production holding part of ``group``; None when, released, it
    leaves production holding every member or none (R-BAL288).  A member the plan will
    never ship is named with the way out no plan can block: reverting the merge that landed
    each member the cut keeps, which deletes its ship marker with its code and leaves
    production holding none of the group (every step below a group that production holds
    at a cut is a member of it).  Never "delete the marker": one deleted
    by hand leaves the code in production unseen (M3's review, its 4)."""
    held = group.carried + group.released
    if not held or not group.undone + group.missing:
        return None
    if not group.undone and not group.carried:
        why = (f"it leaves production holding {', '.join(held)} of the deploy-together group "
               f"{group.key} without {', '.join(group.missing)}"
               + (f" (it names {', '.join(group.through)}, under the group but no leaf of it)"
                  if group.through else ""))
    elif not group.undone:
        why = (f"it carries part of the deploy-together group {group.key} "
               f"({', '.join(group.carried)}) without {', '.join(group.missing)}")
    else:
        why = (f"it leaves {', '.join(group.undone)} of the deploy-together group {group.key} "
               f"out of production (its marker deleted) and keeps {', '.join(held)}"
               + (f", without {', '.join(group.missing)}" if group.missing else ""))
    if group.dead:
        why += (f"; a step the plan will never ship ({', '.join(group.dead)}) is a member "
                "while production holds it, at the base or the cut: a release that leaves "
                "production holding none of the group is whole (reverting the merge that "
                f"landed each of {', '.join(held)})")
    return why + "; the group ships whole"


def _refusals(found: Carried, changed: tuple[Migration, ...], unrehearsed: Collection[str],
              cut_adds: Collection[str]) -> list[str]:
    """Why a cut carrying ``found`` and changing the migrations ``changed`` is refused;
    empty when it is not.  ``cut_adds`` holds the revisions the release's own cut adds:
    an earlier cut adding any other was never put to the skill's rehearsal judgment."""
    reasons = list(found.unreadable)
    movers = [step.key for step in found.steps if step.money]
    if len(movers) > 1:
        reasons.append(f"it carries {len(movers)} steps that move money ({', '.join(movers)}); "
                       "a release carries one at most")
    reasons += [why for why in map(_split, found.groups) if why]
    reasons += [f"{key} moves money and has no rehearsal kit" for key in movers
                if key in unrehearsed]
    reasons += [f"migration {m.revision} ({m.path}) has no rehearsal kit"
                for m in _added(changed) if m.revision in unrehearsed]
    reasons += [f"it adds migration {m.revision} ({m.path}), which the release's cut does "
                "not add, so no rehearsal of it was judged" for m in _added(changed)
                if m.revision not in cut_adds]
    return reasons


def _approvals(changed: tuple[Migration, ...]) -> list[str]:
    """What the approval question must name for a cut changing the migrations ``changed``:
    two or more added migrations, and each migration already on main it edits or
    deletes."""
    added = _added(changed)
    named: list[str] = []
    if len(added) > 1:
        named.append(f"it adds {len(added)} migrations: "
                     + ", ".join(f"{m.revision} ({m.path})" for m in added))
    named += [f"it {m.change.lower()} migration {m.revision} ({m.path}), which is already "
              "on main and which production never re-runs" for m in changed
              if m.change != ADDED]
    return named


class Judge:
    """Judges the release ``<base>..<cut>`` and the earlier cuts offered in its place, each
    with what the release's cut knows (:class:`~tools.release._cards.Cards`), so an offered
    cut carries nothing the cut was refused for that it reads differently."""

    def __init__(self, root: Path, base: str, cut: str, inputs: Inputs) -> None:
        """Resolve ``base`` and ``cut`` and read what ``cut`` knows: the tracker's cards
        its window names (:func:`tools.release._cards.read`)."""
        self.root = root
        self.base = git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
        self.cut = git(root, "rev-parse", "--verify", f"{cut}^{{commit}}").strip()
        self.unrehearsed = frozenset(inputs.unrehearsed)
        if inputs.connect is None:
            raise ReleaseError(f"{self.cut[:12]}'s work is the tracker's cards, and no way to "
                               "reach the tracker was given")
        self._known = _cards.read(root, self.base, self.cut, inputs.connect())
        self._cut_adds = frozenset(m.revision for m in _added(migrations(root, self.base,
                                                                         self.cut)))

    def verdict(self, cut: str, subject: str) -> Verdict:
        """``cut`` (the release's, or an earlier one) judged; ``subject`` is its commit's
        subject, for display."""
        found = _cards.carried(self.root, self.base, cut, self._known)
        changed = migrations(self.root, self.base, cut)
        return Verdict(cut, subject, found, changed,
                       tuple(_refusals(found, changed, self.unrehearsed, self._cut_adds)),
                       tuple(_approvals(changed)))

    def line(self, cut: str) -> list[tuple[str, str]]:
        """``(sha, subject)`` for each cut ``dev`` stood at in ``<base>..<cut>``, newest
        first, ``cut`` itself included (:func:`~tools.release._model.first_parent_line`)."""
        return first_parent_line(self.root, self.base, cut)


def _unknown(verdict: Verdict, unrehearsed: Collection[str]) -> list[str]:
    """Each unrehearsed name that is neither a money mover the cut carries nor a migration
    it adds: a mistyped name would otherwise refuse nothing."""
    known = {step.key for step in verdict.carried.steps if step.money}
    known |= {m.revision for m in verdict.added if m.revision is not None}
    return sorted(name for name in unrehearsed if name not in known)


def plan(root: Path, base: str, cut: str, inputs: Inputs = Inputs()) -> Plan:
    """The release ``<base>..<cut>`` judged, with the cuts to offer.

    Raises:
        ReleaseError: when the range holds no commit; when an unrehearsed name is neither
            a money mover the cut carries nor a migration it adds; when ``inputs`` gives
            no way to reach the tracker; or when the cards cannot be read
            (:func:`tools.release._cards.read`).
        tools.quill.door.TrackerError, tools.quill.door.GitHubError,
            requests.RequestException, OSError: when the tracker cannot be read.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    if not git(root, "rev-list", "--max-count=1", f"{base}..{cut}").strip():
        raise ReleaseError(f"{base}..{cut} holds no commit: nothing to release")
    judge = Judge(root, base, cut, inputs)
    line = judge.line(judge.cut)
    verdict = judge.verdict(*line[0])
    unknown = _unknown(verdict, judge.unrehearsed)
    if unknown:
        raise ReleaseError(f"unrehearsed {', '.join(unknown)}: names no money mover this "
                           "cut carries and no migration it adds")
    return Plan(judge.base, verdict, *_offers(judge, verdict, line[1:]))


def _offers(judge: Judge, verdict: Verdict,
            earlier: list[tuple[str, str]]) -> tuple[Verdict | None, Verdict | None]:
    """The newest of the ``earlier`` cuts that is not refused, when ``verdict`` is; and the
    newest that is not refused and adds at most one migration, when ``verdict`` adds two
    or more.  Each candidate is judged only until both answers are found."""
    want_passing, want_one = verdict.status == REFUSED, len(verdict.added) > 1
    passing = one = None
    for sha, subject in earlier:
        if not (want_passing and passing is None) and not (want_one and one is None):
            break
        candidate = judge.verdict(sha, subject)
        if candidate.status == REFUSED:
            continue
        if want_passing and passing is None:
            passing = candidate
        if want_one and one is None and len(candidate.added) <= 1:
            one = candidate
    return passing, one
