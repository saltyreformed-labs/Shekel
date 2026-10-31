"""The bench: a dump restored once and cloned per state, a detached worktree per state, and a
teardown whose census names anything left (step X-cx's K1a-2).

The cases marked ``cluster`` start a real private cluster (``scripts/private_cluster.py``) on
the daemon that module chooses (the rootless one, unless CI sanctions its own), and restore a
dump planted by the conftest's ``dump`` fixture, so they need docker and the test-db image,
whose own ``pg_dump`` and ``pg_restore`` run inside the cluster (``balance:R-BAL327``); the
conftest says where they run.  The rest grade the bench's pure parts on planted inputs.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from tools.ci import arcs, gitcmd
from tools.rehearsal import bench
from tools.rehearsal.bench import Bench, BenchError


_CREATED = "; Archive created at 2026-10-20 06:07:21 UTC\n"


def test_the_major_a_dump_was_made_by_is_read_from_its_listing():
    """``pg_restore -l`` names the server a custom-format archive came from; its major is the
    first number, a distribution's suffix or not."""
    assert bench.read_listing(
        f";\n{_CREATED};     Dumped from database version: 18.6\n").major == 18
    assert bench.read_listing(
        f"{_CREATED};     Dumped from database version: 16.4 (Ubuntu 16.4-1)\n").major == 16
    with pytest.raises(BenchError, match="not a pg_dump -Fc archive"):
        bench.read_listing(_CREATED)


_MAJOR = ";     Dumped from database version: 18.6\n"


def test_the_instant_a_dump_was_made_is_read_as_utc_and_another_zone_is_refused():
    """The listing's creation time, as the cluster's ``pg_restore`` prints it, is an aware
    UTC instant; no time, or one printed in another zone, is refused by name."""
    assert bench.read_listing(_CREATED + _MAJOR).created == datetime(
        2026, 10, 20, 6, 7, 21, tzinfo=timezone.utc)
    with pytest.raises(BenchError, match="names no time it was created at"):
        bench.read_listing(_MAJOR)
    with pytest.raises(BenchError, match="prints its creation time in EDT, not UTC"):
        bench.read_listing(_CREATED.replace("UTC", "EDT") + _MAJOR)


@pytest.mark.parametrize("created, noon", [
    (datetime(2026, 10, 20, 6, 7, 21, tzinfo=timezone.utc), "2026-10-20T12:00:00-04:00"),
    (datetime(2026, 10, 21, 2, 30, tzinfo=timezone.utc), "2026-10-20T12:00:00-04:00"),
    (datetime(2026, 1, 15, 4, 59, tzinfo=timezone.utc), "2026-01-14T12:00:00-05:00"),
    (datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc), "2026-01-15T12:00:00-05:00"),
])
def test_the_pinned_instant_is_noon_on_the_civil_day_the_dump_was_made_in_the_zone(created,
                                                                                    noon):
    """Noon in the zone on the dump's civil day THERE, never the UTC day: a dump made at
    02:30 UTC is still the evening before in New York, in summer time and in winter."""
    assert bench.Listing(18, created).noon("America/New_York").isoformat() == noon


def test_a_dump_from_another_major_than_the_cluster_s_is_refused_naming_both():
    """180006 is PostgreSQL 18.6, so an 18 dump passes and a 17 one is refused."""
    bench.refuse_another_major(18, 180006)
    with pytest.raises(BenchError, match="made by PostgreSQL 17 and the cluster runs "
                                         "PostgreSQL 18"):
        bench.refuse_another_major(17, 180006)


def test_states_are_name_revision_pairs_each_named_once():
    """A state's name becomes a database and a directory name, so it is held to both."""
    assert bench.parse_states(["base=origin/main", "cut=HEAD"]) == {
        "base": "origin/main", "cut": "HEAD"}
    for given in (["base"], ["=HEAD"], ["base="], ["Base=HEAD"], ["1st=HEAD"], ["a-b=HEAD"],
                  ["../x=HEAD"], ["a" * 31 + "=HEAD"]):
        with pytest.raises(BenchError, match="is not <name>=<revision>"):
            bench.parse_states(given)
    with pytest.raises(BenchError, match="given twice"):
        bench.parse_states(["base=HEAD", "base=HEAD~1"])
    with pytest.raises(BenchError, match="no state given"):
        bench.parse_states([])


