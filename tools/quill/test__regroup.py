"""The regroup's plan (rulings plan#4011, plan#4096, ``_regroup``), over random and hand-built
boards, each move landing by the fake board's own ``place``: the board ends sorted by group with
the developer's order kept inside each group, a card no Priority ranks among them, a second pass
plans nothing, the fewest rows move, and no paused or closed card ever does (ruling plan#4015).
A drop lands first in its new group and a raise last (plan#4013), a leaf ranks through its split
step, a card no Priority ranks waits under the High cards when quill suggested Urgent or High
and sits last otherwise (plan#4017, plan#4096), the rows left alone at the end of a group stay
there, and :func:`_regroup.goes_after` names where the weekly update's card goes (plan#4014).

The random boards are checked against an oracle of the rulings written here, apart from
``_regroup`` and ``_next``: a card's group is the highest Priority at it or at a step above it,
or for a card ranked by none the waiting group when quill's suggestion on it is Urgent or High
and the last group otherwise; and none for a card closed, unread or paused.
"""
from __future__ import annotations

import random
from functools import partial

from tools.quill._fake import FakeBoard
from tools.quill._fields import HIGH, LOW, MEDIUM, ON_HOLD, RANKS, URGENT
from tools.quill._regroup import Move, goes_after, moves, undecided
from tools.quill._tracker import Card, Child

#: How many random boards each property is checked over.
_DRAWS = 400
#: The Priorities a random card holds, none among them.
_PRIORITIES = (*RANKS, ON_HOLD, None)
#: The board's groups, top first, as rulings plan#4016 and plan#4096 name them, spelled here
#: apart from ``_regroup``: a card ranked by none waits between High and Medium when quill
#: suggested Urgent or High, and is in the last group otherwise.
_GROUPS = (URGENT, HIGH, "waiting", MEDIUM, LOW, "the rest")


def _card(number, priority=None, **changes):
    """An open step on the board holding ``priority``, with ``changes`` applied."""
    fields = {
        "number": number, "id": 1000 + number, "node_id": f"I_{number}", "title": f"card {number}",
        "kind": "step", "labels": ("tooling",), "arc": "tooling",
        "priority": priority, "effort": None, "is_open": True, "state_reason": None,
        "parent": None, "children": (), "blocked_by": (), "board_item": f"PVTI_{number}",
        "closed_by_tool": False, "touched_by_hand": False, "outside": (),
    }
    return Card(**{**fields, **changes})


def _split(number, priority, *under):
    """A step holding ``priority`` split into the steps numbered ``under``, off the board as a
    split step is."""
    return _card(number, priority, board_item=None,
                 children=tuple(Child(leaf, "step", True) for leaf in under))


def _board(rows, unread=()):
    """A fake board holding ``rows`` then ``unread`` (closed cards the caller's read does not
    hold) in the order given, top first."""
    board = FakeBoard([], {card.number: card for card in (*rows, *unread)})
    board.items = [card.number for card in rows]
    for card in unread:
        board.items.insert(card.number % (len(board.items) + 1), card.number)
    return board


def _land(board, planned):
    """Write ``planned`` to ``board`` in order, each by its own ``place``."""
    for move in planned:
        board.place(move.item, None if move.after is None else move.after[1])


def _after(rows, also=(), suggested=None):
    """The board's card numbers once the regroup's moves over ``rows`` (cards, top first) land,
    and the moves; ``also`` holds the cards above them that are off the board, and
    ``suggested`` quill's suggestion on each card that holds one."""
    cards = {card.number: card for card in (*rows, *also)}
    board = _board(rows)
    planned = moves(board.order(), cards, suggested or {})
    _land(board, planned)
    return board.items, planned


# -- the oracle, and the random boards ------------------------------------------------------------

def _group(number, cards, suggested):
    """The rulings' group of card ``number``, read apart from ``_regroup``: the highest Priority
    at it or at a step above it, else ``suggested``'s Urgent or High as waiting and anything else
    as the rest, by its place in :data:`_GROUPS`; None when it is not in ``cards``, closed, or
    On hold at it or above it."""
    if number not in cards or not cards[number].is_open:
        return None
    held, card = [], cards[number]
    while True:
        held.append(card.priority)
        if card.parent is None:
            break
        card = cards[card.parent]
    if ON_HOLD in held:
        return None
    marks = [RANKS.index(priority) for priority in held if priority is not None]
    if marks:
        return _GROUPS.index(RANKS[min(marks)])
    return _GROUPS.index("waiting" if suggested.get(number) in (URGENT, HIGH) else "the rest")


