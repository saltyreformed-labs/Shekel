"""Deployment-layer assertions for Commit C-34.

Covers audit findings F-022 (SEED_USER_PASSWORD persists in container
env), F-053 (REGISTRATION_ENABLED=true default), F-054 (stale
container retire helper -- RETIRED 2026-06-12: the one-shot script was
deleted after its targets, the pre-rename containers/volumes, were
removed; polyglot audit OPS/SH-25), and F-113 (.dockerignore excludes
dev-only files).  These tests are intentionally
filesystem-and-text-based: they assert on the on-disk shape of the
deployment artefacts that the Phase 6 audit reviewers will inspect,
rather than booting a container to observe runtime behaviour.

The runtime-behaviour assertions for the Python side of F-022 (env
scrubbing in seed_user.py) live in
tests/test_scripts/test_seed_user.py::TestSeedUserCredentialScrub.
The runtime-behaviour assertions for the route-level F-053 fix
(REGISTRATION_ENABLED=false returns 404) live in
tests/test_routes/test_auth.py::TestRegistrationToggle (existing).
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Files under assertion ──────────────────────────────────────────
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKER_COMPOSE_PROD = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
DOCKER_COMPOSE_DEV = REPO_ROOT / "docker-compose.dev.yml"
ENTRYPOINT_SCRIPT = REPO_ROOT / "entrypoint.sh"
SEED_USER_SCRIPT = REPO_ROOT / "scripts" / "seed_user.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKERFILE = REPO_ROOT / "Dockerfile"
APP_CONFIG = REPO_ROOT / "app" / "config.py"


# ──────────────────────────────────────────────────────────────────
# F-022: SEED_USER_PASSWORD env hygiene
# ──────────────────────────────────────────────────────────────────


class TestSeedCredentialEntrypointScrub:
    """entrypoint.sh must unset SEED_USER_* before exec'ing Gunicorn.

    The unset is the load-bearing fix for F-022: the credentials
    leave the entrypoint shell's environment BEFORE the ``exec "$@"``
    that replaces the shell with Gunicorn, so Gunicorn never inherits
    them in /proc/<pid>/environ.  The ordering matters -- assertions
    check both that the unset exists and that it appears BEFORE the
    final exec.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def entrypoint_text(cls) -> str:
        """Return the full entrypoint.sh source as a string."""
        return ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

    def test_entrypoint_unsets_seed_password(self, entrypoint_text: str) -> None:
        """entrypoint.sh contains an ``unset SEED_USER_PASSWORD`` line."""
        # We accept either a single multi-arg unset or three separate
        # unsets so a future refactor that splits the line for
        # readability does not break the assertion.  The bash ``unset``
        # builtin accepts multiple arguments.
        assert re.search(
            r"\bunset\b[^\n]*\bSEED_USER_PASSWORD\b",
            entrypoint_text,
        ), (
            "entrypoint.sh does not unset SEED_USER_PASSWORD; the seed "
            "credential will be inherited by Gunicorn in /proc/<pid>/environ "
            "(audit finding F-022)"
        )

    def test_entrypoint_unsets_seed_email(self, entrypoint_text: str) -> None:
        """entrypoint.sh also unsets SEED_USER_EMAIL."""
        assert re.search(
            r"\bunset\b[^\n]*\bSEED_USER_EMAIL\b",
            entrypoint_text,
        ), "entrypoint.sh does not unset SEED_USER_EMAIL"

    def test_entrypoint_unset_runs_before_exec(self, entrypoint_text: str) -> None:
        """The unset MUST appear in source order BEFORE ``exec "$@"``.

        If the unset ran AFTER exec, it would never run -- exec
        replaces the shell process with the named binary and any
        following lines are unreachable.
        """
        # Find the byte offsets of (a) the SEED_USER_PASSWORD unset
        # and (b) the final ``exec "$@"`` that hands off to gunicorn.
        unset_match = re.search(
            r"\bunset\b[^\n]*\bSEED_USER_PASSWORD\b",
            entrypoint_text,
        )
        exec_match = re.search(r'^exec\s+"\$@"', entrypoint_text, re.MULTILINE)
        assert unset_match is not None, "no SEED_USER_PASSWORD unset found"
        assert exec_match is not None, (
            "no ``exec \"$@\"`` line in entrypoint.sh; the script "
            "should hand off to the Dockerfile CMD via exec"
        )
        assert unset_match.start() < exec_match.start(), (
            "SEED_USER_PASSWORD unset appears AFTER ``exec \"$@\"`` in "
            "entrypoint.sh; lines after exec never run, so the unset "
            "is dead code (audit finding F-022 regression)"
        )

    def test_entrypoint_uses_seed_sentinel(self, entrypoint_text: str) -> None:
        """entrypoint.sh checks for the seed-complete sentinel."""
        # The exact filename is documented in the script's comment
        # block and exposed as a SEED_SENTINEL shell variable.
        assert "SEED_SENTINEL=" in entrypoint_text, (
            "entrypoint.sh does not declare the SEED_SENTINEL variable; "
            "the noise-reduction sentinel from Commit C-34 is missing"
        )
        assert "/home/shekel/app/state" in entrypoint_text, (
            "entrypoint.sh does not reference the /home/shekel/app/state "
            "writable mount that the seed sentinel lives under"
        )
        assert re.search(
            r"\bif\s*\[\s*-f\s+\"\$\{SEED_SENTINEL\}\"\s*\]",
            entrypoint_text,
        ), (
            "entrypoint.sh does not gate the seed step on the sentinel "
            "file's existence; subsequent restarts will re-run the "
            "(idempotent but noisy) seed_user.py invocation"
        )

    def test_entrypoint_sentinel_written_after_seed_success(
        self, entrypoint_text: str,
    ) -> None:
        """The sentinel is materialised AFTER the seed script returns.

        The ordering invariant: ``: > "${SEED_SENTINEL}"`` (or
        ``touch``) must appear in the same conditional branch as the
        seed_user.py invocation, AFTER it.  ``set -e`` at the top of
        entrypoint.sh aborts the script on a non-zero return from
        seed_user.py, so the sentinel never gets written on failure.
        """
        # Locate the seed_user.py invocation and the sentinel write
        # in source order.
        seed_match = re.search(
            r"python\s+scripts/seed_user\.py", entrypoint_text,
        )
        sentinel_write_match = re.search(
            r"(touch\s+\"\$\{SEED_SENTINEL\}\"|:\s*>\s*\"\$\{SEED_SENTINEL\}\")",
            entrypoint_text,
        )
        assert seed_match is not None, (
            "entrypoint.sh no longer invokes scripts/seed_user.py"
        )
        assert sentinel_write_match is not None, (
            "entrypoint.sh does not write the seed sentinel file; "
            "subsequent restarts cannot skip the seed step"
        )
        assert seed_match.start() < sentinel_write_match.start(), (
            "Seed sentinel is written BEFORE seed_user.py runs; a "
            "failing seed would leave a stale sentinel that masks "
            "the next retry"
        )


