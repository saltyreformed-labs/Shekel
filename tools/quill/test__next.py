"""``quill next``'s order (plan#1820, rulings plan#1830, plan#1831, plan#1832), over hand-built
cards and claims: marks first, then the board's order; a blocked step's turn hands out what it
needs; a step needing one On hold waits, by name.  ``cmd_next``'s lines are
``test_quill_on_hold.py``'s.
"""
from __future__ import annotations

from tools.quill._git import Claim
from tools.quill._next import next_step
from tools.quill._tracker import Card, Child
from tools.quill.setup_tracker import FILING


def _card(number, kind="step", **changes):
    """An open card, unclaimed and unblocked, with ``changes`` applied."""
    fields = {
        "number": number, "id": 1000 + number, "node_id": f"I_{number}", "title": f"card {number}",
        "kind": kind, "labels": ("balance",), "arc": "balance",
        "priority": None, "effort": None, "is_open": True, "state_reason": None,
        "parent": None, "children": (), "blocked_by": (), "board_item": f"PVTI_{number}",
        "closed_by_tool": False, "touched_by_hand": False, "outside": (),
    }
    return Card(**{**fields, **changes})


def _cards(*cards):
    """Cards by number."""
    return {card.number: card for card in cards}


def _claim(number):
    """The branch ``card/<number>``, claimed an hour ago."""
    return Claim(number, f"sha{number}", "2026-11-04T11:00:00+00:00", "2026-11-04T11:00:00Z",
                 frozenset(), None)


def _split(number, *under, **changes):
    """A step split into the steps numbered ``under``, off the board as a split step is."""
    return _card(number, board_item=None, children=tuple(Child(leaf, "step", True)
                                                         for leaf in under), **changes)


def _handed_out(order, cards):
    """Every step ``quill next`` hands out in turn, each shipping before the next is asked for:
    the order the plan is built in, read as the ruling's example reads it."""
    shipped, built = set(), []
    while (card := next_step(order, cards, shipped, {}).card) is not None:
        built.append(card.number)
        shipped.add(card.number)
    return built


# -- the ruling's example ------------------------------------------------------------------

#: Ruling plan#1831's board: A #1, B #2, C #3, D #20, and C needs D.
_A, _B, _C, _D = 1, 2, 3, 4


def _example(**c_changes):
    """The ruling's board, with a step at each place between C and D so D's pull shows: the
    numbers 5 to 20 hold those, and D is on the board at #20."""
    between = list(range(5, 21))
    cards = _cards(_card(_A), _card(_B), _card(_C, blocked_by=(_D,), **c_changes), _card(_D),
                   *(_card(number) for number in between))
    return [_A, _B, _C, *between, _D], cards


def test_the_rulings_example_with_nothing_marked_pulls_d_to_cs_turn():
    """Ruling plan#1831: "Nothing marked: A, B, D, C (D is pulled to C's turn)"."""
    order, cards = _example()
    assert _handed_out(order, cards)[:4] == [_A, _B, _D, _C]


def test_the_rulings_example_with_c_marked_high():
    """Ruling plan#1831: "Mark C High: D, C, A, B"."""
    order, cards = _example(priority="High")
    assert _handed_out(order, cards)[:4] == [_D, _C, _A, _B]


# -- marks ---------------------------------------------------------------------------------

def test_urgent_then_high_then_the_rest_in_the_boards_order():
    """Ruling plan#1831: Medium and Low count as unmarked; the board decides among equals."""
    cards = _cards(_card(1, priority="Low"), _card(2), _card(3, priority="High"),
                   _card(4, priority="Medium"), _card(5, priority="Urgent"),
                   _card(6, priority="High"), _card(7, priority="Urgent"))
    assert _handed_out([1, 2, 3, 4, 5, 6, 7], cards) == [5, 7, 3, 6, 1, 2, 4]


def test_a_prerequisite_goes_first_whatever_its_own_mark():
    """Ruling plan#1830: "A prerequisite step might not be marked high priority but a step it
    depends on might be."  So a Low step goes ahead of the High step needing it."""
    cards = _cards(_card(1), _card(2, priority="High", blocked_by=(3,)), _card(3, priority="Low"))
    assert _handed_out([1, 2, 3], cards) == [3, 2, 1]


def test_a_split_steps_mark_reaches_its_leaves_and_the_highest_holds():
    """A step marked and then split (plan#2023 holds High so, 2026-11-04) leaves the board; its
    mark still orders its leaves, read and never copied."""
    cards = _cards(_split(1, 3, 4, priority="High"), _split(2, 5, priority="Urgent"), _card(6),
                   _card(3, parent=1), _card(4, parent=1, priority="Urgent"), _card(5, parent=2))
    assert _handed_out([6, 3, 4, 5], cards) == [4, 5, 3, 6]


