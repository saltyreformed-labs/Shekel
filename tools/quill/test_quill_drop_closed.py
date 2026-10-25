"""A drop under a card the TOOL closed, X-cx L2's leaf C3 (C2 review LOW 6 and the leaf C3
reviews, applications of ruling ``balance:R-BAL207``): ``quill drop`` of a leaf whose filing
never finished first shows the card it splits as ``sync`` would show it once the drop is
done (``_state.drop_shows``), then takes the leaf out of the split as under an open step
(R-BAL205), so no link is left.  Over :class:`_fake.FakeTracker` and a throwaway git
repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace

from tools.quill._fake import FailOnce, FakeTracker, run, ship, unship
from tools.quill._tracker import Child
from tools.quill.setup_tracker import FILING



def _low6_path(tracker, code):
    """C2 review LOW 6's state: plan#1's only leaf plan#2 (its unmark failed) shipped and
    was closed by a person, so `sync` showed plan#1 completed and off the board; then a
    revert deleting its marker undid plan#2 and a person reopened it.  No sync has run
    since."""
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True,
                children=(Child(2, "step", True),))
    tracker.add(2, parent=1, labels=("balance", FILING), state_reason="REOPENED",
                touched_by_hand=True)
    ship(code, 2)
    unship(code, 2)
    assert tracker.board.items == [2]


def test_dropping_a_leaf_under_a_split_step_the_tool_closed_reopens_it_first(code, capsys):
    """C2 review LOW 6: the drop of plan#2 saw plan#1 closed and only unlinked plan#2, and
    then no `sync` read plan#1 again: shown completed, never shipped, never offered.  The
    tool closed plan#1 as its display of its leaves, and plan#2 is still work, so the drop
    reopens plan#1 first, as `sync` would, then undoes the split as under an open step:
    plan#1 back in plan#2's place, plan#2 unlinked, then closed.  `next` offers plan#1 at
    once, and `sync` has nothing left to write."""
    tracker = FakeTracker()
    _low6_path(tracker, code)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "not after all") == 0
    assert tracker.writes[len(writes):] == [
        ("reopen", 1), ("board_add", 1), ("board_place", 1, "PVTI_2"), ("remove_child", 1, 2),
        ("comment", 2, "Dropped: not after all"), ("close", 2, "not_planned")]
    assert ("  reopened plan#1: quill had closed it, and without plan#2 it still has "
            "work to do (R-BAL190)\n") in capsys.readouterr().out
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#1 ")
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes


def test_a_stale_label_removed_after_such_a_drop_never_drops_the_step(code, capsys):
    """Leaf C3 review M1: with the link left for the next `sync`, a person removing the
    closed leaf's stale 'filing' label before that sync made it a dropped leaf, so R-BAL187
    dropped plan#1, never built, and `next` offered plan#9, which waits on it.  The drop
    leaves no link, so the label edit decides nothing: plan#1 stays plain work and plan#9
    keeps waiting."""
    tracker = FakeTracker()
    _low6_path(tracker, code)
    tracker.add(9, blocked_by=(1,))
    assert run(tracker, code, "drop", "plan#2", "--why", "not after all") == 0
    card = tracker.cards_by_number[2]
    tracker.cards_by_number[2] = replace(card, labels=tuple(label for label in card.labels
                                                            if label != FILING))
    before = len(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert len(tracker.writes) == before
    assert tracker.cards_by_number[1].is_open
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#1 ")


def test_a_tool_closed_split_step_left_split_is_shown_as_its_other_leaves_say(code, capsys):
    """Leaf C3 delta review DM1: plan#1, shown completed by the tool, keeps a leaf a person
    dropped beside the unfinished one dropped now.  The drop reopened plan#1 (judged with
    plan#3 still work), and the next `sync` closed it again; it now shows plan#1 as the
    drop leaves it -- closed again as not planned, every leaf left dropped (R-BAL187) --
    then unlinks plan#3, and `sync` has nothing left to write.  With the other leaf still
    work instead, the drop reopens plan#1, still split, and puts nothing on the board."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True,
                children=(Child(2, "step", False), Child(3, "step", True)))
    tracker.add(2, parent=1, is_open=False, state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.add(3, parent=1, labels=("balance", FILING))
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#3", "--why", "replanned") == 0
    assert tracker.writes == [("close", 1, "not_planned"), ("remove_child", 1, 3),
                              ("comment", 3, "Dropped: replanned"), ("close", 3, "not_planned")]
    assert ("  closed plan#1 again as not planned: what its smaller steps say once plan#3 is out "
            "of them (R-BAL190)\n") in capsys.readouterr().out
    assert run(tracker, code, "sync") == 0
    assert len(tracker.writes) == 4 and 1 not in tracker.board.items
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True,
                children=(Child(2, "step", True), Child(3, "step", True)))
    tracker.add(2, parent=1)
    tracker.add(3, parent=1, labels=("balance", FILING))
    assert run(tracker, code, "drop", "plan#3", "--why", "replanned") == 0
    assert tracker.writes == [("reopen", 1), ("remove_child", 1, 3),
                              ("comment", 3, "Dropped: replanned"), ("close", 3, "not_planned")]
    assert 1 not in tracker.board.items
    assert run(tracker, code, "sync") == 0
    assert len(tracker.writes) == 4


