"""What the plan says, decided from the cards, the board, the claims and git.

Nothing here reads GitHub or git: the caller hands over what it read, so every
decision the ``quill`` command makes is a pure function a test can drive.

**Work** is a step or finding that is not a container (:func:`is_work`): the
only cards a branch builds and a commit ships.  ``claim``, ``next`` and
``sync`` all ask that one predicate.

**A leaf** of a step is a step linked under it -- unless it was dropped before
its filing finished: closed while still marked, and not shipped by git's
answer (:func:`leaves`, the one spelling; a container is a step with a leaf).
Such a card was never part of the split, so a split step left with no other
leaf is the plain step it was, and no read that lags an unlink, and no close
of a person's on the web, makes it a split step whose leaves were all dropped
(``R-BAL187``).

**Resolved** -- the one question ``next`` asks of a blocker and ``sync`` asks
of a container's leaves -- is git's answer for work, and the tracker's for the
rest (ruling ``balance:R-BAL170``):

- a card closed by a PERSON, or by ``quill drop``, is resolved: it was dropped
  (the build plan: "Dropped ... ``plan drop`` ..., or the developer closing a
  card by hand"; ``plan drop`` has been ``quill drop`` since the tool's rename);
- a piece of work git says SHIPPED (its marker ``plan/ships/N`` is in the
  merge of ``dev`` and ``main``, ruling ``balance:R-BAL359``) is resolved,
  whatever its open or closed state; one the TOOL closed as completed is only
  the tool's display of git, so it is resolved only while git says it shipped
  -- a revert deleting its marker makes it unresolved at once, before ``sync``
  reopens it;
- a container -- a step with a leaf (``R-BAL177``, :func:`leaves`) -- holds no decision
  of the tool's (``R-BAL190``): it is resolved by its leaves, when every
  leaf is, and dropped when every leaf was (``R-BAL187``: what waits on
  it is released, and no leaf can be filed under it), or when a PERSON closed
  it, a drop its leaves inherit (``R-BAL185``).  Its open or closed state is
  the tool's display of its leaves, which ``sync`` keeps in step both ways --
  reopened when a leaf is revived, closed as completed or as not planned as
  they end.  No change ships a container, so a marker naming one (a step
  split after its claim was made) is reported and never acted on;
- a ruling or a question is never shipped by git (a ruling is a record, a
  question the developer's), so it is resolved once it is closed: a question
  when it is answered (its card becomes the ruling) or withdrawn.

**A leaf inherits every step above it** (``R-BAL182``, ``R-BAL185``): it is
workable only when its own blockers AND every blocker of every step above it
are resolved, and while no step above it was dropped.  A wait is recorded
once, on the step it was set on, and so is a PERSON's close of a split step;
nothing is copied.  ``quill drop`` of a split step writes its drop on each leaf
below it that is still work instead (``R-BAL190``: the tool records a decision
only where the work is).

**A card whose filing has not finished is never offered** (``R-BAL202``):
every card ``quill file`` creates carries the filing mark from its first write
until its last removes it (:func:`filing_unfinished`), so a card some of whose
writes have not landed -- a leaf not yet linked under its split step, or not
yet in its place -- is never handed out or claimed, and ``next`` and ``sync``
name it until the same command finishes it, or ``quill drop`` drops it (a leaf
dropped while marked is first unlinked from its split step: it was never part
of the split, ``R-BAL205``).  While a filing is still running its card is named
too: no read can tell a filing running from one a failure cut short.  A leaf
dropped while marked some other way -- closed by a person on the web, or with
its split step -- is no leaf from its close, and ``sync`` unlinks it, putting a
split step it leaves with no leaf back on the board (:func:`unsplit`).

**A card linked to an issue outside the tracker is never offered**
(``R-BAL188``): the plan reads only its own cards, so the link is reported
until someone removes it, and a step blocked by an outside issue waits, as
does every leaf under it.

**A finding or a ruling names its owner step by GitHub's "blocked by" link to it**
(ruling ``balance:R-BAL395``, :data:`OWNED`), never as its sub-issue, so a step's
progress counts only its pieces: its one blocker is its owner, and an open finding whose
owner is not one live step is reported (:func:`_owner_lost`).

**A task is a piece of one step or finding** (ruling ``balance:R-BAL386``): a sub-issue
of type ``task`` under that card, a commit inside its one pull request, never offered,
claimed or on the board.  It is DONE when git says so (:func:`done_tasks`): its file
``plan/tasks/N`` is in the merge of ``dev`` and ``main``, or the live claim of the card it
is under wrote it.  Its open or closed state is the tool's display of that, which
``sync`` keeps in step both ways, as it does a step's: closed as completed once done,
reopened once a revert (or a claim released, or a branch rewritten) takes its file away
while the tool's close stands.  A person's close is a drop, and ``quill drop`` of a card
drops the tasks under it that are still to do (R-BAL190's shape, :func:`drop_takes`).
An open task under a card that can never take its file -- dropped, shipped without it, or
no work -- is reported, never closed (:func:`_stale_task`).

**``sync`` never overrides a person** (the build plan: a check "never acts on"
the developer's own edit): a card whose last close or reopen was a person's is
reported, never changed.  **Permanent git history never fails it**
(``R-BAL184``): what ``dev`` and ``main`` already hold wrongly (a marker
naming no card, or a card no change ships; an entry under ``plan/ships/``
that is no card's marker; a task file naming no task) is printed apart as
history, since no tracker write can change it.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from tools.ci.trailers import DEV, MAIN
from tools.quill._git import MARKERS, Claim, task_file
from tools.quill._tracker import BareCard, Card
from tools.quill.setup_tracker import FILING, TASK

#: How long a claim's branch may go without a commit before ``quill next`` reports it
#: stale: the build plan's three days, measured from the branch's last commit since the
#: ship marker (R-BAL359; ``card/N`` idle 3 days, the report's option G).
STALE_CLAIM = timedelta(days=3)
#: The kinds of card a change ships; a ruling is a record and a question the
#: developer's, so ``sync`` never opens or closes either.
SHIPPABLE = ("step", "finding")
#: The kinds that name their owner step by GitHub's "blocked by" link to it, never as its
#: sub-issue, which its progress would count (ruling ``balance:R-BAL395``): the step is
#: the one card each is blocked by.
OWNED = ("finding", "ruling")


def leaves(card: BareCard, cards: Mapping[int, BareCard],
           shipped: Iterable[int]) -> tuple[int, ...]:
    """The steps ``card`` splits into, the one spelling: each step linked under it
    (:attr:`_tracker.Card.step_children`; the findings and rulings it owns are no
    sub-issues of it, R-BAL395) but one dropped before its filing finished -- CLOSED while still
    marked, and :func:`dropped` (not shipped by git's answer).

    Such a card was never part of the split: R-BAL205's reason ("It was never part of
    the split"), which R-BAL205 ruled for ``quill drop`` (which also unlinks it), carried
    by the L2 lane under R-BAL207 to a person's close of a still-marked leaf on the web,
    and to a read that lags ``quill drop``'s own unlink.  An OPEN marked leaf counts: its
    filing is under way, and its split step is not offered meanwhile.  A leaf git says
    shipped counts however it was closed: its work is done.  ``sync`` unlinks a card this
    rule leaves out (:func:`never_split`), so its mark stops deciding.  ``cards`` holds
    every card linked under ``card`` (:func:`missing`)."""
    shipped = set(shipped)
    return tuple(number for number in card.step_children
                 if not (FILING in cards[number].labels and not cards[number].is_open
                         and dropped(number, cards, shipped)))


def never_split(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> tuple[int, ...]:
    """The steps linked under ``card`` that are no leaf of it (:func:`leaves`): each dropped
    before its filing finished, so never part of the split (R-BAL205), and a link ``sync``
    removes (:func:`unsplit`)."""
    split = set(leaves(card, cards, shipped))
    return tuple(number for number in card.step_children if number not in split)


def is_container(card: BareCard, cards: Mapping[int, BareCard], shipped: Iterable[int]) -> bool:
    """A step split into steps: one with a leaf (:func:`leaves`).  A card that is not a
    step is no container, whatever hangs under it."""
    return card.kind == "step" and bool(leaves(card, cards, shipped))


def is_work(card: BareCard, cards: Mapping[int, BareCard], shipped: Iterable[int]) -> bool:
    """Whether a card is something a branch builds and a commit ships: a step or a
    finding, and not a container."""
    return card.kind in SHIPPABLE and not is_container(card, cards, shipped)


def no_work(card: BareCard, cards: Mapping[int, BareCard], shipped: Iterable[int]) -> str | None:
    """What ``card`` is when it is no work (:func:`is_work`), for a line saying so -- ``a
    container`` (:func:`is_container`), ``a card with no type``, or its type, ``a ruling``,
    ``a question``, ``a task`` or any other -- or None when it is work: how ``sync``'s
    history and CI's landing check both name such a card.  (``tools/release/_cards._what``
    still names a card with no type its own way, ``a card of no type``: a tracker card at
    the cutover.)"""
    if is_work(card, cards, shipped):
        return None
    if is_container(card, cards, shipped):
        return "a container"
    return _kind_named(card)


def _kind_named(card: BareCard) -> str:
    """``card``'s type as a line names it: ``a step``, ``a task``, ... or ``a card with no
    type``."""
    return f"a {card.kind}" if card.kind else "a card with no type"


def withdrawn(card: BareCard) -> bool:
    """Whether ``card`` is a ruling withdrawn: closed as anything but completed -- not
    planned or a duplicate, by ``quill drop`` or a person (R-BAL206).  A ruling closed as
    completed, by anyone, is a record."""
    return card.kind == "ruling" and not card.is_open and card.state_reason != "COMPLETED"


def filing_ended(card: BareCard) -> bool:
    """Whether ``card``'s filing ended without standing: a ruling :func:`withdrawn`, marked
    or not, or any other card CLOSED while still marked.  A ruling closed as completed
    while marked is no such card: its filing is unfinished.  Of two cards a filing may
    already have made, one that ended is passed over for one that stands."""
    return withdrawn(card) or (FILING in card.labels and not card.is_open
                               and card.kind != "ruling")


def withdrawn_filing(card: Card, shipped: Iterable[int]) -> bool:
    """Whether a DECISION ended ``card``'s filing, so nothing is ever filed over it: it
    :func:`filing_ended`, and it is not WORK git says shipped -- a ruling withdrawn, or a
    card dropped while still marked, by ``quill drop`` or a person.  Work that shipped and
    was closed while still marked (its filing stopped at its last write, and a person
    closed it as ``sync`` asks) was not dropped (:func:`dropped`): its filing is moot, not
    refused.  A ruling or a question is never shipped, so a marker naming one decides
    nothing here, as it decides nothing anywhere (R-BAL184)."""
    return filing_ended(card) and not (card.kind in SHIPPABLE and card.number in set(shipped))


def filing_unfinished(card: BareCard) -> bool:
    """Whether ``card``'s filing has not finished (R-BAL202) -- one still running, or one a
    failure cut short: it still carries the filing mark, and is open, or is a ruling
    closed as completed, by its own filing (which closes it before the last write removes
    the mark) or by anyone (R-BAL206: a ruling is a closed record, so that close
    withdraws nothing).  A ruling :func:`withdrawn` is not; any other card closed while
    marked was dropped before its filing finished; what either's filing left undone is
    moot."""
    return FILING in card.labels and (card.is_open or (
        card.kind == "ruling" and not withdrawn(card)))


def _shown_shipped(card: BareCard) -> bool:
    """Closed by the tool as completed: the tool's display that git says it shipped,
    which follows git and decides nothing."""
    return not card.is_open and card.closed_by_tool and card.state_reason == "COMPLETED"


def unshipped_shown_done(card: Card, shipped: Iterable[int]) -> bool:
    """Whether ``card``, as a piece of work, is shown shipped -- closed by the tool as
    completed -- though git does not say it shipped (a revert deleted its marker, or it was
    a split step left plain): :func:`shown_write` reopens it."""
    return card.number not in set(shipped) and _shown_shipped(card)


def _closed_dropped(card: BareCard) -> bool:
    """A card that is no container closed by a person (for any reason), or by the tool as
    not planned (``quill drop``)."""
    return not card.is_open and not _shown_shipped(card)


def dropped(number: int, cards: Mapping[int, BareCard], shipped: set[int]) -> bool:
    """Whether a card was dropped.

    A piece of work: closed as dropped, and not shipped (git's answer, whoever
    closed it).  A container: closed by a PERSON (R-BAL185), or every leaf
    dropped (R-BAL187) -- never its own close by the tool, which only shows its
    leaves (R-BAL190).  A container has a leaf (:func:`is_container`), so a step
    left with none is never one whose leaves were all dropped.
    """
    card = cards[number]
    if is_container(card, cards, shipped):
        return (not card.is_open and card.touched_by_hand) or all(
            dropped(leaf, cards, shipped) for leaf in leaves(card, cards, shipped))
    return _closed_dropped(card) and not (is_work(card, cards, shipped) and number in shipped)


def above(card: Card, cards: Mapping[int, Card]) -> Iterator[Card]:
    """The steps above ``card``, nearest first: the step it splits, that step's, ..."""
    while card.parent is not None:
        card = cards[card.parent]
        yield card


