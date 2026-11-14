"""The regroup's plan (ruling plan#4011, ``_regroup``), over random and hand-built boards, each
move landing by the fake board's own ``place``: the board ends sorted by group with the
developer's order kept inside each group, a second pass plans nothing, the fewest rows move, and
no undecided, paused or closed card ever does (rulings plan#4015, plan#4017).  A drop lands
first in its new group and a raise last (plan#4013), a leaf ranks through its split step, the
rows left alone between two groups stay between them, and :func:`_regroup.goes_after` names each
place a card is filed or cleared to (plan#4013, plan#4014, plan#4017).

The random boards are checked against an oracle of the rulings written here, apart from
``_regroup`` and ``_next``: a card's group is the highest Priority at it or at a step above it,
and none for a card closed, unread, paused or unranked.
"""
from __future__ import annotations

import random
from functools import partial

from tools.quill._fake import FakeBoard
from tools.quill._fields import HIGH, LOW, MEDIUM, ON_HOLD, RANKS, URGENT
from tools.quill._regroup import Move, goes_after, moves
from tools.quill._tracker import Card, Child

#: How many random boards each property is checked over.
_DRAWS = 400
#: The Priorities a random card holds, none among them.
_PRIORITIES = (*RANKS, ON_HOLD, None)


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


def _after(rows, also=()):
    """The board's card numbers once the regroup's moves over ``rows`` (cards, top first) land,
    and the moves; ``also`` holds the cards above them that are off the board."""
    cards = {card.number: card for card in (*rows, *also)}
    board = _board(rows)
    planned = moves(board.order(), cards)
    _land(board, planned)
    return board.items, planned


# -- the oracle, and the random boards ------------------------------------------------------------

def _group(number, cards):
    """The rulings' group of card ``number``, read apart from ``_regroup``: the highest Priority
    at it or at a step above it, by its place in ``RANKS``; None when it is not in ``cards``,
    closed, On hold at it or above it, or ranked by none."""
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
    return min(marks) if marks else None


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
    steps off it, each holding a random Priority or none, and a few closed rows its read does not
    hold.  The board, the cards a pass reads, and the board's card numbers before the pass."""
    rng = random.Random(seed)
    splits = [900, 901, 902]
    rows = []
    for number in range(1, rng.randint(0, 40) + 1):
        parent = rng.choice(splits) if rng.random() < 0.2 else None
        rows.append(_card(number, rng.choice(_PRIORITIES), parent=parent,
                          kind="step" if parent else rng.choice(("step", "question")),
                          is_open=rng.random() > 0.1))
    rng.shuffle(rows)
    above = [_split(split, rng.choice(_PRIORITIES),
                    *(card.number for card in rows if card.parent == split)) for split in splits]
    unread = [_card(500 + index, rng.choice(_PRIORITIES), is_open=False)
              for index in range(rng.randint(0, 3))]
    board = _board(rows, unread)
    return board, {card.number: card for card in (*rows, *above)}, list(board.items)


def _draws():
    """Each random board with the cards it reads, its card numbers before the pass and the moves
    the pass plans, which have landed on it."""
    for seed in range(_DRAWS):
        board, cards, before = _random(seed)
        planned = moves(board.order(), cards)
        _land(board, planned)
        yield seed, board, cards, before, planned


def test_the_random_boards_hold_every_kind_of_row_and_move_some():
    """The draws reach what the properties are about, so none holds over boards that hold none
    of it: rows moved, and rows never moved because they are undecided, paused, closed or
    unread, and leaves ranked through their split step alone."""
    seen = {"moved": 0, "undecided": 0, "paused": 0, "closed": 0, "unread": 0, "leaf": 0}
    for _, _, cards, before, planned in _draws():
        seen["moved"] += len(planned)
        for number in before:
            if number not in cards:
                seen["unread"] += 1
                continue
            card = cards[number]
            seen["closed"] += not card.is_open
            chain = [card.priority, cards[card.parent].priority] if card.parent else [
                card.priority]
            seen["paused"] += card.is_open and ON_HOLD in chain
            seen["undecided"] += card.is_open and not any(chain)
            seen["leaf"] += (_group(number, cards) is not None and card.priority is None)
    assert all(seen.values()), seen