def _unranked(number, cards):
    """Whether the oracle groups card ``number`` by a suggestion: open, not paused and ranked by
    no Priority, so it is in the last group when no suggestion is read for it."""
    return _group(number, cards, {}) == _GROUPS.index("the rest")


def _longest_in_order(groups):
    """The length of the longest run of ``groups`` in which each is no lower than the one before
    it, by the plain quadratic count, apart from ``_regroup``'s patience sort."""
    best = []
    for index, group in enumerate(groups):
        best.append(1 + max((best[before] for before in range(index)
                             if groups[before] <= group), default=0))
    return max(best, default=0)


def _random(seed):
    """A random board: up to 40 steps and questions, some closed, some leaves of the three split
    steps off it, each holding a random Priority or none, about half holding a random suggestion
    of quill's, and a few closed rows its read does not hold.  The board, the cards a pass reads,
    the suggestions, and the board's card numbers before the pass."""
    rng = random.Random(seed)
    splits = [900, 901, 902]
    rows = []
    for number in range(1, rng.randint(0, 40) + 1):
        parent = rng.choice(splits) if rng.random() < 0.2 else None
        rows.append(_card(number, rng.choice(_PRIORITIES), parent=parent,
                          kind="step" if parent else rng.choice(("step", "question")),
                          is_open=rng.random() > 0.1))
    rng.shuffle(rows)
    suggested = {card.number: rng.choice(RANKS) for card in rows if rng.random() < 0.5}
    above = [_split(split, rng.choice(_PRIORITIES),
                    *(card.number for card in rows if card.parent == split)) for split in splits]
    unread = [_card(500 + index, rng.choice(_PRIORITIES), is_open=False)
              for index in range(rng.randint(0, 3))]
    board = _board(rows, unread)
    return board, {card.number: card for card in (*rows, *above)}, suggested, list(board.items)


def _draws():
    """Each random board with the cards and suggestions it reads, its card numbers before the
    pass and the moves the pass plans, which have landed on it."""
    for seed in range(_DRAWS):
        board, cards, suggested, before = _random(seed)
        planned = moves(board.order(), cards, suggested)
        _land(board, planned)
        yield seed, board, cards, suggested, before, planned


def test_the_random_boards_hold_every_kind_of_row_and_move_some():
    """The draws reach what the properties are about, so none holds over boards that hold none
    of it: rows moved, cards no Priority ranks moved, both waiting and in the last group, rows
    never moved because they are paused, closed or unread, and leaves ranked through their
    split step alone."""
    seen = dict.fromkeys(("moved", "unranked moved", "waiting", "the rest", "paused", "closed",
                          "unread", "leaf"), 0)
    for _, _, cards, suggested, before, planned in _draws():
        seen["moved"] += len(planned)
        for number in before:
            if number not in cards:
                seen["unread"] += 1
                continue
            card = cards[number]
            chain = [card.priority, cards[card.parent].priority] if card.parent else [
                card.priority]
            group = _group(number, cards, suggested)
            seen["closed"] += not card.is_open
            seen["paused"] += card.is_open and ON_HOLD in chain
            seen["waiting"] += group == _GROUPS.index("waiting")
            seen["the rest"] += group == _GROUPS.index("the rest")
            seen["leaf"] += group is not None and card.priority is None and any(chain)
        seen["unranked moved"] += sum(_unranked(move.number, cards) for move in planned)
    assert all(seen.values()), seen


def test_a_pass_sorts_the_rows_it_moves_by_group_keeping_the_drag_order_inside_each():
    """The board's rows that hold a group, once the moves land, are a stable sort of them by
    group: each group whole and in the order the developer dragged it, a card no Priority ranks
    among them (ruling plan#4096)."""
    for seed, board, cards, suggested, before, _ in _draws():
        group = partial(_group, cards=cards, suggested=suggested)
        grouped = [number for number in before if group(number) is not None]
        assert [number for number in board.items if group(number) is not None] == sorted(
            grouped, key=group), f"seed {seed}"


def test_a_second_pass_plans_nothing():
    """A pass over the board the first leaves moves nothing."""
    for seed, board, cards, suggested, _, _ in _draws():
        assert not moves(board.order(), cards, suggested), f"seed {seed}"