# -- what a blocked step needs -------------------------------------------------------------

def test_the_walk_goes_on_down_to_a_step_that_can_start():
    """C needs D needs E: E is handed out at C's turn, then D, then C, ahead of what is
    between them on the board."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _card(2, blocked_by=(3,)), _card(3))
    assert _handed_out([1, 9, 2, 3], cards) == [3, 2, 1, 9]


def test_of_several_needs_the_best_turn_goes_first():
    """Marks, then the board's order, as for the steps' own turns, and not GitHub's list of
    the blockers."""
    plain = _cards(_card(1, blocked_by=(2, 3)), _card(3), _card(2))
    assert next_step([1, 3, 2], plain, set(), {}).card.number == 3
    marked = _cards(_card(1, blocked_by=(2, 3)), _card(3), _card(2, priority="High"))
    assert next_step([1, 3, 2], marked, set(), {}).card.number == 2


def test_a_claimed_prerequisite_is_passed_over_for_what_else_is_needed():
    """Another session has D, so it is not handed out; what else C needs is."""
    cards = _cards(_card(1, blocked_by=(2, 3)), _card(9), _card(2), _card(3))
    assert next_step([1, 9, 2, 3], cards, set(), {2: _claim(2)}).card.number == 3


def test_a_step_waiting_only_on_a_claim_passes_its_turn():
    """Nothing to hand out for C while D is built, so the next step takes its turn."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _card(2))
    assert next_step([1, 9, 2], cards, set(), {2: _claim(2)}).card.number == 9


def test_what_a_claimed_prerequisite_needs_is_still_walked():
    """A claim does not ask for its blockers to be done: E is still unfinished, and C needs it."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _card(2, blocked_by=(3,)), _card(3))
    assert next_step([1, 9, 2, 3], cards, set(), {2: _claim(2)}).card.number == 3


def test_a_claimed_step_takes_no_turn_so_what_it_needs_is_not_pulled():
    """The spec's order is of unclaimed steps: the branch holding it asks for its own."""
    cards = _cards(_card(1, blocked_by=(3,)), _card(2), _card(3))
    assert next_step([1, 2, 3], cards, set(), {1: _claim(1)}).card.number == 2


def test_a_needed_split_step_hands_out_its_leaves_best_turn_first():
    """A split step is off the board and no branch builds it: what C needs of it is its leaves
    (R-BAL177), in their order on the board."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _split(2, 3, 4), _card(3, parent=2),
                   _card(4, parent=2))
    assert _handed_out([1, 9, 4, 3], cards) == [4, 3, 1, 9]


def test_a_leaf_needs_what_the_step_above_it_is_blocked_by():
    """R-BAL182: a leaf inherits its split step's blockers, so its turn pulls them."""
    cards = _cards(_split(2, 3, blocked_by=(4,)), _card(3, parent=2), _card(9), _card(4))
    assert _handed_out([3, 9, 4], cards) == [4, 3, 9]


def test_two_cards_blocking_each_other_end_the_walk():
    """GitHub is no guarantee against the loop: each passes its turn, and the next step takes
    one."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(2, blocked_by=(1,)), _card(9))
    assert next_step([1, 2, 9], cards, set(), {}).card.number == 9


def test_a_shared_need_is_walked_once():
    """Two ways down to one card hand it out, and name it once."""
    cards = _cards(_card(1, blocked_by=(2, 3)), _card(2, blocked_by=(4,)),
                   _card(3, blocked_by=(4,)), _card(4, priority="On hold"))
    answer = next_step([1, 2, 3, 4], cards, set(), {})
    assert [card.number for card in answer.waiting[0].on] == [4]


def test_a_finding_needed_hands_out_the_step_that_ships_it():
    """A finding is never handed out, but the one step it is blocked by ships it (R-BAL395), so
    that step is what the step needing it waits for."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _card(2, "finding", blocked_by=(3,)),
                   _card(3))
    assert next_step([1, 9, 3], cards, set(), {}).card.number == 3


