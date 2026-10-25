"""No leaf is filed under a claimed step, X-cx L2's leaf C3 (C2 review M4, an application of
ruling ``balance:R-BAL207``): a claim says a branch is building the step as one piece of work,
and a step split into smaller steps is never shipped itself (R-BAL177), so ``quill file step
--parent`` refuses -- a first run, and a run that finishes an earlier filing -- naming the
claim and its ``quill release``; and ``sync`` reports a claim the refusal could not prevent.
A claim is the branch ``card/N`` on the ``code`` fixture's real ``origin`` (ruling
``balance:R-BAL359``).  Over :class:`_fake.FakeTracker` and a throwaway git repository (the
``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tools.ci.scratch import commit as _commit
from tools.ci.scratch import run as _run
from tools.ci.trailers import DEV, tracking
from tools.quill import _git
from tools.quill._fake import FailOnce, FakeTracker, claim, leaf_filing, run, ship
from tools.quill._tracker import Child
from tools.quill.setup_tracker import FILING

_KEEP = ("; or, to keep plan#1 whole, `quill drop plan#2`: its filing never finished, so it "
         "was never part of the split (R-BAL205)")


def _refused(holder):
    """``quill file``'s refusal of a leaf under plan#1 while ``holder`` (:func:`_holder`)
    claims it."""
    return (f"plan#1 is claimed, {holder}: a branch is building it as one piece of work, and a "
            "step split into smaller steps is never shipped itself (R-BAL177), so no leaf is "
            "filed under it while it is claimed.  Release the claim first (`quill release "
            "plan#1`, by the session that holds it, or once its work is abandoned), then run "
            "this again")


def _holder(code, number=1):
    """plan#``number``'s claim as every line names it: ``card/N``, then the line its ship
    marker holds at that branch's tip on ``origin`` (``claimed`` and the instant) and that
    tip's committer date -- each read from git in ``origin`` itself, not through quill, so
    nothing here fetches for the command under test (a claim another clone made, as
    :func:`_fake.claim` makes one, is in ``origin`` and not yet here)."""
    origin = Path(_run(code, "remote", "get-url", "origin"))
    tip = _run(origin, "rev-parse", f"refs/heads/card/{number}")
    marker = _run(origin, "show", f"{tip}:plan/ships/{number}")
    committed = _run(origin, "log", "-1", "--format=%cI", tip)
    return f"card/{number} ({marker}; last commit {committed})"


def _claimed_by_a_race(code, number):
    """A claim on plan#``number`` that `quill claim` would refuse now -- made while a leaf's
    link was landing, or before C3 -- made on ``origin`` as another session's claim is
    (:func:`_fake.claim`); how every line names it (:func:`_holder`)."""
    claim(code, number)
    return _holder(code, number)


def test_a_new_leaf_under_a_claimed_step_is_refused_until_the_claim_is_released(code, tmp_path,
                                                                                 capsys):
    """Nothing is written while a branch holds plan#1; the refusal names the claim and the
    command that releases it.  Released, the same command splits plan#1."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(_holder(code))}\n"
    assert not tracker.writes and set(tracker.cards_by_number) == {1}
    assert run(tracker, code, "release", "plan#1") == 0
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    assert tracker.cards_by_number[1].step_children == (2,) and tracker.board.items == [2]


def test_a_leaf_whose_link_failed_is_not_linked_under_a_step_claimed_since(code, tmp_path,
                                                                           capsys):
    """R-BAL202's link-failed leaf: plan#2 is filed but not linked, so plan#1 is plain work
    and can be claimed.  plan#2's command run again would link it -- the split itself -- so
    it is refused, offering the drop that keeps plan#1 whole; dropped, plan#1 is still
    claimed and on the board."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    assert tracker.cards_by_number[2].parent is None
    assert run(tracker, code, "claim", "plan#1") == 0
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(_holder(code))}{_KEEP}\n"
    assert tracker.writes == writes
    assert run(tracker, code, "drop", "plan#2", "--why", "kept whole") == 0
    assert tracker.writes[len(writes):] == [("comment", 2, "Dropped: kept whole"),
                                            ("close", 2, "not_planned")]
    assert 1 in _git.read(code).claims and 1 in tracker.board.items


def test_a_leaf_reopened_after_sync_unlinked_it_never_splits_the_claimed_step(code, tmp_path,
                                                                              capsys):
    """C2 review M4's probe: a person closes the still-marked leaf, `sync` unlinks it and puts
    plan#1 back on the board, the person reopens the leaf, and plan#1 is claimed.  The
    leaf's command run again linked it under the claimed plan#1 without a word; it is
    refused, and once the claim is released it finishes the split."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=False,
                                         state_reason="COMPLETED", touched_by_hand=True)
    assert run(tracker, code, "sync") == 0
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=True,
                                         state_reason="REOPENED", touched_by_hand=True)
    assert run(tracker, code, "claim", "plan#1") == 0
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(_holder(code))}{_KEEP}\n"
    assert tracker.writes == writes and tracker.cards_by_number[2].parent is None
    assert run(tracker, code, "release", "plan#1") == 0
    writes = list(tracker.writes)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    assert tracker.writes[len(writes):] == [("add_child", 1, 2), ("board_place", 2, "PVTI_1"),
                                            ("board_remove", 1), ("unmark", 2)]


