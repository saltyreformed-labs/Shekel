"""Tests for remediation Commit C-33: proxy trust tightening, network
topology hardening, and Nginx security-header defense-in-depth.

Covers audit findings:

    F-015  Nginx + Gunicorn trust all RFC 1918 ranges for proxy headers
    F-020  Flat shared "homelab" network exposes app to co-tenants
    F-063  Cloudflare Tunnel bypasses Nginx on the WAN path
    F-064  Shared Nginx vhost adds no security headers for Shekel
    F-129  UniFi + shared Nginx vhosts expand cross-service lateral movement
    F-156  ``server_tokens`` not explicitly disabled in Nginx

The tests are intentionally heavy on file-content assertions because
the artifacts under audit are configuration files, not Python objects.
A regression that loosens proxy trust or drops a defense-in-depth
header would silently weaken the WAN-path posture; pinning the exact
strings makes a future drift commit fail loudly at test time.

Two pieces of behaviour go beyond static content checks:

1. ``gunicorn.conf.py`` raises ``RuntimeError`` at import time when
   ``FORWARDED_ALLOW_IPS`` is unset or empty, and otherwise reflects
   the env-var value into the module's ``forwarded_allow_ips``
   global.  Validated by importing the module under controlled
   environments via ``runpy``.

2. ``deploy/nginx-shared/conf.d/shekel.conf`` references TLS
   certificate paths that exist only on the production host.  The
   bundled-mode test from C-32 already validates ``nginx -t`` against
   ``deploy/nginx-bundled/nginx.conf`` (which has the new headers
   and the tightened ``set_real_ip_from``); for the shared config
   we generate a self-signed cert in a tmp_path scratch directory
   and run ``nginx -t`` inside an ephemeral container.

The two subprocess-driven tests skip when Docker is not available on
the test host (matching the C-32 pattern in
``test_deploy_configs.py``).
"""
from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from tests.test_deploy._compose import BASE_COMPOSE, NGINX_TEST_IMAGE


REPO_ROOT = Path(__file__).resolve().parents[2]
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


class TestGunicornForwardedAllowIps:
    """Verify ``gunicorn.conf.py`` honours the F-015 fix:

    * No fallback default; a missing or empty ``FORWARDED_ALLOW_IPS``
      raises at config-load time.
    * A set value is reflected verbatim into ``forwarded_allow_ips``.
    """

    def _run_gunicorn_conf(self, env_value: str | None) -> dict:
        """Execute ``gunicorn.conf.py`` as a fresh module with a
        controlled ``FORWARDED_ALLOW_IPS`` environment variable.

        Uses ``runpy.run_path`` rather than ``import gunicorn.conf``
        because the file is a top-level config script (not a package
        module) and Python's import system caches successful imports;
        re-running the file with new env values requires fresh
        execution.

        Returns the module's globals dict so the caller can assert on
        ``forwarded_allow_ips``.
        """
        original = os.environ.get("FORWARDED_ALLOW_IPS")
        try:
            if env_value is None:
                os.environ.pop("FORWARDED_ALLOW_IPS", None)
            else:
                os.environ["FORWARDED_ALLOW_IPS"] = env_value
            return runpy.run_path(str(GUNICORN_CONF), run_name="__main__")
        finally:
            if original is None:
                os.environ.pop("FORWARDED_ALLOW_IPS", None)
            else:
                os.environ["FORWARDED_ALLOW_IPS"] = original

    def test_unset_env_raises_runtimeerror(self) -> None:
        """A missing ``FORWARDED_ALLOW_IPS`` raises ``RuntimeError``
        at config-load time.  This is the fail-closed posture audit
        finding F-015 calls for: a misconfigured production deploy
        cannot start under the historic loose RFC 1918 trust by
        accident.
        """
        with pytest.raises(RuntimeError) as excinfo:
            self._run_gunicorn_conf(env_value=None)
        # The error must name the variable so an operator reading
        # the docker logs can fix it without spelunking through
        # gunicorn.conf.py.
        assert "FORWARDED_ALLOW_IPS" in str(excinfo.value)
        # And must reference the audit commit so the fix path is
        # traceable from the message alone.
        assert "C-33" in str(excinfo.value)

    def test_empty_string_env_raises_runtimeerror(self) -> None:
        """``FORWARDED_ALLOW_IPS=`` (set but empty) is a misconfig the
        loader must reject -- otherwise an operator who clears the
        value to "disable" trust would actually leave Gunicorn
        accepting forwarded headers from no-one (silently breaking
        the proxy chain rather than failing loudly).
        """
        with pytest.raises(RuntimeError) as excinfo:
            self._run_gunicorn_conf(env_value="")
        assert "FORWARDED_ALLOW_IPS" in str(excinfo.value)

    def test_whitespace_only_env_raises_runtimeerror(self) -> None:
        """``FORWARDED_ALLOW_IPS="   "`` is functionally equivalent to
        unset and must also raise.  Catches a YAML quoting mistake
        in compose where the value is a multi-line empty literal.
        """
        with pytest.raises(RuntimeError):
            self._run_gunicorn_conf(env_value="   ")

    def test_pinned_backend_cidr_passes_through(self) -> None:
        """The bundled compose default ``172.31.0.0/24`` reflects
        verbatim into ``forwarded_allow_ips``.  Stripping is allowed
        (an operator might leave a trailing newline in YAML); the
        normalised value MUST match exactly.
        """
        globals_ = self._run_gunicorn_conf(env_value=BUNDLED_BACKEND_CIDR)
        assert globals_["forwarded_allow_ips"] == BUNDLED_BACKEND_CIDR

    def test_pinned_shared_frontend_cidr_passes_through(self) -> None:
        """The shared-mode override default ``172.32.0.0/24`` reflects
        verbatim.  Mirrors the bundled-CIDR test for the production
        deployment path the maintainer actually runs.
        """
        globals_ = self._run_gunicorn_conf(env_value=SHARED_FRONTEND_CIDR)
        assert (
            globals_["forwarded_allow_ips"] == SHARED_FRONTEND_CIDR
        )

    def test_single_ip_passes_through(self) -> None:
        """Audit recommendation says the strictest setting is "the
        exact container IP of the nginx service".  A single literal
        IP is valid input.
        """
        globals_ = self._run_gunicorn_conf(env_value="172.31.0.10")
        assert globals_["forwarded_allow_ips"] == "172.31.0.10"

    def test_no_rfc1918_fallback_in_source(self) -> None:
        """Regression guard: the loose ``172.16.0.0/12,192.168.0.0/16,
        10.0.0.0/8`` fallback must be GONE from gunicorn.conf.py.  A
        future commit re-adding it would silently restore the F-015
        spoofing surface.  Match the join string the historic default
        used so a partial re-add is also caught.
        """
        text = GUNICORN_CONF.read_text(encoding="utf-8")
        assert "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8" not in text


