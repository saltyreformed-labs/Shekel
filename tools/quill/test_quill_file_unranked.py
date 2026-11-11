"""No step is split while neither it nor a step above it holds a Priority (ruling plan#3229): a
leaf holds none of its own, takes no suggestion and stays out of the Inbox, and is ranked by
its split step's Priority when quill hands out work (``_next._rank``, over
``_state.at_and_above``), so ``quill file step --parent`` refuses one that would be left
without a rank, before anything is written, and the session asks the developer first.
"""
from __future__ import annotations

import pytest

from tools.quill._fake import FakeTracker, leaf_filing, run
from tools.quill._fields import PRIORITY_FIELD, PRIORITY_OPTIONS
from tools.quill._tracker import Child
from tools.quill.setup_tracker import FILING


def _refusal(number):
    """The refusal of a leaf under card ``number``, ranked by no Priority."""
    return (f"refused: neither plan#{number} nor any step above it holds a Priority, and a leaf "
            "is ranked by its split step's (ruling plan#3229), so no leaf is filed under it "
            f"until one does: ask the developer for plan#{number}'s Priority first (`quill "
            f"priority plan#{number} --to PRIORITY` sets it), then run this again\n")


def test_a_leaf_under_a_step_holding_no_priority_is_refused_unwritten(code, tmp_path, capsys):
    """The step stays whole, and on the board, until the developer ranks it."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 1
    assert capsys.readouterr().err == _refusal(1)
    assert not tracker.writes and set(tracker.cards_by_number) == {1}
    assert tracker.board.items == [1]


@pytest.mark.parametrize("priority", PRIORITY_OPTIONS)
def test_a_leaf_under_a_step_holding_any_priority_is_filed_holding_none(code, tmp_path,
                                                                       priority):
    """Each option ranks the leaf, On hold too, whose pause the leaf inherits
    (``_state.held_at``); nothing is copied onto the leaf."""
    tracker = FakeTracker()
    tracker.add(1, priority=priority)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert tracker.cards_by_number[2].parent == 1
    assert tracker.cards_by_number[2].priority is None
    assert tracker.cards_by_number[1].priority == priority


def test_a_leaf_of_a_leaf_is_ranked_by_a_step_above_its_split_step(code, tmp_path):
    """plan#2 splits plan#1, which holds High: a leaf of plan#2 is ranked by plan#1's, the
    walk ``_next._rank`` reads, though plan#2 holds none."""
    tracker = FakeTracker()
    tracker.add(1, priority="High", children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, *leaf_filing(tmp_path, "quarter", "plan#2")) == 0
    assert tracker.cards_by_number[3].parent == 2


def test_a_leaf_of_a_leaf_no_step_above_ranks_is_refused_naming_its_split_step(code, tmp_path,
                                                                              capsys):
    """Neither plan#2 nor plan#1 above it holds a Priority."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, *leaf_filing(tmp_path, "quarter", "plan#2")) == 1
    assert capsys.readouterr().err == _refusal(2)
    assert not tracker.writes


def test_a_leaf_filing_run_again_under_a_step_holding_no_priority_is_refused_until_it_does(
        code, tmp_path, capsys):
    """A leaf an earlier run created and linked, still marked (an older quill's, or its step's
    Priority cleared since), is never offered (R-BAL202), and the same command finishes it
    once the developer ranks the step."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),))
    tracker.add(2, body="Build half.", title="half", labels=("balance", FILING), parent=1,
                effort="2", on_board=False)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 1
    assert capsys.readouterr().err == _refusal(1)
    assert not tracker.writes and FILING in tracker.cards_by_number[2].labels
    tracker.set_field(1, PRIORITY_FIELD, "Medium")
    tracker.writes.clear()
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert ("unmark", 2) in tracker.writes and len(tracker.cards_by_number) == 2
