"""``quill move`` refused a place in another group (ruling plan#4011, :mod:`_move`): ``--top``,
``--bottom`` and ``--after`` each refused across a group and taken within one, a place between
two groups taken for a card of either, a card no Priority ranks refused across the group its
suggestion gives it and taken within it (ruling plan#4096), a leaf grouped by its split step's
Priority, a paused card taken anywhere (ruling plan#4015), and nothing written on a refusal, a
card off the board not added.  The rows a refusal names are the nearest the regroup moves, past a
paused one, and the move reads quill's suggestion on the card and those two rows alone.  Over the
in-memory tracker (:class:`_fake.FakeTracker`) and the ``code`` fixture's real git; that the
refusal is exactly the place the next pass would undo is ``test__regroup.py``'s.
"""
from __future__ import annotations

from tools.quill._draft import Suggestion
from tools.quill._fake import QUILL_ACTOR, FakeTracker, run
from tools.quill._suggestion import Pending
from tools.quill._tracker import Child

#: The reason a refusal gives after the place, and the command it points at, ``{number}`` the
#: card's.
_WHY = ("outside its group: the board keeps each card in its group, the developer's order kept "
        "inside each, and the regroup undoes a drag across groups (ruling plan#4011).  Place it "
        "inside its group, or set the Priority of the group it goes in, and the regroup moves it "
        "there: `quill priority plan#{number} --to PRIORITY`\n")


def _refused(number, group, where):
    """The refusal of card ``number``, in ``group``, at a place ``where`` names."""
    return (f"refused: plan#{number} [step, balance] card {number} ({group}) would sit {where}, "
            + _WHY.format(number=number))


def _board(*priorities):
    """A tracker whose board holds one step per entry of ``priorities``, numbered from 1 in
    that order, each holding its entry's Priority (None for none)."""
    tracker = FakeTracker()
    for number, priority in enumerate(priorities, start=1):
        tracker.add(number, priority=priority)
    return tracker


def _suggest(tracker, number, priority):
    """quill's suggestion of ``priority`` pending on card ``number``, as a filing leaves it."""
    tracker.suggestions.held[number] = Pending(Suggestion(priority, "A test card's reason."),
                                               QUILL_ACTOR)


def test_top_is_refused_above_another_group_and_taken_inside_its_own(code, capsys):
    """Medium plan#3 at the top would sit above High plan#1: refused, nothing written.  High
    plan#2 at the top stays among the High cards: moved."""
    tracker = _board("High", "High", "Medium", "Medium")
    assert run(tracker, code, "move", "plan#3", "--top") == 1
    assert capsys.readouterr().err == _refused(3, "Medium", "above plan#1 (High)")
    assert not tracker.writes and tracker.board.items == [1, 2, 3, 4]
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert tracker.board.items == [2, 1, 3, 4]


def test_bottom_is_refused_below_another_group_and_taken_inside_its_own(code, capsys):
    """High plan#1 at the bottom would sit below Low plan#4: refused, nothing written.  Low
    plan#3 at the bottom stays among the Low cards: moved."""
    tracker = _board("High", "Medium", "Low", "Low")
    assert run(tracker, code, "move", "plan#1", "--bottom") == 1
    assert capsys.readouterr().err == _refused(1, "High", "below plan#4 (Low)")
    assert not tracker.writes and tracker.board.items == [1, 2, 3, 4]
    assert run(tracker, code, "move", "plan#3", "--bottom") == 0
    assert tracker.board.items == [1, 2, 4, 3]


def test_after_is_refused_inside_another_group_and_taken_inside_its_own_or_between(code, capsys):
    """High plan#1 after Medium plan#3 would sit between two Medium cards: refused, nothing
    written.  After High plan#2 it stays among the High cards, and Medium plan#4 after High
    plan#2 sits between the High and the Medium cards, opening its group: both moved."""
    tracker = _board("High", "High", "Medium", "Medium")
    assert run(tracker, code, "move", "plan#1", "--after", "plan#3") == 1
    assert capsys.readouterr().err == _refused(1, "High",
                                               "between plan#3 (Medium) and plan#4 (Medium)")
    assert not tracker.writes and tracker.board.items == [1, 2, 3, 4]
    assert run(tracker, code, "move", "plan#1", "--after", "plan#2") == 0
    assert tracker.board.items == [2, 1, 3, 4]
    assert run(tracker, code, "move", "plan#4", "--after", "plan#1") == 0
    assert tracker.board.items == [2, 1, 4, 3]


