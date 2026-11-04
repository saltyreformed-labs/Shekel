"""``quill next``'s answer, decided from the cards, the board, the claims and git, as
:mod:`_state`'s decisions are: the step it hands out, and the steps that wait on a pause
(plan#1820, rulings plan#1830, plan#1831, plan#1832).

**A step's turn.**  The board's drag order is the build order (R-BAL177), and a mark moves a step
ahead of it: Urgent, then High, then every other step in the board's order; Medium, Low and no
mark are alike (:func:`_rank`).  A leaf holds the highest mark of itself and the steps above it,
as it inherits their waits (R-BAL182), so a mark outlives its step's split and nothing is copied.
Only a step that :func:`_state.has_turn` takes one.  A step a session has claimed keeps its turn,
since a step's prerequisites go just ahead of it whoever holds it: it is never handed out itself,
but what it needs is, and it is named while it waits on a pause.

**A blocked step's turn hands out what it needs**: the first unfinished step it needs, and so on
down, whatever that step's own mark (:func:`_needed`).  So the board A #1, B #2, C #3, D #20 where
C needs D hands out A, B, D, C, and with C marked High, D, C, A, B (ruling plan#1831's example).
What cannot be handed out is passed over for the next thing the step needs: a step another session
claims, whose own needs are still walked, or an open question, which only the developer's answer
closes.  With nothing to hand out for it, the next step takes its turn.

**A pause is never pulled through.**  A step On hold has no turn and is never handed out, and a
leaf inherits a pause from a step above it (:func:`_state.held_at`).  A step that needs one waits
(ruling plan#1830: "waits, and the tool says so"): it is named, with what it waits on
(:class:`Waiting`).  Nothing else it needs is pulled ahead, each of those keeping its own turn: the
developer has chosen that the step not go on yet, so what it needs gains no urgency from it.

``--arc`` hands out the first step of that arc in the same walk, so it never hands out another
arc's step, and a step needing one simply passes.  Split out of :mod:`_state`, which On hold would
take past the 1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass

from tools.quill._fields import HIGH, URGENT
from tools.quill._git import Claim
from tools.quill._state import (
    SHIPPABLE,
    above,
    blockers,
    has_turn,
    held_at,
    is_container,
    leaves,
    never_offered,
    resolved,
    workable,
)
from tools.quill._tracker import BareCard, Card

#: The marks that move a step ahead of the board's order, the first of them first (ruling
#: plan#1831).
_AHEAD = (URGENT, HIGH)


@dataclass(frozen=True)
class Waiting:
    """A step whose turn passes because it needs a card On hold (rulings plan#1830, plan#1832):
    ``step``, and ``on``, each card On hold its walk reaches (:func:`_state.held_at`: a step above
    the card it needs, when the pause is set there)."""

    step: Card
    on: tuple[BareCard, ...]


@dataclass(frozen=True)
class NextAnswer:
    """The step handed out, what the board's order cannot place, and what waits on a pause."""

    card: Card | None
    unplaced: tuple[Card, ...]
    waiting: tuple[Waiting, ...]


def _rank(card: Card, cards: Mapping[int, Card]) -> int:
    """Where ``card``'s mark puts it among :data:`_AHEAD`: the highest of its own and each step
    above it, and past them all with neither."""
    marks = {step.priority for step in (card, *above(card, cards))}
    return next((rank for rank, mark in enumerate(_AHEAD) if mark in marks), len(_AHEAD))


def _needs(card: Card, cards: Mapping[int, Card], shipped: set[int]) -> set[int]:
    """What ``card`` needs and is not yet resolved: its :func:`_state.blockers`, and a split step's
    leaves, by which :func:`_state.resolved` reads it."""
    needs = blockers(card, cards)
    if is_container(card, cards, shipped):
        needs.update(leaves(card, cards, shipped))
    return {number for number in needs if not resolved(number, cards, shipped)}


def _needed(start: Card, cards: Mapping[int, Card], shipped: set[int],
            turn: Callable[[int], tuple]) -> Iterator[Card]:
    """``start``, then every unfinished card it needs, depth first, each once however many need
    it (so two cards blocking each other end the walk), the needs of each in ``turn``'s order.

    The walk stops at a card no work can start on (:func:`_state.never_offered`, On hold among
    them), and at any card that is neither a step nor a finding.  A finding is walked: the one
    card it is blocked by is its owner, whose pull request owes it (R-BAL395, plan#1777).  A
    question is not: what it is blocked by is the step it was asked for, which it does not wait
    on."""
    seen, wanted = set(), [start]
    while wanted:
        card = wanted.pop()
        if card.number in seen:
            continue
        seen.add(card.number)
        yield card
        if card.kind in SHIPPABLE and never_offered(card, cards, shipped) is None:
            wanted += [cards[number] for number in sorted(_needs(card, cards, shipped), key=turn,
                                                          reverse=True)]


def _held(needed: Iterable[Card], cards: Mapping[int, Card]) -> tuple[BareCard, ...]:
    """Each card On hold that holds one of ``needed`` (:func:`_state.held_at`), named once."""
    return tuple({hold.number: hold for need in needed
                  if (hold := held_at(need, cards))}.values())


def next_step(order: Iterable[int], cards: Mapping[int, Card], shipped: Iterable[int],
              claims: Mapping[int, Claim], arc: str | None = None) -> NextAnswer:
    """The step to hand out, with ``order`` the board's card numbers, top first.

    A workable step that is NOT on the board -- filed by hand on the web, or just filed and not
    yet shown by a lagging board -- has no place in the order, so it takes no turn of its own.  A
    step that needs it hands it out all the same, and either way it is never skipped silently: it
    is returned in ``unplaced`` for the caller to say so.
    """
    shipped = set(shipped)
    places = {number: place for place, number in enumerate(order)}

    def turn(number: int) -> tuple[int, int, int]:
        return _rank(cards[number], cards), places.get(number, len(places)), number

    def in_arc(card: Card) -> bool:
        return arc is None or card.arc == arc

    def fits(card: Card) -> bool:
        return workable(card, cards, shipped, claims) and in_arc(card)

    first, waiting = None, []
    for number in sorted((number for number in places if number in cards), key=turn):
        if not has_turn(cards[number], cards, shipped):
            continue
        needed = list(_needed(cards[number], cards, shipped, turn))
        if held := _held(needed, cards):
            if in_arc(cards[number]):
                waiting.append(Waiting(cards[number], held))
        elif first is None:
            first = next((need for need in needed if fits(need)), None)
    unplaced = tuple(sorted(
        (card for card in cards.values() if card.number not in places and fits(card)),
        key=lambda card: card.number,
    ))
    return NextAnswer(first, unplaced, tuple(waiting))