def test_a_pass_sorts_the_rows_it_moves_by_group_keeping_the_drag_order_inside_each():
    """The board's ranked rows, once the moves land, are a stable sort of them by group: each
    group whole and in the order the developer dragged it."""
    for seed, board, cards, before, _ in _draws():
        group = partial(_group, cards=cards)
        ranked = [number for number in before if group(number) is not None]
        assert [number for number in board.items if group(number) is not None] == sorted(
            ranked, key=group), f"seed {seed}"


def test_a_second_pass_plans_nothing():
    """A pass over the board the first leaves moves nothing."""
    for seed, board, cards, _, _ in _draws():
        assert not moves(board.order(), cards), f"seed {seed}"


def test_a_pass_moves_the_rows_less_the_longest_run_already_in_group_order():
    """The fewest moves: every ranked row but those of the longest run already in group order."""
    for seed, _, cards, before, planned in _draws():
        groups = [group for number in before if (group := _group(number, cards)) is not None]
        assert len(planned) == len(groups) - _longest_in_order(groups), f"seed {seed}"


def test_no_undecided_paused_or_closed_card_is_ever_moved():
    """No move names a card that is undecided, paused, closed or unread, and those keep their
    order among themselves."""
    for seed, board, cards, before, planned in _draws():
        assert all(_group(move.number, cards) is not None for move in planned), f"seed {seed}"
        alone = [number for number in before if _group(number, cards) is None]
        assert [number for number in board.items if _group(number, cards) is None] == alone, (
            f"seed {seed}")


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


def test_a_card_whose_priority_rises_goes_last_in_its_new_group():
    """Medium to High, and Low to Medium: each just under the last card of its new group."""
    rows = [_card(1, HIGH), _card(2, HIGH), _card(3, MEDIUM), _card(4, MEDIUM), _card(5, HIGH),
            _card(6, MEDIUM), _card(7, LOW), _card(8, LOW), _card(9, MEDIUM), _card(10, LOW)]
    assert _after(rows) == ([1, 2, 5, 3, 4, 6, 9, 7, 8, 10],
                            [Move(5, "PVTI_5", (2, "PVTI_2")), Move(9, "PVTI_9", (6, "PVTI_6"))])


