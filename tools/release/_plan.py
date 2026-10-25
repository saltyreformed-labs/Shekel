"""A release's verdict: what refuses it, what the developer's approval question must name,
and, when it is refused or carries two migrations, the newest earlier cut to offer.

The rules are card ``plan#9``'s and the developer's (rulings ``balance:R-BAL279`` -
``R-BAL281``, ``R-BAL287`` and ``R-BAL288``):

- **Refused** (exit 1): two steps that move money, an undone one counting as one
  (R-BAL288); a deploy-together group the release leaves production holding in part
  (each member it lacks named; R-BAL288: wholly in production or wholly out); a carried
  step whose money flag cannot be read and is not declared (declared only before the
  cutover, R-BAL287); a claim the cut's reader cannot read; and each money mover
  (declared ones too) or added migration the skill names UNREHEARSED, because it found
  no rehearsal kit for it (R-BAL281's interim, as the developer confirmed it 2026-10-13:
  such a release is refused in R-BAL280's shape).  Whether a kit exists, and an
  unreadable step's flag, are the skill's judgment, so they are inputs.
- **Needs the approval question to name it** (exit 3): each money flag the skill
  DECLARED, with its source; two or more added migrations; and every migration already
  on ``main`` that the release edits or deletes.
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

**Every candidate is judged with what the release's cut knows**, read once by the one
reader the cut's tree calls for: before the cutover
(:class:`~tools.release._carried.Knowledge`), each step's flag read over the CUT's
window (or declared) and the groups' leaves at the cut; after it
(:class:`~tools.release._cards.Cards`), the cards the window of any cut on the cut's
first-parent line names (every candidate's), read from the tracker once.  Before the
cutover, a candidate's own window is narrower and can miss a
version that marks a step, or a leaf filed later, so judged on its own it could pass
what the cut was refused for (the delta review of 2026-10-13 built four such
histories).  So a step the cut reads as a money mover is one at every
candidate, a name the skill gave as unrehearsed refuses every candidate carrying it, and
a candidate that adds a migration the cut does not add is refused, since the skill
judged no rehearsal of it.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from tools.ci.gitcmd import git
from tools.quill.door import Tracker
from tools.release import _cards
from tools.release._carried import Declaration, Registry, carried, knowledge, released
from tools.release._migrations import ADDED, Migration, migrations
from tools.release._model import (
    Carried,
    Group,
    ReleaseError,
    first_parent_line,
    steps_blob,
)

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
    rehearsal kit for; each flag it declared, which only a cut before the cutover takes
    (L8 deletes it with that reader, R-BAL287); and how to reach the tracker, which only a
    cut after the cutover reads.  No connection is made unless one is given: only the
    command line, run as ``python -m``, gives quill's App
    (:data:`tools.quill.quill.CONNECT`)."""

    unrehearsed: Collection[str] = ()
    declared: Collection[Declaration] = ()
    connect: Callable[[], Tracker] | None = None


