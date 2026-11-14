"""Where a card goes on the board: the one home of it (rulings plan#4011 to plan#4017).  Nothing
here reads GitHub or writes: the caller hands over the board's order and the cards, and writes
what this plans.

**The board reads in ``quill next``'s order** (ruling plan#4016, amending plan#1831): Urgent, then
High, then Medium, then Low, then the cards no Priority ranks.  A card's group is its rank as
``quill next`` reads it, the highest Priority at it or at a step above it (the group of
:func:`_next.turn`, the one key both sort by), so a leaf ranks through its split step and takes
its group from it.  Within a group the developer's drag decides its place (R-BAL177).

**The regroup** (ruling plan#4011, :func:`moves`) makes the board a stable sort by group of the
rows it may move (:func:`movable`): open steps and questions on the board that a Priority ranks
and nothing pauses.  It keeps the longest run of them already in group order where it is, and
moves each other row, so it makes the fewest moves and keeps the developer's order inside each
group; a second pass plans nothing.  Of two runs as long, it keeps the one that leaves more of
the rows it leaves alone between the groups they sat between.  A drag that puts a card in
another group is undone at the next pass.  It never moves a card no Priority ranks (an undecided
card, ruling plan#4017), a paused card (On hold at it or at a step above it, ruling plan#4015) or
a closed one.

**Where a moved row lands.**  Just under the row before it in that sort, or at the top when none
is.  A row that opens its group goes under the rows the regroup leaves alone that sit directly
under the end of the group above it, when a row of a later group follows them, so each such run
stays between the two groups: the cards waiting under the High cards stay under the High cards
(ruling plan#4017), and a paused card at the end of its group stays at that end (ruling
plan#4015: a paused High card stays among the High rows).  The rows left alone below the last row
a Priority ranks are the no-Priority group, which a ranked row goes above.  Decided under
R-BAL207, with the tie-break above: the rulings place those cards and leave them alone, and this
keeps a card moving past them from landing between them and their group.

**A Priority changed** (ruling plan#4013) needs no rule of its own.  On a board the last pass
grouped, a card whose Priority drops is above every card of its new group, so the sort puts it
first there; one whose Priority rises is below every card of its new group, so it goes last.
Nothing reads which way it moved.  A card dropped from the end of the High group is already
first in its new group where it stands, so it stays, above any cards waiting there.  A Priority
cleared through ``quill priority --clear`` puts the card at the top of the no-Priority group,
just under the Low cards (:func:`goes_after` with ``LOW``); one cleared on the web leaves the card
where it is, since the regroup leaves undecided cards alone (R-BAL207's application of plan#4013
under plan#4017).

**A card filed** goes:
- a new top-level step or question suggested Urgent or High, just under the High cards
  (:func:`goes_after` with ``HIGH``; ruling plan#4017, amending plan#3555), where it waits until
  the developer sets its Priority, shown in the Inbox meanwhile (rulings plan#2800, plan#3231);
  one suggested Medium or Low, or nothing, at the bottom, where GitHub adds it;
- the card the weekly update files, which holds High, first in the High group, just under the
  Urgent cards (:func:`goes_after` with ``HIGH`` and ``first``; ruling plan#4014, amending
  plan#3620);
- a leaf, where its split step sat (R-BAL179, :func:`_state.leaf_placement`); the regroup then
  moves it only if it ranks in another group.

A task nests under its step on the board, in no place of this order (ruling plan#1814), and is no
row of it (:meth:`_tracker.Board.order`).
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from itertools import takewhile
from operator import itemgetter
from typing import NamedTuple

from tools.quill._fields import RANKS
from tools.quill._next import ranked, turn
from tools.quill._state import held_at
from tools.quill._tracker import Card


class Move(NamedTuple):
    """One row the regroup moves: card ``number``, whose board item is ``item``, goes just after
    ``after``, a card number and its board item, or to the top when None."""

    number: int
    item: str
    after: tuple[int, str] | None


class _Pass(NamedTuple):
    """A pass planned (:func:`_plan`): its ``moves`` in the order they are written, the
    ``board``'s card numbers as they leave it, top first, and the ``groups`` of the rows it may
    move."""

    moves: list[Move]
    board: list[int]
    groups: dict[int, int]


def movable(card: Card, cards: Mapping[int, Card]) -> bool:
    """Whether the regroup may move ``card``, a row of the board's order: it is open, a Priority
    at it or at a step above it ranks it (:func:`_next.ranked`), and nothing at it or above it
    pauses it (:func:`_state.held_at`).  ``cards`` holds every step above it."""
    return card.is_open and ranked(card, cards) and held_at(card, cards) is None


def _spots(order: Sequence[tuple[int, str]],
           groups: Mapping[int, int]) -> Iterator[tuple[int, int]]:
    """Each row of ``order`` that :func:`_kept`'s run may hold, in the board's order, with its
    spot: twice its group for a row the regroup may move (one of ``groups``); for a row it leaves
    alone between two such rows in group order, twice the group above it, and one more when the
    group below it is a later one, since it sits at the end of the group above (as the cards
    waiting under the High cards do).  A row left alone above the first row it may move, below
    the last, or between two out of group order, is passed: it says nothing of where it sits."""
    above: int | None = None
    alone: list[int] = []
    for number, _ in order:
        if number not in groups:
            alone.append(number)
            continue
        group = groups[number]
        if above is not None and above <= group:
            yield from ((row, 2 * above + (above < group)) for row in alone)
        alone, above = [], group
        yield number, 2 * group


def _kept(order: Sequence[tuple[int, str]], groups: Mapping[int, int]) -> set[int]:
    """The rows the regroup may move (``groups``) that stay where they are: a longest run of them
    already in group order, so it moves the fewest, and of such runs the one keeping the most
    rows it leaves alone in their spots (:func:`_spots`), such as the cards waiting at the end of
    a group.  A run is rows whose spots never fall, each row it may move outweighing every row
    left alone together.  ``best`` holds, for each spot, the heaviest run so far ending at it and
    the row ending it; ``links`` each row's row before it in its run.  Spots run no higher than
    twice the last group, so each row reads a handful of them."""
    heavy = len(order) + 1
    best: dict[int, tuple[int, int]] = {}
    links: dict[int, int | None] = {}
    for number, spot in _spots(order, groups):
        weight, links[number] = max((run for value, run in best.items() if value <= spot),
                                    key=itemgetter(0), default=(0, None))
        weight += heavy if number in groups else 1
        if weight > best.get(spot, (0, number))[0]:
            best[spot] = (weight, number)
    row = max(best.values(), key=itemgetter(0))[1] if best else None
    kept: set[int] = set()
    while row is not None:
        kept.add(row)
        row = links[row]
    return kept & set(groups)


def _under(board: Sequence[int], row: int, groups: Mapping[int, int]) -> int:
    """The row a row opening the group after ``row``'s goes just after: the last of the rows the
    regroup leaves alone directly under ``row``, when a row it may move (one of ``groups``)
    follows them, so they stay between the two groups; else ``row`` itself, the rows left alone
    below the last ranked row being the no-Priority group."""
    start = board.index(row) + 1
    run = list(takewhile(lambda number: number not in groups, board[start:]))
    return run[-1] if run and start + len(run) < len(board) else row


def _plan(order: Sequence[tuple[int, str]], cards: Mapping[int, Card]) -> _Pass:
    """The pass over ``order``, the board's order (``(card number, board item)``, top first),
    with ``cards`` holding every open card in it and every step above each; a row it does not
    hold is a closed card, which the regroup never moves.  Each row moved goes after the row
    before it in the sort, or under the rows left alone there when it opens its group
    (:func:`_under`), read off the board as the moves before it leave it."""
    items = dict(order)
    places = {number: place for place, (number, _) in enumerate(order)}
    turns = {number: turn(number, cards, places) for number in places
             if number in cards and movable(cards[number], cards)}
    groups = {number: key.group for number, key in turns.items()}
    kept = _kept(order, groups)
    target = sorted(turns, key=turns.__getitem__)
    board = [number for number, _ in order]
    planned = []
    for before, number in zip([None, *target], target):
        if number in kept:
            continue
        board.remove(number)
        after = before if before is None or groups[before] == groups[number] else _under(
            board, before, groups)
        board.insert(0 if after is None else board.index(after) + 1, number)
        planned.append(Move(number, items[number], None if after is None else (after,
                                                                               items[after])))
    return _Pass(planned, board, groups)


def moves(order: Sequence[tuple[int, str]], cards: Mapping[int, Card]) -> list[Move]:
    """The regroup's moves over ``order``, the board's order (``(card number, board item)``, top
    first), in the order they are written, each planned on the board as the moves before it leave
    it.  A pass keeps nothing for the next, which plans from the board as it finds it, so a pass
    cut short is finished by the next.  ``cards`` holds every open card in ``order`` and every
    step above each; a row it does not hold is a closed card, never moved."""
    return _plan(order, cards).moves


def goes_after(order: Sequence[tuple[int, str]], cards: Mapping[int, Card], priority: str,
               first: bool = False) -> tuple[int, str] | None:
    """Where a card goes on the board once the regroup's moves over ``order`` (:func:`moves`)
    have landed: the row it goes just after, a card number and its board item, or None for the
    top.  ``order`` leaves out the card being placed, so neither its place now nor its own move
    decides it; a caller writes the moves over that same ``order`` first.

    Just under the cards ranked ``priority`` or higher; with ``first``, first in ``priority``'s
    group, which is under the rows left alone at the end of the group above (:func:`_under`), as
    the regroup places a row opening its group.  So under the High cards for a new card suggested
    Urgent or High, first in High for the weekly update's card, and under the Low cards, the top
    of the no-Priority group, for a Priority cleared through quill (this module's docstring)."""
    planned = _plan(order, cards)
    rank = RANKS.index(priority)
    above = [number for number in planned.board if number in planned.groups
             and (planned.groups[number] < rank if first else planned.groups[number] <= rank)]
    if not above:
        return None
    after = _under(planned.board, above[-1], planned.groups) if first else above[-1]
    return after, dict(order)[after]
