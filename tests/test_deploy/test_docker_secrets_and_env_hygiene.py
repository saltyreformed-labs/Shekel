"""Deployment-layer assertions for Commit C-38.

Covers audit findings F-108 (`.env.dev` references nonexistent path),
F-109 (`.env.example` carries a functional dev `POSTGRES_PASSWORD`),
F-112 (DevConfig missing pragma comment about HTTP-localhost cookie
posture), and F-148 (env-only secrets posture migrated to operator-
optional Docker secrets).

Tests fall into two categories:

1. **Filesystem state** -- the files the audit reviewer inspects on
   disk are in the expected shape (.env.dev removed, .env.example
   sanitized, .gitignore covers .env.dev).
2. **Source-level structure** -- the production source files
   (`app/config.py`, `deploy/docker-compose.prod.yml`) carry the
   expected hooks for the file-backed secrets posture.

The entrypoints' secret loader, `_load_secret`, and its behavioural
tests live with the entrypoints, in `test_entrypoints.py`.  The backup
verification script's, `scripts/verify_backup.sh`, read the database
master password until ruling plan#3607 deleted the read: its guards live
in `tests/test_scripts/test_verify_backup.py`.

Style note: this file follows the test_seed_credential_hygiene.py
pattern from Commit C-34 (file-text + behaviour split, fixtures via
Path constants, parametrize for repetitive coverage).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from importlib import reload
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from app import config as config_module
from tests.test_deploy._compose import rendered_text


REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Files under assertion ──────────────────────────────────────────
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_DEV = REPO_ROOT / ".env.dev"
GITIGNORE = REPO_ROOT / ".gitignore"
APP_CONFIG = REPO_ROOT / "app" / "config.py"
DOCKER_COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKER_COMPOSE_PROD = REPO_ROOT / "deploy" / "docker-compose.prod.yml"
RUNBOOK_SECRETS = REPO_ROOT / "docs" / "runbook_secrets.md"

# The sentinel token shipped in .env.example's DATABASE_URL template.
# Mirrors ``_DATABASE_URL_SENTINEL`` in app/config.py; cross-referenced
# here as a string literal so a regression that renames the constant on
# one side without the other surfaces as a test failure (rather than as
# a silent acceptance of the leaked default at runtime).
ENV_EXAMPLE_DB_URL_SENTINEL = "REPLACE-ME-WITH-THE-OWNER-ROLE-PASSWORD"

# Subprocess timeout for ``docker compose config`` invocations.
DOCKER_SUBPROCESS_TIMEOUT_S = 60


def _docker_available() -> bool:
    """Return True when ``docker`` is on PATH and ``docker info`` succeeds."""
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


def _service_secret_names(compose: dict[str, object], service: str) -> set[str]:
    """Return the set of secret names a compose service grants access to.

    Accepts both Compose secret syntaxes because they are
    behaviourally identical for access-grant purposes -- the service
    can read ``/run/secrets/<name>`` either way:

    * short form -- ``secrets: [secret_key, ...]`` (list of str);
    * long form  -- ``secrets: [{source: secret_key, target: ...,
      uid: ..., gid: ..., mode: ...}, ...]`` (list of mappings).

    Commit 4e551f5 moved the prod override to the long form to add
    ``uid``/``gid``/``mode`` fields, which compose ignores for a file
    secret outside Swarm (it warns so on every ``config``, measured
    2026-11-11 on compose 5.7.0), so card plan#2166 removed them: the
    container sees the host file's own owner and mode.  The secret is
    mounted either way; only the YAML spelling differs.  Asserting on
    this parsed name set keeps the deploy tests coupled to behaviour
    rather than to one of two valid spellings
    (testing-standards.md: test behaviour, not implementation).

    Args:
        compose: a parsed docker-compose document (the override file
            on its own, or the merged ``docker compose config``
            output).
        service: the service name to inspect (e.g. ``"app"``).

    Returns:
        The set of top-level secret names ``service`` can read at
        ``/run/secrets/<name>``.  Empty when the service is absent or
        declares no ``secrets:`` block -- the caller asserts on
        membership, so an empty set fails the access-grant check with
        a clear message.
    """
    services = compose.get("services")
    if not isinstance(services, dict):
        return set()
    service_def = services.get(service)
    if not isinstance(service_def, dict):
        return set()
    raw = service_def.get("secrets")
    if raw is None:
        return set()
    names: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            names.add(entry)
        elif isinstance(entry, dict):
            source = entry.get("source")
            if isinstance(source, str):
                names.add(source)
    return names


# ──────────────────────────────────────────────────────────────────
# F-108: .env.dev removed from the branch
# ──────────────────────────────────────────────────────────────────


class TestEnvDevRemoved:
    """``.env.dev`` is gone from the working tree and gitignored.

    The audit verified that ``.env.dev:1`` referenced
    ``FLASK_APP=src/flask_app/app.py`` -- a path that has never existed
    in this repo.  The C-38 fix deletes the file outright and adds a
    ``.gitignore`` entry so an operator who recreates a local
    ``.env.dev`` for any reason cannot accidentally commit it.
    """

    def test_env_dev_file_does_not_exist(self) -> None:
        """``.env.dev`` is absent from the working tree."""
        assert not ENV_DEV.exists(), (
            f".env.dev still present at {ENV_DEV}; the F-108 cleanup did not "
            "complete (the file referenced a nonexistent FLASK_APP path "
            "and is documented for deletion in remediation Commit C-38)."
        )

    def test_gitignore_covers_env_dev(self) -> None:
        """``.gitignore`` lists ``.env.dev`` so a recreated copy stays untracked."""
        text = GITIGNORE.read_text(encoding="utf-8")
        # We require an exact entry on a line by itself rather than a
        # broader glob match -- the audit's ask is the literal
        # ``.env.dev`` filename.  ``re.MULTILINE`` so ``^`` matches the
        # start of any line.
        assert re.search(r"^\.env\.dev\s*$", text, re.MULTILINE), (
            ".gitignore does not list ``.env.dev`` on its own line; "
            "an operator recreating a local .env.dev (e.g. for a test "
            "Flask entrypoint) could accidentally commit it (F-108)."
        )


# ──────────────────────────────────────────────────────────────────
# F-109: .env.example sanitized of the functional dev password
# ──────────────────────────────────────────────────────────────────


class TestEnvExampleSanitized:
    """``.env.example`` ships an empty ``POSTGRES_PASSWORD`` placeholder."""

    @pytest.fixture(scope="class")
    @classmethod
    def env_example_text(cls) -> str:
        """Return the full .env.example source as a string."""
        return ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_postgres_password_field_is_empty(self, env_example_text: str) -> None:
        """``POSTGRES_PASSWORD=`` is empty by default in .env.example.

        Asserts on a line-anchored exact match.  A regression to
        ``POSTGRES_PASSWORD=shekel_pass`` (or any other non-empty
        functional default) would re-introduce the F-109 finding: a
        copy-paste of .env.example would carry a working dev password
        into a production environment.  The base
        ``docker-compose.yml`` then reuses that password in
        DATABASE_URL, so the audit treats it as a leaked credential
        even though the file is committed as a template.
        """
        # Match the exact assignment with nothing after the ``=``
        # except optional whitespace.  This catches both the original
        # ``POSTGRES_PASSWORD=shekel_pass`` regression AND a sneakier
        # ``POSTGRES_PASSWORD= `` (trailing space) form that bash
        # would still treat as empty but a copy-paste would not.
        assert re.search(
            r"^POSTGRES_PASSWORD=\s*$",
            env_example_text,
            re.MULTILINE,
        ), (
            ".env.example does not ship POSTGRES_PASSWORD with an "
            "empty value; F-109 regression -- the file would carry a "
            "functional dev password into production for any operator "
            "who copies it verbatim to .env."
        )

    def test_postgres_password_section_documents_generation(
        self, env_example_text: str,
    ) -> None:
        """The POSTGRES_PASSWORD section explains how to generate a value.

        Without an inline generation command the operator falls back
        to whatever they had in their previous .env, which on a fresh
        checkout means they invent a weak default (``password123``).
        The C-34 SECRET_KEY block established the precedent that
        every required secret in this file ships its own generation
        command; this test enforces that POSTGRES_PASSWORD now
        follows the same pattern.
        """
        # Find the POSTGRES_PASSWORD assignment and read backwards
        # through the comment block above it.  Comment lines are
        # ``#`` prefixed and immediately precede the assignment.
        match = re.search(
            r"((?:^#[^\n]*\n)+)^POSTGRES_PASSWORD=\s*$",
            env_example_text,
            re.MULTILINE,
        )
        assert match is not None, (
            ".env.example POSTGRES_PASSWORD assignment is not preceded "
            "by a comment block; the operator has no inline guidance "
            "for generating a value."
        )
        comment_block = match.group(1)
        # The recommended generation commands in the docs/runbook are
        # ``secrets.token_urlsafe(32)`` and ``openssl rand``.  We
        # accept either being mentioned -- the assertion is that SOME
        # generation hint exists, not which.
        assert "token_urlsafe" in comment_block or "openssl rand" in comment_block, (
            ".env.example POSTGRES_PASSWORD comment block lists no "
            "generation command; an operator copying the template "
            "would fall back to inventing a weak default."
        )

    def test_postgres_password_block_references_docker_secrets_path(
        self, env_example_text: str,
    ) -> None:
        """The POSTGRES_PASSWORD block points operators at Docker secrets.

        Defense-in-depth note: even if an operator forgets to read
        the runbook, the .env.example block tells them about the
        file-backed posture so they can opt in.
        """
        match = re.search(
            r"((?:^#[^\n]*\n)+)^POSTGRES_PASSWORD=\s*$",
            env_example_text,
            re.MULTILINE,
        )
        assert match is not None
        comment_block = match.group(1)
        # The docker-secrets escape hatch must be discoverable from
        # the .env.example comment block -- not just from the runbook.
        assert "docker secret" in comment_block.lower() or (
            "/opt/docker/shekel/secrets" in comment_block
            and "F-148" in comment_block
        ), (
            ".env.example POSTGRES_PASSWORD comment block does not "
            "mention the Docker secrets opt-in (audit finding F-148 / "
            "Commit C-38).  Operators who read only this file will "
            "not discover the file-backed posture."
        )


# ──────────────────────────────────────────────────────────────────
# F-109 (URI form): .env.example DATABASE_URL templates use a sentinel
# ──────────────────────────────────────────────────────────────────


class TestEnvExampleUriTemplatesSanitized:
    """``.env.example`` no longer embeds ``shekel_pass`` in connection URIs.

    The standalone ``POSTGRES_PASSWORD=`` line was sanitised in C-38
    (covered by ``TestEnvExampleSanitized`` above).  The DATABASE_URL
    template that EMBEDS the same password inside a connection URI is
    the second F-109 surface, closed by the C-38 follow-up Issue 3
    work documented in
    ``shekel-audits/security-2026-04-15/c-38-followups.md``.
    ``TEST_DATABASE_URL`` was the other one until ``balance:X-br-4``
    deleted the line entirely; the first test below holds it deleted.

    These tests assert the URI lines never contain the historical
    ``shekel_pass`` default and always contain the sentinel token
    that ``app/config.py:_reject_sentinel`` rejects at load time.
    The two together form a closed feedback loop: an operator who
    edits the URI in .env to a real password keeps the application
    running; an operator who copies .env.example verbatim is caught
    at config load time with a clear app-level error.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def env_example_text(cls) -> str:
        """Return the full .env.example source as a string."""
        return ENV_EXAMPLE.read_text(encoding="utf-8")

    def test_no_test_database_url_template_exists(
        self, env_example_text: str,
    ) -> None:
        """``TEST_DATABASE_URL`` is absent, which is stronger than sanitised.

        It used to be the second URI template swept below, pointing at the
        long-lived ``shekel-dev-test-db`` container on port 5433.
        ``balance:X-br-4`` deleted that container: ``./scripts/test.sh`` starts
        a throwaway cluster per run and EXPORTS this variable at its unix
        socket, so a value in ``.env`` would be overwritten on every run and
        could only mislead.  A template that does not exist cannot be copied
        unsubstituted, so this asserts the absence rather than the sentinel --
        and it fails if anyone reintroduces a test DSN here, which is the
        regression worth catching.
        """
        match = re.search(
            r"^TEST_DATABASE_URL=([^\n]*)$",
            env_example_text,
            re.MULTILINE,
        )

        assert match is None, (
            ".env.example defines TEST_DATABASE_URL again, as "
            f"{match.group(1)!r}. The test runner exports this variable "
            "itself, naming the private cluster's unix socket, so a value "
            "here is dead at best and a credential template nobody sweeps "
            "at worst. Delete the line."
        )

    @pytest.mark.parametrize(
        "var_name",
        ["DATABASE_URL"],
    )
    def test_uri_template_does_not_embed_shekel_pass(
        self, env_example_text: str, var_name: str,
    ) -> None:
        """The URI line for ``var_name`` does not contain ``shekel_pass``.

        Anchored on the start-of-line ``<var_name>=`` assignment so
        the assertion targets the URI template specifically and does
        not also reject the historical reference in the comment
        block above (audit trail context is acceptable in comments;
        a functional credential in the assignment is not).
        """
        # Match the assignment line up to end-of-line.  The pattern
        # then asserts ``shekel_pass`` is NOT present anywhere in
        # the captured assignment.
        match = re.search(
            rf"^{var_name}=([^\n]*)$",
            env_example_text,
            re.MULTILINE,
        )
        assert match is not None, (
            f".env.example does not define ``{var_name}=`` on a line "
            "by itself; the template URI documenting password-auth is "
            "missing entirely.  An operator with no example to copy "
            "would invent one and likely embed a weak default."
        )
        assignment = match.group(1)
        assert "shekel_pass" not in assignment, (
            f".env.example ``{var_name}`` URI still embeds "
            "``shekel_pass`` (audit finding F-109 / Commit C-38 "
            "follow-up Issue 3 regression).  The URI must use the "
            f"sentinel token ``{ENV_EXAMPLE_DB_URL_SENTINEL}`` so "
            "app/config.py:_reject_sentinel fires when an operator "
            "copies the template without substituting a real password."
        )

    @pytest.mark.parametrize(
        "var_name",
        ["DATABASE_URL"],
    )
    def test_uri_template_carries_sentinel(
        self, env_example_text: str, var_name: str,
    ) -> None:
        """The URI line for ``var_name`` carries the sentinel token.

        Without the sentinel, the C-38 follow-up rejection helper
        has nothing to match against -- an operator who copies the
        template would silently authenticate against whatever
        password they happen to have in their dev database.  The
        sentinel is the load-bearing piece that makes the
        unsubstituted-template failure mode loud rather than silent.
        """
        match = re.search(
            rf"^{var_name}=([^\n]*)$",
            env_example_text,
            re.MULTILINE,
        )
        assert match is not None
        assignment = match.group(1)
        assert ENV_EXAMPLE_DB_URL_SENTINEL in assignment, (
            f".env.example ``{var_name}`` URI does not embed the "
            f"sentinel token ``{ENV_EXAMPLE_DB_URL_SENTINEL}``; "
            "app/config.py:_reject_sentinel cannot enforce the "
            "copy-paste-without-substitution failure mode the C-38 "
            "follow-up Issue 3 work introduces."
        )

    def test_database_url_logs_in_as_the_owner(
        self, env_example_text: str,
    ) -> None:
        """A host run logs in as ``shekel_owner``, the deploy's login (ruling plan#3608).

        ``flask db upgrade`` and the host scripts connect by this URL.  A
        migration run as any other login makes tables that login owns, and
        the next deploy, as the owner, cannot alter them until the
        provisioning script runs again.  The URL names the dev database the
        dev stack publishes, ``localhost:5432/shekel``.
        """
        match = re.search(r"^DATABASE_URL=([^\n]*)$", env_example_text, re.MULTILINE)
        assert match is not None
        url = urlsplit(match.group(1))
        assert (url.scheme, url.username, url.hostname, url.port, url.path) == (
            "postgresql", "shekel_owner", "localhost", 5432, "/shekel",
        ), match.group(1)
        assert url.password == ENV_EXAMPLE_DB_URL_SENTINEL, match.group(1)

    def test_database_section_documents_substitution(
        self, env_example_text: str,
    ) -> None:
        """Operator is told to replace the sentinel before booting.

        The audit-trail comment block above the DATABASE_URL block
        must explicitly call out the substitution -- otherwise an
        operator who skims the file may not realise the URI is a
        template requiring an edit.  We accept any reasonable hint
        (``REPLACE``, ``substitute``, etc.) tied to the sentinel
        token spelling, so a future copyedit that rewords the
        guidance does not break the assertion.
        """
        # Capture the comment block (one or more ``#``-prefixed
        # lines) that precedes the DATABASE_URL assignment.
        match = re.search(
            r"((?:^#[^\n]*\n)+)^DATABASE_URL=",
            env_example_text,
            re.MULTILINE,
        )
        assert match is not None, (
            ".env.example DATABASE_URL assignment is not preceded by "
            "an explanatory comment block; the operator has no inline "
            "context for the sentinel substitution requirement."
        )
        comment_block = match.group(1)
        # The sentinel must be named in the comment so the operator
        # can map their .env's literal value to the documented one.
        assert ENV_EXAMPLE_DB_URL_SENTINEL in comment_block, (
            ".env.example DATABASE_URL comment block does not name "
            f"the sentinel token ``{ENV_EXAMPLE_DB_URL_SENTINEL}``; "
            "an operator who hits the _reject_sentinel ValueError "
            "would have to grep the codebase to discover what the "
            "token means."
        )


