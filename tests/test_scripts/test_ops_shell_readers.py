"""The operations scripts' swept readers keep their meaning without a pipe.

Plan step **balance:X-dm**, rulings **R-BAL252** (sweep every early-exiting
reader under ``pipefail``) and **R-BAL254** (the running-check has ONE home).
``deploy/shekel-deploy.sh``'s migration check read a listed revision as
absent because ``grep -q`` left its pipe while ``printf`` was still writing
(finding BAL-618).  The same shape sat in the scripts graded here, none with
a test until now:

* ``scripts/_container_lib.sh``'s ``container_running``, the shell scripts'
  one home of "is this container running?", sourced by
  ``scripts/_backup_lib.sh`` and by the deploy script (whose own module
  grades its side);
* ``scripts/restore.sh``'s ``start_app``, whose wait for the app container
  asks it through that same check;
* ``scripts/reconcile_prod_to_canonical.sh``'s ``extract_env_value`` and
  ``compose_image_digest``.

The restore script is graded further here, for task plan#3609 (ruling
plan#3534): it hands the restored database to the owner login with the
database container's provisioning script, and starts the app the way docker
compose made it, read from the app container's own labels, so the deploy
runs before the app serves; its exit trap says where a failure left the
restore.

**Neither the reconcile script nor the restore script is run here.**  One
writes production's ``/opt/docker/shekel`` (a hardcoded ``PROD_ROOT``), the
other drops a database, so each test reads functions' text out of its script
and runs that, against a temporary tree or a stub ``docker``.  The restore's
whole sequence is graded the same way: its configuration block, every
function and its exit trap, read from its text, run ``main`` with probe
container names, and a stubbed run's ``DOCKER_HOST`` names a socket that
does not exist, so a ``docker`` that is not the stub reaches no daemon.  The
expected values are worked by hand from what the old spellings printed.  The
reconcile pipelines keep grep, now ending in a reader that drains, and both
carried ``|| true``, so their race only ever cost a status nobody read: these
tests grade the meaning, not a race.
"""
from __future__ import annotations

