"""``quill next``'s answer, decided from the cards, the board, the claims and git, as
:mod:`_state`'s decisions are.  Split out of :mod:`_state`, which On hold (ruling plan#1832) would
take past the 1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tools.quill._git import Claim
from tools.quill._state import workable
from tools.quill._tracker import Card


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
        return workable(card, cards, shipped, claims) and (arc is None or card.arc == arc)

    first = next((cards[n] for n in order if n in cards and fits(cards[n])), None)
    placed = set(order)
    unplaced = tuple(sorted(
        (card for card in cards.values() if card.number not in placed and fits(card)),
        key=lambda card: card.number,
    ))
    return NextAnswer(first, unplaced)
