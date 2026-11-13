"""``scripts/reconcile_prod_to_canonical.sh`` lays production out for the owner login.

Task plan#3611 of card plan#2166 (rulings plan#3412 and plan#3413).  The
database container now makes the owner's and the web server's logins with
``deploy/postgres/provision_roles.sh``, reading their passwords as uid 70, and
the migrate service logs in as the owner.  So the reconcile script:

* makes ``secrets/owner_role_password``: ``.env``'s ``OWNER_ROLE_PASSWORD``
  when that holds a real value, else a new URL-safe one, never printed;
* sets each secret file's mode for the container user that reads it, on every
  run, a file already there included: 0644 for the three the database
  container reads, 0600 for the two the app alone reads, 0640 for Valkey's;
* never writes the placeholder its step 4 leaves in ``.env`` into a file as
  the secret, and refuses a migrated secret ``.env`` holds no value for;
* leaves the owner's placeholder in the rewritten ``.env``, which the base
  compose file's interpolation needs;
* copies the provisioning script to the host at 0755, snapshotting a copy
  already there, and its closing text runs it and names its rollback.

**The script is never run whole here**: it writes production's
``/opt/docker/shekel`` (a hardcoded ``PROD_ROOT``).  Each test runs some of
its steps, read from its text, with ``PROD_ROOT`` a temporary tree, and
:func:`_run_steps` refuses a piece of text that sets ``PROD_ROOT`` itself, so
the script's own value never reaches a test.  Steps 3, 4 and 7 write under
``$PROD_ROOT`` alone; a step that spelled production's path outright would
still reach it, and none does.  Step 7's ``docker compose config`` is a
stub's, whose ``DOCKER_HOST`` names no daemon.  The
expected modes are worked from which container user reads each file
(``deploy/docker-compose.prod.yml``, DOCKER SECRETS).
"""
from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path

import pytest

# Pylint: ``shekel-private-module-import`` -- the deploy tests' one home of
# the compose files' paths, the db service as parsed and the render the
# production override is graded by: a restatement here would grade its copy.
# pylint: disable-next=shekel-private-module-import
from tests.test_deploy._compose import (
    BASE_COMPOSE,
    CONFIG_ENV,
    PROD_OVERRIDE,
    config,
    service,
)
from tests.test_scripts._shell import docker_stub, run_bash, section_text

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECONCILE = _REPO_ROOT / "scripts" / "reconcile_prod_to_canonical.sh"
_PROVISION_ROLES = _REPO_ROOT / "deploy" / "postgres" / "provision_roles.sh"

#: What step 4 leaves in ``.env`` for each secret it moves to a file.
_PLACEHOLDER = "replaced_by_docker_secret"

#: The script's placeholder setting and its log, warn and die, read from its
#: text: everything between its paths and its first check.
_HELPERS = section_text(
    _RECONCILE, "SECRET_PLACEHOLDER=", "# Sanity: run from repo root.",
)

#: Each step's heading, and the heading of the step after it (``None``: the
#: step runs to the script's end).
_STEP_ENDS = {"3": "# ── 4.", "4": "# ── 5.", "7": None}

#: Each file under ``secrets/``, the ``.env`` variable it comes from, and the
#: mode its readers need.
_SECRET_FILES = {
    "secret_key": ("SECRET_KEY", 0o600),
    "totp_encryption_key": ("TOTP_ENCRYPTION_KEY", 0o600),
    "postgres_password": ("POSTGRES_PASSWORD", 0o644),
    "app_role_password": ("APP_ROLE_PASSWORD", 0o644),
    "owner_role_password": ("OWNER_ROLE_PASSWORD", 0o644),
    "valkey_password": ("SHEKEL_VALKEY_PASSWORD", 0o640),
}

#: The secrets a ``.env`` must hold a value for, since no new one can be made
#: in their place, each a value no other contains.  Each is one letter
#: repeated, so the committed-secret scan reads none as a key, and the
#: TOTP key's ``=`` shows a value holding one is read whole.
_MIGRATED = {
    "SECRET_KEY": "probe-" + "s" * 32,
    "TOTP_ENCRYPTION_KEY": "probe-" + "t" * 32 + "=",
    "POSTGRES_PASSWORD": "probe-" + "p" * 24,
    "APP_ROLE_PASSWORD": "probe-" + "a" * 24,
    "SHEKEL_VALKEY_PASSWORD": "probe-" + "v" * 24,
}

