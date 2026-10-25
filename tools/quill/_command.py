"""What every ``quill`` command shares: its refusal, its reads of the tracker, and how it
names a card, on its command line (:func:`card_number`) and in what it prints
(:func:`_label`).  What a command reads of git is :func:`_git.read`, once.

Split out of the command module at X-cx L2's leaf C, whose change would otherwise take
that module past the 1,000 lines ``too-many-lines`` allows (it has been ``quill.py``
since the tool's rename), so that ``_filing`` (the ``file`` command), ``_show`` (the
``show`` command) and ``quill`` (every other command, and the command line) read these
from one place.
"""
from __future__ import annotations

import argparse
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol

from tools.ci.arcs import ARCS
from tools.quill._state import filing_unfinished, missing
from tools.quill._tracker import BareCard, Card, Tracker, TrackerError


#: The kinds of card the board holds, in the developer's order (R-BAL177).
ON_BOARD = ("step", "question")
_CARD_REF = re.compile(r"(?:plan#|#)?([1-9][0-9]*)")


class Refused(Exception):
    """A command that will not run as asked; its message says why."""


def card_number(text: str) -> int:
    """``plan#7``, ``#7`` or ``7`` -> 7."""
    found = _CARD_REF.fullmatch(text.strip())
    if not found:
        raise argparse.ArgumentTypeError(f"{text!r} is not a card: write plan#<number>")
    return int(found.group(1))


class _ReadsCards[CardT: BareCard](Protocol):  # pylint: disable=too-few-public-methods
    """A reader of cards by number, each of the type it reads: a :class:`_tracker.Tracker`
    reads :class:`_tracker.Card`\\ s, a :class:`_tracker.CardReader` :class:`BareCard`\\ s.

    Pylint: too-few-public-methods (1/2) -- **a protocol names the one method its
    readers share**, which is all :func:`with_closure` asks of one."""

    def cards(self, numbers: Iterable[int]) -> Mapping[int, CardT]:
        """The cards numbered ``numbers`` that exist."""


def with_cards[CardT: BareCard](tracker: _ReadsCards[CardT], cards: dict[int, CardT],
                                wanted: Iterable[int]) -> dict[int, CardT]:
    """``cards`` plus each card numbered in ``wanted`` that it lacks, read by its number, of
    the type ``tracker`` reads; no read when it lacks none.

    Raises:
        TrackerError: when one of those does not exist.
    """
    if lacked := set(wanted) - set(cards):
        found = tracker.cards(lacked)
        if absent := lacked - set(found):
            raise TrackerError(f"cards {sorted(absent)} are named by others but do not exist")
        cards.update(found)
    return cards


def with_closure[CardT: BareCard](tracker: _ReadsCards[CardT],
                                  cards: dict[int, CardT]) -> dict[int, CardT]:
    """``cards`` plus every card a decision over them reads (blockers, parents, children),
    each of the type ``tracker`` reads (:func:`with_cards`)."""
    while wanted := missing(cards):
        with_cards(tracker, cards, wanted)
    return cards


def _one(tracker: Tracker, number: int) -> Card:
    """One card; :class:`Refused` when it does not exist."""
    found = tracker.cards([number])
    if number not in found:
        raise Refused(f"plan#{number} does not exist")
    return found[number]


def _read(path: str) -> str:
    """A file's text (a spec, a question, an answer, a card's new text, a comment)."""
    return Path(path).read_text(encoding="utf-8")


def _label(card: Card) -> str:
    """``plan#7 [step, balance] title``."""
    arcs = ", ".join(label for label in card.labels if label in ARCS) or "no arc"
    return f"plan#{card.number} [{card.kind or 'no type'}, {arcs}] {card.title}"


def _outside_parent(card: Card) -> str | None:
    """The issue outside the tracker that ``card`` is a sub-issue of, if any."""
    return next((link.issue for link in card.outside if link.what == "parent"), None)


def _unfinished(tracker: Tracker) -> dict[int, Card]:
    """Every card whose filing has not finished (:func:`_state.filing_unfinished`), open or
    a closed ruling, by number."""
    return {number: card for number, card in tracker.marked().items()
            if filing_unfinished(card)}
