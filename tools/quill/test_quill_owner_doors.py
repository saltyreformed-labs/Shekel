"""Every door that sets or changes a finding's owner keeps Josh's rule of 2026-10-26 (ruling
plan#1784, "One owner, always a leaf"): an open finding has exactly one owner, and it is a
step a branch builds -- live work, never a step split into smaller steps -- so some pull
request always owes it.  The doors are the filing, ``quill block``, the split (``quill file
step --parent``) and ``quill drop``; the hand-off is ``quill hand`` (``test_quill_findings``),
and the backstop for a hand edit on the web is CI's landing check
(``test_landing_findings``).  Each door both ways, over :class:`_fake.FakeTracker` and a
throwaway git repository (the ``code`` fixture, ``conftest.py``).
"""
from __future__ import annotations

from tools.quill._fake import FailOnce, FakeTracker, finding_filing, leaf_filing, run, ship
from tools.quill._tracker import Child, OutsideLink
from tools.quill.setup_tracker import FILING


def _split(tracker, step=1, leaf=2):
    """Step ``step`` split into the one leaf ``leaf``, both open."""
    tracker.add(step, children=(Child(leaf, "step", True),), on_board=False)
    tracker.add(leaf, parent=step)


# -- the filing ------------------------------------------------------------------------------

def test_a_finding_filed_for_a_split_step_is_refused_with_nothing_written(code, capsys):
    """A split step is live, but no branch builds it, so it is never a finding's owner: the
    check refuses it before anything is sent, naming why."""
    tracker = FakeTracker()
    _split(tracker)
    assert run(tracker, code, *finding_filing("plan#1")) == 1
    assert ("its owner is a step split into smaller steps" in capsys.readouterr().err
            and not tracker.writes)


def test_a_finding_filed_for_the_leaf_of_a_split_step_is_filed(code):
    """The leaf is live work: the finding is filed blocked by it, its owner."""
    tracker = FakeTracker()
    _split(tracker)
    assert run(tracker, code, *finding_filing("plan#2")) == 0
    assert tracker.writes == [("create", 3, "finding", "Twice", ("balance", FILING)),
                              ("block", 3, 2), ("unmark", 3)]


# -- quill block -----------------------------------------------------------------------------

def test_block_gives_an_open_finding_with_no_blocker_its_owner(code):
    """A finding blocked by no card takes a step a branch builds as its one owner."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "finding")
    assert run(tracker, code, "block", "plan#2", "--by", "plan#1") == 0
    assert tracker.writes == [("block", 2, 1)]


def test_block_refuses_a_second_owner_naming_the_hand_off(code, capsys):
    """An open finding has one owner: a second link is refused, naming ``quill hand``; one with
    an outside link, or with two owners, names how to give it one owner; nothing written."""
    tracker = FakeTracker()
    for number in (1, 3, 4):
        tracker.add(number)
    tracker.add(2, "finding", blocked_by=(1,))
    tracker.add(5, "finding", outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    tracker.add(6, "finding", blocked_by=(1, 3))
    expected = {2: "has an owner already, and an open finding has one (R-BAL395, plan#1784): "
                   "`quill hand plan#2 --to plan#4` hands it on",
                5: "has an owner already, and an open finding has one (R-BAL395, plan#1784): it "
                   "is blocked by saltyreformed-labs/Shekel#6, outside the tracker",
                6: "(R-BAL395, plan#1784): it is blocked by plan#1, plan#3, so it names no one "
                   "owner"}
    for number, sentence in expected.items():
        assert run(tracker, code, "block", f"plan#{number}", "--by", "plan#4") == 1
        assert sentence in capsys.readouterr().err
    assert not tracker.writes


def test_block_refuses_an_owner_no_branch_builds(code, capsys):
    """A split step, a step that shipped and a ruling may not own an open finding."""
    tracker = FakeTracker()
    _split(tracker)
    tracker.add(3)
    tracker.add(4, "ruling", is_open=False, state_reason="COMPLETED")
    tracker.add(5, "finding")
    ship(code, 3)
    for by in (1, 3, 4):
        assert run(tracker, code, "block", "plan#5", "--by", f"plan#{by}") == 1
        assert ("may not own plan#5 [finding, balance] card 5: a finding's owner is a step a "
                "branch builds") in capsys.readouterr().err
    assert not tracker.writes


def test_block_remove_refuses_the_last_owner_of_an_open_finding(code, capsys):
    """Removing the one link an open finding has would leave it ownerless; removing one of two
    leaves it one, and passes."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add(2, "finding", blocked_by=(1,))
    tracker.add(4, "finding", blocked_by=(1, 3))
    assert run(tracker, code, "block", "plan#2", "--by", "plan#1", "--remove") == 1
    assert ("plan#1 is the one owner of plan#2 [finding, balance] card 2: removing its link "
            "would leave an open finding with no owner (plan#1784)") in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, "block", "plan#4", "--by", "plan#3", "--remove") == 0
    assert tracker.writes == [("unblock", 4, 3)]


