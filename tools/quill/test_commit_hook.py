"""The commit hook (ruling plan#1872, ``commit_hook``): each commit on ``card/N`` stamped with
the task in progress, refused while none is, and a merge, a detached ``HEAD`` and any other
branch left alone.  Its rule over plain inputs (:func:`commit_hook.judge`); its reads over the
``code`` fixture's real git, a claim and its starts made by ``quill`` over the in-memory
tracker; and ``git commit`` itself, run through pre-commit with this repository's own entry
for the hook, at both message stages, from a shell whose ``python`` is not the checkout's.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml

from tools.ci.arcs import REPO
from tools.ci.gitcmd import run as _git_run
from tools.ci.scratch import run as _run
from tools.quill import _git, commit_hook
from tools.quill._exits import Refused
from tools.quill._fake import begun, claim, run, two_tasks
from tools.quill.commit_hook import judge

#: Card 1's branch, as ``quill claim plan#1`` checks it out.
BRANCH = "card/1"
#: What ``git commit -v`` leaves in the message file before the editor opens (measured
#: 2026-10-27, git 2.56): comments, git's cut line, and the diff below it.
VERBOSE = ("\n# Please enter the commit message for your changes. Lines starting\n"
           "# with '#' will be ignored, and an empty message aborts the commit.\n#\n"
           "# ------------------------ >8 ------------------------\n"
           "# Do not modify or remove the line above.\n"
           "# Everything below it will be ignored.\n"
           "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1,2 @@\n a\n+b\n")


def _claimed_with_tasks(code):
    """A tracker holding step 1 and its tasks 2 then 3, step 1 claimed in ``code`` (its
    branch checked out); the tracker."""
    tracker = two_tasks()
    assert run(tracker, code, "claim", "plan#1") == 0
    return tracker


def _judged(code, tmp_path, text):
    """Run the hook over a message file holding ``text``, in ``code``: its exit status and
    what the file holds after."""
    message = tmp_path / "MESSAGE"
    message.write_text(text, encoding="utf-8")
    status = commit_hook.main([str(message)], root=code)
    return status, message.read_text(encoding="utf-8")


def _commit(code, message, *paths):
    """Commit ``paths`` (none: an empty commit) on the branch checked out with ``message``,
    no hook run."""
    if paths:
        _run(code, "add", *paths)
    _run(code, "commit", "--quiet", "--allow-empty", "-m", message)


# -- the rule ----------------------------------------------------------------------

def test_judge_stamps_the_one_task_in_progress_on_a_message_naming_none():
    """A task in progress and a message with text naming none: that task is stamped."""
    assert judge(BRANCH, 2, [], True) == 2


def test_judge_passes_a_message_naming_a_task_in_progress_as_it_is():
    """A message naming the task in progress is left as it is, once named or twice."""
    assert judge(BRANCH, 2, ["plan#2"], True) is None
    assert judge(BRANCH, 3, ["plan#3", " plan#3 "], True) is None


def test_judge_stamps_nothing_on_a_message_whose_editor_is_still_to_open():
    """A message with no text yet is left as it is while a task is in progress:
    ``commit-msg`` judges what the editor leaves."""
    assert judge(BRANCH, 2, [], False) is None


def test_judge_refuses_a_commit_while_no_task_is_in_progress():
    """With no task in progress a commit is refused, with text or none, naming a task or
    not, and the refusal says how to begin one and how to amend a piece's last commit."""
    for values, has_text in (([], True), ([], False), (["plan#2"], True)):
        with pytest.raises(Refused) as refused:
            judge(BRANCH, None, values, has_text)
        assert str(refused.value) == (
            "no task of plan#1 is in progress on card/1, so this commit is no piece's: begin "
            "the next with `python -m tools.quill.quill start plan#T` (`quill show plan#1` "
            "lists its tasks in order), then commit.  To amend the commit that finished a "
            "piece, whose file is at the tip: `git reset --soft HEAD~1`, then commit again")


def test_judge_refuses_a_value_that_names_no_task():
    """A ``Task`` trailer whose value is no task's name is refused, whatever else is named."""
    with pytest.raises(Refused) as refused:
        judge(BRANCH, 2, ["plan#2", "1879"], True)
    assert str(refused.value) == ("its trailer `Task: 1879` names no task: a task is named "
                                  "`Task: plan#<number>`")


def test_judge_refuses_a_message_naming_two_tasks():
    """A commit is one piece's, so two tasks named are refused, one of them in progress."""
    with pytest.raises(Refused) as refused:
        judge(BRANCH, 2, ["plan#3", "plan#2"], True)
    assert str(refused.value) == "its message names plan#2 and plan#3: a commit is one piece's"