#: The owner's password as a bundled-mode ``.env`` holds it.
_OWNER = "probe-" + "o" * 24

#: A new password: ``secrets.token_urlsafe(32)``, 32 bytes in 43 characters
#: of the URL-safe alphabet, which a URL carries unencoded.
_NEW_PASSWORD = re.compile(r"[A-Za-z0-9_-]{43}")

#: The image pin step 4 carries forward.
_DIGEST = "sha256:" + "d" * 64

#: The merged config the stub's ``docker compose config`` prints: the three
#: fields step 7's sanity checks look for.
_COMPOSE_CONFIG_ARM = r"""
    "compose config") printf 'services:\n  app:\n    read_only: true\n    networks:\n      shekel-frontend: null\nsecrets:\n' ;;
"""


def _env_text(values: dict[str, str]) -> str:
    """A ``.env`` setting each of *values*, and the image pin.

    Args:
        values: Each variable's value, by name.

    Returns:
        The file's text.
    """
    lines = [f"{key}={value}" for key, value in values.items()]
    return "\n".join([*lines, f"SHEKEL_IMAGE_DIGEST={_DIGEST}"]) + "\n"


def _prod_tree(tmp_path: Path, env: dict[str, str]) -> Path:
    """Lay out a temporary ``PROD_ROOT`` as steps 1 and 2 leave one.

    ``secrets/`` at 0700, ``deploy/postgres/`` (which step 1 makes), a
    ``.env`` setting *env*, and the two compose files step 7 snapshots.

    Args:
        tmp_path: pytest's per-test temporary directory.
        env: The ``.env``'s variables.

    Returns:
        The tree's root.
    """
    root = tmp_path / "prod"
    (root / "secrets").mkdir(parents=True)
    (root / "secrets").chmod(0o700)
    (root / "deploy" / "postgres").mkdir(parents=True)
    (root / ".env").write_text(_env_text(env), encoding="utf-8")
    for name in ("docker-compose.yml", "docker-compose.override.yml"):
        (root / name).write_text(f"# the previous {name}\n", encoding="utf-8")
    return root


def _run_steps(tmp_path: Path, root: Path, *steps: str,
               ) -> subprocess.CompletedProcess:
    """Run the script's *steps*, read from its text, in order, against *root*.

    The helpers run first, then each step's section, with ``REPO_ROOT`` this
    checkout and ``PROD_ROOT`` *root*.  ``docker`` is a stub answering step
    7's ``compose config``.

    Args:
        tmp_path: pytest's per-test temporary directory, which holds *root*
            and the stub.
        root: The temporary ``PROD_ROOT``.
        steps: The steps' numbers, ``"3"``, ``"4"`` or ``"7"``.

    Returns:
        The completed process.
    """
    pieces = [_HELPERS] + [
        section_text(_RECONCILE, f"# ── {step}.", _STEP_ENDS[step])
        for step in steps
    ]
    for piece in pieces:
        assert "PROD_ROOT=" not in piece, (
            "a piece of the script sets PROD_ROOT; run here, it could write "
            "production:\n" + piece
        )
    assert root.is_relative_to(tmp_path), root
    bin_dir = tmp_path / "bin"
    if not bin_dir.exists():
        docker_stub(bin_dir, arms=_COMPOSE_CONFIG_ARM)
    return run_bash(
        'REPO_ROOT="$1"; PROD_ROOT="$2"; shift 2\n'
        'for piece in "$@"; do eval "$piece"; done',
        str(_REPO_ROOT), str(root), *pieces, path_prefix=bin_dir,
    )


def _mode(path: Path) -> int:
    """*path*'s permission bits."""
    return stat.S_IMODE(path.stat().st_mode)


def _output(result: subprocess.CompletedProcess) -> str:
    """Everything *result* printed, both streams."""
    return result.stdout + result.stderr


