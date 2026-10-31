"""Tests for remediation Commit C-37: internal TLS and Cloudflare Access.

Covers audit findings:

    F-061  cloudflared has no Access policy and uses noTLSVerify: true
    F-128  cloudflared metrics endpoint reachable from homelab
    F-154  TLS not on internal connections (DB plain TCP)

The artifacts under audit are:

    cloudflared/config.yml                Adds metrics: 127.0.0.1:2000 +
                                          originRequest.access block.
    deploy/docker-compose.prod.yml        Adds db service TLS command +
                                          volumes for cert/key + app
                                          DATABASE_URL ?sslmode=require +
                                          DB_SSLMODE + PGSSLMODE.
    entrypoint.sh                         Honours DB_SSLMODE in the
                                          DATABASE_URL_APP construction.
    scripts/generate_pg_cert.sh           Generates the self-signed
                                          cert/key with proper modes
                                          and ownership.
    deploy/postgres/                      Bind-mount source directory
                                          for the cert/key (gitignored
                                          contents).
    .gitignore                            Excludes the private key.

Tests are filesystem/text-based: the fast unit suite must not depend
on a live Docker daemon, openssl install, or sudo privileges. A
separate subprocess test (``TestMergedComposeCarriesTLS``) runs
``docker compose config`` to exercise the merge interpolation
behaviour and skips when Docker is not available.

The runtime-behaviour tests (real openssl-generated cert, postgres
container with ssl=on, psycopg sslmode=require connection) are the
manual verification step in the remediation plan and are not
automated here -- automating them would require sudo and a working
Docker daemon on every test host.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
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


# ──────────────────────────────────────────────────────────────────
# F-061: Cloudflare Access policy + noTLSVerify rationale.
# ──────────────────────────────────────────────────────────────────


class TestCloudflaredAccessPolicy:
    """The cloudflared config carries an originRequest.access block
    on the production ingress rule so a request without a valid
    Access JWT is rejected at the cloudflared edge before it ever
    reaches Nginx.  Audit finding F-061.

    The committed file uses placeholder values (``<TEAM_NAME>``,
    ``<AUD_TAG>``); these tests assert the structural shape, not the
    placeholder values themselves -- a runtime check that the
    placeholders have been replaced is the operator's responsibility
    and is documented in docs/runbook.md §6.4a.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the cloudflared config text."""
        return CLOUDFLARED_CONFIG.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Parse the cloudflared config as YAML.

        The placeholder values (``<TUNNEL_ID>``, ``<TEAM_NAME>``,
        ``<AUD_TAG>``, ``<DOMAIN>``) are valid YAML strings so
        ``yaml.safe_load`` succeeds; we just cannot interpret them
        as Cloudflare cares about, which is fine for these tests.
        """
        return yaml.safe_load(CLOUDFLARED_CONFIG.read_text(encoding="utf-8"))

    def test_production_rule_has_access_block(self, parsed: dict) -> None:
        """The production ingress rule (the one matching ``<DOMAIN>``)
        must declare an ``originRequest.access`` block.

        cloudflared evaluates ingress rules top-to-bottom; the first
        rule with a hostname is the production rule.  The catch-all
        (``service: http_status:404``) at the bottom does not need
        an Access block because it never serves real content.
        """
        ingress = parsed.get("ingress")
        assert isinstance(ingress, list) and len(ingress) >= 2, (
            "cloudflared ingress missing or too short -- expected at "
            "least one production rule plus the catch-all 404"
        )
        # The first rule with a hostname must carry the Access block.
        # We do not require a specific position -- a future change
        # might add a staging rule between the production rule and
        # the catch-all.
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        assert production_rules, (
            "no ingress rule with a hostname -- the catch-all alone "
            "is not a valid Cloudflare Tunnel config"
        )
        for rule in production_rules:
            origin_request = rule.get("originRequest") or {}
            access = origin_request.get("access")
            assert isinstance(access, dict), (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"missing originRequest.access block (audit F-061)"
            )

    def test_access_block_required_true(self, parsed: dict) -> None:
        """``access.required: true`` is the load-bearing flag.

        Without ``required: true``, cloudflared treats the Access
        check as advisory -- a request without a valid JWT still
        forwards to the origin.  The audit fix demands hard
        enforcement at the cloudflared edge.
        """
        ingress = parsed["ingress"]
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        for rule in production_rules:
            access = rule["originRequest"]["access"]
            assert access.get("required") is True, (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"has access.required != True (audit F-061): "
                f"{access!r}"
            )

    def test_access_block_carries_team_name_placeholder(
        self, parsed: dict
    ) -> None:
        """``teamName`` is a placeholder (``<TEAM_NAME>``) in the
        committed template.  The presence of the key is what matters
        here -- the placeholder substitution is the operator's step.
        """
        ingress = parsed["ingress"]
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        for rule in production_rules:
            access = rule["originRequest"]["access"]
            team_name = access.get("teamName")
            assert team_name, (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"missing access.teamName (audit F-061)"
            )

    def test_access_block_carries_aud_tag_list(
        self, parsed: dict
    ) -> None:
        """``audTag`` must be a non-empty list.

        Cloudflare allows multiple AUDs per ingress rule (e.g. one
        application protecting both a primary and a staging
        hostname).  We do not require a specific count -- a single
        AUD is the common case.
        """
        ingress = parsed["ingress"]
        production_rules = [
            rule for rule in ingress
            if isinstance(rule, dict) and "hostname" in rule
        ]
        for rule in production_rules:
            access = rule["originRequest"]["access"]
            aud_tag = access.get("audTag")
            assert isinstance(aud_tag, list) and aud_tag, (
                f"ingress rule for hostname={rule.get('hostname')!r} "
                f"access.audTag is not a non-empty list (audit "
                f"F-061): {aud_tag!r}"
            )

    def test_documents_access_rationale_in_comment(
        self, text: str
    ) -> None:
        """The cloudflared template must carry a comment explaining
        the Access policy so a future operator does not "simplify" by
        deleting the block.  The comment cites F-061 / C-37 so the
        audit trail is inline.
        """
        assert "F-061" in text, (
            "cloudflared/config.yml does not mention F-061 -- the "
            "operator has no inline pointer to the audit finding "
            "behind the Access policy block"
        )
        assert "C-37" in text, (
            "cloudflared/config.yml does not mention C-37 -- the "
            "remediation commit reference is missing from the "
            "rationale comment"
        )
        # The rationale must explain the auth surface, not just
        # echo the directive.  We assert on a fragment of the
        # block-comment phrase.
        assert "Cloudflare Access" in text


