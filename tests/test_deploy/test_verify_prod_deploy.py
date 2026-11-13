"""``deploy/verify_prod_deploy.sh`` is DRIVEN against a scripted ``docker``.

Card X-cx L9a moved the coordinator's post-deploy check into the repository,
beside the script whose work it grades.  As ``test_shekel_deploy_behaviour/``
does for the deploy, these observe OUTCOMES -- the exit status, the verdict
line and the calls made -- with a stub ``docker`` first on ``PATH``, so no
daemon is needed and no test here is ``@pytest.mark.docker``: CI and a local
``./scripts/test.sh`` both run them.

The stub answers the three calls the check makes: ``docker ps`` (the
container's status line), ``docker exec`` (one probe per URL, answered from a
table, and the two reads of what gunicorn holds) and ``docker logs`` (the log
since the deploy on stdout and on stderr, as docker passes both through, or a
failure).

The two reads of what gunicorn holds (card plan#2166) are not answered from a
table: the stub runs the check's own program, here, on stand-ins for the
container's two paths.  Gunicorn's ``/proc/1/environ`` is a real process's,
started with the environment a test gives it, so the program reads the
kernel's own format; ``/run/secrets`` is a directory of files.  A read of any
other path is refused, so a check that reads elsewhere fails rather than
reading this host's.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERIFY_SCRIPT = _REPO_ROOT / "deploy" / "verify_prod_deploy.sh"

#: What a good deploy answers: each URL's status and Location, as the probe
#: prints them.
_GOOD_PROBES = {
    "/health": "200 ",
    "/dashboard": "302 /login?next=%2Fdashboard",
    "/no-such-url-xyz": "302 /login?next=%2Fno-such-url-xyz",
    "/login": "200 ",
}

#: Begins every value a test gives gunicorn, in its environment or its secret
#: files, so a test can find one anywhere the check printed or passed it.
_VALUE = "value-never-printed-"
#: Gunicorn's environment after a good deploy: the web server's own
#: settings and credentials, the web login's URL among them, and no other.
_GOOD_ENVIRON = {
    "DATABASE_URL_APP": f"postgresql://shekel_app:{_VALUE}app@db:5432/shekel?sslmode=require",
    "APP_ROLE_PASSWORD": f"{_VALUE}app",
    "SECRET_KEY": f"{_VALUE}key",
    "TOTP_ENCRYPTION_KEY": f"{_VALUE}totp",
    "TOTP_ENCRYPTION_KEY_OLD": "",
    "VALKEY_PASSWORD": f"{_VALUE}store",
    "FLASK_ENV": "production",
    "TZ": "America/New_York",
}
#: The web server's secret files after a good deploy (deploy/docker-compose.prod.yml).
_GOOD_SECRET_FILES = ("secret_key", "app_role_password", "totp_encryption_key",
                      "valkey_password")
#: The variables and secret files that carry the superuser's or the owner's
#: password, which gunicorn must not hold, in the order the check asks.
_CREDENTIAL_VARIABLES = ("POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE", "DB_PASSWORD",
                         "DATABASE_URL", "OWNER_ROLE_PASSWORD", "PGPASSWORD")
_CREDENTIAL_FILES = ("postgres_password", "owner_role_password")
#: The stand-in for gunicorn: a process that waits, holding its environment.
_SLEEP = shutil.which("sleep")


def _write_fake_docker(bin_dir: pathlib.Path) -> None:
    """Install a scripted ``docker`` stub onto a PATH directory.

    Args:
        bin_dir: Directory placed first on ``PATH`` for the run.
    """
    stub = bin_dir / "docker"
    stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        # Every call is logged on ONE line (the probe's program has
        # newlines); behaviour comes from FAKE_* variables.
        printf '%s\\n' "${*//$'\\n'/ }" >>"$FAKE_LOG"
        case "$1" in
        ps) printf '%s\\n' "$FAKE_STATUS" ;;
        exec)
            # FAKE_EXEC_RC models an exec that fails and prints nothing.
            [ "$FAKE_EXEC_RC" != "0" ] && exit "$FAKE_EXEC_RC"
            # exec CONTAINER python -c PROGRAM ARGUMENT...: a read of what
            # gunicorn holds runs its own program here, on the stand-in
            # for the one path it may read.
            case "$6" in
            environ|files)
                case "$6 $7" in
                "environ /proc/1/environ") path="$FAKE_ENVIRON" ;;
                "files /run/secrets") path="$FAKE_SECRETS" ;;
                *) printf 'stub: a read of %s\\n' "$7" >&2; exit 97 ;;
                esac
                exec python3 -c "$5" "$6" "$path" "${@:8}"
                ;;
            esac
            # The probe's last argument is the URL path; its answer is the
            # table line naming that path.
            url="${!#}"
            while IFS=' ' read -r path answer; do
                if [ "$path" = "$url" ]; then
                    printf '%s\\n' "$answer"
                    exit 0
                fi
            done <"$FAKE_PROBES"
            exit 1
            ;;
        logs)
            # docker logs passes the container's stdout and stderr through
            # as its own; gunicorn's error log is the container's stderr.
            [ -n "$FAKE_LOGS" ] && printf '%s\\n' "$FAKE_LOGS"
            [ -n "$FAKE_LOGS_STDERR" ] && printf '%s\\n' "$FAKE_LOGS_STDERR" >&2
            exit "$FAKE_LOGS_RC"
            ;;
        esac
        exit 0
        """), encoding="utf-8")
    stub.chmod(0o755)