def test_block_links_a_finding_shipped_or_dropped_and_any_other_card_as_asked(code):
    """The one-owner rule holds an open finding: one shipped or dropped is owed by no one, and
    a step, a ruling or a question is linked as before."""
    tracker = FakeTracker()
    _split(tracker)
    tracker.add(3, "finding", blocked_by=(2,))
    tracker.add(4, "finding", blocked_by=(2,), is_open=False, state_reason="NOT_PLANNED")
    tracker.add(5, "question")
    ship(code, 3)
    assert run(tracker, code, "block", "plan#3", "--by", "plan#1") == 0
    assert run(tracker, code, "block", "plan#4", "--by", "plan#1") == 0
    assert run(tracker, code, "block", "plan#4", "--by", "plan#2", "--remove") == 0
    assert run(tracker, code, "block", "plan#5", "--by", "plan#1") == 0
    assert tracker.writes == [("block", 3, 1), ("block", 4, 1), ("unblock", 4, 2),
                              ("block", 5, 1)]


# -- quill drop ------------------------------------------------------------------------------

def test_a_drop_that_would_leave_an_open_finding_ownerless_is_refused_unwritten(code, capsys):
    """A step owning an open finding, a split step owning one or a leaf of which owns one, and
    a leaf under a split step owning one (a hand edit) are each refused, every finding named
    with its hand-off; once it is handed on, shipped or dropped, the card is dropped."""
    tracker = FakeTracker()
    _split(tracker)
    tracker.add(3)
    tracker.add(4, "finding", blocked_by=(3,))
    tracker.add(5, "finding", blocked_by=(2,))
    tracker.add(6, "finding", blocked_by=(1,))
    for step, owners in ((3, ((4, 3),)), (1, ((6, 1), (5, 2))), (2, ((5, 2),))):
        assert run(tracker, code, "drop", f"plan#{step}", "--why", "moot") == 1
        err = capsys.readouterr().err
        assert "not dropped: a dropped step owns no open finding (plan#1784)" in err
        assert all(f"`quill hand plan#{finding} --to plan#S`, owned by plan#{held}" in err
                   for finding, held in owners)
    assert not tracker.writes
    ship(code, 4)
    tracker.add(5, "finding", blocked_by=(2,), is_open=False, state_reason="NOT_PLANNED")
    assert run(tracker, code, "drop", "plan#3", "--why", "moot") == 0
    assert run(tracker, code, "drop", "plan#2", "--why", "moot") == 1
    assert ("`quill hand plan#6 --to plan#S`, owned by plan#1" in capsys.readouterr().err
            and ("close", 2, "not_planned") not in tracker.writes)
    ship(code, 6)
    assert run(tracker, code, "drop", "plan#2", "--why", "moot") == 0
    assert ("close", 2, "not_planned") in tracker.writes


# -- the split -------------------------------------------------------------------------------

def test_a_split_hands_the_split_steps_open_findings_to_its_new_leaf(code, tmp_path, capsys):
    """Each open finding the step owns is blocked by the leaf, then unlinked from the step,
    before the leaf's mark comes off (R-BAL202); one shipped or dropped stays where it is."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3, "finding", blocked_by=(1,))
    tracker.add(4, "finding", blocked_by=(1,), is_open=False, state_reason="NOT_PLANNED")
    tracker.add(5, "finding", blocked_by=(1,))
    ship(code, 5)
    assert run(tracker, code, *leaf_filing(tmp_path, "Leaf")) == 0
    leaf = max(tracker.cards_by_number)
    hand = [write for write in tracker.writes if write[0] in ("block", "unblock", "unmark")]
    assert hand == [("block", 3, leaf), ("unblock", 3, 1), ("unmark", leaf)]
    assert (f"  plan#3 is no longer blocked by plan#1: plan#{leaf} owns it"
            in capsys.readouterr().out)


def test_a_split_cut_short_between_a_findings_writes_is_finished_by_the_same_command(
        code, tmp_path):
    """The unlink from the split step fails: the finding has two owners (fails closed) and the
    leaf stays marked; run again, the command removes only the split step's link, then the
    mark."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3, "finding", blocked_by=(1,))
    FailOnce(tracker, "unblock")
    assert run(tracker, code, *leaf_filing(tmp_path, "Leaf")) == 2
    leaf = max(tracker.cards_by_number)
    assert tracker.cards_by_number[3].blocked_by == (1, leaf)
    assert FILING in tracker.cards_by_number[leaf].labels
    del tracker.writes[:]
    assert run(tracker, code, *leaf_filing(tmp_path, "Leaf")) == 0
    hand = [write for write in tracker.writes if write[0] in ("block", "unblock", "unmark")]
    assert hand == [("unblock", 3, 1), ("unmark", leaf)]
    assert tracker.cards_by_number[3].blocked_by == (leaf,)