# ──────────────────────────────────────────────────────────────────
# F-128: cloudflared metrics endpoint binds loopback only.
# ──────────────────────────────────────────────────────────────────


class TestCloudflaredMetricsLoopback:
    """The cloudflared config pins the Prometheus metrics endpoint to
    127.0.0.1:2000 instead of the default 0.0.0.0:2000.  Audit finding
    F-128.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the cloudflared config text."""
        return CLOUDFLARED_CONFIG.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Parse the cloudflared config as YAML."""
        return yaml.safe_load(CLOUDFLARED_CONFIG.read_text(encoding="utf-8"))

    def test_metrics_directive_present(self, parsed: dict) -> None:
        """The top-level ``metrics`` key must exist.

        Without this directive, cloudflared falls back to its
        internal default which historically binds 0.0.0.0:2000
        (audit finding F-128).
        """
        assert "metrics" in parsed, (
            "cloudflared/config.yml has no top-level ``metrics`` "
            "directive -- the binding falls back to 0.0.0.0:2000 "
            "(audit F-128)"
        )

    def test_metrics_binds_loopback_only(self, parsed: dict) -> None:
        """The metrics directive must bind to 127.0.0.1.

        Other loopback shapes (``localhost:2000``, ``[::1]:2000``)
        are functionally equivalent for the security property but
        the canonical form is 127.0.0.1 -- pin the literal string
        so a regression to ``0.0.0.0`` or a sloppy ``:2000`` (no
        host) fails the test loudly.
        """
        metrics = parsed["metrics"]
        assert isinstance(metrics, str), (
            f"cloudflared metrics directive is not a string: "
            f"{metrics!r}"
        )
        assert metrics.startswith("127.0.0.1:"), (
            f"cloudflared metrics directive is not loopback-only "
            f"(audit F-128): {metrics!r}"
        )

    def test_documents_loopback_rationale(self, text: str) -> None:
        """The metrics directive must carry an inline comment citing
        F-128 / C-37 so a future operator who deletes the directive
        re-introduces the audit finding by name in the diff.
        """
        # Find the line with metrics: 127.0.0.1 and walk backward
        # through the immediately-preceding comment block.
        match = re.search(
            r"((?:^#.*\n)+)metrics:\s*127\.0\.0\.1",
            text,
            re.MULTILINE,
        )
        assert match is not None, (
            "cloudflared/config.yml ``metrics: 127.0.0.1:2000`` "
            "has no leading comment block; the operator has no "
            "inline rationale"
        )
        block = match.group(1)
        assert "F-128" in block, (
            "metrics directive comment does not mention F-128 -- the "
            "audit-finding pointer is missing"
        )
        assert "C-37" in block, (
            "metrics directive comment does not mention C-37 -- the "
            "remediation commit pointer is missing"
        )


# ──────────────────────────────────────────────────────────────────
# F-154: Postgres TLS via deploy/docker-compose.prod.yml.
# ──────────────────────────────────────────────────────────────────


class TestProdComposeOverridePostgresTLS:
    """The shared-mode override at deploy/docker-compose.prod.yml
    layers Postgres TLS onto the base file's plaintext db service.
    Audit finding F-154 / Commit C-37.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed prod override document."""
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the raw prod override text for substring assertions."""
        return PROD_COMPOSE_OVERRIDE.read_text(encoding="utf-8")

    def test_db_service_overrides_command_with_ssl_on(
        self, parsed: dict
    ) -> None:
        """The override must declare a ``command:`` block on the db
        service that runs ``postgres -c ssl=on``.

        The base ``docker-compose.yml`` has no command override on
        the db service; without this layer, the postgres process
        starts with the image default (``ssl=off``) and DATABASE_URL's
        ``?sslmode=require`` fails at every connect.
        """
        db = parsed["services"]["db"]
        command = db.get("command")
        assert isinstance(command, list) and command, (
            "deploy/docker-compose.prod.yml services.db has no "
            "command list (audit F-154); the postgres process would "
            "start with ssl=off"
        )
        # The command must include the literal ``ssl=on`` setting.
        # A regression to ``ssl=off`` or a typo like ``ssl=true``
        # would fail this assertion loudly.
        assert "ssl=on" in command, (
            f"deploy/docker-compose.prod.yml services.db.command "
            f"does not pass ``-c ssl=on`` (audit F-154): {command!r}"
        )

    def test_db_command_pins_ssl_cert_and_key_paths(
        self, parsed: dict
    ) -> None:
        """The ``command`` block names the cert/key files.

        Postgres looks for ssl_cert_file and ssl_key_file relative
        to PGDATA when the command-line directive is unset; the
        override pins explicit paths so the bind mount target is
        the source of truth and a future PGDATA tweak does not
        accidentally route Postgres to a non-existent default.
        """
        command = parsed["services"]["db"]["command"]
        joined = " ".join(str(c) for c in command)
        assert "ssl_cert_file=/etc/postgresql/certs/server.crt" in joined, (
            f"db.command does not pin ssl_cert_file to "
            f"/etc/postgresql/certs/server.crt (audit F-154): "
            f"{command!r}"
        )
        assert "ssl_key_file=/etc/postgresql/certs/server.key" in joined, (
            f"db.command does not pin ssl_key_file to "
            f"/etc/postgresql/certs/server.key (audit F-154): "
            f"{command!r}"
        )

    def test_db_command_pins_tls_minimum_version(
        self, parsed: dict
    ) -> None:
        """``ssl_min_protocol_version=TLSv1.2`` bars TLS 1.0 and 1.1.

        ASVS L2 V9.1.2 requires TLS 1.2 or higher.  Postgres 16's
        default minimum is already TLSv1.2, but pinning the
        directive explicitly prevents a future libssl downgrade
        (or a misconfigured Debian backport) from silently
        re-enabling 1.0 / 1.1.
        """
        command = parsed["services"]["db"]["command"]
        joined = " ".join(str(c) for c in command)
        assert "ssl_min_protocol_version=TLSv1.2" in joined, (
            f"db.command does not pin ssl_min_protocol_version to "
            f"TLSv1.2 (ASVS V9.1.2): {command!r}"
        )

    def test_db_volumes_include_cert_and_key_mounts(
        self, parsed: dict
    ) -> None:
        """Two read-only bind mounts expose the operator-generated
        cert and key inside the container.  Compose's per-service
        ``volumes`` list is additively merged with the base file's
        ``shekel-prod-pgdata`` mount, so the override contributes
        only the cert/key entries.

        We do not assert the pgdata mount is present here because
        that test belongs to the merge (compose) check below; the
        override's own volumes list contains only the new entries.
        """
        db = parsed["services"]["db"]
        volumes = db.get("volumes") or []
        # Each entry should be a string in the short-form
        # ``host_path:container_path:options`` format.
        assert any(
            "deploy/postgres/server.crt" in v
            and "/etc/postgresql/certs/server.crt" in v
            and ":ro" in v
            for v in volumes
        ), (
            f"deploy/docker-compose.prod.yml services.db.volumes "
            f"missing read-only mount for server.crt (audit "
            f"F-154): {volumes!r}"
        )
        assert any(
            "deploy/postgres/server.key" in v
            and "/etc/postgresql/certs/server.key" in v
            and ":ro" in v
            for v in volumes
        ), (
            f"deploy/docker-compose.prod.yml services.db.volumes "
            f"missing read-only mount for server.key (audit "
            f"F-154): {volumes!r}"
        )

    def test_db_initdb_args_include_data_checksums(
        self, parsed: dict
    ) -> None:
        """``POSTGRES_INITDB_ARGS=--data-checksums`` only takes effect
        on a fresh ``initdb``, but pinning it future-proofs the next
        cluster bootstrap.  The audit plan called this out as a paired
        hardening item; we assert it survives the override.
        """
        env = parsed["services"]["db"].get("environment") or {}
        initdb_args = env.get("POSTGRES_INITDB_ARGS")
        assert initdb_args, (
            "deploy/docker-compose.prod.yml services.db.environment "
            "missing POSTGRES_INITDB_ARGS (audit F-154 paired "
            "hardening)"
        )
        assert "--data-checksums" in initdb_args, (
            f"POSTGRES_INITDB_ARGS does not include --data-checksums: "
            f"{initdb_args!r}"
        )