class _Production:
    """A scripted production the real check can be pointed at."""

    def __init__(self, tmp_path: pathlib.Path):
        """Lay out the stub's PATH directory and files.

        Args:
            tmp_path: pytest's per-test temporary directory.
        """
        self.root = tmp_path
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        self.log = tmp_path / "docker.log"
        self.log.write_text("", encoding="utf-8")
        _write_fake_docker(self.bin_dir)

    @property
    def calls(self) -> list[str]:
        """Every stub ``docker`` call, in order.

        Returns:
            One entry per call.
        """
        return self.log.read_text(encoding="utf-8").splitlines()

    def verify(self, *args: str, status: str = "Up 31 seconds (healthy)",
               probes: dict[str, str] | None = None, exec_rc: str = "0",
               logs: str = "booted", logs_stderr: str = "", logs_rc: str = "0",
               container: str | None = None,
               gunicorn_env: dict[str, str] | None = None,
               secret_files: tuple[str, ...] | None = _GOOD_SECRET_FILES,
               environ_path: pathlib.Path | None = None,
               secrets_path: pathlib.Path | None = None) -> subprocess.CompletedProcess:
        """Run the real check.

        Args:
            *args: The check's arguments (SINCE, or nothing).
            status: What ``docker ps`` reports for the container.
            probes: Each URL's answer, merged over :data:`_GOOD_PROBES`.
            exec_rc: Exit status of every ``docker exec`` (non-zero: no answer).
            logs: What ``docker logs`` prints on stdout.
            logs_stderr: What it prints on stderr: the container's stderr,
                or docker's own error.
            logs_rc: Exit status of ``docker logs``.
            container: ``SHEKEL_CONTAINER_NAME``, or absent for the default.
            gunicorn_env: Gunicorn's whole environment, or
                :data:`_GOOD_ENVIRON` when absent.
            secret_files: The files under its ``/run/secrets``, each holding a
                value, or ``None`` for no such directory.
            environ_path: Read in place of ``/proc/1/environ``, or the
                stand-in gunicorn's own when absent.
            secrets_path: Read in place of ``/run/secrets``, or the directory
                of ``secret_files`` when absent.

        Returns:
            The completed process.
        """
        table = self.root / "probes.txt"
        table.write_text("".join(
            f"{path} {answer}\n"
            for path, answer in {**_GOOD_PROBES, **(probes or {})}.items()),
            encoding="utf-8")
        secrets_dir = self.root / "secrets"
        if secret_files is not None:
            secrets_dir.mkdir()
            for name in secret_files:
                (secrets_dir / name).write_text(f"{_VALUE}{name}", encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if key != "SHEKEL_CONTAINER_NAME"}
        env.update({
            "PATH": f"{self.bin_dir}:{env['PATH']}",
            "FAKE_LOG": str(self.log),
            "FAKE_STATUS": status,
            "FAKE_PROBES": str(table),
            "FAKE_EXEC_RC": exec_rc,
            "FAKE_LOGS": logs,
            "FAKE_LOGS_STDERR": logs_stderr,
            "FAKE_LOGS_RC": logs_rc,
            "FAKE_SECRETS": str(secrets_path or secrets_dir),
        })
        if container is not None:
            env["SHEKEL_CONTAINER_NAME"] = container
        assert _SLEEP, "no sleep on PATH to stand in for gunicorn"
        # Popen returns once the exec has happened, so the stand-in's
        # environ is already its own, not this process's; leaving the block
        # waits for the killed stand-in.
        with subprocess.Popen([_SLEEP, "60"], env=(
                _GOOD_ENVIRON if gunicorn_env is None else gunicorn_env)) as gunicorn:
            try:
                env["FAKE_ENVIRON"] = str(environ_path or f"/proc/{gunicorn.pid}/environ")
                return subprocess.run(
                    ["bash", str(_VERIFY_SCRIPT), *args], env=env, capture_output=True,
                    text=True, timeout=60, check=False,
                )
            finally:
                gunicorn.kill()