def test_a_linked_leaf_is_not_finished_under_a_claim_made_as_its_link_landed(code, tmp_path,
                                                                             capsys):
    """A claim made in the moment between a filing's read and its link (or before C3)
    leaves plan#1 split while claimed.  Finishing the leaf would complete that split, so
    the re-run is refused and the leaf stays marked, never offered; its drop takes it out
    of the split, and plan#1 is whole again, on the board in its place, still claimed."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    holder = _claimed_by_a_race(code, 1)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(holder)}{_KEEP}\n"
    assert tracker.writes == writes and FILING in tracker.cards_by_number[2].labels
    assert run(tracker, code, "drop", "plan#2", "--why", "kept whole") == 0
    assert tracker.writes[len(writes):] == [
        ("board_add", 1), ("board_place", 1, "PVTI_2"), ("remove_child", 1, 2),
        ("comment", 2, "Dropped: kept whole"), ("close", 2, "not_planned")]
    assert tracker.board.items == [2, 1] and 1 in _git.read(code).claims


def test_a_finished_leaf_run_again_under_a_claim_writes_nothing_and_is_not_refused(
        code, tmp_path, capsys):
    """A run that would write nothing has nothing to refuse: a leaf whose filing finished
    before plan#1 was claimed is filed already."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    _claimed_by_a_race(code, 1)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    assert "is filed already" in capsys.readouterr().out and tracker.writes == writes


def test_the_refusal_offers_no_drop_of_a_shipped_leaf(code, tmp_path, capsys):
    """Work git says shipped is never dropped, so a leaf whose link failed and that shipped
    meanwhile is offered only the release."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    ship(code, 2)
    assert run(tracker, code, "claim", "plan#1") == 0
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(_holder(code))}\n"


def test_a_finding_is_filed_under_a_claimed_step(code):
    """Only a step splits: a finding owned by a claimed step decides nothing about its
    split (R-BAL177; blocked by it, no sub-issue of it, R-BAL395), so it is filed."""
    tracker = FakeTracker()
    tracker.add(1)
    _claimed_by_a_race(code, 1)
    assert run(tracker, code, "file", "finding", "--arc", "balance", "--title", "Off by one",
               "--owner", "plan#1", "--text", "The total is one cent short.") == 0
    finding = tracker.cards_by_number[2]
    assert (finding.kind, finding.parent, finding.blocked_by) == ("finding", None, (1,))


def test_sync_reports_a_claim_on_a_split_step_until_it_is_released(code, tmp_path, capsys):
    """The claim the refusal could not prevent is reported, and nothing is written: a
    split step's claim names who holds it and how to release it; a plain step's claim is
    no report.  Released, the report is gone."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    holder = _claimed_by_a_race(code, 1)
    _claimed_by_a_race(code, 3)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 1
    out = capsys.readouterr().out
    assert out == (f"REPORT: plan#1 is split into smaller steps, but {holder} still claims it: "
                   "a split step is never shipped itself, its smaller steps are the work "
                   "(R-BAL177), so release the claim with `quill release plan#1`\n")
    assert tracker.writes == writes
    assert run(tracker, code, "release", "plan#1") == 0
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert "REPORT" not in capsys.readouterr().out


def test_sync_reaches_a_claimed_split_step_no_listing_holds(code, tmp_path, capsys):
    """plan#1's only leaf was dropped, so `sync` closed plan#1 as not planned (R-BAL187); a
    claim still names it.  Neither listing holds plan#1 or its leaf, and no marker names
    either, so `sync` reads it because it is claimed."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    assert run(tracker, code, "drop", "plan#2", "--why", "not needed") == 0
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "not_planned")
    holder = _claimed_by_a_race(code, 1)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 1
    assert f"REPORT: plan#1 is split into smaller steps, but {holder} still claims it" in (
        capsys.readouterr().out)


def test_sync_names_the_drop_when_every_leaf_of_the_claimed_step_is_still_being_filed(
        code, tmp_path, capsys):
    """Leaf C3 review LOW 2: a claim made as plan#2's link landed (its unmark failed).  The
    REPORT named only the release, which hands the claimed work to the split; every leaf
    of plan#1 is still being filed, so it also names the drop that keeps plan#1 whole, as
    `quill file`'s refusal does -- until plan#2 ships, which no drop undoes."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    holder = _claimed_by_a_race(code, 1)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 1
    assert (f"REPORT: plan#1 is split into smaller steps, but {holder} still claims it: a split "
            "step is never shipped itself, its smaller steps are the work (R-BAL177), so release "
            "the claim with `quill release plan#1`; or, to keep it whole, `quill drop plan#2`: "
            "still being filed, so never part of the split (R-BAL205)\n") in (
        capsys.readouterr().out)
    ship(code, 2)
    assert run(tracker, code, "sync") == 1
    assert "keep it whole" not in capsys.readouterr().out


