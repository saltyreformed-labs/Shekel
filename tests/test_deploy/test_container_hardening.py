"""Tests for remediation Commit C-35: Docker container hardening bundle.

Covers audit findings:

    F-055  no-new-privileges not set at daemon or per-container level
    F-056  No capability dropping on any container
    F-057  Dev databases bound to 0.0.0.0 with public credentials
    F-115  No resource limits on any container
    F-116  No Docker log rotation configured
    F-117  Container root filesystem is writable

The artifacts under audit are YAML compose files plus the bundled
Nginx config that pairs with the read-only nginx service.  Tests
parse the YAML rather than substring-matching the file text so
example snippets in operator-runbook comments do not produce false
positives or negatives.

A separate subprocess-driven test verifies that ``docker compose
config`` parses the merged base + shared-mode override and surfaces
the hardening fields on the merged result.  That test skips when
Docker is not available on the test host (matching the C-32/C-33
patterns in the sibling ``test_deploy_configs.py`` and
``test_proxy_trust_and_headers.py``).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"

BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"
PROD_COMPOSE_OVERRIDE = DEPLOY_DIR / "docker-compose.prod.yml"
BUNDLED_NGINX_CONF = DEPLOY_DIR / "nginx-bundled" / "nginx.conf"

# Production services that must carry the full hardening bundle.
# valkey (redis before ruling R-BAL298) was hardened in an earlier
# commit; including it here pins the regression surface so the
# existing fields cannot be removed.
HARDENED_SERVICES = ("db", "app", "nginx", "valkey")

# Bytes-per-MiB conversion used to normalise compose's mixed
# representation of mem_limit (raw bytes from ``compose config``,
# string suffixes from the source YAML).
MIB = 1024 * 1024

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


def _parse_mem_limit(raw) -> int:
    """Convert a compose ``mem_limit`` value to a byte count.

    Compose accepts an integer (bytes), a number suffixed with the
    SI-style ``k``/``m``/``g`` (kilobytes, megabytes, gigabytes), or
    the IEC ``ki``/``mi``/``gi`` (kibibytes, etc.).  ``yaml.safe_load``
    returns the literal string ``'512m'`` for the source YAML and an
    integer byte count for the output of ``docker compose config``.
    Both shapes resolve through this helper to the same byte total
    so tests can compare against ``>= 96 * MIB`` regardless of which
    file produced the value.
    """
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        raise TypeError(f"unsupported mem_limit type: {type(raw).__name__}")
    text = raw.strip().lower()
    suffix_map = {
        "k": 1_000,
        "m": 1_000_000,
        "g": 1_000_000_000,
        "ki": 1024,
        "mi": MIB,
        "gi": 1024 * MIB,
        "kb": 1_000,
        "mb": 1_000_000,
        "gb": 1_000_000_000,
    }
    for suffix in ("gi", "mi", "ki", "gb", "mb", "kb", "g", "m", "k"):
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * suffix_map[suffix]
    return int(text)


class TestBaseComposeHardeningFields:
    """Parse ``docker-compose.yml`` directly and assert each Shekel
    service carries the C-35 hardening bundle.

    All four services (db, app, nginx, valkey) are checked because:
      * db, app, and nginx are the focus of Commit C-35;
      * valkey (redis before ruling R-BAL298) was hardened in an
        earlier commit (Commit C-15-era)
        and including it here prevents a future change from quietly
        regressing the existing fields.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed base compose document.

        ``yaml.safe_load`` is the standard tool here: we never need
        to construct arbitrary Python objects from this file, just
        read its mapping/list shape.  PyYAML is in the lock's dev
        group (``pyproject.toml``; already used by the sibling C-33
        tests), so a missing import would surface as a suite-level
        failure rather than masking a hardening regression here.
        """
        with BASE_COMPOSE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.mark.parametrize("service_name", HARDENED_SERVICES)
    def test_no_new_privileges(
        self, parsed: dict, service_name: str
    ) -> None:
        """Each hardened service must declare
        ``security_opt: [no-new-privileges:true]`` so a setuid
        binary inside the image cannot elevate.  Audit finding F-055.
        """
        service = parsed["services"][service_name]
        opts = service.get("security_opt") or []
        assert "no-new-privileges:true" in opts, (
            f"service {service_name!r} missing "
            f"no-new-privileges:true (audit F-055): {opts}"
        )

    @pytest.mark.parametrize("service_name", HARDENED_SERVICES)
    def test_cap_drop_all(
        self, parsed: dict, service_name: str
    ) -> None:
        """Each hardened service must declare ``cap_drop: [ALL]`` so
        the container starts with zero Linux capabilities.  Audit
        finding F-056.

        We do not assert ``cap_add`` is absent because the YAML may
        legitimately omit the key entirely; the test below
        (``test_no_cap_add_unless_documented``) catches a regression
        that re-introduces cap_add.
        """
        service = parsed["services"][service_name]
        cap_drop = service.get("cap_drop") or []
        assert "ALL" in cap_drop, (
            f"service {service_name!r} missing cap_drop: [ALL] "
            f"(audit F-056): {cap_drop}"
        )

    @pytest.mark.parametrize("service_name", HARDENED_SERVICES)
    def test_no_cap_add_unless_documented(
        self, parsed: dict, service_name: str
    ) -> None:
        """No service in the C-35 design needs to add a capability
        back.  The audit-blessed approach is to pin a non-root user
        and run on non-privileged ports so the cap drop stays
        absolute.  A future regression that adds cap_add would weaken
        the posture without touching ``cap_drop``; this test catches
        that drift.

        If a future change genuinely needs a cap (e.g. a new sidecar
        binding port < 1024 without ``user:`` rework), update the
        compose comments AND this test in the same commit so the
        review trail is explicit.
        """
        service = parsed["services"][service_name]
        cap_add = service.get("cap_add") or []
        assert cap_add == [], (
            f"service {service_name!r} re-introduced cap_add "
            f"(audit F-056 regression): {cap_add}.  Update the "
            f"compose comment trail and this test in the same "
            f"commit if the change is intentional."
        )

    @pytest.mark.parametrize("service_name", HARDENED_SERVICES)
    def test_read_only_rootfs(
        self, parsed: dict, service_name: str
    ) -> None:
        """Each hardened service must declare ``read_only: true``
        with a tmpfs companion list.  Audit finding F-117.
        """
        service = parsed["services"][service_name]
        assert service.get("read_only") is True, (
            f"service {service_name!r} missing read_only: true "
            f"(audit F-117)"
        )
        tmpfs = service.get("tmpfs") or []
        assert "/tmp" in tmpfs, (
            f"service {service_name!r} read-only rootfs without /tmp "
            f"tmpfs would break Python tempfile, valkey temp writes, "
            f"and nginx pid path: {tmpfs}"
        )

    def test_db_tmpfs_includes_postgres_socket_dir(
        self, parsed: dict
    ) -> None:
        """The postgres image creates a unix socket under
        /var/run/postgresql.  Without a tmpfs there, the socket bind
        fails on the read-only rootfs and pg_isready / health checks
        fail.
        """
        tmpfs = parsed["services"]["db"].get("tmpfs") or []
        assert "/var/run/postgresql" in tmpfs, (
            f"db service tmpfs missing /var/run/postgresql -- the "
            f"postgres unix socket cannot be created on a read-only "
            f"rootfs without it: {tmpfs}"
        )

    def test_nginx_tmpfs_includes_cache_and_run_paths(
        self, parsed: dict
    ) -> None:
        """Nginx writes to /var/cache/nginx (proxy/client_body temp
        dirs) and probes /var/run, /run during the alpine image's
        docker-entrypoint.d phase.  Each path must be a tmpfs so the
        read-only rootfs does not break nginx -t at startup.
        """
        tmpfs = parsed["services"]["nginx"].get("tmpfs") or []
        for required in ("/var/cache/nginx", "/var/run", "/run"):
            assert required in tmpfs, (
                f"nginx service tmpfs missing {required!r}: {tmpfs}"
            )

    @pytest.mark.parametrize(
        ("service_name", "min_mib"),
        [
            ("db", 256),
            ("app", 256),
            ("nginx", 64),
            ("valkey", 64),
        ],
    )
    def test_mem_limit_set_and_reasonable(
        self, parsed: dict, service_name: str, min_mib: int
    ) -> None:
        """Each hardened service must declare a memory cap.  The
        absolute minimum is the smallest plausible working set for
        the workload; the test asserts ``>= min_mib`` rather than an
        exact value so an operator who tunes upward (more workers,
        bigger shared_buffers) does not have to update the test.
        Audit finding F-115.

        Reads ``deploy.resources.limits.memory`` (Compose-Spec form,
        adopted 2026-05-14) with a fallback to legacy ``mem_limit``
        for older branches.
        """
        service = parsed["services"][service_name]
        raw = (
            service.get("deploy", {})
            .get("resources", {})
            .get("limits", {})
            .get("memory")
        )
        if raw is None:
            raw = service.get("mem_limit")
        assert raw is not None, (
            f"service {service_name!r} missing memory limit "
            f"(deploy.resources.limits.memory or mem_limit; audit "
            f"F-115)"
        )
        bytes_value = _parse_mem_limit(raw)
        assert bytes_value >= min_mib * MIB, (
            f"service {service_name!r} memory limit={raw!r} "
            f"({bytes_value} bytes) below the minimum "
            f"{min_mib} MiB working-set floor"
        )

    @pytest.mark.parametrize("service_name", HARDENED_SERVICES)
    def test_pids_limit_set(
        self, parsed: dict, service_name: str
    ) -> None:
        """Each hardened service must declare a pids cap so a fork
        bomb in one container cannot exhaust the host's pid space.
        Audit finding F-115.

        Reads ``deploy.resources.limits.pids`` (Compose-Spec form,
        adopted 2026-05-14) with a fallback to legacy ``pids_limit``
        for older branches.
        """
        service = parsed["services"][service_name]
        pids_limit = (
            service.get("deploy", {})
            .get("resources", {})
            .get("limits", {})
            .get("pids")
        )
        if pids_limit is None:
            pids_limit = service.get("pids_limit")
        assert isinstance(pids_limit, int) and pids_limit > 0, (
            f"service {service_name!r} pids limit not set or "
            f"non-positive (deploy.resources.limits.pids or "
            f"pids_limit; audit F-115): {pids_limit!r}"
        )

    @pytest.mark.parametrize("service_name", HARDENED_SERVICES)
    def test_logging_max_size_configured(
        self, parsed: dict, service_name: str
    ) -> None:
        """Each hardened service must configure ``logging.options``
        with a non-empty ``max-size`` so the json-file driver buffer
        cannot grow unbounded and exhaust /var/lib/docker.  Audit
        finding F-116.
        """
        service = parsed["services"][service_name]
        logging = service.get("logging") or {}
        options = logging.get("options") or {}
        max_size = options.get("max-size")
        assert max_size, (
            f"service {service_name!r} missing logging.options.max-size "
            f"(audit F-116): {logging!r}"
        )
        max_file = options.get("max-file")
        assert max_file, (
            f"service {service_name!r} missing logging.options.max-file "
            f"(audit F-116): {logging!r}"
        )

    @pytest.mark.parametrize(
        ("service_name", "expected_user"),
        [
            ("db", "postgres"),
            ("nginx", "nginx"),
            ("valkey", "valkey"),
        ],
    )
    def test_non_root_user_pinned(
        self,
        parsed: dict,
        service_name: str,
        expected_user: str,
    ) -> None:
        """Pinning ``user:`` to a non-root identity is what lets the
        upstream postgres / nginx / valkey entrypoints take their
        ``[ "$(id -u)" != '0' ]`` branches and skip the chown +
        gosu-drop steps.  Without that, ``cap_drop: [ALL]`` would
        starve the entrypoints of CAP_CHOWN/CAP_SETUID/CAP_SETGID
        and the container would fail to start.

        The app service runs as the unprivileged ``shekel`` user
        baked into the Dockerfile (USER directive), so it does not
        need a compose-level ``user:`` override and is intentionally
        excluded from this parametrize.
        """
        service = parsed["services"][service_name]
        user = service.get("user")
        assert user == expected_user, (
            f"service {service_name!r} expected user={expected_user!r} "
            f"to coexist with cap_drop ALL; got {user!r}"
        )

    def test_app_environment_pythondontwritebytecode(
        self, parsed: dict
    ) -> None:
        """``read_only: true`` would cause Python to log a
        permission-denied warning on every import as it tries to
        materialise __pycache__ files.  PYTHONDONTWRITEBYTECODE=1
        skips the write attempt entirely.
        """
        env = parsed["services"]["app"].get("environment") or {}
        # Compose accepts the value as ``"1"`` (string) or ``1``
        # (integer); the YAML in this repo uses the quoted-string
        # form to keep the type stable across compose's internal
        # interpolation.  Accept either to keep the test resilient
        # to a future formatting change.
        assert env.get("PYTHONDONTWRITEBYTECODE") in ("1", 1), (
            f"app service missing PYTHONDONTWRITEBYTECODE=1; "
            f"read-only rootfs will produce import-time warnings "
            f"on every container start: {env!r}"
        )

    def test_nginx_internal_port_non_privileged(
        self, parsed: dict
    ) -> None:
        """The nginx container listens on 8080 internally so the
        master process running as the unprivileged ``nginx`` user
        does not need CAP_NET_BIND_SERVICE.  The host-side mapping
        keeps port 80 (or NGINX_PORT) externally; only the in-
        container target moved.
        """
        ports = parsed["services"]["nginx"].get("ports") or []
        # ports may be ["${NGINX_PORT:-80}:8080"] (string form) or
        # the long-form mapping with explicit target/published keys.
        # Normalise to the string form for the assertion.
        normalized = []
        for entry in ports:
            if isinstance(entry, str):
                normalized.append(entry)
            elif isinstance(entry, dict):
                target = entry.get("target")
                published = entry.get("published")
                normalized.append(f"{published}:{target}")
        assert any(":8080" in p for p in normalized), (
            f"nginx ports do not include the non-privileged 8080 "
            f"target -- master cannot bind without "
            f"CAP_NET_BIND_SERVICE: {normalized}"
        )


class TestBundledNginxConfigForReadOnlyRoot:
    """Static checks against ``deploy/nginx-bundled/nginx.conf`` to
    ensure the directives that pair with the read-only / non-root
    nginx service are present.

    These tests do NOT invoke ``nginx -t`` -- the existing C-32 test
    in ``test_deploy_configs.py`` already does that.  They assert
    the specific changes Commit C-35 introduced, so a regression
    that reverts to ``listen 80`` or drops the ``pid /tmp``
    redirection fails here loudly.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def conf_text(cls) -> str:
        """Return the raw text of the bundled nginx config so the
        per-test substring assertions can match against it.
        """
        return BUNDLED_NGINX_CONF.read_text(encoding="utf-8")

    def test_listen_directive_uses_8080(self, conf_text: str) -> None:
        """The server block must listen on 8080 (non-privileged) so
        the nginx master can bind without CAP_NET_BIND_SERVICE.
        """
        assert "\n        listen 8080;" in conf_text, (
            "deploy/nginx-bundled/nginx.conf does not listen on "
            "8080; the bundled service runs as the nginx user with "
            "cap_drop ALL and cannot bind privileged ports."
        )
        # The audit-era ``listen 80;`` directive must NOT appear in
        # the server block any longer.  Allow it inside comments
        # (the rationale block above the listen directive references
        # the previous behaviour) but reject it as an active
        # configuration line.
        for line in conf_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert stripped != "listen 80;", (
                "deploy/nginx-bundled/nginx.conf still contains an "
                "uncommented ``listen 80;`` directive; the bundled "
                "service cannot bind privileged ports under "
                "cap_drop ALL"
            )

    def test_pid_redirected_to_tmpfs(self, conf_text: str) -> None:
        """The bundled service mounts /tmp as tmpfs and does not
        mount /run.  The pid file must therefore live under /tmp so
        the master can write it on the read-only rootfs.
        """
        assert "pid /tmp/nginx.pid;" in conf_text, (
            "deploy/nginx-bundled/nginx.conf does not redirect the "
            "pid file to /tmp; nginx will fail to start on the "
            "read-only rootfs because /run/nginx.pid is not "
            "writable by the nginx user"
        )


