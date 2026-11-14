"""The regroup's writes (rulings plan#4011, plan#4012, plan#4013): the moves :mod:`_regroup`
plans over the board, sent to it.  :mod:`_regroup` reads and writes nothing; this reads the
board's order and its cards, and writes the moves.

**Where it runs** (ruling plan#4012): last in ``quill sync``, every three hours, which is where a
Priority set on the web is seen, over the cards ``sync`` already reads (:func:`sync_pass`); in
``quill priority``, after it sets or clears a card's Priority, at once (:func:`priority_pass`);
and in ``quill file``, after it puts a step or question on the board, before its mark comes off
(:func:`filing_pass`).  Each is a whole pass over the board, so any finishes a pass a failure cut
short, the plan being stateless.

**How it writes** (:func:`_send`).  Each move is sent back to back and printed as it lands, then
the board is read back once, until it shows every move where it was sent
(:meth:`_tracker.Board.shows`).  Measured 2026-11-14 04:10 UTC on the tracker, on seven closed
cards in a row: six moves sent back to back, each just after the row the move before it moved,
as the regroup's are, showed as sent at the first read back, 13 s later, and the six that put
them back did too; each move took about 2 s.  ``Board.place`` reads the whole board back after
each move instead, every item it holds (15 s), so 48 moves would take over 10 minutes.  A board
that does not show every move yet is said so, and the next pass finishes what did not land.  The
tracker's first pass, planned 2026-11-14 04:08 UTC: 10 moves (48 on 2026-11-13).

**What ``quill priority`` and ``quill file`` read** (:func:`board_cards`): each row of the board's
order by its number, and every step above each, which is all a plan reads of a card
(``_regroup.movable``, ``_next.turn``).  Measured 2026-11-14 04:06-04:08 UTC on the tracker: 48 s
for the order's 590 rows and the 40 steps above them, where every open card and every card a
decision over them reads (``quill next``'s read) took 72 s for 1,167 cards; the two gave the same
group to every card and the same 10 moves.  The board's order read before it took 15 s.

**A clear through quill** (ruling plan#4013, applied under R-BAL207): a card ``quill priority
--clear`` leaves ranked by no Priority, at it or at a step above it, goes to the top of the
no-Priority group, just under the Low cards (``_regroup.goes_after`` with ``LOW``); one a step
above it still ranks is the regroup's like any other card.  That is the command's end state, so
the same command run again finishes a run a failure cut short, and a clear of a card already
holding none puts it there too.  A Priority cleared on the web moves nothing: the regroup leaves
an undecided card alone (ruling plan#4017).

**A card filed** (rulings plan#4014, plan#4017): the card the weekly update files goes first in the
High group, and a new one no Priority ranks that is suggested Urgent or High goes just under the
High cards, each left out of the order the moves are planned over and placed after them, as a
clear is; any other is the regroup's like every card, a leaf in its split step's place
(R-BAL179) and a card suggested Medium or Low, or nothing, at the bottom, where GitHub adds it
(``_regroup``'s docstring holds the rules).  The filing's mark comes off after the pass, so the
same command run again places the card again (R-BAL202), and moves it only if it is not there.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import NamedTuple

from tools.quill._command import _label, with_cards
from tools.quill._draft import Suggestion
from tools.quill._fields import HIGH, LOW, URGENT
from tools.quill._next import ranked
from tools.quill._regroup import Move, goes_after, moves
from tools.quill._state import SyncPlan
from tools.quill._tracker import Card, Tracker
from tools.quill.setup_tracker import IN_ORDER


def board_cards(tracker: Tracker, numbers: Iterable[int]) -> dict[int, Card]:
    """The cards numbered ``numbers``, each read by its number, and every step above each
    (:func:`_state.above`): all a plan over the board reads of a card, for ``numbers`` the rows
    of its order (this module's docstring).  A number naming no card is absent, as a closed card
    the caller does not hold is to a plan, which never moves one.

    Raises:
        TrackerError: when a step above one of them does not exist (:func:`_command.with_cards`).
    """
    cards = tracker.cards(numbers)
    while wanted := {card.parent for card in cards.values()} - {None, *cards}:
        with_cards(tracker, cards, wanted)
    return cards


#: The suggestions a new card no Priority ranks waits just under the High cards for until its
#: Priority is set (ruling plan#4017); one suggested Medium or Low, or nothing, waits at the
#: bottom.
_UNDER_HIGH = (URGENT, HIGH)


class _Placed(NamedTuple):
    """Card ``number``, board item ``item``, left out of the order a pass is planned over, and
    where it goes once the pass's moves land (:func:`_regroup.goes_after`): just under the cards
    ranked ``priority`` or higher, or with ``first`` first in ``priority``'s group; ``ruling`` is
    the ruling its move is made for."""

    number: int
    item: str
    priority: str
    first: bool
    ruling: str


def _send(tracker: Tracker, sent: Sequence[tuple[Move, str]], cards: Mapping[int, Card],
          dry_run: bool, prefix: str = "") -> None:
    """Send each move of ``sent`` to the board, back to back (:meth:`_tracker.Board.move`), each
    printed as it lands with the ruling it is made for, then read the board back once, saying so
    when it does not show every move where it was sent yet; under ``dry_run``, each printed as
    what would be written, none sent and nothing read back.  Each line opens with ``prefix``, a
    filing's indent under its card.  ``cards`` holds each card moved."""
    for move, ruling in sent:
        if not dry_run:
            tracker.board.move(move.item, move.after and move.after[1])
        where = "to the top" if move.after is None else f"after plan#{move.after[0]}"
        print(f"{prefix}{'would move' if dry_run else 'moved'} on the board {where} (ruling "
              f"{ruling}): {_label(cards[move.number])}")
    if sent and not dry_run and not tracker.board.shows(
            [(move.item, move.after and move.after[1]) for move, _ in sent]):
        print(f"{prefix}  the board does not show every move yet (it lags a write): the next "
              "regroup moves each that did not land")


def _planned(order: Sequence[tuple[int, str]],
             cards: Mapping[int, Card]) -> list[tuple[Move, str]]:
    """The regroup's moves over ``order`` (:func:`_regroup.moves`), each with its ruling."""
    return [(move, "plan#4011") for move in moves(order, cards)]


def sync_pass(tracker: Tracker, cards: Mapping[int, Card], changes: SyncPlan,
              dry_run: bool) -> None:
    """``quill sync``'s regroup, the last of its passes (ruling plan#4012): the moves over the
    board's order read now, ``cards`` holding each card ``sync`` read, every open one and every
    step above each, with each its state writes (``changes``) closed or reopened read as they
    leave it, so no card it has just closed is moved; under ``dry_run``, printed as what would be
    written and none sent (:func:`_send`)."""
    states = {**dict.fromkeys(changes.reopen, True),
              **dict.fromkeys((*changes.close, *changes.drop), False)}
    shown = {number: replace(card, is_open=states[number]) if number in states else card
             for number, card in cards.items()}
    _send(tracker, _planned(tracker.board.order(), shown), shown, dry_run)


def _cleared_out(card: Card, cards: Mapping[int, Card]) -> bool:
    """Whether a clear of ``card``'s Priority, which ``cards`` holds it cleared of, places it at
    the top of the no-Priority group (this module's docstring): it is open, a row of the board's
    order (a step or question on the board), and no Priority ranks it (:func:`_next.ranked`)."""
    return (card.is_open and card.kind in IN_ORDER and card.board_item is not None
            and not ranked(cards[card.number], cards))


def _placing(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
             placed: _Placed) -> list[tuple[Move, str]]:
    """The regroup's moves over ``order``, the board's order, with ``placed``'s card left out of
    it, so neither its place now nor its own move decides them, each with its ruling; then the
    card's move to where ``placed`` says on the board those moves leave, after them, unless
    nothing else moves and it sits there already.  ``cards`` holds every card of ``order``, every
    step above each, and ``placed``'s card."""
    rows = [row for row in order if row[0] != placed.number]
    sent, after = _planned(rows, cards), goes_after(rows, cards, placed.priority, placed.first)
    numbers = [row for row, _ in order]
    before = dict(zip(numbers, [None, *numbers]))
    if sent or placed.number not in before or before[placed.number] != (after and after[0]):
        sent.append((Move(placed.number, placed.item, after), placed.ruling))
    return sent


def priority_pass(tracker: Tracker, card: Card, priority: str | None) -> None:
    """``quill priority``'s regroup once ``card`` holds ``priority`` (None for none; ruling
    plan#4012), over the board's order and its cards read now (:func:`board_cards`), ``card``
    read as holding it whatever a read just after its write shows.  A clear that leaves it ranked
    by nothing (:func:`_cleared_out`) puts it at the top of the no-Priority group, placed after
    the moves over the rest of the board (:func:`_placing`)."""
    order = tracker.board.order()
    cards = board_cards(tracker, [*(number for number, _ in order), card.number])
    cards[card.number] = replace(cards[card.number], priority=priority)
    if priority is not None or not _cleared_out(card, cards):
        _send(tracker, _planned(order, cards), cards, False)
        return
    _send(tracker, _placing(order, cards, _Placed(card.number, card.board_item, LOW, False,
                                                  "plan#4013")), cards, False)


def filing_pass(tracker: Tracker, card: Card, item: str, suggestion: Suggestion | None,
                first: bool) -> None:
    """``quill file``'s regroup (ruling plan#4012) once ``card``, a step or question it files, is
    on the board as ``item``, over the board's order and its cards read now
    (:func:`board_cards`), each line indented under the filing's.  With ``first``, the weekly
    update's card, ``card`` goes first in the High group (ruling plan#4014); one no Priority ranks
    (:func:`_next.ranked`) whose ``suggestion`` is Urgent or High goes just under the High cards
    (ruling plan#4017); each is placed after the moves over the rest of the board
    (:func:`_placing`).  Any other is the regroup's (this module's docstring)."""
    order = tracker.board.order()
    cards = board_cards(tracker, [*(number for number, _ in order), card.number])
    if first:
        sent = _placing(order, cards, _Placed(card.number, item, HIGH, True, "plan#4014"))
    elif (suggestion is not None and suggestion.priority in _UNDER_HIGH
          and not ranked(cards[card.number], cards)):
        sent = _placing(order, cards, _Placed(card.number, item, HIGH, False, "plan#4017"))
    else:
        sent = _planned(order, cards)
    _send(tracker, sent, cards, False, "  ")
