"""The ``quill`` command's tasks (ruling ``balance:R-BAL386``): ``file task``, ``task-file``,
and what ``sync``, ``drop``, ``show``, ``next``, ``claim`` and ``edit`` do with a task, over
the in-memory tracker (:class:`_fake.FakeTracker`) and the ``code`` fixture's real git.

A task is a piece of one step or finding, a commit inside its card's one pull request, done
when its file ``plan/tasks/N`` is in the merge of ``dev`` and ``main`` or at the tip of its
card's live claim; its state on the tracker is the tool's display of that.
"""
from __future__ import annotations

import re

from tools.ci.scratch import run as _run
from tools.quill._fake import (
    FailOnce,
    FakeTracker,
    claim,
    finish,
    piece,
    run,
    ship,
    unfinish,
    unpiece,
)
from tools.quill._tracker import Child


def _task_filing(tmp_path, parent="plan#1", title="4d-4a"):
    """``quill file task`` filing the task ``title`` under ``parent``, its text in a file."""
    piece_file = tmp_path / f"{title}.md"
    piece_file.write_text(f"Build {title}.")
    return ("file", "task", "--parent", parent, "--title", title, "--body-file", str(piece_file))


# -- file task ----------------------------------------------------------------------------

def test_file_task_files_a_sub_issue_of_its_card_with_no_arc_and_off_the_board(code, tmp_path,
                                                                                capsys):
    """R-BAL386: created marked, linked under its card, unmarked last (R-BAL202); no arc label
    (its card's), no board write; and the same command run again files nothing over it."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *_task_filing(tmp_path)) == 0
    assert tracker.writes == [("create", 2, "task", "4d-4a", ("filing",)), ("add_child", 1, 2),
                              ("unmark", 2)]
    assert tracker.board.items == [1]
    capsys.readouterr()
    assert run(tracker, code, *_task_filing(tmp_path)) == 0
    assert len(tracker.writes) == 3
    assert "plan#2 [task, no arc] 4d-4a is filed already" in capsys.readouterr().out


def test_a_task_filing_cut_short_is_finished_by_the_same_command(code, tmp_path, capsys):
    """R-BAL186: a link that failed is made by the next run, which files no second task."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, "add_child")
    assert run(tracker, code, *_task_filing(tmp_path)) == 2
    assert run(tracker, code, *_task_filing(tmp_path)) == 0
    assert "finishing plan#2 [task, no arc] 4d-4a" in capsys.readouterr().out
    assert tracker.writes == [("create", 2, "task", "4d-4a", ("filing",)), ("add_child", 1, 2),
                              ("unmark", 2)]