class TestProdComposeOverrideAppDatabaseUrl:
    """The override repins the app service's DATABASE_URL with
    ``?sslmode=require`` so the SQLAlchemy engine refuses any
    connection the server cannot upgrade to TLS.  Audit finding
    F-154 / Commit C-37.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parsed(cls) -> dict:
        """Return the parsed prod override document."""
        with PROD_COMPOSE_OVERRIDE.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_app_database_url_requires_ssl(self, parsed: dict) -> None:
        """The app service's DATABASE_URL must end in
        ``?sslmode=require`` (or carry it as a non-trailing query
        parameter).
        """
        env = parsed["services"]["app"].get("environment") or {}
        database_url = env.get("DATABASE_URL")
        assert database_url, (
            "deploy/docker-compose.prod.yml services.app.environment "
            "does not override DATABASE_URL (audit F-154): the base "
            "file's plaintext URL would survive the merge"
        )
        # The URL must include a sslmode=require parameter.  Accept
        # ?sslmode=require, &sslmode=require, or sslmode=require as
        # the last param.
        assert "sslmode=require" in database_url, (
            f"deploy/docker-compose.prod.yml services.app.environment"
            f".DATABASE_URL does not request sslmode=require (audit "
            f"F-154): {database_url!r}"
        )

    def test_app_db_sslmode_env_var_set(self, parsed: dict) -> None:
        """``DB_SSLMODE=require`` is consumed by entrypoint.sh when
        constructing DATABASE_URL_APP (the least-privilege role's
        URL).  Without this var, the runtime app would connect under
        the owner role's TLS-enabled URL but the gunicorn process
        would re-export DATABASE_URL_APP without the sslmode flag,
        defeating the C-37 fix at the very last hop.
        """
        env = parsed["services"]["app"].get("environment") or {}
        assert env.get("DB_SSLMODE") == "require", (
            f"deploy/docker-compose.prod.yml services.app.environment"
            f".DB_SSLMODE is not 'require' (audit F-154): "
            f"{env.get('DB_SSLMODE')!r}.  entrypoint.sh's "
            f"DATABASE_URL_APP construction reads this env var."
        )

    def test_app_pgsslmode_env_var_set(self, parsed: dict) -> None:
        """``PGSSLMODE=require`` is the standard libpq env var.

        Every ``psql`` call in entrypoint.sh (init_db.sql apply,
        role provisioning) reads this env var for its connection.
        (The audit-trigger check left the shell for step 3's one
        transaction at plan step balance:X-cv; it connects through
        ``DATABASE_URL``, whose ``sslmode`` the test above pins.)
        Setting it once on the app
        service means we do not have to thread ``--set
        sslmode=require`` through every psql invocation.
        """
        env = parsed["services"]["app"].get("environment") or {}
        assert env.get("PGSSLMODE") == "require", (
            f"deploy/docker-compose.prod.yml services.app.environment"
            f".PGSSLMODE is not 'require' (audit F-154): "
            f"{env.get('PGSSLMODE')!r}"
        )


class TestEntrypointHonoursDbSslmode:
    """entrypoint.sh constructs DATABASE_URL_APP from individual
    DB_* env vars just before exec'ing Gunicorn.  C-37 adds
    DB_SSLMODE consumption to that construction so the runtime app
    inherits the same TLS posture as the owner role.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the entrypoint.sh source as a string."""
        return ENTRYPOINT.read_text(encoding="utf-8")

    def test_script_parses_cleanly(self) -> None:
        """``bash -n`` accepts the script (no syntax errors)."""
        result = subprocess.run(
            ["bash", "-n", str(ENTRYPOINT)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, (
            f"bash -n failed for {ENTRYPOINT}:\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )

    def test_constructs_database_url_app_with_sslmode(
        self, text: str
    ) -> None:
        """The DATABASE_URL_APP construction must reference
        DB_SSLMODE.  We grep for the variable name and the
        construction line.
        """
        assert "DB_SSLMODE" in text, (
            "entrypoint.sh does not reference DB_SSLMODE -- the "
            "DATABASE_URL_APP construction would emit a plaintext "
            "URL even when the prod override sets DB_SSLMODE=require "
            "(audit F-154)"
        )
        # The DATABASE_URL_APP export line must concatenate the
        # ssl-mode query suffix.  The literal pattern allows for
        # whitespace flexibility in the variable name expansion.
        pattern = re.compile(
            r"export\s+DATABASE_URL_APP=.*\$\{DB_SSLMODE_QUERY\}",
        )
        assert pattern.search(text) is not None, (
            "entrypoint.sh's DATABASE_URL_APP export does not "
            "concatenate DB_SSLMODE_QUERY -- the runtime URL would "
            "lack the ?sslmode= suffix"
        )

    def test_db_sslmode_query_built_only_when_set(
        self, text: str
    ) -> None:
        """The construction must guard against an unset DB_SSLMODE
        so bundled-mode (without DB_SSLMODE) keeps emitting a
        plaintext URL.  Mandatory ssl-mode in bundled mode would
        break the README Quick Start, where postgres has no cert.
        """
        # A simple shape check: the script must use the ``${VAR:-}``
        # idiom (or an explicit empty default) to test DB_SSLMODE
        # before appending the query suffix.
        pattern = re.compile(
            r'if\s+\[\s+-n\s+"\$\{DB_SSLMODE:?-?\}"\s+\]',
        )
        assert pattern.search(text) is not None, (
            "entrypoint.sh does not guard the DB_SSLMODE_QUERY "
            "construction with a DB_SSLMODE non-empty check; "
            "bundled-mode deployments without TLS would emit a "
            "broken URL"
        )


# ──────────────────────────────────────────────────────────────────
# scripts/generate_pg_cert.sh: the cert generator helper.
# ──────────────────────────────────────────────────────────────────


class TestGenerateCertScript:
    """``scripts/generate_pg_cert.sh`` is the single source of
    truth for producing the deploy/postgres/server.{crt,key} pair
    with the modes and ownership Postgres requires.  Audit finding
    F-154 / Commit C-37.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the script source as a string."""
        return GENERATE_CERT_SCRIPT.read_text(encoding="utf-8")

    def test_script_is_executable(self) -> None:
        """The committed script has the executable bit set so
        operators can ``./scripts/generate_pg_cert.sh`` directly.
        """
        assert GENERATE_CERT_SCRIPT.is_file(), (
            f"scripts/generate_pg_cert.sh missing at "
            f"{GENERATE_CERT_SCRIPT}"
        )
        # On POSIX systems os.access(X_OK) is the canonical check.
        import os
        assert os.access(GENERATE_CERT_SCRIPT, os.X_OK), (
            "scripts/generate_pg_cert.sh is not executable; the "
            "operator would have to chmod +x it manually"
        )

    def test_script_parses_cleanly(self) -> None:
        """``bash -n`` accepts the script."""
        result = subprocess.run(
            ["bash", "-n", str(GENERATE_CERT_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, (
            f"bash -n failed for {GENERATE_CERT_SCRIPT}:\n"
            f"  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )

    def test_help_runs_without_root(self) -> None:
        """``--help`` must succeed for any user (no sudo / openssl
        check) so an operator can read the doc before they realise
        sudo is needed."""
        result = subprocess.run(
            [str(GENERATE_CERT_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, (
            f"--help exited non-zero:\n  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        # The output must mention the load-bearing flags and the
        # finding number so the operator can cross-reference the
        # audit doc.
        for marker in (
            "--days",
            "--cn",
            "--force",
            "F-154",
            "C-37",
            "deploy/postgres",
        ):
            assert marker in result.stdout, (
                f"--help output does not mention {marker!r}"
            )

    def test_refuses_to_run_without_root(self, tmp_path: Path) -> None:
        """Running without sudo must produce a clean error rather
        than a partial cert + chown failure.  We rely on the test
        environment NOT being uid 0 (standard CI / dev posture).
        """
        import os
        if os.geteuid() == 0:
            pytest.skip(
                "running as root; cannot exercise the non-root "
                "refusal path"
            )
        result = subprocess.run(
            [
                str(GENERATE_CERT_SCRIPT),
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode != 0, (
            "generate_pg_cert.sh succeeded as a non-root user; "
            "the chown step would have failed silently"
        )
        combined = result.stdout + result.stderr
        assert "root" in combined.lower(), (
            "non-root error message does not mention root / sudo: "
            f"{combined!r}"
        )

    def test_validates_days_argument(self, tmp_path: Path) -> None:
        """``--days abc`` must fail with a clear error rather than
        passing the bad value through to openssl."""
        result = subprocess.run(
            [
                str(GENERATE_CERT_SCRIPT),
                "--days",
                "abc",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode != 0, (
            "generate_pg_cert.sh accepted --days abc; argument "
            "validation is missing"
        )
        combined = result.stdout + result.stderr
        assert "positive integer" in combined.lower() or \
               "must be a" in combined.lower(), (
            f"non-numeric --days error message is not actionable: "
            f"{combined!r}"
        )

    def test_documents_postgres_uid_default(self, text: str) -> None:
        """The script must default to uid 70 for the key file
        ownership -- that is the postgres user inside
        postgres:16-alpine, and any other value would produce a
        cert Postgres refuses to read.
        """
        # The default appears in two places: the help text and the
        # POSTGRES_UID variable assignment.  Either is sufficient
        # documentation; we assert on the variable assignment shape
        # so a refactor that moves the default into a constant
        # somewhere else would still pass.
        assert re.search(
            r'POSTGRES_UID="?70"?', text,
        ), (
            "scripts/generate_pg_cert.sh default POSTGRES_UID is not "
            "70 (the postgres user in postgres:16-alpine)"
        )


# ──────────────────────────────────────────────────────────────────
# deploy/postgres/ directory shape + .gitignore guard.
# ──────────────────────────────────────────────────────────────────


class TestPostgresDirectoryShape:
    """deploy/postgres/ exists in the repo as the bind-mount source
    path for the cert/key.  The directory ships with only a README
    (the cert/key are gitignored).  Audit finding F-154 / Commit
    C-37.
    """

    def test_directory_exists(self) -> None:
        """The directory must exist on disk so ``compose config``
        does not fail to resolve the bind mount source path on a
        fresh checkout."""
        assert POSTGRES_DIR.is_dir(), (
            f"deploy/postgres/ missing at {POSTGRES_DIR}; the "
            f"bind-mount source path in deploy/docker-compose.prod.yml"
            f" would fail to resolve on a fresh clone"
        )

    def test_readme_present(self) -> None:
        """A README documents what the directory is for so a future
        operator does not delete it as "empty"."""
        assert POSTGRES_README.is_file(), (
            f"deploy/postgres/README.md missing at "
            f"{POSTGRES_README}; the directory has no inline doc"
        )

    @pytest.mark.parametrize(
        "marker",
        (
            "F-154",
            "C-37",
            "scripts/generate_pg_cert.sh",
            "server.crt",
            "server.key",
            "0644",
            "0600",
        ),
    )
    def test_readme_documents_marker(self, marker: str) -> None:
        """Each load-bearing reference appears in the README so the
        operator workflow is self-contained."""
        text = POSTGRES_README.read_text(encoding="utf-8")
        assert marker in text, (
            f"deploy/postgres/README.md does not mention {marker!r}; "
            f"operator workflow doc is incomplete"
        )


class TestGitignoreExcludesPostgresKeyAndCert:
    """``deploy/postgres/server.{crt,key}`` must be gitignored so a
    careless ``git add deploy/postgres`` cannot leak the private
    key.  Audit finding F-154 / Commit C-37.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the .gitignore source as a string."""
        return GITIGNORE.read_text(encoding="utf-8")

    def test_server_key_excluded(self, text: str) -> None:
        """``deploy/postgres/server.key`` (the private key) must be
        gitignored.  This is the load-bearing exclusion."""
        assert re.search(
            r"^deploy/postgres/server\.key\b",
            text,
            re.MULTILINE,
        ), (
            ".gitignore does not exclude deploy/postgres/server.key "
            "(audit F-154); the private key could leak via a stray "
            "``git add deploy/postgres``"
        )

    def test_server_cert_excluded(self, text: str) -> None:
        """``deploy/postgres/server.crt`` is also excluded.  The
        cert is operationally treated as private-by-default because
        a future upgrade to ``sslmode=verify-ca`` would promote it
        to a CA cert that operators distribute manually."""
        assert re.search(
            r"^deploy/postgres/server\.crt\b",
            text,
            re.MULTILINE,
        ), (
            ".gitignore does not exclude deploy/postgres/server.crt"
        )


# ──────────────────────────────────────────────────────────────────
# Merge fidelity: ``docker compose config`` carries the C-37 fields.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.docker
class TestMergedComposeCarriesTLS:
    """Run ``docker compose config`` against the merged base + prod
    override and verify the C-37 fields appear on the merged result.

    This is the highest-fidelity check: it exercises the same merge
    logic compose uses at deploy time and catches a regression where
    the override accidentally clobbers a base field.

    Skipped when Docker is not available on the test host.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def merged(cls) -> dict:
        """Return the merged base + shared-mode override compose
        document as a parsed dict.

        Class-scoped so the subprocess only runs once per test
        session.
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
            "SECRET_KEY": "a" * 32,
            "APP_ROLE_PASSWORD": "test-app-role-password",
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
            f"docker compose config failed:\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        return yaml.safe_load(result.stdout)

    def test_merged_db_command_runs_postgres_with_ssl_on(
        self, merged: dict
    ) -> None:
        """The merged db service must carry the ``-c ssl=on`` flag.

        Compose merges scalar / list keys differently per key
        (volumes are additive but command is replacement).  This
        test exercises the actual merge behaviour: even though
        the base file has no command, the override's command
        survives the merge.
        """
        db = merged["services"]["db"]
        command = db.get("command")
        assert isinstance(command, list) and "ssl=on" in command, (
            f"merged db service does not carry ``-c ssl=on``: "
            f"{command!r}"
        )

    def test_merged_db_volumes_include_pgdata_and_certs(
        self, merged: dict
    ) -> None:
        """The merged volumes list must include BOTH the base
        file's ``shekel-prod-pgdata`` mount AND the override's
        cert/key mounts.  Compose merges per-service volume
        lists additively (per the v2 docs); a regression that
        replaces the list would drop pgdata and lose the
        database on next ``up``.
        """
        volumes = merged["services"]["db"].get("volumes") or []
        # Volumes come back in long-form mapping shape after compose
        # config rendering.  Extract the source field for each entry.
        sources = []
        for entry in volumes:
            if isinstance(entry, dict):
                sources.append(str(entry.get("source") or ""))
            elif isinstance(entry, str):
                sources.append(entry)
        # The pgdata volume must survive the merge.
        assert any("shekel-prod-pgdata" in s for s in sources), (
            f"merged db service lost the shekel-prod-pgdata volume "
            f"(would lose all data on next up): {sources!r}"
        )
        # The cert/key bind mounts must be present.
        assert any("server.crt" in s for s in sources), (
            f"merged db service missing server.crt bind mount: "
            f"{sources!r}"
        )
        assert any("server.key" in s for s in sources), (
            f"merged db service missing server.key bind mount: "
            f"{sources!r}"
        )

    def test_merged_app_database_url_carries_sslmode(
        self, merged: dict
    ) -> None:
        """The merged app service must have DATABASE_URL with
        ``?sslmode=require``.  Compose maps environment dicts by
        key, so the override replaces the base file's plaintext
        URL on this specific key without touching other env vars.
        """
        env = merged["services"]["app"].get("environment") or {}
        database_url = env.get("DATABASE_URL")
        assert database_url, (
            "merged app service has no DATABASE_URL after override"
        )
        assert "sslmode=require" in database_url, (
            f"merged app DATABASE_URL does not request sslmode=require"
            f": {database_url!r}"
        )

    def test_merged_app_carries_db_sslmode_and_pgsslmode(
        self, merged: dict
    ) -> None:
        """The merged app service environment must carry
        DB_SSLMODE and PGSSLMODE so entrypoint.sh and every
        psql call inherit the same TLS posture.
        """
        env = merged["services"]["app"].get("environment") or {}
        assert env.get("DB_SSLMODE") == "require", (
            f"merged app DB_SSLMODE != 'require': "
            f"{env.get('DB_SSLMODE')!r}"
        )
        assert env.get("PGSSLMODE") == "require", (
            f"merged app PGSSLMODE != 'require': "
            f"{env.get('PGSSLMODE')!r}"
        )


# ──────────────────────────────────────────────────────────────────
# Runbook documentation surface.
# ──────────────────────────────────────────────────────────────────


class TestRunbookDocumentsC37Procedures:
    """docs/runbook.md carries the operator-facing procedures for the
    C-37 changes (cert generation, Access policy attachment, metrics
    binding verification, troubleshooting).  Discoverability matters:
    operators reading the runbook must find the workflow without
    context-switching to the audit findings.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def text(cls) -> str:
        """Return the full runbook source as a string."""
        return RUNBOOK.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "marker",
        (
            "scripts/generate_pg_cert.sh",
            "sslmode=require",
            "originRequest.access",
            "127.0.0.1:2000",
            "F-061",
            "F-128",
            "F-154",
            "C-37",
            "deploy/postgres",
        ),
    )
    def test_runbook_mentions_marker(self, text: str, marker: str) -> None:
        """Each load-bearing reference the operator needs appears
        somewhere in the runbook.  Catches a partial documentation
        regression where one section is updated but the troubleshooting
        table or §6 lags.
        """
        assert marker in text, (
            f"docs/runbook.md does not mention {marker!r}; the "
            f"operator workflow documentation is incomplete"
        )

    def test_runbook_has_postgres_tls_section(self, text: str) -> None:
        """A dedicated subsection covers the cert generation +
        startup procedure.  Section number is not asserted
        (renumbering is fine) but the heading text is."""
        assert re.search(
            r"^###?\s.*Postgres\s*TLS",
            text,
            re.MULTILINE,
        ), (
            "docs/runbook.md does not have a Postgres TLS section "
            "heading; the operator has no documented cert workflow"
        )

    def test_runbook_has_access_policy_section(self, text: str) -> None:
        """A dedicated subsection covers the Cloudflare Access
        attachment procedure (placeholder substitution + reload)."""
        assert re.search(
            r"^###?\s.*Access\s*Policy",
            text,
            re.MULTILINE | re.IGNORECASE,
        ), (
            "docs/runbook.md does not have an Access Policy section "
            "heading; the operator has no documented placeholder-"
            "substitution workflow"
        )
