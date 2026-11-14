"""``quill move`` (R-BAL177): a step or question put at a place in the board's order, and refused a
place in another group (ruling plan#4011).

The board keeps each card in its group, the developer's order kept inside each, and the regroup
undoes a drag across groups at its next pass (:mod:`_regroup`).  So a place outside the card's
group is refused, with nothing written: the refusal names the card's group, the groups of the
rows it would sit between, and ``quill priority``, which moves a card to another group by its
Priority.  It reads the regroup's one group rule (:func:`_regroup.crossing`) over what a pass
reads: every row of the board's order and every step above each
(:func:`_regroup_pass.board_cards`, ``quill priority``'s read), and quill's pending suggestion
on each card a suggestion groups (:func:`_regroup_pass.pending_suggestions`), here the card and
the two rows it would sit between alone.  A paused card is placed anywhere, since the regroup
never moves one (ruling plan#4015).

Split out of the command module (938 lines on 2026-11-14, near the 1,000 ``too-many-lines``
allows), so the refusal grows that module by nothing.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from tools.quill import _git
from tools.quill._command import _label, _one, with_closure
from tools.quill._exits import Refused
from tools.quill._regroup import Crossing, around, crossing
from tools.quill._regroup_pass import board_cards, pending_suggestions
from tools.quill._state import filing_unfinished, is_container
from tools.quill._tracker import Card, Tracker
from tools.quill.setup_tracker import IN_ORDER


def _not_in_order(card: Card) -> str:
    """Why nothing is moved after ``card``, which the board's order read does not list: a step
    or question off the board, or on it but not yet listed by a read that lags a write
    (``_tracker``'s doctrine); any other kind, a task among them, is in no place of it."""
    if card.kind not in IN_ORDER:
        return (f"plan#{card.number} is not in the board's order, its steps and questions "
                "(R-BAL177)")
    if card.board_item is None:
        return (f"{_label(card)} is not on the board, so nothing is placed after it: put it "
                f"on first (`quill move plan#{card.number} --top`, `--bottom` or `--after`)")
    return (f"{_label(card)} is on the board, but the board's order read does not list it yet: "
            "the board lags a write; run this again")


def _crossing_refusal(card: Card, cross: Crossing) -> str:
    """Why ``card`` is not placed where ``cross`` says it would sit, outside its group: its
    group, the rows it would sit between with theirs, and the command that moves it to another
    group (ruling plan#4011)."""
    above, below = (None if row is None else f"plan#{row[0]} ({row[1]})"
                    for row in (cross.above, cross.below))
    where = (f"between {above} and {below}" if above and below
             else f"below {above}" if above else f"above {below}")
    return (f"{_label(card)} ({cross.group}) would sit {where}, outside its group: the board "
            "keeps each card in its group, the developer's order kept inside each, and the "
            "regroup undoes a drag across groups (ruling plan#4011).  Place it inside its group, "
            "or set the Priority of the group it goes in, and the regroup moves it there: "
            f"`quill priority plan#{card.number} --to PRIORITY`")


def _refuse_crossing(tracker: Tracker, card: Card, order: Sequence[tuple[int, str]],
                     after: int | None) -> None:
    """Refuse to place ``card`` just after row ``after`` of ``order``, the board's order (at the
    top when None), when that place is outside its group (:func:`_regroup.crossing`, ruling
    plan#4011), over the cards of every row and every step above each
    (:func:`_regroup_pass.board_cards`) and quill's pending suggestion on the card and on the
    two rows it would sit between (:func:`_regroup.around`), each read as a pass reads it
    (:func:`_regroup_pass.pending_suggestions`)."""
    cards = board_cards(tracker, [*(number for number, _ in order), card.number])
    near = around(order, cards, card.number, after)
    held = pending_suggestions(tracker, (card.number, *(row for row in near if row is not None)),
                               cards)
    if (cross := crossing(order, cards, held, card.number, near)) is not None:
        raise Refused(_crossing_refusal(card, cross))


def cmd_move(args, tracker: Tracker, root: Path) -> int:
    """Put a step or question at a place in the board's order: at the top, at the bottom, or
    just after another row.  Refused, with nothing written, for what is no row of the order (no
    open step or question, or a step split into leaves, R-BAL177, R-BAL179), a step still being
    filed (R-BAL202), a row ``--after`` names that the order read does not list
    (:func:`_not_in_order`), and a place in another group (:func:`_refuse_crossing`, ruling
    plan#4011).  A card off the board is added at its bottom, then placed."""
    card = _one(tracker, args.card)
    shipped = _git.read(root).shipped.cards
    cards = with_closure(tracker, {card.number: card})
    if card.kind not in IN_ORDER or not card.is_open or is_container(card, cards, shipped):
        raise Refused(f"the board's order holds open steps and questions only (a task nests "
                      f"under its step in no order, ruling plan#1814), and no step split into "
                      f"leaves (R-BAL177, R-BAL179): {_label(card)}")
    if filing_unfinished(card) and card.kind == "step":
        raise Refused(f"{_label(card)}'s filing has not finished (R-BAL202): finish it first.  "
                      "If it is a leaf -- one whose link has not landed reads as a top-level "
                      "step -- its `quill file` command moves it again after this move")
    order = tracker.board.order()
    items = dict(order)
    if args.after is not None:
        if args.after not in items:
            raise Refused(_not_in_order(_one(tracker, args.after)))
        after = args.after
    elif args.bottom:
        rest = [number for number, _ in order if number != card.number]
        after = rest[-1] if rest else None
    else:
        after = None
    _refuse_crossing(tracker, card, order, after)
    item = items.get(card.number)
    if item is None:
        item = tracker.board.add(card)
        print(f"  board: added {_label(card)} at the bottom")
    shown = tracker.board.place(item, None if after is None else items[after])
    print(f"moved {_label(card)}" + ("" if shown else
                                     " -- the board has not shown it yet; re-read it later"))
    return 0
