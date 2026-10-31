"""The bundled services, read from the ONE file that declares them.

``docker-compose.yml``'s ``nginx`` service is the repo's only statement of
which nginx it ships, so the deploy tests that run ``nginx -t`` (or stand a
proxy up) read the tag from there instead of restating it: a Dependabot bump
of the compose (``#434`` moved it 1.27 -> 1.29 while two test modules still
said 1.27) then moves the tested parser in the same commit, with no second
spelling to fall behind.  ``test_valkey_store.py`` starts the ``valkey``
service as that file declares it, for the same reason.

What this is NOT: the version fronting production.  The prod override parks
the bundled service and the shared ``/opt/docker/nginx`` container proxies
Shekel; its tag lives in ``/opt/docker/docker-compose.yml``, outside this
repo, and on 2026-09-20 it ran ``nginx:1.31.6`` against the bundled
``1.29-alpine`` here -- so the shared-vhost parse test grades the config
under a different minor line than production parses it with.  Nothing in
this repo establishes that parity; the docstring states the gap rather
than a wish.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"


def service(name: str) -> dict[str, object]:
    """Return ``docker-compose.yml``'s service ``name`` as parsed.

    Raises:
        KeyError: The compose no longer declares the service -- the tests
            that call this have nothing to test.
    """
    with BASE_COMPOSE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["services"][name]


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