def test_a_drag_into_another_group_is_undone():
    """A Medium card dragged up among the High cards goes back first among the Medium cards; a
    High card dragged to the bottom goes back last among the High cards."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4, HIGH), _card(5, MEDIUM),
            _card(6, LOW), _card(7, HIGH)]
    assert _after(rows) == ([1, 3, 4, 7, 2, 5, 6], [Move(7, "PVTI_7", (4, "PVTI_4")),
                                                    Move(2, "PVTI_2", (7, "PVTI_7"))])


def test_the_first_row_of_the_sort_goes_to_the_top():
    """An Urgent card below everything goes above every row, the ones left alone among them."""
    rows = [_card(1), _card(2, MEDIUM), _card(3, MEDIUM), _card(4, URGENT)]
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


def test_undecided_paused_and_closed_cards_stay_where_they_are():
    """Among sorted cards, an undecided card in the Urgent cards, a paused one in the Medium
    cards, a closed High card at the bottom and a closed card the read does not hold move none,
    and are no row out of its group."""
    rows = [_card(1, URGENT), _card(2), _card(3, URGENT), _card(4, MEDIUM), _card(5, ON_HOLD),
            _card(6, MEDIUM), _card(7, HIGH, is_open=False)]
    cards = {card.number: card for card in rows}
    board = _board(rows, [_card(8, URGENT, is_open=False)])
    assert 8 in board.items
    assert not moves(board.order(), cards)


def test_the_cards_waiting_under_the_high_cards_stay_there_when_a_card_drops():
    """A High card dropped to Medium goes under the undecided cards waiting under the High cards
    (ruling plan#4017), never between them and the High cards."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4), _card(5), _card(6, MEDIUM)]
    assert _after(rows) == ([1, 3, 4, 5, 2, 6], [Move(2, "PVTI_2", (5, "PVTI_5"))])


def test_the_cards_waiting_under_the_high_cards_stay_there_when_a_card_rises():
    """A Medium card raised to High goes just under the last High card, above the cards
    waiting there."""
    rows = [_card(1, HIGH), _card(2, HIGH), _card(3), _card(4, MEDIUM), _card(5, HIGH),
            _card(6, MEDIUM)]
    assert _after(rows) == ([1, 2, 5, 3, 4, 6], [Move(5, "PVTI_5", (2, "PVTI_2"))])


def test_a_paused_card_at_the_end_of_its_group_stays_at_that_end():
    """A paused High card under the last High card stays among the High rows (ruling plan#4015)
    when a High card dropped to Medium moves past it."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4, ON_HOLD), _card(5, MEDIUM)]
    assert _after(rows) == ([1, 3, 4, 2, 5], [Move(2, "PVTI_2", (4, "PVTI_4"))])


def test_a_row_opening_the_last_group_goes_above_the_no_priority_group():
    """The undecided cards below the last ranked card are the no-Priority group: a card opening
    the Low group goes above them, never to the bottom."""
    rows = [_card(1, MEDIUM), _card(2, LOW), _card(3, MEDIUM), _card(6, MEDIUM), _card(4),
            _card(5)]
    assert _after(rows) == ([1, 3, 6, 2, 4, 5], [Move(2, "PVTI_2", (6, "PVTI_6"))])


def test_the_last_high_card_dropped_stays_above_the_cards_waiting_there():
    """The last High card dropped to Medium is already first among the Medium cards where it
    stands, so the regroup moves nothing, and it stays above the cards waiting under it (the
    module's docstring)."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3), _card(4, MEDIUM)]
    assert _after(rows) == ([1, 2, 3, 4], [])


# -- where a card is filed or cleared to (rulings plan#4013, plan#4014, plan#4017) ----------------

def _goes_after(rows, priority, first=False):
    """:func:`_regroup.goes_after` over a board holding ``rows``."""
    return goes_after(_board(rows).order(), {card.number: card for card in rows}, priority, first)


def test_a_card_suggested_high_goes_just_under_the_high_cards():
    """Just under the last High card, above the cards already waiting there; at the top when
    nothing is ranked High or higher."""
    rows = [_card(1, URGENT), _card(2, HIGH), _card(3, HIGH), _card(4), _card(5, MEDIUM)]
    assert _goes_after(rows, HIGH) == (3, "PVTI_3")
    assert _goes_after([_card(1), _card(2, MEDIUM)], HIGH) is None


def test_the_updates_card_goes_first_in_the_high_group():
    """Under the last Urgent card and the rows left alone under it, a paused Urgent card among
    them; at the top with no Urgent card."""
    rows = [_card(1, URGENT), _card(2, ON_HOLD), _card(3, HIGH), _card(4, MEDIUM)]
    assert _goes_after(rows, HIGH, first=True) == (2, "PVTI_2")
    assert _goes_after(rows[2:], HIGH, first=True) is None


def test_first_in_a_group_nothing_follows_is_just_under_the_group_above():
    """With no card ranked below Urgent, first in High is just under the last Urgent card, above
    the no-Priority group, never at the bottom."""
    rows = [_card(1, URGENT), _card(2), _card(3)]
    assert _goes_after(rows, HIGH, first=True) == (1, "PVTI_1")


def test_a_priority_cleared_through_quill_goes_to_the_top_of_the_no_priority_group():
    """Just under the last Low card, or the last Medium card with no Low card, above the
    undecided cards below them."""
    rows = [_card(1, MEDIUM), _card(2, LOW), _card(3, LOW), _card(4)]
    assert _goes_after(rows, LOW) == (3, "PVTI_3")
    assert _goes_after([_card(1, MEDIUM), _card(2)], LOW) == (1, "PVTI_1")


def test_a_card_is_placed_on_the_board_the_regroups_moves_leave():
    """On a board out of its groups, a new card suggested High goes just under the High cards
    as the moves leave them: the moves over the board without it land first, then it, and a
    pass with it moves nothing."""
    rows = [_card(1, HIGH), _card(2, MEDIUM), _card(3, HIGH), _card(4, MEDIUM)]
    new = _card(5)
    cards = {card.number: card for card in (*rows, new)}
    board = _board([*rows, new])
    without = [row for row in board.order() if row[0] != new.number]
    after = goes_after(without, cards, HIGH)
    assert after == (3, "PVTI_3")
    _land(board, moves(without, cards))
    board.place(new.board_item, after[1])
    assert board.items == [1, 3, 5, 2, 4]
    assert not moves(board.order(), cards)