def test_a_question_is_answered_not_built_and_its_owner_is_not_what_is_needed():
    """What a question is blocked by is the step it was asked for, which no step needing the
    question needs: only the developer's answer closes it, so C passes its turn."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _card(2, "question", blocked_by=(3,)),
                   _card(3))
    assert next_step([1, 9, 3], cards, set(), {}).card.number == 9


def test_an_open_question_does_not_stop_what_else_is_needed_being_pulled():
    """Spec: "the first unfinished step it needs".  Only a pause stops the pull (ruling
    plan#1832); the question is not a step, so the step it needs is the next need."""
    cards = _cards(_card(1, blocked_by=(2, 3)), _card(9), _card(2, "question"), _card(3))
    assert next_step([1, 9, 3], cards, set(), {}).card.number == 3


def test_the_walk_stops_at_a_step_nothing_can_start():
    """D's filing has not finished, so it is never offered (R-BAL202), and nothing is pulled
    for it: E takes its own turn."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9),
                   _card(2, blocked_by=(3,), labels=(FILING,)), _card(3))
    assert next_step([1, 9, 2, 3], cards, set(), {}).card.number == 9


def test_a_prerequisite_off_the_board_is_handed_out_and_still_named():
    """It has no place in the order, so no turn of its own, but C's turn needs it; and it is
    still named, since no turn of its own would ever come."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(9), _card(2, board_item=None))
    answer = next_step([1, 9], cards, set(), {})
    assert answer.card.number == 2
    assert [card.number for card in answer.unplaced] == [2]


def test_a_marked_step_off_the_board_takes_no_turn():
    """A mark does not give a step a place in the order: it is named instead."""
    cards = _cards(_card(9), _card(2, board_item=None, priority="Urgent"))
    answer = next_step([9], cards, set(), {})
    assert answer.card.number == 9
    assert [card.number for card in answer.unplaced] == [2]


# -- On hold -------------------------------------------------------------------------------

def test_a_step_on_hold_is_never_handed_out_and_nothing_waits_for_it_unasked():
    """Ruling plan#1832: never handed out, whatever its place; and with nothing needing it, it
    is the developer's mark alone, with no line."""
    cards = _cards(_card(1, priority="On hold"), _card(2))
    answer = next_step([1, 2], cards, set(), {})
    assert answer.card.number == 2 and not answer.waiting
    alone = _cards(_card(1, priority="On hold"))
    assert next_step([1], alone, set(), {}).card is None


def test_a_step_needing_one_on_hold_waits_by_name_and_the_next_takes_its_turn():
    """Spec: "a step that needs one is reported as waiting on it, by name"."""
    cards = _cards(_card(1, blocked_by=(3,)), _card(2), _card(3, priority="On hold"))
    answer = next_step([1, 2, 3], cards, set(), {})
    assert answer.card.number == 2
    assert [(wait.step.number, [card.number for card in wait.on]) for wait in answer.waiting] == [
        (1, [3])]


def test_every_step_needing_it_is_named_however_far_down_and_past_the_step_handed_out():
    """C needs D needs H: both wait on H.  A later step waits as well as an earlier one."""
    cards = _cards(_card(1), _card(2, blocked_by=(3,)), _card(3, blocked_by=(4,)),
                   _card(4, priority="On hold"))
    answer = next_step([1, 2, 3, 4], cards, set(), {})
    assert answer.card.number == 1
    assert [(wait.step.number, [card.number for card in wait.on]) for wait in answer.waiting] == [
        (2, [4]), (3, [4])]


def test_a_step_waiting_on_a_pause_pulls_nothing_else_it_needs():
    """C needs H and E: pulling E would bring C no nearer while H is paused, so E takes its own
    turn, at #3 here, and is not moved ahead of B."""
    cards = _cards(_card(1, blocked_by=(2, 4)), _card(3), _card(4), _card(2, priority="On hold"))
    assert _handed_out([1, 3, 4, 2], cards) == [3, 4]


def test_a_step_needing_two_pauses_names_both():
    """Each is a mark the developer would have to lift."""
    cards = _cards(_card(1, blocked_by=(2, 3)), _card(2, priority="On hold"),
                   _card(3, priority="On hold"))
    answer = next_step([1, 2, 3], cards, set(), {})
    assert answer.card is None
    assert [card.number for card in answer.waiting[0].on] == [2, 3]


def test_a_leaf_of_a_split_step_on_hold_is_held_and_no_waiter():
    """R-BAL182's shape: the pause is the step's, read by each leaf: it has no turn at all, and
    a leaf with no turn is no step waiting."""
    cards = _cards(_split(1, 2, priority="On hold"), _card(2, parent=1), _card(3))
    answer = next_step([2, 3], cards, set(), {})
    assert answer.card.number == 3 and not answer.waiting


def test_a_step_needing_a_leaf_under_a_pause_waits_on_the_step_that_holds_it():
    """The name a person can act on is the card that holds the mark."""
    cards = _cards(_split(1, 2, priority="On hold"), _card(2, parent=1), _card(3, blocked_by=(2,)))
    answer = next_step([3, 2], cards, set(), {})
    assert answer.card is None
    assert [card.number for card in answer.waiting[0].on] == [1]


def test_a_step_needing_a_split_step_with_a_leaf_on_hold_waits_on_that_leaf():
    """A split step is resolved by every leaf (R-BAL177), so one paused leaf holds it."""
    cards = _cards(_card(1, blocked_by=(2,)), _split(2, 3, 4), _card(3, parent=2),
                   _card(4, parent=2, priority="On hold"))
    answer = next_step([1, 3, 4], cards, set(), {})
    assert answer.card.number == 3
    assert [(wait.step.number, [card.number for card in wait.on]) for wait in answer.waiting] == [
        (1, [4])]


def test_a_claimed_step_on_hold_still_holds_what_needs_it():
    """The pause is read whoever holds the branch."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(2, priority="On hold"))
    answer = next_step([1, 2], cards, set(), {2: _claim(2)})
    assert answer.card is None
    assert [wait.step.number for wait in answer.waiting] == [1]


def test_a_pause_on_a_finished_card_holds_nothing():
    """Shipped or dropped, it is no longer needed, whatever mark it was left with."""
    cards = _cards(_card(1, blocked_by=(2,)), _card(2, priority="On hold"))
    answer = next_step([1, 2], cards, {2}, {})
    assert answer.card.number == 1 and not answer.waiting
    dropped = _card(2, priority="On hold", is_open=False, state_reason="NOT_PLANNED",
                    closed_by_tool=True)
    answer = next_step([1, 2], {**cards, 2: dropped}, set(), {})
    assert answer.card.number == 1 and not answer.waiting


def test_a_claimed_or_shipped_step_needing_a_pause_is_no_waiter():
    """It takes no turn, so nothing passes over it."""
    cards = _cards(_card(1, blocked_by=(3,)), _card(2, blocked_by=(3,)),
                   _card(3, priority="On hold"))
    assert not next_step([1, 2, 3], cards, {1}, {2: _claim(2)}).waiting


def test_the_mark_lifted_the_step_is_handed_out_at_the_waiter_s_turn():
    """The waiter's turn comes back at once: the pull of the ruling's example."""
    cards = _cards(_card(1, blocked_by=(3,)), _card(2), _card(3))
    assert next_step([1, 2, 3], cards, set(), {}).card.number == 3


# -- --arc ---------------------------------------------------------------------------------

def _arcs():
    """X (balance, #1) needs Y (salary, #4); Z (salary, #2) and W (balance, #3) are free."""
    return _cards(_card(1, blocked_by=(4,)), _card(2, arc="salary"), _card(3),
                  _card(4, arc="salary"))


def test_an_arc_hands_out_its_first_step_of_the_same_walk():
    """The whole plan's walk reads Y first, at X's turn; asked for salary, that is the answer."""
    assert next_step([1, 2, 3, 4], _arcs(), set(), {}).card.number == 4
    assert next_step([1, 2, 3, 4], _arcs(), set(), {}, "salary").card.number == 4


def test_an_arc_never_hands_out_another_arcs_step():
    """Asked for balance, Y is no answer, so X passes its turn as it did before the walk."""
    assert next_step([1, 2, 3, 4], _arcs(), set(), {}, "balance").card.number == 3


def test_an_arc_pulls_nothing_for_another_arcs_step_waiting_on_a_pause():
    """X (balance) needs H and E (salary): it waits, so asked for salary, E keeps its own turn
    behind Z, as asked for the whole plan."""
    cards = _cards(_card(1, blocked_by=(2, 4)), _card(3, arc="salary"), _card(4, arc="salary"),
                   _card(2, priority="On hold"))
    assert next_step([1, 3, 4, 2], cards, set(), {}, "salary").card.number == 3


def test_an_arc_names_only_its_own_steps_waiting():
    """As ``unplaced`` does: another arc's waiting is another lane's."""
    cards = _cards(_card(1, blocked_by=(3,)), _card(2, arc="salary", blocked_by=(3,)),
                   _card(3, priority="On hold"))
    assert [wait.step.number for wait in next_step([1, 2, 3], cards, set(), {}, "salary").waiting
            ] == [2]
    assert [wait.step.number for wait in next_step([1, 2, 3], cards, set(), {}).waiting] == [1, 2]
