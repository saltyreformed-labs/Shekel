"""The regroup's writes (rulings plan#4011, plan#4012, plan#4096): the moves :mod:`_regroup` plans
over the board, sent to it.  :mod:`_regroup` reads and writes nothing; this reads the board's
order, its cards and quill's pending suggestions on them, and writes the moves.

**Where it runs** (ruling plan#4012): last in ``quill sync``, every three hours, which is where a
Priority set or cleared on the web is seen, over the cards ``sync`` already reads
(:func:`sync_pass`); in ``quill priority``, after it sets or clears a card's Priority, at once
(:func:`priority_pass`); and in ``quill file``, after it puts a step or question on the board,
before its mark comes off (:func:`filing_pass`).  Each is a whole pass over the board, so any
finishes a pass a failure cut short, the plan being stateless.

**How it writes** (:func:`_send`).  Each move is sent back to back and printed as it lands, then
the board is read back once, until it shows every row moved where the moves together leave it
(:func:`_regroup.lands`, :meth:`_tracker.Board.shows`): not each just after the row it was sent
after, since a later move sent after the same row lands between them.  Measured 2026-11-14 04:10
UTC on the tracker, on seven closed cards in a row: six moves sent back to back, each just after
the row the move before it moved, showed as sent at the first read back, 13 s later, and the six
that put them back did too; each move took about 2 s.  Not measured: moves sent back to back
after one row, as a group's rows too high are (``_regroup``'s docstring), or after a row no move
before them moved; the read back is what sees one land otherwise.  ``Board.place`` reads the
whole board back after each move instead, every item it holds (15 s), so 48 moves would take
over 10 minutes.  A board that does not show every move yet is said so, and the next pass
finishes what did not land, each group's order kept by every move that did.  The tracker's first
pass, planned 2026-11-14 04:08 UTC: 10 moves (48 on 2026-11-13).

**What ``quill priority`` and ``quill file`` read** (:func:`board_cards`): each row of the board's
order by its number, and every step above each, which is all a plan reads of a card
(``_regroup.movable``, ``_next.turn``).  Measured 2026-11-14 04:06-04:08 UTC on the tracker: 48 s
for the order's 590 rows and the 40 steps above them, where every open card and every card a
decision over them reads (``quill next``'s read) took 72 s for 1,167 cards; the two gave the same
group to every card and the same 10 moves.  The board's order read before it took 15 s.

**What every pass reads besides** (:func:`pending_suggestions`): quill's pending suggestion on
each card the plan takes its group from one (``_regroup.undecided``: open, unpaused and ranked by
no Priority), read card by card (:meth:`_suggestion.Suggestions.pending`), and on no other card,
so a pass costs a read for each card waiting on the developer's decision and none for the rest.
Measured 2026-11-14 12:12 UTC on the tracker: 104 such cards read in 30 s, about 0.29 s each.
``quill move``'s refusal of a place in another group reads the same, of the card and the two rows
it would sit between alone (:mod:`_move`).

**A card filed** (ruling plan#4096): the regroup's like every card, a step or question added at
the bottom, where GitHub adds it, and a leaf in its split step's place (R-BAL179); the plan
reads its pending suggestion as it reads every card's (``_regroup``'s docstring holds the
rules).  A read of the board's order that does not list a card the filing just added lags the
add (``_board_lag``), so the plan holds the card where GitHub put it, at the bottom.  The card the
weekly update files goes first in the High group (ruling plan#4014): it is left out of the order
the moves are planned over and placed after them (:func:`_placing`).  The filing's mark comes
off after the pass, so the same command run again places the card again (R-BAL202), and moves it
only if it is not there.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from tools.quill._command import _label, with_cards
from tools.quill._fields import HIGH
from tools.quill._regroup import Move, goes_after, lands, moves, undecided
from tools.quill._state import SyncPlan
from tools.quill._tracker import Card, Tracker


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


def pending_suggestions(tracker: Tracker, numbers: Iterable[int],
                        cards: Mapping[int, Card]) -> dict[int, str]:
    """What quill's pending suggestion on each card of ``numbers`` whose group it decides
    (:func:`_regroup.undecided`) suggests, each read by itself
    (:meth:`_suggestion.Suggestions.pending`; this module's docstring), a card holding none
    absent.  ``cards`` holds every open card of ``numbers`` and every step above each."""
    return {number: pending.suggestion.priority for number in undecided(numbers, cards)
            if (pending := tracker.suggestions.pending(cards[number])) is not None}


def _said(move: Move, ruling: str, cards: Mapping[int, Card]) -> str:
    """What a line says of ``move``, made for ``ruling``, after its verb: where on the board it
    goes and its card, which ``cards`` holds."""
    where = "to the top" if move.after is None else f"after plan#{move.after[0]}"
    return f"on the board {where} (ruling {ruling}): {_label(cards[move.number])}"


def _send(tracker: Tracker, order: Sequence[tuple[int, str]], sent: Sequence[tuple[Move, str]],
          cards: Mapping[int, Card], prefix: str = "") -> None:
    """Send each move of ``sent``, planned over ``order``, the board's order, to the board, back
    to back (:meth:`_tracker.Board.move`), each printed as it lands with the ruling it is made
    for, then read the board back once, saying so when it does not show every row moved where
    the moves together leave it yet (:func:`_regroup.lands`).  Each line opens with ``prefix``, a
    filing's indent under its card.  ``cards`` holds each card moved."""
    for move, ruling in sent:
        tracker.board.move(move.item, move.after and move.after[1])
        print(f"{prefix}moved {_said(move, ruling, cards)}")
    if sent and not tracker.board.shows(lands(order, [move for move, _ in sent])):
        print(f"{prefix}  the board does not show every move yet (it lags a write): the next "
              "regroup moves each that did not land")


def _planned(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
             suggested: Mapping[int, str]) -> list[tuple[Move, str]]:
    """The regroup's moves over ``order`` (:func:`_regroup.moves`), each with its ruling."""
    return [(move, "plan#4011") for move in moves(order, cards, suggested)]


def sync_pass(tracker: Tracker, cards: Mapping[int, Card], changes: SyncPlan,
              dry_run: bool) -> None:
    """``quill sync``'s regroup, the last of its passes (ruling plan#4012): the moves over the
    board's order read now, ``cards`` holding each card ``sync`` read, every open one and every
    step above each, with each its state writes (``changes``) closed or reopened read as they
    leave it, so no card it has just closed is moved, and the suggestions read now
    (:func:`pending_suggestions`), sent (:func:`_send`); under ``dry_run``, each printed as what
    would be written, none sent and nothing read back."""
    states = {**dict.fromkeys(changes.reopen, True),
              **dict.fromkeys((*changes.close, *changes.drop), False)}
    shown = {number: replace(card, is_open=states[number]) if number in states else card
             for number, card in cards.items()}
    order = tracker.board.order()
    held = pending_suggestions(tracker, (number for number, _ in order), shown)
    planned = _planned(order, shown, held)
    if not dry_run:
        _send(tracker, order, planned, shown)
        return
    for move, ruling in planned:
        print(f"would move {_said(move, ruling, shown)}")


def _placing(order: Sequence[tuple[int, str]], cards: Mapping[int, Card],
             suggested: Mapping[int, str], number: int, item: str) -> list[tuple[Move, str]]:
    """The regroup's moves over ``order``, the board's order, with card ``number``, the weekly
    update's, whose board item is ``item``, left out of it, so neither its place now nor its own
    move decides them, each with its ruling; then the card's move to first in the High group on
    the board those moves leave (:func:`_regroup.goes_after`, ruling plan#4014), after them,
    unless nothing else moves and it sits there already.  ``cards`` holds every card of
    ``order`` and every step above each, and ``suggested`` the suggestions read for them."""
    rows = [row for row in order if row[0] != number]
    sent, after = _planned(rows, cards, suggested), goes_after(rows, cards, suggested, HIGH)
    numbers = [row for row, _ in order]
    before = dict(zip(numbers, [None, *numbers]))
    if sent or number not in before or before[number] != (after and after[0]):
        sent.append((Move(number, item, after), "plan#4014"))
    return sent


def priority_pass(tracker: Tracker, card: Card, priority: str | None) -> None:
    """``quill priority``'s regroup once ``card`` holds ``priority`` (None for none; ruling
    plan#4012), over the board's order, its cards (:func:`board_cards`) and the suggestions
    (:func:`pending_suggestions`) read now, ``card`` read as holding it whatever a read just after
    its write shows.  A clear is a drop like any other (``_regroup``'s docstring)."""
    order = tracker.board.order()
    cards = board_cards(tracker, [*(number for number, _ in order), card.number])
    cards[card.number] = replace(cards[card.number], priority=priority)
    held = pending_suggestions(tracker, (number for number, _ in order), cards)
    _send(tracker, order, _planned(order, cards, held), cards)


def filing_pass(tracker: Tracker, card: Card, item: str, first: bool, added: bool) -> None:
    """``quill file``'s regroup (ruling plan#4012) once ``card``, a step or question it files, is
    on the board as ``item``, over the board's order, its cards (:func:`board_cards`) and the
    suggestions (:func:`pending_suggestions`) read now, each line indented under the filing's.
    With ``added``, this run put it on the board, at the bottom, so an order read that does not
    list it yet lags that add and is planned with it there (this module's docstring); a leaf
    placed in its split step's place, or a card an earlier run put on the board, is planned where
    the read lists it.  With ``first``, the weekly update's card, ``card`` goes first in the High
    group, placed after the moves over the rest of the board (:func:`_placing`, ruling
    plan#4014); any other is the regroup's like every card."""
    order = tracker.board.order()
    if added and card.number not in dict(order):
        order.append((card.number, item))
    cards = board_cards(tracker, [number for number, _ in order])
    held = pending_suggestions(tracker, (number for number, _ in order), cards)
    sent = (_placing(order, cards, held, card.number, item) if first
            else _planned(order, cards, held))
    _send(tracker, order, sent, cards, "  ")
