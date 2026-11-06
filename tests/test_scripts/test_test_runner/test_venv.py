"""The venv the wrapper runs the suite in (ruling plan#2828): where a tree holds ``uv.lock``,
outside CI, a ``.venv`` that is a link is removed and never followed, ``uv sync --locked`` runs
before each run, and the suite runs that venv's ``pytest``; a sync that cannot run stops the
wrapper, naming why.

Each case runs the wrapper in a tree of its own (:func:`_helpers._wrapper_copy`).  Those that
grade what uv itself does run the real one, over a project with no dependency and offline; those
that grade the wrapper's own arms run a stand-in on ``PATH`` that records each call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ._helpers import _run_wrapper, _stub_tree, _wrapper_tree


_PYPROJECT = (
    '[project]\nname = "scratch"\nversion = "0"\nrequires-python = ">=3.12"\n'
    "dependencies = []\n\n[tool.uv]\npackage = false\n"
)
#: What makes the real uv hermetic here: a cache of the case's own, no network, this Python.
_UV_ENV = {"UV_OFFLINE": "1", "UV_PYTHON": sys.executable}


def _real_uv_env(tmp_path: Path) -> dict[str, str]:
    """The environment that runs the real uv here, with no ``UV_`` variable of the caller's.

    Args:
        tmp_path: scratch directory.

    Returns:
        The overrides a case hands the wrapper, or a uv it runs itself.
    """
    assert shutil.which("uv") is not None, "no uv on PATH: CI's install action puts one there"
    caller = {name: "" for name in os.environ if name.startswith("UV_")}
    return {**caller, **_UV_ENV, "UV_CACHE_DIR": str(tmp_path / "uv-cache")}


def _locked_tree(binaries: Path, tmp_path: Path) -> Path:
    """The case's tree, holding a project with no dependency and the real lock of it.

    Args:
        binaries: Directory holding the stubs.
        tmp_path: scratch directory.

    Returns:
        The tree's root.
    """
    tree = _wrapper_tree(binaries)
    tree.mkdir()
    (tree / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    subprocess.run(["uv", "lock", "--quiet"], cwd=tree, check=True,
                   env={**os.environ, **_real_uv_env(tmp_path)})
    return tree


def _shared_venv(tmp_path: Path) -> Path:
    """A real venv standing in for the main checkout's, which an older lane's link names.

    Args:
        tmp_path: scratch directory.

    Returns:
        The venv.
    """
    shared = tmp_path / "shared" / ".venv"
    subprocess.run(["uv", "venv", "--quiet", str(shared)], check=True,
                   env={**os.environ, **_real_uv_env(tmp_path)})
    return shared


def _files(directory: Path) -> dict[Path, tuple[int, int]]:
    """Every path under ``directory`` with its size and its time, following no link.

    Args:
        directory: What to read.

    Returns:
        Each path's size and modification time.
    """
    return {path: (status.st_size, status.st_mtime_ns)
            for path, status in ((path, path.lstat()) for path in directory.rglob("*"))}


def _handed_to_pytest(tmp_path: Path) -> dict[str, str]:
    """The environment the stub pytest was handed.

    Args:
        tmp_path: scratch directory.

    Returns:
        That environment.
    """
    return json.loads((tmp_path / "pytest-env.json").read_text(encoding="utf-8"))


def _stand_in_uv(binaries: Path, status: int = 0) -> Path:
    """Write a stand-in ``uv`` beside the stubs, and a tree holding a lock for it to find.

    Each call records its arguments in ``uv-calls``, then makes the tree's ``.venv`` as far as
    the wrapper reads it: a ``python`` that works, and a ``pytest`` that writes
    ``the-trees-pytest-ran`` and runs the stub.

    Args:
        binaries: Directory holding the stubs.
        status: What it exits with.

    Returns:
        The tree's root.
    """
    tree = _wrapper_tree(binaries)
    tree.mkdir()
    (tree / "uv.lock").write_text("", encoding="utf-8")
    calls = binaries.parent / "uv-calls"
    ran = binaries.parent / "the-trees-pytest-ran"
    program = binaries / "uv"
    program.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
        f"mkdir -p '{tree}/.venv/bin'\n"
        f"ln -sf '{sys.executable}' '{tree}/.venv/bin/python'\n"
        f"printf '#!/bin/sh\\n: > \"{ran}\"\\nexec \"{binaries}/pytest\" \"$@\"\\n' "
        f"> '{tree}/.venv/bin/pytest'\n"
        f"chmod +x '{tree}/.venv/bin/pytest'\n"
        f"exit {status}\n",
        encoding="utf-8")
    program.chmod(0o755)
    return tree


def _uv_calls(tmp_path: Path) -> list[str]:
    """The stand-in ``uv``'s calls.

    Args:
        tmp_path: scratch directory.

    Returns:
        Each call's arguments, in one line; none when it was never called.
    """
    calls = tmp_path / "uv-calls"
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


class TestAnOlderLanesLink:
    """A link in a tree holding the lock, as a lane has once it merges the lock."""

    @pytest.mark.parametrize("names", ["the main checkout's venv", "nothing"])
    def test_it_is_replaced_by_a_venv_of_the_trees_own_and_its_target_left_whole(
        self, tmp_path: Path, names: str
    ) -> None:
        """The real uv, in the environment an older lane's session holds: ``VIRTUAL_ENV`` and
        ``PATH`` naming the venv the link names.  ``uv sync`` follows a link and writes into
        its target (measured 2026-11-04), so the wrapper removes the link first, a dangling
        one too, which uv refuses (``File exists``).  The target is unchanged to the file, its
        size and its time; the tree's ``.venv`` is a real venv; and pytest is handed that venv.

        Args:
            tmp_path: scratch directory.
            names: What the link names.
        """
        binaries = _stub_tree(tmp_path)
        tree = _locked_tree(binaries, tmp_path)
        shared = _shared_venv(tmp_path)
        before = _files(shared)
        target = shared if names == "the main checkout's venv" else tmp_path / "nowhere"
        (tree / ".venv").symlink_to(target, target_is_directory=True)

        result = _run_wrapper(binaries, {
            **_real_uv_env(tmp_path), "VIRTUAL_ENV": str(shared),
            "PATH": f"{shared / 'bin'}:{binaries}:{os.environ['PATH']}"})

        assert result.returncode == 0, result.stderr
        assert f"removed the link at {tree / '.venv'}" in result.stderr
        assert not (tree / ".venv").is_symlink()
        assert (tree / ".venv" / "pyvenv.cfg").is_file()
        assert _files(shared) == before
        handed = _handed_to_pytest(tmp_path)
        assert handed["VIRTUAL_ENV"] == str(tree / ".venv")
        assert handed["PATH"].split(":")[0] == str(tree / ".venv" / "bin")

    def test_the_suite_runs_the_trees_pytest_not_the_one_first_on_path(
        self, tmp_path: Path
    ) -> None:
        """A session an older lane's start began holds the main checkout's venv first on
        ``PATH``, and the wrapper finds ``pytest`` on ``PATH``.  Replacing the link alone
        would leave the suite on that venv's pytest and packages, which is the defect ruling
        plan#2828 exists for: ``import valkey`` stopping collection.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        tree = _stand_in_uv(binaries)
        shared_bin = tmp_path / "shared-bin"
        shared_bin.mkdir()
        (shared_bin / "pytest").write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        (shared_bin / "pytest").chmod(0o755)
        (tree / ".venv").symlink_to(shared_bin.parent, target_is_directory=True)

        result = _run_wrapper(binaries, {
            "PATH": f"{shared_bin}:{binaries}:{os.environ['PATH']}"})

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "the-trees-pytest-ran").exists()


