"""``deploy/verify_prod_deploy.sh`` is DRIVEN against a scripted ``docker``.

Card X-cx L9a moved the coordinator's post-deploy check into the repository,
beside the script whose work it grades.  As ``test_shekel_deploy_behaviour/``
does for the deploy, these observe OUTCOMES -- the exit status, the verdict
line and the calls made -- with a stub ``docker`` first on ``PATH``, so no
daemon is needed and no test here is ``@pytest.mark.docker``: CI and a local
``./scripts/test.sh`` both run them.

The stub answers the three calls the check makes: ``docker ps`` (the
container's status line), ``docker exec`` (one probe per URL, answered from a
table) and ``docker logs`` (the log since the deploy on stdout and on stderr,
as docker passes both through, or a failure).
"""
from __future__ import annotations

import os
import pathlib
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
            # The probe's last argument is the URL path; its answer is the
            # table line naming that path.  FAKE_EXEC_RC models an exec
            # that fails and prints nothing.
            [ "$FAKE_EXEC_RC" != "0" ] && exit "$FAKE_EXEC_RC"
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
               container: str | None = None) -> subprocess.CompletedProcess:
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

        Returns:
            The completed process.
        """
        table = self.root / "probes.txt"
        table.write_text("".join(
            f"{path} {answer}\n"
            for path, answer in {**_GOOD_PROBES, **(probes or {})}.items()),
            encoding="utf-8")
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
        })
        if container is not None:
            env["SHEKEL_CONTAINER_NAME"] = container
        return subprocess.run(
            ["bash", str(_VERIFY_SCRIPT), *args], env=env, capture_output=True,
            text=True, timeout=60, check=False,
        )


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
               for call in calls[1:5]), _said(result, calls)
    assert calls[5:] == [
        "logs --since 2026-10-13T10:39:38-04:00 shekel-prod-app"], _said(result, calls)
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
    assert all(call.startswith("exec probe-app ") for call in calls[1:5]), (
        _said(result, calls))
    assert calls[5] == "logs --since 10m probe-app", _said(result, calls)


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
