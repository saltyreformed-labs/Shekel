"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_deploy/test_shekel_deploy_behaviour.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import subprocess
import textwrap

import pytest

from tests._repo_root import REPO_ROOT


_DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "shekel-deploy.sh"


#: The files the script sources from beside itself, every ``deploy/_*.sh``: with
#: :data:`_DEPLOY_SCRIPT`, the one script's text.
_DEPLOY_PARTS = tuple(sorted((REPO_ROOT / "deploy").glob("_*.sh")))


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
            # Whether the previous image holds the migrate service's
            # entrypoint (FAKE_OLD_LAYOUT_RC: 0 it does, 1 it does not, else
            # docker failing); the target always does.
            case "$*" in
            *owner_entrypoint*)
                case "$*" in
                *{_OLD[7:19]}*) exit "$FAKE_OLD_LAYOUT_RC" ;;
                esac
                exit 0
                ;;
            esac
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
                    # The migrate container's running-check: whether a
                    # deploy is still running there.
                    if [ "${{@: -1}}" = "$FAKE_MIGRATE_CONTAINER" ]; then
                        printf '%s\\n' "$FAKE_MIGRATE_RUNNING"
                        exit 0
                    fi
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
            # refuses, and FAKE_MIGRATE_STOP_RC one that refuses the migrate
            # container.
            [ "$2" = "$FAKE_MIGRATE_CONTAINER" ] && exit "$FAKE_MIGRATE_STOP_RC"
            exit "$FAKE_STOP_RC"
            ;;
        exec)
            for arg in "$@"; do
                case "$arg" in
                psql)
                    # public.alembic_version.  The stamp ADVANCES once the
                    # deploy has run, which is what a migration at the
                    # deploy's step 3 does and what makes the post-failure re-read
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
            # The failed containers' logs, which the script saves beside the
            # dump before a re-pin recreates them (plan step balance:X-cv).
            # FAKE_LOGS_RC and FAKE_MIGRATE_LOGS_RC model a container with
            # none.
            if [ "$2" = "$FAKE_MIGRATE_CONTAINER" ]; then
                [ "$FAKE_MIGRATE_LOGS_RC" != "0" ] && exit "$FAKE_MIGRATE_LOGS_RC"
                printf '%s\\n' "$FAKE_MIGRATE_LOG"
                exit 0
            fi
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
            migrate_running: str = "false",
            migrate_stop_rc: str = "0",
            migrate_log: str = "fake migrate log",
            migrate_logs_rc: str | None = None,
            old_layout_rc: str = "0",
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
            migrate_running: Whether the migrate container is still running
                the deploy (``true``/``false``).
            migrate_stop_rc: Exit status for ``docker stop`` of the migrate
                container.
            migrate_log: What ``docker logs`` prints for the migrate container.
            migrate_logs_rc: Exit status for that ``docker logs``; defaults
                to *logs_rc*, so a run whose logs fail fails both.
            old_layout_rc: Exit status of the probe for the migrate service's
                entrypoint in the CURRENT image: ``0`` it holds it, ``1`` it
                does not, anything else docker failing.
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
            "SHEKEL_MIGRATE_CONTAINER": "probe-migrate",
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
            "FAKE_MIGRATE_CONTAINER": "probe-migrate",
            "FAKE_MIGRATE_RUNNING": migrate_running,
            "FAKE_MIGRATE_STOP_RC": migrate_stop_rc,
            "FAKE_MIGRATE_LOG": migrate_log,
            "FAKE_MIGRATE_LOGS_RC": (
                logs_rc if migrate_logs_rc is None else migrate_logs_rc
            ),
            "FAKE_OLD_LAYOUT_RC": old_layout_rc,
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


def _as_read(text: str) -> str:
    """Return *text* as the operator reads it: one line of prose.

    Every line the script logs carries its ``[shekel-deploy]`` prefix, so a
    sentence wrapped across two lines has one inside it; the prefix is dropped
    before the wrapping is joined.

    Args:
        text: Captured stdout/stderr.

    Returns:
        The text without its prefixes, every run of whitespace one space.
    """
    return _flat(text.replace("[shekel-deploy] ", ""))


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


#: A migration-bearing release whose deploy's step 3 ROLLED BACK (plan step
#: balance:X-cv): the target adds revisions, but its migrations and deploy
#: hooks are one transaction, so a refusal there leaves the stamp unmoved.
_STEP_3_ROLLED_BACK = {
    "old_migrations": "0001_a.py\n",
    "new_migrations": "0001_a.py\n0002_b.py\n",
    "stamped": "0001",
    "stamped_after": "0001",
}


#: What ``init_database.py`` prints when a hook refuses: the operator's pointer
#: to the repair, which only the failed container's log carries.
_REFUSAL_LINE = "Cash posting resync refused (ruling R-BAL104): transfer(s) [7]"


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


#: Filler that outgrows a pipe, C-sorted after every fixture revision (the
#: script's ``comm`` needs C-sorted listings): 65,536 lines, 1,245,184 bytes.
#: Measured 2026-10-09 (GNU grep 3.12, Linux 7.2, 4 KiB pages): before a
#: ``grep -q`` matching line 1 exited, a writer filling its pipe in 4 KiB
#: writes got at most 131,072 bytes in, over 300 trials.  This filler is over
#: nine times that, so the writer is still writing when grep leaves.
_PAST_A_PIPE = "".join(f"f{i:07d}_filler.py\n" for i in range(65536))


#: The image-build workflow of the repository's home since the move to
#: saltyreformed-labs (ruling R-BAL173); a SAN is this, ``@``, and the ref built.
_BUILT_BY = (
    "https://github.com/saltyreformed-labs/Shekel/.github/workflows/"
    "docker-publish.yml"
)


def _the_script_s_stamp_read() -> str:
    """Return the SQL ``db_stamped_revisions`` sends, read from the script itself.

    Returns:
        The one ``SELECT ... public.alembic_version ...`` statement the script
        holds, in any of its files, exactly as ``docker exec psql -c`` would
        send it.
    """
    found = re.findall(
        r'"(SELECT version_num FROM public\.alembic_version[^"]*)"',
        "".join(part.read_text(encoding="utf-8")
                for part in (_DEPLOY_SCRIPT, *_DEPLOY_PARTS)),
    )
    assert len(found) == 1, found
    return found[0]
