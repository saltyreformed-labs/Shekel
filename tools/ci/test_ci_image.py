"""Controls for ``ci.yml``'s ``image`` job: the app image is built and checked before a merge.

``docker-publish.yml`` builds the release image only on a push to ``main``, so before task
plan#2366 a change that broke the build landed in ``dev`` and failed only at the release.  The
job builds the image on every pull request, queue group and push to ``main``, never pushes it,
and checks that it runs psycopg's C implementation and holds exactly the lock's runtime set and
its ``server`` group; ``lint-and-test`` grades it (:mod:`tools.ci.ci_verdict`).
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

import yaml

from tools.ci import arcs
from tools.ci.test_ci_workflow import _jobs, _run_step, _steps, _workflows


class TestTheImageIsBuiltAndCheckedBeforeAMerge:
    """The ``image`` job: the app image built from the Dockerfile on every pull request,
    queue group and push to ``main``, never pushed, and what it holds checked (task
    plan#2366).

    Its three ``run`` steps are RUN here with a stand-in ``docker``; that each check refuses
    a wrong image is measured against real images (built 2026-10-30: an extra package, a
    missing one and no psycopg-c each refused) and is graded on every run by the job itself.
    """

    BUILDX = "Set up Docker Buildx"
    BUILD = "Build the image"
    EXPORT = "Export the Dockerfile's uv"
    IMPL = "The image runs psycopg's C implementation"
    PACKAGES = "The image holds exactly the lock's runtime and server set"
    #: The local name the image is loaded under.
    IMAGE = "shekel:ci"

    #: A stand-in ``docker``: it writes its arguments to ``LOG`` as JSON, runs the code a
    #: ``-c`` hands it with this Python, and otherwise exits ``DOCKER_STATUS`` (0 unset).
    DOCKER = (
        "import json, os, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['LOG'], 'w', encoding='utf-8') as log:\n"
        "    json.dump(args, log)\n"
        "if '-c' in args:\n"
        "    sys.exit(subprocess.run([sys.executable, '-c', args[args.index('-c') + 1]],\n"
        "                            check=False).returncode)\n"
        "sys.exit(int(os.environ.get('DOCKER_STATUS', '0')))\n"
    )

    def _docker(self, tmp_path: Path) -> dict[str, str]:
        """Install the stand-in ``docker`` in ``tmp_path``; the env a step runs it under."""
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        docker = bindir / "docker"
        docker.write_text(f"#!{sys.executable}\n{self.DOCKER}", encoding="utf-8")
        docker.chmod(0o755)
        return {"PATH": f"{bindir}:/usr/bin:/bin", "LOG": str(tmp_path / "docker.json"),
                "IMAGE": self.IMAGE}

    @staticmethod
    def _called(tmp_path: Path) -> list[str]:
        """The arguments the stand-in ``docker`` was last called with."""
        return json.loads((tmp_path / "docker.json").read_text(encoding="utf-8"))

    def test_the_image_is_built_as_the_release_builds_it_and_never_pushed(self):
        """The Dockerfile at the checkout's root, built by the release's own two pinned docker
        actions, loaded into the runner's docker under :attr:`IMAGE` and never pushed.  The
        job holds no secret and widens no permission, and its steps run in this order."""
        job = _jobs()["image"]
        assert set(job) == {"runs-on", "timeout-minutes", "env", "steps"}
        assert job["env"] == {"IMAGE": self.IMAGE}
        assert [step["name"] for step in job["steps"]] == [
            "Checkout code", self.BUILDX, self.BUILD, self.EXPORT, self.IMPL, self.PACKAGES,
        ]
        steps = _steps("image")
        assert steps["Checkout code"]["with"] == {"persist-credentials": False}
        assert steps[self.BUILD]["with"] == {
            "context": ".", "push": False, "load": True, "tags": "${{ env.IMAGE }}",
            "cache-from": "type=gha",
        }
        publish = _workflows()["docker-publish.yml"]["jobs"]["build-and-push"]
        release = {step["uses"].split("@")[0]: step["uses"] for step in publish["steps"]
                   if "uses" in step}
        for name in (self.BUILDX, self.BUILD):
            uses = steps[name]["uses"]
            assert re.fullmatch(r"docker/[\w.-]+@[0-9a-f]{40}", uses), name
            assert release[uses.split("@")[0]] == uses, name
        assert not re.search(r"\bsecrets\b", yaml.safe_dump(job))

    def test_the_layer_cache_is_read_and_never_written(self):
        """No step of the job writes a cache: a pull request's or a queue group's is
        restorable by its own ref alone, and one a push to ``main`` wrote would sit in the
        scope the release workflow restores."""
        for step in _jobs()["image"]["steps"]:
            text = yaml.safe_dump(step)
            assert "cache-to" not in text and "actions/cache@" not in text, step["name"]

    def test_uv_is_exported_from_the_dockerfiles_own_uv_stage(self, tmp_path):
        """The export step, RUN: buildx writes the Dockerfile's ``uv`` stage, which holds the
        binary alone, to the runner's temp directory, where the package check mounts it."""
        assert re.search(r"^FROM \S+ AS uv$",
                         (arcs.REPO / "Dockerfile").read_text(encoding="utf-8"), re.MULTILINE)
        env = self._docker(tmp_path)
        temp = tmp_path / "temp"
        result = _run_step(_steps("image")[self.EXPORT]["run"], {**env, "RUNNER_TEMP": str(temp)})
        assert result.returncode == 0, result.stderr
        assert self._called(tmp_path) == [
            "buildx", "build", "--target", "uv", "--output", f"type=local,dest={temp}/uv", ".",
        ]

    def test_the_psycopg_check_passes_the_c_implementation_alone(self, tmp_path):
        """The psycopg step, RUN: in the image, offline, it reads which implementation psycopg
        loaded and passes ``c`` alone; another implementation, or an import that fails, fails
        the step.  A stand-in ``psycopg`` reports each implementation."""
        env = self._docker(tmp_path)
        run = _steps("image")[self.IMPL]["run"]
        for impl, expected in (("c", 0), ("python", 1), ("binary", 1), (None, 1)):
            # A directory of its own per case: one module rewritten in place within a second
            # at the same size would be read back from its stale bytecode.
            fake = tmp_path / f"site-{impl}" / "psycopg"
            fake.mkdir(parents=True)
            body = ("raise ImportError('no implementation')\n" if impl is None
                    else f"import types\npq = types.SimpleNamespace(__impl__={impl!r})\n")
            (fake / "__init__.py").write_text(body, encoding="utf-8")
            result = _run_step(run, {**env, "PYTHONPATH": str(fake.parent)})
            assert result.returncode == expected, (impl, result.stdout, result.stderr)
            args = self._called(tmp_path)
            assert args[:8] == ["run", "--rm", "--network", "none", "--entrypoint", "python",
                                self.IMAGE, "-c"], impl
            if impl is not None:
                assert f"psycopg implementation: {impl}" in result.stdout, impl

    def test_the_package_check_runs_uv_in_the_image_against_the_lock(self, tmp_path):
        """The package step, RUN: uv, mounted read-only into the image with the checkout, run
        offline against the image's own ``/opt/venv``, checking (never changing) it against
        the lock's runtime set and its ``server`` group; and uv's refusal fails the step."""
        env = self._docker(tmp_path)
        temp, workspace = tmp_path / "temp", tmp_path / "ws"
        run = _steps("image")[self.PACKAGES]["run"]
        step_env = {**env, "RUNNER_TEMP": str(temp), "GITHUB_WORKSPACE": str(workspace)}
        assert _run_step(run, step_env).returncode == 0
        args = self._called(tmp_path)
        assert args[:4] == ["run", "--rm", "--network", "none"]
        image = args.index(self.IMAGE)
        options = args[4:image]
        assert options[-2:] == ["--entrypoint", "uv"]
        pairs = list(zip(options[:-2:2], options[1:-2:2]))
        assert sorted(value for flag, value in pairs if flag == "--volume") == sorted([
            f"{temp}/uv/uv:/usr/local/bin/uv:ro", f"{workspace}:/src:ro",
        ])
        assert {value for flag, value in pairs if flag == "--env"} == {
            "UV_PROJECT_ENVIRONMENT=/opt/venv", "UV_PYTHON_DOWNLOADS=never", "UV_NO_CACHE=1",
            "UV_OFFLINE=1",
        }
        assert {flag for flag, _value in pairs} == {"--volume", "--env"}
        assert args[image + 1:] == [
            "sync", "--project", "/src", "--locked", "--check", "--no-install-project",
            "--no-default-groups", "--group", "server",
        ]
        project = tomllib.loads((arcs.REPO / "pyproject.toml").read_text(encoding="utf-8"))
        assert "server" in project["dependency-groups"]
        assert _run_step(run, {**step_env, "DOCKER_STATUS": "1"}).returncode != 0