class TestSeedUserScriptScrubsEnv:
    """scripts/seed_user.py must scrub SEED_USER_* in os.environ.

    The Python-side scrub is documented in the module docstring and
    implemented as ``_scrub_seed_env_vars``.  Source-level checks
    here catch a regression where the function is removed or the
    ``finally`` block stops calling it.  The behavioural runtime
    test is in tests/test_scripts/test_seed_user.py.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def script_text(cls) -> str:
        """Return the full scripts/seed_user.py source as a string."""
        return SEED_USER_SCRIPT.read_text(encoding="utf-8")

    def test_script_defines_scrub_helper(self, script_text: str) -> None:
        """``_scrub_seed_env_vars`` is defined in the module."""
        assert "def _scrub_seed_env_vars(" in script_text, (
            "scripts/seed_user.py no longer defines _scrub_seed_env_vars; "
            "the F-022 credential-hygiene helper has been removed"
        )

    def test_scrub_helper_pops_password_and_email(self, script_text: str) -> None:
        """The scrub helper targets SEED_USER_PASSWORD and SEED_USER_EMAIL.

        Asserts on the constant tuple at the top of the file rather
        than on the function body so a refactor that promotes the
        list to a public name still passes.
        """
        # Capture the tuple body and parse out the strings.
        match = re.search(
            r"_SEED_SECRET_ENV_VARS[^=]*=\s*\(([^)]+)\)",
            script_text,
        )
        assert match is not None, (
            "scripts/seed_user.py no longer declares _SEED_SECRET_ENV_VARS"
        )
        body = match.group(1)
        assert '"SEED_USER_PASSWORD"' in body, (
            "_SEED_SECRET_ENV_VARS is missing SEED_USER_PASSWORD"
        )
        assert '"SEED_USER_EMAIL"' in body, (
            "_SEED_SECRET_ENV_VARS is missing SEED_USER_EMAIL"
        )
        # Defensive: SEED_USER_DISPLAY_NAME MUST NOT be in the scrub
        # list (display name is not a secret; see the docstring).
        assert "SEED_USER_DISPLAY_NAME" not in body, (
            "_SEED_SECRET_ENV_VARS includes SEED_USER_DISPLAY_NAME -- the "
            "display name is not a secret and should be retained"
        )

    def test_main_calls_scrub_in_finally(self, script_text: str) -> None:
        """The ``__main__`` block calls scrub from a ``finally`` clause.

        The ``finally`` is what guarantees the scrub runs even when
        seed_user() raises (e.g. DB unreachable, transaction rolled
        back).  A regression that moves the call out of finally would
        let a failing seed leave the credential in os.environ for
        subsequent code paths to read.
        """
        # Match the main block ending with the ``finally:`` containing
        # the scrub call.  We look for the sequence: try, then a
        # finally with _scrub_seed_env_vars().  Use DOTALL so .
        # crosses newlines.
        match = re.search(
            r"if __name__ == \"__main__\":(?:.+?)try:(?:.+?)finally:"
            r"(?:.+?)_scrub_seed_env_vars\(\)",
            script_text,
            re.DOTALL,
        )
        assert match is not None, (
            "scripts/seed_user.py's __main__ block does not call "
            "_scrub_seed_env_vars from a finally clause; a failing seed "
            "would skip the scrub and leak the credential"
        )


# ──────────────────────────────────────────────────────────────────
# F-022: docker-compose.yml + Dockerfile carry the state volume
# ──────────────────────────────────────────────────────────────────


class TestComposeAppStateVolume:
    """The bundled prod compose declares the seed-state volume.

    Without the volume, the seed sentinel would land on the rootfs
    (which Commit C-35 plans to make read-only) or be lost on every
    container restart.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def compose_text(cls) -> str:
        """Return the full docker-compose.yml source as a string."""
        return DOCKER_COMPOSE.read_text(encoding="utf-8")

    def test_app_service_mounts_state_volume(self, compose_text: str) -> None:
        """``shekel-prod-app-state`` is mounted at /home/shekel/app/state."""
        assert (
            "shekel-prod-app-state:/home/shekel/app/state" in compose_text
        ), (
            "docker-compose.yml does not mount the shekel-prod-app-state "
            "volume; the seed sentinel cannot persist across restarts"
        )

    def test_top_level_volumes_declares_state(self, compose_text: str) -> None:
        """The state volume is declared in the top-level volumes block."""
        # Match the top-level ``volumes:`` block (no indentation) and
        # check that ``shekel-prod-app-state:`` (one level of
        # indentation) appears inside it.  Catches a regression where
        # the mount line is added but the volume itself isn't
        # declared, which would fail compose at first ``up``.
        assert re.search(
            r"^volumes:\n(?:\s+\S.*\n)*\s+shekel-prod-app-state:",
            compose_text,
            re.MULTILINE,
        ), (
            "docker-compose.yml mounts shekel-prod-app-state but does not "
            "declare it in the top-level volumes block"
        )

    def test_dockerfile_creates_state_dir(self) -> None:
        """The Dockerfile pre-creates /home/shekel/app/state with shekel ownership.

        Pre-creation matters because Docker's volume-mount semantics
        copy the underlying image path's contents (and ownership) to
        the volume on first creation.  Without the pre-create + chown,
        the volume would be empty and root-owned -- entrypoint.sh's
        ``touch`` would fail under ``set -e`` because it runs as the
        unprivileged shekel user.
        """
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "/home/shekel/app/state" in text, (
            "Dockerfile does not pre-create /home/shekel/app/state; the "
            "volume mount will be root-owned and entrypoint.sh's "
            "sentinel touch will fail"
        )
        # The state dir is chowned to shekel directly (one line, shared
        # with /var/www/static). Polyglot CI/DF-03 narrowed the prior
        # ``chown -R shekel:shekel /home/shekel/app`` -- which reached the
        # state dir by recursing the whole app tree -- to chown only the
        # two dirs that need it; state still ends up shekel-owned.
        assert "chown shekel:shekel /home/shekel/app/state" in text, (
            "Dockerfile no longer chowns /home/shekel/app/state to shekel; "
            "the state subdirectory will be root-owned and entrypoint.sh's "
            "sentinel touch will fail under set -e"
        )