def _side(root: Path) -> Path:
    """A planted side's tree: this checkout's own ``pyproject.toml`` and ``uv.lock``, which
    ``uv export --frozen`` reads with no network."""
    root.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(arcs.REPO / name, root / name)
    return root


def test_a_side_s_pins_are_its_locked_runtime_set_and_its_build_and_rehearsal_groups(tmp_path):
    """The side's lock's pins for its runtime set, its ``build`` tools and its ``rehearsal``
    group, every pin with its sha256, and none of the dev tools or the server.  The text names
    no path, so two sides with one lock give one text, and one cache key."""
    pins = bench.side_pins(_side(tmp_path / "a"))
    names = set(re.findall(r"^([a-z0-9][a-z0-9._-]*)==", pins, re.MULTILINE))
    assert {"flask", "psycopg", "psycopg-c", "time-machine", "setuptools", "wheel"} <= names
    assert names.isdisjoint({"pytest", "pylint", "playwright", "pre-commit", "gunicorn"})
    entries = re.split(r"\n(?=\S)", pins.strip())
    assert len(entries) == len(names)
    assert all("--hash=sha256:" in entry for entry in entries)
    assert str(tmp_path) not in pins
    assert bench.side_pins(_side(tmp_path / "b")) == pins


def test_a_side_with_no_lock_or_one_uv_cannot_read_is_refused_by_name(tmp_path):
    """A tree with no ``uv.lock`` is a revision before the lock, and one whose lock uv cannot
    export is quoted."""
    with pytest.raises(BenchError, match="holds no uv.lock, so it is a revision before the lock"):
        bench.side_pins(tmp_path / "c")
    broken = _side(tmp_path / "d")
    (broken / "uv.lock").write_text("not a lock\n", encoding="utf-8")
    with pytest.raises(BenchError, match="uv could not export .* \\(exit 2\\): "):
        bench.side_pins(broken)


class _Builds:
    """A stand-in for :func:`bench.run` that records each ``uv sync`` and the venv it was
    pointed at, and makes that venv's directory as uv would; each sync exits ``status``.  Any
    other command (``uv export``) runs for real."""

    def __init__(self, status: int = 0) -> None:
        """No sync run yet; each will exit ``status``."""
        self.syncs: list[list[str]] = []
        self.into: list[str] = []
        self.status = status
        self._real = bench.run

    def __call__(self, argv: list[str], env: dict[str, str] | None = None,
                 **given: object) -> subprocess.CompletedProcess[str]:
        """Record a sync and answer as uv would; run anything else."""
        if argv[:2] != ["uv", "sync"]:
            return self._real(argv, env, **given)
        assert env is not None
        self.syncs.append(argv)
        self.into.append(env["UV_PROJECT_ENVIRONMENT"])
        (Path(env["UV_PROJECT_ENVIRONMENT"]) / "bin").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, self.status, "", "uv said no\n")