class TestStepThreeMakesEachFileAtItsReadersMode:
    """Step 3: each file's value, and the mode its readers need."""

    def test_each_file_holds_its_env_value_at_its_mode(self, tmp_path):
        """A bundled-mode ``.env``, its owner's password included: each file
        holds its value's exact bytes, at its mode, and no value is printed."""
        env = {**_MIGRATED, "OWNER_ROLE_PASSWORD": _OWNER}
        root = _prod_tree(tmp_path, env)
        result = _run_steps(tmp_path, root, "3")
        assert result.returncode == 0, _output(result)
        for name, (key, mode) in _SECRET_FILES.items():
            path = root / "secrets" / name
            assert path.read_bytes() == env[key].encode(), name
            assert _mode(path) == mode, (name, oct(_mode(path)))
            assert f"bytes, from .env's {key})" in result.stdout, result.stdout
            assert env[key] not in _output(result), key

    def test_a_host_the_old_script_laid_out_keeps_every_value_and_opens_the_web_login(
        self, tmp_path,
    ):
        """A host the previous script laid out: five files,
        ``app_role_password`` at the 0600 it wrote when the app alone read
        it (the db's uid 70 cannot), ``postgres_password`` at the old
        runbook's 0644, a ``.env`` holding the placeholder for each of the
        five and no ``OWNER_ROLE_PASSWORD``.  Every value is kept, the web
        login's file is opened to 0644, and the owner's is new."""
        measured = {
            "secret_key": 0o600, "totp_encryption_key": 0o600,
            "postgres_password": 0o644, "app_role_password": 0o600,
            "valkey_password": 0o640,
        }
        root = _prod_tree(tmp_path, {key: _PLACEHOLDER for key in _MIGRATED})
        for name, mode in measured.items():
            path = root / "secrets" / name
            path.write_text(f"kept-{name}-0123456789", encoding="utf-8")
            path.chmod(mode)
        result = _run_steps(tmp_path, root, "3")
        assert result.returncode == 0, _output(result)
        for name in measured:
            path = root / "secrets" / name
            assert path.read_text(encoding="utf-8") == f"kept-{name}-0123456789"
            assert f"{path} already non-empty -- keeping its value" in result.stderr
        assert _mode(root / "secrets" / "app_role_password") == 0o644
        owner = root / "secrets" / "owner_role_password"
        assert _NEW_PASSWORD.fullmatch(owner.read_text(encoding="utf-8"))
        assert _mode(owner) == 0o644

    @pytest.mark.parametrize("name", list(_SECRET_FILES))
    def test_a_file_already_there_at_another_mode_is_set_to_its_own(
        self, tmp_path, name,
    ):
        """Every run sets the mode, so a file left at 0666 is put right, its
        value kept."""
        root = _prod_tree(tmp_path, _MIGRATED)
        path = root / "secrets" / name
        path.write_text("kept-value-0123456789", encoding="utf-8")
        path.chmod(0o666)
        result = _run_steps(tmp_path, root, "3")
        assert result.returncode == 0, _output(result)
        assert path.read_text(encoding="utf-8") == "kept-value-0123456789"
        assert _mode(path) == _SECRET_FILES[name][1], oct(_mode(path))

    @pytest.mark.parametrize(
        "owner", [None, "", _PLACEHOLDER], ids=["absent", "empty", "placeholder"],
    )
    def test_the_owner_password_is_new_when_env_holds_none(self, tmp_path, owner):
        """No real value in ``.env``: a new URL-safe password, at 0644, said
        to be new, and never printed."""
        env = dict(_MIGRATED)
        if owner is not None:
            env["OWNER_ROLE_PASSWORD"] = owner
        root = _prod_tree(tmp_path, env)
        result = _run_steps(tmp_path, root, "3")
        assert result.returncode == 0, _output(result)
        path = root / "secrets" / "owner_role_password"
        password = path.read_text(encoding="utf-8")
        assert _NEW_PASSWORD.fullmatch(password), password
        assert _mode(path) == 0o644
        assert f"wrote {path} (43 bytes, a new password)" in result.stdout
        assert password not in _output(result)

    def test_a_second_run_keeps_the_new_owner_password(self, tmp_path):
        """The password the first run made is the one the database is given,
        so a second run keeps it."""
        root = _prod_tree(tmp_path, _MIGRATED)
        first = _run_steps(tmp_path, root, "3")
        assert first.returncode == 0, _output(first)
        path = root / "secrets" / "owner_role_password"
        made = path.read_text(encoding="utf-8")
        second = _run_steps(tmp_path, root, "3")
        assert second.returncode == 0, _output(second)
        assert path.read_text(encoding="utf-8") == made
        assert f"{path} already non-empty -- keeping its value" in second.stderr

    @pytest.mark.parametrize("key", list(_MIGRATED))
    @pytest.mark.parametrize(
        "value", [None, "", _PLACEHOLDER], ids=["absent", "empty", "placeholder"],
    )
    def test_a_migrated_secret_env_holds_no_value_for_is_refused(
        self, tmp_path, key, value,
    ):
        """It must be the value in use, so none is made: the script stops,
        naming the file and the variable, and the file is never written (the
        placeholder least of all)."""
        env = {k: v for k, v in _MIGRATED.items() if k != key}
        if value is not None:
            env[key] = value
        root = _prod_tree(tmp_path, env)
        name = next(n for n, (k, _) in _SECRET_FILES.items() if k == key)
        result = _run_steps(tmp_path, root, "3")
        assert result.returncode == 1, _output(result)
        target = root / "secrets" / name
        assert (
            f"FATAL: {target} is missing, and {root}/.env holds no value for {key}"
            in result.stderr
        ), result.stderr
        assert not target.exists()