def _said(result: subprocess.CompletedProcess, calls: list[str]) -> str:
    """Render what a run printed and invoked, for an assertion's message.

    Args:
        result: The completed run.
        calls: The stub's calls, in order.

    Returns:
        The exit status, the output and every call.
    """
    return (f"The check exited {result.returncode} and said:\n"
            f"{result.stdout}{result.stderr}\nCalls: {calls}")


@pytest.fixture(name="production")
def _production(tmp_path) -> _Production:
    """A scripted production with a stub ``docker`` on PATH.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        The prepared :class:`_Production`.
    """
    return _Production(tmp_path)


def test_the_check_is_in_the_repository_and_executable():
    """``deploy/verify_prod_deploy.sh`` is tracked beside the deploy and runnable."""
    assert _VERIFY_SCRIPT.is_file()
    assert _VERIFY_SCRIPT.stat().st_mode & 0o111


def test_a_good_deploy_reads_verify_ok(production):
    """Healthy, every probe as expected, a clean log: exit 0, and each call made."""
    result = production.verify("2026-10-13T10:39:38-04:00")
    calls = production.calls
    assert result.returncode == 0, _said(result, calls)
    assert result.stdout.splitlines()[0] == (
        "container shekel-prod-app: Up 31 seconds (healthy)"), _said(result, calls)
    assert result.stdout.splitlines()[-1] == "VERIFY OK", _said(result, calls)
    assert calls[0] == "ps --filter name=shekel-prod-app --format {{.Status}}", (
        _said(result, calls))
    assert [call.rsplit(" ", 1)[-1] for call in calls[1:5]] == list(_GOOD_PROBES), (
        _said(result, calls))
    assert all(call.startswith("exec shekel-prod-app python -c ")
               for call in calls[1:7]), _said(result, calls)
    assert calls[5].endswith(" environ /proc/1/environ DATABASE_URL_APP "
                             + " ".join(_CREDENTIAL_VARIABLES)), _said(result, calls)
    assert calls[6].endswith(" files /run/secrets " + " ".join(_CREDENTIAL_FILES)), (
        _said(result, calls))
    assert calls[7:] == [
        "logs --since 2026-10-13T10:39:38-04:00 shekel-prod-app"], _said(result, calls)
    assert result.stdout.splitlines()[5:7] == [
        "ok   gunicorn's environment names no superuser or owner credential",
        "ok   gunicorn's secret files hold no superuser or owner password",
    ], _said(result, calls)
    assert ("log lines with Traceback/ERROR in the last "
            "2026-10-13T10:39:38-04:00: 0") in result.stdout, _said(result, calls)


def test_the_log_window_defaults_to_ten_minutes(production):
    """No SINCE: the log since ten minutes ago."""
    result = production.verify()
    calls = production.calls
    assert result.returncode == 0, _said(result, calls)
    assert calls[-1] == "logs --since 10m shekel-prod-app", _said(result, calls)


def test_the_container_name_comes_from_the_deploy_s_variable(production):
    """``SHEKEL_CONTAINER_NAME``, as ``shekel-deploy.sh`` reads it, reaches every call."""
    result = production.verify(container="probe-app")
    calls = production.calls
    assert result.returncode == 0, _said(result, calls)
    assert result.stdout.startswith("container probe-app: "), _said(result, calls)
    assert calls[0] == "ps --filter name=probe-app --format {{.Status}}", (
        _said(result, calls))
    assert all(call.startswith("exec probe-app ") for call in calls[1:7]), (
        _said(result, calls))
    assert calls[7] == "logs --since 10m probe-app", _said(result, calls)


