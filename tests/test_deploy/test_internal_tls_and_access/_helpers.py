"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_deploy/test_internal_tls_and_access.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

import shutil
import subprocess

from tests._repo_root import REPO_ROOT


DEPLOY_DIR = REPO_ROOT / "deploy"


CLOUDFLARED_CONFIG = REPO_ROOT / "cloudflared" / "config.yml"


PROD_COMPOSE_OVERRIDE = DEPLOY_DIR / "docker-compose.prod.yml"


BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"


ENTRYPOINT = REPO_ROOT / "entrypoint.sh"


GENERATE_CERT_SCRIPT = REPO_ROOT / "scripts" / "generate_pg_cert.sh"


POSTGRES_DIR = DEPLOY_DIR / "postgres"


POSTGRES_README = POSTGRES_DIR / "README.md"


GITIGNORE = REPO_ROOT / ".gitignore"


RUNBOOK = REPO_ROOT / "docs" / "runbook.md"


DOCKER_SUBPROCESS_TIMEOUT_S = 60


def _docker_available() -> bool:
    """Return True when the ``docker`` CLI is on PATH and the daemon
    answers ``docker info``.  Mirrors the helper in the sibling
    test_deploy_configs.py / test_container_hardening.py modules.
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
