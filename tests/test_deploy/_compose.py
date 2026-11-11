"""The bundled services, read from the ONE file that declares them.

``docker-compose.yml``'s ``nginx`` service is the repo's only statement of
which nginx it ships, so the deploy tests that run ``nginx -t`` (or stand a
proxy up) read the tag from there instead of restating it: a Dependabot bump
of the compose (``#434`` moved it 1.27 -> 1.29 while two test modules still
said 1.27) then moves the tested parser in the same commit, with no second
spelling to fall behind.  ``test_valkey_store.py`` starts the ``valkey``
service, and makes its socket's volume, as that file declares them, for the
same reason.

What this is NOT: the version fronting production.  The prod override parks
the bundled service and the shared ``/opt/docker/nginx`` container proxies
Shekel; its tag lives in ``/opt/docker/docker-compose.yml``, outside this
repo, and on 2026-09-20 it ran ``nginx:1.31.6`` against the bundled
``1.29-alpine`` here -- so the shared-vhost parse test grades the config
under a different minor line than production parses it with.  Nothing in
this repo establishes that parity; the docstring states the gap rather
than a wish.

``config`` renders the compose files as compose merges them, the one way the tests read
the production override on top of the base file (there is no in-Python equivalent of
compose's merge), and ``CONFIG_ENV`` is the one statement of what that render needs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
PROD_OVERRIDE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"

#: A placeholder for each variable a compose file requires (``${VAR:?...}``), so a render
#: interpolates instead of refusing: the base file's five passwords and secret key, and the
#: production override's image digest.  None reaches a container, since ``config`` only
#: renders.  ``PATH`` alone of the caller's environment is kept, and ``--env-file
#: /dev/null`` keeps a developer's ``.env`` out of the render (and so out of any failure
#: message that echoes it).
CONFIG_ENV = {
    "PATH": "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
    "POSTGRES_PASSWORD": "test-postgres-password",
    "SECRET_KEY": "a" * 32,
    "APP_ROLE_PASSWORD": "test-app-role-password",
    "OWNER_ROLE_PASSWORD": "test-owner-role-password",
    "SHEKEL_VALKEY_PASSWORD": "test-valkey-password",
    # 64 hex chars, the canonical OCI digest shape; the render never pulls it.
    "SHEKEL_IMAGE_DIGEST": "sha256:" + "0" * 64,
}
#: How long one render may take.
CONFIG_TIMEOUT_S = 60


def config(*files: Path, env: dict[str, str] | None = None,
           profiles: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    """Render ``files`` merged, as ``docker compose -f ... config`` does at deploy time.

    Args:
        files: The compose files, in the order compose merges them.
        env: The render's whole environment; :data:`CONFIG_ENV` when omitted.
        profiles: Profiles to activate, each passed as ``--profile``.

    Returns:
        The finished ``docker compose`` process, its output captured; its exit status is
        the caller's to judge (a test of a refused render asserts it is not 0).
    """
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for profile in profiles:
        command += ["--profile", profile]
    for path in files:
        command += ["-f", str(path)]
    return subprocess.run([*command, "config"], capture_output=True, text=True,
                          timeout=CONFIG_TIMEOUT_S, check=False, cwd=str(REPO_ROOT),
                          env=CONFIG_ENV if env is None else env)


def rendered_text(*files: Path, profiles: tuple[str, ...] = ()) -> str:
    """Return ``files`` merged and rendered (:func:`config`), as compose prints it.

    Raises:
        AssertionError: The render failed, with compose's output.
    """
    result = config(*files, profiles=profiles)
    assert result.returncode == 0, (
        f"docker compose config failed for {[path.name for path in files]}"
        f" (profiles {list(profiles)}):\n"
        f"  stdout: {result.stdout}\n  stderr: {result.stderr}")
    return result.stdout


def rendered(*files: Path) -> dict[str, object]:
    """Return ``files`` merged and rendered (:func:`config`), parsed.

    Raises:
        AssertionError: The render failed, with compose's output.
    """
    return yaml.safe_load(rendered_text(*files))


def service(name: str) -> dict[str, object]:
    """Return ``docker-compose.yml``'s service ``name`` as parsed.

    Raises:
        KeyError: The compose no longer declares the service -- the tests
            that call this have nothing to test.
    """
    with BASE_COMPOSE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["services"][name]


def volume(name: str) -> dict[str, object]:
    """Return ``docker-compose.yml``'s top-level volume ``name`` as parsed.

    Raises:
        KeyError: The compose no longer declares the volume -- the tests
            that call this have nothing to test.
    """
    with BASE_COMPOSE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["volumes"][name]


def bundled_nginx_image() -> str:
    """Return the ``image:`` of ``docker-compose.yml``'s ``nginx`` service.

    Returns:
        The tag as written, e.g. ``"nginx:1.29-alpine"``.

    Raises:
        KeyError: The compose no longer declares an ``nginx`` service or its
            ``image`` -- the tests that call this have nothing to test.
    """
    return service("nginx")["image"]


# Read once at import: the image every ``nginx -t`` and proxy test runs.
NGINX_TEST_IMAGE = bundled_nginx_image()