@pytest.mark.parametrize("fault, says", [
    ({"status": "Up 31 seconds (unhealthy)"}, "FAIL: container not healthy"),
    ({"status": "Up 3 seconds (health: starting)"}, "FAIL: container not healthy"),
    ({"status": ""}, "FAIL: container not healthy"),
    ({"probes": {"/health": "500 "}}, "FAIL /health -> 500"),
    ({"probes": {"/dashboard": "200 "}}, "FAIL /dashboard -> 200"),
    ({"probes": {"/dashboard": "302 /elsewhere"}}, "FAIL /dashboard -> 302 /elsewhere"),
    ({"probes": {"/no-such-url-xyz": "404 "}}, "FAIL /no-such-url-xyz -> 404"),
    ({"probes": {"/no-such-url-xyz": "302 /elsewhere"}},
     "FAIL /no-such-url-xyz -> 302 /elsewhere"),
    ({"probes": {"/login": "302 /login"}}, "FAIL /login -> 302 /login"),
    ({"exec_rc": "1"}, "FAIL /health -> "),
    ({"logs": "[ERROR] Control server error"},
     "log lines with Traceback/ERROR in the last 10m: 1"),
    ({"logs_stderr": "[ERROR] Control server error"},
     "log lines with Traceback/ERROR in the last 10m: 1"),
    ({"logs": "Traceback (most recent call last):\nok\nTraceback (most recent call last):"},
     "log lines with Traceback/ERROR in the last 10m: 2"),
    ({"logs": "", "logs_stderr": "Error: No such container: shekel-prod-app",
      "logs_rc": "1"},
     "FAIL: could not read the log of shekel-prod-app: Error: No such container"),
], ids=["unhealthy", "starting", "absent", "health-500", "gate-open", "wrong-redirect",
        "unknown-url-404", "unknown-url-elsewhere", "login-redirects", "exec-fails",
        "error-line", "error-on-stderr", "tracebacks", "log-unreadable"])
def test_each_fault_alone_reads_verify_failed(production, fault, says):
    """One fault, every other check good: exit 1, the fault named, VERIFY FAILED.

    ``log-unreadable`` is the arm that used to pass: ``docker logs | grep -c``
    counted the failed read's zero lines.
    """
    result = production.verify(**fault)
    calls = production.calls
    assert result.returncode == 1, _said(result, calls)
    assert says in result.stdout, _said(result, calls)
    assert result.stdout.splitlines()[-1] == "VERIFY FAILED", _said(result, calls)


def _held_and_failed(result: subprocess.CompletedProcess, calls: list[str],
                     says: str) -> None:
    """Assert a run failed on ``says`` alone, printing no value it was given.

    Args:
        result: The completed run.
        calls: The stub's calls, in order.
        says: The one line the fault must print.
    """
    lines = result.stdout.splitlines()
    assert result.returncode == 1, _said(result, calls)
    assert says in lines, _said(result, calls)
    assert [line for line in lines if line.startswith("FAIL")] == [says], _said(result, calls)
    assert lines[-1] == "VERIFY FAILED", _said(result, calls)
    assert _VALUE not in result.stdout + result.stderr + "\n".join(calls), (
        _said(result, calls))


@pytest.mark.parametrize("variable", _CREDENTIAL_VARIABLES)
def test_each_credential_variable_in_gunicorn_s_environment_fails(production, variable):
    """One variable that carries the superuser's or the owner's password, in
    gunicorn's environment beside the web server's own: the check fails, naming it and
    printing no value."""
    result = production.verify(gunicorn_env={**_GOOD_ENVIRON, variable: f"{_VALUE}{variable}"})
    _held_and_failed(result, production.calls,
                     f"FAIL gunicorn's environment names {variable}")


def test_a_credential_variable_held_blank_still_fails(production):
    """``DATABASE_URL`` named with an empty value fails all the same: the check reads
    names, so a variable that should not be there fails while it is blank, as the compose
    files' own test grades what each service is given."""
    result = production.verify(gunicorn_env={**_GOOD_ENVIRON, "DATABASE_URL": ""})
    _held_and_failed(result, production.calls, "FAIL gunicorn's environment names DATABASE_URL")