def test_a_split_step_shipped_as_plain_work_is_not_reopened_when_its_leaf_is_dropped(code):
    """Leaf C3 delta review LOW 3 (D3): a marker ships plan#1 itself, so once the drop
    leaves it a plain step, git says it shipped: shown completed is right, and neither the
    drop nor `sync` reopens it."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True,
                children=(Child(2, "step", True),))
    tracker.add(2, parent=1, labels=("balance", FILING))
    ship(code, 1)
    assert run(tracker, code, "drop", "plan#2", "--why", "no") == 0
    assert tracker.writes == [("remove_child", 1, 2), ("comment", 2, "Dropped: no"),
                              ("close", 2, "not_planned")]
    assert run(tracker, code, "sync") == 0
    assert len(tracker.writes) == 3


def test_dropping_a_leaf_under_a_split_step_a_person_closed_unlinks_it_at_once(code):
    """A person's close of the split step is their decision (R-BAL185): the drop of its
    unfinished leaf unlinks it first and keeps that close, putting nothing on the board."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="NOT_PLANNED", touched_by_hand=True,
                children=(Child(2, "step", True),))
    tracker.add(2, parent=1, labels=("balance", FILING))
    assert run(tracker, code, "drop", "plan#2", "--why", "replanned") == 0
    assert tracker.writes == [("remove_child", 1, 2), ("comment", 2, "Dropped: replanned"),
                              ("close", 2, "not_planned")]


def test_a_drop_under_a_finding_the_tool_closed_shows_it_as_sync_would(code):
    """A step a person linked under a finding by hand, still marked.  The finding splits
    nothing (R-BAL177); shipped, it stays closed and the drop only unlinks the step.  A
    revert deleting the finding's marker makes its close a display git no longer backs,
    and the unlink removes the one card that led `sync` to it, so the drop reopens it
    first, as `sync` would; nothing goes on the board."""
    tracker = FakeTracker()
    tracker.add(1, kind="finding", is_open=False, state_reason="COMPLETED", closed_by_tool=True,
                children=(Child(2, "step", True),))
    tracker.add(2, parent=1, labels=("balance", FILING))
    ship(code, 1)
    assert run(tracker, code, "drop", "plan#2", "--why", "no") == 0
    assert tracker.writes == [("remove_child", 1, 2), ("comment", 2, "Dropped: no"),
                              ("close", 2, "not_planned")]
    tracker = FakeTracker()
    tracker.add(1, kind="finding", is_open=False, state_reason="COMPLETED", closed_by_tool=True,
                children=(Child(2, "step", True),))
    tracker.add(2, parent=1, labels=("balance", FILING))
    unship(code, 1)
    assert run(tracker, code, "drop", "plan#2", "--why", "no") == 0
    assert tracker.writes == [("reopen", 1), ("remove_child", 1, 2), ("comment", 2, "Dropped: no"),
                              ("close", 2, "not_planned")]
    assert 1 not in tracker.board.items


def test_an_open_split_steps_state_is_left_to_sync_when_its_leaf_is_dropped(code):
    """An open split step is read by every `sync`, so the drop of its unfinished leaf
    writes nothing to its state, though its only other leaf shipped: `sync` closes it, and
    the shipped leaf too -- git's marker alone closes it (R-BAL359), where the claim check
    that marker replaced reported a leaf no claim named and left it open."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, parent=1, labels=("balance", FILING))
    ship(code, 2)
    assert run(tracker, code, "drop", "plan#3", "--why", "no") == 0
    assert tracker.writes == [("remove_child", 1, 3), ("comment", 3, "Dropped: no"),
                              ("close", 3, "not_planned")]
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[3:] == [("close", 1, "completed"), ("close", 2, "completed")]


def test_a_step_a_drop_closed_stays_dropped_when_a_leaf_linked_under_it_is_dropped(code):
    """plan#1 was dropped as a plain step (the tool's not-planned close); a person linked
    plan#2, still being filed, under it on the web, and no `sync` has run since (one would
    reopen plan#1, then a split step whose state is display, R-BAL190).  Dropping plan#2
    leaves plan#1 the plain step it was (R-BAL205): dropped, so it is not reopened, and
    the link goes."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True,
                children=(Child(2, "step", True),))
    tracker.add(2, parent=1, labels=("balance", FILING))
    assert run(tracker, code, "drop", "plan#2", "--why", "no") == 0
    assert tracker.writes == [("remove_child", 1, 2), ("comment", 2, "Dropped: no"),
                              ("close", 2, "not_planned")]
    assert run(tracker, code, "sync") == 0
    assert len(tracker.writes) == 3 and not tracker.cards_by_number[1].is_open


def test_a_drop_cut_short_after_its_reopen_is_finished_by_the_same_drop(code):
    """The drop's reopen of plan#1 lands and its board add fails (exit 2): plan#2 is still
    open and linked, so the same drop run again finds plan#1 open and finishes the undo
    as under an open step, then closes plan#2."""
    tracker = FakeTracker()
    _low6_path(tracker, code)
    FailOnce(tracker.board, "add")
    writes = list(tracker.writes)
    assert run(tracker, code, "drop", "plan#2", "--why", "not after all") == 2
    assert tracker.writes[len(writes):] == [("reopen", 1)]
    assert run(tracker, code, "drop", "plan#2", "--why", "not after all") == 0
    assert tracker.writes[len(writes) + 1:] == [
        ("board_add", 1), ("board_place", 1, "PVTI_2"), ("remove_child", 1, 2),
        ("comment", 2, "Dropped: not after all"), ("close", 2, "not_planned")]