# ──────────────────────────────────────────────────────────────────
# F-053: REGISTRATION_ENABLED defaults to false in production
# ──────────────────────────────────────────────────────────────────


class TestRegistrationDisabledByDefault:
    """REGISTRATION_ENABLED defaults to false at every production layer.

    Defense-in-depth: docker-compose.yml's interpolation default and
    ProdConfig's getenv default both resolve to false.  Either layer
    in isolation would close the audit finding; together they protect
    against an operator removing one of them by hand.
    """

    def test_docker_compose_default_is_false(self) -> None:
        """docker-compose.yml's REGISTRATION_ENABLED interpolation defaults to false."""
        text = DOCKER_COMPOSE.read_text(encoding="utf-8")
        # Match ``REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-false}``
        # exactly so a regression to ``:-true`` surfaces immediately.
        assert (
            "REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-false}" in text
        ), (
            "docker-compose.yml's REGISTRATION_ENABLED interpolation no "
            "longer defaults to ``false``; a deploy without the env var "
            "set in .env would re-open public registration (audit "
            "finding F-053)"
        )
        # Negative assertion: the OLD ``:-true`` default must not be
        # present.  Catches a regression where a maintainer toggles
        # the default back without re-running the audit.
        assert (
            "REGISTRATION_ENABLED: ${REGISTRATION_ENABLED:-true}" not in text
        ), (
            "docker-compose.yml still references the pre-C-34 "
            "``:-true`` default for REGISTRATION_ENABLED"
        )

    def test_env_example_default_is_false(self) -> None:
        """.env.example documents the false-by-default posture."""
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        # The actual setting line.
        assert re.search(
            r"^REGISTRATION_ENABLED=false\b", text, re.MULTILINE,
        ), (
            ".env.example no longer ships REGISTRATION_ENABLED=false; "
            "developers copying the example to .env will get the wrong "
            "production default"
        )

    def test_prodconfig_defaults_to_false(self) -> None:
        """ProdConfig's REGISTRATION_ENABLED defaults to false even with env unset.

        This is the defense-in-depth layer: if a future commit drops
        the ``${REGISTRATION_ENABLED:-false}`` from docker-compose.yml,
        ProdConfig still resolves to False.
        """
        text = APP_CONFIG.read_text(encoding="utf-8")
        # Find the ProdConfig class block and check it carries an
        # explicit override.  We look for the class header + body and
        # then the ``REGISTRATION_ENABLED`` assignment with a
        # ``"false"`` default inside it.
        match = re.search(
            r"class ProdConfig\(BaseConfig\):(.+?)(?=\nclass |\Z)",
            text,
            re.DOTALL,
        )
        assert match is not None, "ProdConfig class not found in app/config.py"
        prod_body = match.group(1)
        assert re.search(
            r"REGISTRATION_ENABLED\s*=\s*os\.getenv\(\s*\n?\s*"
            r"\"REGISTRATION_ENABLED\",\s*\"false\"",
            prod_body,
        ), (
            "ProdConfig does not override REGISTRATION_ENABLED with a "
            "``false`` default; only the docker-compose interpolation "
            "default protects production -- defense-in-depth missing"
        )


