"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_deploy/test_proxy_trust_and_headers.py``, moved verbatim by ``tools/split``, and the
addresses and the nginx directive reader added since.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from tests._repo_root import REPO_ROOT


DEPLOY_DIR = REPO_ROOT / "deploy"


GUNICORN_CONF = REPO_ROOT / "gunicorn.conf.py"


BUNDLED_NGINX_CONF = DEPLOY_DIR / "nginx-bundled" / "nginx.conf"


SHARED_NGINX_CONF = DEPLOY_DIR / "nginx-shared" / "nginx.conf"


SHARED_VHOST_CONF = DEPLOY_DIR / "nginx-shared" / "conf.d" / "shekel.conf"


PROD_COMPOSE_OVERRIDE = DEPLOY_DIR / "docker-compose.prod.yml"


CLOUDFLARED_TEMPLATE = REPO_ROOT / "cloudflared" / "config.yml"


# Pinned subnets used by Commit C-33.  Each test that asserts a CIDR
# match references one of these so a re-pin gets a single point of
# update across the suite.
BUNDLED_BACKEND_CIDR = "172.31.0.0/24"


BUNDLED_FRONTEND_CIDR = "172.30.0.0/24"


SHARED_FRONTEND_CIDR = "172.32.0.0/24"


# cloudflared's pinned address on ``edge-tunnel``, the bridge it shares
# with the shared Nginx and Alloy and nothing else (ruling plan#3942).
# A bare address, not a range: the one peer the shared Nginx believes
# ``CF-Connecting-IP`` from (ruling plan#4066).
CLOUDFLARED_TUNNEL_ADDRESS = "172.34.6.20"


SHARED_CONF_D = DEPLOY_DIR / "nginx-shared" / "conf.d"


# One nginx token, in the order nginx's own reader tries them: a quoted
# string, a comment (a ``#`` that starts a token, to the end of its line),
# a statement end or block brace, then a bare word, which may hold a ``#``.
_NGINX_TOKEN = re.compile(
    r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|#[^\n]*|[;{}]|[^\s;{}"']+"""
)


def nginx_directive_args(conf_text: str, name: str) -> list[list[str]]:
    """Return the arguments of each ``name`` directive in ``conf_text``.

    The text is tokenized as nginx reads it: comments dropped, and each
    directive ended by ``;`` or a brace however its whitespace falls, so
    a space before the ``;``, a directive sharing a line with another, or
    one split over lines is still found.  Quoted arguments keep their
    quotes.
    """
    found: list[list[str]] = []
    statement: list[str] = []
    for token in _NGINX_TOKEN.findall(conf_text):
        if token.startswith("#"):
            continue
        if token in (";", "{", "}"):
            if statement and statement[0] == name:
                found.append(statement[1:])
            statement = []
        else:
            statement.append(token)
    return found


# The four defense-in-depth headers Nginx emits at every server block
# C-33 touches.  Order matches the spec in the commit plan; tests use
# membership checks so file ordering is not load-bearing.
SECURITY_HEADERS = (
    ('X-Content-Type-Options', '"nosniff"'),
    ('X-Frame-Options', '"DENY"'),
    ('Referrer-Policy', '"strict-origin-when-cross-origin"'),
    (
        'Permissions-Policy',
        '"camera=(), microphone=(), geolocation=()"',
    ),
)


DOCKER_SUBPROCESS_TIMEOUT_S = 60


def _docker_available() -> bool:
    """Mirror the helper from C-32's test_deploy_configs.py.

    Returns True only when the ``docker`` CLI is installed and the
    daemon answers ``docker info``.  Hosts without Docker (locked-down
    CI sandboxes) skip subprocess-based tests rather than failing.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0