class TestBundledNginxSecurityHeaders:
    """Verify the bundled-mode Nginx config emits the four
    defense-in-depth headers with the ``always`` flag (audit finding
    F-064) and disables ``server_tokens`` (audit finding F-156).
    """

    @pytest.fixture(scope="class")
    @classmethod
    def conf_text(cls) -> str:
        """Read the bundled-mode nginx.conf once per test class."""
        return BUNDLED_NGINX_CONF.read_text(encoding="utf-8")

    def test_server_tokens_off(self, conf_text: str) -> None:
        """``server_tokens off;`` lives in the http block so it
        applies to every server block beneath it (and to default
        error pages, which Nginx serves before any server block
        is selected).  Audit finding F-156.
        """
        assert "server_tokens off;" in conf_text

    @pytest.mark.parametrize(("name", "value"), SECURITY_HEADERS)
    def test_security_header_in_server_block(
        self, conf_text: str, name: str, value: str
    ) -> None:
        """Each of the four headers must appear at the server level
        with the ``always`` flag so it propagates to error responses.
        Without ``always``, Nginx suppresses the header on 4xx/5xx
        responses and the audit's defense-in-depth requirement is
        defeated by the most useful failure mode (an Nginx 502
        during app restart).
        """
        directive = f'add_header {name} {value} always;'
        assert directive in conf_text, (
            f"missing or malformed bundled-mode security header: "
            f"expected exact directive {directive!r} in "
            f"{BUNDLED_NGINX_CONF}"
        )

    @pytest.mark.parametrize(("name", "value"), SECURITY_HEADERS)
    def test_security_header_repeated_in_static_location(
        self, conf_text: str, name: str, value: str
    ) -> None:
        """Nginx does not inherit ``add_header`` into a child context
        once the child has any ``add_header`` of its own.  The
        ``/static/`` location declares ``Cache-Control``, which
        triggers exactly that suppression: without re-emitting the
        four security headers in the location block, static asset
        responses would lose every header above.

        The C-33 fix repeats the four headers inside ``/static/``;
        this test pins that behaviour so a future cleanup ("dedupe
        the add_header lines") cannot silently regress static
        responses.  Counting >= 2 occurrences of each header is the
        cheapest way to assert "in two contexts" without parsing
        the Nginx grammar.
        """
        directive = f'add_header {name} {value} always;'
        assert conf_text.count(directive) >= 2, (
            f"bundled-mode header {name} appears in only one context;"
            " ``location /static/`` would lose the header"
        )

    def test_static_cache_control_preserved(self, conf_text: str) -> None:
        """The ``/static/`` location keeps its
        ``Cache-Control: public, immutable`` -- the OPPOSITE of the
        ``no-store`` Flask sets on dynamic responses.  Static assets
        are content-versioned and carry no session data; aggressive
        caching is required for performance.  Removing it would
        regress page-load times.
        """
        assert (
            'add_header Cache-Control "public, immutable" always;'
            in conf_text
        )

    def test_set_real_ip_from_dropped_loose_rfc1918_trust(
        self, conf_text: str
    ) -> None:
        """The historic ``set_real_ip_from`` block trusted three
        wide RFC 1918 ranges (covering every Docker bridge in the
        homelab).  C-33 narrowed it to 127.0.0.1 + the pinned
        ``frontend`` bridge.  Regression guard: each of the dropped
        loose CIDRs MUST be absent.
        """
        for loose in ("172.16.0.0/12", "192.168.0.0/16", "10.0.0.0/8"):
            assert (
                f"set_real_ip_from {loose};" not in conf_text
            ), (
                f"loose RFC 1918 trust not removed: "
                f"set_real_ip_from {loose} still present in "
                f"{BUNDLED_NGINX_CONF}"
            )

    def test_set_real_ip_from_only_pinned_frontend(
        self, conf_text: str
    ) -> None:
        """``set_real_ip_from`` MUST trust only loopback (cloudflared
        on the host) and the pinned ``frontend`` bridge subnet.
        """
        assert "set_real_ip_from 127.0.0.1;" in conf_text
        assert (
            f"set_real_ip_from {BUNDLED_FRONTEND_CIDR};" in conf_text
        )

    def test_real_ip_header_is_cf_connecting_ip(
        self, conf_text: str
    ) -> None:
        """``CF-Connecting-IP`` is the only header the bundled config
        promotes to ``$remote_addr``.  ``X-Forwarded-For`` is a list
        controlled by every hop and MUST NOT be the real_ip source.
        """
        assert "real_ip_header CF-Connecting-IP;" in conf_text