def test_a_value_that_names_a_credential_is_not_a_name(production):
    """A value holding ``POSTGRES_PASSWORD=`` is not that variable: the check splits each
    entry at its first ``=`` and reads the name before it, and the web login's
    ``DATABASE_URL_APP`` is not ``DATABASE_URL``."""
    result = production.verify(gunicorn_env={
        **_GOOD_ENVIRON, "NOTE": "POSTGRES_PASSWORD=x DATABASE_URL=y PGPASSWORD=z"})
    calls = production.calls
    assert result.returncode == 0, _said(result, calls)
    assert result.stdout.splitlines()[-1] == "VERIFY OK", _said(result, calls)


def test_gunicorn_as_today_s_image_starts_it_fails_both_reads(production):
    """Gunicorn holding what ``main``'s ``entrypoint.sh`` gives it before plan#2166 -- the
    superuser's password loaded from its file and re-exported in a database URL and in
    ``DB_PASSWORD`` -- with that file mounted: both reads fail, naming each.  Measured
    alike on 2026-11-13 on the rootless daemon, with production's image (``main`` at
    ``c9a43df68``) under production's hardening."""
    result = production.verify(
        gunicorn_env={**_GOOD_ENVIRON, "POSTGRES_PASSWORD": f"{_VALUE}superuser",
                      "DATABASE_URL": f"postgresql://shekel_user:{_VALUE}superuser@db/shekel",
                      "DB_PASSWORD": f"{_VALUE}superuser"},
        secret_files=(*_GOOD_SECRET_FILES, "postgres_password"))
    calls = production.calls
    lines = result.stdout.splitlines()
    assert result.returncode == 1, _said(result, calls)
    assert [line for line in lines if line.startswith("FAIL")] == [
        "FAIL gunicorn's environment names POSTGRES_PASSWORD DB_PASSWORD DATABASE_URL",
        "FAIL gunicorn's secret files hold postgres_password",
    ], _said(result, calls)
    assert lines[-1] == "VERIFY FAILED", _said(result, calls)
    assert _VALUE not in result.stdout + result.stderr + "\n".join(calls), (
        _said(result, calls))


def test_an_environment_without_the_web_login_s_url_is_not_gunicorn_s(production):
    """A PID 1 whose environment names no ``DATABASE_URL_APP`` is not the gunicorn the
    entrypoint execs (an init in front of it holds only what the container was created
    with): the read proves nothing, and the check fails rather than passing on it."""
    result = production.verify(gunicorn_env={
        key: value for key, value in _GOOD_ENVIRON.items() if key != "DATABASE_URL_APP"})
    _held_and_failed(result, production.calls,
                     "FAIL: /proc/1/environ is not gunicorn's: it names no DATABASE_URL_APP")


def test_an_environment_it_cannot_read_fails(production, tmp_path):
    """No ``/proc/1/environ`` to read: the check fails rather than finding nothing held."""
    result = production.verify(environ_path=tmp_path / "no-such-process" / "environ")
    _held_and_failed(result, production.calls,
                     "FAIL: could not read gunicorn's environment, /proc/1/environ")


@pytest.mark.parametrize("secret_file", _CREDENTIAL_FILES)
def test_each_credential_file_in_gunicorn_s_secrets_fails(production, secret_file):
    """The superuser's or the owner's password file mounted beside the web server's own:
    the check fails, naming it and printing no value."""
    result = production.verify(secret_files=(*_GOOD_SECRET_FILES, secret_file))
    _held_and_failed(result, production.calls,
                     f"FAIL gunicorn's secret files hold {secret_file}")


def test_no_secrets_directory_holds_no_credential_file(production):
    """No ``/run/secrets`` at all, as a container given no secret file has: it holds
    neither password's file, so that read passes."""
    result = production.verify(secret_files=None)
    calls = production.calls
    assert result.returncode == 0, _said(result, calls)
    assert ("ok   gunicorn's secret files hold no superuser or owner password"
            in result.stdout.splitlines()), _said(result, calls)
    assert result.stdout.splitlines()[-1] == "VERIFY OK", _said(result, calls)


def test_secrets_it_cannot_read_fail(production, tmp_path):
    """A ``/run/secrets`` that cannot be listed (here a file, not a directory): the check
    fails rather than finding nothing held."""
    not_a_directory = tmp_path / "secrets-file"
    not_a_directory.write_text("", encoding="utf-8")
    result = production.verify(secrets_path=not_a_directory)
    _held_and_failed(result, production.calls,
                     "FAIL: could not read gunicorn's secret files, /run/secrets")