def test_judge_refuses_a_message_naming_a_task_not_in_progress():
    """A task named that is not in progress is refused, naming the one that is."""
    with pytest.raises(Refused) as refused:
        judge(BRANCH, 2, ["plan#3"], True)
    assert str(refused.value) == (
        "its message names plan#3, which is not in progress on card/1, and plan#2 is: a commit "
        "is the piece's it builds, begun by `quill start` and not finished")


# -- the hook over git ---------------------------------------------------------------

def test_the_hook_stamps_each_commit_of_a_piece_its_last_too_and_refuses_the_next(
        code, tmp_path, capsys):
    """Begun by ``quill start``, a piece's commits are each stamped with its task, at the end
    of the message's trailers, the commit adding its file too; the commit after that is
    refused, its message left as it was, until the next piece begins."""
    tracker = _claimed_with_tasks(code)
    assert run(tracker, code, "start", "plan#2") == 0
    status, text = _judged(code, tmp_path, "work\n\nCo-Authored-By: A <a@example.invalid>\n")
    assert status == 0
    assert text == "work\n\nCo-Authored-By: A <a@example.invalid>\nTask: plan#2\n"
    _commit(code, text)
    assert run(tracker, code, "task-file", "plan#2") == 0
    _run(code, "add", _git.task_file(2))
    status, text = _judged(code, tmp_path, "piece a's end\n")
    assert (status, text) == (0, "piece a's end\n\nTask: plan#2\n")
    _commit(code, text)
    capsys.readouterr()
    assert _judged(code, tmp_path, "more\n") == (1, "more\n")
    assert ("refused: no task of plan#1 is in progress on card/1, so this commit is no piece's"
            in capsys.readouterr().err)
    assert run(tracker, code, "start", "plan#3") == 0
    assert _judged(code, tmp_path, "more\n") == (0, "more\n\nTask: plan#3\n")


def test_the_hook_refuses_a_message_naming_another_task(code, tmp_path, capsys):
    """A message naming a task not in progress -- the card's next piece -- is refused, left
    as it was."""
    tracker = _claimed_with_tasks(code)
    assert run(tracker, code, "start", "plan#2") == 0
    assert _judged(code, tmp_path, "work\n\nTask: plan#3\n") == (1, "work\n\nTask: plan#3\n")
    assert "refused: its message names plan#3, which is not in progress on card/1, and plan#2 " \
           "is" in capsys.readouterr().err


def test_the_hook_reads_a_message_as_git_keeps_it(code, tmp_path):
    """Comments, and ``-v``'s diff below git's cut line, are no text: such a message is not
    stamped while a task is in progress, and is refused while none is.  A trailer's key is
    matched in any case, as ``_git.stamps`` reads one, and a ``Task:`` line inside the
    body's prose is no trailer."""
    tracker = _claimed_with_tasks(code)
    assert _judged(code, tmp_path, VERBOSE) == (1, VERBOSE)
    assert run(tracker, code, "start", "plan#2") == 0
    assert _judged(code, tmp_path, VERBOSE) == (0, VERBOSE)
    assert _judged(code, tmp_path, "\n# only a comment\n") == (0, "\n# only a comment\n")
    assert _judged(code, tmp_path, "work\n\ntask: plan#2\n") == (0, "work\n\ntask: plan#2\n")
    assert _judged(code, tmp_path, "work\n\ntask: plan#3\n") == (1, "work\n\ntask: plan#3\n")
    prose = "work\n\nThis said\nTask: plan#3 is next, and more words that make it prose\n"
    assert _judged(code, tmp_path, prose) == (0, f"{prose}\nTask: plan#2\n")
    status, text = _judged(code, tmp_path, f"work\n{VERBOSE}")
    assert status == 0
    assert text.startswith("work\n\nTask: plan#2\n")
    assert text.endswith(VERBOSE.split(">8", 1)[1])


def test_a_reverted_piece_is_in_progress_again_only_while_the_cards_file_names_it(
        code, tmp_path):
    """Ruling plan#2009: one task is in progress at most, the one the card's file names.  A
    revert of a finished piece's done file makes it in progress again while the file still
    names it; once the next piece has begun, the file names that one, which the hook stamps,
    and the reverted piece is not started."""
    tracker = _claimed_with_tasks(code)
    assert run(tracker, code, "start", "plan#2") == 0
    assert run(tracker, code, "task-file", "plan#2") == 0
    _commit(code, "piece a's end\n\nTask: plan#2", _git.task_file(2))
    ended = _run(code, "rev-parse", "HEAD")
    _run(code, "rm", "--quiet", _git.task_file(2))
    _commit(code, "Revert piece a's end")
    assert _git.in_progress(code, "HEAD", 1) == 2
    _run(code, "reset", "--quiet", "--hard", ended)
    assert run(tracker, code, "start", "plan#3") == 0
    _run(code, "rm", "--quiet", _git.task_file(2))
    _commit(code, "Revert piece a's end\n\nTask: plan#3")
    assert _git.in_progress(code, "HEAD", 1) == 3
    assert _judged(code, tmp_path, "more\n") == (0, "more\n\nTask: plan#3\n")


