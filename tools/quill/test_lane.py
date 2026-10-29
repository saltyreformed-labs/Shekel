"""The lane start (:mod:`tools.quill.lane`), over a throwaway main checkout with a real bare
``origin`` (the ``checkout`` fixture): the worktree it makes, what it leaves the
configuration every worktree shares, what it refuses, and the session it starts.  Nothing
here starts ``claude`` but a stand-in, and nothing calls GitHub.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tools.ci import scratch
from tools.ci.arcs import REPO
from tools.ci.gitcmd import DEV, tracking
from tools.ci.scratch import commit, commit_files
from tools.ci.scratch import run as _run
from tools.quill import lane


@pytest.fixture(name="checkout")
def _checkout(tmp_path):
    """A main checkout ``tmp_path / "Shekel"`` (never a real one) with a ``.venv`` of its
    own and a bare ``origin`` whose ``dev`` has moved one commit past the one the checkout
    last fetched, so only a fetch brings the ``dev`` a lane starts from; the checkout and
    that ``dev``'s sha."""
    main = scratch.repository(tmp_path, "Shekel")
    base = commit_files(main, commit(main, "root"), "base", {"README": "base\n"})
    scratch.with_origin(main, base)
    (main / ".venv").mkdir()
    other = scratch.elsewhere(main)
    moved = commit_files(other, base, "dev moved", {"README": "moved\n"})
    _run(other, "push", "--quiet", "origin", f"{moved}:refs/heads/dev")
    return main, moved


@pytest.fixture(name="launched")
def _launched():
    """What a lane start ran, where, and with which venv, each as ``(worktree, argv,
    venv)``, in place of :func:`lane.start`."""
    return []


def _start(root, launched, *argv):
    """``python -m tools.quill.lane`` with ``argv``, run from ``root``, its launch recorded
    in ``launched``; its exit status."""
    return lane.main(list(argv), root=root,
                     launch=lambda worktree, run, venv: launched.append((worktree, run, venv)))


def _config(main):
    """The configuration every worktree of ``main`` shares, as bytes."""
    return (main / ".git" / "config").read_bytes()


# -- the worktree ------------------------------------------------------------------------------