def test_no_drop_is_offered_to_keep_whole_a_claimed_step_another_leaf_splits(code, tmp_path,
                                                                            capsys):
    """Leaf C3 review LOW 3: plan#1 is split already by its finished leaf plan#2, then
    claimed.  Dropping plan#3, still being filed, would not keep plan#1 whole, so neither
    the refusal of plan#3's re-run nor sync's REPORT offers it."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Second")) == 2
    assert tracker.cards_by_number[3].parent == 1
    holder = _claimed_by_a_race(code, 1)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Second")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(holder)}\n"
    assert run(tracker, code, "sync") == 1
    assert "keep it whole" not in capsys.readouterr().out


def test_a_claim_whose_tip_holds_no_marker_is_named_so_everywhere(code, tmp_path, capsys):
    """Leaf C3 review LOW 7 and LOW 9, on the claim R-BAL359 made a branch: a ``card/1``
    pushed by hand, whose tip holds no ``plan/ships/1``, records no instant it was claimed.
    The refusal, sync's REPORT on a split step and `quill show` each name it the one way,
    with the release that frees it."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    tip = _commit(code, "work", parents=[_run(code, "rev-parse", tracking(DEV))])
    _run(code, "push", "--quiet", "origin", f"{tip}:refs/heads/card/1")
    holder = ("card/1 (its tip holds no plan/ships/1, so when it was claimed is unknown; last "
              f"commit {_run(code, 'log', '-1', '--format=%cI', tip)})")
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Other")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(holder)}\n"
    assert run(tracker, code, "sync") == 1
    out = capsys.readouterr().out
    assert f"REPORT: plan#1 is split into smaller steps, but {holder} still claims it: " in out
    assert "`quill release plan#1`" in out
    assert run(tracker, code, "show", "plan#1") == 0
    assert f"\n  claim: {holder}\n" in capsys.readouterr().out


def test_the_claim_is_refused_before_an_unfinished_sibling_is(code, tmp_path, capsys):
    """Leaf C3 review LOW 9: under a claimed step with a leaf still being filed, a new leaf
    is refused for the claim first: releasing it is the first thing to settle."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    holder = _claimed_by_a_race(code, 1)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Other")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(holder)}\n"


def test_the_drop_that_keeps_a_claimed_step_whole_counts_only_its_leaves(code, tmp_path,
                                                                        capsys):
    """Leaf C3 delta review LOW 3 (D5, D6): plan#3, linked under plan#1 and closed while
    still being filed, is no leaf of it (`sync` has not unlinked it yet), so plan#2 is
    plan#1's only leaf: the refusal of plan#2's re-run and sync's REPORT both offer the
    drop that keeps plan#1 whole."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "unmark")
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 2
    tracker.add(3, parent=1, labels=("balance", FILING), is_open=False,
                state_reason="COMPLETED", touched_by_hand=True)
    held = tracker.cards_by_number[1]
    tracker.cards_by_number[1] = replace(held, children=(*held.children,
                                                         Child(3, "step", False)))
    holder = _claimed_by_a_race(code, 1)
    capsys.readouterr()
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert capsys.readouterr().err == f"refused: {_refused(holder)}{_KEEP}\n"
    assert run(tracker, code, "sync", "--dry-run") == 1
    assert "; or, to keep it whole, `quill drop plan#2`: still being filed" in (
        capsys.readouterr().out)


def test_sync_names_the_drop_of_every_leaf_still_being_filed(code, capsys):
    """Leaf C3 delta review LOW 3 (D9): a claimed step whose two leaves are both still being
    filed (split by hand on the web) is kept whole only by dropping both."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1, labels=("balance", FILING))
    tracker.add(3, parent=1, labels=("balance", FILING))
    _claimed_by_a_race(code, 1)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 1
    assert ("; or, to keep it whole, `quill drop plan#2` and `quill drop plan#3`: still being "
            "filed, so never part of the split (R-BAL205)\n") in capsys.readouterr().out
