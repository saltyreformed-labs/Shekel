"""Tests for the version-controlled deployment configurations under deploy/.

Covers remediation Commit C-32 (audit finding F-021): nginx and compose
configs that were running only on the production host are now committed
into the repo. These tests guarantee that:

1. The expected files exist with the structural shape the runbook and
   docker-compose.yml depend on.
2. The bundled-mode Nginx config is syntactically valid (parsed by
   ``nginx -t`` inside an ephemeral nginx:alpine container).
3. The shared-mode compose override parses correctly when merged with
   the base ``docker-compose.yml`` (parsed by ``docker compose config``).

Both subprocess-based tests are skipped when Docker is not available on
the test host (e.g., CI sandboxes that do not expose the Docker socket).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_deploy._compose import BASE_COMPOSE, NGINX_TEST_IMAGE

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"

BUNDLED_NGINX_CONF = DEPLOY_DIR / "nginx-bundled" / "nginx.conf"
SHARED_NGINX_CONF = DEPLOY_DIR / "nginx-shared" / "nginx.conf"
SHARED_VHOST_CONF = DEPLOY_DIR / "nginx-shared" / "conf.d" / "shekel.conf"
PROD_COMPOSE_OVERRIDE = DEPLOY_DIR / "docker-compose.prod.yml"
DEPLOY_README = DEPLOY_DIR / "README.md"

# Subprocess timeout for docker invocations.  Generous because the
# initial image pull on a fresh CI host can take 10-20 seconds, but
# bounded so a stuck docker daemon does not hang the test suite.
DOCKER_SUBPROCESS_TIMEOUT_S = 60


def _docker_available() -> bool:
    """Return True when the ``docker`` CLI is on PATH and the daemon
    answers ``docker info``.

    The test plan calls for nginx and compose validation via subprocess.
    Hosts without a working Docker daemon (typical of locked-down CI
    sandboxes) should skip rather than fail -- the lint/unit suite must
    not depend on container infrastructure.
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


class TestDeployFilesExist:
    """Filesystem-existence and shape checks for the deploy/ tree.

    The repo's docker-compose.yml mounts the bundled nginx.conf into the
    shekel-prod-nginx container, and deploy/README.md / docs/runbook.md
    point operators at every shared-mode file.  A missing or misshapen
    file here is a deployment-integrity bug, not a unit-test failure.
    """

    def test_bundled_nginx_conf_exists_and_is_substantive(self) -> None:
        """deploy/nginx-bundled/nginx.conf must exist and be the
        non-empty Nginx main config the bundled compose service mounts.
        """
        assert BUNDLED_NGINX_CONF.is_file(), (
            f"bundled nginx.conf missing at {BUNDLED_NGINX_CONF}; "
            "docker-compose.yml mount target will fail"
        )
        text = BUNDLED_NGINX_CONF.read_text(encoding="utf-8")
        # Required structural directives.
        assert "worker_processes" in text
        assert "events {" in text
        assert "http {" in text
        # Bundled mode terminates upstream at the in-stack app service.
        assert "upstream gunicorn" in text
        assert "server app:8000" in text

    def test_shared_nginx_conf_exists_and_includes_vhost_dir(self) -> None:
        """deploy/nginx-shared/nginx.conf must include the conf.d
        directory so the shekel vhost is loaded under shared mode.
        """
        assert SHARED_NGINX_CONF.is_file(), (
            f"shared nginx.conf missing at {SHARED_NGINX_CONF}; "
            "shared-mode runtime config has no source of truth"
        )
        text = SHARED_NGINX_CONF.read_text(encoding="utf-8")
        assert "events {" in text
        assert "http {" in text
        # Shared mode loads per-service vhosts via conf.d include.
        assert "include /etc/nginx/conf.d/*.conf;" in text

    def test_shared_vhost_targets_app_container(self) -> None:
        """deploy/nginx-shared/conf.d/shekel.conf must proxy to the
        shekel-prod-app container on the homelab network on port 8000.
        """
        assert SHARED_VHOST_CONF.is_file(), (
            f"shared shekel.conf missing at {SHARED_VHOST_CONF}; "
            "shared-mode reverse proxy has no vhost"
        )
        text = SHARED_VHOST_CONF.read_text(encoding="utf-8")
        assert "shekel-prod-app:8000" in text
        # The vhost terminates TLS, so it MUST listen on 443 ssl.
        assert "listen 443 ssl;" in text

    def test_prod_compose_override_disables_bundled_nginx(self) -> None:
        """deploy/docker-compose.prod.yml must park the bundled nginx
        service in the disabled profile and join app to the dedicated
        ``shekel-frontend`` bridge.  These two changes are the
        load-bearing differences between bundled and shared mode.

        Note: prior to Commit C-33 the override joined the app to the
        wider ``homelab`` network; F-020/F-129 mandated moving Shekel
        onto its own bridge so co-tenants (jellyfin, immich, unifi)
        could no longer reach Gunicorn directly.  See
        tests/test_deploy/test_proxy_trust_and_headers.py
        TestProdComposeNetworkTopology for the topology assertions in
        full.
        """
        assert PROD_COMPOSE_OVERRIDE.is_file(), (
            f"prod compose override missing at {PROD_COMPOSE_OVERRIDE}; "
            "shared-mode runtime override has no source of truth"
        )
        text = PROD_COMPOSE_OVERRIDE.read_text(encoding="utf-8")
        assert 'profiles: ["disabled"]' in text
        assert "shekel-frontend:" in text
        assert "external: true" in text

    def test_deploy_readme_exists(self) -> None:
        """deploy/README.md is the operator-facing guide that the root
        README.md and the runbook reference.  A missing file would
        break those navigation paths."""
        assert DEPLOY_README.is_file(), (
            f"deploy/README.md missing at {DEPLOY_README}"
        )

    def test_root_compose_mount_uses_committed_path(self) -> None:
        """docker-compose.yml must mount the bundled config from its
        committed location.  Catches accidental reverts to the old
        ./nginx/nginx.conf path (the directory no longer exists)."""
        text = BASE_COMPOSE.read_text(encoding="utf-8")
        assert (
            "./deploy/nginx-bundled/nginx.conf:/etc/nginx/nginx.conf:ro"
            in text
        )
        assert "./nginx/nginx.conf:" not in text


