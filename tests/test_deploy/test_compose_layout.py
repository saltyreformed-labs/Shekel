"""Each compose service holds its own credentials and no other, and the deploy runs in the
one-shot migrate service before the web server starts (card plan#2166; rulings plan#3412,
plan#3413).

Read in each way the files run: ``docker-compose.yml`` alone (the bundled quick start), it with
``deploy/docker-compose.prod.yml`` (shared-mode production) and ``docker-compose.dev.yml`` (the
dev stack), each as ``docker compose config`` renders it, and the two files a render does not
need an override for also as parsed, so a run that deselects the ``docker`` tests still grades
them.  A service "holds" a credential when its environment names the variable, whatever its
value (a blank or a placeholder included), or a secret file of it is mounted: the test is what
the service is GIVEN, so a name that should not be there fails even while empty.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from tests.test_deploy._compose import (BASE_COMPOSE, DEV_COMPOSE, PROD_OVERRIDE, REPO_ROOT,
                                        rendered)

ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: Each credential, as the variables that carry it and the secret files that hold it.
_SUPERUSER = "the superuser's password"
_OWNER = "the database owner's password"
_WEB_LOGIN = "the web login's password"
_SECRET_KEY = "the session secret key"
_TOTP = "the TOTP keys"
_STORE = "the rate-limit store's password"
_SEED = "the first user's seed"
_URL = "a database URL or login, which each entrypoint builds instead"
CREDENTIALS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    _SUPERUSER: (frozenset({"POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE"}),
                 frozenset({"postgres_password"})),
    _OWNER: (frozenset({"OWNER_ROLE_PASSWORD"}), frozenset({"owner_role_password"})),
    _WEB_LOGIN: (frozenset({"APP_ROLE_PASSWORD"}), frozenset({"app_role_password"})),
    _SECRET_KEY: (frozenset({"SECRET_KEY"}), frozenset({"secret_key"})),
    _TOTP: (frozenset({"TOTP_ENCRYPTION_KEY", "TOTP_ENCRYPTION_KEY_OLD"}),
            frozenset({"totp_encryption_key", "totp_encryption_key_old"})),
    _STORE: (frozenset({"VALKEY_PASSWORD"}), frozenset({"valkey_password"})),
    _SEED: (frozenset({"SEED_USER_EMAIL", "SEED_USER_PASSWORD", "SEED_USER_DISPLAY_NAME",
                       "SEED_USER_LAST_PAYDAY", "SEED_USER_CADENCE_DAYS",
                       "SEED_USER_NUM_PERIODS"}), frozenset()),
    _URL: (frozenset({"DATABASE_URL", "DATABASE_URL_APP", "DB_USER", "DB_PASSWORD",
                      "PGPASSWORD"}), frozenset()),
}
#: Which services hold each credential, in every way the files run.
HOLDERS: dict[str, frozenset[str]] = {
    _SUPERUSER: frozenset({"db"}),
    _OWNER: frozenset({"db", "migrate"}),
    _WEB_LOGIN: frozenset({"db", "app"}),
    _SECRET_KEY: frozenset({"app"}),
    _TOTP: frozenset({"app"}),
    _STORE: frozenset({"app", "valkey"}),
    _SEED: frozenset({"migrate"}),
    _URL: frozenset(),
}

#: Where the image holds the checkout (the Dockerfile's WORKDIR and ``COPY . .``).
_IMAGE_APP_DIR = "/home/shekel/app"
#: The migrate service's entrypoint and command, as the image holds them.
_ENTRYPOINT = [f"{_IMAGE_APP_DIR}/scripts/owner_entrypoint.sh"]
_COMMAND = [f"{_IMAGE_APP_DIR}/scripts/deploy.sh"]
#: Where the db image runs a first-boot hook from (``provision_roles.sh`` says why this name).
_HOOK = "/docker-entrypoint-initdb.d/10-provision-roles.sh"
_PROVISION = REPO_ROOT / "deploy" / "postgres" / "provision_roles.sh"
_OWNER_ENTRYPOINT = REPO_ROOT / "scripts" / "owner_entrypoint.sh"
_DEPLOY = REPO_ROOT / "scripts" / "deploy.sh"
#: The state volume, and where scripts/deploy.sh writes the seed sentinel under it.
_STATE_VOLUME = "shekel-prod-app-state"


def _parsed(path: Path) -> dict[str, object]:
    """Return ``path`` as parsed, its anchors resolved and nothing interpolated."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