# ──────────────────────────────────────────────────────────────────


class TestDockerignoreCoverage:
    """The image build context is trimmed to runtime essentials.

    Asserts on the .dockerignore file contents directly because
    invoking ``docker build`` to inspect the actual context would
    require a Docker daemon and consume non-trivial CI time on every
    suite run.  The file's text-based assertions are sufficient: a
    pattern in .dockerignore is the source of truth for what gets
    excluded, so source-level checks catch every regression that
    matters.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def dockerignore_text(cls) -> str:
        """Return the full .dockerignore source as a string."""
        return DOCKERIGNORE.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def dockerignore_lines(cls, dockerignore_text: str) -> list[str]:
        """Return the non-comment, non-blank lines of .dockerignore."""
        return [
            line.strip()
            for line in dockerignore_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    @pytest.mark.parametrize("pattern", [
        # Dev tooling
        ".claude/",
        ".audit-venv",
        "amortization-fix.patch",
        "diagnostics/",
        "monitoring/",
        "requirements-dev.txt",
        "pytest.ini",
        # Host-side configs not used by the app container
        "deploy/",
        "cloudflared/",
        # Test infrastructure
        "tests/",
        ".pytest_cache",
        "conftest.py",
        # Build context noise
        ".git",
        ".github/",
        ".vscode/",
        # Ship markers (ruling R-BAL359): git reads them, the app never
        # does, so a claim must not change the image.
        "plan/",
        # Secrets
        ".env",
        # Dev-only scripts (host-side or build-time)
        "scripts/audit/",
        "scripts/hooks/",
        "scripts/vendor_google_fonts.py",
        "scripts/backup.sh",
        "scripts/restore.sh",
        "scripts/deploy.sh",
        "scripts/retire_stale_containers.sh",
        # Host-side cert generator for shared-mode Postgres TLS
        # (audit finding F-154 / Commit C-37).  Runs once on the
        # bare-metal host with sudo + openssl; never executed
        # inside the app container.
        "scripts/generate_pg_cert.sh",
    ])
    def test_dockerignore_excludes_pattern(
        self, dockerignore_lines: list[str], pattern: str,
    ) -> None:
        """Every audited dev-only path appears in .dockerignore."""
        assert pattern in dockerignore_lines, (
            f"{pattern} is not excluded by .dockerignore; the pattern "
            "would be copied into the production image and either "
            "(a) leak dev tooling to a future RCE, or (b) inflate the "
            "image size with non-runtime files (audit finding F-113)"
        )

    def test_dockerignore_keeps_env_example(
        self, dockerignore_text: str,
    ) -> None:
        """.env.example is exempted via a re-include line.

        The image carries the documented template so an operator
        running ``docker exec shekel-prod-app cat .env.example`` for
        a forensic comparison sees the intended shape.
        """
        # The ``!`` prefix in .dockerignore re-includes a previously
        # excluded path.  A regression that drops the ``!.env.example``
        # line would silently exclude the example file.
        assert "!.env.example" in dockerignore_text, (
            ".dockerignore no longer re-includes .env.example via ``!`` "
            "prefix; the documented template will not ship in the image"
        )

    @pytest.mark.parametrize("pattern", [
        # Runtime-essential paths the image MUST carry.
        "scripts/init_db.sql",
        "scripts/init_db_role.sql",
        "scripts/init_database.py",
        "scripts/seed_user.py",
        "scripts/seed_ref_tables.py",
        "scripts/audit_cleanup.py",
        "scripts/integrity_check.py",
        # Its check families and their shared types (ruling R-BAL161):
        # the sweep imports every one of them at startup.
        "scripts/_integrity_core.py",
        "scripts/_integrity_referential.py",
        "scripts/_integrity_orphan.py",
        "scripts/_integrity_balance.py",
        "scripts/_integrity_consistency.py",
        "scripts/reset_mfa.py",
        "scripts/rotate_sessions.py",
        "scripts/rotate_totp_key.py",
        "scripts/seed_companion.py",
        # Shared CLI helpers the runtime scripts above import; excluding
        # it would break every docker exec script at import time.
        "scripts/_script_lib.py",
        "alembic.ini",
        "entrypoint.sh",
        "gunicorn.conf.py",
        "run.py",
        "requirements.txt",
    ])
    def test_dockerignore_does_not_exclude_runtime_path(
        self, dockerignore_lines: list[str], pattern: str,
    ) -> None:
        """Runtime-essential files are NOT excluded by .dockerignore.

        Catches a future maintainer broadly excluding ``scripts/`` or
        similar without realising several of those files are needed
        at container start.  See entrypoint.sh and docs/runbook.md
        for the complete list of in-container script invocations.
        """
        # The path is excluded if (a) it appears verbatim in the
        # ignore list, OR (b) any prefix component is excluded.  We
        # check (a) directly; (b) is mostly covered by the negative
        # parametrize list since the only directory exclusions we
        # apply are ``scripts/audit/`` and ``scripts/hooks/`` --
        # neither of which prefixes any runtime-essential path.
        assert pattern not in dockerignore_lines, (
            f"{pattern} is excluded by .dockerignore but is required "
            "at runtime (entrypoint.sh or docker exec invocations)"
        )

    def test_dockerignore_documents_intent(
        self, dockerignore_text: str,
    ) -> None:
        """The file's header comments document what the image keeps and excludes.

        Discoverability matters here: a developer adding a new
        top-level directory needs to know whether to add a corresponding
        ignore line.  The header block lists the runtime essentials
        explicitly so the question answers itself.
        """
        # Look for the inventory of runtime-essential paths in the
        # leading comment block.  This is a soft assertion -- the
        # exact wording can drift -- so we only check that the key
        # markers are present.
        for marker in ("entrypoint.sh", "init_db.sql", "seed_user.py"):
            assert marker in dockerignore_text, (
                f".dockerignore header comment block no longer mentions "
                f"``{marker}``; future maintainers will not know the "
                "runtime-essential file inventory without spelunking "
                "entrypoint.sh"
            )


# ──────────────────────────────────────────────────────────────────
# Integration: docker-compose.yml + entrypoint.sh agree on paths
# ──────────────────────────────────────────────────────────────────


class TestComposeEntrypointAgreement:
    """The state-volume mount path matches the entrypoint sentinel path.

    A drift here would silently break the sentinel (entrypoint.sh
    would write to the rootfs path while the volume sits empty at a
    different mount point).
    """

    def test_state_mount_path_matches_sentinel_path(self) -> None:
        """docker-compose.yml mounts the volume at the path entrypoint.sh writes to."""
        compose_text = DOCKER_COMPOSE.read_text(encoding="utf-8")
        entrypoint_text = ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

        # Extract the mount target from docker-compose.yml.
        mount_match = re.search(
            r"shekel-prod-app-state:([^\s:]+)", compose_text,
        )
        assert mount_match is not None, (
            "docker-compose.yml does not mount shekel-prod-app-state"
        )
        mount_target = mount_match.group(1)

        # Extract the sentinel directory from entrypoint.sh.
        sentinel_dir_match = re.search(
            r'SEED_STATE_DIR="([^"]+)"', entrypoint_text,
        )
        assert sentinel_dir_match is not None, (
            "entrypoint.sh does not declare SEED_STATE_DIR"
        )
        sentinel_dir = sentinel_dir_match.group(1)

        assert mount_target == sentinel_dir, textwrap.dedent(f"""
            Mount path drift between docker-compose.yml and entrypoint.sh:
              docker-compose.yml mounts at:  {mount_target}
              entrypoint.sh writes to:        {sentinel_dir}
            The sentinel will land on the read-only rootfs (or be lost
            on every restart) instead of the persistent volume.
        """).strip()