def _dev_compose_services_publishing_ports() -> list[str]:
    """Return every dev-compose service that publishes a host port.

    **DERIVED, not hand-listed.**  This was a two-entry literal naming ``db``
    and ``test-db``; ``balance:X-br-4`` deleted the second, and a hand list of
    ONE is maximally likely to rot -- a service added later with a published
    port would simply not be swept, and nothing would say so.  Reading the
    file the assertion is about means a new publisher is covered the day it
    lands.

    Returns:
        Service names, sorted, whose definition carries a non-empty ``ports``.
    """
    with DEV_COMPOSE.open(encoding="utf-8") as fh:
        parsed = yaml.safe_load(fh)
    return sorted(
        name
        for name, service in (parsed.get("services") or {}).items()
        if (service or {}).get("ports")
    )


class TestDevComposeNoImplicitAllInterfaceBind:
    """No dev-compose service publishes a port on ALL interfaces.

    **The asserted property is an EXPLICIT host IP, not loopback specifically,
    and the difference is load-bearing.**  Both findings behind this class are
    about the IMPLICIT bind: F-057 caught ``ports: ["5432:5432"]``, which
    Docker expands to ``0.0.0.0:5432`` and which put the dev database -- with
    the public credentials committed into that file -- on every LAN device
    that could route to the host; D09 (parity audit 2026-06-12) caught the
    same shape on the app service, exposing the Werkzeug debugger.

    The class used to hand-list ``db`` and ``test-db`` and demand
    ``127.0.0.1`` of both.  ``balance:X-br-4`` deleted ``test-db``, and
    deriving the list rather than shortening it to one entry immediately
    turned up why the narrower predicate was wrong: the app service publishes
    ``172.32.0.1:5000:5000`` DELIBERATELY, an explicit bind on the
    shekel-frontend bridge gateway so the operator's LAN-allowlisted nginx
    vhost can proxy to it (D09's own remedy, documented at that mapping).  A
    loopback-only rule calls that a defect. An explicit-host-IP rule does not,
    and still fails the exact shape both findings were written about.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed dev compose document.

        Class-scoped so the YAML is parsed once and reused across
        every parametrize case in the class.
        """
        with DEV_COMPOSE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_the_sweep_found_something_to_sweep(self) -> None:
        """Discovery ran, so the parametrized sweep is not vacuous.

        A parametrized sweep over an EMPTY list reports nothing at all rather
        than failing, so a rename of the ``ports`` key or a broken parse would
        silently retire this guard. ``db`` is pinned by name because it is the
        service F-057 was written about.
        """
        discovered = _dev_compose_services_publishing_ports()

        assert "db" in discovered, (
            f"the dev compose's published-port sweep found {discovered}, "
            "which does not include the dev database -- the guard below "
            "would pass while measuring nothing"
        )

    @pytest.mark.parametrize(
        "service_name", _dev_compose_services_publishing_ports()
    )
    def test_every_published_port_names_its_host_address(
        self,
        parsed: dict,
        service_name: str,
    ) -> None:
        """Each mapping carries a host IP, and it is not ``0.0.0.0``.

        Args:
            parsed: The parsed dev compose document.
            service_name: A service that publishes at least one host port.
        """
        service = parsed["services"][service_name]
        ports = service.get("ports") or []
        # Each entry can be the short string form
        # (``"127.0.0.1:5432:5432"``) or the long mapping with an
        # explicit ``host_ip``.  Normalise both shapes.
        for entry in ports:
            if isinstance(entry, str):
                # ``host_ip:host_port:container_port`` is three parts;
                # ``host_port:container_port`` is two and is the defect.
                host_ip = entry.split(":")[0] if entry.count(":") >= 2 else ""
            elif isinstance(entry, dict):
                host_ip = entry.get("host_ip") or ""
            else:
                raise AssertionError(
                    f"{service_name} port {entry!r} is neither the short "
                    "string form nor the long mapping; this sweep cannot "
                    "read it, so it is ungraded rather than safe"
                )
            assert host_ip and host_ip != "0.0.0.0", (
                f"{service_name} port {entry!r} does not name a host address, "
                "so Docker binds it on ALL interfaces and every LAN device "
                "that can route here reaches it with this file's committed "
                "credentials (audit F-057; parity finding D09 for the app "
                "service and its Werkzeug debugger). Prefix the mapping with "
                "an explicit address -- 127.0.0.1 unless something off-host "
                "genuinely needs it."
            )