def _env_values(text: str) -> dict[str, str]:
    """The variables an ``.env``'s text sets, by name; comments skipped."""
    return dict(
        line.split("=", 1) for line in text.splitlines()
        if line and not line.startswith("#")
    )


class TestStepFourLeavesAPlaceholderForEachFile:
    """Step 4's ``.env``: a placeholder where each secret was, named in its header."""

    def _rewritten(self, tmp_path: Path, env: dict[str, str]) -> tuple[Path, str]:
        """Run steps 3 and 4 on a tree whose ``.env`` sets *env*.

        Returns:
            The tree's root and the rewritten ``.env``'s text.
        """
        root = _prod_tree(tmp_path, env)
        result = _run_steps(tmp_path, root, "3", "4")
        assert result.returncode == 0, _output(result)
        return root, (root / ".env").read_text(encoding="utf-8")

    def test_each_secret_is_a_placeholder_and_no_value_remains(self, tmp_path):
        """The six secrets' variables hold the placeholder, the owner's among
        them, and no real value is left anywhere in the file."""
        env = {**_MIGRATED, "OWNER_ROLE_PASSWORD": _OWNER}
        _, text = self._rewritten(tmp_path, env)
        values = _env_values(text)
        for key, _ in _SECRET_FILES.values():
            assert values[key] == _PLACEHOLDER, key
        for key, value in env.items():
            assert value not in text, key

    def test_the_compose_files_render_with_it(self, tmp_path):
        """The placeholders are there for the base file's ``${VAR:?...}``:
        the production render, given the rewritten ``.env`` alone, passes.
        Without the owner's placeholder it is refused, naming the variable."""
        _, text = self._rewritten(tmp_path, _MIGRATED)
        env = {"PATH": CONFIG_ENV["PATH"], **_env_values(text)}
        result = config(BASE_COMPOSE, PROD_OVERRIDE, env=env)
        assert result.returncode == 0, result.stderr
        del env["OWNER_ROLE_PASSWORD"]
        refused = config(BASE_COMPOSE, PROD_OVERRIDE, env=env)
        assert refused.returncode != 0
        assert "OWNER_ROLE_PASSWORD" in refused.stderr, refused.stderr

    def test_its_header_names_every_file_step_three_made(self, tmp_path):
        """Each file under ``secrets/`` is named in the ``.env``'s comments,
        so a reader of the placeholder knows where the value went."""
        root, text = self._rewritten(tmp_path, _MIGRATED)
        comments = "\n".join(
            line for line in text.splitlines() if line.startswith("#")
        )
        made = sorted(path.name for path in (root / "secrets").iterdir())
        assert made == sorted(_SECRET_FILES), made
        for name in made:
            assert re.search(rf"\b{name}\b", comments), name


