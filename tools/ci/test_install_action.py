"""Controls for ``.github/actions/install-lock``: its own steps, read and RUN.

Which jobs call the action, and with what, is ``test_ci_workflow``'s
(:class:`tools.ci.test_ci_workflow.TestEveryJobInstallsTheLock`).
"""
from __future__ import annotations

import hashlib
import re

import yaml

from tools.ci import arcs
from tools.ci.test_ci_workflow import _run_step
from tools.ci.test_dockerfile_uv_action import USES as DOCKERFILE_UV


#: The file that defines the install action.
INSTALL_LOCK_FILE = arcs.REPO / ".github/actions/install-lock/action.yml"


def _action_steps() -> dict[str, dict]:
    """The install action's steps, keyed by name."""
    action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
    return {step["name"]: step for step in action["runs"]["steps"]}


class TestTheInstallActionInstallsTheLock:
    """The action's own steps, read and RUN with a stand-in for ``uv``."""

    UV = "The Dockerfile's uv"
    NAME = "Name uv's cache"
    CACHE = "Restore uv's cache"
    INSTALL = "Install from the lock"

    def test_its_inputs_steps_and_pins(self):
        """Two required inputs; its four steps in order, the first taking uv from the one
        recipe for it by the ``$/`` form (:mod:`tools.ci.test_dockerfile_uv_action`); every
        third-party action pinned by sha; GitHub's own cache action, run only when the caller
        asks for exactly ``true``, keyed on the install set, the uv binary's sha256, the lock
        and ``.python-version``; and the install step's interpreter uv's own build, never the
        runner's."""
        action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
        assert action["inputs"].keys() == {"install", "cache"}
        assert all(spec["required"] is True for spec in action["inputs"].values())
        assert action["runs"]["using"] == "composite"
        steps = _action_steps()
        assert list(steps) == [self.UV, self.NAME, self.CACHE, self.INSTALL]
        assert steps[self.UV] == {"name": self.UV, "id": "uv", "uses": DOCKERFILE_UV}
        for step in steps.values():
            if "uses" in step and step["uses"] != DOCKERFILE_UV:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["name"]
            elif "uses" not in step:
                assert step["shell"] == "bash", step["name"]
        cache = steps[self.CACHE]
        assert cache["uses"].startswith("actions/cache@")
        assert cache["if"] == "inputs.cache == 'true'"
        key = "uv-${{ runner.os }}-${{ inputs.install }}-${{ steps.cache.outputs.uv }}-"
        assert cache["with"] == {
            "path": "${{ steps.cache.outputs.dir }}",
            "key": key + "${{ hashFiles('uv.lock', '.python-version') }}",
            "restore-keys": key + "\n",
        }
        assert all("if" not in steps[name] for name in (self.UV, self.NAME, self.INSTALL))
        assert steps[self.NAME]["id"] == "cache"
        assert steps[self.NAME]["env"] == {"UV": "${{ steps.uv.outputs.bin }}"}
        assert steps[self.INSTALL]["env"] == {
            "UV": "${{ steps.uv.outputs.bin }}",
            "INSTALL": "${{ inputs.install }}",
            "UV_CACHE_DIR": "${{ steps.cache.outputs.dir }}",
            "UV_PYTHON_PREFERENCE": "only-managed",
        }

    def test_only_the_install_step_writes_the_path_and_zizmor_is_ignored_there_alone(self):
        """No step writes GITHUB_ENV, only the install step writes GITHUB_PATH, and the
        file's one zizmor ignore is the github-env one on that step's ``run`` line (ruling
        plan#2376).  No step reads a secret or a token."""
        for name, step in _action_steps().items():
            text = yaml.safe_dump(step)
            assert "GITHUB_ENV" not in text, name
            assert ("GITHUB_PATH" in text) is (name == self.INSTALL), name
            assert not re.search(r"\bsecrets\b|\btoken\b", text), name
        lines = INSTALL_LOCK_FILE.read_text(encoding="utf-8").splitlines()
        ignores = [i for i, line in enumerate(lines) if "zizmor:" in line]
        assert len(ignores) == 1
        assert lines[ignores[0]] == "      run: | # zizmor: ignore[github-env]"
        names = [line for line in lines[:ignores[0]] if line.startswith("    - name: ")]
        assert names[-1] == f"    - name: {self.INSTALL}"

    def test_the_cache_is_named_for_the_uv_binary_that_writes_it(self, tmp_path):
        """The naming step, RUN: the cache's directory under the runner's temp directory, and
        the sha256 of the uv binary the first step handed on, which the key names uv by; a
        binary it cannot read fails the step, handing nothing on."""
        run = _action_steps()[self.NAME]["run"]
        uv, temp, output = tmp_path / "uv", tmp_path / "temp", tmp_path / "github_output"
        binary = b"a uv binary\n"
        uv.write_bytes(binary)
        env = {"RUNNER_TEMP": str(temp), "GITHUB_OUTPUT": str(output)}
        output.write_text("", encoding="utf-8")
        result = _run_step(run, {**env, "UV": str(uv)})
        assert result.returncode == 0, result.stderr
        assert output.read_text(encoding="utf-8") == (
            f"dir={temp}/uv-cache\nuv={hashlib.sha256(binary).hexdigest()}\n")
        output.write_text("", encoding="utf-8")
        result = _run_step(run, {**env, "UV": str(tmp_path / "missing")})
        assert result.returncode != 0
        assert output.read_text(encoding="utf-8") == ""

    def test_each_install_set_runs_its_one_uv_command_then_puts_the_venv_first(self, tmp_path):
        """Each set runs its one uv command, every sync ``--locked --require-build-hashes``,
        then writes the workspace's ``.venv/bin`` and uv's own directory to GITHUB_PATH.  A
        set the step does not name, and a uv that fails, run nothing more and write no
        PATH."""
        uv, log, path = tmp_path / "uv", tmp_path / "uv.log", tmp_path / "github_path"
        run = _action_steps()[self.INSTALL]["run"]
        sync = "sync --locked --require-build-hashes"
        cases = {
            "all": (0, [sync]),
            "runtime": (0, [f"{sync} --no-dev"]),
            "landing": (0, [f"{sync} --only-group landing"]),
            "refresh": (1, []), "build": (1, []),
            "python": (0, ["venv"]),
            "dev": (1, []), "": (1, []), "all ": (1, []), "ALL": (1, []),
        }
        for status in ("0", "3"):
            uv.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexit {status}\n', encoding="utf-8")
            uv.chmod(0o755)
            for install, (expected, calls) in cases.items():
                log.write_text("", encoding="utf-8")
                path.write_text("", encoding="utf-8")
                result = _run_step(run, {"UV": str(uv), "INSTALL": install,
                                         "GITHUB_PATH": str(path),
                                         "GITHUB_WORKSPACE": str(tmp_path / "ws")})
                assert log.read_text(encoding="utf-8").splitlines() == calls, install
                if expected == 0 and status == "0":
                    assert result.returncode == 0, (install, result.stderr)
                    assert path.read_text(encoding="utf-8") == (
                        f"{tmp_path / 'ws'}/.venv/bin\n{tmp_path}\n")
                else:
                    assert result.returncode != 0, (install, status)
                    assert path.read_text(encoding="utf-8") == "", (install, status)
                if expected == 1:
                    assert "No install set" in result.stderr, install