import gzip
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.test_scripts._shell import (
    docker_stub,
    every_function_text,
    function_text,
    run_bash,
    section_text,
    trap_line,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER_LIB = _REPO_ROOT / "scripts" / "_container_lib.sh"
_BACKUP_LIB = _REPO_ROOT / "scripts" / "_backup_lib.sh"
_RECONCILE = _REPO_ROOT / "scripts" / "reconcile_prod_to_canonical.sh"
_RESTORE = _REPO_ROOT / "scripts" / "restore.sh"

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _fake_docker(bin_dir: Path, *, stdout: str, status: int) -> Path:
    """Install a ``docker`` stub that prints *stdout* and exits *status*.

    Every invocation's arguments are appended to ``docker.log`` beside it.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        stdout: What the stub prints, verbatim.
        status: The stub's exit status.

    Returns:
        The invocation log's path.
    """
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "docker.log"
    answer = bin_dir / "docker.out"
    answer.write_text(stdout, encoding="utf-8")
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{log}'
        cat '{answer}'
        exit {status}
        """), encoding="utf-8")
    stub.chmod(0o755)
    return log


class TestTheRunningCheckHasOneHome:
    """Ruling R-BAL254: ``container_running`` answers for every caller."""

    @pytest.mark.parametrize(
        ("answer", "status", "expected"),
        [
            ("true\n", 0, 0),
            ("false\n", 0, 1),
            ("", 1, 1),
        ],
        ids=["running", "stopped", "docker-cannot-find-it"],
    )
    def test_it_answers_what_docker_reports(
        self, tmp_path, answer, status, expected,
    ):
        """Running is 0; stopped, or unknown to docker, is 1."""
        log = _fake_docker(tmp_path / "bin", stdout=answer, status=status)
        result = run_bash(
            'source "$1"; container_running "$2" || exit "$?"',
            str(_CONTAINER_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == expected, result.stderr
        assert log.read_text(encoding="utf-8").splitlines() == [
            "inspect --format {{.State.Running}} probe-db",
        ]

    def test_the_backup_helper_asks_through_it(self, tmp_path):
        """``require_db_container`` refuses a stopped container, by name."""
        _fake_docker(tmp_path / "bin", stdout="false\n", status=0)
        result = run_bash(
            'source "$1"; require_db_container "$2" || exit "$?"',
            str(_BACKUP_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Database container 'probe-db' is not running" in result.stdout

    def test_the_backup_helper_passes_a_running_container(self, tmp_path):
        """And lets a running one through, saying nothing."""
        _fake_docker(tmp_path / "bin", stdout="true\n", status=0)
        result = run_bash(
            'source "$1"; require_db_container "$2" || exit "$?"',
            str(_BACKUP_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == ""


#: The compose labels the stub's app container carries, as ``docker compose``
#: stamps them: its project, directory, files and service.
_LABELS = {
    "project": "probe-project",
    "project.working_dir": "/stacks/probe",
    "project.config_files": (
        "/stacks/probe/docker-compose.yml,"
        "/stacks/probe/docker-compose.override.yml"
    ),
    "service": "app",
}

#: The start ``restore.sh`` runs for :data:`_LABELS`, as the stub logs it.
_START = (
    "compose --project-name probe-project --project-directory /stacks/probe"
    " --file /stacks/probe/docker-compose.yml"
    " --file /stacks/probe/docker-compose.override.yml up --pull never -d app"
)

#: The database container's provisioning script, where the compose files
#: mount it (``deploy/postgres/provision_roles.sh``).
_PROVISION_ROLES = "/docker-entrypoint-initdb.d/10-provision-roles.sh"

#: The prerequisite check of that script, as the stub logs it.
_HOOK_CHECK = (
    'exec probe-db sh -c test -f "$1" && test -x "$1" sh ' + _PROVISION_ROLES
)

#: The hand-over, as the stub logs it, for the default database name.
_HAND_OVER = f"exec --env POSTGRES_DB=shekel probe-db {_PROVISION_ROLES}"


def _restore_stub(bin_dir: Path, *, health: str | None = None,
                  running: str = "true\n", status: int = 0,
                  app_exists: bool = True,
                  labels: dict[str, str] | None = None,
                  failing: frozenset[str] = frozenset()) -> Path:
    """Install the ``docker`` and ``sleep`` stubs the restore meets.

    The app container exists (or, for ``app_exists=False``, docker cannot
    find it) and carries *labels* (:data:`_LABELS` by default; a key left
    out is a label it lacks, which docker prints as empty).  Its healthcheck
    reports *health*, or, for ``None``, it has none (that inspect fails, so
    ``start_app`` reads ``none``).  The running-check is answered with
    *running* and *status*.  Each step named in *failing* exits 1:
    ``hook-check`` (the provisioning script's prerequisite check),
    ``config`` (the compose project's parse), ``restore`` (the restore's
    ``psql``), ``hand-over`` and ``up``.  Every other call exits 0 and
    prints nothing.  ``sleep`` returns at once, so thirty retries cost
    nothing.  The logs and the labels are :func:`docker_stub`'s.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        health: What the ``{{.State.Health.Status}}`` inspect prints.
        running: What the ``{{.State.Running}}`` inspect prints.
        status: That inspect's exit status.
        app_exists: Whether docker finds the app container.
        labels: The app container's compose labels, by key.
        failing: The steps whose call exits 1.

    Returns:
        The invocation log's path.
    """
    (bin_dir.parent / "health.out").write_text(health or "", encoding="utf-8")
    (bin_dir.parent / "running.out").write_text(running, encoding="utf-8")
    health_arm = (
        "exit 1" if health is None
        else f"cat '{bin_dir.parent}/health.out'; exit 0"
    )

    def outcome(step: str) -> str:
        """The stub's exit for *step*'s call."""
        return "exit 1" if step in failing else "exit 0"

    return docker_stub(bin_dir, labels=_LABELS if labels is None else labels, arms=f"""
        *State.Health.Status*) {health_arm} ;;
        *State.Running*) cat '{bin_dir.parent}/running.out'; exit {status} ;;
        "inspect probe-app") {"exit 0" if app_exists else "exit 1"} ;;
        "exec probe-db sh -c "*) {outcome("hook-check")} ;;
        "exec --env "*) {outcome("hand-over")} ;;
        "exec -i "*) cat >/dev/null; {outcome("restore")} ;;
        "compose "*" config --quiet") {outcome("config")} ;;
        "compose "*" up "*) {outcome("up")} ;;
        """)


#: The restore's configuration block, read from its text: the probe names a
#: program sets before it keep their values, as an exported one would.
_RESTORE_CONFIG = section_text(
    _RESTORE, "# ── Configuration", "# ── Functions",
)


def _restore_functions(tmp_path: Path, program: str, *args: str,
                       **stub) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run *program* with the restore's configuration and functions defined.

    The probe containers are ``probe-db`` and ``probe-app``; the stub is
    :func:`_restore_stub`'s, given *stub*.

    Args:
        tmp_path: pytest's per-test temporary directory.
        program: Bash source run after the definitions, its inputs ``$1`` ...
        args: Its positional parameters.
        **stub: :func:`_restore_stub`'s keyword arguments.

    Returns:
        The completed process and the docker stub's invocation log, a line
        per call.
    """
    log = _restore_stub(tmp_path / "bin", **stub)
    result = run_bash(
        'source "$1"; DB_CONTAINER=probe-db; APP_CONTAINER=probe-app; '
        'eval "$2"; eval "$3"; shift 3\n' + program,
        str(_BACKUP_LIB), _RESTORE_CONFIG, every_function_text(_RESTORE),
        *args, path_prefix=tmp_path / "bin",
    )
    return result, log.read_text(encoding="utf-8").splitlines()