def test_a_lane_is_a_worktree_detached_at_the_dev_a_fetch_just_brought(checkout, launched,
                                                                       capsys):
    """``shekel-NAME`` beside the main checkout, detached at the ``origin/dev`` the start's
    own fetch brought (the checkout's tracking ref still named the commit before), its tree
    that commit's, its ``.venv`` a link to the main checkout's, and ``claude`` started there
    with the first prompt and that venv."""
    main, moved = checkout
    assert _run(main, "rev-parse", tracking(DEV)) != moved

    assert _start(main, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert _run(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert (worktree / "README").read_text(encoding="utf-8") == "moved\n"
    assert (worktree / ".venv").readlink() == main / ".venv"
    assert launched == [(worktree, ["claude", lane.first_prompt(None)], main / ".venv")]
    assert f"made {worktree}, detached at {DEV} {moved[:12]}" in capsys.readouterr().out


def test_the_shared_configuration_and_the_branches_are_as_they_were(checkout, launched):
    """A detached worktree makes no branch and writes no upstream: the configuration every
    worktree shares is byte-identical, and no branch is made (the control below is the way
    that fails)."""
    main, _ = checkout
    config, branches = _config(main), _run(main, "for-each-ref", "refs/heads")

    assert _start(main, launched, "retire") == 0

    assert _config(main) == config
    assert _run(main, "for-each-ref", "refs/heads") == branches


def test_the_control_a_branch_made_from_origin_dev_writes_its_upstream(checkout):
    """What the lane start does not do, done, so the test above can fail: ``git worktree add
    -b`` from the remote-tracking ``dev`` writes the new branch's upstream into the shared
    configuration (on the developer's repository 2026-10-16), git's default pinned here so
    no developer's own setting decides it."""
    main, _ = checkout
    config = _config(main)

    _run(main, "-c", "branch.autoSetupMerge=true", "worktree", "add", "--quiet", "-b",
         "lane", str(main.parent / "shekel-retire"), tracking(DEV))

    assert _config(main) != config
    assert b'[branch "lane"]' in _config(main)


def test_from_a_linked_worktree_the_lane_sits_beside_the_main_checkout(checkout, launched):
    """Run from a worktree that is not the main checkout but inside it, as a reviewer's
    under ``.claude/worktrees`` is, the lane is still made beside the MAIN checkout, its
    ``.venv`` linked to the main checkout's, not to that worktree's."""
    main, moved = checkout
    linked = main / ".claude" / "worktrees" / "agent"
    _run(main, "worktree", "add", "--quiet", "--detach", str(linked), "HEAD")

    assert _start(linked, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert (worktree / ".venv").readlink() == main / ".venv"
    assert [(where, venv) for where, _, venv in launched] == [(worktree, main / ".venv")]
    assert not (linked.parent / "shekel-retire").exists()


# -- what is refused ---------------------------------------------------------------------------

@pytest.mark.parametrize("there", ["a directory", "a dangling link"])
def test_a_path_already_there_is_refused_before_anything_is_fetched_or_made(
        there, checkout, launched, capsys):
    """A path that is there already -- a dangling link too, which ``exists()`` alone would
    read as free -- is refused, exit 1: no worktree made, no fetch (the tracking ``dev``
    unmoved), no session started."""
    main, _ = checkout
    path = main.parent / "shekel-retire"
    if there == "a directory":
        path.mkdir()
    else:
        path.symlink_to(main.parent / "nowhere")
    worktrees, dev = (_run(main, "worktree", "list", "--porcelain"),
                      _run(main, "rev-parse", tracking(DEV)))

    assert _start(main, launched, "retire") == 1

    assert capsys.readouterr().err == f"refused: {path} is there already: name another lane\n"
    assert _run(main, "worktree", "list", "--porcelain") == worktrees
    assert _run(main, "rev-parse", tracking(DEV)) == dev
    assert launched == []


@pytest.mark.parametrize("name", ["", "Retire", "../retire", "re/tire", "-retire", "retire-",
                                  "re--tire", "re tire", ".retire", "retire\n"])
def test_a_name_that_is_not_lowercase_words_joined_by_hyphens_is_refused(name, checkout,
                                                                        launched, capsys):
    """A lane's name is lowercase letters and digits, words joined by single hyphens; any
    other is refused by the command line, exit 2, with nothing made.  (``--`` ends the
    options, so ``-retire`` reaches the name's check rather than the option parser.)"""
    main, _ = checkout

    with pytest.raises(SystemExit) as ended:
        _start(main, launched, "--", name)

    assert ended.value.code == 2
    assert "is not a lane's name" in capsys.readouterr().err
    assert not list(main.parent.glob(f"{lane.PREFIX}*"))
    assert launched == []


def test_a_step_that_is_not_a_card_is_refused(checkout, launched, capsys):
    """The step is named as quill names a card (``plan#N``, ``#N`` or ``N``); anything else
    is refused by the command line, exit 2, with nothing made."""
    main, _ = checkout

    with pytest.raises(SystemExit) as ended:
        _start(main, launched, "retire", "plan#x")

    assert ended.value.code == 2
    assert "is not a card" in capsys.readouterr().err
    assert not list(main.parent.glob(f"{lane.PREFIX}*"))
    assert launched == []


# -- the session ---------------------------------------------------------------------------

@pytest.mark.parametrize("card", ["plan#1823", "#1823", "1823"])
def test_a_step_named_is_the_one_the_session_claims_first(card, checkout, launched):
    """Named, however quill lets a card be written, the step is the one the first prompt
    claims, by quill's own command; ``quill next`` is not asked; ``/charter`` follows."""
    main, _ = checkout

    assert _start(main, launched, "xbi6-4d-c", card) == 0

    [(worktree, (program, prompt), _)] = launched
    assert (worktree, program) == (main.parent / "shekel-xbi6-4d-c", "claude")
    assert "Claim plan#1823: `python -m tools.quill.quill claim plan#1823`." in prompt
    assert "quill next" not in prompt
    assert prompt.endswith("Then run /charter for that step.")


def test_with_no_step_named_the_session_claims_the_one_next_hands_out(checkout, launched):
    """With no step named, the first prompt runs ``quill next`` and claims the step it
    names; ``/charter`` follows."""
    main, _ = checkout

    assert _start(main, launched, "retire") == 0

    [(_, (_, prompt), _)] = launched
    assert ("Run `python -m tools.quill.quill next` and claim the step it names: "
            "`python -m tools.quill.quill claim plan#N`.") in prompt
    assert prompt.endswith("Then run /charter for that step.")


def test_a_claude_that_cannot_start_leaves_the_worktree_and_says_how_to_start_it(checkout,
                                                                                 capsys):
    """A ``claude`` that cannot be run ends the start with exit 2, and the message names the
    worktree, which stands, and the line that starts its session."""
    main, _ = checkout

    def missing(_worktree, argv, _venv):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    assert lane.main(["retire"], root=main, launch=missing) == 2

    worktree = main.parent / "shekel-retire"
    error = capsys.readouterr().err
    assert error.startswith("failed: claude did not start (")
    assert f"the worktree stands: start its session with `cd {worktree} && claude`" in error
    assert (worktree / "README").read_text(encoding="utf-8") == "moved\n"


def test_start_runs_claude_in_the_worktree_in_place_of_this_process_with_the_venv(tmp_path):
    """:func:`lane.start` itself, the one piece the tests above replace: in a child Python
    it BECOMES the program named (the child exits 3 should ``start`` return), in the worktree
    named, ``PWD`` that worktree, the venv's ``python`` first on ``PATH`` and
    ``VIRTUAL_ENV`` naming it, each argument whole, and what the child printed before it
    flushed down the pipe the program inherits -- a stand-in ``claude`` first on the child's
    ``PATH``, a Python script so no shell corrects ``PWD`` for it, writes what it was
    given."""
    stand_in, record, venv = tmp_path / "bin", tmp_path / "record", tmp_path / "venv"
    worktree = tmp_path / "shekel-retire"
    for directory in (stand_in, venv / "bin", worktree):
        directory.mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (stand_in / "claude").write_text(
        f"#!{sys.executable}\nimport os, shutil, sys\n"
        f"open({str(record)!r}, 'w', encoding='utf-8').write('\\n'.join([os.getcwd(), "
        "os.environ['PWD'], shutil.which('python'), os.environ['VIRTUAL_ENV'], *sys.argv[1:]]))\n",
        encoding="utf-8")
    for program in (stand_in / "claude", venv / "bin" / "python"):
        program.chmod(0o755)
    child = ("import sys; from pathlib import Path; from tools.quill.lane import start; "
             "print('printed before the start'); start(Path(sys.argv[1]), "
             "['claude', 'the first prompt, in one argument'], Path(sys.argv[2])); "
             "raise SystemExit(3)")

    done = subprocess.run(
        [sys.executable, "-c", child, str(worktree), str(venv)], cwd=REPO, capture_output=True,
        text=True, check=False,
        env={**os.environ, "PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}"})

    assert done.returncode == 0, done.stderr
    assert done.stdout == "printed before the start\n"
    assert record.read_text(encoding="utf-8").splitlines() == [
        str(worktree), str(worktree), str(venv / "bin" / "python"), str(venv),
        "the first prompt, in one argument"]


def test_the_repository_ignores_the_link_so_no_commit_can_carry_it(tmp_path, launched):
    """The ``.venv`` LINK a lane is made with is ignored by the repository's own
    ``.gitignore``, read here from this checkout, not only by a clone's untracked
    ``info/exclude`` or a developer's own excludes file: the lane's ``git status`` is empty,
    so ``git add --all`` cannot commit it (``.venv/`` matches only a directory)."""
    main = scratch.repository(tmp_path, "Shekel")
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    scratch.with_origin(main, commit_files(main, commit(main, "root"), "base",
                                           {".gitignore": ignored}))
    (main / ".venv").mkdir()

    assert _start(main, launched, "retire") == 0

    worktree = tmp_path / "shekel-retire"
    assert (worktree / ".venv").is_symlink()
    assert _run(worktree, "-c", "core.excludesFile=/dev/null", "status", "--porcelain") == ""


# -- an environment bound to another repository reaches nothing but the checkout named --------

@pytest.mark.parametrize("binding", scratch.BINDINGS)
def test_a_bound_environment_reaches_no_repository_but_the_one_named(binding, checkout,
                                                                     launched, tmp_path,
                                                                     monkeypatch):
    """The 2026-10-04 incident's mechanism replayed onto a sentinel (as quill's own git
    calls are, ``test__git.py``): every git call the start makes -- the worktree list, the
    fetch, the read of ``dev``, the worktree's making -- lands in the checkout named, every
    answer is that checkout's (the main checkout the list names is the one the ``.venv``
    links), and the sentinel's refs, config, HEADs and indexes are byte-identical
    afterwards."""
    main, moved = checkout
    sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)

    assert _start(main, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert (worktree / ".venv").readlink() == main / ".venv"
    assert scratch.sentinel_state(sentinel) == before
