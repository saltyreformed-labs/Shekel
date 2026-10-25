"""Which cards a release carries after the cutover, whether each moves money, and which
deploy-together groups each is under: the release tool's card reader (step X-cx's
L9a-1b, T-a a1; rulings ``balance:R-BAL287`` - ``R-BAL289``), read from the ship markers
since step X-cx's M3 (ruling ``R-BAL359``).

**What a release carries is git's answer, read from the ship markers**: card N shipped
when its marker ``plan/ships/N`` is in the tree (``tools/quill/_git.py``).  A release of
``cut`` into ``base`` leaves production running the tree of their merge, as the release's
own merge into ``main`` writes it (:func:`tools.quill.door.merged_tree`: a marker that
conflicts in it refuses to judge), and it carries each card whose marker that tree adds
(shipped), changes (shipped again after an undo: every claim writes a new line) or deletes
(UNDONE), against ``base``'s: the NET change, so a card shipped and undone inside one
window was in production on neither side and is not carried, and one undone that
production held is carried and judged by the same rules as a ship, since it changes what
production runs as much (the report ruled as ``R-BAL359``, its s.10, refining
``R-BAL288``).  Against the merge and not the cut alone, so a hotfix on ``main`` the cut
lacks reads as no undo.  Production holds ``base``'s markers before the release and the
merge's after it, by quill's one reading of a tree (:func:`tools.quill.door.markers_in`),
which also says which changed entries are markers at all; a
card's commits are those of ``<base>..<cut>`` that changed its marker, by quill's one walk
(:func:`tools.quill.door.marker_changes`): its claim and the merge that landed it.

**A card moves money when it carries the moves-money label**: its OWN label, as the
tracker holds it when the release is judged, split or not (the coordinator, under
R-BAL207, 2026-10-14).  A label is no sentence a tick rewrites, so a card keeps its flag
after it ships (the registries' ``recurrence:R20`` shape, which the pre-cutover reader
reads around, has no counterpart here).  A step split into leaves since its marker
landed is judged as the work it was, by its own label: a split writes the step only its
sub-issue link, so its labels stay (``tools/quill/test_quill_file.py``), and each leaf is
judged by its own when the window names it.  No card inherits a leaf's label: a label a
person moves by hand is an edit any card can take, split or not, and a step undone and
re-planned into a money leaf would count as a second money change beside that leaf,
refusing every release from then on (the full-leaf review of 2026-10-14, its E2).

**A group is a card labelled deploy-together**, and **after a release that touches a
group, production holds it wholly or not at all** (R-BAL288; the coordinator's C', under
R-BAL207, 2026-10-14; only a group the release touches gets a verdict on its members, so
a leaf filed under a group after it was released whole holds back only a release that
touches it; the read's two checks below run over every group it holds).  Its MEMBERS,
judged per cut (:func:`_member`), are the steps below it that production must
hold together: each leaf below it that can still ship, by quill's one leaf rule
(``leaves_below``: a leaf split again counting as its own leaves); each step below it
that production holds at the cut, whatever the plan now says of it, since its code is
in production and its siblings must not be there without it; and each step below
it that will never ship that production holds at the base, which the release keeps or
takes out.  A step that will never ship is dropped (quill's ``dropped``: a leaf closed as
dropped; a step re-planned into leaves that were all dropped; a split step a person
closed; a step left out of quill's leaves, closed while still being filed) or under a
step that was (``dropped_above``; R-BAL185: a person's close of a split step is a drop
its leaves inherit, never copied onto them).  So a step re-planned into leaves that can
still ship is a member only while production holds it at the cut (its leaves are members
throughout), and a finding is never one: a fix the group cannot ship without is a leaf.

"Below" is the group's links read DOWN, each step linked under it, under those, ...
(quill's closure, :func:`tools.quill.door.with_closure`, holds every one): the walk
quill's leaf rule reads, so a group's leaves are always among its steps.  Which groups a
release TOUCHES reads the same links UP (:func:`tools.quill.door.above`, quill's one
walk up the steps a card splits): each group a card it names is under, the card itself
or a step above it -- for a finding, its owner, the one step it is blocked by and never a
parent (ruling ``R-BAL395``, :func:`tools.quill.door.owner`), or a step above that, so a
finding is under the groups its owner is under, and one blocked by no card is under none
(:func:`_line_up`; one blocked by cards that name no one owner refuses the release, below).
**Under every read this reader judges, the two walks give a group the same steps, and no
step hangs below it unread**: the read takes every card below each group, of any kind (a
finding or a ruling below the step that owns it, by that step's blocking read), and
judges nothing when a step hangs under a card that is no
step (:func:`_none_hung`: only a person's link or retype puts one there, since quill
links a step only under the step it splits), and then compares the two walks for each
group and judges nothing when they part (:func:`_one_walk`: only a link or a card's
type changed between the tracker's rounds of the read parts them), so no member is
dropped because a walk missed it (the coordinator, under R-BAL207, 2026-10-14).  **Both
checks run over every group the read holds, whether the release touches it or not**: a
group quill's closure holds for any other reason (for example one blocking a card the
closure reads, one above such a blocker, or one above a named card that touches
nothing) is read below too, and a step hung or a walk parted there refuses the release
(``release plan`` exits 2, nothing judged), naming the cards and the way to mend them,
although no verdict on members reads that group (the coordinator, under R-BAL207,
2026-10-14: kept strict, the PR #565 delta review's M-1).  A card the release names
under a group is a leaf, a step re-planned into leaves since its
marker landed (quill splits no step git says shipped, so it was undone first, or
split before its ship merged; a person can split one on the web), a step that will never
ship, a finding, or the group's own card (the coordinator, under R-BAL207, 2026-10-14).
A card the release names that will never ship touches nothing unless production holds
it, at the base or at the cut.  A release touching a group must leave production holding
every member or none: a release taking a whole group out is whole too.

**A step closed while still being filed is no member once quill's ``sync`` runs**:
``sync`` unlinks it (R-BAL205: it was never part of the split), so it is below no group,
and a release keeping its siblings without it would pass.  No card production holds
carries that mark by any tool's write: quill marks a card only when it creates it, and
CI's landing check refuses a marker a claim writes for a card that still carries the mark
("still being filed: finish it with quill file, then land"; ``tools/quill/landing.py``,
its rule 7), and neither ``dev`` nor ``main`` held a marker before that check existed
(measured 2026-10-23).  So this reader reads the links as the tracker holds them, and no
history of them.

**No refusal of a group is permanent.**  A refusal names each member the release lacks
(missing, or taken out by it) and, when a member will never ship, every member it would
keep; the report's line for the group names every member, carried, released, missing
and undone.  Two ways out stand:

- **Take the group out**: the same release reverts the merge that landed each member it
  would keep (the report's carried and released), and a revert deletes the card's marker
  with its code.  A marker deleted by hand leaves the code in production, which no reader
  here can see (the ruled report's residual 1), so the way out never says "delete the
  marker" (M3's review, its 4).  That leaves production holding none of the
  group, in ONE round by construction: every step below the group that production holds
  at a cut is a member, so with those markers deleted production holds no step below it
  at the new cut.  quill's ``dropped`` and ``leaves`` read git's shipped set, so a deleted
  marker can turn a live step dead or a container back into a leaf; the new cut is judged
  with its own read, and no such change makes production hold a step below the group.
  Where groups nest, the outer group's way out takes the inner one out too.  CI's landing
  check passes a marker's delete from any branch (``tools/quill/landing.py``, its rule 2),
  so a step production holds, a split one included, may be undone and this way out lands.
- **Ship it whole**: a member named as missing or undone that can still ship can ship,
  or ship back.

A member that will never ship leaves by the first way alone, whichever kind it is: no
plan ships it.  After that release production holds it on neither side, so no later
release counts it unless a claim ships it again, and then the same way out stands.  No
card stands in for the leaves it was re-planned into: the delta review of 2026-10-14
built three histories in which such a stand-in, a member never back in production,
refused every later release.  The way out can meet the money rule's cap (a group holding
two money movers comes out in no one release): that is the developer's question, asked
at X-cx's piece L9d.

**What it cannot read refuses the release**, and nothing is declared after the cutover
(R-BAL287, R-BAL289):

- an old-form ``Ships: <arc>:<id>`` that a cut holding ``steps.md`` on ``dev``'s line in
  the window holds: the cutover's freeze ends with a release, so production held every
  one merged before the cutover (R-BAL287), and this one the freeze missed.  Only such a
  cut can judge it, so the refusal names the newest one, to release first with the
  release tool as that cut holds it;
- an old-form ``Reopens: <arc>:<id>`` such a cut holds: it undoes a step that has no
  card, and no reader judges what it takes out of production, the release tool as that
  cut holds it included (``tools/release/_carried.py`` reads no ``Reopens:``), so the
  refusal names no cut to release first (M3's review, its 2).  None exists on ``dev`` or
  ``main`` (measured 2026-10-23).  Every other trailer is prose -- an old-form one merged
  after the cutover, a ``plan#N`` one, a value of neither form -- since a marker is the
  record and nothing after the cutover reads a trailer: refusing one would wedge every
  release over it, once no check refuses a trailer's shape before it lands
  (``R-BAL184``; X-cx's ``m2_plan.md`` s.3);
- a marker naming a card the tracker does not hold: CI's landing check refuses one before
  it lands (R-BAL289; ``tools/quill/landing.py``, its rule 7), so what reaches here is a
  card deleted after its marker landed;
- a marker naming a card of a kind no commit ships (a ruling, a question, a card of no
  type; quill's ``SHIPPABLE``): read as work it would pass as moving no money.  The landing
  check refuses one when it lands, so what reaches here was retyped since;
- a marker naming a finding blocked by cards that name no one owner (two cards or more, or
  an issue outside the tracker beside one or alone; :func:`tools.quill.door.owner`,
  R-BAL395): which groups it is under cannot be read, so the release is refused, and
  quill's ``sync`` reports the finding (R395's review, its F3);
- an entry under ``plan/ships/`` that is no card's marker (a name that is no card number,
  a file in a directory below it, an entry that is no file), which the landing check
  refuses before it lands (its rule 1);
- an earlier cut whose merge with ``base`` conflicts on a marker: what releasing it would
  leave production holding of that card cannot be read, so it is refused, never offered,
  and the release's own cut is judged as ever (M3's review, its 1).  A conflict in the
  merge of the release's own cut and ``base`` judges nothing (exit 2), since nothing
  about that card can be read.

**The tracker is read once, at the release's cut** (:func:`read`), and every earlier cut
offered in its place is judged with that read (:func:`carried`).  The read names every
card the window of ANY cut on the cut's first-parent line names: the cuts the release can
offer (:func:`tools.release._model.first_parent_line`, the one list of them), so no
offered cut names a card the read does not hold, by construction.  So it holds a card an
earlier cut shipped and the cut undid, and one a merge's resolution alone put in an
earlier cut's window.  The plan's
shapes (which steps are leaves, which will never ship) are read with what production
holds after the cut too, so an earlier cut is judged by the shapes the release's cut gives
its steps.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from tools.ci import trailers
from tools.quill.door import (
    MARKERS,
    OWNED,
    SHIPPABLE,
    Card,
    MarkerConflict,
    Tracker,
    above,
    card_of,
    changes,
    dropped,
    dropped_above,
    leaves_below,
    marker_changes,
    markers_in,
    merged_tree,
    owner,
    with_closure,
)
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, MOVES_MONEY_LABEL
from tools.release._model import (
    Carried,
    Group,
    ReleaseError,
    Step,
    first_parent_line,
    steps_blob,
)


@dataclass(frozen=True)
class Cards:
    """What the release's cut knows of the cards its window names, read from the tracker
    once: whether each one that is work moves money; each one's line up (itself, the step
    it splits or, for a finding, its owner, that step's, ...); each deploy-together group
    the read holds that has a
    step below it, with every such step (those that can be its members, :func:`_member`),
    in card order; each such group's leaves by quill's rule, in any state; which of those
    steps and named cards will never ship (dropped, or under a step that was); each named
    card's kind, as the tracker holds it (one that is not there has none: a named card
    that is no work is a kind no commit ships, and a finding's line up starts at its owner,
    R-BAL395); and what blocks each named finding that names no one owner
    (:func:`_unowned`)."""

    money: Mapping[int, bool]
    lines: Mapping[int, tuple[int, ...]]
    groups: Mapping[int, tuple[int, ...]]
    leaves: Mapping[int, frozenset[int]]
    dropped: frozenset[int]
    kinds: Mapping[int, str | None]
    unowned: Mapping[int, str]


@dataclass(frozen=True)
class _Window:
    """What a release of a cut into a base changes in the ship markers: the cards
    production holds before it (``before``, the base's markers) and after it (``after``,
    the markers of the two's merge); each card whose marker that merge adds, changes or
    deletes against the base (``named``), with the commits of ``<base>..<cut>`` that
    changed that marker, newest first; and each other entry under :data:`MARKERS` it
    changes, by path (``strays``): one whose card's marker is a file on neither side, by
    quill's one reading of a tree (:func:`tools.quill.door.markers_in`)."""

    before: frozenset[int]
    after: frozenset[int]
    named: Mapping[int, tuple[str, ...]]
    strays: tuple[str, ...]


def _what(kind: str | None) -> str:
    """What a card of ``kind``, a kind no commit ships, is, for a refusal."""
    return f"a {kind}" if kind else "a card of no type"


@cache
def _held(root: Path, tree: str) -> frozenset[int]:
    """The cards whose markers ``tree`` holds (a commit names its own), read once per
    tree: the judge passes resolved shas, and a tree id names content that never changes."""
    return markers_in(root, tree)[0]


@cache
def _window(root: Path, base: str, cut: str) -> _Window:
    """What releasing ``cut`` into ``base`` changes in the markers (:class:`_Window`), read
    once per pair: the judge passes resolved shas, which name commits that never change.

    Raises:
        tools.quill.door.MarkerConflict: when a marker conflicts in the merge of the two.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    tree = merged_tree(root, (base, cut))
    before, after = _held(root, base), _held(root, tree)
    commits: dict[int, list[str]] = {}
    for change in marker_changes(root, (f"{base}..{cut}",)):
        number = card_of(change.entry.path)
        if number is not None and change.sha not in commits.setdefault(number, []):
            commits[number].append(change.sha)
    named, strays = {}, []
    for entry in changes(root, base, tree, MARKERS):
        number = card_of(entry.path)
        if number in before | after:
            named[number] = tuple(commits.get(number, ()))
        else:
            strays.append(entry.path)
    return _Window(before, after, named, tuple(strays))


def _offered(root: Path, base: str, cut: str) -> tuple[_Window, frozenset[int]]:
    """The window of releasing ``cut`` into ``base``, and every card the window of any cut
    on its first-parent line names: the cuts the release can offer.  An earlier cut whose
    merge with ``base`` conflicts on a marker adds none, since it is refused for that
    (:func:`carried`).

    Raises:
        tools.quill.door.MarkerConflict: when a marker conflicts in the merge of ``base``
            and ``cut``.
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    window, named = _window(root, base, cut), set()
    for sha, _ in first_parent_line(root, base, cut):
        try:
            named |= set(_window(root, base, sha).named)
        except MarkerConflict:
            continue
    return window, frozenset(named)


def read(root: Path, base: str, cut: str, tracker: Tracker) -> Cards:
    """What ``cut`` knows of the release ``<base>..<cut>``: every card the window of any cut
    on its first-parent line names (the cuts the release can offer), read by number, and
    every card a decision over them reads, read once.  ``base`` and ``cut`` are resolved
    shas: the windows and the shipped sets are cached by them.

    Raises:
        ReleaseError: when a step hangs below a deploy-together group under a card that
            is no step (:func:`_none_hung`), or the group's links read down and its cards'
            lines read up give it different steps (:func:`_one_walk`): for any group the
            read holds, whether the release touches it or not.
        tools.quill.door.TrackerError: when a card one of them links to is not there.
        tools.quill.door.GitHubError: when GitHub answers a read with an error.
        requests.RequestException, OSError: when the tracker cannot be reached.
        tools.quill.door.MarkerConflict: when a marker conflicts in the merge of ``base``
            and ``cut`` (an earlier cut's conflict refuses that cut alone: :func:`carried`).
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    window, named = _offered(root, base, cut)
    held = tracker.cards(named)
    closure = with_closure(tracker, dict(held))
    shipped = set(window.after)
    work = {number for number, card in held.items() if card.kind in SHIPPABLE}
    up = {number: _line_up(card, closure) for number, card in closure.items()}

    def never_ships(number: int) -> bool:
        return (dropped(number, closure, shipped)
                or dropped_above(closure[number], closure, shipped) is not None)

    def below(group: int) -> tuple[int, ...]:
        # Every step linked below the group, read down its links as quill's leaf rule is.
        found, wanted = [], list(closure[group].step_children)
        while wanted:
            found += wanted
            wanted = [number for step in wanted for number in closure[step].step_children]
        return tuple(sorted(found))
    groups = {group: below(group) for group, card in closure.items()
              if DEPLOY_TOGETHER_LABEL in card.labels}
    kinds = {number: card.kind for number, card in closure.items()}
    _none_hung(tracker, groups, _below_each(tracker, closure, groups), kinds)
    _one_walk(groups, up, kinds)
    groups = {group: steps for group, steps in groups.items() if steps}
    return Cards(
        {number: MOVES_MONEY_LABEL in held[number].labels for number in work},
        {number: (number, *up[number]) for number in work},
        groups,
        {group: frozenset(leaf.number for leaf in leaves_below(closure[group], closure,
                                                               shipped))
         for group in groups},
        frozenset(number for number in {*work, *(step for steps in groups.values()
                                                 for step in steps)}
                  if never_ships(number)),
        {number: card.kind for number, card in held.items()},
        _unowned(held))


def _line_up(card: Card, cards: Mapping[int, Card]) -> tuple[int, ...]:
    """The steps above ``card``, nearest first, by quill's one walk up
    (:func:`tools.quill.door.above`): for a finding or a ruling, from its owner, the one
    step it is blocked by, never from a parent (ruling ``R-BAL395``,
    :func:`tools.quill.door.owner`), so one with no owner is under no step.  ``cards``
    holds every card the walk reads (quill's closure holds each blocker and parent)."""
    if card.kind not in OWNED:
        return tuple(step.number for step in above(card, cards))
    held_by = owner(card)
    return () if held_by is None else (
        held_by, *(step.number for step in above(cards[held_by], cards)))


def _unowned(held: Mapping[int, Card]) -> dict[int, str]:
    """What blocks each finding of ``held`` whose blockers name no one owner (two cards or
    more, or an issue outside the tracker; :func:`tools.quill.door.owner`, R-BAL395), for a
    refusal: which groups it is under cannot be read.  One blocked by nothing is under no
    group, and is not here."""
    found = {}
    for number, card in held.items():
        blockers = ([f"plan#{blocker}" for blocker in card.blocked_by]
                    + [link.issue for link in card.outside if link.what == "blocker"])
        if card.kind in OWNED and card.kind in SHIPPABLE and blockers and owner(card) is None:
            found[number] = ", ".join(blockers)
    return found


def _below_each(
        tracker: Tracker, closure: Mapping[int, Card], groups: Mapping[int, Collection[int]],
) -> dict[int, tuple[tuple[int, str | None], ...]]:
    """What stands below each card of ``closure``, as ``(number, kind)``, for
    :func:`_none_hung`: its sub-issues and, for each group's own card and each step below a
    group that is a step, each finding and ruling it blocks, its owner's link (R-BAL395: a
    finding or a ruling is no sub-issue of the step that owns it, so a step hung under one
    is below the group all the same; R395's review, its F1), by that step's blocking read
    (``Tracker.blocking``), once each."""
    below = {number: tuple((child.number, child.kind) for child in card.children)
             for number, card in closure.items()}
    for step in {card for group, steps in groups.items() for card in (group, *steps)}:
        if closure[step].kind == "step":
            below[step] += tuple((blocked.number, blocked.kind)
                                 for blocked in tracker.blocking(step)
                                 if blocked.kind in OWNED)
    return below


def _one_walk(groups: Mapping[int, Collection[int]], lines: Mapping[int, tuple[int, ...]],
              kinds: Mapping[int, str | None]) -> None:
    """Refuse to read the release unless, for each group, the steps below it read DOWN its
    links (``groups``) are the steps whose line UP reaches it (``lines``, each card's steps
    above it, nearest first; ``kinds``, each card's kind): two reads of the same links,
    which agree for every link quill writes.  Once no step hangs under a card that is no
    step (:func:`_none_hung`, run first), they part only when a link or a card's type
    changed between the read's rounds; a member either walk misses would be dropped
    unseen, so nothing is judged.

    Raises:
        ReleaseError: naming each group whose walks part and each step one walk holds and
            the other does not.
    """
    parted = [said for group, down in sorted(groups.items())
              if (said := _parted(group, down, lines, kinds))]
    if parted:
        raise ReleaseError("; and ".join(parted))


def _parted(group: int, down: Collection[int], lines: Mapping[int, tuple[int, ...]],
            kinds: Mapping[int, str | None]) -> str | None:
    """How ``group``'s two walks part (:func:`_one_walk`); None when they agree."""
    up = {number for number, line in lines.items()
          if group in line and kinds[number] == "step"}
    down_only, up_only = sorted(set(down) - up), sorted(up - set(down))
    seen = ([f"{_keys(down_only)} below it by its links, which its own read does not place "
             "under it"] if down_only else [])
    seen += ([f"{_keys(up_only)} under it by its own read, which its links do not reach"]
             if up_only else [])
    return (f"the links under the deploy-together group plan#{group} disagree, so its "
            f"members cannot be read: {'; '.join(seen)} (the tracker changed while it was "
            "read: judge again)") if seen else None


def _none_hung(tracker: Tracker, groups: Mapping[int, Collection[int]],
               listed: Mapping[int, tuple[tuple[int, str | None], ...]],
               kinds: Mapping[int, str | None]) -> None:
    """Refuse to read the release when a step hangs below a deploy-together group under a
    card that is no step (a finding, a ruling, a question, a card of no type): quill links
    a step only under the step it splits, so only a person's link puts one there, or a
    person's change of a split step's type.  Neither walk of the read sees such a step
    (quill's closure holds a step's step sub-issues, never a finding's), so a member
    production holds could be dropped unseen.  Every card below each group in ``groups``
    (every group the read holds, touched by the release or not), of any kind and in any
    state, is read for it: ``listed`` holds what stands below each card the read holds, as
    ``(number, kind)`` (its sub-issues and, for a step below a group, the findings and
    rulings it owns: :func:`_below_each`), and ``kinds`` each one's kind; the rest are read
    from ``tracker`` here, in rounds, each card once, by their sub-issues.

    Raises:
        ReleaseError: naming each card a step hangs under, the steps and the groups above
            it; or a card listed below a group that the tracker does not hold.
    """
    listed, kinds = dict(listed), dict(kinds)
    hung: dict[int, tuple[set[int], set[int]]] = {}
    seen = frontier = {(group, card) for group, steps in groups.items()
                       for card in (group, *steps)}
    while frontier:
        for number, card in tracker.cards({card for _, card in frontier} - set(listed)).items():
            listed[number] = tuple((child.number, child.kind) for child in card.children)
            kinds[number] = card.kind
        later = set()
        for group, card in frontier:
            if card not in listed:
                raise ReleaseError(f"plan#{card} is listed below the deploy-together group "
                                   f"plan#{group} and the tracker does not hold it (the "
                                   "tracker changed while it was read: judge again)")
            for child, kind in listed[card]:
                if kind != "step":
                    later.add((group, child))
                elif card != group and kinds[card] != "step":
                    steps, above_it = hung.setdefault(card, (set(), set()))
                    steps.add(child)
                    above_it.add(group)
        frontier = later - seen
        seen = seen | later
    if hung:
        raise ReleaseError("a step hangs under a card that is no step, so the members of a "
                           "group above it cannot be read: " + "; ".join(
                               f"{_keys(sorted(steps))} under plan#{card}, "
                               f"{_what(kinds[card])}, below the deploy-together group "
                               f"{_keys(sorted(groups))} (move each under the step it "
                               f"splits, or give plan#{card} the step type if a person "
                               "changed it)"
                               for card, (steps, groups) in sorted(hung.items())))


def _keys(numbers: Collection[int]) -> str:
    """``numbers`` as the cards they are, for a message."""
    return ", ".join(f"plan#{number}" for number in numbers)


def _judgeable(root: Path, base: str, cut: str, claim: str) -> str | None:
    """The newest cut on ``dev``'s first-parent line in ``<base>..<cut>`` whose tree holds
    ``steps.md`` and whose history holds the commit ``claim``, so that releasing it
    carries the claim; None when none does (the claim was merged after the cutover)."""
    return next((sha for sha, _ in first_parent_line(root, base, cut)
                 if steps_blob(root, sha) is not None
                 and trailers.is_ancestor(root, claim, sha)), None)


@cache
def _old_form(root: Path, base: str, cut: str) -> tuple[str, ...]:
    """Each old-form trailer in ``<base>..<cut>`` that a cut before the cutover holds: a
    ``Ships:``, naming the newest such cut to release first, and a ``Reopens:``, which no
    reader judges; read once per range, as :func:`_window` is.  Every other trailer is
    prose (the module's docstring)."""
    found = trailers.history(root, f"{base}..{cut}")
    lines = []
    for claim in found.old_form:
        judgeable = _judgeable(root, base, cut, claim.sha)
        if judgeable is not None:
            lines.append(f"{claim.sha[:12]} claims `{claim.key}` in the old form, which only a "
                         "cut holding steps.md can judge (R-BAL287: the cutover's freeze ends "
                         f"with a release of every one): release {judgeable[:12]} first, with "
                         "the release tool as that cut holds it")
    lines += [f"{undo.sha[:12]} carries `Reopens: {undo.key}` in the old form: it undoes a "
              "step that has no card, so no reader judges what it takes out of production "
              "(R-BAL287, R-BAL288)" for undo in found.old_form_reopens
              if _judgeable(root, base, cut, undo.sha) is not None]
    return tuple(lines)


def _at(shas: Collection[str]) -> str:
    """Where a card's marker changed in the window, for a refusal; empty when no commit of
    it changed the marker."""
    return f" (its marker changed at {', '.join(sha[:12] for sha in shas)})" if shas else ""


def _unreadable(root: Path, base: str, cut: str, window: _Window, known: Cards) -> list[str]:
    """Each thing this reader cannot read in ``window``, the window of ``<base>..<cut>``:
    an old-form trailer a cut before the cutover holds; a card the tracker does not hold,
    or holds as no work; a finding blocked by cards that name no one owner; and an entry
    under the markers' directory that is no card's marker."""
    lines = list(_old_form(root, base, cut))
    for number, shas in sorted(window.named.items()):
        if number in known.unowned:
            lines.append(f"plan#{number}{_at(shas)} is a finding blocked by "
                         f"{known.unowned[number]}, so it names no one owner (the one step it "
                         "is blocked by, R-BAL395) and which groups it is under cannot be "
                         "read: leave it blocked by its owner alone")
        if number in known.money:
            continue
        if number not in known.kinds:
            lines.append(f"plan#{number}{_at(shas)} is a card the tracker does not hold, so "
                         "whether it moves money cannot be read (R-BAL289)")
        else:
            lines.append(f"plan#{number}{_at(shas)} is {_what(known.kinds[number])}, which no "
                         "commit ships, so it names no work whose flag can be read")
    return lines + [f"{path} is no card's marker (a marker is the file {MARKERS}/<card "
                    "number>), so what this release changes there cannot be read"
                    for path in window.strays]


def _counts(number: int, known: Cards, held: frozenset[int]) -> bool:
    """Whether a card the window names touches the groups above it, in a release whose
    production ``held`` on one side or the other: one that will never ship (``known``'s
    dropped) touches them only while production holds it, at the base or at the cut judged
    (kept by the release, carried in by it or taken out by it; judged per cut, so an
    offered cut that still ships it is held to its siblings)."""
    return number not in known.dropped or number in held


def _member(step: int, group: int, known: Cards, before: frozenset[int],
            after: frozenset[int]) -> bool:
    """Whether ``step``, a step below ``group``, is a member of it in a release whose
    production holds ``before`` at its base and ``after`` at the cut judged: a leaf of it
    that can still ship, any step production holds at the cut, or a step that will never
    ship that production holds at the base (the coordinator's C', under R-BAL207,
    2026-10-14)."""
    return (step in after or (step in known.dropped and step in before)
            or (step in known.leaves[group] and step not in known.dropped))


def _group(group: int, known: Cards, touching: Mapping[int, tuple[int, ...]],
           before: frozenset[int], after: frozenset[int]) -> Group:
    """One group a release touches: each of its members (:func:`_member`), sorted by
    whether the window names it and whether production holds it ``before`` the release
    (at its base) and ``after`` it (at the judged cut) -- carried in by it, released
    before it, taken out by it (undone), or held on neither side (missing) -- and apart,
    each member that will never ship; and each card the window names that touches the
    group and is no member of it, with the cards between (:func:`_route`; the group's own
    card names itself).  ``touching`` maps each card the window
    names to the groups it touches (:func:`_counts`)."""
    members = tuple(step for step in known.groups[group]
                    if _member(step, group, known, before, after))

    def keys(test: Callable[[int], bool]) -> tuple[str, ...]:
        return tuple(f"plan#{member}" for member in members if test(member))
    return Group(f"plan#{group}",
                 carried=keys(lambda member: member in touching and member in after),
                 released=keys(lambda member: member not in touching and member in after),
                 missing=keys(lambda member: member not in before and member not in after),
                 undone=keys(lambda member: member in before and member not in after),
                 through=tuple(_route(walk[:walk.index(group) or 1], known.kinds)
                               for number, walk in sorted(known.lines.items())
                               if group in touching.get(number, ())
                               and number not in members),
                 dead=keys(lambda member: member in known.dropped))


def _route(walk: tuple[int, ...], kinds: Mapping[int, str | None]) -> str:
    """A named card's way up to a group, for the report: ``walk``, the card and each card
    above it short of the group, each joined to the next by its link -- a finding (by its
    kind in ``kinds``) to its owner, the step it is blocked by (R-BAL395), and each other
    card to the step it is under."""
    first, *rest = walk
    text = f"plan#{first}"
    for index, step in enumerate(rest):
        text += f" {'owned by' if not index and kinds[first] in OWNED else 'under'} plan#{step}"
    return text


def carried(root: Path, base: str, cut: str, known: Cards) -> Carried:
    """What ``<base>..<cut>`` carries, judged with ``known``, the release's cut's
    :class:`Cards` (``cut`` is that cut or an earlier one on its first-parent line; both
    are resolved shas, as :func:`read` takes them).  A marker conflict in the merge of
    ``base`` and ``cut`` is the one reason it is refused: only an earlier cut meets one
    here, since :func:`read` raises for the release's own.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot read the range.
    """
    try:
        window = _window(root, base, cut)
    except MarkerConflict as conflict:
        return Carried((), (), (
            f"{cut[:12]} and {base[:12]} merge with a conflict on {', '.join(conflict.paths)}, "
            "so what releasing this cut would leave production holding of that card cannot be "
            "read: it is not offered",))
    standing, before = window.after, window.before
    held = standing | before
    # The groups each named card touches, nearest first: none for one that does not count.
    touching = {number: tuple(step for step in known.lines.get(number, ())
                              if step in known.groups) if _counts(number, known, held) else ()
                for number in window.named}
    steps = tuple(
        Step(f"plan#{number}", shas, known.money[number],
             tuple(f"plan#{group}" for group in touching[number]),
             undone=number in before and number not in standing)
        for number, shas in sorted(window.named.items()) if number in known.money)
    groups = tuple(_group(group, known, touching, before, standing)
                   for group in sorted({group for each in touching.values() for group in each}))
    return Carried(steps, groups, tuple(_unreadable(root, base, cut, window, known)))