def missing(cards: Mapping[int, BareCard]) -> set[int]:
    """Cards the decisions below would read that ``cards`` does not hold yet:
    blockers, parents, and every step linked under a card, each read to decide whether
    it is a leaf (:func:`leaves`; the findings and rulings a card owns decide
    nothing)."""
    wanted = set()
    for card in cards.values():
        wanted.update(card.blocked_by)
        wanted.update(card.step_children)
        if card.parent is not None:
            wanted.add(card.parent)
    return wanted - set(cards)


def resolved(number: int, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """Whether a card is done with: dropped, shipped work, a container whose leaves are,
    or a closed ruling or question."""
    shipped = set(shipped)
    card = cards[number]
    if card.kind not in SHIPPABLE:
        return not card.is_open
    if dropped(number, cards, shipped):
        return True
    if is_container(card, cards, shipped):
        return all(resolved(leaf, cards, shipped) for leaf in leaves(card, cards, shipped))
    return number in shipped


def dropped_above(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> Card | None:
    """The nearest step above ``card`` that was dropped (R-BAL185); None when none was."""
    shipped = set(shipped)
    return next((step for step in above(card, cards) if dropped(step.number, cards, shipped)),
                None)


def is_live(number: int, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """Whether a card is still work to do: not resolved, and no step above it dropped.

    The owner a finding needs (``conventions.md`` rule 1; the one step it is blocked
    by, R-BAL395), and the step a new leaf may split.
    """
    return (not resolved(number, cards, shipped)
            and dropped_above(cards[number], cards, shipped) is None)


def leaves_below(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> list[Card]:
    """Every leaf below ``card`` that is work, in any state -- under the steps it splits,
    under theirs, ... (a step split again is not a leaf; its own leaves are), each level in
    card order.  ``cards`` holds them all (:func:`missing`)."""
    below, wanted = [], sorted(leaves(card, cards, shipped))
    while wanted:
        below += [cards[number] for number in wanted if is_work(cards[number], cards, shipped)]
        wanted = sorted(number for step in wanted for number in leaves(cards[step], cards,
                                                                          shipped))
    return below


def left_bare(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
              ending: set[int]) -> list[Card]:
    """The steps at or below ``card`` whose every leaf is in ``ending`` -- leaves
    whose filing has not finished, dropped now.  Closed while marked, those were never
    leaves (:func:`leaves`), so each such step is a plain step again: no leaf carries its
    drop (R-BAL190), so it carries its own.  One a PERSON closed keeps their close; one
    the tool closed (its display of git, or of its leaves) is closed again, as dropped.
    No two are nested: a step split again is a leaf of the
    one above it, and ``ending`` holds work only (:func:`leaves_below`), never a step
    split again.  ``cards`` holds them all (:func:`missing`)."""
    bare, wanted = [], [card]
    while wanted:
        bare += [step for step in wanted if (step.is_open or not step.touched_by_hand)
                 and (split := leaves(step, cards, shipped)) and set(split) <= ending]
        wanted = [cards[number] for step in wanted for number in leaves(step, cards, shipped)
                  if is_container(cards[number], cards, shipped)]
    return bare


def never_offered(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> str | None:
    """Why a piece of work is never offered, whatever its own blockers: its filing not
    finished (R-BAL202), a link of its own outside the tracker, a blocker outside the tracker on a
    step above it (R-BAL188), or a step above it dropped (R-BAL185); None when none holds
    it back."""
    shipped = set(shipped)
    if filing_unfinished(card):
        return ("its filing has not finished (R-BAL202): unless a `quill file` command is "
                "filing it now, the same command run again finishes it")
    if card.outside:
        links = ", ".join(f"its {link.what} {link.issue}" for link in card.outside)
        return f"it links {links}, outside the tracker (R-BAL188)"
    steps = list(above(card, cards))
    for step in steps:
        for link in step.outside:
            if link.what == "blocker":
                return (f"plan#{step.number} above it waits on {link.issue}, outside the "
                        "tracker (R-BAL188)")
    gone = next((step for step in steps if dropped(step.number, cards, shipped)), None)
    return f"plan#{gone.number} above it was dropped (R-BAL185)" if gone else None


def workable(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
             claims: Mapping[int, Claim]) -> bool:
    """Whether a card is a step someone could start now: open unshipped unclaimed work that
    nothing holds back (:func:`never_offered`), and every blocker of it and of each step
    above it resolved (R-BAL182)."""
    shipped = set(shipped)
    return (
        card.is_open
        and card.kind == "step"
        and is_work(card, cards, shipped)
        and never_offered(card, cards, shipped) is None
        and card.number not in shipped
        and card.number not in claims
        and all(resolved(blocker, cards, shipped)
                for step in (card, *above(card, cards)) for blocker in step.blocked_by)
    )


def outside_reports(cards: Mapping[int, Card]) -> list[str]:
    """Each link an open card has to an issue outside the tracker, as a report line
    (R-BAL188): the card is not offered until someone removes it."""
    return [
        f"plan#{card.number}'s {link.what} is {link.issue}, outside the tracker: "
        f"plan#{card.number} is not offered until that link is removed (R-BAL188)"
        for card in sorted(cards.values(), key=lambda card: card.number) if card.is_open
        for link in card.outside
    ]


def unfinished_reports(cards: Mapping[int, Card], shipped: Iterable[int]) -> list[str]:
    """Each card in ``cards`` whose filing has not finished (:func:`filing_unfinished`), as
    a report line: it is never offered until the same command finishes it, or, while it is
    open and git does not say it shipped, ``quill drop`` drops it -- a leaf unlinked from
    its split step first (R-BAL205), which the line says; a closed ruling is withdrawn by
    reopening it and closing it as not planned (R-BAL206); and ``quill show`` says how to
    finish it by hand."""
    shipped = set(shipped)
    return [
        f"plan#{card.number}'s filing has not finished, so it is never offered (R-BAL202): "
        "unless a `quill file` command is filing it now, run that command again to finish it"
        + ("" if card.number in shipped else ", or `quill drop` it" if card.is_open else
           ", or, to withdraw it, reopen it and close it as not planned on the web")
        + (f" (which unlinks it from plan#{card.parent} first: it was never part of that split, "
           "R-BAL205)" if card.is_open and is_work(card, cards, shipped) and card.kind == "step"
           and card.parent and card.number not in shipped else "")
        + f"; with that command lost, `quill show plan#{card.number}` says how to finish it"
        for card in sorted(cards.values(), key=lambda card: card.number)
        if filing_unfinished(card)
    ]


@dataclass(frozen=True)
class NextAnswer:
    """The first workable step in the board's order, and what the order cannot place."""

    card: Card | None
    unplaced: tuple[Card, ...]


def next_step(order: Iterable[int], cards: Mapping[int, Card], shipped: Iterable[int],
              claims: Mapping[int, Claim], arc: str | None = None) -> NextAnswer:
    """The first workable step in ``order`` (the board's card numbers, top first).

    A workable step that is NOT on the board -- filed by hand on the web, or
    just filed and not yet shown by a lagging board -- has no place in the
    order, so it is never chosen and never skipped silently: it is returned in
    ``unplaced`` for the caller to say so.
    """
    shipped = set(shipped)

    def fits(card: Card) -> bool:
        return workable(card, cards, shipped, claims) and (arc is None or arc in card.labels)

    first = next((cards[n] for n in order if n in cards and fits(cards[n])), None)
    placed = set(order)
    unplaced = tuple(sorted(
        (card for card in cards.values() if card.number not in placed and fits(card)),
        key=lambda card: card.number,
    ))
    return NextAnswer(first, unplaced)


@dataclass(frozen=True)
class Placement:
    """Where a leaf goes on the board (R-BAL179).

    ``move``: whether it is moved; when not, it stays where it is, or where
    GitHub adds it, at the bottom.  ``after``: the board item it goes just after
    when it is moved, None for the top.  ``remove``: the split step's own item,
    which leaves the board as the step becomes a container.
    """

    move: bool
    after: str | None
    remove: str | None
    note: str


def leaf_placement(order: Iterable[tuple[int, str]], parent: Card, leaf: int,
                   cards: Mapping[int, Card], shipped: Iterable[int]) -> Placement:
    """Where leaf ``leaf`` of ``parent`` goes, given the board's ``order``
    (``(card number, item id)``, top first), and ``cards`` and ``shipped`` to read
    ``parent``'s leaves (:func:`leaves`).

    In the split step's place while it is on the board; else just after the
    lowest of the leaves filed before it; else just above the highest of the
    leaves filed after it (a leaf whose link failed was no leaf yet, so a later
    leaf could take the split step's place before its filing finished); else
    nowhere new: where it is, or where GitHub adds it, at the bottom.  Filing
    order is the cards' numbers, never the sub-issue list's, which a person may
    drag.  No leaf it is placed by has an unfinished filing: a leaf is never
    filed while another of its split step's is unfinished (R-BAL204).  One
    dropped before its filing finished is no leaf, so nothing is placed by it,
    wherever a failed move left it.
    """
    order = [(number, item) for number, item in order if number != leaf]
    positions = [item for _, item in order]
    items = dict(order)
    if parent.board_item is not None:
        return Placement(True, parent.board_item, parent.board_item,
                         f"into plan#{parent.number}'s place")
    anchors = [number for number in leaves(parent, cards, shipped) if number in items]
    earlier = [items[number] for number in anchors if number < leaf]
    if earlier:
        return Placement(True, max(earlier, key=positions.index), None,
                         f"to just after the leaves of plan#{parent.number} filed before it")
    later = [items[number] for number in anchors if number > leaf]
    if later:
        first = positions.index(min(later, key=positions.index))
        return Placement(True, positions[first - 1] if first else None, None,
                         f"to just above the leaves of plan#{parent.number} filed after it")
    return Placement(False, None, None,
                     f"no other leaf of plan#{parent.number} is on the board to place it by")


@dataclass(frozen=True)
class Unsplit:
    """R-BAL205's undo of a split that leaves whose filing never finished began.

    ``step``: the card they are linked under.  ``unlink``: the leaves taken out of
    it.  ``add``: whether ``step`` is put back on the board first, where GitHub adds
    it, at the bottom.  ``after``: the leaf whose place on the board ``step`` then
    takes, and that leaf's board item, which ``step`` is moved just after; None when
    it is not moved.
    """

    step: int
    unlink: tuple[int, ...]
    add: bool
    after: tuple[int, str] | None


def unsplit(step: Card, out: Iterable[int], cards: Mapping[int, Card], shipped: Iterable[int],
            board: Iterable[tuple[int, str]] | None) -> Unsplit:
    """Take the leaves ``out`` out of the split of ``step`` (R-BAL205): each was never part of
    it, and is unlinked last, so the same command run again still sees the link and
    finishes every write before it.

    ``board`` is the board's order (``(card number, item id)``, top first) when ``step``
    will be open once the caller's other writes land, and None when it will not -- one a
    person closed, or one the caller closes or drops -- which never goes back on it.
    When no leaf of ``step`` is left without them (:func:`leaves`), a STEP that will be
    open is a plain step again, offered as work only from the board, in the place its
    split began: just after the one of ``out`` highest on the board.  So it is put on the
    board when it is off it, and moved there; and when it is on it already but below that
    leaf and not right after it -- a run whose move failed, or whose add lost its answer,
    left it at the bottom -- it is moved there too.  One above that leaf stays: it never
    left its place (a leaf's own failed move leaves the leaf below it), or a person put it
    there.  With none of ``out`` on the board, an added step stays where GitHub adds it.
    ``cards`` holds ``step`` and every card linked under it (:func:`missing`)."""
    out = tuple(sorted(out))
    order = list(board or ())
    positions = [number for number, _ in order]
    items = dict(order)
    plain = (board is not None and step.kind == "step"
             and set(leaves(step, cards, shipped)) <= set(out))
    anchor = next(((number, items[number]) for number in positions if number in out), None)
    add = plain and step.board_item is None
    below = (step.board_item is not None and step.number in positions and anchor is not None
             and positions.index(step.number) > positions.index(anchor[0]) + 1)
    return Unsplit(step.number, out, add, anchor if plain and (add or below) else None)


def shown_write(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> str | None:
    """The state write that makes the tool's display of ``card`` what git and its leaves
    say -- ``"reopen"``, or a close as ``"completed"`` or as ``"not_planned"`` -- or None when
    it shows that already.  A step split into smaller steps shows its leaves
    (:func:`container_shown`, R-BAL190); a piece of work the tool showed shipped that git
    does not say shipped is reopened (:func:`unshipped_shown_done`).  Open work git says
    shipped is closed by ``_sync_shipped``, which keeps a person's reopen, so this never
    decides it, and no caller passes a card a person last closed or reopened, which holds their
    decision: ``sync`` reports what it must (``_sync_container``), and ``quill drop`` writes
    only a card the tool closed (:func:`drop_shows`).  ``sync`` writes it
    (:func:`sync_plan`), and so does ``quill drop`` for the card it takes a leaf out of."""
    shipped = set(shipped)
    if is_container(card, cards, shipped):
        wanted = container_shown(card, cards, shipped)
        if (None if card.is_open else card.state_reason) == wanted:
            return None
        return {None: "reopen", "NOT_PLANNED": "not_planned", "COMPLETED": "completed"}[wanted]
    return "reopen" if unshipped_shown_done(card, shipped) else None


def drop_shows(split: Card, leaf: Card, cards: Mapping[int, Card],
               shipped: Iterable[int]) -> str | None:
    """The write ``quill drop`` makes to ``split``'s state before taking ``leaf`` -- a leaf of
    it whose filing never finished -- out of its split (C2 review LOW 6): the one ``sync``
    would make (:func:`shown_write`) to ``split`` as the drop leaves it, with ``leaf``
    closed as not planned by the tool and still marked, so no leaf of it (:func:`leaves`).

    Only for a card the TOOL closed: its state is the tool's display, and once the leaf
    is unlinked, no card ``sync`` reads may lead to it again (unless one waits on it,
    has a marker or a claim), so the drop writes it.  Left a plain step (or a
    finding a person linked the leaf under), it is reopened when it is shown done though
    git never shipped it, and a plain step the tool closed as not planned (a drop) stays
    dropped.  Still split by other leaves, it shows them: reopened while one is still
    work, or closed again as they say.  An open card is read by every ``sync``, which
    shows it; one a PERSON closed keeps their close (R-BAL185).  None: no write."""
    if not split.closed_by_tool:
        return None
    gone = replace(leaf, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True,
                   touched_by_hand=False)
    return shown_write(split, {**cards, leaf.number: gone}, shipped)


def drop_unlinks(card: Card, cards: Mapping[int, Card], shipped: Iterable[int],
                 ending: set[int]) -> list[Unsplit]:
    """What a drop of ``card`` unlinks (R-BAL205), so no link it leaves behind could ever
    decide anything: under ``card`` and every step below it, each step linked there that is
    no leaf once the drop has closed the unfinished filings in ``ending`` -- one of those,
    or one already closed while still being filed (:func:`never_split`) -- and below each
    of those too.  Unlinks only: no step they are linked under goes back on the board
    (each is dropped, keeps a leaf, or shipped).  A card that is not a
    step splits nothing: nothing is unlinked from it, nor from what hangs under it, which
    its drop does not close.  ``cards`` holds them all (:func:`missing`)."""
    undos, wanted = [], [card] if card.kind == "step" else []
    while wanted:
        step = wanted.pop(0)
        stale = set(never_split(step, cards, shipped)) | ending
        out = [number for number in step.step_children if number in stale]
        if out:
            undos.append(unsplit(step, out, cards, shipped, None))
        wanted += [cards[number] for number in step.step_children]
    return undos


def release_hint(claim: Claim) -> str:
    """The command that releases ``claim``."""
    return f"`quill release plan#{claim.card}`"


def holder(claim: Claim) -> str:
    """``claim`` as a line names it: its branch, when it was claimed (its marker's instant,
    or that its tip holds no marker for the card -- a ``card/N`` pushed by hand) and when its
    tip was last committed."""
    claimed = (f"claimed {claim.claimed}" if claim.claimed is not None else
               f"its tip holds no {MARKERS}/{claim.card}, so when it was claimed is unknown")
    return f"{claim.branch} ({claimed}; last commit {claim.committed})"


def done_tasks(cards: Mapping[int, BareCard], tasks: Iterable[int],
               claims: Mapping[int, Claim]) -> frozenset[int]:
    """Each task among ``cards`` that git says is done (R-BAL386): its file is in the merge of
    ``dev`` and ``main`` (``tasks``, :attr:`_git.Shipped.tasks`), or the live claim
    (``claims``) of the card the tracker says it is under wrote it (:attr:`_git.Claim.tasks`).
    The ONE reading of a task's state, which ``sync`` and ``show`` ask; ``quill drop`` asks
    only whether that merge holds its file (:func:`drop_takes`), as a drop abandons the
    claim.  A file naming a card that is no task, or a task under another card, makes
    nothing done (:func:`names_no_task`, which each file is read by)."""
    done = {number for number in tasks if names_no_task(number, cards) is None}
    for claim in claims.values():
        done.update(number for number in claim.tasks
                    if names_no_task(number, cards, claim.card) is None)
    return frozenset(done)


def drop_takes(task: BareCard, tasks: Iterable[int]) -> bool:
    """Whether a drop of the card ``task`` is under drops it too (R-BAL190's shape: the tool
    records the decision where the work is): it is no drop yet -- open, or closed by the tool
    as done, which is only its display of git -- and its file is not in the merge of ``dev``
    and ``main`` (``tasks``), which no drop undoes, as a split step's drop leaves a leaf git
    says shipped.  A task done only on its card's claim is dropped with the card, whose
    branch a drop abandons; one a person closed keeps their close."""
    return not _closed_dropped(task) and task.number not in set(tasks)


def stale_claims(claims: Mapping[int, Claim], now: datetime) -> list[Claim]:
    """Claims whose branch's tip was committed more than :data:`STALE_CLAIM` before ``now``:
    work idle that long, whoever holds it."""
    return [claim for claim in claims.values()
            if now - datetime.fromisoformat(claim.committed) > STALE_CLAIM]


@dataclass
class SyncPlan:
    """What ``sync`` would write -- ``close`` as completed, ``drop`` (close as not
    planned), ``reopen``; what it found and must leave to a person (``reports``); and
    what ``dev`` and ``main`` hold wrongly that no tracker write can change
    (``history``, R-BAL184).  Its undo of a split is :func:`sync_unsplits`."""

    close: list[int] = field(default_factory=list)
    drop: list[int] = field(default_factory=list)
    reopen: list[int] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)


def _sync_shipped(card: Card, plan: SyncPlan) -> None:
    """An open card git says shipped: closed as completed, unless a PERSON reopened it,
    which is reported.  No check of the branch that shipped it stands between git and the
    close: a marker is written by ``quill claim`` for the card it claims, never typed, and
    which change may add or change one is CI's one write rule (R-BAL359,
    ``tools/quill/landing.py``), judged where the change lands: on ``main`` from the first
    release that carries the rule there, since main's queue runs main's own code."""
    if card.touched_by_hand:
        plan.reports.append(
            f"plan#{card.number} shipped in git ({MARKERS}/{card.number} is on {DEV} or "
            f"{MAIN}) but a person reopened it: close it by hand, or revert the change that "
            "shipped it"
        )
        return
    plan.close.append(card.number)


def names_no_task(number: int, cards: Mapping[int, BareCard],
                  under: int | None = None) -> str | None:
    """Why the task file ``plan/tasks/<number>`` marks nothing done (:func:`done_tasks`): the
    card it names does not exist, or is no task, or -- read at the tip of card ``under``'s
    claim -- is a task under another card, or under none; None when it names a task (under
    ``under``, when given).  The ONE reading of which branch a task's file is done on: what
    git says is done reads each file by it (:func:`done_tasks`), ``sync`` reports each file
    it says marks nothing done (:func:`_task_files`), and CI's landing check refuses a change
    from ``card/<under>`` writing such a file (``tools/quill/landing.py``, R-BAL386)."""
    card = cards.get(number)
    if card is None:
        return f"plan#{number} does not exist"
    if card.kind != TASK:
        return f"plan#{number} is {_kind_named(card)}, no task"
    if under is not None and card.parent != under:
        where = "no card" if card.parent is None else f"plan#{card.parent}"
        return f"task plan#{number} is under {where}, not plan#{under}"
    return None


def _stale_task(card: Card, cards: Mapping[int, Card], shipped: set[int]) -> str | None:
    """The REPORT for ``card``, a task not done, when it can never be done: the card it is
    under was dropped, is no work (a container, which no change ships, or a card of another
    kind), or shipped without its file; or it is under no card.  None while that card is work
    still to do, or while its parent is outside the tracker, which that link's own report
    names (R-BAL188).  Never closed by ``sync``: whether it was dropped is a person's call."""
    number, drop = card.number, f"drop it (`quill drop plan#{card.number}`)"
    if card.parent is None:
        return (None if any(link.what == "parent" for link in card.outside) else
                f"task plan#{number} is open under no card: link it under the step or finding "
                f"it is a piece of, or {drop}")
    parent = cards[card.parent]
    if dropped(parent.number, cards, shipped):
        why = "was dropped"
    elif what := no_work(parent, cards, shipped):
        why = f"is {what}, which no change ships"
    elif parent.number in shipped:
        why = f"shipped without {task_file(number)}"
    else:
        return None
    return (f"task plan#{number} is open under plan#{parent.number}, which {why}, so it is "
            f"never done: {drop}")


def _sync_task(card: Card, plan: SyncPlan, cards: Mapping[int, Card], shipped: set[int],
               done: frozenset[int]) -> None:
    """A task's state shows git's (R-BAL386), as a piece of work's does: an open task git says
    is done (:func:`done_tasks`) is closed as completed, unless a PERSON reopened it, which is
    reported; one the tool showed done that git no longer says is done is reopened; a
    person's close is a drop, and is left.  An open task that can never be done -- reopened
    now, or open already -- is reported (:func:`_stale_task`)."""
    number = card.number
    if number in done:
        if card.is_open and card.touched_by_hand:
            plan.reports.append(
                f"task plan#{number} is done in git ({task_file(number)} is on its card's "
                f"claim, or on {DEV} or {MAIN}) but a person reopened it: close it by hand, or "
                "take its file out of the branch that holds it")
        elif card.is_open:
            plan.close.append(number)
        return
    reopen = unshipped_shown_done(card, done)
    if reopen:
        plan.reopen.append(number)
    if (card.is_open or reopen) and (stale := _stale_task(card, cards, shipped)):
        plan.reports.append(stale)


def _task_files(plan: SyncPlan, cards: Mapping[int, Card], tasks: Iterable[int],
                claims: Mapping[int, Claim]) -> None:
    """Each task file that marks nothing done (:func:`names_no_task`): a HISTORY line for one
    in the merge of ``dev`` and ``main`` (``tasks``), which no tracker write can change
    (R-BAL184), and a REPORT for one a live claim's own commits wrote
    (:attr:`_git.Claim.tasks`), which a person takes off that branch: a task is done on its
    own card's branch only."""
    for number in sorted(set(tasks)):
        if why := names_no_task(number, cards):
            plan.history.append(f"the task file {task_file(number)} on {DEV} or {MAIN} marks "
                                f"nothing done ({why}): it is not acted on")
    for claim in sorted(claims.values(), key=lambda claim: claim.card):
        for number in sorted(claim.tasks):
            if why := names_no_task(number, cards, claim.card):
                plan.reports.append(
                    f"{claim.branch}'s tip holds {task_file(number)}, but {why}, so it marks "
                    f"nothing done (a task is done on its own card's branch only): take it off "
                    f"{claim.branch}")


def container_shown(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> str | None:
    """The state the tool shows a container in, its display of its leaves (R-BAL190): open
    (None) while one is still work, closed as not planned (``NOT_PLANNED``) once all were
    dropped (R-BAL187), else closed as completed (``COMPLETED``).  ``sync`` writes it
    (:func:`shown_write`)."""
    shipped = set(shipped)
    if not resolved(card.number, cards, shipped):
        return None
    return "NOT_PLANNED" if dropped(card.number, cards, shipped) else "COMPLETED"


def _sync_container(card: Card, plan: SyncPlan, cards: Mapping[int, Card],
                    shipped: set[int]) -> None:
    """A container's state shows its leaves (R-BAL190): open while one is still
    work, closed as not planned once all were dropped (R-BAL187), else closed as
    completed -- reopened, closed or re-closed as they change.

    A person's close is a drop, so the one state a person leaves that its leaves
    contradict is a container reopened by hand while its leaves are all done.
    """
    wanted = container_shown(card, cards, shipped)
    if card.touched_by_hand:
        if wanted is not None and card.is_open:
            plan.reports.append(
                f"container plan#{card.number} was reopened by a person's hand, while its "
                "leaves are all done: to put work back under it, reopen by hand a leaf that "
                "was dropped or that a person closed, and revert the change that shipped one "
                "that shipped; to leave it, close it by hand, which records it dropped (R-BAL190)"
            )
        return
    _record(plan, card.number, shown_write(card, cards, shipped))


def _record(plan: SyncPlan, number: int, write: str | None) -> None:
    """Put :func:`shown_write`'s write for card ``number`` in ``plan``."""
    if write is not None:
        {"reopen": plan.reopen, "completed": plan.close, "not_planned": plan.drop}[write].append(
            number)


def _claimed_split(card: Card, claim: Claim, cards: Mapping[int, Card], shipped: set[int]) -> str:
    """The REPORT for ``claim`` on ``card``, a step split into smaller steps: release the
    claim -- or, when every leaf of ``card`` is still being filed and not shipped, drop
    each of them, which keeps it whole (R-BAL205).  (``quill file``'s refusal under a claim
    names that drop for its own leaf only, when no other leaf splits the step.)"""
    split = leaves(card, cards, shipped)
    filing = [number for number in split
              if filing_unfinished(cards[number]) and number not in shipped]
    keep = ("" if len(filing) < len(split) else
            "; or, to keep it whole, " + " and ".join(f"`quill drop plan#{number}`"
                                                      for number in filing)
            + ": still being filed, so never part of the split (R-BAL205)")
    return (f"plan#{card.number} is split into smaller steps, but {holder(claim)} still claims "
            "it: a split step is never shipped itself, its smaller steps are the work "
            f"(R-BAL177), so release the claim with {release_hint(claim)}" + keep)


def sync_plan(cards: Mapping[int, Card], shipped: Iterable[int],
              claims: Mapping[int, Claim], tasks: Iterable[int] = ()) -> SyncPlan:
    """What ``sync`` writes so each step, finding and task shows git's answer.

    ``tasks``: each task whose file the merge of ``dev`` and ``main`` holds
    (:attr:`_git.Shipped.tasks`; none unless named).  ``cards`` holds every card a task
    file there or one a claim wrote names, and every task under a card it holds, so each
    task's state is written from :func:`done_tasks` (:func:`_sync_task`) and each file that
    marks nothing done is named (:func:`_task_files`).

    A claim on a step split into smaller steps is reported, never released: a
    branch building that step whole would ship a marker that names a split
    step, which no change ships.  ``quill file`` refuses a leaf under a claimed
    step (C2 review M4), but some claims it cannot see (its docstring names them)
    still land on one.  ``cards`` holds every claimed card, so each such claim is
    seen (:func:`_claimed_split`).
    """
    shipped = set(shipped)
    plan = SyncPlan(reports=outside_reports(cards) + unfinished_reports(cards, shipped))
    _task_files(plan, cards, tasks, claims)
    done = done_tasks(cards, tasks, claims)
    for card in sorted(cards.values(), key=lambda card: card.number):
        if card.kind == "ruling" and card.is_open and not filing_unfinished(card):
            plan.reports.append(
                f"ruling plan#{card.number} is open, though a ruling is a record its filing "
                "closes: close it by hand"
            )
        container = is_container(card, cards, shipped)
        if card.number in shipped and (what := no_work(card, cards, shipped)):
            plan.history.append(
                f"the marker {MARKERS}/{card.number} names plan#{card.number}, {what}, which "
                "no change ships (a step split after it was claimed?): it is not acted on"
            )
        if container and (claim := claims.get(card.number)):
            plan.reports.append(_claimed_split(card, claim, cards, shipped))
        if card.kind == TASK:
            _sync_task(card, plan, cards, shipped, done)
        if card.kind not in SHIPPABLE:
            continue
        if container:
            _sync_container(card, plan, cards, shipped)
        elif card.number in shipped and card.is_open:
            _sync_shipped(card, plan)
        else:
            _record(plan, card.number, shown_write(card, cards, shipped))
        if card.kind == "step" and card.is_open and (gone := dropped_above(card, cards,
                                                                            shipped)):
            plan.reports.append(
                f"plan#{card.number} is open under plan#{gone.number}, which was dropped, "
                "so it is never offered (R-BAL185): drop it, or give it a live parent"
            )
        if card.kind == "finding" and card.is_open and card.number not in shipped and (
                lost := _owner_lost(card, cards, shipped)):
            plan.reports.append(f"finding plan#{card.number}'s owner {lost}: give it one open "
                                "step as its owner, the one card it is blocked by (R-BAL395)")
    return plan


def sync_unsplits(cards: Mapping[int, Card], shipped: Iterable[int], plan: SyncPlan,
                  order: Iterable[tuple[int, str]]) -> list[Unsplit]:
    """R-BAL205's undo of every split in ``cards`` that a step closed while still being
    filed is linked under (:func:`never_split`), each by :func:`unsplit`: what ``sync``
    writes after ``plan``'s state writes, so no such link stays.

    The link decides nothing while that step keeps its filing mark (:func:`leaves`),
    but the mark is all that makes it no leaf: a person who removed the stale label
    would make it a dropped leaf, and R-BAL187 would then drop a split step that was
    never split, built or shipped as a plain step or not (review M4).  Whether the
    split step will be open is read through ``plan``: open and not closed by it (as
    completed, or as not planned), or reopened by it.  Only a step's links are undone:
    a card that is not a step splits nothing, so its links decide nothing.

    Such a link is left by a person's close on the web, or by a command a failure cut
    short: the tool's own drops unlink what they close (:func:`drop_unlinks`).  ``sync``
    reads every card still marked, closed or open, and so every such leaf and the step
    above it (:func:`missing`): each link is undone at the next run, wherever its split
    step stands.  A person who removes the label, or reopens the leaf, before that run
    acts on the link as it stands.
    """
    shipped = set(shipped)
    order = list(order)
    undos = []
    for card in sorted(cards.values(), key=lambda card: card.number):
        if card.kind == "step" and (out := never_split(card, cards, shipped)):
            opened = ((card.is_open and card.number not in plan.close + plan.drop)
                      or card.number in plan.reopen)
            undos.append(unsplit(card, out, cards, shipped, order if opened else None))
    return undos


def owner(card: BareCard) -> int | None:
    """A finding's or a ruling's owner (ruling ``balance:R-BAL395``, :data:`OWNED`), the one
    spelling: the card of the tracker it is blocked by, when nothing else blocks it, in the
    tracker or outside it.  None for a card of another kind, and for one blocked by no card
    of the tracker, by an issue outside it, or by more than one, which names no one owner.
    Whether that card is a step, and live, is the reader's to ask (:func:`_owner_lost`)."""
    if card.kind not in OWNED or len(card.blocked_by) != 1 or any(
            link.what == "blocker" for link in card.outside):
        return None
    return card.blocked_by[0]


def _owner_lost(card: Card, cards: Mapping[int, Card], shipped: set[int]) -> str | None:
    """How an open finding lost its live owner (:func:`owner`), the one step it is blocked
    by (R-BAL395); None while it has one -- or an owner outside the tracker, which the
    outside link's own report names (R-BAL188).  Blocked by two cards or more, it names no
    one owner."""
    number = owner(card)
    if number is None:
        outside = [link.issue for link in card.outside if link.what == "blocker"]
        if len(card.blocked_by) + len(outside) > 1:
            named = ", ".join([f"plan#{held}" for held in card.blocked_by] + outside)
            return f"is not one step: it is blocked by {named}"
        return None if outside else "is missing"
    held = cards[number]
    if held.kind != "step":
        return f"plan#{held.number} is {_kind_named(held)}, no step"
    return (None if is_live(held.number, cards, shipped) else
            f"plan#{held.number} is no longer live (done, or under a dropped step)")
