"""Proxy trust tests (C-33, F-064/F-156): the shared nginx config run against a stub
upstream, its security headers and version-free ``Server`` header on a 200 and on a 502.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_deploy._compose import NGINX_TEST_IMAGE

from ._helpers import (
    SHARED_NGINX_CONF,
    SHARED_VHOST_CONF,
    DOCKER_SUBPROCESS_TIMEOUT_S,
    _docker_available,
)


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
