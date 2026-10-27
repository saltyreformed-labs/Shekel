"""The ``quill start`` command (ruling plan#1872): a task begun by an empty commit naming it on
its card's branch, pushed, and in progress until the commit writing its file; and ``show``
saying which task is in progress.  Over the in-memory tracker (:class:`_fake.FakeTracker`) and
the ``code`` fixture's real git, every commit, push and read made as git makes it.
"""
from __future__ import annotations

from tools.ci.scratch import run as _run
from tools.quill import _git
from tools.quill._fake import FakeTracker, claim, named, piece, run

#: Card 1's branch, as ``quill claim plan#1`` checks it out.
BRANCH = "card/1"


def _card_with_two_tasks():
    """A tracker holding step 1 and its two tasks, 2 ("piece a") then 3 ("piece b")."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1, title="piece a")
    tracker.add_task(3, 1, title="piece b")
    return tracker


def _tip(code, ref=f"refs/heads/{BRANCH}"):
    """The commit ``ref`` names in ``code``."""
    return _run(code, "rev-parse", ref)


def _on_origin(code):
    """The commit ``origin``'s ``card/1`` names, asked of ``origin``."""
    return _run(code, "ls-remote", "origin", f"refs/heads/{BRANCH}").partition("\t")[0]


def _commit_its_file(tracker, code, task):
    """Write ``task``'s file with ``quill task-file`` and commit it, naming it, on the branch
    checked out, as the commit ending its piece does."""
    assert run(tracker, code, "task-file", f"plan#{task}") == 0
    _run(code, "add", _git.task_file(task))
    _run(code, "commit", "--quiet", "-m", f"piece's end\n\nTask: plan#{task}")


def test_start_begins_a_piece_with_one_empty_commit_naming_it_and_pushes_it(code, capsys):
    """One commit on the branch's tip, ``start plan#T: <title>`` with the trailer naming T,
    holding the tip's own tree; the branch pushed; the index and working tree left as they
    were; nothing written to the tracker; and the task in progress."""
    tracker = _card_with_two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    claimed = _tip(code)
    (code / "staged.txt").write_text("work\n")
    _run(code, "add", "staged.txt")
    capsys.readouterr()
    assert run(tracker, code, "start", "plan#2") == 0
    begun = _tip(code)
    assert _run(code, "rev-parse", f"{begun}^") == claimed
    assert _run(code, "log", "-1", "--format=%B", begun) == "start plan#2: piece a\n\nTask: plan#2"
    assert _run(code, "rev-parse", f"{begun}^{{tree}}") == _run(code, "rev-parse",
                                                                 f"{claimed}^{{tree}}")
    assert _on_origin(code) == begun
    assert _run(code, "diff", "--cached", "--name-only") == "staged.txt"
    assert not tracker.writes
    assert _git.in_progress(code, "HEAD") == {2}
    assert (f"started plan#2 [task, no arc] piece a: {begun[:12]} on card/1, pushed; each commit "
            "until the one writing its file (`quill task-file plan#2`) is a commit of this piece"
            ) in capsys.readouterr().out


def test_start_run_again_for_a_task_begun_pushes_and_commits_nothing(code, capsys):
    """A start whose push failed is finished by the same command: the branch, a work commit on
    it unpushed, is pushed, and no second start is made."""
    tracker = _card_with_two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "start", "plan#2") == 0
    _run(code, "commit", "--quiet", "--allow-empty", "-m", "work\n\nTask: plan#2")
    work = _tip(code)
    capsys.readouterr()
    assert run(tracker, code, "start", "plan#2") == 0
    assert _tip(code) == work == _on_origin(code)
    assert ("plan#2 [task, no arc] piece a is begun on card/1 already: pushed card/1"
            in capsys.readouterr().out)