def _split(group: Group) -> str | None:
    """Why a cut leaves production holding part of ``group``; None when, released, it
    leaves production holding every member or none (R-BAL288).  A member the plan will
    never ship is named with the way out no plan can block: reverting the merge that landed
    each member the cut keeps, which deletes its ship marker with its code and leaves
    production holding none of the group (after the cutover, every step below a group that
    production holds at a cut is a member of it).  Never "delete the marker": one deleted
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
    reasons += [f"{step.key}: no version of steps.md from base to the cut holds it open, "
                "so whether it moves money cannot be read; the skill declares it "
                f"(--declare {step.key}=money|none@SOURCE)" for step in found.steps
                if step.money is None]
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


def _approvals(found: Carried, changed: tuple[Migration, ...]) -> list[str]:
    """What the approval question must name for a cut carrying ``found`` and changing the
    migrations ``changed``: each flag the skill declared, and its source (R-BAL207: a
    judgment reaches the developer's one approval, never silently); two or more added
    migrations; and each migration already on main it edits or deletes."""
    added = _added(changed)
    named = [f"{step.key}: no version of steps.md from base to the cut holds it open; the "
             "skill declared it "
             f"{'a money mover' if step.money else 'moving no money'}, from {step.declared}"
             for step in found.steps if step.declared is not None]
    if len(added) > 1:
        named.append(f"it adds {len(added)} migrations: "
                     + ", ".join(f"{m.revision} ({m.path})" for m in added))
    named += [f"it {m.change.lower()} migration {m.revision} ({m.path}), which is already "
              "on main and which production never re-runs" for m in changed
              if m.change != ADDED]
    return named


class Judge:
    """Judges the release ``<base>..<cut>`` and the earlier cuts offered in its place, each
    with what the release's cut knows (:class:`~tools.release._carried.Knowledge` before
    the cutover, :class:`~tools.release._cards.Cards` after it), so an offered cut carries
    nothing the cut was refused for that it reads differently."""

    def __init__(self, root: Path, base: str, cut: str, inputs: Inputs) -> None:
        """Resolve ``base`` and ``cut`` and read what ``cut`` knows, by the one reader its
        tree calls for (T-a a1): the registries' when it holds ``steps.md``, with what
        ``base`` released and the skill's declarations; the tracker's cards when it holds
        none, the cutover being behind it (the tracker is reached only then)."""
        self.root = root
        self.base = git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
        self.cut = git(root, "rev-parse", "--verify", f"{cut}^{{commit}}").strip()
        self.unrehearsed = frozenset(inputs.unrehearsed)
        self._carried: Callable[[str], Carried]
        if steps_blob(root, self.cut) is None:
            if inputs.connect is None:
                raise ReleaseError(f"{self.cut[:12]} is after the cutover, whose cards are the "
                                   "tracker's, and no way to reach the tracker was given")
            self._carried = partial(_cards.carried, root, self.base,
                                    known=_cards.read(root, self.base, self.cut,
                                                      inputs.connect()))
        else:
            declared = {declaration.key: declaration for declaration in inputs.declared}
            self._carried = partial(carried, root, self.base,
                                    known=knowledge(Registry(root), self.base, self.cut,
                                                    declared),
                                    shipped=released(root, self.base))
        self._cut_adds = frozenset(m.revision for m in _added(migrations(root, self.base,
                                                                         self.cut)))

    def verdict(self, cut: str, subject: str) -> Verdict:
        """``cut`` (the release's, or an earlier one) judged; ``subject`` is its commit's
        subject, for display."""
        found = self._carried(cut)
        changed = migrations(self.root, self.base, cut)
        return Verdict(cut, subject, found, changed,
                       tuple(_refusals(found, changed, self.unrehearsed, self._cut_adds)),
                       tuple(_approvals(found, changed)))

    def line(self, cut: str) -> list[tuple[str, str]]:
        """``(sha, subject)`` for each cut ``dev`` stood at in ``<base>..<cut>``, newest
        first, ``cut`` itself included (:func:`~tools.release._model.first_parent_line`)."""
        return first_parent_line(self.root, self.base, cut)


def _unknown(verdict: Verdict, unrehearsed: Collection[str]) -> list[str]:
    """Each unrehearsed name that is neither a money mover the cut carries nor a migration
    it adds: a mistyped name would otherwise refuse nothing."""
    known = {step.key for step in verdict.carried.steps if step.money is not False}
    known |= {m.revision for m in verdict.added if m.revision is not None}
    return sorted(name for name in unrehearsed if name not in known)


def plan(root: Path, base: str, cut: str, inputs: Inputs = Inputs()) -> Plan:
    """The release ``<base>..<cut>`` judged, with the cuts to offer.

    Raises:
        ReleaseError: when the range holds no commit; when an unrehearsed name is neither
            a money mover the cut carries nor a migration it adds; when a declaration
            names no carried step whose flag no version holds (every declaration, on a
            cut after the cutover); when the cut is after the cutover and ``inputs``
            gives no way to reach the tracker; or when a version of steps.md in the
            window does not parse.
        tools.quill.door.TrackerError, tools.quill.door.GitHubError,
            requests.RequestException, OSError: when the tracker cannot be read, for a cut
            after the cutover.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    declared = inputs.declared
    if len({declaration.key for declaration in declared}) != len(declared):
        raise ReleaseError("a step is declared twice: each carried step takes one "
                           "declaration, so no value can be dropped unseen")
    if not git(root, "rev-list", "--max-count=1", f"{base}..{cut}").strip():
        raise ReleaseError(f"{base}..{cut} holds no commit: nothing to release")
    judge = Judge(root, base, cut, inputs)
    line = judge.line(judge.cut)
    verdict = judge.verdict(*line[0])
    unknown = _unknown(verdict, judge.unrehearsed)
    if unknown:
        raise ReleaseError(f"unrehearsed {', '.join(unknown)}: names no money mover this "
                           "cut carries and no migration it adds")
    stray = sorted({declaration.key for declaration in declared}
                   - {step.key for step in verdict.carried.steps if step.declared})
    if stray:
        raise ReleaseError(f"declare {', '.join(stray)}: names no step this cut carries "
                           "whose flag no version of steps.md holds; a declaration fills an "
                           "unreadable flag and never overrides a read one")
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