class TestRestoreWaitsForTheAppThroughTheSharedCheck:
    """``scripts/restore.sh``'s ``start_app`` asks ``container_running``.

    Ruling R-BAL254's one home: the wait for an app container with no
    healthcheck spelled its own ``docker inspect``, compared to ``true``.
    The recorder tests put a RECORDER in ``container_running``'s place: the
    first sees ``start_app`` ask it (a spelling of its own would leave the
    recorder unasked and send docker the question instead), the second that
    a healthcheck still starting asks it nothing.
    """

    #: Stands in for ``container_running`` once the real one is sourced:
    #: records each question and answers with ``$RECORDED_ANSWER``.
    _RECORDER = textwrap.dedent("""\
        container_running() {
            printf 'asked %s\\n' "$1" >>"$RECORDER_LOG"
            return "$RECORDED_ANSWER"
        }
        """)

    def _start_app(self, tmp_path: Path, *, health: str | None = None,
                   running: str = "true\n", status: int = 0,
                   recorded_answer: int | None = None,
                   ) -> tuple[subprocess.CompletedProcess, str, str]:
        """Run ``start_app`` from its own text, as ``main`` calls it.

        ``read_app_stack`` runs first, as ``check_prerequisites`` runs it,
        and reads the stub's labels.

        Args:
            tmp_path: pytest's per-test temporary directory.
            health: The healthcheck's report; ``None`` for no healthcheck.
            running: What the running-check's inspect prints.
            status: That inspect's exit status.
            recorded_answer: When given, ``container_running`` is replaced
                by the recorder, answering with this status.

        Returns:
            The completed process, the docker stub's invocation log and the
            recorder's log.
        """
        log = _restore_stub(
            tmp_path / "bin", health=health, running=running, status=status,
        )
        recorder_log = tmp_path / "asked.log"
        recorder_log.write_text("", encoding="utf-8")
        recorder = "" if recorded_answer is None else self._RECORDER
        result = run_bash(
            'source "$1"; eval "$2"; APP_CONTAINER=probe-app; '
            'RECORDER_LOG="$3"; RECORDED_ANSWER="$4"; eval "$5"; '
            'eval "$6"; eval "$7"; eval "$8"; '
            'read_app_stack; start_app',
            str(_BACKUP_LIB), recorder, str(recorder_log),
            str(recorded_answer or 0), _RESTORE_CONFIG,
            function_text(_RESTORE, "app_container_exists"),
            function_text(_RESTORE, "read_app_stack"),
            function_text(_RESTORE, "start_app"),
            path_prefix=tmp_path / "bin",
        )
        return (
            result, log.read_text(encoding="utf-8"),
            recorder_log.read_text(encoding="utf-8"),
        )

    @pytest.mark.parametrize(
        ("answer", "ready", "asked"), [(0, True, 1), (1, False, 30)],
        ids=["it-says-running", "it-says-not-running"],
    )
    def test_with_no_healthcheck_it_asks_the_shared_check(
        self, tmp_path, answer, ready, asked,
    ):
        """Each look asks ``container_running``; docker is never asked ``State.Running``."""
        result, calls, recorded = self._start_app(
            tmp_path, recorded_answer=answer,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert recorded.splitlines() == ["asked probe-app"] * asked, recorded
        assert "State.Running" not in calls, calls
        assert ("Application container is running" in result.stdout) is ready
        assert ("did not become ready within 60s" in result.stdout) is not ready

    def test_with_a_healthcheck_still_starting_it_asks_nothing_of_it(
        self, tmp_path,
    ):
        """A healthcheck decides; "running" alone is never ready then."""
        result, calls, recorded = self._start_app(
            tmp_path, health="starting\n", recorded_answer=0,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert recorded == "", recorded
        assert "Application container is running" not in result.stdout
        assert "did not become ready within 60s" in result.stdout
        assert calls.count("{{.State.Health.Status}} probe-app") == 30, calls

    @pytest.mark.parametrize(
        ("running", "status", "ready"),
        [("true\n", 0, True), ("false\n", 0, False), ("", 1, False)],
        ids=["running", "stopped", "docker-cannot-find-it"],
    )
    def test_the_real_check_answers_through_docker(
        self, tmp_path, running, status, ready,
    ):
        """End to end with the real helper: its one inspect, its answer."""
        result, calls, _ = self._start_app(
            tmp_path, running=running, status=status,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert ("Application container is running" in result.stdout) is ready
        asked = calls.splitlines().count(
            "inspect --format {{.State.Running}} probe-app"
        )
        assert asked == (1 if ready else 30), calls


class TestRestoreStartsTheAppTheWayComposeMadeIt:
    """Ruling plan#3534: the start is a ``docker compose up``, which deploys first.

    A ``docker start`` runs no deploy since plan#3421, so the app would serve
    a restored database its code is ahead of, holding no grant on it.  The
    compose command is read from the app container's own labels, so it
    names the stack the restore stopped and never a compose file of the
    directory the script runs from.
    """

    @pytest.mark.parametrize(
        ("labels", "argv"),
        [
            (_LABELS, _START.split(" ")),
            (
                {
                    "project": "other",
                    "project.working_dir": "/srv/other stack",
                    "project.config_files": "/srv/other stack/compose.yml",
                    "service": "web",
                },
                [
                    "compose", "--project-name", "other",
                    "--project-directory", "/srv/other stack",
                    "--file", "/srv/other stack/compose.yml",
                    "up", "--pull", "never", "-d", "web",
                ],
            ),
        ],
        ids=["two-files", "one-file-another-service"],
    )
    def test_the_start_is_the_compose_up_its_labels_name(
        self, tmp_path, labels, argv,
    ):
        """Every word of the start comes from a label, each label one word;
        the project is parsed first, and a ``docker start`` is never run.

        The second case's directory holds a space, which only the bracketed
        log can show is one word.
        """
        result, calls = _restore_functions(
            tmp_path, "read_app_stack; start_app", labels=labels,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        argvs = (tmp_path / "bin" / "argv.log").read_text(
            encoding="utf-8").splitlines()
        config = argv[:argv.index("up")] + ["config", "--quiet"]
        assert argvs.count("".join(f"[{word}]" for word in config)) == 1, argvs
        assert argvs.count("".join(f"[{word}]" for word in argv)) == 1, argvs
        assert calls.index(" ".join(config)) < calls.index(" ".join(argv)), calls
        assert not [call for call in calls if call.startswith("start ")], calls
        assert "Application container is running" in result.stdout

    @pytest.mark.parametrize("missing", list(_LABELS))
    def test_a_missing_label_is_refused_before_anything_is_changed(
        self, tmp_path, missing,
    ):
        """No label, no start: the refusal names it, and compose is never run.

        Without the check an empty project, directory or file list would
        let compose fall back to the current directory's compose file.
        """
        labels = {key: value for key, value in _LABELS.items() if key != missing}
        result, calls = _restore_functions(
            tmp_path, "read_app_stack", labels=labels,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert (
            f"App container 'probe-app' has no com.docker.compose.{missing} label"
            in result.stdout
        )
        assert "Nothing has been changed." in result.stdout
        assert not [call for call in calls if call.startswith("compose ")], calls

    def test_a_project_that_does_not_parse_is_refused(self, tmp_path):
        """A file gone or a value unset: refused, naming the check that failed."""
        result, calls = _restore_functions(
            tmp_path, "read_app_stack", failing=frozenset({"config"}),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "does not parse" in result.stdout
        assert (
            "      docker compose --project-name probe-project"
            " --project-directory /stacks/probe"
            " --file /stacks/probe/docker-compose.yml"
            " --file /stacks/probe/docker-compose.override.yml config --quiet"
        ) in result.stdout.splitlines()
        assert "Nothing has been changed." in result.stdout
        assert not [call for call in calls if " up " in call], calls

    def test_with_no_app_container_it_advises_a_host_migration_as_the_owner(
        self, tmp_path,
    ):
        """Nothing to start: no label is read, compose is never run, and the
        advice names the owner login a host migration must run as (ruling
        plan#3608)."""
        result, calls = _restore_functions(
            tmp_path, "read_app_stack; start_app", app_exists=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == ["inspect probe-app"], calls
        assert "Application container 'probe-app' not found" in result.stdout
        assert (
            "Run 'flask db upgrade' from the checkout, with .env's DATABASE_URL"
            " naming the owner login, shekel_owner (ruling plan#3608)"
        ) in result.stdout

    def test_a_failed_start_stops_the_script(self, tmp_path):
        """The deploy the start runs failed: the script exits 1 and waits for
        nothing."""
        result, calls = _restore_functions(
            tmp_path, "read_app_stack; start_app", failing=frozenset({"up"}),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert calls.count(_START) == 1, calls
        assert not [call for call in calls if "State." in call], calls


class TestRestoreHandsTheDatabaseToTheOwner:
    """The restored objects are the superuser's until the provisioning script runs.

    ``scripts/backup.sh`` dumps ``--no-owner --no-privileges``, so the
    restore makes every object the superuser's, holding no grant: the
    owner's deploy could not change one and the web login could read none.
    """

    def test_a_db_container_without_the_script_is_refused_first(self, tmp_path):
        """A stack from before plan#2166: refused, and nothing else is asked."""
        result, calls = _restore_functions(
            tmp_path, "check_prerequisites",
            failing=frozenset({"hook-check"}),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert (
            f"Database container 'probe-db' holds no executable {_PROVISION_ROLES}."
            in result.stdout
        )
        assert "Nothing has been changed." in result.stdout
        assert calls == [
            "inspect --format {{.State.Running}} probe-db", _HOOK_CHECK,
        ], calls

    def test_the_prerequisites_check_the_script_then_read_the_app_stack(
        self, tmp_path,
    ):
        """The script is checked as a file and executable, then the app's
        project is read and parsed."""
        result, calls = _restore_functions(tmp_path, "check_prerequisites")
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[:3] == [
            "inspect --format {{.State.Running}} probe-db", _HOOK_CHECK,
            "inspect probe-app",
        ], calls
        assert calls[-1].endswith(" config --quiet"), calls

    @pytest.mark.parametrize(
        ("database", "call"),
        [
            (None, _HAND_OVER),
            ("probe_data",
             f"exec --env POSTGRES_DB=probe_data probe-db {_PROVISION_ROLES}"),
        ],
        ids=["default-database", "the-database-it-restored"],
    )
    def test_the_hand_over_runs_the_script_on_the_restored_database(
        self, tmp_path, database, call,
    ):
        """The provisioning script, in the database container, told which
        database: the one ``PGDATABASE`` names."""
        prefix = "" if database is None else f"PGDATABASE={database}; "
        result, calls = _restore_functions(
            tmp_path, prefix + 'eval "$1"; hand_over_to_owner', _RESTORE_CONFIG,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [call], calls


def _restore_main(tmp_path: Path, *, safety_dump: bool = False, **stub,
                  ) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the restore's ``main`` from its text, with its exit trap armed.

    The backup is a real ``.sql.gz``, so the artifact's validation runs for
    real; ``--skip-confirm`` answers the prompt.  The safety dump is skipped
    unless *safety_dump*, and is then written under *tmp_path*.

    Args:
        tmp_path: pytest's per-test temporary directory.
        safety_dump: Whether the safety dump is taken.
        **stub: :func:`_restore_stub`'s keyword arguments.

    Returns:
        The completed process and the docker stub's log, a line per call.
    """
    backup = tmp_path / "shekel_backup_20261112_020000.sql.gz"
    backup.write_bytes(gzip.compress(b"SELECT 1;\n"))
    flags = ["--skip-confirm"] + ([] if safety_dump else ["--skip-safety-dump"])
    return _restore_functions(
        tmp_path, 'BACKUP_LOCAL_DIR="$1"; eval "$2"; shift 2; main "$@"',
        str(tmp_path / "dumps"), trap_line(_RESTORE), *flags, str(backup),
        **stub,
    )


class TestRestoreSaysWhereAFailureLeftIt:
    """The exit trap names the state a failure left, and the way on from it.

    The ERR trap it replaced never ran: bash runs no ERR trap inside a
    function without errtrace, and every step runs inside ``main``.  The
    first test is the one that fails on it.
    """

    _BANNER = "============================================================"

    def test_a_failed_restore_names_the_safety_dump(self, tmp_path):
        """The restore's ``psql`` failed inside the destructive window: the
        guidance says the database may be empty and names the dump to
        recover; nothing is handed over or started."""
        result, calls = _restore_main(
            tmp_path, safety_dump=True, failing=frozenset({"restore"}),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "RESTORE FAILED INSIDE THE DESTRUCTIVE WINDOW" in result.stderr
        dumps = list((tmp_path / "dumps").glob("pre_restore_safety_*.sql.gz"))
        assert len(dumps) == 1, dumps
        assert (
            f"      ./scripts/restore.sh --skip-safety-dump {dumps[0]}"
            in result.stderr.splitlines()
        ), result.stderr
        assert "The app container was left STOPPED on purpose." in result.stderr
        assert _HAND_OVER not in calls, calls
        assert _START not in calls, calls

    def test_a_failed_hand_over_says_how_to_finish(self, tmp_path):
        """Restored, not handed over: the guidance gives the hand-over and the
        start to run, and the script keeps its own exit status."""
        result, calls = _restore_main(
            tmp_path, failing=frozenset({"hand-over"}),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "RESTORED, BUT NOT HANDED TO THE OWNER" in result.stderr
        lines = result.stderr.splitlines()
        assert (
            "      docker exec --env POSTGRES_DB=shekel probe-db " + _PROVISION_ROLES
        ) in lines, result.stderr
        assert "      docker " + _START in lines, result.stderr
        assert "RESTORE FAILED INSIDE" not in result.stderr
        assert _START not in calls, calls

    def test_a_failed_start_says_where_to_read_why(self, tmp_path):
        """Restored and handed over, the start failed: the guidance gives the
        logs to read and the start to run again."""
        result, calls = _restore_main(tmp_path, failing=frozenset({"up"}))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "RESTORED, BUT THE APP DID NOT START" in result.stderr
        lines = result.stderr.splitlines()
        logs = _START.replace("up --pull never -d app", "logs")
        assert "      docker " + logs in lines, result.stderr
        assert "      docker " + _START in lines, result.stderr
        assert calls.index(_HAND_OVER) < calls.index(_START), calls

    def test_a_refusal_before_the_drop_prints_no_guidance(self, tmp_path):
        """Nothing was changed, so there is no state to report: no banner, and
        the app is never stopped."""
        result, calls = _restore_main(
            tmp_path, failing=frozenset({"hook-check"}),
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert self._BANNER not in result.stderr
        assert "stop probe-app" not in calls, calls

    def test_a_restore_runs_its_steps_in_order_and_prints_no_guidance(
        self, tmp_path,
    ):
        """Checked, dumped, stopped, dropped, restored, handed over, started:
        each step's docker call in that order, and a clean exit."""
        result, calls = _restore_main(tmp_path, safety_dump=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert self._BANNER not in result.stderr
        config = _START.replace("up --pull never -d app", "config --quiet")

        def first(prefix: str) -> int:
            """The index of the first call opening with *prefix*."""
            return next(i for i, call in enumerate(calls) if call.startswith(prefix))

        order = [
            first(_HOOK_CHECK), first(config), first("exec probe-db pg_dump"),
            first("stop probe-app"),
            first("exec probe-db psql -U shekel_user -d postgres --quiet -c DROP"),
            first("exec -i probe-db psql"), first(_HAND_OVER), first(_START),
        ]
        assert order == sorted(order), calls


def _extract_env_value(env_file_text: str | None, key: str, tmp_path: Path,
                       ) -> subprocess.CompletedProcess:
    """Run reconcile's ``extract_env_value`` against a temporary ``.env``.

    Args:
        env_file_text: The ``.env`` to write, or ``None`` for no file.
        key: The key asked for.
        tmp_path: The temporary ``PROD_ROOT``.

    Returns:
        The completed process.
    """
    if env_file_text is not None:
        (tmp_path / ".env").write_text(env_file_text, encoding="utf-8")
    return run_bash(
        'PROD_ROOT="$1"; eval "$2"; value=$(extract_env_value "$3"); '
        'printf "%s" "$value"',
        str(tmp_path), function_text(_RECONCILE, "extract_env_value"), key,
    )


class TestReconcileReadsTheFirstLineOfAKey:
    """``extract_env_value``: the first ``KEY=`` line's value, or nothing."""

    @pytest.mark.parametrize(
        ("env", "key", "value"),
        [
            ("A=1\nKEY=first\nKEY=second\n", "KEY", "first"),
            ("KEY=a=b=c\n", "KEY", "a=b=c"),
            ("KEY_OLD=old\nKEY=new\n", "KEY", "new"),
            ("  KEY=indented\nKEY=flush\n", "KEY", "flush"),
            ("A=1\nKEY=no-newline", "KEY", "no-newline"),
            ("KEY=\n", "KEY", ""),
            ("A=1\n", "KEY", ""),
        ],
        ids=[
            "first-of-two", "value-holds-equals", "a-longer-key-is-not-it",
            "an-indented-line-is-not-it", "last-line-unterminated",
            "empty-value", "absent",
        ],
    )
    def test_it_prints_the_value(self, tmp_path, env, key, value):
        """The value after the first ``=``, of the first matching line."""
        result = _extract_env_value(env, key, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == value, result.stdout

    def test_a_missing_file_is_an_empty_value_not_a_failure(self, tmp_path):
        """Never a non-zero status (OPS/SH-12): the callers' own dies explain."""
        result = _extract_env_value(None, "KEY", tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_an_unreadable_file_is_an_empty_value_not_a_failure(self, tmp_path):
        """A directory where ``.env`` should be reads as no value, too."""
        (tmp_path / ".env").mkdir()
        result = _extract_env_value(None, "KEY", tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


def _compose_image_digest(compose_text: str | None, tmp_path: Path,
                          ) -> subprocess.CompletedProcess:
    """Run reconcile's ``compose_image_digest`` against a temporary compose file.

    Args:
        compose_text: The ``docker-compose.yml`` to write, or ``None``.
        tmp_path: The temporary ``PROD_ROOT``.

    Returns:
        The completed process.
    """
    if compose_text is not None:
        (tmp_path / "docker-compose.yml").write_text(
            compose_text, encoding="utf-8",
        )
    return run_bash(
        'PROD_ROOT="$1"; eval "$2"; digest=$(compose_image_digest); '
        'printf "%s" "$digest"',
        str(tmp_path), function_text(_RECONCILE, "compose_image_digest"),
    )


class TestReconcileFindsTheLegacyInlineDigest:
    """``compose_image_digest``: the first digest on a SHEKEL image line."""

    @pytest.mark.parametrize(
        ("compose", "digest"),
        [
            (
                f"  db:\n    image: postgres:16@{_DIGEST_A}\n"
                f"  app:\n    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B}\n",
                _DIGEST_B,
            ),
            (
                "    image: ghcr.io/saltyreformed/shekel:latest\n"
                f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_C}\n",
                _DIGEST_C,
            ),
            (
                f"    image: ghcr.io/saltyreformed/shekel@{_DIGEST_B} "
                f"# was {_DIGEST_C}\n",
                _DIGEST_B,
            ),
            ("    image: ghcr.io/saltyreformed/shekel:latest\n", ""),
        ],
        ids=[
            "not-the-postgres-pin", "first-shekel-line-with-a-digest",
            "leftmost-on-its-line", "none",
        ],
    )
    def test_it_prints_the_digest(self, tmp_path, compose, digest):
        """Postgres's pin is never it; the first shekel digest is."""
        result = _compose_image_digest(compose, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == digest, result.stdout

    def test_a_missing_file_is_an_empty_digest_not_a_failure(self, tmp_path):
        """The caller's own die names both places it looked."""
        result = _compose_image_digest(None, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_an_unreadable_file_is_an_empty_digest_not_a_failure(self, tmp_path):
        """A directory where the compose file should be: no digest, status 0."""
        (tmp_path / "docker-compose.yml").mkdir()
        result = _compose_image_digest(None, tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
