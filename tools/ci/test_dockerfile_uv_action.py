"""Controls for ``.github/actions/dockerfile-uv``: the one recipe for the uv binary CI runs.

BuildKit exports the Dockerfile's ``uv`` stage to the runner (task plan#2484): the install
action takes every job's uv from it (:mod:`tools.ci.test_install_action`), and ci.yml's
``image`` job its uv for the package check (:mod:`tools.ci.test_ci_image`).  That the real
export writes the binary the image's builder copies was measured 2026-11-01 on the rootless
daemon (Docker 29.9.2, buildx 0.38.1): the export and a pull of the stage's image by its digest
gave one binary, ``sha256`` ``0baa11c5...``; a Dockerfile with no ``uv`` stage failed the
export; and with two, ``--target uv`` and the builder's ``COPY --from=uv`` took the same one.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from tools.ci import arcs
from tools.ci.test_ci_workflow import _every_step, _run_step

#: The action's directory, and the ``$/`` form GitHub loads it by from the running commit
#: (ruling plan#2375).
DIRECTORY = ".github/actions/dockerfile-uv"
USES = f"$/{DIRECTORY}"
#: The file that defines the action.
ACTION_FILE = arcs.REPO / DIRECTORY / "action.yml"


def _action() -> dict:
    """The action, read."""
    return yaml.safe_load(ACTION_FILE.read_text(encoding="utf-8"))


class TestTheDockerfilesUvIsOneRecipe:
    """The action's one step, read and RUN with a stand-in ``docker``, and its callers."""

    EXPORT = "Export the Dockerfile's uv stage"

    #: A stand-in ``docker``: it records each call, then (unless ``FAIL_BUILD``) writes a
    #: ``uv`` that prints a version and exits ``UV_STATUS`` (0 unset) into the directory its
    #: ``--output`` names, or none at all under ``NO_UV``.
    DOCKER = (
        "#!/bin/sh\n"
        'echo "$*" >> "${LOG}"\n'
        '[ -z "${FAIL_BUILD:-}" ] || exit 1\n'
        'for arg in "$@"; do\n'
        '    case "${arg}" in type=local,dest=*) dest="${arg#type=local,dest=}" ;; esac\n'
        "done\n"
        'mkdir -p "${dest}"\n'
        '[ -n "${NO_UV:-}" ] && exit 0\n'
        "printf '#!/bin/sh\\necho \"uv 9.9.9\"\\nexit %s\\n' \"${UV_STATUS:-0}\" \\\n"
        '    > "${dest}/uv"\n'
        'chmod +x "${dest}/uv"\n'
    )

    def test_its_one_output_is_the_binary_from_its_one_step(self):
        """No input; one output, the binary's path, from its one ``bash`` step, which reads no
        secret or token and writes neither GITHUB_ENV nor GITHUB_PATH: which directory a job
        finds uv in is its caller's to say."""
        action = _action()
        assert set(action) == {"name", "description", "outputs", "runs"}
        assert action["outputs"] == {"bin": {
            "description": "The exported uv binary's path.",
            "value": "${{ steps.export.outputs.bin }}"}}
        assert action["runs"]["using"] == "composite"
        step, = action["runs"]["steps"]
        assert set(step) == {"name", "id", "shell", "run"}
        assert (step["name"], step["id"], step["shell"]) == (self.EXPORT, "export", "bash")
        text = yaml.safe_dump(step)
        assert "GITHUB_ENV" not in text and "GITHUB_PATH" not in text
        assert not re.search(r"\bsecrets\b|\btoken\b", text)

    def test_the_install_action_and_the_image_job_alone_call_it_by_the_self_repository_form(
            self):
        """Its callers, in every workflow and every action of this repository's: the install
        action's first step and ci.yml's ``image`` job, each by ``$/``, which GitHub loads from
        the running commit, never ``./``, which reads whatever the checkout holds."""
        callers = set()
        for where, step in _every_step():
            if "dockerfile-uv" in step.get("uses", ""):
                assert step["uses"] == USES, where
                callers.add(where)
        assert callers == {("ci.yml", "image"), ("install-lock", 0)}

    def _export(self, tmp_path: Path, **env: str):
        """RUN the step in a checkout directory: the result, what ``docker`` was asked, and
        what the step wrote to its outputs."""
        work, bindir, temp = tmp_path / "work", tmp_path / "bin", tmp_path / "temp"
        for folder in (work, bindir, temp):
            folder.mkdir(parents=True)
        (bindir / "docker").write_text(self.DOCKER, encoding="utf-8")
        (bindir / "docker").chmod(0o755)
        log, output = tmp_path / "docker.log", tmp_path / "github_output"
        for file in (log, output):
            file.write_text("", encoding="utf-8")
        step, = _action()["runs"]["steps"]
        result = _run_step(step["run"], {
            "PATH": f"{bindir}:/usr/bin:/bin", "LOG": str(log), "RUNNER_TEMP": str(temp),
            "GITHUB_OUTPUT": str(output), **env}, cwd=work)
        return result, log.read_text(encoding="utf-8").splitlines(), output.read_text(
            encoding="utf-8")

    def test_it_exports_the_stage_the_dockerfile_copies_its_uv_from(self, tmp_path):
        """RUN: buildx builds the ``uv`` stage alone from the checkout's Dockerfile and writes
        its files to the runner's temp directory; the step prints the exported uv's version
        and hands on its path.  The Dockerfile holds that stage, and its builder copies uv
        from it, so the binary is the one the image's build runs."""
        lines = (arcs.REPO / "Dockerfile").read_text(encoding="utf-8").splitlines()
        assert [line for line in lines if re.fullmatch(r"FROM \S+ AS uv", line)]
        assert "COPY --from=uv /uv /usr/local/bin/uv" in lines
        result, calls, output = self._export(tmp_path)
        assert result.returncode == 0, result.stderr
        uv = tmp_path / "temp" / "uv"
        assert calls == [f"buildx build --target uv --output type=local,dest={uv} ."]
        assert result.stdout.splitlines()[-1] == "uv 9.9.9"
        assert output == f"bin={uv / 'uv'}\n"

    def test_a_failed_export_or_a_uv_that_does_not_run_hands_nothing_on(self, tmp_path):
        """A build that fails, one that writes no ``uv``, and a ``uv`` that fails to print its
        version each fail the step, and it hands no path on.  Each case runs in a directory of
        its own, so no case's ``uv`` is left for the next to find."""
        for at, env in enumerate(({"FAIL_BUILD": "1"}, {"NO_UV": "1"}, {"UV_STATUS": "1"})):
            result, calls, output = self._export(tmp_path / str(at), **env)
            assert result.returncode != 0, env
            assert len(calls) == 1 and output == "", env
