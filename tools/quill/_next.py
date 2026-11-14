"""``quill next``'s answer, decided from the cards, the board, the claims and git, as
:mod:`_state`'s decisions are: the step it hands out, and the steps that wait on a pause
(plan#1820, rulings plan#1830, plan#1831, plan#1832, plan#4016).

**A step's turn** (:func:`turn`).  A step's mark, its Priority, decides its group, and the board's
drag order its place in the group (R-BAL177): Urgent, then High, then Medium, then Low, then the
steps holding none (:data:`_fields.RANKS`, ruling plan#4016, amending plan#1831, under which
Medium, Low and no mark were alike).  A leaf holds the highest mark of itself and the steps above
it (:func:`_rank`), as it inherits their waits (R-BAL182), so a mark outlives its step's split and
nothing is copied.  Only a step that :func:`_state.has_turn` takes one.  A step a session has
claimed keeps its turn, since a step's prerequisites go just ahead of it whoever holds it: it is
never handed out itself, but what it needs is, and it is named while it waits on a pause.

**A blocked step's turn hands out what it needs**: the first unfinished step it needs, and so on
down, whatever that step's own mark (:func:`_needed`).  So the board A #1, B #2, C #3, D #20 where
C needs D hands out A, B, D, C, and with C marked High, D, C, A, B (ruling plan#1831's example).
What cannot be handed out is passed over for the next thing the step needs: a step another session
claims, whose own needs are still walked, or an open question, which only the developer's answer
closes.  With nothing to hand out for it, the next step takes its turn.

**A pause is never pulled through.**  A step On hold has no turn and is never handed out, and a
leaf inherits a pause from a step above it (:func:`_state.held_at`).  A step that needs one waits
(ruling plan#1830: "waits, and the tool says so"): it is named, with what it waits on
(:class:`Waiting`).  Nothing else it needs is pulled ahead, each of those keeping its own turn
(ruling plan#2823): the developer has chosen that the step not go on yet, so what it needs gains
no urgency from it, and it moves up at once when the mark is lifted.

``--arc`` hands out the first step of that arc in the same walk, so it never hands out another
arc's step, and a step needing one simply passes.  Split out of :mod:`_state`, which On hold would
take past the 1,000 lines ``too-many-lines`` allows.

**A piece nothing ranks is named** (ruling plan#3304): a leaf takes no suggested Priority, and
the Inbox lists none (ruling plan#3229), so one holding no Priority at it or above (:func:`ranked`)
would be seen nowhere; :func:`unranked_lines` names each on the board, and the step to give one.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import partial
from typing import NamedTuple

from tools.quill._command import _label
from tools.quill._fields import RANKS
from tools.quill._git import Claim
from tools.quill._state import (
    SHIPPABLE,
    above,
    at_and_above,
    blockers,
    has_turn,
    held_at,
    is_container,
    is_work,
    leaves,
    never_offered,
    resolved,
    workable,
)
from tools.quill._tracker import BareCard, Card


class Turn(NamedTuple):
    """Where a card's turn falls (:func:`turn`), compared as a tuple: its ``group``, its
    ``place`` on the board, then its ``number``."""

    group: int
    place: int
    number: int


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


def ranked(card: BareCard, cards: Mapping[int, BareCard]) -> bool:
    """Whether a Priority ranks ``card``: one held by it or by a step above it
    (:func:`_state.at_and_above`, the walk :func:`_rank` reads), On hold among them.  The one
    spelling of it: no leaf is filed under a step it does not hold for (ruling plan#3229,
    ``_filing._refuse_unranked``), no clear that would leave a piece without one is taken
    (``_arc_label._unranked_refusal``), ``quill next`` names each piece on the board without
    one (:func:`unranked_lines`; ruling plan#3304), and the regroup groups each card without
    one by quill's pending suggestion on it (:func:`_regroup.undecided`, ruling plan#4096)."""
    return any(step.priority is not None for step in at_and_above(card, cards))


def is_piece(card: Card, cards: Mapping[int, Card], shipped: Iterable[int]) -> bool:
    """Whether ``card`` is a piece of a split step still to build: an open step linked under
    another and split into none (:func:`_state.is_work`), not yet done with
    (:func:`_state.resolved`).  It takes no suggested Priority and the Inbox lists it not
    (ruling plan#3229): the steps above it rank it, unless it holds one of its own."""
    return (card.kind == "step" and card.parent is not None and card.is_open
            and is_work(card, cards, shipped) and not resolved(card.number, cards, shipped))


def unranked_lines(order: Iterable[int], cards: Mapping[int, Card],
                   shipped: Iterable[int]) -> list[str]:
    """A line for each piece on the board (:func:`is_piece`), in the board's order, that no
    Priority ranks (:func:`ranked`), naming the step at the top of its chain to give one (ruling
    plan#3304), so a Priority cleared on the web is seen too.  ``cards`` holds every step above
    each (``quill next``'s read, :func:`_command.with_closure`)."""
    shipped = set(shipped)
    lines = []
    for number in order:
        card = cards.get(number)
        if card is None or not is_piece(card, cards, shipped) or ranked(card, cards):
            continue
        top = list(above(card, cards))[-1].number
        lines.append(f"{_label(card)} holds no Priority, nor does any step above it, and the "
                     f"Inbox lists no piece of a split step (ruling plan#3304): give plan#{top} "
                     f"one (`quill priority plan#{top} --to PRIORITY`)")
    return lines


def _rank(card: Card, cards: Mapping[int, Card]) -> int:
    """``card``'s group, where its mark puts it among :data:`_fields.RANKS`: the highest of its
    own and each step above it (:func:`_state.at_and_above`), and past them all with none (On
    hold ranks nothing; it pauses)."""
    marks = {step.priority for step in at_and_above(card, cards)}
    return next((rank for rank, mark in enumerate(RANKS) if mark in marks), len(RANKS))


def turn(number: int, cards: Mapping[int, Card], places: Mapping[int, int]) -> Turn:
    """Card ``number``'s turn, the one key ``quill next`` takes cards in (ruling plan#4016): its
    group (:func:`_rank`), which the regroup's group of a card on the board reads, splitting
    the last by quill's pending suggestion (:mod:`_regroup`, rulings plan#4011, plan#4096);
    then its row on the board, ``places`` mapping each card on it to its row, top first, and a
    card off it coming after every row; then its number, which orders the cards off the board.
    ``cards`` holds the card and every step above it."""
    return Turn(_rank(cards[number], cards), places.get(number, len(places)), number)


def _needs(card: Card, cards: Mapping[int, Card], shipped: set[int]) -> set[int]:
    """What ``card`` needs and is not yet resolved: its :func:`_state.blockers`, and a split step's
    leaves, by which :func:`_state.resolved` reads it."""
    needs = blockers(card, cards)
    if is_container(card, cards, shipped):
        needs.update(leaves(card, cards, shipped))
    return {number for number in needs if not resolved(number, cards, shipped)}


def _needed(start: Card, cards: Mapping[int, Card], shipped: set[int],
            key: Callable[[int], Turn]) -> Iterator[Card]:
    """``start``, then every unfinished card it needs, depth first, each once however many need
    it (so two cards blocking each other end the walk), the needs of each in ``key``'s order,
    each card's :func:`turn`.

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
            wanted += [cards[number] for number in sorted(_needs(card, cards, shipped), key=key,
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
    key = partial(turn, cards=cards, places=places)

    def in_arc(card: Card) -> bool:
        return arc is None or card.arc == arc

    def fits(card: Card) -> bool:
        return workable(card, cards, shipped, claims) and in_arc(card)

    first, waiting = None, []
    for number in sorted((number for number in places if number in cards), key=key):
        if not has_turn(cards[number], cards, shipped):
            continue
        needed = list(_needed(cards[number], cards, shipped, key))
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
