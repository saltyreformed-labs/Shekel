"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_deploy/test_proxy_trust_and_headers.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

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