def test_a_pass_moves_the_rows_less_the_longest_run_already_in_group_order():
    """The fewest moves: every row holding a group but those of the longest run already in group
    order."""
    for seed, _, cards, suggested, before, planned in _draws():
        groups = [group for number in before
                  if (group := _group(number, cards, suggested)) is not None]
        assert len(planned) == len(groups) - _longest_in_order(groups), f"seed {seed}"


def test_no_paused_or_closed_card_is_ever_moved():
    """No move names a card that is paused, closed or unread, and those keep their order among
    themselves."""
    for seed, board, cards, suggested, before, planned in _draws():
        assert all(_group(move.number, cards, suggested) is not None for move in planned), (
            f"seed {seed}")
        alone = [number for number in before if _group(number, cards, suggested) is None]
        assert [number for number in board.items
                if _group(number, cards, suggested) is None] == alone, f"seed {seed}"


def test_a_pass_reads_the_suggestion_of_each_open_unpaused_card_no_priority_ranks_alone():
    """:func:`_regroup.undecided` names, in the board's order, each row whose group a suggestion
    decides by the oracle: open, not paused and ranked by no Priority, and no other, so a pass
    reads a suggestion for those cards alone."""
    for seed, board, cards, _, _, _ in _draws():
        assert undecided(board.order(), cards) == [
            number for number in board.items if _unranked(number, cards)], f"seed {seed}"


# -- a Priority changed (ruling plan#4013) and a drag across groups (ruling plan#4011) ------------

def test_a_card_whose_priority_drops_goes_first_in_its_new_group():
    """High to Medium, from among the High cards: just under the last High card."""
    rows = [_card(1, URGENT), _card(2, HIGH), _card(3, MEDIUM), _card(4, HIGH), _card(5, HIGH),
            _card(6, MEDIUM), _card(7, MEDIUM)]
    assert _after(rows) == ([1, 2, 4, 5, 3, 6, 7], [Move(3, "PVTI_3", (5, "PVTI_5"))])


def test_a_card_whose_priority_drops_past_a_group_goes_first_in_its_new_group():
    """High to Low, past the Medium cards: just under the last Medium card."""
    rows = [_card(1, HIGH), _card(2, LOW), _card(3, HIGH), _card(4, HIGH), _card(5, MEDIUM),
            _card(6, MEDIUM), _card(7, LOW)]
    assert _after(rows) == ([1, 3, 4, 5, 6, 2, 7], [Move(2, "PVTI_2", (6, "PVTI_6"))])


def test_a_card_whose_priority_is_cleared_goes_first_in_the_last_group():
    """High to none, from among the High cards, however it was cleared: just under the last Low
    card, above the cards already holding none (ruling plan#4096)."""
    rows = [_card(1, HIGH), _card(2), _card(3, HIGH), _card(4, LOW), _card(5)]
    assert _after(rows) == ([1, 3, 4, 2, 5], [Move(2, "PVTI_2", (4, "PVTI_4"))])


def test_a_card_whose_priority_rises_goes_last_in_its_new_group():
    """Medium to High, and Low to Medium: each just under the last card of its new group."""
    rows = [_card(1, HIGH), _card(2, HIGH), _card(3, MEDIUM), _card(4, MEDIUM), _card(5, HIGH),
            _card(6, MEDIUM), _card(7, LOW), _card(8, LOW), _card(9, MEDIUM), _card(10, LOW)]
    assert _after(rows) == ([1, 2, 5, 3, 4, 6, 9, 7, 8, 10],
                            [Move(5, "PVTI_5", (2, "PVTI_2")), Move(9, "PVTI_9", (6, "PVTI_6"))])


def test_a_card_at_the_bottom_set_medium_ends_last_among_the_medium_cards():
    """High, Medium, Low, none, none, then a card set Medium at the bottom: it ends High, Medium,
    Medium, Low, none, none, above the Low card and the cards holding none (finding plan#4092)."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, LOW), _card(4), _card(5), _card(6, MEDIUM)]
    assert _after(rows) == ([1, 2, 6, 3, 4, 5], [Move(6, "PVTI_6", (2, "PVTI_2"))])


def test_a_drag_into_another_group_is_undone():
    """A Medium card dragged up among the High cards goes back first among the Medium cards; a
    High card dragged to the bottom goes back last among the High cards."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4, HIGH), _card(5, MEDIUM),
            _card(6, LOW), _card(7, HIGH)]
    assert _after(rows) == ([1, 3, 4, 7, 2, 5, 6], [Move(7, "PVTI_7", (4, "PVTI_4")),
                                                    Move(2, "PVTI_2", (7, "PVTI_7"))])