def test_file_task_refuses_a_parent_that_is_no_live_piece_of_work(code, tmp_path, capsys):
    """A task's card is work still to do: never a step split into smaller steps, work that
    shipped, a ruling, nor a card whose own filing has not finished; nothing is written."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),))
    tracker.add(2, parent=1)
    tracker.add(3)
    tracker.add(4, "ruling", is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(5, labels=("balance", "filing"))
    ship(code, 3)
    said = {
        "plan#1": "plan#1 [step, balance] card 1 is split into smaller steps (R-BAL177): a task "
                  "is a piece of one of them, so file it under that one",
        "plan#3": "a task is a sub-issue of a LIVE step or finding, the piece of work it is part "
                  "of (R-BAL386); its parent is a step that is done",
        "plan#4": "its parent is ruling",
        "plan#5": "plan#5 [step, balance] card 5's own filing has not finished (R-BAL202)",
    }
    for parent, text in said.items():
        assert run(tracker, code, *_task_filing(tmp_path, parent)) == 1
        assert text in capsys.readouterr().err
    assert not tracker.writes


def test_a_leaf_is_never_filed_under_a_step_with_a_task_still_open(code, tmp_path, capsys):
    """A split step is never shipped itself (R-BAL177), so no task under it could be done: the
    split waits until each open task is dropped; a closed one does not hold it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(3, 1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    spec = tmp_path / "leaf.md"
    spec.write_text("Build the leaf.")
    leaf = ("file", "step", "--parent", "plan#1", "--arc", "balance", "--title", "leaf",
            "--body-file", str(spec))
    assert run(tracker, code, *leaf) == 1
    assert ("plan#1 has tasks still open (plan#2): a step split into smaller steps is never "
            "shipped itself (R-BAL177)") in capsys.readouterr().err
    assert not tracker.writes
    assert run(tracker, code, "drop", "plan#2", "--why", "folded into the leaf") == 0
    assert run(tracker, code, *leaf) == 0


# -- task-file ----------------------------------------------------------------------------

def test_task_file_writes_the_file_on_its_cards_branch_alone(code, capsys):
    """The file is written into the working tree only while ``card/<its card>`` is checked out,
    ``done <instant>``, for the session to commit; nothing is written to the tracker."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    assert run(tracker, code, "task-file", "plan#2") == 1
    assert ("is a piece of plan#1, so its file is written on card/1, and feat/work is checked "
            "out: switch to card/1") in capsys.readouterr().err
    assert not (code / "plan/tasks/2").exists()
    assert run(tracker, code, "claim", "plan#1") == 0
    writes = list(tracker.writes)
    assert run(tracker, code, "task-file", "plan#2") == 0
    assert re.fullmatch(r"done \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n",
                        (code / "plan/tasks/2").read_text())
    assert tracker.writes == writes
    _run(code, "checkout", "--quiet", "--detach")
    assert run(tracker, code, "task-file", "plan#2") == 1
    assert "and no branch is checked out" in capsys.readouterr().err


def test_task_file_refuses_a_card_that_is_no_task_and_a_task_dropped(code, capsys):
    """A step's number names no task; a dropped task's file would mark a dropped piece done."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "task-file", "plan#1") == 1
    assert "plan#1 [step, balance] card 1 is no task" in capsys.readouterr().err
    assert run(tracker, code, "task-file", "plan#2") == 1
    assert "was dropped (closed as NOT_PLANNED)" in capsys.readouterr().err


# -- sync ---------------------------------------------------------------------------------

def test_sync_closes_a_task_its_cards_claim_holds_and_reopens_it_when_it_is_gone(code):
    """Done at the tip of its card's live claim; reopened by the next sync once the branch
    loses the file -- a revert on it -- or the claim is released, keeping its work."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    claim(code, 1)
    piece(code, 1, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == [("close", 2, "completed")]
    unpiece(code, 1, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[1:] == [("reopen", 2)]
    piece(code, 1, 2)
    assert run(tracker, code, "sync") == 0
    assert run(tracker, code, "release", "plan#1") == 0
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[2:] == [("close", 2, "completed"), ("reopen", 2)]


def test_a_revert_of_a_tasks_file_on_dev_reopens_it_and_says_it_is_stale(code, capsys):
    """R-BAL386's "a revert reopens it": closed once dev holds its file, reopened once a revert
    deletes it -- and, its card having shipped without it, reported, never closed."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    ship(code, 1)
    finish(code, 2)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == [("close", 1, "completed"), ("close", 2, "completed")]
    unfinish(code, 2)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 1
    assert tracker.writes[2:] == [("reopen", 2)]
    assert ("REPORT: task plan#2 is open under plan#1, which shipped without plan/tasks/2, so "
            "it is never done: drop it (`quill drop plan#2`)") in capsys.readouterr().out


def test_a_task_file_on_another_cards_branch_marks_nothing_done(code, capsys):
    """Only the tracker says which card a task is under, and it is done on that card's branch
    alone: on another card's claim its file is reported, and the task stays open."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add_task(2, 1)
    claim(code, 3)
    piece(code, 3, 2)
    assert run(tracker, code, "sync") == 1
    assert not tracker.writes
    assert ("REPORT: card/3's tip holds plan/tasks/2, but task plan#2 is under plan#1, not "
            "plan#3, so it marks nothing done") in capsys.readouterr().out


def test_a_claim_cut_after_a_task_landed_on_dev_takes_no_task_file_for_its_own(code, capsys):
    """T1's review, its H1: ``quill claim`` cuts ``card/N`` from dev, so its tip holds every
    task file dev holds; none is the claim's own, so sync names none of them as on the wrong
    branch (whose remedy, taking the file off, would land as a delete of a done task's file)
    and leaves the task done."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add(3)
    ship(code, 1)
    finish(code, 2)
    assert run(tracker, code, "sync") == 0
    claim(code, 3)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert "REPORT" not in capsys.readouterr().out
    assert tracker.writes == [("close", 1, "completed"), ("close", 2, "completed")]

def test_sync_prints_a_task_file_on_dev_naming_no_task_as_history(code, capsys):
    """A file on dev naming a step, or a number nobody filed, is history (R-BAL184): it never
    fails sync, and the step is not closed by it."""
    tracker = FakeTracker()
    tracker.add(1)
    finish(code, 1, 77)
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert ("HISTORY: the task file plan/tasks/1 on origin/dev or origin/main marks nothing done "
            "(plan#1 is a step, no task)") in out
    assert "(plan#77 does not exist)" in out
    assert not tracker.writes


# -- drop ---------------------------------------------------------------------------------

def test_dropping_a_card_drops_its_tasks_still_to_do_first(code, capsys):
    """R-BAL190's shape: each task under it that is open, or shown done by the tool on a branch
    the drop abandons, is dropped with the reason before the card is; a person's close is
    left; one whose file is on dev is named and left, as no drop undoes it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(3, 1, is_open=False, state_reason="COMPLETED", touched_by_hand=True)
    tracker.add_task(4, 1)
    tracker.add_task(5, 1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    finish(code, 4)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert tracker.writes == [
        ("comment", 2, "Dropped: superseded"), ("close", 2, "not_planned"),
        ("comment", 5, "Dropped: superseded"), ("close", 5, "not_planned"),
        ("comment", 1, "Dropped: superseded"), ("close", 1, "not_planned")]
    assert ("task plan#4 is done in git (plan/tasks/4 is on origin/dev or origin/main), so it "
            "is not dropped") in capsys.readouterr().out


def test_a_task_is_dropped_alone_unless_dev_or_main_holds_its_file(code, capsys):
    """A task's own drop is a comment and a close; one done on dev is undone by a revert."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(3, 1)
    finish(code, 3)
    assert run(tracker, code, "drop", "plan#3", "--why", "x") == 1
    assert ("plan#3 [task, no arc] card 3 is done in git (plan/tasks/3 is on origin/dev or "
            "origin/main): undo it by reverting the commit that wrote that file, not by a drop"
            ) in capsys.readouterr().err
    assert run(tracker, code, "drop", "plan#2", "--why", "not needed") == 0
    assert tracker.writes == [("comment", 2, "Dropped: not needed"), ("close", 2, "not_planned")]


def test_sync_leaves_a_task_the_tool_dropped(code):
    """A task ``quill drop`` closed as not planned is a decision, not the tool's display of git:
    sync, reading it under its open card, never reopens it, though git says it is not done."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    assert run(tracker, code, "drop", "plan#2", "--why", "not needed") == 0
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes

# -- show, next, claim, edit ----------------------------------------------------------------

def test_show_names_each_task_and_whether_git_says_it_is_done(code, capsys):
    """A card lists each task under it with its state and git's answer; a task says whether it
    is done, in place of whether it shipped."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1, title="piece a")
    tracker.add_task(3, 1, title="piece b")
    claim(code, 1)
    piece(code, 1, 3)
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert "\n  child: plan#2 (task, open; done (git): no) piece a\n" in out
    assert "\n  child: plan#3 (task, open; done (git): yes) piece b\n" in out
    assert "\n  shipped (git): no\n" in out
    assert run(tracker, code, "show", "plan#3") == 0
    out = capsys.readouterr().out
    assert "\n  done (git): yes\n" in out and "shipped (git)" not in out
    assert "\n  parent: plan#1\n" in out


def test_a_task_is_never_offered_claimed_or_moved(code, capsys):
    """``next`` offers steps alone, and a task is no work a branch ships, nor on the board."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    tracker.add_task(2, 1)
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing\n")
    assert run(tracker, code, "claim", "plan#2") == 1
    assert "is not work a branch ships" in capsys.readouterr().err
    assert run(tracker, code, "move", "plan#2", "--top") == 1
    assert not tracker.writes


def test_edit_rewrites_a_task_held_to_a_tasks_rules(code, capsys):
    """A task carries no arc, so ``edit`` grades it by a task's rules, not a card's: retitled,
    and refused a title past the cap."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    assert run(tracker, code, "edit", "plan#2", "--title", "4d-4a: renamed") == 0
    assert tracker.writes == [("retitle", 2, "4d-4a: renamed")]
    assert run(tracker, code, "edit", "plan#2", "--title", "t" * 101) == 1
    assert capsys.readouterr().err == ("refused: not edited:\n  its title is a short name of at "
                                       "most 100 characters; it is 101\n")
