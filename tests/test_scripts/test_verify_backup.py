"""``scripts/verify_backup.sh`` checks a backup's copy as the owner, and holds no password.

Ruling plan#3607 (A): the script read the database master password out of the
database container and handed it into the web container, or to a host python.
In production that read had come back empty since the passwords moved into
files, and in dev it put the master password into the web container (ruling
plan#3412 forbids it).  Now the copy is made ``OWNER shekel_owner``, its schemas
and its restore run as the owner over the database container's socket, which
asks no password, and the integrity check runs in the migrate service of the
stack the database container belongs to, read from its own compose labels,
with ``DB_NAME`` naming the copy: the owner's entrypoint builds the owner's URL
inside that container.

The script is never run whole: its configuration block and every function are
read from its text and ``main`` runs against a stub ``docker`` (``_shell.py``),
with a stub ``python`` that logs any host run.  The two ``shekel_pass`` guards
of audit finding F-109 moved here from
``tests/test_deploy/test_docker_secrets_and_env_hygiene.py``, whose tests of
the password read were deleted with it.
"""
from __future__ import annotations

import gzip
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_scripts._shell import (
    docker_stub,
    every_function_text,
    run_bash,
    section_text,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKUP_LIB = _REPO_ROOT / "scripts" / "_backup_lib.sh"
_VERIFY = _REPO_ROOT / "scripts" / "verify_backup.sh"

#: The database container's compose labels in the stub.
_LABELS = {
    "project": "probe-project",
    "project.working_dir": "/stacks/probe",
    "project.config_files": (
        "/stacks/probe/docker-compose.yml,"
        "/stacks/probe/docker-compose.override.yml"
    ),
    "service": "db",
}

#: The copy's name, set as an operator would export it.
_COPY = "shekel_verify_probe"

#: The compose command the labels name, as the stub's bracketed log keeps it.
_COMPOSE = [
    "compose", "--project-name", "probe-project",
    "--project-directory", "/stacks/probe",
    "--file", "/stacks/probe/docker-compose.yml",
    "--file", "/stacks/probe/docker-compose.override.yml",
]

#: The integrity check's run, word for word.
_RUN = _COMPOSE + [
    "run", "--rm", "--no-deps", "--pull", "never",
    "--env", f"DB_NAME={_COPY}", "migrate",
    "python", "scripts/integrity_check.py", "--verbose",
]


def _verify(tmp_path: Path, *, integrity: int = 0,
            labels: dict[str, str] | None = None,
            ) -> tuple[subprocess.CompletedProcess, list[str], list[str]]:
    """Run ``main`` from the script's text against the stub, on a real ``.sql.gz``.

    The database container ``probe-db`` is running and answers every sanity
    query with a passing value; the integrity check's run exits *integrity*.
    A ``python`` on ``PATH`` logs any host run to ``python.log``, and
    ``TMPDIR`` is a directory of its own, so a file the script writes there
    is seen.

    Args:
        tmp_path: pytest's per-test temporary directory.
        integrity: The integrity check's exit code.
        labels: The database container's compose labels; :data:`_LABELS`
            when ``None``.

    Returns:
        The completed process, and the docker stub's calls joined by spaces
        and with each argument bracketed, a line per call.
    """
    bin_dir = tmp_path / "bin"
    log = docker_stub(bin_dir, labels=_LABELS if labels is None else labels, arms=f"""
        *State.Running*) echo true ;;
        "exec -i "*) cat >/dev/null ;;
        *"MIN(start_date)"*) echo ' 26 | 2026-01-02 | 2026-12-18' ;;
        *"SELECT EXISTS"*) echo ' t' ;;
        *"alembic_version"*) echo ' a1b2c3d4e5f6' ;;
        *"SELECT COUNT(*)"*) echo ' 3' ;;
        "compose "*" run "*) exit {integrity} ;;
        """)
    (bin_dir / "python").write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >>'{tmp_path}/python.log'\n",
        encoding="utf-8",
    )
    (bin_dir / "python").chmod(0o755)
    (tmp_path / "tmp").mkdir()
    backup = tmp_path / "shekel_backup_20261112_020000.sql.gz"
    backup.write_bytes(gzip.compress(b"SELECT 1;\n"))
    result = run_bash(
        'source "$1"; DB_CONTAINER=probe-db; VERIFY_DB="$2"; TMPDIR="$3"; '
        'eval "$4"; eval "$5"; main "$6"',
        str(_BACKUP_LIB), _COPY, str(tmp_path / "tmp"),
        section_text(_VERIFY, "# ── Configuration", "# ── Functions"),
        every_function_text(_VERIFY), str(backup),
        path_prefix=bin_dir,
    )
    return (
        result,
        log.read_text(encoding="utf-8").splitlines(),
        (bin_dir / "argv.log").read_text(encoding="utf-8").splitlines(),
    )


def _bracketed(words: list[str]) -> str:
    """One call's arguments as the stub's ``argv.log`` writes them."""
    return "".join(f"[{word}]" for word in words)