@pytest.fixture(name="builds")
def _builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Builds:
    """A throwaway ``XDG_CACHE_HOME`` and the stand-in builder."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    stand_in = _Builds()
    monkeypatch.setattr(bench, "run", stand_in)
    return stand_in


def test_a_venv_is_built_once_for_its_pins_and_then_reused_untouched(tmp_path, builds):
    """The first call builds: uv syncs the side's lock into the cached venv under this
    interpreter, with the ``build`` and ``rehearsal`` groups and then with ``rehearsal``
    alone, and marks it built with its pins.  The second, for a side with the same pins,
    syncs nothing."""
    base = _side(tmp_path / "base")
    python = bench.venv_for(base)
    venv = python.parent.parent
    sync = ["uv", "sync", "--locked", "--require-build-hashes", "--no-install-project",
            "--no-default-groups"]
    side = ["--python", sys.executable, "--project", str(base)]
    assert builds.syncs == [[*sync, "--group", "build", "--group", "rehearsal", *side],
                            [*sync, "--group", "rehearsal", *side]]
    assert builds.into == [str(venv), str(venv)]
    assert (venv / ".rehearsal-built").read_text(encoding="utf-8") == bench.side_pins(base)
    assert bench.venv_for(_side(tmp_path / "cut")) == python
    assert len(builds.syncs) == 2


def test_a_venv_whose_build_did_not_finish_is_built_again(tmp_path, builds):
    """A venv directory with no marker, what a killed build leaves, is removed and rebuilt."""
    python = bench.venv_for(_side(tmp_path / "base"))
    venv = python.parent.parent
    (venv / ".rehearsal-built").unlink()
    (venv / "half-installed").write_text("", encoding="utf-8")
    assert bench.venv_for(_side(tmp_path / "base")) == python
    assert len(builds.syncs) == 4 and not (venv / "half-installed").exists()
    assert (venv / ".rehearsal-built").is_file()


def test_a_venv_uv_could_not_install_is_named_and_never_marked_built(tmp_path, builds):
    """uv's refusal is quoted, no second sync runs, and the venv is left unmarked, so the
    next run rebuilds it."""
    builds.status = 1
    with pytest.raises(BenchError, match="uv could not install .* \\(exit 1\\):\nuv said no"):
        bench.venv_for(_side(tmp_path / "base"))
    assert len(builds.syncs) == 1
    assert not list((tmp_path / "cache").rglob(".rehearsal-built"))


def test_a_dump_that_cannot_be_opened_is_refused_by_name_before_any_tool_runs(tmp_path,
                                                                              monkeypatch):
    """The bench opens the dump itself, to hand it to the cluster's ``pg_restore`` on its
    standard input, so a dump it cannot open fails the bench naming it, with no tool run in
    the cluster and the cluster it started stopped.  The cluster module is a stand-in that
    answers each command and records it."""
    commands: list[str] = []

    def cluster_module(argv: list[str], **_given: object) -> subprocess.CompletedProcess[str]:
        """Answer as ``private_cluster.py`` would, recording which command it was given."""
        commands.append(argv[2])
        said = {"token": "01" * 16 + "\n", "start": f"{tmp_path}/sockets\nc0ffee\n"}
        return subprocess.CompletedProcess(argv, 0, said.get(argv[2], ""), "")

    monkeypatch.setattr(bench, "image_tag", lambda: "stub-image:test")
    monkeypatch.setattr(bench, "run", cluster_module)
    absent = tmp_path / "absent.dump"
    with pytest.raises(BenchError) as refused:
        with Bench(absent, {"cut": "HEAD"}):
            pass
    assert str(refused.value).startswith(f"the dump {absent} cannot be read: [Errno 2] No such")
    assert commands == ["token", "start", "stop"]


def _containers() -> str:
    """The ids of every container a bench of this process started, running or not, asked of
    the daemon the cluster module chose: asked of docker's default, a plain run would ask
    the system daemon about a cluster on the rootless one, and find nothing."""
    chosen = bench.cluster("daemon").strip()
    return subprocess.run(["docker", "ps", "--all", "--quiet", "--filter",
                           f"name=shekel-rehearsal-{os.getpid()}-"],
                          check=True, capture_output=True, text=True,
                          env={**os.environ, "DOCKER_HOST": chosen} if chosen else None).stdout


def _left_nothing(made: Bench) -> None:
    """Nothing the bench made is left: no container, socket directory, worktree or listing."""
    assert _containers() == ""
    assert made.socket_dir is not None and not made.socket_dir.exists()
    listed = gitcmd.git(arcs.REPO, "worktree", "list", "--porcelain")
    for state in made.made.values():
        assert not state.worktree.parent.exists(), state.worktree.parent
        assert f"worktree {state.worktree}\n" not in listed


def _commit_of_head_s_tree() -> str:
    """A second commit the bench can build, which every checkout holds whatever its depth: a
    commit of HEAD's own tree, HEAD its parent, on no branch.  Not ``HEAD~1``: a revision
    before the lock is refused, and so is ``dev``'s tip in the pull request that brings it."""
    return gitcmd.git(arcs.REPO, "-c", "user.name=rehearsal bench case",
                      "-c", "user.email=rehearsal@invalid", "commit-tree", "--no-gpg-sign",
                      "-p", "HEAD", "-m", "the bench case's cut", "HEAD^{tree}").strip()