#: Each way the files run: name -> (how to read it, the stack it is).  A render runs
#: ``docker compose``, so its cases carry the ``docker`` mark.
_STACKS = {
    "base file": (lambda: _parsed(BASE_COMPOSE), "bundled"),
    "dev file": (lambda: _parsed(DEV_COMPOSE), "dev"),
    "base render": (lambda: rendered(BASE_COMPOSE), "bundled"),
    "production render": (lambda: rendered(BASE_COMPOSE, PROD_OVERRIDE), "production"),
    "dev render": (lambda: rendered(DEV_COMPOSE), "dev"),
}


def _stack_params(*kinds: str) -> list:
    """The ``_STACKS`` of ``kinds`` as fixture params, each render marked ``docker``."""
    return [pytest.param(name, id=name,
                         marks=[pytest.mark.docker] if name.endswith("render") else [])
            for name, (_read, kind) in _STACKS.items() if kind in kinds]


@pytest.fixture(name="stack", scope="module",
                params=_stack_params("bundled", "production", "dev"))
def _stack(request: pytest.FixtureRequest) -> tuple[str, dict[str, object]]:
    """Each way the files run: its kind and its document."""
    read, kind = _STACKS[request.param]
    return kind, read()


@pytest.fixture(name="image_stack", scope="module",
                params=_stack_params("bundled", "production"))
def _image_stack(request: pytest.FixtureRequest) -> dict[str, object]:
    """Each way the files run the published image (all but the dev stack, which builds it)."""
    return _STACKS[request.param][0]()


def _secrets(spec: dict[str, object]) -> dict[str, str]:
    """A service's mounted secrets, source to target, from either compose spelling."""
    mounted: dict[str, str] = {}
    for entry in spec.get("secrets") or []:
        if isinstance(entry, str):
            mounted[entry] = entry
        else:
            mounted[entry["source"]] = entry.get("target", entry["source"])
    return mounted


def _holders(document: dict[str, object]) -> dict[str, frozenset[str]]:
    """Which services hold each credential in ``document`` (the module docstring's "hold")."""
    services = document["services"]
    return {credential: frozenset(
        name for name, spec in services.items()
        if names & set(spec.get("environment") or {}) or files & set(_secrets(spec)))
        for credential, (names, files) in CREDENTIALS.items()}


def _reads(script: Path, helper: str) -> dict[str, str]:
    """The secrets ``script`` reads with ``helper`` (``helper VAR file``): VAR to file name."""
    return dict(re.findall(rf"^{helper} (\w+) (\w+)$", script.read_text(encoding="utf-8"),
                           re.MULTILINE))


class TestEachServiceHoldsItsOwnCredentials:
    """No service is given a credential it does not use (rulings plan#3412, plan#3413)."""

    def test_each_credential_is_held_by_its_services_alone(
        self, stack: tuple[str, dict[str, object]],
    ) -> None:
        """Each credential is held by exactly the services that use it: the superuser's by
        the db alone, the owner's by the db (which sets it) and migrate (which logs in with
        it), the web login's by the db and the app, the seed's by migrate, and a database URL
        by none.  Exact, so a credential missing where it is needed fails too."""
        _kind, document = stack
        assert _holders(document) == HOLDERS

    @pytest.mark.docker
    def test_production_reads_the_deploy_s_passwords_from_files(self) -> None:
        """In production the db and migrate services read every password from a mounted file,
        at the path each reader reads (``provision_roles.sh``'s ``_password`` and
        ``owner_entrypoint.sh``'s ``_load_secret``), and hold no placeholder in a variable."""
        services = _STACKS["production render"][0]()["services"]
        provision = _reads(_PROVISION, "_password")
        owner = _reads(_OWNER_ENTRYPOINT, "_load_secret")
        assert provision == {"OWNER_ROLE_PASSWORD": "owner_role_password",
                             "APP_ROLE_PASSWORD": "app_role_password"}
        assert owner == {"OWNER_ROLE_PASSWORD": "owner_role_password"}

        for name, reads in (("db", provision), ("migrate", owner)):
            mounted = _secrets(services[name])
            for variable, file_name in reads.items():
                assert mounted.get(file_name) == file_name, (name, mounted)
                assert services[name]["environment"][variable] == "", (name, variable)
        assert services["db"]["environment"]["POSTGRES_PASSWORD"] == ""