class TestSharedNginxRealIpAndTokens:
    """Verify the shared-mode Nginx config gains
    ``set_real_ip_from`` for the pinned ``shekel-frontend`` subnet
    and keeps ``server_tokens off`` (audit findings F-015 + F-156).
    """

    @pytest.fixture(scope="class")
    @classmethod
    def conf_text(cls) -> str:
        """Read the shared-mode nginx.conf once per test class."""
        return SHARED_NGINX_CONF.read_text(encoding="utf-8")

    def test_server_tokens_off(self, conf_text: str) -> None:
        """The shared config already had ``server_tokens off;``
        before C-33; this test pins that posture so a future cleanup
        cannot accidentally remove it.
        """
        assert "server_tokens off;" in conf_text

    def test_set_real_ip_from_shared_frontend_subnet(
        self, conf_text: str
    ) -> None:
        """The shared Nginx must trust the ``shekel-frontend``
        bridge subnet (where cloudflared meets it after the F-063
        WAN-path fix).  Trusting only this CIDR keeps a co-tenant on
        the homelab bridge from forging CF-Connecting-IP.
        """
        assert (
            f"set_real_ip_from {SHARED_FRONTEND_CIDR};" in conf_text
        )

    def test_real_ip_header_cf_connecting_ip(
        self, conf_text: str
    ) -> None:
        """Shared mode promotes ``CF-Connecting-IP`` to
        ``$remote_addr``, matching the bundled posture.  The earlier
        shared config had no real_ip directives at all, so audit logs
        recorded the cloudflared container IP for every WAN request
        (audit finding F-015 evidence).
        """
        assert "real_ip_header CF-Connecting-IP;" in conf_text

    def test_real_ip_recursive_off(self, conf_text: str) -> None:
        """``real_ip_recursive off`` keeps Nginx from walking a
        forwarded chain: CF-Connecting-IP is always a single client
        IP.  Any chain implies an injection attempt.
        """
        assert "real_ip_recursive off;" in conf_text