@pytest.mark.docker
class TestDeployNginxConfigParses:
    """Validate that the bundled Nginx config passes ``nginx -t``.

    The shared-mode config is intentionally not validated here because
    its vhost references TLS certificate paths that exist only on the
    production host.  Full shared-mode validation is the responsibility
    of remediation Commit C-33 (which adds the security headers and
    can stage a self-signed cert as part of its test plan).
    """

    def test_deploy_nginx_config_parses(self) -> None:
        """``nginx -t`` against deploy/nginx-bundled/nginx.conf must
        exit 0 inside an ephemeral container of the compose's bundled
        nginx image (``NGINX_TEST_IMAGE``).
        """
        if not _docker_available():
            pytest.skip("docker not available; cannot run nginx -t in container")

        # Run an ephemeral container with the repo nginx.conf mounted
        # read-only at the same path used by the bundled service.
        # ``--rm`` ensures cleanup; ``--entrypoint`` overrides the
        # image's default entrypoint so we run nginx -t directly
        # without starting workers.
        #
        # The bundled config's ``upstream gunicorn { server app:8000; }``
        # is resolved by Nginx at startup, including under ``-t``.  In
        # production, Docker's embedded DNS resolves the ``app`` service
        # name to the gunicorn container.  In this test we are running
        # a one-shot container in isolation, so we pin ``app`` to the
        # loopback address with ``--add-host``.  Nginx accepts any
        # resolvable address; the actual reachability is irrelevant to
        # syntax validation.
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--add-host",
                "app:127.0.0.1",
                "-v",
                f"{BUNDLED_NGINX_CONF}:/etc/nginx/nginx.conf:ro",
                "--entrypoint",
                "nginx",
                NGINX_TEST_IMAGE,
                "-t",
                "-c",
                "/etc/nginx/nginx.conf",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        # ``nginx -t`` writes its results to stderr regardless of
        # success; surface both streams on failure for the developer.
        assert result.returncode == 0, (
            "nginx -t failed for deploy/nginx-bundled/nginx.conf:\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        # ``-t`` prints "syntax is ok" and "test is successful" on a
        # passing config.  Assert the explicit success message rather
        # than the exit code alone -- a corrupt image would also
        # exit 0 from a no-op entrypoint.
        assert "syntax is ok" in result.stderr
        assert "test is successful" in result.stderr


@pytest.mark.docker
class TestDeployComposeParses:
    """Validate that ``docker compose config`` accepts the merged
    base + shared-mode override.

    A YAML or schema error in deploy/docker-compose.prod.yml would
    surface only at deploy time; this test catches it earlier.
    """

    def test_deploy_compose_parses(self) -> None:
        """``docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml config``
        must succeed and the resulting merged config must place the
        nginx service in the ``disabled`` profile while joining app to
        the ``homelab`` network.
        """
        if not _docker_available():
            pytest.skip("docker not available; cannot run docker compose config")

        # The base docker-compose.yml uses ${VAR:?error message} for
        # required secrets.  ``compose config`` performs interpolation
        # before validation, so unset values fail the parse even though
        # the test only checks structure.  Provide synthetic non-empty
        # values for the required variables.  These never reach a
        # running container -- ``config`` only renders the merged YAML.
        # ``--env-file /dev/null`` ensures the developer's real .env
        # at the repo root is NOT read into this subprocess (which
        # would leak production-shaped values into pytest output if
        # ``compose config`` ever started echoing them).
        env = {
            "PATH": "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
            "POSTGRES_PASSWORD": "test-postgres-password",
            "SECRET_KEY": "test-secret-key",
            "APP_ROLE_PASSWORD": "test-app-role-password",
            # Synthetic image digest satisfies the Commit C-36 / F-060
            # required-form interpolation in deploy/docker-compose.prod.yml
            # (``image: ghcr.io/saltyreformed-labs/shekel@${SHEKEL_IMAGE_DIGEST:?...}``).
            # 64 hex chars is the canonical OCI digest shape; the
            # value never reaches a real pull because ``compose config``
            # only renders the merged YAML.
            "SHEKEL_IMAGE_DIGEST": "sha256:" + "0" * 64,
            # The Valkey password the app's URI and the valkey service
            # interpolate (``${SHEKEL_VALKEY_PASSWORD:?...}``).
            "SHEKEL_VALKEY_PASSWORD": "test-valkey-password",
        }
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                str(BASE_COMPOSE),
                "-f",
                str(PROD_COMPOSE_OVERRIDE),
                "config",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )
        assert result.returncode == 0, (
            "docker compose config failed for the merged base + "
            "shared-mode override:\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        merged = result.stdout
        # Behavioural assertions on the merged result -- catches a
        # silently-malformed override that parses but does not have
        # the intended effect.
        #
        # ``docker compose config`` filters services that are in a
        # profile when that profile is not activated.  Because the
        # override puts the nginx service in the "disabled" profile
        # and we did not pass ``--profile disabled``, the resulting
        # services list MUST NOT include nginx -- proving the override
        # took effect.  Match the YAML-indented service header so a
        # stray "nginx" string elsewhere in the merged output (e.g.
        # an environment variable value) does not produce a false pass.
        #
        # ``shekel-frontend`` is the dedicated bridge introduced by
        # Commit C-33 to replace the wider ``homelab`` network the
        # earlier override joined (audit findings F-020/F-129).  The
        # merged compose must declare it as an external network so
        # the operator's ``docker network create shekel-frontend``
        # is the source of truth.
        assert "shekel-frontend" in merged, (
            "merged compose missing shekel-frontend network reference; "
            "shared-mode app routing would not work after the C-33 "
            "topology change"
        )
        assert "homelab" not in merged, (
            "merged compose still references the homelab network; "
            "C-33 (F-020/F-129) regression -- the app must NOT join "
            "the shared homelab bridge"
        )
        assert "\n  nginx:" not in merged, (
            "merged compose still defines a top-level nginx service; "
            "shared-mode would still start the bundled nginx.  Verify "
            "that deploy/docker-compose.prod.yml parks the nginx "
            "service in the 'disabled' profile"
        )
        # Sanity check: app and db survived the merge (we did not
        # accidentally filter them).
        assert "\n  app:" in merged
        assert "\n  db:" in merged

        # Re-run with the disabled profile activated to confirm the
        # nginx service definition is otherwise still attached to the
        # override (catches the regression where someone deletes the
        # nginx block entirely instead of disabling its profile).
        result_with_profile = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "--profile",
                "disabled",
                "-f",
                str(BASE_COMPOSE),
                "-f",
                str(PROD_COMPOSE_OVERRIDE),
                "config",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )
        assert result_with_profile.returncode == 0, (
            "docker compose config (with --profile disabled) failed:\n"
            f"  stdout: {result_with_profile.stdout}\n"
            f"  stderr: {result_with_profile.stderr}"
        )
        with_profile = result_with_profile.stdout
        assert "\n  nginx:" in with_profile, (
            "nginx service is missing entirely from the merged compose "
            "even with --profile disabled -- the override should disable "
            "it, not delete it"
        )
        assert "disabled" in with_profile, (
            "nginx service does not declare the 'disabled' profile when "
            "rendered with --profile disabled; the override is malformed"
        )