class TestProdComposeOverrideInheritsHardening:
    """Verify the shared-mode override at
    ``deploy/docker-compose.prod.yml`` does NOT redeclare
    hardening fields the base file already sets, so the override
    cannot accidentally weaken them.

    Compose merges scalar keys per-service: if the override redeclared
    ``cap_drop:`` it would replace the base value entirely (compose
    does not deep-merge lists).  Asserting absence keeps the
    inheritance contract explicit.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed shared-mode override document.

        Class-scoped so the YAML is parsed once and reused across
        every parametrize case in the class.
        """
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.mark.parametrize(
        "field",
        (
            "security_opt",
            "cap_drop",
            "cap_add",
            "read_only",
            "tmpfs",
            "mem_limit",
            "pids_limit",
            "logging",
            "user",
        ),
    )
    def test_app_override_does_not_redeclare_hardening(
        self,
        parsed: dict,
        field: str,
    ) -> None:
        """Each hardening key the base file sets on the app service
        must NOT appear under ``services.app`` in the override.
        Redeclaring would reset the value to whatever the override
        specified -- silently weakening the posture if the override
        forgot a directive.
        """
        app = parsed["services"]["app"]
        assert field not in app, (
            f"deploy/docker-compose.prod.yml redeclares "
            f"services.app.{field!r}; this overrides (rather than "
            f"inherits) the base file's hardening.  Remove the "
            f"redeclaration and let compose merge from base."
        )