def test_a_card_no_priority_ranks_is_held_to_the_group_its_suggestion_gives_it(code, capsys):
    """plan#2 and plan#3 hold no Priority and quill's suggestion of High and Urgent, so they
    wait under the High cards, and plan#5 holds none and no suggestion, so it is in the last
    group (ruling plan#4096): plan#2 at the bottom and plan#5 at the top are refused, nothing
    written; plan#3 above plan#2 stays among the waiting cards, moved."""
    tracker = _board("High", None, None, "Medium", None)
    _suggest(tracker, 2, "High")
    _suggest(tracker, 3, "Urgent")
    assert run(tracker, code, "move", "plan#2", "--bottom") == 1
    assert capsys.readouterr().err == _refused(2, "suggested Urgent or High",
                                               "below plan#5 (no Priority)")
    assert run(tracker, code, "move", "plan#5", "--top") == 1
    assert capsys.readouterr().err == _refused(5, "no Priority", "above plan#1 (High)")
    assert not tracker.writes and tracker.board.items == [1, 2, 3, 4, 5]
    assert run(tracker, code, "move", "plan#3", "--after", "plan#1") == 0
    assert tracker.board.items == [1, 3, 2, 4, 5]


def test_a_leaf_is_grouped_by_its_split_steps_priority(code, capsys):
    """Leaf plan#2 holds no Priority, and its split step plan#9, off the board, holds High: it
    is in the High group, so at the bottom, below Medium plan#3, it is refused."""
    tracker = FakeTracker()
    tracker.add(9, priority="High", on_board=False, children=(Child(2, "step", True),))
    tracker.add(1, priority="High")
    tracker.add(2, parent=9)
    tracker.add(3, priority="Medium")
    assert run(tracker, code, "move", "plan#2", "--bottom") == 1
    assert capsys.readouterr().err == _refused(2, "High", "below plan#3 (Medium)")
    assert not tracker.writes


def test_the_rows_a_refusal_names_are_the_nearest_the_regroup_moves(code, capsys, monkeypatch):
    """Medium plan#1 just after paused plan#3 would sit between Low plan#2 and Low plan#4: the
    refusal names those, past the paused card the regroup never moves.  The move reads quill's
    suggestion on the card and on the two rows it would sit between, each when no Priority ranks
    it, and on no other card: none for that refusal, and for plan#6 placed after Low plan#4,
    above plan#5, those two of the three cards holding none."""
    tracker = _board("Medium", "Low", "On hold", "Low", None, None, None)
    _suggest(tracker, 6, "Low")
    read = []
    pending = tracker.suggestions.pending
    monkeypatch.setattr(tracker.suggestions, "pending",
                        lambda card: read.append(card.number) or pending(card))
    assert run(tracker, code, "move", "plan#1", "--after", "plan#3") == 1
    assert capsys.readouterr().err == _refused(1, "Medium",
                                               "between plan#2 (Low) and plan#4 (Low)")
    assert not read and not tracker.writes
    assert run(tracker, code, "move", "plan#6", "--after", "plan#4") == 0
    assert read == [6, 5]
    assert tracker.board.items == [1, 2, 3, 4, 6, 5, 7]


def test_a_paused_card_is_placed_anywhere(code):
    """Paused plan#2, which the regroup never moves (ruling plan#4015), goes to the bottom,
    after Medium plan#3 and to the top, each across the groups around it."""
    tracker = _board("High", "On hold", "Medium", "Low")
    assert run(tracker, code, "move", "plan#2", "--bottom") == 0
    assert tracker.board.items == [1, 3, 4, 2]
    assert run(tracker, code, "move", "plan#2", "--after", "plan#3") == 0
    assert tracker.board.items == [1, 3, 2, 4]
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert tracker.board.items == [2, 1, 3, 4]


def test_a_card_off_the_board_refused_its_place_is_not_added(code, capsys):
    """Low plan#3, off the board, at the top would sit above High plan#1: refused before the add
    that puts it on, so nothing is written; at the bottom it is added, then placed."""
    tracker = _board("High", "Medium")
    tracker.add(3, priority="Low", on_board=False)
    assert run(tracker, code, "move", "plan#3", "--top") == 1
    assert capsys.readouterr().err == _refused(3, "Low", "above plan#1 (High)")
    assert not tracker.writes and tracker.board.items == [1, 2]
    assert run(tracker, code, "move", "plan#3", "--bottom") == 0
    assert tracker.writes == [("board_add", 3), ("board_place", 3, "PVTI_2")]
    assert tracker.board.items == [1, 2, 3]
