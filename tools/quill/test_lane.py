"""The lane start (:mod:`tools.quill.lane`), over a throwaway main checkout with a real bare
``origin`` (the ``checkout`` fixture): the worktree it makes, what it leaves the
configuration every worktree shares, the venv it builds, what it refuses, and the session it
starts.  Nothing here starts ``claude`` but a stand-in, and nothing calls GitHub.  ``uv`` is a
stand-in on ``PATH`` (the ``uv`` fixture), but for the one case that runs the real one over a
project with no dependency.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from tools.ci import scratch
from tools.ci.arcs import REPO
from tools.ci.gitcmd import DEV, tracking
from tools.ci.scratch import commit, commit_files
from tools.ci.scratch import run as _run
from tools.quill import lane


@pytest.fixture(name="uv")
def _uv(tmp_path, monkeypatch):
    """A stand-in ``uv`` first on ``PATH``, as a directory holding it and the file its calls
    are recorded in: each call writes the directory it ran in, ``VIRTUAL_ENV`` and its
    arguments on a line each, makes ``.venv/bin/python`` there as uv would, and exits with the
    status in the file ``uv-status`` beside it (0 with none)."""
    directory = tmp_path / "uv-stand-in"
    directory.mkdir()
    program = directory / "uv"
    program.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$(pwd)\" \"$VIRTUAL_ENV\" \"$*\" >> '{directory}/calls'\n"
        "mkdir -p .venv/bin && : > .venv/bin/python\n"
        f"[ -f '{directory}/uv-status' ] && exit \"$(cat '{directory}/uv-status')\"\n"
        "exit 0\n",
        encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return directory


def _uv_calls(uv):
    """Each call the stand-in ``uv`` recorded, as ``(directory, VIRTUAL_ENV, arguments)``."""
    lines = (uv / "calls").read_text(encoding="utf-8").splitlines()
    return [tuple(lines[at:at + 3]) for at in range(0, len(lines), 3)]


@pytest.fixture(name="checkout")
def _checkout(tmp_path, uv):  # pylint: disable=unused-argument
    """A main checkout ``tmp_path / "Shekel"`` (never a real one) with a ``.venv`` of its
    own and a bare ``origin`` whose ``dev`` has moved one commit past the one the checkout
    last fetched, so only a fetch brings the ``dev`` a lane starts from, with ``uv`` the
    stand-in; the checkout and that ``dev``'s sha.

    Pylint: ``unused-argument`` -- ``uv`` is requested for what it puts on ``PATH``, as every
    test here that starts a lane on this checkout needs it, and its value is theirs to read.
    """
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
    """What a lane start ran, and where, each as ``(worktree, argv)``, in place of
    :func:`lane.start`."""
    return []


def _start(root, launched, *argv):
    """``python -m tools.quill.lane`` with ``argv``, run from ``root``, its launch recorded
    in ``launched``; its exit status."""
    return lane.main(list(argv), root=root,
                     launch=lambda worktree, run: launched.append((worktree, run)))


def _config(main):
    """The configuration every worktree of ``main`` shares, as bytes."""
    return (main / ".git" / "config").read_bytes()


# -- the worktree ------------------------------------------------------------------------------

def test_a_lane_is_a_worktree_detached_at_the_dev_a_fetch_just_brought(checkout, launched,
                                                                       capsys):
    """``shekel-NAME`` beside the main checkout, detached at the ``origin/dev`` the start's
    own fetch brought (the checkout's tracking ref still named the commit before), its tree
    that commit's, and ``claude`` started there with the first prompt."""
    main, moved = checkout
    assert _run(main, "rev-parse", tracking(DEV)) != moved

    assert _start(main, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert _run(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert (worktree / "README").read_text(encoding="utf-8") == "moved\n"
    assert launched == [(worktree, ["claude", lane.first_prompt(None)])]
    assert f"made {worktree}, detached at {DEV} {moved[:12]}" in capsys.readouterr().out


# -- the venv ----------------------------------------------------------------------------------

def test_a_lane_builds_a_venv_of_its_own_from_its_own_lock(checkout, launched, uv, capsys):
    """Ruling plan#2828: ``uv sync --locked`` runs once, in the new worktree, ``VIRTUAL_ENV``
    naming that worktree's venv; the ``.venv`` is a directory and no link, and the main
    checkout's is left as it was.  The session starts only after it."""
    main, _ = checkout

    assert _start(main, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _uv_calls(uv) == [(str(worktree), str(worktree / ".venv"), "sync --locked")]
    assert (worktree / ".venv" / "bin" / "python").is_file()
    assert not (worktree / ".venv").is_symlink()
    assert not list((main / ".venv").iterdir())
    assert "its .venv is built from its own uv.lock" in capsys.readouterr().out


def test_a_sync_that_fails_ends_the_start_and_says_how_to_finish(checkout, launched, uv, capsys):
    """A ``uv sync`` that fails starts no session: exit 2, and the message names the status,
    the worktree, which stands, and the commands that finish it."""
    main, moved = checkout
    (uv / "uv-status").write_text("3\n", encoding="utf-8")

    assert _start(main, launched, "retire") == 2

    worktree = main.parent / "shekel-retire"
    assert capsys.readouterr().err == (
        f"failed: uv sync --locked exited 3; the worktree stands: build its venv and start its "
        f"session with `cd {worktree} && uv sync --locked && source .venv/bin/activate && "
        "claude`\n")
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert launched == []


def test_no_uv_ends_the_start_with_the_worktree_standing(checkout, launched, tmp_path,
                                                         monkeypatch, capsys):
    """With no ``uv`` on ``PATH`` the start fails as any other program it cannot run does:
    exit 2, naming ``uv``, with no session, and what it made said before."""
    main, moved = checkout
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(only_git))

    assert _start(main, launched, "retire") == 2

    worktree = main.parent / "shekel-retire"
    printed = capsys.readouterr()
    assert printed.err.startswith("failed: ") and "'uv'" in printed.err
    assert f"made {worktree}, detached at {DEV} {moved[:12]}" in printed.out
    assert launched == []


def test_the_real_uv_builds_the_lanes_venv_and_writes_no_other(tmp_path, launched, monkeypatch,
                                                               capfd):
    """The stand-in's premise, on the real uv over a project with no dependency, offline.  In
    a terminal whose ``VIRTUAL_ENV`` and ``PATH`` name the main checkout's venv (a session an
    older lane started holds both), the lane's ``.venv`` is a real venv of its own, and the
    main checkout's is unchanged to the file, its size and its time; uv does not warn of the
    mismatch, as the start names the venv it builds."""
    assert shutil.which("uv") is not None, "no uv on PATH: CI's install action puts one there"
    for name in [name for name in os.environ if name.startswith("UV_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nversion = "0"\nrequires-python = ">=3.12"\n'
        "dependencies = []\n\n[tool.uv]\npackage = false\n", encoding="utf-8")
    subprocess.run(["uv", "lock", "--quiet"], cwd=project, check=True)
    main = scratch.repository(tmp_path, "Shekel")
    scratch.with_origin(main, commit_files(main, commit(main, "root"), "base", {
        name: (project / name).read_text(encoding="utf-8")
        for name in ("pyproject.toml", "uv.lock")}))
    shared = main / ".venv"
    subprocess.run(["uv", "venv", "--quiet", str(shared)], check=True)
    before = _files(shared)
    monkeypatch.setenv("VIRTUAL_ENV", str(shared))
    monkeypatch.setenv("PATH", f"{shared / 'bin'}{os.pathsep}{os.environ['PATH']}")
    capfd.readouterr()

    assert _start(main, launched, "retire") == 0

    venv = main.parent / "shekel-retire" / ".venv"
    assert not venv.is_symlink()
    assert (venv / "pyvenv.cfg").is_file()
    assert subprocess.run(
        [venv / "bin" / "python", "-c", "import sys; print(sys.prefix)"], capture_output=True,
        text=True, check=True).stdout.strip() == str(venv)
    assert _files(shared) == before
    assert "does not match the project environment" not in capfd.readouterr().err


def _files(directory):
    """Every path under ``directory`` with its size and its time, without following a link."""
    return {path: (status.st_size, status.st_mtime_ns)
            for path, status in ((path, path.lstat()) for path in directory.rglob("*"))}


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


def test_from_a_linked_worktree_the_lane_sits_beside_the_main_checkout(checkout, launched, uv):
    """Run from a worktree that is not the main checkout but inside it, as a reviewer's
    under ``.claude/worktrees`` is, the lane is still made beside the MAIN checkout, and its
    venv is built there."""
    main, moved = checkout
    linked = main / ".claude" / "worktrees" / "agent"
    _run(main, "worktree", "add", "--quiet", "--detach", str(linked), "HEAD")

    assert _start(linked, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert [where for where, _, _ in _uv_calls(uv)] == [str(worktree)]
    assert [where for where, _ in launched] == [worktree]
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

    [(worktree, (program, prompt))] = launched
    assert (worktree, program) == (main.parent / "shekel-xbi6-4d-c", "claude")
    assert "Claim plan#1823: `python -m tools.quill.quill claim plan#1823`." in prompt
    assert "quill next" not in prompt
    assert prompt.endswith("Then run /charter for that step.")


def test_with_no_step_named_the_session_claims_the_one_next_hands_out(checkout, launched):
    """With no step named, the first prompt runs ``quill next`` and claims the step it
    names; ``/charter`` follows."""
    main, _ = checkout

    assert _start(main, launched, "retire") == 0

    [(_, (_, prompt))] = launched
    assert ("Run `python -m tools.quill.quill next` and claim the step it names: "
            "`python -m tools.quill.quill claim plan#N`.") in prompt
    assert prompt.endswith("Then run /charter for that step.")


def test_a_claude_that_cannot_start_leaves_the_worktree_and_says_how_to_start_it(checkout,
                                                                                 capsys):
    """A ``claude`` that cannot be run ends the start with exit 2, and the message names the
    worktree, which stands, and the line that starts its session."""
    main, _ = checkout

    def missing(_worktree, argv):
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
    named, ``PWD`` that worktree, the worktree's venv's ``python`` first on ``PATH`` and
    ``VIRTUAL_ENV`` naming it, each argument whole, and what the child printed before it
    flushed down the pipe the program inherits -- a stand-in ``claude`` first on the child's
    ``PATH``, a Python script so no shell corrects ``PWD`` for it, writes what it was
    given."""
    stand_in, record = tmp_path / "bin", tmp_path / "record"
    worktree = tmp_path / "shekel-retire"
    venv = worktree / ".venv"
    for directory in (stand_in, venv / "bin"):
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
             "['claude', 'the first prompt, in one argument']); "
             "raise SystemExit(3)")

    done = subprocess.run(
        [sys.executable, "-c", child, str(worktree)], cwd=REPO, capture_output=True,
        text=True, check=False,
        env={**os.environ, "PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}"})

    assert done.returncode == 0, done.stderr
    assert done.stdout == "printed before the start\n"
    assert record.read_text(encoding="utf-8").splitlines() == [
        str(worktree), str(worktree), str(venv / "bin" / "python"), str(venv),
        "the first prompt, in one argument"]


@pytest.mark.usefixtures("uv")
def test_the_repository_ignores_the_venv_and_a_link_so_no_commit_can_carry_either(
        tmp_path, launched):
    """A lane's ``.venv`` is ignored by the repository's own ``.gitignore``, read here from
    this checkout, not only by a clone's untracked ``info/exclude`` or a developer's own
    excludes file, and so is the LINK an older lane still holds until ``scripts/test.sh``
    replaces it (``.venv/`` would match only a directory): the lane's ``git status`` is empty
    with either, so ``git add --all`` cannot commit it."""
    main = scratch.repository(tmp_path, "Shekel")
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    scratch.with_origin(main, commit_files(main, commit(main, "root"), "base",
                                           {".gitignore": ignored}))
    (main / ".venv").mkdir()

    assert _start(main, launched, "retire") == 0

    worktree = tmp_path / "shekel-retire"
    assert (worktree / ".venv").is_dir()
    assert _run(worktree, "-c", "core.excludesFile=/dev/null", "status", "--porcelain") == ""
    shutil.rmtree(worktree / ".venv")
    (worktree / ".venv").symlink_to(main / ".venv", target_is_directory=True)
    assert _run(worktree, "-c", "core.excludesFile=/dev/null", "status", "--porcelain") == ""


# -- an environment bound to another repository reaches nothing but the checkout named --------

@pytest.mark.parametrize("binding", scratch.BINDINGS)
def test_a_bound_environment_reaches_no_repository_but_the_one_named(binding, checkout,
                                                                     launched, tmp_path,
                                                                     monkeypatch):
    """The 2026-10-04 incident's mechanism replayed onto a sentinel (as quill's own git
    calls are, ``test__git.py``): every git call the start makes -- the worktree list, the
    fetch, the read of ``dev``, the worktree's making -- lands in the checkout named, every
    answer is that checkout's (the main checkout the list names is the one the worktree is
    made beside), and the sentinel's refs, config, HEADs and indexes are byte-identical
    afterwards."""
    main, moved = checkout
    sentinel, before = scratch.bound_sentinel(tmp_path, binding, monkeypatch.setenv)

    assert _start(main, launched, "retire") == 0

    worktree = main.parent / "shekel-retire"
    assert _run(worktree, "rev-parse", "HEAD") == moved
    assert scratch.sentinel_state(sentinel) == before