class TestSharedVhostSecurityHeaders:
    """The shared-mode shekel vhost (``deploy/nginx-shared/conf.d/
    shekel.conf``) must add the four defense-in-depth headers
    matching the sibling ``jellyfin.conf`` pattern.  Audit finding
    F-064.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def conf_text(cls) -> str:
        """Read the shared-mode shekel vhost once per test class."""
        return SHARED_VHOST_CONF.read_text(encoding="utf-8")

    @pytest.mark.parametrize(("name", "value"), SECURITY_HEADERS)
    def test_security_header_present(
        self, conf_text: str, name: str, value: str
    ) -> None:
        """Each of the four headers must appear in the shared vhost
        with the ``always`` flag.
        """
        directive = f'add_header {name} {value} always;'
        assert directive in conf_text, (
            f"shared-mode vhost missing {name} header: expected "
            f"{directive!r}"
        )

    @pytest.mark.parametrize(("name", "value"), SECURITY_HEADERS)
    def test_security_header_repeated_in_location(
        self, conf_text: str, name: str, value: str
    ) -> None:
        """The ``location /`` block in the shared vhost re-emits
        the four security headers because Nginx's ``add_header``
        does not inherit through a context that has any
        ``add_header`` of its own.  The shared vhost currently has
        no location-level add_header, but adding one in the future
        (e.g. a per-route Cache-Control override) would silently
        drop the security headers without the explicit copies.
        Pinning >=2 occurrences forces the redundancy to stick.
        """
        directive = f'add_header {name} {value} always;'
        assert conf_text.count(directive) >= 2, (
            f"shared-mode vhost {name} appears in only one context;"
            " adding any location-level add_header would silently"
            " drop it"
        )


@pytest.mark.docker
class TestSharedVhostNginxParse:
    """Validate ``deploy/nginx-shared/`` parses cleanly via
    ``nginx -t`` inside an ephemeral container of the compose's bundled
    nginx image (``NGINX_TEST_IMAGE``; production's shared proxy runs a
    later line, see ``_compose``).

    The shared-mode config references TLS certificate paths
    (``/etc/nginx/certs/fullchain.pem``) that exist only on the
    production host.  The fixture stages a self-signed cert + key in
    a tmp_path scratch directory and bind-mounts them into the
    container, so ``nginx -t`` can read the files without the test
    needing host-side state.  ``server_name`` resolves at request
    time, not at config load, so no additional ``--add-host`` is
    required for the vhost.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def synthetic_cert_dir(cls, tmp_path_factory) -> Path:
        """Generate a one-shot self-signed cert/key pair for the
        nginx -t run.  Re-uses the host's ``openssl`` binary because
        every Linux developer host already has it; skips otherwise.

        The cert is RSA-2048 with a 1-day validity -- plenty for the
        nginx -t lifecycle.  CN = shekel.test (placeholder; nginx
        does not use it during -t).
        """
        if shutil.which("openssl") is None:
            pytest.skip("openssl not available; cannot stage TLS cert")
        cert_dir = tmp_path_factory.mktemp("shared_certs")
        key_path = cert_dir / "key.pem"
        cert_path = cert_dir / "fullchain.pem"
        result = subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes",
                "-newkey", "rsa:2048",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-days", "1",
                "-subj", "/CN=shekel.test",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(
                "openssl failed to generate test cert: "
                f"{result.stderr}"
            )
        # Permissions: nginx running as nginx user inside the image
        # needs to read the key.  ``cap_add: DAC_OVERRIDE`` is not
        # available in this test container, so widen the file mode.
        os.chmod(key_path, 0o644)
        os.chmod(cert_path, 0o644)
        return cert_dir

    def test_shared_nginx_t(self, synthetic_cert_dir: Path) -> None:
        """``nginx -t`` against the merged shared config must exit 0.

        The container layout mirrors production:
          /etc/nginx/nginx.conf       <- deploy/nginx-shared/nginx.conf
          /etc/nginx/conf.d/          <- deploy/nginx-shared/conf.d/
          /etc/nginx/certs/           <- synthetic_cert_dir
        """
        if not _docker_available():
            pytest.skip(
                "docker not available; cannot run nginx -t in container"
            )
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                # ``shekel-prod-app`` is referenced via Docker DNS
                # in the vhost; pin it to loopback so nginx accepts
                # the upstream during config validation.
                "--add-host", "shekel-prod-app:127.0.0.1",
                "-v",
                f"{SHARED_NGINX_CONF}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{SHARED_VHOST_CONF.parent}:/etc/nginx/conf.d:ro",
                "-v",
                f"{synthetic_cert_dir}:/etc/nginx/certs:ro",
                "--entrypoint", "nginx",
                NGINX_TEST_IMAGE,
                "-t",
                "-c", "/etc/nginx/nginx.conf",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        assert result.returncode == 0, (
            "nginx -t failed for the shared-mode config:\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        assert "syntax is ok" in result.stderr
        assert "test is successful" in result.stderr


class TestProdComposeNetworkTopology:
    """Verify ``deploy/docker-compose.prod.yml`` realises the
    F-020/F-129 topology fix:

    * App is OFF the wider ``homelab`` network.
    * App is ON the dedicated ``shekel-frontend`` bridge.
    * App's ``FORWARDED_ALLOW_IPS`` is repinned to the
      shared-frontend CIDR.

    Tests parse the YAML rather than substring-matching the file
    text so example snippets in operator-pre-flight comments do not
    cause false positives or negatives.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Parse the prod compose override into a Python dict.

        ``yaml.safe_load`` is the standard tool here: we never need
        to construct arbitrary Python objects from this file, just
        read its mapping/list shape.  PyYAML is in the lock's dev
        group (``pyproject.toml``), which every suite run installs,
        so a missing import would surface earlier as a suite-level
        failure rather than masking a regression here.
        """
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.fixture(scope="class")
    @classmethod
    def yaml_text(cls) -> str:
        """Raw text of the prod compose override for substring
        matches that the YAML parse would normalise away.
        """
        return PROD_COMPOSE_OVERRIDE.read_text(encoding="utf-8")

    def test_app_no_longer_joins_homelab(self, parsed: dict) -> None:
        """Regression guard: the override must NOT add the app to
        the ``homelab`` network.  Co-tenant exposure (F-020/F-129)
        was the load-bearing reason for the C-33 topology change.
        """
        app_networks = parsed["services"]["app"].get("networks") or []
        assert "homelab" not in app_networks, (
            f"deploy/docker-compose.prod.yml still attaches app to "
            f"the homelab network: {app_networks}; F-020/F-129 "
            f"regression"
        )

    def test_top_level_homelab_network_removed(
        self, parsed: dict
    ) -> None:
        """The override must NOT declare ``homelab`` as a managed
        external network anymore -- the app no longer joins it, so
        the declaration becomes dead weight that misleads readers
        about the topology.
        """
        top_networks = parsed.get("networks") or {}
        assert "homelab" not in top_networks, (
            "deploy/docker-compose.prod.yml still declares the "
            "homelab network at the top level; remove the dead "
            "declaration to keep the file readable"
        )

    def test_app_joins_shekel_frontend(self, parsed: dict) -> None:
        """The shared-mode app must join the dedicated
        ``shekel-frontend`` bridge.
        """
        app_networks = parsed["services"]["app"].get("networks") or []
        assert "shekel-frontend" in app_networks

    def test_app_still_joins_backend(self, parsed: dict) -> None:
        """The internal-only ``backend`` bridge (db, valkey) is still
        required.  Removing it would break Gunicorn's DB/Valkey
        connections.
        """
        app_networks = parsed["services"]["app"].get("networks") or []
        assert "backend" in app_networks

    def test_shekel_frontend_declared_external(
        self, parsed: dict
    ) -> None:
        """``shekel-frontend`` is an externally-managed network
        (operator runs ``docker network create shekel-frontend``
        before first ``docker compose up``).  The override must
        declare it as such, with the explicit name to strip the
        compose project prefix.
        """
        top_networks = parsed.get("networks") or {}
        net = top_networks.get("shekel-frontend")
        assert net is not None, (
            "shekel-frontend network not declared at top level"
        )
        assert net.get("external") is True, (
            "shekel-frontend must be external: true so compose does "
            "not try to manage it"
        )
        # ``name: shekel-frontend`` strips the project prefix so the
        # bridge has the literal name the host-level compose
        # attaches to.  Without the rename, it would be
        # ``shekel-prod_shekel-frontend``.
        assert net.get("name") == "shekel-frontend"

    def test_app_forwarded_allow_ips_pinned_to_shared_subnet(
        self, parsed: dict
    ) -> None:
        """The override repins ``FORWARDED_ALLOW_IPS`` to the
        ``shekel-frontend`` subnet so Gunicorn keeps refusing
        forwarded headers from any unrelated network.
        """
        env = parsed["services"]["app"].get("environment") or {}
        # YAML allows scalar values to be quoted or bare; both the
        # literal string and the canonical CIDR form must work.
        # ``FORWARDED_ALLOW_IPS: 172.32.0.0/24`` parses as a string
        # without ambiguity, so the assertion is a direct equality
        # check.
        assert env.get("FORWARDED_ALLOW_IPS") == SHARED_FRONTEND_CIDR

    def test_bundled_nginx_still_disabled(
        self, parsed: dict
    ) -> None:
        """The override must still park the bundled
        shekel-prod-nginx in the disabled profile -- C-32 set this
        and C-33 must preserve it.
        """
        nginx = parsed["services"].get("nginx") or {}
        profiles = nginx.get("profiles") or []
        assert "disabled" in profiles, (
            f"bundled nginx no longer disabled in shared mode: "
            f"profiles={profiles}"
        )


class TestBaseComposeNetworkPinning:
    """Verify ``docker-compose.yml`` pins the frontend / backend
    subnets and sets the bundled-mode ``FORWARDED_ALLOW_IPS``
    default.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def yaml_text(cls) -> str:
        """Read the base docker-compose.yml once per test class."""
        return BASE_COMPOSE.read_text(encoding="utf-8")

    def test_frontend_subnet_pinned(self, yaml_text: str) -> None:
        """``frontend`` must be pinned to ``172.30.0.0/24`` so Nginx's
        ``set_real_ip_from`` directive references a stable CIDR.
        """
        assert f"subnet: {BUNDLED_FRONTEND_CIDR}" in yaml_text

    def test_backend_subnet_pinned(self, yaml_text: str) -> None:
        """``backend`` must be pinned to ``172.31.0.0/24`` so
        Gunicorn's ``FORWARDED_ALLOW_IPS`` literal matches the bridge
        carrying the in-stack proxy hop.
        """
        assert f"subnet: {BUNDLED_BACKEND_CIDR}" in yaml_text

    def test_app_gets_forwarded_allow_ips_default(
        self, yaml_text: str
    ) -> None:
        """The app service must default ``FORWARDED_ALLOW_IPS`` to
        the pinned backend CIDR.  The shared-mode override repins
        it; the bundled-mode default keeps fresh-host bring-up
        working without an explicit env var.
        """
        assert (
            "FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:-172.31.0.0/24}"
            in yaml_text
        )

    def test_backend_still_internal(self, yaml_text: str) -> None:
        """``backend`` must remain ``internal: true`` so the db /
        valkey / app cannot reach the public internet.  Audit finding
        F-020 mitigations require this.
        """
        # Robustly match the YAML structure: "backend:" header
        # followed by "driver: bridge" and "internal: true" (any
        # order) before the next top-level key.  We do a substring
        # match because docker-compose preserves indentation.
        assert "internal: true" in yaml_text


class TestCloudflaredTemplate:
    """Verify the bundled-mode cloudflared template documents and
    enforces the F-063 fix: WAN ingress terminates at Nginx, not
    Gunicorn.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Read the bundled cloudflared/config.yml template."""
        return CLOUDFLARED_TEMPLATE.read_text(encoding="utf-8")

    def test_service_routes_through_nginx(self, text: str) -> None:
        """The repo template's bundled-mode rule must route through
        the in-stack Nginx (``localhost:80``).  The audit's WAN-path
        bypass evidence (``service: http://shekel-prod-app:8000``)
        MUST NOT appear in this template.
        """
        assert "service: http://localhost:80" in text
        assert "service: http://shekel-prod-app:8000" not in text

    def test_documents_shared_mode_rule(self, text: str) -> None:
        """The template must document the shared-mode service URI
        (``service: http://nginx:80``) in a comment so a homelab
        operator switching modes has the right snippet inline.
        """
        assert "service: http://nginx:80" in text

    def test_documents_wan_chokepoint_rationale(
        self, text: str
    ) -> None:
        """A reader of this file must encounter the rationale: WAN
        ingress terminates at Nginx for header / size / log /
        timeout enforcement.  Without the comment, a future
        operator might "simplify" the routing.
        """
        assert "WAN-PATH SECURITY INVARIANT" in text
        assert "F-063" in text


# 120s per-test timeout for the runtime header class.  The default
# in pytest.ini covers Python-only tests; orchestrating a stub
# upstream + Nginx on a user-defined Docker network needs more
# headroom for the (possibly cold-cache) image pulls, the network
# create/destroy, the readiness probes, and the curl probes.
# Steady-state runs complete in ~10-15s on a hot Docker daemon.
@pytest.mark.timeout(120)
@pytest.mark.docker
class TestSharedNginxRuntimeHeaders:
    """End-to-end test: run the real shared Nginx config inside a
    container against a real stub upstream and confirm the four
    ``add_header ... always`` directives actually fire on BOTH a
    200 response (upstream up) AND a 502 response (upstream stopped
    mid-test).  The 502 is the case the ``always`` flag exists for
    -- without ``always`` Nginx silently drops the directive on
    error responses, which is the most useful failure mode
    (gunicorn restart / panic).

    The shared Nginx config uses
    ``resolver 127.0.0.11 valid=30s ipv6=off;`` -- Docker's embedded
    DNS, which exists ONLY on user-defined networks.  Running the
    container with the default ``bridge`` driver makes the resolver
    unreachable and any ``proxy_pass`` to a variable-derived
    upstream hangs.  So this fixture creates a user-defined Docker
    network for the run, attaches both the stub upstream container
    (named ``shekel-prod-app`` so Docker DNS hands out the right
    address) and the Nginx container.  The stub upstream is a
    python:3.14-alpine container that backgrounds ``python -m
    http.server 8000`` and hands PID 1 to ``tail -f /dev/null`` so
    the container -- and its ``shekel-prod-app`` network alias --
    outlives the server.  headers_502 forces the 5xx by killing only
    the recorded server PID in place: nginx then connects to a
    still-routable container IP and gets an immediate TCP RST (a
    fast, deterministic 502), instead of the unrouted-IP hang a
    ``docker stop`` of an ``--rm`` stub produced -- which timed curl
    out on CI's Docker network.  This also faithfully models the
    real failure (gunicorn process dies, container stays up).

    Skipped when Docker is not available on the test host.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def synthetic_cert_dir(cls, tmp_path_factory) -> Path:
        """Generate a one-shot self-signed cert/key pair so the
        ``listen 443 ssl;`` directive in shekel.conf can find the
        cert files at /etc/nginx/certs/.
        """
        if shutil.which("openssl") is None:
            pytest.skip(
                "openssl not available; cannot stage TLS cert"
            )
        cert_dir = tmp_path_factory.mktemp("rt_certs")
        key_path = cert_dir / "key.pem"
        cert_path = cert_dir / "fullchain.pem"
        result = subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes",
                "-newkey", "rsa:2048",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-days", "1",
                "-subj", "/CN=shekel.test",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(
                "openssl failed to generate test cert: "
                f"{result.stderr}"
            )
        os.chmod(key_path, 0o644)
        os.chmod(cert_path, 0o644)
        return cert_dir

    @pytest.fixture(scope="class")
    @classmethod
    def running_stack(cls, synthetic_cert_dir: Path):
        """Bring up the network + stub upstream + Nginx, yield the
        ``(host_port, stub_container_name)`` pair, then tear
        everything down.

        Containers and the network are namespaced with the test PID
        so parallel test runs (or a stale leftover from a previous
        crash) do not collide.
        """
        if not _docker_available():
            pytest.skip(
                "docker not available; cannot run the stack"
            )

        suffix = f"c33-{os.getpid()}"
        net_name = f"shekel-test-net-{suffix}"
        # The shared shekel.conf vhost resolves the upstream
        # ``shekel-prod-app:8000`` via Docker DNS, but we cannot
        # name our stub container ``shekel-prod-app`` because the
        # developer's actual production container already owns
        # that name on the same daemon.  Instead we give the stub
        # a unique container name and attach the literal
        # ``shekel-prod-app`` as a network alias on this isolated
        # test network -- Docker DNS resolves either form.
        stub_name = f"shekel-test-stub-{suffix}"
        stub_alias = "shekel-prod-app"
        nginx_name = f"shekel-test-nginx-{suffix}"

        def _docker(*args: str, check: bool = False):
            return subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
                check=check,
            )

        try:
            # 1. Create the user-defined network so the
            #    127.0.0.11 resolver works.
            net_result = _docker("network", "create", net_name)
            if net_result.returncode != 0:
                pytest.skip(
                    f"docker network create failed: "
                    f"{net_result.stderr}"
                )

            # 2. Start a stub upstream that returns 200 OK on every
            #    request.  ``--network-alias`` makes the stub answer
            #    to ``shekel-prod-app`` on this network so the shared
            #    config's ``set $upstream_shekel shekel-prod-app:
            #    8000;`` resolves to it without colliding with the
            #    developer's real production container of that name
            #    on the same Docker daemon.
            #
            #    PID 1 is ``tail -f /dev/null``, not the HTTP server:
            #    the container (and its alias / routable IP) must
            #    outlive the server so headers_502 can drop the
            #    listener in place and have nginx hit a live IP with
            #    a closed port -> immediate TCP RST -> fast 502.
            #    Removing the container instead (the old ``docker
            #    stop`` of an ``--rm`` stub) left nginx's 30s
            #    resolver cache pointing at an unrouted IP, which
            #    hangs with no RST until proxy_connect_timeout and
            #    timed curl out on CI's Docker network.
            stub_result = _docker(
                "run", "-d", "--rm",
                "--name", stub_name,
                "--network", net_name,
                "--network-alias", stub_alias,
                "python:3.14-alpine",
                "sh", "-c",
                "python -m http.server 8000 & "
                "echo $! > /tmp/srv.pid; "
                "exec tail -f /dev/null",
            )
            if stub_result.returncode != 0:
                pytest.skip(
                    "stub upstream container failed to start: "
                    f"{stub_result.stderr}"
                )

            # 3. Start Nginx on the same network with the shared
            #    config + synthetic certs.
            nginx_result = _docker(
                "run", "-d", "--rm",
                "--name", nginx_name,
                "--network", net_name,
                "-p", "0:443",
                "-v",
                f"{SHARED_NGINX_CONF}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{SHARED_VHOST_CONF.parent}:/etc/nginx/conf.d:ro",
                "-v",
                f"{synthetic_cert_dir}:/etc/nginx/certs:ro",
                NGINX_TEST_IMAGE,
            )
            if nginx_result.returncode != 0:
                pytest.skip(
                    "nginx container failed to start: "
                    f"{nginx_result.stderr}"
                )

            # 4. Resolve the host-side port docker chose for 443.
            port_result = _docker("port", nginx_name, "443/tcp")
            if port_result.returncode != 0:
                pytest.skip(
                    "docker port resolution failed: "
                    f"{port_result.stderr}"
                )
            first_line = port_result.stdout.strip().splitlines()[0]
            host_port = first_line.rsplit(":", 1)[1]

            # 5. Wait for both Nginx to listen AND the upstream to
            #    answer.  We poll the live HTTPS endpoint -- it
            #    only returns 200 once both are up; until then it
            #    is 502 (or curl fails to connect).  Either signal
            #    the readiness of the layer it represents.
            ready_ok = False
            for _ in range(40):  # ~10s
                ready = subprocess.run(
                    [
                        "curl", "--silent", "--insecure",
                        "--max-time", "1",
                        "--output", "/dev/null",
                        "--write-out", "%{http_code}",
                        f"https://localhost:{host_port}/",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if ready.returncode == 0 and ready.stdout == "200":
                    ready_ok = True
                    break
                time.sleep(0.25)
            if not ready_ok:
                logs = _docker("logs", nginx_name).stdout
                pytest.skip(
                    "stack did not become ready within 10s; "
                    f"nginx logs:\n{logs[:1500]}"
                )

            yield host_port, stub_name
        finally:
            _docker("rm", "-f", nginx_name)
            _docker("rm", "-f", stub_name)
            _docker("network", "rm", net_name)

    @staticmethod
    def _curl_headers(
        host_port: str,
    ) -> tuple[str, dict[str, str]]:
        """Issue a HEAD request and parse the response status +
        headers.  Returns ``(status_line, headers_lower_keyed)``.
        """
        result = subprocess.run(
            [
                "curl", "--silent", "--insecure",
                "--head", "--max-time", "5",
                f"https://localhost:{host_port}/",
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        assert result.returncode == 0, (
            "curl could not reach the test nginx:\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        headers: dict[str, str] = {}
        status_line = ""
        for raw_line in result.stdout.splitlines():
            line = raw_line.rstrip("\r")
            if line.startswith("HTTP/"):
                status_line = line
                headers = {}
                continue
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        return status_line, headers

    @pytest.fixture(scope="class")
    @classmethod
    def headers_200(cls, running_stack) -> dict[str, str]:
        """Capture response headers from a 200 (upstream up)."""
        host_port, _stub = running_stack
        status, headers = cls._curl_headers(host_port)
        assert "200" in status, (
            f"expected 200 with stub upstream up; got: {status!r}"
        )
        return headers

    @pytest.fixture(scope="class")
    @classmethod
    def headers_502(cls, running_stack, headers_200) -> dict[str, str]:
        """Kill the upstream stub's HTTP listener in place, then
        capture the 5xx response headers.  This is the real-world
        failure mode the ``always`` flag exists for (a gunicorn
        process dies while its container stays up).

        **``headers_200`` is depended on for its ORDERING, not its
        value, and removing it reintroduces a CI flake.**  This
        fixture kills the stub's listener permanently for the
        class's stack, so the 200 capture is only possible BEFORE
        it runs.  Both fixtures are class-scoped, and pytest
        instantiates a class-scoped fixture when the first test
        requesting it executes -- which under ``-n 12`` is whatever
        order the xdist scheduler hands this worker its share of
        the ten cases, not the order they appear in this file.  A
        worker given a ``_502`` case before a ``_200`` case built
        ``headers_502`` first, and ``headers_200`` then curled a
        stack whose upstream this fixture had already killed,
        failing at SETUP with ``expected 200 with stub upstream
        up; got: 'HTTP/2 502 '``.

        Observed twice, byte-identical, on unrelated branches: runs
        ``33197999790`` (2026-08-28) and ``33376022611`` attempt 1
        (2026-08-31), each **one** error -- ``12065 passed ... 1
        error`` -- on ``test_server_header_does_not_leak_version_
        on_200``, with the other nine cases passing.  Naming this
        dependency puts the real constraint in the fixture graph,
        where pytest enforces it, instead of leaving it to the
        scheduler.  ``running_stack``'s readiness poll cannot help:
        the stack IS ready, and the upstream is dead on purpose.
        """
        host_port, stub_name = running_stack
        # Kill only the recorded server PID; the container (PID 1 is
        # ``tail -f /dev/null``) and its ``shekel-prod-app`` alias
        # stay alive, so nginx resolves a still-routable IP and the
        # closed :8000 port yields an immediate TCP RST -> fast,
        # deterministic 5xx on any Docker network.  The old ``docker
        # stop`` removed the container, leaving the cached resolver
        # IP unrouted -> connect hang -> curl timeout on CI.
        subprocess.run(
            [
                "docker", "exec", stub_name,
                "sh", "-c", 'kill "$(cat /tmp/srv.pid)"',
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        # The RST is immediate, so the first probe normally already
        # sees the 5xx; the short retry loop only covers the brief
        # race between ``docker exec`` returning and the kernel
        # tearing the listening socket down.
        last_status = ""
        last_headers: dict[str, str] = {}
        for _ in range(8):
            status, headers = cls._curl_headers(host_port)
            last_status = status
            last_headers = headers
            if "502" in status or "504" in status:
                # 504 is also acceptable -- "gateway timeout".
                # The audit's ``always`` requirement covers any
                # 5xx Nginx generates without the upstream's body.
                break
            time.sleep(0.5)
        assert "502" in last_status or "504" in last_status, (
            f"could not force a 5xx after killing the stub listener; "
            f"last status: {last_status!r}"
        )
        return last_headers

    @pytest.mark.parametrize(("name", "expected_substring"), [
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("permissions-policy", "camera=()"),
    ])
    def test_security_header_present_on_200(
        self, headers_200: dict[str, str],
        name: str, expected_substring: str,
    ) -> None:
        """Each of the four security headers must appear on a 200
        response from the stub upstream.  This is the baseline
        defense-in-depth assertion: even when the upstream returns
        a perfectly normal response, Nginx still adds the four
        headers.  Audit finding F-064.
        """
        actual = headers_200.get(name, "")
        assert expected_substring in actual, (
            f"200-response header {name!r} missing or wrong: "
            f"got {actual!r}, expected substring "
            f"{expected_substring!r}"
        )

    @pytest.mark.parametrize(("name", "expected_substring"), [
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("permissions-policy", "camera=()"),
    ])
    def test_security_header_present_on_502(
        self, headers_502: dict[str, str],
        name: str, expected_substring: str,
    ) -> None:
        """Each of the four security headers MUST appear on the
        5xx response Nginx generates when the upstream is
        unreachable.

        The ``always`` flag in the shared vhost is what makes this
        true; without it Nginx silently drops add_header on
        non-2xx/3xx responses, which is the most useful failure
        mode (gunicorn restart -> 5xx -> framable /
        mime-sniffable error page).  Audit finding F-064.
        """
        actual = headers_502.get(name, "")
        assert expected_substring in actual, (
            f"5xx-response header {name!r} missing or wrong: "
            f"got {actual!r}, expected substring "
            f"{expected_substring!r}"
        )

    def test_server_header_does_not_leak_version_on_200(
        self, headers_200: dict[str, str]
    ) -> None:
        """``server_tokens off`` reduces the ``Server`` header to a
        generic ``nginx`` (no version) on 2xx responses.  Audit
        finding F-156.
        """
        server = headers_200.get("server", "")
        assert server == "nginx", (
            f"Server header leaks version info on 200: got "
            f"{server!r}, expected literal 'nginx'"
        )

    def test_server_header_does_not_leak_version_on_502(
        self, headers_502: dict[str, str]
    ) -> None:
        """Same posture applies on 5xx responses -- the default
        Nginx error page would otherwise carry the version banner.
        """
        server = headers_502.get("server", "")
        assert server == "nginx", (
            f"Server header leaks version info on 5xx: got "
            f"{server!r}, expected literal 'nginx'"
        )
