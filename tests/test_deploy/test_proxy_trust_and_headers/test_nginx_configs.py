"""Proxy trust tests (C-33, F-015/F-064/F-156): the bundled and shared nginx configs'
real-IP trust, ``server_tokens`` and security headers, read as text, and the shared config
parsed by ``nginx -t`` in a container.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_deploy._compose import NGINX_TEST_IMAGE

from ._helpers import (
    BUNDLED_NGINX_CONF,
    SHARED_NGINX_CONF,
    SHARED_VHOST_CONF,
    BUNDLED_FRONTEND_CIDR,
    SHARED_FRONTEND_CIDR,
    SECURITY_HEADERS,
    DOCKER_SUBPROCESS_TIMEOUT_S,
    _docker_available,
)


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