def test_start_refuses_a_second_task_until_the_first_ends_and_a_task_done(code, capsys):
    """One piece at a time: a second task is refused while the first is in progress; once the
    commit writing the first's file ends it, the second begins, and the first, done, is
    refused.  A refusal writes nothing."""
    tracker = _card_with_two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "start", "plan#2") == 0
    begun = _tip(code)
    assert run(tracker, code, "start", "plan#3") == 1
    assert ("refused: plan#2 is in progress on card/1: finish it first, its file (`quill "
            "task-file plan#2`) in the commit that ends it, then start plan#3"
            ) in capsys.readouterr().err
    assert _tip(code) == begun
    _commit_its_file(tracker, code, 2)
    assert _git.in_progress(code, "HEAD") == frozenset()
    assert run(tracker, code, "start", "plan#3") == 0
    assert _git.in_progress(code, "HEAD") == {3}
    ended = _tip(code)
    capsys.readouterr()
    assert run(tracker, code, "start", "plan#2") == 1
    assert ("refused: plan#2 [task, no arc] piece a is done: card/1 holds plan/tasks/2"
            in capsys.readouterr().err)
    assert _tip(code) == ended
    assert not tracker.writes


def test_start_refuses_what_task_file_refuses(code, capsys):
    """A card that is no task, a task dropped, and any branch but its card's checked out: the
    refusals ``task-file`` makes, each in start's words."""
    tracker = _card_with_two_tasks()
    tracker.add_task(4, 1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "start", "plan#2") == 1
    assert ("plan#2 [task, no arc] piece a is a piece of plan#1, so its start is written on "
            "card/1, and feat/work is checked out: switch to card/1") in capsys.readouterr().err
    assert run(tracker, code, "start", "plan#1") == 1
    assert ("plan#1 [step, balance] card 1 is no task: its start is written for a task"
            in capsys.readouterr().err)
    assert run(tracker, code, "start", "plan#4") == 1
    assert ("was dropped (closed as NOT_PLANNED): its start would begin a dropped piece"
            in capsys.readouterr().err)
    assert _run(code, "ls-remote", "origin", "refs/heads/card/*") == ""


def test_start_refuses_a_claim_origin_no_longer_holds(code, capsys):
    """A start moves the claim forward and never makes it: with ``origin``'s ``card/1`` gone
    (released elsewhere), nothing is committed or pushed."""
    tracker = _card_with_two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    claimed = _tip(code)
    _run(code, "push", "--quiet", "origin", f":refs/heads/{BRANCH}")
    assert run(tracker, code, "start", "plan#2") == 1
    assert ("refused: origin holds no card/1, so plan#1 is claimed by no one: `quill claim "
            "plan#1` claims it") in capsys.readouterr().err
    assert _tip(code) == claimed
    assert _on_origin(code) == ""


def test_a_start_whose_push_is_refused_stands_on_the_local_branch(code, capsys):
    """``origin``'s branch holding work this one does not, the push is refused, never forced:
    the start commit stands on the local branch, and the failure says so."""
    tracker = _card_with_two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    elsewhere_tip = piece(code, 1, 9)
    assert run(tracker, code, "start", "plan#2") == 2
    err = capsys.readouterr().err
    assert "failed: the push of card/1 answered ! " in err
    assert ("the commits stand on the local branch; bring in what origin's holds, then push it"
            in err)
    assert _on_origin(code) == elsewhere_tip
    assert _run(code, "log", "-1", "--format=%s", BRANCH) == "start plan#2: piece a"


def test_show_names_the_task_in_progress(code, capsys):
    """A card lists its tasks in its order and says which is in progress; the task says so of
    itself.  A task a commit on another card's branch names is in progress nowhere."""
    tracker = _card_with_two_tasks()
    tracker.add(5)
    claim(code, 5)
    named(code, 5, 3)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "start", "plan#2") == 0
    capsys.readouterr()
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert ("\n  child: plan#2 (task, open; done (git): no; in progress (git): yes) piece a\n"
            "  child: plan#3 (task, open; done (git): no) piece b\n") in out
    assert run(tracker, code, "show", "plan#2") == 0
    assert "\n  done (git): no\n  in progress (git): yes\n" in capsys.readouterr().out
    assert run(tracker, code, "show", "plan#3") == 0
    assert "\n  in progress (git): no\n" in capsys.readouterr().out