class TestTheSync:
    """Before each run, wherever the tree holds a lock and the run is not on CI."""

    def test_a_venv_of_the_trees_own_is_synced_before_each_run_and_kept(
        self, tmp_path: Path
    ) -> None:
        """The command is ``uv sync --locked``, at the tree, once a run, from whatever
        directory the wrapper was started in, and it leaves a real ``.venv`` standing.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        tree = _stand_in_uv(binaries)
        (tree / ".venv").mkdir()
        (tree / ".venv" / "kept").write_text("", encoding="utf-8")

        for _ in range(2):
            result = _run_wrapper(binaries, {})
            assert result.returncode == 0, result.stderr

        assert _uv_calls(tmp_path) == [f"sync --locked --quiet --directory {tree}"] * 2
        assert (tree / ".venv" / "kept").exists()
        assert "removed the link" not in result.stderr

    def test_a_sync_that_fails_stops_the_wrapper_and_says_so(self, tmp_path: Path) -> None:
        """The real uv refusing a stale lock, which ``--locked`` is there for (a change this
        lock can resolve, so plain ``uv sync`` would pass; measured 2026-11-04): no cluster, no
        pytest, exit 2, uv's own words and the wrapper's naming what failed and what to do.
        The link had gone by then, and its target is whole.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        tree = _locked_tree(binaries, tmp_path)
        (tree / "pyproject.toml").write_text(
            _PYPROJECT.replace('">=3.12"', '">=3.13"'), encoding="utf-8")
        shared = _shared_venv(tmp_path)
        before = _files(shared)
        (tree / ".venv").symlink_to(shared, target_is_directory=True)

        result = _run_wrapper(binaries, _real_uv_env(tmp_path))

        assert result.returncode == 2, result.stderr
        assert f"[test.sh] uv sync --locked failed in {tree}" in result.stderr
        assert "then run this again" in result.stderr
        assert "error: " in result.stderr, "uv's own words are not above the wrapper's"
        assert not (tmp_path / "pytest-env.json").exists()
        assert not (tmp_path / "docker-calls").exists()
        assert _files(shared) == before

    def test_no_uv_stops_the_wrapper_and_leaves_the_link(self, tmp_path: Path) -> None:
        """With no uv to build a venv, the link stays, so what the lane's ``.venv/bin``
        commands run is what they ran, and the wrapper says what it needs.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        tree = _wrapper_tree(binaries)
        tree.mkdir()
        (tree / "uv.lock").write_text("", encoding="utf-8")
        (tree / ".venv").symlink_to(tmp_path / "shared", target_is_directory=True)
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "python3").symlink_to(sys.executable)
        (tools / "dirname").symlink_to(shutil.which("dirname"))

        result = _run_wrapper(binaries, {"PATH": f"{binaries}:{tools}"})

        assert result.returncode == 2, result.stderr
        assert "needs uv, which is not on PATH" in result.stderr
        assert (tree / ".venv").readlink() == tmp_path / "shared"
        assert not (tmp_path / "pytest-env.json").exists()

    def test_once_a_venv_is_built_the_rest_of_the_run_uses_its_interpreter(
        self, tmp_path: Path
    ) -> None:
        """A tree with no ``.venv`` yet starts on the ``python3`` first on ``PATH``, which
        lacks what the image builder imports.  Only the question of whether this is CI may run
        under it: every later step of the cluster's runs under the venv just built.

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        _stand_in_uv(binaries)
        found_first = tmp_path / "found-first"
        found_first.mkdir()
        record = tmp_path / "python3-calls"
        (found_first / "python3").write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{record}'\nexec '{sys.executable}' \"$@\"\n",
            encoding="utf-8")
        (found_first / "python3").chmod(0o755)

        result = _run_wrapper(binaries, {
            "PATH": f"{found_first}:{binaries}:{os.environ['PATH']}"})

        assert result.returncode == 0, result.stderr
        cluster = [line for line in record.read_text(encoding="utf-8").splitlines()
                   if "private_cluster.py" in line]
        assert [line.split("private_cluster.py ")[1] for line in cluster] == ["on-ci"]


