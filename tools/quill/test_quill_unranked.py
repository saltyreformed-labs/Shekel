"""No piece of a split step is left with nothing ranking it (ruling plan#3304): ``quill priority
--clear`` is refused, nothing written, when a piece still to build would then hold no Priority at
it or above (``_arc_label._unranked_refusal``), and ``quill next`` names each piece on the board
nothing ranks, and the step to give one (``_next.unranked_lines``), so one cleared on the web is
seen too.  "Ranked" is ``_next.ranked``, the walk ``_next._rank`` reads.  Over
:class:`_fake.FakeTracker` and a throwaway git repository (the ``code`` fixture).
"""
from __future__ import annotations

import pytest

from tools.quill._fake import FakeTracker, run, ship
from tools.quill._tracker import Child


def _split(tracker, number, under, **fields):
    """Put in ``tracker`` the step ``number`` split into the steps ``under``, off the board as
    a split step is; each leaf is added by the caller."""
    tracker.add(number, on_board=False, children=tuple(Child(leaf, "step", True)
                                                       for leaf in under), **fields)


def _label(number):
    """How a command names the fake's card ``number``, a step of the balance arc."""
    return f"plan#{number} [step, balance] card {number}"


def test_a_clear_on_a_split_step_leaving_its_pieces_unranked_is_refused(code, capsys):
    """The review's reproduction (review of plan#2798, finding 2): the clear was taken, exit 0,
    writing ``[('set_field', 1, 'Priority', None)]``.  Now refused, exit 1, nothing written, and
    the step at the top of the chain being the one cleared, each piece is to take its own."""
    tracker = FakeTracker()
    _split(tracker, 1, (2, 3), priority="Medium")
    tracker.add(2, parent=1)
    tracker.add(3, parent=1)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 1
    assert (f"once {_label(1)}'s Priority is cleared, plan#2, plan#3 would hold none, its own "
            "or a step's above it, and a piece of a split step nothing ranks is refused (ruling "
            "plan#3304): give each of them its own first") in capsys.readouterr().err
    assert not tracker.writes
    assert tracker.cards_by_number[1].priority == "Medium"


@pytest.mark.parametrize(("why", "fields"), [
    ("each piece holds its own", {2: {"priority": "High"}, 3: {"priority": "Low"}}),
    ("one shipped, the other holds its own", {3: {"priority": "Low"}}),
])
def test_a_clear_on_a_split_step_whose_pieces_are_ranked_otherwise_is_taken(code, why, fields):
    """A piece holding its own Priority is ranked by it; one shipped in git is no piece still
    to build."""
    tracker = FakeTracker()
    _split(tracker, 1, (2, 3), priority="Medium")
    tracker.add(2, parent=1, **fields.get(2, {}))
    tracker.add(3, parent=1, **fields.get(3, {}))
    if 2 not in fields:
        ship(code, 2)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0, why
    assert tracker.writes == [("set_field", 1, "Priority", None)]


def test_a_clear_under_a_step_holding_a_priority_is_taken(code):
    """A step above the one cleared still ranks its pieces, nested splits included."""
    tracker = FakeTracker()
    _split(tracker, 10, (1,), priority="High")
    _split(tracker, 1, (2,), parent=10, priority="Medium")
    tracker.add(2, parent=1)
    assert run(tracker, code, "priority", "plan#1", "--clear") == 0
    assert tracker.writes == [("set_field", 1, "Priority", None)]


def test_a_pieces_own_clear_with_nothing_above_it_is_refused_naming_the_top(code, capsys):
    """A piece clearing its own Priority is a clear that leaves a piece unranked (the ruling's
    words): refused, and the step to give one is the top of its chain."""
    tracker = FakeTracker()
    _split(tracker, 10, (1,))
    _split(tracker, 1, (2,), parent=10)
    tracker.add(2, parent=1, priority="High")
    assert run(tracker, code, "priority", "plan#2", "--clear") == 1
    assert ("plan#2 would hold none, its own or a step's above it, and a piece of a split step "
            "nothing ranks is refused (ruling plan#3304): give plan#10 one first") in (
                capsys.readouterr().err)
    assert not tracker.writes


def test_a_pieces_own_clear_under_a_ranked_step_and_a_plain_steps_clear_are_taken(code):
    """The split step's Priority ranks the piece; a step in no split ranks no piece, and its
    clear puts it in the Inbox (ruling plan#3231)."""
    tracker = FakeTracker()
    _split(tracker, 1, (2,), priority="Medium")
    tracker.add(2, parent=1, priority="High")
    tracker.add(5, priority="Low")
    assert run(tracker, code, "priority", "plan#2", "--clear") == 0
    assert run(tracker, code, "priority", "plan#5", "--clear") == 0
    assert tracker.writes == [("set_field", 2, "Priority", None),
                              ("set_field", 5, "Priority", None)]


def test_quill_next_names_each_piece_nothing_ranks_and_the_step_to_give_one(code, capsys):
    """In the board's order, the top of each chain named; a piece holding its own Priority, and
    one under a ranked step, is not named."""
    tracker = FakeTracker()
    _split(tracker, 10, (1,))
    _split(tracker, 1, (2, 3, 4), parent=10)
    tracker.add(3, parent=1)
    tracker.add(2, parent=1)
    tracker.add(4, parent=1, priority="Low")
    _split(tracker, 20, (21,), priority="High")
    tracker.add(21, parent=20)
    assert run(tracker, code, "next") == 0
    unranked = [line for line in capsys.readouterr().out.splitlines()
                if line.startswith("UNRANKED: ")]
    assert unranked == [
        f"UNRANKED: {_label(number)} holds no Priority, nor does any step above it, and the "
        "Inbox lists no piece of a split step (ruling plan#3304): give plan#10 one (`quill "
        "priority plan#10 --to PRIORITY`)" for number in (3, 2)]


def test_quill_next_names_no_piece_once_its_step_holds_a_priority(code, capsys):
    """The line goes as soon as the step it names holds one."""
    tracker = FakeTracker()
    _split(tracker, 1, (2,), priority="Medium")
    tracker.add(2, parent=1)
    assert run(tracker, code, "next") == 0
    assert "UNRANKED" not in capsys.readouterr().out