@pytest.mark.cluster
def test_a_bench_restores_the_dump_clones_it_per_state_and_leaves_nothing(dump):
    """Each state's clone holds the dump's rows and its worktree the state's commit; the two
    states' pins are one, so they share one venv; after it, nothing is left, and every part
    was timed."""
    head = gitcmd.git(arcs.REPO, "rev-parse", "HEAD").strip()
    cut = _commit_of_head_s_tree()
    made = Bench(dump, {"base": head, "cut": cut})
    with made:
        assert made.listing is not None
        assert abs(datetime.now(timezone.utc) - made.listing.created) < timedelta(hours=1)
        assert {name: state.commit for name, state in made.made.items()} == {
            "base": head, "cut": cut}
        assert made.made["base"].python == made.made["cut"].python
        for state in made.made.values():
            assert gitcmd.git(state.worktree, "rev-parse", "HEAD").strip() == state.commit
            assert gitcmd.git(state.worktree, "status", "--porcelain") == ""
            with psycopg.connect(state.url) as connection:
                assert connection.execute("SELECT id, amount FROM ledger ORDER BY id").fetchall() \
                    == [(1, Decimal("12.34")), (2, Decimal("0.01"))]
            imported = subprocess.run([str(state.python), "-c", "import flask, time_machine"],
                                      check=False, capture_output=True, text=True)
            assert imported.returncode == 0, imported.stderr
    _left_nothing(made)
    assert set(made.timings) == {"image", "start", "restore", "clone base", "worktree base",
                                 "venv base", "clone cut", "worktree cut", "venv cut",
                                 "teardown"}


@pytest.mark.cluster
def test_a_ctrl_c_inside_the_bench_still_leaves_nothing(dump):
    """A KeyboardInterrupt in the caller's body tears the bench down all the same."""
    made = Bench(dump, {"cut": "HEAD"})
    with pytest.raises(KeyboardInterrupt), made:
        raise KeyboardInterrupt
    _left_nothing(made)


@pytest.mark.cluster
def test_a_state_that_cannot_be_made_tears_down_what_was(dump):
    """A revision git cannot resolve fails the bench by name, after the first state, the
    cluster and the restore were made; none of them is left."""
    made = Bench(dump, {"base": "HEAD", "cut": "no-such-revision"})
    with pytest.raises(BenchError, match="state cut's worktree at 'no-such-revision'"):
        with made:
            pass
    _left_nothing(made)


@pytest.mark.cluster
def test_a_worktree_the_teardown_could_not_remove_is_named(dump, monkeypatch):
    """A planted leftover, a worktree whose removal is skipped and the scratch directory kept,
    is named by the census, never passed as clean.  The case then removes both itself."""
    monkeypatch.setattr(Bench, "_remove_worktree", lambda self, worktree: None)
    monkeypatch.setattr(bench.shutil, "rmtree", lambda path, ignore_errors=False: None)
    made = Bench(dump, {"cut": "HEAD"})
    with pytest.raises(BenchError, match="the teardown left something") as refused:
        with made:
            pass
    monkeypatch.undo()
    worktree = made.made["cut"].worktree
    assert f"git still lists worktree {worktree}" in str(refused.value)
    assert f"{worktree.parent} survived teardown" in str(refused.value)
    gitcmd.git(arcs.REPO, "worktree", "remove", "--force", str(worktree))
    bench.shutil.rmtree(worktree.parent)
    _left_nothing(made)


@pytest.mark.cluster
def test_the_command_line_brings_a_bench_up_and_reports_each_part(dump, capsys):
    """``python -m tools.rehearsal.bench`` exits 0, naming each state and each part's time."""
    assert bench.main(["--dump", str(dump), "--state", "cut=HEAD"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("state cut: ") and out.endswith("teardown left nothing\n")
    assert "\nrestore: " in out and "\nclone cut: " in out