def test_another_cards_branch_merged_in_begins_nothing_here(code, tmp_path, capsys):
    """Plan#1991's case: card/1 merges card 5's branch, on which task 55 is begun.  Card 1's
    hook stamps none of card 5's tasks, refusing the commit while no task of card 1 is in
    progress, and ``quill start`` begins card 1's next task, which the hook then stamps."""
    tracker = _claimed_with_tasks(code)
    claim(code, 5)
    begun(code, 5, 55)
    _run(code, "fetch", "--quiet", "origin", "+refs/heads/card/5:refs/remotes/origin/card/5")
    _run(code, "merge", "--quiet", "--no-edit", "refs/remotes/origin/card/5")
    assert _git.in_progress(code, "HEAD", 5) == 55
    assert _git.in_progress(code, "HEAD", 1) is None
    capsys.readouterr()
    assert _judged(code, tmp_path, "work\n") == (1, "work\n")
    assert "refused: no task of plan#1 is in progress on card/1" in capsys.readouterr().err
    assert run(tracker, code, "start", "plan#2") == 0
    assert _judged(code, tmp_path, "work\n") == (0, "work\n\nTask: plan#2\n")


def test_the_hook_leaves_a_merge_a_detached_head_and_another_branch_alone(code, tmp_path):
    """No task in progress and a message naming one that is not: on a branch that is no
    claim, on a detached ``HEAD``, and while a merge is concluded on ``card/1``, the hook
    passes it, as it was."""
    named = "work\n\nTask: plan#9\n"
    assert _git.checked_out(code) == "feat/work"
    assert _judged(code, tmp_path, named) == (0, named)
    _claimed_with_tasks(code)
    claimed = _run(code, "rev-parse", "HEAD")
    _run(code, "checkout", "--quiet", "--detach")
    assert _judged(code, tmp_path, named) == (0, named)
    _run(code, "checkout", "--quiet", "-b", "side")
    (code / "side.txt").write_text("side\n")
    _commit(code, "side", "side.txt")
    _run(code, "checkout", "--quiet", BRANCH)
    assert _run(code, "rev-parse", "HEAD") == claimed
    _run(code, "merge", "--quiet", "--no-ff", "--no-commit", "side")
    assert _judged(code, tmp_path, named) == (0, named)


def test_the_hook_stamps_a_commit_with_no_package_installed(code, tmp_path, monkeypatch):
    """Run as pre-commit runs it, by an interpreter given no installed package (``-S``, so
    neither ``requests`` nor ``cryptography`` can be imported), the hook stamps a commit as
    it does in the venv: it imports the standard library and quill's git reads alone, so no
    package can fail a commit, as a ``python`` lacking ``cryptography`` failed every commit
    while the hook took its refusal from ``_command``, which loads ``_github`` (plan#1990)."""
    tracker = _claimed_with_tasks(code)
    assert run(tracker, code, "start", "plan#2") == 0
    message = tmp_path / "MESSAGE"
    message.write_text("work\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    done = subprocess.run([sys.executable, "-S", "-m", commit_hook.__name__, str(message)],
                          cwd=code, capture_output=True, text=True, check=False)
    assert (done.returncode, done.stderr) == (0, "")
    assert message.read_text(encoding="utf-8") == "work\n\nTask: plan#2\n"


def test_the_hook_needs_one_message_file(code, capsys):
    """Run naming no file, or two, the hook fails (exit 2) and says how it is run: pre-commit
    always names the one file, so neither is skipped."""
    assert commit_hook.main([], root=code) == 2
    assert commit_hook.main(["a", "b"], root=code) == 2
    assert ("usage: python -m tools.quill.commit_hook <message file> (pre-commit names the one "
            "file at prepare-commit-msg and commit-msg); given ['a', 'b']"
            ) in capsys.readouterr().err


# -- git commit through pre-commit ------------------------------------------------------

