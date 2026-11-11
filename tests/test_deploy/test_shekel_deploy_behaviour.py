"""``deploy/shekel-deploy.sh`` is DRIVEN, and its safety properties observed.

Plan step **R-F8**, findings **F-8** and **F-14**, ruling **R-R14**.

**Why this file replaced a source-level one.**  The first attempt at gating
R-F8 asserted the ORDER of the script's steps by locating substrings in its
text, on the premise that a behavioural control "needs Docker, two real GHCR
images with different migration sets, and about ninety seconds -- it cannot
run in CI".  Adversarial review demolished both halves.  The premise is false:
every target in the script is an environment override by deliberate design and
every external command resolves through ``PATH``, so a stub ``docker`` drives
the real branch logic in about a second with no daemon at all.  And the
consequence of the premise was worse -- of 45 mutations, **24 stayed green**,
including one that replaced the entire script with a 33-line do-nothing stub
whose ``take_predeploy_dump`` body was a comment reading ``# die die die die``.
Every assertion was a substring-position check, so any file with the right
bytes in the right order satisfied all of them.  Among the mutations that
passed: inverting the migration guard so a migration-bearing release re-pins
(finding F-8, restored verbatim), and making the dump a no-op.

So this file observes OUTCOMES instead: which commands ran and in what order,
what the pin says afterwards, whether a dump exists on disk, and the exit
status.  None of that can be satisfied by a comment.

**The stub.**  ``_write_fake_docker`` emits a ``docker`` shell script onto a
``PATH`` prefix.  It answers the handful of subcommands the deploy script
issues, logs every invocation so ordering is observable, and is scripted per
test: which migrations each image "contains" (a listing FILE per image, since
the regression below needs a listing past 128 KiB, the most one environment
string may hold), and through environment variables whether the database
container is "running", whether ``pg_dump`` writes a readable archive, and
whether the container ever reports healthy.  Nothing here touches a real
daemon, so these tests are NOT ``@pytest.mark.docker`` and run in CI like any
other.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import textwrap
import threading
import time
from unittest import mock

import pytest
from sqlalchemy import text


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEPLOY_SCRIPT = _REPO_ROOT / "deploy" / "shekel-deploy.sh"

_OLD = "sha256:" + "a" * 64
_NEW = "sha256:" + "b" * 64


def _image_id(digest: str) -> str:
    """Return the local image ID the stub reports for the image at *digest*.

    ``stop_failed_container`` tells the target's container apart by image ID
    (``docker inspect --format '{{.Image}}'`` on the container against
    ``docker image inspect --format '{{.Id}}'`` on the pulled target), never by
    reference spelling.  The stub's IDs are derived from the digest but differ
    from it, so a script that compared an ID with a digest would be seen.

    Args:
        digest: A ``sha256:...`` image digest.

    Returns:
        A ``sha256:...`` image ID unique to that digest.
    """
    return "sha256:" + hashlib.sha256(digest.encode("ascii")).hexdigest()


#: What a readable ``pg_dump -Fc`` archive starts with.  The stub writes it and
#: the stub's ``pg_restore -l`` requires it, so "the dump is validated before
#: it is renamed into place" becomes an observable fact rather than a claim.
_DUMP_MAGIC = "PGDMP-FAKE"
#: The terminator only a COMPLETE archive carries.
_DUMP_END = "ENDOFARCHIVE"


def _write_fake_docker(bin_dir: pathlib.Path) -> None:
    """Install a scripted ``docker`` stub onto a PATH directory.

    Args:
        bin_dir: Directory placed first on ``PATH`` for the run.
    """
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Scripted stand-in for the docker CLI.  Every invocation is logged so
        # the test can assert ORDER; behaviour is driven by FAKE_* env vars.
        printf '%s\\n' "$*" >>"$FAKE_LOG"

        case "$1" in
        info) exit 0 ;;
        pull) exit 0 ;;
        image)
            # `docker image inspect <ref> --format ...`: the target's image ID
            # (an empty FAKE_TARGET_IMAGE_ID models one that cannot be read),
            # or resolving :latest to its digest.
            for arg in "$@"; do
                if [ "$arg" = '{{{{.Id}}}}' ]; then
                    [ -z "$FAKE_TARGET_IMAGE_ID" ] && exit 1
                    printf '%s\\n' "$FAKE_TARGET_IMAGE_ID"
                    exit 0
                fi
            done
            # :latest's RepoDigests, from a FILE like the listings: the
            # regression's list outgrows an environment string.
            # FAKE_IMAGE_INSPECT_RC models an inspect that fails.
            [ "$FAKE_IMAGE_INSPECT_RC" != "0" ] && exit "$FAKE_IMAGE_INSPECT_RC"
            cat "$FAKE_REPO_DIGESTS_FILE"
            exit 0
            ;;
        run)
            # The migration pre-flight: one line per migration file, read from
            # a FILE, since the regression's listing is larger than one
            # environment string may be (128 KiB, MAX_ARG_STRLEN).
            for arg in "$@"; do
                case "$arg" in
                *{_OLD[7:19]}*) cat "$FAKE_OLD_MIGRATIONS_FILE"; exit 0 ;;
                *{_NEW[7:19]}*) cat "$FAKE_NEW_MIGRATIONS_FILE"; exit 0 ;;
                esac
            done
            exit 0
            ;;
        inspect)
            # The db running-check, the health poll, and the app container's
            # image ID (an empty FAKE_CONTAINER_IMAGE_ID models no container).
            for arg in "$@"; do
                case "$arg" in
                *State.Running*)
                    # FAKE_DB_INSPECT_RC models a container docker cannot find.
                    [ "$FAKE_DB_INSPECT_RC" != "0" ] && exit "$FAKE_DB_INSPECT_RC"
                    printf '%s\\n' "$FAKE_DB_RUNNING"
                    exit 0
                    ;;
                *State.Health*)  printf '%s\\n' "$FAKE_HEALTH";     exit 0 ;;
                '{{{{.Image}}}}')
                    [ -z "$FAKE_CONTAINER_IMAGE_ID" ] && exit 1
                    printf '%s\\n' "$FAKE_CONTAINER_IMAGE_ID"
                    exit 0
                    ;;
                esac
            done
            exit 0
            ;;
        stop)
            # Stopping the failed container before the stamp is re-read
            # (plan step balance:X-cv).  FAKE_STOP_RC models a daemon that
            # refuses.
            exit "$FAKE_STOP_RC"
            ;;
        exec)
            for arg in "$@"; do
                case "$arg" in
                psql)
                    # public.alembic_version.  The stamp ADVANCES once the
                    # deploy has run, which is what a migration at entrypoint
                    # step 3 does and what makes the post-failure re-read
                    # mean something.  FAKE_STAMP_REREAD_RC models a re-read
                    # that fails (finding BAL-535).
                    if grep -q '^compose' "$FAKE_LOG"; then
                        [ "$FAKE_STAMP_REREAD_RC" != "0" ] && exit "$FAKE_STAMP_REREAD_RC"
                        printf '%s\\n' "$FAKE_STAMPED_AFTER"
                    else
                        printf '%s\\n' "$FAKE_STAMPED"
                    fi
                    exit 0
                    ;;
                pg_dump)
                    [ "$FAKE_PGDUMP_RC" != "0" ] && {{
                        printf 'fake pg_dump: %s\\n' "$FAKE_PGDUMP_ERR" >&2
                        exit "$FAKE_PGDUMP_RC"
                    }}
                    # What the backup directory holds while the archive is
                    # written: only a .part file, or a Ctrl-C here would
                    # leave a truncated dump wearing a valid name.
                    saw=$(ls -A "$FAKE_BACKUP_DIR")
                    printf 'pg_dump-saw %s\\n' "${{saw//$'\\n'/ }}" >>"$FAKE_LOG"
                    # A truncated archive keeps the header (so a TOC-only
                    # read still succeeds) and loses the terminator.
                    if [ "$FAKE_DUMP_TRUNCATED" = "1" ]; then
                        printf '{_DUMP_MAGIC}|payl'
                    else
                        printf '{_DUMP_MAGIC}|payload|{_DUMP_END}'
                    fi
                    exit 0
                    ;;
                pg_restore)
                    # -l reads only the table of contents; -f decodes every
                    # data block.  Modelled separately ON PURPOSE: measured on
                    # a real archive, -l accepts a 99%-truncated dump, so a
                    # script that validated with -l would rename a corrupt
                    # artifact into place and the test must be able to see it.
                    body=$(cat)
                    for a in "$@"; do
                        if [ "$a" = "-l" ]; then
                            case "$body" in
                            {_DUMP_MAGIC}*) exit 0 ;;
                            *) exit 1 ;;
                            esac
                        fi
                    done
                    case "$body" in
                    *{_DUMP_END}) exit 0 ;;
                    *) exit 1 ;;
                    esac
                    ;;
                esac
            done
            exit 0
            ;;
        logs)
            # The failed container's log, which the script saves beside the
            # dump before a re-pin recreates the container (plan step
            # balance:X-cv).  FAKE_LOGS_RC models a container with none.
            [ "$FAKE_LOGS_RC" != "0" ] && exit "$FAKE_LOGS_RC"
            printf '%s\\n' "$FAKE_CONTAINER_LOG"
            exit 0
            ;;
        compose)
            # Record whether a dump already existed when the deploy ran.
            if compgen -G "$FAKE_BACKUP_DIR/*.dump" >/dev/null; then
                printf 'compose-saw-dump\\n' >>"$FAKE_LOG"
            else
                printf 'compose-saw-NO-dump\\n' >>"$FAKE_LOG"
            fi
            exit "$FAKE_COMPOSE_RC"
            ;;
        esac
        exit 0
        """), encoding="utf-8")
    stub.chmod(0o755)


