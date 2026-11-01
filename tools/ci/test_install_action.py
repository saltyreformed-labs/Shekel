"""Controls for ``.github/actions/install-lock``: its own steps, read and RUN.

Which jobs call the action, and with what, is ``test_ci_workflow``'s
(:class:`tools.ci.test_ci_workflow.TestEveryJobInstallsTheLock`).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from tools.ci import arcs
from tools.ci.test_ci_workflow import _run_step


#: The file that defines the install action.
INSTALL_LOCK_FILE = arcs.REPO / ".github/actions/install-lock/action.yml"


def _action_steps() -> dict[str, dict]:
    """The install action's steps, keyed by name."""
    action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
    return {step["name"]: step for step in action["runs"]["steps"]}


class TestTheInstallActionInstallsTheLock:
    """The action's own steps, read and RUN with stand-ins for ``docker`` and ``uv``."""

    COPY = "Copy uv out of the Dockerfile's uv image"
    CACHE = "Restore uv's cache"
    INSTALL = "Install from the lock"

    #: A stand-in ``docker``: it records each call, hands ``create`` a container id, and
    #: writes a runnable ``uv`` where ``cp`` is told to; ``FAIL_PULL`` fails the pull.
    DOCKER = (
        "#!/bin/sh\n"
        'echo "$*" >> "${LOG}"\n'
        'case "$1" in\n'
        '    pull) [ -z "${FAIL_PULL:-}" ] || exit 1 ;;\n'
        "    create) echo c0ffee ;;\n"
        '    cp) printf \'#!/bin/sh\\necho "uv 9.9.9"\\n\' > "$3" && chmod +x "$3" ;;\n'
        'esac\n'
    )

    def test_its_inputs_steps_and_pins(self):
        """Two required inputs; its three steps in order; every third-party action pinned by
        sha; GitHub's own cache action, run only when the caller asks for exactly
        ``true``, keyed on the install set, uv's digest, the lock and ``.python-version``;
        and the install step's interpreter uv's own build, never the runner's."""
        action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
        assert action["inputs"].keys() == {"install", "cache"}
        assert all(spec["required"] is True for spec in action["inputs"].values())
        assert action["runs"]["using"] == "composite"
        steps = _action_steps()
        assert list(steps) == [self.COPY, self.CACHE, self.INSTALL]
        for step in steps.values():
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["name"]
            else:
                assert step["shell"] == "bash", step["name"]
        cache = steps[self.CACHE]
        assert cache["uses"].startswith("actions/cache@")
        assert cache["if"] == "inputs.cache == 'true'"
        key = "uv-${{ runner.os }}-${{ inputs.install }}-${{ steps.uv.outputs.digest }}-"
        assert cache["with"] == {
            "path": "${{ steps.uv.outputs.cache }}",
            "key": key + "${{ hashFiles('uv.lock', '.python-version') }}",
            "restore-keys": key + "\n",
        }
        assert "if" not in steps[self.COPY] and "if" not in steps[self.INSTALL]
        assert steps[self.INSTALL]["env"] == {
            "UV": "${{ steps.uv.outputs.bin }}",
            "INSTALL": "${{ inputs.install }}",
            "UV_CACHE_DIR": "${{ steps.uv.outputs.cache }}",
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

    def _copy(self, tmp_path: Path, dockerfile: str, **env: str):
        """RUN the copy step in a directory holding ``dockerfile``: the result, what
        ``docker`` was asked, and what the step wrote to its outputs."""
        work, bindir, temp = tmp_path / "work", tmp_path / "bin", tmp_path / "temp"
        for folder in (work, bindir, temp):
            folder.mkdir(exist_ok=True)
        (work / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (bindir / "docker").write_text(self.DOCKER, encoding="utf-8")
        (bindir / "docker").chmod(0o755)
        log, output = tmp_path / "docker.log", tmp_path / "github_output"
        for file in (log, output):
            file.write_text("", encoding="utf-8")
        result = _run_step(_action_steps()[self.COPY]["run"], {
            "PATH": f"{bindir}:/usr/bin:/bin", "LOG": str(log), "RUNNER_TEMP": str(temp),
            "GITHUB_OUTPUT": str(output), **env}, cwd=work)
        return result, log.read_text(encoding="utf-8").splitlines(), output.read_text(
            encoding="utf-8")

    def test_uv_is_copied_from_the_dockerfiles_own_uv_image_by_its_digest(self, tmp_path):
        """With the real Dockerfile the step pulls its uv image by tag and digest, copies
        ``/uv`` out of it, and hands the binary, the digest and the cache directory on."""
        dockerfile = (arcs.REPO / "Dockerfile").read_text(encoding="utf-8")
        # Read here by splitting the line, not by the step's expression.
        line, = (row for row in dockerfile.splitlines()
                 if row.startswith("FROM ghcr.io/astral-sh/uv:"))
        image = line.split()[1]
        digest = image.split("@sha256:")[1]
        result, calls, output = self._copy(tmp_path, dockerfile)
        assert result.returncode == 0, result.stderr
        uv = tmp_path / "temp" / "uv" / "uv"
        assert calls == [f"pull --quiet {image}", f"create {image} /uv",
                         f"cp c0ffee:/uv {uv}", "rm c0ffee"]
        assert result.stdout.splitlines()[-1] == "uv 9.9.9"
        assert output == (f"bin={uv}\ndigest={digest}\n"
                          f"cache={tmp_path / 'temp' / 'uv-cache'}\n")

    def test_a_dockerfile_without_exactly_one_pinned_uv_line_is_refused(self, tmp_path):
        """No uv line, two, a tag with no digest, a short digest or a line that names no
        ``uv`` stage: the step refuses before calling ``docker`` and hands nothing on; and
        a pull that fails fails the step, handing nothing on."""
        pinned = "FROM ghcr.io/astral-sh/uv:0.1.0@sha256:" + "a" * 64 + " AS uv\n"
        refused = {
            "FROM python:3.14-slim\n": "it holds 0",
            pinned + pinned: "it holds 2",
            "FROM ghcr.io/astral-sh/uv:0.1.0 AS uv\n": "it holds 0",
            pinned.replace("a" * 64, "a" * 63): "it holds 0",
            pinned.replace(" AS uv", ""): "it holds 0",
            pinned.replace(" AS uv", " AS tools"): "it holds 0",
        }
        for dockerfile, reason in refused.items():
            result, calls, output = self._copy(tmp_path, dockerfile)
            assert result.returncode == 1, dockerfile
            assert reason in result.stderr, (dockerfile, result.stderr)
            assert calls == [] and output == "", dockerfile
        result, calls, output = self._copy(tmp_path, pinned, FAIL_PULL="1")
        assert result.returncode != 0
        assert calls == ["pull --quiet " + pinned.split()[1]] and output == ""

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