def _closing_text_names() -> tuple[str, str]:
    """The db service's container and where it mounts the provisioning script.

    Read from ``docker-compose.yml``, the one file declaring both.

    Returns:
        The container's name and the script's path inside it.
    """
    db = service("db")
    targets = [
        volume["target"] for volume in db["volumes"]
        if isinstance(volume, dict)
        and volume.get("source") == "./deploy/postgres/provision_roles.sh"
    ]
    assert len(targets) == 1, db["volumes"]
    return db["container_name"], targets[0]


class TestStepSevenCopiesTheProvisioningScript:
    """Step 7: the host copy of ``deploy/postgres/provision_roles.sh``."""

    def _step_seven(self, tmp_path: Path, root: Path,
                    ) -> tuple[subprocess.CompletedProcess, Path]:
        """Run steps 3, 4 and 7 (the closing text names step 4's backup).

        Returns:
            The completed process and step 7's snapshot directory.
        """
        result = _run_steps(tmp_path, root, "3", "4", "7")
        assert result.returncode == 0, _output(result)
        snapshots = list(root.glob(".reconcile-snapshot-*"))
        assert len(snapshots) == 1, snapshots
        return result, snapshots[0]

    def test_it_is_copied_executable(self, tmp_path):
        """The repo's script, byte for byte, at 0755: the image runs an
        executable hook and sources any other.  None was there, so none is
        snapshotted."""
        root = _prod_tree(tmp_path, _MIGRATED)
        _, snapshot = self._step_seven(tmp_path, root)
        copy = root / "deploy" / "postgres" / "provision_roles.sh"
        assert copy.read_bytes() == _PROVISION_ROLES.read_bytes()
        assert _mode(copy) == 0o755
        assert not (snapshot / "provision_roles.sh.pre-reconcile").exists()

    def test_a_copy_already_there_is_snapshotted_and_the_key_beside_it_is_not(
        self, tmp_path,
    ):
        """The old copy is kept for the rollback, as it was.  The TLS key in
        the same directory is uid 70's and unreadable to the operating user,
        so the directory is never copied: a key made unreadable here would
        fail the run, and none reaches the snapshot."""
        root = _prod_tree(tmp_path, _MIGRATED)
        postgres = root / "deploy" / "postgres"
        old = postgres / "provision_roles.sh"
        old.write_text("#!/bin/sh\n# the previous copy\n", encoding="utf-8")
        old.chmod(0o750)
        key = postgres / "server.key"
        key.write_text("probe key\n", encoding="utf-8")
        key.chmod(0o000)
        try:
            _, snapshot = self._step_seven(tmp_path, root)
        finally:
            key.chmod(0o600)
        kept = snapshot / "provision_roles.sh.pre-reconcile"
        assert kept.read_text(encoding="utf-8") == "#!/bin/sh\n# the previous copy\n"
        assert _mode(kept) == 0o750
        assert old.read_bytes() == _PROVISION_ROLES.read_bytes()
        assert _mode(old) == 0o755, oct(_mode(old))
        assert not list(snapshot.rglob("server.key"))

    def test_the_closing_text_runs_it_before_the_stack_and_names_its_rollback(
        self, tmp_path,
    ):
        """The database comes up alone, the script runs in it, then the
        stack: the migrate service logs in as the owner, whom a database
        made before the script lacks.  The rollback names the snapshot."""
        root = _prod_tree(tmp_path, _MIGRATED)
        result, snapshot = self._step_seven(tmp_path, root)
        container, hook = _closing_text_names()
        lines = [line.strip() for line in result.stdout.splitlines()]
        order = [
            lines.index("docker compose down"),
            lines.index("docker compose up -d --wait db"),
            lines.index(f"docker exec {container} {hook}"),
            lines.index("docker compose up -d"),
        ]
        assert order == sorted(order), lines
        assert (
            f"# and deploy/postgres/provision_roles.sh from"
            f" {snapshot}/provision_roles.sh.pre-reconcile"
        ) in lines, result.stdout