def test_a_dragged_card_no_priority_ranks_is_put_back():
    """Ruling plan#4096: a card holding none dragged up among the High cards goes back first
    among the cards holding none, and a card waiting under the High cards dragged to the bottom
    goes back last among the cards waiting there."""
    rows = [_card(1, HIGH), _card(2), _card(3, HIGH), _card(4), _card(5, MEDIUM), _card(6),
            _card(7)]
    assert _after(rows, suggested={4: HIGH, 7: URGENT}) == ([1, 3, 4, 7, 5, 2, 6], [
        Move(7, "PVTI_7", (4, "PVTI_4")), Move(2, "PVTI_2", (5, "PVTI_5"))])


def test_the_first_row_of_the_sort_goes_to_the_top():
    """An Urgent card below everything goes above every row, the paused one left alone at the
    top among them."""
    rows = [_card(1, ON_HOLD), _card(2, MEDIUM), _card(3, MEDIUM), _card(4, URGENT)]
    assert _after(rows) == ([4, 1, 2, 3], [Move(4, "PVTI_4", None)])


# -- what ranks a card, and what the regroup leaves alone ----------------------------------------

def test_a_leaf_ranks_through_its_split_step():
    """A leaf holding no Priority, and one holding a lower one, take their split step's High:
    each moves from among the Medium cards to the end of the High cards."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(5, MEDIUM), _card(6, MEDIUM),
            _card(3, parent=9), _card(7, MEDIUM), _card(4, LOW, parent=9)]
    assert _after(rows, [_split(9, HIGH, 3, 4)]) == ([1, 3, 4, 2, 5, 6, 7], [
        Move(3, "PVTI_3", (1, "PVTI_1")), Move(4, "PVTI_4", (3, "PVTI_3"))])


def test_a_leaf_under_a_paused_step_is_never_moved():
    """A leaf holding High under a step On hold is paused (ruling plan#4015): it stays."""
    rows = [_card(1, MEDIUM), _card(2, HIGH, parent=9), _card(3, MEDIUM)]
    assert _after(rows, [_split(9, ON_HOLD, 2)]) == ([1, 2, 3], [])


def test_paused_and_closed_cards_stay_where_they_are():
    """Among sorted cards, a paused one in the Urgent cards, one in the Medium cards, a closed
    High card at the bottom and a closed card the read does not hold move none, and are no row
    out of its group."""
    rows = [_card(1, URGENT), _card(2, ON_HOLD), _card(3, URGENT), _card(4, MEDIUM),
            _card(5, ON_HOLD), _card(6, MEDIUM), _card(7, HIGH, is_open=False)]
    cards = {card.number: card for card in rows}
    board = _board(rows, [_card(8, URGENT, is_open=False)])
    assert 8 in board.items
    assert not moves(board.order(), cards, {})


def test_a_card_dropped_from_high_goes_under_the_cards_waiting_there():
    """A High card dropped to Medium goes under the cards waiting under the High cards (ruling
    plan#4096), never between them and the High cards."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4), _card(5), _card(6, MEDIUM)]
    assert _after(rows, suggested={4: HIGH, 5: URGENT}) == ([1, 3, 4, 5, 2, 6], [
        Move(2, "PVTI_2", (5, "PVTI_5"))])


def test_the_last_high_card_dropped_goes_under_the_cards_waiting_there():
    """The last High card dropped to Medium, above the card waiting under it, goes under that
    card: the waiting group is above Medium's (ruling plan#4096)."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3), _card(4, MEDIUM)]
    board, planned = _after(rows, suggested={3: HIGH})
    assert board == [1, 3, 2, 4] and len(planned) == 1


def test_a_card_raised_to_high_goes_above_the_cards_waiting_there():
    """A Medium card raised to High goes just under the last High card, above the cards
    waiting there."""
    rows = [_card(1, HIGH), _card(2, HIGH), _card(3), _card(4, MEDIUM), _card(5, HIGH),
            _card(6, MEDIUM)]
    assert _after(rows, suggested={3: HIGH}) == ([1, 2, 5, 3, 4, 6], [
        Move(5, "PVTI_5", (2, "PVTI_2"))])


def test_a_card_suggested_high_at_the_bottom_moves_under_the_high_cards():
    """Ruling plan#4096: a card no Priority ranks that quill suggested High, at the bottom where
    GitHub adds a card, moves to the end of the cards waiting under the High cards, above the
    Medium cards; one suggested Medium, Low or nothing stays among the cards holding none."""
    rows = [_card(1, URGENT), _card(2, HIGH), _card(3), _card(4, MEDIUM), _card(5, LOW),
            _card(6), _card(7), _card(8)]
    assert _after(rows, suggested={3: URGENT, 6: MEDIUM, 7: LOW, 8: HIGH}) == (
        [1, 2, 3, 8, 4, 5, 6, 7], [Move(8, "PVTI_8", (3, "PVTI_3"))])


def test_a_paused_card_at_the_end_of_its_group_stays_at_that_end():
    """A paused High card under the last High card stays among the High rows (ruling plan#4015)
    when a High card dropped to Medium moves past it."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4, ON_HOLD), _card(5, MEDIUM)]
    assert _after(rows) == ([1, 3, 4, 2, 5], [Move(2, "PVTI_2", (4, "PVTI_4"))])


def test_a_paused_card_at_the_bottom_stays_at_the_end_of_its_group():
    """A paused card at the bottom, under the last High card, stays under it when a Medium card
    above them moves: of the two single moves, the one keeping the High card in place, and the
    Medium card goes under the paused card, never between it and the High card (ruling
    plan#4015)."""
    rows = [_card(1, MEDIUM), _card(2, HIGH), _card(3, ON_HOLD)]
    assert _after(rows) == ([2, 3, 1], [Move(1, "PVTI_1", (3, "PVTI_3"))])


def test_a_row_opening_the_low_group_goes_above_the_cards_holding_none():
    """The cards holding no Priority are the last group: a card opening the Low group goes above
    them, never to the bottom (finding plan#4092)."""
    rows = [_card(1, MEDIUM), _card(2, LOW), _card(3, MEDIUM), _card(6, MEDIUM), _card(4),
            _card(5)]
    assert _after(rows) == ([1, 3, 6, 2, 4, 5], [Move(2, "PVTI_2", (6, "PVTI_6"))])


# -- where the weekly update's card goes (ruling plan#4014) ---------------------------------------

def _goes_after(rows, priority, suggested=None):
    """:func:`_regroup.goes_after` over a board holding ``rows``."""
    return goes_after(_board(rows).order(), {card.number: card for card in rows},
                      suggested or {}, priority)


def test_the_updates_card_goes_first_in_the_high_group():
    """Under the last Urgent card and the rows left alone under it, a paused Urgent card among
    them; at the top with no Urgent card."""
    rows = [_card(1, URGENT), _card(2, ON_HOLD), _card(3, HIGH), _card(4, MEDIUM)]
    assert _goes_after(rows, HIGH) == (2, "PVTI_2")
    assert _goes_after(rows[2:], HIGH) is None


def test_first_in_high_under_a_paused_card_at_the_bottom_is_under_it():
    """With a paused card below the last Urgent card at the bottom, first in High is under the
    paused card, which stays at the end of the Urgent group (ruling plan#4015); with cards
    waiting under the High cards and none ranked High, first in High is above them."""
    assert _goes_after([_card(1, URGENT), _card(2, ON_HOLD)], HIGH) == (2, "PVTI_2")
    assert _goes_after([_card(1, URGENT), _card(2), _card(3)], HIGH, {2: HIGH}) == (
        1, "PVTI_1")


def test_the_updates_card_is_placed_on_the_board_the_regroups_moves_leave():
    """On a board out of its groups, the update's card goes first in the High group as the
    moves leave it: the moves over the board without it land first, then it, and a pass with it
    moves nothing."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, URGENT), _card(4, MEDIUM)]
    new = _card(5, HIGH)
    cards = {card.number: card for card in (*rows, new)}
    board = _board([*rows, new])
    without = [row for row in board.order() if row[0] != new.number]
    after = goes_after(without, cards, {}, HIGH)
    assert after == (3, "PVTI_3")
    _land(board, moves(without, cards, {}))
    board.place(new.board_item, after[1])
    assert board.items == [3, 5, 1, 2, 4]
    assert not moves(board.order(), cards, {})