@pytest.mark.docker
class TestMergedComposeHardeningSurvivesOverride:
    """Run ``docker compose config`` against the merged base +
    shared-mode override and verify the hardening fields appear on
    the merged result for the app and db services.

    This is the highest-fidelity check: it exercises the same
    merge logic compose uses at deploy time and catches a regression
    where the override accidentally clobbers a base field.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def merged(cls) -> dict:
        """Return the merged base + shared-mode override compose
        document as a parsed dict.

        Skips the entire class if Docker is not available on the
        test host: there is no in-Python equivalent of compose's
        merge logic and a stubbed merge would not catch the
        regressions this class is meant to detect.

        Class-scoped so the subprocess only runs once per test
        session despite the eight parametrize cases below.
        """
        if not _docker_available():
            pytest.skip(
                "docker not available; cannot run docker compose config"
            )
        env = {
            "PATH": (
                "/usr/local/bin:/usr/local/sbin:/usr/bin:"
                "/usr/sbin:/bin:/sbin"
            ),
            "POSTGRES_PASSWORD": "test-postgres-password",
            "SECRET_KEY": (
                # 32-char fake key satisfies app/config.py's
                # _MIN_SECRET_KEY_LENGTH check.
                "a" * 32
            ),
            "APP_ROLE_PASSWORD": "test-app-role-password",
            # Synthetic image digest satisfies the Commit C-36 / F-060
            # required-form interpolation in deploy/docker-compose.prod.yml
            # (``image: ghcr.io/saltyreformed-labs/shekel@${SHEKEL_IMAGE_DIGEST:?...}``).
            # 64 hex chars is the canonical OCI digest shape; the
            # value never reaches a real pull because ``compose config``
            # only renders the merged YAML.
            "SHEKEL_IMAGE_DIGEST": "sha256:" + "0" * 64,
            # The Valkey password: the app's RATELIMIT_STORAGE_URI and
            # the valkey service's VALKEY_PASSWORD interpolate
            # ``${SHEKEL_VALKEY_PASSWORD:?...}`` from .env.  Synthetic
            # value satisfies the required-form check; the test never
            # actually starts the valkey container.
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
            f"docker compose config failed:\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        return yaml.safe_load(result.stdout)

    @pytest.mark.parametrize("service_name", ("db", "app"))
    def test_merged_service_carries_security_opt(
        self,
        merged: dict,
        service_name: str,
    ) -> None:
        """The shared-mode merge must preserve ``security_opt`` on
        the app and db services (nginx is parked in the disabled
        profile in shared mode and is therefore filtered from the
        default merge result).
        """
        service = merged["services"][service_name]
        opts = service.get("security_opt") or []
        assert "no-new-privileges:true" in opts, (
            f"merged {service_name} service missing "
            f"no-new-privileges:true: {opts}"
        )

    @pytest.mark.parametrize("service_name", ("db", "app"))
    def test_merged_service_carries_cap_drop(
        self,
        merged: dict,
        service_name: str,
    ) -> None:
        """The shared-mode merge must preserve ``cap_drop: [ALL]``
        on the app and db services.
        """
        service = merged["services"][service_name]
        cap_drop = service.get("cap_drop") or []
        assert "ALL" in cap_drop, (
            f"merged {service_name} service missing cap_drop ALL: "
            f"{cap_drop}"
        )

    @pytest.mark.parametrize("service_name", ("db", "app"))
    def test_merged_service_carries_read_only(
        self,
        merged: dict,
        service_name: str,
    ) -> None:
        """The shared-mode merge must preserve ``read_only: true``."""
        service = merged["services"][service_name]
        assert service.get("read_only") is True, (
            f"merged {service_name} service lost read_only: true "
            f"after override merge"
        )

    @pytest.mark.parametrize("service_name", ("db", "app"))
    def test_merged_service_carries_resource_limits(
        self,
        merged: dict,
        service_name: str,
    ) -> None:
        """The shared-mode merge must preserve the memory and pids
        caps -- the override does not redeclare them, so any loss
        here means compose collapsed the values during merge.

        Reads ``deploy.resources.limits`` (Compose-Spec form, adopted
        2026-05-14) with a fallback to legacy ``mem_limit`` /
        ``pids_limit`` for older branches.
        """
        service = merged["services"][service_name]
        limits = (
            service.get("deploy", {})
            .get("resources", {})
            .get("limits", {})
        )
        mem_limit = limits.get("memory") or service.get("mem_limit")
        pids_limit = limits.get("pids") or service.get("pids_limit")
        assert mem_limit, (
            f"merged {service_name} service missing memory limit "
            f"(deploy.resources.limits.memory or mem_limit): "
            f"{mem_limit!r}"
        )
        assert pids_limit, (
            f"merged {service_name} service missing pids limit "
            f"(deploy.resources.limits.pids or pids_limit): "
            f"{pids_limit!r}"
        )
