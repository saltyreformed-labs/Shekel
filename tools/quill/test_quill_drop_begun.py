"""``quill drop`` of a task begun (ruling plan#1900): the drop recorded in git on its card's
branch before the tracker's close, so the piece is in progress no longer and the next
begins; refused, writing nothing, off that branch; finished by the same command after a
push it cut short; and a task closed on the web while begun says how its drop is recorded.
Over the in-memory tracker (:class:`_fake.FakeTracker`) and the ``code`` fixture's real git.
"""
from __future__ import annotations

from tools.ci.scratch import run as _run
from tools.quill import _git, commit_hook
from tools.quill._fake import piece, run, two_tasks

#: Card 1's branch, as ``quill claim plan#1`` checks it out.
BRANCH = "card/1"


def _begun(code):
    """Step 1 claimed in ``code`` with its tasks 2 ("piece a") and 3 ("piece b"), and 2
    begun; the tracker."""
    tracker = two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "start", "plan#2") == 0
    return tracker


def _tip(code, ref=f"refs/heads/{BRANCH}"):
    """The commit ``ref`` names in ``code``."""
    return _run(code, "rev-parse", ref)


def _on_origin(code):
    """The commit ``origin``'s ``card/1`` names, asked of ``origin``."""
    return _run(code, "ls-remote", "origin", f"refs/heads/{BRANCH}").partition("\t")[0]


def test_a_begun_task_dropped_is_dropped_in_git_first_and_the_next_begins(code, tmp_path,
                                                                           capsys):
    """The drop is one empty commit on the branch, naming the task and recording its drop,
    saying why, pushed, before the tracker's comment and close; the task is in progress no
    longer, the next starts, and the commit hook stamps the next; reopened and started
    again, the dropped one is in progress again."""
    tracker = _begun(code)
    begun = _tip(code)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "split differently") == 0
    dropped = _tip(code)
    assert _run(code, "rev-parse", f"{dropped}^") == begun
    assert _run(code, "log", "-1", "--format=%B", dropped) == (
        "drop plan#2: piece a\n\nsplit differently\n\nTask: plan#2\nDropped: plan#2")
    assert _run(code, "rev-parse", f"{dropped}^{{tree}}") == _run(code, "rev-parse",
                                                                   f"{begun}^{{tree}}")
    assert _on_origin(code) == dropped
    assert tracker.writes[len(writes):] == [("comment", 2, "Dropped: split differently"),
                                            ("close", 2, "not_planned")]
    out = capsys.readouterr().out
    assert (f"  recorded its drop on card/1: {dropped[:12]}, pushed; it is in progress no "
            "longer (ruling plan#1900)\n") in out
    assert out.index("recorded its drop") < out.index("dropped plan#2")
    assert _git.in_progress(code, "HEAD") == frozenset()
    assert run(tracker, code, "start", "plan#3") == 0
    message = tmp_path / "MESSAGE"
    message.write_text("work\n")
    assert commit_hook.main([str(message)], root=code) == 0
    assert message.read_text() == "work\n\nTask: plan#3\n"
    tracker.reopen(2)
    assert run(tracker, code, "task-file", "plan#3") == 0
    _run(code, "add", _git.task_file(3))
    _run(code, "commit", "--quiet", "-m", "piece b's end\n\nTask: plan#3")
    assert run(tracker, code, "start", "plan#2") == 0
    assert _git.in_progress(code, "HEAD") == {2}


def test_a_task_never_begun_is_dropped_with_nothing_written_to_git(code):
    """A task the claim never began is dropped on the tracker alone, as before the ruling."""
    tracker = _begun(code)
    tip = _tip(code)
    assert run(tracker, code, "drop", "plan#3", "--why", "not needed") == 0
    assert _tip(code) == tip == _on_origin(code)
    assert tracker.cards([3])[3].state_reason == "NOT_PLANNED"
    assert _git.in_progress(code, "HEAD") == {2}


def test_a_begun_task_dropped_off_its_branch_is_refused_writing_nothing(code, capsys):
    """Its drop is recorded on its card's branch, so with another checked out the drop is
    refused before any write, git's or the tracker's."""
    tracker = _begun(code)
    tip = _tip(code)
    _run(code, "checkout", "--quiet", "feat/work")
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "split differently") == 1
    assert ("refused: plan#2 [task, no arc] piece a is a piece of plan#1, so its drop is "
            "written on card/1, and feat/work is checked out: switch to card/1"
            ) in capsys.readouterr().err
    assert tracker.writes == writes
    assert _tip(code) == tip == _on_origin(code)


def test_a_drop_whose_push_failed_is_finished_by_the_same_command(code, capsys):
    """``origin``'s branch holding work this one does not, the drop's push is refused and
    nothing is written to the tracker; once that work is brought in, the same command pushes
    the drop recorded already, commits nothing more, and drops the task."""
    tracker = _begun(code)
    piece(code, 1, 9)
    writes = list(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "split differently") == 2
    assert "failed: the push of card/1 answered ! " in capsys.readouterr().err
    assert tracker.writes == writes
    assert _run(code, "log", "-1", "--format=%s", BRANCH) == "drop plan#2: piece a"
    _run(code, "fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    _run(code, "merge", "--quiet", "--no-edit", f"refs/remotes/origin/{BRANCH}")
    merged = _tip(code)
    assert run(tracker, code, "drop", "plan#2", "--why", "split differently") == 0
    assert "  its drop is recorded on card/1 already: pushed card/1\n" in capsys.readouterr().out
    assert _tip(code) == merged == _on_origin(code)
    assert tracker.writes[len(writes):] == [("comment", 2, "Dropped: split differently"),
                                            ("close", 2, "not_planned")]


def test_a_task_closed_on_the_web_while_begun_says_how_its_drop_is_recorded(code, capsys):
    """Closed outside ``quill drop`` while begun, the task is still in progress in git: its
    drop is refused as closed, saying how the drop is recorded, and the next start's refusal
    names the drop as the other way on."""
    tracker = _begun(code)
    tracker.close(2, "not_planned")
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "split differently") == 1
    assert ("refused: plan#2 [task, no arc] piece a is already closed, and git still says it is "
            "in progress on card/1: reopen it on the web, then drop it here with card/1 checked "
            "out, which records its drop in git (ruling plan#1900)") in capsys.readouterr().err
    assert run(tracker, code, "start", "plan#3") == 1
    assert ("then start plan#3; or drop it (`quill drop plan#2 --why REASON`), which records "
            "its drop on card/1") in capsys.readouterr().err