class TestTheCopyIsTheOwners:
    """The owner makes, fills and owns the copy, so it reads it with no grant."""

    def test_the_copy_is_made_owned_filled_and_dropped(self, tmp_path):
        """The superuser makes the copy the owner's, the owner makes its
        schemas and restores into it, and the copy is dropped on exit."""
        result, calls, _ = _verify(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        made = (
            "exec probe-db psql -U shekel_user -d postgres --quiet -c "
            f"CREATE DATABASE {_COPY} OWNER shekel_owner;"
        )
        schemas = (
            f"exec probe-db psql -U shekel_owner -d {_COPY} --quiet -c "
            "CREATE SCHEMA IF NOT EXISTS ref; CREATE SCHEMA IF NOT EXISTS auth; "
            "CREATE SCHEMA IF NOT EXISTS budget; CREATE SCHEMA IF NOT EXISTS salary; "
            "CREATE SCHEMA IF NOT EXISTS system; "
        )
        restored = (
            f"exec -i probe-db psql -U shekel_owner -d {_COPY} --quiet "
            "--single-transaction --set ON_ERROR_STOP=1 --output /dev/null"
        )
        dropped = (
            "exec probe-db psql -U shekel_user -d postgres --quiet -c "
            f"DROP DATABASE IF EXISTS {_COPY};"
        )
        assert calls.index(made) < calls.index(schemas) < calls.index(restored)
        assert calls[-1] == dropped, calls


class TestTheCheckRunsInTheMigrateService:
    """The check runs as the owner, in the stack the database container is in."""

    def test_the_run_is_the_migrate_service_of_the_db_containers_stack(
        self, tmp_path,
    ):
        """Every word of the stack comes from the database container's labels,
        and the copy is named by ``DB_NAME``; the project is parsed first."""
        _, _, argvs = _verify(tmp_path)
        config = _bracketed(_COMPOSE + ["config", "--quiet"])
        assert argvs.count(config) == 1, argvs
        assert argvs.count(_bracketed(_RUN)) == 1, argvs
        assert argvs.index(config) < argvs.index(_bracketed(_RUN)), argvs

    @pytest.mark.parametrize(
        ("integrity", "status", "verdict"),
        [(0, 0, "Status: PASS"), (2, 2, "Status: PASS WITH WARNINGS"),
         (1, 1, "Status: FAIL"), (3, 1, "Status: FAIL")],
        ids=["passed", "warnings", "critical", "script-error"],
    )
    def test_the_checks_exit_code_is_the_verdict(
        self, tmp_path, integrity, status, verdict,
    ):
        """``integrity_check.py``'s 0, 2, 1 and 3 reach the summary through
        the run, and the copy is dropped every time."""
        result, calls, _ = _verify(tmp_path, integrity=integrity)
        assert result.returncode == status, result.stdout + result.stderr
        assert f"Integrity checks: exit code {integrity}" in result.stdout
        statuses = [line for line in result.stdout.splitlines() if "Status:" in line]
        assert len(statuses) == 1, result.stdout
        assert statuses[0].endswith(verdict), statuses
        assert calls[-1].endswith(f"DROP DATABASE IF EXISTS {_COPY};"), calls

    def test_a_missing_label_refuses_before_any_copy_is_made(self, tmp_path):
        """No stack to run the check in: refused, naming the label, and no
        copy is made."""
        labels = {k: v for k, v in _LABELS.items() if k != "project.working_dir"}
        result, calls, _ = _verify(tmp_path, labels=labels)
        assert result.returncode == 1, result.stdout + result.stderr
        assert (
            "Database container 'probe-db' has no "
            "com.docker.compose.project.working_dir label" in result.stdout
        )
        assert not [call for call in calls if "CREATE DATABASE" in call], calls


class TestNoPasswordLeavesAnyContainer:
    """Ruling plan#3607: the script reads no password, so none can travel."""

    def test_a_run_reads_no_password_and_writes_no_file(self, tmp_path):
        """No call reads a password or carries a URL, the web container is
        never asked, no host python runs, and no temporary file is written."""
        result, calls, _ = _verify(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        for call in calls:
            assert not re.search(r"printenv|PASSWORD|--env-file|postgresql://|-app\b", call), call
        assert not (tmp_path / "python.log").exists()
        assert not list((tmp_path / "tmp").iterdir())

    def test_no_executable_line_reads_or_builds_a_credential(self):
        """Every branch, run or not: no line outside a comment reads the
        master password, builds a URL or hands an environment file over."""
        for number, line in enumerate(
            _VERIFY.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            if line.lstrip().startswith("#"):
                continue
            assert not re.search(
                r"POSTGRES_PASSWORD|printenv|--env-file|DATABASE_URL|postgresql://",
                line,
            ), f"scripts/verify_backup.sh:{number}: {line!r}"

    def test_the_script_does_not_embed_shekel_pass_in_a_uri(self):
        """Audit finding F-109: no ``:shekel_pass@`` credential fragment.

        Moved from the hygiene module with the read it guarded beside.
        """
        assert ":shekel_pass@" not in _VERIFY.read_text(encoding="utf-8")

    def test_the_script_does_not_fall_back_to_shekel_pass(self):
        """Audit finding F-109: no executable ``|| echo "shekel_pass"`` fallback.

        Comment lines that quote the historical pattern are skipped.
        """
        pattern = re.compile(r"\|\|\s*echo\s+[\"']?shekel_pass[\"']?")
        for number, line in enumerate(
            _VERIFY.read_text(encoding="utf-8").splitlines(), start=1,
        ):
            if line.lstrip().startswith("#"):
                continue
            assert not pattern.search(line), (
                f"scripts/verify_backup.sh:{number} falls back to "
                f"shekel_pass: {line!r}"
            )