class TestWhereTheWrapperLeavesTheVenvAlone:
    """Where it is another's to build, or the tree has no lock to build it from."""

    @pytest.mark.parametrize("value", ["true", "1"])
    def test_on_ci_nothing_is_synced_and_a_link_stays(
        self, tmp_path: Path, value: str
    ) -> None:
        """CI's install action is its one installer and has built the job's venv, so uv is not
        called, and what is first on ``PATH`` stays first.

        Args:
            tmp_path: scratch directory.
            value: ``CI``'s value.
        """
        binaries = _stub_tree(tmp_path)
        tree = _stand_in_uv(binaries)
        (tree / ".venv").symlink_to(tmp_path / "shared", target_is_directory=True)
        given = f"{binaries}:{os.environ['PATH']}"

        result = _run_wrapper(binaries, {"CI": value, "PATH": given, "VIRTUAL_ENV": "given"})

        assert result.returncode == 0, result.stderr
        assert _uv_calls(tmp_path) == []
        assert (tree / ".venv").readlink() == tmp_path / "shared"
        handed = _handed_to_pytest(tmp_path)
        assert (handed["PATH"], handed["VIRTUAL_ENV"]) == (given, "given")

    @pytest.mark.parametrize("value", ["false", "0", ""])
    def test_a_ci_that_means_no_is_not_ci(self, tmp_path: Path, value: str) -> None:
        """``CI`` is read through the cluster module's vocabulary, its one reading, not by
        presence (``private_cluster._on_ci``): ``CI=false`` left set in a shell must not skip
        the sync and run the suite over a stale venv.

        Args:
            tmp_path: scratch directory.
            value: ``CI``'s value.
        """
        binaries = _stub_tree(tmp_path)
        tree = _stand_in_uv(binaries)

        result = _run_wrapper(binaries, {"CI": value})

        assert result.returncode == 0, result.stderr
        assert _uv_calls(tmp_path) == [f"sync --locked --quiet --directory {tree}"]

    def test_a_tree_holding_no_lock_keeps_its_link(self, tmp_path: Path) -> None:
        """A tree with no lock has nothing to build from: its link stays and uv is not called.
        (A lane before the lock keeps its link another way: its wrapper has no such block.)

        Args:
            tmp_path: scratch directory.
        """
        binaries = _stub_tree(tmp_path)
        tree = _stand_in_uv(binaries)
        (tree / "uv.lock").unlink()
        (tree / ".venv").symlink_to(tmp_path / "shared", target_is_directory=True)

        result = _run_wrapper(binaries, {})

        assert result.returncode == 0, result.stderr
        assert _uv_calls(tmp_path) == []
        assert (tree / ".venv").readlink() == tmp_path / "shared"
