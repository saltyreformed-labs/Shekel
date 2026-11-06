"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_scripts/test_test_runner.py``, which ``tools/split`` moved here, and those the
signal cases (``test_signals.py``) added beside them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from tests._repo_root import REPO_ROOT


_TEST_RUNNER = REPO_ROOT / "scripts/test.sh"


# The wrapper's start of its cluster through the module, in one command as
# :func:`_code_lines` gives it.  The ``--`` before the arguments is optional
# HERE because the dash-image case grades it, so each property has one case.
_STARTS_THE_CLUSTER = re.compile(
    r'scripts/private_cluster\.py"\s+start (?:-- )?"\$_run_container" "\$_token" "\$_image"')


def _stub_tree(
    tmp_path: Path, *, rootless: bool = True, make_socket: bool = True,
    survives: int = 0, pytest_status: int = 0,
) -> Path:
    """Write a stub ``docker`` and ``pytest`` into a directory for ``PATH``.

    The docker stub answers only what the per-run branch asks and records
    each subcommand.  The pytest stub records the ENVIRONMENT it was handed,
    which is what the flag-scrub assertion reads.

    A case changes the stubs' behaviour by writing a file into ``tmp_path``
    (the ``survives`` paragraph names three more): ``run-seconds`` makes
    ``run`` sleep that many seconds before it acts, ``never-ready`` makes the
    readiness probe (``exec``) fail every time, and ``pytest-waits`` makes
    pytest run that many seconds, as the signal cases need.  pytest then
    writes ``pytest-running`` once it is ready for a signal, holding its PID
    and whether SIGINT reached it IGNORED (``1``) or at its default (``0``),
    as bash hands a background child it did not reset; at its default, a
    SIGINT ends that wait, and pytest writes ``pytest-interrupts`` a second
    later -- the number of SIGINTs it was sent (a second one passed on by the
    wrapper lands in that second), then ``waited`` if the wrapper, its parent,
    was still alive to wait for it, else ``orphaned`` -- and exits 2, as
    pytest does after a Ctrl-C.

    Args:
        tmp_path: pytest-provided scratch directory.
        rootless: Whether the stub daemon reports itself rootless.  Since
            ``balance:X-br-3`` the wrapper asks before starting anything, so
            a stub that answers nothing is refused -- which is the point.
        make_socket: Whether the stub container leaves a socket behind.  A
            container can report ready and still not have one; the cluster's
            start refuses to hand out a socket that is not there.
        survives: How many removals the stub container survives: the file
            ``stub-container``, which ``run`` makes holding its ``--label``,
            ``ps`` lists while its label is the filter's, and each ``rm`` of
            its id after that many removes.  While a file ``other-container``
            (ANOTHER run's container) exists, ``run`` refuses as docker
            refuses a name in use; a file ``run-kills-the-start`` makes
            ``run`` SIGKILL its start, ``after making`` the container or not.
        pytest_status: The status the stub pytest exits with, which the
            wrapper must exit with too.

    Returns:
        The directory to prepend to ``PATH``.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "docker").write_text(
        _docker_stub(tmp_path, rootless=rootless, make_socket=make_socket,
                     survives=survives),
        encoding="utf-8")
    (binaries / "pytest").write_text(_pytest_stub(tmp_path, pytest_status),
                                     encoding="utf-8")
    for name in ("docker", "pytest"):
        (binaries / name).chmod(0o755)
    return binaries


def _docker_stub(
    tmp_path: Path, *, rootless: bool, make_socket: bool, survives: int
) -> str:
    """The stub ``docker``'s source, as :func:`_stub_tree` describes it.

    Args:
        tmp_path: pytest-provided scratch directory.
        rootless: As :func:`_stub_tree` takes it.
        make_socket: As :func:`_stub_tree` takes it.
        survives: As :func:`_stub_tree` takes it.

    Returns:
        The program.
    """
    security_options = (
        "[name=seccomp,profile=builtin name=rootless]"
        if rootless
        else "[name=seccomp,profile=builtin]"
    )
    socket_arm = "            listener.bind(os.path.join(host, '.s.PGSQL.5432'))"
    if not make_socket:
        socket_arm = "            pass"
    calls = tmp_path / "docker-calls"
    held = tmp_path / "stub-container"
    other = tmp_path / "other-container"
    kills = tmp_path / "run-kills-the-start"
    slow = tmp_path / "run-seconds"
    never_ready = tmp_path / "never-ready"
    return (
        "#!/usr/bin/env python3\n"
        "import os, signal, socket, sys, time\n"
        "args = sys.argv[1:]\n"
        f"open({str(calls)!r}, 'a').write(' '.join(args) + chr(10))\n"
        "cmd = args[0] if args else ''\n"
        f"if cmd == 'run' and os.path.exists({str(slow)!r}):\n"
        f"    time.sleep(float(open({str(slow)!r}).read()))\n"
        "if cmd == 'info':\n"
        f"    print({security_options!r})\n"
        f"elif cmd == 'run' and os.path.exists({str(kills)!r}):\n"
        f"    if open({str(kills)!r}).read() == 'after making':\n"
        f"        open({str(held)!r}, 'w').write(args[args.index('--label') + 1])\n"
        "    os.kill(os.getppid(), signal.SIGKILL)\n"
        f"elif cmd == 'run' and os.path.exists({str(other)!r}):\n"
        "    print('Conflict. The container name is already in use', file=sys.stderr)\n"
        "    sys.exit(125)\n"
        "elif cmd == 'run':\n"
        # The wrapper mounts the run's socket directory at /sockets; a real
        # postgres would create the socket there, so the stub must too.
        f"    open({str(held)!r}, 'w').write(args[args.index('--label') + 1])\n"
        "    mounts = [args[i + 1] for i, a in enumerate(args) if a == '-v']\n"
        "    for mount in mounts:\n"
        "        if mount.endswith(':/sockets'):\n"
        "            host = mount.rsplit(':', 1)[0]\n"
        "            os.makedirs(host, exist_ok=True)\n"
        "            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        # Bound and deliberately NOT unlinked: the file must outlive the stub.
        f"{socket_arm}\n"
        "    print('stubcontainer')\n"
        f"elif cmd == 'exec' and os.path.exists({str(never_ready)!r}):\n"
        "    sys.exit(1)\n"
        f"elif cmd == 'rm' and 'stubcontainer' in args and os.path.exists({str(held)!r}):\n"
        # This removal is already recorded, so it is counted too.
        f"    removals = sum(line.startswith('rm ') for line in open({str(calls)!r}))\n"
        f"    if removals > {survives!r}:\n"
        f"        os.remove({str(held)!r})\n"
        f"elif cmd == 'ps' and os.path.exists({str(held)!r}):\n"
        "    wanted = args[args.index('--filter') + 1].split('=', 1)[1]\n"
        f"    if open({str(held)!r}).read() == wanted:\n"
        "        print('stubcontainer')\n"
        "sys.exit(0)\n"
    )


def _pytest_stub(tmp_path: Path, pytest_status: int) -> str:
    """The stub ``pytest``'s source, as :func:`_stub_tree` describes it.

    Args:
        tmp_path: pytest-provided scratch directory.
        pytest_status: As :func:`_stub_tree` takes it.

    Returns:
        The program.
    """
    env_dump = tmp_path / "pytest-env.json"
    waits = tmp_path / "pytest-waits"
    running = tmp_path / "pytest-running"
    interrupts = tmp_path / "pytest-interrupts"
    return (
        "#!/usr/bin/env python3\n"
        "import json, os, signal, sys, time\n"
        f"json.dump(dict(os.environ), open({str(env_dump)!r}, 'w'))\n"
        f"if os.path.exists({str(waits)!r}):\n"
        # Read before anything here changes it: what the wrapper handed over.
        "    status = open('/proc/self/status').read().splitlines()\n"
        "    ignored = int(next(line.split()[1] for line in status\n"
        "                       if line.startswith('SigIgn:')), 16)\n"
        "    ignores_int = ignored >> (signal.SIGINT - 1) & 1\n"
        "    parent = os.getppid()\n"
        "    got = []\n"
        "    if not ignores_int:\n"
        "        signal.signal(signal.SIGINT, lambda signum, frame: got.append(signum))\n"
        # Renamed into place, so a case never reads it half written.
        f"    open({str(running)!r} + '.new', 'w').write(f'{{os.getpid()}} {{ignores_int}}')\n"
        f"    os.replace({str(running)!r} + '.new', {str(running)!r})\n"
        f"    end = time.monotonic() + float(open({str(waits)!r}).read())\n"
        "    while time.monotonic() < end and not got:\n"
        "        time.sleep(0.01)\n"
        "    if got:\n"
        "        time.sleep(1)\n"
        "        waited = 'waited' if os.getppid() == parent else 'orphaned'\n"
        f"        open({str(interrupts)!r}, 'w').write(f'{{len(got)}} {{waited}}')\n"
        "        sys.exit(2)\n"
        f"sys.exit({pytest_status})\n"
    )


def _wrapper_tree(binaries: Path) -> Path:
    """The tree a case's wrapper runs in, beside the case's stubs.

    Args:
        binaries: Directory holding the stubs.

    Returns:
        The tree's root, which :func:`_wrapper_copy` makes.
    """
    return binaries.parent / "tree"


def _wrapper_copy(binaries: Path) -> Path:
    """The wrapper, with the cluster's module beside it, in a tree of the case's own.

    The wrapper finds its tree from its own path, and where that tree holds ``uv.lock`` it
    replaces a ``.venv`` link and syncs the venv.  So a case runs a copy, which only touches
    the tree the case made: run from this checkout, every case here would sync the checkout's
    own venv, on CI too, as each case scrubs ``CI``.  What a case puts in the tree before the
    wrapper runs is the wrapper's to find; a tree holding nothing more has no lock, and leaves
    the venv alone.

    Args:
        binaries: Directory holding the stubs.

    Returns:
        The copy of ``scripts/test.sh``.
    """
    scripts = _wrapper_tree(binaries) / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for script in (_TEST_RUNNER, _TEST_RUNNER.with_name("private_cluster.py")):
        shutil.copy2(script, scripts)
    return scripts / _TEST_RUNNER.name


def _wrapper_env(
    binaries: Path, runtime: str, env_extra: dict[str, str]
) -> dict[str, str]:
    """The environment the wrapper runs in: this process's, with the stubs
    first on ``PATH``, a stub image and ``runtime`` as ``XDG_RUNTIME_DIR``.

    ``DOCKER_HOST``, ``CI`` and ``SHEKEL_ALLOW_HOST_DOCKER`` are all scrubbed
    unless the caller sets them, so no case in this module can depend on which
    daemon the RUNNING SUITE happens to be using, nor on whether it is running
    in CI.  Each of the three has already been observed inverting a case.

    Args:
        binaries: Directory holding the stubs.
        runtime: The ``XDG_RUNTIME_DIR`` to give it.
        env_extra: Environment overrides, applied last.

    Returns:
        The environment.
    """
    return {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "TEST_DB_IMAGE": "stub-image:test",
        "XDG_RUNTIME_DIR": runtime,
        # Every variable the daemon gate reads is scrubbed unless a
        # case sets it deliberately, so each case states its own
        # premise instead of inheriting one.  CI is the load-bearing
        # one: GitHub always sets CI=true, which SANCTIONS a
        # non-rootless daemon, so the two refusal cases would have
        # asserted the opposite of what happens and taken the
        # branch-protected merge gate red with them.
        # SHEKEL_ALLOW_HOST_DOCKER does the same for a developer who
        # followed docs/testing-standards.md and exported it.
        "CI": "",
        "SHEKEL_ALLOW_HOST_DOCKER": "",
        # DOCKER_HOST is scrubbed unless a case sets it deliberately.
        # The wrapper EXPORTS it in per-run mode -- correctly, since
        # that is what un-skips tests/test_deploy -- so when the suite
        # itself runs per-run, pytest inherits one and a child wrapper
        # sees it already set.  The auto-selection branch then never
        # runs, and the case grading it silently asserts on the
        # parent's value.  Caught by the full suite; every targeted
        # run passed, because a developer shell has no DOCKER_HOST.
        "DOCKER_HOST": "",
        **env_extra,
    }


def _run_wrapper(
    binaries: Path, env_extra: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Invoke the wrapper with the stubs first on ``PATH`` (:func:`_wrapper_env`).

    ``XDG_RUNTIME_DIR`` defaults to a SHORT throwaway directory because the
    per-run socket lives under it and a unix socket path cannot exceed 107
    bytes -- pytest's own ``tmp_path`` is already most of that budget, so a
    test that let it through would be asserting on the wrapper's length
    refusal rather than on the behaviour it means to grade.

    ``env_extra`` is often empty now: since ``balance:X-br-4`` the wrapper
    needs no flag to take the per-run path, so the premise of most cases here
    is simply that it was invoked.

    Args:
        binaries: Directory holding the stubs.
        env_extra: Environment overrides.

    Returns:
        The completed process.
    """
    runtime = env_extra.get("XDG_RUNTIME_DIR")
    scratch = None
    if runtime is None:
        scratch = tempfile.mkdtemp(prefix="skrt", dir="/tmp")
        runtime = scratch
    try:
        return subprocess.run(
            [str(_wrapper_copy(binaries)), "tests/does_not_matter"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=_wrapper_env(binaries, runtime, env_extra),
            check=False,
        )
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


def _launch_wrapper(
    binaries: Path, runtime: Path, env_extra: dict[str, str], *,
    then: str | None = None,
) -> subprocess.Popen[str]:
    """Start the wrapper as a terminal's shell starts a command, and do not
    wait for it.

    SIGINT reaches it at its DEFAULT (``env --default-signal=INT``), whatever
    this process holds: a non-interactive bash cannot trap a signal it was
    started with ignored, and bash hands a background child it did not reset
    -- pytest, before K1a commit B2 -- SIGINT ignored.  It leads a session of
    its own, so a signal to its process group is what a terminal's Ctrl-C
    sends, and reaches nothing of the suite's.

    Args:
        binaries: Directory holding the stubs.
        runtime: The ``XDG_RUNTIME_DIR`` to give it (:func:`_run_wrapper`
            says why it must be short).
        env_extra: Environment overrides.
        then: A command a CALLING shell runs after the wrapper ends, as
            ``bash -c '<the wrapper>; <then>'``; None starts the wrapper
            itself.

    Returns:
        The running process: the wrapper, or the shell calling it.
    """
    command = [str(_wrapper_copy(binaries)), "tests/does_not_matter"]
    if then is not None:
        command = ["bash", "-c", f'"$0" "$1"; {then}', *command]
    return subprocess.Popen(
        ["env", "--default-signal=INT", *command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env=_wrapper_env(binaries, str(runtime), env_extra),
        start_new_session=True,
    )


def _code_lines() -> list[str]:
    """The wrapper's commands as bash reads them, one per entry, in order.

    A line ending in a backslash is joined to the next, as bash joins one
    outside single quotes, and blank and comment lines are dropped.  Read one line at a time, a
    container command split by a continuation (``docker \\`` and then
    ``rm -fv "$_run_container"``) slipped past the wiring check: delta
    review 2's LOW-C.

    Returns:
        The commands of ``scripts/test.sh``.
    """
    commands: list[str] = []
    pending = ""
    for line in _TEST_RUNNER.read_text(encoding="utf-8").splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1]
            continue
        commands.append(pending + line)
        pending = ""
    return commands