# ──────────────────────────────────────────────────────────────────
# F-112: DevConfig pragma comment for HTTP-localhost cookie posture
# ──────────────────────────────────────────────────────────────────


class TestProdConfigRejectsDockerSecretPlaceholder:
    """ProdConfig rejects ``replaced_by_docker_secret`` as a SECRET_KEY value.

    Defense-in-depth complement to the entrypoint.sh case statement:
    the entrypoint catches the placeholder before the server starts;
    this Python-side check catches it again at Flask config
    load time.  Both fire at different layers of the boot sequence so
    a regression in either one is caught by the other.
    """

    @pytest.mark.parametrize(
        "placeholder_value",
        [
            # Exact match -- the placeholder set membership branch.
            "replaced_by_docker_secret",
            # Long-enough padding to clear the 32-char length floor;
            # the ``startswith`` branch is what catches it.
            "replaced_by_docker_secret_padded_to_be_long_enough_to_pass_length_check",
        ],
        ids=["exact-match", "padded-long"],
    )
    def test_prodconfig_rejects_docker_secret_placeholder(
        self, placeholder_value: str, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ProdConfig refuses to load when SECRET_KEY is the documented placeholder.

        Both the exact placeholder and longer ``startswith`` variants
        must surface the ``known placeholder`` error.  Without these
        rejections, an operator on Posture 2 whose docker secret file
        is missing or unreadable would boot under the publicly-known
        placeholder value -- exactly the F-148 weakness Commit C-38
        is closing.
        """
        # ProdConfig reads SECRET_KEY at class-attribute time
        # (BaseConfig.SECRET_KEY = os.getenv(...)) so we must set the
        # env var BEFORE the module reload.  Use monkeypatch so any
        # exception in the test body still rolls the env back.
        monkeypatch.setenv("SECRET_KEY", placeholder_value)
        monkeypatch.setenv("DATABASE_URL", "postgresql://x:x@x:5432/x")
        try:
            reload(config_module)
            with pytest.raises(ValueError, match="known placeholder"):
                config_module.ProdConfig()
        finally:
            # Reload once more after the test so the next test that
            # touches config_module sees the original env-driven
            # class attributes.
            reload(config_module)


class TestDevConfigPragma:
    """``DevConfig`` carries an explicit pragma for its cookie posture.

    The audit-verified gap: DevConfig inherits Flask's defaults for
    ``SESSION_COOKIE_SECURE`` (False), ``SESSION_COOKIE_SAMESITE``
    (None), and ``SESSION_COOKIE_HTTPONLY`` (True), but does not
    document why it diverges from ProdConfig's hardened defaults.  An
    operator skim-reading config.py would see DevConfig as a hole.
    The C-38 pragma makes the rationale explicit so a future reader
    -- or a future audit -- understands the divergence is deliberate.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def config_text(cls) -> str:
        """Return the full app/config.py source as a string."""
        return APP_CONFIG.read_text(encoding="utf-8")

    def test_devconfig_class_has_substantive_docstring(
        self, config_text: str,
    ) -> None:
        """DevConfig's docstring covers the cookie-flag pragma.

        Match the DevConfig class header and isolate its body before
        the next class or end-of-file.  The docstring must mention
        the specific cookie flags that are NOT set, not just a
        generic "dev mode" line.
        """
        # Capture from "class DevConfig" up to the next "class " or
        # end of file.  ``re.DOTALL`` lets ``.`` cross newlines.
        match = re.search(
            r"class DevConfig\(BaseConfig\):(.+?)(?=\nclass |\Z)",
            config_text,
            re.DOTALL,
        )
        assert match is not None, "DevConfig class not found in app/config.py"
        body = match.group(1)
        # Must mention the specific finding ID so a future audit
        # reviewer can match the pragma to the audit catalog.
        assert "F-112" in body, (
            "DevConfig docstring does not reference audit finding F-112; "
            "future audit reviewers cannot match the pragma to the "
            "audit catalog."
        )
        # Must mention the load-bearing cookie flag the pragma is
        # explaining.  ``SESSION_COOKIE_SECURE`` is the flag that
        # would silently break dev if DevConfig adopted ProdConfig's
        # posture, so it is the one that matters most.
        assert "SESSION_COOKIE_SECURE" in body, (
            "DevConfig docstring does not mention SESSION_COOKIE_SECURE; "
            "the pragma is supposed to explain why dev does NOT set "
            "this flag (Flask's default is False; ProdConfig sets True)."
        )


# ──────────────────────────────────────────────────────────────────
# F-148: deploy/docker-compose.prod.yml secrets block
# ──────────────────────────────────────────────────────────────────


class TestProdComposeSecretsBlock:
    """The shared-mode prod override declares Docker secrets and grants access.

    Source-text checks complement the ``docker compose config``
    integration test below: text checks catch a misshapen secret
    declaration immediately, while the integration check catches
    structural bugs that pass the text scan but fail compose's
    schema validation.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def compose_text(cls) -> str:
        """Return the full deploy/docker-compose.prod.yml source as a string."""
        return DOCKER_COMPOSE_PROD.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    @classmethod
    def prod_compose(cls) -> dict[str, object]:
        """Return deploy/docker-compose.prod.yml parsed into a dict.

        The access-grant assertions parse the document rather than
        regex the raw text so they accept both the short
        (``- secret_key``) and long (``- source: secret_key``) Compose
        secret syntaxes -- see ``_service_secret_names``.  The
        ``${VAR:?msg}`` interpolation tokens in the override are inert
        plain scalars to the YAML parser; the secrets structure under
        assertion does not depend on them being resolved.
        """
        return yaml.safe_load(DOCKER_COMPOSE_PROD.read_text(encoding="utf-8"))

    @pytest.mark.parametrize(
        "secret_name",
        [
            "secret_key",
            "postgres_password",
            "app_role_password",
            "owner_role_password",
            "totp_encryption_key",
            "valkey_password",
        ],
    )
    def test_top_level_secrets_block_declares_secret(
        self, compose_text: str, secret_name: str,
    ) -> None:
        """Each required secret has a top-level declaration with a file path."""
        # Match the YAML block: ``<name>:`` followed by an indented
        # ``file: /opt/docker/shekel/secrets/<name>`` line.
        pattern = (
            rf"^  {secret_name}:\n"
            rf"\s+file:\s+/opt/docker/shekel/secrets/{secret_name}\b"
        )
        assert re.search(pattern, compose_text, re.MULTILINE), (
            f"deploy/docker-compose.prod.yml does not declare the "
            f"``{secret_name}`` secret with a file path under "
            f"/opt/docker/shekel/secrets/.  The runbook documents "
            "this exact path; a drift would silently break operator "
            "pre-flight commands."
        )

    @pytest.mark.parametrize(
        "secret_name",
        [
            "secret_key",
            "app_role_password",
            "totp_encryption_key",
            "valkey_password",
        ],
    )
    def test_app_service_grants_access_to_secret(
        self, prod_compose: dict[str, object], secret_name: str,
    ) -> None:
        """The app service's ``secrets:`` list grants access to each of its own secrets.

        Its own and no other: which secrets each service may NOT be given is
        ``test_compose_layout.py``'s (rulings plan#3412, plan#3413).
        """
        granted = _service_secret_names(prod_compose, "app")
        assert secret_name in granted, (
            f"app service in deploy/docker-compose.prod.yml does not "
            f"reference ``{secret_name}`` in its ``secrets:`` list; "
            "the secret will be declared at the top level but never "
            f"mounted into /run/secrets/ for the container.  The app "
            f"service grants access to: {sorted(granted)}."
        )

    def test_db_service_grants_access_to_postgres_password(
        self, prod_compose: dict[str, object],
    ) -> None:
        """The db service mounts the postgres_password secret natively."""
        granted = _service_secret_names(prod_compose, "db")
        assert "postgres_password" in granted, (
            "db service does not mount the ``postgres_password`` "
            "docker secret; the postgres image cannot read /run/"
            "secrets/postgres_password and the POSTGRES_PASSWORD_FILE "
            f"handoff will fail at container start.  The db service "
            f"grants access to: {sorted(granted)}."
        )

    def test_db_service_uses_postgres_password_file(
        self, compose_text: str,
    ) -> None:
        """``POSTGRES_PASSWORD_FILE`` is set in the db environment.

        The postgres image's docker-entrypoint.sh reads this env var
        natively to populate the superuser password from a file.
        """
        db_match = re.search(
            r"\n  db:\n(.+?)(?=\n  \w|\nnetworks:|\nsecrets:|\Z)",
            compose_text,
            re.DOTALL,
        )
        assert db_match is not None, "db service block missing"
        db_body = db_match.group(1)
        assert re.search(
            r"POSTGRES_PASSWORD_FILE:\s+/run/secrets/postgres_password",
            db_body,
        ), (
            "db service environment does not set "
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password; "
            "the postgres image will not read the docker secret."
        )

    def test_db_service_overrides_postgres_password_to_empty(
        self, compose_text: str,
    ) -> None:
        """``POSTGRES_PASSWORD: ""`` is required for the postgres image's mutex.

        The postgres image's ``file_env`` helper errors when both
        POSTGRES_PASSWORD and POSTGRES_PASSWORD_FILE are non-empty
        (the "both set are exclusive" check).  Setting
        POSTGRES_PASSWORD to the empty string lets the file-backed
        branch run.
        """
        db_match = re.search(
            r"\n  db:\n(.+?)(?=\n  \w|\nnetworks:|\nsecrets:|\Z)",
            compose_text,
            re.DOTALL,
        )
        assert db_match is not None, "db service block missing"
        db_body = db_match.group(1)
        # Match either ``POSTGRES_PASSWORD: ""`` or
        # ``POSTGRES_PASSWORD: ''`` -- both are valid YAML for an
        # empty string.  The pattern is built explicitly via raw
        # strings so the pylint implicit-str-concat warning does not
        # fire on adjacent string literals.
        empty_yaml_pattern = r'POSTGRES_PASSWORD:\s+(""|' + "''" + r')'
        assert re.search(empty_yaml_pattern, db_body), (
            "db service environment does not override the base file's "
            "POSTGRES_PASSWORD interpolation to an empty string; the "
            "postgres image's file_env helper will see both env vars "
            "as non-empty and error with ``both ... are exclusive``."
        )


# ──────────────────────────────────────────────────────────────────
# F-148: docker compose merged config integration check
# ──────────────────────────────────────────────────────────────────


@pytest.mark.docker
class TestProdComposeMergedConfig:
    """``docker compose config`` parses the secrets block successfully.

    Skipped on hosts without docker.  Catches a structural break in
    the secrets block that the source-text tests above might miss
    (e.g. a YAML indentation error that slips past the regex but
    fails the schema validator).
    """

    def test_docker_compose_config_succeeds_with_secrets_block(self) -> None:
        """The merged config parses and includes every declared secret."""
        if not _docker_available():
            pytest.skip("docker not available; cannot run docker compose config")

        merged = rendered_text(DOCKER_COMPOSE, DOCKER_COMPOSE_PROD)

        # Every required secret appears in the merged top-level
        # secrets: block.  Compose annotates each secret with a
        # ``name: shekel-prod_<name>`` line; we match on the file:
        # line which must reference /opt/docker/shekel/secrets/.
        for secret in (
            "secret_key",
            "postgres_password",
            "app_role_password",
            "owner_role_password",
            "totp_encryption_key",
        ):
            expected = f"file: /opt/docker/shekel/secrets/{secret}"
            assert expected in merged, (
                f"merged compose config does not include the {secret} "
                f"secret with file path /opt/docker/shekel/secrets/"
                f"{secret}; the shared-mode prod stack would not mount "
                "the file at /run/secrets/<name> for the container."
            )

        # The db service's POSTGRES_PASSWORD must be the empty
        # string in the merged config -- the override step that
        # bypasses the postgres image's mutex check.
        assert 'POSTGRES_PASSWORD: ""' in merged, (
            "merged compose config does not show POSTGRES_PASSWORD as "
            'an empty string ("") on the db service; the postgres '
            "image will see both POSTGRES_PASSWORD and "
            "POSTGRES_PASSWORD_FILE as non-empty and error at start."
        )

        # Every required secret must be wired into the app service so
        # the entrypoint can read ``/run/secrets/<name>``.  Compose's
        # rendered long form is ``- source: <name>\n  target:
        # <name>``: ``target`` is the in-container *filename* (Docker
        # mounts it at /run/secrets/<target> at runtime), so ``docker
        # compose config`` never emits an absolute ``/run/secrets/``
        # path -- asserting on that prefix is wrong for either secret
        # syntax.  Parse the merged document and assert on the source-
        # name set, which is robust to the short/long split and to
        # the rendering detail.
        merged_doc = yaml.safe_load(merged)
        app_secrets = _service_secret_names(merged_doc, "app")
        for secret in (
            "secret_key",
            "app_role_password",
            "totp_encryption_key",
        ):
            assert secret in app_secrets, (
                f"merged compose config does not wire {secret} into "
                "the app service's ``secrets:`` list; the "
                "entrypoint.sh _load_secret helper will not find the "
                f"file at /run/secrets/{secret}.  The app service "
                f"grants access to: {sorted(app_secrets)}."
            )


# ──────────────────────────────────────────────────────────────────
# Documentation -- the runbook covers the docker-secrets posture
# ──────────────────────────────────────────────────────────────────


class TestRunbookDocumentsDockerSecretsPosture:
    """``docs/runbook_secrets.md`` covers the file-backed posture.

    Operator-documentation check: the runbook is the source of truth
    for migration and rotation procedures.  A code change that adds
    docker-secrets support without runbook coverage leaves operators
    with no procedure for the new posture.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def runbook_text(cls) -> str:
        """Return the full docs/runbook_secrets.md source as a string."""
        return RUNBOOK_SECRETS.read_text(encoding="utf-8")

    def test_runbook_describes_two_postures(self, runbook_text: str) -> None:
        """The runbook explicitly contrasts env-backed and file-backed postures."""
        assert "Posture 1" in runbook_text and "Posture 2" in runbook_text, (
            "runbook_secrets.md does not split the two postures into "
            "named sections; operators cannot easily decide which "
            "posture they are running."
        )
        assert "env-backed" in runbook_text.lower(), (
            "runbook does not name the env-backed posture explicitly"
        )
        assert "file-backed" in runbook_text.lower(), (
            "runbook does not name the file-backed posture explicitly"
        )

    def test_runbook_documents_migration_procedure(
        self, runbook_text: str,
    ) -> None:
        """A concrete migration procedure appears under a section header."""
        assert "Migrating to Docker secrets" in runbook_text, (
            "runbook does not include a ``Migrating to Docker secrets`` "
            "section; operators have no documented step-by-step path "
            "from env-backed to file-backed."
        )
        # The procedure must reference the canonical secrets
        # directory path.  A drift between this path and the compose
        # file's secret declarations would silently break operators
        # following the runbook.
        assert "/opt/docker/shekel/secrets" in runbook_text, (
            "runbook migration procedure does not reference "
            "/opt/docker/shekel/secrets/; operators following the "
            "runbook would not know where to put the secret files."
        )

    def test_runbook_secret_inventory_lists_app_role_password(
        self, runbook_text: str,
    ) -> None:
        """The Secret Inventory table includes APP_ROLE_PASSWORD.

        Pre-C-38 the inventory listed three secrets; C-13 added
        APP_ROLE_PASSWORD without updating the inventory.  C-38
        closes that gap.  A regression that drops the row would
        re-create the documentation hole the audit surfaced.
        """
        # Find the inventory table -- a heading-3 ``## Secret
        # Inventory`` followed by a markdown table.  The table rows
        # have ``| `VAR` |`` shape.
        inventory_match = re.search(
            r"## Secret Inventory(.+?)## ", runbook_text, re.DOTALL,
        )
        assert inventory_match is not None, "Secret Inventory section missing"
        table_body = inventory_match.group(1)
        assert "`APP_ROLE_PASSWORD`" in table_body, (
            "Secret Inventory table does not list APP_ROLE_PASSWORD; "
            "the C-13 secret is undocumented in the inventory and "
            "operators rebuilding from disaster would miss it."
        )
