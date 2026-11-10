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

**Neither the reconcile script nor the restore script is run here.**  One
writes production's ``/opt/docker/shekel`` (a hardcoded ``PROD_ROOT``), the
other drops a database, so each test reads ONE function's text out of its
script and runs that, against a temporary tree or a stub ``docker``.  The
expected values are worked by hand from what the old spellings printed.  The
reconcile pipelines keep grep, now ending in a reader that drains, and both
carried ``|| true``, so their race only ever cost a status nobody read: these
tests grade the meaning, not a race.
"""
from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER_LIB = _REPO_ROOT / "scripts" / "_container_lib.sh"
_BACKUP_LIB = _REPO_ROOT / "scripts" / "_backup_lib.sh"
_RECONCILE = _REPO_ROOT / "scripts" / "reconcile_prod_to_canonical.sh"
_RESTORE = _REPO_ROOT / "scripts" / "restore.sh"

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _function_text(script: Path, name: str) -> str:
    """Return shell function *name*'s definition from *script*, verbatim.

    A definition opens with ``name() {`` at the start of a line and closes
    with the first ``}`` alone at the start of a line, which is how every
    top-level function in these scripts is written (shfmt enforces it).

    Args:
        script: The script holding the function.
        name: The function's name.

    Returns:
        The definition, from its first line through its closing brace.
    """
    found = re.findall(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
        script.read_text(encoding="utf-8"), flags=re.S | re.M,
    )
    assert len(found) == 1, (
        f"expected one definition of {name}() in {script}, found {len(found)}"
    )
    return found[0]


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


def _bash(program: str, *args: str, path_prefix: Path | None = None,
          ) -> subprocess.CompletedProcess:
    """Run *program* under the swept scripts' own ``set -euo pipefail``.

    A function its script calls inside a command substitution is called the
    same way here, since bash runs that subshell WITHOUT errexit: grading it
    bare under ``set -e`` would grade a context its callers never give it.

    Args:
        program: Bash source, reading its inputs from ``$1``, ``$2`` ...
        args: The positional parameters.
        path_prefix: A directory to put first on ``PATH`` (the stubs).

    Returns:
        The completed process, stdout and stderr captured.
    """
    env = None
    if path_prefix is not None:
        env = {"PATH": f"{path_prefix}:/usr/bin:/bin"}
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{program}", "_", *args],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )


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
        result = _bash(
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
        result = _bash(
            'source "$1"; require_db_container "$2" || exit "$?"',
            str(_BACKUP_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Database container 'probe-db' is not running" in result.stdout

    def test_the_backup_helper_passes_a_running_container(self, tmp_path):
        """And lets a running one through, saying nothing."""
        _fake_docker(tmp_path / "bin", stdout="true\n", status=0)
        result = _bash(
            'source "$1"; require_db_container "$2" || exit "$?"',
            str(_BACKUP_LIB), "probe-db", path_prefix=tmp_path / "bin",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == ""


def _restore_stub(bin_dir: Path, *, health: str | None, running: str,
                  status: int) -> Path:
    """Install the ``docker`` and ``sleep`` stubs ``start_app`` meets.

    The app container exists and starts.  Its healthcheck reports *health*,
    or, for ``None``, it has none (that inspect fails, so ``start_app``
    reads ``none``).  The running-check is answered with *running* and
    *status*.  ``sleep`` returns at once, so thirty retries cost nothing.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        health: What the ``{{.State.Health.Status}}`` inspect prints.
        running: What the ``{{.State.Running}}`` inspect prints.
        status: That inspect's exit status.

    Returns:
        The invocation log's path.
    """
    bin_dir.mkdir()
    log = bin_dir / "docker.log"
    (bin_dir / "health.out").write_text(health or "", encoding="utf-8")
    (bin_dir / "running.out").write_text(running, encoding="utf-8")
    health_arm = (
        "exit 1" if health is None else f"cat '{bin_dir}/health.out'; exit 0"
    )
    (bin_dir / "docker").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >>'{log}'
        case "$*" in
            *State.Health.Status*) {health_arm} ;;
            *State.Running*) cat '{bin_dir}/running.out'; exit {status} ;;
        esac
        exit 0
        """), encoding="utf-8")
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n",
                                   encoding="utf-8")
    for name in ("docker", "sleep"):
        (bin_dir / name).chmod(0o755)
    return log


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
        result = _bash(
            'source "$1"; eval "$2"; APP_CONTAINER=probe-app; '
            'RECORDER_LOG="$3"; RECORDED_ANSWER="$4"; '
            'eval "$5"; eval "$6"; start_app',
            str(_BACKUP_LIB), recorder, str(recorder_log),
            str(recorded_answer or 0),
            _function_text(_RESTORE, "app_container_exists"),
            _function_text(_RESTORE, "start_app"),
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
    return _bash(
        'PROD_ROOT="$1"; eval "$2"; value=$(extract_env_value "$3"); '
        'printf "%s" "$value"',
        str(tmp_path), _function_text(_RECONCILE, "extract_env_value"), key,
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
    return _bash(
        'PROD_ROOT="$1"; eval "$2"; digest=$(compose_image_digest); '
        'printf "%s" "$digest"',
        str(tmp_path), _function_text(_RECONCILE, "compose_image_digest"),
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
