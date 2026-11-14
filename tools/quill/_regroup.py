"""Where a card goes on the board: the one home of it (rulings plan#4011 to plan#4017, plan#4096).
Nothing here reads GitHub or writes: the caller hands over the board's order, the cards and
quill's pending suggestions on them, and writes what this plans.

**The board's groups** (:data:`_GROUPS`; rulings plan#4016, plan#4096, amending plan#1831 and
plan#4017), top first: Urgent, High, the cards waiting under the High cards, Medium, Low, then
the rest.  A card a Priority ranks takes its group from its rank as ``quill next`` reads it, the
highest Priority at it or at a step above it (the group of :func:`_next.turn`), so a leaf ranks
through its split step and takes its group from it.  A card no Priority ranks takes its group
from quill's pending suggestion on it, which the caller reads for each (:func:`undecided`): one
suggested Urgent or High waits under the High cards; one suggested Medium or Low, or holding no
suggestion, is in the last group.  ``quill next`` ranks a waiting card last until its Priority
is set, so the board shows it ahead of Medium and Low cards ``quill next`` hands out first
(ruling plan#4017's 9B): the board's group of it is not :func:`_next.turn`'s.  Within a group
the developer's drag decides its place (R-BAL177).

**The regroup** (ruling plan#4011, :func:`moves`) makes the board a stable sort by group of the
rows it may move (:func:`movable`): every open step and question on the board that nothing
pauses.  It keeps the longest run of them already in group order where it is, and moves each
other row, so it makes the fewest moves and keeps the developer's order inside each group; a
second pass plans nothing.  Of two runs as long, it keeps the one that leaves more of the rows
it leaves alone between the groups they sat between.  A drag that puts a card in another group
is undone at the next pass, a card no Priority ranks among them (ruling plan#4096).  It never
moves a paused card (On hold at it or at a step above it, ruling plan#4015) or a closed one.

**Where a moved row lands.**  Just under the row before it in that sort, or at the top when none
is.  A row that opens its group goes under the rows the regroup leaves alone that sit directly
under the end of the group above it, so a paused card at the end of its group stays at that
end, at the bottom of the board too (ruling plan#4015: a paused High card stays among the High
rows).  Decided under R-BAL207, with the tie-break above: the ruling leaves a paused card where
it is, and this keeps a card moving past it from landing between it and its group.

**A Priority changed** (ruling plan#4013) needs no rule of its own.  On a board the last pass
grouped, a card whose Priority drops is above every card of its new group, so the sort puts it
first there; one whose Priority rises is below every card of its new group, so it goes last.
Nothing reads which way it moved, nor where it was changed: a Priority cleared, through ``quill
priority --clear`` or on the web, drops the card to the top of the last group, just under the
Low cards (ruling plan#4096), and a card dropped from High to Medium goes under the cards
waiting under the High cards.

**A card filed** goes on the board at the bottom, where GitHub adds it, and the regroup moves it
like any other card: a new top-level step or question suggested Urgent or High to the end of the
cards waiting under the High cards, where it waits until the developer sets its Priority, shown
in the Inbox meanwhile (rulings plan#2800, plan#3231, plan#4017, plan#4096); one suggested
Medium or Low, or nothing, stays at the bottom, in the last group.  A leaf goes where its split
step sat (R-BAL179, :func:`_state.leaf_placement`), and the regroup moves it only if it ranks in
another group.  Only the card the weekly update files, which holds High, is placed apart from
the sort, which would put it last among the High cards: first in the High group, just under the
Urgent cards (:func:`goes_after`; ruling plan#4014, amending plan#3620).

**A place a person picks** (``quill move``, ruling plan#4011, :func:`crossing`) is refused when it
puts its card outside its group: when the nearest row above it the regroup may move is in a later
group, or the nearest below it in an earlier one (:func:`around`), out of the group order a pass
sorts the board into.  On a board a pass has grouped, that is exactly a place the next pass would
undo.  A place between two groups is in either, so a card opening or closing its group is placed
there.  A paused card is placed anywhere, since the regroup never moves one (ruling plan#4015).

A task nests under its step on the board, in no place of this order (ruling plan#1814), and is no
row of it (:meth:`_tracker.Board.order`).
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from functools import partial
from itertools import takewhile
from operator import itemgetter
from typing import NamedTuple

from tools.quill._fields import HIGH, RANKS, URGENT
from tools.quill._next import ranked, turn
from tools.quill._state import held_at
from tools.quill._tracker import Card

#: The group of a card no Priority ranks on which quill's suggestion of Urgent or High is
#: pending (:data:`_WAITS`): it waits just under the High cards (rulings plan#4017, plan#4096).
_WAITING = "waiting"
#: The suggestions that put a card no Priority ranks in :data:`_WAITING`.
_WAITS = (URGENT, HIGH)
#: Where :data:`_WAITING` falls among ``quill next``'s ranks: just after High.
_AFTER_HIGH = RANKS.index(HIGH) + 1
#: The board's groups, top first, a card's group (:func:`_group`) its index here (rulings
#: plan#4016, plan#4096): ``quill next``'s ranks (:data:`_fields.RANKS`) with :data:`_WAITING`
#: just after High, then None, every other card no Priority ranks.
_GROUPS = (*RANKS[:_AFTER_HIGH], _WAITING, *RANKS[_AFTER_HIGH:], None)
#: The name a refusal gives each group of the cards no Priority ranks (:func:`_name`); a
#: Priority's group is named by it.
_NAMES = {_WAITING: "suggested Urgent or High", None: "no Priority"}


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
    """Whether the regroup may move ``card``, a row of the board's order: it is open and nothing
    at it or above it pauses it (:func:`_state.held_at`).  ``cards`` holds every step above
    it."""
    return card.is_open and held_at(card, cards) is None


def undecided(numbers: Iterable[int], cards: Mapping[int, Card]) -> list[int]:
    """The cards of ``numbers`` whose group quill's pending suggestion on them decides
    (:func:`_group`): each the regroup may move (:func:`movable`) that no Priority ranks
    (:func:`_next.ranked`), in the order given.  A caller reads the suggestion of each, and of no
    other card: for :func:`moves`, of the board's rows, and for :func:`crossing`, of the card
    placed and the rows :func:`around` names.  ``cards`` holds every open card of ``numbers`` and
    every step above each."""
    return [number for number in numbers if number in cards
            and movable(cards[number], cards) and not ranked(cards[number], cards)]


def _group(number: int, cards: Mapping[int, Card], places: Mapping[int, int],
           suggested: Mapping[int, str]) -> int:
    """Card ``number``'s group on the board, its index in :data:`_GROUPS`: its rank as ``quill
    next`` reads it (:func:`_next.turn`, ``places`` the board's rows), or, when no Priority
    ranks it, :data:`_WAITING` for a suggestion ``suggested`` holds of Urgent or High and the
    last group for any other or none.  ``cards`` holds the card and every step above it."""
    rank = turn(number, cards, places).group
    if rank < len(RANKS):
        return _GROUPS.index(RANKS[rank])
    return _GROUPS.index(_WAITING if suggested.get(number) in _WAITS else None)


def _name(group: int) -> str:
    """The name of ``group``, an index in :data:`_GROUPS`, as a refusal says it: the Priority's
    own, or for the cards no Priority ranks, :data:`_NAMES`'."""
    value = _GROUPS[group]
    return _NAMES.get(value, value)


def _spots(order: Sequence[tuple[int, str]],
           groups: Mapping[int, int]) -> Iterator[tuple[int, int]]:
    """Each row of ``order`` that :func:`_kept`'s run may hold, in the board's order, with its
    spot: twice its group for a row the regroup may move (one of ``groups``); for a row it leaves
    alone between two such rows in group order, twice the group above it, and one more when the
    group below it is a later one, since it sits at the end of the group above (as a paused card
    at the end of its group does, ruling plan#4015); and for one below the last such row, twice
    that row's group and one more, the end of that group, where :func:`_under` keeps it.  A row
    left alone above the first row it may move, or between two out of group order, is passed:
    it says nothing of where it sits."""
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
    if above is not None:
        yield from ((row, 2 * above + 1) for row in alone)


def _kept(order: Sequence[tuple[int, str]], groups: Mapping[int, int]) -> set[int]:
    """The rows the regroup may move (``groups``) that stay where they are: a longest run of them
    already in group order, so it moves the fewest, and of such runs the one keeping the most
    rows it leaves alone in their spots (:func:`_spots`), such as a paused card at the end of its
    group.  A run is rows whose spots never fall, each row it may move outweighing every row
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
    regroup leaves alone directly under ``row`` (any not of ``groups``, the rows it may move), so
    they stay at the end of ``row``'s group; else ``row`` itself."""
    run = list(takewhile(lambda number: number not in groups, board[board.index(row) + 1:]))
    return run[-1] if run else row


def _plan(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
          suggested: Mapping[int, str]) -> _Pass:
    """The pass over ``order``, the board's order (``(card number, board item)``, top first),
    with ``cards`` holding every open card in it and every step above each, and ``suggested``
    what quill's pending suggestion on each card of ``order`` no Priority ranks suggests
    (:func:`undecided`), a card holding none absent; a row ``cards`` does not hold is a closed
    card, which the regroup never moves.  Each row moved goes after the row before it in the
    sort, or under the rows left alone there when it opens its group (:func:`_under`), read off
    the board as the moves before it leave it."""
    items = dict(order)
    places = {number: place for place, (number, _) in enumerate(order)}
    groups = {number: _group(number, cards, places, suggested) for number in places
              if number in cards and movable(cards[number], cards)}
    kept = _kept(order, groups)
    target = sorted(groups, key=groups.__getitem__)
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


def moves(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
          suggested: Mapping[int, str]) -> list[Move]:
    """The regroup's moves over ``order``, the board's order (``(card number, board item)``, top
    first), in the order they are written, each planned on the board as the moves before it leave
    it.  A pass keeps nothing for the next, which plans from the board as it finds it, so a pass
    cut short is finished by the next.  ``cards`` holds every open card in ``order`` and every
    step above each; a row it does not hold is a closed card, never moved.  ``suggested`` holds
    what quill's pending suggestion on each of :func:`undecided`'s cards suggests, a card holding
    none absent."""
    return _plan(order, cards, suggested).moves


class Crossing(NamedTuple):
    """A place outside its card's group (:func:`crossing`): the card's ``group``, and the rows
    :func:`around` names, ``above`` and ``below``, each a card number and its group, or None for
    a side holding none; each group by its name (:func:`_name`)."""

    group: str
    above: tuple[int, str] | None
    below: tuple[int, str] | None


def around(order: Sequence[tuple[int, str]], cards: Mapping[int, Card], number: int,
           after: int | None) -> tuple[int | None, int | None]:
    """The rows the regroup may move (:func:`movable`) nearest above and below card ``number``
    placed just after row ``after`` of ``order``, the board's order (``(card number, board
    item)``, top first), or at the top when None; None for a side holding none.  The card is
    no row of either side, wherever it sits now, so ``after`` naming the card itself keeps it in
    its row.  ``cards`` holds every open card in ``order`` and every step above each."""
    rows = [row for row, _ in order]
    cut = 0 if after is None else rows.index(after) + 1

    def nearest(side: Iterable[int]) -> int | None:
        return next((row for row in side if row != number and row in cards
                     and movable(cards[row], cards)), None)

    return nearest(reversed(rows[:cut])), nearest(rows[cut:])


def crossing(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
             suggested: Mapping[int, str], number: int,
             near: tuple[int | None, int | None]) -> Crossing | None:
    """How card ``number``, placed between ``near``, the rows :func:`around` names over
    ``order``, sits outside its group (ruling plan#4011): the row above it in a later group or the
    one below it in an earlier one (:func:`_group`), out of the group order a pass sorts the board
    into; None when it sits in its group, or when the regroup never moves it, a paused card
    (:func:`movable`, ruling plan#4015).  ``cards`` holds the card, every open card in ``order``
    and every step above each, and ``suggested`` what quill's pending suggestion on each of them
    :func:`undecided` names suggests, a card holding none absent."""
    if not movable(cards[number], cards):
        return None
    places = {row: place for place, (row, _) in enumerate(order)}
    group = partial(_group, cards=cards, places=places, suggested=suggested)
    above, below = near
    mine = group(number)
    if (above is None or group(above) <= mine) and (below is None or mine <= group(below)):
        return None
    return Crossing(_name(mine),
                    *(None if row is None else (row, _name(group(row))) for row in near))


def goes_after(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
               suggested: Mapping[int, str], priority: str) -> tuple[int, str] | None:
    """Where a card goes to be first in ``priority``'s group once the regroup's moves over
    ``order`` (:func:`moves`, with ``cards`` and ``suggested``) have landed: the row it goes just
    after, a card number and its board item, or None for the top.  ``order`` leaves out the card
    being placed, so neither its place now nor its own move decides it; a caller writes the moves
    over that same ``order`` first.

    Under the rows of every group above ``priority``'s and the rows left alone at the end of the
    last of them (:func:`_under`), as the regroup places a row opening its group: first in High
    for the weekly update's card (ruling plan#4014, this module's docstring)."""
    planned = _plan(order, cards, suggested)
    group = _GROUPS.index(priority)
    above = [number for number in planned.board
             if number in planned.groups and planned.groups[number] < group]
    if not above:
        return None
    after = _under(planned.board, above[-1], planned.groups)
    return after, dict(order)[after]
