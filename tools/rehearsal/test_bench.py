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
import subprocess
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


def _side(root: Path, dev: str = "pytest==9.0.0\ntime-machine==3.5.1\n") -> Path:
    """A planted side's tree: two pins in ``requirements.txt`` and ``dev`` as its dev pins."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "requirements.txt").write_text("Flask==3.1.2\npsycopg[c]==3.3.6\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text("-r requirements.txt\n" + dev, encoding="utf-8")
    return root


def test_a_side_s_pins_are_its_requirements_and_its_time_machine_pin(tmp_path):
    """Exactly ``requirements.txt`` and the one ``time-machine`` line; no pin, or no file, is
    refused by name."""
    assert bench.side_pins(_side(tmp_path / "a")) == (
        "Flask==3.1.2\npsycopg[c]==3.3.6\ntime-machine==3.5.1\n")
    with pytest.raises(BenchError, match="pins no time-machine"):
        bench.side_pins(_side(tmp_path / "b", dev="pytest==9.0.0\n"))
    with pytest.raises(BenchError, match="cannot give its pins"):
        bench.side_pins(tmp_path / "c")


class _Builds:
    """A stand-in for :func:`bench.run` that records each command and makes a venv's
    directory as ``python -m venv`` would; pip exits ``pip_status``."""

    def __init__(self, pip_status: int = 0) -> None:
        """No command run yet; pip will exit ``pip_status``."""
        self.argvs: list[list[str]] = []
        self.pip_status = pip_status

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        """Record ``argv`` and answer as ``python -m venv`` or pip would."""
        self.argvs.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, self.pip_status, "", "pip said no\n")


@pytest.fixture(name="builds")
def _builds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Builds:
    """A throwaway ``XDG_CACHE_HOME`` and the stand-in builder."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    stand_in = _Builds()
    monkeypatch.setattr(bench, "run", stand_in)
    return stand_in


def test_a_venv_is_built_once_for_its_pins_and_then_reused_untouched(tmp_path, builds):
    """The first call builds (venv, then pip on the pins) and marks it built; the second, for
    a side with the same pins, runs nothing."""
    python = bench.venv_for(_side(tmp_path / "base"))
    assert [argv[1:3] for argv in builds.argvs] == [["-m", "venv"], ["-m", "pip"]]
    assert (python.parent.parent / "rehearsal-requirements.txt").read_text(encoding="utf-8") \
        == bench.side_pins(tmp_path / "base")
    assert bench.venv_for(_side(tmp_path / "cut")) == python
    assert len(builds.argvs) == 2


def test_a_venv_whose_build_did_not_finish_is_built_again(tmp_path, builds):
    """A venv directory with no marker, what a killed build leaves, is removed and rebuilt."""
    python = bench.venv_for(_side(tmp_path / "base"))
    venv = python.parent.parent
    (venv / ".rehearsal-built").unlink()
    (venv / "half-installed").write_text("", encoding="utf-8")
    assert bench.venv_for(_side(tmp_path / "base")) == python
    assert len(builds.argvs) == 4 and not (venv / "half-installed").exists()
    assert (venv / ".rehearsal-built").is_file()


def test_a_venv_pip_could_not_install_is_named_and_never_marked_built(tmp_path, builds):
    """pip's refusal is quoted, and the venv is left unmarked, so the next run rebuilds it."""
    builds.pip_status = 1
    with pytest.raises(BenchError, match="pip could not install .* \\(exit 1\\):\npip said no"):
        bench.venv_for(_side(tmp_path / "base"))
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


@pytest.mark.cluster
def test_a_bench_restores_the_dump_clones_it_per_state_and_leaves_nothing(dump):
    """Each state's clone holds the dump's rows and its worktree the state's commit; after
    it, nothing is left, and every part was timed."""
    head = gitcmd.git(arcs.REPO, "rev-parse", "HEAD").strip()
    parent = gitcmd.git(arcs.REPO, "rev-parse", "HEAD~1").strip()
    made = Bench(dump, {"base": parent, "cut": head})
    with made:
        assert made.listing is not None
        assert abs(datetime.now(timezone.utc) - made.listing.created) < timedelta(hours=1)
        assert {name: state.commit for name, state in made.made.items()} == {
            "base": parent, "cut": head}
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