def _through_pre_commit(code, tmp_path, monkeypatch):
    """Arm ``code`` with this repository's own entry for the hook: a configuration holding
    ``.pre-commit-config.yaml``'s top-level keys, its hook ``quill-task``, and a hook naming no
    stage that always fails, committed on the branch checked out; and the shim
    ``pre-commit install`` writes for each message stage, in a hooks directory of the test's.
    ``code``'s own interpreter, ``.venv/bin/python``, runs this test's, as a lane's ``.venv``
    links the main checkout's, while the ``python`` first on ``PATH`` fails every run, as the
    host's did from a shell without the venv active (plan#1990).  The working directory's
    ``tools`` is put on the path the hook's ``python -m`` reads.  The command running ``git``
    with those hooks; ``git`` started through ``tools.ci.gitcmd``, bound to ``code`` alone."""
    config = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"]
                if hook["id"] == "quill-task")
    never = {"id": "never", "name": "never", "entry": "no stage named", "language": "fail"}
    armed = {key: config[key] for key in ("default_install_hook_types", "default_stages")}
    armed["repos"] = [{"repo": "local", "hooks": [hook, never]}]
    (code / ".pre-commit-config.yaml").write_text(yaml.safe_dump(armed), encoding="utf-8")
    _commit(code, "arm\n\nTask: plan#2", ".pre-commit-config.yaml")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for stage in ("prepare-commit-msg", "commit-msg"):
        shim = hooks / stage
        shim.write_text(f'#!/usr/bin/env bash\nexec {sys.executable} -mpre_commit hook-impl '
                        f'--config=.pre-commit-config.yaml --hook-type={stage} '
                        f'--hook-dir "$(dirname "$0")" -- "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    own = code / ".venv" / "bin"
    own.mkdir(parents=True)
    host = tmp_path / "host"
    host.mkdir()
    for python, run_by in ((own, f'exec {sys.executable} "$@"'), (host, "exit 97")):
        (python / "python").write_text(f"#!/bin/sh\n{run_by}\n", encoding="utf-8")
        (python / "python").chmod(0o755)
    monkeypatch.setenv("PATH", f"{host}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "pre-commit-home"))
    monkeypatch.delenv("SKIP", raising=False)

    def commit(*args: str) -> subprocess.CompletedProcess[str]:
        return _git_run(code, "-c", f"core.hooksPath={hooks}", "commit", "--allow-empty", *args)
    return commit


def test_git_commit_through_pre_commit_stamps_refuses_and_keeps_an_empty_message_an_abort(
        code, tmp_path, monkeypatch):
    """Through pre-commit, at both message stages and with this repository's entry: a commit
    given its message is stamped, under ``--no-verify`` too (``prepare-commit-msg`` runs);
    one whose editor writes it is stamped once it closes (``commit-msg``); one whose editor
    leaves it empty is aborted as git aborts it, no commit made; and once the piece's file is
    committed the next commit is refused, under ``--no-verify`` too, none made, its refusal
    on git's stderr, where git sends a hook's output, and so is an amend of that last commit,
    which ``git reset --soft HEAD~1`` and a commit make again, stamped.  The hook that names
    no stage never runs at either, since it would fail every commit."""
    tracker = _claimed_with_tasks(code)
    assert run(tracker, code, "start", "plan#2") == 0
    commit = _through_pre_commit(code, tmp_path, monkeypatch)
    assert commit("--no-verify", "-m", "given").returncode == 0
    assert _run(code, "log", "-1", "--format=%B") == "given\n\nTask: plan#2"
    before = _run(code, "rev-parse", "HEAD")
    monkeypatch.setenv("GIT_EDITOR", "true")
    done = commit()
    assert done.returncode == 1
    assert "Aborting commit due to empty commit message." in done.stderr
    assert _run(code, "rev-parse", "HEAD") == before
    monkeypatch.setenv("GIT_EDITOR", "sed -i 1s/^/edited/")
    assert commit().returncode == 0
    assert _run(code, "log", "-1", "--format=%B") == "edited\n\nTask: plan#2"
    assert run(tracker, code, "task-file", "plan#2") == 0
    _run(code, "add", _git.task_file(2))
    assert commit("-m", "piece a's end").returncode == 0
    assert _run(code, "log", "-1", "--format=%B") == "piece a's end\n\nTask: plan#2"
    ended = _run(code, "rev-parse", "HEAD")
    done = commit("--no-verify", "-m", "after")
    assert done.returncode == 1
    assert "refused: no task of plan#1 is in progress on card/1" in done.stderr
    assert _run(code, "rev-parse", "HEAD") == ended
    assert commit("--amend", "-m", "piece a's end, amended").returncode == 1
    assert _run(code, "rev-parse", "HEAD") == ended
    _run(code, "reset", "--quiet", "--soft", "HEAD~1")
    assert commit("-m", "piece a's end, again").returncode == 0
    assert _run(code, "log", "-1", "--format=%B") == "piece a's end, again\n\nTask: plan#2"
    assert _run(code, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == (
        _git.task_file(2))
