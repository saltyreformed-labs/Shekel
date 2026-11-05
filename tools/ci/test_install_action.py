"""Controls for ``.github/actions/install-lock``: its own steps, read and RUN.

Which jobs call the action, and with what, is ``test_ci_workflow``'s
(:class:`tools.ci.test_ci_workflow.TestEveryJobInstallsTheLock`).
"""
from __future__ import annotations

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
    INSTALL = "Install from the lock"

    def test_its_input_steps_and_interpreter(self):
        """One required input, the install set; its two steps in order, unconditional, the
        first taking uv from the one recipe for it by the ``$/`` form
        (:mod:`tools.ci.test_dockerfile_uv_action`) and the second a bash step; and the install
        step's interpreter uv's own build, never the runner's.  No cache is restored (ruling
        plan#2471)."""
        action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
        assert action["inputs"].keys() == {"install"}
        assert action["inputs"]["install"]["required"] is True
        assert action["runs"]["using"] == "composite"
        steps = _action_steps()
        assert list(steps) == [self.UV, self.INSTALL]
        assert steps[self.UV] == {"name": self.UV, "id": "uv", "uses": DOCKERFILE_UV}
        assert "if" not in steps[self.INSTALL] and steps[self.INSTALL]["shell"] == "bash"
        assert steps[self.INSTALL]["env"] == {
            "UV": "${{ steps.uv.outputs.bin }}",
            "INSTALL": "${{ inputs.install }}",
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

    def test_uv_s_cache_is_a_directory_of_the_job_s_own(self, tmp_path):
        """The install step, RUN: uv's cache is ``uv-cache`` under the runner's temp directory,
        which the runner empties as each job starts and ends, for every install set and
        whatever cache directory the job's environment names (ruling plan#2471); with no temp
        directory the step fails, running no uv and writing no PATH."""
        uv, log, path = tmp_path / "uv", tmp_path / "uv.log", tmp_path / "github_path"
        uv.write_text(f'#!/bin/sh\necho "${{UV_CACHE_DIR}}" >> "{log}"\n', encoding="utf-8")
        uv.chmod(0o755)
        run = _action_steps()[self.INSTALL]["run"]
        env = {"UV": str(uv), "GITHUB_PATH": str(path), "GITHUB_WORKSPACE": str(tmp_path / "ws"),
               "UV_CACHE_DIR": str(tmp_path / "restored")}
        temp = tmp_path / "temp"
        for install in ("all", "runtime", "landing", "python"):
            log.write_text("", encoding="utf-8")
            result = _run_step(run, {**env, "INSTALL": install, "RUNNER_TEMP": str(temp)})
            assert result.returncode == 0, (install, result.stderr)
            assert log.read_text(encoding="utf-8") == f"{temp}/uv-cache\n", install
        log.write_text("", encoding="utf-8")
        path.write_text("", encoding="utf-8")
        result = _run_step(run, {**env, "INSTALL": "all"})
        assert result.returncode != 0
        assert "RUNNER_TEMP" in result.stderr
        assert log.read_text(encoding="utf-8") == ""
        assert path.read_text(encoding="utf-8") == ""

    def test_each_install_set_runs_its_one_uv_command_then_puts_the_venv_first(self, tmp_path):
        """Each set runs its one uv command, every sync ``--locked`` (its build hashes are
        pyproject.toml's ``require-build-hashes`` to require, task plan#2550), then writes the
        workspace's ``.venv/bin`` and uv's own directory to GITHUB_PATH.  A set the step does
        not name, and a uv that fails, run nothing more and write no PATH."""
        uv, log, path = tmp_path / "uv", tmp_path / "uv.log", tmp_path / "github_path"
        run = _action_steps()[self.INSTALL]["run"]
        sync = "sync --locked"
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
                                         "GITHUB_WORKSPACE": str(tmp_path / "ws"),
                                         "RUNNER_TEMP": str(tmp_path / "temp")})
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