class TestTheDeployRunsInTheMigrateService:
    """The migrate service is a one-shot from the app's image, and the app waits for it."""

    def test_migrate_runs_the_owner_s_entrypoint_and_the_deploy(
        self, stack: tuple[str, dict[str, object]],
    ) -> None:
        """Its entrypoint loads the owner's password and its command is the deploy, both
        scripts the image holds and can execute; it does not restart, and the image's
        gunicorn healthcheck is off, since a deploy never answers it."""
        _kind, document = stack
        migrate = document["services"]["migrate"]
        assert (migrate["entrypoint"], migrate["command"]) == (_ENTRYPOINT, _COMMAND)
        for script in (*_ENTRYPOINT, *_COMMAND):
            on_disk = REPO_ROOT / script.removeprefix(f"{_IMAGE_APP_DIR}/")
            assert on_disk.is_file() and os.access(on_disk, os.X_OK), on_disk
        assert migrate["restart"] == "no"
        assert migrate["healthcheck"] == {"disable": True}

    def test_the_app_starts_only_after_the_deploy_succeeds(
        self, stack: tuple[str, dict[str, object]],
    ) -> None:
        """The app depends on migrate exiting 0, and migrate on the database being healthy."""
        _kind, document = stack
        services = document["services"]
        assert services["app"]["depends_on"]["migrate"]["condition"] == (
            "service_completed_successfully")
        assert {name: dependency["condition"]
                for name, dependency in services["migrate"]["depends_on"].items()} == {
            "db": "service_healthy"}

    def test_migrate_runs_the_app_s_image(self, image_stack: dict[str, object]) -> None:
        """The deploy and the web server it prepares the database for are one build."""
        services = image_stack["services"]
        assert services["migrate"]["image"] == services["app"]["image"]

    def test_the_dev_migrate_service_builds_the_app_s_image(self) -> None:
        """The dev stack builds both from this checkout, and both run its code."""
        services = _parsed(DEV_COMPOSE)["services"]
        assert services["migrate"]["build"] == services["app"]["build"] == "."
        mounts = {(entry.split(":")[0], entry.split(":")[1])
                  for entry in services["migrate"]["volumes"]}
        assert mounts == {(".", _IMAGE_APP_DIR)}

    def test_only_migrate_holds_the_deploy_s_state(self, image_stack: dict[str, object]) -> None:
        """The state volume is mounted on migrate alone, which writes the seed sentinel there,
        and migrate joins the backend network alone, so nothing but the database reaches it."""
        services = image_stack["services"]

        def sources(spec: dict[str, object]) -> set[str]:
            """The volume each of ``spec``'s mounts is from, in either compose spelling."""
            return {entry.split(":")[0] if isinstance(entry, str) else entry["source"]
                    for entry in spec.get("volumes") or []}

        assert [name for name, spec in services.items()
                if _STATE_VOLUME in sources(spec)] == ["migrate"]
        assert list(services["migrate"]["networks"]) == ["backend"]


class TestTheDatabaseMakesTheLogins:
    """The db service runs the provisioning script as its first-boot hook."""

    def test_db_mounts_the_provisioning_script_as_its_first_boot_hook(
        self, stack: tuple[str, dict[str, object]],
    ) -> None:
        """Read-only, at the hook's path, from a host file that must exist (a missing one
        refuses the start instead of becoming an empty directory), and executable as
        committed, since the image runs an executable hook and sources any other.  No other
        hook: the schemas are the deploy's, made by the owner."""
        _kind, document = stack
        hooks = [entry for entry in document["services"]["db"]["volumes"]
                 if isinstance(entry, dict) and entry["target"].startswith(
                     "/docker-entrypoint-initdb.d/")]
        assert [((REPO_ROOT / hook["source"]).resolve(), hook["target"], hook["read_only"],
                 hook["bind"]["create_host_path"]) for hook in hooks] == [
            (_PROVISION, _HOOK, True, False)]
        assert not any(isinstance(entry, str) and "docker-entrypoint-initdb.d" in entry
                       for entry in document["services"]["db"]["volumes"])
        assert os.access(_PROVISION, os.X_OK)


def test_env_example_declares_the_owner_s_password_empty() -> None:
    """``.env.example`` ships ``OWNER_ROLE_PASSWORD=`` empty, under a comment that says how to
    generate one, as for the other required passwords, so a copy never carries a working one
    and the base file's ``${OWNER_ROLE_PASSWORD:?...}`` refuses a blank."""
    match = re.search(r"((?:^#[^\n]*\n)+)^OWNER_ROLE_PASSWORD=\s*$",
                      ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    assert "token_urlsafe" in match.group(1)