class _Stack:
    """A throwaway deploy target the real script can be pointed at."""

    def __init__(self, tmp_path: pathlib.Path):
        """Build the directory layout and the stub PATH.

        Args:
            tmp_path: pytest's per-test temporary directory.
        """
        self.root = tmp_path
        self.shekel_dir = tmp_path / "stack"
        self.backup_dir = tmp_path / "backups"
        self.bin_dir = tmp_path / "bin"
        for path in (self.shekel_dir, self.backup_dir, self.bin_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.env_file = self.shekel_dir / ".env"
        self.env_file.write_text(
            f"SHEKEL_IMAGE_DIGEST={_OLD}\n", encoding="utf-8"
        )
        self.env_file.chmod(0o600)
        self.log = tmp_path / "docker.log"
        self.log.write_text("", encoding="utf-8")
        _write_fake_docker(self.bin_dir)

    @property
    def pin(self) -> str:
        """The digest currently written into the stack's ``.env``.

        Returns:
            The ``SHEKEL_IMAGE_DIGEST`` value.
        """
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SHEKEL_IMAGE_DIGEST="):
                return line.split("=", 1)[1]
        raise AssertionError("the stack's .env lost its digest line entirely")

    @property
    def dumps(self) -> list[pathlib.Path]:
        """Completed dump artifacts in the stack's backup directory.

        Returns:
            Every ``*.dump`` file, excluding in-progress ``*.part`` files.
        """
        return sorted(self.backup_dir.glob("*.dump"))

    @property
    def invocations(self) -> list[str]:
        """Every stub ``docker`` invocation, in order.

        Returns:
            One entry per call, plus the ``compose-saw-*`` markers.
        """
        return self.log.read_text(encoding="utf-8").splitlines()

    def run(self, *, target: str = _NEW, old_migrations: str = "0001_a.py\n",
            new_migrations: str = "0001_a.py\n", health: str = "unhealthy",
            db_running: str = "true", pgdump_rc: str = "0",
            pgdump_err: str = "no space left on device",
            dump_truncated: str = "0", stamped: str = "0001",
            stamped_after: str | None = None, dry_run: bool = False,
            compose_rc: str = "0",
            container_log: str = "fake container log",
            logs_rc: str = "0", container_image_id: str | None = None,
            target_image_id: str | None = None,
            stop_rc: str = "0",
            stamp_reread_rc: str = "0",
            pull_latest: bool = False,
            repo_digests: str | None = None,
            image_inspect_rc: str = "0",
            db_inspect_rc: str = "0",
            script: pathlib.Path | None = None,
            args: list[str] | None = None,
            ) -> subprocess.CompletedProcess:
        """Run the real deploy script against this stack.

        Args:
            target: Digest to deploy.
            old_migrations: Migration listing the CURRENT image reports.
            new_migrations: Migration listing the TARGET image reports.
            health: What the health poll reports (``healthy``/``unhealthy``).
            db_running: Whether the database container is up.
            pgdump_rc: Exit status for ``pg_dump``.
            pgdump_err: What ``pg_dump`` writes to stderr when it fails.
            dump_truncated: ``1`` to write an archive whose TOC reads but
                whose data blocks are cut short.
            stamped: The revision ``alembic_version`` holds before the deploy.
            stamped_after: The revision it holds once the deploy has run
                (defaults to *stamped*, i.e. nothing migrated).
            dry_run: Pass ``--dry-run``.
            compose_rc: Exit status for ``docker compose``.
            container_log: What ``docker logs`` prints for the container.
            logs_rc: Exit status for ``docker logs`` (non-zero: no log).
            container_image_id: The image ID the app container runs, as
                ``docker inspect`` reports it; ``""`` for no container.
                Defaults to the TARGET's, the container a failed deploy
                leaves.
            target_image_id: The pulled target's image ID; ``""`` for one
                that cannot be read.  Defaults to the TARGET's.
            stop_rc: Exit status for ``docker stop``.
            stamp_reread_rc: Exit status for every stamp read once the deploy
                has run (non-zero: the re-read fails).
            pull_latest: Pass NO digest, so the script pulls ``:latest`` and
                resolves its digest from the RepoDigests.
            repo_digests: What ``docker image inspect`` reports as the
                RepoDigests of ``:latest``; defaults to the one line naming
                *target* in the production repository.
            image_inspect_rc: Exit status for that inspect.
            db_inspect_rc: Exit status for the database container's
                running-check (non-zero: docker cannot find it).
            script: The script to run, a symlink or copy; defaults to the
                repository's ``deploy/shekel-deploy.sh``.
            args: The script's arguments, given whole; when absent they are
                ``--no-verify``, then ``--dry-run`` per *dry_run*, then
                *target* unless *pull_latest*.

        Returns:
            The completed process, stdout and stderr captured separately.
        """
        old_listing = self.root / "old_migrations.txt"
        new_listing = self.root / "new_migrations.txt"
        old_listing.write_text(old_migrations, encoding="utf-8")
        new_listing.write_text(new_migrations, encoding="utf-8")
        digests = self.root / "repo_digests.txt"
        digests.write_text(
            f"ghcr.io/saltyreformed-labs/shekel@{target}\n" if repo_digests is None
            else repo_digests,
            encoding="utf-8",
        )
        # No SHEKEL_* variable is inherited: the script reads every target
        # from one, so an exported SHEKEL_ENV_FILE would aim these runs at
        # that file's pin.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("SHEKEL_")}
        env.update({
            "PATH": f"{self.bin_dir}:{env['PATH']}",
            "SHEKEL_DIR": str(self.shekel_dir),
            "SHEKEL_BACKUP_DIR": str(self.backup_dir),
            "SHEKEL_CONTAINER_NAME": "probe-app",
            "SHEKEL_DB_CONTAINER": "probe-db",
            "SHEKEL_NTFY_TOKEN_FILE": str(self.root / "no-token"),
            "SHEKEL_HEALTH_TIMEOUT_S": "1",
            "SHEKEL_HEALTH_INTERVAL_S": "1",
            "FAKE_LOG": str(self.log),
            "FAKE_BACKUP_DIR": str(self.backup_dir),
            "FAKE_REPO_DIGESTS_FILE": str(digests),
            "FAKE_IMAGE_INSPECT_RC": image_inspect_rc,
            "FAKE_DB_INSPECT_RC": db_inspect_rc,
            "FAKE_OLD_MIGRATIONS_FILE": str(old_listing),
            "FAKE_NEW_MIGRATIONS_FILE": str(new_listing),
            "FAKE_HEALTH": health,
            "FAKE_DB_RUNNING": db_running,
            "FAKE_PGDUMP_RC": pgdump_rc,
            "FAKE_PGDUMP_ERR": pgdump_err,
            "FAKE_DUMP_TRUNCATED": dump_truncated,
            "FAKE_STAMPED": stamped,
            "FAKE_STAMPED_AFTER": (
                stamped if stamped_after is None else stamped_after
            ),
            "FAKE_COMPOSE_RC": compose_rc,
            "FAKE_CONTAINER_LOG": container_log,
            "FAKE_LOGS_RC": logs_rc,
            "FAKE_CONTAINER_IMAGE_ID": (
                _image_id(target) if container_image_id is None
                else container_image_id
            ),
            "FAKE_TARGET_IMAGE_ID": (
                _image_id(target) if target_image_id is None
                else target_image_id
            ),
            "FAKE_STOP_RC": stop_rc,
            "FAKE_STAMP_REREAD_RC": stamp_reread_rc,
        })
        if args is None:
            args = ["--no-verify"]
            if dry_run:
                args.append("--dry-run")
            if not pull_latest:
                args.append(target)
        return subprocess.run(
            ["bash", str(script or _DEPLOY_SCRIPT), *args], env=env,
            capture_output=True, text=True, timeout=120, check=False,
        )


def _flat(text: str) -> str:
    """Collapse whitespace so an assertion can ignore terminal wrapping.

    The script wraps its messages for an 80-column terminal, so a phrase the
    operator reads as one sentence is split across lines in the raw output.

    Args:
        text: Captured stdout/stderr.

    Returns:
        The same text with every run of whitespace reduced to one space.
    """
    return " ".join(text.split())


def _said(result: subprocess.CompletedProcess, calls: list[str]) -> str:
    """Render what a run printed and invoked, for an assertion's message.

    Finding BAL-618's CI failure carried no diagnosis: ``calls.index(...)``
    raised ``ValueError`` before any assertion's message could run, so the log
    held neither the script's output nor the stub's calls.  Every read in this
    module that could raise on a missing call is an assertion whose message
    renders this instead (plan step balance:X-dm).

    Args:
        result: The completed run.
        calls: The stub's invocation log, in order.

    Returns:
        The exit status, everything the script printed, and every call.
    """
    return (
        f"The script exited {result.returncode} and said:\n"
        f"{result.stdout}{result.stderr}\nInvocations: {calls}"
    )


def _position(
    calls: list[str], call: str, result: subprocess.CompletedProcess,
) -> int:
    """Return where *call* first sits in *calls*, failing with the diagnosis.

    Args:
        calls: The stub's invocation log, in order.
        call: The invocation to find.
        result: The completed run, rendered if *call* is absent.

    Returns:
        The index of the first ``call`` in *calls*.
    """
    assert call in calls, f"{call!r} was never invoked.  {_said(result, calls)}"
    return calls.index(call)


@pytest.fixture(name="stack")
def _stack(tmp_path) -> _Stack:
    """A throwaway stack with a scripted ``docker`` on PATH.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The prepared :class:`_Stack`.
    """
    return _Stack(tmp_path)


#: A forward release: the target adds two migrations, and running them moves
#: the stamp to a revision the CURRENT image has never heard of.
_MIGRATION_BEARING = {
    "old_migrations": "0001_a.py\n",
    "new_migrations": "0001_a.py\n0002_b.py\n0003_c.py\n",
    "stamped": "0001",
    "stamped_after": "0003",
}
#: A pure digest revert: identical migration sets, so the stamp cannot move.
_NO_MIGRATIONS = {
    "old_migrations": "0001_a.py\n0002_b.py\n",
    "new_migrations": "0001_a.py\n0002_b.py\n",
    "stamped": "0002",
}
#: A DOWNGRADE: the target is older than the database.  It adds nothing, so
#: the retired "does this release add migrations" test called it safe -- while
#: it is precisely the image that cannot resolve today's schema.
_DOWNGRADE = {
    "old_migrations": "0001_a.py\n0002_b.py\n0003_c.py\n",
    "new_migrations": "0001_a.py\n",
    "stamped": "0003",
}
#: A migration-bearing release whose entrypoint step 3 ROLLED BACK (plan step balance:X-cv):
#: its migrations, seed and audit-trigger check are one transaction, so the stamp is unmoved.
_STEP_3_ROLLED_BACK = {
    "old_migrations": "0001_a.py\n",
    "new_migrations": "0001_a.py\n0002_b.py\n",
    "stamped": "0001",
    "stamped_after": "0001",
}
#: Part of what ``init_database.py`` prints when step 3 refuses: the operator's pointer to
#: the repair, which only the failed container's log carries.  The audit-trigger check's
#: (``app.audit_infrastructure``); a ledger hook's until plan task plan#3077.
_REFUSAL_LINE = "Missing: budget.transactions (writes to them leave no audit row)."


class TestTheHarnessInheritsNoTarget:
    """The stack's runs read no ``SHEKEL_*`` variable from the test's environment.

    The script takes every target from one (``SHEKEL_ENV_FILE``,
    ``SHEKEL_DB_CONTAINER``, ...), and ``_Stack.run`` sets only some of them,
    so one exported in the shell that runs the suite would aim a deploy at
    whatever it names: a production pin, written by a test.
    """

    def test_an_exported_env_file_is_not_the_one_deployed(
        self, stack, tmp_path, monkeypatch,
    ):
        """A deploy with ``SHEKEL_ENV_FILE`` exported pins the stack's file, not that one."""
        decoy = tmp_path / "decoy.env"
        decoy.write_text(f"SHEKEL_IMAGE_DIGEST={_OLD}\n", encoding="utf-8")
        monkeypatch.setenv("SHEKEL_ENV_FILE", str(decoy))
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert decoy.read_text(encoding="utf-8") == f"SHEKEL_IMAGE_DIGEST={_OLD}\n", (
            _said(result, calls))


class TestTheScriptIsInTheRepository:
    """Finding F-14: the path that rolls production used to be untracked.

    It lived only on the host, so it sat outside every linter in the polyglot
    gate and nobody could review it -- which is why F-8 survived unnoticed.
    Kept from the retired source-level file; everything else that file
    asserted is now observed behaviourally above.
    """

    def test_the_canonical_copy_is_present_and_executable(self):
        """`deploy/shekel-deploy.sh` is tracked here and runnable."""
        assert _DEPLOY_SCRIPT.is_file(), (
            "deploy/shekel-deploy.sh is missing -- the production deploy path "
            "is untracked again, which is finding F-14."
        )
        assert _DEPLOY_SCRIPT.stat().st_mode & 0o111, (
            "deploy/shekel-deploy.sh is not executable"
        )


class TestNoDumpNoDeploy:
    """The database is dumped before the deploy, or the deploy does not run."""

    def test_a_dump_exists_before_compose_is_called(self, stack):
        """When compose runs, a completed dump is already on disk.

        Observed, not inferred: the stub records whether a ``*.dump`` existed
        at the moment ``docker compose`` was invoked.
        """
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "compose-saw-dump" in stack.invocations, (
            f"docker compose ran with no dump on disk; 'no dump, no deploy' "
            f"is not what the script does.  Invocations: {stack.invocations}"
        )
        assert len(stack.dumps) == 1

    def test_pg_dump_failing_aborts_before_anything_is_touched(self, stack):
        """A dump failure leaves the pin alone and never starts a container."""
        result = stack.run(pgdump_rc="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "no dump, no deploy" in result.stdout + result.stderr
        assert stack.pin == _OLD, "the pin moved despite the dump failing"
        assert not any(i.startswith("compose") for i in stack.invocations), (
            f"a container was started after the dump failed: "
            f"{stack.invocations}"
        )
        assert stack.dumps == [], "a dump artifact survived a failed pg_dump"

    def test_an_unreadable_dump_is_rejected_and_not_renamed(self, stack):
        """A truncated archive fails ``pg_restore -l`` and aborts the deploy.

        The property the ``.part`` rename exists for: a truncated dump wearing
        a valid name is the artifact the refusal path tells the operator to
        restore from.
        """
        result = stack.run(dump_truncated="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "truncated" in result.stdout + result.stderr
        assert stack.dumps == [], (
            "an unreadable dump was renamed into place as if it were valid"
        )
        assert list(stack.backup_dir.glob("*.part")) == [], (
            "the rejected .part file was left behind"
        )
        assert stack.pin == _OLD

    def test_the_database_being_down_aborts_the_deploy(self, stack):
        """No reachable database means no dump, so no deploy."""
        result = stack.run(db_running="false", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "no dump, no deploy" in result.stdout + result.stderr
        assert stack.pin == _OLD

    def test_a_database_container_docker_cannot_find_aborts_the_deploy(
        self, stack,
    ):
        """docker cannot inspect it at all: not running, so no deploy.

        Plan step balance:X-dm: the running-check is ``container_running``
        in ``scripts/_container_lib.sh`` now (ruling R-BAL254), and a failed
        inspect is its NO, as the replaced ``| grep -q true`` read it.
        """
        result = stack.run(db_inspect_rc="1", **_MIGRATION_BEARING)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert (
            "database container 'probe-db' is not running; no dump, no deploy"
            in result.stdout + result.stderr
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)
        assert not any(c.startswith("compose") for c in calls), (
            _said(result, calls)
        )


#: The four docker calls a dump-only run makes, in order, and no other: the
#: daemon check, the database's running-check, the dump and its readback.
_DUMP_ONLY_CALLS = [
    "info",
    "inspect --format {{.State.Running}} probe-db",
    "exec probe-db pg_dump -U shekel_user -d shekel -Fc",
    "exec -i probe-db pg_restore -f /dev/null",
]


def _docker_calls(calls: list[str]) -> list[str]:
    """Return *calls* without the stub's own ``*-saw*`` markers.

    Args:
        calls: The stub's invocation log, in order.

    Returns:
        The docker invocations alone.
    """
    return [call for call in calls if "-saw" not in call.split(" ", 1)[0]]


def _dump_written_as_part(
    calls: list[str], dump: pathlib.Path, result: subprocess.CompletedProcess,
) -> None:
    """Assert that while pg_dump wrote, the backup directory held only *dump*'s ``.part``.

    Args:
        calls: The stub's invocation log, in order.
        dump: The completed dump the run left.
        result: The completed run, rendered on failure.
    """
    assert f"pg_dump-saw {dump.name}.part" in calls, _said(result, calls)


class TestDumpOnlyTakesTheDeploysDumpAndNothingElse:
    """``--dump-only``: the same-day dump a release rehearsal restores (card X-cx L9).

    The deploy's own dump function, with its ``.part`` file and its decode of
    every data block, run alone: no pull, no cosign, no pre-flight (listing
    an image's migrations starts a container), no pin written, and no
    container started, stopped or recreated.  The dump's path is the last
    line printed, for a caller to read.
    """

    def test_it_dumps_prints_the_path_and_makes_no_other_call(self, stack):
        """One decoded dump named for the current pin, its path last, four calls.

        .env is not rewritten, not even with the same bytes: its inode,
        modification time and change time are unchanged.
        """
        env_before = stack.env_file.read_bytes()
        stat_before = stack.env_file.stat()
        result = stack.run(args=["--dump-only"])
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert _docker_calls(calls) == _DUMP_ONLY_CALLS, _said(result, calls)
        assert len(stack.dumps) == 1, _said(result, calls)
        assert re.fullmatch(
            rf"shekel_prod_dump_{_OLD[7:19]}_\d{{8}}_\d{{6}}\.dump",
            stack.dumps[0].name,
        ), stack.dumps[0].name
        _dump_written_as_part(calls, stack.dumps[0], result)
        assert result.stdout.splitlines()[-1] == str(stack.dumps[0]), (
            _said(result, calls)
        )
        assert list(stack.backup_dir.glob("*.part")) == [], _said(result, calls)
        stat_after = stack.env_file.stat()
        assert stack.env_file.read_bytes() == env_before, (
            f"--dump-only rewrote .env.  {_said(result, calls)}"
        )
        assert (stat_after.st_ino, stat_after.st_mtime_ns,
                stat_after.st_ctime_ns) == (
            stat_before.st_ino, stat_before.st_mtime_ns, stat_before.st_ctime_ns,
        ), f"--dump-only replaced or touched .env.  {_said(result, calls)}"

    def test_a_read_only_env_file_still_dumps(self, stack):
        """It writes no pin, so it needs .env readable, not writable."""
        stack.env_file.chmod(0o400)
        result = stack.run(args=["--dump-only"])
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert len(stack.dumps) == 1, _said(result, calls)

    def test_a_deploy_still_refuses_a_read_only_env_file(self, stack):
        """The control: a deploy writes the pin, so it refuses before any call."""
        stack.env_file.chmod(0o400)
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert "not writable by" in result.stderr, _said(result, calls)
        assert calls == [], _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    @pytest.mark.parametrize("failure, says", [
        ({"pgdump_rc": "1"}, "pg_dump exited 1"),
        ({"dump_truncated": "1"}, "does not decode end to end"),
        ({"db_running": "false"}, "is not running; no dump"),
        ({"db_inspect_rc": "1"}, "is not running; no dump"),
    ], ids=["pg_dump-fails", "truncated", "database-down", "database-unknown"])
    def test_a_dump_that_fails_leaves_no_file_and_prints_no_path(
        self, stack, failure, says,
    ):
        """Each dump refusal these tests drive for a deploy refuses here too, with no path."""
        result = stack.run(args=["--dump-only"], **failure)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert says in _flat(result.stdout + result.stderr), _said(result, calls)
        assert stack.dumps == [], _said(result, calls)
        assert list(stack.backup_dir.glob("*.part")) == [], _said(result, calls)
        # The path line a caller reads; the log may name the dump it tried.
        assert not [
            line for line in result.stdout.splitlines()
            if line.startswith(str(stack.backup_dir))
        ], _said(result, calls)

    @pytest.mark.parametrize("env_text", [
        "OTHER=1\n", "SHEKEL_IMAGE_DIGEST=\n", "SHEKEL_IMAGE_DIGEST=latest\n",
        "SHEKEL_IMAGE_DIGEST=sha256:\n", "SHEKEL_IMAGE_DIGEST=sha256:zz\n",
        f"SHEKEL_IMAGE_DIGEST={_OLD}0\n",
    ], ids=["no-pin-line", "empty-pin", "not-a-digest", "bare-prefix", "not-hex",
            "too-long"])
    def test_a_pin_it_cannot_read_dumps_nothing(self, stack, env_text):
        """The dump is named for the current pin, so no readable pin, no dump."""
        stack.env_file.write_text(env_text, encoding="utf-8")
        result = stack.run(args=["--dump-only"])
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert "SHEKEL_IMAGE_DIGEST" in result.stderr, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)
        assert not [c for c in calls if c.startswith("exec")], (
            _said(result, calls)
        )

    @pytest.mark.parametrize("args", [
        ["--dump-only", "--dry-run"],
        ["--dry-run", "--dump-only"],
        ["--dump-only", "--no-verify"],
        ["--dump-only", _NEW],
        [_NEW, "--dump-only"],
        ["--dump-only", "--dump-only"],
    ])
    def test_it_takes_no_other_argument(self, stack, args):
        """Beside a digest, a dry run or a skipped check it would read as one: refused first."""
        result = stack.run(args=args)
        calls = stack.invocations
        assert result.returncode == 2, _said(result, calls)
        assert "--dump-only takes no other argument" in result.stderr, (
            _said(result, calls)
        )
        assert calls == [], _said(result, calls)
        assert stack.dumps == [], _said(result, calls)

    def test_a_deploy_s_dump_is_still_named_for_its_target(self, stack):
        """The shared function keeps the deploy's own name: predeploy, then the TARGET."""
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert len(stack.dumps) == 1, _said(result, calls)
        assert re.fullmatch(
            rf"shekel_prod_predeploy_{_NEW[7:19]}_\d{{8}}_\d{{6}}\.dump",
            stack.dumps[0].name,
        ), stack.dumps[0].name
        _dump_written_as_part(calls, stack.dumps[0], result)


class TestTheMigrationBearingReleaseIsNotRePinned:
    """Finding F-8: for such a release, re-pinning is what cannot work."""

    def test_it_refuses_and_leaves_the_pin_at_the_new_digest(self, stack):
        """A failed migration-bearing deploy does not revert the pin."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "REFUSING to roll back" in output
        assert stack.pin == _NEW, (
            "the script re-pinned the previous digest for a migration-bearing "
            "release -- that is finding F-8: the old image cannot resolve the "
            "revision the database is now stamped at, so it dies too."
        )

    def test_the_refusal_names_the_dump_that_exists(self, stack):
        """The dump path it prints is a file that is actually on disk."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        # **The message renders what the script SAID**, because this assertion
        # firing bare is what cost a 47-minute CI cycle its own diagnosis on
        # 2026-09-03: ``output`` was bound one line above, held the answer, and
        # was not read.  ``shekel-deploy.sh`` refuses with one of three named
        # sentences -- a non-zero ``pg_dump``, a dump that does not decode end
        # to end, or a database container that is not running -- and any of
        # them settles in one line what an ``assert 0 == 1`` cannot.
        #
        # The directory listing is the FOURTH case: both refusals that reach
        # here delete the ``.part`` before dying, so a ``.part`` surviving means
        # a path nobody has enumerated.
        assert len(stack.dumps) == 1, (
            f"no dump artifact was written, so the refusal has nothing to "
            f"name.  The script exited {result.returncode} and said:\n{output}"
            f"\nbackup dir holds: "
            f"{sorted(p.name for p in stack.backup_dir.iterdir())}"
        )
        assert str(stack.dumps[0]) in output, (
            "the refusal does not name the dump it took, so the operator has "
            "nothing to restore from"
        )
        assert "pg_restore" in output, "no restore command was given"
        assert "0002_b.py" in output and "0003_c.py" in output, (
            "the refusal does not name the migrations the release added"
        )

    def test_a_compose_failure_also_refuses(self, stack):
        """Fail closed on the other failure path too.

        ``docker compose up -d`` failing usually means the container never
        started, but it also covers one that started and exited having reached
        the migration step.  The two are indistinguishable from the exit
        status, so neither re-pins.
        """
        result = stack.run(compose_rc="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "REFUSING to roll back" in result.stdout + result.stderr
        assert stack.pin == _NEW


class TestTheOrdinaryRollbackSurvives:
    """R-R14 forbids removing it: 6 of the last 10 releases were pure reverts."""

    def test_a_release_with_no_new_migrations_reverts_the_pin(self, stack):
        """The automatic rollback still runs, and puts the old digest back."""
        result = stack.run(health="unhealthy", **_NO_MIGRATIONS)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "REFUSING to roll back" not in output, (
            "the script refused to roll back a release that adds no "
            "migrations; that rollback works and the ruling keeps it"
        )
        assert "rolling back to" in output
        assert stack.pin == _OLD, (
            "the ordinary rollback did not restore the previous digest"
        )

    def test_a_healthy_deploy_keeps_the_new_pin_and_the_dump(self, stack):
        """The success path is unchanged and the restore point is kept."""
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        assert result.returncode == 0
        assert stack.pin == _NEW
        assert len(stack.dumps) == 1
        assert str(stack.dumps[0]) in result.stdout


class TestThePreflightIsHonest:
    """The classification itself, since every branch above turns on it."""

    def test_it_reports_a_migration_bearing_release(self, stack):
        """The added revisions are named, not merely counted."""
        result = stack.run(health="healthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert "MIGRATION-BEARING release" in output
        assert "0002_b.py" in output and "0003_c.py" in output
        assert "adds these revisions" in output, output
        assert "0001_a.py" not in output.split("adds these revisions")[1][:200]

    def test_dry_run_reports_the_classification_and_changes_nothing(self, stack):
        """``--dry-run`` answers "can this be rolled back?" without deploying."""
        result = stack.run(dry_run=True, health="healthy", **_MIGRATION_BEARING)
        assert result.returncode == 0
        assert "MIGRATION-BEARING release" in result.stdout, (
            "--dry-run does not report whether the release can be rolled back"
        )
        assert stack.pin == _OLD, "--dry-run moved the pin"
        assert stack.dumps == [], "--dry-run took a dump"
        assert not any(
            i.startswith("compose") for i in stack.invocations
        ), "--dry-run started a container"


class TestAStep3RefusalRollsBack:
    """Plan step balance:X-cv: a step-3 refusal re-pins, and its log is kept.

    Entrypoint step 3 is one transaction, so a migration-bearing release whose
    step 3 refuses leaves the stamp where the previous image can resolve
    it.  The script's direction-free question then answers "re-pin" with no
    change of its own -- and re-pinning RECREATES the container, so the failed
    run's log, which names what to repair, is saved beside the dump first.
    """

    def test_a_migration_bearing_release_with_an_unmoved_stamp_re_pins(
        self, stack,
    ):
        """The target added a migration, the stamp never moved: roll back.

        A PIN, not evidence for X-cv: the script's re-pin question is unchanged
        (it passes on the pre-X-cv script too).  What X-cv changed is that a
        step-3 failure now PRODUCES this unmoved stamp; the one-transaction
        tests and the clone rehearsal grade that half.
        """
        result = stack.run(
            health="unhealthy", container_log=_REFUSAL_LINE,
            **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "MIGRATION-BEARING release" in output
        assert "REFUSING to roll back" not in output
        assert stack.pin == _OLD

    def test_the_failed_log_is_saved_beside_the_dump_before_the_re_pin(
        self, stack,
    ):
        """The file holds the refusal, is named, and was read before recreation."""
        result = stack.run(
            health="unhealthy", container_log=_REFUSAL_LINE,
            **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, (
            f"no failed-container log beside the dump.  The script said:\n"
            f"{output}"
        )
        assert len(stack.dumps) == 1, output
        assert saved[0].name == stack.dumps[0].name.replace(
            ".dump", ".failed-container.log",
        )
        assert _REFUSAL_LINE in saved[0].read_text(encoding="utf-8")
        assert str(saved[0]) in output
        # Read BEFORE the re-pin's `compose up` replaced the container.
        calls = stack.invocations
        read_at = _position(calls, "logs probe-app", result)
        compose_ups = _compose_ups(calls, result)
        assert len(compose_ups) == 2, _said(result, calls)
        assert compose_ups[0] < read_at < compose_ups[1], _said(result, calls)

    def test_a_compose_failure_saves_the_log_too(self, stack):
        """The other re-pin path keeps the log as well."""
        result = stack.run(
            compose_rc="1", container_log=_REFUSAL_LINE,
            **_STEP_3_ROLLED_BACK,
        )
        assert result.returncode == 1
        assert stack.pin == _OLD
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1
        assert _REFUSAL_LINE in saved[0].read_text(encoding="utf-8")

    def test_a_log_that_cannot_be_read_does_not_stop_the_rollback(
        self, stack,
    ):
        """No log to save: the re-pin still happens, and says where the log is."""
        result = stack.run(
            health="unhealthy", logs_rc="1", **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        assert stack.pin == _OLD
        assert "could not save probe-app's log" in output
        assert "failed container's log: in Loki only" in output
        assert list(stack.backup_dir.glob("*.failed-container.log")) == []


def _last_stamp_read(
    calls: list[str], result: subprocess.CompletedProcess,
) -> int:
    """Return the index of the last ``alembic_version`` read among *calls*.

    Args:
        calls: The stub's invocation log, in order.
        result: The completed run, rendered if no read was made.

    Returns:
        The position of the latest stamp read: after a failure, the re-read.
    """
    reads = [i for i, call in enumerate(calls) if "alembic_version" in call]
    assert reads, f"the stamp was never read.  {_said(result, calls)}"
    return reads[-1]


def _compose_ups(
    calls: list[str], result: subprocess.CompletedProcess,
) -> list[int]:
    """Return the positions of every ``docker compose up`` among *calls*.

    Every caller expects at least the deploy's own, so an empty list fails
    here with the run's diagnosis rather than as an ``IndexError`` at the
    caller's subscript.

    Args:
        calls: The stub's invocation log, in order.
        result: The completed run, rendered if compose never ran.

    Returns:
        The deploy's own ``compose up`` first, then any re-pin's.
    """
    ups = [i for i, call in enumerate(calls) if call.startswith("compose up")]
    assert ups, f"docker compose up never ran.  {_said(result, calls)}"
    return ups


_FAILURE_PATHS = pytest.mark.parametrize(
    "failure", [{"health": "unhealthy"}, {"compose_rc": "1"}],
    ids=["health-check", "compose"],
)


class TestTheStampIsReadOnceTheTargetIsStopped:
    """Plan step balance:X-cv, rulings R-BAL115 and R-BAL119: the re-read is final.

    A container running the TARGET image may still be inside entrypoint step 3,
    whose one transaction can commit the new stamp after the script has read
    the old one, and the re-pin would then meet finding F-8's second dead
    container.  So on both failure paths that container is stopped first, and
    the ruled order is pinned: stop, read the stamp, save the log, re-pin.  A
    container that does not run the target (absent, or the previous one that
    compose failed before replacing) cannot move the stamp and is left alone.
    """

    @_FAILURE_PATHS
    def test_it_stops_then_reads_then_saves_then_re_pins(self, stack, failure):
        """Both failure paths run the four steps in the ruled order.

        And the re-read asks FOR SHARE, so it waits for a COMMIT the stopped
        container had already sent (the stub cannot model the lock, so this
        pins that the question is asked that way).
        """
        result = stack.run(**failure, **_STEP_3_ROLLED_BACK)
        calls = stack.invocations
        assert stack.pin == _OLD, _said(result, calls)
        stopped_at = _position(calls, "stop probe-app", result)
        saved_at = _position(calls, "logs probe-app", result)
        re_pinned_at = _compose_ups(calls, result)[-1]
        read_at = _last_stamp_read(calls, result)
        assert stopped_at < read_at < saved_at < re_pinned_at, (
            _said(result, calls)
        )
        assert calls[read_at].endswith("FROM public.alembic_version FOR SHARE")

    def test_a_target_whose_id_cannot_be_read_is_still_stopped(self, stack):
        """No target ID to compare with: stop, since downtime is the cheaper error."""
        result = stack.run(
            health="unhealthy", target_image_id="", **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        stopped_at = _position(calls, "stop probe-app", result)
        assert stopped_at < _last_stamp_read(calls, result), (
            _said(result, calls)
        )
        assert stack.pin == _OLD, _said(result, calls)

    def test_the_previous_container_compose_never_replaced_keeps_serving(
        self, stack,
    ):
        """Compose failed before replacing it: it is not stopped, and the decision runs."""
        result = stack.run(
            compose_rc="1", container_image_id=_image_id(_OLD),
            **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        assert "stop probe-app" not in calls, _said(result, calls)
        assert (
            _last_stamp_read(calls, result) > _compose_ups(calls, result)[0]
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    def test_an_absent_container_is_not_stopped(self, stack):
        """No container to stop: the stamp is read and the re-pin runs."""
        result = stack.run(
            compose_rc="1", container_image_id="", **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        assert "stop probe-app" not in calls, _said(result, calls)
        assert (
            _last_stamp_read(calls, result) > _compose_ups(calls, result)[0]
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    @_FAILURE_PATHS
    def test_a_target_container_that_will_not_stop_refuses_the_re_pin(
        self, stack, failure,
    ):
        """Its step 3 may still commit, so no read is final: the pin stays, loudly.

        No stamp is read after the failed stop, and nothing is re-pinned.  The
        refusal names the roll-back through this script, whose pre-flight asks
        the stamp question again once the container is down.
        """
        result = stack.run(stop_rc="1", **failure, **_STEP_3_ROLLED_BACK)
        output = result.stdout + result.stderr
        calls = stack.invocations
        assert result.returncode == 1
        assert "REFUSING to roll back" in output
        assert "could not be stopped" in output
        assert stack.pin == _NEW
        assert (
            _last_stamp_read(calls, result)
            < _position(calls, "stop probe-app", result)
        ), _said(result, calls)
        assert len(_compose_ups(calls, result)) == 1, _said(result, calls)
        assert f"{_DEPLOY_SCRIPT} {_OLD}" in _flat(output)


def _refusal(output: str) -> str:
    """Return what the script printed from its refusal onward.

    Args:
        output: Captured stdout/stderr of a run that refused.

    Returns:
        The text after ``REFUSING to roll back.``: what the operator reads
        once the deploy has failed, apart from the pre-flight above it.
    """
    assert "REFUSING to roll back." in output, output
    return output.split("REFUSING to roll back.", 1)[1]


_NEW_SHORT = _NEW[7:19]


class TestTheRefusalSaysOnlyWhatTheStampShows:
    """Finding BAL-535: the refusal's diagnosis comes from the stamp it re-read.

    It used to say the new image "COMMITTED its migrations" and print the
    PRE-FLIGHT's stamp as the database's state whatever brought it there --
    a re-read that FAILED included, where nothing is known -- and it named
    only a restore that discards data, even for a release that was merely
    slower than the health window, which starting the new pin again recovers
    with nothing lost.
    """

    def test_an_unreadable_stamp_claims_no_commit_and_prints_no_stale_stamp(
        self, stack,
    ):
        """The re-read fails: nothing about a commit is claimed, no stamp is printed.

        The pin stays and the way back is this script's own pre-flight, which
        reads the stamp again before writing anything.
        """
        result = stack.run(
            health="unhealthy", stamp_reread_rc="1", **_MIGRATION_BEARING,
        )
        output = result.stdout + result.stderr
        refusal = _refusal(output)
        assert result.returncode == 1, output
        assert stack.pin == _NEW
        calls = stack.invocations
        assert len(_compose_ups(calls, result)) == 1, _said(result, calls)
        assert "The stamp could not be re-read after the failure" in refusal
        assert "UNKNOWN" in refusal
        assert "COMMITTED" not in refusal
        assert "stamped at" not in refusal
        assert f"{_DEPLOY_SCRIPT} {_OLD}" in _flat(refusal)

    def test_a_stamp_the_target_resolves_names_starting_it_again(self, stack):
        """Step 3 committed a stamp the new image wrote: restart first, restore second."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        refusal = _refusal(output)
        assert result.returncode == 1, output
        assert stack.pin == _NEW
        assert "Database is now stamped at: 0003" in refusal
        assert f"{_NEW_SHORT}'s step 3 COMMITTED its" in refusal
        restart = f"cd {stack.shekel_dir} && docker compose up -d app"
        assert restart in refusal
        assert "It resolves this stamp, so" in refusal
        assert "pg_restore" in refusal, output
        assert refusal.index(restart) < refusal.index("pg_restore")

    def test_a_stamp_neither_image_resolves_names_only_the_restore(self, stack):
        """A stamp the new image cannot resolve either: no commit claimed, no restart offered.

        Its step 3 cannot have written a revision it has no script for, so
        something other than this deploy moved the stamp.
        """
        result = stack.run(
            health="unhealthy",
            **{**_MIGRATION_BEARING, "stamped_after": "0009"},
        )
        output = result.stdout + result.stderr
        refusal = _refusal(output)
        assert result.returncode == 1, output
        assert "Database is now stamped at: 0009" in refusal
        assert "cannot resolve that revision either" in refusal
        assert "COMMITTED" not in refusal, (
            "a stamp the new image cannot resolve was not written by its step 3"
        )
        assert "It resolves this stamp" not in refusal
        assert f"start {_NEW_SHORT} again" not in refusal
        assert "pg_restore" in refusal

    def test_the_refusal_saves_the_failed_log_and_names_it(self, stack):
        """The log is kept beside the dump, since the restore recreates the container."""
        result = stack.run(
            health="unhealthy", container_log=_REFUSAL_LINE,
            **_MIGRATION_BEARING,
        )
        output = result.stdout + result.stderr
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, output
        assert _REFUSAL_LINE in saved[0].read_text(encoding="utf-8")
        assert str(saved[0]) in _refusal(output)


#: Filler that outgrows a pipe, C-sorted after every fixture revision (the
#: script's ``comm`` needs C-sorted listings): 65,536 lines, 1,245,184 bytes.
#: Measured 2026-10-09 (GNU grep 3.12, Linux 7.2, 4 KiB pages): before a
#: ``grep -q`` matching line 1 exited, a writer filling its pipe in 4 KiB
#: writes got at most 131,072 bytes in, over 300 trials.  This filler is over
#: nine times that, so the writer is still writing when grep leaves.
_PAST_A_PIPE = "".join(f"f{i:07d}_filler.py\n" for i in range(65536))


class TestAListingLargerThanAPipeIsReadWhole:
    """Finding BAL-618, plan step balance:X-dm: a listed revision read as absent.

    ``image_resolves_revisions`` used to ask ``printf '%s\\n' "$listing" |
    grep -q "^${rev}_"`` under ``pipefail``.  ``grep -q`` exits at its first
    match, and printf, if it was still writing, died of SIGPIPE: status 141,
    so a revision on an early line read as ABSENT.  On a fixture of a few
    lines that was a race a loaded CI runner lost now and then.  Findings
    BAL-509 and N-408 recorded this refusal in CI; BAL-618 and N-510 lost
    their output, and their failures match it.  Holding printf between its
    writes reproduced all four on demand.  On a listing larger than the pipe
    plus grep's one read it is no race: printf is still writing when grep
    leaves, every run.  One test per caller, since each asks a safety
    question of its own.
    """

    def test_the_pre_flight_resolves_a_stamp_on_line_one(self, stack):
        """The deploy is not refused up front for a revision the target lists."""
        listing = "0001_a.py\n" + _PAST_A_PIPE
        result = stack.run(
            health="healthy", old_migrations=listing,
            new_migrations=listing, stamped="0001",
        )
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)

    def test_the_re_pin_decision_resolves_an_unmoved_stamp(self, stack):
        """A failed release that moved no stamp still rolls back on its own."""
        listing = "0001_a.py\n0002_b.py\n" + _PAST_A_PIPE
        result = stack.run(
            health="unhealthy", old_migrations=listing,
            new_migrations=listing, stamped="0002",
        )
        output = result.stdout + result.stderr
        calls = stack.invocations
        assert "REFUSING to roll back" not in output, _said(result, calls)
        assert "rolling back to" in output, _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    def test_the_refusal_resolves_the_stamp_the_target_committed(self, stack):
        """The refusal offers starting the target again, not only the restore."""
        result = stack.run(
            health="unhealthy",
            old_migrations="0001_a.py\n" + _PAST_A_PIPE,
            new_migrations="0001_a.py\n0002_b.py\n0003_c.py\n" + _PAST_A_PIPE,
            stamped="0001", stamped_after="0003",
        )
        refusal = _refusal(result.stdout + result.stderr)
        calls = stack.invocations
        assert stack.pin == _NEW, _said(result, calls)
        assert f"{_NEW_SHORT}'s step 3 COMMITTED its" in refusal, (
            _said(result, calls)
        )
        assert "cannot resolve that revision either" not in refusal, (
            _said(result, calls)
        )


class TestTheLatestTagResolvesToItsFirstDigest:
    """The ``:latest`` path, where no digest is given: the first RepoDigest.

    Plan step balance:X-dm.  It read ``docker image inspect ... | grep -oE
    'sha256:...' | head -1`` under pipefail, and no test drove it, since
    every other test passes a digest.  Once ``head`` had its line, ``grep``
    could die writing the rest, and ``set -e`` then ended the run with no
    message of the script's own; a failed inspect, or an answer holding no
    digest, ended it the same way, so the refusal written for the second
    case never ran.
    """

    def test_the_latest_tag_deploys_its_digest(self, stack):
        """Pulled, resolved to its digest, and pinned."""
        result = stack.run(pull_latest=True, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert "pull -q ghcr.io/saltyreformed-labs/shekel:latest" in calls, (
            _said(result, calls)
        )

    def test_a_digest_list_larger_than_a_pipe_still_resolves(self, stack):
        """The FIRST digest is taken, however long the list after it.

        Every later line holds a digest too, so ``grep -oE`` had far more to
        write than a pipe holds once ``head -1`` had left: the replaced
        spelling failed this every run.
        """
        mirrors = "".join(
            f"registry.example/mirror{i}@sha256:{i:064x}\n"
            for i in range(20000)
        )
        result = stack.run(
            pull_latest=True, health="healthy",
            repo_digests=f"ghcr.io/saltyreformed-labs/shekel@{_NEW}\n" + mirrors,
            **_NO_MIGRATIONS,
        )
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)

    def test_a_failed_inspect_is_refused_in_the_script_s_own_words(self, stack):
        """Before the dump, the pin and any container."""
        result = stack.run(
            pull_latest=True, image_inspect_rc="1", **_NO_MIGRATIONS,
        )
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert (
            "docker image inspect ghcr.io/saltyreformed-labs/shekel:latest failed"
            in result.stdout + result.stderr
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)

    def test_an_answer_with_no_digest_is_refused_in_the_script_s_own_words(
        self, stack,
    ):
        """The refusal that ``set -e`` used to pre-empt now runs."""
        result = stack.run(pull_latest=True, repo_digests="", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert (
            "could not resolve digest for ghcr.io/saltyreformed-labs/shekel:latest"
            in result.stdout + result.stderr
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)


#: The image-build workflow of the repository's home since the move to
#: saltyreformed-labs (ruling R-BAL173); a SAN is this, ``@``, and the ref built.
_BUILT_BY = (
    "https://github.com/saltyreformed-labs/Shekel/.github/workflows/"
    "docker-publish.yml"
)


class TestOnlyMainsBuildInTheNewHomeVerifies:
    """The one signing identity ``cosign verify`` is handed (card L5).

    Every other test here passes ``--no-verify`` or stops before the verify,
    so no test drove the verify call, and the identity regexp it hands cosign
    was a literal nothing read.  cosign matches that regexp ANYWHERE in the
    certificate's SAN, so the unanchored form it replaced accepted a build of
    any ref.  A stub ``cosign`` records what it was handed, and the regexp is
    graded with ``re.search``, the search cosign makes; Go's engine and
    Python's agree on every SAN below (none ends in a newline, the one place
    their ``$`` differ).
    """

    def test_the_regexp_admits_main_s_build_and_refuses_every_other(
        self, stack,
    ):
        """One verify, of the target, accepting only the new home's main."""
        cosign_log = stack.root / "cosign.log"
        cosign = stack.bin_dir / "cosign"
        cosign.write_text(
            "#!/usr/bin/env bash\n"
            f"(IFS=$'\\t'; printf '%s\\n' \"$*\") >>'{cosign_log}'\n",
            encoding="utf-8",
        )
        cosign.chmod(0o755)
        result = stack.run(args=["--dry-run", _NEW], **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        verifies = [
            line.split("\t")
            for line in cosign_log.read_text(encoding="utf-8").splitlines()
        ]
        assert len(verifies) == 1, (verifies, _said(result, calls))
        verify = verifies[0]
        assert verify[0] == "verify" and verify[-1] == (
            f"ghcr.io/saltyreformed-labs/shekel@{_NEW}"
        ), verify
        regexp = verify[verify.index("--certificate-identity-regexp") + 1]
        cases = {
            f"{_BUILT_BY}@refs/heads/main": True,
            # Signed before the move: no second identity is accepted.
            "https://github.com/SaltyReformed/Shekel/.github/workflows/"
            "docker-publish.yml@refs/heads/main": False,
            f"{_BUILT_BY}@refs/heads/dev": False,
            f"{_BUILT_BY}@refs/tags/v1": False,
            # Case exact.
            f"{_BUILT_BY}@refs/heads/main".replace("/Shekel/", "/shekel/"): False,
            # Anchored at the start, then at the end.
            f"https://example.com/?{_BUILT_BY}@refs/heads/main": False,
            f"{_BUILT_BY}@refs/heads/main-x": False,
        }
        assert {
            san: re.search(regexp, san) is not None for san in cases
        } == cases, regexp


class TestTheRunningCheckIsReadFromTheScriptsCheckout:
    """Ruling R-BAL254: one running-check, found beside the script's checkout.

    ``scripts/_container_lib.sh`` holds it for this script and the backup
    scripts alike.  Production runs ``/usr/local/bin/shekel-deploy``, a
    chain of symlinks into the repository, so the script finds the helper
    through ``readlink -f`` of itself.  Every other test here runs the
    repository's copy in place, which is that path's end; these pin the
    chain that starts it, and what a copy without the helper does.
    """

    def test_production_s_two_symlinks_find_the_helper(self, stack, tmp_path):
        """Run through two symlinks, as production does, it deploys.

        ``/usr/local/bin/shekel-deploy`` links to
        ``/opt/docker/scripts/shekel-deploy.sh``, which links into the
        repository: a script resolving only one hop would look for the
        helper beside ``/opt/docker/scripts`` and stop.
        """
        hop = tmp_path / "opt-docker-scripts" / "shekel-deploy.sh"
        hop.parent.mkdir()
        hop.symlink_to(_DEPLOY_SCRIPT)
        link = tmp_path / "usr-local-bin" / "shekel-deploy"
        link.parent.mkdir()
        link.symlink_to(hop)
        result = stack.run(script=link, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert f"running: {_DEPLOY_SCRIPT}\n" in result.stdout, (
            f"the startup report did not name the file the chain resolves "
            f"to.  {_said(result, calls)}"
        )

    def test_a_copy_without_the_helper_stops_before_touching_anything(
        self, stack, tmp_path,
    ):
        """No helper beside it: it refuses to start, naming itself and the file.

        The ruling's words: it "refuses to start, before it touches anything".
        Nothing runs first -- not the startup report, not docker -- so the
        refusal itself names the copy that is running.
        """
        copy = tmp_path / "hand-copy" / "deploy" / "shekel-deploy.sh"
        copy.parent.mkdir(parents=True)
        shutil.copy2(_DEPLOY_SCRIPT, copy)
        result = stack.run(script=copy, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert result.stdout == "", _said(result, calls)
        assert (
            f"ERROR: {copy} cannot load "
            f"{copy.parent}/../scripts/_container_lib.sh" in result.stderr
        ), _said(result, calls)
        assert calls == [], (
            f"the script ran docker before stopping.  {_said(result, calls)}"
        )
        assert stack.pin == _OLD, _said(result, calls)

    def test_a_copy_that_carries_the_helper_deploys(self, stack, tmp_path):
        """The control: the same copy, with the helper laid out beside it."""
        copy = tmp_path / "carried" / "deploy" / "shekel-deploy.sh"
        copy.parent.mkdir(parents=True)
        shutil.copy2(_DEPLOY_SCRIPT, copy)
        helper = copy.parents[1] / "scripts" / "_container_lib.sh"
        helper.parent.mkdir()
        shutil.copy2(_REPO_ROOT / "scripts" / "_container_lib.sh", helper)
        result = stack.run(script=copy, health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)


class TestTheDowngradeCaseTheOldDesignCalledSAFE:
    """A target OLDER than the database adds nothing, and cannot boot.

    The defect adversarial review found in the first implementation of R-F8.
    It classified releases by "does the target ADD migrations", which is
    directional: a rollback deploy adds nothing, was therefore called safe,
    took the ordinary path, and produced the two dead containers F-8 is about.
    The question asked now -- "can the target resolve the revision the
    database is stamped at" -- has no direction and catches this before
    anything is written.
    """

    def test_a_target_older_than_the_database_is_refused_up_front(self, stack):
        """It aborts before the dump, the pin, and any container."""
        result = stack.run(health="healthy", **_DOWNGRADE)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert (
            "cannot resolve the revision this database is stamped at"
            in _flat(output)
        )
        assert stack.pin == _OLD, "the pin moved for an unbootable target"
        assert stack.dumps == [], "a dump was taken for a refused deploy"
        assert not any(i.startswith("compose") for i in stack.invocations), (
            "a container was started with an image that cannot migrate"
        )

    def test_the_refusal_says_how_a_real_downgrade_is_done(self, stack):
        """It names restore-first rather than leaving the operator stuck."""
        result = stack.run(health="healthy", **_DOWNGRADE)
        output = result.stdout + result.stderr
        assert (
            "restoring a dump taken at or before that revision FIRST"
            in _flat(output)
        )


class TestABrokenProbeIsNeverReadAsSafe:
    """An empty migration listing is a broken probe, not "no migrations".

    ``ls`` in a renamed directory, a changed WORKDIR, a missing ``xargs`` --
    each yields an empty list, and an empty list used to classify the release
    as safe to roll back, silently disabling the whole check.
    """

    def test_an_empty_listing_aborts_rather_than_assuming(self, stack):
        """Zero migrations from an image is refused, loudly."""
        result = stack.run(
            old_migrations="", new_migrations="", stamped="0001",
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "ZERO migrations" in output
        assert stack.pin == _OLD
        assert stack.dumps == []


class TestInitDatabaseStillRaises:
    """R-R14's other prohibition, tested by behaviour rather than by shape.

    The earlier source-level version walked the AST for a ``Try`` containing
    the ``command.upgrade`` call.  Review found five wrappings it missed --
    ``except*`` (which parses to ``ast.TryStar``), ``contextlib.suppress``, a
    decorator, a nested function, and a try at the CALL site -- and two
    harmless shapes it wrongly rejected (``try/finally`` with no handler, and
    ``try/except: raise``).  Asking whether the exception actually escapes has
    none of those gaps.
    """

    def test_an_upgrade_failure_propagates_out_of_migrate(self, monkeypatch):
        """``migrate_existing_database`` does not swallow an Alembic error.

        This is the failure mode F-8 turns on: an unresolvable revision must
        abort the deploy loudly rather than let the app boot against a schema
        it cannot describe.
        """
        # Pylint: ``import-outside-toplevel`` -- the module is loaded by path
        # (scripts/ is not a package), and only this test needs it.
        # pylint: disable=import-outside-toplevel
        from app import migration_runner
        from tests._test_helpers import load_init_database_module

        module = load_init_database_module()

        def _boom(*_args, **_kwargs):
            """Stand in for a revision Alembic cannot locate."""
            raise RuntimeError("Can't locate revision identified by 'deadbeef'")

        # Alembic's own ``upgrade``, which the one migration runner calls, so
        # the error starts where a real one would.  It raises before the
        # config's connection is used, so a stand-in that is already inside a
        # transaction (the one question the runner asks of it) takes its place.
        monkeypatch.setattr(migration_runner.command, "upgrade", _boom)
        in_a_transaction = mock.Mock(**{"in_transaction.return_value": True})
        with pytest.raises(RuntimeError, match="Can't locate revision"):
            module.migrate_existing_database(in_a_transaction)


def _the_script_s_stamp_read() -> str:
    """Return the SQL ``db_stamped_revisions`` sends, read from the script itself.

    Returns:
        The one ``SELECT ... public.alembic_version ...`` statement the script
        holds, exactly as ``docker exec psql -c`` would send it.
    """
    found = re.findall(
        r'"(SELECT version_num FROM public\.alembic_version[^"]*)"',
        _DEPLOY_SCRIPT.read_text(encoding="utf-8"),
    )
    assert len(found) == 1, found
    return found[0]


class TestTheStampReadWaitsForAStep3StillEnding:
    """Plan step balance:X-cv, ruling R-BAL115: the re-read's SQL, on real PostgreSQL.

    The stub above can pin only how the stamp question is SPELLED.  This runs
    the script's own statement against the test database while another
    connection holds an uncommitted stamp UPDATE -- the state a stopped
    container's entrypoint step 3 leaves when its COMMIT is still on the
    wire -- and measures that the read WAITS for it (PostgreSQL reports the
    reader blocked on a lock) and then returns the COMMITTED stamp, not the
    one it would have read past.
    """

    def test_the_read_waits_then_returns_what_step_3_committed(self, app, db):
        """Blocked while the UPDATE is open; the new stamp once it commits."""
        engine = db.engine
        sql = _the_script_s_stamp_read()
        read = {}

        def _read_the_stamp():
            """Run the script's read on a connection of its own."""
            with engine.connect() as reader:
                read["stamp"] = reader.execute(text(sql)).scalar_one()

        with engine.connect() as step_3, engine.connect() as watcher:
            step_3.execute(text(
                "UPDATE public.alembic_version SET version_num = 'xcvwaits0001'"
            ))
            reader_thread = threading.Thread(target=_read_the_stamp)
            reader_thread.start()
            deadline = time.monotonic() + 10
            while not watcher.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "  AND pid <> pg_backend_pid() AND wait_event_type = 'Lock' "
                "  AND query LIKE '%public.alembic_version%'"
            )).scalar_one():
                watcher.rollback()
                assert time.monotonic() < deadline, (
                    f"the stamp read never waited on step 3's lock; it read "
                    f"{read.get('stamp')!r} past it"
                )
                time.sleep(0.05)
            assert "stamp" not in read
            step_3.commit()
            reader_thread.join(timeout=10)

        assert not reader_thread.is_alive()
        assert read["stamp"] == "xcvwaits0001"
